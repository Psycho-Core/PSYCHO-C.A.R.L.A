# OpenJarvis Parity Audit — PSYCHO-C.A.R.L.A.

**Date:** 2026-11-13
**Baseline:** `open-jarvis/OpenJarvis` @ `main` (README + `configs/openjarvis/config.toml` +
`src/openjarvis/` tree, fetched live)
**Subject:** `psycho_carla` @ `/home/user/src/psycho_carla`

> **Status update.** The five highest-priority gaps identified by this audit
> have since been closed. See **[`docs/PARITY-CLOSURE.md`](PARITY-CLOSURE.md)**
> for what was built and how it was verified. This document is preserved
> unchanged as the original baseline record; where the two disagree, the
> closure report reflects the current state of the code.
>
> Closed: energy + FLOPs telemetry · learning tool-selection and engine-spec
> optimisers with policy switches · server API-key guard · bench/eval harness
> · skills catalog.

---

## Verdict

**This is a faithful reimplementation of OpenJarvis's five-primitive architecture and
its core subsystems. It is NOT a complete replicate of the upstream product surface.**

Upstream `src/openjarvis/` contains **35 subpackages** and a **58-file CLI module**.
PSYCHO-C.A.R.L.A. has **11 subpackages** and a **single-file CLI**. The architectural
core is complete and faithful; the surrounding product surface (skills, benchmarks,
connectors, A2A, daemon, workflow) is not present.

| Dimension | Upstream | PSYCHO-C.A.R.L.A. | Parity |
|---|---|---|---|
| Subpackages | 35 | 11 | 24/33 mapped (~73%) |
| CLI commands | ~58 | 14 top-level | ~24% |
| Built-in agents | 8 | 8 (different set) | 6/8 direct |
| Tools | — | 30 | ✅ |
| Model adapters | 10 engines | 4 (generic covers rest) | ✅ functionally |
| MCP client + server | ✅ | ✅ both | ✅ |
| Learning surfaces | 4 | 2 | 50% |

---

## ✅ Fully replicated

### Five primitives
All five exist as independently swappable, typed layers with a declarative spec
(`psycho_carla.toml`):

| Primitive | Upstream section | PSYCHO-C.A.R.L.A. |
|---|---|---|
| Intelligence | `[intelligence]` | `core/config.py:IntelligenceConfig` + `providers/router.py` |
| Engine | `[engine]` | `providers/adapters/*` + `server/api/openai_compat.py` |
| Agents | `[agent]` | `agents/{base,builtin,registry,scheduler}.py` |
| Tools & Memory | `[tools]`, `[tools.storage]`, `[tools.mcp]` | `tools/`, `memory/` |
| Learning | `[learning]`, `[telemetry]`, `[traces]` | `learning/`, `core/telemetry.py` |

### Verified present (23 checks, all green)
MCP client + MCP server · OpenAI-compatible API (`/v1/chat/completions`, SSE +
non-stream) · cron scheduler (dependency-free parser) · continuous loop ·
SQLite + float32 vector memory with blended cosine/lexical retrieval · memory
dedup via fingerprint · telemetry (latency, tokens/sec, VRAM, RAM) · trace
logger · prompt optimizer · routing optimizer · faster-whisper STT · Kokoro +
Piper TTS · openWakeWord · barge-in · dual-channel persona splitter · file
sandbox · shell allowlist · code execution · hardware detection · API-key env
resolution · prompt-injection guard.

### Deliberate, documented deviations
| Upstream | PSYCHO-C.A.R.L.A. | Reason |
|---|---|---|
| `native_react` | folded into `orchestrator` | ReAct loop is the shared harness |
| `simple` | folded into `orchestrator` | Single-turn is `max_turns=1` |
| `native_openhands` | `code_assist` | Name per your spec |
| — | `memory_curator` | Added (memory maintenance) |
| — | `scheduler` | Added (cron management) |

---

## ❌ Missing (ranked by significance)

### 1. Learning is only 50% complete — **most significant gap**
Upstream's Learning primitive optimises **four** surfaces:
- ✅ **Agent prompts** — `learning/optimizer.py:optimize_prompt()`
- ✅ **Routing policy** — `learning/optimizer.py:optimize_routing()`
- ❌ **Tool selection & descriptions** — upstream rewrites tool schemas/descriptions
  from trace data. *Not present.*
- ❌ **Engine runtime spec** — upstream emits a revised `[engine]` section
  (batching, quantization, cache). *Not present.*
- ❌ **Intelligence weights** — upstream fine-tunes model weights (LoRA).
  *Not present; the only match in the codebase was the word "exploratory".*

Upstream also exposes per-surface policy toggles
(`[learning.routing].policy`, `[learning.agent].policy`, `[learning.intelligence].policy`).
PSYCHO-C.A.R.L.A. has `optimize_routing()` / `optimize_prompt()` but no policy
switches and no engine/tool/weight optimisers.

### 2. Energy & FLOPs telemetry — **breaks upstream's central research constraint**
Upstream treats **energy, FLOPs, latency and dollar cost as first-class evaluation
constraints** alongside accuracy (this is the paper's core contribution).
PSYCHO-C.A.R.L.A. tracks latency, tokens/sec, VRAM% and RAM% — but **not energy**.
`core/telemetry.py:_nvidia_smi()` queries `memory.used,memory.total` only;
upstream also queries **power draw** and supports `energy_vendor =
"nvidia"/"amd"/"apple"/"cpu_rapl"`. There is no FLOPs metric at all.

Missing upstream telemetry config:
```toml
gpu_metrics = true
gpu_poll_interval_ms = 50        # upstream polls at 50ms
energy_vendor = ""
warmup_samples = 0
steady_state_window = 5
steady_state_threshold = 0.05
```

### 3. Skills system — a headline upstream feature
Upstream ships a **skills catalog** (agents discover skills and invoke them as tools),
with `jarvis skill install/sync`, import from Hermes (~150 skills) and OpenClaw
(~13,700 community skills), the `agentskills.io` spec, `jarvis optimize skills
--policy dspy`, and `jarvis bench skills`. **Nothing equivalent exists here.**

### 4. Benchmarks & evals
Upstream has `bench/` and `evals/` subpackages plus `jarvis bench`, `jarvis eval`.
PSYCHO-C.A.R.L.A. has no benchmark harness.

### 5. A2A protocol (Google)
Upstream `a2a/` subpackage + agent cards. **Not present.**

### 6. Connectors / OAuth
Upstream `connectors/` with `jarvis connect gdrive` (one OAuth → Gmail, Calendar,
Tasks). Only generic MCP-bridged connectors exist here.

### 7. Daemon, workflow, sessions, mining, recipes, operators, prompt-management
Upstream subpackages with no counterpart: `daemon/`, `workflow/`, `sessions/`,
`mining/`, `recipes/`, `operators/`, `prompt/`.

### 8. Server API-key guard
Upstream **refuses to bind a non-loopback host without an API key**
(`OPENJARVIS_API_KEY` / `jarvis auth generate-key`) and ships `jarvis auth`.
PSYCHO-C.A.R.L.A. binds `127.0.0.1` by default but **has no API-key
requirement and no `auth` command** — binding `0.0.0.0` exposes an unauthenticated
agent with shell and file tools.

### 9. Upstream engines not individually present
`vllm`, `sglang`, `exo`, `nexa`, `uzu`, `apple_fm`/`afm`, `mlx`, `lmstudio`.
**Partially mitigated:** all of these speak OpenAI-compatible HTTP, which
`providers/adapters/generic_http.py` covers functionally. `afm` (in-process Apple
Foundation Models) is the one with no HTTP equivalent.

### 10. Presets & CLI breadth
Upstream `jarvis init --preset <name>` with 5 starter presets
(`morning-digest-*`, `deep-research`, `code-assistant`, `scheduled-monitor`,
`chat-simple`). PSYCHO-C.A.R.L.A. has `carla init` with a single generated
config — no presets.

---

## Verification method

1. Fetched upstream `README.md`, `configs/openjarvis/config.toml`, and the live
   `src/openjarvis/` + `src/openjarvis/cli/` directory trees via the GitHub API.
2. Enumerated every local subpackage and mapped it 1:1 to an upstream subpackage.
3. Ran 23 positive feature probes and 22 negative probes against the local
   source tree, then manually re-read each hit to eliminate false positives
   (three: `finetune` → the word "exploratory"; `compress` → agent descriptions;
   `energy` → microphone RMS thresholds; `quantiz` → one config field).
4. Cross-checked the upstream TOML schema section-by-section against
   `core/config.py` and `config_template.py`.
5. Confirmed the local build is green: **71 unit tests**, **32 e2e checks**.

## Recommended priority

1. **Energy/power telemetry + FLOPs** — restores upstream's defining constraint.
2. **Learning: tool-selection and engine-spec optimisers** — completes the
   primitive's four surfaces and the `policy` switches.
3. **Server API-key guard** — a security gap, not a feature gap.
4. **Bench/eval harness** — needed to claim upstream's evaluation story.
5. **Skills catalog** — upstream's most user-visible differentiator.
