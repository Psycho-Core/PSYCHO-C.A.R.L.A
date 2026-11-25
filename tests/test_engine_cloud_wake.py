"""Managed llama.cpp engine, GGUF catalog, OpenRouter, and the wake word."""

from __future__ import annotations

import stat
import sys
import textwrap
import zipfile

import numpy as np
import pytest

from psycho_carla.intelligence import llama_engine as le
from psycho_carla.intelligence import model_manager as mm
from psycho_carla.voice.stt import TranscriptWakeDetector, match_wake_phrase

FAKE_SERVER = textwrap.dedent('''\
    #!{python}
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    args = sys.argv
    port = int(args[args.index("--port") + 1])
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            self._send({{"status": "ok", "data": []}})
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
            self._send({{"id": "x", "model": "local", "choices": [{{"index": 0, "finish_reason": "stop",
                "message": {{"role": "assistant", "content": "pong from fake llama-server"}}}}],
                "usage": {{"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}}})
    HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("fake engine uses a shebang script")
    exe = tmp_path / "llama-server"
    exe.write_text(FAKE_SERVER.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "tiny.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 1024)
    monkeypatch.setenv("PSYCHO_CARLA_LLAMA_SERVER", str(exe))
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path / "home"))
    yield exe, model
    le.server.stop()


# -- engine plumbing ---------------------------------------------------------
ASSETS = [
    {"name": "llama-b9999-bin-win-cuda-12.4-x64.zip"},
    {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip"},
    {"name": "llama-b9999-bin-win-vulkan-x64.zip", "browser_download_url": "u"},
    {"name": "llama-b9999-bin-win-cpu-x64.zip"},
    {"name": "llama-b9999-bin-ubuntu-vulkan-x64.tar.gz"},
]


def test_pick_asset_matches_backend_and_os():
    assert le.pick_asset(ASSETS, "vulkan", "windows")["name"].endswith("win-vulkan-x64.zip")
    assert "cpu" in le.pick_asset(ASSETS, "cpu", "windows")["name"]
    assert "ubuntu" in le.pick_asset(ASSETS, "vulkan", "linux")["name"]
    assert le.pick_asset(ASSETS, "hip", "windows") is None


def test_safe_extract_blocks_zip_slip(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(ValueError):
        le._safe_extract(bad, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_gpu_layer_choice():
    assert le.auto_gpu_layers(2.0, 16, backend="vulkan") == 999      # fits
    assert le.auto_gpu_layers(9.0, 0, backend="vulkan") == 999       # 14B on unknown 16GB card
    partial = le.auto_gpu_layers(19.9, 0, backend="vulkan")          # 32B: partial offload
    assert 0 < partial < 64
    assert le.auto_gpu_layers(2.0, 16, backend="cpu") == 0


def test_build_args_binds_localhost_only(tmp_path):
    args = le.build_args(tmp_path / "x", tmp_path / "m.gguf", 1234, ctx=2048, threads=4, gpu_layers=7)
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("-ngl") + 1] == "7" and args[args.index("-t") + 1] == "4"


def test_start_without_engine_or_model_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PSYCHO_CARLA_LLAMA_SERVER", raising=False)
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    monkeypatch.setattr(le.shutil, "which", lambda n: None)
    assert "not found" in le.LlamaServer().start(str(tmp_path / "nope.gguf"))["message"]
    m = tmp_path / "m.gguf"
    m.write_bytes(b"x")
    assert "not installed" in le.LlamaServer().start(str(m))["message"]


def test_real_process_lifecycle_and_adapter(fake_engine):
    exe, model = fake_engine
    r = le.server.start(str(model), device="cpu", timeout=30)
    assert r["ok"], r
    assert le.server.running and le.server.base_url.startswith("http://127.0.0.1:")
    assert le.server.start(str(model), device="cpu")["already_running"]

    from psycho_carla.providers.adapters.native_gguf import NativeGgufAdapter
    from psycho_carla.providers.base import Message

    ad = NativeGgufAdapter({"model_path": str(model), "device": "cpu"})
    assert ad.health() == (True, "ready")
    out = ad.generate([Message(role="user", content="ping")], "fast_tier")
    assert "pong from fake llama-server" in out.text
    assert le.server.stop() is True and not le.server.running


def test_lifecycle_start_stop_free_for_gguf(fake_engine, monkeypatch):
    exe, model = fake_engine
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle

    monkeypatch.setattr(mm, "find_gguf", lambda mid: model)
    lc = ModelLifecycle()
    started = lc.start("tiny")
    assert started["ok"], started
    assert lc.status()["engine"] == "native_gguf"
    assert lc.stop("tiny")["was_running"] is True
    assert lc.status()["running"] is False
    lc.start("tiny")
    monkeypatch.setattr(ModelLifecycle, "_evict_ollama", staticmethod(lambda: []))
    assert lc.free_memory()["released"] == ["tiny"]
    assert not le.server.running


# -- catalog -----------------------------------------------------------------
def test_gguf_catalog_targets_the_hardware():
    from psycho_carla.intelligence.model_catalog import GGUF_MODELS, list_models

    ids = {m.model_id for m in GGUF_MODELS}
    assert {"gguf:qwen2.5-coder-14b-uncensored", "gguf:qwen2.5-coder-32b-uncensored",
            "gguf:llama-3.2-3b-uncensored"} <= ids
    assert all(m.hf_repo and m.gguf_file.endswith(".gguf") for m in GGUF_MODELS)
    assert {m.model_id for m in list_models(engine="native_gguf")} == ids
    # An 8 GB OptiPlex: the 3B uncensored model fits, the 14B does not.
    optiplex = mm.best_gguf_model(free_ram_gb=6.5, prefer_uncensored=True)
    assert optiplex.model_id == "gguf:llama-3.2-3b-uncensored"
    # The 32 GB main PC: auto-pick stays at <=14B by default.
    assert mm.best_gguf_model(free_ram_gb=26).model_id == "gguf:qwen2.5-coder-14b-uncensored"
    assert mm.best_gguf_model(free_ram_gb=26, max_params_b=32).parameter_count_b == 32


def test_gguf_download_and_custom_validation(monkeypatch):
    seen = {}
    monkeypatch.setattr(mm, "hf_download", lambda repo, f, progress=None: seen.setdefault("a", (repo, f)) and mm.DownloadResult(True, repo, "llamacpp"))
    assert mm.pull_model("gguf:qwen2.5-coder-14b", "native_gguf").engine == "native_gguf"
    assert seen["a"][0] == "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF"
    assert mm.pull_model("not-a-model", "native_gguf").ok is False
    assert mm.pull_custom("native_gguf", "owner/name", "").ok is False          # needs the .gguf name
    assert mm.pull_custom("native_gguf", "owner/name", "m.gguf").ok is True


def test_find_and_activate_gguf(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main

    main(["init", "--force"])
    f = mm.models_dir() / "Qwen2.5-Coder-14B-Instruct-abliterated.Q4_K_M.gguf"
    f.write_bytes(b"x")
    assert mm.find_gguf("gguf:qwen2.5-coder-14b-uncensored") == f
    assert mm.activate_gguf_model("gguf:qwen2.5-coder-14b-uncensored", "deep_tier")["ok"]
    from psycho_carla.core.config import load_config

    t = load_config().intelligence.tiers["deep_tier"]
    assert t.provider == "native_gguf" and t.extra["model_path"] == str(f)
    assert any(m["engine"] == "native_gguf" for m in mm.local_models())


def test_starter_config_has_no_ollama_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main
    from psycho_carla.core.config import load_config

    main(["init", "--force"])
    cfg = load_config()
    assert {t.provider for t in cfg.intelligence.tiers.values()} == {"native_gguf"}
    assert cfg.voice.wake_word_engine == "transcript"


# -- OpenRouter --------------------------------------------------------------
def test_openrouter_parse_and_adapter():
    from psycho_carla.providers.adapters.openrouter import OpenRouterAdapter, parse_models

    models = parse_models({"data": [
        {"id": "b/paid", "name": "Paid", "pricing": {"prompt": "0.001", "completion": "0.002"}},
        {"id": "a/free", "name": "Free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "c/odd", "pricing": {}},
    ]})
    assert [m["id"] for m in models][0] == "a/free" and models[0]["free"] is True
    assert next(m for m in models if m["id"] == "c/odd")["free"] is False
    ad = OpenRouterAdapter({"api_key": "k"})
    assert ad.base_url == "https://openrouter.ai/api/v1" and ad.headers["X-Title"]


def test_openrouter_health_needs_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from psycho_carla.providers.adapters.openrouter import OpenRouterAdapter

    ok, detail = OpenRouterAdapter({}).health()
    assert ok is False and "key" in detail


def test_cloud_tier_stores_key_in_vault_not_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from psycho_carla.cli.main import main

    main(["init", "--force"])
    r = mm.add_cloud_tier("openrouter", "qwen/qwen-2.5-coder-32b-instruct", "deep_tier", "sk-or-SECRET123")
    assert r["ok"]
    assert "SECRET123" not in (tmp_path / "psycho_carla.toml").read_text()
    from psycho_carla.providers.adapters.openrouter import stored_key

    assert stored_key() == "sk-or-SECRET123"
    assert mm.add_cloud_tier("openrouter", "", "deep_tier")["ok"] is False
    assert mm.add_cloud_tier("somewhere", "m")["ok"] is False


# -- wake word ---------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "hey carla", "Hey, Carla!", "hey karla", "hay carlo", "hi carla", "okay carla",
])
def test_wake_phrase_variants_match(text):
    assert match_wake_phrase(text)[0] is True


@pytest.mark.parametrize("text", [
    "hey jarvis", "carla is my friend's name", "what a lovely day", "", "hey there",
])
def test_wake_phrase_rejects_others(text):
    assert match_wake_phrase(text)[0] is False


def test_wake_phrase_extracts_the_command():
    ok, score, rest = match_wake_phrase("Hey Carla, what time is it?")
    assert ok and rest == "what time is it"


class _FakeSTT:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def available(self):
        return True

    def transcribe(self, audio, sr=16000):
        self.calls += 1
        return self.text


def _feed(det, speech_s=1.0, silence_s=1.0, sr=16000):
    events = []
    loud = (np.random.default_rng(0).standard_normal(1280) * 0.2).astype(np.float32)
    quiet = np.zeros(1280, dtype=np.float32)
    for _ in range(int(speech_s * sr / 1280)):
        events.append(det.detect(loud, sr))
    for _ in range(int(silence_s * sr / 1280)):
        events.append(det.detect(quiet, sr))
    return events


def test_transcript_wake_fires_after_a_pause_with_the_phrase():
    det = TranscriptWakeDetector(_FakeSTT("hey carla open my notes"))
    hits = [e for e in _feed(det) if e.detected]
    assert len(hits) == 1 and hits[0].remainder == "open my notes"


def test_transcript_wake_ignores_other_speech_and_silence():
    stt = _FakeSTT("hey jarvis")
    det = TranscriptWakeDetector(stt)
    assert not any(e.detected for e in _feed(det))
    stt2 = _FakeSTT("hey carla")
    det2 = TranscriptWakeDetector(stt2)
    assert not any(e.detected for e in _feed(det2, speech_s=0, silence_s=2))
    assert stt2.calls == 0          # silence is never transcribed


def test_openwakeword_needs_a_custom_model():
    from psycho_carla.voice.stt import WakeWordDetector

    assert WakeWordDetector("hey carla").available() is False


# -- API ---------------------------------------------------------------------
def test_new_api_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    monkeypatch.delenv("PSYCHO_CARLA_LLAMA_SERVER", raising=False)
    from fastapi.testclient import TestClient

    from psycho_carla.cli.main import main
    from psycho_carla.providers.adapters import openrouter
    from psycho_carla.server.app import create_app

    main(["init", "--force"])
    monkeypatch.setattr(openrouter, "list_openrouter_models", lambda k="": [{"id": "a/free", "free": True}])
    with TestClient(create_app(start_runtime=False)) as c:
        assert c.get("/api/engine").json().keys() >= {"installed", "backends", "server"}
        assert c.get("/api/models/cloud/openrouter?free_only=true").json()["models"][0]["id"] == "a/free"
        assert c.post("/api/models/cloud", json={"model": ""}).status_code == 400
        assert c.post("/api/engine/install", json={"backend": "bogus"}).status_code == 400
        assert c.post("/api/voice/select", json={"label": "nope"}).status_code == 400
        assert c.post("/api/voice/select", json={"label": "kokoro_bf_emma_gb_female"}).json()["ok"]
        assert c.get("/api/models?engine=native_gguf").json()["count"] == 7
    from psycho_carla.core.config import load_config

    assert load_config().voice.tts_voice == "bf_emma"


# -- downloads, partials and deletion ----------------------------------------
class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status = data, status
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n=-1):
        out, self._d = self._d[:n], self._d[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_goes_to_part_then_renames(tmp_path, monkeypatch):
    monkeypatch.setattr(mm.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"GGUF" + b"x" * 100))
    out = tmp_path / "m.gguf"
    assert mm._download_resumable("http://x/m.gguf", out, lambda *a: None) == ""
    assert out.read_bytes()[:4] == b"GGUF" and not (tmp_path / "m.gguf.part").exists()


def test_failed_download_leaves_only_a_part_file_and_resumes(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection reset")
        if req.get_header("Range"):
            return _Resp(b"y" * 50, status=206)
        return _Resp(b"x" * 100)

    monkeypatch.setattr(mm.urllib.request, "urlopen", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = tmp_path / "m.gguf"
    (tmp_path / "m.gguf.part").write_bytes(b"x" * 50)           # an earlier partial
    assert mm._download_resumable("http://x/m.gguf", out, lambda *a: None) == ""
    assert out.stat().st_size == 100                              # 50 kept + 50 resumed
    # a download that never succeeds must not create the final file
    monkeypatch.setattr(mm.urllib.request, "urlopen", lambda r, timeout=0: (_ for _ in ()).throw(OSError("down")))
    out2 = tmp_path / "n.gguf"
    assert "resume" in mm._download_resumable("http://x/n.gguf", out2, lambda *a: None, attempts=2)
    assert not out2.exists()


def test_partials_are_listed_and_deletable(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    base = mm.models_dir()
    (base / "a.gguf.part").write_bytes(b"123")
    hub = base / ".cache" / "huggingface" / "download"
    hub.mkdir(parents=True)
    (hub / "b.gguf.incomplete").write_bytes(b"4567")
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle

    lc = ModelLifecycle()
    ids = {p["id"] for p in lc.partial_downloads()}
    assert ids == {"file:a.gguf.part", "file:.cache/huggingface/download/b.gguf.incomplete"}
    assert lc.delete_partial("file:a.gguf.part")["ok"]
    assert not (base / "a.gguf.part").exists() and (hub / "b.gguf.incomplete").exists()
    assert lc.delete_partial("")["removed"]                       # clear all
    assert lc.partial_downloads() == []
    assert mm.delete_partial_gguf("file:../../etc/passwd")["removed"] == []


def test_delete_complete_gguf_and_refuse_outside(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    f = mm.models_dir() / "repo--x" / "model.Q4_K_M.gguf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"GGUF")
    assert mm.delete_local_model("model.Q4_K_M", "native_gguf").ok
    assert not f.exists() and not f.parent.exists()               # tidy empty folder
    assert mm.delete_local_model("model.Q4_K_M", "native_gguf").ok is False


def test_delete_stops_the_engine_first(fake_engine, tmp_path, monkeypatch):
    exe, model = fake_engine
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path / "home"))
    target = mm.models_dir() / "running.gguf"
    target.write_bytes(model.read_bytes())
    assert le.server.start(str(target), device="cpu", timeout=30)["ok"]
    assert mm.delete_local_model("running", "native_gguf").ok
    assert not le.server.running and not target.exists()


def test_marker_release_without_binaries_is_skipped():
    """Real GitHub: llama.cpp's /releases/latest held only 'nightly-tag.txt'."""
    releases = [
        {"tag_name": "v1.0.0", "assets": [{"name": "nightly-tag.txt"}]},
        {"tag_name": "b9001", "draft": True, "assets": ASSETS},
        {"tag_name": "b9000", "assets": ASSETS},
    ]
    rel, asset = le.find_release_asset(releases, "cpu", "windows")
    assert rel["tag_name"] == "b9000" and "win-cpu" in asset["name"]
    assert le.find_release_asset(releases[:1], "cpu", "windows") == (None, None)
    assert "nightly-tag.txt" in le.describe_releases(releases)
