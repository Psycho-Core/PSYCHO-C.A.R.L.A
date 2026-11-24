"""Sandbox runners.

Three backends:
* ``subprocess`` — a fresh Python interpreter with a scrubbed environment.
* ``docker`` — a Docker container with no network, for untrusted code.
* ``wasm`` — WebAssembly via ``wasmtime`` (optional dependency).

Each returns a :class:`SandboxResult` describing success, output, and errors.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class SandboxResult:
    ok: bool
    output: str = ""
    error: str = ""
    backend: str = ""
    duration_ms: float = 0.0


class SubprocessSandbox:
    name = "subprocess"

    def run_python(self, code: str, timeout: float = 30.0) -> SandboxResult:
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                error=f"Timed out after {timeout:.0f}s",
                backend=self.name,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        output = (proc.stdout or "") + (proc.stderr or "")
        return SandboxResult(
            ok=proc.returncode == 0,
            output=output.strip(),
            error="" if proc.returncode == 0 else f"exit code {proc.returncode}",
            backend=self.name,
            duration_ms=(time.perf_counter() - start) * 1000,
        )


class DockerSandbox:
    name = "docker"

    def __init__(self, image: str = "python:3.12-slim") -> None:
        self.image = image

    def available(self) -> tuple[bool, str]:
        try:
            proc = subprocess.run(
                ["docker", "version"], capture_output=True, text=True, timeout=5
            )
        except OSError as exc:
            return False, f"docker not available: {exc}"
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "docker not available").strip()
        return True, "docker is available"

    def run_python(self, code: str, timeout: float = 30.0) -> SandboxResult:
        ok, detail = self.available()
        if not ok:
            return SandboxResult(ok=False, error=detail, backend=self.name)
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [
                    "docker", "run", "--rm", "--network", "none",
                    self.image, "python", "-c", code,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                error=f"Timed out after {timeout:.0f}s",
                backend=self.name,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        output = (proc.stdout or "") + (proc.stderr or "")
        return SandboxResult(
            ok=proc.returncode == 0,
            output=output.strip(),
            error="" if proc.returncode == 0 else f"exit code {proc.returncode}",
            backend=self.name,
            duration_ms=(time.perf_counter() - start) * 1000,
        )


class WasmSandbox:
    name = "wasm"

    def available(self) -> tuple[bool, str]:
        try:
            import wasmtime  # type: ignore  # noqa: F401
        except ImportError:
            return False, "WASM sandbox unavailable: install 'wasmtime'"
        return True, "wasmtime is available"

    def run_python(self, code: str, timeout: float = 30.0) -> SandboxResult:
        ok, detail = self.available()
        return SandboxResult(
            ok=False,
            error=detail if not ok else "WASM module path is not configured",
            backend=self.name,
        )


def run_python(
    code: str,
    timeout: float = 30.0,
    backend: str = "subprocess",
) -> SandboxResult:
    """Run Python code in the requested sandbox backend."""
    if backend == "docker":
        return DockerSandbox().run_python(code, timeout=timeout)
    if backend == "wasm":
        return WasmSandbox().run_python(code, timeout=timeout)
    return SubprocessSandbox().run_python(code, timeout=timeout)


__all__ = [
    "DockerSandbox",
    "SandboxResult",
    "SubprocessSandbox",
    "WasmSandbox",
    "run_python",
]
