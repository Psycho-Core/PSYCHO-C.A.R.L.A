"""OpenRouter — one key, hundreds of hosted models (free and paid).

OpenRouter speaks the OpenAI API, so this is the OpenAI-compatible adapter
with the right base URL, key lookup (environment variable
``OPENROUTER_API_KEY`` or the encrypted vault entry of the same name) and
the optional attribution headers. ``list_free_models`` reads the public
model list and keeps the ones priced at zero.
"""

from __future__ import annotations

import os
from typing import Any

from ..base import register_adapter
from .openai_compat import OpenAICompatAdapter

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
KEY_NAME = "OPENROUTER_API_KEY"


def stored_key() -> str:
    """The OpenRouter key from the environment, else the vault, else empty."""
    key = os.environ.get(KEY_NAME, "").strip()
    if key:
        return key
    try:
        from ...security.vault import Vault

        return Vault().get(KEY_NAME).strip()
    except Exception:
        return ""


def parse_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce OpenRouter's ``/models`` payload to what the UI needs."""
    out = []
    for item in payload.get("data", []) or []:
        pricing = item.get("pricing") or {}

        def _zero(v: Any) -> bool:
            try:
                return float(v) == 0.0
            except (TypeError, ValueError):
                return False

        free = _zero(pricing.get("prompt")) and _zero(pricing.get("completion"))
        out.append({
            "id": item.get("id", ""), "name": item.get("name", ""),
            "context_length": item.get("context_length", 0), "free": free,
        })
    return sorted(out, key=lambda m: (not m["free"], m["id"]))


@register_adapter
class OpenRouterAdapter(OpenAICompatAdapter):
    name = "openrouter"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        opts = dict(options or {})
        opts.setdefault("base_url", OPENROUTER_BASE)
        if not opts.get("api_key"):
            opts["api_key"] = stored_key()
        super().__init__(opts)
        self.headers.setdefault("X-Title", "PSYCHO-C.A.R.L.A.")

    def health(self) -> tuple[bool, str]:
        if not self.api_key or self.api_key == "not-needed":
            return False, "no OpenRouter API key — add one in the Model Manager"
        return super().health()


def list_openrouter_models(api_key: str = "") -> list[dict[str, Any]]:
    """Public model list (no key needed). Returns ``[]`` if unreachable."""
    import httpx

    try:
        r = httpx.get(f"{OPENROUTER_BASE}/models", timeout=15.0)
        r.raise_for_status()
        return parse_models(r.json())
    except Exception:
        return []
