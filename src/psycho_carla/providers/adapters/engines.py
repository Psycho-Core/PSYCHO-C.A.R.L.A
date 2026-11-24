"""
Per-engine adapters — the named inference servers, registered as first-class
providers.

Upstream OpenJarvis ships ten engines: vLLM, Ollama, SGLang, llama.cpp, MLX,
LM Studio, exo, Nexa, Uzu, Apple FM, plus the in-process `afm` engine.

Almost all of them speak the OpenAI `/chat/completions` dialect. Rather than
write ten near-identical classes, this module declares them **data-driven** —
each engine is a row of (id, default host, API prefix) — and generates a
subclass of the OpenAI-compatible adapter for each. This is exactly how
upstream does it (`engine/openai_compat_engines.py`).

The practical difference from just using `openai_compat` everywhere is
discoverability: `carla doctor` can probe each engine's default host, and
`carla tiers` can list what is reachable, without the user having to
remember port numbers.

The one genuinely different engine is **afm** (Apple Foundation Models), which
runs *in-process* via the `apple-foundation-models` SDK rather than over
HTTP. It gets its own adapter in `afm.py`.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

from typing import Any

from ..base import register_adapter
from .openai_compat import OpenAICompatAdapter

# id, class name, default host, API prefix
#
# `llamacpp` is deliberately absent: the dedicated adapter in
# `llamacpp.py` talks to the *native* `/completion` endpoint, which allows
# per-request sampler overrides and exposes llama.cpp's detailed timings —
# strictly better than the generic `/chat/completions` surface.
ENGINE_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("vllm", "VLLMAdapter", "http://localhost:8000", "/v1"),
    ("sglang", "SGLangAdapter", "http://localhost:30000", "/v1"),
    ("mlx", "MLXAdapter", "http://localhost:8080", "/v1"),
    ("lmstudio", "LMStudioAdapter", "http://localhost:1234", "/v1"),
    ("exo", "ExoAdapter", "http://localhost:52415", "/v1"),
    ("nexa", "NexaAdapter", "http://localhost:18181", "/v1"),
    ("uzu", "UzuAdapter", "http://localhost:8080", ""),
    ("apple_fm", "AppleFmAdapter", "http://localhost:8079", "/v1"),
    ("lemonade", "LemonadeAdapter", "http://localhost:13305", "/v1"),
    ("nim", "NIMAdapter", "http://localhost:8000", "/v1"),
    ("litellm", "LiteLLMAdapter", "http://localhost:4000", "/v1"),
)

#: Engines that speak the OpenAI dialect — the whole set above.
OPENAI_COMPAT_ENGINES = tuple(spec[0] for spec in ENGINE_SPECS)


def normalize_openai_base_url(url: str) -> str:
    """Strip a single trailing ``/v1`` from a base URL.

    Users habitually pass ``http://host:8000/v1``; the adapter re-appends
    ``/v1`` to every request path, so a trailing copy would double up as
    ``/v1/v1``. Only a literal trailing ``/v1`` is stripped — proxy and
    gateway path prefixes are preserved.
    """
    base = url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _make_engine_adapter(
    engine_id: str, class_name: str, default_host: str, api_prefix: str
) -> type[OpenAICompatAdapter]:
    """Build one engine adapter class from its declared spec."""

    class _EngineAdapter(OpenAICompatAdapter):
        def __init__(self, options: dict[str, Any] | None = None) -> None:
            opts = dict(options or {})
            if not opts.get("base_url"):
                opts["base_url"] = default_host
            opts["api_prefix"] = api_prefix
            super().__init__(opts)

        @property
        def display_name(self) -> str:
            return self.engine_id.replace("_", " ").title()

    # Class-body scoping: names assigned in the body cannot read the
    # enclosing function's locals, so they are set after class creation.
    _EngineAdapter.engine_id = engine_id
    _EngineAdapter.default_host = default_host
    _EngineAdapter.api_prefix = api_prefix
    _EngineAdapter.__name__ = class_name
    _EngineAdapter.__qualname__ = class_name
    _EngineAdapter.name = engine_id
    _EngineAdapter.__doc__ = (
        f"{class_name} — the {engine_id} inference server "
        f"(default {default_host})."
    )
    return register_adapter(_EngineAdapter)


# Build and register every engine adapter.
_GENERATED: dict[str, type[OpenAICompatAdapter]] = {}
for _id, _cls_name, _host, _prefix in ENGINE_SPECS:
    _GENERATED[_id] = _make_engine_adapter(_id, _cls_name, _host, _prefix)

VLLMAdapter = _GENERATED["vllm"]
SGLangAdapter = _GENERATED["sglang"]
MLXAdapter = _GENERATED["mlx"]
LMStudioAdapter = _GENERATED["lmstudio"]
ExoAdapter = _GENERATED["exo"]
NexaAdapter = _GENERATED["nexa"]
UzuAdapter = _GENERATED["uzu"]
AppleFmAdapter = _GENERATED["apple_fm"]
LemonadeAdapter = _GENERATED["lemonade"]
NIMAdapter = _GENERATED["nim"]
LiteLLMAdapter = _GENERATED["litellm"]


#: Default host for each engine, for doctor-style discovery probes.
ENGINE_DEFAULT_HOSTS: dict[str, str] = {
    spec[0]: spec[2] for spec in ENGINE_SPECS
}
# llama.cpp has a dedicated adapter with its own default host.
ENGINE_DEFAULT_HOSTS["llamacpp"] = "http://localhost:8080"

#: The in-process engines (no HTTP host to probe).
IN_PROCESS_ENGINES = ("afm",)


__all__ = [
    "ENGINE_SPECS",
    "ENGINE_DEFAULT_HOSTS",
    "OPENAI_COMPAT_ENGINES",
    "IN_PROCESS_ENGINES",
    "normalize_openai_base_url",
    "VLLMAdapter",
    "SGLangAdapter",
    "MLXAdapter",
    "LMStudioAdapter",
    "ExoAdapter",
    "NexaAdapter",
    "UzuAdapter",
    "AppleFmAdapter",
    "LemonadeAdapter",
    "NIMAdapter",
    "LiteLLMAdapter",
]
