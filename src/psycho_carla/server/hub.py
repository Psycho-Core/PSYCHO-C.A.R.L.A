"""
PSYCHO-C.A.R.L.A. — WebSocket Hub.

Broadcasts live runtime state to every connected HUD client: agent status,
telemetry samples, voice state, scheduler events, tier changes.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from fastapi import WebSocket


class WebSocketHub:
    """Fan-out broadcaster for the holographic HUD."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_event: Callable[[str, dict[str, Any]], None] | None = None

    # -- lifecycle --------------------------------------------------------- #
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_event_hook(self, hook: Callable[[str, dict[str, Any]], None]) -> None:
        self._on_event = hook

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        await self._send_to(websocket, {"type": "connected",
                                        "ts": time.time()})

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    # -- sending ------------------------------------------------------------ #
    async def _send_to(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(payload, default=str))
        except Exception:
            async with self._lock:
                self._clients.discard(websocket)

    async def broadcast_async(self, event: str, data: dict[str, Any]) -> None:
        payload = {"type": event, "ts": time.time(), "data": data}
        async with self._lock:
            clients = list(self._clients)
        dead = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    def broadcast(self, event: str, data: dict[str, Any]) -> None:
        """Thread-safe broadcast — callable from any thread."""
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.broadcast_async(event, data), self._loop
            )
        except RuntimeError:
            pass

    # -- runtime bridge ------------------------------------------------------- #
    def attach_runtime(self, runtime: Any) -> None:
        """Wire runtime events and start the telemetry push loop."""
        runtime.on(lambda event, data: self.broadcast(event, data))
        self._loop = runtime.__dict__.get("_asyncio_loop")
