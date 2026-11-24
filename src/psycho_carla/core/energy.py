"""
PSYCHO-C.A.R.L.A. — Energy & FLOPs accounting.

OpenJarvis treats **energy, FLOPs, latency and dollar cost as first-class
evaluation constraints** alongside accuracy — that is the core of its
research thesis. Latency and cost are tracked elsewhere; this module
covers the two that need hardware probes:

  * **Power draw** (watts) from whichever vendor is present:
      - ``nvidia``   — ``nvidia-smi --query-gpu=power.draw``
      - ``amd``      — ``rocm-smi --showpower``
      - ``apple``    — ``powermetrics`` (needs sudo; degrades gracefully)
      - ``cpu_rapl`` — Linux RAPL via ``/sys/class/powercap``
      - ``cpu``      — TDP estimate from the CPU model (last resort)
  * **FLOPs** — estimated from the token counts and the model's parameter
    count, using the standard 2·N rule for the forward pass.

Both are best-effort: on hardware we cannot probe, the functions return
``None`` rather than guessing, so a report can honestly say "unknown"
rather than "zero".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

# Vendor identifiers accepted by [telemetry].energy_vendor.
ENERGY_VENDORS = ("auto", "nvidia", "amd", "apple", "cpu_rapl", "cpu", "none")


@dataclass
class PowerSample:
    """A single instantaneous power reading."""

    watts: float = 0.0
    vendor: str = ""
    available: bool = False
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "watts": round(self.watts, 3),
            "vendor": self.vendor,
            "available": self.available,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Vendor probes
# --------------------------------------------------------------------------- #
def _run(cmd: list[str], timeout: int = 6) -> Optional[str]:
    exe = cmd[0]
    if not shutil.which(exe):
        return None
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or None


def probe_nvidia_power() -> Optional[float]:
    """Total board power draw (W) across NVIDIA GPUs."""
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=power.draw",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return None
    total = 0.0
    found = False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total += float(line)
            found = True
        except ValueError:
            continue
    return total if found else None


def probe_amd_power() -> Optional[float]:
    """Average board power (W) across AMD ROCm GPUs."""
    out = _run(["rocm-smi", "--showpower", "--csv"])
    if not out:
        return None
    watts: list[float] = []
    for line in out.splitlines():
        low = line.lower()
        if "average" not in low and "current" not in low:
            continue
        for tok in re.split(r"[,\s]+", line):
            try:
                val = float(tok)
            except ValueError:
                continue
            # Plausible board draw range; filters out CSV headers and indices.
            if 1.0 <= val <= 1000.0:
                watts.append(val)
                break
    return sum(watts) / len(watts) if watts else None


def probe_apple_power() -> Optional[float]:
    """Package power (W) on Apple Silicon via powermetrics.

    Requires elevated privileges; returns None when unavailable, which is
    the normal case for an unprivileged process.
    """
    out = _run(
        ["powermetrics", "--samplers", "cpu_power", "-n", "1", "-i", "200"],
        timeout=10,
    )
    if not out:
        return None
    match = re.search(r"CPU Power:\s*([0-9.]+)\s*mW", out)
    if match:
        return float(match.group(1)) / 1000.0
    match = re.search(r"Package Power:\s*([0-9.]+)\s*mW", out)
    if match:
        return float(match.group(1)) / 1000.0
    return None


class RaplReader:
    """Stateful RAPL reader that converts energy counters into watts.

    The first call establishes a baseline; each subsequent call returns
    the average power over the elapsed interval.
    """

    def __init__(self) -> None:
        self._baseline: Optional[tuple[float, float]] = None

    def _paths(self) -> list[tuple[str, str, str]]:
        base = "/sys/class/powercap"
        out: list[tuple[str, str, str]] = []
        try:
            entries = sorted(os.listdir(base))
        except OSError:
            return out
        for entry in entries:
            if not entry.startswith("intel-rapl:"):
                continue
            energy = os.path.join(base, entry, "energy_uj")
            maxr = os.path.join(base, entry, "max_energy_range_uj")
            name = os.path.join(base, entry, "name")
            if os.path.exists(energy) and os.path.exists(maxr):
                out.append((energy, maxr, name))
        return out

    def read(self) -> Optional[float]:
        """Return average watts since the previous call, or None."""
        import time

        paths = self._paths()
        if not paths:
            return None
        total_uj = 0.0
        for energy, maxr, _name in paths:
            try:
                with open(energy, encoding="utf-8") as fh:
                    val = float(fh.read().strip())
                with open(maxr, encoding="utf-8") as fh:
                    maxv = float(fh.read().strip())
            except (OSError, ValueError):
                continue
            # Handle counter wrap-around.
            if maxv > 0 and val > maxv:
                val -= maxv
            total_uj += val
        now = time.time()
        if self._baseline is None:
            self._baseline = (total_uj, now)
            return None
        prev_uj, prev_ts = self._baseline
        dt = now - prev_ts
        if dt <= 0:
            return None
        self._baseline = (total_uj, now)
        return max(0.0, (total_uj - prev_uj) / 1_000_000.0 / dt)


def probe_cpu_tdp() -> Optional[float]:
    """Nominal TDP (W) of the CPU, read from the model name.

    This is a *worst case* estimate, not a measurement — it is only used
    when no real power probe exists, and is flagged as an estimate.
    """
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    match = re.search(r"model name\s*:\s*(.+)", text)
    if not match:
        return None
    name = match.group(1).lower()
    # Common TDP hints encoded in the model string.
    watts = None
    if "m3 max" in name or "m3 ultra" in name:
        watts = 60.0
    elif "m3 pro" in name:
        watts = 30.0
    elif "m2 max" in name:
        watts = 60.0
    elif "m2 pro" in name:
        watts = 30.0
    elif "m1 max" in name:
        watts = 30.0
    elif "m1 pro" in name:
        watts = 30.0
    elif "m1 " in name or "m2 " in name:
        watts = 20.0
    elif "ryzen 9" in name or "9950" in name or "9955" in name:
        watts = 170.0
    elif "ryzen 7" in name or "7700" in name or "7800" in name:
        watts = 65.0
    elif "ryzen 5" in name or "5600" in name or "7600" in name:
        watts = 65.0
    elif "i9" in name or "ryzen 9" in name:
        watts = 125.0
    elif "i7" in name or "ryzen 7" in name:
        watts = 65.0
    elif "i5" in name:
        watts = 65.0
    elif "i3" in name:
        watts = 35.0
    for pat in (r"(\d{2,3})w", r"tdp[^0-9]{0,8}(\d{2,3})"):
        m = re.search(pat, name)
        if m:
            try:
                watts = float(m.group(1))
                break
            except ValueError:
                pass
    if watts is not None:
        return watts
    # Xeon / EPYC server parts do not carry a TDP in the model string.
    # Fall back to a conservative estimate based on the family.
    if "epyc" in name:
        return 280.0
    if "xeon" in name:
        return 250.0
    return None


# --------------------------------------------------------------------------- #
# FLOPs estimation
# --------------------------------------------------------------------------- #
def estimate_flops(
    prompt_tokens: int,
    completion_tokens: int,
    param_count_billions: float,
) -> Optional[float]:
    """Estimate FLOPs for a forward pass.

    Uses the standard approximation for dense transformer inference:

        FLOPs ≈ 2 · N · (prompt_tokens + completion_tokens)

    where ``N`` is the parameter count. Returns None when the parameter
    count is unknown, because guessing would produce a number that looks
    authoritative but is not.
    """
    if param_count_billions is None or param_count_billions <= 0:
        return None
    total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
    if total_tokens <= 0:
        return 0.0
    return 2.0 * param_count_billions * 1e9 * total_tokens


def params_from_model_name(model: str) -> Optional[float]:
    """Guess a parameter count (in billions) from a model id.

    Recognises the common size suffixes. Returns None when unknown.
    """
    if not model:
        return None
    name = model.lower()
    patterns = [
        (r"\b(\d+(?:\.\d+)?)\s*b\b", 1.0),          # "7b", "13b"
        (r"[-_:]([3579]|1[0-9]|2[0-9]|3[0-9]|4[0-9]|6[0-9]|7[0-9])b\b", 1.0),
        (r"\b(\d+(?:\.\d+)?)\s*billion", 1.0),
    ]
    for pat, scale in patterns:
        m = re.search(pat, name)
        if m:
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            # Guard against matching unrelated numbers.
            if 0.1 <= val <= 400.0:
                return val * scale
    # Model families with known sizes.
    known = {
        "gpt-4": None,
        "llama-3.1-8b": 8.0,
        "llama-3.1-70b": 70.0,
        "llama-3.2-1b": 1.0,
        "llama-3.2-3b": 3.0,
        "qwen2.5-0.5b": 0.5,
        "qwen2.5-1.5b": 1.5,
        "qwen2.5-3b": 3.0,
        "qwen2.5-7b": 7.0,
        "qwen2.5-14b": 14.0,
        "qwen2.5-32b": 32.0,
        "phi-3-mini": 3.8,
        "phi-3": 3.8,
        "mistral-7b": 7.0,
        "mixtral-8x7b": 46.7,
    }
    for key, val in known.items():
        if key in name and val:
            return val
    return None


# --------------------------------------------------------------------------- #
# Monitor
# --------------------------------------------------------------------------- #
class EnergyMonitor:
    """Samples power draw on an interval, with per-vendor fallback."""

    def __init__(self, vendor: str = "auto", interval: float = 1.0) -> None:
        self.vendor = (vendor or "auto").lower()
        self.interval = max(0.05, float(interval or 1.0))
        self._rapl = RaplReader()
        self._resolved: Optional[str] = None

    def resolve_vendor(self) -> Optional[str]:
        """Determine which energy source is usable on this machine."""
        if self._resolved is not None:
            return self._resolved or None
        order: list[tuple[str, callable]] = []
        if self.vendor == "auto":
            order = [
                ("nvidia", probe_nvidia_power),
                ("amd", probe_amd_power),
                ("cpu_rapl", self._rapl.read),
                ("apple", probe_apple_power),
            ]
        elif self.vendor == "nvidia":
            order = [("nvidia", probe_nvidia_power)]
        elif self.vendor == "amd":
            order = [("amd", probe_amd_power)]
        elif self.vendor == "apple":
            order = [("apple", probe_apple_power)]
        elif self.vendor == "cpu_rapl":
            order = [("cpu_rapl", self._rapl.read)]
        elif self.vendor == "cpu":
            order = [("cpu", probe_cpu_tdp)]
        elif self.vendor == "none":
            self._resolved = ""
            return None
        else:
            self._resolved = ""
            return None

        for name, probe in order:
            try:
                if probe() is not None:
                    self._resolved = name
                    return name
            except Exception:
                continue
        # No live probe — fall back to the TDP estimate so the metric is
        # present but explicitly marked as an estimate.
        if self.vendor in ("auto", "cpu"):
            if probe_cpu_tdp() is not None:
                self._resolved = "cpu"
                return "cpu"
        self._resolved = ""
        return None

    def sample(self) -> PowerSample:
        """Take one power reading."""
        vendor = self.resolve_vendor()
        if not vendor:
            return PowerSample(watts=0.0, vendor="", available=False, source="")
        try:
            if vendor == "nvidia":
                watts = probe_nvidia_power()
                source = "nvidia-smi power.draw"
            elif vendor == "amd":
                watts = probe_amd_power()
                source = "rocm-smi --showpower"
            elif vendor == "apple":
                watts = probe_apple_power()
                source = "powermetrics cpu_power"
            elif vendor == "cpu_rapl":
                watts = self._rapl.read()
                source = "/sys/class/powercap"
            elif vendor == "cpu":
                watts = probe_cpu_tdp()
                source = "cpu TDP estimate"
            else:
                return PowerSample(vendor="", available=False)
        except Exception:
            return PowerSample(vendor=vendor, available=False, source=vendor)

        if watts is None:
            return PowerSample(watts=0.0, vendor=vendor, available=False, source=source)
        return PowerSample(
            watts=float(watts), vendor=vendor, available=True, source=source
        )

    def joules(self, watts: float, seconds: float) -> float:
        """Convert a power reading into energy over an interval."""
        return max(0.0, float(watts)) * max(0.0, float(seconds))
