# PSYCHO-C.A.R.L.A. — Naming, Branding & OpenJarvis Parity Audit #5

Date: 2026-11-13. Method: source-level comparison against the upstream
OpenJarvis tree (`/tmp/oj/src/openjarvis`), live introspection of the local
package (registries, CLI parser, FastAPI OpenAPI), and the full test suites.
Nothing in this audit is asserted without a command behind it.

---

## 1. Renaming C.A.R.L.A. — shortlist

The product is currently **PSYCHO-C.A.R.L.A.** You asked for alternatives
for the C.A.R.L.A. part, with expansions, and nothing film-related. All of
these are original, descriptive acronyms.

### Keep the PSYCHO- prefix (recommended)

| Candidate | Short for | Why it works |
|---|---|---|
| **PSYCHO-N.O.V.A.** | **N**ative **O**perational **V**oice **A**ssistant | "Native" is literally your headline feature — models run natively in-process. Short, friendly, "Hey Nova" is a clean wake phrase that no film owns. |
| **PSYCHO-A.R.I.A.** | **A**utonomous **R**easoning & **I**ntelligence **A**ssistant | Describes the agent layer exactly. Very voice-friendly. |
| **PSYCHO-E.C.H.O.** | **E**mbedded **C**ognitive & **H**euristic **O**perator | Voice-first feel; "embedded" = runs on your machine. |
| **PSYCHO-V.E.S.P.E.R.** | **V**oice-**E**nabled Executive **S**ystem for **P**lanning, **E**xecution & **R**easoning | The most "executive assistant" sounding. Longer wake phrase. |
| **PSYCHO-A.T.L.A.S.** | **A**utonomous **T**ask, **L**anguage & **A**gent **S**ystem | Strong, systematic, cartography/knowledge connotation. |
| **PSYCHO-S.A.G.E.** | **S**ystem for **A**utonomous **G**eneral **E**xecution | Calm, competent. |
| **PSYCHO-M.I.R.A.** | **M**achine **I**ntelligence & **R**easoning **A**ssistant | Soft, personable, easy to say. |
| **PSYCHO-O.R.I.O.N.** | **O**perational **R**easoning, **I**ntelligence & **O**rchestration **N**etwork | Emphasises the multi-agent orchestration. |

### Alternative: make PSYCHO itself the acronym

**PSYCHO** = **P**ersonal **SY**stem for **C**ognition, **H**euristics &
**O**rchestration — then the assistant gets a short name (NOVA / ARIA / MIRA)
and the product is just "PSYCHO". Two-layer naming: product PSYCHO, assistant
NOVA, wake phrase "Hey Nova".

### My recommendation

**PSYCHO-N.O.V.A.** — Native Operational Voice Assistant. It advertises the
one thing no competitor build does (native in-process models), it's four
letters, and the wake word is unambiguous for speech recognition.

When you pick one, the rename touches: persona default name
(`PersonaConfig.name`), wake phrase, HUD title/boot logo, desktop window
title, README/docs, and the offline fallback's self-introduction. The Python
package name (`psycho_carla`) and the `carla` CLI entry points can stay as
internal identifiers so existing installs don't break — say the word if you
want those renamed too.

---

## 2. Studio branding — removed

Every reference to the film-studio brand has been removed from code, HUD,
packaging, preview and docs (14 references across 9 files, plus one in this
audit's predecessor). Replacement wording is neutral: "Personal Intelligence
// Local-First", "holographic command HUD".

Verified after the change:

```
full-tree search for the studio brand name
(py/html/css/js/toml/md/ps1/spec, excl. .git/.arena)
→ ZERO references
314 pytest passed · 46/46 e2e · 0 undefined names
```

One related item flagged, not silently changed: `voice/stt.py` defaults the
openWakeWord model id to `hey_jarvis`, because that is the literal filename
of the pretrained model openWakeWord ships. It is a third-party model
identifier, not product branding. It should be renamed as part of the
assistant rename above (a custom wake model can be trained, or the default
can point at a differently-named model file).

References to "OpenJarvis" itself remain throughout the docs — that is the
upstream open-source project this build is audited against, named only as
the comparison baseline.

---

## 3. Parity audit — verdict

**Not 100% parity. The core is at parity; the depth is not.**

What is genuinely complete: the channel layer (30/30 platforms), the
model-agnostic router with 16 adapters including the native in-process ONNX
engine, the Model Manager lifecycle (download/start/stop/delete/clear
partials), MCP client+server, A2A, cron/scheduler, memory, sessions,
workflow/recipes, the HUD, and the OpenAI-compatible serve endpoints.

What is missing is breadth upstream accumulated across a much larger tree
(687 Python files upstream vs 149 here — many upstream files are eval
suites and per-platform backends, but the functional gaps below are real).

### Scorecard

| Area | Upstream | Here | Status |
|---|---|---|---|
| Channels | 30 platforms | 30 platforms (+aliases/daemons) | ✅ Complete |
| Inference engines/adapters | ~16 registered | 16 adapters (incl. native ONNX + offline fallback) | ✅ Mostly — missing `nim`, `litellm`, `gemma_cpp`, `cloud` brand, multi-engine |
| Model catalog | 77 builtin models | 17 (13 server + 4 native) | ⚠️ Partial |
| Agents (registered) | 22 | 8 | ❌ Gap — see §4.1 |
| Tools (registry) | 60 | 26 builtin+runtime (+MCP/A2A dynamic) | ❌ Gap — see §4.2 |
| CLI top-level commands | ~44 | 33 | ⚠️ Partial — renamed equivalents + missing |
| Server routes | ~99 route decorators | 58 route templates | ⚠️ Partial — see §4.3 |
| Config sections | 31 (JarvisConfig) | 21 (CarlaConfig) | ⚠️ Partial — see §5 |
| Connectors (concrete sync) | 27 | 0 (OAuth framework for 7 ids) | ❌ Gap — see §4.4 |
| Skills | 20 bundled + 3 source networks | SKILL.md system, 1 example skill | ❌ Gap — see §4.5 |
| Memory | FactStore + hybrid/KG retrieval backends | SQLite+vector store, extractor, conversations | ✅ Mostly |
| Speech | STT ×3, TTS ×4 (mostly cloud) | STT ×2 + wake + barge-in, TTS Kokoro/Piper CPU | ✅ By design (CPU-only rule) — cloud backends absent deliberately |
| Telemetry/traces | vendor GPU/energy monitors, steady-state | local telemetry, energy, traces+rating | ⚠️ Partial |
| Analytics | cloud analytics client/identity | none | ❌ Missing (arguably by design — local-first) |
| Learning | DSPy/GEPA/ACE, SFT/GRPO training, spec-search | trace-driven routing/prompt/tool/engine/skill optimisers | ⚠️ Partial — no weight training, no DSPy-family |
| Security | scanners, capabilities, signing, SSRF, rate-limit, Merkle audit | AES-GCM vault, file sandbox, shell allowlist | ❌ Gap — see §4.6 |
| Sandbox | Docker + WASM runners | sandboxed file tools, timed subprocess code exec | ⚠️ Partial |
| Evals/bench | GAIA/TerminalBench/etc suites, scorers, trackers | one EvalSuite runner + default cases | ❌ Depth gap |
| System facade | `JarvisSystem` + `SystemBuilder` | `Carla` SDK + `CarlaRuntime` | ⚠️ Equivalent shape, thinner |
| Prompt builder | `prompt/` + memory-files (SOUL/USER) config | persona + per-agent prompts | ⚠️ Partial |
| Operators | operator manifests manager/loader | recipes/composer equivalent | ⚠️ Partial |
| Desktop | Tauri app + React frontend | pywebview app + HUD + native menus | ⚠️ Windows .exe not yet built |

### 4.1 Agents — 8 of 22

Present: `orchestrator`, `operative`, `deep_research`, `morning_digest`,
`monitor_operative` (upstream overlap) + `code_assist`, `memory_curator`,
`scheduler` (local additions).

Missing upstream registrations: `simple`, `native_react`,
`native_openhands` (CodeAct), `openhands`, `opencode`, `claude_code`,
`rlm`, `proactive`, and the hybrid family — `advisors`, `archon`,
`baseline_cloud`, `baseline_local`, `conductor`, `minions`,
`mini_swe_agent`, `skillorchestra`, `toolorchestra` (+ `react` alias).

### 4.2 Tools — 26 of 60

Upstream tool names with no local equivalent include: browser suite
(`browser_navigate/click/type/extract/screenshot/axtree`), git suite
(`git_status/diff/log/commit`), knowledge graph (`kg_add_entity`,
`kg_add_relation`, `kg_query`, `kg_neighbors`), agent spawning
(`agent_spawn/send/list/kill`), proactive queue (`queue_action`,
`get_pending_actions`, `execute_pending_actions`, `record_decision`,
`check_permission`), channel tools (`channel_list/send/status`),
`apply_patch`, `db_query`, `pdf_extract`, `image_generate`,
`audio_transcribe`, `text_to_speech` (as a tool), `llm`, `think`,
`get_weather`, `calendar_search/upcoming`, `fx_macro_data`,
`docker_shell_exec`, `code_interpreter_docker`, `digest_collect`,
`user_profile_manage`, `skill_manage`, `memory_index/manage/retrieve`
(local memory tools use different names), `retrieval`, `scan_chunks`.

Local equivalents under different names: `code_execute`≈code_interpreter,
`web_fetch`, file list/search/info/delete/move, cron tools, skill
search/load/list.

### 4.3 Server routes

Local: 58 templates — the `/api/*` HUD surface (agents, channels, models
incl. lifecycle, memory, jobs, skills, telemetry, traces, voice, config)
plus OpenAI-compatible `/v1/chat/completions`, `/v1/completions`,
`/v1/models`.

Missing upstream families: `/v1/agents` CRUD + managed-agents (tasks,
channels binding, pause/resume, learning runs, credentials),
`/v1/memory/{store,search,stats,config,index}`, `/v1/traces/{id}`,
`/v1/telemetry/{stats,energy}`, `/v1/skills` CRUD, `/v1/sessions/{id}`,
`/v1/budget`, `/metrics`, `/v1/learning/{stats,policy}`,
`/v1/speech/{transcribe,synthesize}`, `/v1/feedback`, `/v1/optimize/runs`,
`/v1/analytics/*`, `/v1/connectors/*` (incl. OAuth start/callback, sync,
upload ingest), `/webhooks/*` (twilio/whatsapp/bluebubbles/sendblue),
`/api/digest/*`, `/api/research`, `/v1/approvals/*`, `/v1/savings`,
`/v1/security/scan`, `/v1/cloud/reload`, `/dashboard`, `/comparison`,
websockets `/v1/chat/stream` and `/v1/agents/events`.

### 4.4 Connectors

Upstream ships 27 concrete syncing connectors (Gmail, Drive, Calendar,
Contacts, Tasks, Notion, Obsidian, Dropbox, IMAP/Outlook, Slack, Spotify,
Strava, Oura, Granola, HackerNews, RSS, iMessage, WhatsApp, weather, five
Apple services…). Here: the OAuth framework (4 providers covering gdrive,
gcalendar, gmail, google_tasks, github, strava, spotify), the connector
registry contract and a local KnowledgeStore — but
`available_connectors()` returns **zero** concrete connectors. Nothing
actually syncs. This is the single largest functional gap.

### 4.5 Skills

Present: agentskills.io-style `SKILL.md` parsing/validation, catalogue,
install from git/URL, sync, uninstall, skill tools, trace-based skill
optimiser. Missing: upstream's 20 bundled skills, the Hermes/OpenClaw/GitHub
source networks with index sync, skill overlays, and the sandboxed skill
executor.

### 4.6 Security

Present: encrypted credential vault (AES-GCM), sandboxed file tools,
shell allowlist + timeout, key redaction. Missing upstream: secret/PII/
injection scanners, capability policy (`default_deny`), code signing,
SSRF protection, rate limiter, Merkle audit log, taint tracking,
subprocess sandbox.

---

## 5. Settings audit (config)

Upstream `JarvisConfig` exposes 31 top-level sections; local `CarlaConfig`
exposes 21. Local-only strengths: capability **tiers** (`[tiers.*]`),
`[cron]`, `[voice]` + `[persona]`, `[web_search]`.

Upstream settings with **no local equivalent**:

- `[analytics]` (enabled/host/key/flush) — absent entirely
- `[sandbox]` (image, runtime, wasm fuel/memory limits, mount allowlist)
  — only `security.sandbox_paths` exists
- `[workflow]` (max_parallel, node timeouts) — engine exists, settings don't
- `[operators]` (manifests_dir, auto_activate)
- `[system_prompt]` + `[memory_files]` (SOUL/MEMORY/USER files, truncation)
- `[compression]` (threshold/strategy) — session compression exists unconfigured
- `[digest]` (sections, schedule, honorific, voice) — digest agent exists unconfigured
- `[proactive]` (schedule, hours_back, notification_channel) — agent missing too
- `[optimize]` (trials, judge model, benchmark) and `[agent_manager]` (db_path)
- `[deep_research]` (engine/model override)
- `[tools.browser]` (headless, viewport, timeout) and `[tools.weather]`
- Security depth: `capabilities` policy, `signing_key_path`, `profile`,
  `ssrf_protection`, rate-limit rpm/burst, `merkle_audit`, scanners on/off
- Per-engine typed tables (`[engine.ollama] host` etc.) — local uses
  `[engine.adapters.*]` + tier `base_url`, functionally close but not the
  same settings surface
- `[mining]` pool settings (local mining registry exists without config)

Settings that map across under different names: `[speech]`→`[voice]`,
`[scheduler]`→`[cron]`, `[channel]`→`[channels]`, `[sessions]`→`[sessions]`,
`[a2a]`→`[server] a2a_*`, `[telemetry]`/`[traces]`/`[learning]`/`[skills]`/
`[server]`/`[agent]`/`[tools]` all present.

---

## 6. Deliberate deviations (not defects)

Recorded so they are not re-reported as gaps later: cloud TTS/STT backends
(ElevenLabs/Cartesia/Deepgram) excluded by your CPU-only voice rule; Apple
engines/connectors irrelevant on Windows 11; cloud analytics excluded by
the local-first rule; the native ONNX engine and offline fallback have no
upstream counterpart at all — they are additions, upstream still requires
an external model server.

## 7. Verification (this audit)

```
pytest:            314 passed
e2e smoke:         46/46 checks passed
undefined names:   0 (pyflakes guard in the suite)
local routes:      58 templates · adapters: 16 · channels: 30 platforms
studio branding:   0 references remaining
Windows .exe:      spec ready (PsychoCARLA.spec), not built — needs Windows
```

## 8. Recommended order to close the remaining gaps

1. **P0** — Concrete connectors (Gmail/Drive/Calendar/Notion/Obsidian/RSS
   first) on the existing OAuth framework.
2. **P0** — `native_react` + `native_openhands` + `simple` + `proactive`
   agents.
3. **P1** — Upstream `/v1/*` REST families (agents, memory, skills,
   sessions, speech, feedback, optimize) alongside the HUD `/api/*`.
4. **P1** — Security scanners + capability policy + signing.
5. **P1** — Missing config sections (sandbox, digest, proactive, workflow,
   system_prompt/memory_files, compression, optimize).
6. **P2** — Missing tools (browser, git, knowledge graph, agent-spawn,
   channel, patch, pdf), bundled skills library + sources, model catalog
   expansion 17→77, eval-suite depth, `nim/litellm/gemma_cpp` adapters.
7. **P2** — Windows PyInstaller build + installer + Start-menu shortcut.
