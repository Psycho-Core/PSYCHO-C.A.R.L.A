"""
Recipe loader — reading composable primitive configurations from TOML.

A recipe file looks like this::

    [recipe]
    name = "general-assistant"
    description = "Balanced general-purpose assistant"
    version = "0.1.0"
    kind = "discrete"

    [intelligence]
    model = "qwen3:8b"

    [engine]
    key = "ollama"

    [agent]
    type = "orchestrator"
    max_turns = 8
    temperature = 0.7
    tools = ["calculator", "web_search"]
    system_prompt = "You are a helpful general-purpose assistant."

    [learning]
    routing = "heuristic"
    agent = "none"

    [eval]
    suites = ["general"]

Layer: Pillar 3 (Agents).
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
class Recipe:
    """A composable primitive configuration loaded from TOML."""

    name: str
    description: str = ""
    version: str = "0.1.0"
    #: ``"discrete"`` (benchmarking) or ``"operator"`` (persistent/scheduled).
    kind: str = "discrete"

    # Intelligence
    model: str | None = None
    quantization: str | None = None
    provider: str | None = None

    # Engine
    engine_key: str | None = None

    # Agent
    agent_type: str | None = None
    max_turns: int | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    system_prompt_path: str | None = None

    # Learning
    routing_policy: str | None = None
    agent_policy: str | None = None

    # Eval (discrete agents)
    eval_suites: list[str] = field(default_factory=list)
    eval_benchmarks: list[str] = field(default_factory=list)
    eval_backend: str | None = None
    eval_max_samples: int | None = None
    eval_judge_model: str | None = None

    # Schedule (operators)
    schedule_type: str | None = None
    schedule_value: str | None = None

    # Channels (operators)
    channels: list[str] = field(default_factory=list)

    # Security
    required_capabilities: list[str] = field(default_factory=list)

    #: The raw TOML, kept for forward compatibility.
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "kind": self.kind,
            "model": self.model,
            "provider": self.provider,
            "quantization": self.quantization,
            "engine_key": self.engine_key,
            "agent_type": self.agent_type,
            "max_turns": self.max_turns,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "tools": list(self.tools),
            "system_prompt": self.system_prompt,
            "routing_policy": self.routing_policy,
            "agent_policy": self.agent_policy,
            "eval_suites": list(self.eval_suites),
            "eval_benchmarks": list(self.eval_benchmarks),
            "schedule_type": self.schedule_type,
            "schedule_value": self.schedule_value,
            "channels": list(self.channels),
            "required_capabilities": list(self.required_capabilities),
        }


def load_recipe(path: str | Path) -> Recipe:
    """Load a recipe from a TOML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Recipe file not found: {p}")
    with open(p, "rb") as fh:
        data = tomllib.load(fh)

    recipe_data = data.get("recipe", {})
    intel = data.get("intelligence", {})
    engine = data.get("engine", {})
    agent = data.get("agent", {})
    learning = data.get("learning", {})
    evaluation = data.get("eval", {})
    schedule = data.get("schedule", {})
    channels = data.get("channels", {})
    security = data.get("security", {})

    channel_list: list[str] = []
    if isinstance(channels, dict):
        channel_list = [str(c) for c in channels.get("enabled", []) or []]
    elif isinstance(channels, list):
        channel_list = [str(c) for c in channels]

    return Recipe(
        name=str(recipe_data.get("name", p.stem)),
        description=str(recipe_data.get("description", "")),
        version=str(recipe_data.get("version", "0.1.0")),
        kind=str(recipe_data.get("kind", "discrete")),
        model=_opt_str(intel.get("model")),
        quantization=_opt_str(intel.get("quantization")),
        provider=_opt_str(intel.get("provider")),
        engine_key=_opt_str(engine.get("key") or engine.get("adapter")),
        agent_type=_opt_str(agent.get("type")),
        max_turns=_opt_int(agent.get("max_turns")),
        temperature=_opt_float(agent.get("temperature")),
        max_tokens=_opt_int(agent.get("max_tokens")),
        tools=[str(t) for t in (agent.get("tools") or [])],
        system_prompt=_opt_str(agent.get("system_prompt")),
        system_prompt_path=_opt_str(agent.get("system_prompt_path")),
        routing_policy=_opt_str(learning.get("routing")),
        agent_policy=_opt_str(learning.get("agent")),
        eval_suites=[str(s) for s in (evaluation.get("suites") or [])],
        eval_benchmarks=[str(b) for b in (evaluation.get("benchmarks") or [])],
        eval_backend=_opt_str(evaluation.get("backend")),
        eval_max_samples=_opt_int(evaluation.get("max_samples")),
        eval_judge_model=_opt_str(evaluation.get("judge_model")),
        schedule_type=_opt_str(schedule.get("type")),
        schedule_value=_opt_str(schedule.get("value") or schedule.get("cron")),
        channels=channel_list,
        required_capabilities=[
            str(c) for c in (security.get("required_capabilities") or [])
        ],
        raw=data,
    )


def builtin_recipes_dir() -> Path:
    """Where the bundled recipes live."""
    return Path(__file__).parent / "data"


def discover_recipes(directory: str | Path | None = None) -> dict[str, Path]:
    """Every recipe file, by name."""
    d = Path(directory) if directory else builtin_recipes_dir()
    out: dict[str, Path] = {}
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.toml")):
        out[entry.stem] = entry
    return out


def resolve_recipe(name: str) -> Recipe | None:
    """Load a recipe by name, or None when it is unknown."""
    path = discover_recipes().get(name)
    if path is None:
        candidate = Path(name)
        if candidate.exists():
            try:
                return load_recipe(candidate)
            except Exception:
                return None
        return None
    try:
        return load_recipe(path)
    except Exception:
        return None


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "Recipe",
    "builtin_recipes_dir",
    "discover_recipes",
    "load_recipe",
    "resolve_recipe",
]
