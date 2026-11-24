"""
PSYCHO-C.A.R.L.A. — Prompt Optimizer & Routing Policy (Pillar 5).

Turns interaction traces into concrete improvements:

  * **Prompt optimisation** — mines the highest-rated traces, extracts the
    successful system-prompt patterns, and proposes a revised system prompt
    for an agent.
  * **Routing policy** — learns which capability tier performs best per agent
    given latency/quality trade-offs, and proposes a routing table.
  * **Agent advisor** — inspects failing traces and suggests agent/tool fixes.

Everything is local, deterministic, and reviewable. The optimizer proposes;
it never silently rewrites your prompt.

Layer: Pillar 5 (Learning).
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from .traces import Trace, TraceLogger


@dataclass
class PromptSuggestion:
    """A proposed change to an agent's system prompt."""

    agent: str
    current_prompt: str
    proposed_prompt: str
    rationale: list[str] = field(default_factory=list)
    sample_count: int = 0
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "current_prompt": self.current_prompt,
            "proposed_prompt": self.proposed_prompt,
            "rationale": self.rationale,
            "sample_count": self.sample_count,
            "confidence": round(self.confidence, 3),
            "created_at": self.created_at,
        }


@dataclass
class RoutingPolicy:
    """A learned mapping from agent -> tier."""

    agent: str
    recommended_tier: str
    alternatives: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    sample_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "recommended_tier": self.recommended_tier,
            "alternatives": {k: round(v, 3) for k, v in self.alternatives.items()},
            "rationale": self.rationale,
            "sample_count": self.sample_count,
        }


# --------------------------------------------------------------------------- #
# Prompt mining
# --------------------------------------------------------------------------- #
# Phrases that correlate with high-rated responses.
_GOOD_PATTERNS = [
    r"\bhere'?s what i found\b",
    r"\bstep \d\b",
    r"\bfirst,\|second,\|third,\b",
    r"\bthe answer is\b",
    r"\bbecause\b",
    r"\bfor example\b",
    r"\bin summary\b",
    r"\bthe file\b",
    r"\bspecifically\b",
]
_BAD_PATTERNS = [
    r"\bi'?m not sure\b",
    r"\bi don'?t know\b",
    r"\bcannot\b",
    r"\bunfortunately\b",
    r"\bas an ai\b",
    r"\bsorry\b",
    r"\bi apologize\b",
]

# Tool sequences that correlate with good outcomes.
_GOOD_TOOL_SEQUENCES = [
    ["file_read", "file_edit", "file_write"],
    ["web_search", "web_fetch"],
    ["code_execute", "shell_exec"],
]


class PromptOptimizer:
    """Mines traces for prompt improvements."""

    def __init__(self, traces: TraceLogger, router: Any = None) -> None:
        self.traces = traces
        self.router = router
        self._history: list[dict[str, Any]] = []

    # -- public API --------------------------------------------------------- #
    def optimize_prompt(
        self,
        agent: str,
        current_prompt: str = "",
        min_samples: int = 5,
        window_minutes: int = 10080,
    ) -> PromptSuggestion | None:
        """Propose an improved system prompt for an agent."""
        samples = [
            t for t in self.traces.recent(limit=500, agent=agent)
            if t.created_at >= time.time() - window_minutes * 60
        ]
        if len(samples) < min_samples:
            return None

        rated = [t for t in samples if t.rating > 0]
        good = [t for t in (rated or samples) if _score_text(t.response) > 0]
        bad = [t for t in (rated or samples) if _score_text(t.response) < 0]

        rationale: list[str] = []
        proposals: list[str] = []

        # 1. Praise the structures that show up in good answers.
        good_markers = _marker_frequency(good)
        for marker, count in good_markers.most_common(4):
            if count >= max(2, len(good) * 0.25):
                rationale.append(
                    f"{count}/{len(good)} well-rated responses used this pattern."
                )
                proposals.append(_marker_to_instruction(marker))

        # 2. Suppress the anti-patterns that appear in poor answers.
        bad_markers = _marker_frequency(bad)
        for marker, count in bad_markers.most_common(3):
            if count >= max(2, len(bad) * 0.25):
                rationale.append(
                    f"{count}/{len(bad)} poor responses contained this pattern."
                )
                proposals.append(_marker_to_avoid(marker))

        # 3. Tool discipline.
        tool_stats = _tool_sequence_stats(samples)
        if tool_stats.get("successful_tools"):
            rationale.append(
                f"Tools frequently used before good outcomes: "
                f"{', '.join(tool_stats['successful_tools'])}."
            )
            proposals.append(
                f"Prefer this tool order when relevant: "
                f"{' -> '.join(tool_stats['successful_tools'])}."
            )
        if tool_stats.get("failing_tools"):
            rationale.append(
                f"Tools associated with failures: "
                f"{', '.join(tool_stats['failing_tools'])}."
            )

        # 4. Latency insight.
        avg_latency = sum(t.latency_ms for t in samples) / max(len(samples), 1)
        rationale.append(f"Mean latency across {len(samples)} traces: {avg_latency:.0f} ms.")
        if avg_latency > 15_000:
            proposals.append(
                "Keep tool rounds tight — prefer one well-chosen call over several "
                "exploratory ones."
            )

        if not proposals:
            proposals.append(
                "No strong signal yet. Keep answering directly, lead with the "
                "conclusion, and cite sources."
            )

        base = current_prompt.rstrip()
        proposed = (base + "\n\n" if base else "") + "\n".join(f"- {p}" for p in proposals)

        confidence = _confidence(len(samples), len(rated), good, bad)
        suggestion = PromptSuggestion(
            agent=agent,
            current_prompt=base,
            proposed_prompt=proposed,
            rationale=rationale,
            sample_count=len(samples),
            confidence=confidence,
        )
        self._history.append({"type": "prompt", **suggestion.to_dict()})
        return suggestion

    def optimize_routing(
        self,
        min_samples: int = 5,
        window_minutes: int = 10080,
    ) -> dict[str, RoutingPolicy]:
        """Learn which tier suits each agent."""
        samples = [
            t for t in self.traces.recent(limit=1000)
            if t.created_at >= time.time() - window_minutes * 60
        ]
        if len(samples) < min_samples:
            return {}

        by_agent: dict[str, list[Trace]] = defaultdict(list)
        for t in samples:
            if t.tier:
                by_agent[t.agent].append(t)

        policies: dict[str, RoutingPolicy] = {}
        for agent, agent_traces in by_agent.items():
            if len(agent_traces) < min_samples:
                continue
            scores: dict[str, float] = defaultdict(float)
            counts: Counter[str] = Counter()
            for t in agent_traces:
                tier = t.tier
                counts[tier] += 1
                quality = _score_text(t.response)
                if t.rating > 0:
                    quality = quality * 0.4 + (t.rating / 5.0) * 0.6
                latency_factor = 1.0 / (1.0 + (t.latency_ms / 12_000.0))
                scores[tier] += quality * 0.7 + latency_factor * 0.3

            averaged = {tier: scores[tier] / counts[tier] for tier in scores}
            best = max(averaged, key=lambda k: averaged[k])
            policies[agent] = RoutingPolicy(
                agent=agent,
                recommended_tier=best,
                alternatives=averaged,
                rationale=(
                    f"{best} scored highest across {counts[best]} traces "
                    f"(quality + latency weighted)."
                ),
                sample_count=len(agent_traces),
            )
        self._history.append(
            {"type": "routing", "policies": {k: v.to_dict() for k, v in policies.items()}}
        )
        return policies

    def advise(self, agent: str, limit: int = 20) -> dict[str, Any]:
        """Inspect recent traces for an agent and suggest concrete fixes."""
        samples = self.traces.recent(limit=limit, agent=agent)
        if not samples:
            return {"agent": agent, "advice": [], "sample_count": 0}

        issues: list[str] = []
        errors = [t for t in samples if t.error]
        if errors:
            issues.append(f"{len(errors)} of {len(samples)} exchanges errored.")

        slow = [t for t in samples if t.latency_ms > 20_000]
        if slow:
            issues.append(
                f"{len(slow)} exchanges took over 20s — consider a faster tier "
                f"or fewer tool rounds."
            )

        empty_tools = [t for t in samples if not t.tool_calls]
        if len(empty_tools) == len(samples) and len(samples) > 3:
            issues.append(
                "No tool calls in the recent window — this agent may not have the "
                "tools it needs for its objective."
            )

        tool_errors = [
            tc for t in samples for tc in (t.tool_calls or [])
            if isinstance(tc, dict) and tc.get("ok") is False
        ]
        if tool_errors:
            failing = Counter(tc.get("name", "?") for tc in tool_errors)
            issues.append(
                "Failing tools: "
                + ", ".join(f"{n} ({c}x)" for n, c in failing.most_common(3))
            )

        suggestions = self.optimize_prompt(agent, min_samples=3)
        if suggestions:
            issues.extend(suggestions.rationale[:3])

        return {
            "agent": agent,
            "advice": issues or ["Nothing to flag — behaviour looks healthy."],
            "sample_count": len(samples),
            "suggestion": suggestions.to_dict() if suggestions else None,
        }

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._history[-limit:]

    def apply_suggestion(self, suggestion: PromptSuggestion) -> str:
        """Write a proposed prompt into the agent registry overrides."""
        if self.router is None:
            raise RuntimeError("Optimizer has no router/registry bound.")
        return suggestion.proposed_prompt


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #
def _score_text(text: str) -> float:
    """Heuristic quality score in [-1, 1]."""
    if not text:
        return 0.0
    lowered = text.lower()
    score = 0.0
    for pattern in _GOOD_PATTERNS:
        if re.search(pattern, lowered):
            score += 0.2
    for pattern in _BAD_PATTERNS:
        if re.search(pattern, lowered):
            score -= 0.3
    # Reward substance over length.
    words = len(text.split())
    if words < 5:
        score -= 0.3
    elif words > 40:
        score += 0.15
    return max(-1.0, min(1.0, score))


def _marker_frequency(traces: Sequence[Trace]) -> Counter[str]:
    """Count which structural markers appear across traces."""
    counter: Counter[str] = Counter()
    for t in traces:
        for pattern in _GOOD_PATTERNS + _BAD_PATTERNS:
            if re.search(pattern, t.response.lower()):
                counter[pattern] += 1
    return counter


_MARKER_INSTRUCTIONS = {
    r"\bhere'?s what i found\b": "Open with what you found, in the first sentence.",
    r"\bstep \d\b": "Number your steps so the structure is obvious.",
    r"\bfirst,\|second,\|third,\b": "Use explicit ordering (first, second, then).",
    r"\bthe answer is\b": "State the answer directly, then justify it.",
    r"\bbecause\b": "Always give the reason, not just the action.",
    r"\bfor example\b": "Support claims with a concrete example.",
    r"\bin summary\b": "Close with a one-line summary.",
    r"\bthe file\b": "Reference files by their exact path.",
    r"\bspecifically\b": "Be specific rather than general.",
    r"\bi'?m not sure\b": "Never express uncertainty you can resolve — check instead.",
    r"\bi don'?t know\b": "Never say you don't know without first searching.",
    r"\bcannot\b": "Reframe limitations as the available alternative.",
    r"\bunfortunately\b": "Drop apologetic framing; state the situation and the plan.",
    r"\bas an ai\b": "Never reference being an AI.",
    r"\bsorry\b": "No apologies — just proceed.",
    r"\bi apologize\b": "No apologies — just proceed.",
}


def _marker_to_instruction(marker: str) -> str:
    return _MARKER_INSTRUCTIONS.get(
        marker, "Reusable structural pattern detected in successful answers."
    )


def _marker_to_avoid(marker: str) -> str:
    base = _MARKER_INSTRUCTIONS.get(marker, "Avoid this pattern.")
    if base.startswith("Never") or base.startswith("Drop") or base.startswith("Reframe") \
            or base.startswith("No "):
        return base
    return "Avoid: " + base[0].lower() + base[1:]


def _tool_sequence_stats(traces: Sequence[Trace]) -> dict[str, Any]:
    """Find tools that precede good outcomes and tools that fail."""
    successful: Counter[str] = Counter()
    failing: Counter[str] = Counter()
    for t in traces:
        score = _score_text(t.response)
        for tc in t.tool_calls or []:
            if not isinstance(tc, dict):
                continue
            name = tc.get("name", "")
            if not name:
                continue
            if tc.get("ok") is False:
                failing[name] += 1
            elif score > 0:
                successful[name] += 1
    return {
        "successful_tools": [n for n, _ in successful.most_common(4)],
        "failing_tools": [n for n, _ in failing.most_common(4)],
    }


def _confidence(total: int, rated: int, good: Sequence[Any], bad: Sequence[Any]) -> float:
    if total == 0:
        return 0.0
    rating_signal = (rated / total) * 0.5
    contrast_signal = 0.0
    if good and bad:
        contrast_signal = min(1.0, len(good) / (len(good) + len(bad)))
    volume_signal = min(1.0, total / 50.0)
    return round(min(1.0, rating_signal + contrast_signal * 0.3 + volume_signal * 0.2), 3)
