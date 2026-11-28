# PSYCHO.md — owner brief

Psycho — this is your copy. What you own, where it stands, and what to do next.

## What you own

**PSYCHO-C.A.R.L.A.** — **C**oding, **A**utomated **R**easoning, &
**L**anguage **A**ssistant (`PSYCHO-C.A.R.L.A.`).

Your own original codebase (no OpenJarvis code in it — it was only ever a
feature checklist). A local-first personal AI for Windows 11: 26 agents,
96 tools, 28 connectors, 32 messaging channels, a 78-model catalog with an
in-process CPU engine (no Ollama required), CPU-only voice, a holographic
web console, and a desktop app shell. It answers even with no model
running, via the built-in offline fallback.

## Where it stands (verified 2026-11-13)

- ✅ Code complete — nothing half-built
- ✅ **490 automated tests pass**, **46/46 end-to-end checks pass**
- ✅ Rename to PSYCHO-C.A.R.L.A. fully applied and audited
  (`docs/audits/NAME-CHANGE-AUDIT.md`)
- ✅ “What she calls you” setting audited (`docs/audits/USER-NAME-AUDIT.md`)
- ✅ Model Manager: custom models, DirectML GPU switch, free memory, auto-pick, remote PC (see `docs/MODELS.md`) — GPU path untested on real hardware
- ✅ Windows `.exe` is built by GitHub Actions and attached to a Release; it has not been run by a person yet — please try it (see Releases)
- 💬 Discussed, not built: your own trainable model (fine-tuning add-on)

Full history: `docs/audits/PARITY-AUDIT-7.md` (feature/settings parity),
`CLAUDE.md` (technical handover for any AI/developer taking over).

## Run it (Windows 11)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
carla init
carla doctor
carla serve          # → http://127.0.0.1:8787
```

Or use `.\install.ps1 -Voice` for the scripted install with voice.

**Please test on your machine:** install, `carla init`, `carla doctor`,
`carla serve`, open the HUD, try chat, the Model Manager (download a small
native model), voice if you installed it, and your channels. If anything
errors, send the exact output — it’ll most likely be a Windows
path/permission/packaging detail, and it’s fixable.

## Build the .exe (Windows 11)

```powershell
pip install -e ".[packaging]"
pyinstaller PsychoCARLA.spec     # → dist\PsychoCARLA\PsychoCARLA.exe
```

## Settings you’ll care about

Your config lives in `psycho_carla.toml` (state dir:
`%LOCALAPPDATA%\PsychoCarla`, or wherever `PSYCHO_CARLA_HOME` points).

```toml
[persona]
user_name = ""       # what she calls you — blank means "Psycho"
```

- **Your name:** set `user_name` and she uses it in chat, channels, HUD,
  and speech. It’s currently blank, so she calls you **Psycho**.
- **Models:** you define capability tiers (`fast_tier`, `deep_tier`, …)
  and point each at any provider — the built-in llama.cpp engine (default,
  no Ollama), OpenRouter, another PC, or any OpenAI-compatible endpoint.
  Nothing is hardcoded.
- **Channels:** add e.g. `[channels.webchat]` / Telegram etc. under
  `[channels.*]`; without one configured, chat-in-from-channels is off.
- **Voice:** wake phrase is “hey carla” — detected by listening with the
  speech recogniser (no trained model needed; `pip install -e ".[voice]"`).
  Pick her voice on the audition page (`/voice`) with **Use this voice**.

## If you pick this up cold later

1. Read this file, then `CLAUDE.md`.
2. `pip install -e .` → `carla doctor` → `carla serve`.
3. Run `python -m pytest tests/ -q` to confirm everything is still green
   (expect 490 passing).
4. Any AI assistant can take over from `CLAUDE.md` — it has the full map,
   the traps, and your settled preferences (your name, the locked product
   name, original-code-only).
