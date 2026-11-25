"""
Approval queue — actions that wait for a human yes/no.

The proactive layer (and any agent with ``queue_action``) stages actions
here instead of executing them. A human approves or denies from the HUD,
the CLI, or ``/v1/approvals``; approval executes the tool call through the
runtime's tool registry and records the outcome on the action itself.

Actions persist to a JSON file next to the traces DB when a path is
given, so a restart does not silently drop pending approvals.

Layer: security.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ApprovalAction:
    id: str = ""
    tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    reason: str = ""
    kind: str = "tool"       # tool | channel | agent_spawn
    status: str = "pending"  # pending | approved | denied | executed | failed
    created_at: float = 0.0
    decided_at: float = 0.0
    result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "tool": self.tool, "arguments": dict(self.arguments),
            "agent": self.agent, "reason": self.reason, "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at, "decided_at": self.decided_at,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApprovalAction":
        return cls(**{k: data.get(k, default) for k, default in {
            "id": "", "tool": "", "arguments": {}, "agent": "", "reason": "", "kind": "tool",
            "status": "pending", "created_at": 0.0, "decided_at": 0.0,
            "result": "",
        }.items()})


class ApprovalStore:
    """Thread-safe queue of pending/decided approval actions."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._lock = threading.RLock()
        self._actions: dict[str, ApprovalAction] = {}
        self._load()

    # -- persistence --------------------------------------------------------- #
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for item in data if isinstance(data, list) else []:
                action = ApprovalAction.from_dict(item)
                self._actions[action.id] = action
        except Exception:
            pass

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps([a.to_dict() for a in self._actions.values()],
                           indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    # -- queue ----------------------------------------------------------------- #
    def queue(self, tool: str, arguments: dict[str, Any] | None = None,
              agent: str = "", reason: str = "", kind: str = "tool") -> ApprovalAction:
        with self._lock:
            action = ApprovalAction(
                id=uuid.uuid4().hex[:12], tool=tool,
                arguments=dict(arguments or {}), agent=agent, reason=reason,
                kind=kind, created_at=time.time(),
            )
            self._actions[action.id] = action
            self._save()
            return action

    def get(self, action_id: str) -> ApprovalAction | None:
        with self._lock:
            return self._actions.get(action_id)

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [a.to_dict() for a in self._actions.values()
                    if a.status == "pending"]

    def all(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._actions.values(),
                           key=lambda a: a.created_at, reverse=True)
            return [a.to_dict() for a in items[:limit]]

    def decide(self, action_id: str, approved: bool,
               executor: Any = None) -> ApprovalAction | None:
        """Approve (and optionally execute) or deny one action."""
        with self._lock:
            action = self._actions.get(action_id)
            if action is None or action.status != "pending":
                return action
            action.decided_at = time.time()
            if not approved:
                action.status = "denied"
                self._save()
                return action
            action.status = "approved"
            if executor is not None and action.kind == "tool":
                try:
                    result = executor(action.tool, dict(action.arguments))
                    action.result = str(result)[:4000]
                    action.status = "executed"
                except Exception as exc:
                    action.result = f"{type(exc).__name__}: {exc}"
                    action.status = "failed"
            self._save()
            return action

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for a in self._actions.values()
                       if a.status == "pending")


__all__ = ["ApprovalAction", "ApprovalStore"]
