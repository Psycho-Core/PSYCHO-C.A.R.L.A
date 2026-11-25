"""
PSYCHO-C.A.R.L.A. — Tool Registry & Execution Harness.

Every tool declares a JSON schema so the model can call it, and a Python
callable so the runtime can execute it. Tools are addressed by name, grouped
into namespaces, and can be enabled/disabled per configuration.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import inspect
import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence


@dataclass
class ToolResult:
    """The outcome of a tool invocation."""

    tool: str
    ok: bool = True
    content: str = ""
    data: Any = None
    error: str = ""
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "ok": self.ok,
            "content": self.content,
            "data": self.data,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
        }

    def as_message(self) -> str:
        """Render for injection back into the conversation."""
        if self.ok:
            body = self.content
            if self.data is not None and not body:
                body = json.dumps(self.data, indent=2, default=str)
            return body if body else "(no output)"
        return f"ERROR in {self.tool}: {self.error}"


@dataclass
class Tool:
    """A registered callable exposed to the model."""

    name: str
    description: str
    func: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    namespace: str = "core"
    dangerous: bool = False
    requires_approval: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "namespace": self.namespace,
            "dangerous": self.dangerous,
            "requires_approval": self.requires_approval,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """Central catalogue of every tool available to agents."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._enabled: set[str] = set()
        self._disabled: set[str] = set()
        self._history: list[dict[str, Any]] = []
        #: Safe-mode hook, set by the runtime: ``gate(name, args)`` returns a
        #: blocking ``ToolResult`` (the call was queued for approval) or ``None``.
        self.gate: Callable[[str, dict[str, Any]], ToolResult | None] | None = None

    # -- registration ----------------------------------------------------- #
    def register(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        parameters: dict[str, Any] | None = None,
        namespace: str = "core",
        dangerous: bool = False,
        requires_approval: bool = False,
    ) -> Tool:
        tool = Tool(
            name=name,
            description=description or (func.__doc__ or "").strip(),
            func=func,
            parameters=parameters or self._infer_schema(func),
            namespace=namespace,
            dangerous=dangerous,
            requires_approval=requires_approval,
        )
        self._tools[name] = tool
        return tool

    def tool(self, *args: Any, **kwargs: Any):
        """Decorator form of `register`."""
        def deco(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register(func.__name__, func, *args, **kwargs)
            return func
        return deco

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    # -- enable / disable -------------------------------------------------- #
    def set_enabled(self, names: Iterable[str]) -> None:
        self._enabled = {n for n in names if n in self._tools}

    def set_disabled(self, names: Iterable[str]) -> None:
        self._disabled = {n for n in names if n in self._tools}

    def is_enabled(self, name: str) -> bool:
        if name in self._disabled:
            return False
        if self._enabled:
            return name in self._enabled
        return name in self._tools

    # -- introspection ----------------------------------------------------- #
    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def enabled(self) -> list[Tool]:
        return [t for t in self._tools.values() if self.is_enabled(t.name)]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def namespaces(self) -> list[str]:
        return sorted({t.namespace for t in self._tools.values()})

    def schemas(self, names: Sequence[str] | None = None) -> list[dict[str, Any]]:
        """OpenAI-style function schemas for the enabled tools."""
        pool = self.enabled() if names is None else [
            self._tools[n] for n in names if n in self._tools
        ]
        return [t.openai_schema() for t in pool]

    def describe(self) -> list[dict[str, Any]]:
        return [t.to_dict() for t in self.all()]

    # -- execution ---------------------------------------------------------- #
    def execute(self, name: str, arguments: dict[str, Any] | None = None, *,
                approved: bool = False) -> ToolResult:
        """Execute a tool by name, capturing errors as structured results.

        ``approved=True`` is for calls a person has already authorised (the
        HUD's own tool runner and the approval queue); everything else goes
        through the safe-mode gate first.
        """
        args = arguments or {}
        tool = self._tools.get(name)
        if tool is None:
            res = ToolResult(tool=name, ok=False, error=f"Unknown tool '{name}'")
            self._record(res)
            return res
        if not self.is_enabled(name):
            res = ToolResult(tool=name, ok=False, error=f"Tool '{name}' is disabled")
            self._record(res)
            return res

        if not approved and self.gate is not None:
            blocked = self.gate(name, dict(args))
            if blocked is not None:
                self._record(blocked)
                return blocked

        t0 = time.perf_counter()
        try:
            sig = inspect.signature(tool.func)
            accepted = {k: v for k, v in args.items() if k in sig.parameters}
            output = tool.func(**accepted)
            duration = (time.perf_counter() - t0) * 1000

            if isinstance(output, ToolResult):
                output.duration_ms = duration
                res = output
            elif isinstance(output, tuple) and len(output) == 2:
                content, data = output
                res = ToolResult(tool=name, ok=True, content=str(content), data=data)
            else:
                res = ToolResult(tool=name, ok=True, content=str(output))
            res.duration_ms = duration
        except TypeError as exc:
            res = ToolResult(
                tool=name, ok=False,
                error=f"Bad arguments for '{name}': {exc}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            res = ToolResult(
                tool=name, ok=False,
                error=f"{type(exc).__name__}: {exc}",
                content=traceback.format_exc(limit=3),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        self._record(res)
        return res

    def execute_many(
        self, calls: Sequence[tuple[str, dict[str, Any]]]
    ) -> list[ToolResult]:
        return [self.execute(name, args) for name, args in calls]

    def _record(self, res: ToolResult) -> None:
        self._history.append(res.to_dict())
        if len(self._history) > 500:
            del self._history[:200]

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._history[-limit:]

    # -- schema inference --------------------------------------------------- #
    @staticmethod
    def _infer_schema(func: Callable[..., Any]) -> dict[str, Any]:
        """Build a JSON schema from the function signature and docstring."""
        props: dict[str, Any] = {}
        required: list[str] = []
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            return {"type": "object", "properties": {}}

        doc = inspect.getdoc(func) or ""
        doc_types = _parse_doc_types(doc)

        for pname, param in sig.parameters.items():
            if pname in ("self", "cls", "ctx", "context"):
                continue
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            ptype = _python_type_to_json(param.annotation)
            desc = doc_types.get(pname, "")
            props[pname] = {"type": ptype, "description": desc}
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema


# --------------------------------------------------------------------------- #
# Docstring helpers
# --------------------------------------------------------------------------- #
def _parse_doc_types(doc: str) -> dict[str, str]:
    """Parse an `Args:` block from a Google-style docstring."""
    out: dict[str, str] = {}
    if not doc:
        return out
    in_args = False
    for raw_line in doc.splitlines():
        line = raw_line.strip()
        if line.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue
        if in_args and line.lower() in ("returns:", "raises:", "yields:", "examples:", ""):
            if line == "":
                continue
            in_args = False
            continue
        if in_args and ":" in line:
            name, _, desc = line.partition(":")
            key = name.split("(")[0].strip()
            if key:
                out[key] = desc.strip()
    return out


def _python_type_to_json(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "string"
    text = str(annotation).lower()
    if "bool" in text:
        return "boolean"
    if "int" in text and "float" not in text:
        return "integer"
    if "float" in text:
        return "number"
    if "list" in text or "sequence" in text:
        return "array"
    if "dict" in text:
        return "object"
    return "string"


# --------------------------------------------------------------------------- #
# Global registry
# --------------------------------------------------------------------------- #
_REGISTRY = ToolRegistry()


def get_registry() -> ToolRegistry:
    return _REGISTRY


def load_builtin_tools(registry: ToolRegistry | None = None) -> ToolRegistry:
    """Import all builtin tool modules so they register themselves."""
    from . import builtin  # noqa: F401

    return registry or _REGISTRY
