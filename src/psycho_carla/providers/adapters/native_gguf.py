"""Native GGUF engine — C.A.R.L.A. runs the model itself (no Ollama).

The tier's ``extra.model_path`` points at a ``.gguf`` file. Two runtimes:

* **in-process** (preferred when ``llama-cpp-python`` is installed): the model is
  loaded into C.A.R.L.A.'s own process — no server, no second program.
* **server** (fallback): C.A.R.L.A. downloads llama.cpp's ``llama-server`` and
  runs it as a hidden child process on 127.0.0.1 (``intelligence/llama_engine.py``).

``runtime = "auto" | "inprocess" | "server"`` (tier ``extra`` or
``[engine.adapters.native_gguf]``); ``auto`` picks in-process when available.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..base import GenerationResult, Message, StreamChunk, Usage, register_adapter
from .openai_compat import OpenAICompatAdapter


def choose_runtime(setting: str = "auto") -> str:
    """``inprocess`` or ``server`` for a runtime setting."""
    from ...intelligence import gguf_inprocess

    import os

    setting = (setting or "auto").strip().lower()
    if setting == "auto":
        setting = os.environ.get("PSYCHO_CARLA_GGUF_RUNTIME", "auto").strip().lower() or "auto"
    if setting == "server":
        return "server"
    if setting == "inprocess":
        return "inprocess"
    return "inprocess" if gguf_inprocess.available()[0] else "server"


@register_adapter
class NativeGgufAdapter(OpenAICompatAdapter):
    name = "native_gguf"
    supports_tools = False  # tool calling depends on the model's template
    supports_vision = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        opts = dict(options or {})
        opts.setdefault("base_url", "http://127.0.0.1:0")
        super().__init__(opts)
        self.model_path = str(self.extra.get("model_path", "") or "")
        self.ctx = int(self.extra.get("ctx", 4096) or 4096)
        self.threads = int(self.extra.get("threads", 0) or 0)
        self.device = str(self.extra.get("device", "") or "auto")
        self.gpu_layers: int | str = self.extra.get("gpu_layers", "auto")
        self.runtime_setting = str(self.extra.get("runtime", "") or "auto")

    @property
    def runtime(self) -> str:
        return choose_runtime(self.runtime_setting)

    # -- starting ----------------------------------------------------------- #
    def _ensure_loaded(self) -> str:
        """Load the model with the chosen runtime. Returns the runtime used."""
        if not self.model_path:
            raise RuntimeError("no GGUF model selected for this tier")
        runtime = self.runtime
        if runtime == "inprocess":
            from ...intelligence.gguf_inprocess import engine

            if engine.running and engine.model_path == self.model_path:
                return runtime
            result = engine.load(
                self.model_path, ctx=self.ctx, threads=self.threads,
                gpu_layers=0 if self.device == "cpu" else self.gpu_layers,
            )
        else:
            from ...intelligence.llama_engine import server

            if server.running and server.model_path == self.model_path:
                self.base_url = server.base_url
                return runtime
            result = server.start(
                self.model_path, ctx=self.ctx, threads=self.threads,
                device=self.device, gpu_layers=self.gpu_layers,
            )
            if result.get("ok"):
                self.base_url = server.base_url
        if not result.get("ok"):
            raise RuntimeError(result.get("message", "could not start the engine"))
        return runtime

    # -- generation --------------------------------------------------------- #
    @staticmethod
    def _chat_messages(messages: Sequence[Message]) -> list[dict[str, str]]:
        return [{"role": m.role if m.role in ("system", "user", "assistant") else "user",
                 "content": m.content} for m in messages]

    def generate(self, messages: Sequence[Message], capability_tier: str = "",
                 stream: bool = False, **kwargs: Any) -> GenerationResult:
        runtime = self._ensure_loaded()
        if runtime != "inprocess":
            kwargs.setdefault("model", "local")
            return super().generate(messages, capability_tier, stream=stream, **kwargs)

        from ...intelligence.gguf_inprocess import engine

        t0 = time.perf_counter()
        try:
            out = engine.chat(self._chat_messages(messages), **kwargs)
        except Exception as exc:
            return GenerationResult(
                provider=self.name, tier=capability_tier, model=Path(self.model_path).name,
                error=f"{type(exc).__name__}: {exc}", finish_reason="error",
                latency_ms=(time.perf_counter() - t0) * 1000)
        choice = (out.get("choices") or [{}])[0]
        usage = out.get("usage") or {}
        return GenerationResult(
            text=(choice.get("message") or {}).get("content") or "",
            usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            model=Path(self.model_path).name, provider=self.name, tier=capability_tier,
            finish_reason=choice.get("finish_reason") or "stop",
            latency_ms=(time.perf_counter() - t0) * 1000, raw=out)

    def stream(self, messages: Sequence[Message], capability_tier: str = "",
               **kwargs: Any) -> Iterator[StreamChunk]:
        runtime = self._ensure_loaded()
        if runtime != "inprocess":
            kwargs.setdefault("model", "local")
            yield from super().stream(messages, capability_tier, **kwargs)
            return

        from ...intelligence.gguf_inprocess import engine

        completion = 0
        try:
            for part in engine.chat_stream(self._chat_messages(messages), **kwargs):
                delta = ((part.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
                if delta:
                    completion += 1
                    yield StreamChunk(text=delta)
        except Exception as exc:
            yield StreamChunk(done=True, error=f"{type(exc).__name__}: {exc}")
            return
        yield StreamChunk(done=True, usage=Usage(0, completion))

    # -- status ------------------------------------------------------------- #
    def health(self) -> tuple[bool, str]:
        if not self.model_path:
            return False, "no model selected — open the Model Manager"
        if not Path(self.model_path).is_file():
            return False, f"model file missing: {self.model_path}"
        if self.runtime == "inprocess":
            return True, "ready (in-process)"
        from ...intelligence.llama_engine import find_binary

        if find_binary() is None:
            return False, ("no GGUF runtime — install llama-cpp-python (in-process) or click "
                           "'Install engine' (separate llama-server program)")
        return True, "ready (server)"

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": Path(self.model_path).name, "provider": self.name}] if self.model_path else []
