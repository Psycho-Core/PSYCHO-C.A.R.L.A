"""
PSYCHO-C.A.R.L.A. — Skills catalog.

Implements the open `agentskills.io` specification: a skill is a folder
containing a ``SKILL.md`` file with YAML frontmatter followed by Markdown
instructions.

    my-skill/
    ├── SKILL.md          # required: metadata + instructions
    ├── scripts/          # optional: executable helpers
    ├── references/       # optional: reference documentation
    └── assets/           # optional: templates and data

Progressive disclosure is the point of the format: the agent holds only
each skill's ``name`` and ``description`` (~100 tokens) in context at
startup, loads the body when a task matches, and opens bundled files only
if the body points at them.

This package provides:

  * :class:`Skill` — one parsed skill
  * :class:`SkillCatalog` — an index of installed skills with search
  * :class:`SkillStore` — install / sync / uninstall on disk
  * Validation against the spec (name rules, description limits,
    folder-name match, frontmatter hygiene)

Layer: Tools & Memory.
"""

from .catalog import (
    Skill,
    SkillCatalog,
    SkillMatch,
    SkillValidationError,
    parse_skill,
    validate_skill,
)
from .bundled import BUNDLED_SKILLS
from .executor import SkillExecutor
from .sources import BundledSource, GitHubSource, IndexSource, ensure_bundled
from .store import SkillStore, SkillSource

__all__ = [
    "BUNDLED_SKILLS",
    "BundledSource",
    "GitHubSource",
    "IndexSource",
    "Skill",
    "SkillCatalog",
    "SkillExecutor",
    "SkillMatch",
    "SkillSource",
    "SkillStore",
    "SkillValidationError",
    "ensure_bundled",
    "parse_skill",
    "validate_skill",
]
