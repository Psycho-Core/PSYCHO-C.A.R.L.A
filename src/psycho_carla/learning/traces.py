"""
PSYCHO-C.A.R.L.A. — Interaction Trace Logger.

Every exchange is recorded: prompt, response, agent, tier, latency, token
usage and tool calls. These traces are the raw material for the learning loop
(prompt optimisation and routing policy learning).

Layer: Pillar 5 (Learning).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL,
    prompt TEXT,
    response TEXT,
    agent TEXT,
    tier TEXT,
    provider TEXT,
    model TEXT,
    latency_ms REAL,
    tokens_per_second REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    tool_calls TEXT DEFAULT '[]',
    rating REAL DEFAULT 0,
    feedback TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_traces_created ON traces(created_at);
CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent);
"""


@dataclass
class Trace:
    id: int = 0
    created_at: float = 0.0
    prompt: str = ""
    response: str = ""
    agent: str = ""
    tier: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    tokens_per_second: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    rating: float = 0.0
    feedback: str = ""
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "prompt": self.prompt,
            "response": self.response,
            "agent": self.agent,
            "tier": self.tier,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 2),
            "tokens_per_second": round(self.tokens_per_second, 2),
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "rating": self.rating,
            "feedback": self.feedback,
            "session_id": self.session_id,
            "metadata": dict(self.metadata),
        }


class TraceLogger:
    """SQLite-backed trace store."""

    def __init__(self, db_path: str | Path, enabled: bool = True) -> None:
        self.db_path = Path(db_path)
        self.enabled = enabled
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._buffer: list[Trace] = []

    # -- lifecycle --------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        self.flush()
        with self._lock:
            if self._conn:
                self._conn.commit()
                self._conn.close()
                self._conn = None

    # -- writes -------------------------------------------------------------- #
    def log_exchange(
        self,
        *,
        prompt: str,
        response: str,
        agent: str = "",
        tier: str = "",
        provider: str = "",
        model: str = "",
        latency_ms: float = 0.0,
        tokens_per_second: float = 0.0,
        usage: dict[str, int] | None = None,
        tool_calls: Sequence[dict[str, Any]] | None = None,
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        """Record one exchange. Returns the trace id."""
        if not self.enabled:
            return None
        usage = usage or {}
        trace = Trace(
            created_at=time.time(),
            prompt=prompt[:20_000],
            response=response[:20_000],
            agent=agent,
            tier=tier,
            provider=provider,
            model=model,
            latency_ms=latency_ms,
            tokens_per_second=tokens_per_second,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            tool_calls=list(tool_calls or []),
            session_id=session_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._buffer.append(trace)
            if len(self._buffer) >= 10:
                self._flush_locked()
        return self._insert(trace)

    def _insert(self, trace: Trace) -> int:
        try:
            with self._lock:
                conn = self._connect()
                cur = conn.execute(
                    """
                    INSERT INTO traces (created_at, prompt, response, agent, tier,
                        provider, model, latency_ms, tokens_per_second,
                        prompt_tokens, completion_tokens, total_tokens,
                        tool_calls, session_id, metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        trace.created_at, trace.prompt, trace.response, trace.agent,
                        trace.tier, trace.provider, trace.model, trace.latency_ms,
                        trace.tokens_per_second, trace.prompt_tokens,
                        trace.completion_tokens, trace.total_tokens,
                        json.dumps(trace.tool_calls, default=str),
                        trace.session_id,
                        json.dumps(trace.metadata, default=str),
                    ),
                )
                conn.commit()
                return int(cur.lastrowid)
        except Exception:
            return 0

    def rate(self, trace_id: int, rating: float, feedback: str = "") -> bool:
        """Attach a human rating to a trace."""
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE traces SET rating = ?, feedback = ? WHERE id = ?",
                (float(rating), feedback[:2000], trace_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def _flush_locked(self) -> None:
        self._buffer.clear()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    # -- reads ---------------------------------------------------------------- #
    def recent(self, limit: int = 50, agent: str = "") -> list[Trace]:
        sql = ("SELECT id, created_at, prompt, response, agent, tier, provider, model, "
               "latency_ms, tokens_per_second, prompt_tokens, completion_tokens, "
               "total_tokens, tool_calls, rating, feedback, session_id, metadata "
               "FROM traces")
        params: list[Any] = []
        if agent:
            sql += " WHERE agent = ?"
            params.append(agent)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_trace(r) for r in rows]

    def stats(self, window_minutes: int = 1440) -> dict[str, Any]:
        cutoff = time.time() - window_minutes * 60
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT COUNT(*), AVG(latency_ms), AVG(tokens_per_second),
                       SUM(total_tokens), AVG(rating),
                       SUM(CASE WHEN rating > 0 THEN 1 ELSE 0 END)
                FROM traces WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            per_agent = conn.execute(
                """
                SELECT agent, COUNT(*), AVG(latency_ms), AVG(rating)
                FROM traces WHERE created_at >= ? GROUP BY agent
                """,
                (cutoff,),
            ).fetchall()
        rated = row[5] or 0
        return {
            "total": row[0] or 0,
            "avg_latency_ms": round(row[1] or 0.0, 2),
            "avg_tokens_per_second": round(row[2] or 0.0, 2),
            "total_tokens": row[3] or 0,
            "avg_rating": round(row[4] or 0.0, 3),
            "rated_count": rated,
            "per_agent": [
                {"agent": r[0], "count": r[1],
                 "avg_latency_ms": round(r[2] or 0.0, 2),
                 "avg_rating": round(r[3] or 0.0, 3)}
                for r in per_agent
            ],
        }

    def prune(self, retain_days: int = 14) -> int:
        cutoff = time.time() - retain_days * 86400
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM traces WHERE created_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount


def _row_to_trace(row: Sequence[Any]) -> Trace:
    try:
        tool_calls = json.loads(row[13] or "[]")
    except Exception:
        tool_calls = []
    try:
        metadata = json.loads(row[17] or "{}")
    except Exception:
        metadata = {}
    return Trace(
        id=row[0], created_at=row[1], prompt=row[2], response=row[3], agent=row[4],
        tier=row[5], provider=row[6], model=row[7], latency_ms=row[8] or 0.0,
        tokens_per_second=row[9] or 0.0, prompt_tokens=row[10] or 0,
        completion_tokens=row[11] or 0, total_tokens=row[12] or 0,
        tool_calls=tool_calls, rating=row[14] or 0.0, feedback=row[15] or "",
        session_id=row[16] or "", metadata=metadata,
    )
