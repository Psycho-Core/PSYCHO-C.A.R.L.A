#!/usr/bin/env python3
"""
Export the C.A.R.L.A. HUD as a single self-contained HTML file.

The live HUD is a FastAPI + WebSocket app, but a static export is useful for
reviewing the visual design, sharing a mockup, or opening the UI on a machine
that cannot reach the server. The export inlines the CSS and JS and ships a
small mock adapter that fakes the REST/WS calls with realistic data.

Usage:
    python3 tools/export_static_hud.py [output.html]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "psycho_carla" / "server" / "web"
TEMPLATE = WEB / "templates" / "index.html"
CSS = WEB / "static" / "carla.css"
JS = WEB / "static" / "carla.js"


MOCK = r"""
/* ------------------------------------------------------------------ *
 *  MOCK ADAPTER - static export only.
 *  Fakes /api/* and /ws so the HUD renders fully offline.
 * ------------------------------------------------------------------ */
const MOCK_AGENTS = [
  {name:"orchestrator",     description:"Plans multi-step work, delegates to specialists.", mode:"on_demand"},
  {name:"operative",        description:"Continuous autonomous task execution.",          mode:"continuous"},
  {name:"deep_research",    description:"Multi-hop research with cited synthesis.",        mode:"on_demand"},
  {name:"morning_digest",   description:"Daily briefing - spoken, summary.",               mode:"scheduled"},
  {name:"code_assist",      description:"Code generation, execution, and review.",        mode:"on_demand"},
  {name:"monitor_operative",description:"Long-horizon monitoring and alerting.",             mode:"continuous"},
  {name:"memory_curator",   description:"Maintains the memory store and prunes noise.",     mode:"scheduled"},
  {name:"scheduler",        description:"Manages scheduled jobs and reminders.",            mode:"continuous"},
];
const MOCK_TIERS = [
  {alias:"fast_tier",   provider:"openai_compat", model:"mock-model",    description:"Default workhorse. Local inference."},
  {alias:"deep_tier",   provider:"ollama",        model:"local-deep",    description:"Deep reasoning. Research, planning, hard analysis."},
  {alias:"vision_tier", provider:"ollama",        model:"local-vision",  description:"Multimodal. Screenshots, images, documents."},
];
const MOCK_TOOLS = [
  {name:"code_execute", namespace:"code", description:"Execute Python code snippets and return its output.", dangerous:true},
  {name:"calculator", namespace:"core", description:"Evaluate a mathematical expression precisely.", dangerous:true},
  {name:"file_read", namespace:"files", description:"Read a file from disk.", dangerous:false},
  {name:"file_write", namespace:"files", description:"Write content to a file.", dangerous:true},
  {name:"file_list", namespace:"files", description:"List directory contents.", dangerous:false},
  {name:"file_delete", namespace:"files", description:"Delete a file or directory.", dangerous:true},
  {name:"file_move", namespace:"files", description:"Move or rename a path.", dangerous:true},
  {name:"file_search", namespace:"files", description:"Search for files matching a pattern.", dangerous:false},
  {name:"file_info", namespace:"files", description:"Show file metadata and size.", dangerous:false},
  {name:"shell_exec", namespace:"shell", description:"Run a shell command.", dangerous:true},
  {name:"shell_which", namespace:"shell", description:"Locate an executable on PATH.", dangerous:false},
  {name:"shell_environment", namespace:"shell", description:"Inspect the process environment.", dangerous:false},
  {name:"shell_python", namespace:"shell", description:"Run an inline Python snippet.", dangerous:true},
  {name:"web_search", namespace:"web", description:"Search the web for information.", dangerous:false},
  {name:"web_fetch", namespace:"web", description:"Fetch and extract text from a URL.", dangerous:false},
  {name:"memory_search", namespace:"memory", description:"Search stored memories semantically.", dangerous:false},
  {name:"memory_store", namespace:"memory", description:"Store a new fact in long-term memory.", dangerous:false},
  {name:"memory_delete", namespace:"memory", description:"Delete a memory by id.", dangerous:true},
  {name:"memory_list", namespace:"memory", description:"List stored memories.", dangerous:false},
  {name:"cron_list", namespace:"cron", description:"List scheduled jobs.", dangerous:false},
  {name:"cron_add", namespace:"cron", description:"Schedule a recurring job.", dangerous:false},
  {name:"cron_remove", namespace:"cron", description:"Remove a scheduled job.", dangerous:true},
  {name:"cron_run_now", namespace:"cron", description:"Trigger a scheduled job immediately.", dangerous:true},
];
const MOCK_MEMORIES = [
  {id:1, content:"Boss prefers dark mode and terse answers", kind:"fact", tags:["preference"]},
  {id:2, content:"Deployments happen on Tuesday mornings", kind:"fact", tags:["process"]},
  {id:3, content:"The HUD accent colour is electric cobalt #00E5FF", kind:"fact", tags:["ui"]},
];
const MOCK_JOBS = [
  {id:"job_1", name:"morning-brief", schedule:"0 7 * * *", agent:"morning_digest", enabled:true},
  {id:"job_2", name:"memory-sweep", schedule:"0 3 * * 0", agent:"memory_curator", enabled:true},
];

const MOCK_SKILLS = [
  {skill:{name:"status-report", description:"Compile a weekly status briefing from git and issue trackers."}},
  {skill:{name:"pdf-processing", description:"Extract and summarise text from PDF files."}},
  {skill:{name:"code-review", description:"Review a diff for correctness, style and security."}},
];

const MOCK_LEARNING = {
  enabled:true, optimizer:"heuristic",
  surfaces:{
    routing:{policy:"heuristic", enabled:true},
    intelligence:{policy:"dspy", enabled:true},
    agent:{policy:"heuristic", enabled:true},
    tools:{policy:"heuristic", enabled:true},
    engine:{policy:"none", enabled:false},
  },
};

function mockTelemetry(){ return {
  summary:{
    requests: 128, avg_latency_ms: 17.4, avg_tokens_per_second: 2763.9,
    avg_ttft_ms: 41.2, total_tokens: 48210, total_cost_usd: 0.0,
    errors: 1, error_rate: 0.008,
    resources:{cpu_percent:34, ram_percent:61, vram_percent:0},
    series:[] },
  energy:{total_joules:62.5, avg_watts:250.0, total_flops:2.1e12,
          vendor:"cpu", source:"cpu TDP estimate"},
  recent:[], models:[] }; }

function installMock(){
  // The HUD's api() helper decides between .json() and .text() by asking for
  // the "content-type" header and testing it for "json". Note the check is
  // on the RESPONSE header value, not the request header name.
  const respond = (data) => ({
    ok:true, status:200,
    headers:{ get:(h)=> (String(h).toLowerCase() === "content-type" ? "application/json" : "application/json") },
    json: async()=>data,
    text: async()=>JSON.stringify(data),
  });
  const json = (path) => {
    if (path.includes("/api/status")) return respond({
      app:"PSYCHO-C.A.R.L.A.", version:"1.0.0", started:true, active_tier:"fast_tier",
      agents:MOCK_AGENTS.map(a=>a.name), tools:MOCK_TOOLS.map(t=>t.name),
      memory:{memories:MOCK_MEMORIES.length}, jobs:MOCK_JOBS.length,
      voice:{tts_backend:"piper", stt_backend:"faster-whisper"},
    });
    if (path.includes("/api/tiers")) return respond({active:"fast_tier", tiers:MOCK_TIERS});
    if (path.includes("/api/agents")) return respond({agents:MOCK_AGENTS});
    if (path.includes("/api/tools")) return respond({tools:MOCK_TOOLS});
    if (path.includes("/api/memory")) return respond({memories:MOCK_MEMORIES, stats:{memories:MOCK_MEMORIES.length}});
    if (path.includes("/api/jobs")) return respond({jobs:MOCK_JOBS});
    if (path.includes("/api/telemetry")) return respond(mockTelemetry());
    if (path.includes("/api/skills")) return respond({enabled:true, count:MOCK_SKILLS.length, skills:MOCK_SKILLS, dir:"~/.psycho_carla/skills"});
    if (path.includes("/api/learning/surfaces")) return respond(MOCK_LEARNING);
    if (path.includes("/api/traces")) return respond({traces:[]});
    if (path.includes("/api/config")) return respond({content:"# static export\n[voice]\ntts_engine = \"piper\"\n"});
    return respond({});
  };
  const origFetch = window.fetch;
  window.fetch = (path, init) => {
    const p = String(path);
    if (p.includes("/api/chat")) {
      return Promise.resolve(respond({
        agent:"orchestrator", tier:"fast_tier",
        speech:"On it, Boss. The answer is 42.",
        display:"The answer is 42. Here is the fix:\n\n```python\ndef solve():\n    return 42\n```\n\nThat returns the value directly.",
        code_blocks:["def solve():\n    return 42"],
        tool_calls:[{name:"calculator", arguments:{expression:"6*7"}}],
        latency_ms:17, usage:{total_tokens:142},
      }));
    }
    if (p.startsWith("/api/")) return Promise.resolve(json(p, init));
    return origFetch(path, init);
  };

  const OrigWS = window.WebSocket;
  // The HUD compares readyState against WebSocket.OPEN, so the mock
  // replacement needs the standard constants attached to it.
  const MockWS = function(url){
    if (String(url).includes("/ws")){
      const self = this;
      self.readyState = 1;
      setTimeout(()=> self.onopen && self.onopen({}), 60);
      setTimeout(()=> self.onmessage && self.onmessage({data: JSON.stringify({
        type:"snapshot",
        data:{
          status:"ok",
          active_tier:"fast_tier",
          agents:MOCK_AGENTS,
          tiers:MOCK_TIERS,
          tools:MOCK_TOOLS,
          jobs:MOCK_JOBS,
          memory:{memories:MOCK_MEMORIES.length},
          telemetry:mockTelemetry(),
          voice:{state:"idle", tts_backend:"piper", stt_backend:"faster-whisper"},
          hardware:{cpu_count:8, ram_total_mb:16384, vram_total_mb:0},
        },
      })}), 220);
      self.send = (msg)=>{
        let m; try { m = JSON.parse(msg); } catch(e) { return; }
        if (m.type === "chat" || m.message){
          const prompt = m.prompt || m.message || "";
          // The live server echoes the user's prompt back as chat_user.
          setTimeout(()=> self.onmessage && self.onmessage({data: JSON.stringify({
            type:"chat_user", data:{prompt: prompt},
          })}), 80);
          setTimeout(()=> self.onmessage && self.onmessage({data: JSON.stringify({
            type:"agent_response",
            data:{agent:"orchestrator", tier:"fast_tier",
                  speech:"On it, Boss. The answer is 42.",
                  display:"The answer is 42. Here is the fix:\n\n```python\ndef solve():\n    return 42\n```",
                  code_blocks:["def solve():\n    return 42"],
                  tool_calls:[{name:"calculator",arguments:{expression:"6*7"}}],
                  latency_ms:17, usage:{total_tokens:142}},
          })}), 340);
        }
      };
      self.close = ()=>{};
      return self;
    }
    return new OrigWS(url);
  };
  MockWS.OPEN = 1;
  MockWS.CLOSED = 3;
  MockWS.CONNECTING = 0;
  window.WebSocket = MockWS;
}
if (document.readyState === "loading"){
  document.addEventListener("DOMContentLoaded", installMock);
} else { installMock(); }
"""


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "hud-preview.html"

    html = TEMPLATE.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")

    inline_css = "<style>\n" + css + "\n</style>"
    # A literal "</script>" inside an inline script would terminate the
    # block early, so neutralise it before embedding.
    safe_mock = MOCK.replace("</script>", "<\\/script>")
    safe_js = js.replace("</script>", "<\\/script>")
    inline_js = "<script>\n" + safe_mock + "\n" + safe_js + "\n</script>"

    # Replace the external stylesheet link.
    html, n_css = re.subn(
        r'<link[^>]*rel=["\']stylesheet["\'][^>]*>',
        lambda _m: inline_css,
        html,
        flags=re.IGNORECASE,
    )
    # Replace the external script tag. The tag may be self-closing or have
    # a body, and the src may be an absolute path (/static/carla.js).
    html, n_js = re.subn(
        r'<script[^>]*src=["\'][^"\']*carla\.js["\'][^>]*>\s*(?:</script>)?',
        lambda _m: inline_js,
        html,
        flags=re.IGNORECASE,
    )

    # Fallback: if the markup shape differs, append instead.
    if n_css == 0:
        html = html.replace("</head>", inline_css + "\n</head>")
    if n_js == 0:
        html = html.replace("</body>", inline_js + "\n</body>")

    banner = (
        '<div style="position:fixed;bottom:10px;right:14px;z-index:99;'
        'font:600 10px/1.5 ui-monospace,monospace;letter-spacing:.16em;'
        'color:#00E5FF;background:rgba(7,11,20,.88);border:1px solid rgba(0,229,255,.3);'
        'border-radius:6px;padding:6px 11px;pointer-events:none">'
        'STATIC EXPORT &middot; MOCK DATA</div>'
    )
    html = html.replace("</body>", banner + "</body>")

    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
