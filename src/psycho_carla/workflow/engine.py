"""
WorkflowEngine — executes a WorkflowGraph against the runtime.

Sequential nodes run in topological order. Nodes in the same execution
stage have no dependency on each other, so they run concurrently in a thread
pool.

Node semantics:

  * **agent** — call ``runtime.ask()`` with the node's prompt template.
  * **tool** — call ``runtime.tools.execute()`` directly.
  * **condition** — evaluate an expression over the prior outputs; the
    result selects which outgoing edge to follow.
  * **parallel** — run the child nodes concurrently and join their outputs.
  * **loop** — repeat a target node until the condition holds or the
    iteration cap is reached.
  * **transform** — map an input into an output (summarise, extract, join).

The expression evaluator is deliberately small and safe: it supports a
handful of comparison and boolean operators over the node outputs. It does
**not** eval arbitrary Python — a workflow file is configuration, and
configuration should not be able to execute code.
"""

from __future__ import annotations

import concurrent.futures
import re
import time
from typing import Any

from .graph import WorkflowGraph
from .types import (
    NodeType,
    WorkflowNode,
    WorkflowResult,
    WorkflowStepResult,
)


class WorkflowEngine:
    """Execute DAG-based workflows."""

    def __init__(
        self,
        *,
        max_parallel: int = 4,
        default_node_timeout: float = 300.0,
    ) -> None:
        self._max_parallel = max(1, int(max_parallel))
        self._default_node_timeout = float(default_node_timeout)

    # -- entry point -------------------------------------------------------- #
    def run(
        self,
        graph: WorkflowGraph,
        runtime: Any = None,
        *,
        initial_input: str = "",
        context: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a workflow graph end-to-end."""
        valid, msg = graph.validate()
        if not valid:
            return WorkflowResult(
                workflow_name=graph.name,
                success=False,
                final_output=f"Invalid workflow: {msg}",
            )

        t0 = time.perf_counter()
        outputs: dict[str, str] = {"_input": initial_input}
        ctx = dict(context or {})
        all_steps: list[WorkflowStepResult] = []
        success = True

        for stage in graph.execution_stages():
            if len(stage) == 1:
                step = self._execute_node(
                    graph.get_node(stage[0]), outputs, ctx, runtime, graph
                )
                all_steps.append(step)
                outputs[stage[0]] = step.output
                if not step.success:
                    success = False
                    break
            else:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(len(stage), self._max_parallel)
                ) as pool:
                    futures = {
                        pool.submit(
                            self._execute_node,
                            graph.get_node(nid),
                            dict(outputs),
                            dict(ctx),
                            runtime,
                            graph,
                        ): nid
                        for nid in stage
                    }
                    for future in concurrent.futures.as_completed(futures):
                        nid = futures[future]
                        try:
                            step = future.result(timeout=self._default_node_timeout)
                        except Exception as exc:  # noqa: BLE001
                            step = WorkflowStepResult(
                                node_id=nid,
                                success=False,
                                output=f"Node execution error: {exc}",
                                error=f"{type(exc).__name__}: {exc}",
                            )
                        all_steps.append(step)
                        outputs[nid] = step.output
                        if not step.success:
                            success = False

            if not success:
                break

        total = time.perf_counter() - t0
        final_output = all_steps[-1].output if all_steps else initial_input
        return WorkflowResult(
            workflow_name=graph.name,
            success=success,
            steps=all_steps,
            final_output=final_output,
            total_duration_seconds=total,
            metadata={"stages": len(graph.execution_stages()), "warning": msg},
        )

    # -- node execution ------------------------------------------------------ #
    def _execute_node(
        self,
        node: WorkflowNode | None,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        runtime: Any,
        graph: WorkflowGraph,
    ) -> WorkflowStepResult:
        if node is None:
            return WorkflowStepResult(node_id="?", success=False, error="missing node")
        t0 = time.perf_counter()
        try:
            if node.node_type == NodeType.AGENT:
                output = self._run_agent(node, outputs, ctx, runtime, graph)
            elif node.node_type == NodeType.TOOL:
                output = self._run_tool(node, outputs, ctx, runtime)
            elif node.node_type == NodeType.CONDITION:
                output = self._run_condition(node, outputs)
            elif node.node_type == NodeType.PARALLEL:
                output = self._run_parallel(node, outputs, ctx, runtime, graph)
            elif node.node_type == NodeType.LOOP:
                output = self._run_loop(node, outputs, ctx, runtime, graph)
            elif node.node_type == NodeType.TRANSFORM:
                output = self._run_transform(node, outputs)
            else:
                output = f"Unknown node type: {node.node_type}"
            return WorkflowStepResult(
                node_id=node.id,
                success=True,
                output=output,
                duration_seconds=time.perf_counter() - t0,
            )
        except Exception as exc:  # noqa: BLE001 - a failed node is data,
            # not a crash. The workflow reports it.
            return WorkflowStepResult(
                node_id=node.id,
                success=False,
                output=f"Error: {exc}",
                duration_seconds=time.perf_counter() - t0,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_agent(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        runtime: Any,
        graph: WorkflowGraph | None = None,
    ) -> str:
        prompt = self._render_prompt(node, outputs, graph)
        if runtime is None:
            return f"[no runtime] {prompt}"
        agent_name = node.agent or ctx.get("agent", "") or ""
        result = runtime.ask(prompt, agent=agent_name)
        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result)

    def _run_tool(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        runtime: Any,
    ) -> str:
        tool_name = str(node.config.get("tool_name", "") or "")
        if not tool_name:
            raise ValueError(f"Tool node '{node.id}' has no tool_name")
        tool_args = node.config.get("tool_args", {}) or {}
        if isinstance(tool_args, str):
            import json

            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {"input": tool_args}
        if not isinstance(tool_args, dict):
            tool_args = {}
        # Render any string argument as a template over the outputs.
        rendered = {
            k: self._render_prompt_text(str(v), outputs) if isinstance(v, str) else v
            for k, v in tool_args.items()
        }
        if runtime is None:
            return f"[no runtime] {tool_name}({rendered})"
        res = runtime.tools.execute(tool_name, rendered)
        return str(getattr(res, "content", res))

    def _run_condition(self, node: WorkflowNode, outputs: dict[str, str]) -> str:
        """Evaluate the condition and return the branch name."""
        expr = node.condition_expr or ""
        value = evaluate_condition(expr, outputs)
        return "true" if value else "false"

    def _run_parallel(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        runtime: Any,
        graph: WorkflowGraph,
    ) -> str:
        children = node.children or [
            e.target for e in graph.outgoing(node.id)
        ]
        if not children:
            return ""
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(children), self._max_parallel)
        ) as pool:
            futures = {
                pool.submit(
                    self._execute_node,
                    graph.get_node(cid),
                    dict(outputs),
                    dict(ctx),
                    runtime,
                    graph,
                ): cid
                for cid in children
            }
            parts: list[str] = []
            for future in concurrent.futures.as_completed(futures):
                cid = futures[future]
                try:
                    step = future.result(timeout=self._default_node_timeout)
                except Exception as exc:  # noqa: BLE001
                    step = WorkflowStepResult(
                        node_id=cid, success=False,
                        output=f"Error: {exc}", error=str(exc),
                    )
                outputs[cid] = step.output
                parts.append(f"{cid}: {step.output}")
        return "\n".join(parts)

    def _run_loop(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        ctx: dict[str, Any],
        runtime: Any,
        graph: WorkflowGraph,
    ) -> str:
        """Repeat the target node until the condition holds or the cap is hit."""
        target_id = node.config.get("target", "") or (
            node.children[0] if node.children else ""
        )
        if not target_id:
            raise ValueError(f"Loop node '{node.id}' has no target")
        target = graph.get_node(target_id)
        if target is None:
            raise ValueError(f"Loop target '{target_id}' not found")

        iterations = 0
        last = ""
        cap = max(1, int(node.max_iterations))
        while iterations < cap:
            iterations += 1
            step = self._execute_node(target, outputs, ctx, runtime, graph)
            last = step.output
            outputs[target_id] = last
            if not step.success:
                return last
            if node.condition_expr:
                if evaluate_condition(node.condition_expr, outputs):
                    break
        return last

    def _run_transform(self, node: WorkflowNode, outputs: dict[str, str]) -> str:
        """Apply a transformation to the node's input."""
        source_id = node.config.get("source", "") or ""
        source = outputs.get(source_id, "") if source_id else ""
        expr = node.transform_expr or node.condition_expr or ""
        if not expr:
            return source
        # A transform expression is a template: {source} becomes the input.
        return expr.replace("{source}", source).replace("{input}", source)

    # -- helpers -------------------------------------------------------------- #
    def _render_prompt(
        self,
        node: WorkflowNode,
        outputs: dict[str, str],
        graph: WorkflowGraph | None = None,
    ) -> str:
        template = node.prompt or node.config.get("prompt", "") or ""
        if not template:
            # Default: use the most recent upstream output.
            if graph is not None:
                upstream = [e.source for e in graph.incoming(node.id)]
                if upstream:
                    return outputs.get(upstream[0], "")
            return outputs.get("_input", "")
        return self._render_prompt_text(template, outputs)

    @staticmethod
    def _render_prompt_text(template: str, outputs: dict[str, str]) -> str:
        out = template
        for key, value in outputs.items():
            out = out.replace("{" + key + "}", value)
        # {input} and {_input} are the workflow's initial input.
        out = out.replace("{_input}", outputs.get("_input", ""))
        out = out.replace("{input}", outputs.get("_input", ""))
        return out


# --------------------------------------------------------------------------- #
# Expression evaluation
# --------------------------------------------------------------------------- #
_COMPARISON_RE = re.compile(
    r"^\s*(?P<left>[^<>=!]+?)\s*(?P<op>==|!=|<=|>=|<|>)\s*(?P<right>.+?)\s*$"
)
_BOOL_RE = re.compile(
    r"^\s*(?P<left>.+?)\s+(?P<op>and|or)\s+(?P<right>.+?)\s*$", re.IGNORECASE
)
_NOT_RE = re.compile(r"^\s*not\s+(?P<expr>.+?)\s*$", re.IGNORECASE)


def evaluate_condition(expr: str, outputs: dict[str, str]) -> bool:
    """Evaluate a workflow condition expression.

    Supports:
      * ``a == b``, ``a != b``, ``a < b``, ``a <= b``, ``a > b``, ``a >= b``
      * ``a and b``, ``a or b``, ``not a``
      * a bare node id — true when that node produced output

    Operands may be literal strings (quoted), or node ids / ``{node_id}``
    references resolved against the outputs. Comparison is numeric when both
    sides parse as numbers, lexical otherwise.

    This is a deliberately restricted evaluator — it never executes Python.
    """
    expr = (expr or "").strip()
    if not expr:
        return False

    match = _NOT_RE.match(expr)
    if match:
        return not evaluate_condition(match.group("expr"), outputs)

    match = _BOOL_RE.match(expr)
    if match:
        left = evaluate_condition(match.group("left"), outputs)
        right = evaluate_condition(match.group("right"), outputs)
        return (
            (left and right)
            if match.group("op").lower() == "and"
            else (left or right)
        )

    match = _COMPARISON_RE.match(expr)
    if match:
        left = _resolve_operand(match.group("left"), outputs)
        right = _resolve_operand(match.group("right"), outputs)
        op = match.group("op")
        left_num = _as_number(left)
        right_num = _as_number(right)
        if left_num is not None and right_num is not None:
            a, b = left_num, right_num
        else:
            a, b = left.lower(), right.lower()
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        return False

    # A bare reference — true when it produced output. A token that names
    # no known node is falsy: it referenced something that never ran, and
    # a Python expression is just a (very long) identifier.
    value = _resolve_reference(expr, outputs)
    return bool(value.strip())


def _blank_unknown_reference(
    resolved: str, token: str, outputs: dict[str, str]
) -> str:
    """Blank a reference to a node that never ran.

    Only used for an explicitly braced reference, where the intent is
    unambiguous.
    """
    token = token.strip()
    if token.startswith("{") and token.endswith("}"):
        return outputs.get(token[1:-1], "")
    return resolved


def _resolve_reference(token: str, outputs: dict[str, str]) -> str:
    """Resolve a *bare* reference to a node's output.

    Unlike an operand, a bare reference is never a literal: an unknown
    node resolves to the empty string, so the condition is false. A
    quoted string is still a literal, because quoting is an explicit
    statement of intent.
    """
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    if token.startswith("{") and token.endswith("}"):
        return outputs.get(token[1:-1], "")
    return outputs.get(token, "")


def _resolve_operand(token: str, outputs: dict[str, str]) -> str:
    """Resolve one operand: a literal, or a reference to a node's output.

    Resolution order:
      1. A quoted string is always a literal.
      2. ``{node_id}`` is always a reference.
      3. A bare token that names a node that has already run is a
         reference to that node's output.
      4. Anything else is a literal — so ``a == 10`` and ``flag == yes``
         compare against the literal, not against a missing node.
    """
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    if token.startswith("{") and token.endswith("}"):
        inner = token[1:-1]
        return outputs.get(inner, "")
    if token in outputs:
        return outputs[token]
    return token


def _as_number(value: str) -> float | None:
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None


__all__ = ["WorkflowEngine", "evaluate_condition"]
