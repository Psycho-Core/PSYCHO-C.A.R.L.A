"""
PSYCHO-C.A.R.L.A. — Scheduler (cron) & Continuous Loops.

Two execution modes beyond on-demand:

  * **Scheduled** — cron-style jobs fired at wall-clock times. Uses a
    `croniter`-compatible expression parser implemented here so there is no
    hard dependency (falls back to croniter if installed).
  * **Continuous** — agents that run on an interval, keeping state and
    compressing their observations between cycles.

Both run on background threads inside the runtime.

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------- #
# Cron expression parsing
# --------------------------------------------------------------------------- #
_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]  # min hour dom mon dow
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOWS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _parse_field(spec: str, index: int) -> set[int]:
    """Expand one cron field into the set of matching values."""
    lo, hi = _FIELD_RANGES[index]
    values: set[int] = set()

    for part in spec.split(","):
        part = part.strip().lower()
        if not part:
            continue

        step = 1
        if "/" in part:
            part, _, step_str = part.partition("/")
            try:
                step = max(1, int(step_str))
            except ValueError:
                step = 1

        if part in ("*", "?"):
            start, end = lo, hi
        elif "-" in part.lstrip("-"):
            start_s, _, end_s = part.partition("-")
            start = _to_int(start_s, lo, hi, index)
            end = _to_int(end_s, lo, hi, index)
        else:
            start = end = _to_int(part, lo, hi, index)

        if start > end:
            values.update(range(start, hi + 1, step))
            values.update(range(lo, end + 1, step))
        else:
            values.update(range(start, end + 1, step))

    if index == 4:
        # 7 == Sunday
        if 7 in values:
            values.discard(7)
            values.add(0)
    return {v for v in values if lo <= v <= hi}


def _to_int(token: str, lo: int, hi: int, index: int) -> int:
    token = token.strip().lower()
    if index == 3 and token in _MONTHS:
        return _MONTHS[token]
    if index == 4 and token in _DOWS:
        return _DOWS[token]
    try:
        return int(token)
    except ValueError:
        return lo


class CronSchedule:
    """A parsed 5-field cron expression."""

    def __init__(self, expression: str) -> None:
        self.expression = expression.strip()
        fields = self.expression.split()
        if len(fields) != 5:
            raise ValueError(
                f"Invalid cron expression '{expression}'. Expected 5 fields "
                "(minute hour day-of-month month day-of-week)."
            )
        self.minutes = _parse_field(fields[0], 0)
        self.hours = _parse_field(fields[1], 1)
        self.days = _parse_field(fields[2], 2)
        self.months = _parse_field(fields[3], 3)
        self.weekdays = _parse_field(fields[4], 4)
        self.dom_restricted = fields[2].strip() not in ("*", "?")
        self.dow_restricted = fields[4].strip() not in ("*", "?")
        self._last_fire: datetime | None = None

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes:
            return False
        if moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False

        dow_ok = moment.weekday() in self.weekdays
        dom_ok = moment.day in self.days
        if self.dom_restricted and self.dow_restricted:
            return dow_ok or dom_ok
        if self.dom_restricted:
            return dom_ok
        if self.dow_restricted:
            return dow_ok
        return True

    def next_after(self, after: datetime | None = None) -> datetime | None:
        """Find the next fire time strictly after `after`."""
        moment = (after or datetime.now()).replace(second=0, microsecond=0)
        if moment <= (after or datetime.now()):
            moment = moment + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):  # ~1 year of minutes
            if self.matches(moment):
                return moment
            moment += timedelta(minutes=1)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "minutes": sorted(self.minutes),
            "hours": sorted(self.hours),
            "days": sorted(self.days),
            "months": sorted(self.months),
            "weekdays": sorted(self.weekdays),
        }

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        return cls(expression)


# --------------------------------------------------------------------------- #
# Job model
# --------------------------------------------------------------------------- #
@dataclass
class ScheduledJob:
    """One scheduled job."""

    id: str
    name: str
    schedule: str            # cron expression
    agent: str = "morning_digest"
    prompt: str = ""
    tier: str = ""
    enabled: bool = True
    speak: bool = True
    last_run: float = 0.0
    last_status: str = ""
    last_error: str = ""
    run_count: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "schedule": self.schedule,
            "agent": self.agent,
            "prompt": self.prompt,
            "tier": self.tier,
            "enabled": self.enabled,
            "speak": self.speak,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #
class Scheduler:
    """Owns scheduled jobs and fires them on a background thread."""

    def __init__(
        self,
        runner: Callable[[ScheduledJob], Any] | None = None,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._jobs: dict[str, ScheduledJob] = {}
        self._lock = threading.RLock()
        self._runner = runner
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tick = 20  # seconds between scheduler ticks
        self._counter = 0

    # -- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="carla-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        last_minute = ""
        while not self._stop.wait(5.0):
            now = datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            if minute_key == last_minute:
                continue
            last_minute = minute_key
            try:
                self.tick(now)
            except Exception:
                continue

    # -- jobs -------------------------------------------------------------- #
    def add(
        self,
        name: str,
        schedule: str,
        agent: str = "morning_digest",
        prompt: str = "",
        tier: str = "",
        enabled: bool = True,
        speak: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ScheduledJob:
        """Create and register a scheduled job. Validates the cron expression."""
        CronSchedule.parse(schedule)  # raises on invalid expression
        with self._lock:
            self._counter += 1
            job = ScheduledJob(
                id=f"job_{int(time.time())}_{self._counter}",
                name=name,
                schedule=schedule,
                agent=agent,
                prompt=prompt,
                tier=tier,
                enabled=enabled,
                speak=speak,
                metadata=metadata or {},
            )
            self._jobs[job.id] = job
        self._emit("job_added", job.to_dict())
        return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job:
            self._emit("job_removed", job.to_dict())
            return True
        return False

    def get(self, job_id: str) -> ScheduledJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, enabled_only: bool = False) -> list[ScheduledJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        if enabled_only:
            jobs = [j for j in jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.created_at)

    def update(self, job_id: str, **changes: Any) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if "schedule" in changes:
                CronSchedule.parse(str(changes["schedule"]))
            for key, value in changes.items():
                if hasattr(job, key):
                    setattr(job, key, value)
        self._emit("job_updated", job.to_dict())
        return True

    # -- firing ------------------------------------------------------------ #
    def tick(self, now: datetime | None = None) -> list[ScheduledJob]:
        """Fire any jobs whose schedule matches `now`."""
        now = now or datetime.now()
        fired: list[ScheduledJob] = []
        for job in self.list_jobs(enabled_only=True):
            try:
                cron = CronSchedule.parse(job.schedule)
            except ValueError:
                job.last_status = "invalid schedule"
                continue
            if not cron.matches(now):
                continue
            if job.last_run and (time.time() - job.last_run) < 55:
                continue  # de-duplicate within the same minute
            job.last_run = time.time()
            fired.append(job)
            self._run_job(job)
        return fired

    def run_now(self, job_id: str) -> bool:
        job = self.get(job_id)
        if not job:
            return False
        self._run_job(job)
        return True

    def _run_job(self, job: ScheduledJob) -> None:
        self._emit("job_started", job.to_dict())
        if self._runner is None:
            job.last_status = "no runner bound"
            return
        try:
            outcome = self._runner(job)
            job.run_count += 1
            job.last_status = "ok"
            job.last_error = ""
            self._emit("job_completed", {**job.to_dict(), "outcome": outcome})
        except Exception as exc:
            job.last_status = "error"
            job.last_error = f"{type(exc).__name__}: {exc}"
            self._emit("job_failed", {**job.to_dict(), "error": job.last_error,
                                     "traceback": traceback.format_exc(limit=3)})

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self._on_event:
            try:
                self._on_event(event, data)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Continuous loops
# --------------------------------------------------------------------------- #
@dataclass
class LoopState:
    """State carried between continuous-agent cycles."""

    iterations: int = 0
    last_run: float = 0.0
    last_output: str = ""
    observations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iterations": self.iterations,
            "last_run": self.last_run,
            "last_output": self.last_output[:500],
            "observations": self.observations[-20:],
            "errors": self.errors[-10:],
        }


class ContinuousLoop:
    """Runs an agent on a fixed interval, retaining state across cycles."""

    def __init__(
        self,
        agent_name: str,
        prompt: str,
        *,
        interval: float = 300.0,
        tier: str = "",
        runner: Callable[[str, str, LoopState], Any] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        max_observations: int = 100,
    ) -> None:
        self.agent_name = agent_name
        self.prompt = prompt
        self.interval = interval
        self.tier = tier
        self.state = LoopState()
        self._runner = runner
        self._on_event = on_event
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._max_observations = max_observations

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"carla-loop-{self.agent_name}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.cycle()
            except Exception:
                continue

    def cycle(self) -> Any:
        """Run one iteration of the loop."""
        if self._runner is None:
            return None
        try:
            outcome = self._runner(self.agent_name, self.prompt, self.state)
            self.state.iterations += 1
            self.state.last_run = time.time()
            text = ""
            if isinstance(outcome, dict):
                text = str(outcome.get("text", "") or outcome.get("speech", ""))
            elif outcome is not None:
                text = str(outcome)
            self.state.last_output = text
            if text:
                self.state.observations.append(text[:600])
                if len(self.state.observations) > self._max_observations:
                    del self.state.observations[: len(self.state.observations) // 2]
            self._emit("loop_cycle", {"agent": self.agent_name,
                                      "iteration": self.state.iterations,
                                      "output": text[:300]})
            return outcome
        except Exception as exc:
            self.state.errors.append(f"{type(exc).__name__}: {exc}")
            if len(self.state.errors) > 50:
                del self.state.errors[:25]
            self._emit("loop_error", {"agent": self.agent_name,
                                      "error": str(exc)})
            return None

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self._on_event:
            try:
                self._on_event(event, data)
            except Exception:
                pass
