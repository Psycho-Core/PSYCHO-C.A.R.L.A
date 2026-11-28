"""A web page you visit must not be able to drive the local server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from psycho_carla.server.app import create_app
from psycho_carla.server.origin import host_allowed, origin_allowed

EVIL = {"Origin": "http://evil.example"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main

    main(["init", "--force"])
    with TestClient(create_app()) as c:
        yield c


def test_origin_rules():
    for ok in ("http://127.0.0.1:8787", "http://localhost:3000", "https://localhost", "http://[::1]:8787",
               "http://127.0.0.5:1", ""):
        assert origin_allowed(ok), ok
    for bad in ("http://evil.example", "https://127.0.0.1.evil.example", "http://localhost.evil.example",
                "null", "file://", "http://evil.example:8787"):
        assert not origin_allowed(bad), bad
    assert origin_allowed("https://my.site", ["https://my.site"])
    assert not origin_allowed("https://other.site", ["https://my.site"])


def test_host_rules():
    for ok in ("127.0.0.1:8787", "localhost", "localhost:1", "[::1]:8787", "testserver", "app.localhost:9"):
        assert host_allowed(ok), ok
    for bad in ("attacker.example", "attacker.example:8787", "127.0.0.1.evil.example",
                "127.0.0.1.evil.example:8787", "", "0.0.0.0:80x", "localhost.evil.example"):
        assert not host_allowed(bad), bad


def test_hostile_preflight_gets_no_permission(client):
    r = client.options("/api/tools/shell_exec/execute", headers={
        **EVIL, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
    assert r.headers.get("access-control-allow-origin") is None


def test_hostile_page_cannot_run_a_tool(client):
    r = client.post("/api/tools/shell_exec/execute", json={"arguments": {"command": "echo hi"}}, headers=EVIL)
    assert r.status_code == 403 and "refused" in r.text


@pytest.mark.parametrize("path", ["/api/models/free", "/api/models/auto", "/api/restore"])
def test_hostile_simple_posts_without_preflight_are_blocked_too(client, path):
    """text/plain POSTs need no preflight — the guard must catch them."""
    r = client.post(path, content=b"x", headers={**EVIL, "Content-Type": "text/plain"})
    assert r.status_code == 403


def test_the_hud_itself_still_works(client):
    me = {"Origin": "http://127.0.0.1:8787"}
    assert client.get("/api/status", headers=me).status_code == 200
    r = client.post("/api/tools/calculator/execute", json={"arguments": {"expression": "2+2"}}, headers=me)
    assert r.status_code == 200 and r.json()["ok"]
    assert client.options("/api/status", headers={**me, "Access-Control-Request-Method": "GET"}).headers[
        "access-control-allow-origin"] == "http://127.0.0.1:8787"


def test_scripts_without_an_origin_header_still_work(client):
    r = client.post("/api/tools/calculator/execute", json={"arguments": {"expression": "1+1"}})
    assert r.status_code == 200


def test_dns_rebinding_host_is_refused(client):
    assert client.get("/api/status", headers={"Host": "attacker.example:8787"}).status_code == 400
    assert client.get("/api/status").status_code == 200


def test_websocket_from_a_foreign_page_is_refused(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws", headers=EVIL):
            pass
    assert exc.value.code == 4403
    with client.websocket_connect("/ws", headers={"Origin": "http://localhost:8787"}) as ws:
        assert ws.receive_json()["type"] in ("connected", "snapshot")


def test_star_in_config_is_ignored_on_loopback_but_listed_origins_work(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main
    from psycho_carla.core.config import load_config

    main(["init", "--force"])
    cfg = load_config()
    cfg.server.cors_origins = ["*", "https://my.dashboard"]
    with TestClient(create_app(cfg)) as c:
        assert c.post("/api/models/free", headers=EVIL).status_code == 403
        ok = c.options("/api/status", headers={"Origin": "https://my.dashboard",
                                               "Access-Control-Request-Method": "GET"})
        assert ok.headers["access-control-allow-origin"] == "https://my.dashboard"


def test_same_origin_requests_work_on_a_lan_bind(tmp_path, monkeypatch):
    """Served on a LAN address (with an API key) the HUD's own POSTs must not be refused."""
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main
    from psycho_carla.core.config import load_config

    main(["init", "--force"])
    cfg = load_config()
    cfg.server.host, cfg.server.api_key = "192.168.1.5", "k" * 24
    with TestClient(create_app(cfg)) as c:
        key = {"X-API-Key": "k" * 24}
        mine = {**key, "Origin": "http://192.168.1.5:8787", "Host": "192.168.1.5:8787"}
        assert c.post("/api/tools/calculator/execute", json={"arguments": {"expression": "1+1"}},
                      headers=mine).status_code == 200
        evil = {**key, "Origin": "http://evil.example", "Host": "192.168.1.5:8787"}
        assert c.post("/api/tools/calculator/execute", json={"arguments": {"expression": "1+1"}},
                      headers=evil).status_code == 403
