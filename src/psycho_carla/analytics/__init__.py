"""
PSYCHO-C.A.R.L.A. — Analytics (local-first).

Upstream ships a cloud analytics client. This build is local-first: events
and preferences are stored in a JSON file in the data directory, the
anonymous identity is a random id generated once, and nothing is
transmitted anywhere unless ``analytics.host`` *and* ``analytics.key``
are explicitly configured — in which case buffered events are POSTed to
that host and the buffer is cleared only on success.

Layer: supporting (telemetry-adjacent).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..core.config import CarlaConfig


class LocalAnalytics:
    """Event/preference store with an opt-in remote flush."""

    def __init__(self, config: CarlaConfig) -> None:
        self.config = config
        self.enabled = bool(config.analytics.enabled)
        self.host = config.analytics.host.rstrip("/")
        self.key = config.analytics.key
        data_dir = config.data_dir
        self._path = data_dir / "analytics.json"
        self._lock = threading.RLock()
        self._state: dict[str, Any] = self._load()
        if not self._state.get("anon_id"):
            anon_path = config.analytics.anon_id_path
            if anon_path and Path(anon_path).expanduser().exists():
                self._state["anon_id"] = Path(anon_path).expanduser() \
                    .read_text(encoding="utf-8").strip()
            else:
                self._state["anon_id"] = uuid.uuid4().hex
            self._save()

    # -- storage ------------------------------------------------------------- #
    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"anon_id": "", "events": [], "preferences": {}}

    def _save(self) -> None:
        try:
            self._path.write_text(json.dumps(self._state, indent=2),
                                  encoding="utf-8")
        except Exception:
            pass

    # -- identity / preferences ------------------------------------------------ #
    def identity(self) -> dict[str, Any]:
        return {
            "anon_id": self._state.get("anon_id", ""),
            "enabled": self.enabled,
            "choice_recorded": bool(self.config.analytics.choice_recorded),
            "remote_configured": bool(self.host and self.key),
            "buffered_events": len(self._state.get("events", [])),
        }

    def set_preference(self, key: str, value: Any) -> dict[str, Any]:
        with self._lock:
            preferences = self._state.setdefault("preferences", {})
            preferences[str(key)] = value
            self.config.analytics.choice_recorded = True
            self._save()
            return {"key": key, "value": value, "preferences": dict(preferences)}

    def preferences(self) -> dict[str, Any]:
        return dict(self._state.get("preferences", {}))

    # -- events ------------------------------------------------------------------ #
    def record(self, event: str, properties: dict[str, Any] | None = None) -> bool:
        """Buffer one event. Returns False when analytics is disabled."""
        if not self.enabled:
            return False
        with self._lock:
            events = self._state.setdefault("events", [])
            events.append({
                "event": event, "ts": time.time(),
                "anon_id": self._state.get("anon_id", ""),
                "properties": dict(properties or {}),
            })
            # Keep the buffer bounded.
            del events[:-1000]
            self._save()
        if len(self._state.get("events", [])) >= max(
                1, self.config.analytics.flush_at_size):
            self.flush()
        return True

    def flush(self) -> dict[str, Any]:
        """POST buffered events to the configured host, if any."""
        if not (self.host and self.key):
            return {"sent": 0, "reason": "no remote host configured (local-only)"}
        with self._lock:
            events = list(self._state.get("events", []))
        if not events:
            return {"sent": 0, "reason": "buffer empty"}
        import httpx

        try:
            resp = httpx.post(
                f"{self.host}/events",
                json={"events": events},
                headers={"Authorization": f"Bearer {self.key}"},
                timeout=15.0,
            )
            resp.raise_for_status()
        except Exception as exc:
            return {"sent": 0, "reason": f"flush failed: {exc}"}
        with self._lock:
            self._state["events"] = []
            self._save()
        return {"sent": len(events)}


__all__ = ["LocalAnalytics"]
