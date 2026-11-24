"""Tests for the model lifecycle service.

The lifecycle is what makes the native engine behave like a program:
it starts a model, keeps it resident, stops it, and cleans up the
downloads that never finished.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def lifecycle():
    from psycho_carla.intelligence.model_lifecycle import ModelLifecycle

    return ModelLifecycle()


# ----------------------------------------------------------------------
# Starting and stopping
# ----------------------------------------------------------------------

def test_starting_a_model_that_is_not_downloaded_fails_cleanly(lifecycle):
    result = lifecycle.start("native:never-downloaded")

    assert not result["ok"]
    assert "not downloaded" in result["message"]
    assert lifecycle.status()["running"] is False


def test_stopping_nothing_is_not_an_error(lifecycle):
    result = lifecycle.stop()

    assert result["ok"]
    assert result["was_running"] is False


def test_starting_a_model_loads_it_and_stopping_frees_it(
    lifecycle, tmp_path, monkeypatch
):
    """The model is loaded into memory, then released again."""
    from psycho_carla.intelligence import model_lifecycle as module

    directory = tmp_path / "native_test"
    directory.mkdir()
    (directory / "model_q4.onnx").write_bytes(b"x" * 8192)

    loaded = {"count": 0, "unloaded": 0}

    class _FakeAdapter:
        def __init__(self, options=None):
            self.options = options or {}

        def _load(self):
            loaded["count"] += 1

        def unload(self):
            loaded["unloaded"] += 1

    monkeypatch.setattr(
        module, "_load_adapter", _FakeAdapter, raising=False
    )

    # Patch the adapter the service builds.
    import psycho_carla.providers.adapters.native_onnx as native_onnx

    monkeypatch.setattr(native_onnx, "NativeOnnxAdapter", _FakeAdapter)

    result = lifecycle.start("native_test", str(directory))
    assert result["ok"], result["message"]
    assert loaded["count"] == 1

    status = lifecycle.status()
    assert status["running"] is True
    assert status["model_id"] == "native_test"

    stopped = lifecycle.stop("native_test")
    assert stopped["ok"]
    assert stopped["was_running"] is True
    assert loaded["unloaded"] == 1
    assert lifecycle.status()["running"] is False


def test_starting_the_same_model_twice_is_harmless(lifecycle, tmp_path,
                                                    monkeypatch):
    directory = tmp_path / "native_test"
    directory.mkdir()
    (directory / "model_q4.onnx").write_bytes(b"x" * 8192)

    import psycho_carla.providers.adapters.native_onnx as native_onnx

    class _FakeAdapter:
        def __init__(self, options=None):
            pass

        def _load(self):
            pass

        def unload(self):
            pass

    monkeypatch.setattr(native_onnx, "NativeOnnxAdapter", _FakeAdapter)

    lifecycle.start("native_test", str(directory))
    again = lifecycle.start("native_test", str(directory))

    assert again["ok"]
    assert again["already_running"] is True


def test_stopping_a_model_that_is_not_the_running_one_is_refused(
    lifecycle, tmp_path, monkeypatch
):
    directory = tmp_path / "native_test"
    directory.mkdir()
    (directory / "model_q4.onnx").write_bytes(b"x" * 8192)

    import psycho_carla.providers.adapters.native_onnx as native_onnx

    class _FakeAdapter:
        def __init__(self, options=None):
            pass

        def _load(self):
            pass

        def unload(self):
            pass

    monkeypatch.setattr(native_onnx, "NativeOnnxAdapter", _FakeAdapter)

    lifecycle.start("native_test", str(directory))
    result = lifecycle.stop("some_other_model")

    assert not result["ok"]
    assert "not the running model" in result["message"]


# ----------------------------------------------------------------------
# Partial downloads
# ----------------------------------------------------------------------

def test_a_partial_download_is_found_and_can_be_cleared(
    lifecycle, tmp_path, monkeypatch
):
    """A download that never finished is offered for clean-up."""
    from psycho_carla.intelligence import model_manager

    monkeypatch.setattr(model_manager, "models_dir", lambda: tmp_path)

    # A half-finished download: a .part file and no usable graph.
    partial = tmp_path / "native" / "native_half_download"
    (partial / "onnx").mkdir(parents=True)
    (partial / "onnx" / "model_q4.onnx.part").write_bytes(b"x" * 5000)

    # A complete download: a real graph.
    complete = tmp_path / "native" / "native_complete"
    (complete / "onnx").mkdir(parents=True)
    (complete / "onnx" / "model_q4.onnx").write_bytes(b"x" * 8192)

    from psycho_carla.intelligence import model_lifecycle as module

    monkeypatch.setattr(
        module, "_native_root", lambda: tmp_path / "native"
    )

    partials = lifecycle.partial_downloads()
    ids = [row["id"] for row in partials]
    assert "native_half_download" in ids
    assert "native_complete" not in ids

    result = lifecycle.delete_partial("native_half_download")
    assert result["ok"]
    assert "native_half_download" in result["removed"]
    assert not (partial / "onnx" / "model_q4.onnx.part").exists()


def test_clearing_when_there_is_nothing_to_clear_is_fine(lifecycle,
                                                          tmp_path,
                                                          monkeypatch):
    from psycho_carla.intelligence import model_lifecycle as module

    monkeypatch.setattr(
        module, "_native_root", lambda: tmp_path / "native"
    )
    (tmp_path / "native").mkdir(parents=True, exist_ok=True)

    result = lifecycle.delete_partial()

    assert result["ok"]
    assert result["removed"] == []


def test_a_truncated_graph_is_not_mistaken_for_a_model(tmp_path):
    """A stub left by an interrupted download must not look usable."""
    from psycho_carla.intelligence.model_manager import _find_onnx_graph

    directory = tmp_path / "native_stub"
    (directory / "onnx").mkdir(parents=True)
    (directory / "onnx" / "model_q4.onnx").write_bytes(b"x")

    assert _find_onnx_graph(directory) is None
