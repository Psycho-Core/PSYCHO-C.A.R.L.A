"""Tests for the native ONNX engine.

These cover the paths that are easy to get wrong and hard to notice:

* the plain-HTTPS download fallback (no ``huggingface_hub`` installed),
* the RAM-aware graph selection,
* the chat-template prompt, and what happens without ``jinja2``,
* pruning the quantisations that are not in use.
"""

from __future__ import annotations

import sys
from pathlib import Path



# ----------------------------------------------------------------------
# The prompt builder
# ----------------------------------------------------------------------

def _adapter(model_path="/tmp/does-not-matter"):
    from psycho_carla.providers.adapters.native_onnx import NativeOnnxAdapter

    return NativeOnnxAdapter({"model_path": model_path})


def _messages(*pairs):
    """Build the message list the adapter expects."""
    from psycho_carla.providers.base import Message

    return [Message(role=role, content=content) for role, content in pairs]


class _FakeTokenizer:
    """A tokenizer with a chat template that works."""

    chat_template = "{{ messages }}"

    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        rendered = "\n".join(
            f"<{m['role']}>{m['content']}" for m in messages
        )
        if add_generation_prompt:
            rendered += "<assistant>"
        return rendered


class _BrokenTemplateTokenizer:
    """A tokenizer whose chat template raises — as transformers does
    when ``jinja2`` is missing."""

    chat_template = "{{ messages }}"

    def apply_chat_template(self, *args, **kwargs):
        raise ImportError(
            "apply_chat_template requires jinja2>=3.1.0 to be installed"
        )


class _NoTemplateTokenizer:
    """A tokenizer with no chat template at all."""

    chat_template = None


def test_prompt_uses_the_chat_template():
    adapter = _adapter()
    adapter._tokenizer = _FakeTokenizer()

    prompt = adapter._build_prompt(_messages(("user", "Hello there")))

    assert "<user>Hello there" in prompt
    assert prompt.endswith("<assistant>")


def test_prompt_falls_back_when_jinja2_is_missing():
    """Without jinja2 the prompt must still be sensible.

    transformers raises ImportError from apply_chat_template when
    jinja2 is not installed, so the adapter has to cope rather than
    crash.
    """
    adapter = _adapter()
    adapter._tokenizer = _BrokenTemplateTokenizer()

    prompt = adapter._build_prompt(
        _messages(
            ("system", "You are C.A.R.L.A."),
            ("user", "Hello there"),
        )
    )

    assert "C.A.R.L.A." in prompt
    assert "Hello there" in prompt
    assert prompt.rstrip().endswith("Assistant:")


def test_prompt_fallback_invents_a_system_line():
    """A prompt with no system message still tells the model who it is."""
    adapter = _adapter()
    adapter._tokenizer = _NoTemplateTokenizer()

    prompt = adapter._build_prompt(_messages(("user", "Hi")))

    assert "C.A.R.L.A." in prompt
    assert "Hi" in prompt


def test_prompt_fallback_keeps_the_order():
    adapter = _adapter()
    adapter._tokenizer = _NoTemplateTokenizer()

    prompt = adapter._build_prompt(
        _messages(
            ("system", "Be brief."),
            ("user", "First question"),
            ("assistant", "First answer"),
            ("user", "Second question"),
        )
    )

    assert prompt.index("First question") < prompt.index("First answer")
    assert prompt.index("First answer") < prompt.index("Second question")


# ----------------------------------------------------------------------
# Graph selection
# ----------------------------------------------------------------------

def _make_graphs(directory: Path, names):
    """Create the graph files, recording a fake size for each one.

    The files themselves are tiny — only their reported size matters
    to the selection logic, and writing a real 900 MB file would use
    more memory than the test machine has.
    """
    onnx_dir = directory / "onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, size in names.items():
        path = onnx_dir / name
        path.write_bytes(b"\x00")
        sizes[name] = size
    return onnx_dir


def test_ram_aware_selection_skips_a_graph_that_cannot_fit(
    monkeypatch, tmp_path
):
    """With little free memory the big graph is skipped for a small one."""
    from psycho_carla.providers.adapters import native_onnx

    directory = tmp_path / "model"
    _make_graphs(
        directory,
        {
            "model.onnx": 900 * 1024 * 1024,   # far too big
            "model_q4.onnx": 20 * 1024 * 1024,  # fits
        },
    )

    # Report the fake sizes instead of reading the real ones.
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self.suffix == ".onnx":
            fake = {
                "model.onnx": 900 * 1024 * 1024,
                "model_q4.onnx": 20 * 1024 * 1024,
            }.get(self.name, result.st_size)
            return type(
                "S", (), {"st_size": fake, "st_mtime": result.st_mtime}
            )()
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(
        native_onnx, "_free_ram_gb", lambda: 0.5, raising=False
    )

    adapter = _adapter(str(directory))
    graph, _tokenizer = adapter._resolve_paths()

    assert graph.name != "model.onnx"


def test_pruning_keeps_only_the_graph_in_use(tmp_path):
    """Unused quantisations are deleted — they waste disk and RAM."""
    from psycho_carla.intelligence.model_manager import _prune_unused_graphs

    onnx_dir = tmp_path / "onnx"
    onnx_dir.mkdir()
    keep = onnx_dir / "model_q4.onnx"
    keep.write_bytes(b"x")
    for name in ("model.onnx", "model_fp16.onnx", "model_int8.onnx"):
        (onnx_dir / name).write_bytes(b"x")

    _prune_unused_graphs(tmp_path, keep)

    assert keep.exists()
    assert list(onnx_dir.glob("*.onnx")) == [keep]


# ----------------------------------------------------------------------
# The plain-HTTPS download fallback
# ----------------------------------------------------------------------

def test_plain_downloader_is_used_without_huggingface_hub(monkeypatch, tmp_path):
    """The app can download a model with only the standard library.

    ``huggingface_hub`` is an optional dependency, so the fallback has
    to work when it is absent — otherwise a fresh install cannot
    download anything.
    """
    import builtins

    from psycho_carla.intelligence import model_manager

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "huggingface_hub":
            raise ImportError("no huggingface_hub")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    calls = []

    class _FakeResponse:
        headers = {"Content-Length": "8192"}

        def __init__(self):
            self._body = b"\x00" * 8192

        def read(self, _n):
            body, self._body = self._body, b""
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(
        model_manager.urllib.request, "urlopen", fake_urlopen
    )

    target = tmp_path / "model"
    result = model_manager._onnx_download_plain(
        "org/repo", "onnx/model_q4.onnx", lambda *a, **k: None,
        target, "native:test",
    )

    assert result.ok, result.message
    assert any("huggingface.co/org/repo" in url for url in calls)
    assert (target / "onnx" / "model_q4.onnx").exists()


def test_plain_downloader_reports_a_missing_graph(monkeypatch, tmp_path):
    """A failed graph download must say so, not claim success."""
    from psycho_carla.intelligence import model_manager

    def fake_urlopen(url, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr(
        model_manager.urllib.request, "urlopen", fake_urlopen
    )

    target = tmp_path / "model"
    result = model_manager._onnx_download_plain(
        "org/repo", "onnx/model_q4.onnx", lambda *a, **k: None,
        target, "native:test",
    )

    assert not result.ok
    assert "download failed" in result.message.lower()


# ----------------------------------------------------------------------
# The adapter's registration and capability flags
# ----------------------------------------------------------------------

def test_native_adapter_is_registered():
    from psycho_carla.providers.adapters import get_adapter_class

    assert get_adapter_class("native_onnx") is not None


def test_native_adapter_declares_no_tool_support():
    """The ONNX exports expose no tool-use format — say so honestly."""
    from psycho_carla.providers.adapters.native_onnx import NativeOnnxAdapter

    assert NativeOnnxAdapter.supports_tools is False
    assert NativeOnnxAdapter.supports_streaming is True


def test_native_adapter_reports_a_missing_onnxruntime(monkeypatch):
    """If onnxruntime is absent the adapter says why, it does not crash."""
    from psycho_carla.providers.adapters import native_onnx

    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    available, reason = native_onnx._onnxruntime_available()

    assert available is False
    assert "onnxruntime" in reason
