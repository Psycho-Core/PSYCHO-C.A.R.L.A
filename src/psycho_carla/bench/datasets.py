"""
Built-in eval suites — deterministic, offline-friendly datasets.

Upstream ships dataset adapters for public benchmarks (GAIA, MMLU-Pro,
GPQA, …) that need network downloads. This build ships small, fixed,
self-contained suites that exercise the same muscles — reasoning,
knowledge, instruction-following, coding, persona invariants — against
whatever engine the tiers point at, including the offline fallback.
Cases grade with the scorers in :mod:`psycho_carla.bench.scorers` plus
the runner's contains/regex checks, so results are reproducible.

Suites: ``core``, ``reasoning``, ``knowledge``, ``instruction``,
``coding``, ``all`` (the union).

Layer: Bench & Eval.
"""

from __future__ import annotations

from .runner import EvalCase, EvalSuite

CORE = EvalSuite(
    name="core",
    cases=[
        EvalCase(name="salutation_user",
                 prompt="Introduce yourself briefly.",
                 expect_contains=["Psycho"]),
        EvalCase(name="no_robotic_filler",
                 prompt="Introduce yourself briefly.",
                 expect_not_contains=["As an AI language model"]),
        EvalCase(name="non_empty_response",
                 prompt="Say hello.", min_tokens=1),
        EvalCase(name="math_response",
                 prompt="What is 2 + 2? Answer with the number.",
                 scorer="numeric", expected="4"),
    ],
)

REASONING = EvalSuite(
    name="reasoning",
    cases=[
        EvalCase(name="arith_add", scorer="numeric", expected="19",
                 prompt="What is 12 + 7? Answer with just the number."),
        EvalCase(name="arith_mul", scorer="numeric", expected="42",
                 prompt="What is 6 * 7? Answer with just the number."),
        EvalCase(name="arith_chain", scorer="numeric", expected="16",
                 prompt="A shop sells pens in packs of 4. You buy 3 packs "
                        "and give away 2 pens, then buy 1 more pack. How "
                        "many pens do you have? Answer with just the number."),
        EvalCase(name="sequence_next", scorer="numeric", expected="16",
                 prompt="What is the next number in the sequence "
                        "2, 4, 8, ...? Answer with just the number."),
        EvalCase(name="unit_convert", scorer="numeric", expected="500:0.001",
                 prompt="How many grams are in 0.5 kilograms? Answer with "
                        "just the number."),
        EvalCase(name="logic_all", scorer="contains", expected="yes",
                 prompt="All bloops are razzies. All razzies are lazzies. "
                        "Are all bloops definitely lazzies? Answer yes or no."),
    ],
)

KNOWLEDGE = EvalSuite(
    name="knowledge",
    cases=[
        EvalCase(name="capital_france", scorer="contains", expected="Paris",
                 prompt="What is the capital of France? One word."),
        EvalCase(name="capital_australia", scorer="contains", expected="Canberra",
                 prompt="What is the capital of Australia? One word."),
        EvalCase(name="water_formula", scorer="regex", expected=r"H2O",
                 prompt="What is the chemical formula for water?"),
        EvalCase(name="planet_red", scorer="contains", expected="Mars",
                 prompt="Which planet is known as the Red Planet? One word."),
        EvalCase(name="boiling_point", scorer="numeric", expected="100:0.5",
                 prompt="At sea level, at what temperature in Celsius does "
                        "water boil? Answer with just the number."),
    ],
)

INSTRUCTION = EvalSuite(
    name="instruction",
    cases=[
        EvalCase(name="json_shape", scorer="json", expected="name,role",
                 prompt="Reply with only a JSON object with keys \"name\" "
                        "and \"role\". Use name \"carla\" and role "
                        "\"assistant\". No other text."),
        EvalCase(name="exact_word", scorer="exact", expected="acknowledged",
                 prompt="Reply with exactly the single word: acknowledged"),
        EvalCase(name="short_answer", scorer="length", expected="1:120",
                 prompt="In one short sentence, what is a hash map?"),
        EvalCase(name="list_format", scorer="regex",
                 expected=r"(?m)^\s*[-*1]\.?\s+Red\b",
                 prompt="List these colours as a bullet list, one per "
                        "line: Red, Blue, Green."),
        EvalCase(name="no_forbidden_word", scorer="not_contains",
                 expected="banana",
                 prompt="Describe an apple in one sentence without using "
                        "the word banana."),
    ],
)

CODING = EvalSuite(
    name="coding",
    cases=[
        EvalCase(name="python_def", scorer="regex", expected=r"def\s+add\s*\(",
                 prompt="Write a Python function `add(a, b)` that returns "
                        "the sum. Code only."),
        EvalCase(name="sql_select", scorer="regex",
                 expected=r"SELECT\s+.+\s+FROM\s+users",
                 prompt="Write a SQL query selecting all columns from the "
                        "users table. Code only."),
        EvalCase(name="complexity", scorer="contains", expected="O(n)",
                 prompt="What is the time complexity of linear search? "
                        "Answer in Big-O notation."),
        EvalCase(name="fix_bug", scorer="contains", expected="==",
                 prompt="This Python line has a bug: `if x = 5: print(x)`. "
                        "Show the corrected comparison operator."),
    ],
)

BUILTIN_SUITES: dict[str, EvalSuite] = {
    suite.name: suite
    for suite in (CORE, REASONING, KNOWLEDGE, INSTRUCTION, CODING)
}
BUILTIN_SUITES["all"] = EvalSuite(
    name="all",
    cases=[case for suite in (CORE, REASONING, KNOWLEDGE, INSTRUCTION, CODING)
           for case in suite.cases],
)


def get_suite(name: str) -> EvalSuite | None:
    """One built-in suite by name (core/reasoning/knowledge/instruction/coding/all)."""
    return BUILTIN_SUITES.get((name or "").lower())


def suite_names() -> list[str]:
    return sorted(BUILTIN_SUITES)


__all__ = ["BUILTIN_SUITES", "CODING", "CORE", "INSTRUCTION", "KNOWLEDGE",
           "REASONING", "get_suite", "suite_names"]
