"""
Mining provider registry — discovery and selection.

Selection is automatic: given the host's hardware and the configured
engine and model, pick the provider that can actually run. If none can,
the reasons are returned so the UI can explain *why* rather than just
failing.

Layer: Pillar 2 (Model).
"""

from __future__ import annotations

from typing import Any

from .types import MiningCapabilities, MiningProvider

_REGISTRY: dict[str, type[MiningProvider]] = {}


def register_provider(
    provider_id: str, provider_class: type[MiningProvider]
) -> None:
    """Register a provider class under an id."""
    _REGISTRY[provider_id] = provider_class


def available_providers() -> list[str]:
    """Every registered provider id."""
    return sorted(_REGISTRY)


def get_provider(provider_id: str) -> MiningProvider | None:
    """Instantiate a provider by id."""
    provider_class = _REGISTRY.get(provider_id)
    if provider_class is None:
        return None
    try:
        return provider_class()
    except Exception:
        return None


def detect_all(
    hw: Any, engine_id: str, model: str
) -> dict[str, MiningCapabilities]:
    """Run every provider's ``detect()`` against this combo."""
    out: dict[str, MiningCapabilities] = {}
    for provider_id, provider_class in _REGISTRY.items():
        try:
            out[provider_id] = provider_class.detect(hw, engine_id, model)
        except Exception as exc:  # noqa: BLE001
            out[provider_id] = MiningCapabilities(
                supported=False, reason=f"detection failed: {exc}"
            )
    return out


def select_provider(
    hw: Any, engine_id: str, model: str
) -> tuple[str | None, dict[str, MiningCapabilities]]:
    """Choose the best provider for this combo.

    Returns the chosen provider id (or None) and the detection results for
    every provider, so the caller can explain the choice.
    """
    capabilities = detect_all(hw, engine_id, model)
    for provider_id in (
        "apple-mps-pearl",  # the most specific match wins
        "vllm-pearl",
        "cpu-pearl",
    ):
        cap = capabilities.get(provider_id)
        if cap is not None and cap.supported:
            return provider_id, capabilities
    return None, capabilities


__all__ = [
    "available_providers",
    "detect_all",
    "get_provider",
    "register_provider",
    "select_provider",
]
