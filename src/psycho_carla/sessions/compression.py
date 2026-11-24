"""
Session compression — turning old messages into a compact anchor.

The summariser is deliberately **extractive**, not abstractive: it selects
the most informative lines from the old messages and stitches them into a
readable anchor. An abstractive summary would need a model call, which
would make consolidation slow, expensive and non-deterministic — and
consolidation runs on a background path where determinism matters.

If a model *is* available, the anchor can be improved later; the extractive
version is always safe to fall back to.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

# Roughly four characters per token for English prose.
_CHARS_PER_TOKEN = 4

# Lines that carry the most signal in a transcript.
_SIGNAL_PATTERNS = (
    r"^(?:user|assistant|you|carla)\s*:",
    r"^\s*[-*]\s+",
    r"\b(?:because|so that|therefore|hence|thus|in order to|which means)\b",
    r"\b(?:error|failed|exception|warning|note that|important|remember)\b",
    r"^\s*\d+\.\s",
    r"\b(?:decided|agreed|conclusion|summary|answer|question)\b",
)

_SIGNAL_RE = re.compile("|".join(_SIGNAL_PATTERNS), re.IGNORECASE)

# Boilerplate that carries no information.
_NOISE_RE = re.compile(
    r"^(?:ok(?:ay)?|sure|thanks?|thank you|yes|no|got it|understood|"
    r"hello|hi|hey|bye|goodbye)[.!]?$",
    re.IGNORECASE,
)


def estimate_tokens(messages: Sequence[Any]) -> int:
    """Estimate the token cost of a message list.

    Accepts dicts with a ``content`` key, or objects with a ``content``
    attribute.
    """
    total = 0
    for message in messages:
        content = ""
        if isinstance(message, dict):
            content = str(message.get("content", ""))
        else:
            content = str(getattr(message, "content", "") or "")
        total += max(1, len(content) // _CHARS_PER_TOKEN)
    return total


def consolidate_messages(
    messages: Sequence[Any],
    *,
    max_tokens: int = 4000,
) -> str:
    """Build an anchor summarising a run of messages.

    Returns an empty string when there is nothing worth keeping.
    """
    lines: list[str] = []
    for message in messages:
        role = ""
        content = ""
        if isinstance(message, dict):
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
        else:
            role = str(getattr(message, "role", "") or "")
            content = str(getattr(message, "content", "") or "")
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or _NOISE_RE.match(line):
                continue
            lines.append(f"{role}: {line}" if role else line)

    if not lines:
        return ""

    # Score each line by how much signal it carries.
    scored: list[tuple[float, int, str]] = []
    for index, line in enumerate(lines):
        score = 1.0
        if _SIGNAL_RE.search(line):
            score += 2.0
        if len(line) > 80:
            score += 0.5
        if len(line) < 15:
            score -= 0.5
        # Later lines are usually more relevant to where the conversation
        # ended up.
        score += index / max(1, len(lines)) * 0.5
        scored.append((score, index, line))

    scored.sort(key=lambda item: (-item[0], item[1]))
    budget = max_tokens * _CHARS_PER_TOKEN
    kept: list[tuple[int, str]] = []
    used = 0
    for score, index, line in scored:
        if used + len(line) > budget:
            continue
        kept.append((index, line))
        used += len(line)

    if not kept:
        return ""
    kept.sort(key=lambda item: item[0])
    body = "\n".join(line for _, line in kept)
    return (
        "[Earlier conversation summary]\n"
        f"{body}\n"
        f"[{len(kept)} of {len(lines)} points retained]"
    )


__all__ = ["consolidate_messages", "estimate_tokens"]
