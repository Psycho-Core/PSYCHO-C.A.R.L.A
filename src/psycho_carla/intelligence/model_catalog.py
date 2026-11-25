"""
Built-in model catalog.

C.A.R.L.A. is model-agnostic at the *runtime* level — the router talks to
whatever engine you point a tier at. But to *download* a model you need to
know where it lives, how big it is, and whether it fits your machine.

This module is that knowledge: a curated catalog of well-known models with
their HuggingFace repository, GGUF filename, quantisation, context length,
licence, and a rough RAM requirement. It is the data behind
``carla model list`` / ``carla model pull`` and the HUD's model manager.

The catalog is intentionally small and honest. It lists models that are
known-good on CPU and on the engines C.A.R.L.A. speaks to. It is not a
replacement for Ollama's own library — ``carla model pull --engine ollama``
will pull *any* tag you name, catalog or not.

Layer: Pillar 1 (Intelligence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ModelSpec:
    """One entry in the catalog."""

    model_id: str
    """The identifier you would type — e.g. ``qwen3:4b``."""

    name: str
    """Human-readable name."""

    parameter_count_b: float
    """Parameter count in billions."""

    context_length: int
    """Maximum context window, in tokens."""

    supported_engines: tuple[str, ...]
    """Engine keys that can serve this model."""

    provider: str = ""
    """Who makes it — alibaba, meta, mistralai…"""

    description: str = ""
    """One line, for the GUI."""

    #: Local GGUF / safetensors download info.
    hf_repo: str = ""
    """HuggingFace repository id, e.g. ``Qwen/Qwen3-4B-GGUF``."""

    gguf_file: str = ""
    """Exact GGUF filename within the repo, when known."""

    quantisation: str = ""
    """e.g. ``Q4_K_M``, ``Q8_0``, ``int8``."""

    size_gb: float = 0.0
    """Approximate download size in gigabytes."""

    ram_gb: float = 0.0
    """Approximate resident memory needed to run it."""

    license: str = ""
    """Licence string."""

    vram_gb: float = 0.0
    """GPU memory needed, if any. 0 means CPU-only is fine."""

    is_vision: bool = False
    """Does it accept images?"""

    #: ONNX repository, for the in-process CPU engine.
    onnx_repo: str = ""
    """HuggingFace repository holding the ONNX export, e.g.
    ``onnx-community/Qwen2.5-0.5B-Instruct-ONNX``."""

    onnx_file: str = ""
    """Exact ONNX filename within that repository, e.g.
    ``onnx/model_q4.onnx``."""

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "parameter_count_b": self.parameter_count_b,
            "context_length": self.context_length,
            "supported_engines": list(self.supported_engines),
            "provider": self.provider,
            "description": self.description,
            "hf_repo": self.hf_repo,
            "gguf_file": self.gguf_file,
            "quantisation": self.quantisation,
            "size_gb": self.size_gb,
            "ram_gb": self.ram_gb,
            "license": self.license,
            "vram_gb": self.vram_gb,
            "is_vision": self.is_vision,
            "onnx_repo": self.onnx_repo,
            "onnx_file": self.onnx_file,
            "metadata": dict(self.metadata),
        }

    # -- helpers ------------------------------------------------------------

    @property
    def ollama_tag(self) -> str:
        """The tag Ollama uses (``qwen3:4b`` → ``qwen3:4b``)."""
        return self.model_id

    def fits_ram(self, available_gb: float, headroom: float = 1.5) -> bool:
        """Would this model run in ``available_gb`` of RAM?"""
        if self.ram_gb <= 0:
            return True
        return self.ram_gb * headroom <= available_gb

    def disk_size_gb(self) -> float:
        """Download size, falling back to a params-based estimate."""
        if self.size_gb > 0:
            return self.size_gb
        # ~0.55 GB per billion params at Q4.
        return round(self.parameter_count_b * 0.55, 2)


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

#: GGUF models that C.A.R.L.A. runs itself through its managed llama.cpp
#: engine (no Ollama). Every repo/file below was confirmed to exist (with the
#: size shown) by ``tools/check_catalog.py`` on 2026-11-25; the 1.5B model was
#: also downloaded and run end to end on Windows. Re-run the check any time:
#: Actions -> "Engine check" -> Run workflow.
def _gguf(model_id, name, params, repo, file, size, ram, vram, desc, lic,
          uncensored=False, target="main-pc", ctx=32768):
    return ModelSpec(
        model_id=model_id, name=name, parameter_count_b=params,
        context_length=ctx, supported_engines=("native_gguf", "llamacpp"),
        provider="huggingface", description=desc, hf_repo=repo, gguf_file=file,
        quantisation="Q4_K_M", size_gb=size, ram_gb=ram, vram_gb=vram, license=lic,
        metadata={"uncensored": uncensored, "verified": True, "target": target},
    )


GGUF_MODELS: tuple[ModelSpec, ...] = (
    # --- small: OptiPlex-class PCs (8 GB RAM, no GPU) ---------------------
    _gguf("gguf:qwen2.5-coder-1.5b", "Qwen2.5-Coder 1.5B (GGUF)", 1.5,
          "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF", "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
          1.1, 2.5, 0, "Fastest coder for old PCs. Not uncensored.", "apache-2.0", target="optiplex"),
    _gguf("gguf:llama-3.2-3b-uncensored", "Llama 3.2 3B abliterated (GGUF)", 3.0,
          "mradermacher/Llama-3.2-3B-Instruct-abliterated-GGUF",
          "Llama-3.2-3B-Instruct-abliterated.Q4_K_M.gguf",
          2.24, 3.8, 0, "Uncensored 3B chat model. Best fit for an 8 GB OptiPlex.",
          "llama3.2", uncensored=True, target="optiplex", ctx=131072),
    _gguf("gguf:qwen2.5-coder-3b", "Qwen2.5-Coder 3B (GGUF)", 3.0,
          "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF", "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
          2.1, 3.6, 0, "Small coder for 8 GB PCs. Not uncensored. Research licence.",
          "qwen-research", target="optiplex"),
    _gguf("gguf:qwen2.5-coder-7b", "Qwen2.5-Coder 7B (GGUF)", 7.0,
          "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
          4.7, 6.8, 6, "Strong coder; tight on 8 GB RAM, comfortable on the main PC.",
          "apache-2.0", target="main-pc"),
    # --- main PC: 16 GB GPU + 32 GB RAM -----------------------------------
    _gguf("gguf:qwen2.5-coder-14b", "Qwen2.5-Coder 14B (GGUF)", 14.0,
          "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
          9.0, 11.5, 10, "Official 14B coder. Fits a 16 GB GPU fully.", "apache-2.0"),
    _gguf("gguf:qwen2.5-coder-14b-uncensored", "Qwen2.5-Coder 14B abliterated (GGUF)", 14.0,
          "mradermacher/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF",
          "Qwen2.5-Coder-14B-Instruct-abliterated.Q4_K_M.gguf",
          8.99, 11.5, 10, "Uncensored 14B coder. Fits a 16 GB GPU fully.", "apache-2.0",
          uncensored=True),
    _gguf("gguf:qwen2.5-coder-32b-uncensored", "Qwen2.5-Coder 32B abliterated (GGUF)", 32.0,
          "bartowski/Qwen2.5-Coder-32B-Instruct-abliterated-GGUF",
          "Qwen2.5-Coder-32B-Instruct-abliterated-Q4_K_M.gguf",
          19.9, 24.0, 16, "Uncensored 32B coder. Too big for 16 GB VRAM alone — the engine "
          "offloads part to the GPU and the rest to RAM (slower, ~24 GB RAM).",
          "apache-2.0", uncensored=True),
)


#: Models that run inside C.A.R.L.A. itself, on the CPU, with no
#: Ollama or llama.cpp installed. These are the zero-setup path.
NATIVE_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        model_id="native:smollm2-360m",
        name="SmolLM2 360M (native)",
        parameter_count_b=0.36,
        context_length=8192,
        supported_engines=("native_onnx",),
        provider="huggingface",
        description=(
            "Runs inside C.A.R.L.A. on the CPU. Tiny, instant, no "
            "Ollama needed — the model to try first."
        ),
        onnx_repo="onnx-community/SmolLM2-360M-Instruct-ONNX",
        onnx_file="onnx/model_q4.onnx",
        quantisation="q4",
        size_gb=0.37,
        ram_gb=1.0,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="native:qwen2.5-0.5b",
        name="Qwen2.5 0.5B (native)",
        parameter_count_b=0.5,
        context_length=32768,
        supported_engines=("native_onnx",),
        provider="huggingface",
        description=(
            "Runs inside C.A.R.L.A. on the CPU. Small, quick, and "
            "surprisingly sharp for its size."
        ),
        onnx_repo="onnx-community/Qwen2.5-0.5B-Instruct-ONNX",
        onnx_file="onnx/model_int8.onnx",
        quantisation="int8",
        size_gb=0.51,
        ram_gb=1.4,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="native:qwen2.5-1.5b",
        name="Qwen2.5 1.5B (native)",
        parameter_count_b=1.5,
        context_length=32768,
        supported_engines=("native_onnx",),
        provider="huggingface",
        description=(
            "Runs inside C.A.R.L.A. on the CPU. The sweet spot for "
            "real work on a laptop."
        ),
        onnx_repo="onnx-community/Qwen2.5-1.5B-Instruct-ONNX",
        onnx_file="onnx/model_int8.onnx",
        quantisation="int8",
        size_gb=1.6,
        ram_gb=4.2,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="native:llama-3.2-1b",
        name="Llama 3.2 1B (native)",
        parameter_count_b=1.0,
        context_length=32768,
        supported_engines=("native_onnx",),
        provider="meta",
        description=(
            "Runs inside C.A.R.L.A. on the CPU. Meta's small model, "
            "good at instruction following."
        ),
        onnx_repo="onnx-community/Llama-3.2-1B-Instruct-ONNX",
        onnx_file="onnx/model_int8.onnx",
        quantisation="int8",
        size_gb=1.1,
        ram_gb=2.9,
        license="llama3.2",
    ),
)


BUILTIN_MODELS: tuple[ModelSpec, ...] = (
    # -- Qwen3 (Alibaba) ----------------------------------------------------
    ModelSpec(
        model_id="qwen3:0.6b",
        name="Qwen3 0.6B",
        parameter_count_b=0.6,
        context_length=40960,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="alibaba",
        description="Tiny and fast — the minimum viable local model.",
        hf_repo="Qwen/Qwen3-0.6B-GGUF",
        gguf_file="Qwen3-0.6B-Q8_0.gguf",
        quantisation="Q8_0",
        size_gb=0.64,
        ram_gb=1.2,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="qwen3:1.7b",
        name="Qwen3 1.7B",
        parameter_count_b=1.7,
        context_length=40960,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="alibaba",
        description="Small but genuinely capable — good for low-RAM machines.",
        hf_repo="Qwen/Qwen3-1.7B-GGUF",
        gguf_file="Qwen3-1.7B-Q8_0.gguf",
        quantisation="Q8_0",
        size_gb=1.8,
        ram_gb=2.6,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="qwen3:4b",
        name="Qwen3 4B",
        parameter_count_b=4.0,
        context_length=262144,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="alibaba",
        description="The sweet spot. Good reasoning on 8 GB of RAM.",
        hf_repo="Qwen/Qwen3-4B-GGUF",
        gguf_file="Qwen3-4B-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=2.5,
        ram_gb=6.0,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="qwen3:8b",
        name="Qwen3 8B",
        parameter_count_b=8.2,
        context_length=32768,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="alibaba",
        description="Strong quality — wants 16 GB of RAM.",
        hf_repo="Qwen/Qwen3-8B-GGUF",
        gguf_file="Qwen3-8B-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=5.0,
        ram_gb=11.0,
        license="apache-2.0",
    ),
    ModelSpec(
        model_id="qwen3:14b",
        name="Qwen3 14B",
        parameter_count_b=14.8,
        context_length=40960,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang"),
        provider="alibaba",
        description="Frontier-quality local reasoning — needs 24 GB+.",
        hf_repo="Qwen/Qwen3-14B-GGUF",
        gguf_file="Qwen3-14B-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=9.0,
        ram_gb=19.0,
        license="apache-2.0",
    ),
    # -- Llama (Meta) -------------------------------------------------------
    ModelSpec(
        model_id="llama3.2:1b",
        name="Llama 3.2 1B",
        parameter_count_b=1.2,
        context_length=131072,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="meta",
        description="Lightweight general assistant.",
        hf_repo="bartowski/Llama-3.2-1B-Instruct-GGUF",
        gguf_file="Llama-3.2-1B-Instruct-Q8_0.gguf",
        quantisation="Q8_0",
        size_gb=1.3,
        ram_gb=2.0,
        license="llama3.2",
    ),
    ModelSpec(
        model_id="llama3.1:8b",
        name="Llama 3.1 8B",
        parameter_count_b=8.0,
        context_length=131072,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang"),
        provider="meta",
        description="Long context, very strong general performance.",
        hf_repo="bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        gguf_file="Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=4.9,
        ram_gb=10.5,
        license="llama3.1",
    ),
    # -- Mistral ------------------------------------------------------------
    ModelSpec(
        model_id="mistral:7b",
        name="Mistral 7B",
        parameter_count_b=7.2,
        context_length=32768,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang"),
        provider="mistralai",
        description="Efficient European model, good at code.",
        hf_repo="bartowski/Mistral-7B-Instruct-v0.3-GGUF",
        gguf_file="Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=4.4,
        ram_gb=9.5,
        license="apache-2.0",
    ),
    # -- Google -------------------------------------------------------------
    ModelSpec(
        model_id="gemma2:2b",
        name="Gemma 2 2B",
        parameter_count_b=2.6,
        context_length=8192,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang", "lemonade"),
        provider="google",
        description="Compact and quick, surprisingly solid.",
        hf_repo="bartowski/gemma-2-2b-it-GGUF",
        gguf_file="gemma-2-2b-it-Q8_0.gguf",
        quantisation="Q8_0",
        size_gb=2.7,
        ram_gb=4.0,
        license="gemma",
    ),
    ModelSpec(
        model_id="gemma2:9b",
        name="Gemma 2 9B",
        parameter_count_b=9.2,
        context_length=8192,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang"),
        provider="google",
        description="High quality for its size — a strong 16 GB option.",
        hf_repo="bartowski/gemma-2-9b-it-GGUF",
        gguf_file="gemma-2-9b-it-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=5.8,
        ram_gb=12.0,
        license="gemma",
    ),
    # -- Vision -------------------------------------------------------------
    ModelSpec(
        model_id="llama3.2-vision:11b",
        name="Llama 3.2 Vision 11B",
        parameter_count_b=11.0,
        context_length=131072,
        supported_engines=("ollama", "vllm", "sglang"),
        provider="meta",
        description="Multimodal — reads images. Needs a real GPU.",
        hf_repo="bartowski/Llama-3.2-11B-Vision-Instruct-GGUF",
        gguf_file="Llama-3.2-11B-Vision-Instruct-Q4_K_M.gguf",
        quantisation="Q4_K_M",
        size_gb=7.9,
        ram_gb=14.0,
        vram_gb=10.0,
        license="llama3.2",
        is_vision=True,
    ),
    # -- Coding -------------------------------------------------------------
    ModelSpec(
        model_id="qwen2.5-coder:7b",
        name="Qwen2.5 Coder 7B",
        parameter_count_b=7.6,
        context_length=32768,
        supported_engines=("ollama", "vllm", "llamacpp", "sglang"),
        provider="alibaba",
        description="Code-specialised — the CodeAct agent's best friend.",
        hf_repo="Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        gguf_file="qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        quantisation="Q4_K_M",
        size_gb=4.7,
        ram_gb=10.0,
        license="apache-2.0",
    ),
    # -- Embedding ----------------------------------------------------------
    ModelSpec(
        model_id="nomic-embed:text",
        name="Nomic Embed Text",
        parameter_count_b=0.14,
        context_length=8192,
        supported_engines=("ollama",),
        provider="nomic",
        description="Text embeddings for semantic memory.",
        hf_repo="nomic-ai/nomic-embed-text-v1.5-GGUF",
        gguf_file="nomic-embed-text-v1.5.Q8_0.gguf",
        quantisation="Q8_0",
        size_gb=0.28,
        ram_gb=0.6,
        license="apache-2.0",
    ),
)


# ---------------------------------------------------------------------------
# --------------------------------------------------------------------------- #
# Catalog ported from upstream OpenJarvis BUILTIN_MODELS (77 entries; the
# ones already curated above are not duplicated). Engine names are mapped
# onto this build's adapter ids.
# --------------------------------------------------------------------------- #
UPSTREAM_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        model_id='qwen3:30b', name='Qwen3 30B',
        parameter_count_b=30.0, context_length=262144,
        supported_engines=('ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=18.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3:32b', name='Qwen3 32B',
        parameter_count_b=32.0, context_length=32768,
        supported_engines=('ollama', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=20.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:0.8b', name='Qwen3.5 0.8B',
        parameter_count_b=0.8, context_length=131072,
        supported_engines=('llamacpp', 'mlx', 'ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:2b', name='Qwen3.5 2B',
        parameter_count_b=2.0, context_length=131072,
        supported_engines=('llamacpp', 'mlx', 'ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:9b', name='Qwen3.5 9B',
        parameter_count_b=9.0, context_length=131072,
        supported_engines=('llamacpp', 'mlx', 'ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:27b', name='Qwen3.5 27B',
        parameter_count_b=27.0, context_length=131072,
        supported_engines=('llamacpp', 'mlx', 'ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=16.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:35b', name='Qwen3.5 35B',
        parameter_count_b=35.0, context_length=131072,
        supported_engines=('ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:122b', name='Qwen3.5 122B',
        parameter_count_b=122.0, context_length=131072,
        supported_engines=('ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=70.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:397b', name='Qwen3.5 397B',
        parameter_count_b=397.0, context_length=131072,
        supported_engines=('sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=220.0, is_vision=False,
    ),
    ModelSpec(
        model_id='llama3.3:70b', name='Llama 3.3 70B',
        parameter_count_b=70.0, context_length=131072,
        supported_engines=('ollama', 'vllm'),
        provider='meta', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=40.0, is_vision=False,
    ),
    ModelSpec(
        model_id='llama3.2:3b', name='Llama 3.2 3B',
        parameter_count_b=3.0, context_length=131072,
        supported_engines=('llamacpp', 'ollama', 'vllm'),
        provider='meta', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='deepseek-coder-v2:16b', name='DeepSeek Coder V2 16B',
        parameter_count_b=16.0, context_length=131072,
        supported_engines=('ollama', 'vllm'),
        provider='deepseek', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gpt-oss:120b', name='GPT-OSS 120B',
        parameter_count_b=117.0, context_length=131072,
        supported_engines=('ollama', 'vllm'),
        provider='open-source', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=12.0, is_vision=False,
    ),
    ModelSpec(
        model_id='glm-4.7-flash', name='GLM 4.7 Flash',
        parameter_count_b=30.0, context_length=131072,
        supported_engines=('llamacpp', 'sglang', 'vllm'),
        provider='zhipu', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=8.0, is_vision=False,
    ),
    ModelSpec(
        model_id='trinity-mini', name='Trinity Mini',
        parameter_count_b=26.0, context_length=128000,
        supported_engines=('llamacpp', 'vllm'),
        provider='trinity', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=8.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:4b', name='Qwen3.5 4B',
        parameter_count_b=4.0, context_length=262144,
        supported_engines=('llamacpp', 'mlx', 'ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=3.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:35b-a3b', name='Qwen3.5 35B A3B',
        parameter_count_b=35.0, context_length=262144,
        supported_engines=('ollama', 'sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=8.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:122b-a10b', name='Qwen3.5 122B A10B',
        parameter_count_b=122.0, context_length=262144,
        supported_engines=('sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=20.0, is_vision=False,
    ),
    ModelSpec(
        model_id='qwen3.5:397b-a17b', name='Qwen3.5 397B A17B',
        parameter_count_b=397.0, context_length=262144,
        supported_engines=('sglang', 'vllm'),
        provider='alibaba', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=50.0, is_vision=False,
    ),
    ModelSpec(
        model_id='LiquidAI/LFM2.5-1.2B-Instruct-MLX', name='LFM2.5 1.2B Instruct (MLX)',
        parameter_count_b=1.2, context_length=32768,
        supported_engines=('mlx',),
        provider='liquidai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=1.0, is_vision=False,
    ),
    ModelSpec(
        model_id='LiquidAI/LFM2.5-1.2B-Thinking-MLX', name='LFM2.5 1.2B Thinking (MLX)',
        parameter_count_b=1.2, context_length=32768,
        supported_engines=('mlx',),
        provider='liquidai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=1.0, is_vision=False,
    ),
    ModelSpec(
        model_id='teichai/glm-4.7-flash-opus-distill', name='GLM 4.7 Flash Claude Opus 4.5 Distill',
        parameter_count_b=30.0, context_length=131072,
        supported_engines=('llamacpp', 'vllm'),
        provider='teichai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=8.0, is_vision=False,
    ),
    ModelSpec(
        model_id='teichai/qwen3-14b-gpt5.2-distill', name='Qwen3 14B GPT-5.2 Distill',
        parameter_count_b=14.8, context_length=32768,
        supported_engines=('llamacpp', 'vllm'),
        provider='teichai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=10.0, is_vision=False,
    ),
    ModelSpec(
        model_id='teichai/nemotron-14b-opus-distill', name='Nemotron Cascade 14B Claude Opus Distill',
        parameter_count_b=14.8, context_length=32768,
        supported_engines=('llamacpp', 'vllm'),
        provider='teichai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=10.0, is_vision=False,
    ),
    ModelSpec(
        model_id='granite3.3:8b', name='Granite 3.3 8B',
        parameter_count_b=8.0, context_length=128000,
        supported_engines=('llamacpp', 'ollama', 'vllm'),
        provider='ibm', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='granite4.0-micro', name='Granite 4.0 Micro 3B',
        parameter_count_b=3.0, context_length=128000,
        supported_engines=('llamacpp', 'ollama', 'vllm'),
        provider='ibm', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='granite4.0-h-small', name='Granite 4.0 H Small 32B',
        parameter_count_b=32.0, context_length=128000,
        supported_engines=('ollama', 'vllm'),
        provider='ibm', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gpt-4o', name='GPT-4o',
        parameter_count_b=0.0, context_length=128000,
        supported_engines=('cloud',),
        provider='openai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gpt-4o-mini', name='GPT-4o Mini',
        parameter_count_b=0.0, context_length=128000,
        supported_engines=('cloud',),
        provider='openai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gpt-5-mini', name='GPT-5 Mini',
        parameter_count_b=0.0, context_length=400000,
        supported_engines=('cloud',),
        provider='openai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gpt-5-mini-2025-08-07', name='GPT-5 Mini (2025-08-07)',
        parameter_count_b=0.0, context_length=400000,
        supported_engines=('cloud',),
        provider='openai', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='codex/gpt-4o', name='GPT-4o (Codex)',
        parameter_count_b=0.0, context_length=128000,
        supported_engines=('cloud',),
        provider='openai-codex', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='codex/gpt-4o-mini', name='GPT-4o Mini (Codex)',
        parameter_count_b=0.0, context_length=128000,
        supported_engines=('cloud',),
        provider='openai-codex', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='codex/o3-mini', name='o3-mini (Codex)',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='openai-codex', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='codex/gpt-5-mini', name='GPT-5 Mini (Codex)',
        parameter_count_b=0.0, context_length=400000,
        supported_engines=('cloud',),
        provider='openai-codex', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='codex/gpt-5-mini-2025-08-07', name='GPT-5 Mini 2025-08-07 (Codex)',
        parameter_count_b=0.0, context_length=400000,
        supported_engines=('cloud',),
        provider='openai-codex', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='claude-sonnet-4-20250514', name='Claude Sonnet 4',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='anthropic', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='claude-opus-4-20250514', name='Claude Opus 4',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='anthropic', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='claude-opus-4-6', name='Claude Opus 4.6',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='anthropic', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='claude-sonnet-4-6', name='Claude Sonnet 4.6',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='anthropic', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='claude-haiku-4-5', name='Claude Haiku 4.5',
        parameter_count_b=0.0, context_length=200000,
        supported_engines=('cloud',),
        provider='anthropic', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='MiniMax-M3', name='MiniMax M3',
        parameter_count_b=0.0, context_length=1000000,
        supported_engines=('cloud',),
        provider='minimax', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='MiniMax-M2.7', name='MiniMax M2.7',
        parameter_count_b=0.0, context_length=204800,
        supported_engines=('cloud',),
        provider='minimax', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='MiniMax-M2.7-highspeed', name='MiniMax M2.7 Highspeed',
        parameter_count_b=0.0, context_length=204800,
        supported_engines=('cloud',),
        provider='minimax', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='MiniMax-M2.5', name='MiniMax M2.5',
        parameter_count_b=0.0, context_length=204800,
        supported_engines=('cloud',),
        provider='minimax', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='MiniMax-M2.5-highspeed', name='MiniMax M2.5 Highspeed',
        parameter_count_b=0.0, context_length=204800,
        supported_engines=('cloud',),
        provider='minimax', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/openai/gpt-4.1-mini', name='GPT-4.1 Mini (Atlas Cloud)',
        parameter_count_b=0.0, context_length=1047576,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/openai/gpt-5.4-mini', name='GPT-5.4 Mini (Atlas Cloud)',
        parameter_count_b=0.0, context_length=400000,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/deepseek-ai/deepseek-v3.2', name='DeepSeek V3.2 (Atlas Cloud)',
        parameter_count_b=0.0, context_length=163840,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/Qwen/Qwen3-235B-A22B-Instruct-2507', name='Qwen3 235B A22B Instruct (Atlas Cloud)',
        parameter_count_b=0.0, context_length=131072,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/qwen/qwen3.5-35b-a3b', name='Qwen3.5 35B A3B (Atlas Cloud)',
        parameter_count_b=0.0, context_length=262144,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/zai-org/GLM-4.6', name='GLM-4.6 (Atlas Cloud)',
        parameter_count_b=0.0, context_length=202752,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/moonshotai/kimi-k2.5', name='Kimi K2.5 (Atlas Cloud)',
        parameter_count_b=0.0, context_length=262144,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='atlascloud/minimaxai/minimax-m2.5', name='MiniMax M2.5 (Atlas Cloud)',
        parameter_count_b=0.0, context_length=196608,
        supported_engines=('cloud',),
        provider='atlascloud', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gemini-2.5-pro', name='Gemini 2.5 Pro',
        parameter_count_b=0.0, context_length=1000000,
        supported_engines=('cloud',),
        provider='google', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gemini-2.5-flash', name='Gemini 2.5 Flash',
        parameter_count_b=0.0, context_length=1000000,
        supported_engines=('cloud',),
        provider='google', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gemini-3-pro', name='Gemini 3 Pro',
        parameter_count_b=0.0, context_length=1000000,
        supported_engines=('cloud',),
        provider='google', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='gemini-3-flash', name='Gemini 3 Flash',
        parameter_count_b=0.0, context_length=1000000,
        supported_engines=('cloud',),
        provider='google', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='afm-3', name='Apple Foundation Model 3',
        parameter_count_b=3.0, context_length=4096,
        supported_engines=('afm', 'apple_fm'),
        provider='apple', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='afm-3-core', name='Apple Foundation Model 3 Core',
        parameter_count_b=3.0, context_length=4096,
        supported_engines=('afm', 'apple_fm'),
        provider='apple', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
    ModelSpec(
        model_id='afm-3-core-advanced', name='Apple Foundation Model 3 Core Advanced',
        parameter_count_b=20.0, context_length=4096,
        supported_engines=('afm', 'apple_fm'),
        provider='apple', description='',
        quantisation='', size_gb=0.0, ram_gb=0.0,
        license='', vram_gb=0.0, is_vision=False,
    ),
)


# Lookup
# ---------------------------------------------------------------------------

def _index() -> dict[str, ModelSpec]:

    index = {spec.model_id: spec for spec in BUILTIN_MODELS}
    index.update({spec.model_id: spec for spec in UPSTREAM_MODELS})
    index.update({spec.model_id: spec for spec in NATIVE_MODELS})
    index.update({spec.model_id: spec for spec in GGUF_MODELS})
    return index


def all_specs() -> list[ModelSpec]:
    """Every catalog entry — the hosted engines and the native ones."""
    return (list(BUILTIN_MODELS) + list(UPSTREAM_MODELS) + list(NATIVE_MODELS)
            + list(GGUF_MODELS))


def find_model_spec(model_id: str) -> ModelSpec | None:
    """Look up a model by id. Tries an exact match, then a prefix match."""
    if not model_id:
        return None

    index = _index()
    if model_id in index:
        return index[model_id]

    # ``qwen3:4b`` vs ``qwen3:4b-instruct`` — longest-prefix wins.
    matches = [
        spec for mid, spec in index.items()
        if mid.startswith(model_id) or model_id.startswith(mid)
    ]
    if not matches:
        return None
    return max(matches, key=lambda s: len(s.model_id))


def list_models(
    engine: str | None = None,
    min_ram_gb: float | None = None,
    include_vision: bool = True,
) -> list[ModelSpec]:
    """List catalog entries, filtered by engine and machine capability."""
    specs: Iterable[ModelSpec] = all_specs()
    if engine:
        specs = [s for s in specs if engine in s.supported_engines]
    if min_ram_gb is not None:
        specs = [s for s in specs if s.ram_gb <= min_ram_gb]
    if not include_vision:
        specs = [s for s in specs if not s.is_vision]
    return list(specs)


def recommend_models(
    ram_gb: float,
    vram_gb: float = 0.0,
    limit: int = 5,
) -> list[ModelSpec]:
    """Suggest models that fit this machine, best first.

    A model fits if its RAM requirement fits. When a GPU is present, GPU
    models are preferred, because they leave more headroom for context.
    """
    candidates = [s for s in all_specs() if s.fits_ram(ram_gb)]

    def _score(spec: ModelSpec) -> tuple:
        # GPU-capable models first when we have a GPU.
        gpu_fit = 1 if (vram_gb > 0 and spec.vram_gb > 0
                        and spec.vram_gb <= vram_gb) else 0
        # Then prefer bigger models that still fit (better quality).
        return (gpu_fit, spec.parameter_count_b)

    ranked = sorted(candidates, key=_score, reverse=True)
    return ranked[:limit]


def resolve_model_id_for_engine(model_id: str, engine_id: str) -> str:
    """Translate a catalog id into the identifier an engine expects.

    Ollama and friends use ``name:tag``; llama.cpp wants a filesystem path
    to a ``.gguf``. When we know the GGUF file we hand back its stem.
    """
    spec = find_model_spec(model_id)
    if spec is None:
        return model_id

    if engine_id in ("llamacpp", "llama_cpp", "native_gguf"):
        if spec.gguf_file:
            return spec.gguf_file
        return model_id

    if engine_id in ("native_onnx", "native", "onnx"):
        return spec.onnx_repo or model_id

    return spec.model_id


def register_builtin_models() -> list[ModelSpec]:
    """Return the catalog. Kept for API symmetry with the engine registry."""
    return all_specs()
