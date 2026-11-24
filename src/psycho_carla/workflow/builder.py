"""
WorkflowBuilder — a fluent API for constructing workflow graphs.

    wf = (WorkflowBuilder("research_pipeline")
          .add_agent("researcher", agent="orchestrator", tools=["web_search"])
          .add_agent("summarizer", agent="orchestrator")
          .connect("researcher", "summarizer")
          .build())
"""

from __future__ import annotations

from typing import Any

from .graph import WorkflowGraph
from .types import NodeType, WorkflowEdge, WorkflowNode


class WorkflowBuilder:
    """Builds a :class:`WorkflowGraph` step by step."""

    def __init__(self, name: str = "") -> None:
        self._name = name
        self._nodes: list[WorkflowNode] = []
        self._edges: list[WorkflowEdge] = []

    # -- nodes ---------------------------------------------------------------- #
    def add_agent(
        self,
        node_id: str,
        *,
        agent: str = "orchestrator",
        tools: list[str] | None = None,
        prompt: str = "",
        config: dict[str, Any] | None = None,
    ) -> "WorkflowBuilder":
        """Add an agent node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.AGENT,
                agent=agent,
                tools=tools or [],
                prompt=prompt,
                config=config or {},
            )
        )
        return self

    def add_tool(
        self,
        node_id: str,
        *,
        tool_name: str,
        tool_args: Any = None,
    ) -> "WorkflowBuilder":
        """Add a tool node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.TOOL,
                config={"tool_name": tool_name, "tool_args": tool_args or {}},
            )
        )
        return self

    def add_condition(
        self,
        node_id: str,
        *,
        expr: str,
    ) -> "WorkflowBuilder":
        """Add a condition node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.CONDITION,
                condition_expr=expr,
            )
        )
        return self

    def add_parallel(
        self,
        node_id: str,
        children: list[str],
    ) -> "WorkflowBuilder":
        """Add a parallel fan-out node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.PARALLEL,
                children=list(children),
            )
        )
        return self

    def add_loop(
        self,
        node_id: str,
        *,
        target: str,
        condition: str = "",
        max_iterations: int = 10,
    ) -> "WorkflowBuilder":
        """Add a loop node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.LOOP,
                children=[target],
                condition_expr=condition,
                max_iterations=max_iterations,
                config={"target": target},
            )
        )
        return self

    def add_transform(
        self,
        node_id: str,
        *,
        expr: str,
        source: str = "",
    ) -> "WorkflowBuilder":
        """Add a transform node."""
        self._nodes.append(
            WorkflowNode(
                id=node_id,
                node_type=NodeType.TRANSFORM,
                transform_expr=expr,
                config={"source": source},
            )
        )
        return self

    # -- edges ---------------------------------------------------------------- #
    def connect(
        self,
        source: str,
        target: str,
        condition: str = "",
    ) -> "WorkflowBuilder":
        """Connect two nodes."""
        self._edges.append(
            WorkflowEdge(source=source, target=target, condition=condition)
        )
        return self

    # -- build ---------------------------------------------------------------- #
    def build(self) -> WorkflowGraph:
        """Validate and return the graph."""
        graph = WorkflowGraph(name=self._name)
        for node in self._nodes:
            graph.add_node(node)
        for edge in self._edges:
            graph.add_edge(edge)
        valid, msg = graph.validate()
        if not valid:
            raise ValueError(f"Invalid workflow '{self._name}': {msg}")
        return graph


__all__ = ["WorkflowBuilder"]
