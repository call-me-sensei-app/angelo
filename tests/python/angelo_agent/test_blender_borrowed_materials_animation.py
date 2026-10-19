# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Animation on a staged object that writes through a slot into a borrowed live material, in a disposable process.

A borrowed material is unanimated itself (stage_borrowing._family), but the
bound object's own Action, NLA strips, its mesh's Action or its shape keys'
Action can hold an F-Curve whose RNA path runs through a material slot into
that material (``material_slots[0].material.roughness``,
``active_material.roughness``, ``materials[0].roughness``,
``user.materials[0].roughness``, or a socket of its node tree). The scene
reader admits such an object, and drivers (which Blender refuses to point
across IDs) are not involved.

Staging copies the object and its Actions but leaves the live material in the
staged slot. ``view_layer.update`` makes the stage's depsgraph active, and an
active depsgraph flushes evaluated animation back to the originals
(``animsys_write_orig_anim_rna``). The staged object's path resolves through
its staged slot to the LIVE material, so the first stage evaluation writes the
keyed value into the artist's material before anything is approved or
committed. An unkeyed tweak (the artist changed an animated value without
keying it) is overwritten outside Undo, and the program is then refused as
STALE_REVISION because the material it just wrote no longer matches its bound
fingerprint. A PREVIEW does the same.

Before borrowing, the staged slot held a private clone, which received the
write: the live value survived and the edit committed. Every check here holds
on a build without borrowing and failed on the first build that borrowed.
"""

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from program_qa_helpers import args_for, no_stages, spent_budget  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}

KEYED, TWEAKED = .2, .9
MOVE = {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}
BEVEL = {"kind": "modifier_configure", "parameters": {"modifier": "Bevel", "type": "BEVEL", "settings": {"width": .2}}}


def check(name, condition):
    report["checks"][name] = bool(condition)


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def fixture(name, owner_of, path, *, nla=False, socket=False):
    """A quad whose one material is keyed (through ``path`` on ``owner_of(obj)``) to KEYED, then tweaked unkeyed."""
    window = bpy.context.window
    material = bpy.data.materials.new(name)
    principled = material.node_tree.nodes["Principled BSDF"].inputs["Roughness"]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    obj.modifiers.new("Bevel", "BEVEL")
    window.scene.collection.objects.link(obj)
    owner = owner_of(obj)
    action = bpy.data.actions.new(name)
    owner.animation_data_create()
    owner.animation_data.action = action
    curve = action.fcurve_ensure_for_datablock(owner, path)
    for frame in (1, 20):
        curve.keyframe_points.insert(frame, KEYED)
    if nla:
        owner.animation_data.nla_tracks.new().strips.new(name, 1, action)
        owner.animation_data.action = None
    window.scene.frame_set(1)
    window.view_layer.update()

    def read():
        return round(principled.default_value if socket else material.roughness, 4)

    def write(value):
        if socket:
            principled.default_value = value
        else:
            material.roughness = value

    assert read() == KEYED, (name, read())
    write(TWEAKED)  # The artist's unkeyed tweak: nothing re-evaluates the Action until the frame changes.
    window.view_layer.update()
    assert read() == TWEAKED, (name, read())
    return window, obj, read


def scenario(name, owner_of, path, operation=MOVE, *, mode="APPLY", nla=False, socket=False):
    window, obj, read = fixture(name, owner_of, path, nla=nla, socket=socket)
    handle = queries.object_handle(obj)
    job = native.begin(window, "angelo_scene_program",
                       args_for(window, [dict(operation, target=handle)], writes=[obj], mode=mode))
    with spent_budget():
        job.poll()  # Preparation only: the stage is created, evaluated and suspended.
    staged = read()
    result = settle(job)
    outcome = result.get("status")
    if result.get("ready_to_commit"):
        outcome = job.commit().get("status")
    elif not result.get("final"):
        job.cancel()
    final = read()
    report["observed"][name] = {"after_staging": staged, "final": final, "outcome": outcome,
                                "error_code": result.get("error_code")}
    check(name + ":live_material_untouched_by_staging", staged == TWEAKED)
    check(name + ":live_material_untouched_at_end", final == TWEAKED)
    check(name + ":program_" + ("previews" if mode == "PREVIEW" else "commits"),
          outcome == ("preview" if mode == "PREVIEW" else "committed"))
    check(name + ":no_stage_left", no_stages())
    native.cleanup_all()


def shape_keys(obj):
    obj.shape_key_add(name="Basis")
    return obj.data.shape_keys


SLOT = "material_slots[0].material.roughness"
SOCKET = 'material_slots[0].material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value'

try:
    scenario("object_action_slot", lambda obj: obj, SLOT)
    scenario("object_action_active_material", lambda obj: obj, "active_material.roughness")
    scenario("object_action_tree_socket", lambda obj: obj, SOCKET, socket=True)
    scenario("object_nla_strip", lambda obj: obj, SLOT, nla=True)
    scenario("mesh_action", lambda obj: obj.data, "materials[0].roughness")
    scenario("shape_key_action", shape_keys, "user.materials[0].roughness")
    scenario("object_action_modifier_configure", lambda obj: obj, SLOT, BEVEL)
    scenario("object_action_preview", lambda obj: obj, SLOT, mode="PREVIEW")
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_ANIMATION failed checks:", json.dumps(failed))
assert report["success"], failed
