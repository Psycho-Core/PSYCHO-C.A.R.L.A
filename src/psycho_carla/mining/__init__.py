"""
PSYCHO-C.A.R.L.A. — Mining subsystem.

An optional subsystem: it uses idle GPU/CPU capacity to mine a
privacy-preserving proof-of-work token. It is **off by default** and every
provider degrades gracefully — if the mining extras are not installed, the
provider reports why rather than raising at import time.

  * :mod:`.types` — the provider ABC, capability descriptor, config and stats
  * :mod:`.providers` — the CPU / vLLM / Apple MPS provider implementations
  * :mod:`.registry` — provider discovery and selection

Nothing in the core runtime depends on this package.

Layer: Pillar 2 (Model) — the optional compute-reuse path.
"""

from .types import (
    MiningCapabilities,
    MiningConfig,
    MiningProvider,
    MiningStats,
    PoolTarget,
    SoloTarget,
    SubmitTarget,
)
from .registry import (
    available_providers,
    detect_all,
    get_provider,
    register_provider,
    select_provider,
)

__all__ = [
    "MiningCapabilities",
    "MiningConfig",
    "MiningProvider",
    "MiningStats",
    "PoolTarget",
    "SoloTarget",
    "SubmitTarget",
    "available_providers",
    "detect_all",
    "get_provider",
    "register_provider",
    "select_provider",
]
