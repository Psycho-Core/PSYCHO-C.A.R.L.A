# PSYCHO-C.A.R.L.A. — OpenJarvis Parity Audit #6 (Gap Closure)

Date: 2026-11-13. Method: source-level comparison against the upstream
OpenJarvis tree (`/tmp/oj/src/openjarvis`), live introspection of the local
package (registries, CLI parser, FastAPI OpenAPI), and the full test suites.
Nothing in this audit is asserted without a command behind it. This audit
closes the remediation ordered by Audit #5 §8 ("PLEASE FIX ALL GAPS").

---

## 1. Verdict

**Full surface parity with upstream OpenJarvis, plus local-only additions
(native in-process ONNX engine, offline fallback, holographic HUD).**

Every P0/P1/P2 item from Audit #5 §8 is implemented and test-covered.
The one item that cannot be executed in this Linux sandbox is the Windows
PyInstaller build (item 7) — the spec and installer are ready and verified
by inspection; the build itself must run on Windows 11.

---

## 2. Closure map (Audit #5 §8 → evidence)

| # | Item | Status | Evidence |
|---|------|--------|----------|
| 1 | P0 — Concrete connectors | ✅ Done | 28 registered connector ids (27 upstream + `github` alias); `KnowledgeStore`; HUD `GET /api/connectors`, `POST /api/connectors/{id}/sync`; CLI `carla connect sync`; 10 tests |
| 2 | P0 — Missing agents | ✅ Done | 26 registered agents (8 core + 18 extended incl. `react` alias), covering all 22 upstream registrations; verified live via `/api/status` |
| 3 | P1 — Upstream `/v1/*` REST families | ✅ Done | `server/api/upstream.py` mounted; 143 route templates, 74 under `/v1`; 20 route tests |
| 4 | P1 — Security scanners + capability policy + signing | ✅ Done | `security/{scanners,capabilities,signing,ssrf,rate_limit,audit,approvals}.py`; capability filtering in `BaseAgent._tool_schemas_for`; SSRF in `web_fetch`; rate-limit middleware; hash-chained audit in runtime `ask()`; 15 tests |
| 5 | P1 — Missing config sections | ✅ Done | 14 new section dataclasses + expanded `SecurityConfig` + `[sandbox]` + extended `SkillsConfig` (bundled/sources); TOML round-trip verified |
| 6 | P2 — Missing tools | ✅ Done | 88 statically registered tools, 96 live at runtime (upstream ≈ 60 names; every Audit §4.2 name present, asserted by test) |
| 6 | P2 — Bundled skills library + sources | ✅ Done | 20 upstream step-skills ported (`skills/bundled.py`), step executor, bundled/GitHub/index sources, install-on-first-use; `skill run` CLI + `skill_manage` tool |
| 6 | P2 — Model catalog 17 → 77+ | ✅ Done | 78 unique models (BUILTIN + 61 ported `UPSTREAM_MODELS` + 4 native ONNX) |
| 6 | P2 — Eval-suite depth | ✅ Done | 7 deterministic scorers, 5 built-in suites (24 cases), report comparison; CLI `eval --suite/--list-suites/--compare` |
| 6 | P2 — `nim`/`litellm`/`gemma_cpp` adapters | ✅ Done | 21 adapters; `cloud` + `multi` (ordered failover) + `gemma_cpp` (honest unavailable) added |
| 7 | P2 — Windows PyInstaller build + installer | ⚠️ Ready, not built here | `PsychoCARLA.spec` + `install.ps1` written and inspected; build requires a Windows 11 machine (cannot run in this Linux sandbox) |

---

## 3. New in this round

### Tools (26 → 88 static / 96 live)
`tools/builtin/extended.py` (git suite, unified-patch apply, read-only SQL,
SSRF-checked HTTP, HTTP-backed browser navigate/extract/click/type/axtree,
PDF extraction, weather, FX, REPL, think scratchpad, Docker execution,
placeholder image generation), `tools/builtin/runtime_tools.py` (memory
aliases, channel tools, agent spawn/send/list/kill with live sessions,
sub-agents over `BackgroundTasks`, approvals, knowledge, voice, `llm`),
`tools/builtin/knowledge_media.py` (SQLite knowledge graph, memory index/
retrieve/manage, transcribe/TTS tools, user profile, skill manage, digest
collect), `tools/builtin/action_queue.py` (proactive action queue, decision
journal, permission policy, hybrid retrieval, chunk scanning, calendar).
Live `memory_get` added alongside the existing runtime-installed
`memory_delete`. A `repl` reset semantics bug found by the new tests
(reset discarded the code argument) is fixed.

### Sandbox package
`sandbox/` with subprocess/Docker/WASM backends; `code_execute` routes
through it when `[sandbox].enabled` — Docker and WASM report unavailable
honestly when their runtimes are absent.

### Skills
The 20 upstream TOML step-skills are converted into the local catalog as
agentskills.io folders (SKILL.md + skill.toml) plus a runtime step executor
(JSON template substitution, inputs → step outputs, hard/soft failure
semantics). Sources: bundled (install-on-first-use), GitHub repo trees,
and generic JSON indexes (Hermes/OpenClaw pattern) with sync markers.
`[skills] install_bundled` / `sources` config added.

### Eval depth
`bench/scorers.py` (exact, contains, not_contains, regex, numeric with
tolerance, JSON shape, length), `bench/datasets.py` (core, reasoning,
knowledge, instruction, coding — 24 deterministic cases graded by the
scorers), `EvalCase.scorer/expected`, and `compare_reports` regression
diffing. Upstream's public-benchmark datasets (GAIA/MMLU-Pro/GPQA) require
network downloads; these built-ins cover the same skills offline.

### CLI (33 → 44 top-level commands)
New: `scan`, `telemetry`, `digest`, `research` (+`deep-research-setup`
alias), `quickstart`, `operators`, `status`; aliases `scheduler`→cron,
`tool`→tools, `channel`→channels; `skill run` and `skill sources`
subcommands. Count matches upstream's ~44.

---

## 4. Verification (this audit)

```
pytest:            391 passed (367 prior + 24 new P2 tests), 0 failures
e2e smoke:         46/46 checks passed (server on :8799, webchat configured)
undefined names:   0 (pyflakes guard in the suite)
routes:            143 templates · 74 under /v1
adapters:          21 · agents: 26 · connectors: 28 ids
tools:             88 static / 96 live at runtime
CLI commands:      44 top-level · model catalog: 78 models
skills:            20 bundled, install-on-first-use verified
studio branding:   0 references remaining
Windows .exe:      spec + installer ready (PsychoCARLA.spec, install.ps1),
                   not built — needs Windows 11
```

Two e2e preconditions worth recording: the smoke target must configure a
channel (`[channels.webchat]`) or inbound-channel checks 503 by design;
and the old `8 agents registered` assertion was updated to `>= 8` now that
the registry legitimately carries 26.

---

## 5. Deliberate deviations (unchanged from Audit #5)

Cloud TTS/STT (ElevenLabs/Cartesia/Deepgram) excluded by the CPU-only
voice rule; Apple engines/connectors irrelevant on Windows 11; cloud
analytics replaced by local-first analytics; the native ONNX engine and
offline fallback have no upstream counterpart.

## 6. Naming (still awaiting your pick)

Audit #5 §1 stands: recommendation **PSYCHO-N.O.V.A.** (Native Operational
Voice Assistant). No code, package, or product rename has been applied.
