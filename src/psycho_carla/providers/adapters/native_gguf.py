"""Native GGUF engine — C.A.R.L.A. runs llama.cpp itself (no Ollama).

The tier's ``extra.model_path`` points at a ``.gguf`` file. On the first
request (or when Start is pressed in the Model Manager) the managed
``llama-server`` child process is launched and this adapter talks to it
over the OpenAI-compatible API on ``127.0.0.1``. See
``intelligence/llama_engine.py``.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..base import GenerationResult, Message, StreamChunk, register_adapter
from .openai_compat import OpenAICompatAdapter


@register_adapter
class NativeGgufAdapter(OpenAICompatAdapter):
    name = "native_gguf"
    supports_tools = False  # llama-server tool calling depends on the model template
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

    def _ensure_server(self) -> None:
        from ...intelligence.llama_engine import server

        if not self.model_path:
            raise RuntimeError("no GGUF model selected for this tier")
        if server.running and server.model_path == self.model_path:
            self.base_url = server.base_url
            return
        result = server.start(
            self.model_path, ctx=self.ctx, threads=self.threads,
            device=self.device, gpu_layers=self.gpu_layers,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("message", "could not start the engine"))
        self.base_url = server.base_url

    def generate(self, messages: Sequence[Message], capability_tier: str = "",
                 stream: bool = False, **kwargs: Any) -> GenerationResult:
        self._ensure_server()
        kwargs.setdefault("model", "local")
        return super().generate(messages, capability_tier, stream=stream, **kwargs)

    def stream(self, messages: Sequence[Message], capability_tier: str = "",
               **kwargs: Any) -> Iterator[StreamChunk]:
        self._ensure_server()
        kwargs.setdefault("model", "local")
        yield from super().stream(messages, capability_tier, **kwargs)

    def health(self) -> tuple[bool, str]:
        from ...intelligence.llama_engine import find_binary
        if not self.model_path:
            return False, "no model selected — open the Model Manager"
        if not Path(self.model_path).is_file():
            return False, f"model file missing: {self.model_path}"
        if find_binary() is None:
            return False, "llama.cpp engine not installed — click 'Install engine'"
        return True, "ready"

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": Path(self.model_path).name, "provider": self.name}] if self.model_path else []
