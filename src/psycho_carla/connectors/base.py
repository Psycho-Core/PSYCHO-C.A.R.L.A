"""
Connector contracts — the shape every data-source connector implements.

A connector is anything that can be synchronised: it authenticates, lists
its documents, and returns them in a common shape so the retrieval layer
does not care whether the source was Gmail or Obsidian.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class SyncStatus(str, Enum):
    """The state of a connector's last sync."""

    NOT_CONNECTED = "not-connected"
    AUTH_REQUIRED = "auth-required"
    SYNCING = "syncing"
    OK = "ok"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class Attachment:
    """A binary attachment associated with a document."""

    filename: str = ""
    mime_type: str = "application/octet-stream"
    size_bytes: int = 0
    content: bytes = b""
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "url": self.url,
            "has_content": bool(self.content),
        }


@dataclass
class Document:
    """A normalised document from any connector."""

    doc_id: str = ""
    title: str = ""
    body: str = ""
    source: str = ""
    url: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The document as plain text, for indexing."""
        parts = [self.title, self.body]
        return "\n\n".join(p for p in parts if p)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "attachments": [a.to_dict() for a in self.attachments],
        }


@runtime_checkable
class BaseConnector(Protocol):
    """The contract every connector implements."""

    #: Short connector id, e.g. "gmail", "gdrive".
    connector_id: str = ""

    #: Human-readable name for the UI.
    display_name: str = ""

    def auth_url(self) -> str:
        """The URL that starts the authorisation flow."""
        ...

    def sync(self, **kwargs: Any) -> list[Document]:
        """Fetch documents from the source."""
        ...

    def status(self) -> SyncStatus:
        """The current connection state."""
        ...


def now() -> float:
    """Wall-clock timestamp — one place, so it can be mocked in tests."""
    return time.time()


__all__ = ["Attachment", "BaseConnector", "Document", "SyncStatus", "now"]
