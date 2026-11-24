"""
Model downloading and management.

This is the machinery behind ``carla model pull`` and the HUD's model
manager. It knows how to fetch a model for each engine:

* **ollama**   — POST to the daemon's ``/api/pull`` (streaming progress).
* **llamacpp** — download a ``.gguf`` file from HuggingFace into the
  local models directory.
* **vllm / sglang** — nothing to do; the server downloads on first serve.
* **openai_compat** — not ours to fetch; point it at a remote endpoint.

Downloads land in ``<home>/models`` — never inside the application
bundle, so they survive upgrades and can be shared between installs.

Layer: Pillar 1 (Intelligence).
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..utils.paths import models_dir
from .model_catalog import NATIVE_MODELS

#: Callback signature for progress: (message, percent_or_None).
ProgressFn = Callable[[str, float | None], None]


def _default_progress(message: str, percent: float | None = None) -> None:
    if percent is None:
        print(message, flush=True)
    else:
        bar_len = 24
        filled = int(bar_len * max(0.0, min(1.0, percent)))
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {bar} {percent * 100:5.1f}%  {message}", end="", flush=True)


@dataclass
class DownloadResult:
    """The outcome of a download attempt."""

    ok: bool
    model_id: str
    engine: str
    path: str = ""
    message: str = ""
    size_gb: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "model_id": self.model_id,
            "engine": self.engine,
            "path": self.path,
            "message": self.message,
            "size_gb": self.size_gb,
        }


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def ollama_host() -> str:
    """Where the Ollama daemon lives."""
    return (
        os.environ.get("OLLAMA_HOST")
        or "http://127.0.0.1:11434"
    ).rstrip("/")


def ollama_pull(
    model_name: str,
    host: str | None = None,
    progress: ProgressFn | None = None,
    timeout: float = 3600.0,
) -> DownloadResult:
    """Pull a model through the Ollama daemon."""
    progress = progress or _default_progress
    host = (host or ollama_host()).rstrip("/")

    if not host.startswith("http"):
        host = f"http://{host}"

    url = f"{host}/api/pull"
    payload = json.dumps({"model": model_name, "stream": True}).encode()

    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue

                if chunk.get("error"):
                    return DownloadResult(
                        False, model_name, "ollama",
                        message=str(chunk["error"]),
                    )

                status = chunk.get("status", "")
                total = chunk.get("total") or 0
                completed = chunk.get("completed") or 0

                if total and completed:
                    pct = completed / total
                    progress(f"{status} ({completed / 1e9:.2f} GB)", pct)
                else:
                    progress(status, None)

        print()  # newline after the progress bar
        return DownloadResult(
            True, model_name, "ollama",
            path=f"{host}#/{model_name}",
            message=f"pulled via {host}",
        )

    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return DownloadResult(
            False, model_name, "ollama",
            message=f"HTTP {exc.code} from {url}. {detail}",
        )
    except urllib.error.URLError as exc:
        return DownloadResult(
            False, model_name, "ollama",
            message=(
                f"cannot reach Ollama at {host} ({exc.reason}). "
                "Is it running? Start it with: ollama serve"
            ),
        )
    except Exception as exc:
        return DownloadResult(False, model_name, "ollama", message=str(exc))


def ollama_list(host: str | None = None) -> list[dict]:
    """List models already present in the Ollama daemon."""
    host = (host or ollama_host()).rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return list(data.get("models", []))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# HuggingFace (GGUF)
# ---------------------------------------------------------------------------

def hf_download(
    repo: str,
    filename: str | None = None,
    progress: ProgressFn | None = None,
    destination: Path | None = None,
) -> DownloadResult:
    """Download a file (or whole snapshot) from HuggingFace.

    Uses the ``huggingface_hub`` library when it is installed — that gives
    resumable downloads, progress bars and revision pinning. Falls back to
    a plain HTTPS GET when it is not.
    """
    progress = progress or _default_progress
    destination = destination or models_dir()

    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        return _hf_download_plain(repo, filename, progress, destination)

    try:
        if filename:
            progress(f"downloading {filename} from {repo}", None)
            path = hf_hub_download(
                repo_id=repo,
                filename=filename,
                local_dir=str(destination),
            )
        else:
            progress(f"downloading snapshot {repo}", None)
            path = snapshot_download(
                repo_id=repo,
                local_dir=str(destination),
            )
        return DownloadResult(
            True, repo, "llamacpp", path=str(path),
            message="downloaded via huggingface_hub",
        )
    except Exception as exc:
        return DownloadResult(
            False, repo, "llamacpp", message=f"download failed: {exc}",
        )


#: Files needed to run an ONNX model: the graph, the tokenizer, the
#: config, and (for some repos) the external data file.
_ONNX_PATTERNS = [
    "*.onnx",
    "*.onnx_data",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "special_tokens_map.json",
    "vocab.json",
    "merges.txt",
    "added_tokens.json",
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "preprocessor_config.json",
]


def native_model_dir(model_id: str) -> Path:
    """Where a native model lives on disk."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model_id)
    return models_dir() / "native" / safe


def hf_download_onnx(
    repo: str,
    filename: str | None = None,
    progress: ProgressFn | None = None,
    model_id: str = "",
) -> DownloadResult:
    """Download an ONNX model and its tokenizer into the local model store.

    Unlike :func:`hf_download`, this fetches the whole set of files the
    in-process engine needs — the graph, the tokenizer and the config —
    into a single directory the adapter can be pointed at directly.
    """
    progress = progress or _default_progress
    target = native_model_dir(model_id or repo.replace("/", "--"))
    target.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        # No hub library — fetch the files over plain HTTPS instead, so
        # the app can still download a model with nothing but the
        # standard library.
        return _onnx_download_plain(
            repo, filename, progress, target, model_id or repo
        )

    # Only the requested graph is fetched, plus the tokenizer files.
    # Downloading every quantisation would pull gigabytes of models the
    # user never asked for.
    patterns = list(_ONNX_PATTERNS)
    if filename:
        patterns.append(filename)
        # Drop the wildcard graphs — the exact file replaces them.
        patterns = [p for p in patterns if not p.endswith(".onnx")]
        patterns.append(filename)
        # An external-data file is needed when the graph is split.
        patterns.append(filename.replace(".onnx", "_data.onnx"))

    try:
        progress(f"downloading {repo} (this can take a while)", None)
        path = snapshot_download(
            repo_id=repo,
            local_dir=str(target),
            allow_patterns=patterns,
        )
        progress(f"downloaded {repo}", 100.0)
    except Exception as exc:
        return DownloadResult(
            False, model_id or repo, "native_onnx",
            message=f"download failed: {exc}",
        )

    graph = _find_onnx_graph(Path(path))
    if graph is None:
        return DownloadResult(
            False, model_id or repo, "native_onnx",
            message=(
                "the download completed but no .onnx graph was found in it — "
                "this repository may not have an ONNX export"
            ),
        )

    _prune_unused_graphs(target, graph)

    size_mb = round(graph.stat().st_size / (1024 * 1024), 1)
    return DownloadResult(
        True, model_id or repo, "native_onnx", path=str(target),
        message=f"ready to run — {graph.name} ({size_mb} MB)",
    )


def _prune_unused_graphs(model_dir: Path, keep: Path) -> None:
    """Delete the ONNX quantisations that are not being used.

    Only one graph is ever needed at a time, and the others are large:
    the full-precision export of the same model is often four times the
    size of the quantised one. Leaving them on disk wastes gigabytes,
    and — because the OS caches them — eats the very memory the running
    model needs.
    """
    keep_name = keep.name
    for candidate in model_dir.rglob("*.onnx"):
        if candidate.name == keep_name:
            continue
        try:
            candidate.unlink()
        except OSError:
            pass


def _onnx_download_plain(
    repo: str,
    filename: str | None,
    progress: ProgressFn,
    target: Path,
    model_id: str,
) -> DownloadResult:
    """Download an ONNX model over plain HTTPS, without huggingface_hub.

    The Hub serves every file at
    ``https://huggingface.co/<repo>/resolve/main/<path>``, so a plain
    GET is enough. This keeps "download a model" working on a fresh
    install with no extra packages.
    """
    names: list[str] = []
    if filename:
        names.append(filename)
    else:
        api = f"https://huggingface.co/api/models/{repo}"
        try:
            with urllib.request.urlopen(api, timeout=30) as resp:
                meta = json.loads(resp.read().decode())
            names = [
                s["rfilename"] for s in meta.get("siblings", [])
                if s["rfilename"].endswith(".onnx")
            ][:1]
        except Exception as exc:
            return DownloadResult(
                False, model_id, "native_onnx",
                message=f"cannot list the repository: {exc}",
            )

    # The tokenizer and config travel with the graph.
    wanted = list(names) + [
        "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json", "vocab.json", "merges.txt",
        "config.json",
    ]

    downloaded: list[str] = []
    for name in wanted:
        url = f"https://huggingface.co/{repo}/resolve/main/{name}"
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        progress(f"downloading {name}", None)
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(out, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            progress(
                                f"downloading {name}",
                                round(done / total * 100, 1),
                            )
        except Exception as exc:
            # A missing tokenizer file is not fatal; a missing graph is.
            if name.endswith(".onnx"):
                return DownloadResult(
                    False, model_id, "native_onnx",
                    message=f"download failed for {name}: {exc}",
                )
            continue
        downloaded.append(name)

    graph = _find_onnx_graph(target)
    if graph is None:
        return DownloadResult(
            False, model_id, "native_onnx",
            message="the download finished but no .onnx graph was found in it",
        )

    _prune_unused_graphs(target, graph)

    size_mb = round(graph.stat().st_size / (1024 * 1024), 1)
    return DownloadResult(
        True, model_id, "native_onnx", path=str(target),
        message=f"ready to run — {graph.name} ({size_mb} MB)",
    )


def _graph_fits_memory(model_dir: str) -> bool:
    """Would the downloaded graph actually fit in the free memory?"""
    graph = _find_onnx_graph(Path(model_dir))
    if graph is None:
        return False
    try:
        free_gb = _free_memory_gb()
    except Exception:
        return True
    # ONNX Runtime's peak runs about 2.6x the graph's own size, and
    # the console needs room on top of that.
    return graph.stat().st_size / 1e9 <= free_gb / 3.4


def _free_memory_gb() -> float:
    """Free RAM right now, in gigabytes."""
    try:
        import psutil

        return psutil.virtual_memory().available / 1e9
    except Exception:
        return 8.0


def _smallest_onnx_name(repo: str) -> str:
    """The name of the smallest ONNX graph a repository offers."""
    import urllib.request

    try:
        api = f"https://huggingface.co/api/models/{repo}"
        with urllib.request.urlopen(api, timeout=30) as resp:
            meta = json.loads(resp.read().decode())
    except Exception:
        return ""

    graphs = [
        s["rfilename"] for s in meta.get("siblings", [])
        if s["rfilename"].endswith(".onnx")
        and "_data" not in s["rfilename"]
    ]
    if not graphs:
        return ""

    # Prefer the well-known small ones, then anything else.
    for name in ("onnx/model_bnb4.onnx", "onnx/model_q4.onnx",
                 "onnx/model_int8.onnx", "onnx/model_uint8.onnx"):
        if name in graphs:
            return name
    return graphs[0]


def _find_onnx_graph(directory: Path) -> Path | None:
    """The best ONNX graph in a downloaded model directory."""
    preferred = [
        "model_q4.onnx", "model_bnb4.onnx", "model_int8.onnx",
        "model_uint8.onnx", "model_quantized.onnx", "model.onnx",
    ]
    for name in preferred:
        for candidate in (directory / "onnx" / name, directory / name):
            # A stub left by an interrupted download is not a model.
            try:
                if candidate.exists() and candidate.stat().st_size > 4096:
                    return candidate
            except OSError:
                continue
    # Any other graph — but not an empty or truncated one. A download
    # that was killed part-way can leave a stub behind, and that must
    # not be mistaken for a usable model.
    found = [
        path for path in directory.rglob("*.onnx")
        if path.stat().st_size > 4096
    ]
    return min(found, key=lambda p: p.stat().st_size) if found else None


def native_models() -> list[dict]:
    """Every native model downloaded onto this machine."""
    root = models_dir() / "native"
    found: list[dict] = []
    if not root.exists():
        return found

    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        graph = _find_onnx_graph(directory)
        size_mb = 0.0
        if graph is not None:
            try:
                size_mb = round(graph.stat().st_size / (1024 * 1024), 1)
            except OSError:
                size_mb = 0.0
        found.append({
            "id": directory.name,
            "name": directory.name,
            "path": str(directory),
            "graph": str(graph) if graph else "",
            "size_mb": size_mb,
            "engine": "native_onnx",
            "installed": graph is not None,
            # An incomplete download is listed so it can be cleaned up,
            # but it is not an installed model.
            "partial": graph is None,
        })
    return found


def delete_native_model(model_id: str) -> DownloadResult:
    """Remove a downloaded native model and everything it owns."""
    target = native_model_dir(model_id)
    if not target.exists():
        return DownloadResult(
            False, model_id, "native_onnx",
            message=f"no native model named '{model_id}'",
        )
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return DownloadResult(
            False, model_id, "native_onnx",
            message=f"could not delete: {exc}",
        )
    return DownloadResult(
        True, model_id, "native_onnx",
        message=f"deleted '{model_id}'",
    )


def _hf_download_plain(
    repo: str,
    filename: str | None,
    progress: ProgressFn,
    destination: Path,
) -> DownloadResult:
    """Minimal HuggingFace download without the hub library."""
    target_dir = destination / repo.replace("/", "--")
    target_dir.mkdir(parents=True, exist_ok=True)

    names = [filename] if filename else []
    if not names:
        # Ask the API what's in the repo.
        api = f"https://huggingface.co/api/models/{repo}"
        try:
            with urllib.request.urlopen(api, timeout=30) as resp:
                meta = json.loads(resp.read().decode())
            names = [
                s["rfilename"] for s in meta.get("siblings", [])
                if s["rfilename"].endswith(".gguf")
            ][:4]
        except Exception as exc:
            return DownloadResult(
                False, repo, "llamacpp",
                message=f"cannot list repo (is huggingface_hub installed?): {exc}",
            )

    if not names:
        return DownloadResult(
            False, repo, "llamacpp",
            message="no downloadable .gguf files found in that repository",
        )

    for name in names:
        url = f"https://huggingface.co/{repo}/resolve/main/{name}"
        out = target_dir / Path(name).name
        progress(f"downloading {Path(name).name}", None)
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(out, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if total:
                            progress(f"{Path(name).name}", done / total)
            print()
        except Exception as exc:
            return DownloadResult(
                False, repo, "llamacpp", message=f"download failed: {exc}",
            )

    return DownloadResult(
        True, repo, "llamacpp", path=str(target_dir),
        message=f"downloaded {len(names)} file(s)",
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def pull_model(
    model_id: str,
    engine: str | None = None,
    progress: ProgressFn | None = None,
) -> DownloadResult:
    """Download a model for the given engine.

    ``model_id`` may be a catalog id (``qwen3:4b``) or any tag your engine
    understands. For engines that fetch on demand, this returns
    immediately with instructions.
    """
    progress = progress or _default_progress
    engine = (engine or "ollama").lower()

    from .model_catalog import find_model_spec

    spec = find_model_spec(model_id)

    if engine == "ollama":
        tag = spec.ollama_tag if spec else model_id
        return ollama_pull(tag, progress=progress)

    if engine in ("llamacpp", "llama_cpp"):
        if spec is None:
            return DownloadResult(
                False, model_id, engine,
                message=(
                    f"'{model_id}' is not in the catalog, so there is no "
                    "download location for it. Try one of: "
                    + ", ".join(
                        s.model_id for s in __import__(
                            "psycho_carla.intelligence.model_catalog",
                            fromlist=["x"],
                        ).BUILTIN_MODELS[:6]
                    )
                ),
            )
        if not spec.hf_repo:
            return DownloadResult(
                False, model_id, engine,
                message=f"no HuggingFace repository is recorded for {model_id}",
            )
        return hf_download(
            spec.hf_repo, spec.gguf_file or None, progress=progress,
        )

    if engine in ("native_onnx", "native", "onnx"):
        # The in-process CPU path: fetch the ONNX export from HuggingFace
        # and the tokenizer files alongside it. No Ollama, no server.
        if spec is None:
            return DownloadResult(
                False, model_id, engine,
                message=(
                    f"'{model_id}' is not in the catalog, so there is no "
                    "download location for it. Try one of: "
                    + ", ".join(s.model_id for s in NATIVE_MODELS[:4])
                ),
            )
        if not spec.onnx_repo:
            return DownloadResult(
                False, model_id, engine,
                message=(
                    f"'{model_id}' has no ONNX export recorded, so it "
                    "cannot run natively. Pick a native: model."
                ),
            )
        result = hf_download_onnx(
            spec.onnx_repo,
            spec.onnx_file or None,
            progress=progress,
            model_id=spec.model_id,
        )
        if not result.ok:
            return result

        # A machine with little RAM cannot run the graph we fetched.
        # Fetch the smallest quantisation as well, so the adapter has
        # something that will actually fit.
        if not _graph_fits_memory(result.path):
            alternative = _smallest_onnx_name(spec.onnx_repo)
            if alternative and alternative != spec.onnx_file:
                progress(
                    f"this machine is tight on memory — also fetching "
                    f"{alternative}",
                    None,
                )
                extra = hf_download_onnx(
                    spec.onnx_repo,
                    alternative,
                    progress=progress,
                    model_id=spec.model_id,
                )
                if extra.ok:
                    result.message += f" (also fetched {alternative})"
        return result

    if engine in ("vllm", "sglang"):
        return DownloadResult(
            True, model_id, engine,
            message=(
                f"{engine} downloads {model_id} automatically the first time "
                "it serves it. Nothing to do now."
            ),
        )

    if engine in ("mlx", "apple_fm", "afm"):
        return DownloadResult(
            False, model_id, engine,
            message=f"{engine} is macOS-only; manual download required.",
        )

    return DownloadResult(
        False, model_id, engine,
        message=(
            f"'{engine}' is a remote or already-installed engine — "
            "C.A.R.L.A. cannot download models for it. "
            "Use --engine ollama or --engine llamacpp."
        ),
    )


# ---------------------------------------------------------------------------
# Local inventory
# ---------------------------------------------------------------------------

def local_models() -> list[dict]:
    """Everything available on this machine, for the GUI."""
    found: list[dict] = []

    for entry in ollama_list():
        name = entry.get("name") or entry.get("model") or ""
        if not name:
            continue
        size = entry.get("size", 0) or 0
        found.append({
            "model_id": name,
            "engine": "ollama",
            "size_gb": round(size / 1e9, 2),
            "path": f"ollama://{name}",
            "details": entry.get("details", {}),
        })

    base = models_dir()
    for path in base.rglob("*.gguf"):
        try:
            size_gb = round(path.stat().st_size / 1e9, 2)
        except OSError:
            size_gb = 0.0
        found.append({
            "model_id": path.stem,
            "engine": "llamacpp",
            "size_gb": size_gb,
            "path": str(path),
            "details": {"file": path.name},
        })

    # Models that run inside C.A.R.L.A. itself. An incomplete
    # download is not an installed model — it is a partial, and the
    # Model Manager offers to clear those out separately.
    for entry in native_models():
        if not entry.get("installed"):
            continue
        found.append({
            "model_id": entry["id"],
            "engine": "native_onnx",
            "size_gb": round(entry["size_mb"] / 1024.0, 2),
            "path": entry["path"],
            "details": {
                "file": entry["graph"],
                "size_mb": entry["size_mb"],
                "native": True,
            },
        })

    return found


def delete_local_model(model_id: str, engine: str = "llamacpp") -> DownloadResult:
    """Remove a downloaded model from disk."""

    if engine in ("native_onnx", "native", "onnx"):
        return delete_native_model(model_id)

    if engine == "ollama":
        return DownloadResult(
            False, model_id, engine,
            message="remove Ollama models with: ollama rm <name>",
        )

    base = models_dir().resolve()
    for path in models_dir().rglob("*.gguf"):
        if path.stem == model_id or path.name == model_id:
            resolved = path.resolve()
            # Never delete outside the models directory.
            if not str(resolved).startswith(str(base)):
                return DownloadResult(
                    False, model_id, engine,
                    message="refusing to delete a file outside the models directory",
                )
            try:
                resolved.unlink()
            except OSError as exc:
                return DownloadResult(
                    False, model_id, engine, message=f"could not delete: {exc}",
                )
            return DownloadResult(
                True, model_id, engine, path=str(resolved), message="deleted",
            )

    return DownloadResult(False, model_id, engine, message="no such local model")


def disk_free_gb(path: Path | None = None) -> float:
    """Free space on the volume that will hold the models."""
    target = path or models_dir()
    try:
        usage = shutil.disk_usage(target)
        return round(usage.free / 1e9, 2)
    except Exception:
        return 0.0

def activate_native_model(model_id: str, tier: str = "fast_tier") -> dict:
    """Point a capability tier at a downloaded native model.

    Writes the tier into ``psycho_carla.toml`` so the console routes to
    the in-process engine. The tier's ``extra.model_path`` is set to the
    folder the model was downloaded into, which is what the adapter
    loads.

    Returns a report describing what happened.
    """
    from ..core.config import save_config

    target = native_model_dir(model_id)
    graph = _find_onnx_graph(target)
    if graph is None:
        return {
            "ok": False,
            "error": (
                f"'{model_id}' is not downloaded. Download it from the "
                "Model Manager first."
            ),
        }

    try:
        from ..core.config import load_config
        config = load_config()
    except Exception as exc:
        return {"ok": False, "error": f"could not read the config: {exc}"}

    from ..core.config import TierConfig

    existing = config.intelligence.tiers.get(tier)
    tier_config = TierConfig(
        provider="native_onnx",
        model=model_id,
        temperature=existing.temperature if existing else 0.7,
        max_tokens=existing.max_tokens if existing else 1024,
        top_p=existing.top_p if existing else 0.9,
        top_k=existing.top_k if existing else 40,
        stop_sequences=list(existing.stop_sequences) if existing else [],
        description=(
            f"Runs inside C.A.R.L.A. on the CPU — {model_id}. "
            "No Ollama, no external server."
        ),
        extra={"model_path": str(target)},
    )
    config.intelligence.tiers[tier] = tier_config

    try:
        save_config(config)
    except Exception as exc:
        return {"ok": False, "error": f"could not save the config: {exc}"}

    return {
        "ok": True,
        "tier": tier,
        "model_id": model_id,
        "model_path": str(target),
        "graph": str(graph),
        "message": (
            f"'{tier}' now runs {model_id} inside C.A.R.L.A. "
            "Restart the console to load it."
        ),
    }



# ---------------------------------------------------------------------------
# Custom models — anything you can name, not just the catalog
# ---------------------------------------------------------------------------

import re as _re

_HF_REPO = _re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
_OLLAMA_TAG = _re.compile(r"^[A-Za-z0-9][\w.\-:/]*$")


def pull_custom(
    engine: str,
    ref: str,
    filename: str = "",
    progress: ProgressFn | None = None,
) -> DownloadResult:
    """Download a model that is not in the catalog.

    ``engine`` / ``ref``:

    * ``ollama`` — an Ollama tag (``name:tag`` or ``hf.co/owner/repo:quant``).
    * ``llamacpp`` — a Hugging Face repo (``owner/repo``) and, optionally,
      the ``.gguf`` file inside it.
    * ``native_onnx`` — a Hugging Face repo with an ONNX export and,
      optionally, the graph file (``onnx/model_q4.onnx``).

    Nothing is run or imported from the download; it is only fetched.
    """
    progress = progress or _default_progress
    engine = (engine or "ollama").strip().lower()
    ref = (ref or "").strip()
    filename = (filename or "").strip()
    if not ref:
        return DownloadResult(False, ref, engine, message="enter a model name first")
    if ".." in filename or filename.startswith(("/", "\\")):
        return DownloadResult(False, ref, engine, message="invalid file name")

    if engine == "ollama":
        if not _OLLAMA_TAG.match(ref):
            return DownloadResult(False, ref, engine, message=f"'{ref}' is not a valid Ollama tag")
        return ollama_pull(ref, progress=progress)

    if not _HF_REPO.match(ref):
        return DownloadResult(
            False, ref, engine,
            message=f"'{ref}' is not a Hugging Face repo — expected owner/name",
        )
    if engine in ("llamacpp", "llama_cpp"):
        return hf_download(ref, filename or None, progress=progress)
    if engine in ("native_onnx", "native", "onnx"):
        model_id = "native:custom-" + ref.replace("/", "--").lower()
        return hf_download_onnx(ref, filename or None, progress=progress, model_id=model_id)
    return DownloadResult(
        False, ref, engine,
        message=f"custom downloads are not supported for '{engine}'",
    )


def set_native_device(device: str) -> dict:
    """Persist the native engine's device (``cpu`` / ``gpu`` / ``auto``)."""
    device = (device or "").strip().lower()
    if device not in ("cpu", "gpu", "auto"):
        return {"ok": False, "error": "device must be cpu, gpu or auto"}
    from ..core.config import load_config, save_config

    config = load_config()
    config.engine.adapters.setdefault("native_onnx", {})["device"] = device
    save_config(config)
    # Models loaded under the old setting must reload.
    from .model_lifecycle import lifecycle

    lifecycle.free_memory()
    return {"ok": True, "device": device,
            "message": f"native engine device set to '{device}' — models reload on next use"}


# ---------------------------------------------------------------------------
# Auto-pick and remote machines
# ---------------------------------------------------------------------------

def best_native_model(free_ram_gb: float | None = None):
    """The largest catalog native model that fits the free RAM, or ``None``.

    Uses the same ~2.6x peak-memory rule as the rest of the manager, with
    headroom for the OS.
    """
    from .model_catalog import NATIVE_MODELS

    if free_ram_gb is None:
        free_ram_gb = _free_memory_gb()
    fitting = [m for m in NATIVE_MODELS if m.ram_gb <= max(free_ram_gb - 1.0, 0)]
    if not fitting:
        return None
    return max(fitting, key=lambda m: m.parameter_count_b)


def add_remote_tier(
    host: str, model: str, tier: str = "deep_tier", kind: str = "ollama",
    port: int = 0, check: bool = True,
) -> dict:
    """Point a tier at a model served by another machine on your network.

    ``kind`` is ``ollama`` (default port 11434) or ``openai`` (any
    OpenAI-compatible server — LM Studio, llama.cpp server, vLLM; default
    port 1234). With ``check`` the server is contacted first, so a typo
    shows up here rather than as a failed chat later.
    """
    host = (host or "").strip().removeprefix("http://").removeprefix("https://").rstrip("/")
    if not _re.match(r"^[A-Za-z0-9.\-]+$", host):
        return {"ok": False, "error": f"'{host}' is not a valid host name or address"}
    if not model.strip():
        return {"ok": False, "error": "a model name is required"}
    kind = (kind or "ollama").lower()
    if kind not in ("ollama", "openai"):
        return {"ok": False, "error": "kind must be 'ollama' or 'openai'"}
    port = port or (11434 if kind == "ollama" else 1234)
    base = f"http://{host}:{port}" + ("" if kind == "ollama" else "/v1")

    if check:
        try:
            import httpx

            url = f"http://{host}:{port}/api/tags" if kind == "ollama" else base + "/models"
            httpx.get(url, timeout=4.0).raise_for_status()
        except Exception as exc:
            return {"ok": False, "error": f"could not reach {base}: {exc}"}

    from ..core.config import TierConfig, load_config, save_config

    config = load_config()
    existing = config.intelligence.tiers.get(tier)
    config.intelligence.tiers[tier] = TierConfig(
        provider="ollama" if kind == "ollama" else "openai_compat",
        model=model.strip(),
        base_url=base,
        temperature=existing.temperature if existing else 0.7,
        max_tokens=existing.max_tokens if existing else 1024,
        description=f"{model.strip()} on {host}",
    )
    save_config(config)
    return {"ok": True, "tier": tier, "base_url": base, "model": model.strip(),
            "message": f"'{tier}' now uses {model.strip()} on {host}"}
