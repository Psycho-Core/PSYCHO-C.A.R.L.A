"""
Eval scorers — deterministic output graders.

Each scorer maps ``(output, expected) -> (passed, detail)``. They are the
depth layer under :class:`~psycho_carla.bench.runner.EvalSuite`: a case
names a scorer and an expected value, and the scorer decides. No model
calls, no randomness — an eval that cannot be reproduced is not an eval.

Scorers:
  * ``exact``       — trimmed output equals expected
  * ``contains``    — expected appears (case-insensitive) in output
  * ``regex``       — expected is a pattern that must match
  * ``numeric``     — last number in output equals expected within tolerance
  * ``json``        — output (or its first JSON block) parses; expected may
                      name keys that must be present, comma-separated
  * ``length``      — expected is ``min:max`` character bounds
  * ``not_contains``— expected must NOT appear (case-insensitive)

Layer: Bench & Eval.
"""

from __future__ import annotations

import json
import re
from typing import Callable

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.S)


def _exact(output: str, expected: str) -> tuple[bool, str]:
    ok = (output or "").strip() == (expected or "").strip()
    return ok, "" if ok else f"expected exactly {expected!r}"


def _contains(output: str, expected: str) -> tuple[bool, str]:
    ok = (expected or "").lower() in (output or "").lower()
    return ok, "" if ok else f"missing {expected!r}"


def _not_contains(output: str, expected: str) -> tuple[bool, str]:
    ok = (expected or "").lower() not in (output or "").lower()
    return ok, "" if ok else f"unexpectedly contains {expected!r}"


def _regex(output: str, expected: str) -> tuple[bool, str]:
    try:
        ok = bool(re.search(expected, output or "", re.IGNORECASE | re.S))
    except re.error as exc:
        return False, f"bad pattern: {exc}"
    return ok, "" if ok else f"pattern {expected!r} did not match"


def _numeric(output: str, expected: str) -> tuple[bool, str]:
    try:
        want = float(str(expected).split(":")[0])
        tol = float(str(expected).split(":")[1]) if ":" in str(expected) else 1e-6
    except (TypeError, ValueError):
        return False, f"expected value {expected!r} is not numeric"
    numbers = _NUMBER.findall(output or "")
    if not numbers:
        return False, "no number in output"
    got = float(numbers[-1])
    ok = abs(got - want) <= tol
    return ok, "" if ok else f"got {got}, want {want} (±{tol})"


def _json(output: str, expected: str) -> tuple[bool, str]:
    text = (output or "").strip()
    payload = None
    try:
        payload = json.loads(text)
    except Exception:
        match = _JSON_BLOCK.search(text)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception:
                payload = None
    if payload is None:
        return False, "output is not valid JSON"
    keys = [k.strip() for k in (expected or "").split(",") if k.strip()]
    if keys and isinstance(payload, dict):
        missing = [k for k in keys if k not in payload]
        if missing:
            return False, f"JSON missing keys: {', '.join(missing)}"
    return True, ""


def _length(output: str, expected: str) -> tuple[bool, str]:
    try:
        low, high = (int(p) for p in str(expected).split(":", 1))
    except (TypeError, ValueError):
        return False, "length expected must be 'min:max'"
    size = len(output or "")
    ok = low <= size <= high
    return ok, "" if ok else f"length {size} outside {low}:{high}"


SCORERS: dict[str, Callable[[str, str], tuple[bool, str]]] = {
    "exact": _exact,
    "contains": _contains,
    "not_contains": _not_contains,
    "regex": _regex,
    "numeric": _numeric,
    "json": _json,
    "length": _length,
}


def score(scorer: str, output: str, expected: str) -> tuple[bool, str]:
    """Run one scorer. Unknown scorers fail closed with an explanation."""
    fn = SCORERS.get((scorer or "").lower())
    if fn is None:
        return False, f"unknown scorer {scorer!r}"
    return fn(output or "", expected or "")


__all__ = ["SCORERS", "score"]
