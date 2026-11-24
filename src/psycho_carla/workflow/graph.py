"""
WorkflowGraph — a DAG with validation and topological staging.

The graph is validated before execution: cycles are rejected (a workflow that
loops without a `loop` node would hang), and orphan nodes — nodes with no
incoming or outgoing edge — are reported but allowed, since a single-node
workflow is legitimate.

`execution_stages()` returns the graph in layers: every node in a stage has
no dependency on another node in the same stage, so the stage can run in
parallel.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from .types import WorkflowEdge, WorkflowNode


class WorkflowGraph:
    """A directed acyclic graph of workflow nodes."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._nodes: dict[str, WorkflowNode] = {}
        self._edges: list[WorkflowEdge] = []
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self._reverse: dict[str, list[str]] = defaultdict(list)

    # -- construction ------------------------------------------------------ #
    def add_node(self, node: WorkflowNode) -> None:
        if node.id in self._nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self._nodes[node.id] = node

    def add_edge(self, edge: WorkflowEdge) -> None:
        if edge.source not in self._nodes:
            raise ValueError(f"Source node '{edge.source}' not found")
        if edge.target not in self._nodes:
            raise ValueError(f"Target node '{edge.target}' not found")
        self._edges.append(edge)
        self._adjacency[edge.source].append(edge.target)
        self._reverse[edge.target].append(edge.source)

    # -- accessors --------------------------------------------------------- #
    def get_node(self, node_id: str) -> WorkflowNode | None:
        return self._nodes.get(node_id)

    @property
    def nodes(self) -> list[WorkflowNode]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[WorkflowEdge]:
        return list(self._edges)

    @property
    def node_ids(self) -> list[str]:
        return list(self._nodes)

    def incoming(self, node_id: str) -> list[WorkflowEdge]:
        """Edges pointing at a node."""
        return [e for e in self._edges if e.target == node_id]

    def outgoing(self, node_id: str) -> list[WorkflowEdge]:
        """Edges leaving a node."""
        return [e for e in self._edges if e.source == node_id]

    # -- validation --------------------------------------------------------- #
    def validate(self) -> tuple[bool, str]:
        """Validate the graph: detect cycles and report orphan nodes."""
        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node_id: str) -> bool:
            visited.add(node_id)
            in_stack.add(node_id)
            for neighbor in self._adjacency.get(node_id, []):
                if neighbor in in_stack:
                    return True  # cycle detected
                if neighbor not in visited and _dfs(neighbor):
                    return True
            in_stack.discard(node_id)
            return False

        for node_id in self._nodes:
            if node_id not in visited:
                if _dfs(node_id):
                    return False, f"Cycle detected involving node '{node_id}'"

        orphans = [
            node_id
            for node_id in self._nodes
            if not self._adjacency.get(node_id) and not self._reverse.get(node_id)
        ]
        if orphans:
            # Orphans are legal but worth surfacing.
            return True, f"warning: orphan node(s): {', '.join(orphans)}"
        return True, ""

    def has_cycle(self) -> bool:
        ok, msg = self.validate()
        return not ok and "Cycle" in msg

    # -- ordering ----------------------------------------------------------- #
    def topological_sort(self) -> list[str]:
        """Kahn's algorithm — a linear order respecting the edges."""
        indegree = {node_id: 0 for node_id in self._nodes}
        for edge in self._edges:
            indegree[edge.target] = indegree.get(edge.target, 0) + 1
        queue = deque(nid for nid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while queue:
            node_id = queue.popleft()
            order.append(node_id)
            for neighbor in self._adjacency.get(node_id, []):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)
        if len(order) != len(self._nodes):
            raise ValueError("Workflow graph contains a cycle")
        return order

    def execution_stages(self) -> list[list[str]]:
        """Group nodes into layers that can run concurrently."""
        indegree = {node_id: 0 for node_id in self._nodes}
        for edge in self._edges:
            indegree[edge.target] = indegree.get(edge.target, 0) + 1

        ready = deque(sorted(nid for nid, deg in indegree.items() if deg == 0))
        stages: list[list[str]] = []
        while ready:
            stage = list(ready)
            ready.clear()
            stages.append(stage)
            next_ready: set[str] = set()
            for node_id in stage:
                for neighbor in self._adjacency.get(node_id, []):
                    indegree[neighbor] -= 1
                    if indegree[neighbor] == 0:
                        next_ready.add(neighbor)
            ready.extend(sorted(next_ready))
        return stages

    # -- serialisation ------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }


__all__ = ["WorkflowGraph"]
