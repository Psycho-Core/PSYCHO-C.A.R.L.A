"""Export conversations — as readable Markdown/JSON, or as a fine-tuning dataset.

The training export writes plain chat-format JSONL
(``{"messages": [{"role": ..., "content": ...}, ...]}`` per line), which
the common LoRA trainers accept. It never leaves the machine.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


def _conversation(memory: Any, session_id: str) -> list[dict[str, Any]]:
    rows = memory.get_conversation(session_id, limit=100000)
    return [
        {"role": r.role, "content": r.content, "created_at": getattr(r, "created_at", 0)}
        for r in rows
    ]


def export_sessions(
    memory: Any, fmt: str = "markdown", session_id: str = "", limit: int = 50
) -> str:
    """Render one session (or the most recent ``limit``) as markdown or json."""
    if session_id:
        sessions = [{"session_id": session_id, "title": session_id}]
    else:
        sessions = memory.list_sessions(limit=limit)
    data = [
        {"session_id": s["session_id"], "title": s.get("title", ""),
         "messages": _conversation(memory, s["session_id"])}
        for s in sessions
    ]
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    out: list[str] = ["# C.A.R.L.A. conversations\n"]
    for sess in data:
        out.append(f"\n## {sess['title'] or sess['session_id']}\n")
        for m in sess["messages"]:
            who = {"user": "You", "assistant": "C.A.R.L.A."}.get(m["role"], m["role"])
            out.append(f"**{who}:** {m['content']}\n")
    return "\n".join(out)


def training_pairs(memory: Any, limit: int = 1000, min_turns: int = 1) -> Iterable[dict[str, Any]]:
    """Yield chat-format training examples from stored sessions.

    Only sessions with at least ``min_turns`` user→assistant exchanges are
    included, and only user/assistant messages (no tool chatter).
    """
    for s in memory.list_sessions(limit=limit):
        msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in _conversation(memory, s["session_id"])
            if m["role"] in ("user", "assistant") and (m["content"] or "").strip()
        ]
        exchanges = sum(1 for m in msgs if m["role"] == "assistant")
        if exchanges >= min_turns and msgs and msgs[0]["role"] == "user":
            yield {"messages": msgs}


def export_training_jsonl(memory: Any, limit: int = 1000, min_turns: int = 1) -> str:
    return "\n".join(
        json.dumps(row, ensure_ascii=False) for row in training_pairs(memory, limit, min_turns)
    )
