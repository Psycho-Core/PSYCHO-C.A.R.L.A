"""
PSYCHO-C.A.R.L.A. — Bench & Eval implementation.

Design goals
------------
* **Model-agnostic** — the harness drives the runtime's router, so it
  works with every adapter without special-casing.
* **Deterministic** — cases are replayable; temperature and seed are
  pinned by default.
* **Honest** — a metric that could not be measured is reported as
  ``None``, never as zero.

Two entry points:

``Bench``
    Performance measurement. Runs a workload, records latency, TTFT,
    tokens/second, energy and FLOPs, then aggregates into a report.

``EvalSuite``
    Quality measurement. Runs graded cases, compares against expected
    outcomes, and reports accuracy per suite.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# --------------------------------------------------------------------------- #
# Bench
# --------------------------------------------------------------------------- #


@dataclass
class BenchCase:
    """One measured interaction."""

    name: str
    prompt: str
    agent: str = "orchestrator"
    tier: str = ""
    expect_contains: list[str] = field(default_factory=list)
    expect_not_contains: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "agent": self.agent,
            "tier": self.tier,
            "expect_contains": list(self.expect_contains),
            "expect_not_contains": list(self.expect_not_contains),
            "metadata": dict(self.metadata),
        }


@dataclass
class BenchWorkload:
    """A named collection of cases to measure."""

    name: str
    cases: list[BenchCase] = field(default_factory=list)
    description: str = ""

    @classmethod
    def chat(cls, turns: int = 10, agent: str = "orchestrator", tier: str = "") -> "BenchWorkload":
        """A synthetic conversational workload of `turns` prompts."""
        prompts = [
            "Give me a one-paragraph briefing on today's engineering priorities.",
            "List three risks in the current deployment pipeline.",
            "Summarise the trade-off between latency and accuracy.",
            "What would you check first when a service starts returning 500s?",
            "Draft a short status update for the team.",
            "Explain what a retrieval-augmented pipeline actually does.",
            "Name two metrics that matter for a local inference server.",
            "How would you decide a regression is worth fixing today?",
            "Write a one-line rollback plan.",
            "What is the strongest argument against adding more tooling?",
        ]
        cases = [
            BenchCase(
                name=f"turn_{i + 1}",
                prompt=prompts[i % len(prompts)],
                agent=agent,
                tier=tier,
            )
            for i in range(max(1, turns))
        ]
        return cls(
            name="chat",
            cases=cases,
            description="Synthetic conversational turns.",
        )

    @classmethod
    def tool_use(cls, turns: int = 8, agent: str = "orchestrator") -> "BenchWorkload":
        """A workload that exercises tool selection."""
        prompts = [
            "What is 17 * 23? Use the calculator.",
            "List the files in the current directory.",
            "Search the web for the latest Python release.",
            "Run 'echo carla' and tell me the output.",
            "Which Python is on the PATH?",
            "Write a short note to notes.txt and read it back.",
            "Calculate 100 / 7 and round to two decimals.",
            "Show me the environment variables for PATH.",
        ]
        cases = [
            BenchCase(
                name=f"tool_{i + 1}",
                prompt=prompts[i % len(prompts)],
                agent=agent,
            )
            for i in range(max(1, turns))
        ]
        return cls(
            name="tool_use",
            cases=cases,
            description="Tool-selection and execution workload.",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "cases": [c.to_dict() for c in self.cases],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchWorkload":
        return cls(
            name=data.get("name", "custom"),
            description=data.get("description", ""),
            cases=[
                BenchCase(
                    name=c.get("name", f"case_{i}"),
                    prompt=c.get("prompt", ""),
                    agent=c.get("agent", "orchestrator"),
                    tier=c.get("tier", ""),
                    expect_contains=list(c.get("expect_contains", [])),
                    expect_not_contains=list(c.get("expect_not_contains", [])),
                    metadata=dict(c.get("metadata", {})),
                )
                for i, c in enumerate(data.get("cases", []))
            ],
        )


@dataclass
class BenchResult:
    """The measured outcome of one bench case."""

    name: str
    agent: str
    tier: str
    ok: bool = True
    error: str = ""
    latency_ms: float = 0.0
    ttft_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_per_second: float = 0.0
    energy_joules: float = 0.0
    flops: float = 0.0
    output_preview: str = ""
    output_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "agent": self.agent,
            "tier": self.tier,
            "ok": self.ok,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "ttft_ms": round(self.ttft_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tokens_per_second": round(self.tokens_per_second, 2),
            "energy_joules": round(self.energy_joules, 4),
            "flops": self.flops,
            "output_preview": self.output_preview,
        }


@dataclass
class BenchReport:
    """Aggregated bench results."""

    workload: str
    results: list[BenchResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        ok = [r for r in self.results if r.ok]
        failed = [r for r in self.results if not r.ok]
        latencies = [r.latency_ms for r in ok] or [0.0]
        ttfts = [r.ttft_ms for r in ok if r.ttft_ms] or []
        tps = [r.tokens_per_second for r in ok if r.tokens_per_second] or []
        total_tokens = sum(r.total_tokens for r in self.results)
        total_joules = sum(r.energy_joules for r in self.results)
        total_flops = sum(r.flops for r in self.results)
        return {
            "workload": self.workload,
            "cases": len(self.results),
            "passed": len(ok),
            "failed": len(failed),
            "success_rate": round(len(ok) / len(self.results) * 100, 2)
            if self.results
            else 0.0,
            "duration_s": round(self.duration_s, 3),
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 2),
                "median": round(statistics.median(latencies), 2),
                "p95": round(_percentile(latencies, 95), 2),
                "min": round(min(latencies), 2),
                "max": round(max(latencies), 2),
            },
            "ttft_ms": {
                "mean": round(statistics.fmean(ttfts), 2) if ttfts else None,
                "median": round(statistics.median(ttfts), 2) if ttfts else None,
                "p95": round(_percentile(ttfts, 95), 2) if ttfts else None,
            },
            "tokens_per_second": {
                "mean": round(statistics.fmean(tps), 2) if tps else None,
                "median": round(statistics.median(tps), 2) if tps else None,
            },
            "total_tokens": total_tokens,
            "energy": {
                "total_joules": round(total_joules, 4),
                "joules_per_case": round(total_joules / len(self.results), 6)
                if self.results
                else 0.0,
            },
            "total_flops": total_flops,
            "flops_per_token": round(total_flops / total_tokens, 2) if total_tokens else 0.0,
            "errors": [
                {"name": r.name, "error": r.error} for r in failed[:20]
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload": self.workload,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


class Bench:
    """Measures performance of the agent stack against a workload."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def run(
        self,
        workload: BenchWorkload,
        *,
        agent: str = "",
        tier: str = "",
        repeat: int = 1,
        verbose: bool = False,
    ) -> BenchReport:
        """Execute a workload and return the aggregated report."""
        report = BenchReport(workload=workload.name)
        started = time.perf_counter()

        for _ in range(max(1, repeat)):
            for case in workload.cases:
                result = self._run_case(
                    case,
                    agent=agent or case.agent,
                    tier=tier or case.tier,
                )
                report.results.append(result)
                if verbose:
                    status = "ok" if result.ok else "FAIL"
                    print(
                        f"  [{status}] {result.name}: {result.latency_ms:.0f} ms, "
                        f"{result.total_tokens} tok"
                    )
        report.duration_s = time.perf_counter() - started
        return report

    def _run_case(self, case: BenchCase, *, agent: str, tier: str) -> BenchResult:
        result = BenchResult(name=case.name, agent=agent, tier=tier)
        try:
            rt = self.runtime
            before = self._telemetry_snapshot()

            t0 = time.perf_counter()
            response = self._invoke(case.prompt, agent=agent, tier=tier)
            result.latency_ms = (time.perf_counter() - t0) * 1000

            text = _extract_text(response)
            result.output_text = text
            result.output_preview = text[:280]
            result.prompt_tokens = _tokens_of(response, "prompt_tokens")
            result.completion_tokens = _tokens_of(response, "completion_tokens")
            result.total_tokens = _tokens_of(response, "total_tokens")
            if not result.total_tokens:
                result.total_tokens = result.prompt_tokens + result.completion_tokens
            if response_error := _response_error(response):
                result.ok = False
                result.error = response_error
            if result.latency_ms > 0 and result.completion_tokens:
                result.tokens_per_second = result.completion_tokens / (
                    result.latency_ms / 1000.0
                )
            result.ttft_ms = getattr(response, "time_to_first_token_ms", 0.0) or 0.0

            # Content assertions, when the case specifies them.
            for needle in case.expect_contains:
                if needle.lower() not in text.lower():
                    result.ok = False
                    result.error = f"expected to contain {needle!r}"
                    break
            if result.ok:
                for needle in case.expect_not_contains:
                    if needle.lower() in text.lower():
                        result.ok = False
                        result.error = f"expected NOT to contain {needle!r}"
                        break

            after = self._telemetry_snapshot()
            result.energy_joules = max(
                0.0, after.get("energy_joules", 0.0) - before.get("energy_joules", 0.0)
            )
            result.flops = max(0.0, after.get("flops", 0.0) - before.get("flops", 0.0))
        except Exception as exc:  # noqa: BLE001 - bench must never abort
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
        return result

    def _invoke(self, prompt: str, *, agent: str, tier: str) -> Any:
        """Call the agent stack, preferring the runtime's high-level API."""
        rt = self.runtime
        # Preferred: the runtime's ask/chat entry point.
        for name in ("ask", "chat", "run_agent", "dispatch"):
            fn = getattr(rt, name, None)
            if callable(fn):
                try:
                    return fn(prompt, agent=agent)
                except TypeError:
                    return fn(prompt)
        # Fallback: drive the router directly.
        router = getattr(rt, "router", None)
        if router is None:
            raise RuntimeError("runtime exposes no ask/chat/run_agent/router API")
        messages = [{"role": "user", "content": prompt}]
        if tier:
            return router.generate(messages, capability_tier=tier)
        return router.generate(messages)

    def _telemetry_snapshot(self) -> dict[str, float]:
        """Snapshot cumulative energy/FLOPs so a case delta can be computed."""
        telemetry = getattr(self.runtime, "telemetry", None)
        if telemetry is None:
            return {}
        try:
            summary = telemetry.summary(window_minutes=5)
        except Exception:
            return {}
        energy = summary.get("energy", {})
        return {
            "energy_joules": float(energy.get("total_joules", 0.0) or 0.0),
            "flops": float(summary.get("total_flops", 0.0) or 0.0),
        }


# --------------------------------------------------------------------------- #
# Eval
# --------------------------------------------------------------------------- #


@dataclass
class EvalCase:
    """A graded case with expected outcomes."""

    name: str
    prompt: str
    agent: str = "orchestrator"
    tier: str = ""
    expect_contains: list[str] = field(default_factory=list)
    expect_not_contains: list[str] = field(default_factory=list)
    expect_regex: list[str] = field(default_factory=list)
    min_tokens: int = 0
    max_latency_ms: float = 0.0
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    scorer: str = ""      # named scorer from bench.scorers (exact/numeric/…)
    expected: str = ""    # scorer's expected value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prompt": self.prompt,
            "agent": self.agent,
            "tier": self.tier,
            "expect_contains": list(self.expect_contains),
            "expect_not_contains": list(self.expect_not_contains),
            "expect_regex": list(self.expect_regex),
            "min_tokens": self.min_tokens,
            "max_latency_ms": self.max_latency_ms,
            "weight": self.weight,
            "metadata": dict(self.metadata),
            "scorer": self.scorer,
            "expected": self.expected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "EvalCase":
        return cls(
            name=data.get("name", f"case_{index + 1}"),
            prompt=data.get("prompt", ""),
            agent=data.get("agent", "orchestrator"),
            tier=data.get("tier", ""),
            expect_contains=list(data.get("expect_contains", [])),
            expect_not_contains=list(data.get("expect_not_contains", [])),
            expect_regex=list(data.get("expect_regex", [])),
            min_tokens=int(data.get("min_tokens", 0) or 0),
            max_latency_ms=float(data.get("max_latency_ms", 0.0) or 0.0),
            weight=float(data.get("weight", 1.0) or 1.0),
            metadata=dict(data.get("metadata", {})),
            scorer=str(data.get("scorer", "") or ""),
            expected=str(data.get("expected", "") or ""),
        )


@dataclass
class EvalResult:
    """The graded outcome of one eval case."""

    name: str
    passed: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    total_tokens: int = 0
    error: str = ""
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "latency_ms": round(self.latency_ms, 2),
            "total_tokens": self.total_tokens,
            "error": self.error,
            "weight": self.weight,
        }


@dataclass
class EvalReport:
    """Aggregated eval results."""

    suite: str
    results: list[EvalResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        passed = [r for r in self.results if r.passed]
        failed = [r for r in self.results if not r.passed]
        total_weight = sum(r.weight for r in self.results) or 1.0
        earned = sum(r.weight for r in passed)
        latencies = [r.latency_ms for r in self.results] or [0.0]
        return {
            "suite": self.suite,
            "cases": len(self.results),
            "passed": len(passed),
            "failed": len(failed),
            "accuracy": round(earned / total_weight * 100, 2),
            "duration_s": round(self.duration_s, 3),
            "avg_latency_ms": round(statistics.fmean(latencies), 2),
            "total_tokens": sum(r.total_tokens for r in self.results),
            "failures": [
                {"name": r.name, "failures": r.failures, "error": r.error}
                for r in failed[:20]
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class EvalSuite:
    """A named collection of graded cases."""

    def __init__(self, name: str, cases: list[EvalCase]) -> None:
        self.name = name
        self.cases = cases

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalSuite":
        return cls(
            name=data.get("name", "eval"),
            cases=[
                EvalCase.from_dict(c, i) for i, c in enumerate(data.get("cases", []))
            ],
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "EvalSuite":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "cases": [c.to_dict() for c in self.cases]}

    def run(self, runtime: Any, *, verbose: bool = False) -> EvalReport:
        """Execute every case and grade the output."""
        report = EvalReport(suite=self.name)
        started = time.perf_counter()
        bench = Bench(runtime)

        for case in self.cases:
            result = EvalResult(name=case.name, weight=case.weight)
            try:
                bench_result = bench._run_case(
                    BenchCase(
                        name=case.name,
                        prompt=case.prompt,
                        agent=case.agent,
                        tier=case.tier,
                    ),
                    agent=case.agent,
                    tier=case.tier,
                )
                result.latency_ms = bench_result.latency_ms
                result.total_tokens = bench_result.total_tokens
                if bench_result.error and not bench_result.ok:
                    result.error = bench_result.error
                full = bench_result.output_text or bench_result.output_preview

                if case.expect_contains:
                    for needle in case.expect_contains:
                        hit = needle.lower() in full.lower()
                        result.checks[f"contains:{needle}"] = hit
                        if not hit:
                            result.failures.append(f"missing {needle!r}")
                if case.expect_not_contains:
                    for needle in case.expect_not_contains:
                        hit = needle.lower() not in full.lower()
                        result.checks[f"not_contains:{needle}"] = hit
                        if not hit:
                            result.failures.append(f"unexpectedly contains {needle!r}")
                if case.expect_regex:
                    for pattern in case.expect_regex:
                        hit = bool(re.search(pattern, full, re.IGNORECASE))
                        result.checks[f"regex:{pattern}"] = hit
                        if not hit:
                            result.failures.append(f"regex {pattern!r} did not match")
                if case.min_tokens:
                    hit = result.total_tokens >= case.min_tokens
                    result.checks[f"min_tokens:{case.min_tokens}"] = hit
                    if not hit:
                        result.failures.append(
                            f"only {result.total_tokens} tokens (need {case.min_tokens})"
                        )
                if case.max_latency_ms:
                    hit = 0 < result.latency_ms <= case.max_latency_ms
                    result.checks[f"max_latency:{case.max_latency_ms}"] = hit
                    if not hit:
                        result.failures.append(
                            f"latency {result.latency_ms:.0f} ms exceeds "
                            f"{case.max_latency_ms:.0f} ms"
                        )
                if case.scorer:
                    from .scorers import score as _score

                    hit, detail = _score(case.scorer, full, case.expected)
                    result.checks[f"scorer:{case.scorer}"] = hit
                    if not hit:
                        result.failures.append(
                            f"scorer {case.scorer}: {detail}")
                result.passed = not result.failures and not result.error
            except Exception as exc:  # noqa: BLE001
                result.error = f"{type(exc).__name__}: {exc}"
                result.passed = False
            report.results.append(result)
            if verbose:
                status = "PASS" if result.passed else "FAIL"
                print(f"  [{status}] {result.name}")
        report.duration_s = time.perf_counter() - started
        return report


def compare_reports(baseline: EvalReport, candidate: EvalReport) -> dict[str, Any]:
    """Diff two eval reports case-by-case (regressions first)."""
    base = {r.name: r for r in baseline.results}
    cand = {r.name: r for r in candidate.results}
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    unchanged = 0
    for name in sorted(set(base) | set(cand)):
        b, c = base.get(name), cand.get(name)
        if b is None:
            improvements.append({"name": name, "change": "new (passed)"
                                 if c and c.passed else "new (failed)"})
        elif c is None:
            regressions.append({"name": name, "change": "removed"})
        elif b.passed and not c.passed:
            regressions.append({"name": name, "change": "pass→fail",
                                "failures": list(c.failures)})
        elif not b.passed and c.passed:
            improvements.append({"name": name, "change": "fail→pass"})
        else:
            unchanged += 1
    base_summary, cand_summary = baseline.summary(), candidate.summary()
    return {
        "baseline_suite": baseline.suite,
        "candidate_suite": candidate.suite,
        "baseline_accuracy": base_summary["accuracy"],
        "candidate_accuracy": cand_summary["accuracy"],
        "accuracy_delta": round(
            cand_summary["accuracy"] - base_summary["accuracy"], 2),
        "latency_delta_ms": round(
            cand_summary["avg_latency_ms"] - base_summary["avg_latency_ms"], 2),
        "regressions": regressions,
        "improvements": improvements,
        "unchanged": unchanged,
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _extract_text(response: Any) -> str:
    """Pull plain text out of whatever shape the runtime returned."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    for attr in ("text", "content", "output", "message"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    if isinstance(response, dict):
        for key in ("text", "content", "output", "message"):
            val = response.get(key)
            if isinstance(val, str) and val:
                return val
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message") or {}
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    return msg["content"]
    return str(response)


def _tokens_of(response: Any, key: str) -> int:
    """Read a token count from a response, whatever shape it arrives in."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if isinstance(usage, dict):
        try:
            return int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
    if usage is not None:
        val = getattr(usage, key, 0)
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0
    # Flat dict form: {"prompt_tokens": 10, ...}
    if isinstance(response, dict):
        try:
            return int(response.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _response_error(response: Any) -> str:
    """Return an error string if the runtime reported one."""
    if isinstance(response, dict):
        err = response.get("error")
        if err:
            return str(err)
    err = getattr(response, "error", None)
    return str(err) if err else ""


def _last_response_text(runtime: Any) -> str:
    """Best-effort retrieval of the full last response from the runtime."""
    for attr in ("last_response", "last_reply", "last_output"):
        val = getattr(runtime, attr, None)
        if isinstance(val, str) and val:
            return val
    history = getattr(runtime, "history", None)
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, str):
            return last
        if isinstance(last, dict):
            return _extract_text(last)
    return ""
