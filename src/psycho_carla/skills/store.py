"""
PSYCHO-C.A.R.L.A. — Skill store: install, sync, uninstall.

Skills live in a local directory (by default ``<home>/skills``). The
store can:

  * **install** a skill from a local folder, a git URL, or an HTTP URL
    that serves a ``SKILL.md``
  * **sync** re-fetch every installed skill from the source it came
    from, so upstream updates land locally
  * **uninstall** remove a skill and its folder
  * **validate** check installed skills against the spec

Everything is local-first: the catalog lives on disk, and the agent
never needs a network round-trip to use a skill that is already
installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import Skill, SkillCatalog, parse_skill, validate_skill

SKILL_FILENAME = "SKILL.md"
STATE_FILENAME = ".skills-state.json"


@dataclass
class SkillSource:
    """Where an installed skill came from, so it can be re-synced."""

    kind: str = "local"  # local | git | url
    location: str = ""
    ref: str = ""
    installed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "location": self.location,
            "ref": self.ref,
            "installed_at": self.installed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillSource":
        return cls(
            kind=str(data.get("kind", "local")),
            location=str(data.get("location", "")),
            ref=str(data.get("ref", "")),
            installed_at=float(data.get("installed_at", time.time())),
        )


class SkillStore:
    """Manages the on-disk skills directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog = SkillCatalog()
        self._sources: dict[str, SkillSource] = {}
        self._load_state()
        self.reload()

    # -- state ----------------------------------------------------------- #
    @property
    def state_path(self) -> Path:
        return self.root / STATE_FILENAME

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for name, raw in (data.get("skills") or {}).items():
            self._sources[name] = SkillSource.from_dict(raw)

    def _save_state(self) -> None:
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "skills": {k: v.to_dict() for k, v in self._sources.items()},
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    # -- discovery -------------------------------------------------------- #
    def reload(self) -> SkillCatalog:
        """Re-scan the skills directory and rebuild the catalog."""
        catalog = SkillCatalog()
        if self.root.is_dir():
            for entry in sorted(self.root.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                skill_file = entry / SKILL_FILENAME
                if not skill_file.is_file():
                    continue
                source = self._sources.get(entry.name)
                skill = parse_skill(
                    skill_file,
                    source=source.location if source else str(entry),
                )
                catalog.add(skill)
        self.catalog = catalog
        return catalog

    # -- install ---------------------------------------------------------- #
    def install(
        self,
        source: str,
        *,
        name: str = "",
        ref: str = "",
        overwrite: bool = False,
    ) -> Skill:
        """Install a skill from a local path, a git URL, or an HTTP URL."""
        kind, location = _classify_source(source)

        if kind == "local":
            src_dir = Path(source).expanduser().resolve()
            skill_file = src_dir / SKILL_FILENAME
            if not skill_file.is_file():
                # Allow pointing directly at a SKILL.md.
                if src_dir.is_file() and src_dir.name == SKILL_FILENAME:
                    skill_file = src_dir
                    src_dir = src_dir.parent
                else:
                    raise FileNotFoundError(f"no {SKILL_FILENAME} in {src_dir}")
            skill = parse_skill(skill_file, source=str(src_dir))
            target_name = name or skill.name or src_dir.name
            self._copy_tree(src_dir, self.root / target_name, overwrite=overwrite)
        elif kind == "git":
            target_name = name or _name_from_git(location)
            self._install_from_git(location, ref, self.root / target_name, overwrite)
            skill = parse_skill(self.root / target_name / SKILL_FILENAME, source=location)
            target_name = name or skill.name or target_name
            if target_name != self.root.name:
                pass
        else:
            target_name = name or _name_from_url(location)
            self._install_from_url(location, self.root / target_name, overwrite)
            skill = parse_skill(self.root / target_name / SKILL_FILENAME, source=location)

        errors = validate_skill(skill, strict=False)
        if errors:
            raise ValueError(
                "Skill does not conform to the agentskills.io spec:\n  - "
                + "\n  - ".join(errors)
            )

        self._sources[skill.name] = SkillSource(
            kind=kind, location=location, ref=ref
        )
        self._save_state()
        self.reload()
        return self.catalog.get(skill.name) or skill

    def _copy_tree(self, src: Path, dst: Path, *, overwrite: bool) -> None:
        if dst.exists():
            if not overwrite:
                raise FileExistsError(
                    f"skill '{dst.name}' is already installed — pass overwrite=True"
                )
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    def _install_from_git(
        self, url: str, ref: str, dst: Path, *, overwrite: bool
    ) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="carla-skill-"))
        try:
            cmd = ["git", "clone", "--depth", "1"]
            if ref:
                cmd += ["--branch", ref]
            cmd += [url, str(tmp / "repo")]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"git clone failed: {(proc.stderr or proc.stdout).strip()}"
                )
            repo = tmp / "repo"
            # The skill may live at the repo root or in a single subfolder.
            if (repo / SKILL_FILENAME).is_file():
                source_dir = repo
            else:
                candidates = [
                    d for d in repo.iterdir() if d.is_dir() and (d / SKILL_FILENAME).is_file()
                ]
                if len(candidates) == 1:
                    source_dir = candidates[0]
                elif len(candidates) > 1:
                    raise RuntimeError(
                        "repo contains multiple skills — install one directly"
                    )
                else:
                    raise RuntimeError(f"no {SKILL_FILENAME} found in {url}")
            if dst.exists():
                if not overwrite:
                    raise FileExistsError(f"'{dst.name}' is already installed")
                shutil.rmtree(dst)
            shutil.copytree(source_dir, dst)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _install_from_url(self, url: str, dst: Path, *, overwrite: bool) -> None:
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"could not fetch {url}: {exc}") from exc
        if "---" not in raw.split("\n", 5)[0:6].__str__():
            # Not obviously frontmatter — still write it, validation will tell.
            pass
        if dst.exists():
            if not overwrite:
                raise FileExistsError(f"'{dst.name}' is already installed")
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        (dst / SKILL_FILENAME).write_text(raw, encoding="utf-8")

    # -- sync -------------------------------------------------------------- #
    def sync(self, name: str = "", *, overwrite: bool = True) -> dict[str, Any]:
        """Re-fetch installed skills from their recorded sources."""
        targets = (
            [name] if name else sorted(self._sources)
        )
        results: dict[str, Any] = {"synced": [], "failed": [], "skipped": []}
        for skill_name in targets:
            source = self._sources.get(skill_name)
            if source is None:
                results["skipped"].append(
                    {"name": skill_name, "reason": "no recorded source"}
                )
                continue
            if source.kind == "local":
                results["skipped"].append(
                    {"name": skill_name, "reason": "local source — nothing to fetch"}
                )
                continue
            try:
                self.install(
                    source.location,
                    name=skill_name,
                    ref=source.ref,
                    overwrite=overwrite,
                )
                results["synced"].append(skill_name)
            except Exception as exc:  # noqa: BLE001
                results["failed"].append({"name": skill_name, "error": str(exc)})
        self.reload()
        return results

    # -- uninstall ---------------------------------------------------------- #
    def uninstall(self, name: str) -> bool:
        skill = self.catalog.get(name)
        if skill is None:
            return False
        root = skill.root
        if root and root.is_dir() and root.parent == self.root:
            shutil.rmtree(root, ignore_errors=True)
        self._sources.pop(name, None)
        self._save_state()
        self.reload()
        return True

    # -- validation --------------------------------------------------------- #
    def validate(self) -> dict[str, Any]:
        """Check every installed skill against the spec."""
        issues: list[dict[str, Any]] = []
        for skill in self.catalog:
            errors = validate_skill(skill, strict=False)
            if errors:
                issues.append({"name": skill.name, "errors": errors})
        return {
            "installed": len(self.catalog),
            "valid": len(self.catalog.valid_skills),
            "invalid": len(self.catalog.invalid_skills),
            "issues": issues,
        }

    # -- discovery / export -------------------------------------------------- #
    def export_catalog(self, path: str | Path) -> Path:
        """Write the catalog as JSON (a portable index, not the skills)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(self.catalog.to_dict(), indent=2), encoding="utf-8"
        )
        return out


def _classify_source(source: str) -> tuple[str, str]:
    """Decide whether a source string is local, git, or http."""
    s = source.strip()
    if s.startswith(("http://", "https://")):
        # A URL ending in .git is a git repository; otherwise it serves
        # a SKILL.md directly.
        if s.rstrip("/").endswith(".git") or "/tree/" in s or "/blob/" in s:
            return "git", s
        return "url", s
    if s.startswith(("git@", "ssh://")) or s.endswith(".git"):
        return "git", s
    return "local", s


def _name_from_git(url: str) -> str:
    stem = url.rstrip("/").split("/")[-1]
    if stem.endswith(".git"):
        stem = stem[:-4]
    return _slug(stem)


def _name_from_url(url: str) -> str:
    stem = url.rstrip("/").split("/")[-1]
    if stem.endswith(".md"):
        stem = stem[:-3]
    return _slug(stem) or "skill"


def _slug(text: str) -> str:
    out = re_slug(text)
    return out[:64].strip("-") or "skill"


def re_slug(text: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")
