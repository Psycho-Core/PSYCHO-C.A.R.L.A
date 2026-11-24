"""
Desktop bundle — a standalone HTML export of the HUD.

The bundle inlines every stylesheet and script into one file, so it opens
straight from disk with a double-click and no server running. It is the
offline twin of the live HUD: the same layout, the same palette, but with
the live panels replaced by a clear "not connected" state.

The palette stays in CSS variables, so the exported file can be
re-themed by editing the `:root` block at the top.

Layer: Pillar 5 (Interface).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_TEMPLATE_DIR = Path(__file__).parent.parent / "server" / "web" / "templates"
_STATIC_DIR = Path(__file__).parent.parent / "server" / "web" / "static"

#: The placeholders the export replaces.
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def desktop_bundle_path(destination: str | Path | None = None) -> Path:
    """Where the desktop bundle is written."""
    if destination:
        return Path(destination)
    home = Path.home() / ".psycho_carla"
    return home / "psycho-carla-hud.html"


def _inline_assets(html: str) -> str:
    """Inline every local stylesheet and script reference."""

    def css_sub(match: re.Match[str]) -> str:
        href = match.group(1)
        path = _STATIC_DIR / href.rsplit("/", 1)[-1]
        if not path.exists():
            return match.group(0)
        return f"<style>\n{path.read_text(encoding='utf-8')}\n</style>"

    html = re.sub(
        r'<link[^>]*href="([^"]+)"[^>]*rel="stylesheet"[^>]*>', css_sub, html
    )
    html = re.sub(
        r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>', css_sub, html
    )

    def js_sub(match: re.Match[str]) -> str:
        src = match.group(1)
        path = _STATIC_DIR / src.rsplit("/", 1)[-1]
        if not path.exists():
            return match.group(0)
        return f"<script>\n{path.read_text(encoding='utf-8')}\n</script>"

    html = re.sub(r'<script[^>]*src="([^"]+)"[^>]*></script>', js_sub, html)
    return html


def export_desktop_bundle(
    destination: str | Path | None = None,
    *,
    template: str = "index.html",
) -> Path:
    """Write the standalone HUD bundle."""
    source = _TEMPLATE_DIR / template
    if not source.exists():
        raise FileNotFoundError(f"HUD template not found: {source}")
    html = source.read_text(encoding="utf-8")
    html = _inline_assets(html)
    html = _PLACEHOLDER.sub(lambda m: "", html)

    out = desktop_bundle_path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


__all__ = ["desktop_bundle_path", "export_desktop_bundle"]
