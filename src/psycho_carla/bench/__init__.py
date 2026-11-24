"""
PSYCHO-C.A.R.L.A. — Bench & Eval harness.

OpenJarvis ships two distinct things under ``bench/`` and ``evals/``:

  * **Bench** — a repeatable *performance* measurement: run a fixed
    workload against the agent stack and report latency, throughput,
    token cost, energy and FLOPs.
  * **Eval** — a *quality* measurement: run a set of graded cases and
    report accuracy against expected outcomes.

This package implements both, locally and deterministically:

    from psycho_carla.bench import Bench, EvalSuite

    bench = Bench(runtime)
    report = bench.run(BenchWorkload(name="chat", turns=20))
    print(report.summary())

    suite = EvalSuite.from_json("evals/reliability.json")
    result = suite.run(runtime)
"""

from .datasets import BUILTIN_SUITES, get_suite, suite_names
from .runner import (
    Bench,
    BenchCase,
    BenchReport,
    BenchResult,
    BenchWorkload,
    EvalCase,
    EvalReport,
    EvalSuite,
    EvalResult,
    compare_reports,
)
from .scorers import SCORERS, score

__all__ = [
    "BUILTIN_SUITES",
    "SCORERS",
    "Bench",
    "BenchCase",
    "BenchReport",
    "BenchResult",
    "BenchWorkload",
    "EvalCase",
    "EvalReport",
    "EvalResult",
    "EvalSuite",
    "compare_reports",
    "get_suite",
    "score",
    "suite_names",
]
