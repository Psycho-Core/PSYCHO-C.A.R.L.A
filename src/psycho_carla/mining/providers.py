"""
Mining providers — one per hardware/engine combination.

Three providers, mirroring upstream:

  * ``cpu-pearl``     — CPU mining, works on darwin/linux
  * ``vllm-pearl``    — rides alongside a vLLM server on the same GPU
  * ``apple-mps-pearl`` — Apple Silicon GPU

Each provider's ``detect()`` is a pure inspection. When the optional
mining packages are absent, ``detect()`` returns ``supported=False`` with a
reason naming the missing dependency — the system never raises at import.

Layer: Pillar 2 (Model).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .types import (
    MiningCapabilities,
    MiningConfig,
    MiningProvider,
    MiningStats,
    SoloTarget,
)

DEFAULT_GATEWAY_RPC_PORT = 8910
DEFAULT_GATEWAY_METRICS_PORT = 8911
DEFAULT_PEARLD_RPC_URL = "http://127.0.0.1:8990"
DEFAULT_GATEWAY_HOST = "127.0.0.1"

#: The optional packages each provider needs.
PROVIDER_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "cpu-pearl": ("pearl_mining",),
    "vllm-pearl": ("pearl_mining", "vllm"),
    "apple-mps-pearl": ("pearl_mining",),
}


def packages_available(names: tuple[str, ...]) -> tuple[bool, list[str]]:
    """Which of the named packages are importable?"""
    import importlib.util

    missing: list[str] = []
    for name in names:
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    return (not missing), missing


def install_hint(provider_id: str) -> str:
    """The pip command that would enable a provider."""
    extras = {
        "cpu-pearl": "pip install 'psycho-carla[mining]'",
        "vllm-pearl": "pip install 'psycho-carla[mining,vllm]'",
        "apple-mps-pearl": "pip install 'psycho-carla[mining]'",
    }
    return extras.get(provider_id, "pip install 'psycho-carla[mining]'")


def mining_log_dir() -> Path:
    """Where mining logs are written."""
    home = os.environ.get("PSYCHO_CARLA_HOME")
    base = Path(home) if home else Path.home() / ".psycho_carla"
    return base / "logs" / "mining"


class BasePearlProvider(MiningProvider):
    """Shared machinery for the Pearl-backed providers."""

    provider_id = "base-pearl"

    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._config: MiningConfig | None = None
        self._started_at: float | None = None
        self._stats = MiningStats(provider_id=self.provider_id)

    # -- helpers -------------------------------------------------------------- #
    def _requirements(self) -> tuple[str, ...]:
        return PROVIDER_REQUIREMENTS.get(self.provider_id, ("pearl_mining",))

    def _detect_missing(self) -> list[str]:
        _ok, missing = packages_available(self._requirements())
        return missing

    @staticmethod
    def _platform_name(hw: Any) -> str:
        """The short platform name: ``linux``, ``darwin`` or ``windows``.

        ``hardware_summary()`` reports ``platform.platform()`` — the full
        uname-style string — so the short name is taken from the runtime
        when the hardware object does not supply one.
        """
        name = getattr(hw, "platform", "") or ""
        if name in {"linux", "darwin", "windows"}:
            return name
        import sys

        if sys.platform.startswith("linux"):
            return "linux"
        if sys.platform == "darwin":
            return "darwin"
        if sys.platform.startswith("win"):
            return "windows"
        lowered = name.lower()
        for candidate in ("linux", "darwin", "windows"):
            if candidate in lowered:
                return candidate
        return name or "unknown"

    def _write_credential_file(
        self, content: dict[str, Any]
    ) -> Path:
        return self._write_credential_file_impl(content)

    def _write_credential_file_impl(self, content: dict[str, Any]) -> Path:
        return MiningProvider._write_credential_file(content)

    # -- contract ------------------------------------------------------------- #
    def is_running(self) -> bool:
        """Is a mining session currently active?"""
        return self._process is not None and self._process.poll() is None

    def start(self, config: MiningConfig) -> dict[str, Any]:
        """Start the mining session."""
        missing = self._detect_missing()
        if missing:
            raise RuntimeError(
                f"{self.provider_id} is unavailable: missing "
                f"{', '.join(missing)}. {install_hint(self.provider_id)}"
            )
        if not config.wallet_address:
            raise ValueError("A wallet address is required to mine.")

        self._config = config
        self._started_at = time.time()
        self._stats = MiningStats(
            provider_id=self.provider_id,
            payout_target=(
                "solo" if isinstance(config.submit_target, SoloTarget) else "pool"
            ),
        )
        return self._stats.to_dict()

    def stop(self) -> dict[str, Any]:
        """Stop the session and return the final counters."""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=10)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._started_at is not None:
            self._stats.uptime_seconds = time.time() - self._started_at
        self._started_at = None
        self._config = None
        return self._stats.to_dict()

    def stats(self) -> MiningStats:
        """Live counters."""
        if not self.is_running():
            return self._stats
        if self._started_at is not None:
            self._stats.uptime_seconds = time.time() - self._started_at
        return self._stats


class CpuPearlProvider(BasePearlProvider):
    """CPU mining via the Pearl miner-loop and gateway."""

    provider_id = "cpu-pearl"

    @classmethod
    def detect(
        cls, hw: Any, engine_id: str, model: str
    ) -> MiningCapabilities:
        platform = cls._platform_name(hw)
        if platform not in {"darwin", "linux"}:
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"cpu-pearl supports darwin and linux only; this host is "
                    f"'{platform}'"
                ),
            )
        ok, missing = packages_available(PROVIDER_REQUIREMENTS["cpu-pearl"])
        if not ok:
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"Pearl packages are not installed ({', '.join(missing)}) — "
                    f"{install_hint('cpu-pearl')}"
                ),
            )
        return MiningCapabilities(supported=True)


class VllmPearlProvider(BasePearlProvider):
    """Mining that rides alongside a running vLLM server on the same GPU."""

    provider_id = "vllm-pearl"

    @classmethod
    def detect(
        cls, hw: Any, engine_id: str, model: str
    ) -> MiningCapabilities:
        if engine_id != "vllm":
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"vllm-pearl requires the vLLM engine; this configuration "
                    f"uses '{engine_id}'"
                ),
            )
        ok, missing = packages_available(PROVIDER_REQUIREMENTS["vllm-pearl"])
        if not ok:
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"Pearl packages are not installed ({', '.join(missing)}) — "
                    f"{install_hint('vllm-pearl')}"
                ),
            )
        return MiningCapabilities(supported=True)


class AppleMpsPearlProvider(BasePearlProvider):
    """Mining on Apple Silicon through Metal Performance Shaders."""

    provider_id = "apple-mps-pearl"

    @classmethod
    def detect(
        cls, hw: Any, engine_id: str, model: str
    ) -> MiningCapabilities:
        platform = cls._platform_name(hw)
        if platform != "darwin":
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"apple-mps-pearl requires macOS; this host is '{platform}'"
                ),
            )
        ok, missing = packages_available(
            PROVIDER_REQUIREMENTS["apple-mps-pearl"]
        )
        if not ok:
            return MiningCapabilities(
                supported=False,
                reason=(
                    f"Pearl packages are not installed ({', '.join(missing)}) — "
                    f"{install_hint('apple-mps-pearl')}"
                ),
            )
        return MiningCapabilities(supported=True)


#: Every provider, by id.
PROVIDERS: dict[str, type[BasePearlProvider]] = {
    CpuPearlProvider.provider_id: CpuPearlProvider,
    VllmPearlProvider.provider_id: VllmPearlProvider,
    AppleMpsPearlProvider.provider_id: AppleMpsPearlProvider,
}


def ensure_registered() -> None:
    """Idempotently register every provider."""
    from .registry import register_provider

    for provider in PROVIDERS.values():
        register_provider(provider.provider_id, provider)


__all__ = [
    "AppleMpsPearlProvider",
    "BasePearlProvider",
    "CpuPearlProvider",
    "PROVIDERS",
    "VllmPearlProvider",
    "ensure_registered",
    "install_hint",
    "mining_log_dir",
    "packages_available",
]
