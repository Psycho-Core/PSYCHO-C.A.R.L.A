"""
PSYCHO-C.A.R.L.A. — MCP (Model Context Protocol) Client.

Speaks the JSON-RPC 2.0 transport over stdio and HTTP. Servers are declared
in `psycho_carla.toml` under `[tools.mcp.servers.*]` and their tools are
bridged into the local ToolRegistry under a `mcp__<server>__<tool>` name so
the model can call them like any native tool.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..registry import ToolRegistry, get_registry

PROTOCOL_VERSION = "2024-11-05"


def _expand_secret(value: str) -> str:
    """Expand ``${VAR}`` in an MCP server's environment.

    The environment is consulted first. When the variable is not set, the
    vault is tried: a config entry of ``${PSYCHO_CARLA_MCP_<NAME>_KEY}``
    resolves to the secret stored in the vault under
    ``mcp_<name>_key``, so a token never has to sit in the config file.
    """
    import re

    match = re.fullmatch(r"\$\{(PSYCHO_CARLA_MCP_[A-Z0-9_]+_KEY)\}", value.strip())
    if not match:
        return os.path.expandvars(value)

    var_name = match.group(1)
    from_env = os.environ.get(var_name, "")
    if from_env:
        return from_env

    # Try the vault: PSYCHO_CARLA_MCP_GITHUB_KEY -> mcp_github_key
    stem = var_name[len("PSYCHO_CARLA_MCP_"):-len("_KEY")]
    vault_name = f"mcp_{stem.lower()}_key"
    try:
        from ...security import Vault

        secret = Vault().get(vault_name)
    except Exception:
        secret = ""
    return secret or os.path.expandvars(value)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class MCPServerConfig:
    """One configured MCP server."""

    name: str
    transport: str = "stdio"          # stdio | http | sse
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    enabled: bool = True
    prefix: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "MCPServerConfig":
        env = data.get("env", {}) or {}
        resolved_env = {
            k: _expand_secret(str(v)) for k, v in env.items()
        }
        return cls(
            name=name,
            transport=str(data.get("transport", "stdio")),
            command=str(data.get("command", "")),
            args=[str(a) for a in (data.get("args", []) or [])],
            env=resolved_env,
            cwd=str(data.get("cwd", "")),
            url=str(data.get("url", "")),
            headers=dict(data.get("headers", {}) or {}),
            timeout=float(data.get("timeout", 30.0)),
            enabled=bool(data.get("enabled", True)),
            prefix=str(data.get("prefix", "")),
        )


# --------------------------------------------------------------------------- #
# JSON-RPC client
# --------------------------------------------------------------------------- #
class JSONRPCClient:
    """Minimal JSON-RPC 2.0 client with stdio and HTTP transports."""

    def __init__(self, server: MCPServerConfig) -> None:
        self.server = server
        self._id = 0
        self._lock = threading.Lock()
        self._proc: Any = None
        self._http: Any = None
        self._initialized = False
        self._server_info: dict[str, Any] = {}

    # -- id generation --------------------------------------------------- #
    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    # -- transports ------------------------------------------------------- #
    def _start_stdio(self) -> None:
        if self._proc is not None:
            return
        cmd = self.server.command
        if not cmd:
            raise RuntimeError(f"MCP server '{self.server.name}' has no command.")
        exe = shutil.which(cmd) or cmd
        env = dict(os.environ)
        env.update(self.server.env)
        self._proc = subprocess.Popen(
            [exe, *self.server.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=self.server.cwd or None,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def _start_http(self) -> None:
        import httpx

        if self._http is None:
            self._http = httpx.Client(timeout=self.server.timeout)

    # -- request ---------------------------------------------------------- #
    def request(self, method: str, params: dict[str, Any] | None = None,
                timeout: float | None = None) -> Any:
        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        if self.server.transport == "stdio":
            return self._request_stdio(payload, timeout)
        return self._request_http(payload, timeout)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if self.server.transport == "stdio":
            self._start_stdio()
            try:
                assert self._proc and self._proc.stdin
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()
            except Exception:
                pass
        else:
            self._start_http()
            try:
                self._http.post(self.server.url, json=payload,
                                headers=self.server.headers)
            except Exception:
                pass

    def _request_stdio(self, payload: dict[str, Any], timeout: float | None) -> Any:
        self._start_stdio()
        assert self._proc and self._proc.stdin and self._proc.stdout
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            raise RuntimeError(f"MCP stdio write failed: {exc}") from exc

        deadline = time.time() + (timeout or self.server.timeout)
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                # Process may have exited; surface stderr.
                err = ""
                if self._proc.stderr:
                    try:
                        err = self._proc.stderr.read() or ""
                    except Exception:
                        pass
                raise RuntimeError(
                    f"MCP server '{self.server.name}' closed the stream. {err[:500]}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") != payload["id"]:
                continue  # notification or another request
            if "error" in msg:
                err = msg["error"]
                raise RuntimeError(f"MCP error: {err.get('message', err)}")
            return msg.get("result")

        raise TimeoutError(
            f"MCP request '{payload['method']}' timed out after "
            f"{timeout or self.server.timeout:.0f}s"
        )

    def _request_http(self, payload: dict[str, Any], timeout: float | None) -> Any:
        self._start_http()
        try:
            resp = self._http.post(
                self.server.url,
                json=payload,
                headers={"Content-Type": "application/json", **self.server.headers},
                timeout=timeout or self.server.timeout,
            )
            resp.raise_for_status()
            msg = resp.json()
        except Exception as exc:
            raise RuntimeError(f"MCP HTTP request failed: {exc}") from exc
        if "error" in msg:
            err = msg["error"]
            raise RuntimeError(f"MCP error: {err.get('message', err)}")
        return msg.get("result")

    # -- lifecycle --------------------------------------------------------- #
    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self._server_info
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "psycho-carla", "version": "1.0.0"},
            },
        )
        self._server_info = result or {}
        self.notify("notifications/initialized")
        self._initialized = True
        return self._server_info

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        return (result or {}).get("tools", []) or []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None
        self._initialized = False


import subprocess  # noqa: E402  (used by the stdio transport)


# --------------------------------------------------------------------------- #
# MCP manager
# --------------------------------------------------------------------------- #
class MCPManager:
    """Owns the configured MCP servers and bridges their tools into the registry."""

    def __init__(self, config: "MCPServerConfig | None" = None) -> None:
        self.servers: dict[str, MCPServerConfig] = {}
        self.clients: dict[str, JSONRPCClient] = {}
        self.errors: dict[str, str] = {}
        self._registry: ToolRegistry | None = None

    # -- configuration ---------------------------------------------------- #
    def configure(self, servers: dict[str, dict[str, Any]],
                  enabled: bool = True) -> None:
        self.servers = {}
        if not enabled:
            return
        for name, data in (servers or {}).items():
            cfg = MCPServerConfig.from_dict(name, data)
            if cfg.enabled:
                self.servers[name] = cfg

    def attach(self, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    # -- lifecycle -------------------------------------------------------- #
    def connect_all(self) -> dict[str, str]:
        """Start every configured server and import its tools."""
        results: dict[str, str] = {}
        for name, cfg in self.servers.items():
            try:
                client = JSONRPCClient(cfg)
                client.initialize()
                self.clients[name] = client
                count = self._import_tools(name, client)
                results[name] = f"ok ({count} tools)"
            except Exception as exc:
                self.errors[name] = f"{type(exc).__name__}: {exc}"
                results[name] = f"error: {exc}"
        return results

    def disconnect_all(self) -> None:
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()

    # -- tool bridging ----------------------------------------------------- #
    def _import_tools(self, server_name: str, client: JSONRPCClient) -> int:
        if self._registry is None:
            self.attach()
        assert self._registry is not None
        try:
            remote_tools = client.list_tools()
        except Exception:
            return 0

        prefix = self.servers[server_name].prefix or f"mcp__{server_name}__"
        for t in remote_tools:
            raw_name = t.get("name", "")
            if not raw_name:
                continue
            local_name = f"{prefix}{raw_name}"
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}

            def _make_caller(c: JSONRPCClient, tool_name: str):
                def _call(**kwargs: Any) -> str:
                    try:
                        result = c.call_tool(tool_name, kwargs)
                    except Exception as exc:
                        return f"Error calling MCP tool '{tool_name}': {exc}"
                    return _render_mcp_result(result)
                return _call

            self._registry.register(
                local_name,
                _make_caller(client, raw_name),
                description=t.get("description", f"MCP tool '{raw_name}' from {server_name}"),
                parameters=schema,
                namespace="mcp",
            )
        return len(remote_tools)

    # -- introspection ------------------------------------------------------ #
    def status(self) -> list[dict[str, Any]]:
        out = []
        for name, cfg in self.servers.items():
            client = self.clients.get(name)
            info = client._server_info if client else {}
            out.append({
                "name": name,
                "transport": cfg.transport,
                "connected": client is not None,
                "server_info": info.get("serverInfo", {}),
                "error": self.errors.get(name, ""),
            })
        return out


def _render_mcp_result(result: Any) -> str:
    """Flatten an MCP tool result into text for the model."""
    if result is None:
        return "(no result)"
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(str(block.get("text", "")))
                    elif block.get("type") == "resource":
                        parts.append(json.dumps(block.get("resource", {}), default=str))
                    else:
                        parts.append(json.dumps(block, default=str))
                else:
                    parts.append(str(block))
            if parts:
                return "\n".join(parts)
        if result.get("isError"):
            return f"MCP tool error: {json.dumps(result, default=str)}"
        return json.dumps(result, indent=2, default=str)
    return json.dumps(result, indent=2, default=str)
