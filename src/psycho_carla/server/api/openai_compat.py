"""
PSYCHO-C.A.R.L.A. — OpenAI-Compatible API.

Exposes `/v1/chat/completions`, `/v1/models` and `/v1/completions` so any
OpenAI SDK or tool can talk to C.A.R.L.A. directly.

The `model` field accepts a **tier alias** (`fast_tier`, `deep_tier`, ...) as
well as a raw provider model id — the whole point of the tier system.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from ...core.persona import Persona
from ...providers.base import Message

router = APIRouter()


class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "fast_tier"
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    agent: str | None = None
    session_id: str = "default"
    persona: bool = False   # return the speech channel too (C.A.R.L.A. extension)


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "psycho-carla"
    created: int = 0
    tier: str = ""


def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised.")
    return runtime


def _resolve_tier(runtime: Any, model: str) -> str:
    """Map a requested model string onto a tier alias when possible."""
    tiers = runtime.config.intelligence.tiers
    if model in tiers:
        return model
    # Match on provider model id.
    for alias, tier in tiers.items():
        if tier.model == model:
            return alias
    # Unknown -> let the router fall back to the default tier.
    return runtime.router.active_tier


@router.post("/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    runtime = _runtime(request)
    if not body.messages:
        raise HTTPException(status_code=400, detail="messages is required.")

    tier = _resolve_tier(runtime, body.model)
    agent = body.agent or runtime.config.agent.default_agent
    persona = Persona(runtime.config.persona)

    prompt = next(
        (m.content for m in reversed(body.messages) if m.role == "user"), ""
    )
    history = [
        Message(role=m.role, content=m.content)
        for m in body.messages[:-1]
    ]

    if not body.stream:
        result = runtime.ask(
            prompt, agent=agent, tier=tier, session_id=body.session_id,
            history=history,
            **({"temperature": body.temperature} if body.temperature is not None else {}),
        )
        content = result.get("text", "")
        if body.persona:
            content = (
                f"{result.get('speech', '')}\n\n---\n\n{content}"
                if result.get("speech")
                else content
            )
        usage = result.get("usage", {}) or {}
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "carla": {
                    "agent": result.get("agent"),
                    "tier": result.get("tier"),
                    "speech": result.get("speech", ""),
                    "code_blocks": result.get("code_blocks", []),
                    "latency_ms": result.get("latency_ms", 0),
                    "tool_calls": result.get("tool_calls", []),
                },
            }
        )

    async def event_stream() -> AsyncIterator[str]:
        yield _sse({"id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": body.model, "choices": []})
        for chunk in runtime.ask_stream(
            prompt, agent=agent, tier=tier, session_id=body.session_id
        ):
            if chunk.get("type") == "token":
                yield _sse({
                    "id": "chunk", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": body.model,
                    "choices": [{"index": 0, "delta": {"content": chunk["content"]},
                                 "finish_reason": None}],
                })
            elif chunk.get("type") == "error":
                yield _sse({"error": {"message": chunk.get("content", "error")}})
        yield _sse({
            "id": "chunk", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": body.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        })
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/models")
async def list_models(request: Request):
    runtime = _runtime(request)
    models = []
    created = int(time.time())
    for tier in runtime.router.list_tiers():
        models.append(
            ModelCard(
                id=tier["alias"], created=created, tier=tier["alias"],
            ).model_dump()
        )
    for m in runtime.router.list_models():
        if not m.get("id"):
            continue
        models.append(
            ModelCard(id=m["id"], created=created,
                      tier=m.get("tier", "")).model_dump()
        )
    return {"object": "list", "data": models}


@router.get("/models/{model_id}")
async def get_model(request: Request, model_id: str):
    runtime = _runtime(request)
    for tier in runtime.router.list_tiers():
        if tier["alias"] == model_id:
            return ModelCard(id=model_id, created=int(time.time()),
                             tier=model_id).model_dump()
    raise HTTPException(status_code=404, detail=f"Unknown model '{model_id}'.")


@router.post("/completions")
async def completions(request: Request, body: dict[str, Any]):
    """Legacy-style text completion endpoint."""
    runtime = _runtime(request)
    prompt = str(body.get("prompt", ""))
    model = str(body.get("model", "fast_tier"))
    tier = _resolve_tier(runtime, model)
    result = runtime.ask(prompt, tier=tier, session_id="default")
    return {
        "id": f"cmpl-{uuid.uuid4().hex[:24]}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "text": result.get("text", ""),
                     "finish_reason": "stop"}],
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
