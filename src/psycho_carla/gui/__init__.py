"""
PSYCHO-C.A.R.L.A. — Desktop GUI client.

Upstream's ``jarvis gui`` starts a separate Node frontend and opens a
browser. PSYCHO-C.A.R.L.A. ships its HUD as a self-contained web
interface served by the Python process, so the equivalent here is:

  * :func:`launch_gui` — start the server if it is not already running,
    then open the HUD in the user's browser (or in an application-mode
    window, when a supported browser is available).
  * :func:`gui_status` — report whether the HUD is reachable.
  * :func:`export_desktop_bundle` — write a standalone ``.html`` bundle
    that opens straight from disk with no server at all.

The desktop-mode requirement is explicit: this is a desktop client for a
desktop screen. There is no mobile-specific layout.

Layer: Pillar 5 (Interface).
"""

from .launcher import (
    gui_status,
    hud_url,
    launch_gui,
    open_hud,
    server_is_up,
    wait_for_server,
)
from .desktop import export_desktop_bundle, desktop_bundle_path

__all__ = [
    "desktop_bundle_path",
    "export_desktop_bundle",
    "gui_status",
    "hud_url",
    "launch_gui",
    "open_hud",
    "server_is_up",
    "wait_for_server",
]
