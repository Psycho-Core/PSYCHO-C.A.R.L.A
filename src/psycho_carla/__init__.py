"""
PSYCHO-C.A.R.L.A.
==================

Personal AI on personal devices — a from-scratch re-engineering of the
OpenJarvis five-primitive architecture for Windows 11, with a C.A.R.L.A.
persona, CPU-only dual-channel voice, and a holographic command HUD.

Five primitives:

  * **Intelligence** — the model router (``psycho_carla.providers.router``)
  * **Engine**      — universal adapters + OpenAI-compatible API
  * **Agents**      — 8 built-in agents, on-demand / scheduled / continuous
  * **Tools & Memory** — tool registry, SQLite+vector memory, MCP client/server
  * **Learning**    — interaction traces + prompt optimiser

Quick start::

    from psycho_carla import Carla

    carla = Carla()
    result = carla.ask("What's on my plate today?", agent="orchestrator")
    print(result["speech"])   # what C.A.R.L.A. says
    print(result["display"])  # what the HUD shows
    carla.close()
"""

from __future__ import annotations

__version__ = "1.0.0"
__all__ = ["Carla", "CarlaRuntime", "__version__"]


def __getattr__(name: str):
    # Lazy imports keep `import psycho_carla` cheap and dependency-light.
    if name == "CarlaRuntime":
        from .core.runtime import CarlaRuntime

        return CarlaRuntime
    if name == "Carla":
        from .core.sdk import Carla

        return Carla
    raise AttributeError(f"module 'psycho_carla' has no attribute '{name}'")
