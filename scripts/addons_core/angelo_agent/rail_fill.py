# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How much of a picture geometry fills, as the session rail's Main tile measures it (``rail_pictures.lead_view``),
and the bounds a world box alone gives of it: the maintainer's measure (R3-M2, "Count where it is", 2026-10-25) and
its pre-test live here together, so they change together."""

import math

CELLS = (16, 10)   # The grid ``fill`` counts in (the maintainer, 2026-10-25, "Count where it is"): cells across and
                   # down, each width/16 x height/10 of whatever picture it is given (16 px on the 256 x 160 tile).
FILL = 1.0 / 16.0  # The camera or the artist's view frames the Main picture only while geometry pixels lie in at least
                   # this share of the cells: 10 of 160, a sixteenth of the picture wherever the geometry lies (two
                   # specks far apart fill 2 cells; a line across the whole width fills 16). A share of the picture,
                   # so the same for any picture size, model size or distance.
KEEP = 7.0 / 160.0  # A source the Main picture already shows stays while its geometry, grown by ``KEEP_PX``, lies in
                    # at least this share of the cells, 7 of 160 (hysteresis, the coordinator, 2026-10-25, under the
                    # maintainer's FILL): a 2 px pan moved Suzanne at ~55 x 39 px from 12 cells to 9, and a thin bar
                    # 12 to 5 (REVIEW-TILE-COST-2, -STAGES-2 L-c).
KEEP_PX = 2         # The pan, in tile pixels, a shown source keeps through: geometry panned this far lies within the
                    # geometry grown by it, so it touches every cell it touched (0 flips of 125,675 panned shapes).


_EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count("1") == 1]   # A box's 12 edges.


def _rectangle(matrix, box):
    """The clip-space rectangle ``((left, right), (bottom, top))``, -1 to 1, of world box ``box`` through perspective
    ``matrix``, never clipped to the picture: its corners projected, the part behind the near plane cut away along its
    edges; None when it lies wholly behind the view."""
    if not all(math.isfinite(value) for corner in box for value in corner):
        return None   # A box that is no box.
    rows = [tuple(row) for row in matrix]
    corners = []
    for index in range(8):
        point = [box[(index >> axis) & 1][axis] for axis in range(3)] + [1.0]
        clip = [sum(row[i] * point[i] for i in range(4)) for row in rows]
        corners.append((clip, clip[2] + clip[3]))   # Second: how far in front of the near plane (z >= -w).
    points = [clip for clip, ahead in corners if ahead >= 0]
    for a, b in _EDGES:
        (clip_a, ahead_a), (clip_b, ahead_b) = corners[a], corners[b]
        if (ahead_a < 0) != (ahead_b < 0):
            t = ahead_a / (ahead_a - ahead_b)
            points.append([clip_a[i] + t * (clip_b[i] - clip_a[i]) for i in range(4)])
    flat = [(clip[0] / clip[3], clip[1] / clip[3]) for clip in points if clip[3] > 1e-12]
    if not flat:
        return None
    return [(min(p[axis] for p in flat), max(p[axis] for p in flat)) for axis in (0, 1)]


def coverage(matrix, box):
    """The share of a picture (0 to 1) that world box ``box`` covers through perspective ``matrix`` (clip space): the
    area of its rectangle (``_rectangle``) clipped to the picture. Exact for a box seen from outside, generous for a
    thin one seen edge-on; 0 when it lies wholly behind the view."""
    found = _rectangle(matrix, box)
    spans = [max(0.0, min(1.0, high) - max(-1.0, low)) for low, high in found or ((0.0, 0.0), (0.0, 0.0))]
    return spans[0] * spans[1] / 4.0


def fill(mask, grow=0):
    """How much of the picture the geometry fills, as the Main tile measures it (R3-M2; the maintainer's choice of
    measure lives here alone, "Count where it is", 2026-10-25): the share of the ``CELLS`` grid's cells that hold any
    geometry pixel, the cells laid over the whole picture by proportion (a pixel lies in the cell its centre lies in:
    column ``x`` of ``width`` in cell ``(2x + 1) * 16 // (2 * width)``, the same as ``x // 16`` on the 256 px tile),
    so any picture size keeps the meaning. ``mask``: rows of booleans, True where a pixel shows geometry (a 2-D numpy
    array or nested sequences). Its upper bound from a box alone, ``fills_at_most``, lives beside it and changes with
    it: the pre-test that rules a candidate out without a picture. ``grow``: tile pixels the geometry is grown by
    first (``KEEP_PX``, scaled to the picture's width), the translation-stable count a shown source keeps by."""
    across, down = CELLS
    if grow:
        mask = _grown(mask, grow)
    if hasattr(mask, "nonzero"):
        import numpy
        height, width = mask.shape
        ys, xs = numpy.nonzero(mask)
        occupied = len(numpy.unique((2 * ys + 1) * down // (2 * height) * across + (2 * xs + 1) * across // (
            2 * width))) if len(xs) else 0
    else:
        height, width = len(mask), len(mask[0]) if mask else 0
        occupied = len({((2 * y + 1) * down // (2 * height), (2 * x + 1) * across // (2 * width))
                        for y, row in enumerate(mask) for x, shown in enumerate(row) if shown})
    return occupied / float(across * down)


def _grown(mask, grow):
    """``mask`` grown by ``grow`` tile pixels (a disc), as rows of booleans or a 2-D numpy array."""
    try:
        import numpy
        mask = numpy.asarray(mask, dtype=bool)
    except ImportError:
        mask = [list(map(bool, row)) for row in mask]
        height, width = len(mask), len(mask[0]) if mask else 0
        radius = max(1, round(grow * width / 256))
        shown = {(y, x) for y, row in enumerate(mask) for x, value in enumerate(row) if value}
        out = [[False] * width for _ in range(height)]
        for y, x in shown:
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= radius * radius + radius and 0 <= y + dy < height and 0 <= x + dx < width:
                        out[y + dy][x + dx] = True
        return out
    height, width = mask.shape
    radius = max(1, round(grow * width / 256))
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius + radius:
                continue
            out[max(0, dy):height + min(0, dy), max(0, dx):width + min(0, dx)] |= \
                mask[max(0, -dy):height + min(0, -dy), max(0, -dx):width + min(0, -dx)]
    return out


def fills_at_most(matrix, box, size):
    """The most ``fill`` can give for what world box ``box`` holds, drawn through ``matrix`` into a picture of
    ``size``: the share of the ``CELLS`` that the box's rectangle touches where it lies, widened by two pixels each
    side (a pixel is drawn where its centre is covered, one more for an anti-aliased edge), then clipped to the
    picture. Every geometry pixel lies inside it, so a candidate below ``FILL`` here is below it in any picture. Its
    area (``coverage``) could not rule out: cells are coarser than pixels, so a 40 x 25 px rectangle (under a
    sixteenth of the tile) across cell lines touches 4 x 3 = 12 cells."""
    found = _rectangle(matrix, box)
    if found is None:
        return 0.0
    counts = []
    for (low, high), pixels, cells in zip(found, size, CELLS):
        first = max(0, math.floor((low + 1.0) / 2.0 * pixels - 2.0))
        last = min(pixels - 1, math.floor((high + 1.0) / 2.0 * pixels + 2.0))
        counts.append((2 * last + 1) * cells // (2 * pixels) - (2 * first + 1) * cells // (2 * pixels) + 1
                      if last >= first else 0)
    return counts[0] * counts[1] / float(CELLS[0] * CELLS[1])


def perspective(fov, aspect, near, far):
    from mathutils import Matrix
    f = 1.0 / math.tan(fov / 2)
    return Matrix(((f / aspect, 0, 0, 0), (0, f, 0, 0),
                   (0, 0, (far + near) / (near - far), 2 * far * near / (near - far)), (0, 0, -1, 0)))


def fitted(projection, frame_aspect, aspect):
    """``projection`` (a frame of ``frame_aspect``, width over height, mapped to the whole view) for a picture of
    ``aspect``: the whole frame fits, centred, and what lies beside it fills the rest (a wider frame keeps its width,
    a taller one its height), as Blender shows a camera's frame inside a view of another shape."""
    projection = projection.copy()
    row = 1 if frame_aspect > aspect else 0
    scale = aspect / frame_aspect if row else frame_aspect / aspect
    projection[row] = [value * scale for value in projection[row]]
    return projection
