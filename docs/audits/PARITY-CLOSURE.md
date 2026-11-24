# OpenJarvis Parity Closure Report

**Date:** 2026-11-13
**Baseline:** [`docs/PARITY-AUDIT.md`](PARITY-AUDIT.md) — the audit that ranked the gaps
**Subject:** `psycho_carla` @ `/home/user/src/psycho_carla`

This report records the closure of the five highest-priority gaps from the
audit, in the agreed order. Each section states what was missing, what was
built, where it lives, and how it was verified.

---

## Summary

| # | Gap (audit rank) | Status | Primary artefact |
|---|---|---|---|
| 1 | Energy + FLOPs telemetry | ✅ **Closed** | `core/energy.py`, `core/telemetry.py` |
| 2 | Learning: tool-selection + engine-spec optimisers, policy switches | ✅ **Closed** | `learning/surfaces.py`, `learning/skills_optimizer.py` |
| 3 | Server API-key guard | ✅ **Closed** | `server/auth.py`, `server/app.py` |
| 4 | Bench / eval harness | ✅ **Closed** | `bench/runner.py` |
| 5 | Skills catalog | ✅ **Closed** | `skills/catalog.py`, `skills/store.py` |

**Verification:** 106 unit tests pass (up from 71); 32/32 e2e checks green;
all HUD routes return 200.

### Where the numbers moved

| Dimension | Before | After |
|---|---|---|
| Subpackages | 11 | 14 (`bench`, `skills`, + `core/energy.py`) |
| CLI commands | ~14 | 19 (`bench`, `eval`, `auth`, `skill`, + `optimize` surfaces) |
| Learning surfaces | 2 | 4 + skill-description optimiser |
| Learning policy switches | 0 | 5 (routing, intelligence, agent, tools, engine) |
| Energy/FLOPs metrics | 0 | watts, joules, joules/request, FLOPs, FLOPs/token |
| Security guard | none | non-loopback bind refused without a key |
| Unit tests | 71 | 106 |

---

## 1. Energy + FLOPs telemetry

### What was missing

The audit found `_nvidia_smi()` queried `memory.used,memory.total` only. There
was no power draw, no energy vendor abstraction, no FLOPs, and none of the
upstream `[telemetry]` fields (`energy_vendor`, `gpu_poll_interval_ms`,
`warmup_samples`, `steady_state_window`, `steady_state_threshold`).

This was the most significant gap because upstream treats **energy, FLOPs,
latency and cost as first-class evaluation constraints** — the core of its
research thesis. Without energy metrics the central claim is untestable.

### What was built

**`core/energy.py`** — a new module with:

- **Six vendor probes**, each returning `None` rather than guessing:
  - `nvidia` — `nvidia-smi --query-gpu=power.draw`
  - `amd` — `rocm-smi --showpower`
  - `apple` — `powermetrics --samplers cpu_power`
  - `cpu_rapl` — Linux RAPL via `/sys/class/powercap`, with a stateful
    `RaplReader` that converts energy counters into watts across an interval
    and handles counter wrap-around
  - `cpu` — TDP estimate parsed from the CPU model string, explicitly labelled
    as an estimate in `PowerSample.source`
  - `none` — disabled
- **`EnergyMonitor`** with `auto` resolution order and per-vendor fallback.
- **FLOPs estimation** — `2 · N · tokens` for a dense forward pass, where `N`
  is the parameter count. `params_from_model_name()` recognises common size
  suffixes (`14b`, `11b`) and known model families.
- **Honesty invariant:** when the parameter count is unknown, `estimate_flops()`
  returns `None` — not `0.0`. A metric that could not be measured is reported
  as unknown, never as zero.

**`core/telemetry.py`** — schema and aggregation:

- New `energy_samples` table (ts, request_id, agent, tier, model, watts,
  joules, vendor, flops).
- `requests` gains `flops`, `energy_joules`, `power_watts`.
- `resources` gains `power_watts`, `energy_vendor`.
- A `_MIGRATIONS` mechanism applies new columns to pre-existing databases.
- `RequestMetric` carries the three new fields.
- `RequestTimer.__exit__` computes FLOPs from token counts and energy from
  measured power × elapsed time.
- `summary()` now returns an `energy` block and `total_flops`.
- `energy_summary()` returns a per-model breakdown with joules/request and
  FLOPs/token.

**`core/config.py`** — `TelemetryConfig` gains `track_energy`, `energy_vendor`,
`gpu_metrics`, `gpu_poll_interval_ms`, `warmup_samples`, `steady_state_window`,
`steady_state_threshold`.

### Verification

```
FLOPs:      estimate_flops(100, 50, 7.0) == 2 × 7e9 × 150  ✓
Unknown:    estimate_flops(100, 50, 0.0) is None            ✓  (not 0.0)
Params:     "qwen2.5-14b-instruct" → 14.0                   ✓
Probe:      EnergyMonitor("none").sample().available False  ✓
Telemetry:  summary()["energy"]["total_joules"] == 12.5     ✓
            energy_summary()["models"][0]["flops_per_token"] ✓
Schema:     PRAGMA table_info(resources) has power_watts    ✓
```

Covered by 7 new tests in `TestEnergyTelemetry`.

---

## 2. Learning: tool-selection + engine-spec optimisers

### What was missing

Upstream optimises **four** surfaces; the local implementation had only
prompts and routing — 50% of the Learning primitive. The per-surface
`[learning.*].policy` switches did not exist either.

### What was built

**`learning/surfaces.py`** — the two missing surfaces:

- **`ToolSelectionOptimizer`** — scores every tool observed in the traces on
  success rate (60%), user rating (25%) and latency penalty (15%), then
  proposes a per-agent whitelist: `keep`, `drop` and `promote`. Tools with
  insufficient evidence are explicitly left in place "for lack of evidence"
  rather than being judged on a single sample.
- **`EngineSpecOptimizer`** — proposes per-tier `quantization`,
  `context_window`, `max_tokens`, `batch_size` and sampling parameters from
  observed behaviour, scored with the same objective mix used everywhere else
  (accuracy 0.6 / latency 0.2 / cost 0.1 / efficiency 0.1). Fast tiers get a
  lower temperature for determinism; high-latency tiers get a quantisation
  proposal.

**`learning/skills_optimizer.py`** — a sixth optimiser for the skill
description, which in the `agentskills.io` model is the *only* thing the agent
sees before deciding whether to load a skill. Flags descriptions that are too
short, too long, missing a trigger clause, or built from generic filler.
Supports `--policy dspy` and `--policy heuristic`.

**`core/config.py`** — `LearningConfig` gains `tool_selection`, `tools`,
`engine` and `weights`, plus `policy_for(surface)` and
`surface_enabled(surface)`. The `tools` / `tool_selection` alias is honoured
either way, and `"none"` / `"off"` disables a surface.

**`core/runtime.py`** — all four optimisers are instantiated; `optimize()`
dispatches by surface and respects the policy switches.

**`cli/main.py`** — `carla optimize` now covers all surfaces and reports the
switch state:

```
carla optimize
carla optimize --surface tools --agent coder
carla optimize --surface engine --tier fast_tier
carla optimize --surface skills --policy dspy
```

### Verification

Synthetic traces: 8 successful `web_search` uses, 6 failed `calculator` uses,
4 deep-tier calls.

```
Tool selection:
  keep    : ['web_search']
  promote : ['web_search']
  drop    : ['calculator']
  scores  : web_search 0.948 > calculator 0.170
  confidence: 0.79

Engine spec:
  fast_tier: model=qwen2.5-7b-instruct quant=none temp=0.3
  deep_tier: model=qwen2.5-32b-instruct quant=q5_k_m
```

Covered by 5 new tests in `TestLearningSurfaces`, plus a live run against 32
real traces from the running server.

---

## 3. Server API-key guard

### What was missing

The audit flagged this as a **security gap, not a feature gap**: upstream
refuses a non-loopback bind without a key, because an exposed agent carries
shell and file tools. The local server bound `0.0.0.0` with no key and no
`auth` command.

### What was built

**`server/auth.py`**:

- `is_loopback()` — recognises `127.0.0.0/8`, `localhost`, `::1`.
- `resolve_auth()` — resolves the key from an explicit value or the
  environment (`PSYCHO_CARLA_API_KEY`, then `CARLA_API_KEY`), then **raises
  `RuntimeError`** if the bind is non-loopback and no key is present. The
  error message names the host and lists three concrete fixes.
- `extract_key()` / `check_key()` — accepts `Authorization: Bearer <key>` and
  `X-API-Key: <key>`; comparison uses `hmac.compare_digest` so it does not
  leak length or content through timing.
- `generate_api_key()` — URL-safe, 32-byte.

**`server/app.py`** — an HTTP middleware rejects unauthorised requests with
401 and `WWW-Authenticate: Bearer`. `/healthz`, `/docs`, `/openapi.json` and
`/redoc` are exempt. The WebSocket endpoint checks separately (it cannot use
HTTP middleware) and accepts `?api_key=<key>` as well as headers, closing with
code `4401`.

**`core/config.py`** — `ServerConfig` gains `api_key_env`, `api_key` and
`require_auth`.

**`cli/main.py`** — `carla auth generate-key` and `carla auth status`.

### Verification

```
loopback 127.0.0.1 : True
loopback 0.0.0.0   : False
0.0.0.0 without key -> refused OK   (RuntimeError raised)
0.0.0.0 with key    -> allowed, enabled=True
bearer extraction   : 'abc123'
key check           : good=True bad=False

HTTP middleware:
  no key      -> 401
  wrong key   -> 401
  bearer key  -> passes
  x-api-key   -> passes
  /healthz    -> 200 (exempt)
```

Covered by 7 new tests in `TestServerAuth`.

---

## 4. Bench / eval harness

### What was missing

Upstream ships `bench/`, `evals/`, `jarvis bench` and `jarvis eval`. Neither
existed locally.

### What was built

**`bench/runner.py`** — two deliberately separate tools, because they answer
different questions:

- **`Bench`** measures *performance*: latency (mean / median / p95 / min /
  max), TTFT, tokens per second, energy per case, total FLOPs, FLOPs/token.
  Built-in workloads `chat` and `tool_use`; JSON workload files supported.
- **`EvalSuite`** measures *quality*: cases checked for expected content,
  forbidden content, regex patterns, minimum token count and latency ceiling,
  with per-case weights. Accuracy is the weighted pass rate.

Both write a JSON report with a `summary` block. Bench errors are captured
per case rather than aborting the run.

**`cli/main.py`** — `carla bench` and `carla eval`, both with `--json OUT`.

**Model-agnostic by construction:** the harness drives `runtime.ask()`, and
falls back to `runtime.router.generate()` — it never names a model or a
provider.

### Verification

Live run against the mock OpenAI-compatible server:

```
cases        : 3
passed       : 3 (100.0%)
latency mean : 13429.89 ms
latency p95  : 36139.31 ms
tokens/sec   : 743.63
total tokens : 426
energy       : 35.4752 J (11.825067 J/case)
```

Eval suite against the same server: 4 cases, accuracy 50% — and the two
failures were **genuine** (the mock model does not say "Boss" and does not
answer "2+2=4"), confirming the grader discriminates rather than rubber-stamps.

Covered by 4 new tests in `TestBenchEval`.

---

## 5. Skills catalog

### What was missing

Upstream's headline feature: ~13,700 community skills, the `agentskills.io`
specification, `jarvis skill install/sync`, and `jarvis optimize skills
--policy dspy`.

### What was built

**`skills/catalog.py`** — the `agentskills.io` specification, implemented:

- `SKILL.md` = YAML frontmatter + Markdown body, with optional `scripts/`,
  `references/` and `assets/` directories.
- **Validation** against the spec: name is 1–64 characters of lowercase
  letters, digits and single hyphens; it must match the parent directory
  exactly; description is required and capped at 1024 characters; compatibility
  capped at 500; body recommended under 500 lines; **angle brackets in the
  frontmatter are rejected**, because the spec warns they can be used to
  smuggle instructions into a system prompt.
- PyYAML is used when available; a tolerant built-in parser handles the
  subset the spec defines otherwise, so the feature has no hard dependency.
- **`SkillCatalog`** implements progressive disclosure: `metadata_prompt()`
  emits only names and descriptions (~100 tokens per skill);
  `prompt_block()` emits the full body for one skill; `search()` ranks skills
  by trigger-term overlap, favouring description hits over name hits.

**`skills/store.py`** — on-disk management:

- `install` from a local folder, a git URL (shallow clone, optional
  `--ref`), or an HTTP URL serving a `SKILL.md`.
- `sync` re-fetches each skill from its recorded source, skipping local
  ones.
- `uninstall`, `reload`, `validate`, `export_catalog`.
- Installed sources are tracked in `.skills-state.json` so `sync` knows where
  each skill came from.
- Install refuses a skill that violates the spec.

**`tools/builtin/skill_tools.py`** — three agent tools: `skill_search`,
`skill_load`, `skill_list`.

**`core/runtime.py` + `agents/base.py`** — the catalog metadata is injected
into the agent's system prompt, with the body loaded only on demand.

**`cli/main.py`** — `carla skill install|list|sync|remove|validate|search|show`.

**`examples/skills/status-report/`** — a worked example with `SKILL.md`,
`references/FORMAT.md` and `scripts/collect_commits.py`.

### Verification

```
install  → 'status-report' installed
list     → 1 skill, description shown
validate → installed 1, valid 1, invalid 0
search   → 'status-report' score 0.85 for "weekly summary briefing"

Spec enforcement:
  Bad_Name           → "name must contain only lowercase letters, digits
                       and single hyphens"
  angle brackets     → "description contains angle brackets — the spec
                       discourages them because they can inject unintended
                       instructions"

Progressive disclosure:
  metadata_prompt contains name + description
  metadata_prompt does NOT contain the body

Runtime:
  total tools 26, skill tools ['skill_list','skill_load','skill_search']
  system prompt contains '# Available skills' + the description
  skill_load returns the full body on demand
```

Covered by 8 new tests in `TestSkillsCatalog`.

---

## Still open (lower priority, unchanged from the audit)

| # | Gap | Note |
|---|---|---|
| 6 | A2A protocol | Not implemented |
| 7 | OAuth connectors (`jarvis connect gdrive`) | Not implemented |
| 8 | Daemon / workflow / sessions / mining / recipes subpackages | Not implemented |
| 9 | Upstream presets (`jarvis init --preset`) | Not implemented |
| 10 | Per-engine adapters for vllm / sglang / exo / nexa / uzu / mlx / lmstudio | Functionally covered by `providers/adapters/generic_http.py` |
| 11 | In-process `afm` engine | Not implemented |
| 12 | `jarvis gui` desktop client | The web HUD is the desktop client |

The architectural constraints that governed the original build still govern
all of the above: model-agnostic, CPU-only voice, raw code routed to the UI
only, persona addressing the user as "Boss", CSS-variable HUD palette.
