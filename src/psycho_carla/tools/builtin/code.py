"""
Code execution tool — a sandboxed Python runner and calculator.

The interpreter runs in a subprocess with a timeout so a runaway loop can't
wedge the agent. It is deliberately *not* a security boundary: treat it as
convenience, not isolation.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import ast
import io
import math
import operator
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from typing import Any

from ..registry import ToolRegistry, get_registry

_TIMEOUT = 30.0


def configure_code(timeout: float = 30.0) -> None:
    global _TIMEOUT
    _TIMEOUT = timeout


def _active_sandbox() -> tuple[str, str] | None:
    """(backend, image) when [sandbox] is enabled in the active config."""
    try:
        from ...core.config import get_config

        sandbox = get_config().sandbox
    except Exception:
        return None
    if not getattr(sandbox, "enabled", False):
        return None
    backend = (getattr(sandbox, "runtime", "") or "subprocess").lower()
    if backend not in ("docker", "wasm", "subprocess"):
        backend = "subprocess"
    return backend, getattr(sandbox, "image", "python:3.12-slim")


# --------------------------------------------------------------------------- #
# Calculator
# --------------------------------------------------------------------------- #
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial,
    "gcd": math.gcd, "pow": pow, "hypot": math.hypot,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function calls are allowed.")
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"Unknown function: {node.func.id}")
        args = [_eval_node(a) for a in node.args]
        return fn(*args)
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"Unknown name: {node.id}")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval_node(e) for e in node.elts]
    if isinstance(node, ast.Subscript):
        seq = _eval_node(node.value)
        idx = _eval_node(node.slice)
        return seq[idx]
    if isinstance(node, ast.Attribute):
        raise ValueError("Attribute access is not allowed.")
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left)
        for op, cmp_node in zip(node.ops, node.comparators):
            right = _eval_node(cmp_node)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                raise ValueError("Unsupported comparison.")
            if not ok:
                return False
            left = right
        return True
    raise ValueError(f"Unsupported expression: {type(node).__name__}")


def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression exactly (no eval of arbitrary code).

    Args:
        expression: The arithmetic expression to compute, e.g. "(3+4)*2/7".
    """
    if not expression or not expression.strip():
        return "Error: empty expression."
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree)
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return str(result)


# --------------------------------------------------------------------------- #
# Python execution
# --------------------------------------------------------------------------- #
def code_execute(code: str, timeout: float = 0.0) -> str:
    """
    Execute a Python snippet and return stdout plus stderr.

    Args:
        code: Python source to execute.
        timeout: Override the default timeout in seconds. Zero uses the configured value.
    """
    if not code or not code.strip():
        return "Error: empty code block."
    effective = timeout or _TIMEOUT

    # When [sandbox] is enabled, route execution through the configured
    # isolated backend (docker / wasm / subprocess) instead of the host.
    sandbox = _active_sandbox()
    if sandbox is not None:
        backend, image = sandbox
        from ...sandbox import run_python

        result = run_python(code, timeout=effective, backend=backend)
        if result.ok:
            return result.output or "(no output)"
        return f"Error: {result.error}" if result.error else "Error: sandbox run failed."

    # Try in-process first (fast, no temp files) for simple scripts.
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"Error: SyntaxError: {exc}"

    if _is_safe(tree):
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exec(compile(tree, "<carla_code>", "exec"), {"__name__": "__carla__"})
        except Exception as exc:
            out = buf.getvalue()
            return (out + f"\n[exception] {type(exc).__name__}: {exc}").strip()
        return buf.getvalue().strip() or "(no output)"

    # Fall back to a subprocess for anything that imports or loops.
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(code)
            script_path = fh.name
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=effective,
        )
    except subprocess.TimeoutExpired:
        return f"Error: execution timed out after {effective:.0f}s."
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass

    out = (proc.stdout or "").rstrip()
    if proc.stderr:
        out += ("\n" if out else "") + f"[stderr]\n{proc.stderr.rstrip()}"
    if proc.returncode != 0:
        out += f"\n[exit code: {proc.returncode}]"
    return out or "(no output)"


def _is_safe(tree: ast.AST) -> bool:
    """A script is 'safe' if it has no imports, no loops and no function defs."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.While, ast.For,
                             ast.AsyncFor, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Try, ast.With, ast.AsyncWith)):
            return False
    return True


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()
    reg.register(
        "calculator", calculator,
        "Evaluate a mathematical expression precisely.",
        {"type": "object", "properties": {
            "expression": {"type": "string", "description": "Arithmetic expression."},
        }, "required": ["expression"]},
        namespace="core",
    )
    reg.register(
        "code_execute", code_execute,
        "Execute a Python code snippet and return its output.",
        {"type": "object", "properties": {
            "code": {"type": "string", "description": "Python source code."},
            "timeout": {"type": "number", "description": "Timeout in seconds."},
        }, "required": ["code"]},
        namespace="code", dangerous=True, requires_approval=True,
    )
    return reg


register()
