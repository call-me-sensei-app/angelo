# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Surface-to-surface distance between triangles, and a bounding-volume tree that finds which pairs are worth
measuring. NumPy only, never ``bpy``: it runs off the main thread (``character_geometry`` uses it).

* ``triangle_pairs(a, b)``: the exact distance between triangle ``a[i]`` and ``b[i]``. Zero when an edge of either
  crosses the other; otherwise the smallest of the six corner-to-triangle and nine edge-to-edge distances, which is
  where the closest points of two separate triangles always lie. A zero-area triangle is measured by its edges.
* ``Tree(triangles, labels)``: triangles sorted along a Morton curve, leaves of ``LEAF``, a complete binary tree of
  boxes over them, and each node's lowest and highest label (a label is a shell, a part, an object).
* ``touching(tree, count, radius)``: which labels come within ``radius`` of another label's surface.
* ``gap(part, tree, label)``: the nearest distance from ``part``'s triangles to ``tree``'s triangles of any other
  label; ``inf`` when there are none.

Both walk node pairs level by level, keeping only pairs whose boxes are closer than the bound, so the work follows
the surfaces that are actually near each other, not the number of triangles squared. ``gap``'s bound is the nearest
distance found so far and, before any is found, the smallest farthest-point distance between two boxes still in play
(each holds a triangle, so their triangles are no farther apart than that), which tightens at every level. ``work`` (optional dict) counts
``node_pairs`` visited and ``triangle_pairs`` measured.
"""

import numpy

LEAF = 8
CHUNK = 50_000      # Triangle pairs measured at once.
_TINY = 1e-300


def _dot(u, v):
    return numpy.einsum("ij,ij->i", u, v)


def _point_segment(p, a, b):
    ab = b - a
    t = numpy.clip(_dot(p - a, ab) / numpy.maximum(_dot(ab, ab), _TINY), 0.0, 1.0)
    return numpy.linalg.norm(p - (a + ab * t[:, None]), axis=1)


def _normal(a, b, c):
    """(normal, usable): ``usable`` is False for a zero-area (or nearly) triangle, which is measured by its edges."""
    n = numpy.cross(b - a, c - a)
    longest = numpy.maximum(numpy.maximum(_dot(b - a, b - a), _dot(c - b, c - b)), _dot(a - c, a - c))
    return n, _dot(n, n) > 1e-20 * longest * longest


def _inside(q, a, b, c, n):
    """Whether ``q`` (on the plane) is inside the triangle, edges included."""
    return ((_dot(numpy.cross(b - a, q - a), n) >= 0) & (_dot(numpy.cross(c - b, q - b), n) >= 0)
            & (_dot(numpy.cross(a - c, q - c), n) >= 0))


def _point_triangle(p, a, b, c):
    n, usable = _normal(a, b, c)
    nn = numpy.maximum(_dot(n, n), _TINY)
    height = _dot(p - a, n) / nn
    face = numpy.abs(height) * numpy.sqrt(nn)
    inside = usable & _inside(p - n * height[:, None], a, b, c, n)
    edges = numpy.minimum(numpy.minimum(_point_segment(p, a, b), _point_segment(p, b, c)), _point_segment(p, c, a))
    return numpy.where(inside, face, edges)


def _segment_segment(p1, q1, p2, q2):
    """Closest distance between segments (Ericson, Real-Time Collision Detection 5.1.9), zero lengths included."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    c, b = _dot(d1, r), _dot(d1, d2)
    eps = 1e-30
    with numpy.errstate(divide="ignore", invalid="ignore"):
        denom = a * e - b * b
        s = numpy.where(denom > 1e-12 * a * e, numpy.clip((b * f - c * e) / denom, 0.0, 1.0), 0.0)
        t = (b * s + f) / e
        low, high = t < 0, t > 1
        s = numpy.where(low, numpy.clip(-c / a, 0.0, 1.0), numpy.where(high, numpy.clip((b - c) / a, 0.0, 1.0), s))
        t = numpy.clip(t, 0.0, 1.0)
        # One or both segments are points.
        s = numpy.where(a <= eps, 0.0, numpy.where(e <= eps, numpy.clip(-c / a, 0.0, 1.0), s))
        t = numpy.where(e <= eps, 0.0, numpy.where(a <= eps, numpy.clip(f / e, 0.0, 1.0), t))
    s, t = numpy.nan_to_num(s), numpy.nan_to_num(t)
    return numpy.linalg.norm((p1 + d1 * s[:, None]) - (p2 + d2 * t[:, None]), axis=1)


def _crosses(p, q, a, b, c):
    """Whether segment ``p``-``q`` passes through the triangle (not lying in its plane)."""
    n, usable = _normal(a, b, c)
    dp, dq = _dot(p - a, n), _dot(q - a, n)
    straddles = usable & (dp * dq <= 0) & (dp != dq)
    with numpy.errstate(divide="ignore", invalid="ignore"):
        t = numpy.nan_to_num(dp / (dp - dq))
    return straddles & _inside(p + (q - p) * t[:, None], a, b, c, n)


def triangle_pairs(a, b):
    """Distance between each ``a[i]`` and ``b[i]`` (both (N, 3, 3), world units)."""
    a, b = numpy.asarray(a, dtype=numpy.float64), numpy.asarray(b, dtype=numpy.float64)
    if not len(a):
        return numpy.zeros(0)
    best = numpy.full(len(a), numpy.inf)
    crossed = numpy.zeros(len(a), dtype=bool)
    for one, other in ((a, b), (b, a)):
        oa, ob, oc = other[:, 0], other[:, 1], other[:, 2]
        for i in range(3):
            best = numpy.minimum(best, _point_triangle(one[:, i], oa, ob, oc))
            crossed |= _crosses(one[:, i], one[:, (i + 1) % 3], oa, ob, oc)
    for i in range(3):
        for j in range(3):
            best = numpy.minimum(best, _segment_segment(a[:, i], a[:, (i + 1) % 3], b[:, j], b[:, (j + 1) % 3]))
    return numpy.where(crossed, 0.0, best)


class Tree:
    """A bounding-volume tree over labelled triangles (see the module text)."""

    def __init__(self, triangles, labels):
        triangles = numpy.asarray(triangles, dtype=numpy.float64).reshape(-1, 3, 3)
        labels = numpy.asarray(labels, dtype=numpy.int64).reshape(-1)
        count = len(triangles)
        if count:
            centres = triangles.mean(axis=1)
            low, high = centres.min(axis=0), centres.max(axis=0)
            cells = ((centres - low) / numpy.maximum(high - low, _TINY) * 1023).astype(numpy.int64)
            code = numpy.zeros(count, dtype=numpy.int64)
            for bit in range(10):
                for axis in range(3):
                    code |= ((cells[:, axis] >> bit) & 1) << (3 * bit + axis)
            order = numpy.argsort(code, kind="stable")
        else:
            order = numpy.zeros(0, dtype=numpy.int64)
        self.triangles, self.labels = triangles[order], labels[order]
        self.tri_low, self.tri_high = self.triangles.min(axis=1), self.triangles.max(axis=1)
        leaves = max(1, -(-count // LEAF))
        width = 1 << (leaves - 1).bit_length()
        self.first_leaf = width - 1
        slots = numpy.full(width * LEAF, -1, dtype=numpy.int64)
        slots[:count] = numpy.arange(count)
        self.leaf_triangles = slots.reshape(width, LEAF)
        nodes = 2 * width - 1
        self.low = numpy.full((nodes, 3), numpy.inf)
        self.high = numpy.full((nodes, 3), -numpy.inf)
        self.label_low = numpy.full(nodes, numpy.iinfo(numpy.int64).max)
        self.label_high = numpy.full(nodes, -1)
        used = self.leaf_triangles >= 0
        safe = numpy.where(used, self.leaf_triangles, 0)
        if count:
            leaf_nodes = slice(self.first_leaf, nodes)
            self.low[leaf_nodes] = numpy.where(used[..., None], self.tri_low[safe], numpy.inf).min(axis=1)
            self.high[leaf_nodes] = numpy.where(used[..., None], self.tri_high[safe], -numpy.inf).max(axis=1)
            self.label_low[leaf_nodes] = numpy.where(used, self.labels[safe], self.label_low[0]).min(axis=1)
            self.label_high[leaf_nodes] = numpy.where(used, self.labels[safe], -1).max(axis=1)
        level = width // 2
        while level:
            parents = numpy.arange(level - 1, 2 * level - 1)
            left, right = 2 * parents + 1, 2 * parents + 2
            self.low[parents] = numpy.minimum(self.low[left], self.low[right])
            self.high[parents] = numpy.maximum(self.high[left], self.high[right])
            self.label_low[parents] = numpy.minimum(self.label_low[left], self.label_low[right])
            self.label_high[parents] = numpy.maximum(self.label_high[left], self.label_high[right])
            level //= 2
        self.size = numpy.where(numpy.isfinite(self.low).all(axis=1),
                                (self.high - self.low).sum(axis=1), -1.0)


def _box_gap(low_a, high_a, low_b, high_b):
    with numpy.errstate(invalid="ignore"):
        apart = numpy.maximum(0.0, numpy.maximum(low_a - high_b, low_b - high_a))
    return numpy.sqrt((numpy.nan_to_num(apart, nan=numpy.inf) ** 2).sum(axis=1))


def _count(work, key, amount):
    if work is not None:
        work[key] = work.get(key, 0) + int(amount)


def _start(work):
    """Both counts are reported, zero when the tree pruned every pair before a leaf."""
    for key in ("node_pairs", "triangle_pairs"):
        _count(work, key, 0)


def _walk(tree_a, tree_b, same, bound, keep, leaves, work, seen=None):
    """Visit node pairs closer than ``bound()``; ``keep(a, b)`` may drop more; ``seen(a, b)`` is told the pairs kept
    at each level before any is measured; ``leaves(a, b)`` measures leaf pairs. ``same``: both trees are one, so
    each unordered pair is visited once."""
    pairs = numpy.zeros((1, 2), dtype=numpy.int64)
    while len(pairs):
        _count(work, "node_pairs", len(pairs))
        a, b = pairs[:, 0], pairs[:, 1]
        near = _box_gap(tree_a.low[a], tree_a.high[a], tree_b.low[b], tree_b.high[b]) <= bound()
        near &= keep(a, b)
        a, b = a[near], b[near]
        if seen is not None and len(a):
            seen(a, b)
            near = _box_gap(tree_a.low[a], tree_a.high[a], tree_b.low[b], tree_b.high[b]) <= bound()
            a, b = a[near], b[near]
        leaf_a, leaf_b = a >= tree_a.first_leaf, b >= tree_b.first_leaf
        both = leaf_a & leaf_b
        if both.any():
            leaves(a[both] - tree_a.first_leaf, b[both] - tree_b.first_leaf)
        a, b, leaf_a, leaf_b = a[~both], b[~both], leaf_a[~both], leaf_b[~both]
        if same:
            diagonal = a == b
            da = a[diagonal]
            split_a = ~diagonal & ~leaf_a & (leaf_b | (tree_a.size[a] >= tree_b.size[b]))
            split_b = ~diagonal & ~split_a
            pairs = numpy.concatenate([
                numpy.stack([2 * da + 1, 2 * da + 1], 1), numpy.stack([2 * da + 1, 2 * da + 2], 1),
                numpy.stack([2 * da + 2, 2 * da + 2], 1),
                *_split(a, b, split_a, split_b)])
        else:
            split_a = ~leaf_a & (leaf_b | (tree_a.size[a] >= tree_b.size[b]))
            pairs = numpy.concatenate(_split(a, b, split_a, ~split_a))
    return None


def _split(a, b, split_a, split_b):
    sa, sb = a[split_a], b[split_a]
    ta, tb = a[split_b], b[split_b]
    return [numpy.stack([2 * sa + 1, sb], 1), numpy.stack([2 * sa + 2, sb], 1),
            numpy.stack([ta, 2 * tb + 1], 1), numpy.stack([ta, 2 * tb + 2], 1)]


def _leaf_pairs(tree_a, tree_b, leaf_a, leaf_b, same):
    """Triangle index pairs of the given leaf pairs, padding dropped; within one leaf of one tree, each pair once."""
    ta = numpy.repeat(tree_a.leaf_triangles[leaf_a], LEAF, axis=1)
    tb = numpy.tile(tree_b.leaf_triangles[leaf_b], (1, LEAF))
    valid = (ta >= 0) & (tb >= 0)
    if same:
        valid &= (leaf_a != leaf_b)[:, None] | (ta < tb)
    return ta[valid], tb[valid]


def touching(tree, count, radius, work=None):
    """Bool per label 0..``count``-1: its surface comes within ``radius`` of another label's."""
    _start(work)
    found = numpy.zeros(max(count, 1), dtype=bool)[:count]

    def keep(a, b):
        one_label = (tree.label_low[a] == tree.label_high[a]) & (tree.label_low[b] == tree.label_high[b]) \
            & (tree.label_low[a] == tree.label_low[b])
        return ~one_label

    def leaves(leaf_a, leaf_b):
        ta, tb = _leaf_pairs(tree, tree, leaf_a, leaf_b, same=True)
        la, lb = tree.labels[ta], tree.labels[tb]
        wanted = (la != lb) & ~(found[la] & found[lb])
        ta, tb = ta[wanted], tb[wanted]
        near = _box_gap(tree.tri_low[ta], tree.tri_high[ta], tree.tri_low[tb], tree.tri_high[tb]) <= radius
        ta, tb = ta[near], tb[near]
        for start in range(0, len(ta), CHUNK):
            ca, cb = ta[start:start + CHUNK], tb[start:start + CHUNK]
            _count(work, "triangle_pairs", len(ca))
            close = triangle_pairs(tree.triangles[ca], tree.triangles[cb]) <= radius
            found[tree.labels[ca[close]]] = True
            found[tree.labels[cb[close]]] = True

    if count > 1 and len(tree.triangles):
        _walk(tree, tree, True, lambda: radius, keep, leaves, work)
    return found


def _box_far(low_a, high_a, low_b, high_b):
    """The farthest any point of one box is from any point of the other."""
    return numpy.sqrt((numpy.maximum(numpy.abs(high_a - low_b), numpy.abs(high_b - low_a)) ** 2).sum(axis=1))


def gap(part, tree, label, work=None):
    """Nearest distance from ``part``'s triangles to ``tree``'s triangles not labelled ``label``; ``inf`` if none."""
    _start(work)
    if not len(part.triangles) or not (tree.labels != label).any():
        return numpy.inf
    best = [numpy.inf]   # Nearest measured, or an upper bound from two boxes that each hold a triangle.

    def keep(a, b):
        return ~((tree.label_low[b] == label) & (tree.label_high[b] == label))

    def seen(a, b):
        best[0] = min(best[0], float(_box_far(part.low[a], part.high[a], tree.low[b], tree.high[b]).min()))

    def leaves(leaf_a, leaf_b):
        ta, tb = _leaf_pairs(part, tree, leaf_a, leaf_b, same=False)
        wanted = tree.labels[tb] != label
        ta, tb = ta[wanted], tb[wanted]
        apart = _box_gap(part.tri_low[ta], part.tri_high[ta], tree.tri_low[tb], tree.tri_high[tb])
        order = numpy.argsort(apart, kind="stable")   # Nearest boxes first, so the bound tightens early.
        ta, tb, apart = ta[order], tb[order], apart[order]
        step = 256
        at = 0
        while at < len(ta) and apart[at] <= best[0]:
            stop = min(len(ta), at + step)
            ca, cb = ta[at:stop], tb[at:stop]
            ca, cb = ca[apart[at:stop] <= best[0]], cb[apart[at:stop] <= best[0]]
            _count(work, "triangle_pairs", len(ca))
            if len(ca):
                best[0] = min(best[0], float(triangle_pairs(part.triangles[ca], tree.triangles[cb]).min()))
            at, step = stop, min(step * 2, CHUNK)

    _walk(part, tree, False, lambda: best[0], keep, leaves, work, seen=seen)
    return best[0]
