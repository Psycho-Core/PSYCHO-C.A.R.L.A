"""The in-process GGUF runtime (llama-cpp-python), tested with a fake module."""

from __future__ import annotations

import sys
import types

import pytest

from psycho_carla.intelligence import gguf_inprocess as gi
from psycho_carla.providers.base import Message


class FakeLlama:
    instances: list = []

    def __init__(self, model_path, n_ctx=512, n_gpu_layers=0, n_threads=None, verbose=True):
        self.model_path, self.n_ctx, self.n_gpu_layers = model_path, n_ctx, n_gpu_layers
        self.closed = False
        FakeLlama.instances.append(self)

    def create_chat_completion(self, messages, stream=False, **kw):
        self.last = (messages, kw)
        if stream:
            return iter([{"choices": [{"delta": {"content": w}}]} for w in ("in-", "process")])
        return {"choices": [{"message": {"content": "hello from inside"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    def close(self):
        self.closed = True


@pytest.fixture
def fake_llama(monkeypatch, tmp_path):
    mod = types.ModuleType("llama_cpp")
    mod.Llama = FakeLlama
    mod.__version__ = "9.9.9-fake"
    mod.llama_supports_gpu_offload = lambda: False
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PSYCHO_CARLA_GGUF_RUNTIME", raising=False)
    FakeLlama.instances.clear()
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 64)
    yield mod, model
    gi.engine.unload()


def test_unavailable_library_is_reported_not_crashed(monkeypatch):
    monkeypatch.setitem(sys.modules, "llama_cpp", None)           # import raises
    ok, detail = gi.available()
    assert ok is False and "llama-cpp-python" in detail
    assert gi.engine.load("nowhere.gguf")["ok"] is False


def test_load_chat_stream_unload(fake_llama):
    mod, model = fake_llama
    r = gi.engine.load(str(model), ctx=2048, threads=3)
    assert r["ok"] and gi.engine.running
    assert gi.engine.load(str(model))["already_running"] is True
    out = gi.engine.chat([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=9,
                         model="ignored", tools=[{"x": 1}])
    assert out["choices"][0]["message"]["content"] == "hello from inside"
    assert FakeLlama.instances[0].last[1] == {"temperature": 0.2, "max_tokens": 9}   # junk filtered
    assert "".join(c["choices"][0]["delta"]["content"] for c in gi.engine.chat_stream([])) == "in-process"
    assert gi.engine.unload() is True and not gi.engine.running
    assert FakeLlama.instances[0].closed
    with pytest.raises(RuntimeError):
        gi.engine.chat([])


def test_switching_models_unloads_the_old_one(fake_llama, tmp_path):
    mod, model = fake_llama
    other = tmp_path / "other.gguf"
    other.write_bytes(b"GGUF")
    gi.engine.load(str(model))
    gi.engine.load(str(other))
    assert FakeLlama.instances[0].closed and not FakeLlama.instances[1].closed


def test_load_failure_leaves_nothing_loaded(fake_llama, monkeypatch):
    mod, model = fake_llama
    monkeypatch.setattr(mod, "Llama", lambda **k: (_ for _ in ()).throw(ValueError("bad gguf")))
    r = gi.engine.load(str(model))
    assert r["ok"] is False and "bad gguf" in r["message"] and not gi.engine.running


def test_gpu_layers_follow_the_build(fake_llama, monkeypatch):
    mod, model = fake_llama
    assert gi.layers_for("auto", 9.0) == 0                         # CPU-only build: never offload
    mod.llama_supports_gpu_offload = lambda: True
    monkeypatch.setattr(gi, "_vram_gb", lambda: 16.0)
    assert gi.layers_for("auto", 9.0) == -1                        # fits: everything
    assert 0 < gi.layers_for("auto", 19.9) < 64                    # 32B: partial
    assert gi.layers_for(12, 9.0) == 12


def test_adapter_uses_inprocess_when_available(fake_llama):
    from psycho_carla.providers.adapters.native_gguf import NativeGgufAdapter

    mod, model = fake_llama
    ad = NativeGgufAdapter({"model_path": str(model)})
    assert ad.runtime == "inprocess" and ad.health() == (True, "ready (in-process)")
    res = ad.generate([Message(role="user", content="hi")], "fast_tier", temperature=0.5)
    assert res.text == "hello from inside" and res.usage.total_tokens == 8 and not res.error
    chunks = list(ad.stream([Message(role="user", content="hi")], "fast_tier"))
    assert "".join(c.text for c in chunks) == "in-process" and chunks[-1].done


def test_runtime_setting_and_env_override(fake_llama, monkeypatch):
    from psycho_carla.providers.adapters.native_gguf import choose_runtime

    assert choose_runtime("auto") == "inprocess"
    assert choose_runtime("server") == "server"
    monkeypatch.setenv("PSYCHO_CARLA_GGUF_RUNTIME", "server")
    assert choose_runtime("auto") == "server"
    monkeypatch.delenv("PSYCHO_CARLA_GGUF_RUNTIME")
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    assert choose_runtime("auto") == "server"                      # falls back


def test_lifecycle_start_stop_free_in_process(fake_llama, monkeypatch):
    from psycho_carla.intelligence import model_manager as mm
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle

    mod, model = fake_llama
    monkeypatch.setattr(mm, "find_gguf", lambda mid: model)
    monkeypatch.setattr(ModelLifecycle, "_evict_ollama", staticmethod(lambda: []))
    lc = ModelLifecycle()
    r = lc.start("tiny")
    assert r["ok"] and r["runtime"] == "inprocess" and gi.engine.running
    assert lc.status()["runtime"] == "inprocess"
    assert lc.stop("tiny")["was_running"] and not gi.engine.running
    lc.start("tiny")
    assert lc.free_memory()["released"] == ["tiny"] and not gi.engine.running


def test_delete_unloads_the_in_process_model_first(fake_llama, tmp_path, monkeypatch):
    from psycho_carla.intelligence import model_manager as mm

    mod, _ = fake_llama
    target = mm.models_dir() / "inproc.gguf"
    target.write_bytes(b"GGUF")
    assert gi.engine.load(str(target))["ok"]
    assert mm.delete_local_model("inproc", "native_gguf").ok
    assert not gi.engine.running and not target.exists()


def test_engine_status_reports_the_in_process_runtime(fake_llama):
    from psycho_carla.intelligence.llama_engine import engine_status

    st = engine_status()
    assert st["inprocess"]["available"] is True and st["inprocess"]["detail"] == "9.9.9-fake"
