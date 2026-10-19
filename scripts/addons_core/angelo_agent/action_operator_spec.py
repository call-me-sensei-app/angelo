# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``operator_run``: allow-listed Blender C operators on one staged object (no ``bpy``).

The kind runs a reviewed C operator (``bpy.ops`` idname) on a private staged
copy and nothing else. Admission is deny-by-default in two layers:

* this explicit allow-list: every admitted operator is named here with the
  growth model the kernel bounds it by (``ALLOWED``): Edit-mode modules
  (``MODULE_TARGETS``: mesh, curve, armature, lattice, UV, transform, hair
  curves, text), Grease Pencil layer operators in Object mode and its stroke
  operators in Edit mode, constraint operators (``OBJECT_MODULES``), pose
  transform operators in Pose mode (``POSE_MODULES``, committed by the
  ``pose`` family) and a data-local ``object`` subset (vertex groups, material
  slots, shape keys, shading, ``forcefield_toggle``, skin marks, transform
  clears and applies). The phase-5 families and their run-time rules are
  tabled in ``action_operator_families``;
* rules over the installed RNA, applied by ``action_operators.admission``:
  Python-defined operators, macros, modal/blocking, cursor-dependent and
  internal operators, operators without Undo, and any file, pointer or
  collection property are refused even when listed here.

Operators that create objects, rename across the file, read clipboards, the
mouse, the view or files, or have superlinear output are denied by name
(``DENIED``), as are those a typed kind covers with its own commit family
(``object.particle_system_add``: ``modifier_add`` and ``particle_set``).

Properties are written by RNA name and checked against the operator's RNA
definition at run time; the integer amplifiers in ``CAPS`` are bounded here
and their growth estimated before the operator runs. Armature operators are
bounded in bones (``bone_estimate``), and the operators measured to take time
growing with the square of their input in elements and selection
(``WORK_BOUNDS``).
"""

import copy
import re

from . import action_operator_families as _families
from .action_schema_types import MAX_OPERATIONS, INDICES, enum, integer, nullable, obj, text

MODULE_TARGETS = {"mesh": ("MESH",), "curve": ("CURVE",), "armature": ("ARMATURE",), "lattice": ("LATTICE",),
                  "uv": ("MESH",), **_families.EDIT_MODULES}
# Modules whose admitted operators run in Object mode on their object type (Grease Pencil strokes: Edit mode,
# _families.EDIT_OPERATORS).
OBJECT_MODULES = {"grease_pencil": ("GREASEPENCIL",), **_families.OBJECT_MODE_MODULES}
# Pose-mode operators on armatures; commit copies the pose (bone_state.transfer_pose, the pose family).
POSE_MODULES = {"pose": ("ARMATURE",)}
# Growth specs: (factor on the base element count, {amplifier property: growth kind}).
# Kinds: "linear" (value + 1), "square" ((value + 1) ** 2), "pow4" (4 ** value); "quadratic"
# as the factor bounds the output by the square of the base edges (grid fill).
_KEEP, _DOUBLE, _GROW = (1, {}), (2, {}), (4, {})


def _module(module, entries):
    return {module + "." + name: spec for name, spec in entries.items()}


def _same(spec, names):
    return {name: spec for name in names.split()}


ALLOWED = {
    **_module("mesh", {
        **_same(_KEEP, "attribute_set average_normals beautify_fill blend_from_shape circularize colors_reverse "
                       "colors_rotate customdata_custom_splitnormals_add customdata_custom_splitnormals_clear "
                       "customdata_face_sets_clear customdata_mask_clear customdata_skin_add customdata_skin_clear "
                       "decimate delete delete_edgeloop delete_loose dissolve_degenerate dissolve_edges dissolve_faces "
                       "dissolve_limited dissolve_mode dissolve_verts edge_collapse edge_rotate edges_select_sharp "
                       "face_make_planar faces_select_linked_flat faces_shade_flat faces_shade_smooth flatten "
                       "flip_normals flip_quad_tessellation hide loop_to_region mark_seam mark_sharp merge "
                       "merge_normals mod_weighted_strength normals_make_consistent normals_tools region_to_loop "
                       "remove_doubles reorder_vertices_spatial reveal select_all select_axis "
                       "select_boundary_loop_multi select_by_attribute select_by_pole_count select_edge_loop_multi "
                       "select_edge_ring_multi select_face_by_sides select_interior_faces select_less select_linked "
                       "select_loose select_mirror select_mode select_more select_non_manifold select_nth "
                       "select_random select_similar select_similar_region select_ungrouped set_normals_from_faces "
                       "set_sharpness_by_angle shape_propagate_to_all shortest_path_select smooth_normals "
                       "sort_elements space_edge_loops_evenly split_normals symmetry_snap tris_convert_to_quads "
                       "unsubdivide uv_texture_add uv_texture_remove uvs_reverse uvs_rotate vertices_smooth "
                       "vertices_smooth_laplacian"),
        **_same(_DOUBLE, "bisect edge_face_add edge_split face_split_by_edges fill fill_holes quads_convert_to_tris "
                         "solidify split symmetrize vert_connect vert_connect_concave vert_connect_nonplanar "
                         "vert_connect_path"),
        **_same(_GROW, "extrude_context extrude_edges_indiv extrude_faces_indiv extrude_region extrude_verts_indiv "
                       "poke wireframe primitive_cube_add primitive_monkey_add primitive_plane_add"),
        "subdivide": (4, {"number_cuts": "square"}), "bridge_edge_loops": (4, {"number_cuts": "linear"}),
        "subdivide_edgering": (4, {"number_cuts": "linear"}), "extrude_repeat": (4, {"steps": "linear"}),
        "spin": (4, {"steps": "linear"}), "screw": (4, {"steps": "linear", "turns": "linear"}),
        "fill_grid": ("quadratic", {}),
        **_same((4, {"vertices": "linear"}), "primitive_circle_add primitive_cone_add primitive_cylinder_add"),
        "primitive_grid_add": (4, {"x_subdivisions": "linear", "y_subdivisions": "linear"}),
        "primitive_uv_sphere_add": (4, {"segments": "linear", "ring_count": "linear"}),
        "primitive_ico_sphere_add": (4, {"subdivisions": "pow4"}),
    }),
    **_module("curve", {
        **_same(_KEEP, "cyclic_toggle de_select_first de_select_last decimate delete dissolve_verts handle_type_set "
                       "hide make_segment match_texture_space normals_make_consistent radius_set reveal select_all "
                       "select_less select_linked select_more select_next select_nth select_previous select_random "
                       "select_row select_similar shade_flat shade_smooth smooth smooth_radius smooth_tilt "
                       "smooth_weight spline_weight_set switch_direction tilt_clear"),
        **_same(_DOUBLE, "duplicate extrude split"),
        **_same(_GROW, "spline_type_set primitive_bezier_circle_add primitive_bezier_curve_add "
                       "primitive_nurbs_circle_add primitive_nurbs_curve_add primitive_nurbs_path_add"),
        "spin": (16, {}), "subdivide": (4, {"number_cuts": "linear"}),
    }),
    # Armature factors count bones exactly: a forked extrude adds two bones per selected one, cuts one each.
    **_module("armature", {
        **_same(_KEEP, "align assign_to_collection calculate_roll collection_add collection_assign "
                       "collection_create_and_assign collection_deselect collection_move collection_remove "
                       "collection_select collection_unassign collection_unassign_named delete dissolve hide "
                       "move_to_collection parent_clear parent_set reveal roll_clear select_all select_hierarchy "
                       "select_less select_linked select_mirror select_more select_similar split switch_direction"),
        **_same(_DOUBLE, "duplicate duplicate_rename fill symmetrize bone_primitive_add"),
        "extrude": _GROW, "subdivide": (1, {"number_cuts": "linear"}),
    }),
    **_module("lattice", _same(_KEEP, "flip make_regular select_all select_less select_mirror select_more "
                                      "select_random select_ungrouped")),
    **_module("uv", _same(_KEEP, "align arrange_islands average_islands_scale copy_mirrored_faces cube_project "
                                 "cylinder_project hide mark_seam move_on_axis pin remove_doubles reset reveal "
                                 "seams_from_islands select_all select_by_winding select_less select_linked select_mode "
                                 "select_more select_overlap select_pinned select_similar select_split select_tile "
                                 "shortest_path_select smart_project sphere_project unwrap weld")),
    # Grease Pencil layers and layer groups (data-local; strokes and materials stay reviewed Python).
    **_module("grease_pencil", {**_same(_KEEP, "layer_active layer_add layer_group_add layer_group_color_tag "
                                               "layer_group_remove layer_hide layer_isolate layer_lock_all "
                                               "layer_mask_add layer_mask_remove layer_mask_reorder layer_merge "
                                               "layer_move layer_remove layer_reveal"), "layer_duplicate": _DOUBLE}),
    # Pose transforms of the selected bones. Pose selection and visibility do not commit through the pose
    # family (bone_set writes visibility), so their operators are not listed.
    **_module("pose", _same(_KEEP, "loc_clear rot_clear scale_clear transforms_clear user_transforms_clear "
                                   "rotation_mode_set quaternions_flip visual_transform_apply")),
    **_module("object", _same(_KEEP, "vertex_group_add vertex_group_assign vertex_group_assign_new vertex_group_clean "
                                     "vertex_group_copy vertex_group_deselect vertex_group_invert vertex_group_levels "
                                     "vertex_group_limit_total vertex_group_lock vertex_group_mirror vertex_group_move "
                                     "vertex_group_normalize vertex_group_normalize_all vertex_group_quantize "
                                     "vertex_group_remove vertex_group_remove_from vertex_group_select "
                                     "vertex_group_set_active vertex_group_smooth vertex_group_sort vertex_weight_copy "
                                     "vertex_weight_delete vertex_weight_normalize_active_vertex vertex_weight_paste "
                                     "vertex_weight_set_active material_slot_add material_slot_assign "
                                     "material_slot_deselect material_slot_move material_slot_remove "
                                     "material_slot_remove_all material_slot_remove_unused material_slot_select "
                                     "shape_key_add shape_key_apply_to_basis shape_key_clear shape_key_copy "
                                     "shape_key_lock shape_key_make_basis shape_key_mirror shape_key_move "
                                     "shape_key_remove shape_key_retime shade_flat shade_smooth shade_smooth_by_angle "
                                     "forcefield_toggle")),
}
assert not set(ALLOWED) & set(_families.ALLOWED)
ALLOWED.update(_families.ALLOWED)
# Object-subset operators by target type: vertex groups, shape keys and their weights on meshes only
# (lattice weights are not fingerprinted); material slots and shading also on curves.
OBJECT_TARGETS = {name: ("MESH",) for name in ALLOWED if name.startswith("object.")}
OBJECT_TARGETS.update({name: ("MESH", "CURVE") for name in OBJECT_TARGETS
                       if name.startswith(("object.material_slot", "object.shade_")) and name != "object.shade_smooth_by_angle"})
OBJECT_TARGETS["object.forcefield_toggle"] = ("MESH", "CURVE", "EMPTY", "LATTICE", "ARMATURE")
OBJECT_TARGETS.update({name: types for name, types in _families.OPERATOR_TARGETS.items() if name.startswith("object.")})
# Waiting for a commit family another track adds; refused until then.
# Listed module operators the kernel never runs, with the reason.
DENIED = {
    **dict.fromkeys(("mesh.separate", "curve.separate", "armature.separate"), "NEW_OBJECTS: creates objects"),
    **dict.fromkeys(("armature.flip_names", "armature.autoside_names"),
                    "FILE_WIDE_RENAME: bone renames rewrite animation paths across the whole file"),
    **dict.fromkeys(("mesh.intersect", "mesh.intersect_boolean"),
                    "SUPERLINEAR: output can grow with the square of the input; use a Boolean modifier"),
    **dict.fromkeys(("uv.copy", "uv.paste"), "CLIPBOARD: shared clipboard state outside the stage"),
    **dict.fromkeys(("uv.snap_cursor", "uv.snap_selected", "uv.cursor_set", "uv.custom_region_set",
                     "uv.project_from_view", "mesh.primitive_cube_add_gizmo", "curve.pen", "curve.draw"),
                    "VIEW_STATE: depends on an editor's view, cursor or gizmo"),
    **{name: "CURSOR_PICK: picks by mouse or view coordinates" for name in (
        "mesh.select_linked_pick", "mesh.shortest_path_pick", "curve.select_linked_pick", "curve.shortest_path_pick",
        "armature.select_linked_pick", "armature.shortest_path_pick", "uv.select", "uv.select_box",
        "uv.select_circle", "uv.select_lasso", "uv.select_loop", "uv.select_edge_ring", "uv.select_linked_pick",
        "uv.shortest_path_pick", "mesh.polybuild_face_at_cursor", "mesh.polybuild_split_at_cursor",
        "mesh.polybuild_transform_at_cursor", "mesh.polybuild_delete_at_cursor", "mesh.polybuild_dissolve_at_cursor")},
    **dict.fromkeys(("object.vertex_group_copy_to_selected", "object.material_slot_copy", "object.shape_key_transfer",
                     "object.join_shapes", "object.data_transfer", "object.datalayout_transfer"),
                    "OTHER_OBJECTS: reads or writes other selected objects"),
    "object.shade_auto_smooth": "ASSET_FILE: appends a node-group asset from disk",
    "grease_pencil.layer_duplicate_object": "OTHER_OBJECTS: reads or writes other selected objects",
    **dict.fromkeys(("pose.flip_names", "pose.autoside_names"),
                    "FILE_WIDE_RENAME: bone renames rewrite animation paths across the whole file"),
    **dict.fromkeys(("pose.copy", "pose.paste"), "CLIPBOARD: the pose clipboard is a file in the temporary folder"),
    "pose.armature_apply": "REST_AND_CHILDREN: applies the pose as rest pose, moving child objects",
    **dict.fromkeys(("pose.paths_calculate", "pose.paths_update", "pose.paths_range_update", "pose.paths_clear"),
                    "FRAME_STEPPING: motion paths evaluate the scene over a frame range"),
    "pose.propagate": "KEYFRAMES: writes keys into the Action",
    **dict.fromkeys(("pose.constraint_add", "pose.constraint_add_with_targets", "pose.constraints_clear",
                     "pose.constraints_copy", "pose.ik_add", "pose.ik_clear"),
                    "BONE_CONSTRAINTS: bone constraints are not part of the pose commit family"),
    "pose.select_constraint_target": "OTHER_OBJECTS: reads or writes other selected objects",
    "pose.select_linked_pick": "CURSOR_PICK: picks by mouse or view coordinates",
    "object.particle_system_add": "COVERED: modifier_add with type PARTICLE_SYSTEM adds a particle system natively, "
                                  "configured by particle_set",
    **_families.DENIED,
}
# Integer amplifiers: the largest value accepted (the RNA default is checked too).
CAPS = {"number_cuts": 32, "steps": 256, "turns": 16, "vertices": 1024, "segments": 256, "ring_count": 256,
        "x_subdivisions": 256, "y_subdivisions": 256, "subdivisions": 7, "repeat": 100, "iterations": 100, "span": 64}
# Operators whose time grows with the square of their input (bmesh walkers, merges into one n-gon, edge-net
# fills, sparse solvers): the most elements x selected elements (V+E+F each, after the selection pass) they run
# on. Each is the work its worst measured input (grid, wire tree or loose points, everything selected) took about
# 2 s for on the reference machine; native_coverage.rst ("operator_run work bounds") has the measurements.
WORK_BOUNDS = {
    "mesh.select_edge_loop_multi": 4e7, "mesh.bridge_edge_loops": 9e8, "mesh.edge_face_add": 1.5e9,
    "mesh.fill": 2e9, "mesh.fill_holes": 7e9, "mesh.delete_edgeloop": 1.5e10, "mesh.dissolve_edges": 1.5e10,
    "mesh.dissolve_mode": 1.5e10, "mesh.dissolve_verts": 1.5e10, "mesh.select_similar": 1.5e10, "uv.unwrap": 2.5e10,
    "mesh.vertices_smooth_laplacian": 4e10, "mesh.select_edge_ring_multi": 5e10, "mesh.unsubdivide": 1e11,
    "mesh.edge_rotate": 1.5e11, **_families.WORK_BOUNDS}
# Integer properties that select or seed rather than amplify; any other integer must keep its default.
BENIGN_INTS = frozenset({"seed", "skip", "nth", "offset", "number", "pole_count", "weight", "twist_offset", "tile",
                         "distance", "value_int", "value_int_vector_2d", "sides", "limit", "precision"}) | _families.BENIGN_INTS
# Every value is bounded JSON: true/false, a number, text or a list of them. RNA types it.
_SCALAR = [{"type": "boolean"}, {"type": "number", "minimum": -1e30, "maximum": 1e30},
           {"type": "string", "minLength": 1, "maxLength": 1024}]
VALUE = {"oneOf": [*_SCALAR, {"type": "array", "items": {"oneOf": _SCALAR}, "minItems": 1, "maxItems": 32}]}
PROPERTY_NAME = r"^[a-z][a-z0-9_]{0,63}$"
OPERATOR_PATTERN = (r"^(?:mesh|curve|armature|lattice|uv|object|pose|grease_pencil|transform|curves|font|constraint)"
                    r"\.[a-z][a-z0-9_]{0,62}$")
MAX_PROPERTIES = 32
_FILE_WORDS = ("filepath", "file_path", "directory", "filename", "files", "script", "python", "text_block")
_indices = dict(INDICES, minItems=0, default=[])
OPERATOR_RUN_SCHEMA = obj({
    "operator": dict(text(64, pattern=OPERATOR_PATTERN), description=(
        "A bpy.ops idname. Edit mode: mesh.*, curve.*, armature.*, lattice.*, uv.*, curves.*, font.*, "
        "grease_pencil strokes, transform.* (explicit values; pivot: selection bounds or center_override); Object mode: "
        "object.vertex_group_*, material_slot_*, shape_key_*, shade_*, skin_*, forcefield_toggle, transform clears, "
        "grease_pencil.layer_*, constraint.* (properties.constraint names one); Pose mode: pose transform clears. "
        "Allow-listed C operators only; a refusal names the reason.")),
    "properties": {"type": "object", "properties": {}, "additionalProperties": VALUE,
                   "propertyNames": {"pattern": PROPERTY_NAME}, "maxProperties": MAX_PROPERTIES, "default": {},
                   "description": "Operator properties by RNA name, checked against the operator's definition."},
    "select": nullable(enum(["ALL", "NONE", "KEEP", "INDICES"])),
    "select_mode": nullable(enum(["VERT", "EDGE", "FACE"])),
    "vertex_indices": _indices, "edge_indices": _indices, "face_indices": _indices,
    "index_scope": enum(["INITIAL", "CURRENT"], "INITIAL"), "topology_step": integer(0, MAX_OPERATIONS, 0)},
    ["operator"])
OPERATOR_CAPABILITIES = {
    "edit_mode_modules": sorted(MODULE_TARGETS), "object_mode_modules": sorted(OBJECT_MODULES),
    "pose_mode_modules": sorted(POSE_MODULES), "object_subset": "vertex groups, material slots, shape keys, shading, "
    "forcefield_toggle (objects that already have force-field settings), skin marks, visual transform and parent "
    "inverse applies, origin and track clears",
    "selection": "Edit and Pose mode: select ALL (default), NONE, KEEP or INDICES (meshes); text: ALL or NONE (cursor "
                 "at the end); Object mode keeps it; pose selection is not committed",
    "effect": "refused unless the object's data or object-level state changes"}


_REFUSAL_POLICY = {
    **dict.fromkeys(("UNKNOWN_PROPERTY", "PROPERTY_NOT_SETTABLE", "CONSTRAINT_MISSING", "CONSTRAINT_NAME",
                     "FIELD_SETTINGS_MISSING", "NO_EFFECT", "POLL_FAILED", "NOT_FINISHED", "PROPERTY_NAME",
                     "PROPERTY_VALUE", "SELECTION", "TARGET_TYPE", "INDEX", "NO_WINDOW"), "VALIDATION"),
    **dict.fromkeys(("MISSING_IN_BUILD", "PYTHON_OPERATOR", "INTERACTIVE", "NO_UNDO", "DATA_PROPERTY",
                     "FILE_PROPERTY", "FILE_OR_SCRIPT", "NOT_ALLOWED", "ANIMATED_POSE", "CONSTRAINT_TARGETS",
                     "SHAPE_KEY_FREE", "UNCOMMITTED_STATE", "POSE_DATA"), "UNSUPPORTED"),
    **dict.fromkeys(("AMPLIFIER_BOUND", "RECURSION_BOUND", "WORK_BOUND", "GROWTH_BOUND", "TOO_MANY_PROPERTIES"),
                   "LIMIT_EXCEEDED"),
    "DATA_REPLACED": "INTERNAL_ERROR", "FREED_DATA": "INTERNAL_ERROR",
}


class OperatorError(ValueError):
    """An operator_run refusal with a stable code (tests and errors name it)."""

    def __init__(self, code, detail, *, refusal_policy=None, details=None):
        self.code = code
        self.refusal_code = refusal_policy or _REFUSAL_POLICY.get(code, "UNKNOWN")
        self.details = dict(details or {})
        super().__init__("operator_run %s: %s" % (code, detail))


def module_of(idname):
    return idname.split(".", 1)[0]


def admitted_by_name(idname):
    """Raise unless ``idname`` is on the allow-list (pure; RNA rules run in the kernel)."""
    if idname in DENIED:
        code, _, detail = DENIED[idname].partition(": ")
        raise OperatorError(code, "%s is denied: %s" % (idname, detail), refusal_policy="UNSUPPORTED")
    if idname not in ALLOWED:
        module = module_of(idname)
        hint = ("this module's operators are allow-listed individually"
                if module in MODULE_TARGETS or module in OBJECT_MODULES or module in POSE_MODULES
                else "only the object operators listed for operator_run run")
        raise OperatorError("NOT_ALLOWED", "%s is not an allow-listed operator; %s" % (idname, hint))
    return ALLOWED[idname]


def target_types(idname):
    module = module_of(idname)
    if idname in _families.OPERATOR_TARGETS:
        return _families.OPERATOR_TARGETS[idname]
    return OBJECT_TARGETS[idname] if module == "object" else {**MODULE_TARGETS, **OBJECT_MODULES, **POSE_MODULES}[module]


def needs_window(idname):
    """Whether the operator's call gets a context window: Edit-mode transforms (their status-bar text needs one)."""
    return module_of(idname) == "transform"


def edit_only(idname):
    """Whether the operator runs only in Edit mode (Edit modules and Grease Pencil strokes)."""
    return module_of(idname) in MODULE_TARGETS or idname in _families.EDIT_OPERATORS


def screen_properties(properties):
    """Raise for a denied property name or a non-JSON value, before any RNA is consulted (pure)."""
    from .rna_rules import json_value
    if not isinstance(properties, dict):
        return
    if len(properties) > MAX_PROPERTIES:
        raise OperatorError("TOO_MANY_PROPERTIES", "at most %d properties" % MAX_PROPERTIES)
    for name, value in properties.items():
        if not isinstance(name, str) or not re.fullmatch(PROPERTY_NAME, name):
            raise OperatorError("PROPERTY_NAME", "properties use lowercase RNA identifiers")
        if any(word in name for word in _FILE_WORDS):
            raise OperatorError("FILE_OR_SCRIPT", "%s names a file or script; operators never touch files" % name)
        if isinstance(value, dict) or value is None:
            raise OperatorError("PROPERTY_VALUE", "%s: operator properties take values, never objects or null" % name)
        json_value(value, name)
        cap = CAPS.get(name)
        if cap is not None and type(value) is int and value > cap:
            raise OperatorError("AMPLIFIER_BOUND", "%s is at most %d natively" % (name, cap))


def canonical(params, raw):
    """Finish ``params`` (schema-validated) from the raw ``raw`` parameters: exact values, selection rules."""
    idname = params["operator"]
    admitted_by_name(idname)
    screen_properties(raw.get("properties", {}))
    params["properties"] = copy.deepcopy(raw.get("properties", {}))  # Integers stay integers for INT properties.
    indices = [key for key in ("vertex_indices", "edge_indices", "face_indices") if params[key]]
    if (params["select"] == "INDICES") != bool(indices):
        raise OperatorError("SELECTION", "select INDICES takes exactly one of vertex_indices, edge_indices or "
                                         "face_indices; other selections take none")
    if len(indices) > 1:
        raise OperatorError("SELECTION", "choose one index domain")
    if indices and params["select_mode"] not in (None, {"vertex_indices": "VERT", "edge_indices": "EDGE",
                                                         "face_indices": "FACE"}[indices[0]]):
        raise OperatorError("SELECTION", "select_mode must match the index domain")
    if params["index_scope"] == "INITIAL" and params["topology_step"] != 0:
        raise ValueError("INITIAL indexes require topology_step 0")
    if indices and "MESH" not in target_types(idname):
        raise OperatorError("SELECTION", "element indexes select mesh elements only")
    _family_rules(idname, params)
    return params


def _family_rules(idname, params):
    """The phase-5 families' pure rules: refused values, the named constraint and their selections."""
    module, properties = module_of(idname), params["properties"]
    for scope, name, refused, reason in _families.REFUSED_VALUES:  # Scope: a module or one operator.
        value = properties.get(name)
        if scope in (module, idname) and name in properties and any(value is item or value == item for item in refused):
            code, _, detail = reason.partition(": ")
            raise OperatorError(code, "%s=%s %s" % (name, properties[name], detail), refusal_policy="UNSUPPORTED")
    if module == "constraint":
        if not isinstance(properties.get("constraint"), str):
            raise OperatorError("CONSTRAINT_NAME", "constraint operators name one of the target's own constraints "
                                                   "in properties.constraint")
        if params["select"] is not None:
            raise OperatorError("SELECTION", "constraint operators run in Object mode and keep the selection")
    if module == "font" and params["select"] in {"KEEP", "INDICES"}:
        raise OperatorError("SELECTION", "the text cursor and selection are not observable: select ALL (the whole "
                                         "text) or NONE (the cursor at the end)")


def families(params):
    """The families commit transfers: the pose for pose operators, object-level state only for
    forcefield_toggle, else the data too (and object-level state through commit_object_state). Armature
    operators also commit the pose: duplicates and mirrored bones get their sources' pose settings."""
    module = module_of(params["operator"])
    if params["operator"] in _families.OPERATOR_FAMILIES:
        return list(_families.OPERATOR_FAMILIES[params["operator"]])
    if module in POSE_MODULES:
        return ["object", "pose"]
    if module == "armature":
        return ["object", "data", "pose"]
    return ["object"] if params["operator"] == "object.forcefield_toggle" else ["object", "data"]


def growth_factor(spec, values):
    """The amplifier product of one operator for these effective integer values."""
    factor = 1
    for name, kind in spec[1].items():
        value = int(values.get(name, 0))
        factor *= {"linear": value + 1, "square": (value + 1) ** 2, "pow4": 4 ** value}[kind]
    return factor


def estimate(spec, counts, values):
    """Conservative element count after the operator, from the base ``counts`` (elements, edges)."""
    base = counts["elements"] + 64  # Primitive operators add geometry to an empty mesh too.
    if spec[0] == "quadratic":
        return base * 4 + (counts["edges"] // 2 + 1) ** 2
    return base * spec[0] * growth_factor(spec, values)


def bone_estimate(spec, bones, values):
    """Upper bound on an armature's bones after one operator. Bone naming is superlinear (unique names are
    probed against every bone), so the result must fit the evaluation budget before the operator runs."""
    return max(bones, 1) * spec[0] * growth_factor(spec, values)


def within_work_bound(idname, elements, selected):
    """Whether a WORK_BOUNDS operator may run on ``elements`` with ``selected`` of them selected (others always)."""
    bound = WORK_BOUNDS.get(idname)
    return bound is None or elements * selected <= bound
