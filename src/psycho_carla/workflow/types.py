"""
Workflow type definitions.

Six node types, each with a different execution semantics:

  * ``agent``     — run an agent with the node's prompt
  * ``tool``      — invoke a tool directly
  * ``condition`` — branch on an expression over prior outputs
  * ``parallel``  — fan out to several child nodes
  * ``loop``      — repeat a node until a condition holds or the cap is hit
  * ``transform`` — map one output into another (e.g. summarise it)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    CONDITION = "condition"
    PARALLEL = "parallel"
    LOOP = "loop"
    TRANSFORM = "transform"


@dataclass
class WorkflowNode:
    """One node in a workflow graph."""

    id: str
    node_type: NodeType
    agent: str = ""
    tools: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    # For condition nodes.
    condition_expr: str = ""
    # For loop nodes.
    max_iterations: int = 10
    # For transform nodes.
    transform_expr: str = ""
    # For parallel nodes — the child node ids.
    children: list[str] = field(default_factory=list)
    # For agent nodes — the prompt template.
    prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.node_type.value,
            "agent": self.agent,
            "tools": list(self.tools),
            "config": dict(self.config),
            "condition_expr": self.condition_expr,
            "max_iterations": self.max_iterations,
            "transform_expr": self.transform_expr,
            "children": list(self.children),
            "prompt": self.prompt,
        }


@dataclass
class WorkflowEdge:
    """A directed edge between two nodes."""

    source: str
    target: str
    condition: str = ""  # optional condition for conditional routing

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "condition": self.condition,
        }


@dataclass
class WorkflowStepResult:
    """The outcome of executing one node."""

    node_id: str
    success: bool = True
    output: str = ""
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "success": self.success,
            "output": self.output,
            "duration_seconds": round(self.duration_seconds, 3),
            "metadata": dict(self.metadata),
            "error": self.error,
        }


@dataclass
class WorkflowResult:
    """The outcome of executing a whole workflow."""

    workflow_name: str = ""
    success: bool = True
    steps: list[WorkflowStepResult] = field(default_factory=list)
    final_output: str = ""
    total_duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def failed_steps(self) -> list[WorkflowStepResult]:
        return [s for s in self.steps if not s.success]

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "success": self.success,
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "total_duration_seconds": round(self.total_duration_seconds, 3),
            "metadata": dict(self.metadata),
        }


__all__ = [
    "NodeType",
    "WorkflowEdge",
    "WorkflowNode",
    "WorkflowResult",
    "WorkflowStepResult",
]
