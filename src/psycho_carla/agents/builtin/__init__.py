"""
The eight built-in PSYCHO-C.A.R.L.A. agents.

  1. orchestrator      — on-demand, plans and delegates complex work
  2. operative         — continuous, executes concrete tasks autonomously
  3. deep_research     — on-demand, multi-hop research with citations
  4. morning_digest    — scheduled, spoken daily briefing
  5. code_assist       — on-demand, code generation and execution
  6. monitor_operative — continuous, long-horizon monitoring with memory
  7. memory_curator    — scheduled/on-demand, maintains the memory store
  8. scheduler         — manages cron jobs and the agent schedule

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from ..base import AgentContext, AgentResult, BaseAgent
from ...providers.base import Message

# --------------------------------------------------------------------------- #
# 1. Orchestrator
# --------------------------------------------------------------------------- #
class OrchestratorAgent(BaseAgent):
    """Plans multi-step work, selects tools, and delegates to specialists."""

    name = "orchestrator"
    description = "Multi-turn reasoning with automatic tool selection and delegation."
    mode = "on_demand"
    tier = ""
    allowed_tools = [
        "file_read", "file_write", "file_list", "file_search", "file_info",
        "shell_exec", "shell_which",
        "web_search", "web_fetch", "calculator", "code_execute",
    ]
    system_prompt_extra = (
        "You are the orchestrator. Break the request into steps, choose the right "
        "tools, and execute them. Work methodically: gather what you need, then "
        "act. If a task is large, focus the first tool calls on the information "
        "you lack. When you have enough, answer completely and concretely. "
        "Cite file paths and URLs so the answer can be verified."
    )
    max_turns = 12


# --------------------------------------------------------------------------- #
# 2. Operative
# --------------------------------------------------------------------------- #
class OperativeAgent(BaseAgent):
    """A persistent executor for recurring personal workflows."""

    name = "operative"
    description = "Continuous autonomous executor with state management."
    mode = "continuous"
    allowed_tools = [
        "file_read", "file_write", "file_list", "file_search",
        "shell_exec", "shell_which", "shell_environment",
        "web_search", "web_fetch", "calculator",
    ]
    system_prompt_extra = (
        "You are the operative. You execute concrete tasks reliably and keep "
        "state between runs. Before acting, check what has already been done "
        "in this session so you do not duplicate work. Record what you changed. "
        "Prefer doing over describing — actually run the tool, then report the "
        "outcome plainly."
    )
    max_turns = 10


# --------------------------------------------------------------------------- #
# 3. Deep research
# --------------------------------------------------------------------------- #
class DeepResearchAgent(BaseAgent):
    """Multi-hop research across the web and local documents, with citations."""

    name = "deep_research"
    description = "Multi-hop research with citations across web and local docs."
    mode = "on_demand"
    allowed_tools = ["web_search", "web_fetch", "file_search", "file_read", "file_list"]
    system_prompt_extra = (
        "You are the research agent. Break the question into sub-questions and "
        "research each one. Search the web and local documents as needed. "
        "Cross-check important claims against a second source. "
        "Every factual claim must carry a citation as [n] matching the source "
        "list. Never fabricate a URL. If evidence is thin, say so plainly. "
        "Finish with a synthesis section that answers the question directly."
    )
    max_turns = 16


# --------------------------------------------------------------------------- #
# 4. Morning digest
# --------------------------------------------------------------------------- #
class MorningDigestAgent(BaseAgent):
    """A spoken daily briefing assembled from local context and the web."""

    name = "morning_digest"
    description = "Daily briefing from local context and news — voiced for TTS."
    mode = "scheduled"
    allowed_tools = ["web_search", "web_fetch", "file_read", "file_list"]
    system_prompt_extra = (
        "You are producing a spoken morning briefing. Write for the ear: "
        "no markdown, no tables, no code fences, no bullet characters. "
        "Short sentences. Lead with the single most important item. "
        "Cover: anything urgent from local files or notes, today's key news "
        "relevant to the user, and a short agenda line. "
        "End with one clear line about what needs attention today. "
        "Never say 'here is your digest' — just begin the briefing."
    )
    max_turns = 8

    def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        kwargs.setdefault("tier", self.tier)
        result = super().run(prompt, **kwargs)
        # Digest speech is the whole answer — no code channel.
        if result.channel:
            result.channel.speech = result.channel.speech.strip()
            result.channel.display = result.text
        return result


# --------------------------------------------------------------------------- #
# 5. Code assist
# --------------------------------------------------------------------------- #
class CodeAssistAgent(BaseAgent):
    """Generates, runs and debugs code against the local filesystem."""

    name = "code_assist"
    description = "Code generation, execution and debugging with file I/O."
    mode = "on_demand"
    allowed_tools = [
        "file_read", "file_write", "file_list", "file_search", "file_info",
        "shell_exec", "shell_python", "code_execute", "calculator",
    ]
    system_prompt_extra = (
        "You are the code assistant. Read the relevant files before changing "
        "anything. Write code that actually runs — then run it and fix what "
        "breaks. Keep changes minimal and in the existing style. "
        "Explain what you changed in two or three sentences after the code. "
        "Put code in fenced blocks with the language tag; the display layer "
        "renders it, the voice layer will not read it aloud."
    )
    max_turns = 14


# --------------------------------------------------------------------------- #
# 6. Monitor operative
# --------------------------------------------------------------------------- #
class MonitorOperativeAgent(BaseAgent):
    """Long-horizon monitoring with memory, compression and retrieval."""

    name = "monitor_operative"
    description = "Long-horizon monitoring with memory and context compression."
    mode = "continuous"
    allowed_tools = ["web_search", "web_fetch", "shell_exec", "file_read", "file_list"]
    system_prompt_extra = (
        "You are a monitoring agent. You watch a small set of conditions over "
        "time. Each cycle: check the current state, compare it against the "
        "last recorded state, and report only meaningful changes. "
        "Do not repeat unchanged information. Keep observations compact so the "
        "monitoring history stays readable. "
        "Raise an alert only when a threshold is actually crossed."
    )
    max_turns = 6

    def compress_observation(self, observation: str, max_chars: int = 400) -> str:
        """Compress a monitoring observation to keep the history compact."""
        observation = (observation or "").strip()
        if len(observation) <= max_chars:
            return observation
        cut = observation[:max_chars]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        return cut.rstrip(",;:") + "."


# --------------------------------------------------------------------------- #
# 7. Memory curator
# --------------------------------------------------------------------------- #
class MemoryCuratorAgent(BaseAgent):
    """Reviews, prunes and enriches the persistent memory store."""

    name = "memory_curator"
    description = "Maintains the memory store — dedupe, prune, enrich, summarise."
    mode = "scheduled"
    allowed_tools = ["memory_search", "memory_store", "memory_delete", "memory_list"]
    system_prompt_extra = (
        "You are the memory curator. Review what has been stored. "
        "Delete anything that was true only in passing, is duplicated, or is "
        "now wrong. Merge related memories into a single clearer one. "
        "Add a short tag to each memory so retrieval stays sharp. "
        "Never delete a memory that records a durable preference or decision "
        "without first confirming there is a replacement."
    )
    max_turns = 10


# --------------------------------------------------------------------------- #
# 8. Scheduler
# --------------------------------------------------------------------------- #
class SchedulerAgent(BaseAgent):
    """Manages the cron schedule and dispatches work to the other agents."""

    name = "scheduler"
    description = "Manages scheduled jobs and dispatches them to specialist agents."
    mode = "continuous"
    allowed_tools = ["cron_add", "cron_list", "cron_remove", "cron_run_now"]
    system_prompt_extra = (
        "You are the scheduler. You decide what should run and when, and you "
        "dispatch work to the right specialist agent. "
        "When the user asks for something recurring, create a cron job with a "
        "clear expression and the agent that owns it. "
        "List existing jobs before creating a new one so you do not duplicate "
        "schedules. Confirm the schedule back in plain language."
    )
    max_turns = 8

    def describe_schedule(self, jobs: Sequence[dict[str, Any]]) -> str:
        """Render the job list in a form that speaks well."""
        if not jobs:
            return f"No jobs scheduled, {self.persona.boss}."
        lines = []
        for job in jobs:
            lines.append(
                f"{job.get('name', 'job')} at {job.get('schedule', '?')}, "
                f"running the {job.get('agent', '?')} agent"
            )
        return "; ".join(lines)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
from ..extended import EXTENDED_AGENTS  # noqa: E402

BUILTIN_AGENTS: list[type[BaseAgent]] = [
    OrchestratorAgent,
    OperativeAgent,
    DeepResearchAgent,
    MorningDigestAgent,
    CodeAssistAgent,
    MonitorOperativeAgent,
    MemoryCuratorAgent,
    SchedulerAgent,
    *EXTENDED_AGENTS,
]


def register_builtin_agents(registry: dict[str, type[BaseAgent]]) -> None:
    """Register every built-in agent class into the agent registry."""
    for cls in BUILTIN_AGENTS:
        registry[cls.name] = cls


__all__ = [
    "OrchestratorAgent",
    "OperativeAgent",
    "DeepResearchAgent",
    "MorningDigestAgent",
    "CodeAssistAgent",
    "MonitorOperativeAgent",
    "MemoryCuratorAgent",
    "SchedulerAgent",
    "BUILTIN_AGENTS",
    "register_builtin_agents",
    "EXTENDED_AGENTS",
]
