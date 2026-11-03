# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check's measurements, on triangle arrays in world space (meters). No Blender dependency.

The heavy parts run in ``kernels``: ``surface(positions, tris)`` and ``nearest(surface, points)`` (distances to a
triangle surface) and ``rasterize(positions, tris, views, resolution)`` (triangle coverage of pixel centres). The
application passes ``_bpy._angelo_reduce``, which runs them natively on all cores; host tests pass a brute-force
NumPy reference. ``glossary`` says once per result what each fact measured, in one sentence each, because the agent
reads these numbers without seeing how they were made (doc/angelo/game_ready_check.rst, "What each fact measures").
"""

import math

import numpy as np

SAMPLES = 32768            # Area-weighted points per surface, beside every vertex.
SILHOUETTE_RESOLUTION = 256
UV_RESOLUTION = 1024
SEED = 0x47524331         # Fixed: the same mesh gives the same facts.
COLLAPSED_UV = 1e-12       # UV area (squared UV units) below which a triangle's UVs are collapsed.


def _round(value, digits=4):
    """Floats to ``digits`` significant digits for the result (the agent reads them; noise costs bytes)."""
    if value is None or isinstance(value, (bool, int)):
        return value
    value = float(value)
    if not math.isfinite(value) or value == 0.0:
        return value if math.isfinite(value) else None
    return round(value, digits - 1 - int(math.floor(math.log10(abs(value)))))


class Geometry:
    """A triangle mesh in world space: positions (V, 3), tris (T, 3) vertex indices, optional per-corner UVs
    (T, 3, 2) and normals (T, 3, 3), per-vertex skin weights (V, G), and ``vertex_ids``: the vertex each
    position came from in its Blender mesh, so a seam split for a vertex buffer still counts as one vertex."""

    def __init__(self, positions, tris, corner_uv=None, corner_normal=None, weights=None, vertex_ids=None,
                 weight_names=None):
        self.positions = np.ascontiguousarray(positions, dtype=np.float32).reshape(-1, 3)
        self.tris = np.ascontiguousarray(tris, dtype=np.uint32).reshape(-1, 3)
        self.corner_uv = None if corner_uv is None else np.asarray(corner_uv, dtype=np.float32).reshape(-1, 3, 2)
        self.corner_normal = (None if corner_normal is None
                              else np.asarray(corner_normal, dtype=np.float32).reshape(-1, 3, 3))
        self.weights = None if weights is None else np.asarray(weights, dtype=np.float32).reshape(len(self.positions), -1)
        self.vertex_ids = (np.arange(len(self.positions), dtype=np.int64) if vertex_ids is None
                           else np.asarray(vertex_ids, dtype=np.int64))
        # The vertex group each weights column belongs to: two meshes' weights compare by name, never by column.
        self.weight_names = list(weight_names) if weight_names is not None else None
        self._surface = None
        self._areas = None
        self._bounds = None
        self._edges = None

    @property
    def triangle_count(self):
        return len(self.tris)

    def corners(self):
        """(T, 3, 3) corner positions."""
        return self.positions[self.tris]

    def triangle_areas(self):
        if self._areas is None:
            c = self.corners().astype(np.float64)
            self._areas = 0.5 * np.linalg.norm(np.cross(c[:, 1] - c[:, 0], c[:, 2] - c[:, 0]), axis=1)
        return self._areas

    def surface(self, kernels):
        if self._surface is None:
            self._surface = kernels.surface(self.positions, self.tris)
        return self._surface

    def bounds(self):
        """The bounding box of the vertices the triangles use (computed once)."""
        if self._bounds is None:
            used = self.positions[np.unique(self.tris)] if len(self.tris) else self.positions[:0]
            self._bounds = ((np.zeros(3), np.zeros(3)) if not len(used) else
                            (used.min(axis=0).astype(np.float64), used.max(axis=0).astype(np.float64)))
        return self._bounds


def _length(value):
    """A distance in meters for the result: float noise below a nanometer reads as 0."""
    return 0.0 if abs(value) < 1e-9 else _round(value)


def _nearest(kernels, surface, points):
    distances, tris, closest = kernels.nearest(surface, np.ascontiguousarray(points, dtype=np.float32))
    return (np.frombuffer(distances, dtype=np.float32), np.frombuffer(tris, dtype=np.int32),
            np.frombuffer(closest, dtype=np.float32).reshape(-1, 3))


class Samples:
    """Points on a surface: its used vertices, then ``count`` area-weighted points, each with its triangle and
    barycentric weights (a vertex as the corner of one triangle that uses it)."""

    def __init__(self, geometry, count=SAMPLES, seed=SEED):
        rng = np.random.default_rng(seed)
        areas = geometry.triangle_areas()
        tris = geometry.tris
        corner_tri = np.repeat(np.arange(len(tris)), 3)
        corner_k = np.tile(np.arange(3), len(tris))
        _, first = np.unique(tris.reshape(-1), return_index=True)   # One corner per used vertex.
        vert_tri, vert_k = corner_tri[first], corner_k[first]
        vert_bary = np.zeros((len(first), 3))
        vert_bary[np.arange(len(first)), vert_k] = 1.0
        total = float(areas.sum())
        if total > 0.0 and count > 0:
            cumulative = np.cumsum(areas)
            area_tri = np.minimum(np.searchsorted(cumulative, rng.random(count) * total, side="right"), len(tris) - 1)
            r1, r2 = np.sqrt(rng.random(count)), rng.random(count)
            area_bary = np.stack([1.0 - r1, r1 * (1.0 - r2), r1 * r2], axis=1)
        else:
            area_tri, area_bary = np.zeros(0, dtype=np.int64), np.zeros((0, 3))
        self.tri = np.concatenate([vert_tri, area_tri]).astype(np.int64)
        self.bary = np.concatenate([vert_bary, area_bary])
        corners = geometry.corners().astype(np.float64)[self.tri]
        self.points = np.einsum("nk,nkd->nd", self.bary, corners).astype(np.float32)
        self.vertex_count, self.area_count = len(first), len(area_tri)


def _barycentric(corners, points):
    """Barycentric weights of ``points`` (N, 3) in triangles ``corners`` (N, 3, 3), clamped to the triangle."""
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    v0, v1, v2 = b - a, c - a, points - a
    d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
    d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
    denom = d00 * d11 - d01 * d01
    safe = np.where(np.abs(denom) > 0.0, denom, 1.0)
    v = np.where(np.abs(denom) > 0.0, (d11 * d20 - d01 * d21) / safe, 1.0 / 3.0)
    w = np.where(np.abs(denom) > 0.0, (d00 * d21 - d01 * d20) / safe, 1.0 / 3.0)
    bary = np.clip(np.stack([1.0 - v - w, v, w], axis=1), 0.0, 1.0)
    return bary / np.maximum(bary.sum(1, keepdims=True), 1e-12)


def _percentiles(values, weights=None):
    if not len(values):
        return {"p50": None, "p95": None, "max": None}
    if weights is None:
        p50, p95 = np.percentile(values, [50, 95])
    else:
        order = np.argsort(values, kind="stable")
        cumulative = np.cumsum(weights[order])
        cumulative /= cumulative[-1]
        p50, p95 = (values[order][min(np.searchsorted(cumulative, q), len(order) - 1)] for q in (0.5, 0.95))
    return {"p50": _round(p50), "p95": _round(p95), "max": _round(values.max())}


def _interpolate(values, tris, tri, bary, per_corner):
    """``values`` per triangle corner (T, 3, D), or per vertex (V, D) through ``tris``, at points in triangles ``tri``
    with barycentric weights ``bary``."""
    rows = values[tri] if per_corner else values[tris[tri]]
    return np.einsum("nk,nkd->nd", bary, rows.astype(np.float64))


class Comparison:
    """A reduction measured against its source, both ways: Hausdorff, and the attributes at each mesh's sample points
    against the other's at the nearest points (which the attribute facts read). Sampling one mesh alone would miss
    whatever only the other has, such as a stray shell or a part one of them lacks."""

    def __init__(self, source, source_samples, lod, kernels, count=SAMPLES):
        self.source, self.lod = source, lod
        lod_samples = Samples(lod, count)
        self.count = count
        to_source, self.source_tri, self.source_point = _nearest(kernels, source.surface(kernels), lod_samples.points)
        to_lod, self.lod_tri, self.lod_point = _nearest(kernels, lod.surface(kernels), source_samples.points)
        self.samples, self.lod_samples = source_samples, lod_samples
        self._nearest_bary = None
        self.forward, self.backward = to_source.astype(np.float64), to_lod.astype(np.float64)
        # The mean reads the area-weighted points only, so a dense mesh's many vertices don't weigh more.
        self.area_distances = np.concatenate([self.forward[lod_samples.vertex_count:],
                                              self.backward[source_samples.vertex_count:]])
        self.sample_counts = (lod_samples.vertex_count + lod_samples.area_count,
                              source_samples.vertex_count + source_samples.area_count)

    def hausdorff(self, diagonal):
        worst = float(max(self.forward.max(initial=0.0), self.backward.max(initial=0.0)))
        mean = float(self.area_distances.mean()) if len(self.area_distances) else 0.0
        worst = 0.0 if abs(worst) < 1e-9 else worst
        return {"max_m": _length(worst), "mean_m": _length(mean),
                "max_of_bounds": _round(worst / diagonal) if diagonal > 0 else None,
                "lod_to_source_max_m": _length(self.forward.max(initial=0.0)),
                "source_to_lod_max_m": _length(self.backward.max(initial=0.0)),
                "points": list(self.sample_counts)}, worst

    def _both_ways(self, source_values, lod_values, per_corner):
        """An attribute at every sampled point of both meshes (``a``) and at its nearest point on the other (``b``)."""
        src, lod = self.source, self.lod
        if self._nearest_bary is None:   # Computed once, for whichever attributes both meshes have.
            self._nearest_bary = tuple(_barycentric(g.corners().astype(np.float64)[tri], point.astype(np.float64))
                                       for g, tri, point in ((lod, self.lod_tri, self.lod_point),
                                                             (src, self.source_tri, self.source_point)))
        lod_bary, src_bary = self._nearest_bary
        a = np.concatenate([_interpolate(source_values, src.tris, self.samples.tri, self.samples.bary, per_corner),
                            _interpolate(lod_values, lod.tris, self.lod_samples.tri, self.lod_samples.bary,
                                         per_corner)])
        b = np.concatenate([_interpolate(lod_values, lod.tris, self.lod_tri, lod_bary, per_corner),
                            _interpolate(source_values, src.tris, self.source_tri, src_bary, per_corner)])
        return a, b

    def attributes(self):
        """Normals, UVs and skin weights at both meshes' sample points against the other's at the nearest points."""
        if not self.lod.triangle_count:
            return {}
        out = {}
        src, lod = self.source, self.lod
        if src.corner_normal is not None and lod.corner_normal is not None:
            a, b = self._both_ways(src.corner_normal, lod.corner_normal, True)
            a /= np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-12)
            b /= np.maximum(np.linalg.norm(b, axis=1, keepdims=True), 1e-12)
            degrees = np.degrees(np.arccos(np.clip((a * b).sum(1), -1.0, 1.0)))
            out["normals"] = _percentiles(degrees)
        if src.corner_uv is not None and lod.corner_uv is not None:
            a, b = self._both_ways(src.corner_uv, lod.corner_uv, True)
            out["uv_shift"] = _percentiles(np.linalg.norm(a - b, axis=1))
        aligned = _aligned_weights(src, lod)
        if aligned is not None:
            a, b = self._both_ways(aligned[0], aligned[1], False)
            out["skin_weights"] = dict(_percentiles(0.5 * np.abs(a - b).sum(1)), groups=int(aligned[0].shape[1]))
        return out


def _aligned_weights(a, b):
    """Both meshes' weights over the union of their groups, matched by name (a group one lacks weighs 0 there);
    by column only when neither names its groups. None when either has no weights."""
    if a.weights is None or b.weights is None:
        return None
    if a.weight_names is None or b.weight_names is None:
        return (a.weights, b.weights) if a.weights.shape[1] == b.weights.shape[1] else None
    names = list(dict.fromkeys(a.weight_names + b.weight_names))

    def spread(geometry):
        out = np.zeros((len(geometry.weights), len(names)), dtype=np.float32)
        for column, name in enumerate(geometry.weight_names):
            out[:, names.index(name)] = geometry.weights[:, column]
        return out
    return spread(a), spread(b)


def _view_basis(direction):
    d = np.asarray(direction, dtype=np.float64)
    d /= np.linalg.norm(d)
    reference = (0.0, 1.0, 0.0) if abs(d[2]) > 0.9 else (0.0, 0.0, 1.0)   # Looking along Z: Y is up.
    right = np.cross(reference, d)
    right /= np.linalg.norm(right)
    return right, np.cross(d, right)


# An orthographic view and its opposite see the same silhouette mirrored, so these are the distinct ones: the three
# axes and the four cube diagonals (one of each opposite pair).
VIEW_DIRECTIONS = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (-1, 1, 1), (1, -1, 1), (1, 1, -1))


def silhouette_views(center, radius, resolution):
    """The orthographic views of ``VIEW_DIRECTIONS``, framing a sphere, as 8 floats each (kernels.rasterize)."""
    rows = []
    scale = resolution / (2.0 * radius) if radius > 0 else 1.0
    for direction in VIEW_DIRECTIONS:
        right, up = _view_basis(direction)
        rows.append(list(right * scale) + [resolution * 0.5 - scale * float(right @ center)]
                    + list(up * scale) + [resolution * 0.5 - scale * float(up @ center)])
    return np.asarray(rows, dtype=np.float32)


def _frame(*geometries):
    """The centre and radius of the sphere around the bounding box of every geometry given that has triangles."""
    boxes = [g.bounds() for g in geometries if g.triangle_count]
    if not boxes:
        return np.zeros(3), 1e-9
    low, high = np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)
    return (low + high) * 0.5, max(float(np.linalg.norm(high - low)) * 0.5, 1e-9)


def _in_frame(center, radius, geometry):
    """Whether views framing this sphere show all of ``geometry``: its bounding box lies inside the sphere (the slack
    is float rounding only, a millionth of the radius, far below a pixel)."""
    if not geometry.triangle_count:
        return True
    low, high = geometry.bounds()
    return float(np.linalg.norm(np.maximum(np.abs(low - center), np.abs(high - center)))) <= radius * (1.0 + 1e-6)


def silhouettes(source, lods, kernels, resolution=SILHOUETTE_RESOLUTION):
    """Silhouette IoU of each LOD against the source in the same 7 views; one fact block per LOD.

    The views frame the source's bounding sphere. A LOD reaching outside it is compared in views framing both meshes
    instead, the source drawn again in them: views framed on the source alone would clip away whatever the LOD has
    beyond it (a stray part, a larger copy), and the clipped LOD would read as a perfect match."""
    pixels = resolution * resolution

    def masks(geometry, views):
        counts = kernels.rasterize(geometry.positions, geometry.tris, views, resolution)
        return np.frombuffer(counts, dtype=np.uint8).reshape(len(views), pixels) > 0

    center, radius = _frame(source)
    source_views = silhouette_views(center, radius, resolution)
    source_masks = masks(source, source_views)
    out = []
    for lod in lods:
        if _in_frame(center, radius, lod):
            views, base = source_views, source_masks
        else:
            views = silhouette_views(*_frame(source, lod), resolution)
            base = masks(source, views)
        other = masks(lod, views)
        union = (base | other).sum(1)
        iou = np.where(union > 0, (base & other).sum(1) / np.maximum(union, 1), 1.0)
        worst = int(np.argmin(iou))
        out.append({"iou_min": _round(iou[worst], 5), "iou_mean": _round(iou.mean(), 5),
                    "worst_view": [int(v) for v in VIEW_DIRECTIONS[worst]]})
    return out


def pixel_error(error_m, distances, resolution, fov_degrees):
    """The worst sampled distance in pixels at each distance, and the distance where it falls to 1 px."""
    tangent = 2.0 * math.tan(math.radians(fov_degrees) * 0.5)
    out = {"distance_at_1px_m": _round(error_m * resolution / tangent)}
    if distances:
        out["at_switch"] = [{"distance_m": _round(d), "px": _round(error_m * resolution / (tangent * d))}
                            for d in distances]
    return out


def _edges(geometry):
    """Undirected edges keyed by ``vertex_ids`` (a seam split counts once), computed once per geometry: the triangle
    sides in edge order (side index = triangle * 3 + corner), each side's edge, each edge's first side and count."""
    if geometry._edges is None:
        ids = geometry.vertex_ids[geometry.tris]
        a = ids[:, [0, 1, 2]].reshape(-1)
        b = ids[:, [1, 2, 0]].reshape(-1)
        key = np.minimum(a, b) * (int(ids.max(initial=0)) + 1) + np.maximum(a, b)   # One int64 per edge.
        order = np.argsort(key, kind="stable")
        sorted_key = key[order]
        start = np.ones(len(order), dtype=bool)
        start[1:] = sorted_key[1:] != sorted_key[:-1]
        group = np.cumsum(start) - 1
        starts = np.flatnonzero(start)
        geometry._edges = (order, group, starts, np.diff(np.append(starts, len(order))))
    return geometry._edges


def topology(geometry):
    if not geometry.triangle_count:
        return {"open_edges": 0, "non_manifold_edges": 0, "degenerate_tris": 0}
    counts = _edges(geometry)[3]
    return {"open_edges": int((counts == 1).sum()), "non_manifold_edges": int((counts > 2).sum()),
            "degenerate_tris": int((geometry.triangle_areas() <= 0.0).sum())}


def _seam_length(geometry):
    """The 3D length of edges whose two sides have different UVs at either end."""
    order, group, starts, counts = _edges(geometry)
    side_tri = order // 3
    corner_a = order % 3
    corner_b = (corner_a + 1) % 3
    ids = geometry.vertex_ids[geometry.tris]
    uv = geometry.corner_uv
    # Each side's UVs at its edge's lower and higher vertex id.
    low_first = (ids[side_tri, corner_a] <= ids[side_tri, corner_b])[:, None]
    uv_a, uv_b = uv[side_tri, corner_a], uv[side_tri, corner_b]
    uv_low = np.where(low_first, uv_a, uv_b)
    uv_high = np.where(low_first, uv_b, uv_a)
    first = starts[group]   # Every side compared with its edge's first side.
    differs = (np.abs(uv_low - uv_low[first]) > 1e-6).any(1) | (np.abs(uv_high - uv_high[first]) > 1e-6).any(1)
    seam = (np.bincount(group, weights=differs, minlength=len(starts)) > 0) & (counts >= 2)
    edges = starts[seam]   # One side per seam edge gives its length.
    tri, ka = side_tri[edges], corner_a[edges]
    p = geometry.positions[geometry.tris[tri, ka]].astype(np.float64)
    q = geometry.positions[geometry.tris[tri, (ka + 1) % 3]].astype(np.float64)
    return float(np.linalg.norm(p - q, axis=1).sum()), int(seam.sum())


def _stretch(corners, uv):
    """Per triangle: the larger over the smaller singular value of the surface-to-UV map, and the UV area."""
    e1, e2 = corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
    d1, d2 = uv[:, 1] - uv[:, 0], uv[:, 2] - uv[:, 0]
    normal = np.cross(e1, e2)
    x = e1 / np.maximum(np.linalg.norm(e1, axis=1, keepdims=True), 1e-30)
    y = np.cross(normal, x)
    y /= np.maximum(np.linalg.norm(y, axis=1, keepdims=True), 1e-30)
    p00, p01 = (e1 * x).sum(1), (e2 * x).sum(1)
    p10, p11 = (e1 * y).sum(1), (e2 * y).sum(1)
    det = p00 * p11 - p01 * p10
    safe = np.where(np.abs(det) > 0.0, det, 1.0)
    # J = U P^-1, with U = [d1 d2] and P = [[p00 p01] [p10 p11]].
    i00, i01, i10, i11 = p11 / safe, -p01 / safe, -p10 / safe, p00 / safe
    a = d1[:, 0] * i00 + d2[:, 0] * i10
    b = d1[:, 0] * i01 + d2[:, 0] * i11
    c = d1[:, 1] * i00 + d2[:, 1] * i10
    d = d1[:, 1] * i01 + d2[:, 1] * i11
    half = 0.5 * (a * a + b * b + c * c + d * d)
    root = np.sqrt(np.maximum(half * half - (a * d - b * c) ** 2, 0.0))
    s1, s2 = np.sqrt(half + root), np.sqrt(np.maximum(half - root, 0.0))
    uv_area = 0.5 * np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0])
    stretch = np.where(s2 > 0.0, s1 / np.where(s2 > 0.0, s2, 1.0), np.inf)
    return stretch, uv_area, np.abs(det) > 0.0


def uv_facts(geometry, texture_size, kernels, resolution=UV_RESOLUTION):
    """Seams, stretch, overlap, flipped triangles and texel density of a mesh's active UVs."""
    if geometry.corner_uv is None or not geometry.triangle_count:
        return None
    corners = geometry.corners().astype(np.float64)
    uv = geometry.corner_uv.astype(np.float64)
    area = geometry.triangle_areas()
    stretch, uv_area, valid = _stretch(corners, uv)
    collapsed = valid & (uv_area <= COLLAPSED_UV)
    ok = valid & ~collapsed
    signed = ((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1])
              - (uv[:, 1, 1] - uv[:, 0, 1]) * (uv[:, 2, 0] - uv[:, 0, 0]))
    seam_m, seam_edges = _seam_length(geometry)
    density = texture_size * np.sqrt(uv_area[ok] / np.maximum(area[ok], 1e-30))
    weights = area[ok]
    out = {"seam_length_m": _round(seam_m), "seam_edges": seam_edges,
           "collapsed_tris": int(collapsed.sum()), "mirrored_tris": int((signed[ok] < 0).sum())}
    if len(density):
        mean = float(np.average(density, weights=weights))
        spread = float(np.sqrt(np.average((density - mean) ** 2, weights=weights))) / mean if mean > 0 else None
        out["stretch"] = dict(_percentiles(stretch[ok], weights), mean=_round(np.average(stretch[ok], weights=weights)))
        out["texel_density"] = dict(_percentiles(density, weights), spread=_round(spread))
    # Overlap: rasterize the UV triangles over their bounds; a pixel centre under two or more is overlapped.
    low, high = uv.reshape(-1, 2).min(0), uv.reshape(-1, 2).max(0)
    size = max(float((high - low).max()), 1e-9)
    scale = resolution / size
    flat = np.concatenate([uv.reshape(-1, 2), np.zeros((len(uv) * 3, 1))], axis=1).astype(np.float32)
    flat_tris = np.arange(len(flat), dtype=np.uint32).reshape(-1, 3)
    view = np.asarray([[scale, 0, 0, -scale * low[0], 0, scale, 0, -scale * low[1]]], dtype=np.float32)
    counts = np.frombuffer(kernels.rasterize(flat, flat_tris, view, resolution), dtype=np.uint8)
    covered = int((counts > 0).sum())
    out["overlap_of_used_area"] = _round((counts > 1).sum() / covered, 4) if covered else 0.0
    out["used_of_bounds"] = _round(covered / counts.size, 4)
    out["bounds"] = [[_round(v) for v in low], [_round(v) for v in high]]
    return out


def glossary(resolution, fov_degrees, texture_size, samples=SAMPLES, silhouette_resolution=SILHOUETTE_RESOLUTION,
             uv_resolution=UV_RESOLUTION):
    """What each fact measured, once per result (the agent reads the numbers without seeing how they were made).
    Lengths are in the result's ``space``; _m names a length."""
    return {
        "hausdorff": ("nearest-surface distance from every vertex plus %d area-weighted points of the LOD to the "
                      "source, and of the source to the LOD (points: both counts); max_m is the largest sampled, a "
                      "lower bound of the true Hausdorff distance; mean_m is over the area-weighted points both ways; "
                      "max_of_bounds divides by the source's bounding-box diagonal" % samples),
        "silhouette": ("covered pixels shared over covered by either, in 7 orthographic views (the 3 axes and the 4 "
                       "cube diagonals; a view and its opposite see the same outline) at %d px framing the source's "
                       "bounding sphere, or both meshes' when the LOD reaches outside it, so nothing is clipped; "
                       "worst_view is the direction looked along" % silhouette_resolution),
        "pixel_error": ("the Hausdorff max in pixels at each switch distance on a %d px tall view with a %g degree "
                        "vertical field of view; distance_at_1px_m is where it shrinks to one pixel"
                        % (resolution, fov_degrees)),
        "normals": ("degrees between the shading normal at each sample point of the source and of this LOD and the "
                    "other mesh's at its nearest point, both interpolated from corner normals"),
        "uv_shift": ("UV-space distance between the UV at each sample point of the source and of this LOD and the "
                     "other mesh's at its nearest point; points next to a seam may read the other side's UVs, so "
                     "trust p95 over max"),
        "skin_weights": ("half the summed absolute difference of the deforming groups' weights at each sample point "
                         "of the source and of this LOD and the other mesh's nearest point: 0 the same influences, 1 "
                         "none shared"),
        "uv": ("seam: 3D length and count of edges whose sides have different UVs; stretch: larger over smaller "
               "singular value of each triangle's surface-to-UV map (1 is none), area-weighted; texel_density: "
               "texels per meter at %d px, texture_size x sqrt(UV area / surface area), area-weighted, spread its "
               "coefficient of variation; overlap_of_used_area: pixel centres under two or more UV triangles over "
               "those under any, %d px over the UV bounds; mirrored_tris: clockwise UVs; collapsed_tris: zero UV "
               "area" % (texture_size, uv_resolution)),
        "topology": "edges with one triangle (open), with three or more (non-manifold), and zero-area triangles",
        "collider": ("closed and convex as tested (each face either way round); consistent_winding: every shared edge "
                     "walked both ways; volume over the source's convex hull (excess_over_hull); hull_distance: the "
                     "Hausdorff facts from the hull to the collider; mesh_outside: share of the source's points "
                     "(points) outside the collider by inside_test, and the farthest of them"),
        "meshopt_error": "meshoptimizer's own quadric error estimate, relative to the mesh extent",
        "lod0_vertex_buffer": ("triangle corners welded where the Blender vertex, UV, shading normal and material all "
                               "match: the vertex buffer an engine receives, which every meshopt LOD indexes"),
        "apart": ("the LOD's bounding box does not overlap the source's: facts compare the objects where they are, so "
                  "a LOD placed beside its source reads far from it"),
    }
