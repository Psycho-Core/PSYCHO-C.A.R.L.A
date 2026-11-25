"""
PSYCHO-C.A.R.L.A. — FastAPI application.

Assembles the runtime, mounts the HUD API, the OpenAI-compatible API, the
WebSocket hub and the static holographic frontend.

Run with:  carla serve  (or: uvicorn psycho_carla.server.app:app)

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import CarlaConfig, load_config
from ..core.runtime import CarlaRuntime
from .api import channels, hud, openai_compat, upstream
from .auth import check_key, extract_key, resolve_auth
from .hub import WebSocketHub

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"
TEMPLATE_DIR = WEB_DIR / "templates"

# Voice samples live outside the package so they can be regenerated without
# reinstalling. Look for them next to the project root first, then under the
# runtime home.
def _voice_samples_dir(cfg_home: Path | None = None) -> Path:
    candidates = [
        Path.cwd() / "voice_samples",
        Path(__file__).resolve().parents[3] / "voice_samples",
    ]
    if cfg_home:
        candidates.append(cfg_home / "voice_samples")
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def create_app(
    config: CarlaConfig | None = None,
    *,
    runtime: CarlaRuntime | None = None,
    start_runtime: bool = True,
) -> FastAPI:
    """Build the C.A.R.L.A. web application."""
    if config is None and runtime is None:
        from ..core.config import ensure_config

        ensure_config()
    cfg = config or load_config()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = runtime or CarlaRuntime(cfg)
        app.state.runtime = rt
        app.state.hub = WebSocketHub()
        app.state.hub.bind_loop(asyncio.get_running_loop())
        rt.hub = app.state.hub
        # Attaching the hub to the runtime is enough: `runtime.emit()` fans
        # out to it. A listener that broadcast again would deliver every
        # event to clients twice.
        if start_runtime:
            rt.start()
        # Push telemetry samples to the HUD.
        async def telemetry_pusher():
            while True:
                await asyncio.sleep(1.0)
                try:
                    summary = rt.telemetry.summary(window_minutes=15)
                    await app.state.hub.broadcast_async("telemetry", summary)
                except Exception:
                    continue

        pusher = asyncio.create_task(telemetry_pusher())
        try:
            yield
        finally:
            pusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pusher
            if runtime is None:
                rt.stop()

    app = FastAPI(
        title="PSYCHO-C.A.R.L.A.",
        description=(
            "Personal AI on personal devices — a holographic "
            "command console for the PSYCHO-C.A.R.L.A. agent framework."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Authentication -------------------------------------------------------- #
    # Refuse to expose an agent (with shell + file tools) on the network
    # without a key. Loopback binds are exempt.
    auth = resolve_auth(
        host=cfg.server.host,
        port=cfg.server.port,
        api_key_env=getattr(cfg.server, "api_key_env", ""),
        api_key=getattr(cfg.server, "api_key", ""),
    )
    app.state.auth = auth

    if auth.enabled:
        @app.middleware("http")
        async def _auth_middleware(request: Request, call_next):
            if request.url.path in ("/healthz", "/docs", "/openapi.json", "/redoc"):
                return await call_next(request)
            presented = extract_key(
                authorization=request.headers.get("authorization", ""),
                x_api_key=request.headers.get("x-api-key", ""),
            )
            if not check_key(presented, auth.api_key):
                return JSONResponse(
                    {"detail": "Invalid or missing API key"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

    # -- Rate limiting (security.rate_limit_enabled) --------------------------- #
    if cfg.security.rate_limit_enabled:
        from ..security.rate_limit import RateLimiter

        limiter = RateLimiter(
            rate_per_minute=cfg.security.rate_limit_rpm,
            burst=cfg.security.rate_limit_burst,
        )
        app.state.rate_limiter = limiter

        @app.middleware("http")
        async def _rate_limit_middleware(request: Request, call_next):
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return JSONResponse(
                    {"detail": "Rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            return await call_next(request)

    # -- API ---------------------------------------------------------------- #
    app.include_router(hud.router, prefix="/api", tags=["hud"])
    app.include_router(channels.router, prefix="/api", tags=["channels"])
    app.include_router(openai_compat.router, prefix=cfg.server.openai_compat_prefix,
                       tags=["openai"])
    app.include_router(upstream.router, tags=["upstream-v1"])

    # -- WebSocket ------------------------------------------------------------ #
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # WebSockets cannot use the HTTP auth middleware, so check here.
        if auth.enabled:
            presented = extract_key(
                authorization=websocket.headers.get("authorization", ""),
                x_api_key=websocket.headers.get("x-api-key", ""),
            )
            query_key = websocket.query_params.get("api_key", "")
            if not check_key(presented or query_key, auth.api_key):
                await websocket.close(code=4401, reason="Invalid or missing API key")
                return
        hub: WebSocketHub = app.state.hub
        await hub.connect(websocket)
        rt: CarlaRuntime = app.state.runtime
        try:
            await websocket.send_json({
                "type": "snapshot",
                "ts": time.time(),
                "data": rt.status(),
            })
            while True:
                # Receive exactly one frame. Reading text and then JSON would
                # consume two frames and desynchronise the stream.
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                raw = message.get("text")
                if raw is None:
                    raw = (message.get("bytes") or b"").decode("utf-8", "ignore")
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                await _handle_ws_message(app, msg, websocket)
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            await hub.disconnect(websocket)

    # -- Static frontend -------------------------------------------------------- #
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Voice samples — regenerated by tools/make_voice_samples.py.
    samples_dir = _voice_samples_dir(getattr(cfg, "home", None))
    if samples_dir.is_dir():
        app.mount(
            "/voice_samples",
            StaticFiles(directory=str(samples_dir)),
            name="voice_samples",
        )

    @app.get("/", include_in_schema=False)
    async def index():
        index_file = TEMPLATE_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse({"error": "HUD template not found"}, status_code=500)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        fav = STATIC_DIR / "favicon.svg"
        if fav.exists():
            return FileResponse(str(fav))
        return JSONResponse({}, status_code=404)

    @app.get("/voice", include_in_schema=False)
    async def voice_audition():
        """Audition page for the TTS voices — see /voice_samples/."""
        page = TEMPLATE_DIR / "voice.html"
        if page.exists():
            return FileResponse(str(page))
        return JSONResponse({"error": "voice template not found"}, status_code=500)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        rt = getattr(app.state, "runtime", None)
        return {"ok": rt is not None, "app": "PSYCHO-C.A.R.L.A."}

    # -- A2A (agent-to-agent) ------------------------------------------------ #
    # Other agents call C.A.R.L.A. through the Google A2A protocol:
    # discover at /.well-known/agent.json, send tasks to /a2a/tasks.
    a2a_server = _build_a2a_server(cfg, lambda: getattr(app.state, "runtime", None))
    app.state.a2a = a2a_server

    @app.get("/.well-known/agent.json", include_in_schema=False)
    async def a2a_agent_card():
        return a2a_server.agent_card.to_dict()

    @app.post("/a2a/tasks", include_in_schema=False)
    async def a2a_tasks(request: Request):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": "",
                 "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )
        if not isinstance(payload, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": "",
                 "error": {"code": -32600, "message": "Invalid Request"}},
                status_code=400,
            )
        token = extract_key(
            authorization=request.headers.get("authorization", ""),
            x_api_key=request.headers.get("x-api-key", ""),
        )
        return a2a_server.handle_request(payload, token=token or None)

    return app


def _build_a2a_server(cfg: CarlaConfig, runtime_getter: Any):
    """Build the A2A server from configuration.

    ``runtime_getter`` is called at request time — the runtime is created in
    the app's lifespan, so it does not exist when the app is built.
    """
    from ..a2a import A2AServer, AgentCard

    server_cfg = getattr(cfg, "server", None)
    agent_name = str(getattr(server_cfg, "a2a_agent", "") or "")
    token = str(getattr(server_cfg, "a2a_token", "") or "")
    # Fall back to the server API key so A2A inherits the same guard.
    if not token:
        token = str(getattr(server_cfg, "api_key", "") or "")
    if not token:
        from ..server.auth import load_api_key

        token = load_api_key()

    card = AgentCard(
        name="PSYCHO-C.A.R.L.A.",
        description=(
            "Personal AI on personal devices — an agent framework with shell, "
            "file, web and memory tools running entirely on your hardware."
        ),
        url=f"http://{getattr(server_cfg, 'host', '127.0.0.1')}:"
            f"{getattr(server_cfg, 'port', 8787)}",
        version="1.0.0",
        capabilities=["chat", "tools", "memory", "skills"],
        skills=["orchestrator", "code_assist", "research"],
    )

    def _handler(input_text: str) -> str:
        rt = runtime_getter()
        if rt is None:
            return "PSYCHO-C.A.R.L.A. is not running."
        result = rt.ask(input_text, agent=agent_name or None)
        return str(result.get("text", "")) if isinstance(result, dict) else str(result)

    return A2AServer(card, handler=_handler, auth_token=token or None)


async def _handle_ws_message(app: FastAPI, msg: dict[str, Any],
                             websocket: WebSocket) -> None:
    """Route an inbound WebSocket message."""
    rt: CarlaRuntime = app.state.runtime
    hub: WebSocketHub = app.state.hub
    mtype = str(msg.get("type", ""))

    if mtype == "chat":
        prompt = str(msg.get("prompt", "")).strip()
        if not prompt:
            return
        agent = str(msg.get("agent", "") or "")
        tier = str(msg.get("tier", "") or "")
        session_id = str(msg.get("session_id", "default"))

        await hub.broadcast_async("chat_user", {
            "prompt": prompt, "agent": agent, "session_id": session_id,
        })
        await hub.broadcast_async("agent_status", {
            "agent": agent or rt.config.agent.default_agent, "state": "working",
        })

        # `runtime.ask` already emits an `agent_response` event, which the hub
        # fans out. Broadcasting here too would render the message twice.
        result = await asyncio.to_thread(
            rt.ask, prompt, agent=agent, tier=tier, session_id=session_id
        )
        await hub.broadcast_async("agent_status", {
            "agent": agent or rt.config.agent.default_agent, "state": "idle",
        })
        _ = result

    elif mtype == "set_tier":
        alias = str(msg.get("tier", ""))
        if rt.router.set_active_tier(alias):
            await hub.broadcast_async("tier_changed", {"tier": alias})

    elif mtype == "voice_speak":
        text = str(msg.get("text", ""))
        if rt.voice and text:
            await asyncio.to_thread(rt.voice.speak, text)

    elif mtype == "ping":
        await hub.broadcast_async("pong", {"ts": time.time()})

    elif mtype == "status":
        await websocket.send_json({"type": "snapshot", "ts": time.time(),
                                   "data": rt.status()})


app = create_app()
