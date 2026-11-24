"""
Proactive action queue, calendar reads, retrieval, and chunk scanning.

* ``queue_action`` / ``get_pending_actions`` / ``execute_pending_actions`` —
  the proactive agent's pending-action queue (persisted in the runtime
  data dir, decisions appended to the audit log when enabled).
* ``record_decision`` / ``check_permission`` — decision journal over the
  audit chain plus the autonomy policy check.
* ``calendar_search`` / ``calendar_upcoming`` — read synced calendar data
  from the connector knowledge store.
* ``retrieval`` — hybrid keyword + knowledge-store document retrieval.
* ``scan_chunks`` — chunked text scanning with the security scanners.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..registry import ToolRegistry, get_registry


def _runtime():
    from ...core.state import get_runtime

    return get_runtime()


def _need(name: str) -> str:
    return (f"Error: {name} needs a running runtime; none is active in "
            "this process. Use it via `carla serve`, the HUD, or the CLI.")


# --------------------------------------------------------------------------- #
# Proactive action queue
# --------------------------------------------------------------------------- #

def _queue_path(runtime: Any) -> Path:
    return Path(runtime.config.data_dir) / "action_queue.json"


def _load_queue(runtime: Any) -> list[dict[str, Any]]:
    path = _queue_path(runtime)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_queue(runtime: Any, queue: list[dict[str, Any]]) -> None:
    path = _queue_path(runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue[-500:], indent=2), encoding="utf-8")


def queue_action(action: str, reason: str = "", priority: str = "normal") -> str:
    """Queue a proactive action for later approval or execution."""
    runtime = _runtime()
    if runtime is None:
        return _need("queue_action")
    queue = _load_queue(runtime)
    entry = {
        "id": (queue[-1]["id"] + 1) if queue else 1,
        "action": action,
        "reason": reason,
        "priority": priority or "normal",
        "status": "pending",
        "created_at": time.time(),
    }
    queue.append(entry)
    _save_queue(runtime, queue)
    return f"Queued action #{entry['id']} ({entry['priority']}): {action[:80]}"


def get_pending_actions() -> str:
    """List pending proactive actions."""
    runtime = _runtime()
    if runtime is None:
        return _need("get_pending_actions")
    pending = [a for a in _load_queue(runtime) if a.get("status") == "pending"]
    if not pending:
        return "No pending actions."
    return "\n".join(
        f"#{a['id']} [{a.get('priority', 'normal')}] {a['action']}"
        f" — {a.get('reason', '')}" for a in pending)


def execute_pending_actions(max_execute: int = 10) -> str:
    """Execute pending queue entries (runs each through the operative agent)."""
    runtime = _runtime()
    if runtime is None:
        return _need("execute_pending_actions")
    queue = _load_queue(runtime)
    done = 0
    lines: list[str] = []
    for entry in queue:
        if entry.get("status") != "pending":
            continue
        if done >= int(max_execute):
            break
        instance = runtime.get_agent("operative")
        if instance is None:
            entry["status"] = "error"
            lines.append(f"#{entry['id']}: no operative agent available")
            continue
        try:
            result = instance.run(entry["action"])
            entry["status"] = "done"
            entry["result"] = (getattr(result, "text", "") or "")[:400]
        except Exception as exc:
            entry["status"] = "error"
            entry["result"] = str(exc)[:200]
        lines.append(f"#{entry['id']} [{entry['status']}] {entry['action'][:60]}")
        done += 1
    _save_queue(runtime, queue)
    return "\n".join(lines) if lines else "Nothing pending to execute."


# --------------------------------------------------------------------------- #
# Decision journal + permission policy
# --------------------------------------------------------------------------- #

def record_decision(decision: str, context: str = "") -> str:
    """Record an agent decision in the audit journal."""
    runtime = _runtime()
    if runtime is None:
        return _need("record_decision")
    audit = getattr(runtime, "audit", None)
    if audit is not None and hasattr(audit, "record"):
        audit.record("decision", decision=decision, context=context)
        return "Decision recorded in the audit journal."
    queue = _load_queue(runtime)
    queue.append({
        "id": (queue[-1]["id"] + 1) if queue else 1,
        "action": f"DECISION: {decision}", "reason": context,
        "priority": "info", "status": "recorded", "created_at": time.time(),
    })
    _save_queue(runtime, queue)
    return "Decision recorded."


def check_permission(action: str) -> str:
    """Check whether an action is permitted under the autonomy policy."""
    runtime = _runtime()
    if runtime is None:
        return _need("check_permission")
    security = runtime.config.security
    if getattr(security, "default_deny", False):
        low = (action or "").lower()
        blocked = ("delete", "rm ", "format", "shutdown", "send")
        if any(token in low for token in blocked):
            return (f"Denied: '{action}' matches a default-deny pattern. "
                    "Queue it for approval instead.")
    level = getattr(runtime.config, "autonomy_level", "balanced") \
        if hasattr(runtime.config, "autonomy_level") else "balanced"
    return (f"Permitted under autonomy={level} (no default-deny match). "
            "Dangerous tools still require approval where flagged.")


# --------------------------------------------------------------------------- #
# llm (upstream alias of llm_generate)
# --------------------------------------------------------------------------- #

def llm(prompt: str) -> str:
    """One-shot LLM generation (upstream alias of llm_generate)."""
    from .runtime_tools import llm_generate

    return llm_generate(prompt)


# --------------------------------------------------------------------------- #
# Retrieval + chunk scanning
# --------------------------------------------------------------------------- #

def retrieval(query: str, top_k: int = 5) -> str:
    """Hybrid retrieval: knowledge-store documents + long-term memory."""
    runtime = _runtime()
    if runtime is None:
        return _need("retrieval")
    lines: list[str] = []
    try:
        from .runtime_tools import knowledge_search

        hits = knowledge_search(query, top_k=int(top_k))
        if hits and not hits.startswith("No knowledge"):
            lines.append("Knowledge base:\n" + hits)
    except Exception:
        pass
    memory_hits = runtime.memory.context_for(query, top_k=int(top_k))
    if memory_hits:
        lines.append("Memory:\n" + memory_hits)
    return "\n\n".join(lines) if lines else "No retrieval results."


def _iter_chunks(text: str, size: int) -> Iterator[str]:
    for index in range(0, len(text or ""), max(1, size)):
        yield (text or "")[index:index + max(1, size)]


def scan_chunks(path: str = "", text: str = "", chunk_size: int = 0) -> str:
    """Scan text (or a file) in chunks with the security scanners."""
    if path:
        target = Path(path).expanduser()
        if not target.exists():
            return f"Error: file not found: {path}"
        text = target.read_text(encoding="utf-8", errors="replace")
    if not text:
        return "Error: provide text or a file path."
    from ...security.scanners import scan_text

    size = chunk_size or 4096
    findings: dict[str, list[str]] = {}
    for index, chunk in enumerate(_iter_chunks(text, size)):
        result = scan_text(chunk)
        result_findings = getattr(result, "findings", result) or []
        for finding in result_findings:
            if hasattr(finding, "scanner"):
                kind = finding.scanner
                detail = f"{finding.kind} ({finding.severity}): {finding.snippet}"
            elif isinstance(finding, dict):
                kind = str(finding.get("scanner") or finding.get("kind") or "?")
                detail = str(finding)[:120]
            else:
                kind, detail = "finding", str(finding)[:120]
            findings.setdefault(kind, []).append(f"chunk {index}: {detail[:120]}")
    if not findings:
        return "No scanner findings."
    lines = []
    for kind in sorted(findings):
        lines.append(f"{kind}: {len(findings[kind])}")
        lines += [f"  - {item}" for item in findings[kind][:10]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Calendar (connector knowledge store)
# --------------------------------------------------------------------------- #

def _calendar_events(runtime: Any) -> list[dict[str, Any]]:
    knowledge = getattr(runtime, "knowledge", None)
    if knowledge is None:
        return []
    try:
        events = knowledge.search("calendar", top_k=100)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for event in events or []:
        if isinstance(event, dict):
            rows.append(event)
        elif hasattr(event, "to_dict"):
            rows.append(event.to_dict())
        else:
            rows.append({"title": str(event)})
    return rows


def calendar_upcoming(days: int = 7) -> str:
    """Upcoming calendar events from synced connector data."""
    runtime = _runtime()
    if runtime is None:
        return _need("calendar_upcoming")
    events = _calendar_events(runtime)
    if not events:
        return ("No synced calendar data. Run `carla connect sync gcalendar` "
                "(or apple_calendar) first, then ask again.")
    cutoff = time.time() + float(days) * 86400
    upcoming = []
    for event in events:
        starts = event.get("start") or event.get("starts_at") or 0
        try:
            starts = float(starts)
        except (TypeError, ValueError):
            starts = 0
        if starts == 0 or time.time() <= starts <= cutoff:
            upcoming.append(event)
    if not upcoming:
        return f"No events in the next {days} days."
    return "\n".join(
        f"- {e.get('title') or e.get('text', '')[:80]}"
        f" @ {e.get('start', '')}" for e in upcoming[:30])


def calendar_search(query: str, top_k: int = 10) -> str:
    """Search synced calendar events by text."""
    runtime = _runtime()
    if runtime is None:
        return _need("calendar_search")
    events = [e for e in _calendar_events(runtime)
              if query.lower() in json.dumps(e, default=str).lower()]
    if not events:
        return (f"No calendar events match '{query}'. If nothing is synced "
                "yet, run `carla connect sync gcalendar` first.")
    return "\n".join(
        f"- {e.get('title') or str(e.get('text', ''))[:80]}"
        for e in events[: int(top_k)])


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

    reg.register("queue_action", queue_action,
                 "Queue a proactive action for later approval or execution.",
                 _s({"action": {"type": "string"}, "reason": {"type": "string"},
                     "priority": {"type": "string"}}, ["action"]), namespace="agents")
    reg.register("get_pending_actions", get_pending_actions,
                 "List pending proactive actions.", _s({}), namespace="agents")
    reg.register("execute_pending_actions", execute_pending_actions,
                 "Execute pending queue entries through the operative agent.",
                 _s({"max_execute": {"type": "integer"}}), namespace="agents",
                 dangerous=True)
    reg.register("record_decision", record_decision,
                 "Record an agent decision in the audit journal.",
                 _s({"decision": {"type": "string"}, "context": {"type": "string"}},
                    ["decision"]), namespace="ops")
    reg.register("check_permission", check_permission,
                 "Check whether an action is permitted under the autonomy policy.",
                 _s({"action": {"type": "string"}}, ["action"]), namespace="ops")
    reg.register("llm", llm,
                 "One-shot LLM generation (upstream alias of llm_generate).",
                 _s({"prompt": {"type": "string"}}, ["prompt"]), namespace="core")
    reg.register("retrieval", retrieval,
                 "Hybrid retrieval over the knowledge store and memory.",
                 _s({"query": {"type": "string"}, "top_k": {"type": "integer"}},
                    ["query"]), namespace="knowledge")
    reg.register("scan_chunks", scan_chunks,
                 "Scan text (or a file) in chunks with the security scanners.",
                 _s({"path": {"type": "string"}, "text": {"type": "string"},
                     "chunk_size": {"type": "integer"}}), namespace="ops")
    reg.register("calendar_upcoming", calendar_upcoming,
                 "Upcoming calendar events from synced connector data.",
                 _s({"days": {"type": "integer"}}), namespace="calendar")
    reg.register("calendar_search", calendar_search,
                 "Search synced calendar events by text.",
                 _s({"query": {"type": "string"}, "top_k": {"type": "integer"}},
                    ["query"]), namespace="calendar")
    return reg


register()

__all__ = [
    "calendar_search", "calendar_upcoming", "check_permission",
    "execute_pending_actions", "get_pending_actions", "llm", "queue_action",
    "record_decision", "register", "retrieval", "scan_chunks",
]
