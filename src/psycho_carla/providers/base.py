"""
PSYCHO-C.A.R.L.A. — Universal Model Adapter Interface.

This is the contract that keeps the whole framework model-agnostic. An adapter
translates the canonical `Message` list into whatever wire format a given
provider speaks, and translates the response back.

Canonical interface:

    adapter.generate(messages, capability_tier, stream) -> GenerationResult

Where `capability_tier` is an *alias* (fast_tier / deep_tier / vision_tier) that
resolves to concrete routing through the config, never to a hardcoded model name.

Layer: Pillar 1 (Intelligence) + Pillar 2 (Engine).
"""

from __future__ import annotations

import abc
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Iterator, Optional, Protocol, Sequence


# --------------------------------------------------------------------------- #
# Canonical message types
# --------------------------------------------------------------------------- #
@dataclass
class Message:
    """A single canonical chat message."""

    role: str                 # system | user | assistant | tool
    content: str = ""
    name: str = ""            # optional tool/function name
    tool_call_id: str = ""    # for tool results
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens


@dataclass
class GenerationResult:
    """The canonical result of a non-streaming generation."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    tier: str = ""
    latency_ms: float = 0.0
    time_to_first_token_ms: float = 0.0
    finish_reason: str = "stop"
    raw: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            },
            "model": self.model,
            "provider": self.provider,
            "tier": self.tier,
            "latency_ms": round(self.latency_ms, 2),
            "time_to_first_token_ms": round(self.time_to_first_token_ms, 2),
            "finish_reason": self.finish_reason,
            "error": self.error,
        }


@dataclass
class StreamChunk:
    """One chunk of a streaming generation."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False
    usage: Usage | None = None
    error: str = ""
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "done": self.done,
            "usage": (
                {
                    "prompt_tokens": self.usage.prompt_tokens,
                    "completion_tokens": self.usage.completion_tokens,
                    "total_tokens": self.usage.total_tokens,
                }
                if self.usage
                else None
            ),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #
class ModelAdapter(abc.ABC):
    """Abstract base for every provider adapter.

    Subclasses implement `generate` and (optionally) `stream`. Everything else —
    retry, tier routing, telemetry, fallbacks — lives in the Router, not here.
    """

    #: Short provider key, e.g. "ollama", "openai_compat", "llamacpp".
    name: str = "base"

    #: Capabilities this adapter supports.
    supports_tools: bool = False
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_json_mode: bool = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        self.base_url: str = str(self.options.get("base_url", "")).rstrip("/")
        self.api_key: str = str(self.options.get("api_key", ""))
        self.timeout: float = float(self.options.get("timeout", 300.0))
        self.extra: dict[str, Any] = {
            k: v for k, v in self.options.items()
            if k not in ("base_url", "api_key", "timeout")
        }

    # -- required -------------------------------------------------------- #
    @abc.abstractmethod
    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate a completion. `capability_tier` is an alias, not a model name."""

    # -- optional -------------------------------------------------------- #
    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        """Stream a completion. Default implementation wraps `generate`."""
        result = self.generate(messages, capability_tier, stream=False, **kwargs)
        yield StreamChunk(text=result.text, tool_calls=result.tool_calls, done=True,
                          usage=result.usage)

    def list_models(self) -> list[dict[str, Any]]:
        """Return available models for this provider (best effort)."""
        return []

    def health(self) -> tuple[bool, str]:
        """Return (reachable, detail)."""
        return True, "ok"

    # -- helpers --------------------------------------------------------- #
    @staticmethod
    def new_id(prefix: str = "req") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def messages_to_dicts(messages: Sequence[Message]) -> list[dict[str, Any]]:
        return [m.to_dict() for m in messages]

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} base_url={self.base_url!r}>"


# --------------------------------------------------------------------------- #
# Adapter registry
# --------------------------------------------------------------------------- #
_REGISTRY: dict[str, type[ModelAdapter]] = {}


def register_adapter(cls: type[ModelAdapter]) -> type[ModelAdapter]:
    """Class decorator that registers an adapter under its `name`."""
    if not cls.name:
        raise ValueError("Adapter must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def get_adapter_class(name: str) -> type[ModelAdapter] | None:
    return _REGISTRY.get(name.lower())


def available_adapters() -> list[str]:
    """Every registered adapter id.

    Loads the bundled adapters first, so the list is complete even when
    called before a router has been built.
    """
    load_builtin_adapters()
    return sorted(_REGISTRY)


def load_builtin_adapters() -> None:
    """Import all bundled adapter modules so they self-register."""
    from . import adapters  # noqa: F401


def build_adapter(name: str, options: dict[str, Any] | None = None) -> ModelAdapter:
    """Instantiate a registered adapter by name."""
    load_builtin_adapters()
    cls = get_adapter_class(name)
    if cls is None:
        raise KeyError(
            f"Unknown adapter '{name}'. Available: {', '.join(available_adapters())}"
        )
    return cls(options or {})
