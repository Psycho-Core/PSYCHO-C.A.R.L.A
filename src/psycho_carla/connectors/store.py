"""
KnowledgeStore — the local index of synced documents.

Connectors push documents in; the retrieval layer queries them. The store
is a SQLite database with a simple full-text-ish index: a tokenised
keyword table plus a scoring function, so a query returns the documents
that mention the query terms most often.

This is deliberately simple. The agent's memory store already provides
semantic search; this store covers the connector corpus, which is smaller
and more literal.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from .base import Document

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id     TEXT PRIMARY KEY,
    source     TEXT,
    title      TEXT,
    body       TEXT,
    url        TEXT,
    created_at REAL,
    updated_at REAL,
    metadata   TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
CREATE TABLE IF NOT EXISTS document_terms (
    doc_id TEXT NOT NULL,
    term   TEXT NOT NULL,
    count  INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_terms_term ON document_terms(term);
CREATE INDEX IF NOT EXISTS idx_terms_doc ON document_terms(doc_id);
"""

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words too common to discriminate.
_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have he in is it its of on that the
    to was were will with this these those they them their there here what
    when where which who whom how why not no nor but so than then you your
    our us we i he she him her his our out up down about into over under
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Split text into meaningful lowercase tokens."""
    return [
        token
        for token in _TOKEN_RE.findall((text or "").lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


class KnowledgeStore:
    """A local, searchable index of connector documents."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass

    # -- writes -------------------------------------------------------------- #
    def add(self, document: Document) -> str:
        """Index one document, replacing any earlier version."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM document_terms WHERE doc_id = ?", (document.doc_id,)
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO documents "
                "(doc_id,source,title,body,url,created_at,updated_at,metadata) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    document.doc_id,
                    document.source,
                    document.title,
                    document.body,
                    document.url,
                    document.created_at or time.time(),
                    document.updated_at or time.time(),
                    _dumps(document.metadata),
                ),
            )
            counts: dict[str, int] = {}
            for token in tokenize(f"{document.title} {document.body}"):
                counts[token] = counts.get(token, 0) + 1
            self._conn.executemany(
                "INSERT INTO document_terms (doc_id,term,count) VALUES (?,?,?)",
                [(document.doc_id, term, n) for term, n in counts.items()],
            )
            self._conn.commit()
        return document.doc_id

    def add_many(self, documents: Sequence[Document]) -> int:
        """Index a batch of documents."""
        count = 0
        for document in documents:
            self.add(document)
            count += 1
        return count

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM documents WHERE doc_id = ?", (doc_id,)
            )
            self._conn.execute(
                "DELETE FROM document_terms WHERE doc_id = ?", (doc_id,)
            )
            self._conn.commit()
            return bool(cur.rowcount)

    def clear(self, source: str = "") -> int:
        """Remove documents, optionally only from one source."""
        with self._lock:
            if source:
                rows = self._conn.execute(
                    "SELECT doc_id FROM documents WHERE source = ?", (source,)
                ).fetchall()
                for (doc_id,) in rows:
                    self._conn.execute(
                        "DELETE FROM document_terms WHERE doc_id = ?", (doc_id,)
                    )
                cur = self._conn.execute(
                    "DELETE FROM documents WHERE source = ?", (source,)
                )
            else:
                self._conn.execute("DELETE FROM document_terms")
                cur = self._conn.execute("DELETE FROM documents")
            self._conn.commit()
            return int(cur.rowcount or 0)

    # -- reads ---------------------------------------------------------------- #
    def get(self, doc_id: str) -> Document | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT doc_id,source,title,body,url,created_at,updated_at,metadata "
                "FROM documents WHERE doc_id = ?",
                (doc_id,),
            ).fetchone()
        if not row:
            return None
        return Document(
            doc_id=row[0], source=row[1], title=row[2], body=row[3], url=row[4],
            created_at=row[5] or 0.0, updated_at=row[6] or 0.0,
            metadata=_loads(row[7]),
        )

    def search(self, query: str, top_k: int = 10) -> list[Document]:
        """Search the corpus, ranked by query-term overlap."""
        terms = tokenize(query)
        if not terms:
            return []
        scores: dict[str, float] = {}
        with self._lock:
            for term in terms:
                rows = self._conn.execute(
                    "SELECT doc_id, SUM(count) FROM document_terms "
                    "WHERE term = ? GROUP BY doc_id",
                    (term,),
                ).fetchall()
                for doc_id, total in rows:
                    scores[doc_id] = scores.get(doc_id, 0.0) + float(total or 0)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        results: list[Document] = []
        for doc_id, _score in ranked[:top_k]:
            document = self.get(doc_id)
            if document is not None:
                results.append(document)
        return results

    def count(self, source: str = "") -> int:
        with self._lock:
            if source:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM documents WHERE source = ?", (source,)
                ).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        return int(row[0] or 0)

    def sources(self) -> dict[str, int]:
        """Document counts per source."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*) FROM documents GROUP BY source"
            ).fetchall()
        return {r[0] or "unknown": r[1] for r in rows}

    def stats(self) -> dict[str, Any]:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            terms = self._conn.execute(
                "SELECT COUNT(DISTINCT term) FROM document_terms"
            ).fetchone()[0]
        return {
            "documents": docs,
            "terms": terms,
            "sources": self.sources(),
            "path": str(self._db_path),
        }


def _dumps(value: Any) -> str:
    import json

    try:
        return json.dumps(value or {})
    except Exception:
        return "{}"


def _loads(value: Any) -> dict[str, Any]:
    import json

    try:
        data = json.loads(value or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


__all__ = ["KnowledgeStore", "tokenize"]
