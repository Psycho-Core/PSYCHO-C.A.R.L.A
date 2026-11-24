"""
A2A client — discover and call external A2A agents.

Discovery reads ``/.well-known/agent.json``; tasks go to ``/a2a/tasks`` over
JSON-RPC 2.0.

The HTTP layer uses ``httpx`` when available and falls back to the standard
library ``urllib`` so the client has no hard dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .protocol import A2ARequest, A2ATask, AgentCard


class A2AClientError(RuntimeError):
    """Raised when an A2A call fails."""


class A2AClient:
    """Client for calling external A2A-compatible agents."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        token: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._token = token
        self._headers = dict(headers or {})
        self._card: AgentCard | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def card(self) -> AgentCard | None:
        return self._card

    # -- discovery --------------------------------------------------------- #
    def discover(self) -> AgentCard:
        """Fetch the agent card from ``/.well-known/agent.json``."""
        data = self._get_json(f"{self._base_url}/.well-known/agent.json")
        self._card = AgentCard.from_dict(data)
        if not self._card.url:
            self._card.url = self._base_url
        return self._card

    # -- tasks -------------------------------------------------------------- #
    def send_task(self, input_text: str) -> A2ATask:
        """Send a task to the remote agent and return the result."""
        request = A2ARequest(
            method="tasks/send",
            params={
                "message": {
                    "role": "user",
                    "parts": [{"text": input_text}],
                }
            },
        )
        data = self._post_json(f"{self._base_url}/a2a/tasks", request.to_dict())
        return _task_from_response(data, input_text)

    def get_task(self, task_id: str) -> A2ATask:
        """Get the status of a previously submitted task."""
        request = A2ARequest(method="tasks/get", params={"id": task_id})
        data = self._post_json(f"{self._base_url}/a2a/tasks", request.to_dict())
        return _task_from_response(data, "")

    def cancel_task(self, task_id: str) -> A2ATask:
        """Cancel a running task."""
        request = A2ARequest(method="tasks/cancel", params={"id": task_id})
        data = self._post_json(f"{self._base_url}/a2a/tasks", request.to_dict())
        return _task_from_response(data, "")

    def call(self, input_text: str) -> str:
        """Send a task and return just the output text."""
        return self.send_task(input_text).output_text

    # -- HTTP --------------------------------------------------------------- #
    def _headers_for(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept}
        headers.update(self._headers)
        if self._token:
            headers.setdefault("Authorization", f"Bearer {self._token}")
        return headers

    def _get_json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=self._headers_for("application/json"))
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise A2AClientError(f"HTTP {exc.code} from {url}") from exc
        except Exception as exc:
            raise A2AClientError(f"{type(exc).__name__}: {exc}") from exc

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = self._headers_for("application/json")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise A2AClientError(
                f"HTTP {exc.code} from {url}" + (f": {detail}" if detail else "")
            ) from exc
        except Exception as exc:
            raise A2AClientError(f"{type(exc).__name__}: {exc}") from exc


def _task_from_response(data: dict[str, Any], fallback_input: str) -> A2ATask:
    """Build a task from a JSON-RPC response, surfacing errors."""
    if isinstance(data, dict) and data.get("error"):
        error = data["error"]
        message = error.get("message", "unknown error") if isinstance(error, dict) else str(error)
        code = error.get("code", "") if isinstance(error, dict) else ""
        raise A2AClientError(f"A2A error {code}: {message}")
    result = (data or {}).get("result", {}) or {}
    task = A2ATask.from_dict(result)
    if not task.task_id:
        task.task_id = str(result.get("id", ""))
    if fallback_input and not task.input_text:
        task.input_text = fallback_input
    return task


__all__ = ["A2AClient", "A2AClientError"]
