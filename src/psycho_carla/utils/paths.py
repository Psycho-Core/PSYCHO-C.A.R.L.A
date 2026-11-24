"""
Filesystem locations used across C.A.R.L.A.

Everything the application writes lives under a single home directory so
that an installed program, a source checkout and a frozen executable all
behave identically — and so uninstalling is a matter of deleting one
folder.

The home directory is, in order of precedence:

1. ``$PSYCHO_CARLA_HOME``
2. ``~/.psycho_carla``

On Windows this is normally
``C:\\Users\\<you>\\.psycho_carla``.
"""

from __future__ import annotations

import os
from pathlib import Path


def carla_home() -> Path:
    """The root of C.A.R.L.A.'s writable state.

    Created if it does not exist.
    """
    override = os.environ.get("PSYCHO_CARLA_HOME")
    home = Path(override) if override else (Path.home() / ".psycho_carla")
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return home


def data_dir() -> Path:
    """Databases and other structured state."""
    path = carla_home() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Downloaded models. Never inside the application bundle."""
    path = carla_home() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def voice_samples_dir() -> Path:
    """Recorded voice samples for persona evaluation."""
    path = carla_home() / "voice_samples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """Runtime logs."""
    path = carla_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    """Regenerable caches. Safe to delete at any time."""
    path = carla_home() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    """The main TOML configuration file."""
    return carla_home() / "psycho_carla.toml"


def is_frozen() -> bool:
    """Are we running inside a PyInstaller bundle?"""
    return bool(getattr(__import__("sys"), "frozen", False))
