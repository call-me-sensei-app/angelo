# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Grease Pencil and hair-curves data with an Action: a plain move fails as INTERNAL_ERROR (second review).

``stage_borrowing._plan`` borrows nothing when any ID in a bound object's
mutable closure is animated, and ``assert_unanimated`` then requires every ID
the stage owns to be unanimated before each stage evaluation (INTERNAL_ERROR
otherwise). The closure walk (``program_dependencies._walk_dependencies``)
follows only Material, NodeTree, Action, Mesh, Armature, Key, Curve, Lattice,
Camera, Light and ParticleSettings, so a GREASEPENCIL object's GreasePencil
data and a CURVES object's Curves data are never in it. The scene reader
admits both object types with an animated data block, ``copy_object`` copies
the data and its Action into the stage, and ``_plan`` cannot see that Action:

* the material is borrowed;
* the first stage evaluation (``finish_stage``) fails ``assert_unanimated``
  with INTERNAL_ERROR, "A staged copy of a borrowing program gained
  animation", ``partial_changes_possible`` and the recovery instruction;
* nothing is written (the guard runs before evaluation), but an ordinary
  artist scene (a Grease Pencil layer's keyed opacity) now puts the session
  into recovery for a move.

On the base (23d9976e9a2) every scenario stages a private clone and commits:
12/12 checks hold. On the fix head (8c6aa6f960d) the six ``no_internal_error``
and ``program_commits`` checks fail; the live value and stage cleanup hold.
Checking ``original.data`` and its shape keys in ``_plan`` as well (validated
in a scratch app) passes 12/12.
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


def check(name, condition):
    report["checks"][name] = bool(condition)


def settle(job):
    for _ in range(200):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def key(owner, path):
    """Key ``path`` on ``owner`` to KEYED (frames 1 and 20) with a new Action."""
    action = bpy.data.actions.new("Keyed " + owner.name)
    owner.animation_data_create()
    owner.animation_data.action = action
    curve = action.fcurve_ensure_for_datablock(owner, path)
    for frame in (1, 20):
        curve.keyframe_points.insert(frame, KEYED)


def scenario(name, obj, holder, attribute):
    """Key, then tweak ``holder.attribute`` unkeyed; a move must commit and keep the tweak."""
    window = bpy.context.window
    window.scene.collection.objects.link(obj)
    window.scene.frame_set(1)
    window.view_layer.update()
    setattr(holder, attribute, TWEAKED)  # The artist's unkeyed tweak.
    window.view_layer.update()
    read = lambda: round(getattr(holder, attribute), 4)
    job = native.begin(window, "angelo_scene_program",
                       args_for(window, [dict(MOVE, target=queries.object_handle(obj))], writes=[obj]))
    with spent_budget():
        first = job.poll()  # Preparation: the stage is created and evaluated.
    staged = read()
    result = first if first.get("final") else settle(job)
    outcome = result.get("status")
    if result.get("ready_to_commit"):
        outcome = job.commit().get("status")
    elif not result.get("final"):
        job.cancel()
    report["observed"][name] = {key: result.get(key) for key in ("status", "error_code", "error",
                                                                 "partial_changes_possible")}
    report["observed"][name].update(after_staging=staged, final=read(), outcome=outcome)
    check(name + ":no_internal_error", result.get("error_code") != "INTERNAL_ERROR"
          and not result.get("partial_changes_possible"))
    check(name + ":program_commits", outcome == "committed" and list(obj.location) == [1., 2., 3.])
    check(name + ":live_value_untouched", staged == TWEAKED and read() == TWEAKED)
    check(name + ":no_stage_left", no_stages())
    native.cleanup_all()


def grease_pencil(name):
    material = bpy.data.materials.new(name)
    bpy.data.materials.create_gpencil_data(material)
    data = bpy.data.grease_pencils.new(name)
    layer = data.layers.new("Layer")
    layer.frames.new(1).drawing.add_strokes([4])
    data.materials.append(material)
    return bpy.data.objects.new(name, data), data, material


def gp_layer_opacity():
    """The everyday case: a Grease Pencil layer's opacity is keyed (an Action on the GreasePencil data)."""
    obj, data, _material = grease_pencil("GP layer opacity")
    key(data, 'layers["Layer"].opacity')
    scenario("gp_layer_opacity", obj, data.layers["Layer"], "opacity")


def gp_data_material_path():
    """The GreasePencil data's Action keys its material through ``materials[0]``."""
    obj, data, material = grease_pencil("GP material path")
    key(data, "materials[0].grease_pencil.mix_factor")
    scenario("gp_data_material_path", obj, material.grease_pencil, "mix_factor")


def hair_data_material_path():
    """A hair-curves object whose Curves data's Action keys its material through ``materials[0]``."""
    material = bpy.data.materials.new("Hair material path")
    curves = bpy.data.hair_curves.new("Hair material path")
    curves.add_curves([2])
    curves.materials.append(material)
    obj = bpy.data.objects.new("Hair material path", curves)
    key(curves, "materials[0].roughness")
    scenario("hair_data_material_path", obj, material, "roughness")


try:
    for test in (gp_layer_opacity, gp_data_material_path, hair_data_material_path):
        try:
            test()
        finally:
            native.cleanup_all()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_DATA_ANIMATION failed checks:", json.dumps(failed))
assert report["success"], failed
