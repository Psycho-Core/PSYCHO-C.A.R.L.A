"""
Configuration presets — complete starter configurations for whole use cases.

A preset is a TOML file that `carla init --preset <name>` installs. Each
one is a working configuration, not a fragment: it names its tiers, its
agents, its cron jobs and its voice settings.

The seven presets mirror upstream's:

  * ``morning-digest-mac``    — a daily briefing, tuned for a Mac
  * ``morning-digest-linux``  — the same, for a Linux box
  * ``morning-digest-minimal``— the same, with the lightest footprint
  * ``deep-research``         — a multi-agent research pipeline
  * ``code-assistant``        — a coding-focused setup
  * ``scheduled-monitor``     — watch services and report
  * ``chat-simple``           — the smallest useful configuration

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class Preset:
    """One installable configuration preset."""

    name: str
    description: str = ""
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path) if self.path else "",
        }


def builtin_presets_dir() -> Path:
    """Where the bundled presets live."""
    return Path(__file__).parent / "presets"


def available_presets() -> list[str]:
    """The names of the bundled presets."""
    directory = builtin_presets_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.toml"))


def load_preset(name: str) -> Preset:
    """Load a preset by name."""
    path = builtin_presets_dir() / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Preset '{name}' not found")
    description = ""
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        description = str(data.get("preset", {}).get("description", ""))
    except Exception:
        pass
    return Preset(name=name, description=description, path=path)


def preset_description(name: str) -> str:
    """A one-line description of a preset."""
    try:
        return load_preset(name).description
    except Exception:
        return ""


def install_preset(
    name: str,
    destination: str | Path,
    *,
    force: bool = False,
) -> Path:
    """Write a preset's TOML to ``destination``."""
    preset = load_preset(name)
    assert preset.path is not None
    out = Path(destination)
    if out.exists() and not force:
        raise FileExistsError(
            f"{out} already exists — pass force=True to overwrite it"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(preset.path.read_text(encoding="utf-8"), encoding="utf-8")
    return out


#: Preset names, for CLI validation and help text.
PRESETS = (
    "morning-digest-mac",
    "morning-digest-linux",
    "morning-digest-minimal",
    "deep-research",
    "code-assistant",
    "scheduled-monitor",
    "chat-simple",
)


__all__ = [
    "PRESETS",
    "Preset",
    "available_presets",
    "builtin_presets_dir",
    "install_preset",
    "load_preset",
    "preset_description",
]
