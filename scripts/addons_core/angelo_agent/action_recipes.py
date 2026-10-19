# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure, exact expansions of approved recipes into closed native actions.

Recipes are planning conveniences, not an execution/retry privilege. Expansion
returns every canonical operation and assertion BEFORE the host's normal program
approval. There is no LLM retry, arbitrary code, inferred destructive selection
or cleanup. Fit dimensions is one analytic scale solve against approved scale
bounds (stated, or the widest a transform takes), not an unattended correction
loop. Only its read-only adapter uses Blender, and that adapter rechecks the
exact observed revision.
"""

import copy
import math
import re
from .refusal_codes import RefusalError
from .request_recovery import FIELD as RECOVERY_FIELD, CITATION_SCHEMA, citation

from .action_catalog import validate_operation
from .validation_spec import LABEL, TARGET, VECTOR, validate_checks, validate_value


def _number(low, high):
    return {"type": "number", "minimum": low, "maximum": high}


def _integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


def _schema(properties, required):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_indices = {"type": "array", "items": _integer(0, 99999), "minItems": 1, "maxItems": 4096, "uniqueItems": True}
_faces = dict(_indices, minItems=0)
_color = {"type": "array", "items": _number(0, 1), "minItems": 4, "maxItems": 4}
_dimension_vector = {"type": "array", "items": _number(0.000001, 10000), "minItems": 3, "maxItems": 3}
_tolerance = _number(0, 0.1)
# Blender's Bevel modifier measures its width in object space, so an object scale other than one stretches it.
# Through Blender MCP GPT-6.1-Sol applied a scaled cube's scale before beveling it in 5 of 5 trials (an even
# bevel); through Angelo Opus never did (a stretched bevel) and Sol spent a lookup and an exec round on
# transform_apply in 7 of 10 (recorded runs). One option lets either do it in the recipe's step.
APPLY_SCALE_NOTE = "Bevel width is in object space; apply_scale bakes the object's own scale first, for an even bevel."
UNIT_SCALE_TOLERANCE = 0.000001
# The only modifier type apply_scale bakes under. Catmull-Clark and simple subdivision place every new vertex at
# an affine combination (weights summing to one) of the cage's vertices, and creases are dimensionless, so
# subdividing the baked mesh is baking the subdivided one. Every other type changes its world result once the
# scale is baked: it measures in object space (Solidify thickness, an Array's constant offset, Bevel width,
# Displace strength, merge distances, Remesh voxel size, Screw offset), refers to another object's space (Hook
# and its stored inverse, Boolean, Armature, Curve, Lattice, Shrinkwrap, a Mirror object), keeps object-space data
# (Multires displacement, Mesh/Surface/Laplacian Deform bindings), depends on angles or areas a non-uniform scale
# changes (Edge Split, Weighted Normal, Triangulate, Decimate, Corrective Smooth) or runs any graph (Geometry
# Nodes). Without this list Solidify, Array, Hook and Multires were baked under and certified.
SCALE_INVARIANT_MODIFIERS = frozenset({"SUBSURF"})
RECIPE_SCHEMAS = {
    "bevel-and-shade": _schema({"width": _number(0.000001, 100), "segments": _integer(1, 8),
                                 "modifier_name": LABEL, "shade_modifier_name": LABEL, "tolerance": _tolerance,
                                 "apply_scale": {"type": "boolean", "description": APPLY_SCALE_NOTE}},
                                ["width", "segments"]),
    # The scale bounds default to the widest a transform takes (FIT_BOUNDS) and the approval shows them with the
    # other inputs: in recorded runs all 108 fit-dimensions calls stated exactly those.
    "fit-dimensions": _schema({"dimensions": _dimension_vector, "tolerance": _tolerance,
                                "minimum_scale": _number(0.000001, 1000), "maximum_scale": _number(0.000001, 1000),
                                "maximum_scale_ratio": _number(1, 100)}, ["dimensions"]),
    "clean-mesh": _schema({"vertex_indices": _indices, "distance": _number(0.000001, 1),
                            "face_indices": _faces, "max_degenerate_faces": _integer(0, 200000),
                            "max_zero_length_edges": _integer(0, 200000), "epsilon": _number(0, 0.01),
                            "manifold": _enum("IGNORE", "CLOSED", "OPEN")}, ["vertex_indices", "distance"]),
    "material-setup": _schema({"name": LABEL, "base_color": _color, "roughness": _number(0, 1),
                                "metallic": _number(0, 1), "tolerance": _tolerance}, ["name", "base_color"]),
    # Location and rotation keys: a scale key is bounded as a transform's scale (keyframe_insert), and a second,
    # scale variant of this recipe would cost every plan and declaration 0.4-1.4 KB.
    "simple-animation": _schema({"property": _enum("location", "rotation_euler"),
                                  "start_frame": _integer(-10000, 100000), "end_frame": _integer(-10000, 100000),
                                  "start_value": VECTOR, "end_value": VECTOR,
                                  "interpolation": _enum("LINEAR", "BEZIER", "CONSTANT"), "tolerance": _tolerance},
                                 ["property", "start_frame", "end_frame", "start_value", "end_value"]),
    "uv-prep": _schema({"method": _enum("PLANAR", "CUBE", "SPHERICAL"), "axis": _enum("X", "Y", "Z"),
                         "name": LABEL, "scale": _number(0.000001, 1000)}, ["method"]),
}
# fit-dimensions' scale bounds when a call leaves them out: the schema's own limits, which equal a transform's
# scale limit (1,000) and the ratio's ceiling, so the default refuses exactly what no stated bound could allow.
FIT_BOUNDS = {"minimum_scale": 0.000001, "maximum_scale": 1000, "maximum_scale_ratio": 100}
DEFAULTS = {
    "bevel-and-shade": {"modifier_name": "Angelo Recipe Bevel", "shade_modifier_name": "Angelo Recipe Shade", "tolerance": 0.00001,
                        "apply_scale": False},
    "fit-dimensions": {"tolerance": 0.0001, **FIT_BOUNDS},
    "clean-mesh": {"face_indices": [], "max_degenerate_faces": 0, "max_zero_length_edges": 0,
                   "epsilon": 0.00000001, "manifold": "IGNORE"},
    "material-setup": {"roughness": 0.5, "metallic": 0, "tolerance": 0.00001},
    "simple-animation": {"interpolation": "LINEAR", "tolerance": 0.00001},
    "uv-prep": {"axis": "Z", "name": "UVMap", "scale": 1},
}
_DESCRIPTIONS = {
    "bevel-and-shade": "Add a bounded non-destructive Bevel and an exact three-node face smooth-shading graph. Never applies/removes modifiers. Refuses an object that already has a Bevel modifier: change that one with modifier_configure. " + APPLY_SCALE_NOTE,
    "fit-dimensions": "One analytic positive scale solve for an unconstrained, unparented, axis-aligned mesh. Reject out-of-bound corrections before approval; no retries.",
    "clean-mesh": "Merge only the explicitly listed vertices within the approved distance, then recalculate the listed/all face normals. Topology changes are explicit; no delete/dissolve/remesh/UV cleanup.",
    "material-setup": "Create and assign one simple Principled node material to every base-mesh face. Existing shader graphs are not rewritten or cleaned up.",
    "simple-animation": "Insert two explicit transform keyframes into a private staged action. Verify direct channel values only; no NLA, drivers or constrained animation evaluator.",
    "uv-prep": "Project the complete base mesh to the explicitly named UV layer using a closed bounded native projection. Existing UV data is only changed by this explicit operation.",
}
_KINDS = {
    "bevel-and-shade": ["transform_apply", "modifier_add", "geometry_nodes"], "fit-dimensions": ["transform"],
    "clean-mesh": ["mesh_merge_by_distance", "mesh_recalculate_normals"],
    "material-setup": ["material_principled"], "simple-animation": ["keyframe_insert", "keyframe_insert"],
    "uv-prep": ["uv_project"],
}


def _bounds(schema):
    items = schema.get("items", {}) if schema.get("type") == "array" else schema
    if "minimum" not in items or "maximum" not in items:
        return None
    text = "%g..%g" % (items["minimum"], items["maximum"])
    if schema.get("type") != "array":
        return text
    size = schema.get("minItems") if schema.get("minItems") == schema.get("maxItems") else ""
    return "[%s] %s" % (size, text)


def parameter_ranges():
    """Every recipe's numeric parameter bounds, from RECIPE_SCHEMAS, as one line of text.

    Codex's code-mode declarations drop bounds (doc/angelo/gpt_launch.rst), so
    a model guessed fit-dimensions' maximum_scale_ratio past 100 in recorded
    GPT runs; ``tool_specs.codex_tools`` shows this line in the recipe tool's
    description instead.
    """
    parts = []
    for recipe, schema in RECIPE_SCHEMAS.items():
        fields = ["%s%s" % (name, (" " if not bounds.startswith("[") else "") + bounds)
                  for name, child in schema["properties"].items() if (bounds := _bounds(child))]
        if fields:
            parts.append("%s %s" % (recipe, ", ".join(fields)))
    return "Parameter ranges (inclusive): " + "; ".join(parts) + "."


def validate_parameters(recipe, parameters):
    if type(recipe) is not str or recipe not in RECIPE_SCHEMAS:
        raise RefusalError("Choose a documented native recipe", code="VALIDATION")
    if type(parameters) is not dict:
        raise RefusalError("Recipe parameters must be a closed object", code="VALIDATION")
    result = validate_value({**copy.deepcopy(DEFAULTS[recipe]), **parameters}, RECIPE_SCHEMAS[recipe], "recipe.parameters")
    if recipe == "fit-dimensions" and result["minimum_scale"] > result["maximum_scale"]:
        raise RefusalError("Approved minimum scale cannot exceed maximum scale", code="VALIDATION")
    if recipe == "simple-animation" and result["start_frame"] >= result["end_frame"]:
        raise RefusalError("Animation start frame must precede the end frame", code="VALIDATION")
    if recipe == "bevel-and-shade" and result["modifier_name"] == result["shade_modifier_name"]:
        raise RefusalError("Bevel and smooth-shading modifier names must be distinct", code="VALIDATION")
    return result


def _action(kind, target, parameters):
    return validate_operation({"kind": kind, "target": target, "parameters": parameters})


def _finite_check(target):
    return {"kind": "finite_coordinates", "target": target}


def _fit_scale(target, parameters, observation):
    if type(observation) is not dict or observation.get("target") != target or observation.get("status") != "pass":
        raise RefusalError("Fit dimensions requires a complete fresh bound target observation", code="VALIDATION")
    if observation.get("type") != "MESH" or observation.get("parent_name") is not None or observation.get("constraints_count") != 0:
        raise RefusalError("Fit dimensions supports only an unparented, unconstrained base mesh", code="UNSUPPORTED")
    transforms = observation.get("transforms", {})
    scale = transforms.get("scale")
    dimensions = observation.get("mesh", {}).get("dimensions_world")
    for values in (scale, dimensions):
        if type(values) is not list or len(values) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise RefusalError("Fit dimensions needs complete finite transform and dimension measurements", code="VALIDATION")
    basis = observation.get("effective_linear_transform")
    if type(basis) is not list or len(basis) != 3 or any(
            type(row) is not list or len(row) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
            for row in basis):
        raise RefusalError("Fit dimensions requires an observed effective world basis, including active rotation and delta channels", code="VALIDATION")
    if any(v <= 0 for v in scale + dimensions) or any(abs(basis[i][i]) < .000001 for i in range(3)) or any(
            abs(basis[i][j]) > 1e-7 * abs(basis[j][j]) for i in range(3) for j in range(3) if i != j):
        raise RefusalError("Fit dimensions requires positive dimensions/scale and an effectively axis-aligned world basis", code="UNSUPPORTED")
    result = [old * desired / current for old, desired, current in zip(scale, parameters["dimensions"], dimensions)]
    if any(not math.isfinite(v) or not parameters["minimum_scale"] <= v <= parameters["maximum_scale"] for v in result):
        raise RefusalError("Required scale correction exceeds the approved scale bounds", code="VALIDATION")
    if any(max(new / old, old / new) > parameters["maximum_scale_ratio"] for old, new in zip(scale, result)):
        raise RefusalError("Required scale correction exceeds the approved ratio", code="VALIDATION")
    return result


def _scale_operations(target, observation):
    """The transform_apply that bakes the target's own scale before its bevel: [] at unit scale, or a refusal.

    ``observation`` holds ``scale_facts`` (``scale_facts`` below, read on the main thread). apply_scale can make
    the bevel even only when the object's own scale is all that stretches it, so a delta scale, a parent's (or
    bone's) scale and a constraint are refused whatever the own scale is: baking nothing, or only the own
    scale, would certify a bevel that is still stretched. Baking is then refused where transform_apply refuses
    (children, delta transforms, anything but a mesh) and where it would change the world result: a scale
    keyed, driven or in NLA (the next frame scales the baked mesh again), a modifier other than subdivision
    (``SCALE_INVARIANT_MODIFIERS``) and a zero axis (it would flatten the mesh). Otherwise the world shape is
    exactly the same: a mirrored axis is baked with the faces flipped and the rotation channels kept, as
    Blender's Apply Scale does (transform_bake), and a mesh other objects share is copied for this object at
    commit while the others keep theirs.
    """
    facts = observation.get("scale_facts") if type(observation) is dict and observation.get("target") == target \
        and observation.get("status") == "pass" else None
    if type(facts) is not dict:
        raise RefusalError("bevel-and-shade apply_scale needs the target's fresh transform facts", code="VALIDATION")
    scale = facts.get("scale")
    if (type(scale) is not list or len(scale) != 3
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in scale)):
        raise RefusalError("bevel-and-shade apply_scale needs the target's finite scale", code="VALIDATION")
    # In this order: a constraint's scale also shows in the world matrix outside_scale reads.
    elsewhere = (("constraints", "UNSUPPORTED", "CONSTRAINED: a constraint may set the scale"),
                 ("delta_scale", "UNSUPPORTED", "DELTA_TRANSFORMS: a delta scale stretches the bevel too, and delta "
                  "transforms are reviewed Python"),
                 ("outside_scale", "UNSUPPORTED", "PARENT_SCALE: the parent (or a bone) scales this object as well, "
                  "which its own scale cannot undo; apply the parent's scale first"))
    unit = all(abs(value - 1) <= UNIT_SCALE_TOLERANCE for value in scale)
    modifiers = facts.get("modifiers")
    baking = (("type", lambda value: value != "MESH", "VALIDATION", "NOT_A_MESH: only a mesh's scale can be applied"),
              ("children", bool, "VALIDATION", "CHILDREN: applying the scale would move child objects; unparent them first"),
              ("delta_transforms", bool, "UNSUPPORTED", "DELTA_TRANSFORMS: delta transforms are reviewed Python"),
              ("animated_scale", bool, "UNSUPPORTED", "ANIMATED_SCALE: the scale is keyed, driven or in NLA, so the "
               "next frame would scale the applied mesh again"),
              ("modifiers", lambda value: type(value) is not list or set(value) - SCALE_INVARIANT_MODIFIERS, "UNSUPPORTED",
               "MODIFIERS: baking the scale would change what %s make (they measure in object space or depend on "
               "its shape; only Subdivision Surface is unaffected)" % ", ".join(
                   sorted(set(modifiers) - SCALE_INVARIANT_MODIFIERS) if type(modifiers) is list else ["its modifiers"])))
    refusals = [(field, bool, code, reason) for field, code, reason in elsewhere] + ([] if unit else list(baking))
    for field, refused, code, reason in refusals:
        if refused(facts.get(field)):
            raise RefusalError("bevel-and-shade apply_scale %s. Otherwise bevel without apply_scale" % reason, code=code)
    if unit:
        return []
    if any(abs(value) <= UNIT_SCALE_TOLERANCE for value in scale):
        raise RefusalError("bevel-and-shade apply_scale ZERO_SCALE: a zero scale axis would flatten the mesh",
                           code="VALIDATION")
    return [_action("transform_apply", target, {"scale": True})]


def _outside_scale(obj):
    """Whether something outside the object's own channels (a parent, a bone) scales or shears it.

    ``matrix_world`` times the inverse of ``matrix_basis`` (deltas included) is what the object's parenting does
    to it; a constraint is refused on its own. A singular basis (a zero own scale axis) is refused as ZERO_SCALE.
    """
    from .transform_bake import inverse, is_rotation, multiply
    world = [[float(value) for value in list(row)[:3]] for row in list(obj.matrix_world)[:3]]
    basis = [[float(value) for value in list(row)[:3]] for row in list(obj.matrix_basis)[:3]]
    try:
        return not is_rotation(multiply(world, inverse(basis)))
    except ValueError:
        return False


def scale_facts(obj):
    """What ``_scale_operations`` decides by, read from ``obj`` (main thread; attribute reads only)."""
    from .anim_channels import channelbag
    animation = getattr(obj, "animation_data", None)
    keyed = False
    if animation is not None:
        bag = channelbag(obj)
        paths = [curve.data_path for curve in (bag.fcurves if bag is not None else ())]
        paths += [driver.data_path for driver in animation.drivers]
        keyed = any(path in ("scale", "delta_scale") for path in paths) or len(animation.nla_tracks) > 0
    deltas = (list(obj.delta_location), list(obj.delta_rotation_euler), list(obj.delta_scale),
              list(obj.delta_rotation_quaternion))
    return {"scale": [float(value) for value in obj.scale], "type": obj.type, "children": len(obj.children),
            "delta_transforms": deltas != ([0, 0, 0], [0, 0, 0], [1, 1, 1], [1, 0, 0, 0]),
            "delta_scale": deltas[2] != [1, 1, 1], "outside_scale": _outside_scale(obj),
            "animated_scale": keyed, "constraints": len(obj.constraints),
            "modifiers": [modifier.type for modifier in obj.modifiers]}


def expand_recipe(recipe, target, parameters, observation=None):
    """Pure exact expansion. Returned defaults/bounds must be shown in approval."""
    params = validate_parameters(recipe, parameters)
    # Validate reference syntax using the same catalog used by every operation.
    validate_operation({"kind": "transform", "target": target, "parameters": {"location": [0, 0, 0]}})
    actions, checks = [], [_finite_check(target)]
    if recipe == "bevel-and-shade":
        if params["apply_scale"]:
            actions += _scale_operations(target, observation)
        if actions:  # The scale is baked into the mesh: the object reads one, its shape in the world unchanged.
            checks.append({"kind": "transform", "target": target, "property": "scale", "expected": [1, 1, 1],
                           "tolerance": params["tolerance"]})
        actions.append(_action("modifier_add", target, {"type": "BEVEL", "name": params["modifier_name"],
                                                         "settings": {"width": params["width"], "segments": params["segments"]}}))
        actions.append(_action("geometry_nodes", target, {"name": params["shade_modifier_name"], "nodes": [
            {"id": "input", "type": "GROUP_INPUT"},
            {"id": "smooth", "type": "SET_SHADE_SMOOTH", "inputs": {"Selection": True, "Shade Smooth": True}, "settings": {"domain": "FACE"}},
            {"id": "output", "type": "GROUP_OUTPUT"}], "links": [
                {"from_node": "input", "from_socket": "Geometry", "to_node": "smooth", "to_socket": "Geometry"},
                {"from_node": "smooth", "from_socket": "Geometry", "to_node": "output", "to_socket": "Geometry"}]}))
        checks += [{"kind": "modifier", "target": target, "name": params["modifier_name"], "type": "BEVEL",
                    "values": {"width": params["width"], "segments": params["segments"], "show_viewport": True, "show_render": True}, "tolerance": params["tolerance"]},
                   {"kind": "smooth_shading_graph", "target": target, "modifier": params["shade_modifier_name"], "expected": True}]
    elif recipe == "fit-dimensions":
        actions = [_action("transform", target, {"scale": _fit_scale(target, params, observation)})]
        checks.append({"kind": "dimensions", "target": target, "expected": params["dimensions"], "tolerance": params["tolerance"], "space": "WORLD"})
    elif recipe == "clean-mesh":
        normals = {"face_indices": params["face_indices"]}
        if params["face_indices"]:
            if type(observation) is not dict or observation.get("target") != target or observation.get("status") != "pass":
                raise RefusalError("Explicit clean-mesh faces require a fresh read-only merge-lineage plan", code="VALIDATION")
            plan = observation.get("clean_mesh_face_plan")
            if (type(plan) is not dict or set(plan) != {"face_indices", "topology_digest"}
                    or type(plan["face_indices"]) is not list or len(plan["face_indices"]) != len(params["face_indices"])):
                raise RefusalError("Explicit clean-mesh faces require an exact surviving-face plan", code="VALIDATION")
            normals = {**plan, "index_scope": "CURRENT", "topology_step": 1}
        actions = [_action("mesh_merge_by_distance", target, {"vertex_indices": params["vertex_indices"], "distance": params["distance"]}),
                   _action("mesh_recalculate_normals", target, normals)]
        checks.append({"kind": "mesh_degenerate", "target": target, "max_faces": params["max_degenerate_faces"],
                       "max_edges": params["max_zero_length_edges"], "epsilon": params["epsilon"]})
        if params["manifold"] != "IGNORE":
            checks.append({"kind": "mesh_manifold", "target": target, "expected": params["manifold"]})
    elif recipe == "material-setup":
        actions = [_action("material_principled", target, {"name": params["name"], "inputs": {
            "Base Color": params["base_color"], "Roughness": params["roughness"], "Metallic": params["metallic"]}})]
        checks += [{"kind": "material_assignment", "target": target, "material": params["name"], "all_faces": True},
                   {"kind": "material_values", "target": target, "material": params["name"], "base_color": params["base_color"],
                    "roughness": params["roughness"], "metallic": params["metallic"], "tolerance": params["tolerance"]}]
    elif recipe == "simple-animation":
        actions = [_action("keyframe_insert", target, {"property": params["property"], "value": params[key + "_value"],
                                                       "frame": params[key + "_frame"], "interpolation": params["interpolation"]})
                   for key in ("start", "end")]
        checks.append({"kind": "animation", "target": target, "property": params["property"], "samples": [
            {"frame": params[key + "_frame"], "expected": params[key + "_value"]} for key in ("start", "end")],
            "tolerance": params["tolerance"], "require_keyframes": True})
    elif recipe == "uv-prep":
        actions = [_action("uv_project", target, {"method": params["method"], "axis": params["axis"], "uv_map": params["name"], "scale": params["scale"]})]
        checks.append({"kind": "uv_layer", "target": target, "name": params["name"], "require_nonempty": True, "require_finite": True})
    result = {"recipe": recipe, "target": target, "parameters": params, "operations": actions,
              "expected_checks": validate_checks(checks), "requires_program_approval": True,
              "correction_policy": {"strategy": "SINGLE_ANALYTIC_SCALE_SOLVE" if recipe == "fit-dimensions" else "NONE",
                                    "automatic_retries": 0, "post_failure_mutations": False},
              "description": _DESCRIPTIONS[recipe]}
    if observation is not None and "revision" in observation:
        result["source_revision"] = observation["revision"]
    return copy.deepcopy(result)


def recipe_capabilities():
    return {"version": 1, "recipes": [{"name": name, "description": _DESCRIPTIONS[name],
                                       "parameters": copy.deepcopy(schema), "defaults": copy.deepcopy(DEFAULTS[name]),
                                       "expanded_kinds": list(_KINDS[name]), "automatic_retries": 0,
                                       "needs_fresh_observation": name == "fit-dimensions"}
                                      for name, schema in RECIPE_SCHEMAS.items()],
            "execution": ("angelo_apply_recipe expands and stages one approved program in a single call; "
                          "angelo_expand_recipe only reads an expansion to review or extend it in an angelo_scene_program")}


_HANDLE = {"type": "string", "pattern": "^scn:[a-f0-9]{32}:[1-9][0-9]{0,19}$", "maxLength": 57}
_REVISION = {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64}
_MODE = _enum("APPLY", "PREVIEW")
EXPAND_TOOL, APPLY_TOOL = "angelo_expand_recipe", "angelo_apply_recipe"


def _recipe_input_schema(extra=None, required=()):
    """One closed variant per recipe, so parameters are checked against their recipe."""
    extra = extra or {}
    fields = ["recipe", "target", "revision", "parameters", *required]
    return {**_schema({"recipe": _enum(*RECIPE_SCHEMAS), "target": _HANDLE, "revision": _REVISION,
                       "parameters": {"oneOf": list(RECIPE_SCHEMAS.values())}, **extra}, fields),
            "oneOf": [_schema({"recipe": {"const": name}, "target": _HANDLE, "revision": _REVISION,
                               "parameters": schema, **extra}, fields)
                      for name, schema in RECIPE_SCHEMAS.items()]}


RECIPE_TOOLS = [{
    "name": EXPAND_TOOL,
    "description": "Read-only exact expansion of a closed recipe into canonical native actions/checks. "
                   "Bind the target by its observed handle and revision. Show the expanded operations before "
                   "requesting normal staged-program approval; expansion never executes, retries or cleans up. "
                   "Fit dimensions uses one analytic correction within approved scale/ratio bounds. "
                   "To apply a recipe without reviewing it first, use angelo_apply_recipe.",
    "inputSchema": _recipe_input_schema(),
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}, {
    "name": APPLY_TOOL,
    "description": "Expand a closed recipe on the host and stage the result as one angelo_scene_program (APPLY "
                   "commits one Undo step; PREVIEW stages without live edits) in a single call. The host expands "
                   "it against the exact observed revision before approval; the approval shows every expanded "
                   "operation and check exactly as for angelo_scene_program, and only that reviewed program is "
                   "staged. The result is the program's result, including its checks and live_revisions.",
    "inputSchema": _recipe_input_schema({"mode": _MODE, RECOVERY_FIELD: CITATION_SCHEMA}, ["mode"]),
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
}]
RECIPE_NAMES = frozenset(t["name"] for t in RECIPE_TOOLS)


def validate_recipe(name, arguments):
    fields = {"recipe", "target", "revision", "parameters"} | ({"mode"} if name == APPLY_TOOL else set())
    if name not in RECIPE_NAMES or type(arguments) is not dict or set(arguments) - fields - ({RECOVERY_FIELD} if name == APPLY_TOOL else set()) or fields - set(arguments):
        raise RefusalError("A recipe call requires recipe, target, revision and closed parameters"
                         + (", and mode APPLY or PREVIEW" if name == APPLY_TOOL else ""), code="VALIDATION")
    target, revision = arguments["target"], arguments["revision"]
    if type(target) is not str or re.fullmatch(r"scn:[a-f0-9]{32}:[1-9][0-9]{0,19}", target) is None:
        raise RefusalError("Use an exact observed document handle, never a name or alias", code="VALIDATION")
    if type(revision) is not str or re.fullmatch(r"[a-f0-9]{64}", revision) is None:
        raise RefusalError("Use the latest exact object_revision", code="VALIDATION")
    result = {"recipe": arguments["recipe"], "target": target, "revision": revision,
              "parameters": validate_parameters(arguments["recipe"], arguments["parameters"])}
    if name == APPLY_TOOL:
        if type(arguments["mode"]) is not str or arguments["mode"] not in {"APPLY", "PREVIEW"}:
            raise RefusalError("A recipe program mode is APPLY or PREVIEW", code="VALIDATION")
        result["mode"] = arguments["mode"]
        if RECOVERY_FIELD in arguments:
            result[RECOVERY_FIELD] = citation(arguments[RECOVERY_FIELD])
    return result


def program_for(window, arguments):
    """The exact canonical program ``angelo_apply_recipe`` stages.

    The same read-only, revision-bound expansion as ``angelo_expand_recipe``,
    with the requested mode, normalized by the program contract. Call on the
    main thread. Returns a new deep copy; the caller reviews and stages it.
    """
    from .program_spec import validate_program
    args = validate_recipe(APPLY_TOOL, arguments)
    expansion = execute(window, EXPAND_TOOL, {key: args[key] for key in ("recipe", "target", "revision", "parameters")})
    return validate_program("angelo_scene_program", dict(expansion["program"], mode=args["mode"]))


def _refuse_second_bevel(obj):
    """bevel-and-shade adds a Bevel; on an object that has one, that is almost never the request.

    In a recorded long run a rebased context asked to *change*
    the cube's bevel applied this recipe, which stacked a second Bevel and a
    second shading graph; the host's own checks passed and the result was
    wrong. The refusal names the existing modifiers so one corrected plan can
    configure them; a deliberate second bevel stays possible with modifier_add.
    """
    existing = [modifier.name for modifier in obj.modifiers if modifier.type == "BEVEL"]
    if existing:
        names = ", ".join(repr(name[:64]) for name in existing[:8])
        raise RefusalError("The target already has a Bevel modifier (%s): bevel-and-shade adds another. To change it, "
                         "submit an angelo_scene_program modifier_configure of %s (type BEVEL); add a second bevel "
                         "only with modifier_add" % (names, repr(existing[0][:64])), code="VALIDATION")


def execute(window, name, arguments):
    if name != EXPAND_TOOL:
        raise RefusalError("angelo_apply_recipe stages through an approved native program job, never directly", code="INTERNAL_ERROR")
    from .scene_queries import object_revision, resolve_object
    from .scene_validation import VerificationContext, observe
    args = validate_recipe(name, arguments)
    target = args["target"]
    obj = resolve_object(window, target)
    if object_revision(window, obj) != args["revision"]:
        raise RefusalError("Recipe target changed since observation; inspect it again before expanding",
                           code="STALE_REVISION", details={"handles": [target]})
    if args["recipe"] == "bevel-and-shade":
        _refuse_second_bevel(obj)
    observation = None
    if args["recipe"] == "bevel-and-shade" and args["parameters"]["apply_scale"]:
        observation = {"target": target, "status": "pass", "revision": args["revision"], "scale_facts": scale_facts(obj)}
    elif args["recipe"] == "fit-dimensions":
        observation = observe(VerificationContext({target: obj}, bindings={target: {"handle": target, "revision": args["revision"]}}), target)
    elif args["recipe"] == "clean-mesh" and args["parameters"]["face_indices"]:
        from .action_recipe_selection import plan_clean_mesh_faces
        observation = {"target": target, "status": "pass", "revision": args["revision"],
                       "clean_mesh_face_plan": plan_clean_mesh_faces(obj, args["parameters"])}
    result = expand_recipe(args["recipe"], target, args["parameters"], observation)
    if object_revision(window, obj) != args["revision"]:
        raise RefusalError("Recipe target changed while expanding; inspect it again",
                           code="STALE_REVISION", details={"handles": [target]})
    result["program"] = {"mode": "APPLY", "read_set": [{"handle": target, "revision": args["revision"]}],
                         "write_set": [{"handle": target, "revision": args["revision"]}],
                         "operations": copy.deepcopy(result["operations"]), "expected_checks": copy.deepcopy(result["expected_checks"])}
    result.update(success=True, scene_changed=False, read_only=True)
    return result


catalog = recipe_capabilities
