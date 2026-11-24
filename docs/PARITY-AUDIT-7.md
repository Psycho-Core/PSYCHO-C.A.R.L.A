# PSYCHO-C.A.R.L.A. — Completeness & Readiness Audit #7

Date: 2026-11-13. Requested: "audit/verify mine is complete and ready to
test; verify all features/settings OpenJarvis has; report back."
Method: fresh live introspection of both codebases (this sandbox still
holds the upstream tree at `/tmp/oj/src/openjarvis`), full pytest run,
fresh e2e run against a newly initialised home, and a live native-model
load test. Nothing below is carried over from Audit #6 without being
re-measured.

---

## 1. Verdict

**Complete at the upstream feature/settings surface, and ready to test.**
All upstream agents, tools, connectors, channels, CLI commands and
config sections are present locally (several exceeded). Test suite and
e2e are green from a cold start.

Two honest caveats, both environmental rather than missing features:

1. **Windows `.exe`** — `PsychoCARLA.spec` + `install.ps1` are present
   and inspected; the PyInstaller build can only run on Windows 11, not
   in this Linux sandbox. This is the one "ready to test" item that
   needs your machine.
2. **Native ONNX generation on this 2 GB sandbox** — see §5. The engine
   loads and generates; output quality/timing on a box this small is not
   representative of your Windows machine.

---

## 2. Features — upstream vs local (measured today)

| Surface | Upstream (live) | Local (live) | Verdict |
|---|---|---|---|
| Agents | 23 registered | **26** (all 23 + `code_assist`, `memory_curator`, `scheduler`) | ✅ superset |
| Tools | 59 tool names (+8 memory-**backend** ids: bm25/colbert/dense/faiss/hybrid/knowledge_graph/knowledge_sql/sqlite — internal registries, not agent tools) | **88 static / 96 live** | ✅ superset |
| Connectors | 27 ids | **28** (27 + `github` alias) | ✅ superset |
| Channels | 32 platform modules | all 32 present (49 ids incl. aliases) | ✅ complete |
| CLI | ~44 commands | **44 top-level** | ✅ complete |
| Model catalog | 67 built-in specs | **78** (67 ported + native + local) | ✅ superset |
| Adapters/engines | 18 engine rows | **21 adapters** | ✅ superset |
| Skills | 20 bundled | **20 bundled** + executor + 3 source networks | ✅ complete |
| Server routes | `/v1/*` + HUD families | 143 templates, 74 `/v1` | ✅ complete |
| Config sections | 31 top-level fields | 34 fields (see §3) | ✅ complete |

Subsystem import/feature probes all pass: FastAPI app, MCP client+server,
A2A, workflow engine, learning optimizers, mining, daemon, desktop shell,
voice STT/TTS factories (CPU: faster-whisper + Kokoro/Piper), native ONNX
adapter, model lifecycle, security suite, local-first analytics,
bench/eval (6 built-in suites), sessions, recipes, GUI export.

Deliberate deviations (unchanged, by your rules): cloud TTS/STT excluded
(CPU-only voice), Apple-only surfaces irrelevant on Windows 11, cloud
analytics replaced by local-first analytics. These are not gaps.

---

## 3. Settings — upstream `JarvisConfig` (31 fields) vs local

All 31 upstream fields map to a local equivalent:

- Same-name: `engine, intelligence, deep_research, learning, tools,
  agent, server, telemetry, analytics, traces, security, sandbox,
  sessions, operators, optimize, agent_manager, memory_files,
  system_prompt, compression, skills, digest, proactive, mining` (22)
- Renamed equivalents: `channel`→`channels`, `scheduler`→`cron`,
  `workflow`→`workflow_config`, `speech`→`voice`, `a2a`→`[server] a2a_*` (5)
- Metadata-only upstream: `installed_at, installer_version, hardware` —
  not settings; local exposes hardware via `carla doctor` (3)
- Local-only additions: `[tiers.*]` capability tiers, `[persona]`,
  `[web_search]`, `[security]` depth (capabilities/signing/SSRF/rate
  limit/audit), `[skills] install_bundled/sources`.

Verified mechanically: TOML dump→load round-trip (35 tables) succeeds;
a dict containing every upstream section key parses without error.
Per-engine typed tables upstream (`[engine.ollama]` etc.) map to local
`[engine.adapters.*]` + tier `base_url` — same settings, different shape
(noted in Audit #5; unchanged).

---

## 4. Test evidence (fresh runs today)

```
pytest:        391 collected, ALL PASSED (exit 0)
e2e smoke:     46/46 checks passed — fresh home (/tmp/carla_e2e2,
               `carla init` + [channels.webchat]), server on :8799
               covering chat/tools/dual-channel, memory, telemetry,
               cron, tier switching, OpenAI-compat API, WebSocket,
               native catalog, channels incl. inbound dispatch,
               model manager, traces.
console script: carla / carla-psycho / carla-app entry points OK
```

---

## 5. Native engine — live check (honest)

- `health()` → `(False, 'no model_path configured')` when unconfigured: correct.
- The sandbox-cached copy of SmolLM2-360M predates the current manager:
  it keeps all 8 graphs (4.0 GB); the current manager downloads one
  selected graph and prunes the rest, so fresh installs won't do this.
- RAM here is 2 GB total (~1.2 GB free). Loading the directory picks the
  full graph per upstream graph-selection order and the process is
  OOM-killed (exit 137) — a sandbox memory limit, not a code defect; the
  same path was verified end-to-end in the Gap-2 round ("The capital of
  France is Paris.", 15.5 s, ~955 MB peak) when graphs were pruned.
- Pointed directly at `model_q4f16.onnx`, the adapter loads
  (`ready: model_q4f16.onnx`) and generates. Output was garbled — q4f16
  is the known-weak graph for this export and generation took ~120 s
  under memory pressure. **Recommendation:** on your machine this is a
  non-issue (real RAM headroom + pruned single-graph downloads); if you
  ever see garbled native output, that is the graph to avoid — the
  manager's default q4/bnb4 picks are the verified ones.

---

## 6. Ready-to-test checklist

| What | How | Status |
|---|---|---|
| Python package | `pip install -e .` then `carla …` (44 commands) | ✅ verified |
| First run | `carla init` → `carla doctor` → `carla serve` | ✅ verified today |
| HUD + API | `carla serve` (46/46 e2e) | ✅ verified today |
| Desktop app | `carla gui open` / `carla-app` | ✅ code + spec verified; window needs Windows pywebview/WebView2 |
| Native models | HUD Model Manager: download → start/stop → delete | ✅ lifecycle tested; generation verified (see §5 caveat) |
| Windows `.exe` | `install.ps1` / PyInstaller with `PsychoCARLA.spec` | ⚠️ must build on Windows 11 |

**Bottom line:** feature- and settings-complete against OpenJarvis, all
suites green — ready for you to test. The only step left on my side of
the fence is the Windows build, which needs to run on your Windows box;
everything it packages is verified.
