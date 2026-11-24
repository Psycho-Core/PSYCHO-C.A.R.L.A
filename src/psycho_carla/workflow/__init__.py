"""
PSYCHO-C.A.R.L.A. — Workflow engine.

DAG-based multi-agent workflows, matching upstream OpenJarvis's `workflow/`
subpackage:

  * :mod:`.types` — ``WorkflowNode``, ``WorkflowEdge``, ``NodeType``,
    ``WorkflowStepResult``, ``WorkflowResult``
  * :mod:`.graph` — ``WorkflowGraph``: DAG validation, topological sort,
    execution stages
  * :mod:`.engine` — ``WorkflowEngine``: executes the graph, with parallel
    stages, conditions, loops and transforms
  * :mod:`.builder` — ``WorkflowBuilder``: a fluent API
  * :mod:`.loader` — ``load_workflow()``: read a workflow from TOML

Layer: Pillar 3 (Agents).
"""

from .builder import WorkflowBuilder
from .engine import WorkflowEngine
from .graph import WorkflowGraph
from .loader import load_workflow, load_workflow_dir
from .types import (
    NodeType,
    WorkflowEdge,
    WorkflowNode,
    WorkflowResult,
    WorkflowStepResult,
)

__all__ = [
    "NodeType",
    "WorkflowBuilder",
    "WorkflowEdge",
    "WorkflowEngine",
    "WorkflowGraph",
    "WorkflowNode",
    "WorkflowResult",
    "WorkflowStepResult",
    "load_workflow",
    "load_workflow_dir",
]
