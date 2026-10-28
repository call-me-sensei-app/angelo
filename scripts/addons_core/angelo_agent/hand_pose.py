# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hand poses that move skin: the fold-to-limit fist and each joint's range of motion with skin contact stops.
numpy over ``hand_rig`` and ``hand_contact``; no scene change (poses are skinned here, never written).

The fist is folded, not aimed at targets (the art director: "fold each finger to the max for each joint",
2026-10-23): each knuckle to its limit, turning the finger toward the base of the thumb as it bends; then the middle
joints in small steps, the end joint following each, until the fingertip segments meet the palm's skin; then the
fingers close sideways until their skins rest on each other. The thumb then lies across the index and middle
fingers' middle segments (its last joint by the index finger's middle knuckle, its tip on the middle finger),
placed within its limits, settled onto the skin and folded on until it rests there. The two reference rigs (A,
B) came out within a few degrees of each other this way, nothing outside the limits.
"""

import itertools
import math
import time

import numpy as np

from . import hand_contact as C
from .hand_math import Budget, HandError, perpendicular, segment_distance, unit
from .hand_rig import (AXES, DIP_RATIO, clamp, forward_kinematics, joint_angles, joint_frames, joint_rotation,
                       limits_for, outside)
from .hand_skin import PALM

STEP = 2.0                 # degrees per middle-joint step while folding
CONVERGE = {"index": 0.0, "middle": 4.0, "ring": 8.0, "little": 12.0}   # spread toward the thumb at a full knuckle bend
CLOSE_STEP = 0.5           # degrees per sideways step
THUMB_IP_AT = 0.15         # along the index finger's middle segment, from its middle knuckle: the thumb's last joint
THUMB_TIP_AT = 0.15        # along the middle finger's middle segment: the thumb's tip
THUMB_IP_FRONT = 0.3       # how far round the index finger's front edge the thumb's last joint sits
ACROSS_WEIGHT = 0.67       # finger radii of cost for a thumb's last segment along a finger instead of across them
MAX_EVALUATIONS = 260      # skinned thumb placements tried, at most
MAX_SECONDS = 20.0         # one budget for a whole tool call, reading the rig included (``Budget``)


class Posed:
    """A hand pose's joint angles skinned: posed matrices and the hand skin's positions (armature space)."""

    def __init__(self, solver, angles):
        self.angles = dict(angles)
        rels = {j: joint_rotation(solver.frames[j], *a) for j, a in angles.items()}
        self.rels = rels
        self.bases = {}   # Each joint's basis rotation: what its bone_set writes.
        self.posed = forward_kinematics(solver.skel, solver.hand.bone, rels, self.bases)
        self.positions = solver.skin.rest.copy()
        self.positions[solver.region] = solver.skin.deform(solver.skel, self.posed, solver.region)


class Solver:
    """One hand's pose work: frames, limits, the skin and contact, with the rest pose of its digits skinned once."""

    def __init__(self, hand_skin, limits=None, budget=None):
        self.hs, self.hand, self.skin = hand_skin, hand_skin.hand, hand_skin.skin
        self.budget = budget or Budget(MAX_SECONDS)
        self.ran_out = None   # The stage the budget ran out in, if it did.
        self.skel = self.hand.skeleton
        self.frames = joint_frames(self.hand)
        self.limits = limits_for(self.hand, limits)
        self.contact = C.Contact(hand_skin)
        self.region = hand_skin.vertices
        self.joints = [j for d in self.hand.digits for j in d.joints]
        self.rest = Posed(self, {j: (0.0, 0.0, 0.0) for j in self.joints})
        self.r = hand_skin.finger_radius
        self.evaluations = 0

    def out_of_time(self, stage):
        """True once the call's budget is spent; the first stage that finds it so is recorded."""
        if self.budget.expired():
            self.ran_out = self.ran_out or stage
            return True
        return False

    def budget_log(self):
        return {"seconds": self.budget.seconds, "spent": round(self.budget.spent(), 2),
                "ran_out_during": self.ran_out}

    def pose(self, angles):
        self.evaluations += 1
        return Posed(self, angles)

    def measure(self, posed, pairs, surfaces=None):
        return self.contact.measure(posed.positions, posed.posed, pairs, self.rest.positions, self.rest.posed,
                                    surfaces)

    def current_angles(self):
        """The joints' current angles (from the armature's pose) in the anatomical frames."""
        from .hand_rig import current_relative
        return {j: joint_angles(current_relative(self.skel, j), self.frames[j]) for j in self.joints}

    def report(self, posed):
        """Limits, contacts between neighbours and the thumb, and fingertip depth in the palm, for a pose."""
        hand = self.hand
        out_of_range = {}
        for j, a in posed.angles.items():
            off = outside(a, self.limits[j])
            if off:
                out_of_range[self.skel.names[j]] = off
        digits = list(range(len(hand.digits)))
        fingers = [k for k in digits if hand.digits[k].role != "thumb"]
        pairs = [(a, b) for a, b in zip(fingers, fingers[1:])]
        pairs += [(0, k) for k in fingers]
        m = self.measure(posed, pairs + [(b, a) for a, b in pairs])
        contacts = {}
        for a, b in pairs:
            gap = min(m[(a, b)][0], m[(b, a)][0])
            depth = max(m[(a, b)][1], m[(b, a)][1])
            contacts["%s-%s" % (hand.digits[a].role, hand.digits[b].role)] = {
                "gap_finger_radii": None if not math.isfinite(gap) else round(gap, 3),
                "depth_finger_radii": round(depth, 3)}
        palm = {hand.digits[k].role: round(self.contact.palm_depth(posed.positions, posed.posed, k), 3)
                for k in fingers}
        return {"outside_limits": out_of_range, "contacts": contacts, "fingertips_in_palm_finger_radii": palm}


def _finger_order(hand):
    fingers = list(range(1, len(hand.digits)))
    roles = [hand.digits[k].role for k in fingers]
    centre = roles.index("middle") if "middle" in roles else len(fingers) // 2
    neighbour = {}
    for i, k in enumerate(fingers):
        if i != centre:
            neighbour[k] = fingers[i + 1] if i < centre else fingers[i - 1]
    return fingers, neighbour


def fold_fingers(solver):
    """The fingers folded to their limits with palm contact, then closed side by side. Returns (angles, log)."""
    hand, limits = solver.hand, solver.limits
    fingers, neighbour = _finger_order(hand)
    angles = {j: (0.0, 0.0, 0.0) for j in solver.joints}
    mcp = {k: limits[hand.digits[k].joints[0]]["flex"][1] for k in fingers}
    pip = {k: 0.0 for k in fingers}
    close = {k: 0.0 for k in fingers}

    def set_fingers():
        for k in fingers:
            d = hand.digits[k]
            j0 = d.joints[0]
            lo, hi = limits[j0]["spread"]
            spread = CONVERGE.get(d.role, 0.0) * mcp[k] / max(limits[j0]["flex"][1], 1e-6)
            if k in neighbour:   # Positive spread turns toward the thumb side, where the lower-numbered digits are.
                spread += close[k] * (1.0 if neighbour[k] < k else -1.0)
            angles[j0] = (mcp[k], min(max(spread, lo), hi), 0.0)
            if len(d.joints) > 1:
                angles[d.joints[1]] = (pip[k], 0.0, 0.0)
            if len(d.joints) > 2:
                angles[d.joints[2]] = (min(pip[k] * DIP_RATIO, limits[d.joints[2]]["flex"][1]), 0.0, 0.0)
        return solver.pose(angles)

    folding = {k for k in fingers if len(hand.digits[k].joints) > 1}
    met = set()
    while folding and not solver.out_of_time("folding the fingers"):
        for k in folding:
            pip[k] = min(pip[k] + STEP, limits[hand.digits[k].joints[1]]["flex"][1])
        posed = set_fingers()
        for k in list(folding):
            if solver.contact.palm_depth(posed.positions, posed.posed, k) >= C.PALM_DEPTH:
                pip[k] -= STEP   # back to the last step before the skin went in
                folding.discard(k)
                met.add(k)
            elif pip[k] >= limits[hand.digits[k].joints[1]]["flex"][1]:
                folding.discard(k)
    posed = set_fingers()
    # Side by side: close each finger toward its neighbour nearer the middle until the skins rest on each other;
    # open it instead when the plain curl already sinks one into the other.
    for k, nb in neighbour.items():
        if solver.out_of_time("closing the fingers sideways"):
            break

        def pair():
            p = set_fingers()
            m = solver.measure(p, [(k, nb), (nb, k)])
            return min(m[(k, nb)][0], m[(nb, k)][0]), max(m[(k, nb)][1], m[(nb, k)][1])
        gap, depth = pair()
        step = CLOSE_STEP if depth <= C.SKIN_DEPTH else -CLOSE_STEP
        for _ in range(40):
            if (step > 0 and gap <= C.CONTACT_GAP) or solver.out_of_time("closing the fingers sideways"):
                break
            close[k] += step
            gap, depth = pair()
            if step > 0 and depth > C.SKIN_DEPTH:
                close[k] -= step
                break
            if step < 0 and depth <= C.SKIN_DEPTH:
                break
    posed = set_fingers()
    log = {hand.digits[k].role: {"knuckle_flex": round(mcp[k], 1), "middle_flex": round(pip[k], 1),
                                 "met_palm": k in met, "closed_sideways": round(close[k], 1)} for k in fingers}
    return angles, posed, log


def _bone_radius(solver, bone):
    skin, skel = solver.skin, solver.skel
    idx = np.where(skin.weights_of([bone]) > 0.5)[0]
    idx = idx[np.isin(idx, solver.region)]
    if not len(idx):
        return solver.r
    return float(np.median(segment_distance(skin.rest[idx], skel.heads[bone], skel.tails[bone])))


def place_thumb(solver, angles, posed):
    """The thumb across the index and middle fingers' middle segments, within its limits, resting on them."""
    hand, skel, limits, r = solver.hand, solver.skel, solver.limits, solver.r
    thumb = hand.thumb.joints
    roles = {d.role: k for k, d in enumerate(hand.digits)}
    index, middle = roles["index"], roles.get("middle", roles.get("ring", roles["little"]))
    if len(thumb) < 3:
        raise HandError("Hand %r: its thumb has %d bones; the fist's thumb needs three" % (hand.name[:64], len(thumb)))
    # Work in the hand's rest frame: undo the hand bone's own pose.
    to_rest = np.linalg.inv(posed.posed[hand.bone] @ np.linalg.inv(skel.rest[hand.bone]))

    def rest_point(p):
        return (to_rest @ np.append(p, 1.0))[:3]

    def segment(k, i):
        b = hand.digits[k].joints[min(i, len(hand.digits[k].joints) - 1)]
        head = posed.posed[b][:3, 3]
        tail = (posed.posed[b] @ np.linalg.inv(skel.rest[b]) @ np.append(skel.tails[b], 1.0))[:3]
        return rest_point(head), rest_point(tail)

    fingers = [k for k in range(len(hand.digits)) if k != 0]
    seg = {k: segment(k, 1) for k in fingers}
    seg_mid = {k: (a + b) / 2 for k, (a, b) in seg.items()}
    knuckles = [rest_point(posed.posed[hand.digits[k].joints[0]][:3, 3]) for k in fingers]
    centre = np.mean([rest_point(posed.posed[j][:3, 3]) for k in fingers for j in hand.digits[k].joints], axis=0)
    row = unit(seg_mid[fingers[-1]] - seg_mid[index])
    face_out = unit(perpendicular((seg_mid[index] + seg_mid[middle]) / 2 - centre, row))
    front = unit(perpendicular(knuckles[0] - knuckles[-1], hand.axis()))
    radius = {b: _bone_radius(solver, b) for b in (thumb[1], thumb[2], hand.digits[index].joints[0],
                                                    hand.digits[index].joints[1], hand.digits[middle].joints[1])}
    r_t = radius[thumb[2]]
    corner = unit(front * THUMB_IP_FRONT + face_out)
    a, b = seg[index]
    ip_target = a + (b - a) * THUMB_IP_AT + corner * (radius[hand.digits[index].joints[1]] + r_t)
    a, b = seg[middle]
    tip_target = a + (b - a) * THUMB_TIP_AT + face_out * (radius[hand.digits[middle].joints[1]] + r_t)

    h0 = skel.heads[thumb[0]]
    rest_dir = [unit(skel.tails[t] - skel.heads[t]) for t in thumb]
    lengths = [float(np.linalg.norm(skel.tails[t] - skel.heads[t])) for t in thumb]
    frames = solver.frames
    spans = (limits[thumb[0]]["flex"], limits[thumb[0]]["spread"], limits[thumb[0]]["twist"],
             (0.0, limits[thumb[1]]["flex"][1]), (0.0, limits[thumb[2]]["flex"][1]))

    def chain(x):
        q1 = joint_rotation(frames[thumb[0]], x[0], x[1], x[2])
        q2 = q1 @ joint_rotation(frames[thumb[1]], x[3], 0.0, 0.0)
        q3 = q2 @ joint_rotation(frames[thumb[2]], x[4], 0.0, 0.0)
        p1 = h0 + q1 @ rest_dir[0] * lengths[0]
        p2 = p1 + q2 @ rest_dir[1] * lengths[1]
        p3 = p2 + q3 @ rest_dir[2] * lengths[2]
        return p1, p2, p3

    index_axis = seg[index]

    def shape_cost(x, gap):
        p1, p2, p3 = chain(x)
        fit = np.linalg.norm(p2 - (ip_target + corner * gap)) + np.linalg.norm(p3 - (tip_target + face_out * gap))
        # The thicker middle segment of the thumb clears the index finger.
        clear = radius[thumb[1]] + radius[hand.digits[index].joints[1]]
        fit += max(0.0, clear - float(segment_distance(p1[None], *index_axis)[0]))
        # The base stays out of the fist's front silhouette (no thumb hooking out to the side).
        fit += max(0.0, float((p1 - seg_mid[index]) @ front) - (radius[hand.digits[index].joints[0]] + 0.45 * r))
        # A soft preference for how a thumb closes in a fist: MCP ~55, IP ~65 degrees, the base not rolled.
        fit += 0.009 * r * (abs(x[3] - 55) + abs(x[4] - 65) + abs(x[2]))
        # The last segment lies across the row of fingers, not along a finger (the checklist's #19).
        fit += ACROSS_WEIGHT * r * (1.0 - abs(float(unit(p3 - p2) @ row)))
        return fit / r

    def span(i, n):
        lo, hi = spans[i]
        return [lo + (hi - lo) * t / (n - 1) for t in range(n)]

    grid = list(itertools.product(span(0, 6), span(1, 6), span(2, 4), span(3, 5), span(4, 5)))

    def refine(x, gap):
        x, c = list(x), shape_cost(x, gap)
        for step in (8.0, 4.0, 2.0, 1.0):
            improved = True
            while improved:
                improved = False
                for i in range(5):
                    for sign in (1, -1):
                        y = list(x)
                        y[i] = min(max(y[i] + sign * step, spans[i][0]), spans[i][1])
                        cy = shape_cost(y, gap)
                        if cy < c - 1e-9:
                            x, c, improved = y, cy, True
        return tuple(x)

    others = [k for k in fingers]
    surfaces = {k: solver.contact.surface(posed.positions, k) for k in others}
    pairs = [(0, k) for k in others]

    def thumb_pose(x):
        trial = dict(angles)
        trial[thumb[0]] = (x[0], x[1], x[2])
        trial[thumb[1]] = (x[3], 0.0, 0.0)
        trial[thumb[2]] = (x[4], 0.0, 0.0)
        return trial

    def skin(x):
        """The thumb's closest gap to the fingers and deepest sinking, both ways (its skin into theirs, and theirs
        into its: a thumb pressed on a finger shows on either side)."""
        p = solver.pose(thumb_pose(x))
        m = solver.measure(p, pairs, surfaces)
        m.update(solver.measure(p, [(k, 0) for k in others]))
        return min(g for g, _ in m.values()), max(dp for _, dp in m.values())

    best, tried = None, []
    for gap in (0.0, 0.11 * r, 0.22 * r, 0.33 * r):
        ranked = sorted(grid, key=lambda x: shape_cost(x, gap))
        seeds, seen = [], set()
        for x in ranked:
            basin = (x[0] // 30, x[1] // 30, x[2])
            if basin not in seen:
                seen.add(basin)
                seeds.append(x)
            if len(seeds) == 8:
                break
        ranked = sorted({refine(x, gap) for x in seeds} | set(ranked[:8]), key=lambda x: shape_cost(x, gap))
        for x in ranked[:12]:
            if solver.out_of_time("placing the thumb"):
                break
            g, d = skin(x)
            tried.append((d, x))
            if d <= C.SKIN_DEPTH:
                c = shape_cost(x, gap)
                if best is None or c < best[1]:
                    best = (x, c, gap)
        if best or solver.ran_out:
            break
    if best is None and not tried:
        return thumb_pose(tuple(angles[thumb[0]]) + (angles[thumb[1]][0], angles[thumb[2]][0])), {
            "note": "The time budget ran out before any thumb placement was tried; the thumb is left as it was"}
    if best is None:
        tried.sort(key=lambda t: t[0])
        x = tried[0][1]
        note = ("No thumb placement within its limits kept the thumb's skin out of the fingers' (least: %.2f finger "
                "radii deep); the thumb is left at that one" % tried[0][0])
    else:
        x, note = best[0], None
    # Settle onto the skin: the pad resting on the fingers without sinking in, the anatomical fit a soft term.
    gap0 = best[2] if best else 0.0

    def settle_cost(y):
        g, d = skin(y)
        return g + 20.0 * max(0.0, d - C.SKIN_DEPTH) + shape_cost(y, gap0), g, d

    c, g, d = settle_cost(x)
    for step in (4.0, 2.0, 1.0):
        improved = True
        while improved and solver.evaluations < MAX_EVALUATIONS and not solver.out_of_time("settling the thumb"):
            improved = False
            for i in range(5):
                for sign in (1, -1):
                    y = list(x)
                    y[i] = min(max(y[i] + sign * step, spans[i][0]), spans[i][1])
                    cy = settle_cost(tuple(y))
                    if cy[0] < c - 1e-9:
                        x, (c, g, d), improved = tuple(y), cy, True
    # Rest the thumb on the fingers: fold its last joint, then its middle one, on until the skin touches.
    for i in (4, 3):
        while g > C.CONTACT_GAP and x[i] + 1 <= spans[i][1] and solver.evaluations < MAX_EVALUATIONS \
                and not solver.out_of_time("resting the thumb on the fingers"):
            y = list(x)
            y[i] += 1
            g2, d2 = skin(tuple(y))
            if d2 > C.SKIN_DEPTH:
                break
            x, g, d = tuple(y), g2, d2
    p1, p2, p3 = chain(x)
    log = {"base_flex_spread_twist": [round(v, 1) for v in x[:3]], "middle_and_last_flex": [round(v, 1) for v in x[3:]],
           "rests_on_fingers": bool(g <= C.CONTACT_GAP * 3 and d <= C.SKIN_DEPTH),
           "skin_gap_finger_radii": round(g, 3) if math.isfinite(g) else None,
           "skin_depth_finger_radii": round(d, 3),
           "last_joint_from_target_finger_radii": round(float(np.linalg.norm(p2 - ip_target)) / r, 2),
           "tip_from_target_finger_radii": round(float(np.linalg.norm(p3 - tip_target)) / r, 2)}
    if note:
        log["note"] = note
    return thumb_pose(x), log


def fist(hand_skin, limits=None, thumb=True, budget=None):
    """The fold-to-limit fist of one hand: {"angles": {bone index: (flex, spread, twist)}, "posed": Posed, "log"}.
    The log's ``complete`` is False when the call's budget ran out first; ``budget`` names the stage it ran out in."""
    solver = Solver(hand_skin, limits, budget)
    if solver.out_of_time("reading the rig"):
        raise HandError("The call's %.0f s budget ran out before hand %r was solved (%.1f s spent reading the rig and "
                        "its skin); nothing was solved" % (solver.budget.seconds, hand_skin.hand.name[:64],
                                                           solver.budget.spent()))
    started = time.perf_counter()
    angles, posed, log = fold_fingers(solver)
    out = {"fingers": log}
    if thumb:
        angles, out["thumb"] = place_thumb(solver, angles, posed)
    final = {j: clamp(a, solver.limits[j]) for j, a in angles.items()}
    posed = solver.pose(final)
    out.update(solver.report(posed))
    out["evaluations"] = solver.evaluations
    out["seconds"] = round(time.perf_counter() - started, 2)
    out["complete"], out["budget"] = solver.ran_out is None, solver.budget_log()
    return {"angles": final, "posed": posed, "log": out, "solver": solver}


# --- range of motion ----------------------------------------------------------------------------------------------------

# The rigs' joint sheets (on rigs A and B), in finger radii: contact is skin sinking a third of a finger
# radius into the still rest of the hand; skin within FOLD_RADIUS of the joint folds over itself, it does not collide;
# moving skin within REST_TOUCH of the still skin at rest already touches it there (a web, a neighbour).
CONTACT_STOP = 0.33
FOLD_RADIUS = 1.6          # x the moving bone's own skin radius
REST_TOUCH = 0.21
STILL_REACH = 1.05
STILL_WRONG_SIDE = 0.85
RANGE_STEP = 2.0


def joint_range(solver, joint, axis="flex"):
    """One joint moved alone through its range along ``axis`` (the bones past it follow), from rest toward each
    limit in RANGE_STEP steps, stopping where its moving skin sinks CONTACT_STOP into the still rest of the hand.
    Returns {"limits": (lo, hi), "reach": (lo, hi), "stopped_by": {"back": part or None, "forward": ...}}."""
    hand, skel, skin, r = solver.hand, solver.skel, solver.skin, solver.r
    k = next(i for i, d in enumerate(hand.digits) if joint in d.joints)
    digit = hand.digits[k]
    share = skin.weights_of(digit.joints[digit.joints.index(joint):])
    fold = np.linalg.norm(skin.rest - skel.heads[joint], axis=1) < FOLD_RADIUS * _bone_radius(solver, joint)
    region = np.zeros(len(skin.rest), dtype=bool)
    region[solver.region] = True
    moving = np.where(region & (share > 0.5) & ~fold)[0]
    still = region & (share < 0.02) & ~fold
    tri = skin.triangles[np.all(still[skin.triangles], axis=1)]
    surface = C.Surface(solver.rest.positions, tri)
    owner = solver.hs.owner

    def depth(positions):
        hits, normals, index = surface.nearest(positions[moving], STILL_REACH * r)
        found = index >= 0
        d = np.full(len(moving), np.inf)
        d[found] = ((positions[moving][found] - hits[found]) * normals[found]).sum(axis=1) / r
        return d, index

    rest_d, _ = depth(solver.rest.positions)
    keep = rest_d > REST_TOUCH   # Clear of the still skin at rest: touching or inside it there is the mesh's.
    lo, hi = solver.limits[joint][axis]
    which = AXES.index(axis)
    reach, stopped = {}, {}
    for direction, end in (("back", lo), ("forward", hi)):
        value, hit = 0.0, None
        sign = 1.0 if end >= 0 else -1.0
        while abs(value) < abs(end) - 1e-9 and not solver.out_of_time("measuring joint ranges"):
            nxt = value + sign * min(RANGE_STEP, abs(end - value))
            angles = {j: (0.0, 0.0, 0.0) for j in solver.joints}
            a = [0.0, 0.0, 0.0]
            a[which] = nxt
            angles[joint] = tuple(a)
            d, index = depth(solver.pose(angles).positions)
            inside = keep & (d < 0) & (d > -STILL_WRONG_SIDE)
            if inside.sum() >= 3 and -np.sort(d[inside])[2] >= CONTACT_STOP:
                deepest = index[inside][np.argsort(d[inside], kind="stable")[:3]]
                parts = owner[tri[deepest][:, 0]]
                part = int(np.bincount(parts - parts.min()).argmax() + parts.min())
                hit = "palm" if part == PALM else hand.digits[part].role
                break
            value = nxt
        reach[direction] = round(value, 1)
        stopped[direction] = hit
    return {"limits": (lo, hi), "reach": (reach["back"], reach["forward"]), "stopped_by": stopped}
