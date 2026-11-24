"""
PSYCHO-C.A.R.L.A. — Agent Registry.

Holds agent classes and their per-agent configuration overrides, and
instantiates them on demand.

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

from typing import Any

from .base import BaseAgent


class AgentRegistry:
    """Catalogue of every agent the runtime can dispatch to."""

    def __init__(self) -> None:
        self._classes: dict[str, type[BaseAgent]] = {}
        self._overrides: dict[str, dict[str, Any]] = {}

    # -- registration -------------------------------------------------------- #
    def register(self, cls: type[BaseAgent]) -> None:
        self._classes[cls.name] = cls

    def register_all(self, classes: list[type[BaseAgent]]) -> None:
        for cls in classes:
            self.register(cls)

    def unregister(self, name: str) -> None:
        self._classes.pop(name, None)
        self._overrides.pop(name, None)

    # -- configuration ------------------------------------------------------- #
    def set_overrides(self, overrides: dict[str, dict[str, Any]]) -> None:
        """Apply per-agent overrides from the `[agents.*]` config table."""
        for name, data in (overrides or {}).items():
            self._overrides[name] = dict(data)

    def overrides_for(self, name: str) -> dict[str, Any]:
        return dict(self._overrides.get(name, {}))

    # -- introspection -------------------------------------------------------- #
    def names(self) -> list[str]:
        return sorted(self._classes)

    def get_class(self, name: str) -> type[BaseAgent] | None:
        return self._classes.get(name)

    def describe(self) -> list[dict[str, Any]]:
        out = []
        for name, cls in sorted(self._classes.items()):
            ov = self._overrides.get(name, {})
            out.append({
                "name": name,
                "description": ov.get("description", cls.description),
                "mode": ov.get("mode", cls.mode),
                "tier": ov.get("tier", ""),
                "max_turns": ov.get("max_turns", cls.max_turns),
                "tools": ov.get("tools", list(cls.allowed_tools)),
                "schedule": ov.get("schedule", ""),
            })
        return out

    # -- instantiation ---------------------------------------------------------- #
    def create(
        self,
        name: str,
        config: Any,
        router: Any,
        tools: Any,
        memory: Any,
        persona: Any = None,
    ) -> BaseAgent | None:
        """Instantiate an agent by name, applying configured overrides."""
        cls = self._classes.get(name)
        if cls is None:
            return None

        agent = cls(config, router, tools, memory, persona)

        ov = self._overrides.get(name, {})
        if "tier" in ov and ov["tier"]:
            agent.tier = ov["tier"]
        if "max_turns" in ov and ov["max_turns"]:
            agent.max_turns = int(ov["max_turns"])
        if "tools" in ov and ov["tools"] is not None:
            agent.allowed_tools = list(ov["tools"])
        if "allowed_tools" in ov and ov["allowed_tools"] is not None:
            agent.allowed_tools = list(ov["allowed_tools"])
        if "mode" in ov and ov["mode"]:
            agent.mode = ov["mode"]
        if "system_prompt_extra" in ov and ov["system_prompt_extra"]:
            agent.system_prompt_extra = ov["system_prompt_extra"]
        return agent
