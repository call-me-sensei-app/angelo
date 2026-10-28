# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The RIG tools in Blender: the armature and its skinned mesh read into arrays (``rig_read``), ``hand_rig`` and
``hand_pose`` run on them, and the answer. Main thread; read-only (no RNA write, no depsgraph tag, no Undo step).

Poses are skinned here, not by Blender, so a candidate pose never touches the scene. That is only sound when this
linear-blend skinning gives what Blender's Armature modifier gives, so every call compares the two on the current
pose and says how far apart they are (``skinning_check``); beyond ``rig_read.SKINNING_REFUSE`` finger radii, or with
finger bones that follow constraints or drivers, the pose tools refuse rather than judge skin that is not there.
"""

import time

import numpy as np

from .refusal_codes import RefusalError
from . import hand_rig as H, hand_skin as S
from .rig_read import Rig, _armature_modifier  # noqa: F401  (rig_host.Rig: the live eval's hand checks read with it)
from .rig_spec import DEFAULT_VIEWS, validate_rig


def _fused_fingers(rig, hand_skin):
    """Adjacent fingers whose skin never parts between them: a segment from one middle bone's axis to the
    next stays inside the mesh. A mitten hand from image-to-3D can't be measured or folded (rig B): it must be
    replaced from a known-good hand's fit inputs, not refitted (doc/angelo/hand_rigging.rst)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    hand, skel, skin = hand_skin.hand, hand_skin.hand.skeleton, hand_skin.skin
    tri = skin.triangles[np.all(hand_skin.region[skin.triangles], axis=1)]
    tree = BVHTree.FromPolygons([tuple(p) for p in skin.rest], [tuple(t) for t in tri])
    fused = []
    for a, b in zip(hand.fingers, hand.fingers[1:]):
        ja, jb = a.joints[min(1, len(a.joints) - 1)], b.joints[min(1, len(b.joints) - 1)]
        pa = (skel.heads[ja] + skel.tails[ja]) / 2
        pb = (skel.heads[jb] + skel.tails[jb]) / 2
        span = float(np.linalg.norm(pb - pa))
        hit = tree.ray_cast(Vector(pa), Vector(pb - pa), span)
        if hit[0] is None:
            fused.append("%s-%s" % (a.role, b.role))
    return fused


def _hand_summary(rig, hand_skin):
    hand, skel = hand_skin.hand, rig.skeleton
    return {"hand": hand.name[:64], "forearm": skel.names[hand.forearm][:64] if hand.forearm is not None else None,
            "thumb": [skel.names[j][:64] for j in hand.thumb.joints],
            "fingers": [{"role": d.role, "bones": [skel.names[j][:64] for j in d.joints],
                         **({"metacarpal": skel.names[d.metacarpal][:64]} if d.metacarpal is not None else {})}
                        for d in hand.fingers],
            "palm_normal_world": [round(float(v), 4) for v in rig.direction_to_world(hand.palm_normal)],
            "palm_normal_source": hand.palm_source,
            "finger_radius": round(hand_skin.finger_radius, 5),
            **({"notes": hand.notes} if hand.notes else {})}


def _current(solver):
    """The current pose as ``hand_pose.Posed``: Blender's posed matrices, skinned here."""
    from .hand_pose import Posed
    posed = Posed.__new__(Posed)
    posed.angles = solver.current_angles()
    posed.posed = solver.skel.posed.copy()
    posed.positions = solver.skin.rest.copy()
    posed.positions[solver.region] = solver.skin.deform(solver.skel, posed.posed, solver.region)
    return posed


def _angles_by_name(skel, angles):
    return {skel.names[j][:64]: [round(v, 1) for v in a] for j, a in angles.items()}


def inspect(rig, args):
    from .hand_pose import Solver
    hands = []
    for hs in rig.skins:
        solver = Solver(hs, args.get("limits"), rig.budget)
        current = solver.current_angles()
        out_of_range = {}
        for j, a in current.items():
            off = H.outside(a, solver.limits[j])
            if off:
                out_of_range[rig.skeleton.names[j][:64]] = off
        entry = _hand_summary(rig, hs)
        entry.update(checklist=S.checklist(hs.hand), weights=S.weight_audit(hs),
                     fused_fingers=_fused_fingers(rig, hs), current_outside_limits=out_of_range)
        hands.append(entry)
    return {"hands": hands}


def contact(rig, args):
    from .hand_pose import Solver
    hands = []
    for hs in rig.skins:
        rig.require_reproducible(hs)
        solver = Solver(hs, args.get("limits"), rig.budget)
        posed = _current(solver)
        entry = {"hand": hs.hand.name[:64], "finger_radius": round(hs.finger_radius, 5),
                 "angles": _angles_by_name(rig.skeleton, posed.angles)}
        entry.update(solver.report(posed))
        hands.append(entry)
    return {"hands": hands}


def _one_hand(rig, tool):
    if len(rig.skins) != 1:
        raise RefusalError("%s works on one hand; name it (hand): %s"
                           % (tool, ", ".join(h.name[:64] for h in rig.hands)), code="VALIDATION")
    return rig.skins[0]


def joint_range(rig, args, references):
    from .hand_pose import Solver, joint_range as reach
    hs = _one_hand(rig, "angelo_rig_hand_range")
    rig.require_reproducible(hs)
    solver = Solver(hs, args.get("limits"), rig.budget)
    skel, hand = rig.skeleton, hs.hand
    wanted = args.get("joints")
    joints = [j for d in hand.digits for j in d.joints]
    if wanted:
        unknown = [n for n in wanted if n not in skel.index or skel.index[n] not in joints]
        if unknown:
            raise RefusalError("Not joints of hand %r: %s" % (hand.name[:64], ", ".join(n[:64] for n in unknown[:8])),
                               code="VALIDATION")
        joints = [skel.index[n] for n in wanted]
    rows, unmeasured = [], []
    first = {d.joints[0] for d in hand.digits}
    for j in joints:
        for axis in ("flex", "spread") if j in first else ("flex",):
            if solver.ran_out:
                unmeasured.append({"bone": skel.names[j][:64], "axis": axis})
                continue
            lo, hi = solver.limits[j][axis]
            if lo == hi == 0:
                continue
            r = reach(solver, j, axis)
            if solver.ran_out:   # Cut short: this joint's reach is where the budget stopped it, not a stop.
                unmeasured.append({"bone": skel.names[j][:64], "axis": axis})
                continue
            rows.append({"bone": skel.names[j][:64], "axis": axis, "limits": list(r["limits"]),
                         "reach": list(r["reach"]), "stopped_by": r["stopped_by"]})
    result = {"hand": hand.name[:64], "joints": rows,
              "note": ("Each joint moved alone from rest, the bones past it following; reach is where its skin "
                       "first sank %.2f finger radii into another part, or the limit." % 0.33)}
    if solver.ran_out:
        result.update(complete=False, unmeasured=unmeasured, budget=solver.budget_log(),
                      note_budget="The call's %.0f s budget ran out; the joints under unmeasured were not measured"
                      % solver.budget.seconds)
    if args.get("pictures"):
        from . import rig_views
        result["views"] = rig_views.range_sheets(rig, solver, [r for r in rows if r["axis"] == "flex"],
                                                 args.get("view", "THUMB_SIDE"), references)
    return result


def fist(rig, args, references):
    from . import hand_pose
    hands, operations = [], []
    solved = []
    for hs in rig.skins:
        rig.require_reproducible(hs)
        out = hand_pose.fist(hs, args.get("limits"), budget=rig.budget)
        skel = rig.skeleton
        for j in out["angles"]:
            # The basis the prediction was skinned with, so the applied pose is the predicted one (hand_inherit).
            rotation = H.euler_xyz(out["posed"].bases[j])
            operations.append({"kind": "bone_set", "target": rig.handle,
                               "parameters": {"bone": skel.names[j], "rotation": [round(v, 6) for v in rotation]}})
        entry = {"hand": hs.hand.name[:64], "angles": _angles_by_name(skel, out["angles"])}
        entry.update(out["log"])
        hands.append(entry)
        solved.append((hs, out))
    result = {"hands": hands, "operations": operations,
              "apply": ("Apply operations unchanged in one angelo_scene_program; then angelo_rig_hand_contact or "
                        "angelo_rig_hand_views on the result. Nothing has changed yet.")}
    short = [(hs.hand.name[:64], out["log"]["budget"]) for hs, out in solved if not out["log"]["complete"]]
    if short:
        result["complete"] = False
        result["apply"] = ("Incomplete: the call's %.0f s budget ran out (%s), so these operations pose only what was "
                           "solved by then, within the limits. Apply them only as a partial fist, or call again on one "
                           "hand. Nothing has changed yet." % (short[0][1]["seconds"], "; ".join(
                               "hand %r while %s" % (name, log["ran_out_during"]) for name, log in short)))
    if args.get("pictures"):
        from . import rig_views
        views = []
        for hs, out in solved:
            views += rig_views.pose_views(rig, out["solver"], out["posed"], args.get("views") or list(DEFAULT_VIEWS),
                                          references, label="fist")
        for extra in views[6:]:   # A result carries six pictures at most (references.tool_images).
            references.discard_observation(extra)
        result["views"] = views[:6]
    return result


def pose(rig, args, references):
    """Anatomical angles to bone_set operations (``angelo_rig_hand_pose``), with the pose's contacts."""
    from .hand_pose import Solver, Posed
    from .hand_rig import DIP_RATIO, AXES
    hs = _one_hand(rig, "angelo_rig_hand_pose")
    rig.require_reproducible(hs)
    solver = Solver(hs, args.get("limits"), rig.budget)
    skel, hand = rig.skeleton, hs.hand
    joints = {j for d in hand.digits for j in d.joints}
    unknown = [name for name in args["angles"] if name not in skel.index or skel.index[name] not in joints]
    if unknown:
        raise RefusalError("Not joints of hand %r: %s; its joints: %s" % (
            hand.name[:64], ", ".join(n[:64] for n in unknown[:8]),
            ", ".join(skel.names[j][:64] for d in hand.digits for j in d.joints)), code="VALIDATION")
    angles = {j: list(a) for j, a in solver.current_angles().items()}
    given = {skel.index[name]: axes for name, axes in args["angles"].items()}
    for j, axes in given.items():
        for axis, value in axes.items():
            angles[j][AXES.index(axis)] = value
    clamped = {}

    def inside(j):
        a = tuple(angles[j])
        angles[j] = H.clamp(a, solver.limits[j])
        if H.outside(a, solver.limits[j]):
            clamped[skel.names[j][:64]] = {"asked": [round(v, 1) for v in a], "set": [round(v, 1) for v in angles[j]]}
    for j in given:   # Only the joints this call writes: the others keep their current angles, as the scene will.
        inside(j)
    for digit in hand.fingers:   # An end joint not given follows its middle joint as clamped (the checklist's #11).
        if len(digit.joints) == 3 and digit.joints[1] in given and digit.joints[2] not in given:
            angles[digit.joints[2]] = [angles[digit.joints[1]][0] * DIP_RATIO, 0.0, 0.0]
            inside(digit.joints[2])
    posed = Posed(solver, angles)
    operations = []
    for j in sorted(set(given) | {d.joints[2] for d in hand.fingers if len(d.joints) == 3 and d.joints[1] in given}):
        rotation = H.euler_xyz(posed.bases[j])   # The basis the prediction was skinned with.
        operations.append({"kind": "bone_set", "target": rig.handle,
                           "parameters": {"bone": skel.names[j], "rotation": [round(v, 6) for v in rotation]}})
    result = {"hand": hand.name[:64], "angles": _angles_by_name(skel, {j: angles[j] for j in sorted(
        {skel.index[o["parameters"]["bone"]] for o in operations})}), "clamped_to_limits": clamped,
              "operations": operations,
              "apply": "Apply operations unchanged in one angelo_scene_program. Nothing has changed yet."}
    result.update(solver.report(posed))
    if args.get("pictures"):
        from . import rig_views
        result["views"] = rig_views.pose_views(rig, solver, posed, args.get("views") or list(DEFAULT_VIEWS),
                                               references, label="pose")
    return result


def views(rig, args, references):
    from . import rig_views
    from .hand_pose import Solver
    hs = _one_hand(rig, "angelo_rig_hand_views")
    rig.require_reproducible(hs)
    solver = Solver(hs, budget=rig.budget)
    return {"hand": hs.hand.name[:64],
            "views": rig_views.pose_views(rig, solver, _current(solver), args.get("views") or list(DEFAULT_VIEWS),
                                          references, markers=args.get("markers", False), label="current")}


def execute(window, name, arguments, references=None):
    args = validate_rig(name, arguments)
    started = time.perf_counter()
    try:
        return _execute(window, name, args, references, started)
    except H.HandError as error:   # A hand the solvers can't pose (a two-bone thumb, say): a refusal with the reason.
        raise RefusalError(str(error), code="VALIDATION") from None


def _execute(window, name, args, references, started):
    rig = Rig(window, args)
    from .hand_pose import MAX_SECONDS
    rig.budget = H.Budget(MAX_SECONDS, started)   # One budget for the call, the reading above included.
    if name == "angelo_rig_hand_inspect":
        body = inspect(rig, args)
    elif name == "angelo_rig_hand_contact":
        body = contact(rig, args)
    else:
        if name in ("angelo_rig_hand_range", "angelo_rig_hand_views") or args.get("pictures"):
            if references is None and (name == "angelo_rig_hand_views" or args.get("pictures")):
                raise RuntimeError("Bind a conversation reference store before drawing hand pictures")
        body = {"angelo_rig_hand_range": joint_range, "angelo_rig_hand_fist": fist, "angelo_rig_hand_pose": pose,
                "angelo_rig_hand_views": views}[name](rig, args, references)
    body.update({"success": True, "read_only": True, "scene_changed": False, "armature": rig.handle,
                 "mesh": rig.mesh_handle, "skinning_check": rig.checks,
                 "uncertain": bool(rig.uncertainty), "uncertainty": rig.uncertainty,
                 "native_ms": round((time.perf_counter() - started) * 1000, 1)})
    return body
