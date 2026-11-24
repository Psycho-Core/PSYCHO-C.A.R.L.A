"""
PSYCHO-C.A.R.L.A. — A2A (Agent-to-Agent) protocol.

Implements the Google A2A specification over JSON-RPC 2.0, matching
upstream OpenJarvis's `a2a/` subpackage:

  * :mod:`.protocol` — wire types: ``AgentCard``, ``A2ATask``, ``TaskState``,
    ``A2ARequest``, ``A2AResponse``
  * :mod:`.server` — serves agents at ``/.well-known/agent.json`` and
    ``/a2a/tasks``
  * :mod:`.client` — discovers and calls remote A2A agents
  * :mod:`.tool` — wraps a remote A2A agent as an ordinary C.A.R.L.A.
    tool, so another agent can call it

This is how C.A.R.L.A. talks to *other* agents: MCP is for tools, A2A is
for agents. An external agent can be called as a tool, and C.A.R.L.A. can
itself be called by one.

Layer: Pillar 2 (Engine) — the inter-agent boundary.
"""

from .client import A2AClient
from .protocol import (
    A2AError,
    A2ARequest,
    A2AResponse,
    A2ATask,
    AgentCard,
    TaskState,
)
from .server import A2AServer
from .tool import A2AAgentTool

__all__ = [
    "A2AAgentTool",
    "A2AClient",
    "A2AError",
    "A2ARequest",
    "A2AResponse",
    "A2AServer",
    "A2ATask",
    "AgentCard",
    "TaskState",
]
