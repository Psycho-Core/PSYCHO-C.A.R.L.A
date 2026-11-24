"""
Generic HTTP adapter.

The final escape hatch: if a provider speaks some bespoke JSON dialect, describe
it here with template strings and PSYCHO-C.A.R.L.A. will do the translation
without writing a new adapter class.

Config example:

    [engine.adapters.my_backend]
    base_url = "https://example.local/generate"
    prompt_template = "{prompt}"
    response_path = "output.text"

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    Usage,
    register_adapter,
)


def _dig(data: Any, path: str) -> Any:
    """Resolve a dotted path such as `output.text` inside a nested dict."""
    if not path:
        return data
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


@register_adapter
class GenericHttpAdapter(ModelAdapter):
    """Configurable HTTP JSON adapter driven entirely by the TOML file."""

    name = "generic_http"
    supports_tools = False
    supports_vision = False
    supports_streaming = False
    supports_json_mode = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.base_url = self.base_url or ""
        self.method = str(self.extra.get("method", "POST")).upper()
        self.prompt_template = str(self.extra.get("prompt_template", "{prompt}"))
        self.response_path = str(self.extra.get("response_path", ""))
        self.prompt_path = str(self.extra.get("prompt_path", ""))
        self.model_path = str(self.extra.get("model_path", ""))
        self.static_fields: dict[str, Any] = dict(self.extra.get("static_fields", {}) or {})
        self.headers: dict[str, str] = dict(self.extra.get("headers", {}) or {})

    def _render(self, template: str, values: dict[str, Any]) -> str:
        out = template
        for key, val in values.items():
            out = out.replace("{" + key + "}", str(val))
        return out

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        import httpx

        model = str(kwargs.pop("model", "") or self.extra.get("model", ""))
        convo = "\n\n".join(f"{m.role}: {m.content}" for m in messages)
        values = {"prompt": convo, "model": model, "system": "", "tier": capability_tier}

        body: dict[str, Any] = {}
        prompt_val = self._render(self.prompt_template, values)
        if self.prompt_path:
            body[self.prompt_path] = prompt_val
        else:
            body["prompt"] = prompt_val
        if model and self.model_path:
            body[self.model_path] = model
        if kwargs.get("temperature") is not None:
            body["temperature"] = kwargs.pop("temperature")
        if kwargs.get("max_tokens") is not None:
            body["max_tokens"] = kwargs.pop("max_tokens")
        body.update(self.static_fields)

        headers = {"Content-Type": "application/json"}
        headers.update(self.headers)
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")

        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(self.method, self.base_url, json=body,
                                      headers=headers)
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
        text = _dig(data, self.response_path) if self.response_path else data
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)

        return GenerationResult(
            text=text,
            usage=Usage(),
            model=model,
            provider=self.name,
            tier=capability_tier,
            latency_ms=latency,
            time_to_first_token_ms=latency if text else 0.0,
            raw=data,
        )

    def health(self) -> tuple[bool, str]:
        import httpx

        try:
            with httpx.Client(timeout=min(self.timeout, 8.0)) as client:
                resp = client.get(self.base_url)
                return (resp.status_code < 500, f"HTTP {resp.status_code}")
        except Exception as exc:
            return (False, f"{type(exc).__name__}: {exc}")
