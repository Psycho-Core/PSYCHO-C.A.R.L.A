"""
Mining contracts — the ABC every mining provider implements.

A provider is one ``(hardware, engine, model)`` combination. The contract
is deliberately small:

  * ``detect()`` — pure inspection. Is this combo mineable at all? No
    subprocess, no network, no Docker. This is what ``carla mine doctor``
    calls.
  * ``start()`` / ``stop()`` — run a mining session.
  * ``stats()`` — live counters.

Layer: Pillar 2 (Model).
"""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union


@dataclass(slots=True)
class MiningCapabilities:
    """The result of a provider's ``detect()`` call.

    ``reason`` is human-readable and is surfaced verbatim by
    ``carla mine doctor`` when ``supported`` is False.
    """

    supported: bool
    reason: str | None = None
    estimated_hashrate: float | None = None  # shares/sec, best-effort

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported": self.supported,
            "reason": self.reason,
            "estimated_hashrate": self.estimated_hashrate,
        }


@dataclass(slots=True)
class SoloTarget:
    """Mine directly to a node."""

    pearld_rpc_url: str


@dataclass(slots=True)
class PoolTarget:
    """Mine through an operator-run pool."""

    url: str
    worker_id: str | None = None


SubmitTarget = Union[SoloTarget, PoolTarget]


@dataclass(slots=True)
class MiningConfig:
    """User-supplied mining configuration, from the ``[mining]`` TOML section."""

    provider: str = ""
    wallet_address: str = ""
    submit_target: SubmitTarget | None = None
    fee_bps: int = 0
    fee_payout_address: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "wallet_address": self.wallet_address,
            "submit_target": (
                {
                    "kind": type(self.submit_target).__name__,
                    **self.submit_target.__dict__,
                }
                if self.submit_target is not None
                else None
            ),
            "fee_bps": self.fee_bps,
            "fee_payout_address": self.fee_payout_address,
            "extra": dict(self.extra),
        }


@dataclass(slots=True)
class MiningStats:
    """Live counters for a mining session."""

    provider_id: str = ""
    shares_submitted: int = 0
    shares_accepted: int = 0
    blocks_found: int = 0
    hashrate: float = 0.0
    uptime_seconds: float = 0.0
    last_share_at: float | None = None
    last_error: str | None = None
    payout_target: str = "solo"
    fees_owed: int = 0

    @property
    def acceptance_rate(self) -> float:
        """Accepted shares over submitted shares, or 1.0 when idle."""
        if not self.shares_submitted:
            return 1.0
        return self.shares_accepted / self.shares_submitted

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "shares_submitted": self.shares_submitted,
            "shares_accepted": self.shares_accepted,
            "blocks_found": self.blocks_found,
            "hashrate": self.hashrate,
            "uptime_seconds": self.uptime_seconds,
            "last_share_at": self.last_share_at,
            "last_error": self.last_error,
            "payout_target": self.payout_target,
            "fees_owed": self.fees_owed,
            "acceptance_rate": round(self.acceptance_rate, 4),
        }


class MiningProvider(ABC):
    """Orchestrates a mining session for one hardware/engine/model combo."""

    provider_id: str = ""

    @classmethod
    @abstractmethod
    def detect(
        cls, hw: Any, engine_id: str, model: str
    ) -> MiningCapabilities:
        """Whether this provider can run on the given combo.

        Pure inspection only — no subprocess, no network, no Docker.
        """
        raise NotImplementedError

    @abstractmethod
    def start(self, config: MiningConfig) -> dict[str, Any]:
        """Begin a mining session."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> dict[str, Any]:
        """End the session and return the final stats."""
        raise NotImplementedError

    @abstractmethod
    def stats(self) -> MiningStats:
        """Live counters for the running session."""
        raise NotImplementedError

    # -- shared helpers ------------------------------------------------------ #
    @staticmethod
    def _write_credential_file(content: dict[str, Any], prefix: str = "carla-mining") -> Path:
        """Write a credential file with owner-only permissions."""
        handle = tempfile.NamedTemporaryFile(
            mode="w", prefix=prefix, suffix=".json", delete=False
        )
        with handle:
            json.dump(content, handle)
        path = Path(handle.name)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return path


__all__ = [
    "MiningCapabilities",
    "MiningConfig",
    "MiningProvider",
    "MiningStats",
    "PoolTarget",
    "SoloTarget",
    "SubmitTarget",
]
