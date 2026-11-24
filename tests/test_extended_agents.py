"""Extended agents: every upstream registration exists and runs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from psycho_carla.agents.builtin import BUILTIN_AGENTS  # noqa: E402
from psycho_carla.agents.registry import AgentRegistry  # noqa: E402
from psycho_carla.core.config import CarlaConfig  # noqa: E402
from psycho_carla.memory.store import Embedder, MemoryStore  # noqa: E402
from psycho_carla.providers.base import GenerationResult, Usage  # noqa: E402
from psycho_carla.tools.registry import ToolRegistry  # noqa: E402

UPSTREAM_AGENTS = {
    "simple", "native_react", "native_openhands", "openhands", "opencode",
    "claude_code", "rlm", "proactive", "orchestrator", "operative",
    "deep_research", "morning_digest", "monitor_operative", "advisors",
    "archon", "baseline_local", "baseline_cloud", "conductor", "minions",
    "mini_swe_agent", "skillorchestra", "toolorchestra", "react",
}


class StubRouter:
    """Answers every generation with canned text; records tiers used."""

    def __init__(self, text: str = "Stub answer, Boss."):
        self.text = text
        self.tiers: list[str] = []
        self.calls = 0

    def generate(self, messages, tier="", agent="", tools=None, **kwargs):
        self.calls += 1
        self.tiers.append(tier)
        # Decomposition prompts get a two-part plan back.
        system = messages[0].content if messages else ""
        if "Split the user's task" in system:
            text = "What is the capital of France?\nWhat language is spoken there?"
        else:
            text = self.text
        return GenerationResult(
            text=text,
            usage=Usage(prompt_tokens=5, completion_tokens=7),
            model="stub", provider="stub", tier=tier,
        )


def _make(cls, tmp_path, router=None):
    cfg = CarlaConfig()
    memory = MemoryStore(tmp_path / "memory.db", Embedder())
    return cls(cfg, router or StubRouter(), ToolRegistry(), memory)


def test_all_upstream_agents_registered():
    names = {cls.name for cls in BUILTIN_AGENTS}
    assert UPSTREAM_AGENTS <= names


def test_registry_creates_every_agent(tmp_path):
    registry = AgentRegistry()
    registry.register_all(BUILTIN_AGENTS)
    cfg = CarlaConfig()
    memory = MemoryStore(tmp_path / "memory.db", Embedder())
    for name in registry.names():
        agent = registry.create(name, cfg, StubRouter(), ToolRegistry(), memory)
        assert agent is not None, name
        assert agent.name == name


def test_simple_agent_uses_no_tools(tmp_path):
    router = StubRouter()
    agent = _make(_by_name("simple"), tmp_path, router)
    assert agent._tool_schemas_for() == []
    result = agent.run("Say hello.")
    assert result.text == "Stub answer, Boss."
    assert result.turns == 1


def test_rlm_decomposes_and_synthesises(tmp_path):
    router = StubRouter()
    agent = _make(_by_name("rlm"), tmp_path, router)
    result = agent.run("Tell me about France.")
    # plan + 2 sub-answers + synthesis
    assert router.calls == 4
    assert result.metadata["sub_questions"] == [
        "What is the capital of France?",
        "What language is spoken there?",
    ]
    assert result.usage["total_tokens"] == 4 * 12


def test_minions_split_work(tmp_path):
    router = StubRouter()
    agent = _make(_by_name("minions"), tmp_path, router)
    result = agent.run(
        "Summarise the report. Then list the risks. Then draft an email.")
    assert result.metadata["workers"] >= 2
    assert result.text == "Stub answer, Boss."


def test_advisors_take_three_views(tmp_path):
    router = StubRouter()
    agent = _make(_by_name("advisors"), tmp_path, router)
    result = agent.run("Should we rewrite the service in Rust?")
    assert result.metadata["advisors"] == 3
    assert router.calls == 4  # three advisors + synthesis


def test_external_cli_agents_fall_back_natively(tmp_path, monkeypatch):
    import psycho_carla.agents.extended as extended

    monkeypatch.setattr(extended.shutil, "which", lambda binary: None)
    for name in ("openhands", "opencode", "claude_code"):
        agent = _make(_by_name(name), tmp_path, StubRouter())
        result = agent.run("Fix the bug.")
        assert result.text == "Stub answer, Boss."
        assert result.metadata["external_cli_missing"] is True


def test_proactive_prompt_includes_memory(tmp_path):
    cls = _by_name("proactive")
    agent = _make(cls, tmp_path, StubRouter())
    agent.memory.store("Boss prefers dark mode", kind="fact")
    prompt = agent.system_prompt()
    assert "dark mode" in prompt


def _by_name(name):
    for cls in BUILTIN_AGENTS:
        if cls.name == name:
            return cls
    raise KeyError(name)
