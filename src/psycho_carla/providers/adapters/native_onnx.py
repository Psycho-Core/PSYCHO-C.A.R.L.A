"""
Native in-process CPU inference (ONNX Runtime).

This is the engine that lets C.A.R.L.A. run a model **inside its own
process**, with no Ollama, no llama.cpp server and no other external
daemon to install. The user downloads a model from the Model Manager and
the console runs it directly.

How it works:

* The model is a GGUF-free ONNX graph exported for a decoder-only
  transformer, downloaded from HuggingFace (see
  :mod:`psycho_carla.intelligence.model_manager`).
* ``onnxruntime`` executes it on the CPU Execution Provider.
* ``transformers`` supplies the tokenizer and the chat template, so the
  model sees the same prompt format it was trained on.

The adapter is model-agnostic in the same sense as every other adapter:
the model is named by the tier configuration, never hardcoded here.

Where a GGUF is the more natural artefact (the llama.cpp ecosystem), the
``llamacpp`` adapter still exists for users who already run
``llama-server``. This adapter is the zero-setup path.

Layer: Pillar 2 (Engine).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..base import (
    GenerationResult,
    Message,
    ModelAdapter,
    StreamChunk,
    ToolCall,
    Usage,
    register_adapter,
)

logger = logging.getLogger("psycho_carla.providers.native_onnx")

#: Thread-safety lock — ONNX sessions are not safe to run concurrently
#: on the same cache.
_SESSION_LOCK = threading.Lock()


def _available_ram_gb() -> float:
    """How much RAM this machine has, in gigabytes."""
    try:
        import psutil

        return psutil.virtual_memory().total / 1e9
    except Exception:
        try:
            return (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1e9
        except Exception:
            return 8.0


def _free_ram_gb() -> float:
    """How much RAM is free right now, in gigabytes.

    This is the number that matters when choosing a model: the
    machine may have plenty of RAM but be using most of it already.
    """
    try:
        import psutil

        return psutil.virtual_memory().available / 1e9
    except Exception:
        return _available_ram_gb()


def _default_threads() -> int:
    """A sensible thread count that leaves room for the OS."""
    cores = os.cpu_count() or 2
    return max(1, min(4, cores // 2))


def _optimization_level() -> Any:
    """Choose a graph-optimisation level that fits the machine's RAM.

    ONNX Runtime's full optimisation can multiply a quantised model's
    peak memory several times over. On a machine with little to spare
    that is the difference between loading and being killed.
    """
    import onnxruntime as ort

    ram_gb = _available_ram_gb()
    if ram_gb < 4.0:
        # Very tight — do not let ORT expand the graph at all.
        return ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    if ram_gb < 8.0:
        # Enough for basic folding, but not for aggressive fusion.
        return ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    return ort.GraphOptimizationLevel.ORT_ENABLE_ALL


def _onnxruntime_available() -> tuple[bool, str]:
    """Is onnxruntime importable?"""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "onnxruntime is not installed"
    return True, "ok"


@register_adapter
class NativeOnnxAdapter(ModelAdapter):
    """Runs an ONNX model in-process on the CPU.

    Options
    -------
    model_path:
        Path to the ``.onnx`` graph, or to a directory containing one.
    tokenizer_path:
        Path to the tokenizer files. Defaults to the model's directory.
    threads:
        CPU threads to use. Defaults to a sensible slice of the cores.
    """

    name = "native_onnx"
    supports_tools = False
    supports_vision = False
    supports_streaming = True
    supports_json_mode = False

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.model_path = str(
            self.options.get("model_path", "") or self.extra.get("model_path", "")
        )
        self.tokenizer_path = str(
            self.options.get("tokenizer_path", "") or self.extra.get("tokenizer_path", "")
        )
        self.threads = int(self.options.get("threads", 0) or self.extra.get("threads", 0))
        self._session: Any = None
        self._tokenizer: Any = None
        self._kv_inputs: list[Any] = []
        self._input_names: set[str] = set()
        self._wants_position_ids: bool = True
        self._loaded_model: str = ""
        self._load_error: str = ""

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _resolve_paths(self) -> tuple[Path, Path]:
        """Find the graph and the tokenizer."""
        if not self.model_path:
            raise ValueError("native_onnx needs a model_path")

        raw = Path(self.model_path).expanduser()
        if raw.is_dir():
            graph = self._find_graph(raw)
        else:
            graph = raw

        if self.tokenizer_path:
            tokenizer = Path(self.tokenizer_path).expanduser()
        else:
            tokenizer = self._find_tokenizer_dir(graph)

        if not graph.exists():
            raise FileNotFoundError(f"No ONNX graph at {graph}")
        return graph, tokenizer

    @staticmethod
    def _find_tokenizer_dir(graph: Path) -> Path:
        """Locate the tokenizer files that go with a graph.

        Exports keep the graph in an ``onnx/`` subfolder while the
        tokenizer sits beside it, so the search walks upward from the
        graph until it finds the tokenizer files.
        """
        markers = ("tokenizer.json", "tokenizer.model", "vocab.json")

        # The graph's own directory first.
        for candidate in [graph.parent, graph]:
            if any((candidate / m).exists() for m in markers):
                return candidate

        # Then walk up — onnx/model_x.onnx -> onnx -> the model root.
        for parent in graph.parents:
            if any((parent / m).exists() for m in markers):
                return parent
            if any((parent / "onnx").is_dir() and
                   any((parent / m).exists() for m in markers)
                   for m in markers):
                return parent

        # Nothing found — the graph's own directory is the best guess.
        return graph.parent

    @staticmethod
    def _find_graph(directory: Path) -> Path:
        """Pick the best ONNX graph in a directory.

        Preference order: q4 (smallest, fastest), then bnb4, int8,
        uint8, quantized, then the full-precision graph.

        When the machine has little RAM, a smaller graph is chosen over
        a preferred one — ONNX Runtime's peak memory tracks the graph
        size closely, and a 32 GB model on a 4 GB machine is a crash
        rather than a slow answer.
        """
        preferred = [
            "model_q4.onnx", "model_bnb4.onnx", "model_int8.onnx",
            "model_uint8.onnx", "model_quantized.onnx", "model.onnx",
        ]

        candidates: list[Path] = []
        for name in preferred:
            for candidate in (directory / "onnx" / name, directory / name):
                if candidate.exists() and candidate not in candidates:
                    candidates.append(candidate)

        if not candidates:
            found = sorted(directory.rglob("*.onnx"))
            if not found:
                raise FileNotFoundError(f"No .onnx graph in {directory}")
            candidates = found

        # ONNX Runtime's peak memory runs well above the graph's own
        # size: the weights are held twice over (initialised and
        # arena-allocated) and the activations add more. Measured here,
        # a 366 MB graph peaked at 949 MB — about 2.6x — and a 386 MB
        # graph would not fit at all in the same space. Leave a margin
        # on top of that, because the OS and the console need room too.
        headroom = _free_ram_gb() / 3.4
        fitting = [
            c for c in candidates if c.stat().st_size / 1e9 <= headroom
        ]
        if fitting:
            return fitting[0]

        # Nothing fits comfortably — take the smallest.
        return min(candidates, key=lambda p: p.stat().st_size)

    def _load(self) -> None:
        """Load the session and tokenizer. Safe to call repeatedly."""
        if self._session is not None:
            return

        import onnxruntime as ort
        from transformers import AutoTokenizer

        graph, tokenizer_dir = self._resolve_paths()

        so = ort.SessionOptions()
        threads = self.threads or _default_threads()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1

        # Graph optimisation is a memory trap: ORT_ENABLE_ALL can
        # triple the peak RSS of a quantised model, which is fatal on a
        # small machine. We pick the level that fits the RAM we have.
        so.graph_optimization_level = _optimization_level()

        self._session = ort.InferenceSession(
            str(graph), so, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir))
        self._kv_inputs = [
            i for i in self._session.get_inputs()
            if i.name.startswith("past_key_values")
        ]
        # Not every export wants position_ids, and not every export
        # names its cache the same way — read the graph rather than
        # assuming.
        self._input_names = {i.name for i in self._session.get_inputs()}
        self._wants_position_ids = "position_ids" in self._input_names
        self._loaded_model = str(graph)

    def unload(self) -> None:
        """Free the session and its memory."""
        with _SESSION_LOCK:
            self._session = None
            self._tokenizer = None
            self._kv_inputs = []
            self._input_names = set()
            self._loaded_model = ""

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _build_prompt(self, messages: Sequence[Message]) -> str:
        """Render the conversation with the model's chat template."""
        payload = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("system", "user", "assistant")
        ]
        if not payload:
            payload = [{"role": "user", "content": ""}]

        tok = self._tokenizer
        template = getattr(tok, "chat_template", None)
        if template:
            try:
                return tok.apply_chat_template(
                    payload, tokenize=False, add_generation_prompt=True
                )
            except Exception as exc:
                # transformers raises ImportError when jinja2 is absent.
                logger.debug(
                    "Chat template unavailable (%s) — using the plain "
                    "fallback prompt.", exc,
                )

        # No chat template (or no jinja2) — fall back to a plain prompt.
        # The instruction still tells the model what it is, which keeps
        # the answer coherent even without the model's own format.
        lines: list[str] = []
        system = [m for m in payload if m["role"] == "system"]
        if not system:
            system = [{
                "role": "system",
                "content": "You are C.A.R.L.A., a helpful AI assistant.",
            }]
        for entry in system:
            lines.append(f"System: {entry['content']}")
        for entry in payload:
            if entry["role"] in ("user", "assistant"):
                lines.append(f"{entry['role'].capitalize()}: {entry['content']}")
        lines.append("Assistant:")
        return "\n".join(lines)

    def _base_feed(
        self,
        ids: Any,
        mask: Any,
        position: Any,
        cache: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the input tensors the graph actually declares."""
        feeds: dict[str, Any] = {
            "input_ids": ids,
            "attention_mask": mask,
        }
        if self._wants_position_ids:
            feeds["position_ids"] = position
        feeds.update(cache)
        # Drop anything the graph does not want.
        return {k: v for k, v in feeds.items() if k in self._input_names}

    def _empty_cache(self) -> dict[str, Any]:
        """An empty KV cache matching the graph's declared shapes."""
        feeds: dict[str, Any] = {}
        import numpy as np

        for spec in self._kv_inputs:
            batch, heads, _seq, head_dim = spec.shape
            feeds[spec.name] = np.zeros(
                [int(batch) if isinstance(batch, int) else 1,
                 int(heads) if isinstance(heads, int) else 1,
                 0,
                 int(head_dim) if isinstance(head_dim, int) else 64],
                dtype=np.float32,
            )
        return feeds

    def _cache_feeds(self, present: dict[str, Any], length: int) -> dict[str, Any]:
        """Slice the returned KV cache down to *length* tokens."""
        import numpy as np

        feeds: dict[str, Any] = {}
        for spec in self._kv_inputs:
            _layer, kind = spec.name.split(".")[1], spec.name.split(".")[2]
            source = present.get(f"present.{_layer}.{kind}")
            batch, heads, _seq, head_dim = spec.shape
            batch = int(batch) if isinstance(batch, int) else 1
            heads = int(heads) if isinstance(heads, int) else 1
            head_dim = int(head_dim) if isinstance(head_dim, int) else 64
            if source is None:
                feeds[spec.name] = np.zeros(
                    [batch, heads, 0, head_dim], dtype=np.float32
                )
            else:
                feeds[spec.name] = np.ascontiguousarray(
                    source[:, :, :length, :], dtype=np.float32
                )
        return feeds

    def _sampler(
        self,
        logits: Any,
        temperature: float,
        top_p: float,
        top_k: int,
        rng: Any,
        repetition_penalty: float = 1.0,
        generated: list[int] | None = None,
    ) -> int:
        """Pick the next token id from the logits.

        ``repetition_penalty`` damps tokens that have already appeared —
        small models otherwise fall into loops.
        """
        import numpy as np

        row = np.asarray(logits[0, -1], dtype=np.float32)

        if repetition_penalty and repetition_penalty != 1.0 and generated:
            for token_id in set(generated):
                if 0 <= token_id < row.shape[0]:
                    value = row[token_id]
                    if value > 0:
                        row[token_id] = value / repetition_penalty
                    else:
                        row[token_id] = value * repetition_penalty

        if temperature <= 0:
            return int(np.argmax(row))

        row = row / max(temperature, 1e-5)
        # Softmax.
        row = row - row.max()
        probs = np.exp(row)
        probs = probs / probs.sum()

        # Top-k.
        if top_k and top_k > 0:
            cutoff = np.argpartition(probs, -min(top_k, len(probs)))[-top_k:]
            probs = probs.copy()
            probs[cutoff] = 0.0
            total = probs.sum()
            if total > 0:
                probs = probs / total

        # Top-p (nucleus).
        if top_p and 0 < top_p < 1:
            order = np.argsort(probs)[::-1]
            cumulative = np.cumsum(probs[order])
            cut = int(np.searchsorted(cumulative, top_p)) + 1
            keep = order[:cut]
            mask = np.zeros_like(probs)
            mask[keep] = probs[keep]
            total = mask.sum()
            if total > 0:
                probs = mask / total

        return int(rng.choice(len(probs), p=probs))

    def _run(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        top_k: int,
        stop: Sequence[str],
        seed: int | None,
        repetition_penalty: float = 1.0,
    ) -> tuple[str, int, int, float]:
        """Run the model. Returns (text, prompt_tokens, completion_tokens, seconds)."""
        import numpy as np

        rng = np.random.default_rng(seed)

        with _SESSION_LOCK:
            self._load()
            tok = self._tokenizer
            session = self._session

            prompt = self._build_prompt(messages)
            encoded = tok(prompt, return_tensors="np")
            ids = encoded["input_ids"]
            mask = encoded["attention_mask"]
            prompt_len = int(ids.shape[1])

            position = np.arange(prompt_len, dtype=np.int64)[None, :]
            feeds = self._base_feed(
                ids, mask, position, self._empty_cache()
            )

            outputs = session.run(None, feeds)
            present = {o.name: v for o, v in zip(session.get_outputs(), outputs)}

            generated: list[int] = []
            eos = tok.eos_token_id
            eos_set = {eos} if isinstance(eos, int) else set(eos or [])

            started = time.time()
            current_len = prompt_len

            for _step in range(max(1, int(max_tokens))):
                logits = outputs[0]
                next_id = self._sampler(
                    logits, temperature, top_p, top_k, rng,
                    repetition_penalty, generated,
                )
                if next_id in eos_set:
                    break
                generated.append(next_id)

                text_so_far = tok.decode(generated, skip_special_tokens=True)
                if stop and any(s and s in text_so_far for s in stop):
                    break

                ids = np.array([[next_id]], dtype=np.int64)
                mask = np.ones((1, current_len + 1), dtype=np.int64)
                position = np.array([[current_len]], dtype=np.int64)
                feeds = self._base_feed(
                    ids, mask, position,
                    self._cache_feeds(present, current_len),
                )
                outputs = session.run(None, feeds)
                present = {o.name: v for o, v in zip(session.get_outputs(), outputs)}
                current_len += 1

            elapsed = max(time.time() - started, 1e-6)
            text = tok.decode(generated, skip_special_tokens=True)

        # Trim any stop sequence from the tail.
        if stop:
            for marker in stop:
                if marker and marker in text:
                    text = text.split(marker)[0]

        return (
            text.strip(),
            prompt_len,
            len(generated),
            elapsed,
        )

    # ------------------------------------------------------------------
    # ModelAdapter interface
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult:
        """Generate a completion, entirely in this process."""
        max_tokens = int(kwargs.pop("max_tokens", 512) or 512)
        temperature = float(kwargs.pop("temperature", 0.7) or 0.0)
        top_p = float(kwargs.pop("top_p", 0.95) or 0.0)
        top_k = int(kwargs.pop("top_k", 40) or 0)
        seed = kwargs.pop("seed", None)
        stop = kwargs.pop("stop_sequences", None) or kwargs.pop("stop", []) or []
        repetition_penalty = float(
            kwargs.pop("repetition_penalty", 1.0) or 1.0
        )

        try:
            text, prompt_tokens, completion_tokens, elapsed = self._run(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                stop=stop,
                seed=seed,
                repetition_penalty=repetition_penalty,
            )
        except Exception as exc:
            self._load_error = str(exc)
            return GenerationResult(
                text="",
                model=self._loaded_model or "native_onnx",
                error=str(exc),
            )

        usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        return GenerationResult(
            text=text,
            model=self._loaded_model or "native_onnx",
            provider=self.name,
            tier=capability_tier,
            usage=usage,
            latency_ms=elapsed * 1000.0,
            finish_reason="stop",
        )

    def stream(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        **kwargs: Any,
    ) -> Iterator[StreamChunk]:
        """Stream a completion token by token."""
        import numpy as np

        max_tokens = int(kwargs.pop("max_tokens", 512) or 512)
        temperature = float(kwargs.pop("temperature", 0.7) or 0.0)
        top_p = float(kwargs.pop("top_p", 0.95) or 0.0)
        top_k = int(kwargs.pop("top_k", 40) or 0)
        seed = kwargs.pop("seed", None)
        stop = kwargs.pop("stop_sequences", None) or kwargs.pop("stop", []) or []
        repetition_penalty = float(
            kwargs.pop("repetition_penalty", 1.0) or 1.0
        )

        try:
            with _SESSION_LOCK:
                self._load()
                tok = self._tokenizer
                session = self._session

                prompt = self._build_prompt(messages)
                encoded = tok(prompt, return_tensors="np")
                ids = encoded["input_ids"]
                mask = encoded["attention_mask"]
                prompt_len = int(ids.shape[1])

                position = np.arange(prompt_len, dtype=np.int64)[None, :]
                feeds = self._base_feed(
                    ids, mask, position, self._empty_cache()
                )

                outputs = session.run(None, feeds)
                present = {
                    o.name: v for o, v in zip(session.get_outputs(), outputs)
                }

                rng = np.random.default_rng(seed)
                eos = tok.eos_token_id
                eos_set = {eos} if isinstance(eos, int) else set(eos or [])

                started = time.time()
                current_len = prompt_len
                produced = 0
                buffer = ""
                generated: list[int] = []

                for _step in range(max(1, int(max_tokens))):
                    next_id = self._sampler(
                        outputs[0], temperature, top_p, top_k, rng,
                        repetition_penalty, generated,
                    )
                    if next_id in eos_set:
                        break
                    generated.append(next_id)

                    piece = tok.decode(
                        [next_id], skip_special_tokens=True
                    )
                    produced += 1
                    buffer += piece

                    if stop and any(s and s in buffer for s in stop):
                        break

                    yield StreamChunk(text=piece, done=False)

                    ids = np.array([[next_id]], dtype=np.int64)
                    mask = np.ones((1, current_len + 1), dtype=np.int64)
                    position = np.array([[current_len]], dtype=np.int64)
                    feeds = self._base_feed(
                        ids, mask, position,
                        self._cache_feeds(present, current_len),
                    )
                    outputs = session.run(None, feeds)
                    present = {
                        o.name: v for o, v in zip(session.get_outputs(), outputs)
                    }
                    current_len += 1

                elapsed = max(time.time() - started, 1e-6)
                usage = Usage(
                    prompt_tokens=prompt_len,
                    completion_tokens=produced,
                    total_tokens=prompt_len + produced,
                )
                yield StreamChunk(text="", done=True, usage=usage)

        except Exception as exc:
            self._load_error = str(exc)
            yield StreamChunk(text="", done=True, error=str(exc))

    def list_models(self) -> list[dict[str, Any]]:
        """The models this adapter can see on disk."""
        models: list[dict[str, Any]] = []
        if not self.model_path:
            return models
        root = Path(self.model_path).expanduser()
        if root.is_file():
            root = root.parent
        for graph in root.rglob("*.onnx"):
            try:
                size_mb = round(graph.stat().st_size / (1024 * 1024), 1)
            except OSError:
                size_mb = 0.0
            models.append({
                "id": str(graph),
                "name": graph.stem,
                "size_mb": size_mb,
                "engine": "native_onnx",
            })
        return models

    def health(self) -> tuple[bool, str]:
        """Is the runtime able to run a model?"""
        available, detail = _onnxruntime_available()
        if not available:
            return False, detail
        if not self.model_path:
            return False, "no model_path configured"
        try:
            graph, _tokenizer = self._resolve_paths()
        except Exception as exc:
            return False, str(exc)
        return True, f"ready: {graph.name}"


__all__ = ["NativeOnnxAdapter"]
