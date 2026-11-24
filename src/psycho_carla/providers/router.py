"""
PSYCHO-C.A.R.L.A. — Model Router (Pillar 1: Intelligence).

The router resolves *capability tier aliases* to concrete provider+model pairs,
applies per-tier sampling overrides, honours fallback chains, and records
telemetry. It is the only component that knows about specific model names —
and even then, only via configuration.

Layer: Pillar 1 (Intelligence).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator, Sequence

logger = logging.getLogger("psycho_carla.providers.router")

from ..core.config import CarlaConfig, TierConfig
from ..core.telemetry import RequestTimer, Telemetry
from .base import (
    GenerationResult,
    Message,
    ModelAdapter,
    StreamChunk,
    build_adapter,
    available_adapters,
)


@dataclass
class TierResolution:
    """The concrete routing decision for one tier alias."""

    tier: str
    adapter: ModelAdapter
    provider: str
    model: str
    config: TierConfig

    @property
    def sampling(self) -> dict[str, Any]:
        c = self.config
        return {
            "temperature": c.temperature,
            "max_tokens": c.max_tokens,
            "top_p": c.top_p,
            "top_k": c.top_k,
            "repetition_penalty": c.repetition_penalty,
            "stop_sequences": list(c.stop_sequences),
        }


class ModelRouter:
    """Resolves tier aliases to adapters and performs the generation call."""

    def __init__(
        self,
        config: CarlaConfig,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.config = config
        self.telemetry = telemetry
        self._adapters: dict[str, ModelAdapter] = {}
        self._lock = threading.RLock()
        self._active_tier_override: str = ""

    # -- adapter management ---------------------------------------------- #
    def _adapter_options(self, provider: str, tier: TierConfig) -> dict[str, Any]:
        """Merge global [engine.adapters.*] options with per-tier overrides."""
        options = dict(self.config.engine.adapters.get(provider, {}) or {})
        if tier.base_url:
            options["base_url"] = tier.base_url
        if tier.api_key_env:
            options["api_key"] = os.environ.get(tier.api_key_env, "")
        elif options.get("api_key_env"):
            options["api_key"] = os.environ.get(str(options["api_key_env"]), "")
            options.pop("api_key_env", None)
        options.pop("api_key_env", None)
        if provider == "local_fallback":
            # The built-in responder should address the user by the
            # configured [persona] user_name (default "Psycho").
            options.setdefault("persona", self.config.persona.address_name)
        options.setdefault("timeout", tier.timeout)
        # Tier `extra` wins — it is the most specific source.
        options.update(tier.extra)
        return options

    def get_adapter(self, provider: str, tier: TierConfig) -> ModelAdapter:
        key = f"{provider}:{tier.model}:{tier.base_url}"
        with self._lock:
            ad = self._adapters.get(key)
            if ad is None:
                ad = build_adapter(provider, self._adapter_options(provider, tier))
                self._adapters[key] = ad
            return ad

    # -- tier resolution -------------------------------------------------- #
    def resolve(self, tier_alias: str | None = None) -> TierResolution:
        """Resolve a tier alias to a concrete adapter + model."""
        tiers = self.config.intelligence.tiers
        if not tiers:
            raise RuntimeError(
                "No tiers configured. Define [tiers.fast_tier] (and friends) "
                "in psycho_carla.toml, or run `carla init`."
            )

        alias = tier_alias or self._active_tier_override or self.config.intelligence.default_tier
        tier = tiers.get(alias)
        if tier is None:
            # Alias unknown -> fall back to default tier, then to any tier.
            tier = tiers.get(self.config.intelligence.default_tier)
            if tier is None:
                tier = next(iter(tiers.values()))
            alias = self.config.intelligence.default_tier

        provider = tier.provider or self.config.engine.default_adapter
        model = tier.model
        if not model:
            model = self.config.intelligence.default_model or ""

        adapter = self.get_adapter(provider, tier)
        return TierResolution(
            tier=alias,
            adapter=adapter,
            provider=provider,
            model=model,
            config=tier,
        )

    # -- tier switching --------------------------------------------------- #
    def set_active_tier(self, tier_alias: str) -> bool:
        if tier_alias not in self.config.intelligence.tiers:
            return False
        self._active_tier_override = tier_alias
        return True

    @property
    def active_tier(self) -> str:
        if self._active_tier_override:
            return self._active_tier_override
        return self.config.intelligence.default_tier

    def list_tiers(self) -> list[dict[str, Any]]:
        out = []
        for name, tier in self.config.intelligence.tiers.items():
            out.append(
                {
                    "alias": name,
                    "provider": tier.provider,
                    "model": tier.model,
                    "description": tier.description,
                    "temperature": tier.temperature,
                    "max_tokens": tier.max_tokens,
                    "active": name == self.active_tier,
                    "base_url": tier.base_url,
                }
            )
        return out

    # -- generation -------------------------------------------------------- #
    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "",
        *,
        agent: str = "orchestrator",
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> GenerationResult:
        """Route a generation request through the tier -> adapter -> provider path."""
        resolution = self.resolve(capability_tier or None)
        call_kwargs = dict(resolution.sampling)
        call_kwargs.update(kwargs)
        call_kwargs["model"] = resolution.model
        if tools:
            call_kwargs["tools"] = tools

        request_id = ModelAdapter.new_id("carla")
        timer = RequestTimer(
            self.telemetry,
            request_id=request_id,
            agent=agent,
            tier=resolution.tier,
            provider=resolution.provider,
            model=resolution.model,
        )

        timer.__enter__()
        try:
            result = resolution.adapter.generate(
                messages,
                capability_tier=resolution.tier,
                stream=stream,
                **call_kwargs,
            )
        except Exception as exc:
            # The provider is not there — no Ollama running, no model
            # downloaded, a server that went away. The console still
            # has to answer, so the built-in responder takes over
            # rather than the whole request failing.
            fallback = self._fallback_result(messages, exc)
            if fallback is None:
                timer.__exit__(*sys.exc_info())
                raise
            logger.warning(
                "Provider %s/%s could not answer (%s) — using the "
                "built-in fallback.", resolution.provider,
                resolution.model, exc,
            )
            result = fallback
            timer.metric.prompt_tokens = result.usage.prompt_tokens
            timer.metric.completion_tokens = result.usage.completion_tokens
            timer.metric.total_tokens = result.usage.total_tokens
            timer.__exit__(None, None, None)
            result.model = result.model or resolution.model
            result.provider = result.provider or resolution.provider
            result.tier = resolution.tier
            return result

        # An adapter that catches its own errors reports them in the
        # result rather than raising — an unreachable Ollama comes back
        # as an empty answer with an error attached. That is still a
        # failure, and the console should still say something.
        if not (result.text or "").strip() and (
            result.error or result.finish_reason == "error"
        ):
            reason = result.error or "the provider returned nothing"
            logger.warning(
                "Provider %s/%s could not answer (%s) — using the "
                "built-in fallback.", resolution.provider,
                resolution.model, reason,
            )
            fallback = self._fallback_result(messages, reason)
            if fallback is not None:
                fallback.model = fallback.model or resolution.model
                fallback.tier = resolution.tier
                result = fallback

        # Token counts must land on the metric *before* the timer finalises it,
        # otherwise throughput is recorded as zero.
        timer.metric.prompt_tokens = result.usage.prompt_tokens
        timer.metric.completion_tokens = result.usage.completion_tokens
        timer.metric.total_tokens = result.usage.total_tokens
        timer.__exit__(None, None, None)

        if not result.model:
            result.model = resolution.model
        result.provider = result.provider or resolution.provider
        result.tier = resolution.tier
        return result

    def _fallback_result(
        self,
        messages: Sequence[Message],
        cause: Exception,
    ) -> GenerationResult | None:
        """Answer with the built-in responder, if it can.

        Returns None when the fallback is unavailable or disabled, so
        the caller re-raises the original error.
        """
        if os.environ.get("PSYCHO_CARLA_NO_FALLBACK", "").strip():
            return None

        try:
            adapter = build_adapter(
                "local_fallback",
                {"persona": self.config.persona.address_name},
            )
        except Exception:
            return None
        if adapter is None:
            return None

        try:
            result = adapter.generate(messages)
        except Exception:
            logger.exception("The built-in fallback failed too")
            return None

        result.raw = dict(result.raw or {})
        result.raw["fallback_reason"] = str(cause)
        return result

    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "",
        *,
        agent: str = "orchestrator",
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        """Stream a generation request. Yields StreamChunk objects."""
        resolution = self.resolve(capability_tier or None)
        call_kwargs = dict(resolution.sampling)
        call_kwargs.update(kwargs)
        call_kwargs["model"] = resolution.model
        if tools:
            call_kwargs["tools"] = tools

        request_id = ModelAdapter.new_id("carla")
        timer = RequestTimer(
            self.telemetry,
            request_id=request_id,
            agent=agent,
            tier=resolution.tier,
            provider=resolution.provider,
            model=resolution.model,
        )
        timer.__enter__()
        try:
            for chunk in resolution.adapter.stream(
                messages, capability_tier=resolution.tier, **call_kwargs
            ):
                if chunk.text:
                    timer.mark_first_token()
                yield chunk
            timer.__exit__(None, None, None)
        except Exception as exc:
            timer.__exit__(type(exc), exc, None)
            yield StreamChunk(done=True, error=f"{type(exc).__name__}: {exc}")

    # -- discovery --------------------------------------------------------- #
    def list_models(self) -> list[dict[str, Any]]:
        """Ask every configured provider what models it exposes."""
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for alias, tier in self.config.intelligence.tiers.items():
            provider = tier.provider or self.config.engine.default_adapter
            try:
                adapter = self.get_adapter(provider, tier)
                models = adapter.list_models()
            except Exception:
                models = []
            for m in models:
                key = (provider, m.get("id", ""))
                if key in seen:
                    continue
                seen.add(key)
                m = dict(m)
                m["tier"] = alias
                out.append(m)
        return out

    def health(self) -> list[dict[str, Any]]:
        """Health-check each distinct provider referenced by a tier."""
        results = []
        seen: set[str] = set()
        for alias, tier in self.config.intelligence.tiers.items():
            provider = tier.provider or self.config.engine.default_adapter
            if provider in seen:
                continue
            seen.add(provider)
            try:
                adapter = self.get_adapter(provider, tier)
                ok, detail = adapter.health()
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            results.append({"provider": provider, "ok": ok, "detail": detail})
        return results
