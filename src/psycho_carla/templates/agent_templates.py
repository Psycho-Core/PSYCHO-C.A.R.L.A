"""
Agent template loader — pre-configured agent manifests from TOML.

Format:

    [template]
    name = "code-reviewer"
    description = "Reviews code for bugs, style, and best practices"

    [agent]
    type = "orchestrator"
    max_turns = 8
    temperature = 0.3
    tools = ["file_read", "file_search"]
    system_prompt = "You are an expert code reviewer."

The templates ship in this package under ``data/``, so they are available
from an installed wheel as well as from a source checkout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # noqa: F401


@dataclass
class AgentTemplate:
    """A pre-configured agent manifest."""

    name: str
    description: str = ""
    system_prompt: str = ""
    agent_type: str = "orchestrator"
    tools: list[str] = field(default_factory=list)
    max_turns: int = 10
    temperature: float = 0.7
    tier: str = ""
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "agent_type": self.agent_type,
            "tools": list(self.tools),
            "max_turns": self.max_turns,
            "temperature": self.temperature,
            "tier": self.tier,
            "source_path": self.source_path,
        }

    def to_agent_override(self) -> dict[str, Any]:
        """The TOML-shaped override for ``[agent.agents.<name>]``."""
        override: dict[str, Any] = {
            "max_turns": self.max_turns,
            "system_prompt_extra": self.system_prompt,
        }
        if self.tools:
            override["tools"] = list(self.tools)
        if self.tier:
            override["tier"] = self.tier
        return override


def load_template(path: str | Path) -> AgentTemplate:
    """Load an agent template from a TOML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Template file not found: {p}")
    with open(p, "rb") as fh:
        data = tomllib.load(fh)

    template_data = data.get("template", {})
    agent_data = data.get("agent", {})
    return AgentTemplate(
        name=str(template_data.get("name", p.stem)),
        description=str(template_data.get("description", "")),
        system_prompt=str(agent_data.get("system_prompt", "")),
        agent_type=str(agent_data.get("type", "orchestrator")),
        tools=list(agent_data.get("tools", []) or []),
        max_turns=int(agent_data.get("max_turns", 10) or 10),
        temperature=float(agent_data.get("temperature", 0.7) or 0.7),
        tier=str(agent_data.get("tier", "") or ""),
        source_path=str(p),
    )


def builtin_templates_dir() -> Path:
    """Where the bundled templates live."""
    return Path(__file__).parent / "data"


def available_templates() -> list[str]:
    """The names of the bundled templates."""
    directory = builtin_templates_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.toml"))


def load_template_dir(directory: str | Path | None = None) -> dict[str, AgentTemplate]:
    """Load every template in a directory (the bundled one by default)."""
    d = Path(directory) if directory else builtin_templates_dir()
    out: dict[str, AgentTemplate] = {}
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.toml")):
        try:
            template = load_template(entry)
        except Exception:
            continue
        out[template.name] = template
    return out


__all__ = [
    "AgentTemplate",
    "available_templates",
    "builtin_templates_dir",
    "load_template",
    "load_template_dir",
]
