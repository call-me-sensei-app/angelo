# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Expected checks the host derives from a program's own operations.

Many results are already stated exactly by an operation's parameters: the
name and transform ``create_primitive`` gives its object (and a cube's or
plane's dimensions), the channels a ``SET`` ``transform`` writes, the settings
``modifier_add`` configures, the material ``material_principled`` creates and
the parent ``parent_set`` sets. Models used to restate them as
``expected_checks``, which cost output tokens and proved nothing the host could
not state itself. ``program_checks.required_checks`` admits the complete
derived obligations separately from authored arguments. They run on the staged
copy, again immediately before commit, and on Accept.

Rules, all conservative:

* ``property_set`` derives one ``property`` check per owner with every value
  it wrote; later ``property_set`` operations on the same owner merge into it.
* Pure function of canonical (validated, defaults applied) operations. No
  Blender access. The result is deterministic, so normalizing twice yields the
  same program and the same invocation digest.
* A check is derived only from parameters whose read-back is exact up to
  float32 storage. Relative modes (``DELTA``), modifier operands, generic node
  graphs, keyframes and topology edits derive nothing.
* Names are checked only when the model chose one (not the schema default):
  Blender suffixes a colliding name (``.001``), so a requested name that is
  already taken fails before commit, as a declared name check would. A
  creation that keeps Blender's suffix (``SUFFIXED``) asking for a name an
  earlier creation of the same program asked for is not checked: Blender
  names it the next free ``.NNN`` and its result says so (recorded 2026-10-25:
  three object names asked twice cost three phase 1 workers a round each).
  That holds only for a name free before the program; staging refuses a repeat
  of one Main held then (``program_names.held_repeats``, break-it E2E-19B M-1).
* A later operation in the same program that may change the derived property
  of the same object drops the derived check (the ``*_KEEPS`` tables list the
  kinds known to leave each property alone; a kind missing from a table drops
  the check). The final state is what the checks prove.
* Authored checks never replace an implied assertion. The obligation owner
  deduplicates only equivalent complete predicates, including all values.

Derived checks prove that each operation landed as specified. They do not
replace checks of requested results that the operations do not state, such as
dimensions after a scale change, object counts or relationships.

An edit that changes an object's geometry and states no check of it is an
unverified effect (``derived_geometry``).
"""

import copy

from . import material_rules
from .action_schemas import OPERATION_SCHEMAS, CONSTRAINT_SETTINGS
from .action_catalog import deleted_references
from .derived_camera_light import created_checks
from .validation_spec import MODIFIER_PROPERTIES
from . import action_bones_spec as bone_spec
from . import particle_rules
from . import anim_spec, anim_structure_spec
from . import node_edit_spec, derived_everyday
from .program_names import created_name

DEFAULT_PRIMITIVE_NAME = "Angelo Primitive"
DEFAULT_MATERIAL_NAME = "Angelo Material"
DEFAULT_SHADER_NAME = "Angelo Shader"
# create_object types: the object type they make and their default name.
CREATED_OBJECTS = {"EMPTY": ("EMPTY", "Angelo Empty"), "TEXT": ("FONT", "Angelo Text"), "CURVE": ("CURVE", "Angelo Curve"),
                   "LATTICE": ("LATTICE", "Angelo Lattice"), "GREASEPENCIL": ("GREASEPENCIL", "Angelo Grease Pencil")}

# Action modifier settings -> the modifier check's allowlisted property names.
MODIFIER_FIELDS = {
    "BEVEL": {"width": "width", "segments": "segments", "limit_method": "limit_method",
              "angle_limit": "angle_limit", "clamp_overlap": "use_clamp_overlap"},
    "SUBSURF": {"levels": "levels", "render_levels": "render_levels", "subdivision_type": "subdivision_type"},
    "SOLIDIFY": {"thickness": "thickness", "offset": "offset", "use_even_offset": "use_even_offset"},
    "MIRROR": {"axes": "use_axis", "use_clip": "use_clip", "use_merge_vertices": "use_mirror_merge",
               "merge_threshold": "merge_threshold"},
}
# Types whose action settings share their names with the checked RNA properties.
for _kind in ("ARRAY", "DECIMATE", "WEIGHTED_NORMAL", "TRIANGULATE", "SHRINKWRAP", "DISPLACE", "SCREW", "CURVE",
              "BOOLEAN", "REMESH"):
    MODIFIER_FIELDS[_kind] = {name: name for name in MODIFIER_PROPERTIES[_kind] if name not in ("show_viewport", "show_render")}

_ALL_KINDS = frozenset(OPERATION_SCHEMAS)
_MESH_EDITS = frozenset(kind for kind in _ALL_KINDS if kind.startswith("mesh_"))
_UV_EDITS = frozenset(kind for kind in _ALL_KINDS if kind.startswith("uv_"))
# Later kinds on the same object that cannot change each derived property.
# Name and type: no closed action renames, converts or deletes an object.
NAME_KEEPS = _ALL_KINDS
# Creations whose kernel keeps Blender's ".NNN" for a taken name (the others refuse NAME_TAKEN themselves).
SUFFIXED = frozenset({"create_primitive", "camera_create", "light_create", "armature_create"})
# Local location/rotation/scale RNA. keyframe_insert, parent_set (unparenting
# with keep_transform rewrites the channels) and the configure kinds (look_at)
# may write them.
TRANSFORM_KEEPS = frozenset({"modifier_add", "modifier_configure", "material_principled", "shader_nodes",
                             "material_assign", "geometry_nodes", "armature_bind", "constraint_add", "property_set", "modifier_remove",
                             "modifier_move", "modifier_apply", "object_join"}) | _MESH_EDITS | _UV_EDITS
# World-space dimensions: anything moving vertices or the world matrix
# (transforms, parents, constraints, keyframes, mesh edits) may change them, and
# so may modifiers (the check reads evaluated bounds once there is one).
DIMENSIONS_KEEPS = frozenset({"material_principled", "shader_nodes", "material_assign"}) | _UV_EDITS
MODIFIER_KEEPS = _ALL_KINDS - {"modifier_configure", "modifier_remove", "modifier_apply", "operator_run"}
# Kinds that change a modifier stack's membership or order.
STACK_EDITS = frozenset({"modifier_add", "modifier_remove", "modifier_move", "modifier_apply", "geometry_nodes",
                         "armature_bind"})
# A stack check survives later kinds that keep membership and order; removals,
# applies and moves keep removed names absent, so an absent-only check merges.
STACK_KEEPS = _ALL_KINDS - STACK_EDITS - {"operator_run"}
# Only collection_link changes membership; later links merge into the check.
MEMBERSHIP_KEEPS = _ALL_KINDS
# A curve's splines and points: only another curve_points or an operator may change them.
CURVE_KEEPS = _ALL_KINDS - {"curve_points", "operator_run"}
# A UV layer stays present and finite under every kind but a topology edit.
UV_KEEPS = _ALL_KINDS - _MESH_EDITS - {"modifier_apply", "object_join", "operator_run"}
# Key checks read the active Action: a new key or a push-down changes what they see, as do
# structural key edits, pasted keys and another Action.
KEYS_KEEPS = _ALL_KINDS - {"keyframe_insert", "nla_push_down", "keyframe_edit", "keyframe_copy", "action_set"}
NLA_KEEPS = _ALL_KINDS - {"keyframe_insert", "property_keyframe", "keyframe_copy", "action_set", "nla_edit",
                          "nla_push_down"}
# An F-Curve modifier: its own channel's modifiers, or the channel itself, may change.
FMODIFIER_KEEPS = _ALL_KINDS - {"fcurve_modifier", "keyframe_edit", "keyframe_copy", "action_set", "keyframe_delete"}
# Markers of the bound scene: only marker_edit changes them (later ones merge in).
MARKER_KEEPS = _ALL_KINDS
# operator_run (an arbitrary allow-listed operator) drops the checks it could falsify.
SHAPE_KEY_KEEPS = _ALL_KINDS - {"operator_run"}
# Slot contents and per-face material indexes: material/shader assignments and
# topology edits may change them.
MATERIAL_KEEPS = frozenset({"transform", "parent_set", "constraint_add", "keyframe_insert", "modifier_add",
                            "modifier_configure", "geometry_nodes", "camera_configure", "light_configure",
                            "property_set"}) | _UV_EDITS
PARENT_KEEPS = _ALL_KINDS - {"parent_set", "armature_bind"}
# No other kind writes an allow-listed setting; a later property_set on the
# same owner merges into the earlier check (later values win).
PROPERTY_KEEPS = _ALL_KINDS - {"operator_run"}
# A constraint's settings: only another configure of the same object may change them.
CONSTRAINT_KEEPS = _ALL_KINDS - {"constraint_configure", "operator_run"}
# A bone's rest, pose and settings: bone_set (bone_spec.keeps decides) and armature, pose or transform
# operators write them.
BONE_KEEPS = _ALL_KINDS - {"operator_run"}
# A node tree: kinds that replace slot materials, remove modifiers or rebuild the World drop it;
# a node_edit of another root tree keeps it (node_edit_spec.keeps).
NODE_GRAPH_KEEPS = _ALL_KINDS - {"node_edit", "material_principled", "shader_nodes", "material_assign", "modifier_remove",
                                 "modifier_apply", "operator_run", "world_configure"}


def identity(check):
    """What makes two checks assert the same thing about the same object."""
    kind = check.get("kind")
    detail = {"transform": "property", "modifier": "name", "modifier_values": "name", "constraint_values": "name",
              "particle_values": "name",
              "material_assignment": "material",
              "material_values": "material", "dimensions": "space", "animation": "property",
              "keyframes": "property", "shape_key": "name", "visibility": "collection", "bone": "bone",
              "fcurve_modifier": "name", "copies": "alias"}.get(kind)
    if kind == "node_graph":
        return (kind, check.get("target"), node_edit_spec.tree_key(check))
    return (kind, check.get("target"), check.get(detail) if detail else None)


def _reference(operation):
    if operation["kind"] in {"create_primitive", "object_duplicate", "create_object", "object_convert", "camera_create",
                             "light_create"}:
        alias = operation["parameters"].get("alias")
        return None if not alias else (alias if alias.startswith("$") else "$" + alias)
    return operation["target"]


def _primitive_checks(target, params):
    checks = []
    if params["name"] != DEFAULT_PRIMITIVE_NAME and params["name"] not in {"Angelo Empty", "Angelo Text", "Angelo Curve"}:
        checks.append(({"kind": "object_name", "target": target, "expected": params["name"]}, NAME_KEEPS))
    # Location is always stated; an identity rotation or unit scale adds a
    # check line to every summary and receipt while proving nothing useful.
    for key, prop, identity_value in (("location", "location", None), ("rotation", "rotation_euler", [0, 0, 0]),
                                      ("scale", "scale", [1, 1, 1])):
        value = [float(item) for item in params[key]]
        if identity_value is not None and value == identity_value:
            continue
        checks.append(({"kind": "transform", "target": target, "property": prop, "expected": value}, TRANSFORM_KEEPS))
    if params["primitive"] in {"CUBE", "PLANE", "GRID"} and all(value == 0 for value in params["rotation"]):
        size, scale = params["size"], params["scale"]
        depth = size if params["primitive"] == "CUBE" else 0
        dimensions = [abs(size * scale[0]), abs(size * scale[1]), abs(depth * scale[2])]
        if max(dimensions) <= 10000:
            checks.append(({"kind": "dimensions", "target": target, "expected": dimensions, "space": "WORLD"},
                           DIMENSIONS_KEEPS))
    return checks


def _operation_checks(operation):
    """(check, kinds that keep it) pairs one canonical operation states exactly."""
    kind, params, target = operation["kind"], operation["parameters"], _reference(operation)
    if target is None:
        return []
    if kind == "create_primitive":
        return _primitive_checks(target, params)
    if kind in {"camera_create", "light_create"}:  # Name, place, every setting and the aim (derived_camera_light).
        return created_checks(target, operation)
    if kind == "create_object":
        object_type, default = CREATED_OBJECTS[params["type"]]
        checks = [({"kind": "object_type", "target": target, "expected": object_type}, NAME_KEEPS)]
        primitive = _primitive_checks(target, dict(params, primitive=params["type"]))
        return checks + [(check, keeps) for check, keeps in primitive
                         if check["kind"] != "object_name" or params["name"] != default]
    if kind == "transform" and params["mode"] == "SET":
        return [({"kind": "transform", "target": target, "property": prop, "expected": [float(v) for v in params[key]]},
                 TRANSFORM_KEEPS)
                for key, prop in (("location", "location"), ("rotation", "rotation_euler"), ("scale", "scale"))
                if params[key] is not None]
    if kind in {"modifier_add", "modifier_configure"} and params["type"] in MODIFIER_FIELDS:
        # The stated settings (SETTINGS_NOTE), none for a modifier that states none: it is there, of its type.
        # Mirror's optional origin is checked only on set/clear.
        fields, name = MODIFIER_FIELDS[params["type"]], params.get("name") or params.get("modifier")
        values = {fields[key]: copy.deepcopy(value) for key, value in params["settings"].items() if key in fields}
        checks = [({"kind": "modifier", "target": target, "name": name, "type": params["type"], "values": values},
                   MODIFIER_KEEPS)]
        settings = params["settings"]
        if params["type"] == "MIRROR" and (settings.get("mirror_object") is not None or settings.get("clear")):
            origin = None if settings.get("clear") else {"object": settings["mirror_object"]}
            checks.append(({"kind": "modifier_values", "target": target, "name": name, "type": "MIRROR",
                            "values": {"mirror_object": origin}, "tolerance": 0.000001}, MODIFIER_KEEPS))
        return checks
    if kind in {"modifier_add", "modifier_configure"} and params["type"] not in MODIFIER_FIELDS and params["settings"]:
        # A generic type: the written settings by RNA path, read back after commit.
        name = params.get("name") or params.get("modifier") or "Angelo " + params["type"].replace("_", " ").title()
        values = copy.deepcopy(params["settings"])
        return [({"kind": "modifier_values", "target": target, "name": name, "type": params["type"],
                  "values": values}, MODIFIER_KEEPS)]
    if (kind == "constraint_add" and params["type"] not in CONSTRAINT_SETTINGS and params["settings"]) or (
            kind == "constraint_configure" and (params["settings"] or params["influence"] is not None)):
        name = params.get("constraint") or params.get("name") or "Angelo " + params["type"].replace("_", " ").title()
        values = copy.deepcopy(params["settings"])
        if kind == "constraint_configure" and params["influence"] is not None:
            values["influence"] = params["influence"]
        return [({"kind": "constraint_values", "target": target, "name": name, "values": values}, CONSTRAINT_KEEPS)]
    if kind == "material_principled" and params["name"] != DEFAULT_MATERIAL_NAME:
        from .material_rules import principled_inputs
        inputs = principled_inputs(params)  # Only what it states (SETTINGS_NOTE).
        values = {key: [float(v) for v in inputs[name]] if key == "base_color" else float(inputs[name])
                  for key, name in (("base_color", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic"))
                  if name in inputs}
        # all_faces stays false: a mesh without faces cannot satisfy it, yet the
        # operation still did exactly what it states.
        return [({"kind": "material_assignment", "target": target, "material": params["name"], "all_faces": False},
                 MATERIAL_KEEPS)] + ([({"kind": "material_values", "target": target, "material": params["name"],
                                        **values}, MATERIAL_KEEPS)] if values else [])
    if kind == "shader_nodes" and params["name"] != DEFAULT_SHADER_NAME:
        # A graph's values (image textures included) are not a Principled-only material_values check.
        return [({"kind": "material_assignment", "target": target, "material": params["name"], "all_faces": False},
                 MATERIAL_KEEPS)]
    if kind == "parent_set":
        return [({"kind": "parent", "target": target, "expected": params["parent"], "parent_type": "OBJECT"},
                 PARENT_KEEPS)]
    if kind == "node_edit":  # The nodes, absent nodes and links the steps state (node_edit_spec).
        check = node_edit_spec.derived_check(target, params)
        return [(check, NODE_GRAPH_KEEPS)] if check is not None else []
    if kind == "particle_set":  # Every written settings path; a later particle_set of the system merges in.
        return [(particle_rules.derived_check(target, params), _ALL_KINDS)]
    if kind == "property_set":
        # Exact typed values; the predicate compares numbers at float32 precision.
        return [({"kind": "property", "target": target, "values": copy.deepcopy(params["values"])}, PROPERTY_KEEPS)]
    if kind == "visibility_set":
        values = {key: value for key, value in params.items() if value is not None}
        return [({"kind": "visibility", "target": target, **values}, PROPERTY_KEEPS)]
    if kind == "world_configure":
        return [({"kind": "world_settings", "target": target, "values": copy.deepcopy(params)}, PROPERTY_KEEPS)]
    if kind == "object_rename":
        return [({"kind": "object_name", "target": target, "expected": params["name"]}, NAME_KEEPS)]
    if kind == "object_delete":
        return [({"kind": "object_exists", "target": target, "expected": False}, _ALL_KINDS)]
    if kind == "object_join":
        return [({"kind": "object_exists", "target": source, "expected": False}, _ALL_KINDS)
                for source in params["sources"]]
    if kind == "object_convert":
        checks = [({"kind": "object_type", "target": target, "expected": "MESH"}, NAME_KEEPS)]
        if not params["keep_original"]:
            checks.append(({"kind": "object_exists", "target": params["source"], "expected": False}, _ALL_KINDS))
        return checks
    if kind == "object_duplicate":
        checks = [({"kind": "object_exists", "target": target, "expected": True}, _ALL_KINDS)]
        if params["name"]:
            checks.append(({"kind": "object_name", "target": target, "expected": params["name"]}, NAME_KEEPS))
        if params["location"] is not None:
            checks.append(({"kind": "transform", "target": target, "property": "location",
                            "expected": [float(v) for v in params["location"]]}, TRANSFORM_KEEPS))
        return checks
    if kind == "transform_apply":  # Applied channels become identity (every rotation representation zeroed).
        return [({"kind": "transform", "target": target, "property": prop, "expected": value, "tolerance": 0.00001},
                 TRANSFORM_KEEPS) for key, prop, value in (("location", "location", [0., 0., 0.]),
                                                           ("rotation", "rotation_euler", [0., 0., 0.]),
                                                           ("scale", "scale", [1., 1., 1.])) if params[key]]
    if kind == "collection_link":
        name = params["collection"]
        return [({"kind": "collection_membership", "target": target,
                  "collections": [] if params["mode"] == "UNLINK" else [name],
                  "absent": [name] if params["mode"] == "UNLINK" else []}, MEMBERSHIP_KEEPS)]
    if kind == "curve_points":  # One spline through exactly these local points (a 2D curve's at Z 0) replaces them.
        count, flat = len(params["points"]), params["dimensions"] == "2D"
        points = [[float(x), float(y), 0. if flat else float(z)] for x, y, z in params["points"]]
        return [({"kind": "curve_counts", "target": target, "points": {"minimum": count, "maximum": count},
                  "curves": {"minimum": 1, "maximum": 1}, "positions": points}, CURVE_KEEPS)]
    if kind in {"uv_smart_project", "uv_pack"}:
        named = {"name": params["uv_map"]} if params.get("uv_map") is not None else {}  # Else the active map.
        return [({"kind": "uv_layer", "target": target, **named, "require_nonempty": True,
                  "require_finite": True}, UV_KEEPS)]
    if kind == "property_keyframe":
        settings = {"interpolation": params["interpolation"]} if params["interpolation"] is not None else {}
        return [({"kind": "keyframes", "target": target, "property": path, "index": -1, "frames": [params["frame"]],
                  **settings}, KEYS_KEEPS) for path in params["values"]]
    if kind == "keyframe_delete":
        return [({"kind": "keyframes", "target": target, "property": params["property"], "index": params["index"],
                  "absent": [params["frame"]]}, KEYS_KEEPS)]
    if kind in anim_spec.KEY_KINDS:
        return [(check, FMODIFIER_KEEPS if check["kind"] == "fcurve_modifier" else KEYS_KEEPS)
                for check in anim_spec.derived_checks(target, operation)]
    if kind in anim_structure_spec.STRUCTURE_KINDS:
        return [(check, MARKER_KEEPS if check["kind"] == "markers" else NLA_KEEPS)
                for check in anim_structure_spec.derived_checks(target, operation)]
    if kind == "shape_key_add":
        return [({"kind": "shape_key", "target": target, "name": params["name"], "exists": True,
                  "value": params["value"]}, SHAPE_KEY_KEEPS)]
    if kind == "shape_key_value":
        # A keyed value is animated, which a stored-value check cannot certify.
        check = {"kind": "shape_key", "target": target, "name": params["name"], "exists": True}
        if params["frame"] is None:
            check["value"] = params["value"]
        return [(check, SHAPE_KEY_KEEPS)]
    if kind == "nla_push_down":
        return [({"kind": "nla", "target": target, "minimum_tracks": 1, "active_action": False}, NLA_KEEPS)]
    if kind == "bone_set":
        return [(bone_spec.derived_check(target, params), BONE_KEEPS)]
    if kind in {"modifier_remove", "modifier_apply"}:
        return [({"kind": "modifier_stack", "target": target, "absent": [params["modifier"]]}, STACK_KEEPS)]
    if kind == "modifier_move":
        return [({"kind": "modifier_stack", "target": target,
                  "positions": [{"name": params["modifier"], "index": params["index"]}]}, STACK_KEEPS)]
    return []


def _keeps(check, keeps, operation):
    """Whether a later ``operation`` on the check's object leaves it true."""
    if check["kind"] == "modifier_values" and check.get("type") == "MIRROR" and set(check["values"]) == {"mirror_object"}:
        params = operation["parameters"]
        if operation["kind"] in {"modifier_remove", "modifier_apply"}:
            return params["modifier"] != check["name"]
        if operation["kind"] == "modifier_configure":
            if params["modifier"] != check["name"]:
                return True
            return (params["type"] == "MIRROR" and params["settings"].get("mirror_object") is None
                    and not params["settings"].get("clear"))
    if check["kind"] == "modifier_stack" and operation["kind"] in {"modifier_remove", "modifier_apply", "modifier_move"}:
        return not check.get("positions")
    if check["kind"] == "bone" and operation["kind"] == "bone_set":
        return bone_spec.keeps(check, operation)
    if check["kind"] == "keyframes" and operation["kind"] == "keyframe_configure":
        return anim_spec.keeps(check, operation)
    if check["kind"] == "node_graph" and operation["kind"] == "node_edit":
        return node_edit_spec.keeps(check, operation)
    if check["kind"] == "modifier" and operation["kind"] == "modifier_configure":
        # A configure writes only the settings it states (SETTINGS_NOTE): another modifier's check stays, and so does
        # this one's, merged below with the configure's own (a typed type: a generic one changes it by RNA path).
        from .action_schemas import MODIFIER_SETTINGS
        return check["name"] != operation["parameters"]["modifier"] or operation["parameters"]["type"] in MODIFIER_SETTINGS
    if operation["kind"] == "property_keyframe" and check["kind"] == "property":
        # A keyed setting's stored value follows its F-Curve, not the earlier write.
        return not set(check["values"]) & set(operation["parameters"]["values"])
    if operation["kind"] == "transform":
        # A transform writes only the channels it names; location never moves
        # base-mesh dimensions.
        written = {prop for key, prop in (("location", "location"), ("rotation", "rotation_euler"),
                                          ("scale", "scale")) if operation["parameters"][key] is not None}
        if check["kind"] == "transform":
            return check["property"] not in written
        if check["kind"] == "dimensions":
            return not written & {"rotation_euler", "scale"}
    return operation["kind"] in keeps


def _removed(operation):
    return list(deleted_references(operation))


def derive_checks(operations):
    """Checks the canonical ``operations`` imply about their final state, in order.

    Later derivations of the same identity replace earlier ones (a later SET
    transform wins), and a later operation on the same object that may change
    a derived property drops that check. Returns new dictionaries.
    """
    derived, created = {}, set()
    for index, operation in enumerate(operations):
        reference = _reference(operation)
        own, name = _operation_checks(operation), created_name(operation)
        if name in created and operation["kind"] in SUFFIXED:  # Asked twice in this program: Blender's ".NNN".
            own = [(check, keeps) for check, keeps in own if check["kind"] != "object_name"]
        if name:
            created.add(name)
        # A material kind also writes its also objects' slots and faces: an earlier check on them goes as on its target.
        written = {reference} | set(operation["parameters"].get("also") or ()
                                    if operation["kind"] in material_rules.KINDS else ())
        written.discard(None)
        if written:
            for key, (check, keeps) in list(derived.items()):
                if check["target"] in written and not _keeps(check, keeps, operation):
                    del derived[key]
        for removed in _removed(operation):
            # Nothing earlier certifies an object this operation deletes.
            for key, (check, _) in list(derived.items()):
                if check["target"] == removed:
                    del derived[key]
        for key in derived_everyday.displaced(derived, operation):
            del derived[key]
        # The everyday kinds add their own: also objects, a batch's copies, adaptive settings (derived_everyday).
        for check, keeps in derived_everyday.extend(operations, index, own, {
                "material": MATERIAL_KEEPS, "modifier": MODIFIER_KEEPS, "all": _ALL_KINDS}):
            key = identity(check)
            previous = derived.pop(key, None)
            if previous is not None and (check["kind"] in ("property", "world_settings", "particle_values") or (
                    check["kind"] == "modifier" and previous[0]["type"] == check["type"])):
                check = dict(check, values={**previous[0]["values"], **check["values"]})
            elif previous is not None and check["kind"] == "keyframes" and operation["kind"] == "property_keyframe":
                # Another key on the same setting: both frames are keyed.
                frames = sorted(set(previous[0].get("frames", [])) | set(check["frames"]))
                absent = [frame for frame in previous[0].get("absent", []) if frame not in frames]
                check = dict(check, frames=frames, absent=absent)
                if previous[0].get("interpolation") != check.get("interpolation"):
                    check.pop("interpolation", None)
            elif previous is not None and check["kind"] == "keyframes":
                check = _merge_keys(previous[0], check)
            elif previous is not None and check["kind"] == "visibility":
                check = {**previous[0], **check}
            elif previous is not None and check["kind"] == "bone":
                check = bone_spec.merge_check(previous[0], check, operation["parameters"])
            elif previous is not None and check["kind"] == "shape_key":
                check = {**previous[0], **check}
                if "value" not in check or (operation["kind"] == "shape_key_value" and operation["parameters"]["frame"] is not None):
                    check.pop("value", None)
            elif previous is not None and check["kind"] == "collection_membership":
                check = _merge_membership(previous[0], operation["parameters"])
            elif previous is not None and check["kind"] == "markers":
                check = anim_structure_spec.merge_markers(previous[0], check)
            elif previous is not None and check["kind"] == "modifier_stack":
                check = dict(check, absent=sorted(set(previous[0].get("absent", [])) | set(check.get("absent", []))))
            derived[key] = (check, keeps)
    return [copy.deepcopy(check) for check, _ in derived.values()]


def _merge_keys(previous, new):
    """Key state after a later delete or configure on the same property."""
    if not new.get("frames"):  # A delete: earlier present frames lose the deleted one.
        frames = [frame for frame in previous.get("frames", []) if frame not in new["absent"]]
        merged = dict(previous, frames=frames, absent=sorted(set(previous.get("absent", [])) | set(new["absent"])))
        if not frames:
            for key in ("interpolation", "easing", "handle_type"):
                merged.pop(key, None)
        return merged
    absent = [frame for frame in previous.get("absent", []) if frame not in new["frames"]]
    return dict(new, absent=absent)


def _merge_membership(previous, params):
    """The membership a later collection_link leaves, from the earlier derived check."""
    name, present, absent = params["collection"], set(previous["collections"]), set(previous["absent"])
    if params["mode"] == "MOVE":
        absent, present = (absent | present) - {name}, {name}
    elif params["mode"] == "LINK":
        present, absent = present | {name}, absent - {name}
    else:
        present, absent = present - {name}, absent | {name}
    return {"kind": "collection_membership", "target": previous["target"], "collections": sorted(present),
            "absent": sorted(absent)}


def derived_flags(checks, operations):
    """For each canonical check, whether it equals one the operations imply.

    Reporting only: a declared check identical to a derived one, or proving it
    (program_checks._keys), is reported as derived, which is true of both.
    """
    from .program_checks import host_checks, _key, _keys
    implied = {_key(check) for check in host_checks(operations)}
    return [any(key in implied for key in _keys(check)) for check in checks]
