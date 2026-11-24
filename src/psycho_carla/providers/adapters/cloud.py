"""
Cloud, multi-engine failover, and gemma.cpp adapters.

  * ``cloud``     — a hosted OpenAI-compatible endpoint (the upstream
    "cloud" engine). Point it at any hosted API with ``base_url``; the
    API key comes from the tier's ``api_key_env``.
  * ``multi``     — failover across several providers, in order. Options:
    ``{"providers": [{"provider": "ollama", ...}, ...]}`` — each entry is
    an adapter spec. The first provider that answers without an error
    wins; the result records which one served.
  * ``gemma_cpp`` — Google's gemma.cpp inference binary, run as a
    subprocess. Options: ``binary_path``, ``model_path``. When the binary
    or model is absent, generation returns an error result explaining
    what to install — it never fabricates text.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    Usage,
    build_adapter,
    register_adapter,
)
from .openai_compat import OpenAICompatAdapter


@register_adapter
class CloudAdapter(OpenAICompatAdapter):
    """Hosted OpenAI-compatible endpoint."""

    name = "cloud"
    supports_tools = True
    supports_vision = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        opts = dict(options or {})
        opts.setdefault("base_url", "https://api.openai.com")
        super().__init__(opts)


@register_adapter
class MultiEngineAdapter(ModelAdapter):
    """Ordered failover across several provider adapters."""

    name = "multi"
    supports_tools = True
    supports_streaming = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self._specs: list[dict[str, Any]] = list(
            self.options.get("providers", []))
        self._adapters: list[ModelAdapter] = []

    def _chain(self) -> list[ModelAdapter]:
        if not self._adapters:
            for spec in self._specs:
                spec = dict(spec)
                provider = str(spec.pop("provider", ""))
                if not provider:
                    continue
                try:
                    self._adapters.append(build_adapter(provider, spec))
                except Exception:
                    continue
        return self._adapters

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        chain = self._chain()
        if not chain:
            return GenerationResult(
                text="", provider="multi", tier=capability_tier,
                error="multi adapter has no usable providers configured — "
                      "set options.providers to a list of adapter specs.",
            )
        errors: list[str] = []
        for adapter in chain:
            try:
                result = adapter.generate(
                    messages, capability_tier, stream=False, **kwargs)
            except Exception as exc:
                errors.append(f"{adapter.name}: {exc}")
                continue
            if result.error:
                errors.append(f"{adapter.name}: {result.error}")
                continue
            if not result.provider:
                result.provider = adapter.name
            return result
        return GenerationResult(
            text="", provider="multi", tier=capability_tier,
            error="all providers failed: " + "; ".join(errors),
        )

    def health(self) -> tuple[bool, str]:
        for adapter in self._chain():
            try:
                ok, detail = adapter.health()
            except Exception:
                continue
            if ok:
                return True, f"via {adapter.name}: {detail}"
        return False, "no provider in the chain is healthy"

    def list_models(self) -> list[str]:
        for adapter in self._chain():
            try:
                models = adapter.list_models()
            except Exception:
                continue
            if models:
                return models
        return []


@register_adapter
class GemmaCppAdapter(ModelAdapter):
    """gemma.cpp subprocess inference (text completion, no tool calls)."""

    name = "gemma_cpp"
    supports_tools = False
    supports_streaming = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.binary_path = str(
            self.options.get("binary_path", "") or shutil.which("gemma") or "")
        self.model_path = str(self.options.get("model_path", ""))

    def health(self) -> tuple[bool, str]:
        if not self.binary_path or not Path(self.binary_path).exists():
            return False, "gemma.cpp binary not found (set binary_path)"
        if not self.model_path or not Path(self.model_path).exists():
            return False, "gemma model file not found (set model_path)"
        return True, f"gemma.cpp at {self.binary_path}"

    def list_models(self) -> list[str]:
        return [Path(self.model_path).stem] if self.model_path else []

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        ok, detail = self.health()
        if not ok:
            return GenerationResult(
                text="", provider="gemma_cpp", tier=capability_tier,
                error=f"gemma_cpp unavailable: {detail}. Install gemma.cpp "
                      "and point binary_path/model_path at it.",
            )
        prompt = "\n".join(
            f"{m.role}: {m.content}" for m in messages if m.content)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                [self.binary_path, "-m", self.model_path, "-p", prompt],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except Exception as exc:
            return GenerationResult(
                text="", provider="gemma_cpp", tier=capability_tier,
                error=f"gemma_cpp failed to run: {exc}",
            )
        text = (proc.stdout or "").strip()
        if proc.returncode != 0 and not text:
            return GenerationResult(
                text="", provider="gemma_cpp", tier=capability_tier,
                error=f"gemma_cpp exited {proc.returncode}: "
                      f"{(proc.stderr or '')[:300]}",
            )
        return GenerationResult(
            text=text, provider="gemma_cpp",
            model=Path(self.model_path).stem, tier=capability_tier,
            latency_ms=(time.perf_counter() - started) * 1000,
            usage=Usage(prompt_tokens=len(prompt) // 4,
                        completion_tokens=len(text) // 4),
        )


__all__ = ["CloudAdapter", "GemmaCppAdapter", "MultiEngineAdapter"]
