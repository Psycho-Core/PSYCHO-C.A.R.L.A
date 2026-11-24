"""
OpenAI-compatible adapter.

This is the universal escape hatch: any provider exposing an OpenAI-style
`/chat/completions` endpoint (OpenAI, OpenRouter, Together, Groq, LM Studio,
vLLM, SGLang, llama.cpp's llama-server, LiteLLM, Azure, DeepSeek, etc.) works
by pointing `base_url` at it. No code changes, no hardcoded model names.

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
class OpenAICompatAdapter(ModelAdapter):
    """Talks to any OpenAI-compatible `/chat/completions` endpoint."""

    name = "openai_compat"
    supports_tools = True
    supports_vision = True
    supports_streaming = True
    supports_json_mode = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.base_url = self.base_url or "http://localhost:1234/v1"
        if not self.base_url.endswith("/v1") and "/v1" not in self.base_url:
            self.base_url = self.base_url.rstrip("/") + "/v1"
        self.api_key = self.api_key or "not-needed"
        self.api_path = str(self.extra.get("api_path", "/chat/completions"))
        self.models_path = str(self.extra.get("models_path", "/models"))
        self.headers: dict[str, str] = dict(self.extra.get("headers", {}) or {})

    # -- payload building ------------------------------------------------ #
    def _payload(
        self,
        messages: Sequence[Message],
        capability_tier: str,
        stream: bool,
        model: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self.messages_to_dicts(messages),
            "stream": stream,
            "temperature": kwargs.pop("temperature", 0.7),
            "max_tokens": kwargs.pop("max_tokens", 2048),
        }
        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs.pop("top_p")
        if kwargs.get("top_k") is not None:
            payload["top_k"] = kwargs.pop("top_k")
        if kwargs.get("repetition_penalty") is not None:
            payload["repetition_penalty"] = kwargs.pop("repetition_penalty")
        stop = kwargs.pop("stop_sequences", None)
        if stop:
            payload["stop"] = stop
        if kwargs.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}
        tools = kwargs.pop("tools", None)
        if tools:
            payload["tools"] = tools
            if kwargs.pop("tool_choice_auto", False):
                payload["tool_choice"] = "auto"
        if kwargs.get("seed") is not None:
            payload["seed"] = kwargs.pop("seed")
        # Any remaining provider-specific extras.
        payload.update(kwargs.pop("extra_body", {}) or {})
        return payload

    def _headers(self, stream: bool = False) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        h.update(self.headers)
        return h

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
        url = f"{self.base_url}{self.api_path}"
        t0 = time.perf_counter()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers(False))
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            return GenerationResult(
                provider=self.name,
                tier=capability_tier,
                model=model,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.perf_counter() - t0) * 1000,
                finish_reason="error",
            )

        latency = (time.perf_counter() - t0) * 1000
        choices = data.get("choices") or []
        text = ""
        tool_calls: list[ToolCall] = []
        finish_reason = "stop"

        if choices:
            choice = choices[0]
            msg = choice.get("message") or {}
            text = msg.get("content") or ""
            finish_reason = choice.get("finish_reason") or "stop"
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {"_raw": raw_args}
                tool_calls.append(
                    ToolCall(id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                             name=fn.get("name", ""), arguments=args)
                )

        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
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
            finish_reason=finish_reason,
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
        url = f"{self.base_url}{self.api_path}"
        t0 = time.perf_counter()
        ttft = 0.0
        collected = ""
        usage = Usage()
        finish = "stop"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                with client.stream(
                    "POST", url, json=payload, headers=self._headers(True)
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line = line.strip()
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        choices = chunk.get("choices") or []
                        if not choices:
                            u = chunk.get("usage")
                            if u:
                                usage = Usage(
                                    prompt_tokens=int(u.get("prompt_tokens", 0) or 0),
                                    completion_tokens=int(u.get("completion_tokens", 0) or 0),
                                    total_tokens=int(u.get("total_tokens", 0) or 0),
                                )
                            continue
                        delta = (choices[0].get("delta") or {})
                        piece = delta.get("content") or ""
                        if piece:
                            if not collected:
                                ttft = (time.perf_counter() - t0) * 1000
                            collected += piece
                            yield StreamChunk(text=piece)
                        finish = choices[0].get("finish_reason") or finish
        except Exception as exc:
            yield StreamChunk(done=True, error=f"{type(exc).__name__}: {exc}")
            return

        yield StreamChunk(
            done=True,
            usage=usage,
        )
        _ = finish

    # -- discovery -------------------------------------------------------- #
    def list_models(self) -> list[dict[str, Any]]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 10.0)) as client:
                resp = client.get(f"{self.base_url}{self.models_path}",
                                  headers=self._headers(False))
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        out = []
        for item in data.get("data") or []:
            out.append({"id": item.get("id", ""), "provider": self.name,
                        "raw": item})
        return out

    def health(self) -> tuple[bool, str]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 8.0)) as client:
                resp = client.get(f"{self.base_url}{self.models_path}",
                                  headers=self._headers(False))
                return (resp.status_code < 500, f"HTTP {resp.status_code}")
        except Exception as exc:
            return (False, f"{type(exc).__name__}: {exc}")
