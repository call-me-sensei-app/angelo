# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A mesh skinned to the armature, one hand's skin and its parts, the anatomy checklist and the weight audit, for
the RIG tools. numpy only.

Skinning is linear blending of the deforming bones' vertex-group weights, normalised per vertex as Blender's
Armature modifier does; ``rig_host`` checks it against Blender's own result on every call. Every distance judged
here is a fraction of the hand's own finger radius (``HandSkin.finger_radius``), never millimetres of one hand.
"""

import math

import numpy as np

from .hand_math import HandError, segment_distance, unit


class Skin:
    """A mesh's vertices skinned to the armature: rest positions in armature space and the weights as parallel
    arrays (vertex, bone, weight) over deforming bones, normalised per vertex as Blender's Armature modifier
    normalises them."""

    def __init__(self, rest_positions, vertex, bone, weight, triangles, edges):
        self.rest = np.asarray(rest_positions, dtype=np.float64).reshape(-1, 3)
        self.vertex = np.asarray(vertex, dtype=np.int64)
        self.bone = np.asarray(bone, dtype=np.int64)
        self.weight = np.asarray(weight, dtype=np.float64)
        self.triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        self.edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
        totals = np.zeros(len(self.rest))
        np.add.at(totals, self.vertex, self.weight)
        self.total = totals

    def deform(self, skel, posed, vertices=None):
        """Positions (armature space) of ``vertices`` (indices; all when None) for ``posed`` matrices: linear blend
        of each bone's posed @ rest^-1, normalised by total weight; unweighted vertices keep their rest position."""
        deform = posed @ np.linalg.inv(skel.rest)
        if vertices is None:
            vertices = np.arange(len(self.rest))
        vertices = np.asarray(vertices, dtype=np.int64)
        local = np.full(len(self.rest), -1, dtype=np.int64)
        local[vertices] = np.arange(len(vertices))
        at = local[self.vertex]
        keep = at >= 0
        b, w, at = self.bone[keep], self.weight[keep], at[keep]
        points = np.concatenate([self.rest[self.vertex[keep]], np.ones((len(at), 1))], axis=1)
        moved = np.einsum("kij,kj->ki", deform[b, :3, :], points) * w[:, None]
        out = np.zeros((len(vertices), 3))
        np.add.at(out, at, moved)
        totals = self.total[vertices]
        result = self.rest[vertices].copy()
        weighted = totals > 1e-6
        result[weighted] = out[weighted] / totals[weighted, None]
        return result

    def weights_of(self, bones):
        """Per vertex, the summed normalised weight of ``bones`` (indices)."""
        bones = np.asarray(sorted(bones), dtype=np.int64)
        share = np.zeros(len(self.rest))
        pick = np.isin(self.bone, bones)
        np.add.at(share, self.vertex[pick], self.weight[pick])
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(self.total > 1e-6, share / np.maximum(self.total, 1e-12), 0.0)


# --- the hand's skin: region, parts and size --------------------------------------------------------------------------

PALM = -1


class HandSkin:
    """The vertices of one hand and which part owns each: a digit (its index in ``hand.digits``) or the palm.

    The region is flooded from the fingertip vertices along mesh edges without crossing the wrist plane, so skin
    of the thigh or the other hand lying close by is never part of it (rig B: heat weights had put thigh weight on 626
    hand vertices; doc/angelo/hand_rigging.rst). A vertex belongs to the part whose bones carry most of its weight.
    """

    def __init__(self, hand, skin):
        self.hand, self.skin = hand, skin
        skel = hand.skeleton
        fore = hand.forearm_axis()
        along = (skin.rest - hand.wrist) @ fore
        digit_share = np.stack([skin.weights_of(d.joints) for d in hand.digits])
        palm_bones = {hand.bone} | {d.metacarpal for d in hand.digits if d.metacarpal is not None}
        palm_share = skin.weights_of(palm_bones)
        seeds = np.where(digit_share.max(axis=0) > 0.5)[0]
        if not len(seeds):
            raise HandError("Hand %r: no vertex is weighted mainly to its fingers; bind the mesh first"
                            % hand.name[:64])
        allowed = along >= 0.0
        self.vertices = _flood(len(skin.rest), skin.edges, seeds[allowed[seeds]], allowed)
        # For pictures: the hand and a stretch of forearm past the wrist, so a picture shows no ragged cut there.
        reach = -0.35 * float(np.linalg.norm(skel.heads[hand.fingers[0].joints[0]] - hand.wrist))
        self.display = _flood(len(skin.rest), skin.edges, self.vertices, along >= reach)
        region = np.zeros(len(skin.rest), dtype=bool)
        region[self.vertices] = True
        self.region = region
        shares = np.concatenate([digit_share, palm_share[None]], axis=0)
        owner = np.argmax(shares, axis=0)
        owner = np.where(owner == len(hand.digits), PALM, owner)
        self.owner = np.where(region, owner, -2)
        self.digit_share = digit_share
        # Weight from bones outside this arm on hand skin: the arm is the hand, its digits and the bones above it up
        # to the first that branches (a chest or hips, where the other limbs start).
        arm = set(hand.bones())
        for a in skel.ancestors(hand.bone):
            if len(skel.children[a]) > 1:
                break
            arm.add(a)
        foreign = np.isin(skin.bone, sorted(arm), invert=True) & region[skin.vertex] & (skin.weight > 1e-4)
        self.foreign_vertices = np.unique(skin.vertex[foreign])
        self.foreign_bones = sorted({skel.names[b] for b in skin.bone[foreign]})
        self._compact()
        self.finger_radius = self._finger_radius()

    def _compact(self):
        """Keep only the hand's own skin (the drawn region, the hand inside it): every array from here on indexes
        it, and ``mesh_index`` maps it back to the mesh. Each pose then skins and searches the hand's vertices
        only, however many unrelated vertices the mesh has (2026-10-27 audit: 600 -> 199,600 vertices took a
        fist from 0.72 s to 26.8 s)."""
        skin, keep = self.skin, self.display
        local = np.full(len(skin.rest), -1, dtype=np.int64)
        local[keep] = np.arange(len(keep))
        entries = local[skin.vertex] >= 0
        tri = skin.triangles[np.all(local[skin.triangles] >= 0, axis=1)]
        edges = skin.edges[np.all(local[skin.edges] >= 0, axis=1)]
        self.skin = Skin(skin.rest[keep], local[skin.vertex[entries]], skin.bone[entries], skin.weight[entries],
                         local[tri], local[edges])
        self.mesh_index = keep
        self.vertices, self.display = local[self.vertices], np.arange(len(keep))
        self.region, self.owner = self.region[keep], self.owner[keep]
        self.digit_share = self.digit_share[:, keep]
        self.foreign_vertices = local[self.foreign_vertices]

    def part_vertices(self, part):
        return np.where(self.owner == part)[0]

    def _finger_radius(self):
        """The median distance of the fingers' middle-segment skin from its bone axis: the unit of every distance
        these tools judge by."""
        skel, hand = self.hand.skeleton, self.hand
        radii = []
        for k, digit in enumerate(hand.digits):
            if digit.role == "thumb" or len(digit.joints) < 2:
                continue
            j = digit.joints[1]
            a, b = skel.heads[j], skel.tails[j]
            idx = np.where((self.owner == k) & (self.skin.weights_of([j]) > 0.5))[0]
            if not len(idx):
                continue
            radii.append(segment_distance(self.skin.rest[idx], a, b))
        if not radii:
            raise HandError("Hand %r: its fingers own no skin; check the weights" % hand.name[:64])
        r = float(np.median(np.concatenate(radii)))
        if not r > 0:
            raise HandError("Hand %r: its finger skin lies on the bones' axes" % hand.name[:64])
        return r


def _flood(count, edges, seeds, allowed):
    """Vertices reachable from ``seeds`` through ``edges`` among ``allowed`` ones, breadth first over a sorted
    adjacency: numpy over the edges once, then work in proportion to the vertices reached."""
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    e = edges[allowed[edges[:, 0]] & allowed[edges[:, 1]]]
    a = np.concatenate([e[:, 0], e[:, 1]])
    b = np.concatenate([e[:, 1], e[:, 0]])
    order = np.argsort(a, kind="stable")
    a, b = a[order], b[order]
    start = np.searchsorted(a, np.arange(count + 1))
    seen = np.zeros(count, dtype=bool)
    frontier = np.unique(np.asarray(seeds, dtype=np.int64))
    seen[frontier] = True
    while len(frontier):
        lo, n = start[frontier], start[frontier + 1] - start[frontier]
        total = int(n.sum())
        if not total:
            break
        reached = b[np.arange(total) - np.repeat(np.cumsum(n) - n, n) + np.repeat(lo, n)]
        frontier = np.unique(reached[~seen[reached]])
        seen[frontier] = True
    return np.where(seen)[0]


# --- the anatomy checklist ----------------------------------------------------------------------------------------------

def checklist(hand):
    """The checklist items the bones can show (doc/angelo/hand_rigging.rst): each {item, ok, measured, expected}."""
    skel = hand.skeleton
    axis = hand.axis()
    out = []

    def length(d):
        return float(np.linalg.norm(hand.tip(d) - skel.heads[d.joints[0]]))

    def reach(point):
        return float((point - hand.wrist) @ axis)

    roles = {d.role: d for d in hand.fingers}
    lengths = {d.role: length(d) for d in hand.fingers}
    longest = max(lengths, key=lengths.get)
    if "middle" in roles:
        out.append({"item": "#4 the middle finger is the longest", "ok": longest == "middle",
                    "measured": {r: round(v, 4) for r, v in lengths.items()}, "expected": "middle longest"})
    if "ring" in roles and "index" in roles:
        ratio = lengths["ring"] / lengths["index"]
        out.append({"item": "#4 ring and index about equal", "ok": 0.9 <= ratio <= 1.12,
                    "measured": round(ratio, 3), "expected": "ring / index 0.9-1.12"})
    if "ring" in roles and len(roles["ring"].joints) == 3:
        little_tip = reach(hand.tip(roles["little"]))
        ring_dip = reach(skel.heads[roles["ring"].joints[2]])
        span = float(np.linalg.norm(skel.heads[roles["ring"].joints[2]] - skel.heads[roles["ring"].joints[1]]))
        out.append({"item": "#4 the little finger's tip is at about the ring finger's end joint",
                    "ok": abs(little_tip - ring_dip) <= 0.5 * span,
                    "measured": round(little_tip - ring_dip, 4), "expected": "within half the ring's middle segment"})
    knuckles = {d.role: reach(skel.heads[d.joints[0]]) for d in hand.fingers}
    if "middle" in knuckles:
        order_ok = knuckles["middle"] >= max(knuckles.values()) - 1e-6 and knuckles["little"] <= min(knuckles.values()) + 1e-6
        out.append({"item": "#5 the knuckles arc: middle farthest from the wrist, little nearest", "ok": order_ok,
                    "measured": {r: round(v, 4) for r, v in knuckles.items()}, "expected": "middle max, little min"})
    for d in hand.fingers:
        if len(d.joints) != 3:
            continue
        pts = [skel.heads[j] for j in d.joints] + [hand.tip(d)]
        seg = [float(np.linalg.norm(pts[i + 1] - pts[i])) for i in range(3)]
        share = [s / sum(seg) for s in seg]
        ok = abs(share[0] - 0.5) <= 0.08 and abs(share[1] - 0.3) <= 0.08 and abs(share[2] - 0.2) <= 0.08
        out.append({"item": "#6 %s finger bones about 50/30/20" % d.role, "ok": ok,
                    "measured": [round(s, 3) for s in share], "expected": [0.5, 0.3, 0.2]})
    if "middle" in roles:
        palm = reach(skel.heads[roles["middle"].joints[0]])
        ratio = palm / lengths["middle"]
        out.append({"item": "#1 the palm is about as long as the middle finger", "ok": 0.75 <= ratio <= 1.3,
                    "measured": round(ratio, 3), "expected": "palm / middle finger 0.75-1.3"})
    t = hand.thumb.joints
    base = reach(skel.heads[t[0]]) / max(knuckles.values())
    out.append({"item": "#3 the thumb's base joint sits low on the palm, near the wrist", "ok": base <= 0.5,
                "measured": round(base, 3), "expected": "at most half way from the wrist to the knuckles"})
    if hand.palm_normal is not None:
        direction = unit(skel.heads[t[1]] - skel.heads[t[0]])
        forward = math.degrees(math.asin(max(-1.0, min(1.0, float(direction @ hand.plane_normal())))))
        out.append({"item": "#3 the rest thumb sits forward of the palm (its first bone out of the palm's plane)",
                    "ok": forward >= 10.0, "measured": round(forward, 1),
                    "expected": "at least 10 degrees toward the palm side (the rigs' thumbs: 26)"})
    return out


# --- weights ------------------------------------------------------------------------------------------------------------

HARD_JUMP = 0.4   # a bone's weight changing this much across one edge is a candidate cut
# A cut is a step, not a steep fade: the edge's jump is nearly all of the change over its two vertices and their
# neighbours. A fade done over two edges (the rigs' knuckle fade on rigs A and B, 0.5 per edge, owner-approved) is
# a ramp there whatever the edge lengths; the rigs' thumb cut (0.55 to 0 across one edge, flat on both sides) is a
# step, and it folded the skin when the thumb turned.
STEP_SHARE = 0.8
MAX_CANDIDATES = 400


def weight_audit(hand_skin):
    """Weight problems on the hand's skin: cuts (a digit or hand bone's weight stepping by HARD_JUMP or more across
    one edge, not fading), finger weight reaching behind the knuckle, and weight from bones outside the arm."""
    hand, skin = hand_skin.hand, hand_skin.skin
    skel = hand.skeleton
    region = hand_skin.region
    e = skin.edges[region[skin.edges[:, 0]] & region[skin.edges[:, 1]]]
    cuts = {}
    joints = sorted(hand.bones())
    heads = skel.heads[joints]
    r = hand_skin.finger_radius
    neighbours = {}
    for a, c in e:
        neighbours.setdefault(int(a), []).append(int(c))
        neighbours.setdefault(int(c), []).append(int(a))
    for b in joints:
        w = skin.weights_of([b])
        jump = np.abs(w[e[:, 0]] - w[e[:, 1]])
        hard = np.where(jump >= HARD_JUMP)[0]
        hard = hard[np.argsort(-jump[hard], kind="stable")][:MAX_CANDIDATES]
        steps = []
        for i in hard:
            ring = neighbours[int(e[i, 0])] + neighbours[int(e[i, 1])]
            around = w[ring]
            if jump[i] >= STEP_SHARE * float(around.max() - around.min()):
                steps.append(i)
        if steps:
            hard = np.array(steps)
            worst = int(hard[np.argmax(jump[hard])])
            middle = skin.rest[e[worst]].mean(axis=0)
            near = int(np.argmin(np.linalg.norm(heads - middle, axis=1)))
            cuts[skel.names[b]] = {"edges": int(len(hard)), "largest_jump": round(float(jump[worst]), 3),
                                   "worst_near_joint": skel.names[joints[near]][:64],
                                   "worst_from_joint_finger_radii": round(float(np.linalg.norm(heads[near] - middle)) / r, 2)}
    behind = {}
    for k, digit in enumerate(hand.digits):
        if digit.role == "thumb":
            continue
        base = skel.heads[digit.joints[0]]
        a = unit(skel.heads[digit.joints[1]] - base)
        share = hand_skin.digit_share[k]
        idx = np.where(region & (share > 0.05))[0]
        back = (base - skin.rest[idx]) @ a
        far = back > 2.0 * r
        if far.any():
            behind[skel.names[digit.joints[0]]] = {"vertices": int(far.sum()),
                                                   "farthest_finger_radii": round(float(back.max() / r), 2)}
    return {"cuts": cuts, "finger_weight_behind_knuckle": behind,
            "foreign_bones": hand_skin.foreign_bones[:16], "foreign_vertices": int(len(hand_skin.foreign_vertices))}
