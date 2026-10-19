# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run's Edit-mode families through real staged programs: transforms, Grease Pencil, hair and text.

* ``transform`` operators with explicit values on selected elements: the
  pivot is the selection's bounding-box centre or ``center_override``, never
  the live 3D cursor; live snapping, proportional editing, pivot and
  orientation settings are neither read nor written; proportional editing is
  bounded; view orientations, hidden settings and a missing window are
  refused; PREVIEW and accept.
* Grease Pencil stroke operators on an object with a Grease Pencil material
  (now bindable: its stroke and fill settings join the revision), hair curves
  and text, each committed with one Undo step and Redo, with stale manual
  edits refused; objects whose Grease Pencil or hair modifiers have no native
  evaluation bound are refused before staging.
* Stroke growth: resampling, adaptive simplification, merge simplification,
  density-matching closes, curve-type resampling, similar selection and merge
  by distance are refused by value or name before anything runs (unbounded
  growth, quadratic time, an upstream size mismatch), and so is setting a hair
  attribute (the active one can be the curves' resolution); closing a stroke
  keeps its points; any stroke or hair operator whose result exceeds the
  native point budget is refused.

Run only in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-operator-run-families"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import action_operators, native_programs as native, scene_queries as queries  # noqa: E402
from operator_run_qa import Driver  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
qa = Driver(report)


def rounded(values, digits=5):
    return [round(value, digits) for value in values]


def vertices(name):
    return [rounded(vertex.co) for vertex in qa.objects()[name].data.vertices]


def link(obj):
    qa.window.scene.collection.objects.link(obj)
    return obj


def grid(name, size):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=size, y_segments=size, size=1)
    bm.to_mesh(mesh)
    bm.free()
    return link(bpy.data.objects.new(name, mesh))


def sketch(name, material=True):
    data = bpy.data.grease_pencils.new(name)
    drawing = data.layers.new("Ink", set_active=True).frames.new(qa.window.scene.frame_current).drawing
    drawing.add_strokes([4, 6])
    for index, point in enumerate(drawing.attributes["position"].data):
        point.vector = (index * .1, (index % 2) * .1, 0)
    obj = link(bpy.data.objects.new(name, data))
    if material:
        style = bpy.data.materials.new(name + " Ink")
        bpy.data.materials.create_gpencil_data(style)
        obj.data.materials.append(style)
    return obj


def setup():
    live = qa.window.scene
    live.cursor.location = (5, 5, 5)  # A pivot that read the live cursor would show.
    settings = live.tool_settings
    settings.use_proportional_edit = settings.use_snap = settings.use_mesh_automerge = True
    settings.transform_pivot_point = "CURSOR"
    live.transform_orientation_slots[0].type = "LOCAL"
    grid("QA Grid", 240)
    sketch("QA Sketch")
    hair = bpy.data.hair_curves.new("QA Hair")
    hair.add_curves([4, 4, 4])
    for index, point in enumerate(hair.points):
        point.position = (index * .1, 0, (index % 4) * .2)
    link(bpy.data.objects.new("QA Hair", hair))
    text = bpy.data.curves.new("QA Text", "FONT")
    text.body = "Angelo"
    link(bpy.data.objects.new("QA Text", text))
    bpy.ops.ed.undo_push(message="operator_run families QA")
    queries.invalidate()
    report["live_settings_before"] = live_settings()


def live_settings():
    live = qa.window.scene
    settings = live.tool_settings
    return [list(live.cursor.location), settings.use_proportional_edit, settings.use_snap, settings.use_mesh_automerge,
            settings.transform_pivot_point, live.transform_orientation_slots[0].type, list(settings.mesh_select_mode)]


def centroid(points):
    return [sum(point[axis] for point in points) / len(points) for axis in range(3)]


def transforms():
    cube = qa.objects()["Cube"]
    top = [index for index, vertex in enumerate(cube.data.vertices) if vertex.co.z > 0]
    face = next(face.index for face in cube.data.polygons if face.normal.z > .9)
    before = vertices("Cube")
    after = qa.applied("translate_selected_face", [qa.op(cube, "transform.translate", select="INDICES",
                                                         face_indices=[face], properties={"value": [0, 0, 1]})],
                       [cube], vertices, lambda after: all(
                           after["Cube"][index] == rounded([*before[index][:2], before[index][2] + (1 if index in top else 0)])
                           for index in range(len(before))))
    report["translate"] = after
    # Rotation about an explicit pivot: the cube's centre moves around (1, 0, 0), not the origin or the cursor.
    qa.applied("rotate_about_center_override", [qa.op(qa.objects()["Cube"], "transform.rotate", properties={
        "value": math.pi / 2, "orient_axis": "Z", "center_override": [1, 0, 0]})], [qa.objects()["Cube"]], vertices,
        lambda after: rounded(centroid(after["Cube"]), 4) in ([1, 1, 0], [1, -1, 0]))
    # The default pivot is the selection's bounding-box centre: a scaled top face keeps its centre.
    qa.applied("resize_about_selection_bounds", [qa.op(qa.objects()["Cube"], "transform.resize", select="INDICES",
                                                       face_indices=[face], properties={"value": [2, 2, 1]})],
               [qa.objects()["Cube"]], vertices,
               lambda after: rounded(centroid([after["Cube"][index] for index in top]), 4) == [0, 0, 1]
               and max(abs(after["Cube"][index][0]) for index in top) == 2)
    check_live_settings("transforms")


def check_live_settings(label):
    qa.check(label + "_live_tool_settings_unchanged", live_settings() == report["live_settings_before"])


def transform_refusals():
    cube, grid_object = qa.objects()["Cube"], qa.objects()["QA Grid"]
    qa.check("view_orientation_refused", qa.refused_by_validation(
        [qa.op(cube, "transform.translate", properties={"value": [1, 0, 0], "orient_type": "VIEW"})], [cube], "VIEW_STATE"))
    qa.check("projected_falloff_refused", qa.refused_by_validation(
        [qa.op(cube, "transform.resize", properties={"use_proportional_projected": True})], [cube], "VIEW_STATE"))
    qa.check("cursor_bend_denied", qa.refused_by_validation([qa.op(cube, "transform.bend")], [cube], "VIEW_STATE"))
    qa.refused("hidden_snap", [qa.op(cube, "transform.translate", properties={"value": [1, 0, 0], "snap": True})],
               [cube], "PROPERTY_NOT_SETTABLE")
    with patch.object(action_operators, "program_window", lambda: None):
        qa.refused("no_window", [qa.op(cube, "transform.translate", properties={"value": [1, 0, 0]})], [cube], "NO_WINDOW")
    # Proportional editing on a 58,081-vertex grid (falloff by KD-tree or mesh walk: bounded by the vertex budget).
    center = min(range(len(grid_object.data.vertices)), key=lambda index: grid_object.data.vertices[index].co.length)
    qa.applied("proportional_one_vertex", [qa.op(grid_object, "transform.translate", select="INDICES",
                                                 vertex_indices=[center], properties={
                                                     "value": [0, 0, 1], "use_proportional_edit": True,
                                                     "proportional_size": .05})],
               [grid_object], lambda name: round(sum(vertex.co.z for vertex in qa.objects()[name].data.vertices), 4),
               lambda after: after["QA Grid"] > 1.0)
    check_live_settings("transform_refusals")


def preview_accept():
    cube = qa.objects()["Cube"]
    before = vertices("Cube")
    result = qa.run(qa.program([qa.op(cube, "transform.translate", properties={"value": [0, 2, 0]})], writes=[cube],
                               mode="PREVIEW"))[1]
    qa.check("transform_preview_without_live_edit", result.get("status") == "preview" and vertices("Cube") == before
             and no_stages())
    accepted = native.begin(qa.window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    qa.check("transform_preview_accepted", qa.settle(accepted).get("ready_to_commit") and qa.commit(accepted)
             and vertices("Cube") == [rounded([x, y + 2, z]) for x, y, z in before])
    qa.undo()
    qa.check("transform_preview_one_undo", vertices("Cube") == before and no_stages())


def large_sketch(name, strokes, points):
    data = bpy.data.grease_pencils.new(name)
    drawing = data.layers.new("Ink", set_active=True).frames.new(qa.window.scene.frame_current).drawing
    drawing.add_strokes([points] * strokes)
    drawing.attributes["position"].data.foreach_set("vector", [index * 1e-4 for index in range(strokes * points * 3)])
    return link(bpy.data.objects.new(name, data))


def strokes(name):
    data = qa.objects()[name].data
    drawing = data.layers["Ink"].frames[0].drawing
    radius = drawing.attributes.get("radius")
    return [len(drawing.attributes["position"].data), [round(value.value, 5) for value in radius.data] if radius else None,
            [slot.material.name for slot in qa.objects()[name].material_slots]]


def grease_pencil():
    target = qa.objects()["QA Sketch"]
    before = strokes("QA Sketch")
    qa.applied("grease_pencil_strokes", [qa.op(target, "grease_pencil.stroke_subdivide", properties={"number_cuts": 1}),
                                         qa.op(target, "grease_pencil.set_uniform_thickness",
                                               properties={"thickness": .2})],
               [target], strokes, lambda after: after["QA Sketch"][0] > before[0]
               and len(set(after["QA Sketch"][1])) == 1 and after["QA Sketch"][2] == before[2],
               checks=[{"kind": "curve_counts", "points": {"minimum": 18, "maximum": 18},
                        "curves": {"minimum": 2, "maximum": 2}}])

    def edit():
        qa.objects()["QA Sketch"].data.layers["Ink"].frames[0].drawing.attributes["position"].data[0].vector = (9, 9, 9)

    def restore():
        qa.objects()["QA Sketch"].data.layers["Ink"].frames[0].drawing.attributes["position"].data[0].vector = (0, 0, 0)

    qa.stale("grease_pencil", [qa.op(qa.objects()["QA Sketch"], "grease_pencil.stroke_smooth")],
             qa.objects()["QA Sketch"], edit, restore)
    target = qa.objects()["QA Sketch"]  # Python references go stale after Undo.
    qa.check("grease_pencil_separate_denied", qa.refused_by_validation(
        [qa.op(target, "grease_pencil.separate")], [target], "NEW_OBJECTS"))
    # Extruding every point grows faster than linearly: 90,000 selected stroke points exceed its work bound.
    large = large_sketch("QA Sketch Large", 90, 1000)
    bpy.ops.ed.undo_push(message="operator_run families QA large sketch")
    queries.invalidate()
    qa.refused("grease_pencil_extrude_work", [qa.op(large, "grease_pencil.extrude")], [large], "WORK_BOUND")
    modified = sketch("QA Sketch Modified", material=False)
    modified.modifiers.new("Thickness", "GREASE_PENCIL_THICKNESS")
    bpy.ops.ed.undo_push(message="operator_run families QA modifier")
    queries.invalidate()
    try:
        arguments = qa.program([qa.op(modified, "grease_pencil.stroke_smooth")], writes=[modified])
        result = qa.run(arguments)[1]
        refused = result.get("status") == "failed"
        report["grease_pencil_modifier_refusal"] = result.get("error")
    except Exception as error:  # Its modifier keeps the revision inexact, which refuses it even earlier.
        refused = True
        report["grease_pencil_modifier_refusal"] = str(error)
    qa.check("grease_pencil_modifiers_refused", refused and no_stages())


def large_hair(name, curves, points):
    data = bpy.data.hair_curves.new(name)
    data.add_curves([points] * curves)
    data.position_data.foreach_set("vector", [index * 1e-4 for index in range(curves * points * 3)])
    return link(bpy.data.objects.new(name, data))


def stroke_bounds():
    target = qa.objects()["QA Sketch"]
    simplify = "grease_pencil.stroke_simplify"
    for label, code, operator, properties in (
            ("resample", "UNBOUNDED_GROWTH", simplify, {"mode": "SAMPLE", "length": 1e-5}),
            ("adaptive_simplify", "SUPERLINEAR", simplify, {"mode": "ADAPTIVE"}),
            ("merge_simplify", "UPSTREAM_DEFECT", simplify, {"mode": "MERGE"}),
            ("close_matching_density", "UNBOUNDED_GROWTH", "grease_pencil.cyclical_set",
             {"subdivide_cyclic_segment": True}),
            ("convert_curve_type", "UNBOUNDED_GROWTH", "grease_pencil.convert_curve_type", {"type": "POLY"}),
            ("select_similar", "SUPERLINEAR", "grease_pencil.select_similar", {}),
            ("merge_by_distance", "UPSTREAM_DEFECT", "grease_pencil.stroke_merge_by_distance", {})):
        qa.check("stroke_%s_refused" % label, qa.refused_by_validation(
            [qa.op(target, operator, properties=properties)], [target], code))
    before = strokes("QA Sketch")
    qa.applied("grease_pencil_close_keeps_points", [qa.op(target, "grease_pencil.cyclical_set",
                                                          properties={"type": "CLOSE"})],
               [target], strokes, lambda after: after["QA Sketch"][0] == before[0])
    # 60,000 points pass the estimate for a duplicate (at most 2x); the 120,000 it makes exceed the point budget.
    sketch_budget, hair_budget = large_sketch("QA Sketch Budget", 60, 1000), large_hair("QA Hair Budget", 60, 1000)
    bpy.ops.ed.undo_push(message="operator_run families QA point budget")
    queries.invalidate()
    qa.refused("grease_pencil_point_budget", [qa.op(sketch_budget, "grease_pencil.duplicate")], [sketch_budget],
               "GROWTH_BOUND")
    hair_budget = qa.objects()["QA Hair Budget"]
    qa.refused("hair_point_budget", [qa.op(hair_budget, "curves.duplicate")], [hair_budget], "GROWTH_BOUND")


def hair_points(name):
    data = qa.objects()[name].data
    return [len(data.points), len(data.curves), [rounded(point.position, 4) for point in data.points][:4]]


def hair_curves():
    target = qa.objects()["QA Hair"]
    qa.applied("hair_curves", [qa.op(target, "curves.subdivide", properties={"number_cuts": 1}),
                               qa.op(target, "curves.switch_direction")],
               [target], hair_points, lambda after: after["QA Hair"][0] == 21 and after["QA Hair"][1] == 3,
               checks=[{"kind": "curve_counts", "points": {"minimum": 21, "maximum": 21},
                        "curves": {"minimum": 3, "maximum": 3}}])

    def edit():
        qa.objects()["QA Hair"].data.points[0].position = (3, 3, 3)

    def restore():
        qa.objects()["QA Hair"].data.points[0].position = (0, 0, 0)

    qa.stale("hair_curves", [qa.op(qa.objects()["QA Hair"], "curves.delete")], qa.objects()["QA Hair"], edit, restore)
    # The active attribute can be the curves' resolution, clamped only below (1e9 crashed Blender's evaluation).
    target = qa.objects()["QA Hair"]
    qa.check("hair_attribute_set_denied", qa.refused_by_validation(
        [qa.op(target, "curves.attribute_set", properties={"value_int": 1000000000})], [target], "UNBOUNDED_GROWTH"))


def body(name):
    data = qa.objects()[name].data
    return [data.body, [item.use_bold for item in data.body_format]]


def text():
    target = qa.objects()["QA Text"]
    qa.applied("text_edits", [qa.op(target, "font.text_insert", select="NONE", properties={"text": " QA"}),
                              qa.op(target, "font.line_break", select="NONE"),
                              qa.op(target, "font.text_insert", select="NONE", properties={"text": "two"}),
                              qa.op(target, "font.case_set", properties={"case": "UPPER"}),
                              qa.op(target, "font.style_set", properties={"style": "BOLD"})],
               [target], body, lambda after: after["QA Text"][0] == "ANGELO QA\nTWO" and all(after["QA Text"][1]),
               checks=[{"kind": "curve_counts", "characters": {"minimum": 13, "maximum": 13},
                        "lines": {"minimum": 2, "maximum": 2}}])
    # A count the type does not have fails, and a wrong count fails the program before anything commits.
    target = qa.objects()["QA Text"]
    job, result = qa.run(qa.program([qa.op(target, "font.line_break", select="NONE")], writes=[target], checks=[
        {"kind": "curve_counts", "lines": {"minimum": 3, "maximum": 3}, "points": {"minimum": 0, "maximum": 9}}]))
    qa.check("curve_counts_refuses_a_count_text_lacks", result.get("status") == "failed" and no_stages()
             and qa.objects()["QA Text"].data.body == "Angelo")
    target = qa.objects()["QA Text"]
    qa.check("text_keep_selection_refused", qa.refused_by_validation(
        [qa.op(target, "font.delete", select="KEEP")], [target], "SELECTION"))
    qa.check("text_cursor_move_denied", qa.refused_by_validation([qa.op(target, "font.move")], [target], "TEXT_CURSOR"))
    qa.check("text_clipboard_denied", qa.refused_by_validation([qa.op(target, "font.text_paste")], [target], "CLIPBOARD"))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    setup()
    transforms()
    transform_refusals()
    preview_accept()
    grease_pencil()
    stroke_bounds()
    hair_curves()
    text()
    check_live_settings("all_families")
    qa.check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "operator-run-families.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_OPERATOR_RUN_FAMILIES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
