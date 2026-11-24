# Models, GPU and memory

C.A.R.L.A. never bundles a model. You point a **tier** (`fast_tier`,
`deep_tier`, …) at a provider; the Model Manager (HUD → **Models**, or
`carla model …`) fetches and controls models.

## Engines

| Engine | Where it runs | Notes |
|---|---|---|
| `native_onnx` | inside C.A.R.L.A. (CPU, optional DirectML GPU) | no other software; small models |
| Ollama / llama.cpp / LM Studio / vLLM | separate process | best for 7B+ and GPUs |
| Remote | another PC on your network | `carla model remote HOST MODEL` |

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
