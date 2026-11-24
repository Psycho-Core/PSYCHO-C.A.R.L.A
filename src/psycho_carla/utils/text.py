"""Small text utilities used across the framework."""

from __future__ import annotations

import re
from typing import Iterable, Iterator


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token for English prose."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate(text: str, max_chars: int, suffix: str = "…") -> str:
    """Truncate on a word boundary."""
    if not text or len(text) <= max_chars:
        return text or ""
    cut = text[:max_chars]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + suffix


def word_wrap(text: str, width: int = 88) -> str:
    """Wrap text to a column width, preserving paragraph breaks."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        lines: list[str] = []
        for raw_line in para.splitlines():
            if not raw_line.strip():
                lines.append("")
                continue
            lines.extend(textwrap_wrap(raw_line, width))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def textwrap_wrap(line: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(line, width=width) or [""]


def chunk_text(text: str, size: int = 1024, overlap: int = 128) -> list[str]:
    """Split text into overlapping chunks."""
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    step = max(1, size - overlap)
    for i in range(0, len(text), step):
        piece = text[i : i + size]
        if piece:
            chunks.append(piece)
        if i + size >= len(text):
            break
    return chunks
