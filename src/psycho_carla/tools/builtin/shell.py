"""
Shell execution tools.

Windows-first: uses PowerShell when available, falling back to cmd.exe.
Commands are executed with a timeout, and an optional allow-list of command
names can be enforced.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from ..registry import ToolRegistry, get_registry

# Runtime state, configured at startup.
_ALLOW_SHELL = True
_TIMEOUT = 60.0
_ALLOWED_COMMANDS: list[str] = []
_CWD: str | None = None


def configure_shell(
    allow: bool = True,
    timeout: float = 60.0,
    allowed_commands: list[str] | None = None,
    cwd: str | None = None,
) -> None:
    global _ALLOW_SHELL, _TIMEOUT, _ALLOWED_COMMANDS, _CWD
    _ALLOW_SHELL = allow
    _TIMEOUT = timeout
    _ALLOWED_COMMANDS = [c.lower() for c in (allowed_commands or [])]
    _CWD = cwd


def _shell_command() -> list[str]:
    """Pick the shell to run commands with."""
    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        if pwsh:
            return [pwsh, "-NoProfile", "-NonInteractive", "-Command"]
        powershell = shutil.which("powershell")
        if powershell:
            return [powershell, "-NoProfile", "-NonInteractive", "-Command"]
        return ["cmd.exe", "/c"]
    return ["/bin/sh", "-c"]


def _check_allowed(command: str) -> str | None:
    """Return an error message if the command is disallowed."""
    if not _ALLOW_SHELL:
        return "Shell execution is disabled in the configuration."
    if not _ALLOWED_COMMANDS:
        return None
    first = command.strip().split()[0].lower() if command.strip() else ""
    first = first.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if first.endswith(".exe"):
        first = first[:-4]
    if first not in _ALLOWED_COMMANDS:
        return (
            f"Command '{first}' is not in the allowed list "
            f"({', '.join(_ALLOWED_COMMANDS)})."
        )
    return None


def shell_exec(command: str, timeout: float = 0.0) -> str:
    """
    Execute a shell command and return its output.

    Args:
        command: The command line to run.
        timeout: Override the default timeout in seconds. Zero uses the configured value.
    """
    if not command or not command.strip():
        return "Error: empty command."

    denied = _check_allowed(command)
    if denied:
        return f"Error: {denied}"

    cmd = _shell_command() + [command]
    effective_timeout = timeout or _TIMEOUT
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            cwd=_CWD,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {effective_timeout:.0f}s."
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"

    parts: list[str] = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
    if proc.returncode != 0:
        parts.append(f"[exit code: {proc.returncode}]")
    out = "\n".join(parts)
    if not out:
        out = "(command completed with no output)"
    # Cap output so we don't blow the context window.
    if len(out) > 20_000:
        out = out[:20_000] + "\n\n[... output truncated ...]"
    return out


def shell_which(command: str) -> str:
    """
    Locate an executable on PATH.

    Args:
        command: The executable name to find.
    """
    path = shutil.which(command)
    return path or f"'{command}' not found on PATH."


def shell_environment() -> str:
    """List the current environment variables (names and values, truncated)."""
    lines = [f"{k}={v[:200]}" for k, v in sorted(os.environ.items())]
    return "\n".join(lines) if lines else "(environment empty)"


def shell_python(code: str) -> str:
    """
    Run a short Python snippet in a subprocess and return stdout/stderr.

    Args:
        code: Python source to execute.
    """
    if not _ALLOW_SHELL:
        return "Error: shell execution is disabled."
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=_TIMEOUT, cwd=_CWD,
        )
    except subprocess.TimeoutExpired:
        return f"Error: timed out after {_TIMEOUT:.0f}s."
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    out = (proc.stdout or "").rstrip()
    if proc.stderr:
        out += ("\n" if out else "") + f"[stderr]\n{proc.stderr.rstrip()}"
    if proc.returncode != 0:
        out += f"\n[exit code: {proc.returncode}]"
    return out or "(no output)"


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()
    reg.register(
        "shell_exec", shell_exec,
        "Execute a shell command and return stdout/stderr.",
        {"type": "object", "properties": {
            "command": {"type": "string", "description": "Command line to execute."},
            "timeout": {"type": "number", "description": "Timeout in seconds."},
        }, "required": ["command"]},
        namespace="shell", dangerous=True, requires_approval=True,
    )
    reg.register(
        "shell_which", shell_which,
        "Locate an executable on the system PATH.",
        {"type": "object", "properties": {
            "command": {"type": "string", "description": "Executable name."},
        }, "required": ["command"]},
        namespace="shell",
    )
    reg.register(
        "shell_environment", shell_environment,
        "List environment variables.",
        {"type": "object", "properties": {}},
        namespace="shell",
    )
    reg.register(
        "shell_python", shell_python,
        "Execute a Python snippet and return its output.",
        {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python source to run."},
        }, "required": ["code"]},
        namespace="shell", dangerous=True, requires_approval=True,
    )
    return reg


register()
