"""Run a GGUF model *inside C.A.R.L.A.'s own process* — no server, no second program.

Uses ``llama-cpp-python`` (Python bindings to llama.cpp). The model is loaded
into this process's memory; Stop / Free memory unloads it. This is the
preferred runtime for ``native_gguf`` whenever the library is installed;
otherwise C.A.R.L.A. falls back to the managed ``llama-server`` child process
(``llama_engine.py``).

Install (Windows, CPU, prebuilt wheel):
    pip install llama-cpp-python --prefer-binary --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
GPU needs a build of the library with a GPU backend (Vulkan / CUDA); see docs/MODELS.md.
"""

from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger("psycho_carla.gguf_inprocess")


def available() -> tuple[bool, str]:
    """Is ``llama_cpp`` importable? Returns (ok, detail)."""
    try:
        import llama_cpp  # type: ignore
    except Exception as exc:  # ImportError, or a missing DLL
        return False, f"llama-cpp-python is not usable: {type(exc).__name__}: {exc}"
    return True, str(getattr(llama_cpp, "__version__", "installed"))


def gpu_offload_supported() -> bool:
    """Was the installed library built with a GPU backend?"""
    try:
        import llama_cpp  # type: ignore

        fn = getattr(llama_cpp, "llama_supports_gpu_offload", None)
        return bool(fn()) if fn else False
    except Exception:
        return False


def layers_for(n_gpu_layers: int | str, model_gb: float) -> int:
    """Translate a gpu_layers setting into llama.cpp's ``n_gpu_layers`` (-1 = all)."""
    if not gpu_offload_supported():
        return 0                               # a CPU-only build ignores it anyway
    if isinstance(n_gpu_layers, int) and n_gpu_layers >= 0:
        return n_gpu_layers
    from .llama_engine import auto_gpu_layers

    ngl = auto_gpu_layers(model_gb, _vram_gb(), backend="vulkan")
    return -1 if ngl >= 999 else ngl


def _vram_gb() -> float:
    try:
        from ..core.energy import hardware_summary

        return float(getattr(hardware_summary(), "vram_gb", 0.0) or 0.0)
    except Exception:
        return 0.0


class InProcessGguf:
    """One loaded model. At most one is held in memory at a time."""

    def __init__(self) -> None:
        self._llm: Any = None
        self._lock = threading.RLock()          # guards load/unload
        self._gen_lock = threading.Lock()       # llama.cpp contexts are not re-entrant
        self.model_path = ""
        self.n_gpu_layers = 0
        self.loaded_at = 0.0

    @property
    def running(self) -> bool:
        return self._llm is not None

    def load(self, model_path: str, *, ctx: int = 4096, threads: int = 0,
             gpu_layers: int | str = "auto") -> dict[str, Any]:
        path = Path(model_path)
        if not path.is_file():
            return {"ok": False, "message": f"model file not found: {path}"}
        ok, detail = available()
        if not ok:
            return {"ok": False, "message": detail}
        with self._lock:
            if self._llm is not None and self.model_path == str(path):
                return {"ok": True, "already_running": True, "message": "already loaded"}
            self._unload_locked()
            from llama_cpp import Llama  # type: ignore

            ngl = layers_for(gpu_layers, path.stat().st_size / 1e9)
            try:
                self._llm = Llama(
                    model_path=str(path), n_ctx=int(ctx), n_gpu_layers=ngl,
                    n_threads=int(threads) or None, verbose=False,
                )
            except Exception as exc:
                self._llm = None
                return {"ok": False, "message": f"could not load {path.name}: {type(exc).__name__}: {exc}"}
            self.model_path, self.n_gpu_layers, self.loaded_at = str(path), ngl, time.time()
            return {"ok": True, "gpu_layers": ngl, "message": f"{path.name} loaded in-process"}

    def unload(self) -> bool:
        with self._lock:
            was = self._llm is not None
            self._unload_locked()
            return was

    def _unload_locked(self) -> None:
        llm, self._llm = self._llm, None
        self.model_path, self.n_gpu_layers, self.loaded_at = "", 0, 0.0
        if llm is not None:
            try:
                close = getattr(llm, "close", None)
                if close:
                    close()
            except Exception:
                logger.exception("error closing the model")
            del llm
        gc.collect()

    # -- generation ------------------------------------------------------- #
    @staticmethod
    def _kwargs(sampling: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("temperature", "top_p", "top_k", "max_tokens", "repeat_penalty"):
            if sampling.get(key) is not None:
                out[key] = sampling[key]
        stops = sampling.get("stop_sequences") or sampling.get("stop")
        if stops:
            out["stop"] = list(stops)
        return out

    def chat(self, messages: Sequence[dict[str, str]], **sampling: Any) -> dict[str, Any]:
        """Non-streaming chat completion (OpenAI-shaped dict)."""
        llm = self._llm
        if llm is None:
            raise RuntimeError("no GGUF model is loaded")
        with self._gen_lock:
            return llm.create_chat_completion(messages=list(messages), **self._kwargs(sampling))

    def chat_stream(self, messages: Sequence[dict[str, str]], **sampling: Any) -> Iterator[dict[str, Any]]:
        llm = self._llm
        if llm is None:
            raise RuntimeError("no GGUF model is loaded")
        with self._gen_lock:
            yield from llm.create_chat_completion(
                messages=list(messages), stream=True, **self._kwargs(sampling))

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "model_path": self.model_path,
                "gpu_layers": self.n_gpu_layers}


engine = InProcessGguf()
