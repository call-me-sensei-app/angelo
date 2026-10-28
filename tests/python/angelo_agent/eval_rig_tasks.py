# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live eval's hand-rigging set (``rig``): fists and a finger curl on skinned hands, graded by closed host checks.

Each task starts from a harness-made hand (``hand_rig`` fixture: ``hand_fixture_scenes``, a metaball body bound with
heat weights) and asks in plain words for a pose, never naming a tool. ``hand_pose`` grades the pose the agent left,
read with the RIG tools' own reader (``rig_host.Rig.of``) and measured in the hand's anatomical frames:

* FIST: every joint inside its limits (3 degrees' slack); every finger folded (knuckle at two thirds of its limit or
  more, middle joint 60 degrees or more); no skin through skin (no pair of parts deeper than 0.25 finger radii, no
  fingertip deeper than 0.5 in the palm); the thumb outside the fist, resting on the index or middle finger (gap
  0.3 finger radii or less) with its last segment off the palm (0.5 or more).
* CURL_INDEX: the index finger's knuckle at 60 degrees or more and its middle joint at 80 or more, the end joint
  following (0.4-0.95 of the middle joint), inside its limits, no skin through skin, every other joint within 5
  degrees of rest.

Thresholds are the checklist's, in the tools' own unit (the finger radius), and were set before any trial ran. The
``--hand-guidance off`` arm removes ``HAND_GUIDANCE`` from the lookup; both arms keep the tools. ``--chosen-tool
hands`` chooses Work on hands in the + menu before each message, as a person would. The harness calls this module's
hooks (``add_arguments``, ``environment``, ``arm``, ``chosen``). Importable without Blender: the fixture, check and
hooks take ``bpy`` and the add-on when they run.
"""

from contextlib import contextmanager
import math
import os

RIG = "Eval Hand Rig"
BODY = "Eval Hand Body"
SLACK = 3.0
DEPTH = 0.25
TIP_DEPTH = 0.5
THUMB_GAP = 0.3
THUMB_OFF_PALM = 0.5


def _task(task_id, layout, prompt, pose, hand, source):
    # ``checks`` stays empty, as a survey request's: the driver's closed scene checks have no hand kind, and the
    # result checks grade the task (eval_grading_live).
    return {"id": task_id, "kind": "edit", "requires_object": False, "prompt": prompt, "checks": [],
            "fixtures": [{"kind": "hand_rig", "layout": layout}],
            "result_checks": [{"kind": "hand_pose", "pose": pose, "armature": RIG, "mesh": BODY, "hand": hand,
                               "label": "%s %s" % (hand, pose.lower())}],
            "source": source}


def build_rig_tasks():
    return [
        _task("fist_hanging", "HANGING", "Pose the left hand of the armature '%s' in a natural, closed fist." % RIG,
              "FIST", "LeftHand", "hand rigging: a fist, arms down with the palms toward the thighs"),
        _task("fist_t_pose", "T_POSE", "'%s' stands in a T-pose. Close its left hand into a natural fist." % RIG,
              "FIST", "LeftHand", "hand rigging: a fist, a T-pose with the palms down"),
        _task("fist_cartoon", "CARTOON", ("'%s' is a cartoon character with three fingers and a thumb on each hand. "
                                          "Close its right hand into a natural fist.") % RIG,
              "FIST", "RightHand", "hand rigging: a fist on a three-fingered hand"),
        _task("curl_index", "HANGING", ("On the armature '%s', curl the left index finger as far as it naturally "
                                        "bends. Leave the other fingers and the thumb as they are.") % RIG,
              "CURL_INDEX", "LeftHand", "hand rigging: one finger through its range"),
    ]


def make_hand(bpy, scene, fixture):
    """The ``hand_rig`` fixture: the layout's armature and skinned mesh, under the set's names."""
    import hand_fixture_scenes
    made = hand_fixture_scenes.build(fixture["layout"])
    made["armature"].name, made["mesh"].name = RIG, BODY
    return ["fixture:hand_rig:" + fixture["layout"]], None


def _measure(bpy, scene, check):
    from types import SimpleNamespace
    from angelo_agent import rig_host
    from angelo_agent.hand_pose import Solver
    armature, mesh = scene.objects.get(check["armature"]), scene.objects.get(check["mesh"])
    if armature is None or mesh is None:
        return None
    window = SimpleNamespace(scene=scene, view_layer=bpy.context.view_layer)
    rig = rig_host.Rig.of(window, armature, mesh, {"hand": check["hand"]})
    solver = Solver(rig.skins[0])
    posed = rig_host._current(solver)
    return rig, solver, posed


def _thumb_off_palm(solver, posed):
    """How far the thumb's last segment is from the palm's skin (finger radii; negative inside it)."""
    import numpy as np
    from angelo_agent.hand_skin import PALM
    hand, skin = solver.hand, solver.skin
    last = hand.thumb.joints[-1]
    idx = np.where((solver.hs.owner == 0) & (skin.weights_of([last]) > 0.5))[0]
    d = solver.contact.signed(posed.positions, posed.posed, 0, PALM, idx) / solver.r
    d = d[np.isfinite(d)]
    return float(d.min()) if len(d) else math.inf


def check_hand_pose(bpy, scene, resolve, check):
    """``(passed, details)`` of one hand's pose (module docstring)."""
    from angelo_agent import hand_rig as H
    measured = _measure(bpy, scene, check)
    if measured is None:
        return False, {"reason": "object missing"}
    rig, solver, posed = measured
    hand, skel = solver.hand, solver.skel
    angles = posed.angles
    names = {d.role: d for d in hand.digits}
    report = solver.report(posed)
    outside = {skel.names[j]: off for j, a in angles.items()
               for off in [H.outside(a, solver.limits[j], tolerance=SLACK)] if off}
    deepest = max([c["depth_finger_radii"] for c in report["contacts"].values()] or [0.0])
    tips = max(report["fingertips_in_palm_finger_radii"].values() or [0.0])
    details = {"angles": {d.role: [[round(v, 1) for v in angles[j]] for j in d.joints] for d in hand.digits},
               "outside_limits": outside, "deepest_contact_finger_radii": round(deepest, 3),
               "fingertip_in_palm_finger_radii": round(tips, 3), "contacts": report["contacts"],
               "skinning_check": rig.checks}
    parts = {"inside_limits": not outside}
    if check["pose"] == "FIST":
        parts["folded"] = all(
            angles[d.joints[0]][0] >= 0.67 * solver.limits[d.joints[0]]["flex"][1]
            and (len(d.joints) < 2 or angles[d.joints[1]][0] >= 60.0) for d in hand.fingers)
        parts["no_skin_through_skin"] = deepest <= DEPTH and tips <= TIP_DEPTH
        on = [report["contacts"].get("thumb-%s" % role) for role in ("index", "middle")]
        gaps = [c["gap_finger_radii"] for c in on if c and c["gap_finger_radii"] is not None]
        off_palm = _thumb_off_palm(solver, posed)
        details["thumb_gap_finger_radii"] = round(min(gaps), 3) if gaps else None
        details["thumb_off_palm_finger_radii"] = round(off_palm, 3)
        parts["thumb_on_the_fingers_outside"] = bool(gaps) and min(gaps) <= THUMB_GAP and off_palm >= THUMB_OFF_PALM
    else:
        index = names["index"].joints
        flex = [angles[j][0] for j in index]
        parts["curled"] = flex[0] >= 60.0 and flex[1] >= 80.0 and (
            len(flex) < 3 or 0.4 <= flex[2] / max(flex[1], 1e-6) <= 0.95)
        index_contacts = [c["depth_finger_radii"] for key, c in report["contacts"].items() if "index" in key.split("-")]
        parts["no_skin_through_skin"] = (max(index_contacts or [0.0]) <= DEPTH
                                         and report["fingertips_in_palm_finger_radii"].get("index", 0.0) <= TIP_DEPTH)
        parts["others_left_alone"] = all(abs(v) <= 5.0 for d in hand.digits if d.role != "index"
                                         for j in d.joints for v in angles[j])
    details["parts"] = parts
    return all(parts.values()), details


def add_arguments(parser):
    parser.add_argument("--hand-guidance", choices=("on", "off"), default="on",
                        help="off removes HAND_GUIDANCE from the rig tools' lookup (the rig set's trials)")
    parser.add_argument("--chosen-tool", choices=("", "hands"), default="",
                        help="hands: choose Work on hands in the + menu before each message")


def environment(args):
    return {"ANGELO_EVAL_HAND_GUIDANCE": getattr(args, "hand_guidance", "on"),
            "ANGELO_EVAL_CHOSEN_TOOL": getattr(args, "chosen_tool", "")}


def arm():
    """In the trial: apply the arm's guidance setting; returns the arm for the trial report."""
    guidance = os.environ.get("ANGELO_EVAL_HAND_GUIDANCE", "on")
    if guidance == "off":
        from angelo_agent.agent_guidance import FAMILY_GUIDANCE
        FAMILY_GUIDANCE.pop("RIG", None)
    return {"hand_guidance": guidance, "chosen_tool": os.environ.get("ANGELO_EVAL_CHOSEN_TOOL", "")}


@contextmanager
def chosen(controller, window, observation, record):
    """Around one send: perform the arm's chosen tool as the chat's Send step does (``console.execute``), add its
    facts to the observation, and report its row after the person's message."""
    tool = os.environ.get("ANGELO_EVAL_CHOSEN_TOOL", "")
    performed = None
    if tool:
        from angelo_agent import chosen_tools as ct, chosen_tools_host as host
        controller.chosen_tools = ct.choose([], tool, tool)
        performed = host.perform_for_request(controller, window)
        record["chosen_tool"] = {"tool": tool, "done": bool(performed and performed[0][0]["done_by_angelo"]),
                                 "not_done": performed[0][0].get("not_done") if performed else "busy"}
        observation.update(host.observation_facts(performed))
    try:
        yield
    finally:
        if performed:
            host.report(controller, performed)
