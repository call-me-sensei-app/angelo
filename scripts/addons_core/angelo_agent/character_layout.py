# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where each generated part goes on the body: placement math for Place Character. numpy only, no Blender.

Inputs are the split's part boxes (sheet pixels in one shared figure frame, x
from the cell centre, y down from the cell top), the drawn body's silhouette
profile, and the generated meshes' vertices. Generated meshes come back at
arbitrary scale and position, so each part is scaled and moved to its box:

* **Frame.** The body's box maps to the requested height with its soles at z=0;
  one scale ``k`` (metres per sheet pixel) then maps every other box.
* **Size.** A part scales uniformly by the geometric mean of its width and
  height fit, then horizontally by how much wider or narrower the generated
  body is than the drawn one at the part's heights (``width_correction``), so a
  coat follows the body that was actually generated. Accessories keep their
  drawn size.
* **Depth.** The sheet is a front view, so depth comes from the body: a part is
  centred on the body's depth under its own footprint (a held item on the hand,
  not the torso).

The research behind this (10 characters, 2026-10-20) found sheet boxes
reliable for the body, large garments and footwear; small parts (hats, hair,
held items) drift. Measured against the character image, the sheet draws
garments about 1.5x and small parts about 2.4x too large, so a part fitted to
the character image (``parts_ground``) is placed in that image's frame instead.
"""

import numpy as np

CORRECTED_KINDS = frozenset({"INNER", "OUTER", "LOWER", "FOOTWEAR", "HAIR", "HEADWEAR"})
CORRECTION_LIMITS = (0.7, 1.4)


class Frame:
    """Sheet pixels to world metres, from the body's box: soles at z=0, the body centred at x=0."""

    def __init__(self, body_box, height):
        x0, y0, x1, y1 = body_box
        if y1 <= y0:
            raise ValueError("The body's box has no height")
        self.k = height / float(y1 - y0)
        self.cx = (x0 + x1) / 2.0
        self.sole = float(y1)

    def x(self, px):
        return (px - self.cx) * self.k

    def z(self, py):
        return (self.sole - py) * self.k

    def box(self, box):
        """(xmin, xmax, zmin, zmax) in metres."""
        x0, y0, x1, y1 = box
        return self.x(x0), self.x(x1), self.z(y1), self.z(y0)


def bounds(vertices):
    vertices = np.asarray(vertices, float)
    return vertices.min(axis=0), vertices.max(axis=0)


def uniform_scale(vertices, target):
    """The scale that fits a mesh to a target (xmin, xmax, zmin, zmax): geometric mean of width and height fits."""
    lo, hi = bounds(vertices)
    width, height = max(hi[0] - lo[0], 1e-9), max(hi[2] - lo[2], 1e-9)
    fit_w, fit_h = (target[1] - target[0]) / width, (target[3] - target[2]) / height
    return float(np.sqrt(max(fit_w, 1e-9) * max(fit_h, 1e-9)))


def body_widths(body_vertices, heights, band):
    """The generated body's x extent at each height (None where it has no vertices)."""
    v = np.asarray(body_vertices, float)
    widths = []
    for z in heights:
        sel = v[np.abs(v[:, 2] - z) <= band]
        widths.append(float(sel[:, 0].max() - sel[:, 0].min()) if len(sel) >= 3 else None)
    return widths


def width_correction(part_kind, target, frame, profile, body_vertices):
    """Horizontal scale factor: generated body width / drawn body width over the part's heights.

    ``frame`` is the sheet's frame (the profile is the sheet's drawn body); ``target`` is in metres, so it can come
    from any frame that puts the body at the same height. 1.0 for kinds that don't follow the body, or when the two
    can't be compared. Clamped to CORRECTION_LIMITS so one bad sample can't distort a part.
    """
    if part_kind not in CORRECTED_KINDS or not profile:
        return 1.0
    zmin, zmax = target[2], target[3]
    samples = [(frame.z(y), (x1 - x0) * frame.k) for y, x0, x1 in profile if zmin <= frame.z(y) <= zmax]
    if not samples:
        return 1.0
    band = max(0.01, (zmax - zmin) / 20.0)
    generated = body_widths(body_vertices, [z for z, _ in samples], band)
    ratios = [g / d for (_, d), g in zip(samples, generated) if g and d > 1e-6]
    if not ratios:
        return 1.0
    low, high = CORRECTION_LIMITS
    return float(np.clip(np.median(ratios), low, high))


def depth_centre(body_vertices, target):
    """The body's depth centre under a footprint (x range and z range), falling back to the z band."""
    v = np.asarray(body_vertices, float)
    xmin, xmax, zmin, zmax = target
    band = v[(v[:, 2] >= zmin) & (v[:, 2] <= zmax)]
    under = band[(band[:, 0] >= xmin) & (band[:, 0] <= xmax)]
    chosen = under if len(under) >= 10 else band
    if len(chosen) < 10:
        return 0.0
    return float((chosen[:, 1].min() + chosen[:, 1].max()) / 2.0)


def place(part_kind, vertices, box, frame, profile, body_vertices, sheet_frame=None):
    """(scale (sx, sy, sz), translation (tx, ty, tz)) to apply about the world origin: v' = v * scale + translation.

    ``box`` is in ``frame``'s pixels: the sheet's, or the character image's when the part was grounded there
    (``sheet_frame`` is then the sheet's, for the body profile). The mesh ends centred on its box in x and z and on
    the body's depth under it.
    """
    target = frame.box(box)
    s = uniform_scale(vertices, target)
    c = width_correction(part_kind, target, sheet_frame or frame, profile, body_vertices)
    scale = np.array([s * c, s * c, s])
    lo, hi = bounds(np.asarray(vertices, float) * scale)
    centre = np.array([(target[0] + target[1]) / 2.0, depth_centre(body_vertices, target), (target[2] + target[3]) / 2.0])
    translation = centre - (lo + hi) / 2.0
    return tuple(float(v) for v in scale), tuple(float(v) for v in translation), {"uniform": s, "width_correction": c}


def body_transform(vertices, height):
    """(scale, translation) that makes the body ``height`` tall, centred at x=y=0 with its soles at z=0."""
    lo, hi = bounds(vertices)
    s = height / max(hi[2] - lo[2], 1e-9)
    lo, hi = lo * s, hi * s
    return (s, s, s), (-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2])
