"""Regression tests for issues found in the code audit."""

from __future__ import annotations

import io
import stat
import sys
import textwrap
import threading
import time
import zipfile

import httpx
import pytest

from psycho_carla.security.ssrf import SSRFError, request_hook


# -- SSRF: an agent must not reach the local server through its own HTTP tool --
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8787/api/safe-mode", "http://localhost:8787/api/status", "http://[::1]:8787/",
    "http://192.168.1.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data/",
    "http://2130706433/", "http://0x7f.1/", "file:///etc/passwd", "ftp://example.com/x",
])
def test_http_request_refuses_local_and_private_targets(url):
    from psycho_carla.tools.builtin.extended import _check_url, http_request

    assert _check_url(url), url
    assert http_request("GET", url).startswith("Error")


def test_data_urls_still_work_for_the_browser_tools():
    from psycho_carla.tools.builtin.extended import _check_url

    assert _check_url("data:text/html,<h1>hi</h1>") is None


def test_a_redirect_to_the_local_server_is_refused_hop_by_hop():
    """Validating only the first URL lets 'public page -> 302 -> 127.0.0.1' through."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8787/api/safe-mode"})
        return httpx.Response(200, text="PWNED")

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                      event_hooks={"request": [request_hook]}) as c:
        with pytest.raises(SSRFError):
            c.get("http://93.184.216.34/")


def test_the_redirect_hook_is_wired_into_the_tools():
    import inspect

    from psycho_carla.tools.builtin import extended, web

    assert "request_hook" in inspect.getsource(extended._request)
    assert "request_hook" in inspect.getsource(web.web_fetch)


# -- llama-server: chatty stderr must never block it ---------------------------
CHATTY = textwrap.dedent('''\
    #!{python}
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    port = int(sys.argv[sys.argv.index("--port") + 1])
    sys.stderr.write("log line\\n" * 400000)      # ~3.6 MB: far more than a pipe buffer
    sys.stderr.flush()
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            b = b"{{}}"; self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b)
    HTTPServer(("127.0.0.1", port), H).serve_forever()
''')


@pytest.mark.skipif(sys.platform == "win32", reason="shebang script")
def test_chatty_server_logs_to_a_file_and_does_not_hang(tmp_path, monkeypatch):
    from psycho_carla.intelligence import llama_engine as le

    exe = tmp_path / "llama-server"
    exe.write_text(CHATTY.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setenv("PSYCHO_CARLA_LLAMA_SERVER", str(exe))
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path / "home"))
    srv = le.LlamaServer()
    try:
        r = srv.start(str(model), device="cpu", timeout=20)
        assert r["ok"], r                      # with an undrained PIPE this never became ready
        assert srv.log_path.stat().st_size > 1_000_000
    finally:
        srv.stop()


# -- in-process: unloading waits for an answer in progress ---------------------
def test_unload_waits_for_a_running_generation(monkeypatch):
    from psycho_carla.intelligence.gguf_inprocess import InProcessGguf

    eng = InProcessGguf()

    class Llm:
        closed = False

        def close(self):
            Llm.closed = True

    eng._llm = Llm()
    eng.model_path = "x"
    started = threading.Event()

    def generate():
        with eng._gen_lock:
            started.set()
            time.sleep(0.4)
            assert not Llm.closed             # nobody may close the model under us

    t = threading.Thread(target=generate)
    t.start()
    started.wait()
    assert eng.unload(wait=5) is True and Llm.closed
    t.join()


# -- backup: size limits --------------------------------------------------------
def test_zip_bomb_backup_is_refused(tmp_path, monkeypatch):
    from psycho_carla.core import backup

    monkeypatch.setattr(backup, "MAX_UNPACKED_BYTES", 1000)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BACKUP.txt", "x")
        z.writestr("data/memory.db", b"0" * 100_000)           # compresses to almost nothing
    r = backup.restore_backup(tmp_path, buf.getvalue())
    assert r["ok"] is False and "large" in r["message"]
    assert not (tmp_path / "data").exists()


def test_restore_endpoint_rejects_oversized_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from psycho_carla.cli.main import main
    from psycho_carla.server.app import create_app

    main(["init", "--force"])
    with TestClient(create_app()) as c:
        r = c.post("/api/restore", content=b"x", headers={"content-length": str(2 * 1024**3)})
        assert r.status_code in (413, 400)


# -- bugs found by static analysis, each confirmed by running it first -----------
def test_vram_detection_reads_the_real_hardware_summary(monkeypatch):
    """core.energy.hardware_summary never existed, so VRAM was silently always 0."""
    from psycho_carla.core import telemetry
    from psycho_carla.intelligence import gguf_inprocess, llama_engine

    monkeypatch.setattr(telemetry, "hardware_summary", lambda: {"vram_total_mb": 16384.0})
    assert llama_engine._vram_gb() == 16.0 and gguf_inprocess._vram_gb() == 16.0


def test_models_recommend_endpoint_sees_vram(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from fastapi.testclient import TestClient

    from psycho_carla.cli.main import main
    from psycho_carla.core import telemetry
    from psycho_carla.server.app import create_app

    main(["init", "--force"])
    monkeypatch.setattr(telemetry, "hardware_summary", lambda: {
        "vram_total_mb": 8192.0, "ram_total_mb": 32768.0})
    with TestClient(create_app()) as c:
        assert c.get("/api/models/recommend").json()["vram_gb"] == 8.0
        r = c.get("/v1/recommended-model")                  # used to be a 500 (missing ram_gb)
        assert r.status_code == 200 and r.json()["recommendations"]


def test_skill_install_from_git_and_url_no_longer_crashes(tmp_path, monkeypatch):
    from psycho_carla.skills.store import SkillStore

    store = SkillStore(tmp_path / "skills")
    seen = {}

    def fake_install(self, *args, overwrite=False):
        dst = args[-1]
        seen["overwrite"] = overwrite
        dst.mkdir(parents=True)
        (dst / "SKILL.md").write_text(
            f"---\nname: {dst.name}\ndescription: A demo skill for tests.\n---\n# Demo\n")

    monkeypatch.setattr(SkillStore, "_install_from_git", fake_install)
    monkeypatch.setattr(SkillStore, "_install_from_url", fake_install)
    store.install("https://github.com/x/demo.git")                 # TypeError before the fix
    assert seen["overwrite"] is False
    store.install("https://example.com/demo2.zip")


def test_reddit_login_helper_accepts_form_data(monkeypatch):
    from psycho_carla.channels import httpclient

    seen = {}
    monkeypatch.setattr(httpclient, "request", lambda url, **kw: seen.update(kw) or (200, "{}"))
    httpclient.request_json("https://x.example/token", method="POST", data=b"grant_type=x")
    assert seen["data"] == b"grant_type=x"


def test_channel_connect_tool_works():
    from psycho_carla.tools.builtin.runtime_tools import channel_connect

    assert "registered" in channel_connect("telegram")
    assert "unknown channel" in channel_connect("nonsense-channel")


def test_prompt_advice_works_once_traces_exist(tmp_path):
    from psycho_carla.learning.optimizer import PromptOptimizer
    from psycho_carla.learning.traces import TraceLogger

    traces = TraceLogger(tmp_path / "t.db")
    for i in range(3):
        traces.log_exchange(agent="a", prompt="p", response="Error: boom" if i else "fine", latency_ms=1.0)
    traces.flush() if hasattr(traces, "flush") else None
    out = PromptOptimizer(traces).advise("a")                      # AttributeError: Trace.error before
    assert out["sample_count"] == 3 and any("errored" in line for line in out["advice"])
    traces.close()


def test_native_menu_routes_match_what_the_hud_listens_for():
    from psycho_carla.desktop import DesktopApp

    app = DesktopApp()
    calls = []

    class Win:
        def evaluate_js(self, js):
            calls.append(("js", js))

        def load_url(self, url):
            calls.append(("url", url))

    app.window = Win()
    app._open("/models")                       # the menu passes '/models'; the HUD wants 'models'
    app._open("/voice")
    assert calls[0] == ("js", "window.location.hash = 'models';")
    assert calls[1][0] == "url" and calls[1][1].endswith("/voice")


def test_optimize_prompt_returning_nothing_is_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main
    from psycho_carla.core.config import load_config
    from psycho_carla.core.runtime import CarlaRuntime

    main(["init", "--force"])
    rt = CarlaRuntime(load_config())
    monkeypatch.setattr(rt.optimizer, "optimize_prompt", lambda **k: None)
    out = rt.optimize("prompt")                                    # AttributeError on None before
    assert out["prompt"] is None
