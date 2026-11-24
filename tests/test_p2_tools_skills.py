"""P2 depth: extended tools, bundled skills + sources, sandbox, eval depth."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psycho_carla.tools.registry import get_registry, load_builtin_tools  # noqa: E402

load_builtin_tools()
REG = get_registry()


def run_tool(tool_name, **args):
    result = REG.execute(tool_name, args)
    assert result.ok, f"{tool_name} failed: {result.error}"
    return result.content


# --------------------------------------------------------------------------- #
# Extended tools
# --------------------------------------------------------------------------- #

def test_tool_surface_covers_audit_list():
    names = set(REG.names())
    audit_names = {
        "git_status", "git_diff", "git_log", "git_commit", "browser_navigate",
        "browser_extract", "browser_click", "browser_type", "browser_axtree",
        "browser_screenshot", "kg_add_entity", "kg_add_relation", "kg_query",
        "kg_neighbors", "agent_spawn", "agent_send", "agent_list", "agent_kill",
        "queue_action", "get_pending_actions", "execute_pending_actions",
        "record_decision", "check_permission", "channel_list", "channel_send",
        "channel_status", "channel_message", "channel_connect",
        "channel_approve", "apply_patch", "db_query", "pdf_extract",
        "image_generate", "audio_transcribe", "text_to_speech", "llm", "llm_generate",
        "think", "get_weather", "calendar_search", "calendar_upcoming",
        "fx_macro_data", "docker_shell_exec", "code_interpreter_docker",
        "digest_collect", "user_profile_manage", "skill_manage", "memory_index",
        "memory_manage", "memory_retrieve", "retrieval", "scan_chunks",
        "subagent_spawn", "subagent_list", "subagent_collect", "delegate",
        "delegate_to_coworker", "run_tool", "approval_action", "knowledge_search",
        "voice_speak", "repl", "http_request", "code_interpreter",
        "memory_store", "memory_search", "memory_get", "memory_list",
        "memory_forget", "skills_list", "agent_message", "agent_browser",
        "local_tool_autonomy",
    }
    assert audit_names <= names, f"missing: {sorted(audit_names - names)}"
    assert len(names) >= 80


def test_db_query_readonly(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE items (name TEXT, qty INT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", [("pen", 3), ("pad", 5)])
    conn.commit()
    conn.close()
    out = run_tool("db_query", database=str(db), query="SELECT name FROM items ORDER BY qty")
    assert "pen" in out and "pad" in out
    refused = run_tool("db_query", database=str(db), query="DELETE FROM items")
    assert refused.startswith("Error:")


def test_apply_patch(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("one\ntwo\nthree\n")
    patch = (
        "--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
    )
    out = run_tool("apply_patch", patch=patch, root=str(tmp_path))
    assert "a.txt" in out
    assert target.read_text() == "one\nTWO\nthree\n"


def test_repl_persistence_and_think():
    assert "42" in run_tool("repl", code="zz = 20\nprint(zz + 22)", reset=True)
    assert run_tool("repl", code="print(zz + 1)") == "21"  # namespace persisted
    assert "Noted" in run_tool("think", thought="test thought")


def test_git_tools(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n")
    status = run_tool("git_status", path=str(tmp_path))
    assert "f.txt" in status
    run_tool("git_commit", message="init", path=str(tmp_path))
    log = run_tool("git_log", path=str(tmp_path))
    assert "init" in log


def test_pdf_extract(tmp_path):
    text = "Hello PDF world"
    stream = f"BT ({text}) Tj ET".encode()
    content = zlib.compress(stream)
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Length " + str(len(content)).encode()
        + b">>stream\n" + content + b"\nendstream endobj\n%%EOF"
    )
    out = run_tool("pdf_extract", path=str(pdf))
    assert "Hello PDF world" in out


def test_browser_no_page_errors():
    out = run_tool("browser_extract")
    assert out.startswith("Error:")


def test_image_generate_placeholder(tmp_path):
    out = run_tool("image_generate", prompt="cobalt orb",
                   path=str(tmp_path / "img.png"))
    assert (tmp_path / "img.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "placeholder" in out.lower()


def test_sandbox_subprocess():
    from psycho_carla.sandbox import DockerSandbox, WasmSandbox, run_python

    result = run_python("print(6 * 7)")
    assert result.ok and result.output == "42"
    docker_ok, _ = DockerSandbox().available()
    wasm_ok, _ = WasmSandbox().available()
    assert isinstance(docker_ok, bool) and isinstance(wasm_ok, bool)


def test_scan_chunks():
    out = run_tool("scan_chunks", text="nothing suspicious here")
    assert "No scanner findings" in out
    out = run_tool("scan_chunks", text='password = "[SECURITY_DATA]"')
    assert "secret" in out.lower() or "credential" in out.lower()


# --------------------------------------------------------------------------- #
# Runtime-bound tools
# --------------------------------------------------------------------------- #

@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    from psycho_carla.core.config import config_from_dict
    from psycho_carla.core.runtime import CarlaRuntime
    from psycho_carla.core.state import set_runtime

    monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
    cfg = config_from_dict(
        {
            "intelligence": {"default_tier": "fast_tier"},
            "tiers": {"fast_tier": {"provider": "ollama", "model": "test"}},
            "voice": {"enabled": False},
            "tools": {"mcp": {"enabled": False}},
        },
        config_path=tmp_path / "psycho_carla.toml",
    )
    rt = CarlaRuntime(cfg, with_voice=False, with_mcp=False)
    set_runtime(rt)
    yield rt
    set_runtime(None)


def test_kg_tools(runtime):
    assert "recorded" in run_tool("kg_add_entity", name="Boss", kind="person")
    run_tool("kg_add_relation", source="Boss", relation="owns", target="Carla")
    out = run_tool("kg_neighbors", name="Boss")
    assert "Carla" in out
    assert "Boss" in run_tool("kg_query", query="Boss")


def test_memory_index_and_manage(runtime, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.txt").write_text("The cobalt protocol uses harmonic relays.")
    out = run_tool("memory_index", path=str(docs))
    assert "Indexed" in out
    hits = run_tool("memory_retrieve", query="cobalt protocol")
    assert "cobalt" in hits.lower()
    listed = run_tool("memory_manage", action="list")
    assert "cobalt" in listed.lower()


def test_action_queue(runtime):
    run_tool("queue_action", action="Check the server logs", reason="test")
    pending = run_tool("get_pending_actions")
    assert "server logs" in pending
    assert "Permitted" in run_tool("check_permission", action="read a file")
    assert "Decision recorded" in run_tool("record_decision", decision="proceed")


def test_agent_sessions_honest_empty(runtime):
    out = run_tool("agent_list")
    assert "No live agent sessions" in out


def test_memory_delete_live(runtime):
    stored = run_tool("memory_store", content="ephemeral fact xyz")
    mem_id = int("".join(ch for ch in stored if ch.isdigit()) or "0")
    if mem_id:
        assert "Deleted" in run_tool("memory_delete", memory_id=mem_id)


def test_user_profile(runtime):
    run_tool("user_profile_manage", action="set", content="Boss prefers dark UI.")
    out = run_tool("user_profile_manage", action="get")
    assert "dark UI" in out


# --------------------------------------------------------------------------- #
# Skills: bundled library, executor, sources
# --------------------------------------------------------------------------- #

def test_bundled_skills_install_and_run(tmp_path):
    from psycho_carla.skills import SkillStore
    from psycho_carla.skills.executor import SkillExecutor
    from psycho_carla.skills.sources import ensure_bundled

    store = SkillStore(tmp_path / "skills")
    installed = ensure_bundled(store)
    assert len(installed) == 20
    assert len(store.catalog) == 20
    out = SkillExecutor(REG).run(
        store, "code-lint", {"file_path": "src/psycho_carla/bench/scorers.py"})
    # file_read resolves relative to cwd; run from repo root if needed.
    if "ERROR" in out.splitlines()[1]:
        root = Path(__file__).resolve().parents[1]
        out = SkillExecutor(REG).run(
            store, "code-lint",
            {"file_path": str(root / "src/psycho_carla/bench/scorers.py")})
    assert "file_read" in out and "Final output" in out


def test_index_source_sync_and_install(tmp_path, monkeypatch):
    import psycho_carla.skills.sources as sources
    from psycho_carla.skills import SkillStore

    index = {"skills": [{"name": "demo-skill", "description": "d",
                         "url": "https://example.invalid/demo/SKILL.md"}]}
    monkeypatch.setattr(sources, "_fetch_json", lambda url: index)
    monkeypatch.setattr(
        sources, "_fetch_text",
        lambda url: "---\nname: demo-skill\ndescription: Demo skill.\n---\n\n# Demo\n")
    store = SkillStore(tmp_path / "skills")
    source = sources.IndexSource("https://example.invalid/index.json")
    result = source.sync(store)
    assert result["skills"] == 1
    folder = source.install(store, "demo-skill")
    assert (folder / "SKILL.md").is_file()
    store.reload()
    assert "demo-skill" in store.catalog.names


def test_github_source_index(monkeypatch):
    import psycho_carla.skills.sources as sources

    monkeypatch.setattr(sources, "_fetch_json", lambda url: {
        "tree": [{"path": "alpha/SKILL.md"}, {"path": "beta/SKILL.md"},
                 {"path": "README.md"}]})
    entries = sources.GitHubSource("owner/repo").index()
    assert [e["name"] for e in entries] == ["alpha", "beta"]


# --------------------------------------------------------------------------- #
# Eval depth: scorers, datasets, comparison
# --------------------------------------------------------------------------- #

def test_scorers():
    from psycho_carla.bench.scorers import score

    assert score("numeric", "The answer is 42.", "42")[0]
    assert not score("numeric", "The answer is 41.", "42")[0]
    assert score("numeric", "500 grams", "500:0.001")[0]
    assert score("json", '{"name": "carla", "role": "assistant"}',
                 "name,role")[0]
    assert not score("json", "not json", "")[0]
    assert score("exact", " acknowledged ", "acknowledged")[0]
    assert score("length", "short", "1:120")[0]
    assert not score("length", "x" * 200, "1:120")[0]
    assert score("not_contains", "an apple a day", "banana")[0]
    assert not score("bogus", "x", "y")[0]


def test_builtin_suites():
    from psycho_carla.bench import get_suite, suite_names

    for name in ("core", "reasoning", "knowledge", "instruction", "coding"):
        suite = get_suite(name)
        assert suite is not None and len(suite.cases) >= 4
    assert "all" in suite_names()
    assert len(get_suite("all").cases) >= 20


def test_compare_reports():
    from psycho_carla.bench import EvalReport, EvalResult, compare_reports

    baseline = EvalReport(suite="s", results=[
        EvalResult(name="a", passed=True), EvalResult(name="b", passed=False)])
    candidate = EvalReport(suite="s", results=[
        EvalResult(name="a", passed=False), EvalResult(name="b", passed=True)])
    diff = compare_reports(baseline, candidate)
    assert diff["accuracy_delta"] == 0.0
    assert [r["name"] for r in diff["regressions"]] == ["a"]
    assert [r["name"] for r in diff["improvements"]] == ["b"]


def test_eval_case_scorer_roundtrip():
    from psycho_carla.bench import EvalCase

    case = EvalCase(name="x", prompt="p", scorer="numeric", expected="4")
    clone = EvalCase.from_dict(case.to_dict())
    assert clone.scorer == "numeric" and clone.expected == "4"


# --------------------------------------------------------------------------- #
# CLI: new commands parse and the no-runtime ones run
# --------------------------------------------------------------------------- #

def test_cli_new_commands():
    from psycho_carla.cli.main import main

    assert main(["eval", "--list-suites"]) == 0
    assert main(["scan", "--text", "hello world"]) == 0
    assert main(["scan", "--text", 'api_key = "[SECURITY_DATA]"']) == 1
    assert main(["skill", "sources"]) in (0, 1)  # writes under real home; parse-level


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
