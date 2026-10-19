# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``operator_run`` families beyond the core set (no ``bpy``).

``action_operator_spec`` merges these tables into its allow-list; the kernel
(``action_operators``, ``action_operator_modes``) applies them at run time.

* Edit-mode ``transform`` operators run with ``EXEC_DEFAULT`` and explicit
  values. They need a context window (their status-bar text dereferences it),
  so only their call gets the program's window, nested around the stage's own
  scene, view layer and objects. With no editor area the transform system
  pivots on the selection's bounding-box centre and never reads the 3D cursor
  or a view; ``center_override`` (world space) is the only other pivot.
  Snapping, auto-merge and attribute correction are pinned off, orientations
  that read the view or the cursor are refused, and proportional editing is off
  unless requested, then bounded by the element budget alone (falloff by a
  KD-tree or a mesh walk, measured linear; see the note above ``WORK_BOUNDS``).
  They wait while an artist's modal tool runs (``program_jobs``).
* Grease Pencil stroke operators run in Edit mode on the drawings of the
  current frame; hair ``curves`` and text (``font``) operators in their Edit
  modes. All edit only the staged data, which commit swaps. Options whose
  growth or time has no bound are refused by value (``REFUSED_VALUES``) and
  the result must fit the native point budget.
* ``constraint`` operators name one of the target's own constraints
  (``constraint``, a hidden RNA property they would otherwise read from the
  editor) and commit through the ``constraints`` family (the whole stack by
  name, journaled); bone constraints stay out, and so does any stack with an
  Armature constraint, whose target list commit and rollback cannot carry.
* Data-local ``object`` operators: skin marks, transform clears and applies
  whose effect stays on the target (``transforms`` family).
"""

_KEEP, _DOUBLE, _TRIPLE, _GROW = (1, {}), (2, {}), (3, {}), (4, {})
EDIT_TYPES = ("MESH", "CURVE", "LATTICE", "ARMATURE", "GREASEPENCIL", "CURVES")
# Object types whose constraint stack and transforms the object operators below may write.
OWNER_TYPES = ("MESH", "CURVE", "FONT", "EMPTY", "LATTICE", "ARMATURE", "CAMERA", "LIGHT", "GREASEPENCIL", "CURVES")


def _module(module, entries):
    return {module + "." + name: spec for name, spec in entries.items()}


def _same(spec, names):
    return {name: spec for name in names.split()}


EDIT_MODULES = {"transform": EDIT_TYPES, "curves": ("CURVES",), "font": ("FONT",)}
OBJECT_MODE_MODULES = {"constraint": OWNER_TYPES}
ALLOWED = {
    **_module("transform", _same(_KEEP, "translate rotate resize mirror push_pull tosphere trackball shrink_fatten "
                                        "edge_slide edge_crease edge_bevelweight vert_crease skin_resize rotate_normal "
                                        "vertex_random tilt bbone_resize")),
    # Stroke operators on the current frame's drawings (Edit mode); layers stay Object-mode operators.
    **_module("grease_pencil", {
        **_same(_KEEP, "delete dissolve stroke_smooth stroke_simplify stroke_switch_direction cyclical_set caps_set "
                       "set_uniform_thickness set_uniform_opacity stroke_material_set set_material set_active_material "
                       "stroke_reset_vertex_color set_handle_type set_corner_type set_start_point reorder stroke_split "
                       "separate_fills join_fills join_selection reset_uvs select_all select_more select_less "
                       "select_linked select_random select_alternate select_ends select_by_stroke_type select_fill "
                       "material_select remove_fill_guides clean_loose move_to_layer active_frame_delete delete_frame "
                       "frame_clean_duplicate delete_breakdown insert_blank_frame frame_duplicate"),
        # A Bezier or Catmull-Rom stroke converted with its handles (or kept as NURBS) triples its points.
        "set_curve_type": _TRIPLE,
        **_same(_DOUBLE, "duplicate extrude"), "stroke_subdivide": (1, {"number_cuts": "linear"})}),
    **_module("curves", {
        **_same(_KEEP, "cyclic_toggle delete handle_type_set select_all select_ends select_less select_linked "
                       "select_more select_random split switch_direction tilt_clear"),
        "curve_type_set": _TRIPLE,
        **_same(_DOUBLE, "duplicate extrude"), **_same(_GROW, "add_bezier add_circle"),
        "subdivide": (1, {"number_cuts": "linear"})}),
    **_module("font", _same(_KEEP, "text_insert delete line_break case_set case_toggle style_set style_toggle "
                                   "change_character change_spacing select_all textbox_add textbox_remove")),
    **_module("constraint", _same(_KEEP, "delete move_up move_down move_to_index copy apply childof_set_inverse "
                                         "childof_clear_inverse stretchto_reset limitdistance_reset")),
    **_module("object", _same(_KEEP, "skin_loose_mark_clear skin_radii_equalize skin_root_mark visual_transform_apply "
                                     "origin_clear parent_inverse_apply track_clear")),
}
# Grease Pencil operators that run in Edit mode (the module's layer operators run in Object mode).
EDIT_OPERATORS = frozenset(name for name in ALLOWED if name.startswith("grease_pencil."))
# Target types of operators narrower than their module's (mesh-only transforms, curve tilt, bone sizes).
_MESH_TRANSFORMS = ("shrink_fatten edge_slide edge_crease edge_bevelweight vert_crease skin_resize rotate_normal "
                    "vertex_random")
OPERATOR_TARGETS = {
    **{"transform." + name: ("MESH",) for name in _MESH_TRANSFORMS.split()},
    "transform.tilt": ("CURVE", "CURVES"), "transform.bbone_resize": ("ARMATURE",),
    **{name: ("GREASEPENCIL",) for name in EDIT_OPERATORS},
    **{"object." + name: ("MESH",) for name in ("skin_loose_mark_clear", "skin_radii_equalize", "skin_root_mark")},
    **{"object." + name: OWNER_TYPES for name in ("visual_transform_apply", "origin_clear", "parent_inverse_apply",
                                                   "track_clear")},
}
# Families commit transfers, when not the module's default (object and data).
OPERATOR_FAMILIES = {
    **{"constraint." + name: ["object", "constraints"] for name in ("delete", "move_up", "move_down", "move_to_index",
                                                                    "copy", "childof_set_inverse", "childof_clear_inverse",
                                                                    "stretchto_reset", "limitdistance_reset")},
    "constraint.apply": ["object", "transforms", "constraints"],
    "object.visual_transform_apply": ["object", "transforms"], "object.origin_clear": ["object", "transforms"],
    "object.parent_inverse_apply": ["object", "transforms"], "object.track_clear": ["object", "transforms", "constraints"],
}
# Hidden RNA properties an agent may set: the transform pivot and the constraint an operator edits.
HIDDEN_SETTABLE = {"transform": frozenset({"center_override"}), "constraint": frozenset({"constraint"})}
# Properties the kernel always passes when an operator has them (the agent cannot change them): snapping,
# auto-merge and projection never read the artist's settings or a view; constraint operators edit the
# object's own stack and do not report.
PINNED = {"transform": {"snap": False, "use_automerge_and_split": False, "use_snap_project": False},
          "constraint": {"owner": "OBJECT", "report": False}}
# Defaults the kernel passes unless the agent sets the property, so nothing falls back to a tool setting.
DEFAULTS = {"transform": {"orient_type": "GLOBAL", "use_proportional_edit": False, "use_proportional_connected": False,
                          "use_proportional_projected": False, "proportional_edit_falloff": "SMOOTH",
                          "proportional_size": 1.0, "mirror": False, "correct_uv": False},
            "grease_pencil": {"subdivide_cyclic_segment": False}}  # Closing a stroke adds no points.
_VIEW = "VIEW_STATE: reads the view or the 3D cursor"
# Values refused by reason: (module or operator, property, values, "CODE: why"), checked before anything runs.
REFUSED_VALUES = (
    ("transform", "orient_type", ("VIEW", "CURSOR"), _VIEW),
    ("transform", "use_proportional_projected", (True,), _VIEW), ("transform", "use_snap_project", (True,), _VIEW),
    # Resampling adds a point per length step: four 0.75 m strokes of 16 points became 300,004 at 1e-5.
    ("grease_pencil.stroke_simplify", "mode", ("SAMPLE",), "UNBOUNDED_GROWTH: resampling adds a point per length "
                                                           "step; use FIXED, or stroke_subdivide to add points"),
    # Ramer-Douglas-Peucker is quadratic in one stroke's points: a 40,000-point zigzag stroke took 8.3 s.
    ("grease_pencil.stroke_simplify", "mode", ("ADAPTIVE",), "SUPERLINEAR: adaptive simplification takes time "
                                                             "growing with the square of a stroke's points; use FIXED"),
    # Merging reads per-evaluated-point lengths as per-point ones (Blender asserts on NURBS and Catmull-Rom strokes).
    ("grease_pencil.stroke_simplify", "mode", ("MERGE",), "UPSTREAM_DEFECT: merging misreads curved strokes' lengths; "
                                                          "use FIXED"),
    # Matching point density cuts the closing segment by the stroke's density, unbounded for NURBS strokes.
    ("grease_pencil.cyclical_set", "subdivide_cyclic_segment", (True,),
     "UNBOUNDED_GROWTH: matching point density adds points without bound; close, then stroke_subdivide"),
)
# Proportional editing needs no bound of its own: falloff distances use a KD-tree or a mesh walk, measured at
# 0.8 s at most on the 100,000-vertex budget (native_coverage.rst, "operator_run work bounds").
# Superlinear operators of these families, bounded like action_operator_spec.WORK_BOUNDS: elements (stroke
# points) x selected elements, at the work the worst measured input (two-point strokes) takes about 2 s for.
WORK_BOUNDS = {"grease_pencil.extrude": 7e9}
# Operators that recurse once per connected vertex (skin_root_clear): a 10,000-vertex chain overflowed the main
# thread's stack and crashed the app, 5,000 did not. At most this many mesh vertices.
RECURSION_BOUNDS = {"object.skin_root_mark": 2048}
# Integer properties of these families that select or seed rather than amplify.
BENIGN_INTS = frozenset({"index", "delta", "amount_start", "amount_end"})
DENIED = {
    **dict.fromkeys(("transform.bend", "transform.vertex_warp"),
                    "VIEW_STATE: works around the 3D cursor in view space"),
    "transform.shear": "VIEW_STATE: needs an editor area and its view axes",
    "transform.vert_slide": "CURSOR_PICK: the slide direction follows the mouse",
    "transform.from_gizmo": "VIEW_STATE: depends on an editor's view, cursor or gizmo",
    **dict.fromkeys(("transform.create_orientation", "transform.delete_orientation", "transform.select_orientation"),
                    "SCENE_STATE: edits the scene's transform orientations"),
    "transform.transform": "COVERED: use translate, rotate, resize or another specific transform operator",
    **dict.fromkeys(("grease_pencil.separate", "curves.separate"), "NEW_OBJECTS: creates objects"),
    **dict.fromkeys(("grease_pencil.copy", "grease_pencil.paste", "font.text_copy", "font.text_cut", "font.text_paste"),
                    "CLIPBOARD: shared clipboard state outside the stage"),
    **dict.fromkeys(("grease_pencil.snap_to_cursor", "grease_pencil.snap_cursor_to_selected", "grease_pencil.snap_to_grid",
                     "grease_pencil.reproject", "grease_pencil.outline"),
                    "VIEW_STATE: depends on an editor's view, cursor or gizmo"),
    **dict.fromkeys(("grease_pencil.material_hide", "grease_pencil.material_isolate", "grease_pencil.material_lock_all",
                     "grease_pencil.material_lock_unselected", "grease_pencil.material_lock_unused",
                     "grease_pencil.material_reveal", "grease_pencil.material_unlock_all"),
                    "SHARED_DATA: writes Grease Pencil materials, which other objects may use"),
    **dict.fromkeys(("grease_pencil.material_copy_to_object", "curves.snap_curves_to_surface", "curves.surface_set",
                     "curves.convert_to_particle_system", "curves.convert_from_particle_system",
                     "constraint.copy_to_selected", "object.constraints_copy", "object.modifiers_copy_to_selected",
                     "object.modifier_copy_to_selected", "object.vertex_parent_set", "object.track_set",
                     "object.parent_no_inverse_set"),
                    "OTHER_OBJECTS: reads or writes other selected objects"),
    "constraint.followpath_path_animate": "OTHER_OBJECTS: animates the path curve the constraint follows",
    **dict.fromkeys(("constraint.objectsolver_set_inverse", "constraint.objectsolver_clear_inverse"),
                    "MOTION_TRACKING: reads the scene's movie clip"),
    **dict.fromkeys(("curves.select_linked_pick", "curves.pen", "curves.draw", "grease_pencil.pen", "font.select_word",
                     "font.selection_set"), "CURSOR_PICK: picks by mouse or view coordinates"),
    **dict.fromkeys(("font.move", "font.move_select"),
                    "TEXT_CURSOR: moves only the text cursor, which no revision observes; select ALL or NONE instead"),
    **dict.fromkeys(("font.open", "font.text_paste_from_file"), "FILE: reads a file"),
    "font.text_insert_unicode": "INTERACTIVE: opens a character picker",
    **dict.fromkeys(("object.correctivesmooth_bind", "object.laplaciandeform_bind", "object.meshdeform_bind",
                     "object.surfacedeform_bind"),
                    "MODIFIER_BIND_DATA: bind data is not an RNA setting, so commit could not carry it"),
    **dict.fromkeys(("object.paths_calculate", "object.paths_update", "object.paths_update_visible", "object.paths_clear"),
                    "FRAME_STEPPING: motion paths evaluate the scene over a frame range"),
    # No bound or only a lossy rollback: denied rather than bounded (no measured demand).
    "grease_pencil.select_similar": "SUPERLINEAR: compares every unselected point with every selected value "
                                    "(32,000 points: 0.57 s, quadratic); select by material, layer or stroke type",
    "grease_pencil.convert_curve_type": "UNBOUNDED_GROWTH: resamples curved strokes at their resolution (up to "
                                        "10,000 points a segment); use set_curve_type",
    "grease_pencil.stroke_merge_by_distance": "UPSTREAM_DEFECT: misreads curved strokes' lengths (Blender asserts on "
                                              "NURBS and Catmull-Rom strokes); use stroke_simplify FIXED or dissolve",
    "curves.attribute_set": "UNBOUNDED_GROWTH: writes the active attribute, which can be the curves' resolution "
                            "(evaluated points per segment, clamped only below: 1e9 crashed Blender) or type; use "
                            "reviewed Python",
    "object.modifiers_clear": "COVERED: modifier_remove removes modifiers by name under its own rules; clearing the "
                              "stack at once would drop Multires, bind, hook, particle or cache data a failed commit "
                              "cannot rebuild",
}
