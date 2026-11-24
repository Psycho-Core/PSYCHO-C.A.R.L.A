# PSYCHO-C.A.R.L.A. — Long-form reference

> This is the original long README, kept for detail. The short, current
> overview is [../README.md](../README.md). Some numbers in this file are
> older than the code (for example, the test count and "8 built-in agents" —
> there are now 26 agents; see `docs/FEATURES.md` for the verified state).

---

## What this is

OpenJarvis proved the shape of local-first personal AI: five composable primitives
(**Intelligence · Engine · Agents · Tools & Memory · Learning**) that can be
benchmarked, substituted and optimised independently.

**PSYCHO-C.A.R.L.A.** takes that shape and rebuilds it — not a wrapper, not a fork —
around three commitments:

1. **Model-agnostic by construction.** Nothing in this codebase names a required model.
   You define *capability tiers* (`fast_tier`, `deep_tier`, `vision_tier`) and point
   each at whatever provider you run. Ollama, llama.cpp, vLLM, LM Studio, OpenAI,
   OpenRouter, Groq, or a bespoke HTTP endpoint — all work through one adapter
   interface, no code changes.

2. **A persona that earns its place.** C.A.R.L.A. speaks a *concise summary* while
   the raw text — code, diffs, tables — goes to the UI untouched. A dual-channel
   splitter decides what gets vocalised and what gets displayed. It never moralises,
   never apologises, never says "Certainly!".

3. **Efficiency as a first-class constraint.** Latency, tokens-per-second and
   VRAM/RAM pressure are tracked continuously and surfaced in the HUD, the CLI and
   the `/api/telemetry` endpoint.

---

## Quick start (Windows 11)

```powershell
# 1. Python 3.10+ and a virtual environment
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install
pip install -e .

# 3. Optional: the full CPU-only voice stack
pip install -e ".[voice]"

# 4. Create your config
carla init --force

# 5. Diagnose
carla doctor

# 6. Launch the holographic console
carla serve
```

Then open **http://127.0.0.1:8787**.

No model installed yet? C.A.R.L.A. still runs — point `fast_tier` at any
OpenAI-compatible endpoint and you're live.

### Getting a model

C.A.R.L.A. is model-agnostic: a *tier* is a role, not a model. Point
`fast_tier` at whatever you run.

**Ollama** (the usual local choice):

```powershell
winget install Ollama.Ollama
ollama pull qwen2.5:7b-instruct
```

That is the default the generated config already expects — `carla init`
writes `provider = "ollama"`, `model = "qwen2.5:7b-instruct"`.

**llama.cpp**, **LM Studio**, **vLLM** — all speak the OpenAI shape:

```powershell
# LM Studio: start the server on 1234, then:
#   [tiers.fast_tier]
#   provider = "openai_compat"
#   model    = "qwen2.5-7b-instruct"
#   [engine.adapters.openai_compat]
#   base_url = "http://127.0.0.1:1234/v1"
```

**A cloud API** — same shape, different `base_url` and `api_key_env`.

Fifteen engines ship in total: `ollama`, `openai_compat`, `llamacpp`
(native `/completion`), `vllm`, `sglang`, `mlx`, `lmstudio`, `exo`,
`nexa`, `uzu`, `apple_fm`, `afm` (in-process), `lemonade`, and
`generic_http`.

### The five-minute tour

```powershell
carla doctor                    # is anything broken?
carla ask "What is 6 times 7?"  # one-shot query
carla chat                      # interactive console
carla serve                     # the HUD + API
carla gui open                  # HUD in an application window
```

### Running it as an application (no terminal)

The graphical entry point opens a **real native window** (WebView2 on
Windows 11) containing the holographic HUD:

```powershell
pip install pywebview
python -m psycho_carla.desktop
```

Or build a standalone Windows executable:

```powershell
pyinstaller PsychoCARLA.spec
```

The resulting `dist\PsychoCARLA\PsychoCARLA.exe` needs no console and
no Python install. The voice stack is excluded by default (it pulls in
torch); build it in with:

```powershell
$env:CARLA_BUILD_VOICE = "1"
pyinstaller PsychoCARLA.spec
```

Models are **never** bundled inside the executable — they live in
`%USERPROFILE%\.psycho_carla\models` and are downloaded at runtime.

### When there is no model yet

C.A.R.L.A. always answers. If the model you have configured is not
there — no Ollama running, no model downloaded yet, a server that went
away — a built-in responder takes over. It needs no network and no
model file, so the console is usable the moment you open it.

It is honest about what it is: it tells you what it can do, tells you
the time, and points you at the Model Manager. Download a model and it
steps aside.

Set `PSYCHO_CARLA_NO_FALLBACK=1` if you would rather see the raw
error.

### Running a model without Ollama

C.A.R.L.A. can run a model **inside its own process**. There is no
Ollama to install, no `llama-server` to start, no separate process to
manage — you download a model and the console uses it.

From the HUD: open **Model Manager**, pick **native (runs inside
C.A.R.L.A.)**, and press **Download**. When it finishes, press **Use
this** to point a capability tier at it.

From a terminal:

```powershell
carla model list --engine native_onnx   # what is available
carla model native native:smollm2-360m   # download it
carla model activate native_smollm2-360m # point fast_tier at it
carla model local                         # what is on this machine
carla model remove native_smollm2-360m --engine native_onnx
```

The native engine needs about **2.6× the model's own size** in RAM while
it runs. The Model Manager warns you before a download that will not
fit, and C.A.R.L.A. automatically fetches a smaller quantisation
when the machine is tight.

Models are downloaded from HuggingFace into
`%USERPROFILE%\.psycho_carla\models\native\` — never inside the
application.

### Connecting a channel

C.A.R.L.A. can be reached on 34 messaging platforms. Add one and it
comes online with the console:

```powershell
carla channels list              # every available channel
carla channels status            # each channel's state
carla channels add telegram --secret <bot-token>
carla channels remove telegram
```

The secret goes to the **encrypted vault** — the config only holds a
reference, never a credential. Available platforms include Telegram,
Slack, Discord, WhatsApp, Signal, email (SMTP/IMAP), Matrix, IRC,
Teams, Mastodon, Reddit, Nostr, Twitch, X/Twitter, LINE, Viber,
Feishu, Google Chat, Twilio SMS, Mattermost, Rocket.Chat, Zulip,
SendBlue, BlueBubbles, XMPP, and a generic webhook.

Send a test message from the console:

```powershell
carla channels send telegram <chat-id> "Hello, Boss."
```

Inbound messages are handled the same way on every platform: they are
mapped to a session (so C.A.R.L.A. remembers the conversation), and
sent to the active agent. A few commands work anywhere:

```
/help          /agents        /agent <name>
/session       /clear         /status
```

The HUD has a **Channels** panel showing every channel's live state.

### Downloading models

C.A.R.L.A. can fetch models for you, so you never touch a terminal:

```powershell
carla model list                    # the catalog
carla model info qwen3:4b           # full details
carla model recommend               # what fits this machine
carla model pull qwen3:4b --engine ollama
carla model pull qwen3:4b --engine llamacpp    # GGUF, straight from HuggingFace
carla model local                   # what is already installed
```

The same thing is in the HUD: **Model Manager** in the sidebar, or click
**Manage**. It shows your RAM, free disk, every catalogued model with a
Download button, and what is installed.

### Testing it

```powershell
# Unit + integration suite — 221 tests, no server needed
pytest tests/ -q

# End-to-end smoke against a running server
#   start `carla serve` in another shell first
python tests/e2e_smoke.py
python tests/e2e_smoke.py --port 9000        # a different port

# The HUD's own REST API
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/api/status
curl http://127.0.0.1:8787/api/telemetry
curl http://127.0.0.1:8787/api/models
curl http://127.0.0.1:8787/api/models/recommend
curl http://127.0.0.1:8787/.well-known/agent.json     # the A2A card
```

The OpenAI-compatible API works against C.A.R.L.A. exactly as it would
against a cloud provider:

```powershell
curl -X POST http://127.0.0.1:8787/v1/chat/completions `
  -H "Content-Type: application/json" `
  -d '{"model":"fast_tier","messages":[{"role":"user","content":"hi"}]}'
```

And other agents can call C.A.R.L.A. over A2A:

```powershell
curl -X POST http://127.0.0.1:8787/a2a/tasks `
  -H "Content-Type: application/json" `
  -d '{"jsonrpc":"2.0","id":"1","method":"tasks/send",
       "params":{"message":{"parts":[{"text":"Summarise my day."}]}}}'
```

### Installing from a built wheel

```powershell
pip wheel --no-deps -w dist .
pip install dist\psycho_carla-1.0.0-py3-none-any.whl
```

The agent templates, presets, recipes and example workflows all ship
*inside* the package, so every command works from a clean install.

---

## The five primitives

```
                    ┌─────────────────────────────────────────────┐
                    │           Agents  (Pillar 3)                │
                    │  8 roles × 3 modes: on-demand / scheduled  │
                    │  / continuous                              │
                    └───────┬──────────────────────┬──────────────┘
                            │                      │
                ┌───────────▼──────────┐  ┌────────▼─────────────────┐
                │ Intelligence         │  │ Tools & Memory          │
                │ (Pillar 1)           │  │ (Pillar 4)              │
                │ Model Router         │  │ 23 tools · SQLite+vector│
                │ tier → adapter →     │  │ MCP client & server     │
                │ provider             │  │ cron scheduler          │
                └───────────┬──────────┘  └────────▲─────────────────┘
                            │                      │
                ┌───────────▼──────────────────────▼──────────────┐
                │           Engine  (Pillar 2)                   │
                │  4 adapters · OpenAI-compatible API server     │
                │  FastAPI + WebSocket · HUD                      │
                └───────────────────────────┬────────────────────┘
                                            │
                ┌───────────────────────────▼────────────────────┐
                │           Learning  (Pillar 5)                  │
                │  trace logger · prompt optimiser · routing     │
                │  policy learner                                │
                └────────────────────────────────────────────────┘
```

### Pillar 1 — Intelligence (the model router)

The router is the only component that knows model names exist, and even then only
via configuration.

```toml
[tiers.fast_tier]
provider = "ollama"
model    = "qwen2.5:7b-instruct"

[tiers.deep_tier]
provider = "openai_compat"
model    = "gpt-4o"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Agents request `"fast_tier"` or `"deep_tier"`. They never learn what that maps to.
Swap a local 3B for a cloud 70B and nothing downstream notices.

### Pillar 2 — Engine (adapters + API)

Four adapters ship in the box:

| Adapter | Protocol | Use for |
|---------|----------|---------|
| `ollama` | native `/api/chat` | Local Ollama daemon |
| `openai_compat` | OpenAI `/chat/completions` | **Universal** — OpenAI, OpenRouter, Groq, Together, LM Studio, vLLM, SGLang, llama.cpp, LiteLLM, DeepSeek |
| `llamacpp` | native `/completion` | `llama-server`, for its detailed timing data |
| `generic_http` | template-driven | Any bespoke JSON dialect, configured — no code required |

The OpenAI-compatible API is live at `/v1`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="carla")
resp = client.chat.completions.create(
    model="fast_tier",                       # a tier alias, not a model name
    messages=[{"role": "user", "content": "Status report."}],
)
print(resp.choices[0].message.content)
```

Every response carries an extra `carla` object with `speech`, `code_blocks`,
`agent`, `tier`, `latency_ms` and `tool_calls`.

### Pillar 3 — Agents (8 built-ins)

| Agent | Mode | What it does |
|-------|------|--------------|
| `orchestrator` | on-demand | Plans multi-step work, selects tools, delegates |
| `operative` | continuous | Persistent autonomous executor with state |
| `deep_research` | on-demand | Multi-hop research with `[n]` citations |
| `morning_digest` | scheduled | Spoken daily briefing — terse, prioritised |
| `code_assist` | on-demand | Code generation, execution, debugging |
| `monitor_operative` | continuous | Long-horizon monitoring with memory compression |
| `memory_curator` | scheduled | Dedupes, prunes and enriches the memory store |
| `scheduler` | continuous | Manages cron jobs, dispatches to specialists |

Each is configurable under `[agents.<name>]` in the TOML.

### Pillar 4 — Tools & Memory

23 tools across five namespaces:

- **files** — `file_read` `file_write` `file_list` `file_delete` `file_move` `file_search` `file_info`
- **shell** — `shell_exec` `shell_which` `shell_environment` `shell_python`
- **web** — `web_search` `web_fetch` (DuckDuckGo / Brave / SearxNG / Tavily)
- **code** — `code_execute` `calculator` (exact AST evaluation, never `eval`)
- **memory / cron** — runtime-bound tools for the curator and scheduler agents

Plus **full MCP support**: connect to any MCP server over stdio or HTTP and its
tools appear as `mcp__<server>__<tool>`. And run C.A.R.L.A. *as* an MCP server
so Claude Desktop or another agent can drive it.

Memory is SQLite + an embedded float32 vector index with cosine retrieval.
The default embedder is a deterministic hashing projection — no model download,
no network, and genuinely decent recall on short personal notes. Set
`embedding_model` to a SentenceTransformers id to upgrade it.

### Pillar 5 — Learning

Every exchange is traced (prompt, response, agent, tier, latency, tokens, tool
calls, rating). The optimiser then mines those traces for:

- **Prompt suggestions** — patterns that correlate with high-rated answers, and
  anti-patterns that correlate with failures.
- **Routing policies** — which capability tier actually performs best per agent,
  weighting quality against latency.
- **Tool selection** — which tools each agent should keep, drop and promote,
  scored on observed success and latency. A smaller, more reliable tool surface
  means fewer wrong picks and a shorter prompt.
- **Engine specification** — per-tier quantisation, context window, batch size
  and sampling, derived from what actually worked, weighted by the same
  accuracy / latency / cost / efficiency objectives used everywhere else.
- **Skill descriptions** — the `agentskills.io` description is the only thing
  the agent sees before deciding to load a skill, so the optimiser treats it as
  the trigger text it is.
- **Agent advice** — failing tools, error rates, missing capabilities.

Each surface is switched independently in the TOML:

```toml
[learning.routing]
policy      = "heuristic"

[learning.intelligence]
policy = "sft"

[learning.agent]
policy = "agent_advisor"

[learning.tools]
policy = "tool_selection"     # "" disables the surface

[learning.engine]
policy = "engine_spec"        # "" disables the surface

[learning.metrics]
accuracy_weight   = 0.6
latency_weight    = 0.2
cost_weight       = 0.1
efficiency_weight = 0.1
```

```bash
carla optimize                              # all surfaces
carla optimize --surface tools              # just tool selection
carla optimize --surface engine --tier fast_tier
carla optimize --surface skills --policy dspy
```

---

### Energy & FLOPs telemetry

Upstream OpenJarvis treats **energy, FLOPs, latency and dollar cost as
first-class evaluation constraints** — that is the core of its research
thesis. This implementation measures all four.

Power draw is probed from whichever source the machine has:

| `energy_vendor` | Source |
|---|---|
| `nvidia` | `nvidia-smi --query-gpu=power.draw` |
| `amd` | `rocm-smi --showpower` |
| `apple` | `powermetrics` (needs sudo; degrades gracefully) |
| `cpu_rapl` | Linux RAPL via `/sys/class/powercap` |
| `cpu` | TDP estimate from the CPU model (last resort) |
| `auto` | Tries each in turn |
| `none` | Disables power probing |

FLOPs are estimated from the token counts and the model's parameter count,
using the standard `2 · N · tokens` rule for a dense forward pass. Where the
parameter count is unknown the result is reported as **unknown**, never as
zero — a metric that could not be measured is not a metric of zero.

```toml
[telemetry]
track_energy         = true
energy_vendor        = "auto"
gpu_metrics          = true
gpu_poll_interval_ms = 50
warmup_samples       = 0
steady_state_window  = 5
steady_state_threshold = 0.05
```

Read it back through the API:

```bash
curl http://127.0.0.1:8787/api/telemetry
# summary.energy  → { total_joules, avg_watts, vendor, available }
# summary.total_flops
# energy.models[] → per-model joules, FLOPs, joules/request, FLOPs/token
```

---

### Bench & eval harness

Two different questions, two different tools:

- **`bench`** measures *performance*: latency (mean / median / p95 / max),
  time-to-first-token, tokens per second, energy per case, and FLOPs.
- **`eval`** measures *quality*: graded cases checked for expected content,
  forbidden content, regex patterns, minimum length and latency ceilings.

```bash
carla bench --workload chat --turns 20 --json report.json
carla bench --workload tool_use --turns 10
carla bench --workload-file my-workload.json

carla eval --json eval-report.json
carla eval --eval-file my-suite.json
```

A workload is a JSON file:

```json
{
  "name": "smoke",
  "cases": [
    {
      "name": "greeting",
      "prompt": "Introduce yourself.",
      "expect_contains": ["Psycho"]
    }
  ]
}
```

An eval suite adds grading:

```json
{
  "name": "reliability",
  "cases": [
    {
      "name": "math",
      "prompt": "What is 2 + 2?",
      "expect_contains": ["4"],
      "expect_not_contains": ["I cannot"],
      "expect_regex": ["\\b4\\b"],
      "max_latency_ms": 5000,
      "weight": 2.0
    }
  ]
}
```

---

### Skills catalog

Implements the open [`agentskills.io`](https://agentskills.io) specification:
a skill is a folder containing a `SKILL.md` with YAML frontmatter followed by
Markdown instructions.

```
my-skill/
├── SKILL.md          # required: name + description + instructions
├── scripts/          # optional
├── references/       # optional
└── assets/           # optional
```

```markdown
---
name: status-report
description: Produce a concise engineering status report. Use when asked for a
  status update, weekly summary, or briefing.
license: MIT
metadata:
  author: psycho-carla
  version: "1.0"
---

# Status Report

## Procedure
1. Gather shipped work.
2. Identify risks.
3. Define next actions.
```

Progressive disclosure is the point of the format: only each skill's name and
description (~100 tokens) enter the agent's context at startup. The full body
loads on demand via `skill_load`; bundled files load only if the body points
at them.

```bash
carla skill install ./examples/skills/status-report
carla skill install https://example.com/skill.md
carla skill install https://github.com/org/skills-repo --ref main
carla skill sync                 # re-fetch from recorded sources
carla skill list
carla skill search "weekly summary"
carla skill validate              # check against the spec
carla skill remove status-report
```

The agent gets three tools: `skill_search`, `skill_load` and `skill_list`.

Validation enforces the spec: lowercase-hyphen names that match the folder,
required description, length limits, and a ban on angle brackets in the
frontmatter (they can be used to smuggle instructions into a prompt).

---

### Server authentication

An exposed agent carries shell and file tools, so the server refuses to bind a
non-loopback address without a key.

```bash
carla auth generate-key
export PSYCHO_CARLA_API_KEY="<the key>"

# or on Windows PowerShell
$env:PSYCHO_CARLA_API_KEY="<the key>"
```

```toml
[server]
host         = "0.0.0.0"
api_key_env  = "PSYCHO_CARLA_API_KEY"
require_auth = true
```

With a key set, every HTTP request must present it as `Authorization: Bearer
<key>` or `X-API-Key: <key>`, and the WebSocket accepts `?api_key=<key>`.
Loopback binds need no key. Without one, a non-loopback bind **refuses to
start** and tells you how to fix it.

---

## The persona & dual-channel output

This is the part that makes it feel like C.A.R.L.A. rather than a chatbot.

```
Model response
      │
      ├──► SPEECH channel  ──► CPU TTS (Kokoro / Piper) ──► speakers
      │      · concise, one tight sentence
      │      · code, diffs and tables REMOVED
      │      · "Right, Psycho. The answer is 42."
      │
      └──► DISPLAY channel ──► HUD
             · full text, verbatim
             · code fences rendered and syntax-highlighted
             · tool-call pills, latency, token counts
```

**What should she call you?** By default C.A.R.L.A. calls you **Psycho**.
To change it, set your name in the config — she will use it in chat, on
your channels, in the HUD and when speaking:

```toml
[persona]
user_name = "Alex"   # blank (the default) means she calls you "Psycho"
```

The splitter is a standalone, testable module
(`psycho_carla/core/persona.py`) that:

- strips banned filler (`as an AI`, `Certainly!`, `I apologize`, `let me know if…`)
- extracts fenced code and diffs out of the spoken path entirely
- flattens bullet lists into speakable prose
- condenses to a single tight sentence with a C.A.R.L.A. lead-in

```python
from psycho_carla import Carla

f = Carla()
result = f.ask("Write me a debounce function in Python.")

print(result["speech"])   # "Right, Psycho. Here's the debounce implementation. It waits until calls stop."
print(result["display"])  # full markdown, code fence intact
print(result["code_blocks"])  # ["```python\ndef debounce...\n```"]
```

### CPU-only voice

| Component | Engine | Notes |
|-----------|--------|-------|
| TTS | **Kokoro** (`kokoro`) | 82M params, real-time on CPU. `irish_female` → `bf_emma` |
| TTS alt | **Piper** (`piper`) | ONNX, very fast, ~200 MB RAM. `irish_female` → `en_GB-alba-medium` |
| STT | **faster-whisper** | CTranslate2, `int8` on CPU by default |
| Wake word | **openWakeWord** | ONNX, tiny, fully offline |
| Barge-in | built-in | Energy detection interrupts playback mid-utterance |

> **On the "Irish" voice.** No Irish (`en_IE`) voice model exists in Piper or
> Kokoro — this was checked against both upstream voice manifests (Piper:
> 182 voices, zero `en_IE`; Kokoro: eight languages, none Irish). The
> `irish_*` aliases therefore resolve to the closest available voices, which
> are **British English**. The alias names stay stable so a future Irish
> model drops in without touching your TOML.
>
> To hear every candidate, generate the samples and open `/voice` in the HUD:
> `python3 tools/make_voice_samples.py` then `python3 tools/make_kokoro_voices.py bf_emma b`.

Everything degrades gracefully: if Kokoro isn't installed, `SilentTTS` takes over and
the UI keeps working — you just don't hear it. `carla doctor` shows what's present.

---

## The holographic HUD

A self-hosted holographic command console. FastAPI + WebSockets, no build step, no CDN —
every asset is local and self-contained.

**Palette** (all CSS variables at the top of `carla.css`):

```css
--carla-bg:      #070B14;   /* deep obsidian-slate */
--carla-cyan:    #00E5FF;   /* electric cyan  */
--carla-cobalt:  #2979FF;   /* cobalt accent  */
```

Change those three lines and the whole console re-skins.

**What's on screen:**

- **Voice Core** — a central animated waveform ring with rotating reticle rings and
  tick marks. It idles gently, pulses on wake-word detection, goes green while
  listening and amber while speaking.
- **Agent modules** — all 8 with live status dots (cyan = selected, amber = working).
- **Capability tier switcher** — click to re-route mid-session.
- **Efficiency telemetry** — avg latency, tokens/sec, request count, token count,
  **energy in joules, power draw in watts and FLOPs**, plus CPU / RAM / VRAM
  pressure bars that shift amber then red under load.
- **Skills catalog** — the installed `agentskills.io` skills and what they do.
- **Learning surfaces** — every optimiser and whether it is switched on.
- **Memory inspector** — semantic search over the vector store, delete, store.
- **Cron scheduler** — create, list and fire jobs.
- **Settings editor** — a full `psycho_carla.toml` editor with live validation.
  Save it and the runtime reloads.

A number that was never measured shows as `—`, not `0`.

Everything is driven by a WebSocket that pushes state as it changes.

---

## Desktop client

The HUD is the desktop interface. Three ways to open it:

```bash
carla gui open              # start the console if needed, open the HUD
carla gui status            # is it reachable?
carla gui export            # write a standalone .html that needs no server
```

`gui open` prefers an *application* window (no tabs, no address bar) when
Chrome, Chromium or Edge is available, and falls back to the default
browser. `gui export` writes a single self-contained HTML file with every
stylesheet and script inlined — the offline twin of the live console, with
the same CSS-variable palette.

---

## Agent-to-Agent (A2A)

C.A.R.L.A. speaks the A2A protocol, so other agents can call it.

```bash
curl http://127.0.0.1:8787/.well-known/agent.json     # the discovery card
curl -X POST http://127.0.0.1:8787/a2a/tasks \
     -d '{"jsonrpc":"2.0","id":"1","method":"tasks/send",
          "params":{"message":{"parts":[{"text":"What did I decide?"}]}}}'
```

Or from Python:

```python
from psycho_carla.a2a import A2AClient

client = A2AClient("http://127.0.0.1:8787")
print(client.discover().name)              # PSYCHO-C.A.R.L.A.
task = client.send_task("Summarise my day.")
print(task.state, task.output_text)
```

A remote agent can also be exposed *as a tool*:

```python
from psycho_carla.a2a import A2AClient, register_a2a_agent

register_a2a_agent("researcher", A2AClient("http://127.0.0.1:9000"))
```

---

## Workflows

A workflow is a graph of nodes — agents, tools, conditions, parallel
branches, loops — that runs as one job.

```bash
carla workflow list
carla workflow show examples/workflows/research_pipeline.toml
carla workflow run examples/workflows/research_pipeline.toml --input "..."
```

```toml
[workflow]
name = "research_pipeline"

[[workflow.nodes]]
id = "researcher"
type = "agent"
agent = "orchestrator"
prompt = "Research this:\n\n{input}"

[[workflow.nodes]]
id = "summarizer"
type = "agent"
agent = "orchestrator"
prompt = "Summarise in three bullets:\n\n{researcher}"

[[workflow.edges]]
source = "researcher"
target = "summarizer"
```

Independent nodes run in parallel; conditions branch; loops are capped.
Condition expressions are parsed by a restricted evaluator — they are
never passed to `eval`.

---

## Connectors

OAuth-backed data sources, with tokens written owner-only (0600).

```bash
carla connect list
carla connect login gmail --client-id ... --client-secret ...
carla connect status gmail
carla connect logout gmail
```

Providers: **Google** (Drive, Calendar, Gmail, Tasks — one consent covers
all of them), **GitHub**, **Strava**, **Spotify**. The flow opens your
browser, waits on a localhost callback, exchanges the code and stores the
tokens. A token response that carries no `access_token` is rejected rather
than saved as a false success.

---

## The vault

Secrets that do not belong in a configuration file — API keys, OAuth
client secrets, webhook URLs — live in an AES-GCM encrypted file.

```bash
carla vault set brave_api_key sk-...     # prompted without echo if you omit the value
carla vault get brave_api_key
carla vault list                         # key names only
carla vault list --values                # the values too, if you really want
carla vault remove brave_api_key
```

One master key, 32 bytes of OS entropy, generated on first use and
written `0600` beside the vault. The vault **refuses to store anything**
when the `cryptography` package is missing — a secret store that quietly
falls back to plaintext is worse than no secret store.

---

## MCP servers

```bash
carla mcp add filesystem        # a preset
carla mcp add github --key ghp_...
carla mcp add mysrv --exec npx --args -y @scope/server
carla mcp connect
carla mcp status
```

A `--key` goes **into the vault**, not the config file. The config keeps
only a reference, and the MCP client resolves it at start-up:

```toml
[tools.mcp.servers.github]
transport = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[tools.mcp.servers.github.env]
GITHUB_PERSONAL_ACCESS_TOKEN = "${PSYCHO_CARLA_MCP_GITHUB_KEY}"
```

---

## Sessions

One conversation, identified independently of the channel it arrived on.
The HUD and the CLI share a session when their channel ids are linked.

```bash
carla sessions list
carla sessions show <session-id>
carla sessions consolidate <session-id>
carla sessions expire --hours 24
```

Long sessions are consolidated into *anchors* — a summary of the older
messages — so the model sees a coherent history rather than a truncated
one. Idle sessions expire.

---

## Presets and templates

Seven presets, each a complete working configuration:

```bash
carla init --list-presets
carla init --preset deep-research
```

`morning-digest-mac` · `morning-digest-linux` · `morning-digest-minimal`
· `deep-research` · `code-assistant` · `scheduled-monitor` · `chat-simple`

Sixteen agent templates ship with the framework:

```bash
carla templates list
carla templates show code-reviewer
```

`architect` · `assistant` · `code-reviewer` · `data-analyst` · `debugger`
· `deep-researcher` · `fact-checker` · `inbox-triager` · `meeting-prep`
· `monitor` · `note-taker` · `security-auditor` · `summarizer` ·
`translator` · `tutor` · `writer`

---

## Daemon

Run the console as a background service so the HUD is reachable without
a terminal open.

```bash
carla daemon install       # systemd unit / launchd plist / scheduled task
carla daemon start --background
carla daemon status
```

---

## CLI

```
carla init [--force]         Create a starter configuration
    --preset NAME, --list-presets
carla doctor                 Diagnose the environment
carla ask "..."              One-shot query
carla chat                   Interactive console
carla serve                  Start the HUD + API server
carla gui                    Open the HUD as a desktop client
    open|status|export
carla agents                 List agents
carla tiers                  List capability tiers
carla memory search|list|store|delete|clear|sessions|stats
carla cron list|add|remove|run
carla optimize               Run the learning optimiser
    --surface routing|prompt|tools|engine|skills, --agent X, --tier Y, --policy dspy
carla bench                  Run a performance benchmark
    --workload chat|tool_use, --workload-file F, --turns N, --json OUT
carla eval                   Run a quality evaluation suite
    --eval-file F, --json OUT
carla auth                   Server API-key management
    generate-key | status
carla skill                  Manage the skills catalog
    install|list|sync|remove|validate|search|show
carla workflow               Run and inspect workflows
    list|show|validate|run
carla templates              Agent template catalogue
    list|show
carla presets → carla init --preset
carla connect                Manage connector connections
    list|login|status|logout
carla sessions               Cross-channel session management
    list|show|consolidate|expire|stats
carla daemon                 Run the console as a background service
    start|status|install|uninstall
carla mine                   The optional mining subsystem
    doctor|providers|select
carla recipes                Composable primitive configurations
    list|show
carla voice speak|listen|status|audition
carla vault                  The encrypted local secret store
    set|get|list|remove
carla tools list|inspect     List and inspect the registered tools
carla registry list|show     Agents, tools, tiers and adapters
carla feedback list|score|thumbs|stats
carla compose list|show      Composable primitive configurations
carla tunnel start|status    Expose the HUD through a public tunnel
carla mcp serve|connect|status|add
carla config show|path|validate
```

## Python SDK

```python
from psycho_carla import Carla

with Carla() as f:
    f.ask("What did I decide about the database?")     # full structured result
    f.say("Summarise my day")                          # speech channel only
    f.show("Show me the config")                       # display channel only
    f.memory_store("Psycho prefers dark mode", tags=["ui"])
    print(f.telemetry())
```

---

## Configuration

`psycho_carla.toml` lives at:

- Windows: `%LOCALAPPDATA%\PsychoCarla\psycho_carla.toml`
- Linux/macOS: `~/.psycho_carla/psycho_carla.toml`
- Override with `PSYCHO_CARLA_HOME` or `PSYCHO_CARLA_CONFIG`

The shipped template is fully commented — 340 lines documenting every section:
`[intelligence]` `[tiers.*]` `[engine]` `[agents.*]` `[tools]` `[tools.mcp.servers.*]`
`[memory]` `[learning]` `[traces]` `[telemetry]` `[server]` `[voice]` `[persona]`
`[web_search]` `[security]`.

---

## Installing a voice stack on Windows 11

```powershell
pip install -e ".[voice]"
```

`kokoro` pulls `misaki` (espeak-ng) — on Windows, install the eSpeak NG runtime:

```powershell
winget install --id eSpeak-NG.eSpeak-NG
# or set ESPEAK_DATA_PATH to your espeak-ng\\share\\espeak-ng-data
```

Kokoro's closest-to-Irish voice is `bf_emma` (British female). Piper's is
`en_GB-alba-medium`. Both run on CPU with no GPU.

**Memory note:** loading the Kokoro checkpoint needs ~1.2 GB resident RAM.
On a 4–8 GB machine that is tight — switch to Piper (`tts_engine = "piper"`),
which needs ~200 MB and synthesises faster. `tts_model_path` lets you point
Kokoro at a local copy of the checkpoint and skip the ~315 MB download.

---

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

106 unit tests covering all five primitives, the persona splitter, the cron parser,
the memory store, the security sandbox, the voice backends, the energy/FLOPs
telemetry, the four learning surfaces, the server API-key guard, the bench/eval
harness and the skills catalog — plus `tests/e2e_smoke.py`, a 32-check live
integration suite.

---

## Tooling

| Script | What it does |
|--------|--------------|
| `tools/export_static_hud.py` | Export the HUD as one self-contained HTML file with mock data — open it straight from disk, no server needed |
| `tools/make_voice_samples.py` | Render the C.A.R.L.A. persona lines through every Piper voice into `voice_samples/` |
| `tools/make_kokoro_voices.py` | Same for one Kokoro voice per invocation (`bf_emma b`) — one process per voice keeps memory low |

---

## License

MIT — see [LICENSE](LICENSE). This is original code; OpenJarvis was used only as a feature checklist.

---

<div align="center">

*Personal AI, on personal devices.*

</div>
