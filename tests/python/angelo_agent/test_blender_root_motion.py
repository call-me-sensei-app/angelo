# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Root motion from the hips on rigs animated the way people animate, in a background Blender.

Each rig is a procedural humanoid with a root (``qa_humanoid``) walking with Euler-keyed limbs (the way most hand-made
and imported animation is keyed), plus a non-deforming prop bone under the root. Checked (doc/angelo/rigging.rst,
"Root motion"):

* ``IN_PLACE`` on a rig *with* a root: the hips stay over their rest place, and the root is left alone (it is
  neither moved nor keyed).
* ``ROOT``: the root travels as the hips did, the hips stay over the root, the prop under the root keeps its place
  in the world.
* Either way every limb points where it pointed before (within 0.5 degrees) and keeps its Euler rotation mode: the
  limbs' own curves still play.
* Running ``ROOT`` again changes nothing.
* ``yaw``: a walk that turns 90 degrees about the vertical turns the root by 90 degrees (within 3), and the hips
  face where they faced before.

Run through ``run_native_suites.py --include test_blender_root_motion``; check ``root-motion.json`` for ``success``.
Expected to fail on 2.0.0 (06259fa5bb0): the operator does not exist there.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import tempfile
import traceback
from pathlib import Path

import addon_utils  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-root-motion-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from qa_humanoid import angle, humanoid, walk, world_dir  # noqa: E402

REPORT = OUT / "root-motion.json"
SAMPLES = (1, 8, 15, 22, 29, 36)
LIMBS = [side + part for side in ("Left", "Right") for part in ("UpperArm", "LowerArm", "UpperLeg", "LowerLeg")]
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head", "Root"] + [
    side + part for side in ("Left", "Right")
    for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes")]
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def rig(label, turn=False):
    names = {role: label + "_" + role for role in ROLES}
    obj = humanoid(label, names, 1.6, 0, clavicles=True, root=True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    prop = obj.data.edit_bones.new(label + "_Prop")
    prop.head, prop.tail, prop.parent, prop.use_deform = (0.3, 0, 0), (0.3, 0.1, 0), obj.data.edit_bones[
        names["Root"]], False
    bpy.ops.object.mode_set(mode='OBJECT')
    names["Prop"] = label + "_Prop"
    walk(obj, names)
    if turn:
        hips = obj.pose.bones[names["Hips"]]
        hips.rotation_mode = 'XYZ'
        for f in range(1, 41):
            hips.rotation_euler = (0.0, math.radians(90.0) * (f - 1) / 39, 0.0)   # The hips bone's Y is up.
            hips.keyframe_insert("rotation_euler", frame=f)
    return obj, names


def snapshot(obj, names, bones):
    out = {}
    for f in SAMPLES:
        bpy.context.scene.frame_set(f)
        out[f] = {b: world_dir(obj, names.get(b, b)) for b in bones}
    return out


def limbs_kept(obj, names, before):
    worst = 0.0
    after = snapshot(obj, names, LIMBS)
    for f in SAMPLES:
        for b in LIMBS:
            worst = max(worst, angle(before[f][b][0], after[f][b][0]))
    modes = all(obj.pose.bones[names[b]].rotation_mode == 'XYZ' for b in LIMBS)
    return worst, modes


def root_keyed(obj, names):
    action = obj.animation_data.action
    path = 'pose.bones["%s"]' % names["Root"]
    return any(fc.data_path.startswith(path) for bag in _channelbags(action) for fc in bag.fcurves)


def _channelbags(action):
    for layer in action.layers:
        for strip in layer.strips:
            yield from strip.channelbags


def in_place_case():
    obj, names = rig("InPlace")
    before = snapshot(obj, names, LIMBS)
    result = bpy.ops.angelo.extract_root_motion(armature=obj.name, mode='IN_PLACE')
    rest = obj.matrix_world @ obj.data.bones[names["Hips"]].head_local
    span = 0.0
    for f in SAMPLES:
        bpy.context.scene.frame_set(f)
        hips = world_dir(obj, names["Hips"])[1]
        span = max(span, Vector((hips.x - rest.x, hips.y - rest.y)).length)
    worst, modes = limbs_kept(obj, names, before)
    check("in_place_hips_stay_root_untouched", result == {'FINISHED'} and span < 0.02 * 1.6
          and not root_keyed(obj, names), {"span": round(span, 4), "root_keyed": root_keyed(obj, names)})
    check("in_place_limbs_and_euler_kept", worst < 0.5 and modes, {"worst_degrees": round(worst, 3)})


def root_case():
    obj, names = rig("OnRoot")
    before = snapshot(obj, names, LIMBS + ["Hips", "Prop"])
    result = bpy.ops.angelo.extract_root_motion(armature=obj.name, mode='ROOT')
    after = snapshot(obj, names, ["Hips", "Root", "Prop"])
    travel_error, over_root, prop_moved = 0.0, 0.0, 0.0
    for f in SAMPLES:
        h0, r1, h1 = before[f]["Hips"][1], after[f]["Root"][1], after[f]["Hips"][1]
        rest_root = obj.matrix_world @ obj.data.bones[names["Root"]].head_local
        rest_hips = obj.matrix_world @ obj.data.bones[names["Hips"]].head_local
        travel_error = max(travel_error, ((r1 - rest_root).xy - (h0 - rest_hips).xy).length)
        over_root = max(over_root, ((h1 - r1).xy - (rest_hips - rest_root).xy).length)
        prop_moved = max(prop_moved, (after[f]["Prop"][1] - before[f]["Prop"][1]).length)
    worst, modes = limbs_kept(obj, names, before)
    check("root_travels_hips_over_root_prop_still", result == {'FINISHED'} and travel_error < 0.01 * 1.6
          and over_root < 0.01 * 1.6 and prop_moved < 1e-4,
          {"travel_error": round(travel_error, 4), "over_root": round(over_root, 4), "prop": round(prop_moved, 5)})
    check("root_limbs_and_euler_kept", worst < 0.5 and modes, {"worst_degrees": round(worst, 3)})
    first = snapshot(obj, names, ["Root"])
    bpy.ops.angelo.extract_root_motion(armature=obj.name, mode='ROOT')
    second = snapshot(obj, names, ["Root"])
    drift = max((first[f]["Root"][1] - second[f]["Root"][1]).length for f in SAMPLES)
    check("root_rerun_changes_nothing", drift < 1e-4, {"drift": drift})


def hips_rotation(obj, names):
    """The hips' world orientation at each sampled frame (their direction alone is vertical, blind to a turn)."""
    out = {}
    for f in SAMPLES:
        bpy.context.scene.frame_set(f)
        out[f] = (obj.matrix_world.to_3x3() @ obj.pose.bones[names["Hips"]].matrix.to_3x3()).to_quaternion()
    return out


def yaw_case():
    obj, names = rig("Turning", turn=True)
    before = hips_rotation(obj, names)
    result = bpy.ops.angelo.extract_root_motion(armature=obj.name, mode='ROOT', yaw=True)
    bpy.context.scene.frame_set(40)
    root = obj.pose.bones[names["Root"]]
    rest = obj.data.bones[names["Root"]].matrix_local.to_3x3()
    turned = (obj.matrix_world.to_3x3() @ root.matrix.to_3x3() @ rest.inverted()) @ Vector((0, -1, 0))
    degrees = math.degrees(math.atan2(turned.y, turned.x) - math.atan2(-1, 0))
    degrees = (degrees + 180) % 360 - 180
    after = hips_rotation(obj, names)
    hips_kept = max(math.degrees(before[f].rotation_difference(after[f]).angle) for f in SAMPLES)
    check("yaw_on_root_hips_kept", result == {'FINISHED'} and abs(abs(degrees) - 90.0) < 3.0 and hips_kept < 0.5,
          {"root_turn_degrees": round(degrees, 2), "hips_change_degrees": round(hips_kept, 3)})


def main():
    try:
        in_place_case()
        root_case()
        yaw_case()
        state["success"] = len(state["checks"]) == 6 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_ROOT_MOTION", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
