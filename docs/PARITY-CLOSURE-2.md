# PSYCHO-C.A.R.L.A. — Parity Closure, Round 2

**Status:** ✅ Complete — every audited gap from round 2 is closed and verified.

Round 1 closed five gaps (energy/FLOPs telemetry, learning surfaces, the
server API-key guard, the bench/eval harness, the skills catalog). Round 2
closes the **remaining** gaps: the A2A protocol, OAuth connectors, the
daemon / workflow / sessions / mining / recipes subpackages, the upstream
presets, the per-engine adapters, the in-process `afm` engine, and the
desktop GUI client.

Upstream was cloned to `/tmp/oj` (`open-jarvis/OpenJarvis`, shallow) and
every implementation was written against the real upstream source rather
than from a description of it.

---

## Verification summary

| Check | Result |
|---|---|
| `pytest tests/test_core.py tests/test_round2.py` | **198 passed** (106 + 92 new) |
| `tests/e2e_smoke.py` | **32/32 green** |
| Live A2A round-trip against the running HUD | **passed** — card discovered, task completed, real answer returned |
| Live workflow run through the real agent stack | **passed** — parallel staging, templated prompts, condition branching |
| Live session recording through `ask()` | **passed** — exchanges land in the session store |
| Live MCP server connection | **passed** — `carla mcp add github` then `mcp status` reported `connected: true`, `github-mcp-server 0.6.2` |
| Live vault round-trip | **passed** — secret stored, retrieved, and confirmed absent from the file on disk |
| Live HUD screenshot | **passed** — Energy, Power, FLOPs, Skills Catalog and Learning Surfaces all render; zero console errors |
| 14 adapters registered | **passed** — `afm, apple_fm, exo, generic_http, lemonade, llamacpp, lmstudio, mlx, nexa, ollama, openai_compat, sglang, uzu, vllm` |
| 7 presets install and load | **passed** — all parse through the real config loader |
| 16 agent templates load | **passed** — every one names only real tool names |
| Default config TOML parses cleanly | **passed** — regression test added after a section-header bug was caught |
| 31 top-level CLI commands | **passed** — every one parses and dispatches |

---

## 1. Per-engine adapters

`src/psycho_carla/providers/adapters/engines.py`

A data-driven `ENGINE_SPECS` table generates OpenAI-compatible adapter
subclasses for **vllm, sglang, mlx, lmstudio, exo, nexa, uzu, apple_fm,
lemonade**. `normalize_openai_base_url()` strips a single trailing `/v1`
(the adapter re-appends it, so `http://host:8000/v1` would otherwise
double up). `ENGINE_DEFAULT_HOSTS` holds the default port for each
engine.

**`llamacpp` is deliberately excluded from the generated table.** It
speaks llama.cpp's native `/completion` endpoint rather than
`/chat/completions`, and the pre-existing dedicated adapter is strictly
better for it. A regression test asserts that `build_adapter("llamacpp")`
still resolves to `LlamaCppAdapter`.

## 2. In-process `afm` engine

`src/psycho_carla/providers/adapters/afm.py`

`AfmAdapter` drives Apple's Foundation Models **in-process** — no HTTP
hop, no local server. `afm_available()` returns `(available, reason)`
rather than raising, so the absence of the optional
`apple-foundation-models` SDK is an actionable message, not an import
error. `IN_PROCESS_ENGINES = ("afm",)`.

## 3. A2A protocol

`src/psycho_carla/a2a/{protocol,server,client,tool}.py`

JSON-RPC 2.0 throughout.

- **States** — `submitted` · `working` · `input-required` · `completed` ·
  `canceled` · `failed`.
- **Card** — `AgentCard(name, description, url, version, capabilities,
  skills, authentication)`; served at `GET /.well-known/agent.json`.
- **Task** — `A2ATask(task_id, state, input_text, output_text, history,
  metadata)`.
- **Server methods** — `tasks/send`, `tasks/get`, `tasks/cancel`,
  `agent/card`. Auth uses `secrets.compare_digest`. Error codes:
  `-32001` unauthorised, `-32601` method not found, `-32602` invalid
  params.
- **Client** — `discover()`, `send_task()`, `get_task()`, `cancel_task()`,
  `call()`, over `urllib`.
- **As a tool** — `A2AAgentTool` + `register_a2a_agent()` expose a remote
  agent in the tool registry; failures come back as a `ToolResult`, never
  as an exception.

**Live proof:** the client discovered the card as `PSYCHO-C.A.R.L.A.`
(capabilities `chat`, `tools`, `memory`, `skills`), sent a task, and got
state `completed` with a real answer and two history entries.

## 4. Workflow engine

`src/psycho_carla/workflow/{types,graph,engine,builder,loader}.py`

Node types: **agent · tool · condition · parallel · loop · transform**.

- `WorkflowGraph.validate()` — DFS cycle detection, orphan warning.
- `WorkflowGraph.topological_sort()` — Kahn's algorithm.
- `WorkflowGraph.execution_stages()` — groups independent nodes so a
  stage runs in parallel on a `ThreadPoolExecutor`.
- `WorkflowEngine(max_parallel=4, default_node_timeout=300)` — runs the
  stages, renders `{input}` / `{node_id}` prompt templates, honours loop
  iteration caps.
- `evaluate_condition()` — a **restricted** expression evaluator (`==`,
  `!=`, `<`, `<=`, `>`, `>=`, `and`, `or`, `not`). It never calls `eval`,
  and a Python expression is treated as an unknown identifier, not
  executed.
- `WorkflowBuilder` — a fluent API.
- `load_workflow()` / `load_workflow_dir()` — TOML.

Two condition-semantics bugs were found and fixed while testing:

1. A bare numeric token (`10`) was resolved as a node reference and came
   back empty. Now a bare token that names no known node is a **literal**
   in a comparison — so `count == 10` and `flag == yes` both work.
2. A bare *standalone* reference to a node that never ran was truthy
   (it fell through to the literal path). Now a standalone reference is
   falsy, and only an explicit `{node}` reference resolves to empty.

## 5. Cross-channel sessions

`src/psycho_carla/sessions/{session,session,compression}.py`

`SessionStore` — SQLite with `sessions`, `session_messages` and
`session_anchors` tables. `SessionIdentity(user_id, display_name,
channel_ids)` maps a person across channels; `find_by_channel()` and
`link_channel()` resolve the same conversation from the HUD or the CLI.

- **Consolidation** — past `consolidation_threshold`, the older half is
  summarised into an *anchor* and the recent half is kept verbatim.
- **Decay** — `expire()` removes sessions idle beyond `max_age_hours`
  *and* their messages and anchors.
- **Compression** — deliberately **extractive**, not abstractive: it
  scores lines by signal and stitches the most informative ones together.
  An abstractive summary would need a model call on a background path,
  where determinism matters.

`ask()` now records into the session store alongside the memory store.

## 6. OAuth connectors

`src/psycho_carla/connectors/{oauth,base,registry,store}.py`

Providers: **google** (Drive + Calendar + Gmail + Tasks, one consent),
**github**, **strava**, **spotify** (port 8888, basic auth).

`run_connector_oauth()` resolves credentials (argument → stored file →
environment), builds the authorisation URL, opens the browser, waits on a
localhost callback server, exchanges the code and writes the tokens.

Two security properties, both tested:

- `require_access_token()` rejects an error-shaped token response, so a
  false-success credential file is never written.
- Credential files are written **0600** on POSIX.

`KnowledgeStore` indexes synced documents with a tokenised term table
and ranks by query-term overlap.

## 7. Daemon

`src/psycho_carla/daemon/{gateway,service}.py`

`GatewayDaemon` owns the runtime, scheduler and server as one supervised
unit. `session_key(platform, chat_type, chat_id, thread_id)` produces the
canonical `agent:main:{platform}:{chat_type}:{chat_id}:{thread_id}`.

Service files for all three platforms: a **systemd** user unit, a
**launchd** plist, and a **Windows scheduled task**. Each embeds the
absolute path to the current interpreter, so the service keeps working
after the virtual environment is deactivated.

## 8. Presets and agent templates

`src/psycho_carla/templates/{presets.py,presets/,agent_templates.py,data/}`

**7 presets**, each a complete working configuration:
`morning-digest-mac` · `morning-digest-linux` · `morning-digest-minimal`
· `deep-research` · `code-assistant` · `scheduled-monitor` ·
`chat-simple`.

**16 agent templates**, each with a role, tools and a system prompt:
`architect` · `assistant` · `code-reviewer` · `data-analyst` · `debugger`
· `deep-researcher` · `fact-checker` · `inbox-triager` · `meeting-prep` ·
`monitor` · `note-taker` · `security-auditor` · `summarizer` ·
`translator` · `tutor` · `writer`.

A regression test asserts every template names only tool names that
actually exist in the registry.

### Config-driven cron

Writing the presets exposed a real gap: the scheduler had no config
path. `[[cron]]` blocks are now parsed into a new `CronConfig` and
registered with the scheduler when the runtime starts — verified with
two jobs from the `scheduled-monitor` preset.

## 9. Mining subsystem

`src/psycho_carla/mining/{types,providers,registry}.py`

The optional compute-reuse path, off by default and dependent on nothing
in the core runtime.

Providers: `cpu-pearl` · `vllm-pearl` · `apple-mps-pearl`.

Every provider's `detect()` is a **pure inspection** — no subprocess, no
network — and returns a *reason* when it cannot run. With the optional
packages absent the system reports *"Pearl packages are not installed …
pip install 'psycho-carla[mining]'"* rather than failing.

## 10. Recipes

`src/psycho_carla/recipes/{loader,composer,data/}`

A recipe pins an engine, model, agent, learning policy and eval suite
into one reproducible bundle. Two kinds: `discrete` (benchmarking) and
`operator` (persistent/scheduled).

7 recipes ship: `general-assistant` · `coding-assistant` ·
`research-assistant` · `monitor-assistant` · `gaia-orchestrator` ·
`terminalbench-react` · `coding-benchmark`.

`recipe_to_config_overrides()` and `recipe_to_eval_suite()` compose a
recipe into a runnable system or a graded suite.

## 11. Desktop GUI client

`src/psycho_carla/gui/{launcher,desktop}.py`

Upstream's `jarvis gui` starts a separate Node frontend. PSYCHO-C.A.R.L.A.
serves its HUD from Python, so the equivalent is:

- `launch_gui()` — starts the console if it is not already running, then
  opens the HUD, preferring an **application-mode window** (no tabs, no
  address bar) when Chrome, Chromium or Edge is available.
- `gui_status()` — reports whether the HUD is reachable.
- `export_desktop_bundle()` — writes a **standalone HTML file** with every
  stylesheet and script inlined. It opens straight from disk with no
  server at all.

The desktop-mode requirement holds: there is no mobile-specific layout,
and the exported bundle keeps the CSS-variable palette so it can be
re-themed by editing the `:root` block.

---

## 12. The secret vault

`src/psycho_carla/security/vault.py`

The one capability upstream had that PSYCHO-C.A.R.L.A. was missing
entirely: a place for secrets that do not belong in a configuration file.

- **AES-GCM** via `cryptography`. The vault **refuses to store anything**
  when that package is absent — a secret store that quietly degrades to
  plaintext is worse than no secret store.
- **One master key**, generated on first use from 32 bytes of OS entropy,
  written 0600 beside the vault.
- **No plaintext on disk** — verified by a test that searches the
  encrypted file for the secret.
- A **wrong key raises**, rather than returning garbage.
- `PSYCHO_CARLA_VAULT_KEY` overrides the key file for headless setups.

## 13. MCP quick-setup

`carla mcp add <name>` writes a server into the config. Presets for
`filesystem`, `github`, `slack`, `memory`, `fetch`, `notion`, `postgres`,
`puppeteer` and `sqlite`; anything else takes `--exec` / `--url`.

A `--key` goes **into the vault**, not the config file. The config holds
only a reference (`${PSYCHO_CARLA_MCP_GITHUB_KEY}`), and the MCP client
resolves it against the vault at start-up. Verified end-to-end: the
secret resolved with no environment variable set, and the config file
contained no token.

The config is edited **textually**, so the user's comments and formatting
survive.

## 14. Remaining CLI surface

Six more commands, each covering a real upstream capability that had no
local equivalent:

| Command | Covers |
|---|---|
| `carla vault set\|get\|list\|remove` | The encrypted secret store |
| `carla tools list\|inspect` | Tool catalogue and full schema |
| `carla registry list\|show` | Agents, tools, tiers and adapters in one place |
| `carla feedback score\|thumbs\|list\|stats` | The trace-rating loop, which existed in the library and the HUD but had **no CLI** |
| `carla compose list\|show` | The upstream name for the recipes command |
| `carla tunnel start\|status` | Public exposure via `cloudflared` |

The CLI is now **31 top-level commands**.

## 15. The HUD surfaces the round-1 and round-2 data

The one standing item from round 1: the energy, skills and learning data
existed as API endpoints but were **never shown in the UI**.

- **Energy**, **Power** and **FLOPs** metrics in the telemetry panel.
- **Skills Catalog** panel.
- **Learning Surfaces** panel — all five surfaces with their policies and
  on/off state.

And the honesty rule was applied to the numbers: `energy_summary()` now
returns `None` — not `0` — for a window with no measurement. An unmeasured
joule count is not zero joules. The HUD shows `—` in that case.

The static exporter's mock adapter was updated to match, so the offline
preview shows the same panels.

Verified with a headless Chromium screenshot: all panels render, and
there are **zero console errors**.

---

## Other changes

- `available_adapters()` now loads the bundled adapters before answering,
  so the list is complete even when called before a router exists.
- `providers/adapters/__init__.py` re-exports `available_adapters`,
  `build_adapter` and `get_adapter_class`.
- New HUD endpoints, all verified **200**: `/api/workflows`,
  `/api/connectors`, `/api/templates`, `/api/recipes`, `/api/mining`.
- The default config template gained `[sessions]`, `[[cron]]` and the
  server's `a2a_*` keys.

## Bugs found and fixed while testing

| Bug | Fix |
|---|---|
| `SessionStore.expire()` deleted the session rows before selecting them, leaving orphaned messages and anchors | Select first, then delete children, then parents |
| Workflow bare numeric operand resolved to a node reference and came back empty | Bare token that names no known node is a literal in a comparison |
| Workflow standalone reference to an unrun node was truthy | Standalone references resolve through `outputs` and are falsy when absent |
| `[cron]` inserted before `[server]` in the config template silently swallowed the server's keys into the cron table | Restored the `[server]` header; added a regression test asserting each table holds its own keys |
| `available_adapters()` returned an empty list when called before a router was built | It now loads the bundled adapters first |
| `SessionStore` consolidation left the old rows in place | Fixed in the same commit as the expiry fix |
| `normalize_openai_base_url()` semantics were assumed, not read | Confirmed against the implementation: it *strips* a trailing `/v1` |

---

## What is still open

These are **not** parity gaps — they are polish items beyond the audited
scope:

- `requirements.txt` and an exported `psycho_carla.toml`.
- `core/hardware.py` as a dedicated module (currently `hardware_summary()`
  lives in `core/telemetry.py`).
- Package entry-point files for the console scripts.
- MCP client late-import cleanup.
- Exact local validation of the Kokoro API path (Kokoro OOMs on this
  1.9 GB sandbox; voice is validated via Piper).
- The upstream `channels` subsystem — messaging-platform integrations.
  The A2A protocol and the connectors cover the data-source side, but
  there is no Slack/Telegram/Discord *channel* adapter.
- `deep_research_setup`, which detects local sources (Apple Notes, etc.)
  to seed a research profile.
- `self_update`, which pulls a newer version from git.
