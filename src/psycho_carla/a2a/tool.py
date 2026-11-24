"""
A2A agent tool — wraps a remote A2A agent as an ordinary C.A.R.L.A. tool.

This is the bridge that lets one agent call another: an external A2A agent
becomes a tool in the local registry, so any agent with that tool enabled
can delegate to it without knowing anything about JSON-RPC.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..tools.registry import Tool, ToolRegistry, ToolResult
from .client import A2AClient, A2AClientError

logger = logging.getLogger(__name__)


class A2AAgentTool:
    """Wraps an external A2A agent as a callable tool."""

    def __init__(self, client: A2AClient, *, name: str = "") -> None:
        self._client = client
        self._name = name or "a2a_agent"
        self._description = "External A2A agent"
        # Try to discover the remote agent's identity for a better name and
        # description. A failure here is not fatal — the tool still works.
        try:
            card = client.discover()
            if not name and card.name:
                self._name = f"a2a_{card.name.lower().replace(' ', '_')}"
            if card.description:
                self._description = card.description
            else:
                self._description = f"External A2A agent: {card.name}"
        except Exception as exc:
            logger.debug("Could not discover A2A agent details: %s", exc)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def client(self) -> A2AClient:
        return self._client

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Input text to send to the remote agent.",
                }
            },
            "required": ["input"],
        }

    def execute(self, **params: Any) -> ToolResult:
        """Send the input to the remote agent and return its output."""
        input_text = str(params.get("input", "") or "")
        if not input_text:
            return ToolResult(
                tool=self._name, ok=False, content="No input provided.",
                error="no_input",
            )
        t0 = time.perf_counter()
        try:
            task = self._client.send_task(input_text)
        except A2AClientError as exc:
            return ToolResult(
                tool=self._name, ok=False,
                content=f"A2A call failed: {exc}", error=str(exc),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool=self._name, ok=False,
                content=f"A2A call failed: {exc}",
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        duration = (time.perf_counter() - t0) * 1000
        state = task.state.value if hasattr(task.state, "value") else str(task.state)
        ok = state in ("completed", "working")
        return ToolResult(
            tool=self._name,
            ok=ok,
            content=task.output_text,
            data={"task_id": task.task_id, "state": state},
            error="" if ok else state,
            duration_ms=duration,
        )


def register_a2a_agent(
    registry: ToolRegistry,
    base_url: str,
    *,
    name: str = "",
    token: str = "",
    timeout: float = 30.0,
) -> Tool:
    """Register a remote A2A agent as a tool in ``registry``."""
    client = A2AClient(base_url, timeout=timeout, token=token)
    agent_tool = A2AAgentTool(client, name=name)

    def _invoke(input: str = "") -> dict[str, Any]:
        return agent_tool.execute(input=input)

    _invoke.__name__ = agent_tool.name
    _invoke.__doc__ = agent_tool.description

    registry.register(
        agent_tool.name,
        _invoke,
        agent_tool.description,
        agent_tool.parameters,
        namespace="a2a",
    )
    return registry.get(agent_tool.name)  # type: ignore[return-value]


__all__ = ["A2AAgentTool", "register_a2a_agent"]
