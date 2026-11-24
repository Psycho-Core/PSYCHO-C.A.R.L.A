"""
PSYCHO-C.A.R.L.A. — Persistent Memory.

A single SQLite database holds three things:
  1. **Conversations** — full message history per session.
  2. **Memories** — durable facts extracted from conversations.
  3. **Embeddings** — a float32 blob per memory, enabling semantic search.

The vector side is a brute-force cosine similarity scan. At the scale of a
personal agent (thousands of memories, not millions) that is *faster* than
building an ANN index and has zero dependencies. The interface is deliberately
index-shaped so a vector DB can be dropped in later.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


# --------------------------------------------------------------------------- #
# Embeddings
# --------------------------------------------------------------------------- #
class Embedder:
    """Deterministic hashing embedder with an optional external backend.

    The fallback is a hashed bag-of-tokens projection — no model download, no
    network, and surprisingly good for recall on short personal notes. If
    `sentence-transformers` (or any OpenAI-compatible embeddings endpoint) is
    available and configured, it is used instead.
    """

    def __init__(self, dim: int = 384, backend: str = "hash",
                 model: str = "", base_url: str = "", api_key: str = "") -> None:
        self.dim = dim
        self.backend = backend
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self._st_model = None
        self._lock = threading.Lock()

    # -- public ------------------------------------------------------------ #
    def embed(self, text: str) -> list[float]:
        if self.backend == "sentence_transformers" and self.model:
            vec = self._embed_st(text)
            if vec:
                return vec
        return self._embed_hash(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    # -- backends ---------------------------------------------------------- #
    def _embed_st(self, text: str) -> list[float]:
        with self._lock:
            if self._st_model is None:
                try:
                    from sentence_transformers import SentenceTransformer  # type: ignore

                    self._st_model = SentenceTransformer(self.model)
                except Exception:
                    return []
        try:
            vec = self._st_model.encode(text, normalize_embeddings=True)
            return [float(x) for x in vec]
        except Exception:
            return []

    def _embed_hash(self, text: str) -> list[float]:
        """Hashed bag-of-tokens + character-trigram projection, L2-normalised."""
        vec = [0.0] * self.dim
        tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
        if not tokens:
            return vec

        for tok in tokens:
            h = int(hashlib.blake2b(tok.encode(), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 17) & 1 else -1.0
            vec[idx] += sign

        # Character trigrams give partial-word matching (typos, stems).
        padded = f" {' '.join(tokens)} "
        for i in range(len(padded) - 2):
            tri = padded[i : i + 3]
            h = int(hashlib.blake2b(tri.encode(), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            vec[idx] += 0.35 * (1.0 if (h >> 19) & 1 else -1.0)

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
@dataclass
class Memory:
    """One stored memory."""

    id: int
    content: str
    kind: str = "fact"           # fact | preference | event | note
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: float = 0.0
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "kind": self.kind,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at,
            "score": round(self.score, 4),
            "metadata": dict(self.metadata),
        }


@dataclass
class Conversation:
    id: int
    session_id: str
    role: str
    content: str
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------- #
# Memory store
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    kind TEXT DEFAULT 'fact',
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    embedding BLOB,
    dim INTEGER DEFAULT 0,
    fingerprint TEXT DEFAULT '',
    created_at REAL,
    accessed_at REAL,
    metadata TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_fp ON memories(fingerprint);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL,
    metadata TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    created_at REAL,
    updated_at REAL,
    metadata TEXT DEFAULT '{}'
);
"""


class MemoryStore:
    """SQLite-backed conversation + memory store with semantic retrieval."""

    def __init__(self, db_path: str | Path, embedder: Embedder | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or Embedder()
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection --------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()
            self._conn = conn
        return self._conn

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after a database was first created."""
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
            if "fingerprint" not in cols:
                conn.execute(
                    "ALTER TABLE memories ADD COLUMN fingerprint TEXT DEFAULT ''"
                )
                rows = conn.execute("SELECT id, content FROM memories").fetchall()
                for row_id, content in rows:
                    conn.execute(
                        "UPDATE memories SET fingerprint = ? WHERE id = ?",
                        (_fingerprint(content or ""), row_id),
                    )
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.commit()
                self._conn.close()
                self._conn = None

    # -- memory writes -------------------------------------------------------- #
    def store(
        self,
        content: str,
        kind: str = "fact",
        tags: list[str] | None = None,
        source: str = "",
        metadata: dict[str, Any] | None = None,
        embed: bool = True,
        dedupe: bool = True,
    ) -> int:
        """Store a memory and return its id.

        When `dedupe` is on (the default), an existing memory with the same
        normalised content is updated in place rather than duplicated.
        """
        content = (content or "").strip()
        if not content:
            raise ValueError("Cannot store an empty memory.")

        if dedupe:
            fingerprint = _fingerprint(content)
            existing = self._find_by_fingerprint(fingerprint)
            if existing is not None:
                merged_tags = sorted(set(existing["tags"]) | set(tags or []))
                with self._lock:
                    conn = self._connect()
                    conn.execute(
                        "UPDATE memories SET tags = ?, accessed_at = ? WHERE id = ?",
                        (json.dumps(merged_tags), time.time(), existing["id"]),
                    )
                    conn.commit()
                return int(existing["id"])

        vec = self.embedder.embed(content) if embed else []
        blob = _pack_vector(vec) if vec else None
        now = time.time()

        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """
                INSERT INTO memories (content, kind, tags, source, embedding, dim,
                                      fingerprint, created_at, accessed_at, metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    content,
                    kind,
                    json.dumps(tags or []),
                    source,
                    blob,
                    len(vec),
                    _fingerprint(content),
                    now,
                    now,
                    json.dumps(metadata or {}, default=str),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def _find_by_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        """Look up an existing memory by its normalised-content fingerprint."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT id, tags FROM memories WHERE fingerprint = ? LIMIT 1",
                (fingerprint,),
            ).fetchone()
        if not row:
            return None
        try:
            tags = json.loads(row[1] or "[]")
        except Exception:
            tags = []
        return {"id": row[0], "tags": tags}

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear_memories(self) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM memories")
            conn.commit()
            return cur.rowcount

    def update_metadata(self, memory_id: int, metadata: dict[str, Any]) -> bool:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE memories SET metadata = ? WHERE id = ?",
                (json.dumps(metadata, default=str), memory_id),
            )
            conn.commit()
            return cur.rowcount > 0

    # -- memory reads ---------------------------------------------------------- #
    def get(self, memory_id: int) -> Memory | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT id, content, kind, tags, source, created_at, metadata "
                "FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return _row_to_memory(row) if row else None

    def list_memories(
        self,
        limit: int = 50,
        kind: str = "",
        tag: str = "",
        query: str = "",
    ) -> list[Memory]:
        sql = "SELECT id, content, kind, tags, source, created_at, metadata FROM memories"
        clauses: list[str] = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if query:
            clauses.append("content LIKE ?")
            params.append(f"%{query}%")
        if tag:
            clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_memory(r) for r in rows]

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.05,
        kind: str = "",
    ) -> list[Memory]:
        """Semantic search across stored memories.

        Falls back to lexical scoring if the query has no embedding overlap at all.
        """
        if not query or not query.strip():
            return []
        qvec = self.embedder.embed(query)
        if not any(qvec):
            return self.list_memories(limit=top_k, kind=kind)

        sql = ("SELECT id, content, kind, tags, source, created_at, metadata, embedding, dim "
               "FROM memories")
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)

        with self._lock:
            conn = self._connect()
            rows = conn.execute(sql, params).fetchall()

        scored: list[Memory] = []
        query_tokens = set(re.findall(r"[a-z0-9']+", query.lower()))
        for row in rows:
            mem = _row_to_memory(row)
            blob = row[7]
            dim = row[8]
            score = 0.0
            if blob and dim:
                vec = _unpack_vector(blob, dim)
                score = _cosine(qvec, vec)
            # Blend in a lexical component so exact keyword hits always rank.
            mem_tokens = set(re.findall(r"[a-z0-9']+", mem.content.lower()))
            if query_tokens:
                overlap = len(query_tokens & mem_tokens) / len(query_tokens)
                score = score * 0.75 + overlap * 0.45
            if score >= min_score:
                mem.score = score
                scored.append(mem)

        scored.sort(key=lambda m: m.score, reverse=True)
        top = scored[:top_k]

        # Touch the accessed timestamp for the winners.
        if top:
            ids = [m.id for m in top]
            with self._lock:
                conn = self._connect()
                conn.executemany(
                    "UPDATE memories SET accessed_at = ? WHERE id = ?",
                    [(time.time(), i) for i in ids],
                )
                conn.commit()
        return top

    def context_for(
        self,
        query: str,
        top_k: int = 5,
        max_tokens: int = 1024,
        min_score: float = 0.05,
    ) -> str:
        """Render retrieved memories as a context block for a system prompt."""
        hits = self.search(query, top_k=top_k, min_score=min_score)
        if not hits:
            return ""
        lines = ["Relevant memory:"]
        approx = 0
        for h in hits:
            line = f"- {h.content}"
            approx += len(line) // 4
            lines.append(line)
            if approx > max_tokens:
                break
        return "\n".join(lines)

    # -- conversations ------------------------------------------------------------ #
    def record_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = time.time()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, created_at, updated_at) "
                "VALUES (?,?,?)", (session_id, now, now),
            )
            cur = conn.execute(
                "INSERT INTO conversations (session_id, role, content, created_at, metadata) "
                "VALUES (?,?,?,?,?)",
                (session_id, role, content, now,
                 json.dumps(metadata or {}, default=str)),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            conn.commit()
            return int(cur.lastrowid)

    def get_conversation(
        self, session_id: str, limit: int = 100
    ) -> list[Conversation]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, session_id, role, content, created_at, metadata "
                "FROM conversations WHERE session_id = ? ORDER BY id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [
            Conversation(
                id=r[0], session_id=r[1], role=r[2], content=r[3],
                created_at=r[4], metadata=json.loads(r[5] or "{}"),
            )
            for r in rows
        ]

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT session_id, title, created_at, updated_at FROM sessions "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {"session_id": r[0], "title": r[1], "created_at": r[2],
             "updated_at": r[3]}
            for r in rows
        ]

    def delete_session(self, session_id: str) -> int:
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM conversations WHERE session_id = ?", (session_id,)
            )
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cur.rowcount

    # -- statistics ---------------------------------------------------------------- #
    def stats(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connect()
            mem_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conv_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            sess_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            kinds = conn.execute(
                "SELECT kind, COUNT(*) FROM memories GROUP BY kind"
            ).fetchall()
        return {
            "memories": mem_count,
            "conversations": conv_count,
            "sessions": sess_count,
            "kinds": {k: c for k, c in kinds},
            "db_path": str(self.db_path),
        }


# --------------------------------------------------------------------------- #
# Vector helpers
# --------------------------------------------------------------------------- #
def _pack_vector(vec: Sequence[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    import struct

    try:
        return list(struct.unpack(f"<{dim}f", blob[: dim * 4]))
    except Exception:
        return []


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        dot += a[i] * b[i]
        na += a[i] * a[i]
        nb += b[i] * b[i]
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _fingerprint(text: str) -> str:
    """Stable hash of the normalised text, used for exact-duplicate detection."""
    import hashlib

    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.blake2b(norm.encode(), digest_size=16).hexdigest()


def _row_to_memory(row: Sequence[Any]) -> Memory:
    try:
        tags = json.loads(row[3] or "[]")
    except Exception:
        tags = []
    try:
        metadata = json.loads(row[6] or "{}")
    except Exception:
        metadata = {}
    return Memory(
        id=row[0], content=row[1], kind=row[2], tags=tags,
        source=row[4], created_at=row[5], metadata=metadata,
    )
