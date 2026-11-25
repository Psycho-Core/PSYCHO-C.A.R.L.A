"""Safe mode: risky tools wait for the owner; an agent cannot approve itself."""

from __future__ import annotations

import pytest

from psycho_carla.core.config import load_config
from psycho_carla.core.runtime import CarlaRuntime


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    from psycho_carla.cli.main import main

    main(["init", "--force"])
    from psycho_carla.core.state import clear_runtime, set_runtime
    from psycho_carla.tools.builtin import files

    old = files.get_sandbox()
    runtime = CarlaRuntime(load_config())
    files.configure_file_sandbox(tmp_path)      # after the runtime, which sets its own
    set_runtime(runtime)
    yield runtime
    clear_runtime()
    files._SANDBOX = old


def test_safe_mode_is_on_by_default(rt):
    assert rt.config.security.enforce_tool_confirmation is True


def test_risky_tool_is_queued_not_run(rt, tmp_path):
    target = tmp_path / "x.txt"
    res = rt.tools.execute("file_write", {"path": str(target), "content": "hi"})
    assert res.ok is False and "approval" in res.error
    assert not target.exists()
    pending = rt.approvals.pending()
    assert len(pending) == 1 and pending[0]["tool"] == "file_write"


def test_harmless_tools_are_not_gated(rt):
    assert rt.tools.execute("calculator", {"expression": "2+2"}).ok is True
    assert rt.approvals.pending() == []


def test_owner_approval_runs_it_exactly_once(rt, tmp_path):
    target = tmp_path / "ok.txt"
    rt.tools.execute("file_write", {"path": str(target), "content": "approved"})
    action_id = rt.approvals.pending()[0]["id"]
    done = rt.approvals.decide(
        action_id, True, executor=lambda t, a: rt.tools.execute(t, a, approved=True).as_message())
    assert done.status == "executed" and target.read_text() == "approved"
    assert rt.approvals.decide(action_id, True).status == "executed"     # no second run


def test_denied_request_never_runs(rt, tmp_path):
    target = tmp_path / "no.txt"
    rt.tools.execute("file_write", {"path": str(target), "content": "x"})
    rt.approvals.decide(rt.approvals.pending()[0]["id"], False)
    assert not target.exists()


def test_run_tool_cannot_bypass_the_gate(rt, tmp_path):
    from psycho_carla.tools.builtin.runtime_tools import run_tool

    out = run_tool("file_write", '{"path": "%s", "content": "x"}' % (tmp_path / "b.txt"))
    assert "NOT run" in out and not (tmp_path / "b.txt").exists()


def test_agent_cannot_approve_its_own_request(rt):
    from psycho_carla.tools.builtin.runtime_tools import approval_action

    rt.tools.execute("shell_exec", {"command": "echo hi"})
    action_id = rt.approvals.pending()[0]["id"]
    assert "only be granted by the owner" in approval_action(action_id, "approve")
    assert rt.approvals.pending()[0]["status"] == "pending"
    assert "rejected" in approval_action(action_id, "reject") or "denied" in approval_action(action_id, "reject")


def test_safe_mode_can_be_switched_off(rt, tmp_path):
    rt.config.security.enforce_tool_confirmation = False
    target = tmp_path / "free.txt"
    assert rt.tools.execute("file_write", {"path": str(target), "content": "y"}).ok
    assert target.read_text() == "y"


def test_non_tool_approvals_do_not_execute(rt):
    entry = rt.approvals.queue("ABC123", kind="channel", reason="pairing")
    done = rt.approvals.decide(entry.id, True, executor=lambda t, a: 1 / 0)
    assert done.status == "approved"                        # executor skipped, no crash


def test_channel_and_spawn_tools_queue_without_crashing(rt):
    from psycho_carla.tools.builtin.runtime_tools import agent_spawn, channel_approve

    assert "approval" in agent_spawn("claude --help")
    assert "Queued" in channel_approve("XYZ")
