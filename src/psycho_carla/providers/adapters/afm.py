"""
In-process Apple Foundation Models engine (`afm`).

Unlike every other engine, `afm` does not talk to an HTTP server — it drives
the `apple-foundation-models` SDK directly in-process. It is therefore only
available on macOS 26+ with Apple Intelligence enabled, and the SDK is an
optional dependency.

This adapter follows the same contract as every other adapter, so the router
cannot tell the difference. When the SDK is absent, the adapter reports
itself as unavailable with an actionable message rather than raising at
import time — the rest of the stack must keep working on Windows and Linux.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    StreamChunk,
    Usage,
    register_adapter,
)

#: The `use_case` values the SDK accepts.
USE_CASES = ("general", "content_tagging")

#: The `guardrails` values the SDK accepts.
GUARDRAILS = ("default", "permissive_content_transformations")

#: The `sampling` values the SDK accepts.
SAMPLING_MODES = ("greedy", "random")


def afm_available() -> tuple[bool, str]:
    """Is the Apple Foundation Models SDK usable here?"""
    try:
        import apple_foundation_models  # type: ignore  # noqa: F401
    except ImportError:
        return (
            False,
            "apple-foundation-models is not installed "
            "(pip install apple-foundation-models); this engine needs macOS 26+ "
            "with Apple Intelligence enabled",
        )
    except Exception as exc:  # pragma: no cover - platform-specific
        return (False, f"apple-foundation-models failed to import: {exc}")
    return (True, "available")


@register_adapter
class AfmAdapter(ModelAdapter):
    """Drives Apple Foundation Models in-process."""

    name = "afm"
    supports_tools = False
    supports_vision = False
    supports_streaming = False
    supports_json_mode = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.instructions = str(self.extra.get("instructions", "") or "")
        self.use_case = str(self.extra.get("use_case", "general") or "general")
        self.guardrails = str(
            self.extra.get("guardrails", "default") or "default"
        )
        self.sampling = str(self.extra.get("sampling", "greedy") or "greedy")
        self.model = str(self.extra.get("model", "") or "")
        self._session: Any = None

    # -- session ---------------------------------------------------------- #
    def _open_session(self) -> Any:
        """Open (and cache) an AFM session."""
        if self._session is not None:
            return self._session
        ok, detail = afm_available()
        if not ok:
            raise RuntimeError(detail)
        import apple_foundation_models as afm  # type: ignore

        kwargs: dict[str, Any] = {
            "use_case": self.use_case,
            "guardrails": self.guardrails,
            "sampling": self.sampling,
        }
        if self.instructions:
            kwargs["instructions"] = self.instructions
        self._session = afm.SystemLanguageModel(**kwargs)
        return self._session

    # -- generation -------------------------------------------------------- #
    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        model = str(kwargs.pop("model", "") or self.model)
        t0 = time.perf_counter()
        try:
            session = self._open_session()
            prompt = self._compose(messages)
            response = session.respond_to(prompt)
            text = _response_text(response)
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
        usage = _response_usage(response)
        return GenerationResult(
            text=text,
            usage=usage,
            model=model or "afm",
            provider=self.name,
            tier=capability_tier,
            latency_ms=latency,
            time_to_first_token_ms=latency if text else 0.0,
            finish_reason="stop",
            raw=response,
        )

    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        **kwargs: Any,
    ):
        """AFM has no streaming API — yield the full response as one chunk."""
        result = self.generate(messages, capability_tier, stream=False, **kwargs)
        if result.error:
            yield StreamChunk(done=True, error=result.error)
            return
        if result.text:
            yield StreamChunk(text=result.text)
        yield StreamChunk(done=True, usage=result.usage)

    # -- discovery --------------------------------------------------------- #
    def list_models(self) -> list[dict[str, Any]]:
        ok, _ = afm_available()
        if not ok:
            return []
        return [
            {
                "id": "afm",
                "provider": self.name,
                "raw": {
                    "use_case": self.use_case,
                    "guardrails": self.guardrails,
                    "sampling": self.sampling,
                    "in_process": True,
                },
            }
        ]

    def health(self) -> tuple[bool, str]:
        return afm_available()

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _compose(messages: Sequence[Message]) -> str:
        """Flatten the message list into the prompt AFM expects."""
        parts = []
        for message in messages:
            role = message.role
            if role == "system":
                parts.append(f"Instructions:\n{message.content}")
            elif role == "assistant":
                parts.append(f"Assistant: {message.content}")
            else:
                parts.append(message.content)
        return "\n\n".join(p for p in parts if p).strip()


def _response_text(response: Any) -> str:
    """Pull the text out of an AFM response object."""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    for attr in ("text", "content", "output"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    if isinstance(response, dict):
        for key in ("text", "content", "output"):
            val = response.get(key)
            if isinstance(val, str) and val:
                return val
    return str(response)


def _response_usage(response: Any) -> Usage:
    """Pull token counts out of an AFM response, if it reports them."""
    if response is None:
        return Usage()
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        return Usage(
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
        )
    if usage is not None:
        return Usage(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )
    return Usage()


__all__ = ["AfmAdapter", "afm_available", "USE_CASES", "GUARDRAILS", "SAMPLING_MODES"]
