"""Upstream /v1 route families against a live (in-process) runtime."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psycho_carla.core.config import config_from_dict  # noqa: E402
from psycho_carla.core.runtime import CarlaRuntime  # noqa: E402
from psycho_carla.providers.base import GenerationResult, Usage  # noqa: E402
from psycho_carla.server.app import create_app  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PSYCHO_CARLA_HOME", td)
        cfg = config_from_dict(
            {
                "intelligence": {"default_tier": "fast_tier"},
                "tiers": {
                    "fast_tier": {"provider": "ollama", "model": "test-fast"},
                    "deep_tier": {"provider": "ollama", "model": "test-deep"},
                },
                "agent": {"default_agent": "orchestrator"},
                "voice": {"enabled": False},
                "tools": {"mcp": {"enabled": False}},
            },
            config_path=Path(td) / "psycho_carla.toml",
        )
        runtime = CarlaRuntime(cfg, with_voice=False, with_mcp=False)

        class FakeAdapter:
            name = "ollama"

            def generate(self, messages, capability_tier="fast_tier",
                         stream=False, **kw):
                return GenerationResult(
                    text="All good, Boss.",
                    usage=Usage(prompt_tokens=5, completion_tokens=5),
                    model="fake", provider="ollama", tier=capability_tier,
                )

            def health(self):
                return True, "ok"

            def list_models(self):
                return []

        runtime.router.get_adapter = lambda provider, tier: FakeAdapter()
        app = create_app(cfg, runtime=runtime, start_runtime=False)
        with TestClient(app) as test_client:
            yield test_client
        runtime.stop()


def test_health_and_info(client):
    assert client.get("/health").status_code == 200
    info = client.get("/v1/info").json()
    assert info["name"] == "PSYCHO-C.A.R.L.A."
    assert "orchestrator" in info["agents"]


def test_v1_agents_list_and_message(client):
    agents = client.get("/v1/agents").json()
    assert agents["count"] >= 26
    reply = client.post("/v1/agents/orchestrator/message",
                        json={"message": "status?"}).json()
    assert reply["text"] == "All good, Boss."


def test_v1_custom_agent_lifecycle(client):
    created = client.post("/v1/agents", json={
        "name": "greeter", "system_prompt": "Be warm.",
    })
    assert created.status_code == 200
    assert client.delete("/v1/agents/orchestrator").status_code == 404
    assert client.delete("/v1/agents/greeter").status_code == 200


def test_v1_memory_round_trip(client):
    stored = client.post("/v1/memory/store",
                         json={"content": "Boss likes black coffee"}).json()
    assert stored["stored"]
    found = client.post("/v1/memory/search",
                        json={"query": "coffee"}).json()
    assert found["count"] >= 1
    assert client.get("/v1/memory/stats").status_code == 200
    assert client.get("/v1/memory/config").json()["enabled"] in (True, False)


def test_v1_traces_and_feedback(client):
    client.post("/v1/agents/orchestrator/message", json={"message": "hi"})
    traces = client.get("/v1/traces").json()
    assert traces["count"] >= 1
    trace_id = traces["traces"][0]["id"]
    assert client.get(f"/v1/traces/{trace_id}").status_code == 200
    rated = client.post("/v1/feedback",
                        json={"trace_id": trace_id, "score": 1.0})
    assert rated.status_code == 200


def test_v1_telemetry_and_metrics(client):
    assert client.get("/v1/telemetry/stats").status_code == 200
    assert client.get("/v1/telemetry/energy").status_code == 200
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "carla_requests_total" in metrics.text


def test_v1_budget(client):
    client.put("/v1/budget/limits", json={"max_tokens_per_day": 1000})
    budget = client.get("/v1/budget").json()
    assert budget["limits"]["max_tokens_per_day"] == 1000


def test_v1_savings_shape(client):
    savings = client.get("/v1/savings").json()
    for key in ("assumed_hosted_usd", "estimated_saving_usd", "assumptions"):
        assert key in savings


def test_v1_connectors_catalogue(client):
    connectors = client.get("/v1/connectors").json()
    ids = {c["id"] for c in connectors["connectors"]}
    assert {"gmail", "obsidian", "hackernews", "notion"} <= ids
    one = client.get("/v1/connectors/obsidian").json()
    assert one["id"] == "obsidian"
    ingest = client.post("/v1/connectors/upload/ingest",
                         json={"text": "Quarterly plan: ship it.",
                               "title": "Plan"})
    assert ingest.status_code == 200


def test_v1_approvals_flow(client):
    # queue via runtime through a managed run is overkill; use pending list
    pending = client.get("/v1/approvals/pending").json()
    assert pending["count"] == 0
    assert client.post("/v1/approvals/nope/approve").status_code == 404


def test_v1_security_scan(client):
    scan = client.get("/v1/security/scan",
                      params={"text": "key AKIAIOSFODNN7EXAMPLE"}).json()
    assert not scan["scan"]["clean"]


def test_v1_skills_and_sessions(client):
    skills = client.get("/v1/skills").json()
    assert "skills" in skills
    sessions = client.get("/v1/sessions").json()
    assert "sessions" in sessions


def test_v1_optimize_runs(client):
    run = client.post("/v1/optimize/runs", json={"surface": "routing"}).json()
    assert run["status"] == "complete"
    runs = client.get("/v1/optimize/runs").json()
    assert runs["count"] == 1
    assert client.get(f"/v1/optimize/runs/{run['id']}").status_code == 200


def test_v1_learning_endpoints(client):
    assert client.get("/v1/learning/stats").status_code == 200
    assert client.get("/v1/learning/policy").status_code == 200


def test_v1_analytics(client):
    identity = client.get("/v1/analytics/identity").json()
    assert identity["anon_id"]
    pref = client.post("/v1/analytics/preference",
                       json={"key": "theme", "value": "dark"}).json()
    assert pref["preferences"]["theme"] == "dark"


def test_v1_channels_aliases(client):
    channels = client.get("/v1/channels").json()
    assert channels["status"] == "disconnected"


def test_managed_agents_crud(client):
    created = client.post("/v1/managed-agents",
                          json={"name": "watcher", "agent": "monitor_operative"})
    assert created.status_code == 200
    agent_id = created.json()["id"]
    assert client.get(f"/v1/managed-agents/{agent_id}").status_code == 200
    task = client.post(f"/v1/managed-agents/{agent_id}/tasks",
                       json={"title": "check", "prompt": "check the thing"})
    assert task.status_code == 200
    paused = client.post(f"/v1/managed-agents/{agent_id}/pause")
    assert paused.json()["status"] == "paused"
    assert client.delete(f"/v1/managed-agents/{agent_id}").status_code == 200


def test_digest_schedule_and_research_validation(client):
    schedule = client.post("/api/digest/schedule",
                           json={"cron": "15 6 * * *", "enabled": True})
    assert schedule.json()["schedule"] == "15 6 * * *"
    assert client.get("/api/digest/history").json()["count"] == 0
    assert client.post("/api/research", json={}).status_code == 400


def test_dashboard_and_comparison_pages(client):
    assert client.get("/dashboard").status_code == 200
    comparison = client.get("/comparison")
    assert comparison.status_code == 200
    assert "native:smollm2-360m" in comparison.text


def test_webhook_without_bridge_is_honest(client):
    result = client.post("/webhooks/twilio",
                         data={"From": "+1", "Body": "hello", "To": "+2"})
    assert result.status_code == 200
    assert result.json()["delivered"] is False
