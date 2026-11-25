"""
Runtime-bound tools.

Tools that act on live runtime state: memory aliases, channels, agents,
background sub-agents, approvals, skills, knowledge, voice, and the engine
itself. Outside a live runtime they explain exactly what is missing.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
from typing import Any

from ..registry import ToolRegistry, get_registry


def _runtime():
    from ...core.state import get_runtime

    return get_runtime()


def _need_runtime(tool: str) -> str:
    return (
        f"Error: {tool} needs a running runtime; none is active in this process. "
        "Use it via `carla serve`, the HUD, or the CLI."
    )


# --------------------------------------------------------------------------- #
# Memory aliases
# --------------------------------------------------------------------------- #


def memory_store(content: str, kind: str = "fact", tags: str = "") -> str:
    """Store a memory."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("memory_store")
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    memory_id = runtime.memory.store(content, kind=kind or "fact", tags=tag_list)
    return f"Stored memory #{memory_id}."


def memory_search(query: str, top_k: int = 5) -> str:
    """Search long-term memory."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("memory_search")
    results = runtime.memory.context_for(query, top_k=int(top_k))
    return results or "No memories matched."


def memory_get(memory_id: int) -> str:
    """Fetch one memory by id."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("memory_get")
    memory = runtime.memory.get(int(memory_id))
    if memory is None:
        return f"Error: no memory with id {memory_id}."
    return memory.summary() if hasattr(memory, "summary") else str(memory.content)


def memory_list(limit: int = 25, kind: str = "") -> str:
    """List recent memories."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("memory_list")
    memories = runtime.memory.list_memories(limit=int(limit), kind=kind or "")
    if not memories:
        return "No memories stored."
    return "\n".join(f"#{m.id} [{m.kind}] {(m.content or '')[:110]}" for m in memories)


def memory_forget(memory_id: int) -> str:
    """Delete one memory by id."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("memory_forget")
    deleted = runtime.memory.delete(int(memory_id))
    return (
        f"Deleted memory #{memory_id}."
        if deleted
        else f"Error: no memory with id {memory_id}."
    )


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def channel_message(channel: str, message: str, target: str = "") -> str:
    """Send a message out on a configured channel."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("channel_message")
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None or not channel:
        return "Error: no channel bridge is running."
    target = target or (runtime.config.channels.targets or {}).get(channel, "")
    if not target:
        return (
            f"Error: no target set for channel '{channel}'. Pass a target or set "
            "[channels.targets]."
        )
    result = bridge.send_to_target(channel, target, message)
    return f"Sent on {channel}." if result else f"Error: send on {channel} failed."


def channel_send(channel: str, text: str, target: str = "") -> str:
    """Send text on a channel (alias of channel_message)."""
    return channel_message(channel, text, target)


def channel_list() -> str:
    """List available channels."""
    from ...channels import available_channels

    names = [str(c["name"] if isinstance(c, dict) else c) for c in available_channels()]
    return f"{len(names)} channels: {', '.join(sorted(names))}"


def channel_status() -> str:
    """Channel bridge status."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("channel_status")
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is not None and hasattr(bridge, "status"):
        return json.dumps(bridge.status(), default=str)[:4000]
    return channel_list()


def channel_connect(name: str) -> str:
    """Note how to connect a channel (OAuth/token driven, no side effects)."""
    from ...channels import get_channel_class

    if get_channel_class(name) is None:
        return f"Error: unknown channel '{name}'."
    return (
        f"Channel '{name}' is registered. Configure its token under "
        f"[channels.tokens].{name} or in the HUD Settings tab, then restart "
        "the server to start polling."
    )


def channel_approve(code: str) -> str:
    """Record an OAuth pairing/approval code for a channel."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("channel_approve")
    approvals = getattr(runtime, "approvals", None)
    if approvals is None:
        return "Error: the approval store is not initialised."
    entry = approvals.queue(code, kind="channel", reason="channel pairing code")
    return f"Queued channel pairing as approval {entry.id}."


# --------------------------------------------------------------------------- #
# Agents / sub-agents
# --------------------------------------------------------------------------- #


def agent_spawn(command: str, cwd: str = "") -> str:
    """Spawn a coding-CLI agent process (queued for approval)."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("agent_spawn")
    entry = runtime.approvals.queue(
        command, kind="agent_spawn", reason="external agent process spawn"
    )
    return (
        f"Spawn request queued as approval {entry.id}: {command!r}. "
        "Approve it in the HUD Approvals panel before it runs."
    )


def agent_send(agent: str, message: str) -> str:
    """Send a message to a persistent agent instance (stateful, upstream name)."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("agent_send")
    if session_registry.get(agent) is None:
        instance = runtime.get_agent(agent)
        if instance is None:
            return f"Error: unknown agent '{agent}'."
        session_registry.set(agent, instance)
    return session_registry.chat(agent, message)


class _SessionRegistry:
    """Live name → BaseAgent instances for stateful multi-turn chats."""

    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}
        self._history: dict[str, list[Any]] = {}

    def get(self, name: str) -> Any:
        return self._sessions.get(name)

    def set(self, name: str, agent: Any) -> None:
        self._sessions[name] = agent
        self._history.setdefault(name, [])

    def chat(self, name: str, message: str) -> str:
        from ...providers.base import Message

        agent = self._sessions.get(name)
        if agent is None:
            return f"Error: no live session for '{name}'."
        history = self._history.setdefault(name, [])
        history.append(Message(role="user", content=message))
        result = agent.run(message, history=history)
        history.append(Message(role="assistant", content=result.text))
        return result.text

    def kill(self, name: str) -> bool:
        existed = name in self._sessions
        self._sessions.pop(name, None)
        self._history.pop(name, None)
        return existed

    def list(self) -> list[str]:
        return sorted(self._sessions)


session_registry = _SessionRegistry()


def agent_list() -> str:
    """List agents with live sessions; empty when no sessions are open.

    The agent catalog itself is always non-empty — this tool reports
    *sessions*, so an honest empty state means nobody spawned a
    persistent agent yet (use agent_send / agent_spawn to start one).
    """
    names = session_registry.list()
    if not names:
        return ("No live agent sessions. Use agent_message for one-shot "
                "runs or agent_send to open a persistent session.")
    return f"{len(names)} live agent sessions: {', '.join(names)}"


def agent_kill(agent: str) -> str:
    """Close a live agent session by name."""
    if session_registry.kill(agent):
        return f"Session '{agent}' closed."
    return f"Error: no live session named '{agent}'."


def agent_message(agent: str, message: str) -> str:
    """Run one message through a named registered agent."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("agent_message")
    instance = runtime.get_agent(agent)
    if instance is None:
        return f"Error: unknown agent '{agent}'."
    try:
        result = instance.run(message)
        text = getattr(result, "text", None) or getattr(result, "output", "")
        return str(text)[:8000]
    except Exception as exc:  # noqa: BLE001
        return f"Error: agent run failed: {exc}"


def agent_browser(command: str, target: str = "") -> str:
    """Drive the browser tool from an agent command."""
    from . import extended as _ext

    verb = (command or "").strip().lower()
    if verb.startswith("goto") or verb.startswith("open"):
        return _ext.browser_navigate(target or verb.split(" ", 1)[-1])
    if verb == "extract":
        return _ext.browser_extract()
    if verb == "axtree":
        return _ext.browser_axtree()
    return "Commands: 'goto <url>', 'extract', 'axtree'."


def _run_agent(runtime: Any, agent: str, task: str) -> str:
    instance = runtime.get_agent(agent)
    if instance is None:
        raise ValueError(f"unknown agent '{agent}'")
    result = instance.run(task)
    return getattr(result, "text", None) or getattr(result, "output", "")


def subagent_spawn(task: str, agent: str = "operative") -> str:
    """Spawn a sub-agent run in the background and return its id."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("subagent_spawn")
    from ...core.state import background_tasks

    handle = background_tasks.start(
        f"subagent:{agent}", lambda: _run_agent(runtime, agent, task)
    )
    return f"Sub-agent {agent!r} started as task #{handle} for: {task[:80]}"


def subagent_list() -> str:
    """List background sub-agent tasks."""
    from ...core.state import background_tasks

    tasks = background_tasks.list()
    if not tasks:
        return "No background tasks."
    return "\n".join(f"#{t['id']} {t['label']} — {t['status']}" for t in tasks)


def subagent_collect(task_id: int) -> str:
    """Collect the result of a finished background task."""
    from ...core.state import background_tasks

    task = background_tasks.get(int(task_id))
    if task is None:
        return f"Error: no background task #{task_id}."
    if task["status"] == "running":
        return f"Task #{task_id} is still running."
    if task["status"] == "error":
        return f"Task #{task_id} failed: {task.get('error', 'unknown error')}"
    return str(task.get("result") or "(no output)")[:8000]


def delegate(agent: str, task: str) -> str:
    """Delegate a task to a registered agent and return its report."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("delegate")
    instance = runtime.get_agent(agent)
    if instance is None:
        return f"Error: unknown agent '{agent}'."
    result = instance.run(task)
    return getattr(result, "text", None) or getattr(result, "output", "")


def delegate_to_coworker(agent: str, task: str) -> str:
    """Delegate to a coworker agent (upstream alias of delegate)."""
    return delegate(agent, task)


def local_tool_autonomy(task: str) -> str:
    """Plan local tool use for a task and execute the plan's first steps."""
    return agent_message("operative", f"Use local tools to complete: {task}")


def run_tool(tool: str, arguments: str = "{}") -> str:
    """Run any registered tool by name with JSON arguments."""
    try:
        args = json.loads(arguments or "{}")
    except Exception:  # noqa: BLE001
        return "Error: arguments must be a JSON object."
    result = get_registry().execute(tool, args)
    return (result.content or "") if result.ok else f"Error: {result.error}"


# --------------------------------------------------------------------------- #
# Approvals / skills / knowledge / voice / engine
# --------------------------------------------------------------------------- #


def approval_action(approval_id: str, action: str = "approve") -> str:
    """Reject a queued approval (only the owner can approve)."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("approval_action")
    approve = action == "approve"
    if approve:
        # Safe mode only means something if an agent cannot approve itself.
        return ("Error: approvals can only be granted by the owner, in the HUD "
                "Approvals panel — not by an agent.")
    entry = runtime.approvals.decide(str(approval_id), False)
    if entry is None:
        return f"Error: no approval {approval_id}."
    return f"Approval {approval_id}: {entry.status}."


def skills_list() -> str:
    """List installed skills."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("skills_list")
    skills = runtime.skills.list()
    if not skills:
        return "No skills installed."
    return "\n".join(f"{s.name} — {s.description}" for s in skills)


def knowledge_search(query: str, top_k: int = 5) -> str:
    """Search the synced connector knowledge base."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("knowledge_search")
    try:
        results = runtime.knowledge_search(query, limit=int(top_k))
    except Exception as exc:  # noqa: BLE001
        return f"Error: knowledge search failed: {exc}"
    if not results:
        return "No knowledge matches. Sync a connector first (carla connect sync)."
    if isinstance(results, str):
        return results
    lines = []
    for item in results:
        if isinstance(item, dict):
            lines.append(
                f"[{item.get('connector', '?')}] {item.get('title', '')} "
                f"{str(item.get('text', ''))[:160]}"
            )
        else:
            lines.append(str(item)[:220])
    return "\n".join(lines)


def voice_speak(text: str) -> str:
    """Speak text aloud via the voice adapter (barge-in capable)."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("voice_speak")
    voice = getattr(runtime, "voice", None)
    if voice is None:
        return _need_runtime("voice_speak")
    speak = getattr(voice, "speak", None)
    if speak is None:
        return "Error: the voice adapter has no speak() method."
    result = speak(text)
    return str(result or "Spoken.")


def llm_generate(prompt: str) -> str:
    """One-shot generation through the active engine routing."""
    runtime = _runtime()
    if runtime is None:
        return _need_runtime("llm_generate")
    from ...providers.base import Message

    try:
        result = runtime.router.generate(
            [Message(role="user", content=prompt)], capability_tier="mid"
        )
        return result.content or "(empty response)"
    except Exception as exc:  # noqa: BLE001
        return f"Error: generation failed: {exc}"


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def _s(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()

    reg.register("memory_store", memory_store, "Store a memory.", _s({"content": {"type": "string"}, "kind": {"type": "string"}, "tags": {"type": "string"}}, ["content"]), namespace="memory")
    reg.register("memory_search", memory_search, "Search long-term memory.", _s({"query": {"type": "string"}, "top_k": {"type": "integer"}}, ["query"]), namespace="memory")
    reg.register("memory_get", memory_get, "Fetch one memory by id.", _s({"memory_id": {"type": "integer"}}, ["memory_id"]), namespace="memory")
    reg.register("memory_list", memory_list, "List recent memories.", _s({"limit": {"type": "integer"}, "kind": {"type": "string"}}), namespace="memory")
    reg.register("memory_forget", memory_forget, "Delete one memory by id.", _s({"memory_id": {"type": "integer"}}, ["memory_id"]), namespace="memory", dangerous=True, requires_approval=True)
    reg.register("channel_message", channel_message, "Send a message out on a configured channel.", _s({"channel": {"type": "string"}, "message": {"type": "string"}, "target": {"type": "string"}}, ["channel", "message"]), namespace="channels")
    reg.register("channel_send", channel_send, "Send text on a channel.", _s({"channel": {"type": "string"}, "text": {"type": "string"}, "target": {"type": "string"}}, ["channel", "text"]), namespace="channels")
    reg.register("channel_list", channel_list, "List available channels.", _s({}), namespace="channels")
    reg.register("channel_status", channel_status, "Channel bridge status.", _s({}), namespace="channels")
    reg.register("channel_connect", channel_connect, "How to connect a channel.", _s({"name": {"type": "string"}}, ["name"]), namespace="channels")
    reg.register("channel_approve", channel_approve, "Record an OAuth pairing code as an approval.", _s({"code": {"type": "string"}}, ["code"]), namespace="channels")
    reg.register("agent_spawn", agent_spawn, "Queue a coding-CLI agent spawn for approval.", _s({"command": {"type": "string"}, "cwd": {"type": "string"}}, ["command"]), namespace="agents", dangerous=True, requires_approval=True)
    reg.register("agent_send", agent_send, "Send a message to a persistent agent session.", _s({"agent": {"type": "string"}, "message": {"type": "string"}}, ["agent", "message"]), namespace="agents")
    reg.register("agent_list", agent_list, "List live agent sessions.", _s({}), namespace="agents")
    reg.register("agent_kill", agent_kill, "Close a live agent session.", _s({"agent": {"type": "string"}}, ["agent"]), namespace="agents")
    reg.register("agent_message", agent_message, "Run one message through a named agent.", _s({"agent": {"type": "string"}, "message": {"type": "string"}}, ["agent", "message"]), namespace="agents")
    reg.register("agent_browser", agent_browser, "Drive the browser: goto/extract/axtree.", _s({"command": {"type": "string"}, "target": {"type": "string"}}, ["command"]), namespace="agents")
    reg.register("subagent_spawn", subagent_spawn, "Spawn a sub-agent in the background.", _s({"task": {"type": "string"}, "agent": {"type": "string"}}, ["task"]), namespace="agents")
    reg.register("subagent_list", subagent_list, "List background sub-agent tasks.", _s({}), namespace="agents")
    reg.register("subagent_collect", subagent_collect, "Collect a finished background task's result.", _s({"task_id": {"type": "integer"}}, ["task_id"]), namespace="agents")
    reg.register("delegate", delegate, "Delegate a task to a registered agent.", _s({"agent": {"type": "string"}, "task": {"type": "string"}}, ["agent", "task"]), namespace="agents")
    reg.register("delegate_to_coworker", delegate_to_coworker, "Delegate to a coworker agent.", _s({"agent": {"type": "string"}, "task": {"type": "string"}}, ["agent", "task"]), namespace="agents")
    reg.register("local_tool_autonomy", local_tool_autonomy, "Autonomous local tool run for a task.", _s({"task": {"type": "string"}}, ["task"]), namespace="agents")
    reg.register("run_tool", run_tool, "Run any registered tool by name with JSON arguments.", _s({"tool": {"type": "string"}, "arguments": {"type": "string"}}, ["tool"]), namespace="core")
    reg.register("approval_action", approval_action, "Approve or reject a queued approval.", _s({"approval_id": {"type": "string"}, "action": {"type": "string"}}, ["approval_id"]), namespace="ops")
    reg.register("skills_list", skills_list, "List installed skills.", _s({}), namespace="skills")
    reg.register("knowledge_search", knowledge_search, "Search the synced connector knowledge base.", _s({"query": {"type": "string"}, "top_k": {"type": "integer"}}, ["query"]), namespace="knowledge")
    reg.register("voice_speak", voice_speak, "Speak text aloud.", _s({"text": {"type": "string"}}, ["text"]), namespace="voice")
    reg.register("llm_generate", llm_generate, "One-shot generation via the active engine.", _s({"prompt": {"type": "string"}}, ["prompt"]), namespace="core")
    return reg


register()

__all__ = [
    "agent_browser",
    "agent_kill",
    "agent_list",
    "agent_message",
    "agent_send",
    "agent_spawn",
    "approval_action",
    "channel_approve",
    "channel_connect",
    "channel_list",
    "channel_message",
    "channel_send",
    "channel_status",
    "delegate",
    "delegate_to_coworker",
    "knowledge_search",
    "llm_generate",
    "local_tool_autonomy",
    "memory_forget",
    "memory_get",
    "memory_list",
    "memory_search",
    "memory_store",
    "register",
    "run_tool",
    "session_registry",
    "skills_list",
    "subagent_collect",
    "subagent_list",
    "subagent_spawn",
    "voice_speak",
]
