"""Process-wide runtime state.

A single ``CarlaRuntime`` lives here so tools, agents, and servers share
one instance, plus a small background-task registry for sub-agent work.
"""

from __future__ import annotations

import itertools
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

_runtime: Any = None
_runtime_lock = threading.Lock()


def set_runtime(runtime: Any) -> None:
    """Register the active runtime (called by runtime/server startup)."""
    global _runtime
    with _runtime_lock:
        _runtime = runtime


def get_runtime() -> Any:
    """Return the active runtime, or None outside a live process."""
    return _runtime


def clear_runtime() -> None:
    """Drop the active runtime reference."""
    global _runtime
    with _runtime_lock:
        _runtime = None


class BackgroundTasks:
    """A tiny registry of background jobs (sub-agents, long tools)."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._counter = itertools.count(1)
        self._tasks: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, label: str, fn: Callable[[], Any]) -> int:
        task_id = next(self._counter)
        future = self._executor.submit(fn)
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "label": label,
                "status": "running",
                "result": None,
                "error": "",
                "future": future,
            }
        future.add_done_callback(lambda _f, tid=task_id: self._finish(tid))
        return task_id

    def _finish(self, task_id: int) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return
        future: Future = task["future"]
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                task["status"] = "error"
                task["error"] = str(exc)
            return
        with self._lock:
            task["status"] = "done"
            task["result"] = result

    def get(self, task_id: int) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(t) for t in self._tasks.values()]


background_tasks = BackgroundTasks()


__all__ = [
    "BackgroundTasks",
    "background_tasks",
    "clear_runtime",
    "get_runtime",
    "set_runtime",
]
