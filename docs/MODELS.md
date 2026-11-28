# Models, GPU and memory

C.A.R.L.A. never bundles a model. You point a **tier** (`fast_tier`,
`deep_tier`, …) at a provider; the Model Manager (HUD → **Models**, or
`carla model …`) fetches and controls models.

## Engines

| Engine | Where it runs | Notes |
|---|---|---|
| **`native_gguf`** (default) | C.A.R.L.A. downloads llama.cpp and runs it for you, hidden | GGUF models 3B–32B; CPU, Vulkan GPU or HIP |
| `native_onnx` | inside C.A.R.L.A. (CPU, optional DirectML GPU) | tiny models only (≤1.5B) |
| OpenRouter | cloud | free and paid hosted models; `carla model cloud MODEL --ask-key` |
| Remote | another PC on your network | `carla model remote HOST MODEL` |
| Ollama / LM Studio / vLLM | separate app you run | optional; not needed |

### How GGUF models run (no Ollama, no separate program)

`native_gguf` loads the model **inside C.A.R.L.A.'s own process** with
`llama-cpp-python` — nothing else runs. Start loads it into RAM, Stop / Free memory
unloads it. Verified on Windows: model loaded in-process, no child processes,
answered a question, memory released.

- **Windows exe:** the release build bundles the CPU build of the library.
- **From source:** `pip install llama-cpp-python --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`
- **GPU (RX 7800 XT):** the prebuilt wheel is CPU-only. GPU offload needs a build of
  llama-cpp-python with Vulkan (`CMAKE_ARGS="-DGGML_VULKAN=on"`, needs the Vulkan SDK and a
  C++ compiler) — or use the separate llama-server below, whose Vulkan build is prebuilt.
- If llama-cpp-python is missing, C.A.R.L.A. automatically falls back to the separate program:

### The separate llama-server fallback

```powershell
carla engine install --backend vulkan   # any GPU: AMD RX 7800 XT, NVIDIA, Intel
carla engine install --backend cpu      # the OptiPlex (no GPU)
carla model local ; carla model pull gguf:qwen2.5-coder-14b-uncensored --engine native_gguf
```

Or do it all in the HUD: **Models → Install engine → Download → Use this → Start**.
Start launches `llama-server` on `127.0.0.1` (never exposed); Stop / Free
memory shuts it down. GPU layers are chosen from model size (a 32B model on a
16 GB card is split between GPU and RAM — slower but it runs).

### Catalog (GGUF)

| Model | Size | For |
|---|---|---|
| Qwen2.5-Coder 1.5B / 3B | 1.1 / 2.1 GB | OptiPlex (8 GB RAM) — official, not uncensored |
| **Llama 3.2 3B abliterated** | 2.2 GB | OptiPlex — uncensored chat |
| Qwen2.5-Coder 7B | 4.7 GB | tight on 8 GB; fine on the main PC |
| Qwen2.5-Coder 14B (official / **abliterated**) | 9 GB | 16 GB GPU fits fully |
| **Qwen2.5-Coder 32B abliterated** | 19.9 GB | 16 GB GPU + 32 GB RAM, partial offload |

All seven repos and files were confirmed to exist on 2026-11-25 (sizes match;
run the *Engine check* workflow to re-verify), and the 1.5B model was
downloaded and answered a question on a real Windows machine through the
built-in engine. The 14B and 32B models were confirmed to exist but have not been
run by us. If a file ever disappears, use **Custom model** with the new name. "Abliterated" = refusals removed by editing the weights;
check each model's licence and card.

**Ajax (PewDiePie):** a Qwen3.5-9B fine-tune. As of the last reports there
were no public weights. When a Hugging Face GGUF exists, add it with
**Custom model** (repo + `.gguf` file). It is too big for the OptiPlex anyway.

## Controls (HUD Model Manager and CLI)

- **Download / Delete** — catalog models, or **Custom model**: an Ollama tag
  (`name:tag`) or a Hugging Face repo `owner/name` (+ optional file:
  `.gguf` for llama.cpp, `.onnx` for native). `carla model custom REF --engine …`.
- **Start / Stop** (native) — loads / unloads the model. Chat and the manager
  share one loaded copy, so Stop really frees the RAM.
- **Free memory** — unloads every native model and asks a running Ollama to
  evict its models. `carla model free`.
- **Auto-pick** — largest built-in model that fits free RAM
  (needs ~2.6x the model file size). `carla model auto`.
- **Use another PC** — `carla model remote 192.168.1.50 qwen3:8b [--kind openai]`.

## GPU (native engine, Windows)

The native engine uses **DirectML**, which works on AMD Radeon (e.g. RX 7800 XT),
NVIDIA and Intel GPUs.

```powershell
pip uninstall onnxruntime
pip install -e ".[native-gpu]"      # installs onnxruntime-directml
carla model device gpu              # or the "device" menu in the Model Manager
```

`carla model device` shows what is installed. If the GPU session fails to
start, the engine falls back to CPU and says so. Default is `cpu`
(the path tested here). **GPU mode is implemented and unit-tested for provider
selection, but has not been run on real GPU hardware** — report any issue.
For 7B+ models on a GPU, Ollama or LM Studio are the better-trodden route.

## Choosing a model for weak hardware

Rule of thumb for Q4 models: file size ≈ 0.55 GB per billion parameters; keep
2 GB of RAM free for the OS. An 8 GB machine → about 3B parameters at most.

## Uncensored models

The catalog lists only mainstream models. For community "abliterated" /
"uncensored" models, use **Custom model** with the Ollama tag or Hugging Face
repo — and check each model's licence yourself.

## Your conversations as training data

`carla export --format training -o data.jsonl` writes chat-format JSONL from
your stored sessions (user/assistant turns only). Fine-tuning itself is not
built in; this is the dataset step.
