"""
PSYCHO-C.A.R.L.A. — Runtime.

The single object that wires all five primitives together:

  Intelligence (router) · Engine (adapters) · Agents (registry + scheduler)
  Tools & Memory (registry + store + MCP) · Learning (traces + optimizer)

This is what `carla serve`, the CLI, and the SDK all talk to.

Layer: Core.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable, Iterable, Sequence

from ..agents.base import AgentResult, BaseAgent
from ..agents.builtin import BUILTIN_AGENTS
from ..agents.registry import AgentRegistry
from ..agents.scheduler import ContinuousLoop, CronSchedule, ScheduledJob, Scheduler
from ..core.config import CarlaConfig, load_config, set_active_config
from ..core.persona import Persona
from ..core.telemetry import Telemetry, hardware_summary
from ..learning.traces import TraceLogger
from ..memory.service import MemoryService, build_model_extractor
from ..memory.store import Embedder, MemoryStore
from ..providers.router import ModelRouter
from ..tools.mcp.client import MCPManager
from ..tools.registry import ToolRegistry, get_registry, load_builtin_tools
from ..voice.voice import VoiceSystem


class CarlaRuntime:
    """The C.A.R.L.A. runtime: config, router, tools, memory, agents, voice."""

    def __init__(
        self,
        config: CarlaConfig | None = None,
        *,
        with_voice: bool = True,
        with_mcp: bool = True,
    ) -> None:
        self.config = config or load_config()
        set_active_config(self.config)

        # -- Pillar 4: Tools & Memory ------------------------------------- #
        self.tools: ToolRegistry = get_registry()
        load_builtin_tools(self.tools)
        self._configure_tools()

        embedder = Embedder(
            dim=self.config.tools.storage.vector_dim,
            backend=(
                "sentence_transformers"
                if self.config.tools.storage.embedding_model
                else "hash"
            ),
            model=self.config.tools.storage.embedding_model,
        )
        self.memory = MemoryStore(self.config.memory_db, embedder)
        self.memory_service = MemoryService(
            self.memory,
            enabled=self.config.memory.enabled,
            max_facts=self.config.memory.max_facts,
        )

        # -- Pillar 1/2: Intelligence + Engine ----------------------------- #
        self.telemetry = Telemetry(
            self.config.telemetry_db,
            enabled=self.config.telemetry.enabled,
            sample_interval=self.config.telemetry.sample_interval,
            track_resources=self.config.telemetry.track_resources,
            track_energy=getattr(self.config.telemetry, "track_energy", True),
            energy_vendor=getattr(self.config.telemetry, "energy_vendor", "auto"),
        )
        self.router = ModelRouter(self.config, self.telemetry)

        # -- Pillar 3: Agents ------------------------------------------------ #
        self.persona = Persona(self.config.persona)
        self.agents = AgentRegistry()
        self.agents.register_all(BUILTIN_AGENTS)
        self.agents.set_overrides(self.config.agent.agents)

        # -- Pillar 5: Learning ---------------------------------------------- #
        self.traces = TraceLogger(
            self.config.traces_db, enabled=self.config.traces.enabled
        )
        # Four learning surfaces, each independently switchable via
        # [learning.<surface>].policy in the TOML.
        from ..learning.optimizer import PromptOptimizer
        from ..learning.surfaces import EngineSpecOptimizer, ToolSelectionOptimizer

        self.optimizer = PromptOptimizer(self.traces, self.router)
        self.tool_optimizer = ToolSelectionOptimizer(self.traces, self.tools)
        self.engine_optimizer = EngineSpecOptimizer(self.traces, self.config)
        self._attach_memory_extractor()

        # -- Audit log (security.merkle_audit / audit_log_path) ------------------ #
        self.audit = None
        if self.config.security.merkle_audit or self.config.security.audit_log_path:
            try:
                from ..security.audit import AuditLogger

                self.audit = AuditLogger(
                    self.config.security.audit_log_path or None)
            except Exception:
                self.audit = None

        # -- Approval queue (proactive actions wait for a human) ----------------- #
        from ..security.approvals import ApprovalStore

        self.approvals = ApprovalStore(self.config.data_dir / "approvals.json")

        # -- Cross-channel sessions -------------------------------------------- #
        self.sessions = None
        if self.config.sessions.enabled:
            try:
                from ..sessions import SessionStore

                self.sessions = SessionStore(
                    self.config.sessions_db,
                    max_age_hours=self.config.sessions.max_age_hours,
                    consolidation_threshold=(
                        self.config.sessions.consolidation_threshold
                    ),
                    max_message_tokens=self.config.sessions.max_message_tokens,
                )
            except Exception:
                self.sessions = None

        # -- Channels ---------------------------------------------------------- #
        # Messaging platforms. The bridge is what actually routes messages;
        # this attribute is None when no channel is configured.
        self.channel_bridge = None
        self.channels: dict[str, Any] = {}
        if self.config.channels.enabled and self.config.channels.channels:
            try:
                from ..channels import ChannelBridge, create_channel

                for channel_id, options in self.config.channels.channels.items():
                    adapter = create_channel(channel_id, dict(options))
                    if adapter is None:
                        continue
                    self.channels[channel_id] = adapter

                if self.channels:
                    self.channel_bridge = ChannelBridge(
                        self.channels,
                        runtime=self,
                        session_store=self.sessions,
                        max_length=self.config.channels.max_length,
                    )
                    if self.config.channels.default_agent:
                        self.channel_bridge.set_default_agent(
                            self.config.channels.default_agent
                        )
            except Exception:
                # A misconfigured channel must not stop the console, but the
                # reason must be visible — otherwise the user sees "no
                # channels" with no explanation.
                import logging

                logging.getLogger("psycho_carla.runtime").warning(
                    "Channels could not be initialised", exc_info=True
                )
                self.channel_bridge = None
                self.channels = {}

        # -- Skills catalog (agentskills.io format) -------------------------- #
        from ..tools.builtin.skill_tools import install_skill_tools

        self.skills = None
        if self.config.skills.enabled:
            try:
                from ..skills import SkillStore
                from ..skills.sources import ensure_bundled

                self.skills = SkillStore(self.config.skills_dir)
                ensure_bundled(self.skills, self.config)
                install_skill_tools(self.tools, self)
            except Exception:
                self.skills = None

        # -- Connectors / knowledge store -------------------------------------- #
        # Synced connector documents land here; `knowledge_search` queries it.
        self.knowledge = None
        try:
            from ..connectors import KnowledgeStore

            self.knowledge = KnowledgeStore(self.config.data_dir / "knowledge.db")
        except Exception:
            self.knowledge = None

        # -- MCP -------------------------------------------------------------- #
        self.mcp = MCPManager()
        if with_mcp and self.config.tools.mcp.enabled:
            self.mcp.configure(self.config.tools.mcp.servers, enabled=True)
            self.mcp.attach(self.tools)

        # -- Runtime-bound tools (memory + cron need a live handle) --------- #
        from ..tools.builtin.memory_tools import install_runtime_tools

        install_runtime_tools(self.tools, self)

        # -- Scheduling -------------------------------------------------------- #
        self.scheduler = Scheduler(
            runner=self._run_scheduled_job,
            on_event=self._on_scheduler_event,
        )
        self.loops: dict[str, ContinuousLoop] = {}

        # -- Voice -------------------------------------------------------------- #
        self.voice = VoiceSystem(self.config, self.persona) if with_voice else None

        # -- Event bus ----------------------------------------------------------- #
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._lock = threading.RLock()
        self._started = False
        # Optional WebSocket hub, attached by the server layer. Runtime code
        # calls `self.broadcast(...)` so it works with or without a hub.
        self.hub: Any = None

    # ------------------------------------------------------------------ #
    # Configuration helpers
    # ------------------------------------------------------------------ #
    def _configure_tools(self) -> None:
        from ..tools.builtin import code as code_tools
        from ..tools.builtin import files as file_tools
        from ..tools.builtin import shell as shell_tools
        from ..tools.builtin import web as web_tools

        file_tools.configure_file_sandbox(
            root=self.config.security.sandbox_paths[0] if self.config.security.sandbox_paths else None,
            allowed_paths=self.config.security.sandbox_paths,
        )
        shell_tools.configure_shell(
            allow=self.config.security.allow_shell,
            timeout=self.config.security.shell_timeout,
            allowed_commands=self.config.security.allowed_commands,
        )
        ws = self.config.web_search
        api_key = ""
        if ws.api_key_env:
            import os

            api_key = os.environ.get(ws.api_key_env, "")
        web_tools.configure_web_search(
            provider=ws.provider, api_key=api_key, base_url=ws.base_url,
            max_results=ws.max_results, timeout=ws.timeout,
            ssrf_protection=self.config.security.ssrf_protection,
        )
        code_tools.configure_code(timeout=self.config.security.shell_timeout)

        if self.config.tools.enabled:
            self.tools.set_enabled(self.config.tools.enabled)
        if self.config.tools.disabled:
            self.tools.set_disabled(self.config.tools.disabled)

    def _attach_memory_extractor(self) -> None:
        if not self.config.memory.enabled:
            return
        if self.config.memory.extraction_model or self.config.intelligence.tiers:
            try:
                tier = self.config.memory.extraction_model or \
                    self.config.intelligence.default_tier
                self.memory_service.set_extractor(
                    build_model_extractor(self.router, tier)
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Start background services (telemetry, scheduler, MCP, voice)."""
        if self._started:
            return
        self._started = True
        from .state import set_runtime

        set_runtime(self)
        self.telemetry.start()
        self.scheduler.start()
        self._load_config_cron()

        # Bring the messaging channels online. A channel that cannot
        # connect reports its own status and is left for the user to
        # inspect — it must not stop the console from starting.
        if self.channel_bridge is not None:
            try:
                self.channel_bridge.connect()
            except Exception:
                pass
        if self.mcp.servers:
            self.mcp.connect_all()
        if self.voice:
            try:
                self.voice.start()
            except Exception:
                pass
        self.emit("runtime_started", {"tiers": self.router.list_tiers()})

    def _load_config_cron(self) -> None:
        """Register the scheduled jobs declared in the configuration file."""
        cron = getattr(self.config, "cron", None)
        if cron is None or not getattr(cron, "enabled", True):
            return
        for job in getattr(cron, "jobs", []) or []:
            if not job.enabled or not job.schedule:
                continue
            try:
                self.scheduler.add(
                    job.name or job.schedule,
                    job.schedule,
                    agent=job.agent,
                    prompt=job.prompt,
                    tier=job.tier or None,
                    speak=job.speak,
                )
            except Exception:
                # A malformed cron expression should not stop the runtime.
                continue

    # -- Learning ----------------------------------------------------------- #
    def optimize(self, surface: str = "", **kwargs: Any) -> dict[str, Any]:
        """Run a learning optimiser for one surface (or all of them).

        Each surface is gated by ``[learning.<surface>].policy``. Passing an
        explicit ``surface`` overrides the switch so you can always run an
        optimiser on demand.
        """
        learning = self.config.learning
        results: dict[str, Any] = {}

        def _enabled(name: str) -> bool:
            if surface:
                return True
            return learning.surface_enabled(name)

        if surface in ("", "prompt", "intelligence"):
            if _enabled("intelligence"):
                results["prompt"] = self.optimizer.optimize_prompt(
                    agent=kwargs.get("agent", "")
                ).to_dict()
        if surface in ("", "routing"):
            if _enabled("routing"):
                policies = self.optimizer.optimize_routing(
                    min_samples=kwargs.get("min_samples", 5)
                )
                results["routing"] = {k: v.to_dict() for k, v in policies.items()}
        if surface in ("", "tools", "tool_selection"):
            if _enabled("tools") or _enabled("agent"):
                policies = self.tool_optimizer.optimize_tool_selection(
                    agent=kwargs.get("agent", "")
                )
                results["tool_selection"] = {
                    k: v.to_dict() for k, v in policies.items()
                }
        if surface in ("", "engine", "engine_spec"):
            if _enabled("engine"):
                specs = self.engine_optimizer.optimize_engine_spec(
                    tier=kwargs.get("tier", "")
                )
                results["engine_spec"] = {k: v.to_dict() for k, v in specs.items()}
        return results

    def stop(self) -> None:
        """Stop all background services and release resources."""
        for loop in list(self.loops.values()):
            loop.stop()
        self.loops.clear()
        self.scheduler.stop()
        if self.channel_bridge is not None:
            try:
                self.channel_bridge.disconnect()
            except Exception:
                pass
        self.mcp.disconnect_all()
        if self.voice:
            try:
                self.voice.stop()
            except Exception:
                pass
        self.telemetry.stop()
        self.memory.close()
        self.traces.close()
        self._started = False
        self.emit("runtime_stopped", {})

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "CarlaRuntime":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    def on(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """Publish an event to listeners and the WebSocket hub."""
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event, data)
            except Exception:
                continue
        # Also push to the WebSocket hub, if one is attached.
        if self.hub is not None:
            try:
                self.hub.broadcast(event, data)
            except Exception:
                pass

    def broadcast(self, event: str, data: dict[str, Any]) -> None:
        """Publish an event. Alias of `emit` for API symmetry."""
        self.emit(event, data)

    def _on_scheduler_event(self, event: str, data: dict[str, Any]) -> None:
        self.emit(event, data)

    # ------------------------------------------------------------------ #
    # Agent dispatch
    # ------------------------------------------------------------------ #
    def get_agent(self, name: str) -> BaseAgent | None:
        agent = self.agents.create(
            name, self.config, self.router, self.tools, self.memory, self.persona
        )
        if agent is not None and self.skills is not None:
            # Progressive disclosure: only skill metadata enters the prompt.
            agent.skills_prompt = self._skills_prompt
        return agent

    def _skills_prompt(self) -> str:
        """The skills catalog block for the agent's system prompt."""
        if self.skills is None or not self.config.skills.auto_load:
            return ""
        catalog = self.skills.catalog
        if not len(catalog):
            return ""
        block = catalog.metadata_prompt()
        if not block:
            return ""
        return (
            f"{block}\n\n"
            "If a skill above applies to the task, call skill_load with its "
            "name before proceeding. If none clearly applies, ignore this list."
        )

    def list_agents(self) -> list[dict[str, Any]]:
        return self.agents.describe()

    def ask(
        self,
        prompt: str,
        *,
        agent: str = "",
        tier: str = "",
        session_id: str = "default",
        history: Sequence[Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Run a prompt through an agent and return a structured result."""
        agent_name = agent or self.config.agent.default_agent
        instance = self.get_agent(agent_name)
        if instance is None:
            return {
                "text": f"Unknown agent '{agent_name}'.",
                "speech": f"Unknown agent '{agent_name}', {self.persona.boss}.",
                "display": f"Unknown agent '{agent_name}'.",
                "error": f"unknown_agent:{agent_name}",
                "agent": agent_name,
            }

        msgs = list(history or [])
        if msgs:
            kwargs["history"] = msgs
        started = time.perf_counter()
        try:
            result = instance.run(
                prompt, session_id=session_id, tier=tier or instance.tier, **kwargs
            )
        except Exception as exc:
            self.emit("agent_error", {"agent": agent_name, "error": str(exc)})
            return {
                "text": f"Error: {exc}",
                "speech": self.persona.salutation("error"),
                "display": f"Error: {exc}",
                "error": str(exc),
                "agent": agent_name,
            }
        elapsed = (time.perf_counter() - started) * 1000

        # Persist the exchange.
        try:
            self.memory.record_message(session_id, "user", prompt)
            self.memory.record_message(session_id, "assistant", result.text)
            if self.config.memory.enabled:
                self.memory_service.observe(prompt, result.text)
        except Exception:
            pass

        # Record it in the cross-channel session store too.
        try:
            if self.sessions is not None:
                if self.sessions.get(session_id) is None:
                    self.sessions.create(session_id)
                self.sessions.add_message(session_id, "user", prompt)
                self.sessions.add_message(session_id, "assistant", result.text)
        except Exception:
            pass

        # Trace it for the learning loop.
        try:
            self.traces.log_exchange(
                prompt=prompt,
                response=result.text,
                agent=agent_name,
                tier=result.tier,
                latency_ms=elapsed,
                usage=result.usage,
                tool_calls=result.tool_calls,
            )
        except Exception:
            pass

        # Tamper-evident audit trail, when enabled.
        if self.audit is not None:
            try:
                self.audit.record(
                    "ask", agent=agent_name, tier=result.tier,
                    tools=[c.get("name", "") for c in result.tool_calls],
                    error=result.error or "",
                )
            except Exception:
                pass

        payload = result.to_dict()
        payload["session_id"] = session_id
        self.emit("agent_response", payload)
        return payload

    def ask_stream(
        self,
        prompt: str,
        *,
        agent: str = "",
        tier: str = "",
        session_id: str = "default",
        **kwargs: Any,
    ):
        """Yield response chunks. Falls back to a single chunk for non-streaming agents."""
        agent_name = agent or self.config.agent.default_agent
        instance = self.get_agent(agent_name)
        if instance is None:
            yield {"type": "error", "content": f"Unknown agent '{agent_name}'."}
            return

        from ..providers.base import Message

        messages = [
            Message(role="system", content=instance.system_prompt()),
            Message(role="user", content=prompt),
        ]
        schemas = instance._tool_schemas_for()  # noqa: SLF001
        accumulated = ""
        for chunk in self.router.stream(
            messages, tier or instance.tier, agent=agent_name, tools=schemas or None,
            **kwargs
        ):
            if chunk.error:
                yield {"type": "error", "content": chunk.error}
                return
            if chunk.text:
                accumulated += chunk.text
                yield {"type": "token", "content": chunk.text}
        if accumulated:
            channel = self.persona.split(accumulated)
            yield {"type": "final", "content": accumulated, "speech": channel.speech,
                   "code_blocks": channel.code_blocks}

    # ------------------------------------------------------------------ #
    # Scheduling
    # ------------------------------------------------------------------ #
    def add_job(
        self,
        name: str,
        schedule: str,
        agent: str = "morning_digest",
        prompt: str = "",
        tier: str = "",
        speak: bool = True,
    ) -> ScheduledJob:
        return self.scheduler.add(
            name, schedule, agent=agent, prompt=prompt, tier=tier, speak=speak
        )

    def list_jobs(self) -> list[dict[str, Any]]:
        return [j.to_dict() for j in self.scheduler.list_jobs()]

    def remove_job(self, job_id: str) -> bool:
        return self.scheduler.remove(job_id)

    def _run_scheduled_job(self, job: ScheduledJob) -> dict[str, Any]:
        prompt = job.prompt or f"Run the {job.name} scheduled task."
        result = self.ask(
            prompt, agent=job.agent, tier=job.tier,
            session_id=f"cron:{job.id}",
        )
        if job.speak and self.voice and result.get("speech"):
            try:
                self.voice.speak(result["speech"])
            except Exception:
                pass
        self.emit("cron_result", {"job": job.to_dict(), "result": result})
        return result

    def start_loop(
        self,
        agent_name: str,
        prompt: str,
        *,
        interval: float = 300.0,
        tier: str = "",
        key: str = "",
    ) -> ContinuousLoop:
        loop = ContinuousLoop(
            agent_name,
            prompt,
            interval=interval,
            tier=tier,
            runner=self._run_loop_cycle,
            on_event=lambda e, d: self.emit(e, d),
        )
        self.loops[key or agent_name] = loop
        loop.start()
        return loop

    def _run_loop_cycle(self, agent_name: str, prompt: str, state: Any) -> dict[str, Any]:
        result = self.ask(
            prompt, agent=agent_name, tier="",
            session_id=f"loop:{agent_name}",
        )
        try:
            self.memory.store(
                f"[{agent_name} loop] {result.get('text', '')[:600]}",
                kind="event",
                tags=["loop", agent_name],
                source="loop",
            )
        except Exception:
            pass
        return result

    def stop_loop(self, key: str) -> bool:
        loop = self.loops.pop(key, None)
        if loop:
            loop.stop()
            return True
        return False

    # ------------------------------------------------------------------ #
    # Memory passthrough
    # ------------------------------------------------------------------ #
    def sync_connector(self, connector_id: str, **kwargs: Any) -> int:
        """Sync one connector into the knowledge store. Returns doc count."""
        if self.knowledge is None:
            raise RuntimeError("The knowledge store is not available.")
        from ..connectors import sync_connector as _sync

        return _sync(connector_id, self.knowledge, **kwargs)

    def knowledge_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Search synced connector documents."""
        if self.knowledge is None:
            return []
        return [d.to_dict() for d in self.knowledge.search(query, top_k=top_k)]

    def memory_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.memory.search(query, top_k=top_k)]

    def memory_store(self, content: str, tags: list[str] | None = None) -> int:
        return self.memory.store(content, tags=tags, source="user")

    def memory_list(self, limit: int = 50) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.memory.list_memories(limit=limit)]

    def memory_delete(self, memory_id: int) -> bool:
        return self.memory.delete(memory_id)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        return {
            "app": "PSYCHO-C.A.R.L.A.",
            "version": "1.0.0",
            "assistant_name": self.persona.name,
            "user_name": self.persona.boss,
            "started": self._started,
            "config_path": str(self.config.config_path),
            "home": str(self.config.home),
            "active_tier": self.router.active_tier,
            "tiers": self.router.list_tiers(),
            "agents": self.list_agents(),
            "tools": self.tools.describe(),
            "memory": self.memory.stats(),
            "telemetry": self.telemetry.summary(),
            "jobs": self.list_jobs(),
            "loops": {k: v.state.to_dict() for k, v in self.loops.items()},
            "mcp": self.mcp.status(),
            "voice": self.voice.status() if self.voice else {"enabled": False},
            "hardware": hardware_summary(),
        }
