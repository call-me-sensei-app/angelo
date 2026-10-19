# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed ``property`` predicate: allow-listed settings of an object or the bound scene.

Reads the checked settings paths (``rna_rules``) on the already
resolved target (a staged or live object, or the scene a binding resolved).
Numbers compare within the check's tolerance, or float32 read-back precision
when it has none; booleans, enums, text and object pointers compare exactly.
A setting that an action curve or driver animates is inconclusive, because its
stored value need not be what evaluation shows. Read-only; no evaluation.
"""

from .action_properties import SCENE, holder_and_name, owner_of, plain, same, PropertyPathError
from .rna_rules import pointer_matches
from .action_world_spec import NO_ENVIRONMENT, WorldGraphError, graph_values, world_graph
from . import world_sky
from .validation_reader import Inconclusive, context_value, resolve_target, values_near


def _curves(block):
    """Data paths of the Action curves, NLA strips' curves and drivers on one ID (``bone_state.animated_paths``)."""
    from .bone_state import animated_paths
    return animated_paths(block)


def _animated(target, path):
    first, _, rest = path.partition(".")
    hop = {"data": "data", "active_material": "active_material", "world": "world"}.get(first) if rest else None
    block, relative = (getattr(target, hop, None), rest) if hop else (target, path)
    curves = _curves(block)
    return "*" in curves or relative in curves


def evaluate_property(reader, target, check):
    """Return (passed, bounded metrics) for one canonical property check."""
    values = check["values"]
    scene_target = getattr(getattr(target, "bl_rna", None), "identifier", "") == "Scene"
    if (owner_of(next(iter(values))) == SCENE) != scene_target:
        return False, {"owner": "SCENE" if scene_target else "OBJECT", "reason": "settings of a different owner"}
    reader.spend(len(values))
    observed, failed, unresolved = {}, [], []
    for path, expected in values.items():
        if _animated(target, path):
            raise Inconclusive("An animated or driven setting can differ from its stored value: " + path)
        try:
            holder, name = holder_and_name(target, path)
            value = getattr(holder, name)
        except (PropertyPathError, AttributeError):
            return False, {"path": path, "missing": True}
        prop = getattr(getattr(getattr(holder, "bl_rna", None), "properties", None), "get", lambda _name: None)(name)
        if getattr(prop, "type", None) == "POINTER":
            matched, missing = pointer_matches(value, expected,
                                               lambda reference: resolve_target(reader.context, reference))
            observed[path] = value.name[:64] if value is not None else None
            failed += [] if matched else [path]
            unresolved += [path] if missing else []
            continue
        value = plain(value)
        observed[path] = value
        numeric = type(expected) is float or (isinstance(expected, list) and any(type(item) is float for item in expected))
        if check.get("tolerance") is not None and numeric:
            matched = values_near(value, expected, check["tolerance"])
        else:
            matched = same(path, expected, value)
        if not matched:
            failed.append(path)
    result = {"values": observed, "scope": "STORED_SETTINGS"}
    if failed:
        result["mismatched"] = failed
    if unresolved:
        result["unresolved"] = unresolved  # The expected value names no object (a name, or an undeclared alias).
    return not failed, result


def _near(expected, actual, tolerance):
    if tolerance is None:
        numbers = expected if isinstance(expected, list) else [expected]
        tolerance = max(.000001, 4e-7 * max(abs(value) for value in numbers))
    return values_near(actual, expected, tolerance)


def evaluate_world(reader, scene, check):
    """Return (passed, bounded metrics) for one canonical world_settings check.

    A staged World holds no environment image (commit assigns it); the staging
    context's ``environment_images`` supplies that image instead.
    """
    if getattr(getattr(scene, "bl_rna", None), "identifier", "") != "Scene":
        return False, {"reason": "world_settings target is not the bound scene"}
    world = scene.world
    if world is None:
        return False, {"world": None}
    if _curves(world) or _curves(getattr(world, "node_tree", None)):
        raise Inconclusive("An animated or driven World can differ from its stored settings")
    try:
        values = graph_values(world_graph(world))
    except WorldGraphError as error:
        raise Inconclusive(str(error)) from error
    reader.spend(len(check["values"]))
    pending = context_value(reader.context, "environment_images", {}) or {}
    image = pending[check["target"]] if check["target"] in pending else values.get("image")
    from .action_world import image_id  # Blender-only; IDs carry the document epoch.
    observed = {"color": values["color"], "strength": values["strength"],
                "environment": image_id(image) if "image" in values else NO_ENVIRONMENT}
    if "rotation" in values:
        observed["rotation"] = values["rotation"]
    observed["sky"] = values.get("sky", world_sky.NO_SKY)
    failed = []
    for key, expected in check["values"].items():
        if key == "environment":
            matched = observed["environment"] == expected
        elif key == "sky":
            matched = world_sky.matches(expected, observed["sky"])
        elif key == "rotation":
            # A World without an environment texture has nothing to turn: any rotation describes it.
            matched = "rotation" not in observed or _near(expected, observed["rotation"], check.get("tolerance"))
        else:
            matched = _near(expected, observed[key], check.get("tolerance"))
        if not matched:
            failed.append(key)
    result = {"values": observed, "scope": "STORED_WORLD_BACKGROUND"}
    if failed:
        result["mismatched"] = failed
    return not failed, result
