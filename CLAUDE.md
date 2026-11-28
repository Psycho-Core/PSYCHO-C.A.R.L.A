# CLAUDE.md — PSYCHO-C.A.R.L.A. takeover brief

Read this first. It tells you what this project is, where things live, how to
run and verify it, and the traps previous sessions already fell into.

## What this is

**PSYCHO-C.A.R.L.A.** — **C**oding, **A**utomated **R**easoning, &
**L**anguage **A**ssistant. A local-first, model-agnostic personal AI for
Windows 11: agent framework + holographic web HUD + desktop shell + CPU-only
voice, written from scratch in this repo (package `psycho_carla`).

Key facts, all verified 2026-11-13 (today):

- Original code. OpenJarvis (Stanford) was used only as a *feature/settings
  checklist* during development — there are **zero `import openjarvis`**
  references and no OpenJarvis code in the tree. Parity results live in
  `docs/audits/PARITY-AUDIT-7.md`.
- Renamed from “Psycho-F.R.I.D.A.Y.” to PSYCHO-C.A.R.L.A. on 2026-11-13
  (package, classes, CLI, config, HUD, packaging). See
  `docs/audits/NAME-CHANGE-AUDIT.md`. The only “Friday” left in the source is the
  weekday in one example doc — that is correct English, leave it.
- Status: **feature/settings parity with upstream is complete.**
  490 pytest tests pass (489 + 1 skip); end-to-end smoke is 46/46. See `docs/audits/USER-NAME-AUDIT.md`
  for the most recent feature audit.
- Not yet done (owner-side): the Windows `.exe` has never been built in this
  Linux sandbox — `PsychoCARLA.spec` + `install.ps1` are ready and must be
  built/tested on the owner’s Windows 11 machine.
- Discussed but **not built**: a user-trainable model pipeline (LoRA
  fine-tuning of an open-weight base). Don’t assume it exists.

## The owner

- The owner’s chosen address is in config: `[persona] user_name`.
  **Blank = “Psycho”** (that is the default and how they like it).
  Resolution: `user_name` → legacy `user_title` → `"Psycho"`, via
  `PersonaConfig.address_name` / `Persona.boss`. Never hardcode an address
  name; always go through the persona.
- Owner preferences that are settled — don’t relitigate: their own original
  code (no forks/imports of OpenJarvis), no movie/real-world AI names,
  CPU-only local-first on Windows 11, name is locked as PSYCHO-C.A.R.L.A.

## Layout (src layout, repo root = this directory)

| Path | What |
|---|---|
| `src/psycho_carla/core/` | config (`CarlaConfig`, 35 TOML tables), runtime (`CarlaRuntime`), persona, state, telemetry |
| `src/psycho_carla/agents/` | `BaseAgent` + registry — 26 agents |
| `src/psycho_carla/tools/` | tool registry — 88 static / 96 live tools (`tools/builtin/`) |
| `src/psycho_carla/providers/` | model router + 21 adapters incl. `adapters/native_onnx.py` (in-process ONNX, no Ollama) and `adapters/local_fallback.py` (offline responder) |
| `src/psycho_carla/intelligence/` | model catalog (78 models), model manager, `model_lifecycle.py` (download/start/stop/delete) |
| `src/psycho_carla/channels/` | 32 platform channels + bridge (bridge replies use the persona address name) |
| `src/psycho_carla/connectors/` | 28 knowledge connectors + `KnowledgeStore` |
| `src/psycho_carla/server/` | FastAPI app; HUD API (`server/api/hud.py`, `/api/*`), upstream-compatible `/v1/*` (`server/api/upstream.py`), templates/static in `server/web/` |
| `src/psycho_carla/voice/` | CPU voice: Kokoro/Piper TTS, faster-whisper STT, openWakeWord |
| `src/psycho_carla/cli/main.py` | 44-command CLI (`carla …`) incl. `model custom/free/device/remote/auto` and `export` |
| `src/psycho_carla/core/exports.py` | conversation + training-JSONL export |
| `src/psycho_carla/intelligence/llama_engine.py` | managed llama.cpp: installs the official build, launches/stops `llama-server` (provider `native_gguf`) |
| `src/psycho_carla/providers/adapters/{native_gguf,openrouter}.py` | GGUF-via-managed-engine and OpenRouter adapters |
| `src/psycho_carla/desktop.py` | pywebview desktop shell (`carla-app`) |
| `src/psycho_carla/skills/` | 20 bundled skills, executor, sources |
| `src/psycho_carla/bench/` | eval suites/scorers; CLI `eval --suite/--compare` |
| `src/psycho_carla/security/`, `sandbox/`, `memory/`, `learning/`, `workflow/`, `a2a/`, `daemon/`, `mining/`, `analytics/` | supporting subsystems, all implemented |
| `tests/` | pytest suite (490) + `tests/e2e_smoke.py` (46 checks) |
| `docs/` | `MODELS.md`, `FEATURES.md`, `ARCHITECTURE.md`, `REFERENCE.md` (long README); history in `docs/audits/` |
| `PsychoCARLA.spec`, `install.ps1` | Windows packaging (PyInstaller onedir; installer) |
| `voice_samples/`, `examples/`, `tools/` | samples, example workflows/skills, HUD exporter + sample generators |

Package name in `pyproject.toml` is `psycho-carla`. Entry points:
`carla`, `psycho-carla`, `carla-app` (all → `psycho_carla…`).

## Run / verify

```bash
pip install -e .                       # Python ≥3.10 (3.11 recommended)
carla init                             # writes psycho_carla.toml
carla doctor                           # environment check — expect “Ready”
carla serve                            # HUD + API on http://127.0.0.1:8787
python -m pytest tests/ -q             # 490 tests, expect exit 0
```

End-to-end smoke (needs a configured channel or the bridge is disabled):

```bash
export PSYCHO_CARLA_HOME=/tmp/carla_e2e
carla init
printf '\n[channels.webchat]\nenabled = true\n' >> "$PSYCHO_CARLA_HOME/psycho_carla.toml"
carla serve --port 8799 &              # then:
python tests/e2e_smoke.py --port 8799  # expect 46/46
```

Config/state: `~/.psycho_carla/psycho_carla.toml` (Windows:
`%LOCALAPPDATA%\PsychoCarla`), overridable with `PSYCHO_CARLA_HOME` /
`PSYCHO_CARLA_CONFIG` / `PSYCHO_CARLA_API_KEY`. Models are never bundled;
they download at runtime (Model Manager in HUD / `carla model …`).

Windows packaging (owner’s machine, cannot be done in the Linux sandbox):

```powershell
pip install -e ".[packaging]"
pyinstaller PsychoCARLA.spec           # → dist/PsychoCARLA/PsychoCARLA.exe
.\install.ps1 -Voice                   # or the scripted install path
```

## Model plumbing to know

- **Default engine is `native_gguf`**, which runs GGUF models **inside this process** via
  `llama-cpp-python` (`intelligence/gguf_inprocess.py`; verified on Windows by the Engine check
  workflow: model loaded in-process, zero child processes, Free memory releases it). If
  llama-cpp-python is not installed it falls back to the managed `llama-server` child process
  (`llama_engine.py`). `PSYCHO_CARLA_GGUF_RUNTIME=server|inprocess` forces one. The CPU wheel
  has no GPU offload; a GPU needs a Vulkan/CUDA build of the library. Ollama is
  optional/legacy and absent from the starter config. The real `llama-server`
  binary has never been run in the sandbox (GitHub unreachable) — tests use a
  fake server script locally, but the **Engine check** workflow (Actions tab) has run the
  real thing on Windows: it installed llama.cpp, downloaded Qwen2.5-Coder 1.5B and got an
  answer. All 7 GGUF catalog files were confirmed to exist (`tools/check_catalog.py`).
  llama.cpp's `releases/latest` is a marker with no binaries — the installer scans
  recent releases instead.
- Wake word default is `transcript` (STT-based). openWakeWord needs a custom model.

- The router and the Model Manager share **one** native engine per
  (model path, device) via `native_onnx.shared_adapter()`; Stop / Free memory
  unload that same object. Don't construct `NativeOnnxAdapter` directly for chat.
- Device (`cpu`/`gpu`/`auto`) comes from `PSYCHO_CARLA_NATIVE_DEVICE` or
  `[engine.adapters.native_onnx] device`. DirectML is unverified on real hardware.
- Tests that need tiers require `carla init` (or a config) first; the native-engine
  tests need `onnxruntime`.

- **Safe mode:** `ToolRegistry.execute(..., approved=False)` goes through
  `registry.gate` (set by `CarlaRuntime._confirmation_gate`); only the owner's
  paths (HUD tool runner, approval queue) pass `approved=True`. Tests that call
  risky tools directly must pass `approved=True`.
- **Windows exe:** built by `.github/workflows/release.yml` from `PsychoCARLA.spec`,
  entry `packaging/carla_app.py` (desktop.py cannot be the frozen entry — relative
  imports). A windowed exe has `sys.stdout/stderr = None` — `desktop.ensure_std_streams()`
  fixes that (uvicorn crashed without it); startup errors go to `<home>/logs/desktop.log`;
  `PsychoCARLA.exe --no-window` serves without a window. The release job smoke-tests
  the exe headless on Windows; the real window has not been checked by a person.
  First launch calls `ensure_config()`.

## Traps previous sessions hit (don’t repeat)

- **Sandbox limits:** ~2 GB RAM, no swap; `/tmp` is a small tmpfs — put
  models under `/var/tmp`. Loading more than one ONNX graph at once gets
  OOM-killed (exit 137). The native engine’s verified path is the manager’s
  pruned single-graph q4/bnb4 download — don’t judge it by the weak q4f16
  graph or by sandbox timings.
- **No `llama-cpp-python`** (source build fails here); ONNX Runtime is the
  verified native path.
- **pytest output quirk:** full-run output through the shell tool can
  truncate; trust the exit code, use `--collect-only` for counts, and run
  per-file to see failures.
- **Channel bridge is `None`** unless at least one channel is configured
  (e.g. `[channels.webchat]`) — e2e homes must enable one.
- **Stale servers** on port 8799 produce misleading e2e results; restart
  before trusting a run.
- Make file edits sequentially and re-read/grep to confirm them — batched
  parallel edits to the same file have silently lost changes before.
- Wake phrase is “hey carla”, but `voice/stt.py` still names the openWakeWord
  *pretrained model file* `hey_jarvis`. That’s the library’s filename, not
  branding — only change it if training a custom wake model.

## Conventions

- Zero Stark/Marvel branding; zero references to the old F.R.I.D.A.Y. name
  in source (weekday usage excepted). Keep it that way.
- Everything user-facing that addresses the user goes through
  `Persona.boss` / `status()["user_name"]` — no hardcoded “Boss/Psycho”.
- New features get pytest coverage (see `tests/test_user_name.py` for the
  pattern) and, if they touch serving paths, an e2e check.
- `PSYCHO.md` (repo root) is the owner’s brief — read it for intent and
  priorities; this file is the technical map. Audit reports in `docs/`
  are the verified history.

## Working agreements

- This is a git repository (GitHub: Psycho-Core/PSYCHO-C.A.R.L.A, default
  branch `Master`). Work on a branch and open a PR.
- Dev tooling (`ruff`, `mypy`, `pytest`) is behind the `dev` extra:
  `pip install -e ".[dev]"`. It is not installed in the Linux sandbox by
  default; `pytest` alone is the reliable gate there.
- In a fresh session, before changing anything: run
  `python -m pytest tests/ -q` (expect exit 0) so you start from a known
  green tree.
- Don’t declare work done without running the suite, and re-read files
  after editing to confirm the change actually landed.
