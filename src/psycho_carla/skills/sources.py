"""
Skill sources — where skills come from.

Three upstream-style source networks, adapted to this build:

* :class:`BundledSource` — the 20 packaged step-skills, installed into
  the skills directory as agentskills.io folders on first use.
* :class:`GitHubSource` — skills published in GitHub repositories
  (folder ``<name>/SKILL.md``), indexed by the repo tree API and
  installed over HTTPS.
* :class:`IndexSource` — a generic index-synced network: an HTTP(S)
  JSON index of ``{"skills": [{"name", "url", ...}]}`` (the Hermes /
  OpenClaw pattern), synced into a local cache and installed on demand.

Layer: Tools & Memory.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .bundled import BUNDLED_SKILLS
from .executor import render_skill_md, render_skill_toml

MARKER_FILENAME = ".psycho-carla-sources.json"


def _fetch_text(url: str) -> str:
    """HTTPS GET helper (module-level so tests can monkeypatch it)."""
    import httpx

    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _fetch_json(url: str) -> Any:
    return json.loads(_fetch_text(url))


def _marker_path(store: Any) -> Path:
    return Path(store.root) / MARKER_FILENAME


def _read_marker(store: Any) -> dict[str, Any]:
    path = _marker_path(store)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_marker(store: Any, marker: dict[str, Any]) -> None:
    marker["updated_at"] = time.time()
    _marker_path(store).write_text(json.dumps(marker, indent=2), encoding="utf-8")


def _materialise(store: Any, entry: dict[str, Any], markdown: str,
                 toml_text: str, source_label: str) -> Path:
    folder = Path(store.root) / entry["name"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(markdown, encoding="utf-8")
    if toml_text:
        (folder / "skill.toml").write_text(toml_text, encoding="utf-8")
    try:
        store._sources[entry["name"]] = store._sources.get(
            entry["name"]) or _source_record(entry["name"], source_label)
        store._sources[entry["name"]].location = source_label
        store._save_state()
    except Exception:
        pass
    return folder


def _source_record(name: str, location: str) -> Any:
    from .store import SkillSource

    return SkillSource(kind="local", location=location)


# --------------------------------------------------------------------------- #
# Bundled library
# --------------------------------------------------------------------------- #

class BundledSource:
    """The packaged 20-skill library, installed as folders on demand."""

    name = "bundled"

    def list(self) -> list[dict[str, Any]]:
        return [
            {"name": entry["name"], "description": entry["description"],
             "version": entry.get("version", ""), "source": self.name}
            for entry in BUNDLED_SKILLS.values()
        ]

    def install_all(self, store: Any, *, overwrite: bool = False) -> list[str]:
        """Copy every bundled skill into the store root; returns names."""
        installed: list[str] = []
        for entry in BUNDLED_SKILLS.values():
            folder = Path(store.root) / entry["name"]
            if folder.exists() and not overwrite:
                installed.append(entry["name"])
                continue
            _materialise(store, entry, render_skill_md(entry),
                         render_skill_toml(entry), f"bundled:{entry['name']}")
            installed.append(entry["name"])
        marker = _read_marker(store)
        marker["bundled_installed"] = sorted(installed)
        _write_marker(store, marker)
        store.reload()
        return sorted(installed)


def ensure_bundled(store: Any, config: Any = None) -> list[str]:
    """First-run hook: materialise the bundled library when enabled."""
    enabled = True
    if config is not None:
        skills_cfg = getattr(config, "skills", None)
        enabled = bool(getattr(skills_cfg, "install_bundled", True))
    if not enabled:
        return []
    marker = _read_marker(store)
    installed = set(marker.get("bundled_installed") or [])
    missing = [name for name in BUNDLED_SKILLS if name not in installed]
    if not missing:
        return sorted(installed)
    return BundledSource().install_all(store)


# --------------------------------------------------------------------------- #
# GitHub source network
# --------------------------------------------------------------------------- #

class GitHubSource:
    """Skills hosted in a GitHub repo as ``<folder>/SKILL.md``."""

    name = "github"

    def __init__(self, repo: str, branch: str = "main", subdir: str = "") -> None:
        self.repo = repo.strip("/")
        self.branch = branch or "main"
        self.subdir = (subdir or "").strip("/")

    def _api(self, suffix: str) -> str:
        return f"https://api.github.com/repos/{self.repo}/{suffix}"

    def index(self) -> list[dict[str, Any]]:
        """List installable skills by walking the repo tree."""
        tree = _fetch_json(self._api(
            f"git/trees/{self.branch}?recursive=1")) or {}
        names: set[str] = set()
        prefix = f"{self.subdir}/" if self.subdir else ""
        for item in tree.get("tree") or []:
            path = item.get("path", "")
            if not path.endswith("/SKILL.md"):
                continue
            folder = path[: -len("/SKILL.md")]
            if prefix and not folder.startswith(prefix):
                continue
            skill_name = folder.rsplit("/", 1)[-1]
            if skill_name:
                names.add(skill_name)
        return [{"name": n, "source": self.name, "repo": self.repo}
                for n in sorted(names)]

    def install(self, store: Any, name: str) -> Path:
        raw_base = ("https://raw.githubusercontent.com/"
                    f"{self.repo}/{self.branch}/")
        folder_path = f"{self.subdir}/{name}" if self.subdir else name
        markdown = _fetch_text(f"{raw_base}{folder_path}/SKILL.md")
        entry = {"name": name, "description": "", "version": "",
                 "source": self.name}
        return _materialise(store, entry, markdown, "",
                            f"github:{self.repo}/{folder_path}")

    def sync(self, store: Any) -> dict[str, Any]:
        """Refresh the source index cache (names + timestamp)."""
        entries = self.index()
        marker = _read_marker(store)
        sources = marker.setdefault("indexes", {})
        sources[f"github:{self.repo}"] = {
            "synced_at": time.time(),
            "skills": [entry["name"] for entry in entries],
        }
        _write_marker(store, marker)
        return {"source": f"github:{self.repo}", "skills": len(entries)}


# --------------------------------------------------------------------------- #
# Generic index source (Hermes / OpenClaw pattern)
# --------------------------------------------------------------------------- #

class IndexSource:
    """A remote JSON index of skill download URLs."""

    name = "index"

    def __init__(self, index_url: str) -> None:
        self.index_url = index_url

    def index(self) -> list[dict[str, Any]]:
        payload = _fetch_json(self.index_url) or {}
        rows = payload.get("skills") if isinstance(payload, dict) else payload
        entries: list[dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("name"):
                entries.append({
                    "name": row["name"],
                    "description": row.get("description", ""),
                    "url": row.get("url", ""),
                    "source": self.name,
                })
        return entries

    def install(self, store: Any, name: str) -> Path:
        for entry in self.index():
            if entry["name"] == name:
                url = entry.get("url") or ""
                if not url:
                    raise ValueError(f"index entry '{name}' has no url")
                markdown = _fetch_text(url)
                return _materialise(store, entry, markdown, "",
                                    f"index:{self.index_url}#{name}")
        raise KeyError(f"no skill named '{name}' in the index")

    def sync(self, store: Any) -> dict[str, Any]:
        entries = self.index()
        marker = _read_marker(store)
        sources = marker.setdefault("indexes", {})
        key = f"index:{hashlib.sha256(self.index_url.encode()).hexdigest()[:12]}"
        sources[key] = {
            "url": self.index_url,
            "synced_at": time.time(),
            "skills": [entry["name"] for entry in entries],
        }
        _write_marker(store, marker)
        return {"source": self.index_url, "skills": len(entries)}


def sync_configured_sources(store: Any, config: Any) -> list[dict[str, Any]]:
    """Sync every source listed in ``[skills].sources`` (URL or owner/repo)."""
    results: list[dict[str, Any]] = []
    skills_cfg = getattr(config, "skills", None)
    for raw in getattr(skills_cfg, "sources", []) or []:
        entry = (raw or "").strip()
        if not entry:
            continue
        try:
            if entry.startswith("http://") or entry.startswith("https://"):
                results.append(IndexSource(entry).sync(store))
            else:
                results.append(GitHubSource(entry).sync(store))
        except Exception as exc:
            results.append({"source": entry, "error": str(exc)})
    return results


__all__ = [
    "BundledSource", "GitHubSource", "IndexSource", "ensure_bundled",
    "sync_configured_sources",
]
