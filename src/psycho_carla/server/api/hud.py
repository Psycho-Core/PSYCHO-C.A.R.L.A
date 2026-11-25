"""
PSYCHO-C.A.R.L.A. — HUD API.

REST endpoints backing the holographic dashboard: agent status, tier
switching, memory inspector, cron manager, telemetry and the TOML settings
editor.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ...core.telemetry import hardware_summary
from ...learning.optimizer import PromptOptimizer

router = APIRouter()


def _runtime(request: Request) -> Any:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not initialised.")
    return runtime


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
@router.get("/status")
async def status(request: Request):
    runtime = _runtime(request)
    return runtime.status()


@router.get("/health")
async def health(request: Request):
    runtime = _runtime(request)
    return {"ok": True, "started": runtime._started}


@router.get("/setup")
async def setup_state(request: Request):
    """Is a model usable? Drives the first-run banner in the HUD."""
    from ...intelligence.model_manager import (
        _free_memory_gb, best_gguf_model, best_native_model,
    )

    runtime = _runtime(request)
    router_ = runtime.router
    try:
        res = router_.resolve(None)
        ok, detail = res.adapter.health()
    except Exception as exc:
        res, ok, detail = None, False, str(exc)
    pick = best_gguf_model() or best_native_model()
    return {
        "needs_setup": not ok,
        "reason": "" if ok else detail,
        "provider": res.provider if res else "",
        "model": res.model if res else "",
        "free_ram_gb": round(_free_memory_gb(), 1),
        "native_pick": pick.model_id if pick else "",
    }


#: Voices offered on the audition page → (engine, voice id).
VOICE_CHOICES = {
    "piper_alba_gb_female": ("piper", "en_GB-alba-medium"),
    "piper_cori_gb_female_high": ("piper", "en_GB-cori-high"),
    "piper_alan_gb_male": ("piper", "en_GB-alan-medium"),
    "kokoro_af_heart_us_female": ("kokoro", "af_heart"),
    "kokoro_am_michael_us_male": ("kokoro", "am_michael"),
    "kokoro_bf_emma_gb_female": ("kokoro", "bf_emma"),
    "kokoro_bm_george_gb_male": ("kokoro", "bm_george"),
}


@router.post("/voice/select")
async def voice_select(request: Request, body: dict[str, Any]):
    """Make an audition-page voice the default (applies after a restart)."""
    from ...core.config import load_config, save_config

    label = str(body.get("label", ""))
    if label not in VOICE_CHOICES:
        raise HTTPException(status_code=400, detail="unknown voice")
    engine, voice = VOICE_CHOICES[label]
    config = load_config()
    config.voice.tts_engine, config.voice.tts_voice, config.voice.voice_id = engine, voice, ""
    save_config(config)
    return {"ok": True, "engine": engine, "voice": voice,
            "message": f"voice set to {engine} / {voice} — restart C.A.R.L.A. to hear it"}


@router.post("/persona/name")
async def persona_name(request: Request, body: dict[str, Any]):
    """Set what she calls you (``[persona] user_name``). Blank = 'Psycho'."""
    from ...core.config import save_config

    runtime = _runtime(request)
    name = str(body.get("name", "")).strip()[:40]
    if any(c in name for c in '\r\n"\\'):
        raise HTTPException(status_code=400, detail="that name has characters I can't store")
    runtime.config.persona.user_name = name
    save_config(runtime.config)
    return {"ok": True, "name": runtime.config.persona.address_name}


@router.get("/safe-mode")
async def safe_mode_get(request: Request):
    from ...security.capabilities import SAFE_MODE_TOOLS

    runtime = _runtime(request)
    return {"enabled": bool(runtime.config.security.enforce_tool_confirmation),
            "tools": list(SAFE_MODE_TOOLS)}


@router.post("/safe-mode")
async def safe_mode_set(request: Request, body: dict[str, Any]):
    """Turn safe mode (approval before risky tools) on or off; saved to the config."""
    from ...core.config import save_config

    runtime = _runtime(request)
    runtime.config.security.enforce_tool_confirmation = bool(body.get("enabled", True))
    save_config(runtime.config)
    return {"enabled": runtime.config.security.enforce_tool_confirmation}


@router.get("/hardware")
async def hardware():
    return hardware_summary()


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
@router.get("/agents")
async def list_agents(request: Request):
    runtime = _runtime(request)
    return {"agents": runtime.list_agents()}


@router.post("/agents/{agent}/run")
async def run_agent(request: Request, agent: str, body: dict[str, Any]):
    runtime = _runtime(request)
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required.")
    tier = str(body.get("tier", "") or "")
    session_id = str(body.get("session_id", "default"))
    result = runtime.ask(prompt, agent=agent, tier=tier, session_id=session_id)
    return result


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
@router.post("/chat")
async def chat(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    prompt = str(body.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required.")
    agent = str(body.get("agent", "") or "")
    tier = str(body.get("tier", "") or "")
    session_id = str(body.get("session_id", "default"))
    speak = bool(body.get("speak", False))

    result = runtime.ask(prompt, agent=agent, tier=tier, session_id=session_id)

    if speak and runtime.voice and result.get("speech"):
        try:
            runtime.voice.speak(result["speech"])
        except Exception:
            pass

    # `runtime.ask` already emits `agent_response`, which the hub fans out to
    # every client. No extra broadcast here — it would double-render.
    return result


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #
@router.get("/tiers")
async def list_tiers(request: Request):
    runtime = _runtime(request)
    return {"tiers": runtime.router.list_tiers(), "active": runtime.router.active_tier}


@router.post("/tiers/active")
async def set_active_tier(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    alias = str(body.get("tier", "")).strip()
    if not alias:
        raise HTTPException(status_code=400, detail="tier is required.")
    ok = runtime.router.set_active_tier(alias)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown tier '{alias}'.")
    runtime.broadcast("tier_changed", {"tier": alias})
    return {"active": runtime.router.active_tier, "tiers": runtime.router.list_tiers()}


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
@router.get("/memory")
async def memory_list(request: Request, limit: int = 50, q: str = "", kind: str = ""):
    runtime = _runtime(request)
    if q:
        memories = runtime.memory.search(q, top_k=limit)
    else:
        memories = runtime.memory.list_memories(limit=limit, kind=kind)
    return {
        "memories": [m.to_dict() for m in memories],
        "stats": runtime.memory.stats(),
    }


@router.post("/memory")
async def memory_store(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required.")
    kind = str(body.get("kind", "fact"))
    tags = body.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    mem_id = runtime.memory_store(content, tags=tags)
    runtime.broadcast("memory_changed", {"id": mem_id})
    return {"id": mem_id, "ok": True}


@router.delete("/memory/{memory_id}")
async def memory_delete(request: Request, memory_id: int):
    runtime = _runtime(request)
    ok = runtime.memory_delete(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found.")
    runtime.broadcast("memory_changed", {"deleted": memory_id})
    return {"ok": True}


@router.get("/memory/search")
async def memory_search(request: Request, q: str, top_k: int = 5):
    runtime = _runtime(request)
    return {"results": runtime.memory_search(q, top_k=top_k)}


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 50):
    runtime = _runtime(request)
    return {"sessions": runtime.memory.list_sessions(limit=limit)}


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str):
    runtime = _runtime(request)
    messages = runtime.memory.get_conversation(session_id)
    return {"session_id": session_id,
            "messages": [m.to_dict() for m in messages]}


# --------------------------------------------------------------------------- #
# Scheduler / cron
# --------------------------------------------------------------------------- #
@router.get("/jobs")
async def list_jobs(request: Request):
    runtime = _runtime(request)
    return {"jobs": runtime.list_jobs()}


@router.post("/jobs")
async def add_job(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    name = str(body.get("name", "")).strip()
    schedule = str(body.get("schedule", "")).strip()
    if not name or not schedule:
        raise HTTPException(status_code=400, detail="name and schedule are required.")
    try:
        job = runtime.add_job(
            name=name,
            schedule=schedule,
            agent=str(body.get("agent", "morning_digest")),
            prompt=str(body.get("prompt", "")),
            tier=str(body.get("tier", "") or ""),
            speak=bool(body.get("speak", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.broadcast("job_added", job.to_dict())
    return job.to_dict()


@router.delete("/jobs/{job_id}")
async def delete_job(request: Request, job_id: str):
    runtime = _runtime(request)
    if not runtime.remove_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    runtime.broadcast("job_removed", {"id": job_id})
    return {"ok": True}


@router.post("/jobs/{job_id}/run")
async def run_job(request: Request, job_id: str):
    runtime = _runtime(request)
    if not runtime.scheduler.run_now(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Telemetry & traces
# --------------------------------------------------------------------------- #
@router.get("/telemetry")
async def telemetry(request: Request, window: int = 60):
    runtime = _runtime(request)
    return {
        "summary": runtime.telemetry.summary(window_minutes=window),
        "recent": runtime.telemetry.recent_requests(limit=40),
        "models": runtime.telemetry.per_model_breakdown(),
        # Energy + FLOPs per model — the efficiency half of the picture.
        "energy": runtime.telemetry.energy_summary(window_minutes=window),
    }


@router.get("/skills")
async def skills(request: Request):
    """The installed skills catalog (agentskills.io format)."""
    runtime = _runtime(request)
    store = getattr(runtime, "skills", None)
    if store is None:
        return {"enabled": False, "count": 0, "skills": []}
    data = store.catalog.to_dict()
    data["enabled"] = True
    data["dir"] = str(store.root)
    return data


@router.get("/workflows")
async def workflows(request: Request):
    """The workflow definitions available on disk."""
    import pathlib

    from ...workflow import load_workflow_dir

    runtime = _runtime(request)
    directory = pathlib.Path(
        runtime.config.home / "workflows"
    )
    graphs = load_workflow_dir(directory) if directory.is_dir() else {}
    return {
        "dir": str(directory),
        "count": len(graphs),
        "workflows": [
            {
                "name": name,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
                "valid": graph.validate()[0],
            }
            for name, graph in sorted(graphs.items())
        ],
    }


@router.get("/connectors")
async def connectors(request: Request):
    """Connector catalogue and connection state (all registered connectors)."""
    from ...connectors import available_connectors, get_connector

    runtime = _runtime(request)
    out = []
    for connector_id in available_connectors():
        connector = get_connector(connector_id)
        if connector is None:
            continue
        try:
            status = connector.status()
            status_value = status.value if hasattr(status, "value") else str(status)
        except Exception:
            status_value = "unknown"
        entry = {
            "connector": connector_id,
            "display_name": getattr(connector, "display_name", connector_id),
            "status": status_value,
            "connected": status_value == "ok",
            "documents": (
                runtime.knowledge.count(connector_id)
                if getattr(runtime, "knowledge", None) is not None
                else 0
            ),
        }
        out.append(entry)
    return {"count": len(out), "connectors": out}


@router.post("/connectors/{connector_id}/sync")
async def connectors_sync(connector_id: str, request: Request):
    """Sync one connector into the knowledge store."""
    runtime = _runtime(request)
    try:
        count = runtime.sync_connector(connector_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"connector": connector_id, "documents_indexed": count}


@router.get("/templates")
async def templates(request: Request):
    """The bundled agent templates."""
    from ...templates.agent_templates import load_template_dir

    catalogue = load_template_dir()
    return {
        "count": len(catalogue),
        "templates": [t.to_dict() for t in catalogue.values()],
    }


@router.get("/recipes")
async def recipes(request: Request):
    """The bundled recipes."""
    from ...recipes import discover_recipes, resolve_recipe

    out = []
    for name in sorted(discover_recipes()):
        recipe = resolve_recipe(name)
        if recipe is not None:
            out.append(recipe.to_dict())
    return {"count": len(out), "recipes": out}


@router.get("/mining")
async def mining(request: Request):
    """Mining provider availability for this machine."""
    from ...core.telemetry import hardware_summary
    from ...mining import detect_all
    from ...mining.providers import ensure_registered

    ensure_registered()
    capabilities = detect_all(hardware_summary(), "", "")
    return {
        "providers": {
            provider_id: cap.to_dict()
            for provider_id, cap in capabilities.items()
        },
    }


@router.get("/learning/surfaces")
async def learning_surfaces(request: Request):
    """Which learning surfaces are switched on, and what they propose."""
    runtime = _runtime(request)
    learning = runtime.config.learning
    surfaces = {}
    for name in ("routing", "intelligence", "agent", "tools", "engine"):
        surfaces[name] = {
            "policy": learning.policy_for(name),
            "enabled": learning.surface_enabled(name),
        }
    return {
        "enabled": learning.enabled,
        "optimizer": learning.optimizer,
        "weights": dict(learning.weights),
        "surfaces": surfaces,
    }


@router.get("/traces")
async def traces(request: Request, limit: int = 50, agent: str = ""):
    runtime = _runtime(request)
    items = runtime.traces.recent(limit=limit, agent=agent)
    return {
        "traces": [t.to_dict() for t in items],
        "stats": runtime.traces.stats(),
    }


@router.post("/traces/{trace_id}/rate")
async def rate_trace(request: Request, trace_id: int, body: dict[str, Any]):
    runtime = _runtime(request)
    rating = float(body.get("rating", 0))
    feedback = str(body.get("feedback", ""))
    ok = runtime.traces.rate(trace_id, rating, feedback)
    if not ok:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Learning / optimizer
# --------------------------------------------------------------------------- #
@router.get("/learning/optimize")
async def optimize(request: Request, agent: str = ""):
    runtime = _runtime(request)
    optimizer = PromptOptimizer(runtime.traces, runtime.router)
    result: dict[str, Any] = {"routing": {k: v.to_dict() for k, v in
                                          optimizer.optimize_routing().items()}}
    if agent:
        suggestion = optimizer.optimize_prompt(agent)
        result["suggestion"] = suggestion.to_dict() if suggestion else None
        result["advice"] = optimizer.advise(agent)
    return result


# --------------------------------------------------------------------------- #
# Settings editor
# --------------------------------------------------------------------------- #
@router.get("/config")
async def get_config(request: Request):
    runtime = _runtime(request)
    path = Path(runtime.config.config_path)
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    return {
        "path": str(path),
        "toml": raw,
        "parsed": runtime.config.raw,
        "exists": path.exists(),
    }


@router.put("/config")
async def put_config(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    toml_text = str(body.get("toml", ""))
    path = Path(runtime.config.config_path)
    try:
        import tomllib

        parsed = tomllib.loads(toml_text)
    except Exception as exc:
        raise HTTPException(status_code=400,
                            detail=f"Invalid TOML: {exc}") from exc

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(toml_text, encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not write config: {exc}") from exc

    runtime.broadcast("config_reloaded", {"path": str(path)})
    return {"ok": True, "path": str(path), "keys": sorted(parsed.keys())}


@router.post("/config/reload")
async def reload_config(request: Request):
    runtime = _runtime(request)
    from ...core.config import reload_config as _reload

    try:
        new_cfg = _reload(runtime.config.config_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.broadcast("config_reloaded", {"path": str(new_cfg.config_path)})
    return {"ok": True, "path": str(new_cfg.config_path)}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
@router.get("/tools")
async def list_tools(request: Request):
    runtime = _runtime(request)
    return {
        "tools": runtime.tools.describe(),
        "namespaces": runtime.tools.namespaces(),
        "history": runtime.tools.history(limit=25),
    }


@router.post("/tools/{name}/execute")
async def execute_tool(request: Request, name: str, body: dict[str, Any]):
    runtime = _runtime(request)
    args = body.get("arguments") or {}
    result = runtime.tools.execute(name, args, approved=True)
    runtime.broadcast("tool_executed", result.to_dict())
    return result.to_dict()


# --------------------------------------------------------------------------- #
# MCP
# --------------------------------------------------------------------------- #
@router.get("/mcp")
async def mcp_status(request: Request):
    runtime = _runtime(request)
    return {"servers": runtime.mcp.status()}


@router.post("/mcp/connect")
async def mcp_connect(request: Request):
    runtime = _runtime(request)
    results = runtime.mcp.connect_all()
    runtime.broadcast("mcp_status", {"results": results})
    return {"results": results}


# --------------------------------------------------------------------------- #
# Voice
# --------------------------------------------------------------------------- #
@router.get("/voice")
async def voice_status(request: Request):
    runtime = _runtime(request)
    return runtime.voice.status() if runtime.voice else {"enabled": False}


@router.post("/voice/speak")
async def voice_speak(request: Request, body: dict[str, Any]):
    runtime = _runtime(request)
    if not runtime.voice:
        raise HTTPException(status_code=503, detail="Voice is not enabled.")
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required.")
    result = runtime.voice.speak(text)
    runtime.broadcast("voice_state", {"spoken": text[:300]})
    return result


@router.post("/voice/listen")
async def voice_listen(request: Request):
    runtime = _runtime(request)
    if not runtime.voice:
        raise HTTPException(status_code=503, detail="Voice is not enabled.")
    text = runtime.voice.listen_once()
    return {"text": text}


# --------------------------------------------------------------------------- #
# Models — the catalog, local inventory, and downloads
# --------------------------------------------------------------------------- #
@router.get("/models")
async def models_catalog(
    request: Request,
    engine: str | None = None,
    max_ram: float | None = None,
):
    """The catalog of models C.A.R.L.A. knows how to fetch."""
    from ...intelligence.model_catalog import list_models

    specs = list_models(engine=engine, min_ram_gb=max_ram)
    return {
        "models": [s.to_dict() for s in specs],
        "count": len(specs),
    }


@router.get("/models/local")
async def models_local(request: Request):
    """Models already present on this machine.

    Includes which one is running right now, and any downloads that
    never finished, so the Model Manager can offer to clean them up.
    """
    from ...intelligence.model_manager import local_models
    from ...intelligence.model_lifecycle import lifecycle

    models = local_models()
    running = lifecycle.status().get("model_id", "")

    for entry in models:
        entry["running"] = bool(running) and entry.get("model_id") == running

    return {
        "models": models,
        "running": running,
        "partial": lifecycle.partial_downloads(),
    }


@router.get("/models/ram")
async def models_ram(request: Request):
    """How much memory this machine has, and how much is free.

    The Model Manager uses this to warn before a download that would
    not fit — ONNX Runtime's peak memory is roughly 2.6x the graph's
    own size.
    """
    ram_gb = 0.0
    free_gb = 0.0
    try:
        import psutil

        memory = psutil.virtual_memory()
        ram_gb = round(memory.total / 1e9, 1)
        free_gb = round(memory.available / 1e9, 1)
    except Exception:
        pass

    return {
        "ram_gb": ram_gb,
        "free_gb": free_gb,
        "peak_multiplier": 2.6,
    }


@router.get("/models/recommend")
async def models_recommend(request: Request, limit: int = 5):
    """Suggest models that fit this machine's RAM and VRAM."""
    from ...intelligence.model_catalog import recommend_models
    from ...intelligence.model_manager import disk_free_gb

    ram_gb = 0.0
    vram_gb = 0.0
    try:
        import psutil

        ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    try:
        from ...core.energy import hardware_summary

        hw = hardware_summary()
        vram_gb = float(getattr(hw, "vram_gb", 0.0) or 0.0)
    except Exception:
        pass

    specs = recommend_models(ram_gb, vram_gb, limit=limit)
    free = disk_free_gb()
    return {
        "ram_gb": ram_gb,
        "vram_gb": vram_gb,
        "disk_free_gb": free,
        "models": [
            {**s.to_dict(), "fits_disk": s.disk_size_gb() <= free}
            for s in specs
        ],
    }


@router.post("/models/pull")
async def models_pull(request: Request, body: dict[str, Any]):
    """Download a model. This is a long call — it runs to completion."""
    from ...intelligence.model_manager import pull_model

    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")
    engine = str(body.get("engine", "ollama")).strip() or "ollama"

    result = pull_model(model_id, engine)
    return result.to_dict()


@router.delete("/models/local/{model_id}")
async def models_remove(request: Request, model_id: str):
    """Delete a downloaded model from disk."""
    from ...intelligence.model_manager import delete_local_model

    engine = request.query_params.get("engine", "llamacpp")
    return delete_local_model(model_id, engine).to_dict()


@router.post("/models/delete")
async def models_delete(request: Request, body: dict[str, Any]):
    """Delete a downloaded model. The POST twin of the DELETE route."""
    from ...intelligence.model_manager import delete_local_model

    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")
    engine = str(body.get("engine", "llamacpp")).strip() or "llamacpp"
    return delete_local_model(model_id, engine).to_dict()


@router.post("/models/start")
async def models_start(request: Request, body: dict[str, Any]):
    """Load a downloaded model into memory and start using it.

    The model stays resident so the next message is instant. Only one
    native model runs at a time — that is all the RAM budget allows.
    """
    from ...intelligence.model_lifecycle import lifecycle

    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")

    result = lifecycle.start(model_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=result.get("message", "could not start")
        )
    return result


@router.post("/models/stop")
async def models_stop(request: Request, body: dict[str, Any]):
    """Unload the running model and free the memory it was using."""
    from ...intelligence.model_lifecycle import lifecycle

    model_id = str(body.get("model_id", "")).strip()
    return lifecycle.stop(model_id)


@router.post("/models/custom")
async def models_custom(request: Request, body: dict[str, Any]):
    """Download a model that is not in the catalog (HF repo or Ollama tag)."""
    from ...intelligence.model_manager import pull_custom

    result = pull_custom(
        str(body.get("engine", "ollama")),
        str(body.get("ref", "")),
        str(body.get("filename", "")),
    )
    return result.to_dict()


@router.post("/models/free")
async def models_free(request: Request):
    """Free memory: unload every loaded model (native and Ollama)."""
    from ...intelligence.model_lifecycle import lifecycle

    return lifecycle.free_memory()


@router.get("/models/device")
async def models_device_get(request: Request):
    """GPU support for the native engine, and the current device setting."""
    from ...providers.adapters.native_onnx import gpu_status

    return gpu_status()


@router.post("/models/device")
async def models_device_set(request: Request, body: dict[str, Any]):
    """Set the native engine's device: cpu, gpu (DirectML) or auto."""
    from ...intelligence.model_manager import set_native_device

    result = set_native_device(str(body.get("device", "")))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "bad device"))
    return result


@router.post("/models/auto")
async def models_auto(request: Request):
    """Pick the best model for this PC, download it and start it.

    Prefers a GGUF model on C.A.R.L.A.'s own llama.cpp engine (installing
    the engine first if needed); falls back to the small built-in ONNX
    models if the engine cannot be installed.
    """
    from ...intelligence.llama_engine import find_binary, install_engine
    from ...intelligence.model_lifecycle import lifecycle
    from ...intelligence.model_manager import (
        activate_gguf_model, activate_native_model, best_gguf_model,
        best_native_model, pull_model,
    )

    gguf = best_gguf_model()
    if gguf is not None and (find_binary() is not None or install_engine("cpu").get("ok")):
        pulled = pull_model(gguf.model_id, "native_gguf")
        if pulled.ok:
            activate_gguf_model(gguf.model_id, "fast_tier")
            started = lifecycle.start(gguf.model_id)
            return {"ok": bool(started.get("ok")), "model_id": gguf.model_id,
                    "message": started.get("message", "")}
    spec = best_native_model()
    if spec is None:
        raise HTTPException(status_code=400, detail="not enough free RAM for any local model")
    pulled = pull_model(spec.model_id, "native_onnx")
    if not pulled.ok:
        raise HTTPException(status_code=400, detail=pulled.message)
    activate_native_model(spec.model_id, "fast_tier")
    started = lifecycle.start(spec.model_id)
    return {"ok": bool(started.get("ok")), "model_id": spec.model_id,
            "message": started.get("message", "")}


@router.get("/engine")
async def engine_info(request: Request):
    """Is the llama.cpp engine installed, and is a model running on it?"""
    from ...intelligence.llama_engine import engine_status

    return engine_status()


@router.post("/engine/install")
async def engine_install(request: Request, body: dict[str, Any]):
    """Download the official llama.cpp build (vulkan | cpu | hip). Long call."""
    from ...intelligence.llama_engine import install_engine

    result = install_engine(str(body.get("backend", "vulkan")))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "install failed"))
    return result


@router.get("/models/cloud/openrouter")
async def openrouter_models(request: Request, free_only: bool = False):
    """OpenRouter's model list (public); ``free_only`` keeps zero-price ones."""
    from ...providers.adapters.openrouter import list_openrouter_models, stored_key

    models = list_openrouter_models()
    if free_only:
        models = [m for m in models if m["free"]]
    return {"models": models, "has_key": bool(stored_key())}


@router.post("/models/cloud")
async def models_cloud(request: Request, body: dict[str, Any]):
    """Use a hosted model (OpenRouter). The key goes to the encrypted vault."""
    from ...intelligence.model_manager import add_cloud_tier

    result = add_cloud_tier(
        str(body.get("provider", "openrouter")), str(body.get("model", "")),
        tier=str(body.get("tier", "deep_tier")) or "deep_tier",
        api_key=str(body.get("api_key", "")),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed"))
    return result


@router.post("/models/remote")
async def models_remote(request: Request, body: dict[str, Any]):
    """Use a model served by another PC (Ollama or an OpenAI-compatible server)."""
    from ...intelligence.model_manager import add_remote_tier

    result = add_remote_tier(
        str(body.get("host", "")), str(body.get("model", "")),
        tier=str(body.get("tier", "deep_tier")) or "deep_tier",
        kind=str(body.get("kind", "ollama")), port=int(body.get("port") or 0),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "failed"))
    return result


@router.get("/backup")
async def backup_download(request: Request, include_vault: bool = False):
    """Download a backup zip of your config, memory, sessions, knowledge and skills."""
    import time as _t

    from fastapi.responses import Response

    from ...core.backup import create_backup

    runtime = _runtime(request)
    data = create_backup(runtime.config.home, include_vault=include_vault)
    name = _t.strftime("carla-backup-%Y%m%d-%H%M%S.zip")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/restore")
async def backup_restore(request: Request):
    """Restore a backup zip sent as the raw request body."""
    from ...core.backup import restore_backup

    runtime = _runtime(request)
    result = restore_backup(runtime.config.home, await request.body())
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/export")
async def export_conversations(
    request: Request, format: str = "markdown", session_id: str = "", limit: int = 50,
):
    """Download conversations as markdown/json, or format=training for JSONL."""

    from ...core.exports import export_sessions, export_training_jsonl

    runtime = _runtime(request)
    if format == "training":
        text, kind = export_training_jsonl(runtime.memory, limit=limit), "application/x-ndjson"
    elif format in ("markdown", "json"):
        text = export_sessions(runtime.memory, format, session_id, limit)
        kind = "application/json" if format == "json" else "text/markdown"
    else:
        raise HTTPException(status_code=400, detail="format must be markdown, json or training")
    return PlainTextResponse(text, media_type=kind)


@router.get("/models/status")
async def models_status(request: Request):
    """What is running right now."""
    from ...intelligence.model_lifecycle import lifecycle

    return lifecycle.status()


@router.get("/models/partial")
async def models_partial(request: Request):
    """Downloads that never finished — so they can be cleaned up."""
    from ...intelligence.model_lifecycle import lifecycle

    return {"partial": lifecycle.partial_downloads()}


@router.post("/models/partial/clear")
async def models_partial_clear(request: Request, body: dict[str, Any]):
    """Delete partial downloads.

    With a model_id it removes that one; without it, it clears all of
    them.
    """
    from ...intelligence.model_lifecycle import lifecycle

    model_id = str(body.get("model_id", "")).strip()
    result = lifecycle.delete_partial(model_id)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "could not clean up"),
        )
    return result


@router.post("/models/activate")
async def models_activate(request: Request, body: dict[str, Any]):
    """Point a capability tier at a native model.

    Native models run inside C.A.R.L.A.'s own process, so activating
    one is all it takes — no Ollama, no daemon, nothing to start.
    """
    from ...intelligence.model_manager import (
        activate_gguf_model, activate_native_model, find_gguf, native_model_dir,
    )

    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required.")
    tier = str(body.get("tier", "fast_tier")).strip() or "fast_tier"

    if find_gguf(model_id) is not None and not native_model_dir(model_id).exists():
        result = activate_gguf_model(model_id, tier)
    else:
        result = activate_native_model(model_id, tier)
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=result.get("error", "could not activate")
        )
    return result
