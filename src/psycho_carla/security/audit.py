"""
Audit log — append-only JSONL with a hash chain.

Each entry records the SHA-256 of the previous entry, so any later edit
or deletion breaks the chain and is detectable with :meth:`verify`.
Enabled by ``security.merkle_audit`` (or an explicit
``security.audit_log_path``). Off by default: it is the operator's call
to keep a tamper-evident trail.

Layer: security.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from .vault import _default_home

_GENESIS = "0" * 64


def _entry_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256(f"{prev_hash}|{payload}".encode("utf-8")).hexdigest()


class AuditLogger:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _default_home() / "audit.jsonl"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last_hash = self._read_last_hash()

    @property
    def path(self) -> Path:
        return self._path

    def _read_last_hash(self) -> str:
        if not self._path.exists():
            return _GENESIS
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if line.strip():
                    return str(json.loads(line).get("hash") or _GENESIS)
        except Exception:
            return _GENESIS
        return _GENESIS

    def record(self, event: str, **details: Any) -> dict[str, Any]:
        """Append one entry and return it."""
        with self._lock:
            entry: dict[str, Any] = {
                "ts": time.time(),
                "event": event,
                "details": details,
                "prev": self._last_hash,
            }
            payload = json.dumps(
                {k: entry[k] for k in ("ts", "event", "details", "prev")},
                sort_keys=True, default=str,
            )
            entry["hash"] = _entry_hash(self._last_hash, payload)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
            self._last_hash = entry["hash"]
            return entry

    def verify(self) -> tuple[bool, int]:
        """(ok, entries checked). Detects edits, deletions, reordering."""
        if not self._path.exists():
            return True, 0
        prev = _GENESIS
        count = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except Exception:
                return False, count
            payload = json.dumps(
                {k: entry.get(k) for k in ("ts", "event", "details", "prev")},
                sort_keys=True, default=str,
            )
            if entry.get("prev") != prev:
                return False, count
            if entry.get("hash") != _entry_hash(prev, payload):
                return False, count
            prev = entry["hash"]
            count += 1
        return True, count

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        return out[-limit:]


__all__ = ["AuditLogger"]
