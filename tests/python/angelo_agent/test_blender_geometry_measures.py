# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Only a check that can see a geometry change verifies it (``derived_geometry``), in the compiled app.

Earlier builds certified programs whose checks could not see what
changed: a curve made 2D (its points flattened) and a lattice resized (its
points regenerated) with only their property checks, a top face subdivided or
deleted measured by unchanged dimensions; and false partials for selection,
slot and shading operators, a rotation-only transform apply and explicit curve
points. This suite sweeps every settable curve, lattice and mesh data setting
to pin which rewrite points, commits each case, refuses a library material and
a slot hop before approval, and certifies only what a check saw. Run in a
disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-geometry-measures-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.action_catalog import validate_operation  # noqa: E402
from angelo_agent.claude_outcomes import certified_completion  # noqa: E402
from angelo_agent.derived_geometry import POINT_PATHS  # noqa: E402
from angelo_agent.program_completion import capture_program, mint_completion  # noqa: E402
from angelo_agent.rna_rules import PropertyPathError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
HANDLE = "scn:" + "a" * 32 + ":1"


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def handle(obj):
    return queries.object_handle(obj)


def op(kind, target=None, **parameters):
    reference = target if isinstance(target, str) or target is None else handle(target)
    return {"kind": kind, "target": reference, "parameters": parameters}


def fresh():
    """A new scene with a 2 m cube 'Cube' and a 3D poly curve 'Curve' (points at Z 0, 0.5 and 1)."""
    import bmesh
    scene = bpy.data.scenes.new("Measures")
    previous, window.scene = window.scene, scene
    if previous.name.startswith("Measures"):
        doomed = set(previous.objects) | {obj.data for obj in previous.objects if obj.data is not None}
        bpy.data.scenes.remove(previous)
        bpy.data.batch_remove(ids=doomed)
    mesh = bpy.data.meshes.new("Cube")
    shape = bmesh.new()
    bmesh.ops.create_cube(shape, size=2)
    shape.to_mesh(mesh)
    shape.free()
    cube = bpy.data.objects.new("Cube", mesh)
    curve = bpy.data.curves.new("Curve", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(2)
    for index, point in enumerate(spline.points):
        point.co = (index, 0, 0.5 * index, 1)
    curve_object = bpy.data.objects.new("Curve", curve)
    for obj in (cube, curve_object):
        scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    queries.invalidate()
    return cube, curve_object


def certify(job, arguments):
    host = SimpleNamespace(_epoch=1, _request_generation=1, _closing=False, cancelled=False)
    delivery = SimpleNamespace(id="measures", epoch=1, generation=1, name="angelo_scene_program",
                               arguments=arguments, program_arguments=None)
    completion = mint_completion(host, delivery, capture_program(job), job.result)
    return certified_completion([{"tool": "angelo_scene_program", "arguments": arguments}], [job.result],
                                read_only=lambda tool: False, completions=[completion])


def commit_program(name, operations, writes=(), checks=()):
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "write_set": [{"handle": handle(obj), "revision": queries.object_revision(window, obj)}
                                       for obj in writes], "operations": operations, "expected_checks": list(checks)})
    approved = native.precondition(window, "angelo_scene_program", arguments)
    job = native.begin(window, "angelo_scene_program", arguments, approved_obligations=approved.obligations)
    for _ in range(1024):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    check(name + "_stages", staged.get("ready_to_commit"))
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    summary = certify(job, arguments)
    report["cases"][name] = {"status": job.result.get("status"), "verification": job.result.get("verification"),
                             "certified": summary is not None, "error": job.result.get("error")}
    check(name + "_commits", job.result.get("status") == "committed"
          and all(row.get("passed") or row.get("status") == "unobservable" for row in job.result.get("checks", ())))
    return job, summary


def partial(job, summary):
    return job.result.get("verification", {}).get("status") == "partial" and summary is None


def certified(job, summary):
    return "verification" not in job.result and summary is not None and summary.startswith("Host-verified result")


def _changed(data, prop):
    """Another valid value of a plain setting, or None."""
    value = getattr(data, prop.identifier)
    if prop.type == "BOOLEAN" and not getattr(prop, "is_array", False):
        return not value
    if prop.type == "ENUM" and not prop.is_enum_flag:
        return next((item.identifier for item in prop.enum_items if item.identifier != value), None)
    if prop.type in {"INT", "FLOAT"} and not getattr(prop, "is_array", False):
        step = 1 if prop.type == "INT" else 0.25
        for candidate in (value + step, value - step):
            if prop.hard_min <= candidate <= prop.hard_max:
                return candidate
    return None


def _geometry(data):
    if isinstance(data, bpy.types.Lattice):
        return (data.points_u, data.points_v, data.points_w,
                [tuple(round(c, 5) for c in point.co_deform) for point in data.points])
    if isinstance(data, bpy.types.Curve):
        return [(spline.type, [tuple(round(c, 5) for c in point.co) for point in spline.points],
                 [tuple(round(c, 5) for c in (*point.co, *point.handle_left, *point.handle_right))
                  for point in spline.bezier_points]) for spline in data.splines]
    return ([tuple(round(c, 5) for c in vertex.co) for vertex in data.vertices], len(data.edges),
            [tuple(polygon.vertices) for polygon in data.polygons])


def data_sweep():
    """Every settable curve, lattice and mesh data setting: those that rewrite points are POINT_PATHS."""
    curve = bpy.data.curves.new("Sweep Curve", "CURVE")
    curve.dimensions = "3D"
    bezier = curve.splines.new("BEZIER")
    bezier.bezier_points.add(2)
    for index, point in enumerate(bezier.bezier_points):
        point.co = (index, 1, 0.5 * index)
        point.handle_left_type = point.handle_right_type = "AUTO"
    lattice = bpy.data.lattices.new("Sweep Lattice")
    lattice.points_u = lattice.points_v = lattice.points_w = 3
    lattice.points[13].co_deform = (0.2, 0.3, 0.4)  # The inner point.
    mesh = bpy.data.meshes.new("Sweep Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 1)], [], [(0, 1, 2, 3)])
    found, swept = set(), set()
    for data in (curve, lattice, mesh):
        for prop in data.bl_rna.properties:
            if prop.is_readonly or prop.identifier == "rna_type":
                continue
            path, value = "data." + prop.identifier, _changed(data, prop)
            if value is None:
                continue
            try:
                validate_operation({"kind": "property_set", "target": HANDLE, "parameters": {"values": {path: value}}})
            except ValueError:
                continue  # Not a setting property_set writes.
            copy = data.copy()
            try:
                before = _geometry(copy)
                setattr(copy, prop.identifier, value)
                swept.add(path)
                if _geometry(copy) != before:
                    found.add(path)
            except (TypeError, ValueError, AttributeError):
                pass
            finally:
                getattr(bpy.data, {"Curve": "curves", "Lattice": "lattices", "Mesh": "meshes"}[
                    data.bl_rna.identifier]).remove(copy)
    report["cases"]["data_sweep"] = {"swept": len(swept), "rewrite_points": sorted(found)}
    check("the_sweep_reaches_settings", len(swept) > 30)
    check("every_point_rewriting_setting_is_a_point_path", found == set(POINT_PATHS) & swept and found)
    for data in (curve, lattice, mesh):
        getattr(bpy.data, {"Curve": "curves", "Lattice": "lattices", "Mesh": "meshes"}[data.bl_rna.identifier]).remove(data)


def point_paths():
    cube, curve = fresh()
    job, summary = commit_program("curve_flattened_unchecked", [op("property_set", curve, values={"data.dimensions": "2D"})],
                                  writes=[curve])
    check("a_flattened_curve_without_positions_is_partial", partial(job, summary)
          and all(point.co[2] == 0 for point in curve.data.splines[0].points))
    cube, curve = fresh()
    positions = {"kind": "curve_counts", "target": handle(curve), "curves": {"minimum": 1, "maximum": 1},
                 "positions": [[0, 0, 0], [1, 0, 0], [2, 0, 0]]}
    job, summary = commit_program("curve_flattened_positions", [op("property_set", curve, values={"data.dimensions": "2D"})],
                                  writes=[curve], checks=[positions])
    check("its_stated_positions_certify_it", certified(job, summary))


def weak_measures():
    for kind, parameters in (("mesh_subdivide", {"face_selector": {"normal": [0, 0, 1]}, "cuts": 2}),
                             ("mesh_delete", {"face_selector": {"normal": [0, 0, 1]}})):
        cube, _curve = fresh()
        unchanged = {"kind": "dimensions", "target": handle(cube), "expected": [2, 2, 2], "tolerance": .001}
        job, summary = commit_program(kind + "_dimensions", [op(kind, cube, **parameters)], writes=[cube],
                                      checks=[unchanged])
        check("a_" + kind + "_measured_by_unchanged_dimensions_is_partial", partial(job, summary))
        cube, _curve = fresh()
        counted = {"kind": "mesh_counts", "target": handle(cube), **{key: {"minimum": 1, "maximum": 1000}
                                                                    for key in ("vertices", "edges", "faces")}}
        job, summary = commit_program(kind + "_counts", [op(kind, cube, **parameters)], writes=[cube], checks=[counted])
        check("a_" + kind + "_with_a_count_check_is_certified", certified(job, summary))


def no_false_partials():
    box = op("create_primitive", primitive="CUBE", name="Measures Marker", alias="m", size=1, location=[6, 0, 0])
    for operator in ("object.shade_smooth", "object.vertex_group_add", "object.material_slot_add"):
        cube, _curve = fresh()
        job, summary = commit_program(operator, [box, op("operator_run", cube, operator=operator)], writes=[cube])
        check(operator.replace(".", "_") + "_is_certified", certified(job, summary))
    for mode in ("XYZ", "QUATERNION"):
        cube, _curve = fresh()
        cube.rotation_mode = mode
        cube.rotation_euler, cube.rotation_quaternion = (0.3, 0.2, 0.5), (0.9, 0.1, 0.2, 0.3)
        bpy.context.view_layer.update()
        job, summary = commit_program("rotation_apply_" + mode, [op("transform_apply", cube, rotation=True)], writes=[cube])
        check("a_rotation_apply_is_certified_" + mode.lower(), certified(job, summary)
              and tuple(cube.rotation_euler) == (0, 0, 0) and tuple(cube.rotation_quaternion) == (1, 0, 0, 0))
    for spline_type, dimensions in (("BEZIER", None), ("POLY", "2D"), ("NURBS", "3D")):
        cube, curve = fresh()
        job, summary = commit_program("curve_points_" + spline_type, [op(
            "curve_points", curve, points=[[0, 0, 0], [1, 1, 0.5], [2, 0, 1]], spline_type=spline_type,
            dimensions=dimensions)], writes=[curve])
        observed = [row for row in job.result["checks"] if row["kind"] == "curve_counts"][0]["checks"][0]["observed"]
        check("curve_points_positions_are_certified_" + spline_type.lower(), certified(job, summary)
              and observed["positions"][1][2] == (0 if dimensions == "2D" else 0.5))


def keyframe_sweep():
    """Every object and data setting property_keyframe admits is one RNA can animate (NOT_ANIMATABLE pins the rest)."""
    admitted, refused = [], []
    types = ((bpy.types.Object, ""), (bpy.types.Mesh, "data."), (bpy.types.Curve, "data."), (bpy.types.TextCurve, "data."),
             (bpy.types.Camera, "data."), (bpy.types.PointLight, "data."), (bpy.types.SpotLight, "data."),
             (bpy.types.AreaLight, "data."), (bpy.types.SunLight, "data."), (bpy.types.Lattice, "data."),
             (bpy.types.Armature, "data."), (bpy.types.MetaBall, "data."), (bpy.types.SurfaceCurve, "data."))
    for rna, prefix in types:
        for prop in rna.bl_rna.properties:
            if prop.is_readonly or prop.type not in {"BOOLEAN", "INT", "FLOAT", "ENUM"}:
                continue
            value = {"BOOLEAN": False, "INT": 0, "FLOAT": 0.5}.get(prop.type) if prop.type != "ENUM" else (
                prop.enum_items[0].identifier if len(prop.enum_items) else "X")
            if getattr(prop, "is_array", False) and prop.array_length:
                value = [value] * prop.array_length
            try:
                validate_operation({"kind": "property_keyframe", "target": HANDLE,
                                    "parameters": {"values": {prefix + prop.identifier: value}, "frame": 1}})
            except ValueError:
                continue
            admitted.append(prefix + prop.identifier)
            if not prop.is_animatable:
                refused.append(prefix + prop.identifier + " (" + rna.bl_rna.identifier + ")")
    report["cases"]["keyframe_sweep"] = {"admitted": len(admitted), "not_animatable": refused}
    check("every_admitted_keyframe_path_is_animatable", admitted and not refused)


def classes_observed():
    """A move is certified by a check that sees positions, never by counts alone."""
    translate = lambda cube: op("operator_run", cube, operator="transform.translate", properties={"value": [0, 0, 1]},
                                select="INDICES", vertex_indices=[1, 3, 5, 7], select_mode="VERT")
    cube, _curve = fresh()
    counted = {"kind": "mesh_counts", "target": handle(cube), **{key: {"minimum": 1, "maximum": 100}
                                                                for key in ("vertices", "edges", "faces")}}
    job, summary = commit_program("translate_counts", [translate(cube)], writes=[cube], checks=[counted])
    check("a_move_measured_by_counts_is_partial", partial(job, summary))
    cube, _curve = fresh()
    top = [v.index for v in cube.data.vertices if v.co.z > 0]
    job, summary = commit_program("translate_dimensions", [op(
        "operator_run", cube, operator="transform.translate", properties={"value": [0, 0, 1]}, select="INDICES",
        vertex_indices=top, select_mode="VERT")], writes=[cube],
        checks=[{"kind": "dimensions", "target": handle(cube), "expected": [2, 2, 3], "tolerance": .001}])
    check("a_move_of_some_vertices_is_never_certified_by_bounds", partial(job, summary))  # Round 5: shape.
    cube, _curve = fresh()
    job, summary = commit_program("resize_all_dimensions", [op(
        "operator_run", cube, operator="transform.resize", properties={"value": [2, 2, 2]}, select="ALL")],
        writes=[cube], checks=[{"kind": "dimensions", "target": handle(cube), "expected": [4, 4, 4], "tolerance": .001}])
    check("scaling_everything_is_certified_by_dimensions", certified(job, summary))


def unevaluated_objects():
    """A disabled object's world-space measures come from base data and its parents, or certify
    nothing; never from its stale matrix or cached bounds."""
    cube, _curve = fresh()
    cube.hide_viewport = True
    bpy.context.view_layer.update()
    queries.invalidate()
    job, summary = commit_program("disabled_scale_true_dims", [op("transform", cube, mode="SET", scale=[2, 2, 2])],
                                  writes=[cube], checks=[{"kind": "dimensions", "target": handle(cube),
                                                          "expected": [4, 4, 4], "tolerance": .001}])
    check("a_disabled_objects_true_dimensions_certify", certified(job, summary))
    cube, _curve = fresh()
    cube.hide_viewport = True
    bpy.context.view_layer.update()
    queries.invalidate()
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "write_set": [{"handle": handle(cube), "revision": queries.object_revision(window, cube)}],
        "operations": [op("transform", cube, mode="SET", scale=[2, 2, 2])],
        "expected_checks": [{"kind": "dimensions", "target": handle(cube), "expected": [2, 2, 2], "tolerance": .001}]})
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(1024):
        staged = job.poll()
        if staged.get("final") or staged.get("ready_to_commit"):
            break
    check("its_stale_dimensions_are_refused", staged.get("status") == "failed" and staged.get("error_code") == "CHECK_FAILED")
    parent, child = fresh()[0], None
    child = bpy.data.objects.new("Child", bpy.data.meshes["Cube"].copy())
    window.scene.collection.objects.link(child)
    child.parent, child.hide_viewport = parent, True
    bpy.context.view_layer.update()
    queries.invalidate()
    job, summary = commit_program("parent_scaled_child_disabled", [op("transform", parent, mode="SET", scale=[2, 2, 2])],
                                  writes=[parent, child], checks=[{"kind": "dimensions", "target": handle(child),
                                                            "expected": [4, 4, 4], "tolerance": .001}])
    check("a_disabled_childs_dimensions_follow_its_scaled_parent", certified(job, summary))
    cube, _curve = fresh()
    cube.modifiers.new("Subdivision", "SUBSURF")
    cube.hide_viewport = True
    bpy.context.view_layer.update()
    queries.invalidate()
    job, summary = commit_program("disabled_subsurf_dimensions", [op("transform", cube, mode="SET", scale=[2, 2, 2])],
                                  writes=[cube], checks=[{"kind": "dimensions", "target": handle(cube),
                                                          "expected": [4, 4, 4], "tolerance": .5}])
    check("a_disabled_objects_modifier_bounds_are_unobservable", partial(job, summary) and any(
        row.get("reason") == "unobservable_check" for row in job.result["verification"]["unknowns"]))


def refused_before_approval():
    """A library material and a slot hop never reach an approval."""
    path = str(OUT / "library.blend")
    block = bpy.data.materials.new("Measures Library Paint")
    block.roughness = 0.5
    bpy.data.libraries.write(path, {block}, fake_user=True)
    bpy.data.materials.remove(block)
    with bpy.data.libraries.load(path, link=True) as (_source, target):
        target.materials = ["Measures Library Paint"]
    linked = next(material for material in bpy.data.materials if material.library)
    cube, _curve = fresh()
    cube.data.materials.append(linked)
    queries.invalidate()
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "write_set": [{"handle": handle(cube), "revision": queries.object_revision(window, cube)}],
        "operations": [op("property_set", cube, values={"active_material.roughness": .9})]})
    try:
        native.precondition(window, "angelo_scene_program", arguments)
        refusal = None
    except PropertyPathError as error:
        refusal = error
    check("a_library_material_is_refused_before_approval", refusal is not None and refusal.code == "LINKED_MATERIAL"
          and refusal.refusal_code == "UNSUPPORTED" and no_stages() and abs(linked.roughness - 0.5) < 1e-6)
    try:
        validate_call("angelo_scene_program", {
            "mode": "APPLY", "write_set": [{"handle": handle(cube), "revision": queries.object_revision(window, cube)}],
            "operations": [op("property_set", cube, values={"active_material_index": 0}),
                           op("property_set", cube, values={"active_material.roughness": .9})]})
        hop = None
    except PropertyPathError as error:
        hop = error
    check("a_slot_hop_in_separate_operations_is_refused_accurately", hop is not None
          and hop.code == "ACTIVE_SLOT_CONFLICT" and "separate program" in str(hop) and "BUDGET" not in str(hop))
    # The advised route works in one program: a local copy into the active slot, then its settings.
    job, summary = commit_program("linked_remedy", [
        op("material_assign", cube, material="Measures Library Paint", copy_as="Measures Local Paint", slot=0),
        op("property_set", cube, values={"active_material.roughness": .9})], writes=[cube])
    check("the_linked_material_remedy_commits_in_one_program", cube.active_material.name == "Measures Local Paint"
          and abs(cube.active_material.roughness - .9) < 1e-6 and abs(linked.roughness - 0.5) < 1e-6)
    # An override holder: refused before approval (no staging, no residue on the library material).
    cube, _curve = fresh()
    override = linked.override_create(remap_local_usages=True)
    cube.data.materials.append(override)
    queries.invalidate()
    users = linked.users
    arguments = validate_call("angelo_scene_program", {
        "mode": "APPLY", "write_set": [{"handle": handle(cube), "revision": queries.object_revision(window, cube)}],
        "operations": [op("material_assign", cube, material=override.name, copy_as="Override Local", slot=0),
                       op("property_set", cube, values={"active_material.roughness": .9})]})
    try:
        native.precondition(window, "angelo_scene_program", arguments)
        refusal = None
    except PropertyPathError as error:
        refusal = error
    native.cleanup_all()
    check("an_override_copy_is_refused_before_approval_without_residue", refusal is not None
          and refusal.code == "LINKED_MATERIAL" and no_stages() and linked.users == users)


try:
    bpy.data.batch_remove(ids=list(bpy.data.objects) + list(bpy.data.meshes))  # Factory names would be taken.
    bpy.utils.register_class(commit_operator(report))
    native.register()
    data_sweep()
    keyframe_sweep()
    classes_observed()
    unevaluated_objects()
    point_paths()
    weak_measures()
    no_false_partials()
    refused_before_approval()
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "geometry-measures.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_GEOMETRY_MEASURES", json.dumps({key: report.get(key) for key in ("success",)}), flush=True)
assert report.get("success"), report.get("error")
