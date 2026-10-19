# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script that raised distinguishes a proved Main reload from unverified effects.

Run in an isolated ``--background --factory-startup`` process with Blender's
Undo history started by an explicit push, as the other background suites do.
Each case starts from the factory scene plus a few datablocks an Undo must
bring back (a driver, an instanced collection, a packed image), puts the
person in a mode, runs one reviewed script through the operator the approval
would choose (``script_errors.apply_operator``), and then reads the scene
itself: light, camera, world, render settings, objects, mesh, material, image
settings and pixels, driver, instance and mode.

The cases marked ``restores`` must reload Main and disclose partial recovery;
the ones marked ``remains`` must say that the changes remain. Pixel fixtures
separately show that unchanged metadata never proves unchanged buffers.
A finished script never says ``undoable`` where the person's Undo
keeps any of it, and ``undo_step_cases`` counts the steps: a finished script
is one step with no empty step before it, two scripts are two steps, and a
script that was rolled back leaves one step that changes nothing.
``script_review_fixtures`` adds exits, history replays, linked data, brush
assets and interface data.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import script_errors  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-rollback-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": {}, "cases": {}, "success": False}
LAST = {}
# Nothing here is an object's name, transform or mesh: the read-back that used to stand for the scene saw none of it.
LIGHTS = ("import bpy\n"
          "bpy.data.lights['Light'].energy *= 2\n"
          "bpy.data.objects['Camera'].data.lens = 20.0\n"
          "bpy.data.worlds['World'].color = (1, 0, 0)\n"
          "bpy.context.scene.render.resolution_x = 640\n")
HEAD = LIGHTS + "bpy.data.objects['Cube'].location.x = 5\n"
RAISE = "raise KeyError('Scale')\n"
QA_OPERATORS = {script_errors.APPLY: "qa_script", script_errors.APPLY_SCRIPT: "qa_script_grouped"}  # Not the add-on's own.
DEEP = ("bpy.data.images['QA Tex'].colorspace_settings.name = 'Non-Color'\n"
        "bpy.data.objects['Cube'].animation_data.drivers[0].driver.expression = '7'\n"
        "bpy.data.objects['QA Instance'].instance_collection = bpy.data.collections['QA Kit B']\n"
        "bpy.data.materials['QA Mat'].diffuse_color = (0, 1, 0, 1)\n"
        "bpy.data.objects.remove(bpy.data.objects['QA Extra'], do_unlink=True)\n"
        "bpy.context.scene.collection.objects.link(bpy.data.objects.new('Half Built', None))\n")
# name: (the person's mode, the script, what must be true afterwards)
CASES = {
    "object_mode_script_raises": ("OBJECT", HEAD + DEEP + RAISE, "restores"),
    "light_and_camera_changed_then_edit_mode_entered": ("OBJECT", LIGHTS + "bpy.ops.object.mode_set(mode='EDIT')\n"
                                                        + RAISE, "restores"),
    "person_in_edit_mode_light_and_camera_changed": ("EDIT", LIGHTS + RAISE, "remains"),
    "person_in_sculpt_mode_light_and_camera_changed": ("SCULPT", LIGHTS + RAISE, "remains"),
    "person_in_edit_curve_mode_light_and_camera_changed": ("EDIT_CURVE", LIGHTS + RAISE, "remains"),
    "script_enters_edit_mode_and_raises": ("OBJECT", HEAD + "bpy.ops.object.mode_set(mode='EDIT')\n"
                                           "bpy.ops.mesh.subdivide()\n" + RAISE, "restores"),
    "script_enters_edit_mode_and_leaves": ("OBJECT", HEAD + "bpy.ops.object.mode_set(mode='EDIT')\n"
                                           "bpy.ops.mesh.subdivide()\nbpy.ops.object.mode_set(mode='OBJECT')\n"
                                           + DEEP + RAISE, "restores"),
    "script_enters_sculpt_mode_and_raises": ("OBJECT", HEAD + "bpy.ops.object.mode_set(mode='SCULPT')\n" + RAISE,
                                             "restores"),
    "script_pushes_its_own_undo_step": ("OBJECT", HEAD + "getattr(bpy.ops.ed, 'undo' + '_push')(message='mine')\n"
                                        + DEEP + RAISE, "never_false"),
    "script_clears_the_scenes_custom_properties": ("OBJECT", HEAD + "for key in list(bpy.context.scene.keys()):\n"
                                                   "    del bpy.context.scene[key]\n" + RAISE, "never_false"),
    "person_in_edit_mode": ("EDIT", HEAD + RAISE, "remains"),
    "person_in_edit_mode_script_leaves_it": ("EDIT", "import bpy\nbpy.ops.object.mode_set(mode='OBJECT')\n"
                                             + HEAD[11:] + RAISE, "remains"),
    "person_in_sculpt_mode": ("SCULPT", HEAD + RAISE, "remains"),
    "person_in_weight_paint_mode": ("WEIGHT_PAINT", HEAD + RAISE, "restores"),
    "person_in_pose_mode": ("POSE", HEAD + RAISE, "restores"),
    "pixels_changed_and_repacked": ("OBJECT", "import bpy\nim = bpy.data.images['QA Tex']\n"
                                    "im.pixels[:] = [0.6, 0.6, 0.6, 1.0] * 64\nim.update()\nim.pack()\n" + RAISE, "remains"),
    "image_reloaded_from_another_file": ("OBJECT", "import bpy\nim = bpy.data.images['QA Tex']\nim.unpack(method='REMOVE')\n"
                                         "im.filepath = bpy.context.scene['qa_other']\nim.reload()\nv = im.pixels[0]\n"
                                         + RAISE, "remains"),
    # The same writes with no name the host could read in the script: only what they leave on the image gives them away.
    "pixels_changed_without_naming_them": ("OBJECT", "import bpy\nim = bpy.data.images['QA Tex']\n"
                                           "getattr(im, 'pix' + 'els')[:] = [0.6, 0.6, 0.6, 1.0] * 64\n" + LIGHTS[11:] + RAISE,
                                           "remains"),
    "pixels_changed_and_repacked_without_naming_them": (
        "OBJECT", "import bpy\nim = bpy.data.images['QA Tex']\n"
        "getattr(im, 'pix' + 'els')[:] = [v / 256 for v in range(256)]\ngetattr(im, 'pa' + 'ck')()\n" + RAISE, "remains"),
    # A timer or another add-on changed a light without an Undo step: a rollback must not take that too.
    "changes_made_without_an_undo_step_before_the_script": ("OBJECT", HEAD + RAISE, "restores"),
    "no_undo_history": ("OBJECT", HEAD + RAISE, "restores"),
}


class ANGELO_OT_qa_script(bpy.types.Operator):
    """The add-on's apply operator in what matters here: its name, its Undo step and what cancels it."""
    bl_idname = "angelo.qa_script"
    bl_label = script_errors.STEP
    bl_options = {"UNDO"}
    code: bpy.props.StringProperty()

    def execute(self, context):
        LAST.update(BlenderExecutor(context.window_manager.windows[0]).execute(
            "angelo_execute_python", {"description": "Rollback probe", "code": self.code}))
        return {"CANCELLED"} if LAST.get("main_data_restored") else {"FINISHED"}


def window():
    return bpy.context.window_manager.windows[0]


def run_script(code):
    """One approved script, through the operator the approval chooses in this mode. Its tool result."""
    name = script_errors.apply_operator(bpy, window(), "angelo_execute_python")
    LAST.clear()
    getattr(bpy.ops.angelo, QA_OPERATORS[name])("EXEC_DEFAULT", True, code=code)
    assert LAST, "The script operator did not run"
    return dict(LAST, operator=name)


def png(name, value):
    path = str(OUT / (name + ".png"))
    seed = bpy.data.images.new(name + " seed", 8, 8)
    seed.pixels[:] = [value, value, value, 1.0] * 64
    seed.filepath_raw, seed.file_format = path, "PNG"
    seed.save()
    bpy.data.images.remove(seed)
    return path


def build(mode, history=True):
    """The factory scene plus what an Undo must bring back; the person ends in ``mode``."""
    bpy.ops.wm.read_factory_settings(use_empty=False)
    bpy.context.preferences.edit.use_global_undo = True
    scene, cube = window().scene, window().scene.objects["Cube"]
    image = bpy.data.images.load(png("QA A", 0.2), check_existing=False)
    image.name = "QA Tex"
    image.pack()
    scene["qa_other"] = png("QA B", 0.8)
    material = bpy.data.materials.new("QA Mat")
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeTexImage").image = image
    cube.data.materials.append(material)
    cube.driver_add("location", 2).driver.expression = "3"
    for name in ("QA Kit", "QA Kit B"):
        bpy.data.collections.new(name)
    instance = bpy.data.objects.new("QA Instance", None)
    instance.instance_type, instance.instance_collection = "COLLECTION", bpy.data.collections["QA Kit"]
    scene.collection.objects.link(instance)
    scene.collection.objects.link(bpy.data.objects.new("QA Extra", None))
    armature = bpy.data.objects.new("QA Rig", bpy.data.armatures.new("QA Rig"))
    scene.collection.objects.link(armature)
    curve = bpy.data.objects.new("QA Curve", bpy.data.curves.new("QA Curve", "CURVE"))
    curve.data.splines.new("BEZIER")
    scene.collection.objects.link(curve)
    _ = image.pixels[0]
    window().view_layer.update()
    target, mode = (armature, mode) if mode == "POSE" else (curve, "EDIT") if mode == "EDIT_CURVE" else (cube, mode)
    for obj in scene.objects:
        obj.select_set(obj is target)
    window().view_layer.objects.active = target
    if history:
        bpy.ops.ed.undo_push(message="Rollback probe baseline")
    if mode != "OBJECT":
        assert "FINISHED" in bpy.ops.object.mode_set("EXEC_DEFAULT", True, mode=mode), mode


def probe():
    """What the scene is, read from Blender: nothing here comes from the host's own account."""
    scene, data = window().scene, bpy.data
    image, cube = data.images.get("QA Tex"), data.objects.get("Cube")
    drivers = cube.animation_data.drivers if cube is not None and cube.animation_data else []
    return {
        "mode": bpy.context.mode, "energy": data.lights["Light"].energy, "lens": data.objects["Camera"].data.lens,
        "world": [round(v, 3) for v in data.worlds["World"].color], "resolution_x": scene.render.resolution_x,
        "objects": sorted((o.name, [round(v, 3) for v in o.location]) for o in scene.objects),
        "meshes": sorted(m.name for m in data.meshes),
        "cube_vertices": len(cube.data.vertices) if cube is not None and bpy.context.mode == "OBJECT" else None,
        "material": [round(v, 3) for v in data.materials["QA Mat"].diffuse_color],
        "image": None if image is None else [image.colorspace_settings.name, os.path.basename(image.filepath_raw),
                                             image.packed_file is not None, round(image.pixels[0], 3)],
        "driver": [d.driver.expression for d in drivers],
        "instance": getattr(data.objects["QA Instance"].instance_collection, "name", None),
        "scene_keys": sorted(key for key in scene.keys() if key != "qa_other"),
    }


def run_case(name):
    mode, code, expect = CASES[name]
    build(mode, history=name != "no_undo_history")
    if name == "changes_made_without_an_undo_step_before_the_script":
        bpy.data.lights["Light"].energy = 300.0  # A timer or another add-on: a change that is no Undo step.
    before = probe()
    result, after = run_script(code), probe()
    changed = sorted(key for key in before if before[key] != after[key])
    claimed = result.get("main_data_restored") is True
    said_remains = (result.get("rolled_back") is False and result.get("partial_changes_possible") is True
                    and "still in the scene" in str(result.get("rollback")))
    REPORT["cases"][name] = {"expect": expect, "rolled_back": result.get("rolled_back"), "changed": changed,
                             "main_data_restored": result.get("main_data_restored"),
                             "rollback": result.get("rollback"), "error": result.get("error"),
                             "scene_changed": result.get("scene_changed")}
    REPORT["checks"][name + ":never_reported_rolled_back_while_changed"] = not (claimed and changed)
    REPORT["checks"][name + ":says_one_or_the_other"] = claimed or said_remains
    REPORT["checks"][name + ":partial_recovery_explicit"] = (
        result.get("partial_changes_possible") is True and result.get("rolled_back") is False and "scene_changed" not in result)
    if expect == "restores":
        REPORT["checks"][name + ":restored_and_said_so"] = claimed and not changed and result.get("rolled_back") is False
    if expect == "remains":
        REPORT["checks"][name + ":says_the_changes_remain"] = said_remains
    REPORT["checks"][name + ":no_host_marker_left"] = not [key for key in after["scene_keys"] if key.startswith("_angelo")]


def finished_script_cases():
    """``undoable`` on a script that finished: never true where the person's Undo keeps any of it."""
    stays = HEAD + "bpy.ops.object.mode_set(mode='EDIT')\nresult = {'ok': True}\n"
    for name, mode, code in (("OBJECT", "OBJECT", HEAD + "result = {'ok': True}\n"), ("EDIT", "EDIT", HEAD + "result = {'ok': True}\n"),
                             ("SCULPT", "SCULPT", HEAD + "result = {'ok': True}\n"), ("OBJECT_script_stays_in_EDIT", "OBJECT", stays)):
        build(mode)
        before = probe()
        result = run_script(code)
        bpy.ops.ed.undo()
        taken_back = probe() == before
        REPORT["cases"]["finished_in_" + name] = {"undoable": result.get("undoable"), "undo_took_it_back": taken_back,
                                                  "undo": result.get("undo"), "operator": result["operator"]}
        REPORT["checks"]["finished_in_%s:never_undoable_where_undo_keeps_it" % name] = (
            result.get("success") is True and (taken_back or result.get("undoable") is False))
    REPORT["checks"]["finished_in_OBJECT:image_undo_unverified"] = REPORT["cases"]["finished_in_OBJECT"]["undoable"] is False
    for name in ("EDIT", "SCULPT"):
        REPORT["checks"]["finished_in_%s:says_why_not" % name] = "Undo holds only that mode's own data" in str(
            REPORT["cases"]["finished_in_" + name]["undo"])


def empties():
    return sorted(o.name for o in bpy.data.objects if o.name.startswith("Step "))


def edit_vertices():
    bpy.context.edit_object.update_from_editmode()
    return len(bpy.context.edit_object.data.vertices)


def add(name):
    return "import bpy\nbpy.context.scene.collection.objects.link(bpy.data.objects.new('Step %s', None))\n" % name


def undo_step_cases():
    """How many Undo steps scripts leave, counted by undoing: the person's own step is the one before them."""
    checks = REPORT["checks"]
    build("OBJECT")
    bpy.ops.object.empty_add("EXEC_DEFAULT", True)  # The person's own edit, one Undo step.
    bpy.context.object.name = "Step person"
    bpy.ops.ed.undo_push(message="The person's step")
    first, second = run_script(add("A")), run_script(add("B"))
    checks["steps:a_script_in_object_mode_uses_the_grouped_operator"] = first["operator"] == script_errors.APPLY_SCRIPT
    trail = [empties()]
    for _ in range(3):
        bpy.ops.ed.undo()
        trail.append(empties())
    REPORT["cases"]["two_finished_scripts"] = trail
    checks["steps:two_scripts_are_two_steps_with_none_between"] = trail == [
        ["Step A", "Step B", "Step person"], ["Step A", "Step person"], ["Step person"], []]
    for _ in range(3):
        bpy.ops.ed.redo()
    checks["steps:redo_brings_each_back"] = empties() == ["Step A", "Step B", "Step person"] and second["undo_available"] is True
    failed, corrected = run_script(add("C") + RAISE), run_script(add("C"))
    trail = [empties()]
    for _ in range(3):
        bpy.ops.ed.undo()
        trail.append(empties())
    REPORT["cases"]["rolled_back_then_corrected"] = trail
    checks["steps:a_rolled_back_script_leaves_one_step_that_changes_nothing"] = (
        failed.get("main_data_restored") is True and corrected.get("success") is True and trail == [
            ["Step A", "Step B", "Step C", "Step person"], ["Step A", "Step B", "Step person"],
            ["Step A", "Step B", "Step person"], ["Step A", "Step person"]])
    # In Edit Mode there is no restore point, so the plain operator applies: two scripts stay two steps.
    build("EDIT")
    first, second = run_script("import bpy\nbpy.ops.mesh.subdivide()\n"), run_script("import bpy\nbpy.ops.mesh.subdivide()\n")
    counts = [edit_vertices()]
    for _ in range(2):
        bpy.ops.ed.undo()
        counts.append(edit_vertices())
    REPORT["cases"]["two_scripts_in_edit_mode"] = {"vertices": counts, "operator": first["operator"]}
    checks["steps:in_edit_mode_the_plain_operator_applies"] = first["operator"] == second["operator"] == script_errors.APPLY
    checks["steps:two_scripts_in_edit_mode_are_two_steps"] = len(set(counts)) == 3 and counts[-1] == 8


def main():
    assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background process"
    bpy.utils.register_class(ANGELO_OT_qa_script)
    bpy.utils.register_class(script_errors.grouped(ANGELO_OT_qa_script, QA_OPERATORS[script_errors.APPLY_SCRIPT]))
    try:
        for name in CASES:
            run_case(name)
        finished_script_cases()
        undo_step_cases()
        from script_pixel_fixtures import pixel_cases, success_history_cases
        pixel_cases(REPORT, build, run_script)
        success_history_cases(REPORT, build, run_script)
        from script_review_fixtures import review_cases
        review_cases(REPORT, build, run_script, OUT)
        REPORT["success"] = all(REPORT["checks"].values())
    except Exception:
        REPORT.update(success=False, error=traceback.format_exc())
    (OUT / "script-rollback.json").write_text(json.dumps(REPORT, indent=2, default=str))
    failed = sorted(name for name, passed in REPORT["checks"].items() if not passed)
    print("ANGELO_SCRIPT_ROLLBACK", json.dumps({"success": REPORT["success"], "checks": len(REPORT["checks"]),
                                                "failed": failed, "error": REPORT.get("error", "")[-600:]}), flush=True)
    assert REPORT["success"], failed or REPORT.get("error")
    print("ANGELO_SCRIPT_ROLLBACK_OK", flush=True)


if __name__ == "__main__":
    main()
