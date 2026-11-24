"""
Ollama adapter — native `/api/chat` protocol.

Model-agnostic by construction: the model string is supplied by the tier
config, never hardcoded here.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterator, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    StreamChunk,
    ToolCall,
    Usage,
    register_adapter,
)


@register_adapter
class OllamaAdapter(ModelAdapter):
    """Talks to a local (or remote) Ollama daemon."""

    name = "ollama"
    supports_tools = True
    supports_vision = True
    supports_streaming = True
    supports_json_mode = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.base_url = self.base_url or "http://localhost:11434"

    # -- payload --------------------------------------------------------- #
    def _payload(
        self,
        messages: Sequence[Message],
        capability_tier: str,
        stream: bool,
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "temperature": kwargs.pop("temperature", 0.7),
            "num_predict": kwargs.pop("max_tokens", 2048),
        }
        if kwargs.get("top_p") is not None:
            opts["top_p"] = kwargs.pop("top_p")
        if kwargs.get("top_k") is not None:
            opts["top_k"] = kwargs.pop("top_k")
        if kwargs.get("repeat_penalty") is not None:
            opts["repeat_penalty"] = kwargs.pop("repeat_penalty")
        if kwargs.get("repeat_last_n") is not None:
            opts["repeat_last_n"] = kwargs.pop("repeat_last_n")
        if kwargs.get("stop_sequences"):
            opts["stop"] = list(kwargs.pop("stop_sequences"))
        if kwargs.get("num_ctx"):
            opts["num_ctx"] = kwargs.pop("num_ctx")
        if kwargs.get("seed") is not None:
            opts["seed"] = kwargs.pop("seed")
        if kwargs.get("extra_options"):
            opts.update(kwargs.pop("extra_options"))

        payload: dict[str, Any] = {
            "model": model,
            "messages": self.messages_to_dicts(messages),
            "stream": stream,
            "options": opts,
        }
        tools = kwargs.pop("tools", None)
        if tools:
            payload["tools"] = tools
        if kwargs.get("format"):
            payload["format"] = kwargs.pop("format")
        return payload

    # -- generation ------------------------------------------------------ #
    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        import httpx

        model = str(kwargs.pop("model", "") or self.extra.get("model", ""))
        payload = self._payload(messages, capability_tier, stream, model, **kwargs)
        url = f"{self.base_url}/api/chat"
        t0 = time.perf_counter()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload,
                                   headers={"Content-Type": "application/json"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return GenerationResult(
                provider=self.name, tier=capability_tier, model=model,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.perf_counter() - t0) * 1000,
                finish_reason="error",
            )

        latency = (time.perf_counter() - t0) * 1000
        msg = data.get("message") or {}
        text = msg.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            tool_calls.append(
                ToolCall(id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                         name=fn.get("name", ""), arguments=args)
            )

        usage = Usage(
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(data.get("eval_count", 0) or 0),
        )
        return GenerationResult(
            text=text,
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", model),
            provider=self.name,
            tier=capability_tier,
            latency_ms=latency,
            time_to_first_token_ms=latency if text else 0.0,
            finish_reason=data.get("done_reason", "stop"),
            raw=data,
        )

    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        import httpx

        model = str(kwargs.pop("model", "") or self.extra.get("model", ""))
        payload = self._payload(messages, capability_tier, True, model, **kwargs)
        url = f"{self.base_url}/api/chat"
        t0 = time.perf_counter()
        ttft = 0.0
        usage = Usage()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream("POST", url, json=payload,
                                   headers={"Content-Type": "application/json"}) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        piece = ((chunk.get("message") or {}).get("content")) or ""
                        if piece:
                            if usage.completion_tokens == 0:
                                ttft = (time.perf_counter() - t0) * 1000
                            yield StreamChunk(text=piece)
                        if chunk.get("done"):
                            usage = Usage(
                                prompt_tokens=int(chunk.get("prompt_eval_count", 0) or 0),
                                completion_tokens=int(chunk.get("eval_count", 0) or 0),
                            )
                            break
        except Exception as exc:
            yield StreamChunk(done=True, error=f"{type(exc).__name__}: {exc}")
            return

        yield StreamChunk(done=True, usage=usage)

    # -- discovery -------------------------------------------------------- #
    def list_models(self) -> list[dict[str, Any]]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 10.0)) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        return [
            {
                "id": m.get("name", ""),
                "provider": self.name,
                "size": m.get("size", 0),
                "modified": m.get("modified_at", ""),
            }
            for m in data.get("models") or []
        ]

    def health(self) -> tuple[bool, str]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 8.0)) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return (resp.status_code == 200, f"HTTP {resp.status_code}")
        except Exception as exc:
            return (False, f"{type(exc).__name__}: {exc}")
