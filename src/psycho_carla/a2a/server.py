"""
A2A server — exposes C.A.R.L.A.'s agents to other agents.

Routes (mountable on the existing FastAPI app):

  * ``GET  /.well-known/agent.json`` — the discovery card
  * ``POST /a2a/tasks``               — JSON-RPC 2.0 task endpoint

Authentication mirrors the local server guard: when ``auth_token`` is set,
every call must present a matching bearer token. The token is advertised on
the card's ``authentication`` field so a caller knows to send one.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Callable

from .protocol import (
    ERROR_INVALID_PARAMS,
    ERROR_METHOD_NOT_FOUND,
    ERROR_UNAUTHORIZED,
    A2AResponse,
    A2ATask,
    AgentCard,
    TaskState,
)


class A2AServer:
    """Processes incoming A2A tasks via agent execution."""

    def __init__(
        self,
        agent_card: AgentCard,
        *,
        handler: Callable[[str], str] | None = None,
        auth_token: str | None = None,
        max_tasks: int = 500,
    ) -> None:
        self._card = agent_card
        self._handler = handler
        self._auth_token = auth_token or None
        self._tasks: dict[str, A2ATask] = {}
        self._lock = threading.RLock()
        self._max_tasks = max(1, int(max_tasks))
        if self._auth_token:
            # Advertise the required scheme on the discovery card.
            self._card.authentication = {"schemes": ["bearer"]}

    # -- accessors -------------------------------------------------------- #
    @property
    def agent_card(self) -> AgentCard:
        return self._card

    @property
    def tasks(self) -> dict[str, A2ATask]:
        with self._lock:
            return dict(self._tasks)

    # -- auth -------------------------------------------------------------- #
    def authenticate(self, token: str | None) -> bool:
        """Constant-time check of a presented bearer token.

        Returns True when no token is configured (auth disabled).
        """
        if not self._auth_token:
            return True
        if not token:
            return False
        return secrets.compare_digest(token, self._auth_token)

    # -- request handling --------------------------------------------------- #
    def handle_request(
        self,
        request_data: dict[str, Any],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Process a JSON-RPC 2.0 A2A request."""
        req_id = str(request_data.get("id", ""))
        if not self.authenticate(token):
            return A2AResponse(
                error={
                    "code": ERROR_UNAUTHORIZED,
                    "message": "Unauthorized",
                },
                request_id=req_id,
            ).to_dict()

        method = str(request_data.get("method", ""))
        params = request_data.get("params", {}) or {}

        if method == "tasks/send":
            return self._handle_task_send(params, req_id)
        if method == "tasks/get":
            return self._handle_task_get(params, req_id)
        if method == "tasks/cancel":
            return self._handle_task_cancel(params, req_id)
        if method == "agent/card":
            return A2AResponse(
                result=self._card.to_dict(), request_id=req_id
            ).to_dict()
        return A2AResponse(
            error={
                "code": ERROR_METHOD_NOT_FOUND,
                "message": f"Method not found: {method}",
            },
            request_id=req_id,
        ).to_dict()

    def _handle_task_send(self, params: dict[str, Any], req_id: str) -> dict[str, Any]:
        """Create and execute a task."""
        input_text = _extract_input(params)
        if not input_text:
            return A2AResponse(
                error={
                    "code": ERROR_INVALID_PARAMS,
                    "message": "No input text in task",
                },
                request_id=req_id,
            ).to_dict()

        task = A2ATask(input_text=input_text)
        task.add_history("user", input_text)
        self._store(task)

        task.state = TaskState.WORKING
        try:
            if self._handler:
                result = self._handler(input_text)
            else:
                result = (
                    "No handler configured for A2A task: "
                    f"{input_text}"
                )
            task.output_text = result
            task.state = TaskState.COMPLETED
            task.add_history("agent", result)
        except Exception as exc:  # noqa: BLE001 - a failed task must not
            # take the server down; it becomes a FAILED task instead.
            task.output_text = str(exc)
            task.state = TaskState.FAILED
            task.metadata["error"] = f"{type(exc).__name__}: {exc}"
        task.metadata["completed_at"] = time.time()
        return A2AResponse(result=task.to_dict(), request_id=req_id).to_dict()

    def _handle_task_get(self, params: dict[str, Any], req_id: str) -> dict[str, Any]:
        """Retrieve task status."""
        task_id = str(params.get("id", ""))
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return A2AResponse(
                error={
                    "code": ERROR_INVALID_PARAMS,
                    "message": f"Task not found: {task_id}",
                },
                request_id=req_id,
            ).to_dict()
        return A2AResponse(result=task.to_dict(), request_id=req_id).to_dict()

    def _handle_task_cancel(
        self, params: dict[str, Any], req_id: str
    ) -> dict[str, Any]:
        """Cancel a task."""
        task_id = str(params.get("id", ""))
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return A2AResponse(
                error={
                    "code": ERROR_INVALID_PARAMS,
                    "message": f"Task not found: {task_id}",
                },
                request_id=req_id,
            ).to_dict()
        if task.terminal:
            # Already finished — cancelling is a no-op, not an error.
            return A2AResponse(result=task.to_dict(), request_id=req_id).to_dict()
        task.state = TaskState.CANCELED
        task.metadata["canceled_at"] = time.time()
        return A2AResponse(result=task.to_dict(), request_id=req_id).to_dict()

    # -- storage ------------------------------------------------------------ #
    def _store(self, task: A2ATask) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            # Bound the store — oldest tasks fall off first.
            if len(self._tasks) > self._max_tasks:
                for key in list(self._tasks)[: len(self._tasks) - self._max_tasks]:
                    self._tasks.pop(key, None)

    # -- route descriptions -------------------------------------------------- #
    def get_routes(self) -> list[dict[str, Any]]:
        """Route definitions, for mounting in a web framework."""
        return [
            {
                "path": "/.well-known/agent.json",
                "method": "GET",
                "handler": "agent_card",
            },
            {"path": "/a2a/tasks", "method": "POST", "handler": "handle_request"},
        ]


def _extract_input(params: dict[str, Any]) -> str:
    """Pull the task input out of the A2A params shape.

    The canonical form is ``message.parts[0].text``; ``input`` is accepted
    as a convenience shorthand.
    """
    message = params.get("message")
    if isinstance(message, dict):
        parts = message.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    return str(part["text"])
        if message.get("text"):
            return str(message["text"])
    value = params.get("input")
    if isinstance(value, str):
        return value
    if value:
        return str(value)
    return ""


__all__ = ["A2AServer"]
