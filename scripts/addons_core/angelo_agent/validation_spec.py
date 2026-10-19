# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed, Blender-free deterministic scene-check schemas.

Targets are aliases in an already bound execution/observation context, never
expressions, selectors, paths or executable code. Verification is fail closed:
unsupported observations are inconclusive, not successful assertions.
"""

import copy
import math
import re

from .action_camera_light_spec import CAMERA_SETTINGS, LIGHT_SETTINGS
from .action_properties import references as _property_references, PROPERTY_SET_SCHEMA as _PROPERTY_SET
from .action_world_spec import WORLD_CONFIGURE_SCHEMA as _WORLD_VALUES
from .action_schema_types import NULLABLE_POINTER, POINTER, stored_pointer
from .check_defaults import complete as _complete


def _property_values():
    return _PROPERTY_SET["properties"]["values"]

MAX_CHECKS = 32
MAX_OBJECTS = 2048
MAX_VERTICES = 250000
MAX_MESH_ELEMENTS = 750000
MAX_TOTAL_ELEMENTS = 1000000
MAX_MODIFIERS = 32
MAX_MATERIALS = 32
MAX_UV_LAYERS = 16
MAX_BONES = 4096
MAX_ANIMATION_CURVES = 96
MAX_KEYFRAMES = 8192

LABEL = {"type": "string", "minLength": 1, "maxLength": 64}
TARGET = {"type": "string", "minLength": 1, "maxLength": 128}
OBJECT_TYPES = ("MESH", "CURVE", "SURFACE", "META", "FONT", "ARMATURE", "LATTICE", "EMPTY", "CAMERA", "LIGHT", "LIGHT_PROBE", "SPEAKER", "VOLUME", "POINTCLOUD", "CURVES", "GREASEPENCIL")
NUMBER = {"type": "number", "minimum": -100000, "maximum": 100000}
POSITIVE = {"type": "number", "minimum": 0.000001, "maximum": 10000}
TOLERANCE = {"type": "number", "minimum": 0, "maximum": 100}
VECTOR = {"type": "array", "items": NUMBER, "minItems": 3, "maxItems": 3}
DIMENSIONS = {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 10000}, "minItems": 3, "maxItems": 3}
BOOL = {"type": "boolean"}


def _integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


def _check(kind, properties, required=None):
    """A check variant; ``required`` defaults to every property but ``tolerance`` (check_defaults)."""
    values = {"kind": {"const": kind}, **properties}
    required = [name for name in properties if name != "tolerance"] if required is None else required
    return {"type": "object", "properties": values, "required": ["kind", *required], "additionalProperties": False}


# This is intentionally a tiny property allowlist, not arbitrary RNA traversal.
MODIFIER_PROPERTIES = {
    "BEVEL": {"width": {"type": "number", "minimum": 0, "maximum": 100},
              "segments": _integer(1, 8), "limit_method": _enum("NONE", "ANGLE", "WEIGHT", "VGROUP"),
              "angle_limit": {"type": "number", "minimum": 0, "maximum": math.pi},
              "affect": _enum("EDGES", "VERTICES"), "use_clamp_overlap": BOOL},
    "SUBSURF": {"levels": _integer(0, 3), "render_levels": _integer(0, 6),
                "subdivision_type": _enum("CATMULL_CLARK", "SIMPLE")},
    "SOLIDIFY": {"thickness": {"type": "number", "minimum": -100, "maximum": 100},
                 "offset": {"type": "number", "minimum": -1, "maximum": 1}, "use_even_offset": BOOL},
    "ARRAY": {"count": _integer(1, 1024), "use_relative_offset": BOOL, "use_constant_offset": BOOL,
              "relative_offset_displace": VECTOR, "constant_offset_displace": VECTOR, "use_merge_vertices": BOOL,
              "merge_threshold": {"type": "number", "minimum": 0, "maximum": 1}},
    "MIRROR": {"use_axis": {"type": "array", "items": BOOL, "minItems": 3, "maxItems": 3},
               "use_clip": BOOL, "use_mirror_merge": BOOL,
               "merge_threshold": {"type": "number", "minimum": 0, "maximum": 1}},
    "DECIMATE": {"decimate_type": _enum("COLLAPSE", "UNSUBDIV", "DISSOLVE"),
                 "ratio": {"type": "number", "minimum": 0.0001, "maximum": 1}, "iterations": _integer(0, 32),
                 "angle_limit": {"type": "number", "minimum": 0, "maximum": math.pi}, "use_symmetry": BOOL,
                 "symmetry_axis": _enum("X", "Y", "Z")},
    "WEIGHTED_NORMAL": {"keep_sharp": BOOL, "weight": _integer(1, 100),
                        "mode": _enum("FACE_AREA", "CORNER_ANGLE", "FACE_AREA_WITH_ANGLE"),
                        "thresh": {"type": "number", "minimum": 0, "maximum": 10}, "use_face_influence": BOOL},
    "TRIANGULATE": {"quad_method": _enum("BEAUTY", "FIXED", "FIXED_ALTERNATE", "SHORTEST_DIAGONAL", "LONGEST_DIAGONAL"),
                    "ngon_method": _enum("BEAUTY", "CLIP"), "min_vertices": _integer(4, 255), "keep_custom_normals": BOOL},
    "ARMATURE": {"use_deform_preserve_volume": BOOL, "use_vertex_groups": BOOL},
    "SHRINKWRAP": {"wrap_method": _enum("NEAREST_SURFACEPOINT", "PROJECT", "NEAREST_VERTEX", "TARGET_PROJECT"),
                   "wrap_mode": _enum("ON_SURFACE", "INSIDE", "OUTSIDE", "OUTSIDE_SURFACE", "ABOVE_SURFACE"),
                   "offset": {"type": "number", "minimum": -100, "maximum": 100}},
    "DISPLACE": {"strength": {"type": "number", "minimum": -100, "maximum": 100},
                 "mid_level": {"type": "number", "minimum": 0, "maximum": 1},
                 "direction": _enum("X", "Y", "Z", "NORMAL", "CUSTOM_NORMAL", "RGB_TO_XYZ"), "space": _enum("LOCAL", "GLOBAL")},
    "SCREW": {"angle": {"type": "number", "minimum": -4 * math.pi, "maximum": 4 * math.pi}, "steps": _integer(1, 10000),
              "render_steps": _integer(1, 10000), "screw_offset": {"type": "number", "minimum": -100, "maximum": 100},
              "iterations": _integer(1, 10000), "axis": _enum("X", "Y", "Z"), "use_merge_vertices": BOOL,
              "merge_threshold": {"type": "number", "minimum": 0, "maximum": 1}, "use_normal_calculate": BOOL,
              "use_normal_flip": BOOL, "use_smooth_shade": BOOL},
    "CURVE": {"deform_axis": _enum("POS_X", "POS_Y", "POS_Z", "NEG_X", "NEG_Y", "NEG_Z")},
    "BOOLEAN": {"operation": _enum("DIFFERENCE", "UNION", "INTERSECT"), "solver": _enum("EXACT", "FLOAT", "MANIFOLD"),
                "use_self": BOOL, "use_hole_tolerant": BOOL},
    "REMESH": {"mode": _enum("VOXEL", "SMOOTH", "SHARP", "BLOCKS"),
               "voxel_size": {"type": "number", "minimum": 0, "maximum": 100}, "octree_depth": _integer(1, 24),
               "scale": {"type": "number", "minimum": 0, "maximum": 1}, "use_remove_disconnected": BOOL,
               "use_smooth_shade": BOOL},
}
for _properties in MODIFIER_PROPERTIES.values():
    _properties.update(show_viewport=BOOL, show_render=BOOL)

CHECK_VARIANTS = [
    _check("dimensions", {"target": TARGET, "expected": DIMENSIONS, "tolerance": TOLERANCE,
                          "space": _enum("WORLD", "LOCAL")}, ["target", "expected"]),
    _check("transform", {"target": TARGET, "property": _enum("location", "rotation_euler", "scale"),
                         "expected": VECTOR, "tolerance": TOLERANCE}),
    _check("object_count", {"minimum": _integer(0, MAX_OBJECTS), "maximum": _integer(0, MAX_OBJECTS),
                            "types": {"type": "array", "items": _enum(*OBJECT_TYPES), "maxItems": len(OBJECT_TYPES), "uniqueItems": True}},
           ["minimum", "maximum"]),
    _check("object_type", {"target": TARGET, "expected": _enum(*OBJECT_TYPES)}),
    _check("object_name", {"target": TARGET, "expected": LABEL}),
    _check("finite_coordinates", {"target": TARGET}),
    _check("mesh_counts", {"target": TARGET, **{key: {
        "type": "object", "properties": {"minimum": _integer(0, limit), "maximum": _integer(0, limit)},
        "required": ["minimum", "maximum"], "additionalProperties": False}
        for key, limit in (("vertices", MAX_VERTICES), ("edges", MAX_MESH_ELEMENTS), ("faces", MAX_MESH_ELEMENTS))}}),
    _check("mesh_degenerate", {"target": TARGET, "max_faces": _integer(0, MAX_MESH_ELEMENTS),
                               "max_edges": _integer(0, MAX_MESH_ELEMENTS),
                               "epsilon": {"type": "number", "minimum": 0, "maximum": 0.01}}),
    _check("mesh_manifold", {"target": TARGET, "expected": _enum("CLOSED", "OPEN")}),
    _check("material_assignment", {"target": TARGET, "material": LABEL, "all_faces": BOOL}),
    _check("material_values", {"target": TARGET, "material": LABEL,
                               "base_color": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 4, "maxItems": 4},
                               "roughness": {"type": "number", "minimum": 0, "maximum": 1},
                               "metallic": {"type": "number", "minimum": 0, "maximum": 1}, "tolerance": TOLERANCE},
           ["target", "material"]),
    _check("uv_layer", {"target": TARGET, "name": LABEL, "require_nonempty": BOOL, "require_finite": BOOL},
           ["target", "require_nonempty", "require_finite"]),
    _check("smooth_shading_graph", {"target": TARGET, "modifier": LABEL, "expected": BOOL}),
    _check("parent", {"target": TARGET, "expected": NULLABLE_POINTER,
                      "parent_type": _enum("OBJECT", "BONE"), "bone": LABEL}, ["target", "expected"]),
    _check("rig", {"target": TARGET, "minimum_bones": _integer(1, MAX_BONES), "maximum_bones": _integer(1, MAX_BONES),
                   "expected_bones": {"type": "array", "items": LABEL, "maxItems": 64, "uniqueItems": True},
                   "require_finite": BOOL}, ["target", "minimum_bones", "maximum_bones", "require_finite"]),
    _check("armature_binding", {"target": TARGET, "armature": POINTER, "modifier": LABEL,
                                "every_vertex_weighted": BOOL, "tolerance": TOLERANCE}),
    _check("animation", {"target": TARGET, "property": _enum("location", "rotation_euler", "scale"),
                         "samples": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                             "type": "object", "properties": {"frame": _integer(-100000, 100000), "expected": VECTOR},
                             "required": ["frame", "expected"], "additionalProperties": False}},
                         "tolerance": TOLERANCE, "require_keyframes": dict(
                             BOOL, description="Each channel is keyframed; samples between keys read the interpolation")}),
]
# Camera/light values are the action catalog's own closed settings objects.
CHECK_VARIANTS.extend(_check(kind, {"target": TARGET, "values": settings, "tolerance": TOLERANCE})
                      for kind, settings in (("camera_settings", CAMERA_SETTINGS), ("light_settings", LIGHT_SETTINGS)))
CHECK_VARIANTS.append(_check("aim", {"target": TARGET, "point": VECTOR,
                                     "tolerance": {"type": "number", "minimum": 0, "maximum": math.pi}}))
# No values: the modifier is there, of its type (what a modifier_add stating no setting wrote).
CHECK_VARIANTS.extend(_check("modifier", {"target": TARGET, "name": LABEL, "type": {"const": kind},
                                       "values": {"type": "object", "properties": props,
                                                  "maxProperties": max(8, len(props)), "additionalProperties": False},
                                       "tolerance": TOLERANCE}) for kind, props in MODIFIER_PROPERTIES.items())
# A generic (untyped) modifier type's settings by RNA path, checked like property_set values.
GENERIC_VALUES = {"type": "object", "properties": {}, "additionalProperties": True, "minProperties": 1,
                  "maxProperties": 64, "description": "RNA settings paths and values, as in modifier settings"}
CHECK_VARIANTS.append(_check("modifier_values", {"target": TARGET, "name": LABEL,
                                                 "type": {"type": "string", "pattern": "^[A-Z][A-Z_]{1,31}$"},
                                                 "values": GENERIC_VALUES, "tolerance": TOLERANCE},
                             ["target", "name", "type", "values"]))
CHECK_VARIANTS.append(_check("constraint_values", {"target": TARGET, "name": LABEL,
                                                   "values": GENERIC_VALUES, "tolerance": TOLERANCE},
                             ["target", "name", "values"]))
# ParticleSettings of a PARTICLE_SYSTEM modifier (by name), by the RNA paths of particle_set.
CHECK_VARIANTS.append(_check("particle_values", {"target": TARGET, "name": LABEL,
                                                 "values": GENERIC_VALUES, "tolerance": TOLERANCE},
                             ["target", "name", "values"]))
# Settings of an object (its data or active material) or the bound scene (its World), by the generic RNA paths
# of property_set: the schema names the shape, validate_checks applies property_set's value rules.
PROPERTY_CHECK_VALUES = {"type": "object", "properties": {"camera": NULLABLE_POINTER}, "additionalProperties": True,
                         "minProperties": 1, "maxProperties": 32,
                         "description": "Paths and value types of property_set's values"}
CHECK_VARIANTS.append(_check("property", {"target": TARGET, "values": PROPERTY_CHECK_VALUES, "tolerance": TOLERANCE},
                             ["target", "values"]))
# Keys present or absent at frames of a transform property, with their interpolation, easing and handles.
_FRAMES = {"type": "array", "items": _integer(-100000, 100000), "maxItems": 64, "uniqueItems": True}
# property: a transform channel or a keyable property_set path (property_keyframe).
from .anim_spec import PATH as _CHANNEL, INTERPOLATIONS, EASINGS, HANDLE_TYPES, KEY_TYPES, EXTRAPOLATIONS  # noqa: E402
from .anim_structure_spec import STRIP_SETTINGS as _STRIP  # noqa: E402
CHECK_VARIANTS.append(_check("keyframes", {
    "target": TARGET, "property": _CHANNEL, "index": _integer(-1, 63),
    "frames": _FRAMES, "absent": _FRAMES, "interpolation": _enum(*INTERPOLATIONS), "easing": _enum(*EASINGS),
    "handle_type": _enum(*HANDLE_TYPES), "key_type": _enum(*KEY_TYPES), "extrapolation": _enum(*EXTRAPOLATIONS)},
    ["target", "property"]))
# One channel's F-Curve modifier by name (every index of the path for -1): present with settings, or absent.
CHECK_VARIANTS.append(_check("fcurve_modifier", {
    "target": TARGET, "property": _CHANNEL, "index": _integer(-1, 63), "name": LABEL, "exists": BOOL,
    "values": GENERIC_VALUES, "tolerance": TOLERANCE}, ["target", "property", "name"]))
# The bound scene's timeline markers by exact name: present (frame and bound camera when stated) or absent.
_MARKER = {"type": "object", "properties": {"name": LABEL, "frame": _integer(-1048574, 1048574),
                                            "camera": NULLABLE_POINTER},
           "required": ["name"], "additionalProperties": False}
CHECK_VARIANTS.append(_check("markers", {
    "target": TARGET, "present": {"type": "array", "items": _MARKER, "maxItems": 32},
    "absent": {"type": "array", "items": LABEL, "maxItems": 32, "uniqueItems": True}}, ["target"]))
CHECK_VARIANTS.append(_check("shape_key", {"target": TARGET, "name": LABEL, "exists": BOOL,
                                           "value": {"type": "number", "minimum": -10, "maximum": 10},
                                           "tolerance": TOLERANCE}, ["target", "name"]))
_NAMES = {"type": "array", "items": LABEL, "maxItems": 32, "uniqueItems": True}
# Track count and active Action, the Action's name, track names present or absent, one strip's settings.
CHECK_VARIANTS.append(_check("nla", {
    "target": TARGET, "minimum_tracks": _integer(0, 64), "active_action": BOOL,
    "action": {"anyOf": [LABEL, {"type": "null"}]}, "track_names": _NAMES, "absent_tracks": _NAMES,
    "absent_strips": _NAMES, "strip": {"type": "object", "properties": {
        "track": LABEL, "name": LABEL, "values": {"type": "object", "additionalProperties": False, "properties": {
            key: value["oneOf"][0] for key, value in _STRIP["properties"].items()}}},
        "required": ["track", "name"], "additionalProperties": False}}, ["target"]))
# Whether a target still exists (deleted objects and created duplicates).
CHECK_VARIANTS.append(_check("object_exists", {"target": TARGET, "expected": BOOL}))
# Collection names an object is in, and names it is not in (bound scene only).
CHECK_VARIANTS.append(_check("collection_membership", {
    "target": TARGET, "collections": {"type": "array", "items": LABEL, "maxItems": 16, "uniqueItems": True},
    "absent": {"type": "array", "items": LABEL, "maxItems": 16, "uniqueItems": True}}, ["target"]))
# Modifier names at exact stack indexes, and names no longer in the stack.
CHECK_VARIANTS.append(_check("modifier_stack", {
    "target": TARGET,
    "positions": {"type": "array", "maxItems": 16, "items": {
        "type": "object", "properties": {"name": LABEL, "index": _integer(0, 31)}, "required": ["name", "index"],
        "additionalProperties": False}},
    "absent": {"type": "array", "items": LABEL, "maxItems": 16, "uniqueItems": True}}, ["target"]))
# The bound scene's World background (world_configure's own value schema).
CHECK_VARIANTS.append(_check("world_settings", {"target": TARGET, "values": _WORLD_VALUES, "tolerance": TOLERANCE},
                             ["target", "values"]))
# An object's view-layer eye, or (scene target) a collection's exclude, eye and own disable flags.
CHECK_VARIANTS.append(_check("visibility", {"target": TARGET, "hidden": BOOL, "collection": LABEL, "exclude": BOOL,
                                            "hide_viewport": BOOL, "hide_render": BOOL, "hide_select": BOOL},
                             ["target"]))
# One bone of an armature: rest geometry, stored pose and Bone/PoseBone settings (bone_set's fields).
CHECK_VARIANTS.append(_check("bone", {
    "target": TARGET, "bone": LABEL, "head": VECTOR, "tail": VECTOR, "roll": {"type": "number", "minimum": -2 * math.pi,
    "maximum": 2 * math.pi}, "parent": {"anyOf": [LABEL, {"type": "null"}]}, "connected": BOOL,
    "rotation_mode": _enum("QUATERNION", "XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX", "AXIS_ANGLE"), "location": VECTOR,
    "rotation": VECTOR, "scale": VECTOR, "settings": GENERIC_VALUES, "pose_settings": GENERIC_VALUES,
    "tolerance": TOLERANCE}, ["target", "bone"]))
# One node tree's nodes (by name or type), absent nodes, links and unlinked inputs (node_edit_spec).
from .node_edit_spec import NODE_GRAPH_CHECK  # noqa: E402
CHECK_VARIANTS.append(NODE_GRAPH_CHECK)
# Points and curves of Grease Pencil drawings (current frame), hair curves, curves and lattices; characters and
# lines of text; positions: a curve's only spline's points, local, in order.
CHECK_VARIANTS.append(_check("curve_counts", {"target": TARGET, **{key: {
    "type": "object", "properties": {"minimum": _integer(0, MAX_MESH_ELEMENTS), "maximum": _integer(0, MAX_MESH_ELEMENTS)},
    "required": ["minimum", "maximum"], "additionalProperties": False}
    for key in ("points", "curves", "characters", "lines")},
    "positions": {"type": "array", "items": VECTOR, "minItems": 1, "maxItems": 256}, "tolerance": TOLERANCE}, ["target"]))
# A batch's copies of one source: that many exist, of its type, on its data when linked. alias: the stem of the
# copies' aliases when the source is not itself an alias ($copy<batch>_<part>). first, stride: the first copy's
# name and the step of its number, for a check outside the program that made them (a batch result states both).
CHECK_VARIANTS.append(_check("copies", {"target": TARGET, "count": _integer(1, 1024), "linked": BOOL,
                                        "alias": {"type": "string", "pattern": "^\\$[A-Za-z][A-Za-z0-9_]{0,31}$"},
                                        "first": {"type": "string", "minLength": 1, "maxLength": 63},
                                        "stride": _integer(1, 8)}, ["target", "count", "linked"]))
CHECK_SCHEMA = {"oneOf": CHECK_VARIANTS}
CHECKS_SCHEMA = {"type": "array", "items": CHECK_SCHEMA, "minItems": 1, "maxItems": MAX_CHECKS}
CHECK_KINDS = frozenset(v["properties"]["kind"]["const"] for v in CHECK_VARIANTS)


def _range_text(schema):
    low, high = schema.get("minimum"), schema.get("maximum")
    if low is not None and high is not None:
        return "%g to %g" % (low, high)
    return "at least %g" % low if low is not None else "at most %g" % high


def validate_value(value, schema, path="value"):
    """Validate just the closed JSON-schema subset used here; return a deep copy."""
    if "oneOf" in schema or "anyOf" in schema:
        choices = schema.get("oneOf", schema.get("anyOf"))
        matches = []
        for choice in choices:
            try:
                matches.append(validate_value(value, choice, path))
            except ValueError:
                pass
        if not matches or ("oneOf" in schema and len(matches) != 1):
            raise ValueError(path + " does not match a supported closed schema")
        return stored_pointer(matches[0], choices)
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise ValueError(path + " has an unsupported value")
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise ValueError(path + " has an unsupported value")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool, "null": type(None)}
    if kind == "number":
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except (OverflowError, TypeError):
            finite = False
        if not finite:
            raise ValueError(path + " requires a finite number")
    elif kind in types and type(value) is not types[kind]:
        raise ValueError(path + " requires " + kind)
    if kind in ("integer", "number"):
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            # The range itself, so a model that could not see the bound
            # (Codex declarations omit it) corrects the value in one round.
            raise ValueError(path + " is outside its bounded range (" + _range_text(schema) + ")")
    if kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", math.inf) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(path + " requires bounded printable text")
        if schema.get("minLength", 0) and not value.strip():
            raise ValueError(path + " cannot be blank")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(path + " is not a typed identifier")
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise ValueError(path + " exceeds its item bounds")
        result = [validate_value(v, schema["items"], path + "[" + str(i) + "]") for i, v in enumerate(value)]
        if schema.get("uniqueItems") and len({repr(v) for v in result}) != len(result):
            raise ValueError(path + " requires distinct items")
        return result
    if kind == "object":
        props = schema["properties"]
        # additionalProperties: True only where the caller validates the open part itself.
        open_object = schema.get("additionalProperties") is True
        if (set(value) - set(props) and not open_object) or set(schema.get("required", ())) - set(value):
            raise ValueError(path + " has unexpected or missing fields")
        if not schema.get("minProperties", 0) <= len(value) <= schema.get("maxProperties", math.inf):
            raise ValueError(path + " exceeds its field bounds")
        return {k: validate_value(v, props[k], path + "." + k) if k in props else copy.deepcopy(v)
                for k, v in value.items()}
    return copy.deepcopy(value)


def validate_checks(checks):
    """Strictly validate 1-32 assertions without consulting or changing Blender."""
    from .action_properties import canonical, screen_values
    from .open_values import long_handle, wrapped
    checks = [wrapped(check, CHECK_SCHEMA, long_handle) for check in checks] if isinstance(checks, list) else checks
    for check in checks if isinstance(checks, list) else ():  # A long handle in a map of values is an object.
        if isinstance(check, dict) and check.get("kind") == "property":
            screen_values(check.get("values"))  # Typed reason for a path outside the allow-list.
    result = validate_value(checks, CHECKS_SCHEMA, "checks")
    for index, check in enumerate(result):
        if check["kind"] == "keyframes":
            pass  # Any channel data path (anim_spec.CHANNEL_PATTERN): keys are read from the slot, never evaluated.
            check.setdefault("index", -1)
            check.setdefault("frames", [])
            check.setdefault("absent", [])
            if not check["frames"] and not check["absent"] or set(check["frames"]) & set(check["absent"]):
                raise ValueError("A keyframes check needs distinct present or absent frames")
            if not check["frames"] and any(key in check for key in ("interpolation", "easing", "handle_type", "key_type")):
                raise ValueError("Key settings apply to present frames")
        if check["kind"] == "markers":
            check.setdefault("present", [])
            check.setdefault("absent", [])
            names = [row["name"] for row in check["present"]]
            if not names and not check["absent"] or len(set(names)) != len(names) or set(names) & set(check["absent"]):
                raise ValueError("A markers check names distinct present or absent markers")
        if check["kind"] == "fcurve_modifier" and check.get("values"):
            from .rna_rules import screen_path, json_value
            for path, value in check["values"].items():
                screen_path(path, struct=True)
                json_value(value, path)
        if check["kind"] == "collection_membership":
            check.setdefault("collections", [])
            check.setdefault("absent", [])
            if not check["collections"] and not check["absent"] or set(check["collections"]) & set(check["absent"]):
                raise ValueError("collection_membership needs distinct collections or absent names")
        if check["kind"] == "visibility":
            flags = [key for key in ("exclude", "hide_viewport", "hide_render", "hide_select") if key in check]
            if "collection" not in check and ("hidden" not in check or flags):
                raise ValueError("An object's visibility check takes hidden only; name a collection for the others")
            if "collection" in check and "hidden" not in check and not flags:
                raise ValueError("A collection visibility check needs at least one state")
        if check["kind"] == "modifier_stack":
            check.setdefault("positions", [])
            check.setdefault("absent", [])
            names = [item["name"] for item in check["positions"]]
            if not names and not check["absent"]:
                raise ValueError("A modifier_stack check needs positions or absent names")
            if len(set(names)) != len(names) or set(names) & set(check["absent"]):
                raise ValueError("modifier_stack names must be distinct and not both placed and absent")
        if check["kind"] in ("modifier_values", "constraint_values", "particle_values"):
            from .rna_rules import screen_path, json_value
            for path, value in check["values"].items():
                screen_path(path, struct=True)
                json_value(value, path)
        if check["kind"] == "bone":
            from .action_bones_spec import screen_check
            screen_check(check)
        if check["kind"] == "node_graph":
            from .node_edit_spec import screen_check as screen_graph
            screen_graph(check)
        if check["kind"] == "property":
            from .action_schema_types import _validate
            _validate(check["values"], _property_values(), "checks[%d].values" % index)  # Shape only.
            check["values"] = canonical(copy.deepcopy(check["values"]))  # Integers stay integers.
        _complete(check)  # An omitted tolerance (check_defaults).
        if check["kind"] == "dimensions":
            check.setdefault("space", "WORLD")
        if check["kind"] == "object_count":
            if check["minimum"] > check["maximum"]:
                raise ValueError("Object-count minimum cannot exceed maximum")
            check.setdefault("types", [])
        if check["kind"] == "mesh_counts" and any(check[key]["minimum"] > check[key]["maximum"] for key in ("vertices", "edges", "faces")):
            raise ValueError("Mesh-count minimum cannot exceed maximum")
        if check["kind"] == "curve_counts":
            stated = [key for key in ("points", "curves", "characters", "lines") if key in check]
            if not stated or any(check[key]["minimum"] > check[key]["maximum"] for key in stated):
                raise ValueError("A curve_counts check states at least one count, each with minimum <= maximum")
        if check["kind"] == "rig":
            if check["minimum_bones"] > check["maximum_bones"]:
                raise ValueError("Bone-count minimum cannot exceed maximum")
            check.setdefault("expected_bones", [])
        if check["kind"] == "parent":
            if check.get("parent_type") == "BONE" and not check.get("bone"):
                raise ValueError("Bone parenting requires an explicit bone name")
            if check.get("bone") and check.get("parent_type") != "BONE":
                raise ValueError("A bone name is valid only for BONE parenting")
        if check["kind"] == "animation":
            frames = [s["frame"] for s in check["samples"]]
            if len(frames) != len(set(frames)):
                raise ValueError("Animation sample frames must be distinct")
    return result


def validate_check(check):
    """Single-check convenience, with the same semantic validation as a batch."""
    return validate_checks([check])[0]


def check_references(check):
    """Every object dependency of a canonical assertion, including rig/parent."""
    check = validate_check(check)
    refs = [check["target"]] if "target" in check else []
    if check["kind"] == "parent" and check["expected"] is not None:
        refs.append(check["expected"])
    if check["kind"] == "armature_binding":
        refs.append(check["armature"])
    if check["kind"] == "property":
        refs.extend(_property_references(check["values"]))
    if check["kind"] == "bone":
        from .action_bones_spec import references
        refs.extend(references(check))
    if check["kind"] == "markers":
        refs.extend(row["camera"] for row in check["present"] if row.get("camera") is not None)
    return tuple(dict.fromkeys(refs))


VALIDATION_TOOLS = [{
    "name": "angelo_scene_verify",
    "description": "Verify 1-32 closed predicates against exact observed object handles and revisions. "
                   "Read-only aggregate base-mesh measurements (dimensions include modifiers, as observed); no "
                   "geometry dumps, evaluated modifier meshes, "
                   "frame changes, arbitrary predicates or retries. Unsupported/over-budget checks are inconclusive, "
                   "never passing. Direct animation channels do not prove constrained/NLA/world-space output.",
    "inputSchema": {"type": "object", "properties": {
        "read_set": {"type": "array", "maxItems": 64, "items": {
            "type": "object", "properties": {
                "handle": {"type": "string", "pattern": "^scn:[a-f0-9]{32}:[1-9][0-9]{0,19}$", "maxLength": 57},
                "revision": {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64}},
            "required": ["handle", "revision"], "additionalProperties": False}},
        "checks": CHECKS_SCHEMA}, "required": ["read_set", "checks"], "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}]
VALIDATION_NAMES = frozenset(t["name"] for t in VALIDATION_TOOLS)


def validate_validation(name, arguments):
    if name not in VALIDATION_NAMES or type(arguments) is not dict or set(arguments) != {"read_set", "checks"}:
        raise ValueError("Verification requires exactly a read_set and closed checks")
    read_set = arguments["read_set"]
    if type(read_set) is not list or len(read_set) > 64:
        raise ValueError("Verification may bind at most 64 observed objects")
    result, handles = [], set()
    for entry in read_set:
        if type(entry) is not dict or set(entry) != {"handle", "revision"}:
            raise ValueError("Each verification binding needs only an observed handle and exact revision")
        handle, revision = entry["handle"], entry["revision"]
        if type(handle) is not str or re.fullmatch(r"scn:[a-f0-9]{32}:[1-9][0-9]{0,19}", handle) is None:
            raise ValueError("Use an exact observed document object handle")
        if type(revision) is not str or re.fullmatch(r"[a-f0-9]{64}", revision) is None:
            raise ValueError("Use the exact object_revision from a fresh scene observation")
        if handle in handles:
            raise ValueError("Verification bindings must be unique")
        handles.add(handle)
        result.append(dict(entry))
    checks = validate_checks(arguments["checks"])
    for check in checks:
        if any(ref not in handles for ref in check_references(check)):
            raise ValueError("Every verification target/parent must be bound by an observed handle and revision")
    return {"read_set": result, "checks": checks}


def validation_capabilities():
    return {"version": 1, "kinds": sorted(CHECK_KINDS), "schema": copy.deepcopy(CHECK_SCHEMA),
            "max_checks": MAX_CHECKS, "read_only": True, "geometry_scope": "BASE_MESH",
            "limits": {"objects": MAX_OBJECTS, "vertices_per_mesh": MAX_VERTICES,
                       "mesh_elements": MAX_MESH_ELEMENTS, "total_elements": MAX_TOTAL_ELEMENTS},
            "unsupported_is": "inconclusive", "automatic_correction": False}
