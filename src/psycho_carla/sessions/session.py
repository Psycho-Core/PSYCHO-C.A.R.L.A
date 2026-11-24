"""
Session storage — SQLite-backed, cross-channel, with consolidation and decay.

Tables:

  * ``sessions``        — one row per session, plus its identity mapping
  * ``session_messages`` — the message history

Consolidation replaces a run of old messages with a single anchor message
that carries a summary of them. Decay removes sessions that have gone quiet
for longer than the configured maximum age.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compression import consolidate_messages, estimate_tokens

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT,
    display_name  TEXT DEFAULT '',
    channel_ids   TEXT DEFAULT '{}',
    created_at    REAL,
    last_activity REAL,
    metadata      TEXT DEFAULT '{}',
    preferred_notification_channel TEXT,
    pending_response             TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_activity ON sessions(last_activity);
CREATE TABLE IF NOT EXISTS session_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    channel    TEXT DEFAULT '',
    timestamp  REAL,
    metadata   TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON session_messages(session_id);
CREATE TABLE IF NOT EXISTS session_anchors (
    session_id   TEXT NOT NULL,
    anchor_id    TEXT NOT NULL,
    content      TEXT NOT NULL,
    message_ids  TEXT DEFAULT '[]',
    created_at   REAL,
    PRIMARY KEY (session_id, anchor_id)
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created.

    ``CREATE TABLE IF NOT EXISTS`` does not add columns to an existing
    table, so an older database is upgraded in place here.
    """
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    additions = [
        ("preferred_notification_channel", "TEXT"),
        ("pending_response", "TEXT"),
    ]
    changed = False
    for column, coltype in additions:
        if column not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {coltype}")
            changed = True
    if changed:
        conn.commit()


@dataclass
class SessionIdentity:
    """Canonical user identity across channels."""

    user_id: str
    display_name: str = ""
    #: channel_type -> channel_user_id
    channel_ids: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "channel_ids": dict(self.channel_ids),
        }


@dataclass
class SessionMessage:
    """A single message within a session."""

    role: str  # "user" | "assistant" | "system"
    content: str
    channel: str = ""
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.message_id,
            "role": self.role,
            "content": self.content,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class Session:
    """A conversation session with cross-channel message history."""

    session_id: str = ""
    identity: SessionIdentity | None = None
    messages: list[SessionMessage] = field(default_factory=list)
    created_at: float = 0.0
    last_activity: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str, *, channel: str = "") -> None:
        self.messages.append(
            SessionMessage(
                role=role,
                content=content,
                channel=channel,
                timestamp=time.time(),
            )
        )
        self.last_activity = time.time()

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "identity": self.identity.to_dict() if self.identity else None,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "message_count": self.message_count,
            "metadata": dict(self.metadata),
        }


class SessionStore:
    """SQLite-backed session persistence."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_age_hours: float = 24.0,
        consolidation_threshold: int = 100,
        max_message_tokens: int = 4000,
    ) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._lock = threading.RLock()
        self._max_age_hours = float(max_age_hours)
        self._consolidation_threshold = int(consolidation_threshold)
        self._max_message_tokens = int(max_message_tokens)
        self._conn.executescript(_SCHEMA)
        _migrate(self._conn)
        self._conn.commit()

    # -- lifecycle --------------------------------------------------------- #
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- sessions ----------------------------------------------------------- #
    def create(
        self,
        session_id: str = "",
        *,
        identity: SessionIdentity | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new session."""
        sid = session_id or uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(session_id,user_id,display_name,channel_ids,created_at,"
                "last_activity,metadata) VALUES (?,?,?,?,?,?,?)",
                (
                    sid,
                    identity.user_id if identity else "",
                    identity.display_name if identity else "",
                    json.dumps(identity.channel_ids if identity else {}),
                    now,
                    now,
                    json.dumps(metadata or {}),
                ),
            )
            self._conn.commit()
        return Session(
            session_id=sid,
            identity=identity,
            created_at=now,
            last_activity=now,
            metadata=dict(metadata or {}),
        )

    def get(self, session_id: str) -> Session | None:
        """Load a session with its messages."""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id,user_id,display_name,channel_ids,created_at,"
                "last_activity,metadata FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            messages = [
                SessionMessage(
                    message_id=m[0],
                    role=m[1],
                    content=m[2],
                    channel=m[3],
                    timestamp=m[4],
                    metadata=json.loads(m[5] or "{}"),
                )
                for m in self._conn.execute(
                    "SELECT id,role,content,channel,timestamp,metadata "
                    "FROM session_messages WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
            ]
        try:
            channel_ids = json.loads(row[3] or "{}")
        except Exception:
            channel_ids = {}
        try:
            metadata = json.loads(row[6] or "{}")
        except Exception:
            metadata = {}
        identity = None
        if row[1]:
            identity = SessionIdentity(
                user_id=row[1], display_name=row[2] or "", channel_ids=channel_ids
            )
        return Session(
            session_id=row[0],
            identity=identity,
            messages=messages,
            created_at=row[5] or 0.0,
            last_activity=row[6] or 0.0,
            metadata=metadata,
        )

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        channel: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Append a message to a session."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO session_messages "
                "(session_id,role,content,channel,timestamp,metadata) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, role, content, channel, now,
                 json.dumps(metadata or {})),
            )
            self._conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE session_id = ?",
                (now, session_id),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def delete(self, session_id: str) -> bool:
        """Delete a session and all its messages."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM session_messages WHERE session_id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM session_anchors WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
            return bool(cur.rowcount)

    def list_sessions(
        self, *, limit: int = 50, user_id: str = ""
    ) -> list[dict[str, Any]]:
        """List sessions, most recently active first."""
        query = (
            "SELECT session_id,user_id,display_name,created_at,last_activity "
            "FROM sessions"
        )
        params: list[Any] = []
        if user_id:
            query += " WHERE user_id = ?"
            params.append(user_id)
        query += " ORDER BY last_activity DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "session_id": r[0],
                "user_id": r[1],
                "display_name": r[2],
                "created_at": r[3],
                "last_activity": r[4],
            }
            for r in rows
        ]

    def find_by_channel(self, channel: str, channel_user_id: str) -> Session | None:
        """Find a session by its channel-specific user id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id,channel_ids FROM sessions"
            ).fetchall()
        for session_id, raw in rows:
            try:
                mapping = json.loads(raw or "{}")
            except Exception:
                continue
            if mapping.get(channel) == channel_user_id:
                return self.get(session_id)
        return None

    def link_channel(
        self, session_id: str, channel: str, channel_user_id: str
    ) -> bool:
        """Map a channel-specific user id onto an existing session."""
        with self._lock:
            row = self._conn.execute(
                "SELECT channel_ids FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return False
            try:
                mapping = json.loads(row[0] or "{}")
            except Exception:
                mapping = {}
            mapping[channel] = channel_user_id
            self._conn.execute(
                "UPDATE sessions SET channel_ids = ? WHERE session_id = ?",
                (json.dumps(mapping), session_id),
            )
            self._conn.commit()
            return True

    # -- notifications -------------------------------------------------------- #
    def set_notification_preference(
        self, session_id: str, channel: str
    ) -> bool:
        """Record the channel a session wants notifications delivered on.

        An empty *channel* clears the preference.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET preferred_notification_channel = ?"
                " WHERE session_id = ?",
                (channel or None, session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_notification_preference(self, session_id: str) -> str:
        """The channel this session wants notifications on, or ''."""
        with self._lock:
            row = self._conn.execute(
                "SELECT preferred_notification_channel FROM sessions"
                " WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return (row[0] or "") if row else ""

    def get_notification_targets(self) -> list[dict[str, str]]:
        """Every session that has a notification channel set."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, user_id, preferred_notification_channel"
                " FROM sessions"
                " WHERE preferred_notification_channel IS NOT NULL"
            ).fetchall()
        return [
            {
                "session_id": row[0],
                "sender_id": row[1] or "",
                "preferred_notification_channel": row[2] or "",
            }
            for row in rows
        ]

    # -- pending responses ---------------------------------------------------- #
    def set_pending_response(
        self, session_id: str, response: str | None
    ) -> bool:
        """Store a response the user has not yet collected.

        Long replies on chat platforms are often truncated by the
        platform, so the full text is held here and retrieved with
        ``/more``.
        """
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE sessions SET pending_response = ? WHERE session_id = ?",
                (response or None, session_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def get_pending_response(self, session_id: str) -> str:
        """The pending response for a session, or ''."""
        with self._lock:
            row = self._conn.execute(
                "SELECT pending_response FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return (row[0] or "") if row else ""

    def clear_pending_response(self, session_id: str) -> bool:
        """Discard the pending response."""
        return self.set_pending_response(session_id, None)

    # -- consolidation -------------------------------------------------------- #
    def consolidate(self, session_id: str) -> dict[str, Any]:
        """Consolidate a long session into anchors.

        Returns a report describing what was consolidated.
        """
        session = self.get(session_id)
        if session is None:
            return {"session_id": session_id, "consolidated": 0, "anchors": 0}
        messages = session.messages
        if len(messages) < self._consolidation_threshold:
            return {
                "session_id": session_id,
                "consolidated": 0,
                "anchors": 0,
                "skipped": "below threshold",
            }
        # Keep the most recent half verbatim; consolidate the older half.
        keep_from = len(messages) // 2
        old, recent = messages[:keep_from], messages[keep_from:]
        summary = consolidate_messages(old, max_tokens=self._max_message_tokens)
        if not summary:
            return {
                "session_id": session_id,
                "consolidated": 0,
                "anchors": 0,
                "skipped": "nothing to consolidate",
            }
        anchor_id = f"anchor_{int(time.time() * 1000)}"
        old_ids = [m.message_id for m in old]
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO session_anchors "
                "(session_id,anchor_id,content,message_ids,created_at) "
                "VALUES (?,?,?,?,?)",
                (session_id, anchor_id, summary, json.dumps(old_ids), now),
            )
            self._conn.executemany(
                "DELETE FROM session_messages WHERE id = ?",
                [(mid,) for mid in old_ids],
            )
            self._conn.commit()
        return {
            "session_id": session_id,
            "consolidated": len(old),
            "anchors": 1,
            "anchor_id": anchor_id,
            "recent_kept": len(recent),
        }

    def anchors(self, session_id: str) -> list[dict[str, Any]]:
        """The consolidation anchors for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT anchor_id,content,message_ids,created_at "
                "FROM session_anchors WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [
            {
                "anchor_id": r[0],
                "content": r[1],
                "message_ids": json.loads(r[2] or "[]"),
                "created_at": r[3],
            }
            for r in rows
        ]

    def context(
        self, session_id: str, *, max_tokens: int = 4000
    ) -> list[dict[str, str]]:
        """The session as a message list, with anchors in place.

        This is what the agent sees: the anchors first (they carry the
        summarised early history), then the recent messages verbatim.
        """
        session = self.get(session_id)
        if session is None:
            return []
        out: list[dict[str, str]] = []
        for anchor in self.anchors(session_id):
            out.append({"role": "system", "content": anchor["content"]})
        for message in session.messages:
            out.append({"role": message.role, "content": message.content})
        # Trim from the front if the whole thing is too large.
        while out and estimate_tokens(out) > max_tokens:
            out.pop(0)
        return out

    # -- decay ---------------------------------------------------------------- #
    def expire(self, max_age_hours: float | None = None) -> int:
        """Delete sessions older than the maximum age."""
        hours = self._max_age_hours if max_age_hours is None else float(max_age_hours)
        cutoff = time.time() - (hours * 3600)
        with self._lock:
            # Collect the ids *before* deleting, so the child rows can be
            # cleaned up too.
            expired = [
                r[0]
                for r in self._conn.execute(
                    "SELECT session_id FROM sessions WHERE last_activity < ?",
                    (cutoff,),
                ).fetchall()
            ]
            for session_id in expired:
                self._conn.execute(
                    "DELETE FROM session_messages WHERE session_id = ?", (session_id,)
                )
                self._conn.execute(
                    "DELETE FROM session_anchors WHERE session_id = ?", (session_id,)
                )
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE last_activity < ?", (cutoff,)
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def stats(self) -> dict[str, Any]:
        """Counts for the HUD."""
        with self._lock:
            sessions = self._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            messages = self._conn.execute(
                "SELECT COUNT(*) FROM session_messages"
            ).fetchone()[0]
            anchors = self._conn.execute(
                "SELECT COUNT(*) FROM session_anchors"
            ).fetchone()[0]
            channels = self._conn.execute(
                "SELECT channel, COUNT(*) FROM session_messages "
                "WHERE channel != '' GROUP BY channel"
            ).fetchall()
        return {
            "sessions": sessions,
            "messages": messages,
            "anchors": anchors,
            "channels": {c: n for c, n in channels},
        }


__all__ = [
    "Session",
    "SessionIdentity",
    "SessionMessage",
    "SessionStore",
]
