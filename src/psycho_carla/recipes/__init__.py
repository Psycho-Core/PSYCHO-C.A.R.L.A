"""
PSYCHO-C.A.R.L.A. — Recipes.

A recipe is a **composable primitive configuration**: one file that pins
an engine, a model, an agent, a learning policy and an evaluation suite
into one reproducible bundle. Recipes come in two kinds:

  * ``discrete`` — a one-shot agent for benchmarking. It has an eval
    suite and can be turned into an :class:`EvalSuite`.
  * ``operator`` — a persistent, scheduled agent. It has a schedule and a
    set of channels.

The point of a recipe is reproducibility: the same recipe always builds
the same system, so a benchmark result can be re-run and compared.

Layer: Pillar 3 (Agents) — the composition layer above the primitives.
"""

from .loader import (
    Recipe,
    discover_recipes,
    load_recipe,
    resolve_recipe,
)
from .composer import recipe_to_eval_suite, recipe_to_config_overrides

__all__ = [
    "Recipe",
    "discover_recipes",
    "load_recipe",
    "recipe_to_config_overrides",
    "recipe_to_eval_suite",
    "resolve_recipe",
]
