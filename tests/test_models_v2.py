"""Custom models, GPU device selection, free-memory, remote tiers, exports."""

from __future__ import annotations

import json

import pytest

from psycho_carla.intelligence import model_manager as mm
from psycho_carla.providers.adapters import native_onnx as nx


# -- device selection -------------------------------------------------------
def test_cpu_is_the_default_device():
    assert nx.select_providers("cpu", ["DmlExecutionProvider"])[0] == ["CPUExecutionProvider"]
    assert nx.select_providers("", [])[0] == ["CPUExecutionProvider"]
    assert nx.select_providers("nonsense", ["DmlExecutionProvider"])[0] == ["CPUExecutionProvider"]


def test_gpu_uses_directml_with_cpu_fallback_last():
    providers, note = nx.select_providers("gpu", ["DmlExecutionProvider", "CPUExecutionProvider"])
    assert providers == ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert note.startswith("gpu")


def test_gpu_without_provider_falls_back_to_cpu():
    providers, note = nx.select_providers("auto", ["CPUExecutionProvider"])
    assert providers == ["CPUExecutionProvider"]
    assert "directml" in note.lower()


# -- shared engine / free memory -------------------------------------------
def test_shared_adapter_is_one_object_per_model_and_device(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_NATIVE_DEVICE", "cpu")
    a = nx.shared_adapter({"model_path": str(tmp_path)})
    b = nx.shared_adapter({"model_path": str(tmp_path)})
    assert a is b
    assert nx.shared_adapter({"model_path": str(tmp_path), "device": "gpu"}) is not a


def test_unload_all_unloads_and_counts():
    ad = nx.shared_adapter({"model_path": "/nonexistent/for-test"})
    ad._session = object()
    assert ad.loaded
    assert nx.unload_all() >= 1
    assert not ad.loaded


def test_free_memory_reports_and_is_safe_when_nothing_is_loaded(monkeypatch):
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle

    monkeypatch.setattr(ModelLifecycle, "_evict_ollama", staticmethod(lambda: []))
    result = ModelLifecycle().free_memory()
    assert result["ok"] is True
    assert result["released"] == []


def test_stop_clears_running_state():
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle, ModelState

    lc = ModelLifecycle()

    class _A:
        def unload(self):
            pass

    lc._adapter, lc._adapter_model_id = _A(), "m"
    lc._states["m"] = ModelState(model_id="m", engine="native_onnx", running=True)
    lc.stop("m")
    assert lc._states["m"].running is False


# -- custom downloads: validation only (no network) -------------------------
@pytest.mark.parametrize("engine,ref", [
    ("llamacpp", "not a repo"), ("native_onnx", "../etc"), ("ollama", "bad tag!"),
    ("llamacpp", ""),
])
def test_custom_rejects_bad_references(engine, ref):
    assert mm.pull_custom(engine, ref).ok is False


def test_custom_rejects_path_traversal_filename():
    assert mm.pull_custom("llamacpp", "owner/name", "../../x.gguf").ok is False


def test_custom_routes_to_the_right_downloader(monkeypatch):
    calls = {}
    monkeypatch.setattr(mm, "ollama_pull", lambda tag, progress=None: calls.setdefault("o", tag) or mm.DownloadResult(True, tag, "ollama"))
    monkeypatch.setattr(mm, "hf_download", lambda repo, f, progress=None: calls.setdefault("g", (repo, f)) and mm.DownloadResult(True, repo, "llamacpp"))
    monkeypatch.setattr(mm, "hf_download_onnx", lambda repo, f, progress=None, model_id="": calls.setdefault("n", (repo, f, model_id)) and mm.DownloadResult(True, repo, "native_onnx"))
    mm.pull_custom("ollama", "qwen3:4b")
    mm.pull_custom("llamacpp", "owner/name", "m.gguf")
    mm.pull_custom("native_onnx", "Owner/Name", "onnx/model_q4.onnx")
    assert calls["o"] == "qwen3:4b"
    assert calls["g"] == ("owner/name", "m.gguf")
    assert calls["n"] == ("Owner/Name", "onnx/model_q4.onnx", "native:custom-owner--name")


# -- auto-pick & remote ------------------------------------------------------
def test_best_native_model_respects_free_ram():
    assert mm.best_native_model(0.5) is None
    small, big = mm.best_native_model(2.5), mm.best_native_model(32)
    assert small.parameter_count_b < big.parameter_count_b


def test_remote_tier_validates_input_without_network():
    assert mm.add_remote_tier("bad host!", "m", check=False)["ok"] is False
    assert mm.add_remote_tier("10.0.0.5", "", check=False)["ok"] is False
    assert mm.add_remote_tier("10.0.0.5", "m", kind="x", check=False)["ok"] is False


def test_remote_tier_writes_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main

    main(["init", "--force"])
    r = mm.add_remote_tier("http://10.0.0.5", "qwen3:8b", tier="deep_tier", check=False)
    assert r["ok"] and r["base_url"] == "http://10.0.0.5:11434"
    from psycho_carla.core.config import load_config

    tier = load_config().intelligence.tiers["deep_tier"]
    assert tier.base_url == "http://10.0.0.5:11434" and tier.model == "qwen3:8b"


# -- exports -----------------------------------------------------------------
class _Row:
    def __init__(self, role, content):
        self.role, self.content, self.created_at = role, content, 0


class _Mem:
    def list_sessions(self, limit=50):
        return [{"session_id": "s1", "title": "T"}, {"session_id": "s2", "title": "empty"}]

    def get_conversation(self, sid, limit=100):
        return [_Row("user", "hi"), _Row("assistant", "hello")] if sid == "s1" else [_Row("user", "x")]


def test_markdown_and_json_export():
    from psycho_carla.core.exports import export_sessions

    md = export_sessions(_Mem(), "markdown")
    assert "**You:** hi" in md and "**C.A.R.L.A.:** hello" in md
    assert json.loads(export_sessions(_Mem(), "json"))[0]["messages"][1]["content"] == "hello"


def test_training_export_keeps_only_real_exchanges():
    from psycho_carla.core.exports import export_training_jsonl

    rows = [json.loads(x) for x in export_training_jsonl(_Mem()).splitlines()]
    assert len(rows) == 1 and rows[0]["messages"][0]["role"] == "user"


def test_api_routes_exist():
    from fastapi.testclient import TestClient

    from psycho_carla.server.app import create_app

    with TestClient(create_app(start_runtime=False)) as c:
        assert c.get("/api/models/device").status_code == 200
        assert c.post("/api/models/free").json()["ok"] is True
        assert c.get("/api/setup").json().keys() >= {"needs_setup", "native_pick"}
        assert c.post("/api/models/device", json={"device": "bogus"}).status_code == 400
        assert c.get("/api/export?format=nope").status_code == 400
        assert c.get("/api/export?format=training").status_code == 200
