"""
PSYCHO-C.A.R.L.A. — Bundled examples.

Shipped inside the package so they survive an install:

  * ``examples/workflows/`` — workflow definitions
  * ``examples/skills/``     — a sample skill in the ``agentskills.io`` format

Use :func:`examples_dir` to find them at runtime.
"""

from pathlib import Path


def examples_dir() -> Path:
    """The bundled examples directory."""
    return Path(__file__).parent


def workflows_dir() -> Path:
    """The bundled workflow definitions."""
    return examples_dir() / "workflows"


def skills_dir() -> Path:
    """The bundled sample skills."""
    return examples_dir() / "skills"


__all__ = ["examples_dir", "skills_dir", "workflows_dir"]
