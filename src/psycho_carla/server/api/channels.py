"""
Channel API routes.

Exposes the messaging channels over HTTP so they can be inspected and
driven from the HUD, and so webhook-style platforms (Discord, WhatsApp,
Google Chat, webchat) have somewhere to deliver their events.

Layer: Pillar 3 (Channels).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _bridge(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised.")
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="No channels are configured.")
    return bridge


@router.get("/channels")
async def list_channels(request: Request):
    """Every configured channel and its state."""
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised.")

    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        return {"channels": [], "count": 0, "status": "disconnected"}

    return {
        "channels": bridge.describe(),
        "count": len(bridge.channels),
        "status": bridge.status().value,
    }


@router.get("/channels/status")
async def channel_status(request: Request):
    """The aggregate channel status."""
    bridge = _bridge(request)
    return {
        "status": bridge.status().value,
        "destinations": bridge.list_channels(),
        "count": len(bridge.channels),
        "channels": bridge.describe(),
    }


@router.post("/channels/send")
async def channel_send(request: Request, body: dict[str, Any]):
    """Send a message through a channel."""
    bridge = _bridge(request)

    channel = str(body.get("channel", "")).strip()
    target = str(body.get("target", "")).strip()
    text = str(body.get("text", "")).strip()

    if not channel:
        raise HTTPException(status_code=400, detail="channel is required.")
    if not target:
        raise HTTPException(status_code=400, detail="target is required.")
    if not text:
        raise HTTPException(status_code=400, detail="text is required.")

    ok = bridge.send(channel, text)
    return {"ok": ok, "channel": channel, "target": target}


@router.post("/channels/inbound/{channel}")
async def channel_inbound(request: Request, channel: str, body: dict[str, Any]):
    """Deliver an inbound message to a channel.

    This is the endpoint webhook-style platforms POST to. The reply, if
    any, is returned in the body — for platforms where the HTTP response
    *is* the delivery (WhatsApp, Google Chat, webchat) that is exactly
    right.
    """
    bridge = _bridge(request)

    adapter = bridge.channels.get(channel)
    if adapter is None:
        raise HTTPException(
            status_code=404, detail=f"No channel '{channel}' is configured."
        )

    handler = getattr(adapter, "handle_event", None)
    if handler is None:
        raise HTTPException(
            status_code=400,
            detail=f"'{channel}' does not accept inbound HTTP events.",
        )

    try:
        reply = handler(body)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"reply": reply or ""}


@router.post("/channels/broadcast")
async def channel_broadcast(request: Request, body: dict[str, Any]):
    """Send a message to every known destination."""
    bridge = _bridge(request)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required.")
    sent = bridge.broadcast(text)
    return {"ok": True, "sent": sent}


__all__ = ["router"]
