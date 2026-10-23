# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Outlines for ``angelo_view``'s reference comparisons: a picture's subject, the targets' silhouette, the gap.

NumPy only, never ``bpy``: these run on the view's worker thread.

* ``subject_mask``: the subject of a reference picture taken against a plain
  background (a reference sheet, a studio photo, a render): the background's
  colour is the median of the picture's border and the subject every pixel
  that differs from it by more than ``THRESHOLD`` (or ``NOISE_FACTOR`` times
  the border's own spread). A border of many colours is busy; a border with a
  second main colour (``BACKGROUND_SHARE`` of it or more) is ambiguous: the
  majority colour is taken and the result says so. A cut-out (transparency)
  needs none of this (``view_compare``).
* ``normalized``: the one outline both sides are compared by, whatever made
  each mask: gaps and slots narrower than the closing (``CLOSE_SHARE`` of the
  picture's longer side, so strands read as one mass) are joined, parts
  smaller than ``SPECK_SHARE`` of the picture are left out, and holes are
  filled unless both masks can show them (a cut-out against the silhouette).
* ``compare``: how far the targets' outline lies from the picture's: every
  edge pixel's distance to the other outline's nearest, both ways (mean, 95th
  percentile, maximum), their overlap (IoU), the offset between their centres
  and the ratio of their sizes. Outlines of more than ``MAX_EDGE_POINTS`` edge
  pixels are compared at half size (and so on), which the result states;
  distances are worked out ``CHUNK_ENTRIES`` at a time, so memory is bounded.
* ``gaps``: where the outlines are farthest apart, searched from both, a few
  places spread along them: the targets' edge pixel there, the 3D point it
  shows (``unproject``, from the silhouette's depth) and the offset in the
  view's plane from it to the picture's outline, so a world-space brush path
  can go straight to it.
* Regions by runs (a union-find over each row's runs of set pixels), so a
  1,024-pixel picture is labelled in milliseconds without SciPy.
"""

import numpy

# Three 8-bit levels in every channel (sqrt(3) * 3 / 255): above the rounding and dithering of one flat colour,
# so a render's dark shaded edge on a dark viewport background stays the subject's (on angelo_view's own SOLID
# pictures 5% of a head's pixels lie within 0.05 of the background).
THRESHOLD = 0.02
NOISE_FACTOR = 3.0         # A noisy background's own median spread, times this, is background too.
# Median spread of a border about its colour above which it is busy (about six levels a channel). On the 79
# reference pictures of the one-shot tests it read every studio sheet as plain and every close-up, hero view and
# photo as busy.
PLAIN_SPREAD = 0.04
BACKGROUND_SHARE = 0.2     # A second colour on this share of the border or more makes the background ambiguous.
CLOSE_SHARE = 3 / 1024     # Closing radius as a share of the longer side (3 px at 1,024; at least 1 px).
SPECK_SHARE = 0.001        # Parts smaller than this share of the picture are left out of both outlines.
MAX_EDGE_POINTS = 8000     # Edge pixels per outline compared at full size at most.
CHUNK_ENTRIES = 250_000    # Point pairs whose distance is worked out at once (2 MB of float64 an array).
MAX_GAPS = 6               # Places reported by ``gaps``, the farthest apart first.
MIN_GAP_PX = 2.0           # A gap of a pixel or less is the outlines' own rasterization.
GAP_CANDIDATES = 200       # Farthest edge pixels of each outline considered for ``gaps``.


def _runs(row):
    """``[(start, end)]`` of a boolean row's runs of True (end exclusive)."""
    padded = numpy.concatenate(([False], row, [False])).astype(numpy.int8)
    change = numpy.flatnonzero(numpy.diff(padded))
    return list(zip(change[0::2].tolist(), change[1::2].tolist()))


def _labels(mask):
    """Runs of every row and the component each run belongs to (4-connected): ``(runs, roots)``."""
    runs, parents = [], []

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    previous = []
    for row in range(mask.shape[0]):
        current = []
        for start, end in _runs(mask[row]):
            index = len(runs)
            runs.append((row, start, end))
            parents.append(index)
            for other in previous:
                _row, other_start, other_end = runs[other]
                if other_start < end and start < other_end:   # Overlapping columns: touching vertically.
                    a, b = find(index), find(other)
                    if a != b:
                        parents[max(a, b)] = min(a, b)
            current.append(index)
        previous = current
    return runs, [find(index) for index in range(len(runs))]


def _regions(mask, keep):
    """``mask`` with only the regions ``keep(areas)`` names (a set of roots, given ``{root: area}``)."""
    runs, roots = _labels(mask)
    out = numpy.zeros_like(mask)
    if not runs:
        return out
    area = {}
    for (_row, start, end), root in zip(runs, roots):
        area[root] = area.get(root, 0) + end - start
    kept = keep(area)
    for (row, start, end), root in zip(runs, roots):
        if root in kept:
            out[row, start:end] = True
    return out


def largest_region(mask):
    """The largest 4-connected region of ``mask`` (all False when it has none)."""
    return _regions(mask, lambda area: {max(area, key=area.get)})


def significant(mask, minimum):
    """``mask`` with only its 4-connected parts of at least ``minimum`` pixels (the largest is always kept)."""
    return _regions(mask, lambda area: {root for root, size in area.items() if size >= minimum}
                    | {max(area, key=area.get)})


def fill_holes(mask):
    """``mask`` with every region of False that does not touch the picture's border made True."""
    runs, roots = _labels(~mask)
    height, width = mask.shape
    outside = set()
    for (row, start, end), root in zip(runs, roots):
        if row in (0, height - 1) or start == 0 or end == width:
            outside.add(root)
    out = mask.copy()
    for (row, start, end), root in zip(runs, roots):
        if root not in outside:
            out[row, start:end] = True
    return out


def _disk(radius):
    return [(dy, dx) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1)
            if dy * dy + dx * dx <= radius * radius]


def _shifted(mask, dy, dx, fill):
    height, width = mask.shape
    out = numpy.full_like(mask, fill)
    out[max(dy, 0):height + min(dy, 0), max(dx, 0):width + min(dx, 0)] = \
        mask[max(-dy, 0):height + min(-dy, 0), max(-dx, 0):width + min(-dx, 0)]
    return out


def close(mask, radius):
    """``mask`` with gaps narrower than about ``2 * radius`` pixels filled (a dilation, then an erosion, by a disk)."""
    grown = numpy.zeros_like(mask)
    for dy, dx in _disk(radius):
        grown |= _shifted(mask, dy, dx, False)
    shrunk = numpy.ones_like(mask)
    for dy, dx in _disk(radius):
        shrunk &= _shifted(grown, dy, dx, True)   # Past the border counts as covered: a mass there stays.
    return shrunk & grown


def close_radius(shape):
    return max(1, int(round(CLOSE_SHARE * max(shape))))


def normalized(mask, holes=False):
    """The outline both sides are compared by: narrow gaps closed, specks left out, holes filled unless ``holes``."""
    if not mask.any():
        return mask
    out = significant(close(mask, close_radius(mask.shape)), max(1, int(SPECK_SHARE * mask.size)))
    return out if holes else fill_holes(out)


def touches_border(mask):
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def subject_mask(rgb):
    """The subject of a picture (rows top first, RGB or RGBA, 0-255) against its background, and facts.

    The mask is as found (``normalized`` makes the outline both sides are compared by). ``background``
    is PLAIN, BUSY (the border is many colours) or AMBIGUOUS (a second colour covers ``BACKGROUND_SHARE`` of the
    border or more: the majority colour was taken, which may be the subject's).
    """
    pixels = numpy.asarray(rgb, dtype=numpy.float32)[..., :3] / 255.0
    border = numpy.concatenate((pixels[0], pixels[-1], pixels[:, 0], pixels[:, -1]))
    background = numpy.median(border, axis=0)
    apart = numpy.linalg.norm(border - background, axis=1)
    spread = float(numpy.median(apart))
    threshold = max(THRESHOLD, NOISE_FACTOR * spread)
    other = float((apart > threshold).mean())   # The border's share that is not the background's colour.
    mask = numpy.linalg.norm(pixels - background, axis=2) > threshold
    kind = "BUSY" if spread > PLAIN_SPREAD else "AMBIGUOUS" if other >= BACKGROUND_SHARE else "PLAIN"
    return mask, {"background": kind, "background_rgb": [int(round(value * 255)) for value in background.tolist()],
                  "background_share_of_border": round(1.0 - other, 3)}


def edges(mask):
    """Edge pixels of a mask: set pixels with an unset 4-neighbour (or the picture's border beside them)."""
    padded = numpy.pad(mask, 1, constant_values=False)
    inner = padded[1:-1, 1:-1]
    interior = inner & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return inner & ~interior


def _pooled(mask, factor):
    """``mask`` at 1/``factor`` size: a coarse pixel is set when any of its pixels is."""
    if factor == 1:
        return mask
    height, width = mask.shape
    rows, columns = -(-height // factor), -(-width // factor)
    padded = numpy.zeros((rows * factor, columns * factor), dtype=bool)
    padded[:height, :width] = mask
    return padded.reshape(rows, factor, columns, factor).any(axis=(1, 3))


def _scaled(reference, target):
    """``(factor, reference, target)`` at the largest size where neither outline has more than MAX_EDGE_POINTS."""
    factor = 1
    while True:
        a, b = _pooled(reference, factor), _pooled(target, factor)
        if max(numpy.count_nonzero(edges(a)), numpy.count_nonzero(edges(b))) <= MAX_EDGE_POINTS or min(a.shape) < 16:
            return factor, a, b
        factor *= 2


def _nearest(a, b, index=False):
    """Each point of ``a``'s distance to the nearest of ``b`` (and with ``index``, which one), in bounded chunks."""
    a, b = a.astype(numpy.float64), b.astype(numpy.float64)
    found, which = numpy.empty(len(a)), numpy.empty(len(a), dtype=numpy.int64)
    squares = (b * b).sum(axis=1)
    chunk = max(1, CHUNK_ENTRIES // max(len(b), 1))
    for start in range(0, len(a), chunk):
        part = a[start:start + chunk]
        squared = (part * part).sum(axis=1)[:, None] + squares[None, :] - 2.0 * (part @ b.T)
        nearest = squared.argmin(axis=1)
        which[start:start + chunk] = nearest
        found[start:start + chunk] = numpy.sqrt(numpy.maximum(squared[numpy.arange(len(part)), nearest], 0.0))
    return (found, which) if index else found


def compare(reference, target, pixel_world=None):
    """How far the targets' outline (mask ``target``) lies from the picture's (mask ``reference``), both
    ``normalized`` the same way."""
    if not reference.any() or not target.any():
        return {"compared": False, "reason": "no subject found in the picture" if not reference.any()
                else "the targets are not in this view"}
    factor, small_ref, small_tgt = _scaled(reference, target)
    ref_edges = numpy.argwhere(edges(small_ref))
    tgt_edges = numpy.argwhere(edges(small_tgt))
    distances = numpy.concatenate((_nearest(ref_edges, tgt_edges), _nearest(tgt_edges, ref_edges))) * factor
    union = numpy.count_nonzero(reference | target)
    ref_centre, tgt_centre = numpy.argwhere(reference).mean(axis=0), numpy.argwhere(target).mean(axis=0)
    result = {"compared": True, "iou": round(float(numpy.count_nonzero(reference & target) / union), 4),
              "mean_px": round(float(distances.mean()), 2), "p95_px": round(float(numpy.percentile(distances, 95)), 2),
              "max_px": round(float(distances.max()), 2),
              # Down and right are positive, as image rows and columns run.
              "centre_offset_px": [round(float(tgt_centre[1] - ref_centre[1]), 2),
                                   round(float(tgt_centre[0] - ref_centre[0]), 2)],
              "size_ratio": round(float(numpy.sqrt(target.sum() / reference.sum())), 4)}
    if factor > 1:
        result["measured_every_px"] = factor   # Outlines this detailed are compared at 1/factor size.
    if pixel_world:
        result["pixel_world"] = pixel_world
        for key in ("mean", "p95", "max"):
            result[key + "_world"] = round(result[key + "_px"] * pixel_world, 6)
    return result


def unproject(camera, pixels, depth):
    """World points of view pixels ``[(column, row)]`` (rows top first) at window ``depth`` (0-1) of ``camera``."""
    height, width = depth.shape
    matrix = numpy.linalg.inv(numpy.array(camera["projection_matrix"], dtype=numpy.float64)
                              @ numpy.array(camera["view_matrix"], dtype=numpy.float64))
    columns, rows = pixels[:, 0], pixels[:, 1]
    ndc = numpy.column_stack(((columns + 0.5) / width * 2.0 - 1.0, 1.0 - (rows + 0.5) / height * 2.0,
                              depth[rows, columns] * 2.0 - 1.0, numpy.ones(len(pixels))))
    world = ndc @ matrix.T
    return world[:, :3] / world[:, 3:4]


def pixel_size(camera, width, along):
    """World size of one pixel at view distance ``along`` (perspective) or anywhere (orthographic)."""
    projection = numpy.array(camera["projection_matrix"], dtype=numpy.float64)
    return 2.0 / (projection[0, 0] * width) * (along if projection[3, 3] == 0.0 else 1.0)


def _along(camera, points):
    view = numpy.array(camera["view_matrix"], dtype=numpy.float64)
    return -(points @ view[:3, :3].T + view[:3, 3])[:, 2]    # A camera looks along its -Z.


def outline_pixel_world(target, depth, camera):
    """One pixel's world size at the median distance of the targets' drawn outline, or None."""
    pixels = numpy.argwhere(edges(target) & (depth < 1.0))[:, ::-1]
    if not len(pixels):
        return None
    along = float(numpy.median(_along(camera, unproject(camera, pixels, depth))))
    return float("%.4g" % pixel_size(camera, depth.shape[1], along))


def _drawn_near(depth, pixel, radius):
    """``pixel`` (column, row) when the silhouette drew a surface there, else the nearest drawn one within
    ``radius`` (a pixel only the closing covers), else None."""
    column, row = int(pixel[0]), int(pixel[1])
    if depth[row, column] < 1.0:
        return numpy.array((column, row))
    window = depth[max(row - radius, 0):row + radius + 1, max(column - radius, 0):column + radius + 1]
    drawn = numpy.argwhere(window < 1.0)
    if not len(drawn):
        return None
    drawn = drawn + (max(row - radius, 0), max(column - radius, 0))
    best = drawn[((drawn - (row, column)) ** 2).sum(axis=1).argmin()]
    return numpy.array((best[1], best[0]))


def gaps(reference, target, depth, camera):
    """The places the outlines are farthest apart, searched from both (at most ``MAX_GAPS``, at least
    ``MIN_GAP_PX``): each with the targets' edge pixel there, its 3D point and the in-plane offset (world units at
    that point's distance) to the picture's outline.

    ``direction`` is IN where the targets reach past the picture's subject (the picture's outline lies inside
    them, or the targets have a part the picture has none of) and OUT where the subject reaches past the targets
    (they would grow to meet it). A place nearer to a farther one already taken than a twelfth of the outlines'
    length is left out.
    """
    factor, small_ref, small_tgt = _scaled(reference, target)
    ref_edges = numpy.argwhere(edges(small_ref))[:, ::-1]    # (column, row)
    tgt_edges = numpy.argwhere(edges(small_tgt))[:, ::-1]
    if not len(ref_edges) or not len(tgt_edges):
        return []
    from_ref, nearest_tgt = _nearest(ref_edges, tgt_edges, index=True)
    from_tgt, nearest_ref = _nearest(tgt_edges, ref_edges, index=True)

    def direction(picture, edge, from_targets):
        if small_tgt[picture[1], picture[0]]:
            return "IN"
        if small_ref[edge[1], edge[0]]:
            return "OUT"
        return "IN" if from_targets else "OUT"    # Neither inside the other: a part only one side has.

    # Largest first; among equal gaps, the picture's edges before the targets', each in image order (top row first,
    # then left). A stable sort is what makes that hold on every machine: the default one leaves ties to the CPU's
    # sorting code, and x86-64 and arm64 listed a different "worst" place for the same pictures.
    candidates = ([(from_ref[i], ref_edges[i], tgt_edges[nearest_tgt[i]], False)
                   for i in numpy.argsort(-from_ref, kind="stable")[:GAP_CANDIDATES]]
                  + [(from_tgt[i], ref_edges[nearest_ref[i]], tgt_edges[i], True)
                     for i in numpy.argsort(-from_tgt, kind="stable")[:GAP_CANDIDATES]])
    candidates.sort(key=lambda found: -found[0])
    height, width = depth.shape
    spacing = max(8.0, (len(ref_edges) + len(tgt_edges)) / (4.0 * MAX_GAPS))
    reach = factor + close_radius(depth.shape) + 1
    taken, found = [], []
    for gap, picture, edge, from_targets in candidates:
        if gap * factor < MIN_GAP_PX or len(found) == MAX_GAPS:
            break
        middle = (picture + edge) / 2.0
        if any(abs(middle - other).max() < spacing for other in taken):
            continue
        full_edge = _drawn_near(depth, numpy.minimum(edge * factor + factor // 2, (width - 1, height - 1)), reach)
        if full_edge is None:
            continue
        taken.append(middle)
        full_picture = numpy.minimum(picture * factor + factor // 2, (width - 1, height - 1))
        surface = unproject(camera, full_edge[None, :], depth)[0]
        step = (full_picture - full_edge).astype(float)
        view = numpy.array(camera["view_matrix"], dtype=numpy.float64)
        offset = ((view[0, :3] * step[0] - view[1, :3] * step[1])
                  * pixel_size(camera, width, float(_along(camera, surface[None, :])[0])))
        found.append({"direction": direction(picture, edge, from_targets), "gap_px": round(float(gap * factor), 1),
                      "gap_world": round(float(numpy.linalg.norm(offset)), 6),
                      "targets_edge_px": full_edge.tolist(), "picture_edge_px": full_picture.tolist(),
                      "surface_world": [round(float(v), 5) for v in surface],
                      "offset_world": [round(float(v), 5) for v in offset]})
    return found


def draw_edges(rgba, mask, colour, width=1):
    """Paint ``mask``'s outline onto ``rgba`` (rows top first) in ``colour`` (RGB 0-255), ``width`` pixels wide."""
    line = edges(mask)
    for _ in range(width - 1):
        padded = numpy.pad(line, 1, constant_values=False)
        line = line | padded[:-2, 1:-1] | padded[2:, 1:-1] | padded[1:-1, :-2] | padded[1:-1, 2:]
    rgba[line, :3] = colour
    return rgba
