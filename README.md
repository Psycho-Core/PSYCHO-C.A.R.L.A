# PSYCHO-C.A.R.L.A.

**C**oding, **A**utomated **R**easoning, & **L**anguage **A**ssistant — a
local-first, model-agnostic personal AI for Windows 11, written from scratch.
An agent framework, a web console (HUD), a desktop shell and CPU-only voice,
in one Python package (`psycho_carla`).

![HUD](screenshots/01-hud-top.png)

## What you get

- **26 agents**, **96 tools**, 28 knowledge connectors, 32 messaging channels, 20 skills
- **Runs with no model** (built-in offline responder), or with any model, **without Ollama**:
  GGUF models (3B–32B) run **inside the app itself** (llama.cpp library; the Windows exe is CPU, a Vulkan GPU route is in docs/MODELS.md), tiny in-process ONNX models,
  **OpenRouter** (free and paid cloud models), or a model on **another PC**
- **Model Manager**: install engine, download, start, stop, delete, *free memory*,
  custom models (any Hugging Face repo / GGUF file), OpenRouter, CPU / GPU switch
- Persistent memory, scheduler, MCP client + server, A2A, workflows, local telemetry
- Voice (optional): Kokoro / Piper TTS, faster-whisper STT, working "hey carla" wake word, voice audition page
- **Safe mode (on by default):** before an agent edits files, runs code or deletes
  anything, the request waits in the **Approvals** panel for your OK
- Backup / restore of config, memory and skills; export of conversations; a LoRA
  fine-tuning script for training on your own chats ([docs/FINETUNE.md](docs/FINETUNE.md))

## Download (Windows 11)

Grab `PsychoCARLA-windows-x64.zip` from the [Releases page](../../releases), unzip,
run `PsychoCARLA\PsychoCARLA.exe`. A short welcome wizard asks what to call you,
installs the engine and picks a model. (Built by GitHub Actions; not code-signed,
so SmartScreen may ask you to confirm. Voice is not in this build.)

## Install from source (Windows 11, Python 3.10+)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .                # core (the llama.cpp engine is downloaded from the HUD)
carla init                      # writes psycho_carla.toml
carla doctor                    # should end with "Ready"
carla serve                     # open http://127.0.0.1:8787
```

Extras: `".[voice]"` voice + wake word · `".[native]"` tiny ONNX models ·
`".[desktop]"` native window · `".[dev]"` tests. See [docs/MODELS.md](docs/MODELS.md).
Or run `.\install.ps1 -Voice`.

## Get a model (pick one)

1. **Easiest:** click **Auto-pick a model** — installs the engine, picks the best
   model that fits your free RAM (uncensored preferred on a tie), downloads and starts it.
2. **Model Manager** (`Models`): **Install engine**, then download from the catalog
   (3B for a small PC … 14B / 32B Qwen2.5-Coder for a big GPU), or **Custom model**.
3. **Cloud:** `carla model cloud qwen/qwen-2.5-coder-32b-instruct --ask-key` (OpenRouter).
4. **Another PC:** `carla model remote 192.168.1.50 MODEL` uses a stronger machine.

Everything is also available from the CLI: `carla model --help`.

## Everyday commands

```text
carla chat | ask "…"          talk to it
carla backup create|restore · carla engine install|status · carla model auto|custom|cloud|free|device|remote|list|local
carla export --format markdown|json|training -o out.md
carla agents | tiers | memory | cron | doctor
```

## Configuration

`psycho_carla.toml` — in `%LOCALAPPDATA%\PsychoCarla` (override with
`PSYCHO_CARLA_HOME`). Set `[persona] user_name` for what she calls you
(blank = "Psycho"). Models are *tiers* (`fast_tier`, `deep_tier`, …) you point
at any provider; nothing is hardcoded.

## Tests

```bash
pip install -e ".[dev,native]" && carla init
python -m pytest tests/ -q            # 400+ tests
```

## Status

Code-complete and automatically tested. **Not yet verified:** a real model
answering end-to-end on your hardware, voice, the 32 channels, the desktop
window, and the Windows `.exe` build (`PsychoCARLA.spec`) — these need your PC.
See [PSYCHO.md](PSYCHO.md) and [docs/FEATURES.md](docs/FEATURES.md).

## More

[Models & GPU](docs/MODELS.md) · [Fine-tuning](docs/FINETUNE.md) · [Features](docs/FEATURES.md) ·
[Architecture](docs/ARCHITECTURE.md) · [MCP](docs/MCP.md) ·
[Voice](docs/VOICE.md) · [Full reference](docs/REFERENCE.md) ·
[Audits](docs/audits/) · [Technical map for developers/AI: CLAUDE.md](CLAUDE.md)

## License

MIT — see [LICENSE](LICENSE). Original code; OpenJarvis was only a feature checklist.
