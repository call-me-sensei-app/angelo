# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-body posing by role (``body_pose.pose_body``) on unlike humanoids, in a background Blender.

Two procedural humanoids (``qa_humanoid``: a T-pose adult with clavicles and no root, an A-pose smaller rig with a
root and other names), each with Euler-moded limbs. A right-hand target forward and down, past the arm's reach, with
the feet pinned (doc/angelo/rigging.rst, "Full-body pose"):

* the hand reaches the target (within 1% of the height) and the solver says it reached;
* both feet stay where they were (within 0.5% of the height) and the knees bend forward;
* the hips moved (the body took part), and the limbs kept their rotation modes;
* a target far out of reach is reported as not reached, with the distance left, and the feet still stay;
* balance: on that far reach, the body's centre of mass (segment masses by role, computed here from the posed
  bones) stays over the planted feet and toes, widened by 3% of the height, where without balance it lies far
  beyond them;
* limits: a hand target on its own shoulder, which folds the elbow past 150 degrees without limits, never folds it
  past 150 degrees with them (measured from the posed bones' heads);
* the operator (``angelo.pose_body``, the person's F3 entry and the agent's verb) poses exactly as the function does
  from the same start, using only the named targets, and reports it finished.

Run through ``run_native_suites.py --include test_blender_body_pose``; check ``body-pose.json`` for ``success``.
Expected to fail on 5fc885e: the module does not exist there; the balance and limit checks on 665ecd73b27; the
operator check on 86aeb2bfbcd.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-body-pose-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_LIBRARY_DIR=str(OUT / "library"))
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.use_preferences_save = False
from qa_humanoid import humanoid  # noqa: E402

REPORT = OUT / "body-pose.json"
ROLES = ["Hips", "Spine", "Chest", "Neck", "Head", "Root"] + [
    side + part for side in ("Left", "Right")
    for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes")]
state = {"success": False, "checks": {}, "details": {}}


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if detail is not None:
        state["details"][name] = detail


def head(obj, bone):
    bpy.context.view_layer.update()
    return obj.matrix_world @ obj.pose.bones[bone].head


# Segment masses as fractions of the body (de Leva 1996), at each segment's middle; a role to itself is a point.
MASSES = [("Hips", "Spine", 0.112), ("Spine", "Chest", 0.163), ("Chest", "Neck", 0.160), ("Head", "Head", 0.069)] + [
    (side + a, side + b, m) for side in ("Left", "Right") for a, b, m in (
        ("UpperArm", "LowerArm", 0.027), ("LowerArm", "Hand", 0.016), ("Hand", "Hand", 0.006),
        ("UpperLeg", "LowerLeg", 0.142), ("LowerLeg", "Foot", 0.043), ("Foot", "Toes", 0.014))]


def centre_outside(obj, names, height):
    """How far the centre of mass lies beyond the feet seen from above (the hull of both feet and toes, where a
    standing body's weight rests), less 3% of the height."""
    pts = {r: head(obj, names[r]) for r in names if names[r] in obj.pose.bones}
    total = sum(m for _, _, m in MASSES)
    c = sum(((pts[a] + pts[b]) * (m / 2.0) for a, b, m in MASSES), Vector()) / total
    support = sorted({(round(pts[r].x, 9), round(pts[r].y, 9)) for r in pts
                      if r.endswith("Foot") or r.endswith("Toes")})

    def turn(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    hull = []
    for half in (support, support[::-1]):   # Andrew's monotone chain.
        chain = []
        for q in half:
            while len(chain) >= 2 and turn(chain[-2], chain[-1], q) <= 0:
                chain.pop()
            chain.append(q)
        hull += chain[:-1]
    q = (c.x, c.y)
    if len(hull) >= 3 and all(turn(a, b, q) >= 0 for a, b in zip(hull, hull[1:] + hull[:1])):
        return 0.0

    def to_edge(a, b):
        ab = (b[0] - a[0], b[1] - a[1])
        t = max(0.0, min(1.0, ((q[0] - a[0]) * ab[0] + (q[1] - a[1]) * ab[1]) / max(ab[0] ** 2 + ab[1] ** 2, 1e-18)))
        return math.hypot(q[0] - a[0] - t * ab[0], q[1] - a[1] - t * ab[1])
    gap = min(to_edge(a, b) for a, b in zip(hull, hull[1:] + hull[:1]))
    return max(0.0, gap - 0.03 * height)


def fold(obj, names, side):
    """The elbow's fold in degrees: 0 straight, 180 the forearm back along the upper arm."""
    s, e, w = (head(obj, names[side + r]) for r in ("UpperArm", "LowerArm", "Hand"))
    return math.degrees((e - s).angle(w - e))


def case(label, names, height, drop, clavicles, root):
    from angelo_agent import body_pose
    obj = humanoid(label, names, height, drop, clavicles=clavicles, root=root)
    for role in ROLES:
        if role in names and names[role] in obj.pose.bones and ("Arm" in role or "Leg" in role):
            obj.pose.bones[names[role]].rotation_mode = 'XYZ'
    feet = {side: head(obj, names[side + "Foot"]) for side in ("Left", "Right")}
    hips = head(obj, names["Hips"])
    knee_rest = head(obj, names["LeftLowerLeg"])
    hand = head(obj, names["RightHand"])
    target = hand + Vector((0.1, -0.25, -0.35)) * height
    solved = body_pose.pose_body(obj, {"RightHand": tuple(target)})
    body_pose.apply(obj, solved)
    reach = (head(obj, names["RightHand"]) - target).length
    moved = max((head(obj, names[s + "Foot"]) - feet[s]).length for s in feet)
    knee_forward = -(head(obj, names["LeftLowerLeg"]) - knee_rest).y
    modes = all(obj.pose.bones[names[r]].rotation_mode == 'XYZ' for r in ROLES
                if ("Arm" in r or "Leg" in r) and r in names)
    check("%s_hand_reaches" % label, solved["reached"] and reach < 0.01 * height, {"reach": round(reach, 4)})
    check("%s_feet_stay_knees_forward" % label, moved < 0.005 * height and knee_forward > -1e-4,
          {"feet_moved": round(moved, 4), "knee_forward": round(knee_forward, 4)})
    check("%s_body_took_part_modes_kept" % label, (head(obj, names["Hips"]) - hips).length > 1e-3 * height and modes)
    before = {pb.name: pb.matrix_basis.copy() for pb in obj.pose.bones}

    def restore():
        for pb in obj.pose.bones:
            pb.matrix_basis = before[pb.name]
    far = body_pose.pose_body(obj, {"RightHand": tuple(target + Vector((0, -5.0 * height, 0)))})
    body_pose.apply(obj, far)
    moved = max((head(obj, names[s + "Foot"]) - feet[s]).length for s in feet)
    check("%s_out_of_reach_reported" % label, not far["reached"] and far["errors"]["RightHand"] > height
          and moved < 0.005 * height, {"left": round(far["errors"]["RightHand"], 3)})
    outside = centre_outside(obj, names, height)
    restore()   # The same start for the pose without balance.
    loose = body_pose.pose_body(obj, {"RightHand": tuple(target + Vector((0, -5.0 * height, 0)))}, balance=False)
    body_pose.apply(obj, loose)
    loose_outside = centre_outside(obj, names, height)
    check("%s_balance_keeps_the_centre_over_the_feet" % label, outside <= 1e-3 * height < 0.01 * height < loose_outside,
          {"outside": round(outside, 4), "without_balance": round(loose_outside, 4)})
    restore()
    shoulder = head(obj, names["RightUpperArm"])   # The hand back on its own shoulder: past an elbow's range.
    free = body_pose.pose_body(obj, {"RightHand": tuple(shoulder)}, limits=False)
    body_pose.apply(obj, free)
    free_fold = fold(obj, names, "Right")
    restore()
    back = body_pose.pose_body(obj, {"RightHand": tuple(shoulder)})
    body_pose.apply(obj, back)
    check("%s_elbow_within_its_range" % label, free_fold > 150.5 and fold(obj, names, "Right") <= 150.0 + 0.5,
          {"fold": round(fold(obj, names, "Right"), 2), "without_limits": round(free_fold, 2),
           "left": round(back["errors"]["RightHand"], 4)})
    restore()
    aim = head(obj, names["RightHand"]) + Vector((0.05, -0.1, 0.1)) * height
    by_function = body_pose.pose_body(obj, {"RightHand": tuple(aim)})
    body_pose.apply(obj, by_function)
    expected = {r: head(obj, names[r]) for r in ("RightHand", "LeftHand", "Hips", "Head")}
    restore()
    done = bpy.ops.angelo.pose_body(armature=obj.name, targets={'RIGHT_HAND'}, right_hand=tuple(aim),
                                    left_hand=(0.0, 0.0, 9.0 * height))   # Not named in targets: ignored.
    off = max((head(obj, names[r]) - p).length for r, p in expected.items())
    check("%s_operator_poses_by_role" % label, done == {'FINISHED'} and off < 1e-5 * height, {"off": off})


def main():
    try:
        case("Adult", {r: "a_" + r for r in ROLES}, 1.8, 0, clavicles=True, root=False)
        case("Small", {r: "S." + r.lower() for r in ROLES}, 1.2, 40, clavicles=False, root=True)
        state["success"] = len(state["checks"]) == 14 and all(state["checks"].values())
    except Exception:
        state["error"] = traceback.format_exc()
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_BODY_POSE", json.dumps(
        {"success": state["success"], "failed": sorted(k for k, v in state["checks"].items() if not v)}), flush=True)
    sys.exit(0 if state["success"] else 1)


main()
