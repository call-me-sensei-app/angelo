# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom normals survive a baked transform: the typed ``transform_apply``, a script's ``Mesh.transform()`` with
``flip_normals()``, and Blender's own Apply (#121831; upstream fixed only the operator, 8306860f963).

Tangent-space custom normals are stored relative to each corner's fan of faces, whose angles a non-uniform scale
changes and whose winding a mirror reverses, so baking such a matrix used to turn them into shading artefacts.
Every case compares, per face and vertex, each corner's world-space normal before and after the bake (the shading
must not change) and checks the faces still face outwards:

* scales (1, 2, 0.5), (-1, 1, 1), (-1, -1, 2) and a rotated (1, 3, -0.5), on a cube with tangent-space normals
  pointing away from its centre, through the three paths;
* held out: the same with free (float) custom normals, which keep their storage; a mesh with a shape key; a mesh
  without custom normals (none are added); and glTF and FBX imports of a mesh with custom normals.

Run only in a disposable instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import traceback
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-custom-normals-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]
TOLERANCE_DEGREES = 0.25   # Tangent-space storage is 16-bit; the bug is tens of degrees.
SCALES = {"stretched": ((1, 2, 0.5), (0, 0, 0)), "mirrored": ((-1, 1, 1), (0, 0, 0)),
          "two_axes_mirrored": ((-1, -1, 2), (0, 0, 0)), "rotated_mirrored": ((1, 3, -0.5), (0.4, -0.3, 0.9))}


def check(name, condition):
    report["checks"][name] = bool(condition)


# -- Meshes ------------------------------------------------------------------------------------------------------

def cube(name, normals="TANGENT", shape_key=False):
    """A beveled cube whose custom normals point away from its centre (never its face normals), as an imported
    smooth-shaded asset's do."""
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.active_object
    obj.name = name
    bevel = obj.modifiers.new("Bevel", 'BEVEL')
    bevel.width, bevel.segments = 0.3, 2
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    mesh = obj.data
    away = [vertex.co.normalized() for vertex in mesh.vertices]
    if normals == "TANGENT":
        mesh.normals_split_custom_set_from_vertices(away)
    elif normals == "FREE":
        layer = mesh.attributes.new("custom_normal", 'FLOAT_VECTOR', 'CORNER')
        layer.data.foreach_set("vector", [c for corner in mesh.loops for c in away[corner.vertex_index]])
    if shape_key:
        obj.shape_key_add(name="Basis")
        key = obj.shape_key_add(name="Pinch")
        for point in key.data:
            point.co *= 0.9
    return obj


def storage(mesh):
    layer = mesh.attributes.get("custom_normal")
    return None if layer is None else layer.data_type


def world_normals(obj):
    """``{(face, vertex): world-space corner normal}``: corners keyed so a flipped winding still matches."""
    bpy.context.view_layer.update()
    mesh = obj.data
    to_world = obj.matrix_world.to_3x3().inverted().transposed()
    normals = mesh.corner_normals
    found = {}
    for face in mesh.polygons:
        for corner in face.loop_indices:
            found[(face.index, mesh.loops[corner].vertex_index)] = (to_world @ Vector(normals[corner].vector)).normalized()
    return found


def faces_outward(obj):
    """Every face's world normal points away from the object's world-space centre."""
    bpy.context.view_layer.update()
    to_world = obj.matrix_world.to_3x3().inverted().transposed()
    centre = obj.matrix_world @ Vector()
    return all((to_world @ face.normal).dot(obj.matrix_world @ face.center - centre) > 0 for face in obj.data.polygons)


def worst_angle(before, after):
    if before.keys() != after.keys():
        return 180.0
    return max(math.degrees(before[key].angle(after[key], math.pi)) for key in before)


# -- The three ways to bake ----------------------------------------------------------------------------------------

def typed_apply(obj):
    queries.invalidate()
    binding = {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}
    operation = {"kind": "transform_apply", "target": queries.object_handle(obj),
                 "parameters": {"location": False, "rotation": True, "scale": True}}
    job = native.begin(window, "angelo_scene_program",
                       validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding],
                                                              "operations": [operation]}))
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            break
    done = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    assert "FINISHED" in done and job.result.get("status") == "committed", job.result


def script_apply(obj):
    """What a script does: Mesh.transform() warns that a negative matrix inverts normals, so it flips after."""
    bake = obj.matrix_basis.to_3x3().to_4x4()
    obj.data.transform(bake, shape_keys=True)
    if bake.determinant() < 0:
        obj.data.flip_normals()
    obj.rotation_euler, obj.scale = (0, 0, 0), (1, 1, 1)


def operator_apply(obj):
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)


PATHS = {"typed": typed_apply, "script": script_apply, "operator": operator_apply}


def bake_case(name, obj, path, scale, rotation):
    obj.scale, obj.rotation_euler = scale, rotation
    before, kind = world_normals(obj), storage(obj.data)
    try:
        PATHS[path](obj)
    except Exception as error:   # One path refusing never hides the other cases.
        report["cases"][name] = {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
        check(name, False)
        bpy.data.objects.remove(obj)
        return
    after = world_normals(obj)
    row = {"worst_degrees": round(worst_angle(before, after), 4), "storage": [kind, storage(obj.data)],
           "outward": faces_outward(obj), "applied": tuple(obj.scale) == (1, 1, 1)}
    report["cases"][name] = row
    check(name, row["worst_degrees"] < TOLERANCE_DEGREES and row["outward"] and row["applied"] and kind == row["storage"][1])
    bpy.data.objects.remove(obj)


def imported(kind):
    """A mesh with custom normals as an importer makes it: exported and read back as glTF or FBX."""
    source = cube("Exported")
    path = OUT / ("custom-normals." + ("glb" if kind == "gltf" else "fbx"))
    for other in bpy.context.view_layer.objects:
        other.select_set(other == source)
    if kind == "gltf":
        bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, export_normals=True)
    else:
        bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, mesh_smooth_type='OFF')
    bpy.data.objects.remove(source)
    names = set(bpy.data.objects.keys())
    (bpy.ops.import_scene.gltf if kind == "gltf" else bpy.ops.import_scene.fbx)(filepath=str(path))
    obj = next(o for o in bpy.data.objects if o.name not in names and o.type == 'MESH')
    obj.parent = None
    obj.matrix_world = Matrix.Identity(4)
    assert storage(obj.data) is not None, kind + " import kept no custom normals"
    return obj


def render(path):
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.render.resolution_x = scene.render.resolution_y = 256
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixels = list(image.pixels)
    bpy.data.images.remove(image)
    return pixels


def rendered_shading_unchanged():
    """What a person sees: an imported asset, stretched and mirrored, rendered before and after the agent's apply."""
    obj = imported("fbx")
    obj.scale, obj.rotation_euler = (-1, 2, 0.6), (0.3, 0.2, 0.5)
    camera_data = bpy.data.cameras.new("QA Camera")
    camera = bpy.data.objects.new("QA Camera", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location, camera.rotation_euler = (6, -6, 4), (1.1, 0, 0.785)
    bpy.context.scene.camera = camera
    before = render(OUT / "shading-before.png")
    try:
        typed_apply(obj)
    except Exception as error:
        report["cases"]["rendered_error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        check("rendered_shading_unchanged_after_the_agents_apply", False)
        return
    after = render(OUT / "shading-after.png")
    difference = sum(abs(a - b) for a, b in zip(before, after)) / len(before)
    report["cases"]["rendered_mean_abs_difference"] = round(difference, 5)
    check("rendered_shading_unchanged_after_the_agents_apply", difference < 0.002)
    bpy.data.objects.remove(obj)
    bpy.data.objects.remove(camera)


def every_attribute_type_is_fingerprinted():
    """A typed program reads its targets exactly (scene_geometry); an attribute type it cannot read refuses the whole
    program, as tangent-space custom normals (INT16_2D, on most imported assets) did. Every type this Blender defines
    is in the one shared table (attribute_types) or named unreadable there, and every reader uses that table."""
    from angelo_agent import attribute_types, export_baked_reads, operator_state, scene_geometry
    defined = {item.identifier for item in bpy.types.Attribute.bl_rna.properties["data_type"].enum_items}
    covered = set(attribute_types.FIELDS) | set(attribute_types.UNREAD)
    report["cases"]["attribute_types_not_covered"] = sorted(defined - covered)
    check("every_attribute_type_is_read_or_named_unreadable", defined <= covered)
    for name, table in (("scene_geometry", scene_geometry.ATTRIBUTE_READS),
                        ("operator_state", operator_state._ATTRIBUTE_LAYOUT),
                        ("export_baked_reads", export_baked_reads.FIELDS)):
        check(name + "_reads_the_shared_table", set(table) == set(attribute_types.FIELDS))
    check("tangent_space_normals_are_read", "INT16_2D" in scene_geometry.ATTRIBUTE_READS)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    every_attribute_type_is_fingerprinted()
    for path in PATHS:
        for label, (scale, rotation) in SCALES.items():
            bake_case("%s_%s_tangent" % (path, label), cube("Cube"), path, scale, rotation)
            bake_case("%s_%s_free" % (path, label), cube("Cube", normals="FREE"), path, scale, rotation)
        bake_case(path + "_shape_keys", cube("Cube", shape_key=True), path, *SCALES["rotated_mirrored"])
        bake_case(path + "_gltf_import", imported("gltf"), path, *SCALES["stretched"])
        bake_case(path + "_fbx_import", imported("fbx"), path, *SCALES["mirrored"])
        plain = cube("Plain", normals=None)
        bake_case(path + "_no_custom_normals", plain, path, *SCALES["rotated_mirrored"])
    rendered_shading_unchanged()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
report["failed"] = [name for name, ok in report["checks"].items() if not ok]
(OUT / "custom-normals.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_CUSTOM_NORMALS", json.dumps({key: report.get(key) for key in ("success", "failed", "paid_requests")}),
      flush=True)
assert report.get("success"), report.get("error") or report["failed"]
