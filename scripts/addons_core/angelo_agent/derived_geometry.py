# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometry an operation changes but no check can see: an unverified effect (pure Python, no bpy).

An object's geometry is the vertices, edges, faces and corner order of its
mesh, the splines and points of its curve and the points of its lattice. An
operation writes geometry when what it changes (``mutates``) or what it makes
from another object (``creates``, ``action_catalog.resource_dependencies``)
includes a geometry family, unless its kind is in ``GEOMETRY_KEEPS`` (it leaves
geometry as it was or states it by its parameters). Two writers are decided by
their parameters: a ``property_set`` or ``property_keyframe`` of a data path
that rewrites points (``POINT_PATHS``), and an ``operator_run`` of an operator
outside ``keeps_geometry`` (selection, visibility, flags, UV maps, custom
normals, vertex groups, material slots and shading leave it).

Each change has effect classes (``effects``): topology (elements added,
removed or reconnected), positions (of vertices or curve and lattice points),
order (element order, face winding) and point data (a curve's or lattice's
handles, radius, tilt or weights). Each check kind observes some
(``OBSERVES``): counts, degenerate elements and manifoldness see topology; a
mesh's dimensions and a curve's stated point positions see positions; nothing
sees order or point data. An object's change is verified only when every class
of it is observed by a check of the final program on that object (declared, or
derived: ``curve_points`` states its counts and positions); an unknown
operator or a new kind has every class. Otherwise ``unchecked_geometry`` names
it as an unverified effect, with the classes no check observes, the
verification is partial and the host never certifies the program (otherwise
an unchecked extrude, apply, join, conversion, operator, curve
rebuild, flattened curve or resized lattice, a subdivide measured by unchanged
dimensions, and moves, smoothing, sorting or flipped normals measured only by
counts were certified "Host-verified"). Commit then re-runs every check on the
live objects (``live_checks``).
"""


GEOMETRY_FAMILIES = frozenset({"mesh", "curve", "data"})
# Effect classes of a geometry change. TOPOLOGY: elements added, removed or reconnected. EXTENT: how far the
# geometry reaches (a resize, an extrude's displacement). SHAPE: where vertices or points are within it (a
# rotation, translation or move of some of them, smoothing). ORDER: element order or face winding. POINT_DATA: a
# curve's or lattice's per-point data besides positions (handles, radius, tilt, weights).
TOPOLOGY, EXTENT, SHAPE, ORDER, POINT_DATA = "topology", "extent", "shape", "order", "point_data"
POSITIONS = SHAPE  # Older name of the class a curve's stated point positions observe.
EVERY_CLASS = frozenset({TOPOLOGY, EXTENT, SHAPE, ORDER, POINT_DATA})
# The classes each check kind observes on its target. Counts, degenerate elements and manifoldness see topology;
# a mesh's dimensions see its extent (bounds cannot see a top face turned 90 degrees, every vertex moved by the
# same offset or one vertex moved inward); a curve's stated point positions see its extent and shape. Nothing
# reads a mesh's shape, element order, winding or point data, so a change of those is never certified.
OBSERVES = {"mesh_counts": {TOPOLOGY}, "mesh_degenerate": {TOPOLOGY}, "mesh_manifold": {TOPOLOGY},
            "curve_counts": {TOPOLOGY}, "dimensions": {EXTENT}}
POSITIONS_CHECK = "curve_counts with positions"  # How a row names the curve check that sees extent and shape.
# Kinds whose topology is fully determined by their explicit parameters on the staged mesh: the host derives the
# resulting counts from the staged result, and commit proves the live mesh has them (commit_fidelity), so their
# topology is observed without a declared count check (an extrude certified by dimensions alone needed a round).
DERIVED_TOPOLOGY = frozenset({"mesh_extrude", "mesh_bevel", "mesh_inset"})
GEOMETRY_CHECKS = tuple(sorted(OBSERVES))
# Writers of a geometry family that leave the vertices, edges, faces and splines as they were, or state them
# exactly by their parameters. A kind missing here counts as changing geometry, so a new kind is unverified
# until a check states it.
GEOMETRY_KEEPS = frozenset({
    # Material slots and face material indexes.
    "material_assign", "material_principled", "shader_nodes",
    # UV maps, shape keys and vertex groups beside the geometry.
    "uv_pack", "uv_project", "uv_smart_project", "uv_unwrap", "shape_key_add", "shape_key_value", "armature_bind",
    # Vertices move against an origin the derived transform checks state; the shape in the world stays.
    "transform_apply", "origin_set",
    # A data setting by path (the derived property check states it; POINT_PATHS below rewrite points), and keys
    # of data settings.
    "property_set", "property_keyframe", "keyframe_configure", "keyframe_edit", "fcurve_modifier",
    # New primitives, empties, curves and text whose shape the parameters state.
    "create_primitive", "create_object",
})
# property_set (and property_keyframe) data paths that rewrite points beside the value they state, and how.
# test_blender_geometry_measures sweeps every settable curve, lattice and mesh data setting and pins this table.
POINT_PATHS = {"data.dimensions": {SHAPE},  # A curve made 2D flattens its points.
               "data.points_u": EVERY_CLASS - {ORDER}, "data.points_v": EVERY_CLASS - {ORDER},
               "data.points_w": EVERY_CLASS - {ORDER},  # A lattice resolution regenerates its points.
               "data.use_outside": {SHAPE}}  # A lattice's outside-only mode moves its inner points.
# Effect classes of admitted operator_run operators known to change only some (modules, then operators); any
# other operator, admitted or future, has every class. keeps_geometry lists those that change none.
_OPERATOR_EFFECTS = {
    "transform": {SHAPE},  # Moves, rotates or slides the selected elements.
    "transform.resize": {EXTENT, SHAPE},  # Scales them: the extent, and the shape unless it scales them all.
    **dict.fromkeys(("transform.tilt", "transform.edge_crease", "transform.edge_bevelweight", "transform.vert_crease",
                     "transform.skin_resize", "transform.bbone_resize", "transform.rotate_normal"), {POINT_DATA}),
    **dict.fromkeys(("mesh.vertices_smooth", "mesh.vertices_smooth_laplacian", "mesh.face_make_planar",
                     "mesh.symmetry_snap", "mesh.blend_from_shape", "mesh.circularize", "lattice.flip",
                     "lattice.make_regular", "curve.smooth"), {SHAPE}),
    **dict.fromkeys(("curve.radius_set", "curve.smooth_radius", "curve.smooth_tilt", "curve.smooth_weight",
                     "curve.spline_weight_set", "curve.tilt_clear", "curve.handle_type_set",
                     "curve.normals_make_consistent"), {POINT_DATA}),
    **dict.fromkeys(("mesh.sort_elements", "mesh.reorder_vertices_spatial", "mesh.flip_normals",
                     "mesh.normals_make_consistent"), {ORDER}),
}
# operator_run operators that keep every position, element and corner order: besides these, every uv operator
# and every select, de-select, hide and reveal operator. test_blender_operator_run runs each admitted one.
_KEEPING_OPERATORS = frozenset((
    "object.vertex_group_add object.vertex_group_assign object.vertex_group_assign_new object.vertex_group_clean "
    "object.vertex_group_copy object.vertex_group_invert object.vertex_group_levels object.vertex_group_limit_total "
    "object.vertex_group_lock object.vertex_group_mirror object.vertex_group_move object.vertex_group_normalize "
    "object.vertex_group_normalize_all object.vertex_group_quantize object.vertex_group_remove "
    "object.vertex_group_remove_from object.vertex_group_set_active object.vertex_group_smooth "
    "object.vertex_group_sort object.vertex_weight_copy object.vertex_weight_delete "
    "object.vertex_weight_normalize_active_vertex object.vertex_weight_paste object.vertex_weight_set_active "
    "object.material_slot_add object.material_slot_assign object.material_slot_move object.material_slot_remove "
    "object.material_slot_remove_all object.material_slot_remove_unused object.shade_flat object.shade_smooth "
    "object.shade_smooth_by_angle object.forcefield_toggle object.shape_key_add object.shape_key_lock "
    "mesh.customdata_custom_splitnormals_add mesh.customdata_custom_splitnormals_clear "
    "mesh.customdata_face_sets_clear mesh.customdata_mask_clear mesh.customdata_skin_add mesh.customdata_skin_clear "
    "mesh.faces_shade_flat mesh.faces_shade_smooth mesh.mark_seam mesh.mark_sharp "
    "mesh.set_sharpness_by_angle mesh.uv_texture_add mesh.uv_texture_remove mesh.uvs_reverse mesh.uvs_rotate "
    "mesh.colors_reverse mesh.colors_rotate mesh.loop_to_region mesh.region_to_loop mesh.shortest_path_select "
    "mesh.edges_select_sharp mesh.faces_select_linked_flat "
    "curve.shade_flat curve.shade_smooth curve.match_texture_space").split())
REASON = "unchecked_geometry_edit"


def keeps_geometry(operator):
    """Whether an ``operator_run`` operator leaves its target's geometry exactly as it was."""
    module, _, name = operator.partition(".")
    return (operator in _KEEPING_OPERATORS or module == "uv" or name in {"hide", "reveal"}
            or name.startswith(("select", "de_select")))


def effects(operation):
    """The effect classes of ``operation``'s geometry change (call only when ``geometry_reference`` names one)."""
    kind, params = operation["kind"], operation["parameters"]
    if kind in ("property_set", "property_keyframe"):
        return frozenset().union(*(POINT_PATHS[path] for path in params["values"] if path in POINT_PATHS))
    if kind == "operator_run":
        name = params["operator"]
        value = params.get("properties", {}).get("value") or []
        if (name == "transform.resize" and params.get("select") in (None, "ALL")  # None selects all, too.
                and "center_override" not in params.get("properties", {}) and all(item > 0 for item in value)):
            # Every visible element scaled, positively, about the default pivot: only the extent changes. A
            # mirror (a negative factor), another pivot or hidden elements (action_operators records them as an
            # unverified effect) change the shape.
            return frozenset({EXTENT})
        return frozenset(_OPERATOR_EFFECTS.get(name, _OPERATOR_EFFECTS.get(name.partition(".")[0], EVERY_CLASS)))
    if kind == "mesh_recalculate_normals":
        return frozenset({ORDER})
    if kind in ("mesh_bevel", "mesh_delete", "mesh_merge_by_distance", "object_join"):
        return frozenset({TOPOLOGY})  # New elements on the bevelled edges; removed, merged or joined ones.
    if kind == "mesh_subdivide":  # Smoothing moves the cut vertices.
        return frozenset({TOPOLOGY, SHAPE} if params["smoothness"] else {TOPOLOGY})
    if kind == "mesh_inset":  # Depth lifts the inset faces within the bounds.
        return frozenset({TOPOLOGY, SHAPE} if params["depth"] else {TOPOLOGY})
    if kind == "mesh_extrude":
        return frozenset({TOPOLOGY, EXTENT})  # New faces, displaced by its translation.
    if kind == "mesh_bend":  # Its cuts add elements; the bow moves vertices and the extent along up.
        return frozenset({TOPOLOGY, EXTENT, SHAPE} if params["segments"] > 1 else {EXTENT, SHAPE})
    if kind == "curve_points":
        return frozenset({TOPOLOGY, EXTENT, SHAPE})  # Its derived counts and positions observe all three.
    if kind in ("modifier_apply", "object_convert", "mesh_separate"):
        return frozenset({TOPOLOGY, EXTENT, SHAPE})  # Whatever the modifier or conversion made.
    return EVERY_CLASS  # A new kind writing geometry: unverified until it is classified.


def _observed(check):
    seen = set(OBSERVES.get(check.get("kind"), ()))
    if check.get("kind") == "curve_counts" and "positions" in check:
        seen.update({EXTENT, SHAPE})
    return seen


def geometry_reference(operation):
    """The object whose geometry ``operation`` (canonical) changes: its target, or what it makes from another; else None.

    The kind's static families decide first, without validating the operation
    again (most kinds never write geometry); the operation's own families then
    decide for the rest (an operator that writes only object state or a pose).
    Keys of a data channel add ``data`` to an animation kind: they change its
    Action, not its geometry, and its static families have no geometry family.
    """
    from .action_catalog import CREATES, _mutations, resource_dependencies
    kind = operation["kind"]
    if kind in ("property_set", "property_keyframe"):  # A value written (and keyed) like any other, or points rewritten.
        return operation["target"] if any(path in POINT_PATHS for path in operation["parameters"]["values"]) else None
    if kind == "operator_run" and keeps_geometry(operation["parameters"]["operator"]):
        return None
    if kind in GEOMETRY_KEEPS or not (set(_mutations[kind]) | set(CREATES.get(kind, ()))) & GEOMETRY_FAMILIES:
        return None
    dependencies = resource_dependencies(operation)
    if operation["target"] is not None:
        return operation["target"] if set(dependencies["mutates"]) & GEOMETRY_FAMILIES else None
    alias = operation["parameters"].get("alias")
    if alias and set(dependencies["creates"]) & GEOMETRY_FAMILIES:
        return alias if alias.startswith("$") else "$" + alias
    return None


def unchecked_geometry(operations, checks):
    """Unverified-effect rows for each geometry change whose every class no check of its object observes."""
    observed = {}
    for check in checks:
        observed.setdefault(check.get("target"), set()).update(_observed(check))
    rows = []
    for index, operation in enumerate(operations):
        reference = geometry_reference(operation)
        if reference is None:
            continue
        derived = {TOPOLOGY} if operation["kind"] in DERIVED_TOPOLOGY else set()
        missing = sorted(effects(operation) - observed.get(reference, set()) - derived)
        if missing:
            rows.append({"operation": index, "root": {"target": reference}, "reason": REASON,
                         "affected": [{"kind": operation["kind"], "unobserved": missing,
                                       "checked_by": sorted({kind for kind, seen in OBSERVES.items() if seen & set(missing)}
                                                            | ({POSITIONS_CHECK} if set(missing) & {EXTENT, SHAPE}
                                                               else set()))}]})
    return rows
