"""
Connector registry — the catalogue of available connectors.

A connector is registered by id. Built-ins that need no credentials (RSS
feeds, a local Obsidian vault, Hacker News) register themselves on import;
OAuth-backed ones register their *provider* and are connected at runtime.
"""

from __future__ import annotations

from typing import Any, Callable

from .base import BaseConnector

_REGISTRY: dict[str, BaseConnector] = {}
_FACTORIES: dict[str, Callable[..., BaseConnector]] = {}


def register_connector(
    connector_id: str,
    factory: Callable[..., BaseConnector] | None = None,
    *,
    connector: BaseConnector | None = None,
) -> None:
    """Register a connector under an id."""
    if factory is not None:
        _FACTORIES[connector_id] = factory
    if connector is not None:
        _REGISTRY[connector_id] = connector


def get_connector(connector_id: str, **kwargs: Any) -> BaseConnector | None:
    """Instantiate a connector by id, if it is available."""
    _ensure_builtins()
    if connector_id in _REGISTRY:
        return _REGISTRY[connector_id]
    factory = _FACTORIES.get(connector_id)
    if factory is None:
        return None
    try:
        instance = factory(**kwargs)
    except Exception:
        return None
    _REGISTRY[connector_id] = instance
    return instance


_builtins_loaded = False


def _ensure_builtins() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    try:
        from .builtin import register_builtin_connectors

        register_builtin_connectors()
    except Exception:
        pass


def available_connectors() -> list[str]:
    """Every known connector id."""
    _ensure_builtins()
    return sorted(set(_REGISTRY) | set(_FACTORIES))


def connector_descriptions() -> dict[str, str]:
    """A short description for each connector, for `carla connect list`."""
    out: dict[str, str] = {}
    for cid in available_connectors():
        instance = _REGISTRY.get(cid)
        if instance is not None:
            out[cid] = getattr(instance, "display_name", cid)
        else:
            out[cid] = cid
    return out


class ConnectorRegistry:
    """An object-scoped view of the connector registry.

    The module-level registry is process-global; this wraps it so a
    runtime can hold its own view without disturbing anything else.
    """

    def __init__(self) -> None:
        self._instances: dict[str, BaseConnector] = {}

    def register(
        self,
        connector_id: str,
        factory: Callable[..., BaseConnector] | None = None,
        *,
        connector: BaseConnector | None = None,
    ) -> None:
        register_connector(connector_id, factory, connector=connector)
        if connector is not None:
            self._instances[connector_id] = connector

    def get(self, connector_id: str, **kwargs: Any) -> BaseConnector | None:
        if connector_id in self._instances:
            return self._instances[connector_id]
        instance = get_connector(connector_id, **kwargs)
        if instance is not None:
            self._instances[connector_id] = instance
        return instance

    def names(self) -> list[str]:
        return available_connectors()

    def __len__(self) -> int:
        return len(available_connectors())


__all__ = [
    "ConnectorRegistry",
    "available_connectors",
    "connector_descriptions",
    "get_connector",
    "register_connector",
]
