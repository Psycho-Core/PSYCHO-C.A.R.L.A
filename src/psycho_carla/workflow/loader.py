"""
Workflow loader — read workflows from TOML files.

Format:

    [workflow]
    name = "research_pipeline"

    [[workflow.nodes]]
    id = "researcher"
    type = "agent"
    agent = "orchestrator"
    prompt = "Research {input}"

    [[workflow.nodes]]
    id = "summarizer"
    type = "agent"
    agent = "orchestrator"
    prompt = "Summarise: {researcher}"

    [[workflow.edges]]
    source = "researcher"
    target = "summarizer"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .graph import WorkflowGraph
from .types import NodeType, WorkflowEdge, WorkflowNode


def load_workflow(path: str | Path) -> WorkflowGraph:
    """Load a workflow definition from a TOML file."""
    p = Path(path)
    with open(p, "rb") as fh:
        data = tomllib.load(fh)

    wf_data = data.get("workflow", {})
    name = wf_data.get("name", p.stem)

    graph = WorkflowGraph(name=name)
    for node_data in wf_data.get("nodes", []):
        graph.add_node(_parse_node(node_data))
    for edge_data in wf_data.get("edges", []):
        graph.add_edge(
            WorkflowEdge(
                source=edge_data["source"],
                target=edge_data["target"],
                condition=edge_data.get("condition", ""),
            )
        )

    valid, msg = graph.validate()
    if not valid:
        raise ValueError(f"Invalid workflow in {p}: {msg}")
    return graph


def load_workflow_dir(directory: str | Path) -> dict[str, WorkflowGraph]:
    """Load every workflow in a directory, keyed by file stem."""
    d = Path(directory)
    out: dict[str, WorkflowGraph] = {}
    if not d.is_dir():
        return out
    for entry in sorted(d.glob("*.toml")):
        try:
            out[entry.stem] = load_workflow(entry)
        except Exception:
            # A broken workflow file must not break the whole directory.
            continue
    return out


def _parse_node(data: dict[str, Any]) -> WorkflowNode:
    """Parse one node from TOML data."""
    raw_type = str(data.get("type", "agent"))
    try:
        node_type = NodeType(raw_type)
    except ValueError:
        raise ValueError(f"Unknown workflow node type: {raw_type}") from None
    return WorkflowNode(
        id=data["id"],
        node_type=node_type,
        agent=data.get("agent", ""),
        tools=list(data.get("tools", []) or []),
        config=dict(data.get("config", {}) or {}),
        condition_expr=data.get("condition_expr", data.get("condition", "")),
        max_iterations=int(data.get("max_iterations", 10) or 10),
        transform_expr=data.get("transform_expr", data.get("transform", "")),
        children=list(data.get("children", []) or []),
        prompt=data.get("prompt", ""),
    )


__all__ = ["load_workflow", "load_workflow_dir"]
