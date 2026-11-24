"""
Core layer: configuration, persona, runtime, telemetry.

Imports are lazy to avoid a circular dependency between `core.runtime` and the
`agents` package (agents depend on core.persona / core.config; core.runtime
depends on agents).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CarlaConfig", "get_config", "load_config", "reload_config", "save_config",
    "ChannelPayload", "Persona",
    "CarlaRuntime",
    "Carla",
    "RequestTimer", "Telemetry", "hardware_summary",
]

_LAZY = {
    "CarlaConfig": "psycho_carla.core.config",
    "get_config": "psycho_carla.core.config",
    "load_config": "psycho_carla.core.config",
    "reload_config": "psycho_carla.core.config",
    "save_config": "psycho_carla.core.config",
    "ChannelPayload": "psycho_carla.core.persona",
    "Persona": "psycho_carla.core.persona",
    "CarlaRuntime": "psycho_carla.core.runtime",
    "Carla": "psycho_carla.core.sdk",
    "RequestTimer": "psycho_carla.core.telemetry",
    "Telemetry": "psycho_carla.core.telemetry",
    "hardware_summary": "psycho_carla.core.telemetry",
}


def __getattr__(name: str) -> Any:
    module_path = _LAZY.get(name)
    if module_path is None:
        raise AttributeError(f"module 'psycho_carla.core' has no attribute '{name}'")
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(__all__)
