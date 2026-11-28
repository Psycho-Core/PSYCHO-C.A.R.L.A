"""
Upstream-compatible REST surface (``/v1/*`` + webhooks + digest/research).

The HUD talks to ``/api/*``; this router exposes the OpenJarvis-style
``/v1`` families so external clients, scripts and the upstream tooling
expectations work against the same runtime: agents, memory, traces,
telemetry, skills, sessions, budget, learning, speech, feedback,
optimize, analytics, connectors, approvals, savings, security scan,
channels, models, managed agents, templates, webhooks, digest, research,
plus ``/metrics``, ``/health``, ``/dashboard`` and ``/comparison``.

Everything is backed by the live :class:`CarlaRuntime` — no shadow
state except where the upstream concept is inherently server-side
(custom agents, managed agents, optimize runs, digest history, budget
limits), which lives on ``app.state``.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised.")
    return runtime


def _state(request: Request, key: str, factory: Any) -> Any:
    value = getattr(request.app.state, key, None)
    if value is None:
        value = factory()
        setattr(request.app.state, key, value)
    return value


def _custom_agents(request: Request) -> dict[str, dict[str, Any]]:
    return _state(request, "custom_agents", dict)


def _managed_agents(request: Request) -> dict[str, dict[str, Any]]:
    return _state(request, "managed_agents", dict)


def _analytics(request: Request) -> Any:
    def build() -> Any:
        from ...analytics import LocalAnalytics

        return LocalAnalytics(_runtime(request).config)

    return _state(request, "analytics", build)


def _trace_dict(trace: Any) -> dict[str, Any]:
    return trace.to_dict() if hasattr(trace, "to_dict") else dict(trace)


# --------------------------------------------------------------------------- #
# Health / info / metrics / savings
# --------------------------------------------------------------------------- #

@router.get("/health")
async def health(request: Request):
    runtime = _runtime(request)
    status = runtime.status()
    return {"status": "ok", "agents": len(status.get("agents", [])),
            "tiers": len(status.get("tiers", []))}


@router.get("/v1/info")
async def info(request: Request):
    runtime = _runtime(request)
    status = runtime.status()
    return {
        "name": "PSYCHO-C.A.R.L.A.",
        "version": "1.0.0",
        "agents": [a.get("name") for a in status.get("agents", [])],
        "tiers": [t.get("name") for t in status.get("tiers", [])],
        "hardware": status.get("hardware", {}),
    }


@router.get("/metrics")
async def metrics(request: Request):
    runtime = _runtime(request)
    summary = runtime.telemetry.summary(window_minutes=60)
    lines = [
        "# HELP carla_requests_total Requests in the last hour",
        "# TYPE carla_requests_total counter",
        f"carla_requests_total {summary.get('requests', 0)}",
        "# HELP carla_tokens_total Tokens processed in the last hour",
        "# TYPE carla_tokens_total counter",
        f"carla_tokens_total {summary.get('total_tokens', 0)}",
        "# HELP carla_avg_latency_ms Average latency (ms)",
        "# TYPE carla_avg_latency_ms gauge",
        f"carla_avg_latency_ms {summary.get('avg_latency_ms', 0)}",
        "# HELP carla_energy_joules Energy used (J)",
        "# TYPE carla_energy_joules counter",
        f"carla_energy_joules {summary.get('energy_joules', 0)}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n",
                             media_type="text/plain; version=0.0.4")


@router.get("/v1/savings")
async def savings(request: Request):
    """Estimated saving vs hosted APIs, from measured token volume.

    The hosted comparison prices are stated assumptions (blended
    $3/M input, $15/M output), not fetched prices.
    """
    runtime = _runtime(request)
    summary = runtime.telemetry.summary(window_minutes=24 * 60)
    recent = runtime.telemetry.recent_requests(limit=200)
    prompt_tokens = sum(r.get("prompt_tokens", 0) for r in recent)
    completion_tokens = sum(r.get("completion_tokens", 0) for r in recent)
    hosted_usd = (prompt_tokens / 1e6) * 3.0 + (completion_tokens / 1e6) * 15.0
    energy_kwh = float(summary.get("energy_joules", 0.0)) / 3_600_000.0
    local_usd = energy_kwh * 0.30  # assumed $0.30/kWh
    return {
        "window_hours": 24,
        "requests": summary.get("requests", 0),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "assumed_hosted_usd": round(hosted_usd, 4),
        "assumed_local_electricity_usd": round(local_usd, 4),
        "estimated_saving_usd": round(max(0.0, hosted_usd - local_usd), 4),
        "assumptions": {
            "hosted_input_usd_per_million": 3.0,
            "hosted_output_usd_per_million": 15.0,
            "electricity_usd_per_kwh": 0.30,
        },
    }


# --------------------------------------------------------------------------- #
# Agents (/v1/agents)
# --------------------------------------------------------------------------- #

@router.get("/v1/agents")
async def v1_agents(request: Request):
    runtime = _runtime(request)
    out = runtime.list_agents()
    for name, spec in _custom_agents(request).items():
        out.append({"name": name, "description": spec.get("description", ""),
                    "mode": "on_demand", "custom": True})
    return {"agents": out, "count": len(out)}


@router.post("/v1/agents")
async def v1_agents_create(request: Request, body: dict[str, Any]):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required.")
    runtime = _runtime(request)
    if runtime.agents.get_class(name) is not None:
        raise HTTPException(status_code=409,
                            detail=f"'{name}' is a built-in agent.")
    _custom_agents(request)[name] = {
        "description": str(body.get("description", "")),
        "system_prompt": str(body.get("system_prompt", "")),
        "tier": str(body.get("tier", "")),
        "tools": list(body.get("tools", [])),
    }
    return {"name": name, "created": True}


@router.delete("/v1/agents/{agent_id}")
async def v1_agents_delete(request: Request, agent_id: str):
    custom = _custom_agents(request)
    if agent_id not in custom:
        raise HTTPException(
            status_code=404,
            detail="Only custom agents created via the API can be deleted.")
    del custom[agent_id]
    return {"deleted": agent_id}


@router.post("/v1/agents/{agent_id}/message")
async def v1_agents_message(request: Request, agent_id: str,
                            body: dict[str, Any]):
    runtime = _runtime(request)
    prompt = str(body.get("message", body.get("prompt", ""))).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="message is required.")
    custom = _custom_agents(request)
    if agent_id in custom:
        spec = custom[agent_id]
        from ...agents.base import BaseAgent

        class _CustomAgent(BaseAgent):
            name = agent_id
            description = spec.get("description", "")
            allowed_tools = list(spec.get("tools", []))
            system_prompt_extra = spec.get("system_prompt", "")

        agent = _CustomAgent(runtime.config, runtime.router, runtime.tools,
                             runtime.memory, runtime.persona)
        result = agent.run(prompt, tier=spec.get("tier", ""))
        return result.to_dict()
    if runtime.agents.get_class(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent_id}'.")
    return runtime.ask(prompt, agent=agent_id,
                       tier=str(body.get("tier", "")),
                       session_id=str(body.get("session_id", "default")))


# --------------------------------------------------------------------------- #
# Managed agents (/v1/managed-agents) + templates
# --------------------------------------------------------------------------- #

@router.get("/v1/managed-agents")
async def managed_list(request: Request):
    agents = _managed_agents(request)
    return {"agents": list(agents.values()), "count": len(agents)}


@router.post("/v1/managed-agents")
async def managed_create(request: Request, body: dict[str, Any]):
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required.")
    agents = _managed_agents(request)
    agent_id = uuid.uuid4().hex[:12]
    record = {
        "id": agent_id, "name": name,
        "agent": str(body.get("agent", "orchestrator")),
        "system_prompt": str(body.get("system_prompt", "")),
        "status": "idle", "channels": [], "tasks": [], "messages": [],
        "created_at": time.time(),
    }
    agents[agent_id] = record
    return record


def _managed(request: Request, agent_id: str) -> dict[str, Any]:
    record = _managed_agents(request).get(agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Managed agent not found.")
    return record


@router.get("/v1/managed-agents/{agent_id}")
async def managed_get(request: Request, agent_id: str):
    return _managed(request, agent_id)


@router.patch("/v1/managed-agents/{agent_id}")
async def managed_patch(request: Request, agent_id: str,
                        body: dict[str, Any]):
    record = _managed(request, agent_id)
    for key in ("name", "agent", "system_prompt", "status"):
        if key in body:
            record[key] = body[key]
    return record


@router.delete("/v1/managed-agents/{agent_id}")
async def managed_delete(request: Request, agent_id: str):
    agents = _managed_agents(request)
    if agents.pop(agent_id, None) is None:
        raise HTTPException(status_code=404, detail="Managed agent not found.")
    return {"deleted": agent_id}


@router.post("/v1/managed-agents/{agent_id}/run")
async def managed_run(request: Request, agent_id: str,
                      body: dict[str, Any]):
    record = _managed(request, agent_id)
    if record["status"] == "paused":
        raise HTTPException(status_code=409, detail="Agent is paused.")
    runtime = _runtime(request)
    prompt = str(body.get("prompt", body.get("message", ""))).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required.")
    record["status"] = "running"
    try:
        result = runtime.ask(prompt, agent=record["agent"],
                             session_id=f"managed:{agent_id}")
    finally:
        record["status"] = "idle"
    record["messages"].append({"role": "user", "content": prompt})
    record["messages"].append({"role": "assistant",
                                "content": result.get("text", "")})
    return result


@router.post("/v1/managed-agents/{agent_id}/pause")
async def managed_pause(request: Request, agent_id: str):
    _managed(request, agent_id)["status"] = "paused"
    return {"id": agent_id, "status": "paused"}


@router.post("/v1/managed-agents/{agent_id}/resume")
async def managed_resume(request: Request, agent_id: str):
    _managed(request, agent_id)["status"] = "idle"
    return {"id": agent_id, "status": "idle"}


@router.post("/v1/managed-agents/{agent_id}/recover")
async def managed_recover(request: Request, agent_id: str):
    record = _managed(request, agent_id)
    record["status"] = "idle"
    record["recovered_at"] = time.time()
    return {"id": agent_id, "status": "idle", "recovered": True}


@router.get("/v1/managed-agents/{agent_id}/messages")
async def managed_messages(request: Request, agent_id: str):
    return {"messages": _managed(request, agent_id)["messages"]}


@router.post("/v1/managed-agents/{agent_id}/messages")
async def managed_message(request: Request, agent_id: str,
                          body: dict[str, Any]):
    return await managed_run(request, agent_id, body)


@router.get("/v1/managed-agents/{agent_id}/tasks")
async def managed_tasks(request: Request, agent_id: str):
    return {"tasks": _managed(request, agent_id)["tasks"]}


@router.post("/v1/managed-agents/{agent_id}/tasks")
async def managed_task_create(request: Request, agent_id: str,
                              body: dict[str, Any]):
    record = _managed(request, agent_id)
    task = {
        "id": uuid.uuid4().hex[:10],
        "title": str(body.get("title", "task")),
        "prompt": str(body.get("prompt", "")),
        "status": "pending", "result": "", "created_at": time.time(),
    }
    record["tasks"].append(task)
    return task


@router.patch("/v1/managed-agents/{agent_id}/tasks/{task_id}")
async def managed_task_update(request: Request, agent_id: str, task_id: str,
                              body: dict[str, Any]):
    record = _managed(request, agent_id)
    for task in record["tasks"]:
        if task["id"] == task_id:
            if "status" in body:
                task["status"] = str(body["status"])
            if task["status"] == "run" or body.get("run"):
                runtime = _runtime(request)
                result = runtime.ask(task["prompt"], agent=record["agent"],
                                     session_id=f"managed:{agent_id}")
                task["result"] = result.get("text", "")
                task["status"] = "done"
            return task
    raise HTTPException(status_code=404, detail="Task not found.")


@router.delete("/v1/managed-agents/{agent_id}/tasks/{task_id}")
async def managed_task_delete(request: Request, agent_id: str, task_id: str):
    record = _managed(request, agent_id)
    record["tasks"] = [t for t in record["tasks"] if t["id"] != task_id]
    return {"deleted": task_id}


@router.get("/v1/managed-agents/{agent_id}/channels")
async def managed_channels(request: Request, agent_id: str):
    return {"channels": _managed(request, agent_id)["channels"]}


@router.post("/v1/managed-agents/{agent_id}/channels")
async def managed_channel_bind(request: Request, agent_id: str,
                               body: dict[str, Any]):
    record = _managed(request, agent_id)
    binding = {"id": uuid.uuid4().hex[:10],
               "channel": str(body.get("channel", "")),
               "target": str(body.get("target", ""))}
    record["channels"].append(binding)
    return binding


@router.delete("/v1/managed-agents/{agent_id}/channels/{binding_id}")
async def managed_channel_unbind(request: Request, agent_id: str,
                                 binding_id: str):
    record = _managed(request, agent_id)
    record["channels"] = [c for c in record["channels"]
                          if c["id"] != binding_id]
    return {"deleted": binding_id}


@router.get("/v1/managed-agents/{agent_id}/state")
async def managed_state(request: Request, agent_id: str):
    record = _managed(request, agent_id)
    return {"id": agent_id, "status": record["status"],
            "tasks": len(record["tasks"]),
            "messages": len(record["messages"])}


@router.get("/v1/managed-agents/{agent_id}/traces")
async def managed_traces(request: Request, agent_id: str):
    record = _managed(request, agent_id)
    runtime = _runtime(request)
    traces = runtime.traces.recent(limit=50, agent=record["agent"])
    return {"traces": [_trace_dict(t) for t in traces]}


@router.post("/v1/managed-agents/{agent_id}/learning/run")
async def managed_learning_run(request: Request, agent_id: str):
    _managed(request, agent_id)
    return await optimize_run_create(request, {"surface": "agent"})


@router.post("/v1/templates/{template_id}/instantiate")
async def template_instantiate(request: Request, template_id: str,
                               body: dict[str, Any]):
    from ...templates.agent_templates import load_template_dir

    catalogue = load_template_dir()
    template = catalogue.get(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    record_body = {
        "name": str(body.get("name", template_id)),
        "agent": "orchestrator",
        "system_prompt": getattr(template, "system_prompt", "") or "",
    }
    created = await managed_create(request, record_body)
    created["template"] = template_id
    return created


@router.get("/v1/agents/health")
async def agents_health(request: Request):
    runtime = _runtime(request)
    return {"status": "ok", "agents": runtime.list_agents(),
            "managed": len(_managed_agents(request))}


@router.get("/v1/agents/errors")
async def agents_errors(request: Request):
    runtime = _runtime(request)
    errors = [
        _trace_dict(t) for t in runtime.traces.recent(limit=100)
        if getattr(t, "error", "")
    ]
    return {"errors": errors, "count": len(errors)}


@router.get("/v1/recommended-model")
async def recommended_model(request: Request):
    from ...intelligence.model_catalog import recommend_models

    ram_gb = vram_gb = 0.0
    try:
        from ...core.telemetry import hardware_summary

        hw = hardware_summary()
        ram_gb = float(hw.get("ram_total_mb", 0.0) or 0.0) / 1024.0
        vram_gb = float(hw.get("vram_total_mb", 0.0) or 0.0) / 1024.0
    except Exception:
        pass
    recommendations = recommend_models(ram_gb or 8.0, vram_gb)
    return {"recommendations": [
        r.to_dict() if hasattr(r, "to_dict") else str(r)
        for r in recommendations
    ]}


# --------------------------------------------------------------------------- #
# Memory (/v1/memory)
# --------------------------------------------------------------------------- #

@router.post("/v1/memory/store")
async def memory_store(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required.")
    memory_id = runtime.memory_store(
        content, tags=list(body.get("tags", [])) or None)
    return {"id": memory_id, "stored": True}


@router.post("/v1/memory/search")
async def memory_search(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required.")
    results = runtime.memory_search(query, top_k=int(body.get("top_k", 5)))
    return {"results": results, "count": len(results)}


@router.get("/v1/memory/stats")
async def memory_stats(request: Request):
    runtime = _runtime(request)
    stats = runtime.memory.stats() if hasattr(runtime.memory, "stats") else {}
    return {"stats": stats}


@router.get("/v1/memory/config")
async def memory_config(request: Request):
    runtime = _runtime(request)
    memory = runtime.config.memory
    return {
        "enabled": memory.enabled, "backend": memory.backend,
        "max_facts": memory.max_facts,
        "extraction_model": memory.extraction_model,
        "context_top_k": runtime.config.tools.storage.context_top_k,
    }


@router.post("/v1/memory/index")
async def memory_index(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required.")
    memory_id = runtime.memory_store(text, tags=["indexed"])
    return {"id": memory_id, "indexed": True}


# --------------------------------------------------------------------------- #
# Traces / telemetry / learning / feedback / optimize
# --------------------------------------------------------------------------- #

@router.get("/v1/traces")
async def traces_list(request: Request, limit: int = 50, agent: str = ""):
    runtime = _runtime(request)
    traces = runtime.traces.recent(limit=limit, agent=agent)
    return {"traces": [_trace_dict(t) for t in traces], "count": len(traces)}


@router.get("/v1/traces/{trace_id}")
async def traces_get(request: Request, trace_id: int):
    runtime = _runtime(request)
    for trace in runtime.traces.recent(limit=500):
        if trace.id == trace_id:
            return _trace_dict(trace)
    raise HTTPException(status_code=404, detail="Trace not found.")


@router.get("/v1/telemetry/stats")
async def telemetry_stats(request: Request, window: int = 60):
    runtime = _runtime(request)
    return runtime.telemetry.summary(window_minutes=window)


@router.get("/v1/telemetry/energy")
async def telemetry_energy(request: Request, window: int = 60):
    runtime = _runtime(request)
    if hasattr(runtime.telemetry, "energy_summary"):
        return runtime.telemetry.energy_summary(window_minutes=window)
    summary = runtime.telemetry.summary(window_minutes=window)
    return {"energy_joules": summary.get("energy_joules", 0)}


@router.post("/v1/telemetry/reset")
async def telemetry_reset(request: Request):
    runtime = _runtime(request)
    if hasattr(runtime.telemetry, "prune"):
        removed = runtime.telemetry.prune(retain_days=0)
        return {"reset": True, "removed": removed}
    return {"reset": False, "reason": "telemetry store has no reset"}


@router.get("/v1/learning/stats")
async def learning_stats(request: Request):
    runtime = _runtime(request)
    learning = runtime.config.learning
    surfaces = {
        name: learning.surface_enabled(name)
        for name in ("routing", "intelligence", "agent", "tools", "engine")
    } if hasattr(learning, "surface_enabled") else {}
    return {"traces": runtime.traces.stats(), "surfaces": surfaces}


@router.get("/v1/learning/policy")
async def learning_policy(request: Request):
    runtime = _runtime(request)
    try:
        policies = runtime.optimizer.optimize_routing(min_samples=5)
        return {"policy": {k: v.to_dict() for k, v in policies.items()}}
    except Exception:
        return {"policy": {}, "reason": "not enough traces yet"}


@router.post("/v1/feedback")
async def feedback_create(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    trace_id = body.get("trace_id")
    if trace_id is None:
        raise HTTPException(status_code=400, detail="trace_id is required.")
    score = float(body.get("score", 1.0 if body.get("up", True) else -1.0))
    ok = runtime.traces.rate(int(trace_id), score,
                             str(body.get("note", "")))
    if not ok:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {"rated": True, "trace_id": trace_id, "score": score}


@router.get("/v1/feedback/stats")
async def feedback_stats(request: Request):
    runtime = _runtime(request)
    return runtime.traces.stats()


def _optimize_runs(request: Request) -> list[dict[str, Any]]:
    return _state(request, "optimize_runs", list)


@router.get("/v1/optimize/runs")
async def optimize_runs(request: Request):
    runs = _optimize_runs(request)
    return {"runs": runs, "count": len(runs)}


@router.get("/v1/optimize/runs/{run_id}")
async def optimize_run_get(request: Request, run_id: str):
    for run in _optimize_runs(request):
        if run["id"] == run_id:
            return run
    raise HTTPException(status_code=404, detail="Run not found.")


@router.post("/v1/optimize/runs")
async def optimize_run_create(request: Request,
                              body: dict[str, Any] | None = None):
    runtime = _runtime(request)
    body = body or {}
    surface = str(body.get("surface", "prompt"))
    result: dict[str, Any] = {"surface": surface}
    try:
        result["results"] = runtime.optimize(surface=surface)
        result["status"] = "complete"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    run = {"id": uuid.uuid4().hex[:12], "created_at": time.time(), **result}
    _optimize_runs(request).append(run)
    return run


# --------------------------------------------------------------------------- #
# Skills / sessions / budget
# --------------------------------------------------------------------------- #

@router.get("/v1/skills")
async def v1_skills(request: Request):
    runtime = _runtime(request)
    if runtime.skills is None:
        return {"skills": [], "count": 0}
    catalogue = runtime.skills.catalog
    return {
        "skills": [s.to_dict() for s in catalogue.valid_skills],
        "count": len(catalogue.valid_skills),
    }


@router.post("/v1/skills")
async def v1_skills_install(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    if runtime.skills is None:
        raise HTTPException(status_code=503, detail="Skills are disabled.")
    source = str(body.get("source", "")).strip()
    if not source:
        raise HTTPException(status_code=400, detail="source is required.")
    skill = runtime.skills.install(source, name=str(body.get("name", "")),
                                   overwrite=True)
    return {"installed": skill.name if skill else source}


@router.delete("/v1/skills/{skill_name}")
async def v1_skills_delete(request: Request, skill_name: str):
    runtime = _runtime(request)
    if runtime.skills is None:
        raise HTTPException(status_code=503, detail="Skills are disabled.")
    if not runtime.skills.uninstall(skill_name):
        raise HTTPException(status_code=404, detail="Skill not found.")
    return {"deleted": skill_name}


@router.get("/v1/sessions")
async def v1_sessions(request: Request):
    runtime = _runtime(request)
    if runtime.sessions is None:
        return {"sessions": [], "count": 0}
    sessions = runtime.sessions.list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/v1/sessions/{session_id}")
async def v1_session_get(request: Request, session_id: str):
    runtime = _runtime(request)
    if runtime.sessions is None:
        raise HTTPException(status_code=503, detail="Sessions are disabled.")
    session = runtime.sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return session if isinstance(session, dict) else {"session": str(session)}


def _budget(request: Request) -> dict[str, Any]:
    return _state(request, "budget_limits",
                  lambda: {"max_tokens_per_day": 0, "max_requests_per_day": 0})


@router.get("/v1/budget")
async def budget_get(request: Request):
    runtime = _runtime(request)
    summary = runtime.telemetry.summary(window_minutes=24 * 60)
    return {
        "limits": _budget(request),
        "used_today": {
            "requests": summary.get("requests", 0),
            "total_tokens": summary.get("total_tokens", 0),
        },
    }


@router.put("/v1/budget/limits")
async def budget_put(request: Request, body: dict[str, Any]):
    limits = _budget(request)
    for key in ("max_tokens_per_day", "max_requests_per_day"):
        if key in body:
            limits[key] = int(body[key])
    return {"limits": limits}


# --------------------------------------------------------------------------- #
# Speech (/v1/speech)
# --------------------------------------------------------------------------- #

def _voice(request: Request) -> Any:
    runtime = _runtime(request)
    voice = getattr(runtime, "voice", None)
    if voice is None:
        raise HTTPException(status_code=503,
                            detail="Voice is not enabled on this runtime.")
    return voice


@router.get("/v1/speech/health")
async def speech_health(request: Request):
    voice = _voice(request)
    return {"ok": True, **voice.status()}


@router.get("/v1/speech/tts/health")
async def tts_health(request: Request):
    voice = _voice(request)
    status = voice.status()
    return {"ok": bool(status.get("tts_available")),
            "backend": status.get("tts_backend")}


@router.post("/v1/speech/synthesize")
async def speech_synthesize(request: Request, body: dict[str, Any]):
    voice = _voice(request)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required.")
    import tempfile

    out_path = Path(tempfile.gettempdir()) / f"carla_tts_{uuid.uuid4().hex}.wav"
    result = voice.tts.synthesize(text, out_path)
    if not getattr(result, "ok", False):
        raise HTTPException(
            status_code=503,
            detail=f"TTS unavailable: {getattr(result, 'error', 'unknown')}")
    audio = base64.b64encode(out_path.read_bytes()).decode("ascii")
    return {"audio_base64": audio, "format": "wav",
            "backend": getattr(result, "backend", "")}


@router.post("/v1/speech/transcribe")
async def speech_transcribe(request: Request, body: dict[str, Any]):
    voice = _voice(request)
    audio_b64 = str(body.get("audio_base64", ""))
    if not audio_b64:
        raise HTTPException(status_code=400,
                            detail="audio_base64 is required.")
    import tempfile

    suffix = str(body.get("format", "wav"))
    audio_path = Path(tempfile.gettempdir()) / \
        f"carla_stt_{uuid.uuid4().hex}.{suffix}"
    audio_path.write_bytes(base64.b64decode(audio_b64))
    text = voice.stt.transcribe_file(audio_path)
    if not text:
        raise HTTPException(
            status_code=503,
            detail="STT produced no text (backend unavailable or silent audio).")
    return {"text": text, "backend": getattr(voice.stt, "name", "")}


# --------------------------------------------------------------------------- #
# Analytics (/v1/analytics)
# --------------------------------------------------------------------------- #

@router.get("/v1/analytics/identity")
async def analytics_identity(request: Request):
    return _analytics(request).identity()


@router.post("/v1/analytics/preference")
async def analytics_preference(request: Request, body: dict[str, Any]):
    key = str(body.get("key", "")).strip()
    if not key:
        raise HTTPException(status_code=400, detail="key is required.")
    return _analytics(request).set_preference(key, body.get("value"))


# --------------------------------------------------------------------------- #
# Connectors (/v1/connectors) + upload ingest
# --------------------------------------------------------------------------- #

@router.get("/v1/connectors")
async def v1_connectors(request: Request):
    from ...connectors import available_connectors, get_connector

    runtime = _runtime(request)
    out = []
    for connector_id in available_connectors():
        connector = get_connector(connector_id)
        if connector is None:
            continue
        status = connector.status()
        out.append({
            "id": connector_id,
            "display_name": getattr(connector, "display_name", connector_id),
            "status": status.value if hasattr(status, "value") else str(status),
            "documents": runtime.knowledge.count(connector_id)
            if runtime.knowledge is not None else 0,
        })
    return {"connectors": out, "count": len(out)}


@router.get("/v1/connectors/{connector_id}")
async def v1_connector_get(request: Request, connector_id: str):
    from ...connectors import get_connector

    _runtime(request)
    connector = get_connector(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Unknown connector.")
    status = connector.status()
    return {
        "id": connector_id,
        "display_name": getattr(connector, "display_name", connector_id),
        "status": status.value if hasattr(status, "value") else str(status),
        "auth_url": connector.auth_url() if hasattr(connector, "auth_url") else "",
    }


@router.post("/v1/connectors/{connector_id}/connect")
async def v1_connector_connect(request: Request, connector_id: str):
    """Start (or describe) the connection flow for one connector."""
    from ...connectors import connector_status, get_connector

    _runtime(request)
    connector = get_connector(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Unknown connector.")
    status = connector_status(connector_id)
    auth_url = connector.auth_url() if hasattr(connector, "auth_url") else ""
    return {
        **status,
        "auth_url": auth_url,
        "next_step": (
            "Open auth_url and approve access."
            if auth_url else
            "Run `carla connect login " + connector_id + "` on this machine "
            "to complete the browser OAuth flow."
        ),
    }


@router.post("/v1/connectors/{connector_id}/disconnect")
async def v1_connector_disconnect(request: Request, connector_id: str):
    from ...connectors import delete_tokens, get_provider_for_connector
    from ...connectors.oauth import CONNECTORS_DIR

    _runtime(request)
    provider = get_provider_for_connector(connector_id)
    removed = 0
    if provider is not None:
        for filename in provider.credential_files:
            if delete_tokens(CONNECTORS_DIR / filename):
                removed += 1
    own = CONNECTORS_DIR / f"{connector_id}.json"
    if delete_tokens(own):
        removed += 1
    return {"connector": connector_id, "credential_files_removed": removed}


@router.get("/v1/connectors/{connector_id}/oauth/start")
async def v1_connector_oauth_start(request: Request, connector_id: str):
    from ...connectors import get_connector

    _runtime(request)
    connector = get_connector(connector_id)
    if connector is None:
        raise HTTPException(status_code=404, detail="Unknown connector.")
    auth_url = connector.auth_url() if hasattr(connector, "auth_url") else ""
    if not auth_url:
        raise HTTPException(
            status_code=400,
            detail="No OAuth URL available — client credentials are not "
                   "configured for this provider.")
    return {"auth_url": auth_url, "connector": connector_id}


@router.get("/v1/connectors/{connector_id}/oauth/callback")
async def v1_connector_oauth_callback(request: Request, connector_id: str,
                                      code: str = ""):
    from ...connectors import get_provider_for_connector, save_tokens
    from ...connectors.oauth import (
        CONNECTORS_DIR,
        _exchange_token,
        _redirect_uri,
        get_client_credentials,
        require_access_token,
    )

    _runtime(request)
    provider = get_provider_for_connector(connector_id)
    if provider is None:
        raise HTTPException(status_code=404,
                            detail="No OAuth provider for this connector.")
    if not code:
        raise HTTPException(status_code=400, detail="code is required.")
    creds = get_client_credentials(provider)
    if not creds:
        raise HTTPException(status_code=400,
                            detail="Client credentials are not configured.")
    tokens = _exchange_token(provider, code, creds[0], creds[1],
                             _redirect_uri(provider))
    payload = {
        "access_token": require_access_token(tokens),
        "refresh_token": tokens.get("refresh_token", ""),
        "token_type": tokens.get("token_type", "Bearer"),
        "expires_in": tokens.get("expires_in", 3600),
        "obtained_at": time.time(),
        "client_id": creds[0],
        "client_secret": creds[1],
    }
    for filename in provider.credential_files:
        save_tokens(CONNECTORS_DIR / filename, payload)
    return {"connected": connector_id, "provider": provider.name}


@router.post("/v1/connectors/{connector_id}/sync")
async def v1_connector_sync(request: Request, connector_id: str,
                            body: dict[str, Any] | None = None):
    runtime = _runtime(request)
    try:
        count = runtime.sync_connector(connector_id, **(body or {}))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connector": connector_id, "documents_indexed": count}


@router.get("/v1/connectors/{connector_id}/sync")
async def v1_connector_sync_status(request: Request, connector_id: str):
    runtime = _runtime(request)
    return {
        "connector": connector_id,
        "documents": runtime.knowledge.count(connector_id)
        if runtime.knowledge is not None else 0,
    }


@router.post("/v1/connectors/upload/ingest")
async def upload_ingest(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    if runtime.knowledge is None:
        raise HTTPException(status_code=503,
                            detail="Knowledge store unavailable.")
    from ...connectors.base import Document

    text = str(body.get("text", ""))
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    doc = Document(
        doc_id=str(body.get("id", f"upload:{uuid.uuid4().hex[:10]}")),
        title=str(body.get("title", "Uploaded document")),
        body=text, source=str(body.get("source", "upload")),
        url=str(body.get("url", "")),
    )
    runtime.knowledge.add(doc)
    return {"indexed": doc.doc_id}


@router.post("/v1/connectors/upload/ingest/files")
async def upload_ingest_files(request: Request):
    runtime = _runtime(request)
    if runtime.knowledge is None:
        raise HTTPException(status_code=503,
                            detail="Knowledge store unavailable.")
    from ...connectors.base import Document

    form = await request.form()
    indexed: list[str] = []
    for _key, upload in form.items():
        raw = await upload.read()
        text = raw.decode("utf-8", "replace")
        doc = Document(
            doc_id=f"upload:{uuid.uuid4().hex[:10]}",
            title=getattr(upload, "filename", "file"), body=text,
            source="upload",
        )
        runtime.knowledge.add(doc)
        indexed.append(doc.doc_id)
    if not indexed:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    return {"indexed": indexed, "count": len(indexed)}


# --------------------------------------------------------------------------- #
# Approvals (/v1/approvals)
# --------------------------------------------------------------------------- #

@router.get("/v1/approvals/pending")
async def approvals_pending(request: Request):
    runtime = _runtime(request)
    return {"pending": runtime.approvals.pending(),
            "count": len(runtime.approvals)}


@router.post("/v1/approvals/{action_id}/approve")
async def approvals_approve(request: Request, action_id: str):
    runtime = _runtime(request)

    def execute(tool: str, arguments: dict[str, Any]) -> str:
        result = runtime.tools.execute(tool, arguments, approved=True)
        return result.as_message() if hasattr(result, "as_message") \
            else str(result)

    action = runtime.approvals.decide(action_id, True, executor=execute)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found.")
    return action.to_dict()


@router.post("/v1/approvals/{action_id}/deny")
async def approvals_deny(request: Request, action_id: str):
    runtime = _runtime(request)
    action = runtime.approvals.decide(action_id, False)
    if action is None:
        raise HTTPException(status_code=404, detail="Action not found.")
    return action.to_dict()


# --------------------------------------------------------------------------- #
# Security scan / cloud reload / channels & models aliases
# --------------------------------------------------------------------------- #

@router.get("/v1/security/scan")
async def security_scan(request: Request, text: str = ""):
    from ...security.scanners import scan_text

    runtime = _runtime(request)
    cfg = runtime.config
    if not text:
        config_path = Path(cfg.config_path)
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8", errors="replace")
    result = scan_text(
        text,
        secrets=cfg.security.secret_scanner,
        pii=cfg.security.pii_scanner,
        injection=True,
    )
    return {"scan": result.to_dict(),
            "mode": cfg.security.mode,
            "ssrf_protection": cfg.security.ssrf_protection,
            "default_deny": cfg.security.default_deny}


@router.post("/v1/cloud/reload")
async def cloud_reload(request: Request):
    from ...core.config import load_config, set_active_config
    from ...providers.base import available_adapters

    runtime = _runtime(request)
    fresh = load_config(runtime.config.config_path)
    set_active_config(fresh)
    return {"reloaded": True, "adapters": available_adapters(),
            "tiers": sorted(fresh.intelligence.tiers)}


@router.get("/v1/channels")
async def v1_channels(request: Request):
    runtime = _runtime(request)
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        return {"channels": [], "count": 0, "status": "disconnected"}
    return {"channels": bridge.describe(), "count": len(bridge.channels),
            "status": bridge.status().value}


@router.get("/v1/channels/status")
async def v1_channels_status(request: Request):
    runtime = _runtime(request)
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503,
                            detail="No channels are configured.")
    return {"status": bridge.status().value,
            "destinations": bridge.list_channels(),
            "channels": bridge.describe()}


@router.post("/v1/channels/send")
async def v1_channels_send(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503,
                            detail="No channels are configured.")
    channel = str(body.get("channel", "")).strip()
    text = str(body.get("text", "")).strip()
    if not channel or not text:
        raise HTTPException(status_code=400,
                            detail="channel and text are required.")
    return {"ok": bridge.send(channel, text), "channel": channel}


@router.post("/v1/models/pull")
async def v1_models_pull(request: Request, body: dict[str, Any]):
    from ...intelligence.model_manager import pull_model

    _runtime(request)
    model = str(body.get("model", "")).strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required.")
    result = pull_model(model, engine=str(body.get("engine", "ollama")))
    return {"model": model, "ok": getattr(result, "ok", True),
            "message": getattr(result, "message", "")}


@router.delete("/v1/models/{model_name:path}")
async def v1_models_delete(request: Request, model_name: str):
    from ...intelligence.model_manager import delete_local_model

    _runtime(request)
    result = delete_local_model(model_name)
    return {"model": model_name, "ok": getattr(result, "ok", True),
            "message": getattr(result, "message", "")}


# --------------------------------------------------------------------------- #
# Webhooks (/webhooks/*)
# --------------------------------------------------------------------------- #

def _deliver_webhook(request: Request, channel: str, sender: str,
                     content: str, conversation_id: str = "") -> dict[str, Any]:
    from ...channels.base import ChannelMessage

    runtime = _runtime(request)
    bridge = getattr(runtime, "channel_bridge", None)
    if bridge is None:
        return {"delivered": False,
                "reason": "no channel bridge (no channels configured)"}
    reply = bridge.handle_incoming(ChannelMessage(
        channel=channel, sender=sender, content=content,
        conversation_id=conversation_id,
    ))
    return {"delivered": True, "reply": reply or ""}


@router.post("/webhooks/twilio")
async def webhook_twilio(request: Request):
    form = await request.form()
    return _deliver_webhook(
        request, "twilio", str(form.get("From", "")),
        str(form.get("Body", "")), str(form.get("To", "")))


@router.post("/webhooks/bluebubbles")
async def webhook_bluebubbles(request: Request):
    body = await request.json()
    data = body.get("data") or {}
    return _deliver_webhook(
        request, "bluebubbles", str(data.get("handle", "")),
        str(data.get("text", "")), str(data.get("chat", "")))


@router.get("/webhooks/whatsapp")
async def webhook_whatsapp_verify(request: Request):
    params = request.query_params
    return PlainTextResponse(params.get("hub.challenge", ""))


@router.post("/webhooks/whatsapp")
async def webhook_whatsapp(request: Request):
    body = await request.json()
    entries = body.get("entry") or []
    delivered: list[dict[str, Any]] = []
    for entry in entries:
        for change in entry.get("changes") or []:
            for message in (change.get("value") or {}).get("messages") or []:
                delivered.append(_deliver_webhook(
                    request, "whatsapp", str(message.get("from", "")),
                    str((message.get("text") or {}).get("body", "")),
                ))
    return {"delivered": delivered}


@router.post("/webhooks/sendblue")
async def webhook_sendblue(request: Request):
    body = await request.json()
    return _deliver_webhook(
        request, "sendblue", str(body.get("from_number", "")),
        str(body.get("content", "")), str(body.get("to_number", "")))


# --------------------------------------------------------------------------- #
# Digest (/api/digest) + research (/api/research)
# --------------------------------------------------------------------------- #

def _digest_state(request: Request) -> dict[str, Any]:
    return _state(request, "digest_state",
                  lambda: {"history": [], "schedule": {}})


@router.get("/api/digest")
async def digest_latest(request: Request):
    state = _digest_state(request)
    if not state["history"]:
        return {"digest": None, "reason": "No digest generated yet."}
    return {"digest": state["history"][-1]}


@router.post("/api/digest/generate")
async def digest_generate(request: Request):
    runtime = _runtime(request)
    result = runtime.ask(
        "Produce my morning digest now.", agent="morning_digest",
        session_id="digest")
    entry = {"generated_at": time.time(), "text": result.get("text", ""),
             "speech": result.get("speech", "")}
    state = _digest_state(request)
    state["history"].append(entry)
    del state["history"][:-20]
    return {"digest": entry}


@router.get("/api/digest/audio")
async def digest_audio(request: Request):
    state = _digest_state(request)
    if not state["history"]:
        raise HTTPException(status_code=404, detail="No digest generated yet.")
    voice = _voice(request)
    import tempfile

    out_path = Path(tempfile.gettempdir()) / f"carla_digest_{uuid.uuid4().hex}.wav"
    result = voice.tts.synthesize(state["history"][-1]["speech"]
                                  or state["history"][-1]["text"], out_path)
    if not getattr(result, "ok", False):
        raise HTTPException(status_code=503,
                            detail="TTS is unavailable on this machine.")
    return {"audio_base64": base64.b64encode(out_path.read_bytes()).decode(),
            "format": "wav"}


@router.get("/api/digest/history")
async def digest_history(request: Request):
    state = _digest_state(request)
    return {"history": state["history"], "count": len(state["history"])}


@router.get("/api/digest/schedule")
async def digest_schedule_get(request: Request):
    runtime = _runtime(request)
    state = _digest_state(request)
    return {
        "enabled": runtime.config.digest.enabled,
        "schedule": state["schedule"].get(
            "cron", runtime.config.digest.schedule),
        "timezone": runtime.config.digest.timezone,
    }


@router.post("/api/digest/schedule")
async def digest_schedule_set(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    state = _digest_state(request)
    cron = str(body.get("cron", body.get("schedule", ""))).strip()
    if cron:
        state["schedule"]["cron"] = cron
        runtime.config.digest.schedule = cron
    if "enabled" in body:
        runtime.config.digest.enabled = bool(body["enabled"])
    return await digest_schedule_get(request)


@router.post("/api/research")
async def research(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    query = str(body.get("query", body.get("prompt", ""))).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required.")
    tier = runtime.config.deep_research.model and "deep_tier" or ""
    result = runtime.ask(query, agent="deep_research", tier=tier,
                         session_id="research")
    return result


# --------------------------------------------------------------------------- #
# WebSockets (/v1/chat/stream, /v1/agents/events)
# --------------------------------------------------------------------------- #

@router.websocket("/v1/chat/stream")
async def chat_stream(websocket: WebSocket):
    await websocket.accept()
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        await websocket.close(code=4503, reason="Runtime not initialised.")
        return
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"prompt": raw}
            prompt = str(payload.get("prompt", payload.get("message", "")))
            if not prompt:
                await websocket.send_json(
                    {"type": "error", "content": "prompt is required."})
                continue
            for chunk in runtime.ask_stream(
                    prompt,
                    agent=str(payload.get("agent", "")),
                    tier=str(payload.get("tier", "")),
                    session_id=str(payload.get("session_id", "default"))):
                await websocket.send_json(chunk)
            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        return


@router.websocket("/v1/agents/events")
async def agents_events(websocket: WebSocket):
    await websocket.accept()
    runtime = getattr(websocket.app.state, "runtime", None)
    if runtime is None:
        await websocket.close(code=4503, reason="Runtime not initialised.")
        return

    import asyncio

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: str, data: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(
            queue.put_nowait, {"event": event, "data": data})

    runtime.on(listener)
    try:
        while True:
            item = await queue.get()
            await websocket.send_json(item)
    except WebSocketDisconnect:
        return


# --------------------------------------------------------------------------- #
# Dashboard + comparison pages
# --------------------------------------------------------------------------- #

_PAGE_STYLE = (
    "body{background:#070B14;color:#D6E4F5;font-family:system-ui,sans-serif;"
    "margin:2rem}h1{color:#00E5FF}a{color:#2979FF}table{border-collapse:"
    "collapse}td,th{border:1px solid #1C2B3A;padding:.4rem .8rem}"
)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    runtime = _runtime(request)
    status = runtime.status()
    agents = "".join(
        f"<tr><td>{a.get('name')}</td><td>{a.get('mode')}</td>"
        f"<td>{a.get('description', '')}</td></tr>"
        for a in status.get("agents", []))
    return HTMLResponse(
        f"<html><head><title>C.A.R.L.A. Dashboard</title>"
        f"<style>{_PAGE_STYLE}</style></head><body>"
        f"<h1>PSYCHO-C.A.R.L.A. — Dashboard</h1>"
        f"<p>Agents: {len(status.get('agents', []))} · "
        f"Tiers: {len(status.get('tiers', []))} · "
        f"<a href='/'>Holographic HUD</a></p>"
        f"<table><tr><th>Agent</th><th>Mode</th><th>Description</th></tr>"
        f"{agents}</table></body></html>")


@router.get("/comparison", response_class=HTMLResponse)
async def comparison(request: Request):
    from ...intelligence.model_catalog import list_models

    runtime = _runtime(request)
    rows = "".join(
        f"<tr><td>{m.model_id}</td><td>{m.name}</td>"
        f"<td>{', '.join(m.supported_engines)}</td>"
        f"<td>{m.ram_gb} GB</td></tr>"
        for m in list_models())
    tiers = "".join(
        f"<tr><td>{t.get('name')}</td><td>{t.get('model', '')}</td>"
        f"<td>{t.get('provider', '')}</td></tr>"
        for t in runtime.status().get("tiers", []))
    return HTMLResponse(
        f"<html><head><title>Model Comparison</title>"
        f"<style>{_PAGE_STYLE}</style></head><body>"
        f"<h1>Configured tiers</h1>"
        f"<table><tr><th>Tier</th><th>Model</th><th>Provider</th></tr>"
        f"{tiers}</table><h1>Catalog</h1>"
        f"<table><tr><th>Model</th><th>Name</th><th>Engines</th>"
        f"<th>RAM</th></tr>{rows}</table></body></html>")


__all__ = ["router"]
