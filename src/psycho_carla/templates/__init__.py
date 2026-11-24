"""
PSYCHO-C.A.R.L.A. — Agent templates and configuration presets.

Two related ideas:

  * **Agent templates** — a pre-configured agent manifest: its role, its
    tools, its system prompt. Sixteen ship with the framework, mirroring
    upstream's `templates/data/`.
  * **Presets** — a complete starter configuration for a whole use case.
    Upstream ships seven (`morning-digest-mac`, `deep-research`,
    `code-assistant`, `scheduled-monitor`, `chat-simple`, …). These are the
    ones `carla init --preset` installs.

Layer: Pillar 3 (Agents).
"""

from .agent_templates import (
    AgentTemplate,
    available_templates,
    load_template,
    load_template_dir,
)
from .presets import (
    PRESETS,
    available_presets,
    install_preset,
    preset_description,
)

__all__ = [
    "AgentTemplate",
    "PRESETS",
    "available_presets",
    "available_templates",
    "install_preset",
    "load_template",
    "load_template_dir",
    "preset_description",
]
