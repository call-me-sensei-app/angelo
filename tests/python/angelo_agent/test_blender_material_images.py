# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Image-texture materials from loaded images through shader_nodes' TEX_IMAGE node.

The stage never links the live image (a suspended stage would copy it into its
bundle): the node carries the image ID and commit assigns the image. Covers a
forced suspend/resume, no duplicate images, one-step Undo and a missing image.
Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
No provider or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-material-images-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.action_world import image_id  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator, spent_budget  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    report.setdefault("results", []).append({key: job.result.get(key) for key in ("status", "error")})
    return "FINISHED" in result and job.result.get("status") == "committed"


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def scene_binding():
    settings = scene_settings.execute(window, {})
    return {"handle": settings["handle"], "revision": settings["revision"]}


def program(operations, writes=(), scene=None):
    write_set = [binding(obj) for obj in writes] + ([scene] if scene else [])
    return validate_call("angelo_scene_program", {"mode": "APPLY", "write_set": write_set, "operations": operations})


def textured(obj, image, name="QA Textured"):
    nodes = [{"id": "tex", "type": "TEX_IMAGE", "settings": {"image": image, "interpolation": "Closest"}},
             {"id": "bsdf", "type": "BSDF_PRINCIPLED"}, {"id": "out", "type": "OUTPUT_MATERIAL"}]
    links = [{"from_node": "tex", "from_socket": "Color", "to_node": "bsdf", "to_socket": "Base Color"},
             {"from_node": "bsdf", "from_socket": "BSDF", "to_node": "out", "to_socket": "Surface"}]
    return {"kind": "shader_nodes", "target": handle(obj), "parameters": {"name": name, "nodes": nodes, "links": links}}


def run(arguments):
    job = native.begin(window, "angelo_scene_program", arguments)
    return job, settle(job)


def passed(job):
    return bool(job.result.get("checks")) and all(row.get("passed") for row in job.result["checks"])


def failed_with(result, code):
    return result.get("status") == "failed" and code in (result.get("error") or "")


def texture_node(obj, name="QA Textured"):
    material = next(slot.material for slot in obj.material_slots if slot.material and slot.material.name == name)
    return next(node for node in material.node_tree.nodes if node.bl_idname == "ShaderNodeTexImage")


def textures():
    image = bpy.data.images.new("QA Checker", 32, 32)
    image.generated_type = "COLOR_GRID"
    bpy.ops.ed.undo_push(message="material image QA")
    queries.invalidate()
    image, cube = bpy.data.images["QA Checker"], window.scene.objects["Cube"]
    images_before, users_before = len(bpy.data.images), image.users
    listed = [item["id"] for item in scene_settings.execute(window, {})["images"]]
    check("image_listed_by_scene_settings", image_id(image) in listed)
    with spent_budget():  # Every step yields: the stage is suspended to its bundle and resumed.
        job, staged = run(program([textured(cube, image_id(image))], writes=[cube]))
    check("texture_ready_without_live_edit", staged.get("ready_to_commit") and not any(
        slot.material and slot.material.name == "QA Textured" for slot in cube.material_slots))
    check("texture_stage_never_copies_the_image", len(bpy.data.images) == images_before)
    check("texture_commits", commit(job) and passed(job))
    cube = window.scene.objects["Cube"]
    node = texture_node(cube)
    check("texture_node_uses_the_live_image", node.image == image and node.interpolation == "Closest"
          and "_angelo_image" not in node.keys())
    check("texture_no_duplicate_images", len(bpy.data.images) == images_before and image.users == users_before + 1)
    bpy.ops.ed.undo()
    queries.invalidate()
    check("texture_one_undo", not any(slot.material and slot.material.name == "QA Textured"
                                      for slot in window.scene.objects["Cube"].material_slots) and no_stages())
    cube = window.scene.objects["Cube"]
    missing = image_id(bpy.data.images["QA Checker"]).rsplit(":", 1)[0] + ":999999"
    job, result = run(program([textured(cube, missing)], writes=[cube]))
    check("missing_image_refused", failed_with(result, "IMAGE_NOT_FOUND") and no_stages())
    job, staged = run(program([textured(cube, image_id(bpy.data.images["QA Checker"]), "QA Late")], writes=[cube]))
    bpy.data.images.remove(bpy.data.images["QA Checker"])  # Removed after staging, before commit.
    queries.invalidate()
    check("removed_image_fails_commit", not commit(job) and not any(
        slot.material and slot.material.name == "QA Late" for slot in window.scene.objects["Cube"].material_slots))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    textures()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "material-images.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_MATERIAL_IMAGES", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
