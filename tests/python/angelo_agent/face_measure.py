# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Face and hair measurements, the same for the ground truth and both sides (any caster of ``face_casters``).

Everything is measured in the reference pictures' own orthographic views,
in metres on a fixed lattice (``u = k * STEP``, ``v = l * STEP``), so a
side's readings and the truth's line up sample for sample:

* **Outline** of a view: every silhouette edge point, where a lattice row or
  column passes from a miss to a hit, each found by bisection to 0.05 mm.
* **Profile landmarks** (side view; the face is on the right): the profile
  is the front edge of the silhouette every 0.25 mm of height. Each landmark
  is the most forward (``max``) or most set-back (``min``) point of the
  profile inside its window, the windows lying between the expected heights
  of the neighbouring landmarks (``windows``). Its height is the centre of
  the stretch within ``DELTA`` of the extreme, which is stable where the
  profile is flat; ``at_edge`` marks an extreme on a window's edge (the
  feature is not inside the window) and ``prominence`` how far it stands out
  from both window ends.
* **Eyes**: the centre of each eye object's evaluated bounds.
* **Chin bottom and jaw line** (front view): going down from the mouth,
  where the visible surface turns more than ``TURN_DEG`` away from the
  viewer (or jumps back to the neck, or to nothing): the line the front
  picture shows under the jaw, whether the jaw overhangs the neck or blends
  into it.
* **Mouth corners** (front view): the mouth line is a groove, a row deeper
  than the rows 3 mm above and below; the corners are where it has faded to
  ``GROOVE_SHARE`` of its depth at the middle (and at least ``GROOVE``),
  going out from the middle.
* **Relief** (front view): the head's surface points on the lattice, whose
  distance to the other head's surface is compared per region
  (``face_compare``, ``face_truth``).
* **Hair** (front, side, back, top): which pixels show hair (a hair mesh hit
  first, or a strand nearer than whatever the ray hits), the outer outline of
  head and hair together, the hairline over the forehead and the length at
  the back and the sides.

NumPy only; the scene is read only through the caster.
"""

import math

import numpy

STEP = 0.001
PROFILE_STEP = 0.00025
EDGE_TOLERANCE = 0.00005
DELTA = 0.0005
GROOVE = 0.0003
GROOVE_SPAN = 0.003
GROOVE_SHARE = 0.25
TURN_DEG = 70.0
HAIR_CLOSE = 0.003   # Strands closer than twice this read as one mass (``hair_view``),
HAIR_OPEN = 0.002    # and strands that stray from it thinner than twice this are not read (``mass``).
# The mouth line itself (stomion) is not one: how deep a closed mouth's crease reads in profile depends on how
# narrow a gap the mesh holds; the mouth is read from the front instead (``mouth_corners``). Its expected height
# still places the neighbouring windows.
PROFILE_LANDMARKS = {"glabella": "max", "nasion": "min", "pronasale": "max", "subnasale": "min",
                     "labrale_superius": "max", "labrale_inferius": "max", "sublabiale": "min", "pogonion": "max"}
MIN_PROMINENCE = 0.0004


# ---------------------------------------------------------------- lattice and casting helpers


def lattice(low, high, step=STEP):
    """Integer lattice indices covering ``low``..``high``."""
    return int(math.floor(low / step)), int(math.ceil(high / step))


def grid(bounds, frame, margin=0.004, step=STEP):
    """``(us, vs)``: lattice samples over the subject's bounds plus a margin, inside the view's frame."""
    u0, u1, v0, v1 = bounds
    f0, f1, g0, g1 = frame
    k0, k1 = lattice(max(u0 - margin, f0), min(u1 + margin, f1), step)
    l0, l1 = lattice(max(v0 - margin, g0), min(v1 + margin, g1), step)
    return numpy.arange(k0, k1 + 1) * step, numpy.arange(l1, l0 - 1, -1) * step


def raster(caster, us, vs, match=None):
    """Depth and names of one ray per (row, column); rows from the top (``vs`` descending)."""
    uu, vv = numpy.meshgrid(us, vs)
    depth, names = caster.first(uu.ravel(), vv.ravel(), match)
    return depth.reshape(uu.shape), names.reshape(uu.shape)


def bisect(caster, fixed, outside, inside, match=None, along="u", tolerance=EDGE_TOLERANCE):
    """Edges between misses at ``outside`` and hits at ``inside`` (arrays), moving along u (rows) or v (columns)."""
    lo, hi = numpy.asarray(outside, dtype=numpy.float64).copy(), numpy.asarray(inside, dtype=numpy.float64).copy()
    fixed = numpy.asarray(fixed, dtype=numpy.float64)
    while len(lo) and numpy.max(numpy.abs(hi - lo)) > tolerance:
        mid = (lo + hi) * 0.5
        depth, _ = caster.first(mid, fixed, match) if along == "u" else caster.first(fixed, mid, match)
        hit = numpy.isfinite(depth)
        lo, hi = numpy.where(hit, lo, mid), numpy.where(hit, mid, hi)
    return (lo + hi) * 0.5


def outline(caster, us, vs, depth, match=None):
    """Outermost left and right silhouette edges of every row (NaN for a row without a hit)."""
    hit = numpy.isfinite(depth)
    rows = numpy.flatnonzero(hit.any(axis=1))
    left, right = numpy.full(len(vs), numpy.nan), numpy.full(len(vs), numpy.nan)
    if not len(rows):
        return left, right
    step = us[1] - us[0]
    first = hit[rows].argmax(axis=1)
    last = len(us) - 1 - hit[rows][:, ::-1].argmax(axis=1)
    left[rows] = bisect(caster, vs[rows], us[first] - step, us[first], match)
    right[rows] = bisect(caster, vs[rows], us[last] + step, us[last], match)
    return left, right


def contour(caster, us, vs, hit, match=None, refine=True):
    """Every silhouette edge point: each change from miss to hit along a row (bisected along u) and along a
    column (bisected along v), so near-horizontal edges (the crown, an ear's lowest tip) are as exact as
    near-vertical ones. ``refine`` False (strands, which no ray can hit) puts each point half way."""
    across = numpy.argwhere(hit[:, :-1] != hit[:, 1:])
    down = numpy.argwhere(hit[:-1, :] != hit[1:, :])
    points = []
    if len(across):
        r, c = across[:, 0], across[:, 1]
        inside = numpy.where(hit[r, c], us[c], us[c + 1])
        outside = numpy.where(hit[r, c], us[c + 1], us[c])
        u = bisect(caster, vs[r], outside, inside, match) if refine else (inside + outside) * 0.5
        points.append(numpy.column_stack((u, vs[r])))
    if len(down):
        r, c = down[:, 0], down[:, 1]
        inside = numpy.where(hit[r, c], vs[r], vs[r + 1])
        outside = numpy.where(hit[r, c], vs[r + 1], vs[r])
        v = bisect(caster, us[c], outside, inside, match, along="v") if refine else (inside + outside) * 0.5
        points.append(numpy.column_stack((us[c], v)))
    return numpy.concatenate(points) if points else numpy.zeros((0, 2))


# ---------------------------------------------------------------- the profile


def front_edges(caster, vs, guess, match=None, reach=0.03):
    """The front (largest u) silhouette edge at heights ``vs``, starting from ``guess`` (NaN where none)."""
    vs = numpy.asarray(vs, dtype=numpy.float64)
    outside = numpy.where(numpy.isfinite(guess), guess + 0.003, numpy.nan)
    result = numpy.full(len(vs), numpy.nan)
    todo = numpy.flatnonzero(numpy.isfinite(outside))
    for _ in range(12):   # Make sure ``outside`` is a miss: step out where it is not.
        if not len(todo):
            break
        depth, _ = caster.first(outside[todo], vs[todo], match)
        blocked = numpy.isfinite(depth)
        outside[todo[blocked]] += 0.004
        todo = todo[blocked]
    inside = outside.copy()
    found = numpy.zeros(len(vs), dtype=bool)
    todo = numpy.flatnonzero(numpy.isfinite(outside))
    walked = 0.0
    while len(todo) and walked < reach:
        inside[todo] -= 0.0008
        walked += 0.0008
        depth, _ = caster.first(inside[todo], vs[todo], match)
        hit = numpy.isfinite(depth)
        found[todo[hit]] = True
        todo = todo[~hit]
    rows = numpy.flatnonzero(found)
    result[rows] = bisect(caster, vs[rows], inside[rows] + 0.0008, inside[rows], match)
    return result


def windows(anchors, top=0.020, bottom=0.012):
    """``{name: (low, high)}`` heights of each landmark's window: half way to its neighbours' expected heights."""
    ordered = sorted(anchors.items(), key=lambda item: -item[1])
    found = {}
    for index, (name, v) in enumerate(ordered):
        high = v + top if index == 0 else (v + ordered[index - 1][1]) * 0.5
        low = v - bottom if index == len(ordered) - 1 else (v + ordered[index + 1][1]) * 0.5
        found[name] = (low, high)
    return found


def extremum(vs, us, window, kind, delta=DELTA):
    """The landmark of one window of a profile (``vs`` descending), or None when the window holds no profile."""
    low, high = window
    inside = (vs >= low) & (vs <= high) & numpy.isfinite(us)
    index = numpy.flatnonzero(inside)
    if len(index) < 3:
        return None
    values = us[index] if kind == "max" else -us[index]
    best = int(numpy.argmax(values))
    run_low = run_high = best
    while run_low > 0 and values[run_low - 1] >= values[best] - delta and index[run_low - 1] == index[run_low] - 1:
        run_low -= 1
    while run_high < len(index) - 1 and values[run_high + 1] >= values[best] - delta and \
            index[run_high + 1] == index[run_high] + 1:
        run_high += 1
    v = float(numpy.mean(vs[index[run_low:run_high + 1]]))
    prominence = float(values[best] - max(values[0], values[-1]))
    return {"u": float(us[index[best]]), "v": v, "at_edge": best in (0, len(index) - 1),
            "prominence": prominence}


def profile_landmarks(vs, us, anchors):
    """Every profile landmark named in ``anchors`` (expected heights) found on the profile ``(vs, us)``; every
    anchor, the mouth's included, places the windows."""
    spans = windows(anchors)
    return {name: extremum(vs, us, spans[name], PROFILE_LANDMARKS[name]) for name in anchors
            if name in PROFILE_LANDMARKS}


# ---------------------------------------------------------------- the front view


def turn_below(columns, start_v, depth_at, reach=0.07, row=0.0005):
    """Per column (u), going down from ``start_v``: where the surface first turns more than ``TURN_DEG`` away
    from the viewer (its depth falls away faster than tan(TURN_DEG) per unit of height), or jumps back to
    something further or to nothing. The jaw line and the chin's bottom as the front picture shows them,
    whether the jaw overhangs the neck or blends into it. Heights interpolated between rows (NaN: none).

    ``depth_at(us, vs)`` reads planar depths.
    """
    columns = numpy.asarray(columns, dtype=numpy.float64)
    rows = numpy.arange(start_v, start_v - reach, -row)
    uu, vv = numpy.meshgrid(columns, rows)
    depth = depth_at(uu.ravel(), vv.ravel()).reshape(uu.shape)
    result = numpy.full(len(columns), numpy.nan)
    found = _first_turn(depth, row)
    hit = numpy.flatnonzero(found >= 0)
    if not len(hit):
        return result
    # Again between the rows around each turn, 0.05 mm apart: the same rule, a tenth of the step.
    fine_row = row / 10.0
    offsets = numpy.arange(0, 31) * fine_row
    tops = rows[numpy.maximum(found[hit] - 1, 0)]
    fine_vs = tops[:, None] - offsets[None, :]
    fine_us = numpy.repeat(columns[hit][:, None], len(offsets), axis=1)
    fine = depth_at(fine_us.ravel(), fine_vs.ravel()).reshape(fine_vs.shape).T
    turn = _first_turn(fine, fine_row)
    result[hit] = numpy.where(turn >= 0, fine_vs[numpy.arange(len(hit)), numpy.maximum(turn, 0)] - fine_row * 0.5,
                              rows[found[hit]] - row * 0.5)
    return result


def _first_turn(depth, row):
    """Per column of ``depth`` (rows going down ``row`` apart): the first row index after which the surface
    falls away by more than tan(TURN_DEG) per unit of height, or jumps to nothing (-1: never)."""
    limit = math.tan(math.radians(TURN_DEG)) * row
    above, below = depth[:-1], depth[1:]
    with numpy.errstate(invalid="ignore"):
        steps = numpy.where(numpy.isfinite(below), below - above, numpy.inf)
    steep = numpy.isfinite(above) & (steps > limit)
    return numpy.where(steep.any(axis=0), steep.argmax(axis=0), -1)


def mouth_corners(caster, centre_v, match=None, half_width=0.045):
    """The mouth line's two corners and its middle (``[u, v]`` each) and its depth at the middle, or None."""
    us = numpy.arange(-half_width, half_width + 1e-9, 0.0005)
    vs = numpy.arange(centre_v + 0.008, centre_v - 0.008 - 1e-9, -0.00025)
    depth, _ = raster(caster, us, vs, match)
    span = int(round(GROOVE_SPAN / 0.00025))
    groove = depth[span:-span] - 0.5 * (depth[:-2 * span] + depth[2 * span:])
    groove = numpy.where(numpy.isfinite(groove), groove, -numpy.inf)
    strength, deepest = groove.max(axis=0), groove.argmax(axis=0) + span
    middle = int(numpy.argmin(numpy.abs(us)))
    if strength[middle] < GROOVE:
        return None
    # The corners are where the groove has faded to a quarter of its depth at the middle: a share, so a
    # finer or coarser mesh of the same mouth (whose groove is deeper or shallower overall) reads the same.
    floor = max(GROOVE, GROOVE_SHARE * strength[middle])
    left = right = middle
    while left > 0 and strength[left - 1] >= floor:
        left -= 1
    while right < len(us) - 1 and strength[right + 1] >= floor:
        right += 1
    return {"left": [float(us[left]), float(vs[deepest[left]])], "right": [float(us[right]), float(vs[deepest[right]])],
            "middle": [float(us[middle]), float(vs[deepest[middle]])], "middle_depth": float(strength[middle])}


# ---------------------------------------------------------------- the whole head


def measure_head(casters, match, bounds, frames, anchors, eyes, rows, jaw_columns=None):
    """Every face reading of one head. ``casters[view]`` reads the scene, ``bounds[view]`` frames the subject,
    ``frames[view]`` is the reference picture's extent, ``anchors`` place the profile landmarks' windows (side
    view), ``eyes`` are the eye centres already read, ``rows`` the heights the mouth and the jaw are read from
    and ``jaw_columns`` where to read the jaw line (None: choose them)."""
    result = {"eyes": eyes, "rows": dict(rows)}
    for name in ("front", "side"):
        if bounds.get(name) is None:
            result[name] = None
            continue
        us, vs = grid(bounds[name], frames[name])
        depth, _ = raster(casters[name], us, vs, match)
        left, right = outline(casters[name], us, vs, depth, match)
        result[name] = {"us": us, "vs": vs, "depth": depth, "left": left, "right": right,
                        "contour": contour(casters[name], us, vs, numpy.isfinite(depth), match)}
    side = result.get("side")
    if side is not None:
        top = max(anchors.values()) + 0.025
        low = min(anchors.values()) - 0.022
        fine = numpy.arange(top, low, -PROFILE_STEP)
        guess = numpy.interp(-fine, -side["vs"], numpy.where(numpy.isfinite(side["right"]), side["right"], -1.0))
        guess = numpy.where(guess > -1.0, guess, numpy.nan)
        front = front_edges(casters["side"], fine, guess, match)
        result["profile"] = {"vs": fine, "us": front}
        result["landmarks"] = profile_landmarks(fine, front, anchors)
    front_view = result.get("front")
    if front_view is not None:
        caster = casters["front"]
        result["mouth"] = mouth_corners(caster, rows["mouth"], match)
        if jaw_columns is None:
            row = int(numpy.argmin(numpy.abs(front_view["vs"] - rows["mouth"])))
            half = 0.5 * (front_view["right"][row] - front_view["left"][row])
            reach = 0.85 * half if numpy.isfinite(half) else 0.04
            jaw_columns = sorted(set(numpy.round(numpy.concatenate(
                (numpy.arange(0.008, reach, 0.004), -numpy.arange(0.008, reach, 0.004))), 6).tolist()))

        def depth_at(us, vs):
            return caster.first(us, vs, match)[0]

        drops = turn_below([0.0] + list(jaw_columns), rows["jaw"], depth_at)
        result["menton"] = None if numpy.isnan(drops[0]) else float(drops[0])
        result["jaw"] = {"u": list(jaw_columns), "v": drops[1:].tolist()}
    return result


def surface_points(view, data):
    """World points of a front raster's hits, and the lattice cells they belong to (row, column)."""
    rows, columns = numpy.nonzero(numpy.isfinite(data["depth"]))
    origins = view.origins(data["us"][columns], data["vs"][rows])
    return origins + view.forward * data["depth"][rows, columns][:, None], rows, columns


# ---------------------------------------------------------------- hair


def _shifted(mask, dy, dx, fill):
    out = numpy.full(mask.shape, fill)
    h, w = mask.shape
    out[max(0, dy):h + min(0, dy), max(0, dx):w + min(0, dx)] = \
        mask[max(0, -dy):h + min(0, -dy), max(0, -dx):w + min(0, -dx)]
    return out


def _grow(mask, offsets, fill):
    grown = numpy.zeros_like(mask)
    for dy, dx in offsets:
        grown |= _shifted(mask, dy, dx, fill)
    return grown


def _shrink(mask, offsets, fill):
    shrunk = numpy.ones_like(mask)
    for dy, dx in offsets:
        shrunk &= _shifted(mask, dy, dx, fill)
    return shrunk


def _disk(radius):
    return [(dy, dx) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)
            if dy * dy + dx * dx <= radius * radius]


def mass(mask, close_radius, open_radius):
    """The hair as a mass: gaps narrower than ``2 * close_radius`` pixels closed, then anything thinner than
    ``2 * open_radius`` (a stray strand) opened away; the outer edge of the mass stays where it was."""
    if close_radius >= 1:
        disk = _disk(close_radius)
        mask = _shrink(_grow(mask, disk, False), disk, True)
    if open_radius >= 1:
        disk = _disk(open_radius)
        mask = _grow(_shrink(mask, disk, False), disk, False)
    return mask


def hair_view(caster, us, vs, strands_depth, is_hair_name):
    """``{"hair", "union"}`` masks of one view: what shows hair, and head and hair together.

    Strands are read as the mass they make (``mass``): gaps up to ``2 * HAIR_CLOSE`` wide are closed and
    strays thinner than ``2 * HAIR_OPEN`` dropped before anything is read, so the same style in 2,000 strands
    or in 16,000 reads alike (``face_variants``), a single strand hanging loose sets no edge, and a visible gap
    wider than that (a parting, the skin beside a sideburn) stays.
    """
    depth, names = raster(caster, us, vs, None)
    mesh_hair = numpy.vectorize(lambda name: bool(name) and is_hair_name(name), otypes=[bool])(names)
    pitch = float(us[1] - us[0])
    radii = int(round(HAIR_CLOSE / pitch)), int(round(HAIR_OPEN / pitch))
    strand_seen = mass(strands_depth < depth, *radii)
    strands_any = mass(numpy.isfinite(strands_depth), *radii)
    return {"hair": mesh_hair | strand_seen, "union": numpy.isfinite(depth) | strands_any, "us": us, "vs": vs}


def strand_depths(view, strands, us, vs, sample=0.0004):
    """Nearest strand depth per lattice pixel (``inf`` where no strand passes): every strand sampled every
    ``sample`` metres along its length, each sample marking the pixel it falls in."""
    depth = numpy.full((len(vs), len(us)), numpy.inf)
    if not len(strands):
        return depth
    points = numpy.concatenate(strands)
    lengths = numpy.asarray([len(strand) for strand in strands])
    ends = numpy.cumsum(lengths) - 1
    segment_start = numpy.setdiff1d(numpy.arange(len(points) - 1), ends)
    a, b = points[segment_start], points[segment_start + 1]
    counts = numpy.maximum(1, numpy.ceil(numpy.linalg.norm(b - a, axis=1) / sample).astype(numpy.int64))
    offsets = numpy.arange(counts.sum()) - numpy.repeat(numpy.cumsum(counts) - counts, counts)
    t = offsets / numpy.repeat(counts, counts)
    samples = numpy.concatenate((numpy.repeat(a, counts, axis=0) + numpy.repeat(b - a, counts, axis=0) * t[:, None],
                                 points[ends]))
    u, v, d = view.project(samples)
    step_u, step_v = us[1] - us[0], vs[0] - vs[1]
    column = numpy.round((u - us[0]) / step_u).astype(numpy.int64)
    row = numpy.round((vs[0] - v) / step_v).astype(numpy.int64)
    inside = (column >= 0) & (column < len(us)) & (row >= 0) & (row < len(vs))
    flat, d = row[inside] * len(us) + column[inside], d[inside]
    order = numpy.lexsort((d, flat))
    unique, first = numpy.unique(flat[order], return_index=True)
    depth.ravel()[unique] = d[order][first]
    return depth


def hairline(view_data, columns, eye_v):
    """Per column (u): the lowest hair above ``eye_v + 0.012`` (the lower edge of the hair mass over the forehead)."""
    us, vs, hair = view_data["us"], view_data["vs"], view_data["hair"]
    found = []
    for u in columns:
        column = int(numpy.argmin(numpy.abs(us - u)))
        rows = numpy.flatnonzero(hair[:, column] & (vs > eye_v + 0.012))
        found.append(float(vs[rows.max()]) if len(rows) else numpy.nan)
    return found


def lowest_hair(view_data, low, high):
    """The median over columns ``low <= |u| <= high`` of each column's lowest hair pixel (NaN where none)."""
    us, vs, hair = view_data["us"], view_data["vs"], view_data["hair"]
    found = []
    for column in numpy.flatnonzero((numpy.abs(us) >= low) & (numpy.abs(us) <= high)):
        rows = numpy.flatnonzero(hair[:, column])
        if len(rows):
            found.append(vs[rows.max()])
    return float(numpy.median(found)) if found else math.nan


def hair_contour(view_data):
    """Edge points of head and hair together (each half way between a lattice pixel and its neighbour)."""
    return contour(None, view_data["us"], view_data["vs"], view_data["union"], refine=False)
