# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Skin-to-skin contact between the parts of one hand (digits and palm) in a pose: how far a part's skin is from
resting on another's (gap) and how deep it sinks in (depth). numpy, with mathutils' BVHTree inside Blender and an
exact brute-force search without it (the host tests).

Contact is judged on the skin itself, never on capsules around the bones: capsules only approximate a chibi hand's
round fingers, and the rigs' fist checked by capsules still sank skin into skin (2026-10-22). A vertex is
inside another part when it is nearer that part's bone axis than that part's skin is at the nearest point: each
part's skin is an open surface (it ends at the webs), so a face normal alone misreads vertices beyond its edge.
A vertex is inside the palm when it is behind the palm skin's nearest face. Vertices already that close to the other
part at rest (the webs, where fingers join) are left out, so only contact the pose makes counts. Every distance is a fraction of the hand's finger radius (``hand_rig.HandSkin``).
"""

import numpy as np

from .hand_math import segment_distance
from .hand_skin import PALM

REST_CLEARANCE = 0.45   # finger radii: nearer than this at rest is a web, not contact
WRONG_SIDE = 0.7        # finger radii: deeper "inside" readings are behind an open skin edge, not real overlap
REACH = 4.0             # finger radii searched for the nearest skin
CONTACT_GAP = 0.035     # finger radii: skin resting on skin
SKIN_DEPTH = 0.07       # finger radii: skin pressing into skin as flesh does, not visibly through it
PALM_DEPTH = 0.11       # finger radii: a curled finger this deep in the palm's skin has met it
SURFACE_POSES = 3       # poses whose part skins are kept (the rest pose, the current one, one candidate)


def _closest_on_triangles(p, a, b, c):
    """Closest points on triangles (a, b, c: (m, 3)) to one point p (Ericson, Real-Time Collision Detection 5.1.5)."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = (ab * ap).sum(1), (ac * ap).sum(1)
    bp = p - b
    d3, d4 = (ab * bp).sum(1), (ac * bp).sum(1)
    cp = p - c
    d5, d6 = (ab * cp).sum(1), (ac * cp).sum(1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-30, 1e-30, denom)
    v, w = vb / denom, vc / denom
    out = a + ab * v[:, None] + ac * w[:, None]
    region = [
        ((d1 <= 0) & (d2 <= 0), a),
        ((d3 >= 0) & (d4 <= d3), b),
        ((d6 >= 0) & (d5 <= d6), c)]
    edge_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = d1 / np.where(np.abs(d1 - d3) < 1e-30, 1e-30, d1 - d3)
    out = np.where(edge_ab[:, None], a + ab * t[:, None], out)
    edge_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = d2 / np.where(np.abs(d2 - d6) < 1e-30, 1e-30, d2 - d6)
    out = np.where(edge_ac[:, None], a + ac * t[:, None], out)
    edge_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    t = (d4 - d3) / np.where(np.abs((d4 - d3) + (d5 - d6)) < 1e-30, 1e-30, (d4 - d3) + (d5 - d6))
    out = np.where(edge_bc[:, None], b + (c - b) * t[:, None], out)
    for mask, corner in reversed(region):
        out = np.where(mask[:, None], corner, out)
    return out


class Surface:
    """The nearest point of a triangle set to query points, with that triangle's normal and index: BVHTree in
    Blender, an exact brute-force search without it. Normals follow the triangles' winding (outward on a mesh whose
    faces point out, as Blender's own normals do). Built from the vertices its triangles use only, so its cost is
    the part's skin, never the mesh's; triangle indices stay those of ``triangles``."""

    def __init__(self, positions, triangles):
        triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
        used, local = np.unique(triangles, return_inverse=True)
        self.positions = np.asarray(positions, dtype=np.float64)[used]
        self.triangles = local.reshape(-1, 3)
        self.tree = None
        try:
            from mathutils.bvhtree import BVHTree
        except ImportError:
            BVHTree = None
        if BVHTree is not None and len(self.triangles):
            self.tree = BVHTree.FromPolygons(self.positions.tolist(), self.triangles.tolist())

    def nearest(self, points, reach):
        """(points, normals, triangle indices) of the nearest skin to each query point; NaN points and index -1
        where none is within ``reach``."""
        hits = np.full((len(points), 3), np.nan)
        normals = np.zeros((len(points), 3))
        index = np.full(len(points), -1, dtype=np.int64)
        if not len(self.triangles):
            return hits, normals, index
        if self.tree is not None:
            for i, p in enumerate(points):
                found = self.tree.find_nearest(tuple(p), reach)
                if found[0] is not None:
                    hits[i], normals[i], index[i] = found[0][:], found[1][:], found[2]
            return hits, normals, index
        tri = self.positions[self.triangles]
        centre = tri.mean(axis=1)
        size = np.linalg.norm(tri - centre[:, None], axis=2).max(axis=1)
        face = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        face /= np.maximum(np.linalg.norm(face, axis=1, keepdims=True), 1e-30)
        for i, p in enumerate(points):
            near = np.where(np.linalg.norm(centre - p, axis=1) - size <= reach)[0]
            if not len(near):
                continue
            closest = _closest_on_triangles(p, tri[near, 0], tri[near, 1], tri[near, 2])
            d = np.linalg.norm(closest - p, axis=1)
            k = int(np.argmin(d))
            if d[k] <= reach:
                hits[i], normals[i], index[i] = closest[k], face[near[k]], near[k]
        return hits, normals, index


class Contact:
    """Contact measurement for one hand (``hand_rig.HandSkin``): parts are digit indices or ``PALM``."""

    def __init__(self, hand_skin):
        self.hs = hand_skin
        hand, skin = hand_skin.hand, hand_skin.skin
        self.r = hand_skin.finger_radius
        owner = hand_skin.owner
        self.parts = list(range(len(hand.digits))) + [PALM]
        tri = skin.triangles
        self.part_triangles = {p: tri[np.all(owner[tri] == p, axis=1)] for p in self.parts}
        self.part_vertices = {p: np.where(owner == p)[0] for p in self.parts}
        self.vertices = hand_skin.vertices
        rest = skin.rest
        self._eligible = {}
        self._rest_inside = {}   # Eligible skin already inside the other part at rest (a fold in the mesh).
        self._rest = rest
        # Fingertip skin (a finger's last bone) is tucked into the palm in a fist, where the tips crowd together out of
        # sight: finger-to-finger contact is judged on the visible segments only.
        skel = hand.skeleton
        last = [d.joints[-1] for d in hand.digits if d.role != "thumb"]
        self.tucked = skin.weights_of(last) > 0.5
        self.segment_vertices = {}
        for k, digit in enumerate(hand.digits):
            later = digit.joints[1:]
            share = skin.weights_of(later) if later else np.zeros(len(rest))
            self.segment_vertices[k] = np.where((owner == k) & (share > 0.5))[0]
        self.skel = skel
        self._surfaces = []   # [(positions, {part: Surface})], newest last: each skin built once per pose.

    def _surface(self, positions, part):
        """The part's skin at ``positions`` (a pose's positions, never changed once measured), built once."""
        for held, built in self._surfaces:
            if held is positions:
                break
        else:
            built = {}
            self._surfaces = (self._surfaces + [(positions, built)])[-SURFACE_POSES:]
        if part not in built:
            built[part] = Surface(positions, self.part_triangles[part])
        return built[part]

    def axes(self, part, posed):
        """The bone segments (posed heads and tails, armature space) of a part: its joints, or the palm's bones."""
        hand, skel = self.hs.hand, self.skel
        if part == PALM:
            bones = [hand.bone] + [d.metacarpal for d in hand.digits if d.metacarpal is not None]
        else:
            bones = hand.digits[part].joints
        out = []
        for b in bones:
            head = posed[b][:3, 3]
            tail = posed[b] @ np.linalg.inv(skel.rest[b]) @ np.append(skel.tails[b], 1.0)
            out.append((head, tail[:3]))
        return out

    def _axis_distance(self, points, axes):
        return np.min([segment_distance(points, a, b) for a, b in axes], axis=0)

    def surface(self, positions, part):
        """The part's skin at ``positions``, for repeated queries against skin that does not move."""
        return Surface(positions, self.part_triangles[part])

    def signed(self, positions, posed, part, other, vertices, surface=None):
        """Distance of ``vertices`` (of part) from ``other``'s skin, negative inside it; +inf where none is near.
        ``surface``: other's skin built once (``surface``), when other has not moved since."""
        surface = surface or self._surface(positions, other)
        points = positions[vertices]
        hits, normals, _ = surface.nearest(points, REACH * self.r)
        out = np.full(len(vertices), np.inf)
        found = ~np.isnan(hits[:, 0])
        if not found.any():
            return out
        d = np.linalg.norm(points[found] - hits[found], axis=1)
        behind = ((points[found] - hits[found]) * normals[found]).sum(axis=1) < 0
        if other == PALM:
            inside = behind   # The palm is broad, not a tube round a bone: its faces' side alone (the rigs' palm).
        else:
            axes = self.axes(other, posed)
            inside = behind & (self._axis_distance(points[found], axes) < self._axis_distance(hits[found], axes))
        out[found] = np.where(inside, -d, d)
        return out

    def eligible(self, rest_positions, rest_posed, part, other):
        key = (part, other)
        if key not in self._eligible:
            idx = self.part_vertices[part]
            d = self.signed(rest_positions, rest_posed, part, other, idx)
            self._eligible[key] = idx[np.abs(d) > REST_CLEARANCE * self.r]
            self._rest_inside[key] = idx[d < -REST_CLEARANCE * self.r]
        return self._eligible[key]

    def rest_inside(self, rest_positions, rest_posed, part, other):
        """The part's skin already inside ``other`` at rest: a fold in the mesh, not contact a pose made."""
        self.eligible(rest_positions, rest_posed, part, other)
        return self._rest_inside[(part, other)]

    def measure(self, positions, posed, pairs, rest_positions, rest_posed, surfaces=None):
        """{(a, b): (gap, depth)} in finger radii: gap = how far the nearest 5 % of a's eligible skin is from b's
        (0 = resting on it), depth = how deep the deepest 2 % sinks into b (at least three vertices agree).
        ``surfaces``: {part: Surface} of parts that have not moved since they were built."""
        out = {}
        surfaces = surfaces or {}
        for a, b in pairs:
            idx = self.eligible(rest_positions, rest_posed, a, b)
            if a != PALM and b != PALM and self.hs.hand.digits[a].role != "thumb" \
                    and self.hs.hand.digits[b].role != "thumb":
                idx = idx[~self.tucked[idx]]
            d = self.signed(positions, posed, a, b, idx, surfaces.get(b)) / self.r
            d = d[np.isfinite(d) & (d > -WRONG_SIDE)]
            if not len(d):
                out[(a, b)] = (np.inf, 0.0)
                continue
            deep = max(float(np.percentile(d, 2)), float(np.sort(d)[min(2, len(d) - 1)]))
            out[(a, b)] = (float(max(0.0, np.percentile(d, 5))), max(0.0, -deep))
        return out

    def palm_depth(self, positions, posed, part):
        """How deep a digit's middle and end segments are in the palm's skin, finger radii (third-deepest vertex).
        The first segment is left out: with the knuckle bent its palm side folds into the palm crease."""
        idx = self.segment_vertices[part]
        if not len(idx):
            return 0.0
        d = self.signed(positions, posed, part, PALM, idx) / self.r
        inside = -d[np.isfinite(d) & (d < 0) & (d > -WRONG_SIDE)]
        return float(np.sort(inside)[-3]) if len(inside) >= 3 else 0.0
