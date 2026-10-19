# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derived checks of the everyday kinds, composed for ``derived_checks`` (pure Python, no bpy).

``derived_checks`` derives one operation's checks and decides which earlier
ones a later operation leaves true. The kinds here state more than their own
target: ``also`` objects of a material kind and a name-only restatement
(``material_rules``), a batch's copies and the sources it moves
(``batch_spec``), and a Subdivision modifier's adaptive settings. They live
here so each rule stays with its kind and ``derived_checks`` only calls in.
"""

from . import batch_spec, material_rules
from .action_schema_types import adaptive_values


def extend(operations, index, derived, keeps):
    """``(check, kinds that keep it)`` pairs of ``operations[index]``: ``derived`` plus the everyday kinds' own.

    ``keeps``: the keep sets of ``derived_checks`` by family (material,
    modifier, all). Derived checks state no tolerance: each number compares at
    its own float32 read-back precision (check_defaults).
    """
    operation = operations[index]
    kind, params = operation["kind"], operation["parameters"]
    if kind == "material_assign":  # The assigned material, or its copy, by the name commit gives it.
        derived = derived + [({"kind": "material_assignment", "target": operation["target"],
                               "material": params["copy_as"] or params["material"], "all_faces": False},
                              keeps["material"])]
    if kind in {"modifier_add", "modifier_configure"} and params["type"] == "SUBSURF":
        adaptive = adaptive_values(params["settings"])
        if adaptive:  # Adaptive settings by RNA path: written only when stated.
            derived = derived + [({"kind": "modifier_values", "target": operation["target"],
                                   "name": params.get("name") or params.get("modifier"), "type": "SUBSURF",
                                   "values": adaptive},
                                  keeps["modifier"])]
    derived = material_rules.checks(operations, index, derived, keeps["material"])
    return derived + batch_spec.checks(operations, index, keeps["all"])


def displaced(derived, operation):
    """Keys of earlier checks ``operation`` makes untrue beyond its target: a batch moves the sources it places."""
    moved = batch_spec.moved(operation)
    return [key for key, (check, _) in derived.items()
            if check["target"] in moved and check["kind"] in ("transform", "dimensions")]
