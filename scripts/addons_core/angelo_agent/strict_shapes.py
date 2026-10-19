# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shorter strict forms of the plan's everyday objects: trailing fields may be left out.

A strict ``json_schema`` response format requires every property of every
object (``strict_schema``), so an optional field the model does not need still
costs its name and ``null``. On a recorded bridge session (GPT-6 Astra)
those nulls were 8.6 KB of the 31.9 KB of plan JSON the model
wrote: a material operation carried eight of them, a cube two.

The subset has no optional property, but it has ``anyOf``. An object given a
*shape* here is offered as a few variants that are prefixes of one property
order: the model writes fields in that order and may close the object at a
*cut*, leaving the rest out (a default, or a setting kept as it is).
``strict_schema.decode`` already reads a missing optional property as omitted,
and the host's exact validation runs on the decoded plan as before, so a shape
changes only what is written, never what is accepted.

``SHAPES`` is keyed by where the object sits in the plan document. Orders put
what requests state first (a primitive's name, place and size before its
tessellation); cuts are placed where the recorded plans stopped. A name a
schema no longer has is ignored, and an object without a shape keeps its one
complete form. ``annotate`` marks the plan document before ``strict_schema``
derives the variant; ``forms`` is the one place that turns a shape into
property lists, for the schema and for ``strict_schema.encode`` alike.

Pure Python; no Blender access.
"""

SHAPE = "x-shape"
_PLACED = ("name", "alias", "location")


def _shape(order, *cuts):
    return {"order": tuple(order), "cuts": tuple(cuts)}


# create_primitive parameters by primitive: identity and place, the fields that size it, then the rest.
PRIMITIVES = {
    "CUBE": _shape(_PLACED + ("scale",), "scale"),
    "PLANE": _shape(_PLACED + ("scale",), "scale"),
    "UV_SPHERE": _shape(_PLACED + ("radius",), "radius"),
    "ICO_SPHERE": _shape(_PLACED + ("radius",), "radius"),
    "CYLINDER": _shape(_PLACED + ("radius", "depth", "rotation"), "rotation"),
}
# Modifier settings by type, for modifier_add and modifier_configure.
MODIFIERS = {"BEVEL": _shape(("width", "segments"), "segments")}
# Other operation parameters by kind.
PARAMETERS = {
    # A transform may stop after scale: "mode" (SET, the default) followed it in every recorded plan that wrote it.
    "transform": _shape(("location", "rotation", "scale"), "location", "rotation", "scale"),
    # A material's name, its look, and where a build has the field the further objects that get it too (also).
    "material_principled": _shape(("name", "inputs", "also"), "name", "inputs", "also"),
    "material_assign": _shape(("material", "also"), "material", "also"),
}
PRINCIPLED_INPUTS = _shape(("Base Color", "Roughness", "Metallic", "Emission Color", "Emission Strength"),
                           "Roughness", "Metallic")
CHECKS = {}
# Tool arguments: bindings the host infers come last. A call of a shaped recipe (RECIPES) may end after its mode:
# in the recorded runs every GPT recipe plan (246) wrote "recovery_observation":null, and each a revision
# (GPT-6.1-Sol) or "revision":null (GPT-6 Astra), which the host infers from the observation.
ARGUMENTS = {"angelo_scene_program": _shape(("mode", "operations", "expected_checks"), "operations", "expected_checks"),
             "angelo_apply_recipe": _shape(("recipe", "target", "parameters", "mode", "revision"), "mode")}
# A recipe's parameters by recipe (action_recipes.RECIPE_SCHEMAS), one cut each where the recorded plans stopped:
# in the same runs every fit-dimensions call (108) wrote the widest scale bounds and a tolerance after its
# dimensions, every material-setup call (104) a tolerance after its metallic, and every bevel-and-shade call (34)
# its names and a tolerance before apply_scale. The recipes no recorded plan used keep their one complete form.
RECIPES = {
    "bevel-and-shade": _shape(("width", "segments", "apply_scale"), "apply_scale"),
    "fit-dimensions": _shape(("dimensions",), "dimensions"),
    "material-setup": _shape(("name", "base_color", "roughness", "metallic"), "metallic"),
}


def forms(names, first, required, shape):
    """The property lists an object may be written with, shortest first; the last is the whole object.

    ``names`` are the object's strict properties in document order, ``first``
    those that select a union member (written first, whatever the shape says)
    and ``required`` the ones no form may leave out.
    """
    names = list(names)
    order = [name for name in names if name in first]
    order += [name for name in (shape or {}).get("order", ()) if name in names and name not in order]
    order += [name for name in names if name not in order]
    result = []
    for cut in (shape or {}).get("cuts", ()):
        prefix = order[:order.index(cut) + 1] if cut in order else order
        if len(prefix) < len(order) and set(required) <= set(prefix) and prefix not in result:
            result.append(prefix)
    return result + [order]


def _variants(node):
    """The object schemas at ``node``: itself, or the members of its union."""
    members = node.get("oneOf") or node.get("anyOf")
    return [member for member in members if isinstance(member, dict)] if isinstance(members, list) else [node]


def _const(node, name):
    return ((node.get("properties") or {}).get(name) or {}).get("const")


def _operation(variant):
    kind = _const(variant, "kind")
    parameters = (variant.get("properties") or {}).get("parameters")
    if not isinstance(parameters, dict):
        return
    for member in _variants(parameters):
        if kind == "create_primitive":
            shape = PRIMITIVES.get(_const(member, "primitive"))
        elif kind in ("modifier_add", "modifier_configure"):
            shape = None
            settings = (member.get("properties") or {}).get("settings")
            if isinstance(settings, dict) and _const(member, "type") in MODIFIERS:
                settings[SHAPE] = MODIFIERS[_const(member, "type")]
        else:
            shape = PARAMETERS.get(kind)
        if shape is not None:
            member[SHAPE] = shape
        inputs = (member.get("properties") or {}).get("inputs")
        if kind == "material_principled" and isinstance(inputs, dict):
            inputs[SHAPE] = PRINCIPLED_INPUTS


def annotate(document):
    """Mark the shaped objects of a plan document (``plan_schema.plan_document``) in place; returns it."""
    for variant in _variants(document["properties"]["operations"].get("items") or {}):
        arguments = (variant.get("properties") or {}).get("arguments")
        tool = _const(variant, "tool")
        if not isinstance(arguments, dict) or tool not in ("angelo_scene_program", "angelo_scene_verify",
                                                           "angelo_apply_recipe"):
            continue
        if tool == "angelo_apply_recipe":  # One arguments member per recipe; only the recorded recipes are shaped.
            for member in _variants(arguments):
                parameters = (member.get("properties") or {}).get("parameters")
                if isinstance(parameters, dict) and _const(member, "recipe") in RECIPES:
                    member[SHAPE], parameters[SHAPE] = ARGUMENTS[tool], RECIPES[_const(member, "recipe")]
            continue
        if tool in ARGUMENTS:
            arguments[SHAPE] = ARGUMENTS[tool]
        properties = arguments.get("properties") or {}
        for operation in _variants((properties.get("operations") or {}).get("items") or {}):
            _operation(operation)
        for field in ("expected_checks", "checks"):
            for check in _variants((properties.get(field) or {}).get("items") or {}):
                if _const(check, "kind") in CHECKS:
                    check[SHAPE] = CHECKS[_const(check, "kind")]
    return document
