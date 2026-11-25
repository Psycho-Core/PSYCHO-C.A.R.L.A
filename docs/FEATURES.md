# PSYCHO-C.A.R.L.A. — Features

**C**oding, **A**utomated **R**easoning, & **L**anguage **A**ssistant.

This document separates what **has been built** (Part A) from what **has
not been built yet** (Part B), so nobody has to guess. Part A reflects the
verified state on 2026-11-13: 423 pytest tests pass and the end-to-end
smoke suite is 46/46 (see `docs/audits/PARITY-AUDIT-7.md`,
`docs/audits/NAME-CHANGE-AUDIT.md`, `docs/audits/USER-NAME-AUDIT.md`).

---

# Part A — Features that are built

## A1. Core & runtime
- Local-first, model-agnostic agent runtime (`CarlaRuntime`) — no model or
  provider is hardcoded anywhere.
- Capability tiers (`fast_tier`, `deep_tier`, `vision_tier`, custom tiers)
  that can each point at any provider; switchable at runtime.
- 35-table TOML configuration (`psycho_carla.toml`) covering every section,
  with dump/load round-trip.
- Persona system with dual-channel output: a short spoken summary plus the
  full display text (code, diffs, tables stripped from speech).
- The user sets what she calls them — `[persona] user_name`, blank =
  **“Psycho”** — honoured in chat, channels, CLI, HUD, and speech.
- Sessions, traces, telemetry (latency / tokens / energy / cost tracked as
  first-class metrics), and an efficiency summary in HUD, CLI, and API.

## A2. Intelligence & models
- **Safe mode** (default on): shell/code/file-write/delete/patch/commit tool calls
  are queued in the HUD **Approvals** panel instead of running; an agent cannot
  approve its own request. `[security] enforce_tool_confirmation = false` or the
  panel checkbox turns it off.
- **Backup / restore** (`carla backup`, HUD buttons): config, memory, sessions,
  knowledge, skills; models/engine excluded; vault only with `--include-vault`.
- **First-run wizard** in the HUD; first launch writes a starter config itself.
- **GGUF downloads resume** (`.part` files) and partial downloads can be cleared;
  complete models can be deleted (the engine is stopped first if it holds the file).
- **Windows release build**: GitHub Actions builds `PsychoCARLA.exe` (see
  `.github/workflows/release.yml`) and publishes it under Releases.
- **Managed llama.cpp engine** (`native_gguf`): C.A.R.L.A. downloads the
  official llama.cpp build and runs GGUF models (3B–32B) itself as a hidden
  child process — no Ollama. Backends: Vulkan (any GPU), CPU, HIP.
  GGUF catalog tuned for an 8 GB PC (3B uncensored) and a 16 GB-GPU PC
  (Qwen2.5-Coder 14B / 32B, official and abliterated).
  Verified with a fake llama-server in tests; not yet with the real binary.
- **OpenRouter** cloud models (free and paid); key kept in the encrypted vault.
- Model Manager (HUD + CLI): download / start / stop / delete, **custom model**
  (any Ollama tag or Hugging Face repo), **free memory**, **auto-pick** by free
  RAM, **remote PC** tiers, native-engine **device switch (CPU / DirectML GPU)**.
  See `docs/MODELS.md`. (GPU path unit-tested for selection only.)
- Conversation export (markdown / JSON) and a fine-tuning dataset export (JSONL).
- First-run setup banner in the HUD when no model is usable.
- 78-model catalog with per-model metadata; `carla model` commands to
  list, inspect, recommend, pull, and remove.
- **Native in-process ONNX engine** — downloads, prunes, starts, stops, and
  deletes models inside her own process on CPU. No Ollama or external
  server required.
- Model lifecycle management in the HUD (Model Manager) and the desktop
  app, including clearing partial downloads.
- 21 provider adapters (Ollama, llama.cpp, vLLM, LM Studio,
  OpenAI-compatible cloud endpoints, native ONNX, and more), with failover.
- **Offline fallback responder** — if no provider answers, she still
  answers (greetings, time/date, basic help) instead of erroring;
  disable with `PSYCHO_CARLA_NO_FALLBACK=1`.
- OpenAI-compatible `/v1/*` API alongside the HUD’s `/api/*` (143 routes
  total), so standard tools can talk to her.

## A3. Agents, tools, skills
- 26 agents (orchestrator, code assistant, researcher, memory curator,
  scheduler, extended specialists) with capability-based tool filtering.
- 96 live tools: files, shell, code execution, web fetch/search (SSRF-
  guarded), browser, knowledge/media, memory CRUD, cron, agent-to-agent
  send/list/kill, git, database, weather, and more.
- Sandboxed code execution (subprocess / Docker / WASM backends) with
  timeouts and honest unavailability reporting.
- 20 bundled skills, a skill executor, skill sources, and `carla skill run`.
- MCP client **and** server; agent-to-agent (A2A) protocol support;
  DAG-based multi-agent workflows (`workflow/`).

## A4. Memory & knowledge
- Semantic + store/search memory with runtime memory tools, knowledge
  graph tools, and memory-file nudges.
- 28 knowledge connectors (files, feeds, APIs, vault-backed sources) with
  sync into a searchable `KnowledgeStore`; `carla connect list/sync` and
  HUD endpoints.
- Learning/optimisation surfaces (router, tools, skills, prompts) via
  `carla optimize`, plus a proactive action queue, digests, and mining
  primitives.

## A5. Channels & voice
- All 32 upstream channel platforms (49 ids including aliases): Slack,
  Telegram, email, IRC, webchat, and the rest — one bridge, sessions kept
  across messages, `/help`, `/agents`, `/agent`, `/notify`, `/clear`, …
- CPU-only voice: Kokoro/Piper TTS, faster-whisper STT, wake phrase
  “hey carla”, barge-in, and the dual-channel split so she never reads
  code aloud.
- Voice audition page to compare the local voices.

## A6. Interfaces
- Holographic web HUD: chat, agents, tiers, memory, skills, learning,
  cron jobs, vault, channels, telemetry, model manager, config editor.
- Desktop app (`carla-app`, pywebview/WebView2) wrapping the same console.
- 44-command CLI (`carla …`): init, doctor, ask, chat, serve, agents,
  tiers, memory, cron, optimize, bench, eval, voice, tunnel, channels,
  registry, model, vault, tools, gui, mine, recipes, compose, sessions,
  templates, connect, daemon, workflow, auth, skill, mcp, config, scan,
  telemetry, digest, research, quickstart, operators, status, + aliases.
- Evaluation built in: deterministic scorers, built-in suites
  (core/reasoning/knowledge/instruction/coding), `carla eval --suite`,
  `--list-suites`, and baseline comparison (`--compare`).

## A7. Security
- Secret/credential scanners (`carla scan`), capability policy enforced
  per agent, Ed25519 message signing, SSRF protection on web tools,
  token-bucket rate limiting, an approvals flow, and a hash-chained
  audit log.
- Encrypted vault for channel/MCP secrets; local-first analytics (no
  cloud analytics).

## A8. Packaging (ready, not yet built — see B1)
- `PsychoCARLA.spec` (PyInstaller onedir) and `install.ps1` (venv, install,
  starter config) are written and config-verified.
- Product naming and the address-name setting are fully renamed/wired and
  audit-verified end to end.

---

# Part B — Features not built yet (candidates for later)

## B1. Needs the owner’s Windows 11 machine
1. **Built `.exe` + installer run** — the packaging in A8 has never been
   compiled or run on Windows. This is the single most important remaining
   task and only the owner can do it today.

## B2. Product features we may add later
2. **Trainable personal model pipeline** — LoRA/QLoRA fine-tuning of an
   open-weight base on the owner’s data (chats, memories, documents),
   evaluated against the base model with the built-in suites, then managed
   in the Model Manager like any other model. Discussed; designed in
   conversation; not started.
3. **One-click Windows experience** — system-tray icon, start-on-login,
   background daemon polish, and an auto-updater.
4. **Dedicated “Hey Carla” acoustic model** — wake word works today by
   transcribing speech; a trained openWakeWord model would be lighter and
   steadier in noise (`wake_model_path` is ready for it).
5. **Memory controls in the HUD** — timeline view, per-memory “forget,”
   import/export, and one-click backup/restore of config + memory + skills.
6. **Proactive routines productised** — morning brief, reminders, and
   watch-jobs delivered to the owner’s channels (plumbing exists:
   daemon/cron/digest; this is the user-facing layer).
7. **First-run model auto-benchmark** — benchmark candidate native models
   against the actual machine and recommend tiers automatically.
8. **Approvals & audit viewer UI** — surface the existing approvals flow
   and hash-chained audit log in the HUD.
9. **Local-network sync** — same Carla and memory across the owner’s
   machines, LAN-only, no cloud.
10. **In-HUD skill editor/marketplace** — create, edit, and install skills
    from the console instead of files/CLI.
11. **Voice upgrades within the CPU-only rule** — better local voices as
    CPU models improve; optional local voice cloning if it can stay
    on-device and fast enough.

## B2b. More candidates (round 2, 2026-11-13)

### Everyday superpowers
12. **Global summon hotkey + command palette** — one key combo anywhere in
    Windows summons a small overlay: type or speak, she acts. The HUD
    becomes something you reach for without switching windows.
13. **Screen understanding** — “Carla, what’s on my screen?”: screenshot →
    vision tier/local OCR, explain error dialogs, read dialogs aloud,
    guide the next click. Opt-in per use; nothing stored by default.
14. **Meeting & voice-memo notes** — record locally, transcribe with the
    existing faster-whisper stack, produce summary + action items, file
    them into memory/tasks.
15. **Downloads & inbox triage** — watch folders, propose renames/sorting,
    extract receipts and documents, weekly cleanup proposal. She proposes;
    nothing moves without approval.
16. **Teach-by-demonstration** — do a repetitive task once while she
    watches (steps recorded with consent), she drafts it as a reusable
    skill/workflow for you to approve and reuse.
17. **People & life-admin graph** — remember people, birthdays,
    preferences, last topics; track document/warranty expiries and nudge
    before they lapse. All local.

### Work & thinking
18. **Coding autopilot mode** — project watcher, test runner, and a
    diff-review board in the HUD: she proposes fixes as reviewable diffs,
    never silent edits.
19. **Multi-model council** — for important questions, two tiers answer
    independently; she compares, flags disagreement, shows confidence and
    citations instead of one confident-sounding guess.
20. **Long-task project board** — visual plans with checkpoints,
    pause/resume, and approval gates, so multi-hour agent work is
    inspectable instead of a black box.
21. **Deep research reports** — multi-source research saved to the vault
    with citations, plus scheduled re-checks that report what changed.

### Home & presence
22. **Smart-home control** — Home Assistant / local device APIs: lights,
    media, climate, scenes (“movie mode”). The proper living-room Carla
    moment; still local-first.
23. **PC & workspace control** — launch named setups (“coding setup”
    opens your apps/layout), control volume/media, and a game/performance
    mode that pauses background jobs when you’re fullscreen.
24. **Phone companion (PWA)** — install the HUD on your phone over your
    LAN: quick capture, chat, and approvals from the couch; push via your
    existing channels.
25. **Profiles & guest mode** — separate memory partitions per person,
    speaker-aware replies, and a locked-down guest/kid mode with reduced
    tools.

### Trust & self-maintenance
26. **Trust centre** — big panic-stop button (halt all agents/tools),
    offline kill-switch, per-channel trust levels (a Telegram stranger ≠
    you at the keyboard), and quarantine for untrusted web/email content
    so injected instructions can’t steer her.
27. **`carla doctor --fix` + weekly self-report** — auto-repair common
    setup problems, and a weekly summary of what she learned, what
    failed, and what optimisations she proposes — nothing applied without
    your approval.
28. **Plugin packs** — export/import agents + skills + workflows as a
    single shareable file, with a local “installed packs” view. Your own
    tiny ecosystem, no cloud store.

## B3. Deliberately not added (by design, not omission)
- **Cloud TTS/STT** — excluded by the owner’s CPU-only, local-first rule.
- **Apple-only engines/connectors** (MLX etc.) — irrelevant on Windows 11.
- **Cloud analytics/telemetry upload** — replaced by local-first
  analytics; nothing leaves the machine.
- **A from-scratch frontier model** — out of scope by physics and budget;
  the trainable path (B2.2) fine-tunes open-weight bases instead.

---

*Part A is the shipped truth; Part B is the menu. Nothing in Part B is
required for the product to be complete — it is complete today without
them.*
