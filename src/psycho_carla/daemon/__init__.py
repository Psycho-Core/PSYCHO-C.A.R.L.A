"""
PSYCHO-C.A.R.L.A. — Daemon service.

Runs the console as a background service rather than a foreground process,
so the HUD is reachable without a terminal window open.

  * :mod:`.service` — generates the platform service files (systemd unit on
    Linux, launchd plist on macOS, a scheduled task on Windows)
  * :mod:`.gateway` — the daemon itself: owns the runtime, the scheduler and
    the HTTP server, and shuts them down cleanly

Layer: Pillar 1 (Intelligence) — the process lifecycle around the system.
"""

from .gateway import GatewayDaemon
from .service import (
    generate_launchd_plist,
    generate_systemd_service,
    generate_windows_task,
    install_service,
    service_instructions,
    uninstall_service,
)

__all__ = [
    "GatewayDaemon",
    "generate_launchd_plist",
    "generate_systemd_service",
    "generate_windows_task",
    "install_service",
    "service_instructions",
    "uninstall_service",
]
