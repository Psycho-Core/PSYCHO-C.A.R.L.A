"""
Extended built-in tools.

Adds upstream-parity tools beyond the original files/shell/web/code set:
git operations, patching, SQLite queries, HTTP requests, browser driving,
PDF extraction, weather, FX rates, a persistent REPL, scratchpad thinking,
Docker code execution, and placeholder image generation.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import tempfile
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from ..registry import ToolRegistry, get_registry


def _get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET a JSON document (patchable in tests)."""
    import httpx

    resp = httpx.get(url, params=params or {}, timeout=20.0)
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, dict) else {}


def _check_url(url: str) -> str | None:
    """Return an error string if the URL is disallowed, else None.

    Refuses loopback, private-network and link-local targets (the local C.A.R.L.A.
    server, your router, cloud metadata endpoints) and every scheme except http(s)
    and ``data:``. An agent must not be able to reach its own API through its own
    HTTP tool — that would bypass safe mode.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url or "")
    if parsed.scheme == "data":
        return None
    if parsed.scheme not in ("http", "https"):
        return f"scheme '{parsed.scheme or '(none)'}' is not allowed"
    try:
        from ...security.ssrf import validate_url

        validate_url(url)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return None


def _request(method: str, url: str, headers: dict[str, str] | None = None,
             data: Any = None, params: dict[str, Any] | None = None):
    import httpx

    from ...security.ssrf import request_hook

    # The hook re-validates every redirect hop, not just the first URL.
    with httpx.Client(timeout=20.0, follow_redirects=True,
                      event_hooks={"request": [request_hook]}) as client:
        return client.request(method, url, headers=headers or {},
                              data=data, params=params or {})


# --------------------------------------------------------------------------- #
# git
# --------------------------------------------------------------------------- #


def _git(args: list[str], cwd: str = "") -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Error: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return out.strip() or "(no output)"


def git_status(path: str = "") -> str:
    """Show the working tree status of a git repository."""
    return _git(["status", "--short", "--branch"], path)


def git_diff(path: str = "", staged: bool = False) -> str:
    """Show unstaged (or staged) changes in a git repository."""
    args = ["diff", "--cached"] if staged else ["diff"]
    return _git(args, path)[:12000]


def git_log(path: str = "", limit: int = 10) -> str:
    """Show recent commits in a git repository."""
    return _git(["log", f"-{max(1, int(limit))}", "--oneline", "--decorate"], path)


def git_commit(message: str, path: str = "", add_all: bool = True) -> str:
    """Stage changes and commit them with a message."""
    if add_all:
        _git(["add", "-A"], path)
    return _git(["commit", "-m", message], path)


# --------------------------------------------------------------------------- #
# patch / database / HTTP helpers
# --------------------------------------------------------------------------- #


def _parse_patch(patch: str) -> dict[str, list[tuple[int, list[str]]]]:
    files: dict[str, list[tuple[int, list[str]]]] = {}
    current: str | None = None
    hunks: list[tuple[int, list[str]]] = []
    start = 0
    lines: list[str] = []

    def flush():
        nonlocal lines
        if current is not None and lines:
            hunks.append((start, list(lines)))
        lines = []

    for line in (patch or "").splitlines():
        if line.startswith("+++ b/"):
            flush()
            if current is not None:
                files[current] = hunks
            current = line[6:]
            hunks = []
        elif line.startswith("@@"):
            flush()
            match = re.search(r"-(\d+)", line)
            start = int(match.group(1)) if match else 1
        elif current is not None:
            lines.append(line)
    flush()
    if current is not None:
        files[current] = hunks
    return files


def _find_block(lines: list[str], block: list[str]) -> int:
    if not block:
        return 0
    for i in range(len(lines) - len(block) + 1):
        if lines[i:i + len(block)] == block:
            return i
    return -1


def apply_patch(patch: str, root: str = "") -> str:
    """Apply a unified diff to files under root (default: cwd)."""
    base = Path(root) if root else Path.cwd()
    parsed = _parse_patch(patch)
    if not parsed:
        return "Error: no file hunks found in the patch."
    applied: list[str] = []
    for rel, hunks in parsed.items():
        target = (base / rel).resolve()
        if not str(target).startswith(str(base.resolve())):
            return f"Error: patch path '{rel}' escapes the root."
        lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        offset = 0
        for start, hunk in hunks:
            old_block = [ln[1:] for ln in hunk if ln[:1] in (" ", "-")]
            new_block = [ln[1:] for ln in hunk if ln[:1] in (" ", "+")]
            index = max(0, start - 1 + offset)
            if lines[index:index + len(old_block)] != old_block:
                index = _find_block(lines, old_block)
                if index < 0:
                    return f"Error: context mismatch in {rel} near line {start}; patch not applied."
            lines[index:index + len(old_block)] = new_block
            offset += len(new_block) - len(old_block)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        applied.append(rel)
    return f"Applied patch to: {', '.join(applied)}"


def db_query(database: str, query: str, limit: int = 50) -> str:
    """Run a read-only SELECT/PRAGMA query against a SQLite database."""
    stripped = (query or "").strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|pragma|with)\b", stripped):
        return "Error: only SELECT / WITH / PRAGMA queries are allowed."
    path = Path(database).expanduser()
    if not path.exists():
        return f"Error: database not found: {database}"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cursor = conn.execute(stripped)
            columns = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(max(1, int(limit)))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return f"Error: query failed: {exc}"
    if not rows:
        return "(no rows)"
    lines = [" | ".join(columns)] if columns else []
    lines += [" | ".join("" if v is None else str(v) for v in row) for row in rows]
    return "\n".join(lines)


def http_request(method: str = "GET", url: str = "", headers: str = "", body: str = "") -> str:
    """Make an HTTP request and return status + body (SSRF-checked)."""
    if not url:
        return "Error: http_request needs a url."
    refusal = _check_url(url)
    if refusal:
        return f"Error: URL refused by SSRF protection: {refusal}"
    parsed_headers: dict[str, str] = {}
    if headers:
        try:
            parsed_headers = json.loads(headers)
        except Exception:  # noqa: BLE001
            return "Error: headers must be a JSON object."
    try:
        resp = _request(method.upper(), url, headers=parsed_headers, data=body or None)
    except Exception as exc:  # noqa: BLE001
        return f"Error: request failed: {exc}"
    text = (resp.text or "")[:8000]
    return f"HTTP {resp.status_code}\n{text}"


# --------------------------------------------------------------------------- #
# Browser (HTTP-backed)
# --------------------------------------------------------------------------- #


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.text_parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.buttons: list[str] = []
        self.forms: list[dict[str, Any]] = []
        self._link_href = ""
        self._link_text: list[str] = []
        self._current_form: dict[str, Any] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and attr.get("href"):
            self._link_href = attr["href"]
            self._link_text = []
        elif tag == "button":
            self.buttons.append(attr.get("id") or attr.get("name") or "button")
        elif tag == "form":
            self._current_form = {
                "action": attr.get("action", ""),
                "method": attr.get("method", "get"),
                "fields": [],
            }
            self.forms.append(self._current_form)
        elif tag == "input" and self._current_form is not None:
            if attr.get("name"):
                self._current_form["fields"].append(attr["name"])

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link_href:
            self.links.append((" ".join(self._link_text).strip(), self._link_href))
            self._link_href = ""
        elif tag == "form":
            self._current_form = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        self.text_parts.append(text)
        if self._link_href:
            self._link_text.append(text)


class _Browser:
    def __init__(self) -> None:
        self.url = ""
        self.html = ""
        self.page = _PageParser()

    def navigate(self, url: str) -> str:
        refusal = _check_url(url)
        if refusal:
            return f"Error: URL refused by SSRF protection: {refusal}"
        try:
            resp = _request("GET", url)
        except Exception as exc:  # noqa: BLE001
            return f"Error: navigation failed: {exc}"
        self.url = str(resp.url) if hasattr(resp, "url") else url
        self.html = resp.text or ""
        self.page = _PageParser()
        self.page.feed(self.html)
        return (
            f"Loaded {self.url} — {self.page.title or '(no title)'} "
            f"({len(self.page.links)} links, {len(self.page.forms)} forms)"
        )


_BROWSER = _Browser()


def browser_navigate(url: str) -> str:
    """Navigate the browser session to a URL (SSRF-checked)."""
    return _BROWSER.navigate(url)


def browser_extract(max_chars: int = 8000) -> str:
    """Extract the readable text of the current page."""
    if not _BROWSER.url:
        return "Error: no page loaded — call browser_navigate first."
    return "\n".join(_BROWSER.page.text_parts)[: int(max_chars)]


def browser_click(target: str) -> str:
    """Follow a link by index ('0', '1', …) or by its visible text."""
    if not _BROWSER.url:
        return "Error: no page loaded — call browser_navigate first."
    links = _BROWSER.page.links
    chosen: str | None = None
    if target.isdigit() and int(target) < len(links):
        chosen = links[int(target)][1]
    else:
        for text, href in links:
            if target.lower() in text.lower():
                chosen = href
                break
    if not chosen:
        return f"Error: no link matching '{target}'."
    return _BROWSER.navigate(urljoin(_BROWSER.url, chosen))


def browser_type(field: str, value: str) -> str:
    """Fill a form field on the current page and submit the form."""
    if not _BROWSER.url:
        return "Error: no page loaded — call browser_navigate first."
    for form in _BROWSER.page.forms:
        if field in form["fields"]:
            action = urljoin(_BROWSER.url, form["action"] or _BROWSER.url)
            refusal = _check_url(action)
            if refusal:
                return f"Error: form action refused: {refusal}"
            try:
                resp = _request(form["method"].upper(), action, params={field: value})
            except Exception as exc:  # noqa: BLE001
                return f"Error: form submission failed: {exc}"
            _BROWSER.url = str(resp.url) if hasattr(resp, "url") else action
            _BROWSER.html = resp.text or ""
            _BROWSER.page = _PageParser()
            _BROWSER.page.feed(_BROWSER.html)
            return f"Submitted {field}={value!r} to {action}."
    return f"Error: no form field named '{field}' on this page."


def browser_axtree() -> str:
    """A simplified accessibility tree of the current page."""
    if not _BROWSER.url:
        return "Error: no page loaded — call browser_navigate first."
    page = _BROWSER.page
    lines = [f"page: {page.title or _BROWSER.url}"]
    for text, href in page.links[:50]:
        lines.append(f"  link: {text or href} -> {href}")
    for form in page.forms:
        lines.append(f"  form ({form['method']}) fields: {', '.join(form['fields'])}")
    for button in page.buttons[:20]:
        lines.append(f"  button: {button}")
    return "\n".join(lines)


def browser_screenshot(path: str = "") -> str:
    """Screenshot the current page (needs playwright; honest otherwise)."""
    try:
        import playwright  # type: ignore  # noqa: F401
    except Exception:  # noqa: BLE001
        return (
            "Error: screenshots need the playwright package and a browser build "
            "(pip install playwright && playwright install chromium). "
            "Text extraction is available via browser_extract."
        )
    return "Error: playwright is installed but no browser session is attached."


# --------------------------------------------------------------------------- #
# PDF extraction
# --------------------------------------------------------------------------- #


def _pdf_text(data: bytes) -> str:
    texts: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = match.group(1)
        try:
            content = zlib.decompress(raw)
        except Exception:  # noqa: BLE001
            content = raw
        for string_match in re.finditer(rb"\((?:[^()\\]|\\.)*\)", content):
            chunk = string_match.group(0)[1:-1]
            chunk = chunk.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            texts.append(chunk.decode("utf-8", "replace"))
    return " ".join(texts)


def pdf_extract(path: str, max_chars: int = 12000) -> str:
    """Extract text from a PDF file."""
    target = Path(path).expanduser()
    if not target.exists():
        return f"Error: file not found: {path}"
    text = ""
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(target))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except ImportError:
        text = _pdf_text(target.read_bytes())
    except Exception as exc:  # noqa: BLE001
        return f"Error: could not read PDF: {exc}"
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "No extractable text found in the PDF."
    return text[: int(max_chars)]


# --------------------------------------------------------------------------- #
# Weather + FX
# --------------------------------------------------------------------------- #


def get_weather(location: str = "Adelaide", days: int = 3) -> str:
    """Current forecast for a place, via Open-Meteo (no key needed)."""
    try:
        geo = _get_json(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
        ) or {}
        results = geo.get("results") or []
        if not results:
            return f"Error: unknown location '{location}'."
        place = results[0]
        forecast = _get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": int(days),
                "timezone": "auto",
            },
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return f"Error: weather lookup failed: {exc}"
    daily = forecast.get("daily") or {}
    lines = [f"Weather for {place.get('name', location)}, {place.get('country', '')}:"]
    for i, date in enumerate(daily.get("time") or []):
        lines.append(
            f"  {date}: {daily['temperature_2m_min'][i]}–{daily['temperature_2m_max'][i]} °C, "
            f"precipitation {(daily.get('precipitation_probability_max') or [0] * 9)[i]}%"
        )
    return "\n".join(lines)


def fx_macro_data(base: str = "USD", symbols: str = "EUR,GBP,AUD") -> str:
    """Latest FX rates (frankfurter.app, ECB data, no key needed)."""
    try:
        data = _get_json(
            "https://api.frankfurter.app/latest",
            params={"from": base.upper(), "to": symbols.upper()},
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return f"Error: FX lookup failed: {exc}"
    rates = data.get("rates") or {}
    lines = [f"FX rates (base {data.get('base', base)}, {data.get('date', '')}):"]
    lines += [f"  {code}: {rate}" for code, rate in rates.items()]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# think / repl / Docker execution / image generation
# --------------------------------------------------------------------------- #

_THOUGHTS: list[str] = []
_REPL_NAMESPACE: dict[str, Any] = {}


def think(thought: str) -> str:
    """Record a reasoning step on the scratchpad and return it."""
    entry = (thought or "").strip()
    if not entry:
        return "Error: empty thought."
    _THOUGHTS.append(entry)
    del _THOUGHTS[:-50]
    return f"Noted. ({len(_THOUGHTS)} thoughts on the scratchpad)"


def repl(code: str, reset: bool = False) -> str:
    """Execute Python in a persistent REPL namespace (stateful).

    With reset=True the namespace is cleared first, then the code (if
    any) runs in the fresh namespace.
    """
    prefix = ""
    if reset:
        _REPL_NAMESPACE.clear()
        prefix = "REPL namespace cleared. "
        if not (code or "").strip():
            return prefix.strip()
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            exec(compile(code, "<repl>", "exec"), _REPL_NAMESPACE)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return (prefix + buffer.getvalue().strip()).strip() or "(ok)"


def code_interpreter_docker(code: str, timeout: float = 30.0) -> str:
    """Run Python inside Docker (no network) — needs Docker installed."""
    from ...sandbox import DockerSandbox

    result = DockerSandbox().run_python(code, timeout=timeout)
    if not result.ok and result.error:
        return f"Error: {result.error}"
    return result.output or "(no output)"


def docker_shell_exec(command: str, image: str = "python:3.12-slim", timeout: float = 30.0) -> str:
    """Run a shell command inside a Docker image — needs Docker installed."""
    from ...sandbox import DockerSandbox

    sandbox = DockerSandbox(image=image)
    ok, detail = sandbox.available()
    if not ok:
        return f"Error: Docker sandbox unavailable: {detail}"
    code = (
        "import subprocess, sys\n"
        f"p = subprocess.run({command!r}, shell=True, capture_output=True, text=True)\n"
        "sys.stdout.write(p.stdout); sys.stderr.write(p.stderr)\n"
    )
    result = sandbox.run_python(code, timeout=timeout)
    return result.output or f"Error: {result.error}"


def image_generate(prompt: str, path: str = "") -> str:
    """Generate a procedural placeholder PNG (no image model configured)."""
    import hashlib
    import random
    import struct

    seed = int(hashlib.sha256((prompt or "carla").encode()).hexdigest()[:12], 16)
    rng = random.Random(seed)
    width = height = 256
    base = (rng.randrange(20, 80), rng.randrange(40, 110), rng.randrange(90, 180))
    circles = [
        (
            rng.randrange(width),
            rng.randrange(height),
            rng.randrange(15, 70),
            (rng.randrange(120, 255), rng.randrange(120, 255), rng.randrange(120, 255)),
        )
        for _ in range(14)
    ]
    rows: list[bytes] = []
    for y in range(height):
        row = bytearray(b"\x00")
        for x in range(width):
            r = int(base[0] + (x / width) * 40)
            g = int(base[1] + (y / height) * 40)
            b = base[2]
            for cx, cy, radius, colour in circles:
                if (x - cx) ** 2 + (y - cy) ** 2 < radius * radius:
                    r, g, b = colour
            row += bytes((min(255, r), min(255, g), min(255, b)))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")

    out = (
        Path(path).expanduser()
        if path
        else Path(tempfile.gettempdir()) / f"carla_image_{seed % 99999}.png"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return (
        f"Wrote procedural placeholder image to {out} (seeded by the prompt). "
        "No image model is configured in this build, so this is an abstract placeholder."
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def _s(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()

    reg.register("git_status", git_status, "Show the working tree status of a git repository.", _s({"path": {"type": "string"}}), namespace="git")
    reg.register("git_diff", git_diff, "Show changes in a git repository.", _s({"path": {"type": "string"}, "staged": {"type": "boolean"}}), namespace="git")
    reg.register("git_log", git_log, "Show recent commits in a git repository.", _s({"path": {"type": "string"}, "limit": {"type": "integer"}}), namespace="git")
    reg.register("git_commit", git_commit, "Stage changes and commit them.", _s({"message": {"type": "string"}, "path": {"type": "string"}, "add_all": {"type": "boolean"}}, ["message"]), namespace="git", dangerous=True, requires_approval=True)
    reg.register("apply_patch", apply_patch, "Apply a unified diff to files under a root directory.", _s({"patch": {"type": "string"}, "root": {"type": "string"}}, ["patch"]), namespace="files", dangerous=True, requires_approval=True)
    reg.register("db_query", db_query, "Run a read-only SELECT query against a SQLite database.", _s({"database": {"type": "string"}, "query": {"type": "string"}, "limit": {"type": "integer"}}, ["database", "query"]), namespace="data")
    reg.register("http_request", http_request, "Make an HTTP request (SSRF-checked).", _s({"method": {"type": "string"}, "url": {"type": "string"}, "headers": {"type": "string"}, "body": {"type": "string"}}, ["method", "url"]), namespace="web")
    reg.register("browser_navigate", browser_navigate, "Navigate the browser session to a URL.", _s({"url": {"type": "string"}}, ["url"]), namespace="browser")
    reg.register("browser_extract", browser_extract, "Extract readable text from the current page.", _s({"max_chars": {"type": "integer"}}), namespace="browser")
    reg.register("browser_click", browser_click, "Follow a link by index or visible text.", _s({"target": {"type": "string"}}, ["target"]), namespace="browser")
    reg.register("browser_type", browser_type, "Fill a form field and submit the form.", _s({"field": {"type": "string"}, "value": {"type": "string"}}, ["field", "value"]), namespace="browser")
    reg.register("browser_axtree", browser_axtree, "Simplified accessibility tree of the current page.", _s({}), namespace="browser")
    reg.register("browser_screenshot", browser_screenshot, "Screenshot the current page (needs playwright).", _s({"path": {"type": "string"}}), namespace="browser")
    reg.register("pdf_extract", pdf_extract, "Extract text from a PDF file.", _s({"path": {"type": "string"}, "max_chars": {"type": "integer"}}, ["path"]), namespace="files")
    reg.register("get_weather", get_weather, "Weather forecast for a place (Open-Meteo).", _s({"location": {"type": "string"}, "days": {"type": "integer"}}), namespace="data")
    reg.register("fx_macro_data", fx_macro_data, "Latest FX exchange rates (ECB via frankfurter.app).", _s({"base": {"type": "string"}, "symbols": {"type": "string"}}), namespace="data")
    reg.register("think", think, "Record a reasoning step on the scratchpad.", _s({"thought": {"type": "string"}}, ["thought"]), namespace="core")
    reg.register("repl", repl, "Execute Python in a persistent REPL namespace.", _s({"code": {"type": "string"}, "reset": {"type": "boolean"}}, ["code"]), namespace="code", dangerous=True)
    reg.register("code_interpreter_docker", code_interpreter_docker, "Run Python inside Docker with no network.", _s({"code": {"type": "string"}, "timeout": {"type": "number"}}, ["code"]), namespace="code", dangerous=True, requires_approval=True)
    reg.register("docker_shell_exec", docker_shell_exec, "Run a shell command inside a Docker image.", _s({"command": {"type": "string"}, "image": {"type": "string"}, "timeout": {"type": "number"}}, ["command"]), namespace="code", dangerous=True, requires_approval=True)
    reg.register("image_generate", image_generate, "Generate a procedural placeholder PNG image.", _s({"prompt": {"type": "string"}, "path": {"type": "string"}}, ["prompt"]), namespace="media")

    from .code import code_execute

    reg.register(
        "code_interpreter",
        code_execute,
        "Execute a Python code snippet and return its output.",
        _s({"code": {"type": "string"}, "timeout": {"type": "number"}}, ["code"]),
        namespace="code",
        dangerous=True,
        requires_approval=True,
    )
    return reg


register()

__all__ = [
    "apply_patch",
    "browser_axtree",
    "browser_click",
    "browser_extract",
    "browser_navigate",
    "browser_screenshot",
    "browser_type",
    "code_interpreter_docker",
    "db_query",
    "docker_shell_exec",
    "fx_macro_data",
    "get_weather",
    "git_commit",
    "git_diff",
    "git_log",
    "git_status",
    "http_request",
    "image_generate",
    "pdf_extract",
    "register",
    "repl",
    "think",
]
