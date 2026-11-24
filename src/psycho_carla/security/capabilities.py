"""
Capability policy — which agents may use which tools.

The policy is a JSON (or TOML) file named by
``security.capabilities_policy_path``:

.. code-block:: json

    {
      "default_deny": false,
      "agents": {
        "morning_digest": {"allow": ["web_search", "web_fetch"]},
        "operative": {"deny": ["shell_exec"]}
      },
      "require_confirmation": ["shell_exec", "file_delete"]
    }

With ``default_deny`` on, an agent may only use tools in its allow list.
``security.default_deny`` in the main config is the global fallback when
the file does not say. When neither is set the policy permits everything
— C.A.R.L.A. is a local tool, and the sandbox/allowlist layer is the
boundary — but every decision is still computed here so the HUD and the
scan endpoint can show it.

Layer: security.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class CapabilityPolicy:
    default_deny: bool = False
    allow: dict[str, set[str]] = field(default_factory=dict)
    deny: dict[str, set[str]] = field(default_factory=dict)
    require_confirmation: set[str] = field(default_factory=set)

    # -- decisions ----------------------------------------------------------- #
    def check(self, agent: str, tool: str) -> tuple[bool, str]:
        """(allowed, reason) for one agent→tool pair."""
        if tool in self.deny.get(agent, set()) or tool in self.deny.get("*", set()):
            return False, f"'{tool}' is denied for agent '{agent}'"
        allow = self.allow.get(agent, self.allow.get("*", set()))
        if self.default_deny or (allow and self._agent_has_allow_list(agent)):
            if tool not in allow:
                return False, f"'{tool}' is not in the allow list for '{agent}'"
        return True, "allowed"

    def _agent_has_allow_list(self, agent: str) -> bool:
        return agent in self.allow or "*" in self.allow

    def filter_tools(self, agent: str, tools: Iterable[str]) -> list[str]:
        return [tool for tool in tools if self.check(agent, tool)[0]]

    def needs_confirmation(self, tool: str) -> bool:
        return tool in self.require_confirmation

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_deny": self.default_deny,
            "allow": {k: sorted(v) for k, v in self.allow.items()},
            "deny": {k: sorted(v) for k, v in self.deny.items()},
            "require_confirmation": sorted(self.require_confirmation),
        }


def _sets(value: Any) -> set[str]:
    if isinstance(value, dict):
        out: set[str] = set()
        for item in value.values():
            out |= _sets(item)
        return out
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value}
    return set()


def load_policy(config: Any = None) -> CapabilityPolicy:
    """Build the active policy from config (+ optional policy file)."""
    policy = CapabilityPolicy()
    security = getattr(config, "security", None) if config is not None else None
    if security is not None:
        policy.default_deny = bool(getattr(security, "default_deny", False))
        if getattr(security, "enforce_tool_confirmation", False):
            policy.require_confirmation |= {"shell_exec", "file_delete",
                                            "file_write", "memory_delete"}
    path = getattr(security, "capabilities_policy_path", "") if security else ""
    if path:
        file_path = Path(path).expanduser()
        if file_path.exists():
            try:
                if file_path.suffix == ".toml":
                    import tomllib

                    data = tomllib.loads(file_path.read_text(encoding="utf-8"))
                else:
                    data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if isinstance(data, dict):
                policy.default_deny = bool(
                    data.get("default_deny", policy.default_deny))
                agents = data.get("agents") or {}
                if isinstance(agents, dict):
                    for agent, rules in agents.items():
                        if not isinstance(rules, dict):
                            continue
                        if rules.get("allow"):
                            policy.allow[str(agent)] = _sets(rules["allow"])
                        if rules.get("deny"):
                            policy.deny[str(agent)] = _sets(rules["deny"])
                policy.require_confirmation |= _sets(
                    data.get("require_confirmation") or [])
    return policy


__all__ = ["CapabilityPolicy", "load_policy"]
