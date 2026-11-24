"""
Skill executor — runs a skill's declared step pipeline.

A runnable skill is a folder containing ``SKILL.md`` (agentskills.io
frontmatter + instructions) and a ``skill.toml`` with the upstream-style
step list::

    [[steps]]
    tool = "file_read"
    arguments = '{"path": "{file_path}"}'
    output = "source"

Templates substitute ``{placeholders}`` from the run inputs first, then
from earlier step outputs (in that order, JSON-escaped inside strings).
Each step's result is stored under its ``output`` key; the executor
returns a transcript of every step plus the final output.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

from .bundled import BUNDLED_SKILLS

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# Tool arguments that template authors may include but local tools do
# not accept; dropped before dispatch.
_DROPPED_ARGS = {"metadata"}


def load_steps(skill_root: str | Path) -> list[dict[str, Any]]:
    """Read the ``[[steps]]`` list from a skill folder's skill.toml."""
    toml_path = Path(skill_root) / "skill.toml"
    if not toml_path.is_file():
        return []
    with open(toml_path, "rb") as fh:
        data = tomllib.load(fh)
    steps = data.get("steps") or (data.get("skill") or {}).get("steps") or []
    normalised: list[dict[str, Any]] = []
    for step in steps:
        normalised.append({
            "tool": step.get("tool") or step.get("tool_name") or "",
            "arguments": step.get("arguments")
            or step.get("arguments_template") or "{}",
            "output": step.get("output") or step.get("output_key") or "",
        })
    return normalised


def render_skill_md(entry: dict[str, Any]) -> str:
    """Render a bundled-skill entry as SKILL.md text."""
    name = entry["name"]
    description = entry["description"].replace('"', "'")
    inputs = ", ".join(f"`{item}`" for item in entry.get("inputs", [])) or "(none)"
    lines = [
        "---",
        f"name: {name}",
        f'description: "{description}"',
        f'version: "{entry.get("version", "0.1.0")}"',
        f"author: {entry.get('author', 'openjarvis')}",
        "---",
        "",
        f"# {name}",
        "",
        entry["description"],
        "",
        f"Inputs: {inputs}",
        "",
        "## Steps",
        "",
    ]
    for index, step in enumerate(entry.get("steps", []), 1):
        lines.append(f"{index}. `{step['tool']}` → `{step.get('output', '')}`")
    lines += [
        "",
        "Run with: carla skill run " + name + ' \'{"input": "value"}\'',
        "",
    ]
    return "\n".join(lines)


def render_skill_toml(entry: dict[str, Any]) -> str:
    """Render a bundled-skill entry as skill.toml text."""
    lines = [
        f"# Bundled skill: {entry['name']} (ported from upstream)",
        "",
    ]
    for step in entry.get("steps", []):
        lines += [
            "[[steps]]",
            f'tool = "{step["tool"]}"',
            f"arguments = '{step['arguments']}'",
            f'output = "{step.get("output", "")}"',
            "",
        ]
    return "\n".join(lines)


class SkillExecutor:
    """Executes declarative skill step pipelines against a tool registry."""

    def __init__(self, registry: Any) -> None:
        self.registry = registry

    def run(self, store: Any, name: str, inputs: dict[str, Any] | None = None) -> str:
        skill = store.catalog.get(name) if store is not None else None
        steps: list[dict[str, Any]] = []
        if skill is not None and skill.root is not None:
            steps = load_steps(skill.root)
        if not steps and name in BUNDLED_SKILLS:
            steps = list(BUNDLED_SKILLS[name].get("steps", []))
        if skill is None and name not in BUNDLED_SKILLS:
            return f"Error: no skill named '{name}'."
        if not steps:
            return (f"Skill '{name}' has no executable steps (it is an "
                    "instruction-only skill — load it and follow the text).")

        context: dict[str, Any] = dict(inputs or {})
        transcript: list[str] = [f"Skill '{name}' — {len(steps)} steps"]
        for index, step in enumerate(steps, 1):
            tool_name = step.get("tool", "")
            rendered = self._render(step.get("arguments") or "{}", context)
            try:
                arguments = json.loads(rendered or "{}")
            except Exception as exc:
                return (f"Error: step {index} ({tool_name}) produced invalid "
                        f"JSON arguments: {exc}")
            arguments = {k: v for k, v in arguments.items()
                         if k not in _DROPPED_ARGS}
            result = self.registry.execute(tool_name, arguments)
            soft_failed = (isinstance(result.content, str)
                           and result.content.startswith("Error:"))
            output = result.content if result.ok else \
                f"ERROR: {result.error or result.content}"
            key = step.get("output") or f"step_{index}"
            context[key] = output
            transcript.append(f"[{index}] {tool_name} → {key}: "
                              f"{str(output)[:240]}")
            if not result.ok:
                transcript.append("Step failed; stopping.")
                break
            if soft_failed:
                transcript.append(
                    f"Step {index} reported an error; continuing with "
                    "partial context.")
        final_key = steps[-1].get("output") or ""
        if final_key and final_key in context:
            transcript += ["", "--- Final output ---", str(context[final_key])]
        return "\n".join(transcript)

    @staticmethod
    def _render(template: str, context: dict[str, Any]) -> str:
        def replace(match: re.Match) -> str:
            value = context.get(match.group(1), "")
            text = value if isinstance(value, str) else json.dumps(value)
            return json.dumps(text)[1:-1]

        return _PLACEHOLDER.sub(replace, template or "{}")


__all__ = ["SkillExecutor", "load_steps", "render_skill_md", "render_skill_toml"]
