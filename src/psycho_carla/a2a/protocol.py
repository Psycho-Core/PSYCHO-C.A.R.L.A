"""
A2A protocol types — the Google A2A spec over JSON-RPC 2.0.

Mirrors upstream OpenJarvis's `a2a/protocol.py`, with two additions the local
implementation needs: a structured :class:`A2AError`, and an
``input_required`` state that the task machine actually uses rather than
merely declaring.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    """The A2A task lifecycle."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"


#: JSON-RPC error codes used by the A2A methods.
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INVALID_PARAMS = -32602
ERROR_INTERNAL = -32603
ERROR_UNAUTHORIZED = -32001


@dataclass(slots=True)
class A2AError:
    """A JSON-RPC error object."""

    code: int = ERROR_INTERNAL
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data:
            out["data"] = self.data
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2AError":
        return cls(
            code=int(data.get("code", ERROR_INTERNAL)),
            message=str(data.get("message", "")),
            data=dict(data.get("data", {}) or {}),
        )


@dataclass(slots=True)
class AgentCard:
    """Agent discovery card, served at ``/.well-known/agent.json``."""

    name: str
    description: str = ""
    url: str = ""
    version: str = "0.1.0"
    capabilities: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    authentication: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "authentication": self.authentication,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCard":
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            url=str(data.get("url", "")),
            version=str(data.get("version", "")),
            capabilities=list(data.get("capabilities", []) or []),
            skills=list(data.get("skills", []) or []),
            authentication=dict(data.get("authentication", {}) or {}),
        )


@dataclass
class A2ATask:
    """An A2A task with its state machine and history."""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    state: TaskState = TaskState.SUBMITTED
    input_text: str = ""
    output_text: str = ""
    history: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal(self) -> bool:
        """Has the task reached a final state?"""
        return self.state in (
            TaskState.COMPLETED,
            TaskState.CANCELED,
            TaskState.FAILED,
        )

    def add_history(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "state": self.state.value,
            "input": self.input_text,
            "output": self.output_text,
            "history": list(self.history),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2ATask":
        raw_state = str(data.get("state", "submitted"))
        try:
            state = TaskState(raw_state)
        except ValueError:
            state = TaskState.SUBMITTED
        return cls(
            task_id=str(data.get("id", "")),
            state=state,
            input_text=str(data.get("input", "")),
            output_text=str(data.get("output", "")),
            history=list(data.get("history", []) or []),
            metadata=dict(data.get("metadata", {}) or {}),
        )


@dataclass(slots=True)
class A2ARequest:
    """A JSON-RPC 2.0 request for A2A."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def to_dict(self) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "method": self.method,
            "params": self.params,
            "id": self.request_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2ARequest":
        return cls(
            method=str(data.get("method", "")),
            params=dict(data.get("params", {}) or {}),
            request_id=str(data.get("id", "")),
        )


@dataclass(slots=True)
class A2AResponse:
    """A JSON-RPC 2.0 response for A2A."""

    result: Any = None
    error: dict[str, Any] | None = None
    request_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        resp: dict[str, Any] = {"jsonrpc": "2.0", "id": self.request_id}
        if self.error:
            resp["error"] = self.error
        else:
            resp["result"] = self.result
        return resp

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str) -> "A2AResponse":
        parsed = json.loads(data)
        return cls(
            result=parsed.get("result"),
            error=parsed.get("error"),
            request_id=str(parsed.get("id", "")),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "A2AResponse":
        return cls(
            result=data.get("result"),
            error=data.get("error"),
            request_id=str(data.get("id", "")),
        )

    @property
    def ok(self) -> bool:
        return self.error is None


__all__ = [
    "A2AError",
    "A2ARequest",
    "A2AResponse",
    "A2ATask",
    "AgentCard",
    "TaskState",
    "ERROR_INTERNAL",
    "ERROR_INVALID_PARAMS",
    "ERROR_METHOD_NOT_FOUND",
    "ERROR_UNAUTHORIZED",
]
