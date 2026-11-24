"""
PSYCHO-C.A.R.L.A. — MCP Server.

Exposes PSYCHO-C.A.R.L.A.'s own capabilities (memory, tools, telemetry,
agent invocation) over the Model Context Protocol, so any MCP client — Claude
Desktop, a custom harness, another agent — can drive C.A.R.L.A.

Transport: stdio (default) or HTTP via FastAPI.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """A JSON-RPC 2.0 MCP server over stdio."""

    def __init__(self, name: str = "psycho-carla", version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self.tools: dict[str, dict[str, Any]] = {}
        self.handlers: dict[str, Callable[..., Any]] = {}
        self._running = False
        self._out_lock = threading.Lock()

    # -- tool registration ------------------------------------------------- #
    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        handler: Callable[..., Any] | None = None,
    ) -> None:
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": parameters or {"type": "object", "properties": {}},
        }
        if handler:
            self.handlers[name] = handler

    # -- JSON-RPC plumbing -------------------------------------------------- #
    def _send(self, payload: dict[str, Any]) -> None:
        with self._out_lock:
            sys.stdout.write(json.dumps(payload) + "\n")
            sys.stdout.flush()

    def _result(self, req_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": code, "message": message}})

    def handle(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        elif method == "notifications/initialized":
            return
        elif method == "ping":
            self._result(req_id, {})
        elif method == "tools/list":
            self._result(req_id, {"tools": list(self.tools.values())})
        elif method == "tools/call":
            self._call_tool(req_id, params)
        elif method == "resources/list":
            self._result(req_id, {"resources": []})
        elif method == "prompts/list":
            self._result(req_id, {"prompts": []})
        else:
            if req_id is not None:
                self._error(req_id, -32601, f"Method not found: {method}")

    def _call_tool(self, req_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name", "")
        args = params.get("arguments") or {}
        handler = self.handlers.get(name)
        if handler is None:
            self._result(req_id, {
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
                "isError": True,
            })
            return
        try:
            output = handler(**args)
            if not isinstance(output, str):
                output = json.dumps(output, indent=2, default=str)
            self._result(req_id, {
                "content": [{"type": "text", "text": output}],
                "isError": False,
            })
        except Exception as exc:
            self._result(req_id, {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            })

    # -- run loop ------------------------------------------------------------ #
    def serve_stdio(self) -> None:
        """Read JSON-RPC messages from stdin until EOF."""
        self._running = True
        sys.stderr.write(f"[{self.name}] MCP server ready on stdio\n")
        sys.stderr.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            try:
                self.handle(msg)
            except Exception as exc:
                if msg.get("id") is not None:
                    self._error(msg["id"], -32603, str(exc))
        self._running = False


# --------------------------------------------------------------------------- #
# Default server wiring
# --------------------------------------------------------------------------- #
def build_default_server(runtime: Any = None) -> MCPServer:
    """Create an MCP server exposing PSYCHO-C.A.R.L.A.'s core capabilities."""
    server = MCPServer()

    def _ask(prompt: str, agent: str = "orchestrator", tier: str = "") -> str:
        if runtime is None:
            return "Error: no runtime bound to this MCP server."
        result = runtime.ask(prompt, agent=agent, tier=tier or None)
        return result.get("text", "") if isinstance(result, dict) else str(result)

    def _memory_search(query: str, top_k: int = 5) -> str:
        if runtime is None:
            return "Error: no runtime bound."
        return json.dumps(runtime.memory.search(query, top_k=top_k), indent=2, default=str)

    def _memory_store(content: str, tags: str = "") -> str:
        if runtime is None:
            return "Error: no runtime bound."
        mem_id = runtime.memory.store(content, tags=[t for t in tags.split(",") if t])
        return f"Stored memory {mem_id}."

    def _list_agents() -> str:
        if runtime is None:
            return "[]"
        return json.dumps(runtime.list_agents(), indent=2, default=str)

    def _telemetry() -> str:
        if runtime is None:
            return "{}"
        return json.dumps(runtime.telemetry.summary(), indent=2, default=str)

    server.register_tool(
        "carla_ask", "Send a prompt to the PSYCHO-C.A.R.L.A. runtime.",
        {"type": "object", "properties": {
            "prompt": {"type": "string"},
            "agent": {"type": "string"},
            "tier": {"type": "string"},
        }, "required": ["prompt"]},
        _ask,
    )
    server.register_tool(
        "carla_memory_search", "Search the persistent memory store.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        }, "required": ["query"]},
        _memory_search,
    )
    server.register_tool(
        "carla_memory_store", "Store a memory in the persistent store.",
        {"type": "object", "properties": {
            "content": {"type": "string"},
            "tags": {"type": "string"},
        }, "required": ["content"]},
        _memory_store,
    )
    server.register_tool(
        "carla_list_agents", "List the available agents.",
        {"type": "object", "properties": {}},
        _list_agents,
    )
    server.register_tool(
        "carla_telemetry", "Get the current efficiency telemetry summary.",
        {"type": "object", "properties": {}},
        _telemetry,
    )
    return server
