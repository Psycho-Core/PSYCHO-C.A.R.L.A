"""
Extended agents — the upstream OpenJarvis registrations beyond the core eight.

  * ``simple``            — one-shot, no tools
  * ``native_react``      — ReAct loop (Thought → Action → Observation)
  * ``react``             — upstream alias for ``native_react``
  * ``native_openhands``  — CodeAct: acts by writing and running code
  * ``openhands`` / ``opencode`` / ``claude_code`` — external CLI agents,
    used when the binary is installed, native fallback otherwise
  * ``rlm``               — recursive LM: decompose, answer parts, synthesise
  * ``proactive``         — scheduled sweeps over memory + pending work
  * Hybrid local/cloud family: ``conductor``, ``minions``, ``advisors``,
    ``archon``, ``baseline_local``, ``baseline_cloud``, ``mini_swe_agent``,
    ``skillorchestra``, ``toolorchestra``

Every agent runs on the same model-agnostic router — "local" and "cloud"
are just capability tiers from the config, never hardcoded providers.

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from ..providers.base import GenerationResult, Message
from .base import AgentContext, AgentResult, BaseAgent

_CODE_TOOLS = [
    "file_read", "file_write", "file_list", "file_search", "file_info",
    "shell_exec", "shell_python", "code_execute", "calculator",
]


# --------------------------------------------------------------------------- #
# Simple — one shot, no tools
# --------------------------------------------------------------------------- #
class SimpleAgent(BaseAgent):
    """A single direct completion. No tools, no loop."""

    name = "simple"
    description = "One-shot answer with no tool use."
    mode = "on_demand"
    allowed_tools = []
    max_turns = 1
    system_prompt_extra = (
        "Answer directly in one pass. You have no tools this run — work "
        "only from what you know and what the user gave you."
    )

    def _tool_schemas_for(self) -> list[dict[str, Any]]:
        return []


# --------------------------------------------------------------------------- #
# Native ReAct
# --------------------------------------------------------------------------- #
class NativeReActAgent(BaseAgent):
    """ReAct: reason in the open, act through tools, observe, repeat."""

    name = "native_react"
    description = "ReAct reasoning loop with explicit thought/action steps."
    mode = "on_demand"
    allowed_tools = _CODE_TOOLS + ["web_search", "web_fetch"]
    max_turns = 16
    system_prompt_extra = (
        "Work in the ReAct pattern. For each step, state a one-line "
        "Thought about what you still need, then call the tool that gets "
        "it (the Action), then read the Observation before deciding the "
        "next step. Keep thoughts to a single line — they are scaffolding, "
        "not the answer. Finish with the answer itself, no thought prefix."
    )


class ReactAgent(NativeReActAgent):
    """Upstream backward-compatible alias for ``native_react``."""

    name = "react"
    description = "Alias for native_react (upstream compatibility)."


# --------------------------------------------------------------------------- #
# CodeAct (native_openhands) + external CLI agents
# --------------------------------------------------------------------------- #
class NativeOpenHandsAgent(BaseAgent):
    """CodeAct: solve the task by writing and executing code."""

    name = "native_openhands"
    description = "CodeAct agent — acts by writing, running and fixing code."
    mode = "on_demand"
    allowed_tools = _CODE_TOOLS
    max_turns = 20
    system_prompt_extra = (
        "You are a CodeAct agent. Prefer action through code: write a "
        "script or command, run it, read the real output, and iterate on "
        "what it actually says. Do not speculate about results you could "
        "measure. Verify every fix by running it. Keep each code action "
        "small enough to debug in one step."
    )


class ExternalCliAgent(BaseAgent):
    """Base for agents backed by an external coding-agent CLI.

    When the binary is on PATH it is run headless and its stdout is the
    answer. When it is absent the agent falls back to the native tool
    loop and says so in the result metadata — never silently.
    """

    cli_binary: str = ""
    cli_args: list[str] = []

    def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        binary = shutil.which(self.cli_binary) if self.cli_binary else None
        if binary is None:
            result = super().run(prompt, **kwargs)
            result.metadata["external_cli"] = self.cli_binary
            result.metadata["external_cli_missing"] = True
            return result
        timeout = float(getattr(self.config.security, "shell_timeout", 60.0)) * 10
        try:
            proc = subprocess.run(
                [binary, *self.cli_args, prompt],
                capture_output=True, text=True, timeout=timeout,
            )
            text = (proc.stdout or "").strip()
            if proc.returncode != 0 and not text:
                text = (proc.stderr or "").strip() or \
                    f"{self.cli_binary} exited with code {proc.returncode}."
            result = AgentResult(text=text, agent=self.name)
        except Exception as exc:
            result = AgentResult(text="", agent=self.name, error=str(exc))
        result.metadata["external_cli"] = self.cli_binary
        return result


class OpenHandsAgent(ExternalCliAgent):
    name = "openhands"
    description = "OpenHands CLI agent (native CodeAct fallback)."
    cli_binary = "openhands"
    cli_args = ["--headless", "-t"]
    allowed_tools = _CODE_TOOLS
    max_turns = 20
    system_prompt_extra = NativeOpenHandsAgent.system_prompt_extra


class OpenCodeAgent(ExternalCliAgent):
    name = "opencode"
    description = "OpenCode CLI agent (native fallback)."
    cli_binary = "opencode"
    cli_args = ["run"]
    allowed_tools = _CODE_TOOLS
    max_turns = 16
    system_prompt_extra = NativeOpenHandsAgent.system_prompt_extra


class ClaudeCodeAgent(ExternalCliAgent):
    name = "claude_code"
    description = "Claude Code CLI agent (native fallback)."
    cli_binary = "Claude"
    cli_args = ["-p"]
    allowed_tools = _CODE_TOOLS
    max_turns = 16
    system_prompt_extra = NativeOpenHandsAgent.system_prompt_extra


# --------------------------------------------------------------------------- #
# RLM — recursive language model
# --------------------------------------------------------------------------- #
class RLMAgent(BaseAgent):
    """Decompose a hard question, answer the parts, synthesise."""

    name = "rlm"
    description = "Recursive LM — decomposes, answers sub-questions, synthesises."
    mode = "on_demand"
    allowed_tools = []
    max_turns = 8

    def _tool_schemas_for(self) -> list[dict[str, Any]]:
        return []

    def _generate(self, ctx: AgentContext, system: str, user: str) -> GenerationResult:
        return self.router.generate(
            [Message(role="system", content=system),
             Message(role="user", content=user)],
            ctx.tier or self.tier, agent=self.name, tools=None,
        )

    def _execute(self, ctx: AgentContext, **kwargs: Any) -> AgentResult:
        prompt = ctx.messages[-1].content if ctx.messages else ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        def absorb(gen: GenerationResult) -> None:
            usage["prompt_tokens"] += gen.usage.prompt_tokens
            usage["completion_tokens"] += gen.usage.completion_tokens
            usage["total_tokens"] += gen.usage.total_tokens

        plan = self._generate(
            ctx,
            "Split the user's task into 2-5 independent sub-questions. "
            "Output one sub-question per line, no numbering, no preamble.",
            prompt,
        )
        absorb(plan)
        if plan.error:
            return AgentResult(text="", usage=usage, error=plan.error, turns=1)

        sub_questions = [
            re.sub(r"^[\d\-\*\.\)\s]+", "", line).strip()
            for line in plan.text.splitlines() if line.strip()
        ][:5]

        if len(sub_questions) < 2:
            direct = self._generate(ctx, self.system_prompt(ctx), prompt)
            absorb(direct)
            return AgentResult(text=direct.text, usage=usage, turns=2,
                               metadata={"sub_questions": sub_questions})

        answers: list[str] = []
        for question in sub_questions:
            gen = self._generate(
                ctx,
                "Answer this sub-question completely and concretely. "
                "Facts only; say what you do not know.",
                question,
            )
            absorb(gen)
            answers.append(f"Q: {question}\nA: {gen.text.strip()}")

        synthesis = self._generate(
            ctx, self.system_prompt(ctx),
            f"Original task: {prompt}\n\nResearch:\n" + "\n\n".join(answers) +
            "\n\nWrite the final answer to the original task using only "
            "this research.",
        )
        absorb(synthesis)
        return AgentResult(
            text=synthesis.text, usage=usage, turns=2 + len(sub_questions),
            metadata={"sub_questions": sub_questions},
        )


# --------------------------------------------------------------------------- #
# Proactive
# --------------------------------------------------------------------------- #
class ProactiveAgent(BaseAgent):
    """Scheduled sweeps: look for what needs attention before being asked."""

    name = "proactive"
    description = "Proactive sweep over memory and pending work."
    mode = "scheduled"
    allowed_tools = [
        "memory_search", "memory_list", "cron_list", "web_search", "web_fetch",
    ]
    max_turns = 8
    system_prompt_extra = (
        "You are running a proactive sweep. Look at what is known and "
        "pending, and surface only what genuinely needs the user's "
        "attention: deadlines, unfinished threads, contradictions, "
        "anything time-sensitive. If nothing needs attention, say exactly "
        "that in one line. Never invent urgency."
    )

    def system_prompt(self, ctx: AgentContext | None = None) -> str:
        prompt = super().system_prompt(ctx)
        try:
            recent = self.memory.list_memories(limit=8)
        except Exception:
            recent = []
        if recent:
            lines = "\n".join(
                f"- {m.content[:160]}" if not isinstance(m, dict)
                else f"- {str(m.get('content', ''))[:160]}"
                for m in recent
            )
            prompt += f"\n\nRecent memory:\n{lines}"
        return prompt


# --------------------------------------------------------------------------- #
# Hybrid local/cloud family
# --------------------------------------------------------------------------- #
class HybridAgent(BaseAgent):
    """Routes between a local tier and a cloud/deep tier by task shape."""

    def _local_tier(self) -> str:
        return self.config.intelligence.default_tier

    def _cloud_tier(self) -> str:
        tiers = self.config.intelligence.tiers
        for name in tiers:
            if "cloud" in name or "deep" in name:
                return name
        return self.config.intelligence.fallback_tier or self._local_tier()

    def _pick_tier(self, prompt: str) -> str:
        heavy = ("research", "analyse", "analyze", "write code", "debug",
                 "plan", "compare", "explain in detail")
        if len(prompt) > 600 or any(word in prompt.lower() for word in heavy):
            return self._cloud_tier()
        return self._local_tier()


class BaselineLocalAgent(HybridAgent):
    name = "baseline_local"
    description = "Baseline: everything on the local/default tier."
    max_turns = 10

    def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        kwargs.setdefault("tier", self._local_tier())
        return super().run(prompt, **kwargs)


class BaselineCloudAgent(HybridAgent):
    name = "baseline_cloud"
    description = "Baseline: everything on the cloud/deep tier."
    max_turns = 10

    def run(self, prompt: str, **kwargs: Any) -> AgentResult:
        kwargs.setdefault("tier", self._cloud_tier())
        return super().run(prompt, **kwargs)


class ConductorAgent(HybridAgent):
    """Plans on the deep tier, executes the plan on the local tier."""

    name = "conductor"
    description = "Conductor — deep-tier planning, local-tier execution."
    mode = "on_demand"
    allowed_tools = _CODE_TOOLS + ["web_search", "web_fetch"]
    max_turns = 14
    system_prompt_extra = (
        "You execute a plan that was drafted for you. Follow it step by "
        "step; deviate only when a step is impossible, and note why."
    )

    def _execute(self, ctx: AgentContext, **kwargs: Any) -> AgentResult:
        prompt = ctx.messages[-1].content if ctx.messages else ""
        plan = self.router.generate(
            [Message(role="system", content=(
                "Draft a short numbered execution plan for the task. "
                "Steps only, no prose.")),
             Message(role="user", content=prompt)],
            self._cloud_tier(), agent=self.name, tools=None,
        )
        if not plan.error and plan.text.strip():
            ctx.messages.append(Message(
                role="user",
                content=f"Execution plan (follow it):\n{plan.text.strip()}",
            ))
            ctx.tier = self._local_tier()
        result = super()._execute(ctx, **kwargs)
        result.metadata["planned_on"] = self._cloud_tier()
        result.metadata["executed_on"] = ctx.tier
        return result


class MinionsAgent(HybridAgent):
    """Many small local workers in parallel, one synthesis pass."""

    name = "minions"
    description = "Minions — splits work across small fast-tier workers."
    mode = "on_demand"
    max_turns = 6

    def _tool_schemas_for(self) -> list[dict[str, Any]]:
        return []

    def _execute(self, ctx: AgentContext, **kwargs: Any) -> AgentResult:
        prompt = ctx.messages[-1].content if ctx.messages else ""
        pieces = [p.strip() for p in re.split(r"\n|(?<=[.!?])\s+", prompt)
                  if len(p.strip()) > 12][:4]
        if len(pieces) < 2:
            pieces = [prompt]
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        outputs: list[str] = []
        for piece in pieces:
            gen = self.router.generate(
                [Message(role="system", content=(
                    "Handle this one small piece completely and briefly.")),
                 Message(role="user", content=piece)],
                self._local_tier(), agent=self.name, tools=None,
            )
            usage["prompt_tokens"] += gen.usage.prompt_tokens
            usage["completion_tokens"] += gen.usage.completion_tokens
            usage["total_tokens"] += gen.usage.total_tokens
            outputs.append(f"Piece: {piece}\nResult: {gen.text.strip()}")
        final = self.router.generate(
            [Message(role="system", content=self.system_prompt(ctx)),
             Message(role="user", content=(
                 f"Original request: {prompt}\n\nWorker results:\n" +
                 "\n\n".join(outputs) +
                 "\n\nCombine these into one coherent answer."))],
            ctx.tier or self.tier, agent=self.name, tools=None,
        )
        usage["prompt_tokens"] += final.usage.prompt_tokens
        usage["completion_tokens"] += final.usage.completion_tokens
        usage["total_tokens"] += final.usage.total_tokens
        return AgentResult(text=final.text, usage=usage,
                           turns=1 + len(pieces),
                           metadata={"workers": len(pieces)})


class AdvisorsAgent(HybridAgent):
    """Three independent advisors, then a synthesis."""

    name = "advisors"
    description = "Advisors — multiple perspectives, one recommendation."
    mode = "on_demand"
    max_turns = 6

    def _tool_schemas_for(self) -> list[dict[str, Any]]:
        return []

    def _execute(self, ctx: AgentContext, **kwargs: Any) -> AgentResult:
        prompt = ctx.messages[-1].content if ctx.messages else ""
        lenses = (
            "You are the pragmatic advisor. Focus on what works soonest.",
            "You are the sceptical advisor. Focus on risks and failure modes.",
            "You are the long-term advisor. Focus on consequences in a year.",
        )
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        views: list[str] = []
        for lens in lenses:
            gen = self.router.generate(
                [Message(role="system", content=lens),
                 Message(role="user", content=prompt)],
                self._pick_tier(prompt), agent=self.name, tools=None,
            )
            usage["prompt_tokens"] += gen.usage.prompt_tokens
            usage["completion_tokens"] += gen.usage.completion_tokens
            usage["total_tokens"] += gen.usage.total_tokens
            views.append(gen.text.strip())
        final = self.router.generate(
            [Message(role="system", content=self.system_prompt(ctx)),
             Message(role="user", content=(
                 f"Question: {prompt}\n\nAdvisor views:\n" +
                 "\n\n---\n\n".join(views) +
                 "\n\nGive one clear recommendation, noting where the "
                 "advisors disagreed."))],
            ctx.tier or self.tier, agent=self.name, tools=None,
        )
        usage["prompt_tokens"] += final.usage.prompt_tokens
        usage["completion_tokens"] += final.usage.completion_tokens
        usage["total_tokens"] += final.usage.total_tokens
        return AgentResult(text=final.text, usage=usage, turns=4,
                           metadata={"advisors": len(lenses)})


class ArchonAgent(HybridAgent):
    """Architecture and design-review specialist."""

    name = "archon"
    description = "Archon — system design and architecture review."
    mode = "on_demand"
    allowed_tools = ["file_read", "file_list", "file_search", "file_info"]
    max_turns = 12
    system_prompt_extra = (
        "You are the architecture reviewer. Read the actual code before "
        "judging it. Assess: boundaries between components, failure modes, "
        "scaling limits, and what will hurt in six months. Name specific "
        "files. Rank findings by severity and give the smallest change "
        "that fixes each."
    )


class MiniSWEAgent(HybridAgent):
    """Focused software-engineering loop: reproduce, fix, verify."""

    name = "mini_swe_agent"
    description = "Mini SWE agent — reproduce, patch, and verify bugs."
    mode = "on_demand"
    allowed_tools = _CODE_TOOLS
    max_turns = 25
    system_prompt_extra = (
        "You fix software defects. Discipline: (1) reproduce the failure "
        "with a command or script before changing anything; (2) make the "
        "smallest change that fixes it; (3) re-run the reproduction and "
        "the relevant tests; (4) report what changed and the evidence. "
        "Never claim a fix you did not run."
    )


class SkillOrchestraAgent(HybridAgent):
    """Routes work through the installed skills catalog first."""

    name = "skillorchestra"
    description = "SkillOrchestra — skill-first orchestration."
    mode = "on_demand"
    allowed_tools = ["skill_search", "skill_load", "skill_list"] + _CODE_TOOLS
    max_turns = 14
    system_prompt_extra = (
        "Before acting, search the skills catalog for a skill that covers "
        "this task and load it if one fits — skills encode proven "
        "procedures. Follow the loaded skill's instructions. If no skill "
        "fits, proceed directly and note that no skill matched."
    )


class ToolOrchestraAgent(HybridAgent):
    """Tool-selection specialist: minimal, parallel tool plans."""

    name = "toolorchestra"
    description = "ToolOrchestra — optimal tool selection and sequencing."
    mode = "on_demand"
    allowed_tools = []
    max_turns = 14
    system_prompt_extra = (
        "Your craft is tool selection. Use the smallest set of tools that "
        "fully answers the request. Batch independent calls together. "
        "Never call a tool to learn something a previous result already "
        "told you. Prefer reading before writing, and verify writes."
    )


EXTENDED_AGENTS: list[type[BaseAgent]] = [
    SimpleAgent,
    NativeReActAgent,
    ReactAgent,
    NativeOpenHandsAgent,
    OpenHandsAgent,
    OpenCodeAgent,
    ClaudeCodeAgent,
    RLMAgent,
    ProactiveAgent,
    BaselineLocalAgent,
    BaselineCloudAgent,
    ConductorAgent,
    MinionsAgent,
    AdvisorsAgent,
    ArchonAgent,
    MiniSWEAgent,
    SkillOrchestraAgent,
    ToolOrchestraAgent,
]

__all__ = [
    "SimpleAgent", "NativeReActAgent", "ReactAgent", "NativeOpenHandsAgent",
    "OpenHandsAgent", "OpenCodeAgent", "ClaudeCodeAgent", "RLMAgent",
    "ProactiveAgent", "BaselineLocalAgent", "BaselineCloudAgent",
    "ConductorAgent", "MinionsAgent", "AdvisorsAgent", "ArchonAgent",
    "MiniSWEAgent", "SkillOrchestraAgent", "ToolOrchestraAgent",
    "EXTENDED_AGENTS",
]
