"""
PSYCHO-C.A.R.L.A. — Skill parsing, validation and catalog.

The `agentskills.io` specification:

  * ``name`` is **required**: 1–64 characters, lowercase letters, digits
    and hyphens only; no leading, trailing or consecutive hyphens; and
    it must match the parent directory name exactly.
  * ``description`` is **required**: 1–1024 characters, and must state
    both what the skill does and when to use it.
  * ``license``, ``compatibility``, ``metadata`` and ``allowed-tools``
    are optional.
  * The body is free-form Markdown.

Angle brackets in the frontmatter are discouraged by the spec: they can
be used to smuggle instructions into a system prompt. We reject them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Spec limits.
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
BODY_MAX_LINES = 500

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillValidationError(ValueError):
    """Raised when a skill violates the agentskills.io specification."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass
class Skill:
    """One skill, parsed from a SKILL.md file."""

    name: str
    description: str
    path: Path | None = None
    body: str = ""
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    version: str = ""
    author: str = ""
    source: str = ""
    installed_at: float = field(default_factory=time.time)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def root(self) -> Path | None:
        """The skill's folder (the parent of SKILL.md)."""
        if self.path is None:
            return None
        return self.path.parent

    def resources(self) -> list[Path]:
        """Bundled scripts / references / assets, relative to the skill root."""
        root = self.root
        if root is None:
            return []
        out: list[Path] = []
        for sub in ("scripts", "references", "assets"):
            folder = root / sub
            if folder.is_dir():
                out.extend(sorted(p for p in folder.rglob("*") if p.is_file()))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path) if self.path else "",
            "license": self.license,
            "compatibility": self.compatibility,
            "metadata": dict(self.metadata),
            "allowed_tools": list(self.allowed_tools),
            "version": self.version,
            "author": self.author,
            "source": self.source,
            "installed_at": self.installed_at,
            "valid": self.valid,
            "errors": list(self.errors),
            "body_lines": len(self.body.splitlines()),
            "resources": len(self.resources()),
        }

    def prompt_block(self) -> str:
        """Render the skill for inclusion in an agent's context."""
        parts = [f"# Skill: {self.name}", "", self.description.strip(), ""]
        if self.body.strip():
            parts.append(self.body.strip())
        return "\n".join(parts)


@dataclass
class SkillMatch:
    """A scored match between a query and a skill."""

    skill: Skill
    score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.skill.name,
            "score": round(self.score, 4),
            "reason": self.reason,
            "description": self.skill.description,
        }


# --------------------------------------------------------------------------- #
# Frontmatter parsing
# --------------------------------------------------------------------------- #
def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a SKILL.md into (frontmatter_text, body)."""
    stripped = text.lstrip("﻿")
    if not stripped.startswith("---"):
        return "", text
    lines = stripped.splitlines()
    # Find the closing delimiter.
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return "", text


def _parse_frontmatter(raw: str) -> dict[str, Any]:
    """Parse the YAML-ish frontmatter.

    Uses PyYAML when available; otherwise falls back to a small parser
    that handles the subset the spec uses (scalars, a `metadata` map, and
    a space-separated `allowed-tools` string).
    """
    raw = raw.strip()
    if not raw:
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return _mini_yaml(raw)


def _mini_yaml(raw: str) -> dict[str, Any]:
    """A tolerant parser for the frontmatter subset the spec defines."""
    out: dict[str, Any] = {}
    current_map: dict[str, str] | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_map is not None:
            key, _, value = line.strip().partition(":")
            current_map[key.strip()] = _scalar(value.strip())
            continue
        current_map = None
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value == "":
            # Either an empty scalar or the header of a nested map.
            current_map = {}
            out[key] = current_map
        elif value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            out[key] = [_scalar(v.strip()) for v in inner.split(",")] if inner else []
        else:
            out[key] = _scalar(value)
    return out


def _scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    low = value.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "none", "~"):
        return ""
    try:
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)
        if "." in value and value.replace(".", "", 1).replace("-", "", 1).isdigit():
            return float(value)
    except ValueError:
        pass
    return value


# --------------------------------------------------------------------------- #
# Parsing and validation
# --------------------------------------------------------------------------- #
def parse_skill(path: str | Path, *, source: str = "") -> Skill:
    """Parse a SKILL.md file into a :class:`Skill`.

    Never raises for content problems — problems are recorded on
    ``skill.errors`` so a catalog can list a broken skill alongside
    working ones.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return Skill(
            name=p.parent.name,
            description="",
            path=p,
            source=source,
            errors=[f"cannot read {p}: {exc}"],
        )

    raw_front, body = _split_frontmatter(text)
    data = _parse_frontmatter(raw_front)

    metadata_raw = data.get("metadata")
    metadata: dict[str, str] = {}
    if isinstance(metadata_raw, dict):
        metadata = {str(k): str(v) for k, v in metadata_raw.items()}
    elif isinstance(metadata_raw, str):
        metadata = {"value": metadata_raw}

    allowed = data.get("allowed-tools", "")
    if isinstance(allowed, list):
        allowed_tools = [str(a) for a in allowed]
    else:
        allowed_tools = [a for a in str(allowed or "").split() if a]

    skill = Skill(
        name=str(data.get("name", "") or "").strip(),
        description=str(data.get("description", "") or "").strip(),
        path=p,
        body=body,
        license=str(data.get("license", "") or "").strip(),
        compatibility=str(data.get("compatibility", "") or "").strip(),
        metadata=metadata,
        allowed_tools=allowed_tools,
        version=str(metadata.get("version", "") or ""),
        author=str(metadata.get("author", "") or ""),
        source=source or str(p.parent),
    )
    skill.errors = validate_skill(skill, strict=False)
    return skill


def validate_skill(skill: Skill, *, strict: bool = True) -> list[str]:
    """Validate a skill against the agentskills.io specification.

    Returns a list of error strings. When ``strict`` is True the function
    raises :class:`SkillValidationError` instead of returning.
    """
    errors: list[str] = []

    name = skill.name
    if not name:
        errors.append("missing required field: name")
    else:
        if len(name) > NAME_MAX:
            errors.append(f"name exceeds {NAME_MAX} characters ({len(name)})")
        if not _NAME_RE.match(name):
            errors.append(
                "name must contain only lowercase letters, digits and single "
                "hyphens (no leading, trailing or consecutive hyphens)"
            )
        if skill.root is not None and skill.root.name != name:
            errors.append(
                f"name '{name}' does not match directory '{skill.root.name}'"
            )

    if not skill.description:
        errors.append("missing required field: description")
    elif len(skill.description) > DESCRIPTION_MAX:
        errors.append(
            f"description exceeds {DESCRIPTION_MAX} characters "
            f"({len(skill.description)})"
        )

    if len(skill.compatibility) > COMPATIBILITY_MAX:
        errors.append(
            f"compatibility exceeds {COMPATIBILITY_MAX} characters "
            f"({len(skill.compatibility)})"
        )

    # Angle brackets in frontmatter can smuggle instructions into a prompt.
    for field_name in ("name", "description", "license", "compatibility"):
        value = getattr(skill, field_name, "") or ""
        if "<" in value or ">" in value:
            errors.append(
                f"{field_name} contains angle brackets — the spec discourages "
                "them because they can inject unintended instructions"
            )

    body_lines = len(skill.body.splitlines())
    if body_lines > BODY_MAX_LINES:
        errors.append(
            f"SKILL.md body is {body_lines} lines; the spec recommends under "
            f"{BODY_MAX_LINES} — move detail into references/"
        )

    if strict and errors:
        raise SkillValidationError(errors)
    return errors


class SkillCatalog:
    """An in-memory index of installed skills."""

    def __init__(self, skills: Iterable[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills or ():
            self.add(skill)

    # -- mutation -------------------------------------------------------- #
    def add(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def remove(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    def clear(self) -> None:
        self._skills.clear()

    # -- access ---------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return str(name) in self._skills

    def __iter__(self):
        return iter(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._skills)

    @property
    def valid_skills(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.valid]

    @property
    def invalid_skills(self) -> list[Skill]:
        return [s for s in self._skills.values() if not s.valid]

    def list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in sorted(self._skills.values(), key=lambda s: s.name)]

    # -- progressive disclosure ------------------------------------------ #
    def metadata_prompt(self) -> str:
        """The always-loaded catalog block: names and descriptions only."""
        if not self._skills:
            return ""
        lines = ["# Available skills", ""]
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            if not skill.valid:
                continue
            lines.append(f"- {skill.name}: {skill.description}")
        return "\n".join(lines)

    def search(self, query: str, limit: int = 10) -> list[SkillMatch]:
        """Rank skills against a free-text query.

        Scoring favours description hits (the trigger text) over name
        hits, and rewards query terms that appear in the description.
        """
        terms = [t for t in re.split(r"[^\w-]+", (query or "").lower()) if len(t) > 1]
        if not terms:
            return []
        matches: list[SkillMatch] = []
        for skill in self._skills.values():
            name = skill.name.lower()
            desc = skill.description.lower()
            score = 0.0
            hits: list[str] = []
            for term in terms:
                if term in name:
                    score += 0.45
                    hits.append(f"name:{term}")
                if term in desc:
                    score += 0.25
                    hits.append(f"description:{term}")
                # Prefix matches catch plurals and verb forms.
                if term in desc.split() or desc.startswith(term):
                    score += 0.05
            if score > 0:
                matches.append(
                    SkillMatch(skill=skill, score=score, reason=", ".join(hits))
                )
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:limit]

    def match(self, query: str, limit: int = 3) -> list[Skill]:
        """The skills most likely relevant to a query."""
        return [m.skill for m in self.search(query, limit=limit)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self._skills),
            "valid": len(self.valid_skills),
            "invalid": len(self.invalid_skills),
            "skills": self.list(),
        }
