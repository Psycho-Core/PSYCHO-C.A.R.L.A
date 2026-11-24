"""
Recipe composition — turning a recipe into something runnable.

Two compositions:

  * :func:`recipe_to_eval_suite` — a recipe becomes an
    :class:`~psycho_carla.bench.runner.EvalSuite`, so a benchmark can be
    reproduced from a recipe alone.
  * :func:`recipe_to_config_overrides` — a recipe becomes the set of
    config overrides that build the same system.

Layer: Pillar 3 (Agents).
"""

from __future__ import annotations

from typing import Any

from .loader import Recipe


def recipe_to_config_overrides(recipe: Recipe) -> dict[str, Any]:
    """The config overrides that reproduce a recipe's system."""
    overrides: dict[str, Any] = {}

    if recipe.agent_type or recipe.max_turns or recipe.temperature is not None:
        agent: dict[str, Any] = {}
        if recipe.agent_type:
            agent["type"] = recipe.agent_type
        if recipe.max_turns:
            agent["max_turns"] = recipe.max_turns
        if recipe.temperature is not None:
            agent["temperature"] = recipe.temperature
        if recipe.tools:
            agent["tools"] = list(recipe.tools)
        if recipe.system_prompt:
            agent["system_prompt_extra"] = recipe.system_prompt
        overrides["agents"] = {recipe.name: agent}

    if recipe.routing_policy or recipe.agent_policy:
        learning: dict[str, Any] = {}
        if recipe.routing_policy:
            learning["routing"] = recipe.routing_policy
        if recipe.agent_policy:
            learning["agent"] = recipe.agent_policy
        overrides["learning"] = learning

    if recipe.engine_key:
        overrides["engine"] = {"default_adapter": recipe.engine_key}

    if recipe.model:
        overrides["tiers"] = {
            "recipe_tier": {
                "provider": recipe.provider or "",
                "model": recipe.model,
                "quantization": recipe.quantization or "",
            }
        }

    return overrides


def recipe_to_eval_suite(recipe: Recipe) -> Any:
    """Turn a recipe into an :class:`EvalSuite`.

    Returns None when the recipe declares no eval suites — there is
    nothing to grade.
    """
    from ..bench.runner import EvalCase, EvalSuite

    if not recipe.eval_suites:
        return None

    cases: list[EvalCase] = []
    for suite_name in recipe.eval_suites:
        cases.append(
            EvalCase(
                name=f"{recipe.name}:{suite_name}",
                prompt=f"Run the '{suite_name}' suite for the '{recipe.name}' recipe.",
                agent=recipe.agent_type or "orchestrator",
                metadata={
                    "recipe": recipe.name,
                    "suite": suite_name,
                    "backend": recipe.eval_backend or "keyword",
                    "max_samples": recipe.eval_max_samples or 0,
                    "judge_model": recipe.eval_judge_model or "",
                },
            )
        )
    return EvalSuite(name=f"{recipe.name}-eval", cases=cases)


__all__ = ["recipe_to_config_overrides", "recipe_to_eval_suite"]
