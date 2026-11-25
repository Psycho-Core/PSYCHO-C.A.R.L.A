"""The model lifecycle service.

This is the piece that makes C.A.R.L.A. behave like a program rather
than a set of commands: it keeps the running model in memory, lets the
user start and stop it, tracks partial downloads so they can be
cleaned up, and reports what is on the machine.

The service is deliberately engine-agnostic. It knows about the native
in-process engine because that is the one it can start and stop
directly; other engines (Ollama, llama.cpp) run as separate processes
that the user manages themselves, so they are reported but not
controlled.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("psycho_carla.model_lifecycle")


@dataclass
class ModelState:
    """What C.A.R.L.A. knows about one model on this machine."""

    model_id: str
    engine: str
    path: str = ""
    size_mb: float = 0.0
    installed: bool = False
    complete: bool = False
    running: bool = False
    partial: bool = False
    loaded_at: float = 0.0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ModelLifecycle:
    """Starts, stops and tracks the models C.A.R.L.A. runs itself.

    One instance lives on the app; the HUD and the desktop menu bar
    both talk to it.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapter: Any = None
        self._adapter_model_id: str = ""
        self._gguf_id: str = ""
        self._states: dict[str, ModelState] = {}

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_models(self) -> list[dict[str, Any]]:
        """Every complete model on this machine, with its state."""
        from psycho_carla.intelligence.model_manager import local_models

        rows: list[dict[str, Any]] = []
        for entry in local_models():
            model_id = entry.get("model_id") or entry.get("id", "")
            if not entry.get("installed"):
                # An incomplete download — it is listed separately, as
                # a partial, not as an installed model.
                continue
            with self._lock:
                state = self._states.get(model_id)
            row = dict(entry)
            row["running"] = bool(state and state.running)
            row["complete"] = True
            row["partial"] = False
            rows.append(row)
        return rows

    def partial_downloads(self) -> list[dict[str, Any]]:
        """Downloads that never finished, so they can be cleared out."""
        root = _native_root()
        if not root.exists():
            return []

        found: list[dict[str, Any]] = []
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            if _has_complete_graph(directory):
                continue  # a complete download
            size_mb = _dir_size_mb(directory)
            found.append({
                "id": directory.name,
                "path": str(directory),
                "size_mb": size_mb,
                "engine": "native_onnx",
                "partial": True,
            })
        return found

    # ------------------------------------------------------------------
    # Starting and stopping
    # ------------------------------------------------------------------

    def start(self, model_id: str, model_path: str = "") -> dict[str, Any]:
        """Load a model into memory and start using it.

        The model stays resident so the next request is instant — that
        is the whole point of running it natively rather than paying the
        load cost on every message.
        """
        from psycho_carla.intelligence.model_manager import (
            find_gguf, native_model_dir,
        )

        gguf = None if model_path else find_gguf(model_id)
        if gguf is not None and not native_model_dir(model_id).exists():
            return self._start_gguf(model_id, gguf)

        with self._lock:
            if self._adapter is not None:
                if self._adapter_model_id == model_id:
                    return {
                        "ok": True,
                        "model_id": model_id,
                        "message": f"'{model_id}' is already running",
                        "already_running": True,
                    }
                # Only one model runs at a time — the RAM budget allows
                # no more than that.
                self._stop_locked()

            path = model_path or str(native_model_dir(model_id))
            if not Path(path).exists():
                return {
                    "ok": False,
                    "model_id": model_id,
                    "message": (
                        f"'{model_id}' is not downloaded yet — download it "
                        "from the Model Manager first"
                    ),
                }

            try:
                from psycho_carla.providers.adapters.native_onnx import (
                    shared_adapter,
                )

                # The same object the router chats through, so Stop and
                # Free memory really release the model chat is using.
                adapter = shared_adapter({"model_path": path})
                adapter._load()
            except Exception as exc:
                logger.exception("Could not start %s", model_id)
                return {
                    "ok": False,
                    "model_id": model_id,
                    "message": f"could not start '{model_id}': {exc}",
                }

            self._adapter = adapter
            self._adapter_model_id = model_id
            self._states[model_id] = ModelState(
                model_id=model_id,
                engine="native_onnx",
                path=path,
                installed=True,
                complete=True,
                running=True,
                loaded_at=time.time(),
            )

        return {
            "ok": True,
            "model_id": model_id,
            "message": f"'{model_id}' is running",
            "already_running": False,
        }

    def _start_gguf(self, model_id: str, path: Any) -> dict[str, Any]:
        from psycho_carla.intelligence.llama_engine import server
        from psycho_carla.providers.adapters.native_onnx import configured_device

        with self._lock:
            if self._adapter is not None:
                self._stop_locked()
            result = server.start(str(path), device=configured_device())
            if result.get("ok"):
                self._gguf_id = model_id
            return {"ok": bool(result.get("ok")), "model_id": model_id,
                    "message": result.get("message", ""),
                    "already_running": bool(result.get("already_running"))}

    def stop(self, model_id: str = "") -> dict[str, Any]:
        """Unload the running model and free the memory."""
        from psycho_carla.intelligence.llama_engine import server

        with self._lock:
            if self._gguf_id and (not model_id or model_id == self._gguf_id):
                name, self._gguf_id = self._gguf_id, ""
                server.stop()
                return {"ok": True, "model_id": name, "message": f"'{name}' stopped",
                        "was_running": True}
            if self._adapter is None:
                return {
                    "ok": True,
                    "model_id": model_id,
                    "message": "nothing is running",
                    "was_running": False,
                }
            if model_id and model_id != self._adapter_model_id:
                return {
                    "ok": False,
                    "model_id": model_id,
                    "message": f"'{model_id}' is not the running model",
                }

            name = self._adapter_model_id
            self._stop_locked()

        return {
            "ok": True,
            "model_id": name,
            "message": f"'{name}' stopped",
            "was_running": True,
        }

    def _stop_locked(self) -> None:
        adapter = self._adapter
        model_id = self._adapter_model_id
        self._adapter = None
        self._adapter_model_id = ""
        if adapter is None:
            return
        try:
            adapter.unload()
        except Exception:
            logger.exception("Error while unloading the model")
        state = self._states.get(model_id)
        if state:
            state.running = False

    def status(self) -> dict[str, Any]:
        """The running model, if any."""
        from psycho_carla.intelligence.llama_engine import server

        with self._lock:
            if self._gguf_id and server.running:
                return {"running": True, "model_id": self._gguf_id,
                        "engine": "native_gguf",
                        "device": f"gpu layers: {server.gpu_layers}"}
            if self._adapter is None:
                return {"running": False, "model_id": ""}
            return {
                "running": True,
                "model_id": self._adapter_model_id,
                "engine": "native_onnx",
                "device": getattr(self._adapter, "active_device", ""),
                "loaded_at": self._states[
                    self._adapter_model_id
                ].loaded_at if self._adapter_model_id in self._states else 0.0,
            }

    def free_memory(self) -> dict[str, Any]:
        """Release every model this process holds, and ask Ollama to do the same.

        Unloads the native engine, tells a running Ollama to evict its
        loaded models (``keep_alive: 0``), and collects garbage. Reports
        how much RAM came back so the button can say something useful.
        """
        import gc

        def _avail() -> float:
            try:
                import psutil

                return psutil.virtual_memory().available / 1e9
            except Exception:
                return 0.0

        before = _avail()
        released: list[str] = []
        from psycho_carla.intelligence.llama_engine import server

        with self._lock:
            if self._gguf_id or server.running:
                released.append(self._gguf_id or "llama.cpp engine")
                self._gguf_id = ""
                server.stop()
            if self._adapter is not None:
                released.append(self._adapter_model_id)
                self._stop_locked()
        try:
            from psycho_carla.providers.adapters.native_onnx import unload_all

            unload_all()
        except Exception:
            logger.exception("Error while unloading native models")
        released += self._evict_ollama()
        gc.collect()
        after = _avail()
        return {
            "ok": True,
            "released": released,
            "freed_gb": round(max(after - before, 0.0), 2),
            "free_gb": round(after, 2),
            "message": (
                f"released {', '.join(released)}" if released
                else "no model was loaded"
            ),
        }

    @staticmethod
    def _evict_ollama() -> list[str]:
        """Ask a running Ollama to unload its models. Quiet if it is not running."""
        try:
            import httpx

            from psycho_carla.intelligence.model_manager import ollama_host

            host = ollama_host()
            ps = httpx.get(f"{host}/api/ps", timeout=2.0).json()
            names = [m.get("name", "") for m in ps.get("models", []) if m.get("name")]
            for name in names:
                httpx.post(
                    f"{host}/api/generate",
                    json={"model": name, "keep_alive": 0},
                    timeout=10.0,
                )
            return [f"ollama:{n}" for n in names]
        except Exception:
            return []

    @property
    def adapter(self) -> Any:
        """The live adapter, so the router can use it directly."""
        with self._lock:
            return self._adapter

    # ------------------------------------------------------------------
    # Cleaning up
    # ------------------------------------------------------------------

    def delete_partial(self, model_id: str = "") -> dict[str, Any]:
        """Remove partial downloads — the ones that never finished."""
        from psycho_carla.intelligence.model_manager import (
            delete_native_model,
        )

        if model_id:
            targets = [model_id]
        else:
            targets = [row["id"] for row in self.partial_downloads()]

        if not targets:
            return {
                "ok": True,
                "message": "there are no partial downloads to clean up",
                "removed": [],
            }

        removed: list[str] = []
        problems: list[str] = []
        for target in targets:
            result = delete_native_model(target)
            if result.ok:
                removed.append(target)
            else:
                problems.append(result.message)

        return {
            "ok": not problems,
            "message": (
                f"cleaned up {len(removed)} partial download"
                f"{'s' if len(removed) != 1 else ''}"
                if removed else "nothing was cleaned up"
            ),
            "removed": removed,
            "problems": problems,
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _native_root() -> Path:
    """Where downloaded native models live."""
    from psycho_carla.utils.paths import models_dir

    return models_dir() / "native"


def _has_complete_graph(directory: Path) -> bool:
    """Does this directory hold a graph that is actually usable?

    A download that was killed part-way leaves either nothing behind or
    a ``.part`` file — neither of which can be run.
    """
    from psycho_carla.intelligence.model_manager import _find_onnx_graph

    return _find_onnx_graph(directory) is not None


def _dir_size_mb(directory: Path) -> float:
    """How much disk a directory uses."""
    total = 0
    try:
        for path in directory.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)


#: One shared instance — the app, the HUD and the menu bar all use it.
lifecycle = ModelLifecycle()
