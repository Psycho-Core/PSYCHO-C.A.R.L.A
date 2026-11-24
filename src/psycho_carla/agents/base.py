"""
PSYCHO-C.A.R.L.A. — Agent Base Harness.

Every agent inherits from `BaseAgent`, which provides:
  * the system prompt (persona-aware),
  * tool selection and tool-call execution,
  * context assembly (memory + conversation history),
  * retry / exit logic and the turn budget,
  * telemetry hooks.

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from ..core.config import CarlaConfig
from ..core.persona import ChannelPayload, Persona
from ..memory.store import MemoryStore
from ..providers.base import GenerationResult, Message, ToolCall
from ..providers.router import ModelRouter
from ..tools.registry import ToolRegistry


@dataclass
class AgentContext:
    """Everything an agent needs for one turn."""

    messages: list[Message] = field(default_factory=list)
    session_id: str = "default"
    tier: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    scratchpad: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    """The outcome of an agent run."""

    text: str = ""
    channel: ChannelPayload | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    tier: str = ""
    agent: str = ""
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "speech": self.channel.speech if self.channel else "",
            "display": self.channel.display if self.channel else self.text,
            "code_blocks": self.channel.code_blocks if self.channel else [],
            "tool_calls": self.tool_calls,
            "turns": self.turns,
            "tier": self.tier,
            "agent": self.agent,
            "latency_ms": round(self.latency_ms, 2),
            "usage": dict(self.usage),
            "error": self.error,
            "metadata": self.metadata,
        }


class BaseAgent:
    """Base class for all PSYCHO-C.A.R.L.A. agents."""

    #: Registry key.
    name: str = "base"
    #: One-line description shown in the HUD.
    description: str = ""
    #: Execution mode: "on_demand", "scheduled", "continuous".
    mode: str = "on_demand"
    #: Which tools this agent may use (empty = all enabled tools).
    allowed_tools: list[str] = []
    #: Preferred capability tier alias.
    tier: str = ""
    #: Extra system prompt appended after the persona.
    system_prompt_extra: str = ""
    #: Max tool-calling turns before a final answer is forced.
    max_turns: int = 10

    def __init__(
        self,
        config: CarlaConfig,
        router: ModelRouter,
        tool_registry: ToolRegistry,
        memory: MemoryStore,
        persona: Persona | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.tool_registry = tool_registry
        self.memory = memory
        self.persona = persona or Persona(config.persona)
        self.tier = self.tier or config.intelligence.default_tier
        self.max_turns = self.max_turns or config.agent.max_turns

    # -- prompts ------------------------------------------------------------ #
    def system_prompt(self, ctx: AgentContext | None = None) -> str:
        """Compose the system prompt: persona + agent role + context."""
        parts: list[str] = [self._persona_block()]
        if self.system_prompt_extra:
            parts.append(self.system_prompt_extra)
        if self.config.agent.objective:
            parts.append(f"Objective: {self.config.agent.objective}")
        skills_block = self.skills_prompt()
        if skills_block:
            parts.append(skills_block)
        if ctx is not None and self.config.agent.context_from_memory:
            query = " ".join(m.content for m in ctx.messages[-3:] if m.role == "user")
            if query:
                block = self.memory.context_for(
                    query,
                    top_k=self.config.tools.storage.context_top_k,
                    max_tokens=self.config.tools.storage.context_max_tokens,
                    min_score=self.config.tools.storage.context_min_score,
                )
                if block:
                    parts.append(block)
        return "\n\n".join(p for p in parts if p).strip()

    def skills_prompt(self) -> str:
        """The skills catalog block — names and descriptions only.

        This is the progressive-disclosure contract: only the metadata is
        always in context (~100 tokens per skill). The full SKILL.md body
        is pulled in on demand via the ``skill_load`` tool.
        """
        return ""

    def _persona_block(self) -> str:
        boss = self.persona.boss
        return (
            f"You are {self.persona.name}, the personal AI of {boss}. "
            f"Persona: {self.config.persona.style}. "
            f"Address {boss} by that title. "
            "Be concise and direct — no preamble, no filler, no moralising. "
            "Never apologise for your own limitations. "
            "Give the answer first, then the supporting detail. "
            "Do not narrate what you are about to do; just do it."
        )

    # -- tool plumbing -------------------------------------------------------- #
    def available_tools(self) -> list[dict[str, Any]]:
        """Schemas for the tools this agent is allowed to call."""
        return self._tool_schemas_for()

    def tools_registry_has(self, name: str) -> bool:
        return (
            self.tool_registry.get(name) is not None
            and self.tool_registry.is_enabled(name)
        )

    def _tool_schemas_for(self) -> list[dict[str, Any]]:
        if self.allowed_tools:
            names = [t for t in self.allowed_tools if self.tools_registry_has(t)]
        else:
            names = [t.name for t in self.tool_registry.all()]
        # Capability policy: an agent only *sees* tools it may use, so a
        # denied tool cannot be invoked at all (security.capabilities).
        try:
            from ..security.capabilities import load_policy

            policy = load_policy(self.config)
            names = policy.filter_tools(self.name, names)
        except Exception:
            pass
        return self.tool_registry.schemas(names)

    # -- execution -------------------------------------------------------------- #
    def run(
        self,
        prompt: str,
        *,
        session_id: str = "default",
        tier: str = "",
        history: Sequence[Message] | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        """Run the agent on a prompt and return its result."""
        ctx = AgentContext(session_id=session_id, tier=tier or self.tier)
        ctx.messages = list(history or [])
        ctx.messages.append(Message(role="user", content=prompt))

        started = time.perf_counter()
        result = self._execute(ctx, **kwargs)
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.agent = self.name
        result.tier = ctx.tier or self.tier
        if result.channel is None:
            result.channel = self.persona.split(result.text)
        return result

    def _execute(self, ctx: AgentContext, **kwargs: Any) -> AgentResult:
        """Core agent loop. Subclasses may override for specialised behaviour."""
        messages: list[Message] = [Message(role="system", content=self.system_prompt(ctx))]
        messages.extend(ctx.messages)

        tool_calls_log: list[dict[str, Any]] = []
        final_text = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        turns = 0

        for turn in range(self.max_turns):
            turns = turn + 1
            schemas = self._tool_schemas_for() if turn < self.max_turns - 1 else []
            gen = self.router.generate(
                messages,
                ctx.tier or self.tier,
                agent=self.name,
                tools=schemas or None,
            )
            usage["prompt_tokens"] += gen.usage.prompt_tokens
            usage["completion_tokens"] += gen.usage.completion_tokens
            usage["total_tokens"] += gen.usage.total_tokens

            if gen.error:
                return AgentResult(
                    text="", tool_calls=tool_calls_log, turns=turns,
                    usage=usage, error=gen.error,
                )

            if not gen.tool_calls:
                final_text = gen.text
                break

            messages.append(
                Message(role="assistant", content=gen.text,
                        metadata={"tool_calls": [c.to_dict() for c in gen.tool_calls]})
            )

            for call in gen.tool_calls:
                result = self.tool_registry.execute(call.name, call.arguments)
                tool_calls_log.append(
                    {"name": call.name, "arguments": call.arguments,
                     "ok": result.ok, "error": result.error,
                     "duration_ms": result.duration_ms}
                )
                messages.append(
                    Message(
                        role="tool",
                        content=result.as_message()[:12_000],
                        name=call.name,
                        tool_call_id=call.id,
                    )
                )
        else:
            # Budget exhausted — force a final answer from what we have.
            messages.append(
                Message(role="user",
                        content="Stop using tools. Give your final answer now, in full.")
            )
            gen = self.router.generate(messages, ctx.tier or self.tier,
                                       agent=self.name, tools=None)
            final_text = gen.text
            usage["prompt_tokens"] += gen.usage.prompt_tokens
            usage["completion_tokens"] += gen.usage.completion_tokens
            usage["total_tokens"] += gen.usage.total_tokens

        return AgentResult(
            text=final_text,
            channel=self.persona.split(final_text),
            tool_calls=tool_calls_log,
            turns=turns,
            usage=usage,
        )

    # -- helpers ------------------------------------------------------------------ #
    def remember(self, content: str, kind: str = "fact",
                 tags: list[str] | None = None) -> int:
        return self.memory.store(content, kind=kind, tags=tags, source=self.name)

    def recall(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.memory.search(query, top_k=top_k)]
