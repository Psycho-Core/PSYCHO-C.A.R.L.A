"""
Web search tool.

Supports DuckDuckGo (no key required), Brave Search, SearxNG, and Tavily —
selected purely by configuration. No provider is hardcoded as "the" default.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from ..registry import ToolRegistry, get_registry

# Runtime configuration, set by `configure_web_search`.
_CONFIG: dict[str, Any] = {
    "provider": "duckduckgo",
    "api_key": "",
    "base_url": "",
    "max_results": 5,
    "timeout": 15.0,
    "ssrf_protection": True,
}


def configure_web_search(
    provider: str = "duckduckgo",
    api_key: str = "",
    base_url: str = "",
    max_results: int = 5,
    timeout: float = 15.0,
    ssrf_protection: bool = True,
) -> None:
    _CONFIG.update(
        {
            "provider": provider or "duckduckgo",
            "api_key": api_key or "",
            "base_url": base_url or "",
            "max_results": max_results,
            "timeout": timeout,
            "ssrf_protection": ssrf_protection,
        }
    )


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#x27;", "'", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    import httpx

    results: list[dict[str, str]] = []
    with httpx.Client(timeout=_CONFIG["timeout"], follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as client:
        resp = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
        )
        resp.raise_for_status()
        html = resp.text

    blocks = re.findall(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        html, re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>',
        html, re.DOTALL,
    )
    for idx, (href, title) in enumerate(blocks[:max_results]):
        if href.startswith("//duckduckgo.com/l/?uddg="):
            parsed = urllib.parse.urlparse("https:" + href)
            qs = urllib.parse.parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        snippet = _clean(snippets[idx]) if idx < len(snippets) else ""
        results.append({"title": _clean(title), "url": href, "snippet": snippet})
    return results


def _brave(query: str, max_results: int) -> list[dict[str, str]]:
    import httpx

    if not _CONFIG["api_key"]:
        return []
    with httpx.Client(timeout=_CONFIG["timeout"]) as client:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={
                "X-Subscription-Token": _CONFIG["api_key"],
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    out = []
    for item in (data.get("web") or {}).get("results", [])[:max_results]:
        out.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": _clean(item.get("description", "")),
        })
    return out


def _searxng(query: str, max_results: int) -> list[dict[str, str]]:
    import httpx

    base = (_CONFIG["base_url"] or "http://localhost:8888").rstrip("/")
    with httpx.Client(timeout=_CONFIG["timeout"]) as client:
        resp = client.get(
            f"{base}/search",
            params={"q": query, "format": "json", "categories": "general"},
        )
        resp.raise_for_status()
        data = resp.json()
    out = []
    for item in (data.get("results") or [])[:max_results]:
        out.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": _clean(item.get("content", "")),
        })
    return out


def _tavily(query: str, max_results: int) -> list[dict[str, str]]:
    import httpx

    if not _CONFIG["api_key"]:
        return []
    with httpx.Client(timeout=_CONFIG["timeout"]) as client:
        resp = client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": _CONFIG["api_key"],
                "query": query,
                "max_results": max_results,
            },
        )
        resp.raise_for_status()
        data = resp.json()
    out = []
    for item in (data.get("results") or [])[:max_results]:
        out.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": _clean(item.get("content", "")),
        })
    return out


def web_search(query: str, max_results: int = 0) -> str:
    """
    Search the web and return ranked results.

    Args:
        query: The search query.
        max_results: Maximum number of results to return. Zero uses the configured limit.
    """
    if not query or not query.strip():
        return "Error: empty query."
    limit = max_results or int(_CONFIG["max_results"])
    provider = _CONFIG["provider"]

    try:
        if provider == "brave":
            results = _brave(query, limit)
        elif provider == "searxng":
            results = _searxng(query, limit)
        elif provider == "tavily":
            results = _tavily(query, limit)
        elif provider == "none":
            return "Web search is disabled."
        else:
            results = _duckduckgo(query, limit)
    except Exception as exc:
        return f"Error: web search failed ({provider}): {type(exc).__name__}: {exc}"

    if not results:
        return f"No results found for '{query}'."
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


def web_fetch(url: str, max_chars: int = 8000) -> str:
    """
    Fetch a URL and return its readable text content.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters of text to return.
    """
    import httpx

    if _CONFIG.get("ssrf_protection", True):
        from ...security.ssrf import validate_url

        try:
            validate_url(url)
        except Exception as exc:
            return f"Error: URL refused by SSRF protection: {exc}"

    try:
        hooks = {}
        if _CONFIG.get("ssrf_protection", True):
            from ...security.ssrf import request_hook

            hooks = {"request": [request_hook]}        # re-check every redirect hop
        with httpx.Client(
            timeout=_CONFIG["timeout"],
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            event_hooks=hooks,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                text = json.dumps(resp.json(), indent=2, default=str)
            else:
                text = _clean(re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                                     resp.text, flags=re.DOTALL | re.IGNORECASE))
                text = re.sub(r"\s+", " ", text).strip()
    except Exception as exc:
        return f"Error: could not fetch {url}: {type(exc).__name__}: {exc}"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated ...]"
    return text


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()
    reg.register(
        "web_search", web_search,
        "Search the web for current information.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum results."},
        }, "required": ["query"]},
        namespace="web",
    )
    reg.register(
        "web_fetch", web_fetch,
        "Fetch a web page and return its text content.",
        {"type": "object", "properties": {
            "url": {"type": "string", "description": "URL to fetch."},
            "max_chars": {"type": "integer", "description": "Max characters to return."},
        }, "required": ["url"]},
        namespace="web",
    )
    return reg


register()
