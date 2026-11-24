"""
llama.cpp adapter (`llama-server`).

llama-server exposes an OpenAI-compatible surface, but this adapter talks to
the native `/completion` endpoint so we can pass per-request sampler overrides
and read the detailed timings llama.cpp reports (useful for the HUD's
tokens/sec readout).

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    StreamChunk,
    Usage,
    register_adapter,
)


@register_adapter
class LlamaCppAdapter(ModelAdapter):
    """Talks to a llama.cpp `llama-server` instance."""

    name = "llamacpp"
    supports_tools = False
    supports_vision = False
    supports_streaming = True
    supports_json_mode = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.base_url = self.base_url or "http://localhost:8080"

    def _payload(self, messages: Sequence[Message], model: str, **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": self.messages_to_dicts(messages),
            "stream": False,
            "cache_prompt": True,
        }
        if kwargs.get("temperature") is not None:
            payload["temperature"] = kwargs.pop("temperature")
        if kwargs.get("max_tokens") is not None:
            payload["n_predict"] = kwargs.pop("max_tokens")
        if kwargs.get("top_p") is not None:
            payload["top_p"] = kwargs.pop("top_p")
        if kwargs.get("top_k") is not None:
            payload["top_k"] = kwargs.pop("top_k")
        if kwargs.get("repeat_penalty") is not None:
            payload["repeat_penalty"] = kwargs.pop("repeat_penalty")
        if kwargs.get("stop_sequences"):
            payload["stop"] = list(kwargs.pop("stop_sequences"))
        if kwargs.get("json_mode"):
            payload["grammar"] = "json"
        return payload

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        import httpx

        model = str(kwargs.pop("model", "") or self.extra.get("model", ""))
        payload = self._payload(messages, model, **kwargs)
        url = f"{self.base_url}/completion"
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
        timings = data.get("timings") or {}
        completion_tokens = int(timings.get("predicted_n", 0) or 0)
        prompt_tokens = int(timings.get("prompt_n", 0) or 0)
        text = data.get("content") or ""

        return GenerationResult(
            text=text,
            usage=Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
            model=model,
            provider=self.name,
            tier=capability_tier,
            latency_ms=latency,
            time_to_first_token_ms=latency if text else 0.0,
            finish_reason="stop" if data.get("stopped_eos", True) else "length",
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
        payload = self._payload(messages, model, **kwargs)
        payload["stream"] = True
        url = f"{self.base_url}/completion"

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
                        piece = chunk.get("content") or ""
                        if piece:
                            yield StreamChunk(text=piece)
                        if chunk.get("stop"):
                            break
        except Exception as exc:
            yield StreamChunk(done=True, error=f"{type(exc).__name__}: {exc}")
            return

        yield StreamChunk(done=True)

    def list_models(self) -> list[dict[str, Any]]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 10.0)) as client:
                resp = client.get(f"{self.base_url}/v1/models")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []
        return [{"id": m.get("id", ""), "provider": self.name}
                for m in data.get("data") or []]

    def health(self) -> tuple[bool, str]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 8.0)) as client:
                resp = client.get(f"{self.base_url}/health")
                return (resp.status_code == 200, f"HTTP {resp.status_code}")
        except Exception as exc:
            return (False, f"{type(exc).__name__}: {exc}")
