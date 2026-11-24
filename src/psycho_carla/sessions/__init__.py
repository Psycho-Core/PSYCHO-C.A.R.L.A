"""
PSYCHO-C.A.R.L.A. — Cross-channel sessions.

A session is one conversation, identified independently of the channel it
arrived on. The same person talking through the HUD, the CLI and a
connector shares one session — that is the point of a
`SessionIdentity` carrying a map of channel-specific ids.

Two behaviours make a long session usable:

  * **Consolidation** — when a session grows past a threshold, the oldest
    messages are summarised into a single anchor message, so the model sees
    a coherent history rather than a truncated one.
  * **Decay** — sessions that have not been touched within a maximum age
    are eligible for expiry, so an idle installation does not accumulate
    state forever.

Layer: Pillar 4 (Tools & Memory).
"""

from .compression import consolidate_messages, estimate_tokens
from .session import (
    Session,
    SessionIdentity,
    SessionMessage,
    SessionStore,
)

__all__ = [
    "Session",
    "SessionIdentity",
    "SessionMessage",
    "SessionStore",
    "consolidate_messages",
    "estimate_tokens",
]
