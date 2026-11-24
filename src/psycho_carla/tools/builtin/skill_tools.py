"""
Skills tools — discovery and activation for the agentskills.io catalog.

The agent holds each skill's name and description in context (that is the
progressive-disclosure contract). When a task matches a skill, it calls
``skill_load`` to pull in the full instructions, and ``skill_search``
when it is unsure which skill applies.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

from typing import Any

from ..registry import ToolRegistry


def install_skill_tools(registry: ToolRegistry, runtime: Any) -> ToolRegistry:
    """Register the skill discovery/activation tools."""

    def skill_search(query: str, limit: int = 5) -> str:
        """
        Search the installed skills catalog by task description.

        Use this when you have a task but are not sure which skill, if
        any, covers it. Returns the best-matching skills with their
        descriptions.

        Args:
            query: What you need to do, in natural language.
            limit: Maximum number of skills to return.
        """
        store = getattr(runtime, "skills", None)
        if store is None:
            return "No skills catalog is installed."
        matches = store.catalog.search(query, limit=limit)
        if not matches:
            return "No installed skill matches that task."
        lines = []
        for match in matches:
            lines.append(f"- {match.skill.name}: {match.skill.description}")
        return "\n".join(lines)

    def skill_load(name: str) -> str:
        """
        Load a skill's full instructions by name.

        Call this once you have decided a skill applies. The returned
        text is the skill's complete playbook — follow it.

        Args:
            name: The skill name, as shown by skill_search.
        """
        store = getattr(runtime, "skills", None)
        if store is None:
            return "No skills catalog is installed."
        skill = store.catalog.get(name)
        if skill is None:
            available = ", ".join(store.catalog.names) or "none"
            return f"No skill named '{name}'. Installed: {available}"
        if not skill.valid:
            problems = "; ".join(skill.errors)
            return f"Skill '{name}' is invalid and was not loaded: {problems}"
        return skill.prompt_block()

    def skill_list() -> str:
        """
        List every installed skill with its description.

        Use this when you want to see the full catalog rather than search
        it. Returns one line per skill.
        """
        store = getattr(runtime, "skills", None)
        if store is None:
            return "No skills catalog is installed."
        if not len(store.catalog):
            return "No skills are installed."
        return "\n".join(
            f"- {skill.name}: {skill.description}"
            for skill in store.catalog.valid_skills
        )

    registry.register(
        "skill_search", skill_search,
        "Search the installed skills catalog by task description.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "The task, in natural language."},
            "limit": {"type": "integer", "description": "Maximum results."},
        }, "required": ["query"]},
        namespace="skills",
    )
    registry.register(
        "skill_load", skill_load,
        "Load a skill's full instructions by name.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "The skill name."},
        }, "required": ["name"]},
        namespace="skills",
    )
    registry.register(
        "skill_list", skill_list,
        "List every installed skill with its description.",
        {"type": "object", "properties": {}},
        namespace="skills",
    )
    return registry
