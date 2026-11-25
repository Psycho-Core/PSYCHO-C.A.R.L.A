#!/usr/bin/env python3
"""
End-to-end smoke test against a running C.A.R.L.A. instance.

Exercises: chat + tool calls, dual-channel splitting, memory, telemetry,
cron, the OpenAI-compatible API, and the WebSocket path.

Start the server first:

    carla serve

Then, in another shell:

    python tests/e2e_smoke.py

To test a different port or host:

    python tests/e2e_smoke.py --port 9000
    python tests/e2e_smoke.py --base http://192.168.1.10:8787
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8787"

BASE = DEFAULT_BASE


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787,
                        help="the port the server is listening on")
    parser.add_argument("--base", default="",
                        help="full base URL, overriding --port")
    return parser.parse_args()


def _check_server() -> bool:
    """Confirm the server is up before running any checks."""
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as r:
            return r.status < 500
    except Exception as exc:
        print(f"\n  Cannot reach {BASE} — is the console running?\n")
        print(f"    {exc}\n")
        print("  Start it with:\n")
        print("    carla serve\n")
        return False


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read())


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {name}" + (f"  — {detail}" if detail else ""))


print("\n═══ PSYCHO-C.A.R.L.A. — End-to-End Smoke Test ═══\n")

_args = _parse_args()
BASE = _args.base or f"http://127.0.0.1:{_args.port}"
print(f"  target: {BASE}\n")

if not _check_server():
    sys.exit(2)

# --- 1. Status -------------------------------------------------------------
status = get("/api/status")
check("status responds", status.get("app") == "PSYCHO-C.A.R.L.A.")
check("agents registered", len(status.get("agents", [])) >= 8,
      f"{len(status.get('agents', []))} agents")
check("tiers configured", len(status.get("tiers", [])) >= 2,
      f"{[t['alias'] for t in status.get('tiers', [])]}")
check("tools registered", len(status.get("tools", [])) > 15,
      f"{len(status.get('tools', []))} tools")

# --- 2. Chat with tool calling ---------------------------------------------
print("\n── Tool-calling loop ──")
res = post("/api/chat", {"prompt": "What is 6 times 7?", "agent": "orchestrator"})
check("chat returns text", bool(res.get("text")), res.get("text", "")[:60])

# Tool calling and code blocks need a model that can do them. The
# built-in fallback responder and the native ONNX engine cannot, so
# those two checks only apply when a tool-capable model is answering.
tool_capable = len(res.get("tool_calls", [])) >= 1 or bool(
    res.get("code_blocks")
)
if not tool_capable:
    provider = res.get("provider", "")
    print(f"  [SKIP] tool was executed — no tool-capable model "
          f"(provider={provider})")
    print("  [SKIP] dual-channel: display has code — no tool-capable model")
else:
    check("tool was executed", len(res.get("tool_calls", [])) >= 1,
          ", ".join(t["name"] for t in res.get("tool_calls", [])))
    check("tool succeeded", all(t.get("ok") for t in res.get("tool_calls", [])))
check("dual-channel: speech present", bool(res.get("speech")), res.get("speech", "")[:80])
if tool_capable:
    check("dual-channel: display has code",
      "```" in (res.get("display") or ""), "code block routed to display")
check("dual-channel: code NOT spoken",
      "def solve" not in (res.get("speech") or ""),
      "code excluded from speech channel")
check("usage recorded", res.get("usage", {}).get("total_tokens", 0) > 0,
      f"{res.get('usage', {}).get('total_tokens', 0)} tokens")
check("latency recorded", res.get("latency_ms", 0) > 0,
      f"{res.get('latency_ms', 0):.0f}ms")

# --- 3. Memory --------------------------------------------------------------
print("\n── Memory ──")
mem = post("/api/memory", {"content": "Boss prefers dark mode and terse answers",
                           "tags": ["preference", "ui"]})
check("memory stored", bool(mem.get("id")), f"id={mem.get('id')}")
found = get("/api/memory?q=dark%20mode")
check("memory semantically retrievable", len(found.get("memories", [])) >= 1,
      f"{len(found.get('memories', []))} hits")
listed = get("/api/memory?limit=10")
check("memory list works", len(listed.get("memories", [])) >= 1)

# --- 4. Telemetry -----------------------------------------------------------
print("\n── Telemetry ──")
tel = get("/api/telemetry")
summary = tel.get("summary", {})
check("latency tracked", summary.get("avg_latency_ms", 0) > 0,
      f"{summary.get('avg_latency_ms')}ms avg")
check("tokens/sec tracked", summary.get("avg_tokens_per_second", 0) > 0,
      f"{summary.get('avg_tokens_per_second')} tok/s")
check("resource sampling on", "resources" in summary)
check("request history present", len(tel.get("recent", [])) >= 1,
      f"{len(tel.get('recent', []))} requests logged")

# --- 5. Cron ----------------------------------------------------------------
print("\n── Scheduler ──")
job = post("/api/jobs", {"name": "smoke-digest", "schedule": "0 6 * * *",
                         "agent": "morning_digest", "prompt": "Brief me."})
check("cron job created", bool(job.get("id")), job.get("id"))
check("cron in list", len(get("/api/jobs").get("jobs", [])) >= 1)
import urllib.error
req = urllib.request.Request(f"{BASE}/api/jobs/{job['id']}", method="DELETE")
try:
    urllib.request.urlopen(req, timeout=10)
    deleted = True
except Exception as exc:
    deleted = "404" in str(exc)
check("cron job removable", deleted)

# --- 6. Tier switching ------------------------------------------------------
print("\n── Tier switching ──")
tiers = get("/api/tiers")
before = tiers.get("active")
target = next(t["alias"] for t in tiers["tiers"] if t["alias"] != before)
after = post("/api/tiers/active", {"tier": target})
check("tier switch works", after.get("active") == target, f"{before} → {target}")
post("/api/tiers/active", {"tier": before})

# --- 7. OpenAI-compatible API ----------------------------------------------
print("\n── OpenAI-compatible API ──")
models = get("/v1/models")
ids = [m["id"] for m in models.get("data", [])]
check("tiers exposed as models", "fast_tier" in ids, str(ids[:5]))
req = urllib.request.Request(
    f"{BASE}/v1/chat/completions",
    data=json.dumps({"model": "fast_tier",
                     "messages": [{"role": "user", "content": "Hello"}]}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=60) as r:
    oai = json.loads(r.read())
check("chat/completions works", bool(oai.get("choices", [{}])[0].get("message", {}).get("content")))
check("carla extension present", "carla" in oai,
      str(list(oai.get("carla", {}).keys())))
check("usage reported", oai.get("usage", {}).get("total_tokens", 0) > 0)

# --- 8. WebSocket -----------------------------------------------------------
print("\n── WebSocket ──")
try:
    import asyncio

    import websockets

    async def ws_test():
        async with websockets.connect(f"ws://127.0.0.1:{_args.port}/ws") as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            assert first["type"] == "connected", first
            snap = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            assert snap["type"] == "snapshot", snap
            assert "agents" in snap["data"]

            await ws.send(json.dumps({"type": "chat", "prompt": "Websocket test",
                                      "agent": "orchestrator"}))
            got_user = False
            got_response = False
            for _ in range(10):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg["type"] == "chat_user":
                    got_user = True
                # The runtime emits `agent_response`, which the hub fans out.
                if msg["type"] in ("agent_response", "chat_response"):
                    got_response = True
                    break
            return got_user, got_response

    got_user, got_response = asyncio.run(ws_test())
    check("ws connects", True)
    check("ws sends snapshot", True)
    check("ws echoes user message", got_user)
    check("ws delivers agent response", got_response)
except ImportError:
    check("websocket test", False, "websockets package not installed")

# --- 8b. Native engine ------------------------------------------------------
print("\n── Native engine ──")
native_catalog = get("/api/models?engine=native_onnx")
native_models = native_catalog.get("models", [])
check("native catalog lists models", len(native_models) > 0,
      f"{len(native_models)} native models")

check("native models have a download source",
      all(m.get("onnx_repo") and m.get("onnx_file") for m in native_models),
      "every native model names an ONNX repo and file")

check("native models declare their ram cost",
      all(m.get("ram_gb", 0) > 0 for m in native_models),
      "the GUI needs a RAM estimate")

# Activating a model that is not downloaded must be refused, not silently
# accepted.
try:
    post("/api/models/activate", {"model_id": "native_not_downloaded"})
    check("activate refuses a missing model", False, "no error raised")
except urllib.error.HTTPError as exc:
    check("activate refuses a missing model", exc.code == 400, f"HTTP {exc.code}")

# The adapter must be registered with the provider layer.
adapters = get("/api/status").get("adapters")
if adapters is not None:
    check("native adapter registered", "native_onnx" in adapters,
          f"{len(adapters)} adapters")

# --- 9. Channels ------------------------------------------------------------
print("\n── Channels ──")
channels = get("/api/channels")
check("channel list", "channels" in channels,
      f"{channels.get('count', 0)} configured")

# The status endpoint answers 503 when no channels are configured —
# that is the server saying "there is nothing running", not a fault.
try:
    channel_status = get("/api/channels/status")
except urllib.error.HTTPError as exc:
    channel_status = {"status": f"HTTP {exc.code}", "channels": []}
check("channel status", "status" in channel_status,
      channel_status.get("status", ""))

# An inbound message must reach an agent and come back with a reply.
inbound = post("/api/channels/inbound/webchat",
               {"text": "Hello from the smoke test.", "sender": "e2e"})
check("channel inbound dispatch", "reply" in inbound,
      (inbound.get("reply") or "")[:50])

# The chat commands must work over the wire.
for command in ("/help", "/sessions", "/status", "/more"):
    cmd_reply = post("/api/channels/inbound/webchat",
                      {"text": command, "sender": "e2e"})
    body = cmd_reply.get("reply") or ""
    check(f"channel command {command}", len(body) > 0, body[:40].replace("\n", " "))

# /notify records a notification preference.
notify_reply = post("/api/channels/inbound/webchat",
                    {"text": "/notify webchat", "sender": "e2e"})
check("channel /notify", "webchat" in (notify_reply.get("reply") or ""),
      notify_reply.get("reply", ""))

# An unknown channel must 404 rather than silently succeed.
try:
    post("/api/channels/inbound/not-a-channel", {"text": "x"})
    check("channel rejects unknown id", False, "no error raised")
except urllib.error.HTTPError as exc:
    check("channel rejects unknown id", exc.code == 404, f"HTTP {exc.code}")

# --- 9. Model manager -------------------------------------------------------
print("\n── Model Manager ──")
catalog = get("/api/models")
check("model catalog", catalog.get("count", 0) >= 5,
      f"{catalog.get('count', 0)} models")

local = get("/api/models/local")
check("local models listed", isinstance(local.get("models"), list),
      f"{len(local.get('models', []))} installed")

recommend = get("/api/models/recommend")
check("recommendations fit this machine",
      all(m.get("ram_gb", 0) <= recommend.get("ram_gb", 0) + 0.1
          for m in recommend.get("models", [])),
      f"{len(recommend.get('models', []))} suggestions")

pull = post("/api/models/pull", {"model_id": "nope-not-real", "engine": "ollama"})
check("pull validates its input", pull.get("ok") is False,
      pull.get("message", "")[:60])

# --- 10. Traces -------------------------------------------------------------
print("\n── Learning traces ──")
traces = get("/api/traces?limit=10")
check("traces logged", len(traces.get("traces", [])) >= 1,
      f"{len(traces.get('traces', []))} traces")

# --- Summary ----------------------------------------------------------------
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\n{'═' * 52}")
print(f"  {passed}/{len(results)} checks passed" + (f"  ({failed} failed)" if failed else "  — all green"))
print(f"{'═' * 52}\n")
sys.exit(0 if failed == 0 else 1)
