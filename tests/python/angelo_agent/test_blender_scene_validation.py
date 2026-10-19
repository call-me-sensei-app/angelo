# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real isolated Blender kernel + deterministic verification; no inference."""

import contextlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and "--background" in sys.argv, "Use an isolated headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-scene-validation-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
os.environ["ANGELO_CONNECTIONS_DIR"] = str(OUT / "accounts")
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")

from angelo_agent.action_kernels import StagedActionContext, apply_operation
from angelo_agent.action_recipes import expand_recipe
from angelo_agent.scene_validation import VerificationContext, execute, observe, verify
from angelo_agent.scene_queries import object_handle, object_revision

report = {"checks": {}, "paid_requests": 0, "binary": bpy.app.binary_path, "version": bpy.app.version_string,
          "build_features": {"opensubdiv": bool(bpy.app.build_options.opensubdiv)},
          "scope": "disposable_staged_copies_only"}


def expect(name, value):
    report["checks"][name] = bool(value)
    assert value, name


@contextlib.contextmanager
def stage_override(window, scene, view_layer, **members):
    """``window``, then ``scene`` and ``view_layer`` as one pair, as the program executor binds them.

    Given in one call, Blender's ``CTX_wm_window_set`` drops the ``scene`` override and keeps ``view_layer``:
    an operator's view-layer update then evaluates the artist's scene with the stage's view layer and crashes.
    """
    with bpy.context.temp_override(window=window), \
            bpy.context.temp_override(scene=scene, view_layer=view_layer, **members):
        yield


def ids():
    return [block for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.node_groups,
                                     bpy.data.scenes, bpy.data.collections, bpy.data.actions, bpy.data.armatures,
                                     bpy.data.lattices)  # Shape keys go with their mesh.
            for block in collection]


def local_dimensions_cases(window, stage, collection, source):
    """LOCAL dimensions are Blender's ``Object.dimensions``.

    Blender measures the evaluated bounds whenever it deforms the base mesh, also with no modifier listed:
    shape keys and an armature or lattice parent run as virtual modifiers. A staged child of a parent the stage
    does not hold takes that parent's scale. Each case compares the reader with ``obj.dimensions`` after the
    stage's view layer is evaluated (which makes its depsgraph active, so originals hold the evaluated bounds).
    """
    layer = stage.view_layers[0]

    def staged(name, scale=(1, 1, 1)):
        obj = source.copy()
        obj.name, obj.data = name, source.data.copy()
        obj.modifiers.clear()
        obj.scale = scale
        collection.objects.link(obj)
        return obj

    def matches(name, obj):
        layer.update()
        blender = [round(value, 5) for value in obj.dimensions]
        result = verify(VerificationContext({"qa:dims": obj}, list(stage.objects), depsgraph=layer.depsgraph),
                        [{"kind": "dimensions", "target": "qa:dims", "expected": blender, "tolerance": 0.0001,
                          "space": "LOCAL"}])
        report.setdefault("local_dimensions", {})[name] = {"blender": blender, "check": result["checks"][0]}
        expect(name, result["passed"])
        return blender

    keyed = staged("QA LOCAL Shape Keys", (2, 1, 1))
    keyed.shape_key_add(name="Basis", from_mix=False)
    moved = keyed.shape_key_add(name="Key 1", from_mix=False)
    for point in moved.data:
        if point.co.x > 0:
            point.co.x = 3
    moved.value = 1.0
    expect("local_dimensions_shape_keys_deform", abs(matches("local_dimensions_shape_keys_match_blender", keyed)[0]
                                                         - 8) < 0.001)

    lattice = bpy.data.objects.new("QA LOCAL Lattice", bpy.data.lattices.new("QA LOCAL Lattice"))
    lattice.scale = (2, 2, 2)  # Its 2x2x2 points span the cube.
    collection.objects.link(lattice)
    for point in lattice.data.points:
        if point.co[0] > 0:
            point.co_deform[0] = 1.5
    framed = staged("QA LOCAL Lattice Child")
    framed.parent, framed.parent_type = lattice, "LATTICE"
    expect("local_dimensions_lattice_deforms", matches("local_dimensions_lattice_parent_match_blender", framed)[0] > 2.5)

    rig = bpy.data.objects.new("QA LOCAL Rig", bpy.data.armatures.new("QA LOCAL Rig"))
    collection.objects.link(rig)
    with stage_override(window, stage, layer, active_object=rig, object=rig, selected_objects=[rig],
                        selected_editable_objects=[rig]):
        expect("stage_override_binds_scene_and_view_layer",
               bpy.context.scene == stage and bpy.context.view_layer == layer and bpy.context.window == window)
        layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        bone = rig.data.edit_bones.new("Bone")
        bone.head, bone.tail = (0, 0, -1), (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")
    skinned = staged("QA LOCAL Armature Child")
    group = skinned.vertex_groups.new(name="Bone")
    group.add(list(range(len(skinned.data.vertices))), 1.0, "REPLACE")
    skinned.parent, skinned.parent_type = rig, "ARMATURE"
    rig.pose.bones["Bone"].scale = (1, 2, 1)  # Along the bone: world Z from the head at -1 to 3.
    expect("local_dimensions_armature_deforms", matches("local_dimensions_armature_parent_match_blender", skinned)[2]
           > 2.5)

    unstaged = bpy.data.objects.new("QA LOCAL Unstaged Parent", None)  # In no scene: the stage only depends on it.
    unstaged.scale, unstaged.rotation_euler = (3, 1, 1), (0, 0, 0.5)
    child = staged("QA LOCAL Staged Child", (1, 2, 0.5))
    child.parent = unstaged
    matches("local_dimensions_staged_child_of_unstaged_parent_match_blender", child)


def main():
    window = bpy.context.window
    live_scene = window.scene
    source = live_scene.objects["Cube"]
    before = {"coordinates": [list(v.co) for v in source.data.vertices], "scale": list(source.scale),
              "objects": [(obj.name, obj.as_pointer()) for obj in live_scene.objects],
              "selected": [obj.name for obj in bpy.context.selected_objects], "active": bpy.context.view_layer.objects.active}
    original_ids = {block.as_pointer() for block in ids()}
    stage = bpy.data.scenes.new("QA Verification Private Stage")
    collection = bpy.data.collections.new("QA Verification Private Collection")
    stage.collection.children.link(collection)
    target = source.copy()
    target.name = "QA Verification Private Copy"
    target.data = source.data.copy()
    collection.objects.link(target)
    context = StagedActionContext(stage, collection, objects={"qa:source": target}, owned_ids=[target, target.data])
    validation = VerificationContext({"qa:source": target}, list(stage.objects))
    goal = [{"kind": "dimensions", "target": "qa:source", "expected": [4, 1, 3], "tolerance": 0.00001}]
    try:
        expect("initial_goal_genuinely_unmet", verify(validation, goal)["status"] == "fail")
        fit = expand_recipe("fit-dimensions", "qa:source", {"dimensions": [4, 1, 3], "minimum_scale": 0.1,
                            "maximum_scale": 4, "maximum_scale_ratio": 2}, observe(validation, "qa:source"))
        with stage_override(window, stage, stage.view_layers[0]):
            result = apply_operation(context, fit["operations"][0])
        stage.view_layers[0].update()
        fulfilled = verify(validation, goal)
        report["real_operation"] = result
        report["goal_verification"] = fulfilled
        expect("real_native_operation_fulfills_goal", fulfilled["success"])
        expect("real_analytic_fit_recipe_verified", verify(validation, fit["expected_checks"])["success"])
        expect("native_transform_is_read_back", list(target.scale) == [2, 0.5, 1.5])
        recipe = expand_recipe("material-setup", "qa:source", {"name": "QA Verification Principled", "base_color": [0.25, 0.5, 0.75, 1], "roughness": 0.3, "metallic": 0.4})
        for operation in recipe["operations"]:
            apply_operation(context, operation)
        stage.view_layers[0].update()
        expect("real_material_recipe_verified", verify(validation, recipe["expected_checks"])["success"])
        recipe = expand_recipe("uv-prep", "qa:source", {"method": "CUBE", "name": "QA Verification UV"})
        for operation in recipe["operations"]:
            apply_operation(context, operation)
        expect("real_uv_recipe_verified", verify(validation, recipe["expected_checks"])["success"])
        recipe = expand_recipe("bevel-and-shade", "qa:source", {"width": 0.1, "segments": 2})
        for operation in recipe["operations"]:
            apply_operation(context, operation)
        stage.view_layers[0].update()
        report["bevel_shade_verification"] = verify(validation, recipe["expected_checks"])
        expect("real_bevel_and_shade_recipe_verified", report["bevel_shade_verification"]["success"])
        sampled = observe(validation, "qa:source")
        expect("typed_observation_no_geometry_dump", "coordinates" not in json.dumps(sampled) and sampled["mesh"]["vertices"] == 8)
        clean_target = source.copy()
        clean_target.name = "QA Verification Clean Copy"
        clean_target.data = source.data.copy()
        collection.objects.link(clean_target)
        context.admit_object("qa:clean", clean_target)
        validation.targets["qa:clean"] = clean_target
        clean_target.data.vertices[1].co = clean_target.data.vertices[0].co
        clean_target.data.update()
        cleanup = expand_recipe("clean-mesh", "qa:clean", {"vertex_indices": [0, 1], "distance": 0.001, "manifold": "CLOSED"})
        expect("real_cleanup_goal_initially_unmet", verify(validation, cleanup["expected_checks"])["status"] == "fail")
        for operation in cleanup["operations"]:
            apply_operation(context, operation)
        stage.view_layers[0].update()
        expect("real_explicit_cleanup_recipe_verified", verify(validation, cleanup["expected_checks"])["success"] and len(clean_target.data.vertices) == 7)
        wrong = [{"kind": "dimensions", "target": "qa:source", "expected": [400, 100, 300], "tolerance": 0.00001}]
        expect("invalid_result_rejected_not_renamed_success", verify(validation, wrong)["status"] == "fail")
        target.data.vertices[0].co.x = float("nan")
        corrupt = verify(validation, [{"kind": "finite_coordinates", "target": "qa:source"}])
        expect("nonfinite_real_mesh_rejected", corrupt["status"] == "fail")
        json.dumps(corrupt, allow_nan=False)
        local_dimensions_cases(window, stage, collection, source)
        handle, revision = object_handle(source), object_revision(window, source)
        checked = execute(window, "angelo_scene_verify", {"read_set": [{"handle": handle, "revision": revision}],
                           "checks": [{"kind": "dimensions", "target": handle, "expected": [2, 2, 2], "tolerance": 0}]})
        expect("public_readonly_checks_exact_revision_binding", checked["success"] and checked["checks"][0]["revision"] == revision)
        try:
            execute(window, "angelo_scene_verify", {"read_set": [{"handle": handle, "revision": "0" * 64}],
                    "checks": [{"kind": "object_type", "target": handle, "expected": "MESH"}]})
        except ValueError:
            expect("stale_public_revision_rejected", True)
        else:
            expect("stale_public_revision_rejected", False)
    finally:
        # Only the IDs allocated in this explicit disposable test are removed.
        allocated = [block for block in ids() if block.as_pointer() not in original_ids]
        bpy.data.batch_remove(ids=allocated)
    expect("source_geometry_unchanged", before["coordinates"] == [list(v.co) for v in source.data.vertices])
    expect("source_transform_unchanged", before["scale"] == list(source.scale))
    expect("user_scene_objects_unchanged", before["objects"] == [(obj.name, obj.as_pointer()) for obj in live_scene.objects])
    expect("window_selection_active_preserved", window.scene == live_scene and before["selected"] == [obj.name for obj in bpy.context.selected_objects]
           and before["active"] == bpy.context.view_layer.objects.active)


try:
    main()
except Exception:
    report["error"] = traceback.format_exc()
finally:
    report["success"] = not report.get("error") and all(report["checks"].values())
    (OUT / "scene-validation.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("ANGELO_SCENE_VALIDATION", json.dumps(report, allow_nan=False), flush=True)
assert report["success"], report.get("error", report)
