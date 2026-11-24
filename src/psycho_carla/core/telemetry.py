"""
PSYCHO-C.A.R.L.A. — Efficiency Telemetry.

Tracks the three metrics OpenJarvis treats as first-class constraints:
  * latency            (ms, per request)
  * tokens per second  (generation throughput)
  * resource pressure  (VRAM / RAM, sampled in the background)

Everything is local SQLite. Nothing leaves the machine.

Layer: Core.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is optional
    psutil = None  # type: ignore


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class RequestMetric:
    request_id: str
    agent: str
    tier: str
    provider: str
    model: str
    started_at: float
    latency_ms: float = 0.0
    time_to_first_token_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_per_second: float = 0.0
    status: str = "ok"
    error: str = ""
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    cost_usd: float = 0.0
    # Efficiency metrics — energy and FLOPs are first-class constraints in
    # the OpenJarvis research model, not afterthoughts.
    flops: float = 0.0
    energy_joules: float = 0.0
    power_watts: float = 0.0

    def to_row(self) -> tuple:
        return (
            self.request_id,
            self.agent,
            self.tier,
            self.provider,
            self.model,
            self.started_at,
            self.latency_ms,
            self.time_to_first_token_ms,
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.tokens_per_second,
            self.status,
            self.error[:2000],
            self.vram_used_mb,
            self.vram_total_mb,
            self.ram_used_mb,
            self.ram_total_mb,
            self.cost_usd,
            self.flops,
            self.energy_joules,
            self.power_watts,
        )


@dataclass
class ResourceSample:
    ts: float
    cpu_percent: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    ram_percent: float = 0.0
    vram_used_mb: float = 0.0
    vram_total_mb: float = 0.0
    vram_percent: float = 0.0
    power_watts: float = 0.0
    energy_vendor: str = ""

    def to_row(self) -> tuple:
        return (
            self.ts,
            self.cpu_percent,
            self.ram_used_mb,
            self.ram_total_mb,
            self.ram_percent,
            self.vram_used_mb,
            self.vram_total_mb,
            self.vram_percent,
            self.power_watts,
            self.energy_vendor,
        )


# --------------------------------------------------------------------------- #
# Hardware probes
# --------------------------------------------------------------------------- #
def _nvidia_smi() -> Optional[tuple[float, float]]:
    """Return (used_mb, total_mb) for the first NVIDIA GPU, or None."""
    exe = shutil_which("nvidia-smi")
    if not exe:
        return None
    try:
        import subprocess

        out = subprocess.run(
            [
                exe,
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        used, total = out.stdout.strip().splitlines()[0].split(",")
        return float(used.strip()), float(total.strip())
    except Exception:
        return None


def shutil_which(cmd: str) -> Optional[str]:
    import shutil

    return shutil.which(cmd)


def probe_vram() -> tuple[float, float]:
    """Best-effort VRAM probe. Returns (used_mb, total_mb); zeros when unknown."""
    nv = _nvidia_smi()
    if nv:
        return nv
    # AMD via rocm-smi
    rocm = shutil_which("rocm-smi")
    if rocm:
        try:
            import subprocess

            out = subprocess.run(
                [rocm, "--showmeminfo", "vram"],
                capture_output=True, text=True, timeout=6,
            )
            text = out.stdout
            used = _grab_mb(text, "Total used memory")
            total = _grab_mb(text, "Total memory")
            if total:
                return used, total
        except Exception:
            pass
    # Apple Silicon unified memory is reported as RAM by psutil.
    return 0.0, 0.0


def _grab_mb(text: str, label: str) -> float:
    for line in text.splitlines():
        if label.lower() in line.lower():
            for tok in line.replace(":", " ").split():
                if tok.isdigit():
                    return float(tok) / (1024 * 1024)
    return 0.0


def probe_ram() -> tuple[float, float]:
    """Return (used_mb, total_mb) of system RAM."""
    if psutil is not None:
        vm = psutil.virtual_memory()
        return vm.used / (1024 * 1024), vm.total / (1024 * 1024)
    try:  # Windows fallback without psutil
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        total = stat.ullTotalPhys / (1024 * 1024)
        avail = stat.ullAvailPhys / (1024 * 1024)
        return max(total - avail, 0.0), total
    except Exception:
        return 0.0, 0.0


def probe_cpu() -> float:
    if psutil is not None:
        try:
            return psutil.cpu_percent(interval=None)
        except Exception:
            return 0.0
    return 0.0


def hardware_summary() -> dict[str, Any]:
    """A compact hardware description used by `carla doctor` and the HUD."""
    ram_used, ram_total = probe_ram()
    vram_used, vram_total = probe_vram()
    cpu_count = os.cpu_count() or 1
    brand = platform.processor() or platform.machine()
    if psutil is not None:
        try:
            brand = psutil.cpu_count(logical=False) and platform.processor() or platform.machine()
        except Exception:
            pass
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": cpu_count,
        "cpu_brand": brand,
        "ram_total_mb": ram_total,
        "ram_used_mb": ram_used,
        "vram_total_mb": vram_total,
        "vram_used_mb": vram_used,
        "psutil": psutil is not None,
    }


# --------------------------------------------------------------------------- #
# Telemetry store
# --------------------------------------------------------------------------- #
_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    agent TEXT,
    tier TEXT,
    provider TEXT,
    model TEXT,
    started_at REAL,
    latency_ms REAL,
    ttft_ms REAL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    tokens_per_second REAL,
    status TEXT,
    error TEXT,
    vram_used_mb REAL,
    vram_total_mb REAL,
    ram_used_mb REAL,
    ram_total_mb REAL,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_requests_started ON requests(started_at);
CREATE TABLE IF NOT EXISTS resources (
    ts REAL PRIMARY KEY,
    cpu_percent REAL,
    ram_used_mb REAL,
    ram_total_mb REAL,
    ram_percent REAL,
    vram_used_mb REAL,
    vram_total_mb REAL,
    vram_percent REAL,
    power_watts REAL,
    energy_vendor TEXT
);
CREATE INDEX IF NOT EXISTS idx_resources_ts ON resources(ts);
CREATE TABLE IF NOT EXISTS energy_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    request_id TEXT,
    agent TEXT,
    tier TEXT,
    model TEXT,
    watts REAL,
    joules REAL,
    vendor TEXT,
    flops REAL,
    energy_vendor TEXT
);
CREATE INDEX IF NOT EXISTS idx_energy_ts ON energy_samples(ts);
CREATE INDEX IF NOT EXISTS idx_energy_request ON energy_samples(request_id);
"""

# Columns added after the initial schema — applied on open for existing DBs.
_MIGRATIONS = [
    ("requests", "flops", "REAL"),
    ("requests", "energy_joules", "REAL"),
    ("requests", "power_watts", "REAL"),
    ("resources", "power_watts", "REAL"),
    ("resources", "energy_vendor", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the schema was first written."""
    for table, column, ctype in _MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
        except sqlite3.Error:
            continue
    conn.commit()


class Telemetry:
    """Thread-safe SQLite telemetry store with a background resource sampler."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool = True,
        sample_interval: float = 1.0,
        track_resources: bool = True,
        track_energy: bool = True,
        energy_vendor: str = "auto",
    ) -> None:
        self.db_path = Path(db_path)
        self.enabled = enabled
        self.sample_interval = sample_interval
        self.track_resources = track_resources
        self.track_energy = track_energy
        self.energy_vendor = energy_vendor or "auto"
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._stop = threading.Event()
        self._sampler: threading.Thread | None = None
        self._live: list[ResourceSample] = []
        from .energy import EnergyMonitor

        self._energy = EnergyMonitor(self.energy_vendor, sample_interval)

    # -- lifecycle ------------------------------------------------------- #
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            _migrate(conn)
            conn.commit()
            self._conn = conn
        return self._conn

    def start(self) -> None:
        if not self.enabled or not self.track_resources:
            return
        if self._sampler and self._sampler.is_alive():
            return
        self._stop.clear()
        self._sampler = threading.Thread(
            target=self._sample_loop, name="carla-telemetry", daemon=True
        )
        self._sampler.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sampler:
            self._sampler.join(timeout=2.0)
            self._sampler = None
        with self._lock:
            if self._conn:
                self._conn.commit()
                self._conn.close()
                self._conn = None

    def close(self) -> None:
        self.stop()

    # -- sampling -------------------------------------------------------- #
    def _sample_loop(self) -> None:
        while not self._stop.wait(self.sample_interval):
            try:
                self.sample_now()
            except Exception:
                continue

    def sample_now(self) -> ResourceSample:
        ram_used, ram_total = probe_ram()
        vram_used, vram_total = probe_vram()
        power = self._sample_power()
        sample = ResourceSample(
            ts=time.time(),
            cpu_percent=probe_cpu(),
            ram_used_mb=ram_used,
            ram_total_mb=ram_total,
            ram_percent=(ram_used / ram_total * 100) if ram_total else 0.0,
            vram_used_mb=vram_used,
            vram_total_mb=vram_total,
            vram_percent=(vram_used / vram_total * 100) if vram_total else 0.0,
            power_watts=power.watts if power.available else 0.0,
            energy_vendor=power.vendor if power.available else "",
        )
        with self._lock:
            self._live.append(sample)
            if len(self._live) > 600:
                del self._live[:300]
            if self.enabled:
                try:
                    conn = self._connect()
                    conn.execute(
                        "INSERT OR REPLACE INTO resources "
                        "(ts,cpu_percent,ram_used_mb,ram_total_mb,ram_percent,"
                        "vram_used_mb,vram_total_mb,vram_percent,power_watts,energy_vendor) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        sample.to_row(),
                    )
                    conn.commit()
                except Exception:
                    pass
        return sample

    def _sample_power(self):
        """Read power draw, if an energy source is configured and available."""
        if not self.track_energy:
            from .energy import PowerSample

            return PowerSample(available=False)
        try:
            return self._energy.sample()
        except Exception:
            from .energy import PowerSample

            return PowerSample(available=False)

    # -- writes ---------------------------------------------------------- #
    def record_request(self, metric: RequestMetric) -> None:
        if not self.enabled:
            return
        with self._lock:
            try:
                conn = self._connect()
                conn.execute(
                    "INSERT OR REPLACE INTO requests "
                    "(request_id,agent,tier,provider,model,started_at,latency_ms,"
                    "ttft_ms,prompt_tokens,completion_tokens,total_tokens,"
                    "tokens_per_second,status,error,vram_used_mb,vram_total_mb,"
                    "ram_used_mb,ram_total_mb,cost_usd,flops,energy_joules,power_watts) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    metric.to_row(),
                )
                conn.commit()
            except Exception:
                pass
        if self.track_energy and metric.energy_joules:
            self.record_energy(
                request_id=metric.request_id,
                agent=metric.agent,
                tier=metric.tier,
                model=metric.model,
                joules=metric.energy_joules,
                watts=metric.power_watts,
                flops=metric.flops,
            )

    def record_energy(
        self,
        *,
        request_id: str = "",
        agent: str = "",
        tier: str = "",
        model: str = "",
        joules: float = 0.0,
        watts: float = 0.0,
        flops: float = 0.0,
    ) -> None:
        """Record an energy sample for a request."""
        if not self.enabled:
            return
        vendor = getattr(self._energy, "_resolved", "") or ""
        with self._lock:
            try:
                conn = self._connect()
                conn.execute(
                    "INSERT INTO energy_samples "
                    "(ts,request_id,agent,tier,model,watts,joules,vendor,flops,energy_vendor) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        time.time(),
                        request_id,
                        agent,
                        tier,
                        model,
                        float(watts or 0.0),
                        float(joules or 0.0),
                        vendor,
                        float(flops or 0.0),
                        vendor,
                    ),
                )
                conn.commit()
            except Exception:
                pass

    # -- reads ----------------------------------------------------------- #
    def summary(self, window_minutes: int = 60) -> dict[str, Any]:
        """Aggregate metrics for the HUD telemetry panel."""
        cutoff = time.time() - (window_minutes * 60)
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                """
                SELECT COUNT(*),
                       AVG(latency_ms),
                       AVG(tokens_per_second),
                       AVG(ttft_ms),
                       SUM(total_tokens),
                       SUM(cost_usd),
                       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END),
                       SUM(energy_joules),
                       SUM(flops),
                       AVG(power_watts)
                FROM requests WHERE started_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            live = list(self._live[-60:])

        count = row[0] or 0
        avg_latency = row[1] or 0.0
        avg_tps = row[2] or 0.0
        avg_ttft = row[3] or 0.0
        total_tokens = row[4] or 0
        total_cost = row[5] or 0.0
        errors = row[6] or 0
        total_joules = row[7] or 0.0
        total_flops = row[8] or 0.0
        avg_watts = row[9] or 0.0

        latest = live[-1] if live else None
        energy_vendor = ""
        try:
            energy_vendor = getattr(self._energy, "_resolved", "") or ""
        except Exception:
            pass
        return {
            "window_minutes": window_minutes,
            "requests": count,
            "avg_latency_ms": round(avg_latency, 2),
            "avg_tokens_per_second": round(avg_tps, 2),
            "avg_ttft_ms": round(avg_ttft, 2),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "errors": errors,
            "error_rate": round(errors / count * 100, 2) if count else 0.0,
            # Efficiency metrics — energy and FLOPs alongside latency/cost.
            "energy": {
                "total_joules": round(total_joules, 4),
                "avg_watts": round(avg_watts, 3),
                "vendor": energy_vendor,
                "available": bool(energy_vendor),
            },
            "total_flops": total_flops,
            "resources": {
                "cpu_percent": round(latest.cpu_percent, 1) if latest else 0.0,
                "ram_used_mb": round(latest.ram_used_mb, 1) if latest else 0.0,
                "ram_total_mb": round(latest.ram_total_mb, 1) if latest else 0.0,
                "ram_percent": round(latest.ram_percent, 1) if latest else 0.0,
                "vram_used_mb": round(latest.vram_used_mb, 1) if latest else 0.0,
                "vram_total_mb": round(latest.vram_total_mb, 1) if latest else 0.0,
                "vram_percent": round(latest.vram_percent, 1) if latest else 0.0,
                "power_watts": round(latest.power_watts, 2) if latest else 0.0,
                "energy_vendor": latest.energy_vendor if latest else "",
            },
            "series": [
                {
                    "ts": s.ts,
                    "cpu": round(s.cpu_percent, 1),
                    "ram": round(s.ram_percent, 1),
                    "vram": round(s.vram_percent, 1),
                    "watts": round(s.power_watts, 2),
                }
                for s in live
            ],
        }

    def energy_summary(self, window_minutes: int = 60) -> dict[str, Any]:
        """Energy and FLOPs for the window — totals plus a per-model split.

        The aggregate fields are ``None`` when no request in the window
        carried an energy or FLOPs measurement. Reporting zero there
        would be a lie: an unmeasured joule count is not zero joules.
        """
        cutoff = time.time() - (window_minutes * 60)
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT model,
                       COUNT(*),
                       SUM(energy_joules),
                       SUM(flops),
                       SUM(total_tokens),
                       AVG(power_watts)
                FROM requests
                WHERE started_at >= ?
                GROUP BY model
                ORDER BY 3 DESC
                """,
                (cutoff,),
            ).fetchall()
        out = []
        total_joules = 0.0
        total_flops = 0.0
        watts_values: list[float] = []
        measured_joules = False
        measured_flops = False
        for model, n, joules, flops, tokens, watts in rows:
            if joules:
                measured_joules = True
                total_joules += joules
            if flops:
                measured_flops = True
                total_flops += flops
            if watts:
                watts_values.append(watts)
            out.append(
                {
                    "model": model or "unknown",
                    "requests": n or 0,
                    "total_joules": round(joules or 0.0, 4),
                    "total_flops": flops or 0.0,
                    "total_tokens": tokens or 0,
                    "avg_watts": round(watts or 0.0, 3),
                    "joules_per_request": round((joules or 0.0) / n, 6) if n else 0.0,
                    "flops_per_token": (
                        round((flops or 0.0) / tokens, 2) if tokens else 0.0
                    ),
                }
            )
        avg_watts = (
            sum(watts_values) / len(watts_values) if watts_values else None
        )
        return {
            "window_minutes": window_minutes,
            "total_joules": round(total_joules, 4) if measured_joules else None,
            "total_flops": total_flops if measured_flops else None,
            "avg_watts": round(avg_watts, 3) if avg_watts is not None else None,
            "requests": sum(r[1] or 0 for r in rows),
            "models": out,
        }

    def recent_requests(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT request_id, agent, tier, provider, model, started_at,
                       latency_ms, ttft_ms, total_tokens, tokens_per_second, status, error
                FROM requests ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "request_id": r[0],
                    "agent": r[1],
                    "tier": r[2],
                    "provider": r[3],
                    "model": r[4],
                    "started_at": r[5],
                    "latency_ms": round(r[6] or 0.0, 2),
                    "ttft_ms": round(r[7] or 0.0, 2),
                    "total_tokens": r[8] or 0,
                    "tokens_per_second": round(r[9] or 0.0, 2),
                    "status": r[10],
                    "error": (r[11] or "")[:300],
                }
            )
        return out

    def per_model_breakdown(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                """
                SELECT provider, model, tier, COUNT(*), AVG(latency_ms),
                       AVG(tokens_per_second), SUM(total_tokens),
                       SUM(energy_joules), SUM(flops), AVG(power_watts)
                FROM requests GROUP BY provider, model, tier
                ORDER BY COUNT(*) DESC
                """
            ).fetchall()
        return [
            {
                "provider": r[0],
                "model": r[1],
                "tier": r[2],
                "requests": r[3],
                "avg_latency_ms": round(r[4] or 0.0, 2),
                "avg_tokens_per_second": round(r[5] or 0.0, 2),
                "total_tokens": r[6] or 0,
                "total_joules": round(r[7] or 0.0, 4),
                "total_flops": r[8] or 0.0,
                "avg_watts": round(r[9] or 0.0, 2),
            }
            for r in rows
        ]

    def prune(self, retain_days: int = 30) -> int:
        cutoff = time.time() - retain_days * 86400
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM requests WHERE started_at < ?", (cutoff,))
            conn.execute("DELETE FROM resources WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount


# --------------------------------------------------------------------------- #
# Live metric context manager
# --------------------------------------------------------------------------- #
class RequestTimer:
    """Context manager that measures latency + throughput for one model call."""

    def __init__(
        self,
        telemetry: Telemetry | None,
        *,
        request_id: str,
        agent: str,
        tier: str,
        provider: str,
        model: str,
    ) -> None:
        self.telemetry = telemetry
        self.metric = RequestMetric(
            request_id=request_id,
            agent=agent,
            tier=tier,
            provider=provider,
            model=model,
            started_at=time.time(),
        )
        self._t0 = time.perf_counter()
        self._ttft: float | None = None

    def mark_first_token(self) -> None:
        if self._ttft is None:
            self._ttft = (time.perf_counter() - self._t0) * 1000

    def __enter__(self) -> "RequestTimer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.perf_counter() - self._t0
        self.metric.latency_ms = elapsed * 1000
        self.metric.time_to_first_token_ms = self._ttft or 0.0
        if exc_type is not None:
            self.metric.status = "error"
            self.metric.error = f"{exc_type.__name__}: {exc}"
        else:
            gen = max(elapsed - (self._ttft or 0) / 1000, 1e-6)
            if self.metric.completion_tokens:
                self.metric.tokens_per_second = self.metric.completion_tokens / gen
            self.metric.total_tokens = (
                self.metric.prompt_tokens + self.metric.completion_tokens
            )
        vram_used, vram_total = probe_vram()
        ram_used, ram_total = probe_ram()
        self.metric.vram_used_mb = vram_used
        self.metric.vram_total_mb = vram_total
        self.metric.ram_used_mb = ram_used
        self.metric.ram_total_mb = ram_total

        # Efficiency: FLOPs from token counts, energy from measured power.
        try:
            from .energy import estimate_flops, params_from_model_name

            params = params_from_model_name(self.metric.model)
            flops = estimate_flops(
                self.metric.prompt_tokens,
                self.metric.completion_tokens,
                params or 0.0,
            )
            if flops:
                self.metric.flops = flops
        except Exception:
            pass

        if self.telemetry and self.telemetry.track_energy:
            try:
                power = self.telemetry._sample_power()
                if power.available:
                    self.metric.power_watts = power.watts
                    self.metric.energy_joules = power.watts * elapsed
            except Exception:
                pass

        if self.telemetry and self.telemetry.enabled:
            self.telemetry.record_request(self.metric)
