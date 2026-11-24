"""
PSYCHO-C.A.R.L.A. — Python SDK.

The ergonomic front door to the runtime. Mirrors OpenJarvis's `Jarvis()` object.

    from psycho_carla import Carla

    f = Carla()
    f.ask("Summarise my notes")
    f.ask_full("Research X and cite sources", agent="deep_research")
    f.close()
"""

from __future__ import annotations

from typing import Any, Sequence

from ..providers.base import Message
from .config import CarlaConfig, load_config
from .persona import Persona
from .runtime import CarlaRuntime


class Carla:
    """The C.A.R.L.A. SDK facade."""

    def __init__(
        self,
        config: CarlaConfig | None = None,
        *,
        start: bool = True,
        with_voice: bool = True,
    ) -> None:
        self.config = config or load_config()
        self.runtime = CarlaRuntime(self.config, with_voice=with_voice)
        self.persona = Persona(self.config.persona)
        if start:
            self.runtime.start()

    # -- core API ---------------------------------------------------------- #
    def ask(
        self,
        prompt: str,
        *,
        agent: str = "",
        tier: str = "",
        session_id: str = "default",
        history: Sequence[Message] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Ask a question. Returns the full structured result."""
        return self.runtime.ask(
            prompt, agent=agent, tier=tier, session_id=session_id,
            history=history, **kwargs
        )

    def ask_full(
        self,
        prompt: str,
        *,
        agent: str = "",
        tier: str = "",
        session_id: str = "default",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Alias of `ask` — returns everything (text, speech, tool calls, usage)."""
        return self.ask(prompt, agent=agent, tier=tier, session_id=session_id,
                        **kwargs)

    def say(self, prompt: str, **kwargs: Any) -> str:
        """Ask and return only what C.A.R.L.A. would *say*."""
        return self.ask(prompt, **kwargs).get("speech", "")

    def show(self, prompt: str, **kwargs: Any) -> str:
        """Ask and return only what would be displayed on the HUD."""
        return self.ask(prompt, **kwargs).get("display", "")

    def stream(self, prompt: str, **kwargs: Any):
        """Stream a response chunk by chunk."""
        return self.runtime.ask_stream(prompt, **kwargs)

    # -- agents ------------------------------------------------------------ #
    def list_agents(self) -> list[dict[str, Any]]:
        return self.runtime.list_agents()

    def get_agent(self, name: str):
        return self.runtime.get_agent(name)

    # -- tiers -------------------------------------------------------------- #
    def list_tiers(self) -> list[dict[str, Any]]:
        return self.runtime.router.list_tiers()

    def set_tier(self, alias: str) -> bool:
        return self.runtime.router.set_active_tier(alias)

    # -- memory -------------------------------------------------------------- #
    def memory_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self.runtime.memory_search(query, top_k=top_k)

    def memory_store(self, content: str, tags: list[str] | None = None) -> int:
        return self.runtime.memory_store(content, tags=tags)

    def memory_list(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.runtime.memory_list(limit)

    # -- scheduling ----------------------------------------------------------- #
    def add_job(self, name: str, schedule: str, **kwargs: Any):
        return self.runtime.add_job(name, schedule, **kwargs)

    def list_jobs(self) -> list[dict[str, Any]]:
        return self.runtime.list_jobs()

    # -- telemetry ------------------------------------------------------------ #
    def telemetry(self) -> dict[str, Any]:
        return self.runtime.telemetry.summary()

    def status(self) -> dict[str, Any]:
        return self.runtime.status()

    # -- lifecycle ------------------------------------------------------------- #
    def close(self) -> None:
        self.runtime.close()

    def __enter__(self) -> "Carla":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
