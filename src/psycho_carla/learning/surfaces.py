"""
PSYCHO-C.A.R.L.A. — Tool-Selection & Engine-Spec Optimisers (Pillar 5).

OpenJarvis's Learning primitive optimises **four** surfaces:

  1. Prompt / weights       — implemented in ``optimizer.py``
  2. Routing                — implemented in ``optimizer.py``
  3. **Tool selection**      — implemented here
  4. **Engine specification** — implemented here

This module closes surfaces 3 and 4.

Tool selection
--------------
Mines trace history for the tools each agent actually used, scores them
on observed success (no error, high user rating, tool result present) and
cost (latency), then proposes a **tool whitelist per agent** — tools worth
keeping, tools worth dropping, and tools worth promoting. The goal is a
smaller, more reliable tool surface: fewer tools means fewer wrong picks
and a shorter prompt.

Engine specification
--------------------
Proposes per-tier engine configuration — quantisation, context window,
batch size, sampling parameters, and the preferred provider — derived
from what actually worked in the traces, weighted by the same
accuracy / latency / cost / efficiency objectives the framework uses
everywhere else.

Both optimisers are local, deterministic and reviewable: they propose,
they never silently rewrite your configuration.

Layer: Pillar 5 (Learning).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

from .traces import Trace, TraceLogger

# Quantisation choices ordered from most aggressive to least.
QUANT_LEVELS = ("none", "q8_0", "q5_k_m", "q4_k_m", "q3_k_s")

# Sampling parameters the engine optimiser may adjust.
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "repetition_penalty")


@dataclass
class ToolScore:
    """Observed performance of one tool within one agent's history."""

    agent: str
    tool: str
    uses: int = 0
    successes: int = 0
    failures: int = 0
    avg_latency_ms: float = 0.0
    avg_rating: float = 0.0
    score: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.uses if self.uses else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "tool": self.tool,
            "uses": self.uses,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "avg_rating": round(self.avg_rating, 2),
            "score": round(self.score, 4),
        }


@dataclass
class ToolPolicy:
    """A proposed tool whitelist for one agent."""

    agent: str
    keep: list[str] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    promote: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)
    sample_count: int = 0
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "keep": self.keep,
            "drop": self.drop,
            "promote": self.promote,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "rationale": self.rationale,
            "sample_count": self.sample_count,
            "confidence": round(self.confidence, 3),
            "created_at": self.created_at,
        }


@dataclass
class EngineSpec:
    """A proposed engine configuration for one capability tier."""

    tier: str
    provider: str = ""
    model: str = ""
    quantization: str = "none"
    context_window: int = 8192
    max_tokens: int = 2048
    batch_size: int = 1
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 40
    repetition_penalty: float = 1.1
    rationale: list[str] = field(default_factory=list)
    sample_count: int = 0
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "quantization": self.quantization,
            "context_window": self.context_window,
            "max_tokens": self.max_tokens,
            "batch_size": self.batch_size,
            "sampling": {
                "temperature": self.temperature,
                "top_p": self.top_p,
                "top_k": self.top_k,
                "repetition_penalty": self.repetition_penalty,
            },
            "rationale": self.rationale,
            "sample_count": self.sample_count,
            "confidence": round(self.confidence, 3),
            "created_at": self.created_at,
        }


def _trace_tools(trace: Trace) -> list[str]:
    """Extract the tool names used by a trace.

    Tools appear in ``trace.tool_calls`` as dicts (``{"name": ..., ...}``)
    and may also be listed as plain names in ``trace.metadata``.
    """
    names: list[str] = []
    for item in trace.tool_calls or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("tool") or item.get("function")
            if isinstance(name, dict):
                name = name.get("name")
            if name:
                names.append(str(name))
    meta = trace.metadata or {}
    for source in (meta.get("tools_used", []), meta.get("tools", [])):
        if isinstance(source, str):
            names.append(source)
        elif isinstance(source, Sequence):
            for item in source:
                if isinstance(item, str):
                    names.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("tool")
                    if name:
                        names.append(str(name))
    return names


def _trace_failed(trace: Trace) -> bool:
    """Did this exchange fail?"""
    meta = trace.metadata or {}
    if meta.get("error"):
        return True
    if meta.get("failed"):
        return True
    if "success" in meta:
        return not bool(meta["success"])
    for call in trace.tool_calls or []:
        if isinstance(call, dict) and (call.get("error") or call.get("ok") is False):
            return True
    return False


def _quality_score(trace: Trace) -> float:
    """Quality in [0, 1] combining rating and response signals."""
    quality = 0.5
    if trace.rating and trace.rating > 0:
        quality = (trace.rating / 5.0) * 0.7 + 0.3
    if _trace_failed(trace):
        quality *= 0.3
    response_len = len(trace.response or "")
    if response_len > 80:
        quality = min(1.0, quality + 0.1)
    return max(0.0, min(1.0, quality))


class ToolSelectionOptimizer:
    """Learns which tools each agent should keep, drop, and promote."""

    def __init__(self, traces: TraceLogger, registry: Any = None) -> None:
        self.traces = traces
        self.registry = registry
        self._history: list[dict[str, Any]] = []

    # -- public API ------------------------------------------------------ #
    def score_tools(
        self,
        agent: str = "",
        window_minutes: int = 10080,
        limit: int = 1000,
    ) -> dict[str, ToolScore]:
        """Score every tool observed for an agent (or across all agents)."""
        cutoff = time.time() - window_minutes * 60
        samples = [
            t for t in self.traces.recent(limit=limit) if t.created_at >= cutoff
        ]
        if agent:
            samples = [t for t in samples if t.agent == agent]

        scores: dict[str, ToolScore] = {}
        latencies: dict[str, list[float]] = defaultdict(list)
        ratings: dict[str, list[float]] = defaultdict(list)

        for trace in samples:
            for tool in _trace_tools(trace):
                key = f"{trace.agent}:{tool}"
                score = scores.get(key)
                if score is None:
                    score = ToolScore(agent=trace.agent, tool=tool)
                    scores[key] = score
                score.uses += 1
                if _trace_failed(trace):
                    score.failures += 1
                else:
                    score.successes += 1
                latencies[key].append(trace.latency_ms or 0.0)
                if trace.rating and trace.rating > 0:
                    ratings[key].append(float(trace.rating))

        for key, score in scores.items():
            lats = latencies.get(key) or [0.0]
            rats = ratings.get(key) or [0.0]
            score.avg_latency_ms = sum(lats) / len(lats)
            score.avg_rating = sum(rats) / len(rats)
            # Success dominates; latency is a mild penalty; rating rewards.
            latency_penalty = min(1.0, (score.avg_latency_ms / 15_000.0))
            score.score = (
                score.success_rate * 0.6
                + (score.avg_rating / 5.0 if score.avg_rating else 0.5) * 0.25
                + (1.0 - latency_penalty) * 0.15
            )
        return scores

    def optimize_tool_selection(
        self,
        agent: str = "",
        min_samples: int = 3,
        window_minutes: int = 10080,
        drop_threshold: float = 0.35,
        promote_threshold: float = 0.8,
    ) -> dict[str, ToolPolicy]:
        """Propose a tool whitelist per agent."""
        scores = self.score_tools(agent=agent, window_minutes=window_minutes)
        if not scores:
            return {}

        by_agent: dict[str, list[ToolScore]] = defaultdict(list)
        for score in scores.values():
            by_agent[score.agent].append(score)

        policies: dict[str, ToolPolicy] = {}
        for agent_name, agent_scores in by_agent.items():
            # A tool needs repeated observation before we judge it.
            reliable = [s for s in agent_scores if s.uses >= min_samples]
            if not reliable:
                reliable = agent_scores

            keep: list[str] = []
            drop: list[str] = []
            promote: list[str] = []
            rationale: list[str] = []

            for score in sorted(agent_scores, key=lambda s: s.score, reverse=True):
                if score.uses < min_samples:
                    rationale.append(
                        f"{score.tool}: only {score.uses} use(s) — left in place "
                        "for lack of evidence."
                    )
                    keep.append(score.tool)
                    continue
                if score.score < drop_threshold and score.failures > 0:
                    drop.append(score.tool)
                    rationale.append(
                        f"{score.tool}: {score.success_rate * 100:.0f}% success "
                        f"over {score.uses} uses — drop."
                    )
                elif score.score >= promote_threshold:
                    promote.append(score.tool)
                    keep.append(score.tool)
                    rationale.append(
                        f"{score.tool}: {score.success_rate * 100:.0f}% success "
                        f"over {score.uses} uses — keep and promote."
                    )
                else:
                    keep.append(score.tool)
                    rationale.append(
                        f"{score.tool}: {score.success_rate * 100:.0f}% success "
                        f"over {score.uses} uses — keep."
                    )

            total_uses = sum(s.uses for s in agent_scores)
            rated = sum(s.uses for s in agent_scores if s.avg_rating)
            confidence = _tool_confidence(total_uses, rated, keep)
            policies[agent_name] = ToolPolicy(
                agent=agent_name,
                keep=sorted(keep),
                drop=sorted(drop),
                promote=sorted(promote),
                scores={s.tool: s.score for s in agent_scores},
                rationale=rationale,
                sample_count=total_uses,
                confidence=confidence,
            )

        self._history.append(
            {
                "type": "tool_selection",
                "policies": {k: v.to_dict() for k, v in policies.items()},
            }
        )
        return policies

    def advise(self, agent: str, limit: int = 20) -> dict[str, Any]:
        """Explain what the optimiser would change for one agent."""
        policy = self.optimize_tool_selection(agent=agent).get(agent)
        if policy is None:
            return {"agent": agent, "advice": [], "sample_count": 0}
        return {
            "agent": agent,
            "advice": policy.rationale,
            "keep": policy.keep,
            "drop": policy.drop,
            "promote": policy.promote,
            "sample_count": policy.sample_count,
            "confidence": round(policy.confidence, 3),
        }

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._history[-limit:]


class EngineSpecOptimizer:
    """Proposes per-tier engine configuration from observed behaviour."""

    def __init__(self, traces: TraceLogger, config: Any = None) -> None:
        self.traces = traces
        self.config = config
        self._history: list[dict[str, Any]] = []

    # -- public API ------------------------------------------------------ #
    def optimize_engine_spec(
        self,
        tier: str = "",
        window_minutes: int = 10080,
        min_samples: int = 3,
    ) -> dict[str, EngineSpec]:
        """Propose an engine spec per tier."""
        cutoff = time.time() - window_minutes * 60
        samples = [
            t for t in self.traces.recent(limit=1000) if t.created_at >= cutoff
        ]
        if tier:
            samples = [t for t in samples if t.tier == tier]
        if not samples:
            return {}

        by_tier: dict[str, list[Trace]] = defaultdict(list)
        for trace in samples:
            if trace.tier:
                by_tier[trace.tier].append(trace)

        specs: dict[str, EngineSpec] = {}
        for tier_name, tier_traces in by_tier.items():
            specs[tier_name] = self._spec_for_tier(tier_name, tier_traces, min_samples)
        self._history.append(
            {"type": "engine_spec", "specs": {k: v.to_dict() for k, v in specs.items()}}
        )
        return specs

    def _spec_for_tier(
        self, tier: str, traces: list[Trace], min_samples: int
    ) -> EngineSpec:
        spec = EngineSpec(tier=tier)
        spec.sample_count = len(traces)
        rationale: list[str] = []

        # -- model / provider: the one with the best weighted score ----- #
        scores: dict[str, float] = defaultdict(float)
        counts: Counter[str] = Counter()
        providers: dict[str, Counter[str]] = defaultdict(Counter)
        latencies: dict[str, list[float]] = defaultdict(list)
        qualities: dict[str, list[float]] = defaultdict(list)

        for trace in traces:
            model = trace.model or "unknown"
            counts[model] += 1
            if trace.provider:
                providers[model][trace.provider] += 1
            latencies[model].append(trace.latency_ms or 0.0)
            qualities[model].append(_quality_score(trace))

        for model, count in counts.items():
            lats = latencies[model]
            quals = qualities[model]
            avg_lat = sum(lats) / len(lats)
            avg_quality = sum(quals) / len(quals)
            # Same objective mix as the rest of the framework:
            # accuracy 0.6, latency 0.2, cost 0.1, efficiency 0.1.
            latency_component = 1.0 / (1.0 + avg_lat / 5_000.0)
            token_component = 1.0 / (1.0 + avg_lat / 40_000.0)
            scores[model] = (
                avg_quality * 0.6
                + latency_component * 0.2
                + token_component * 0.1
                + 0.1
            )

        if scores:
            best_model = max(scores, key=lambda k: scores[k])
            spec.model = best_model
            if providers.get(best_model):
                spec.provider = providers[best_model].most_common(1)[0][0]
            rationale.append(
                f"{best_model} scored highest across {counts[best_model]} traces "
                f"(accuracy 0.6 / latency 0.2 / cost 0.1 / efficiency 0.1)."
            )

        # -- quantisation: lower it when latency dominates -------------- #
        avg_latency = sum(t.latency_ms or 0.0 for t in traces) / len(traces)
        if avg_latency > 8_000:
            spec.quantization = "q5_k_m"
            rationale.append(
                f"Mean latency {avg_latency:.0f} ms is high for this tier — "
                "proposing q5_k_m quantisation to cut decode time."
            )
        elif avg_latency > 3_000:
            spec.quantization = "q8_0"
            rationale.append(
                f"Mean latency {avg_latency:.0f} ms suggests q8_0 for a "
                "quality-preserving speedup."
            )
        else:
            spec.quantization = "none"
            rationale.append(
                f"Mean latency {avg_latency:.0f} ms is comfortable — keeping "
                "full precision."
            )

        # -- context window: sized to what prompts actually needed ------ #
        prompt_lengths = [t.prompt_tokens or 0 for t in traces if t.prompt_tokens]
        if prompt_lengths:
            p95 = sorted(prompt_lengths)[int(len(prompt_lengths) * 0.95) - 1]
            # Round up to the next power-of-two-ish bucket, min 4096.
            target = max(4096, min(32768, p95 * 2))
            spec.context_window = int(target)
            rationale.append(
                f"95th-percentile prompt is {p95} tokens — proposing a "
                f"{spec.context_window} token context window."
            )

        # -- max output tokens -------------------------------------------- #
        completion_lengths = [
            t.completion_tokens or 0 for t in traces if t.completion_tokens
        ]
        if completion_lengths:
            avg_completion = sum(completion_lengths) / len(completion_lengths)
            spec.max_tokens = int(max(256, min(8192, avg_completion * 2)))
            rationale.append(
                f"Mean completion is {avg_completion:.0f} tokens — proposing "
                f"max_tokens = {spec.max_tokens}."
            )

        # -- sampling ----------------------------------------------------- #
        spec.temperature = 0.7
        spec.top_p = 0.95
        spec.top_k = 40
        spec.repetition_penalty = 1.1

        # Deterministic tiers (fast, extraction) want lower temperature.
        if tier.lower() in ("fast", "fast_tier", "fast_tier", "quick"):
            spec.temperature = 0.3
            spec.top_p = 0.9
            rationale.append(
                "Fast tier favours determinism — lowering temperature to 0.3."
            )

        # -- batch size ---------------------------------------------------- #
        spec.batch_size = 1
        if len(traces) >= 20:
            spec.batch_size = 4
            rationale.append(
                f"{len(traces)} traces on this tier — batching 4 requests "
                "would improve throughput."
            )

        rated = sum(1 for t in traces if t.rating and t.rating > 0)
        spec.confidence = _tool_confidence(len(traces), rated, [])
        spec.rationale = rationale
        return spec

    def advise(self, tier: str = "") -> dict[str, Any]:
        """Explain the proposed engine changes."""
        specs = self.optimize_engine_spec(tier=tier)
        return {
            "tier": tier,
            "specs": {k: v.to_dict() for k, v in specs.items()},
        }

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._history[-limit:]


def _tool_confidence(total_uses: int, rated: int, keep: Sequence[str]) -> float:
    """Confidence in a policy, based on how much evidence backs it."""
    if total_uses <= 0:
        return 0.0
    evidence = min(1.0, total_uses / 20.0)
    rating_evidence = min(1.0, rated / max(1, total_uses))
    coverage = min(1.0, len(keep) / 5.0) if keep else 0.5
    return round(min(1.0, evidence * 0.5 + rating_evidence * 0.3 + coverage * 0.2), 3)
