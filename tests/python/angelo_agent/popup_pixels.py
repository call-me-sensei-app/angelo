# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pixel check that an open Angelo-drawn menu is opaque: nothing of the card under it shows through.

A list menu (rows of labels on a panel) is sampled on its label-free lines: just above and below every row
boundary and in the panel's top and bottom padding, where an opaque panel shows only its own fill. Each control
of the card that the panel covers (``covered``: its rectangles) is probed on those lines, and at least ``SHARE``
of its probed pixels must be the panel's fill, the most common probed colour. A see-through panel shows the
controls' own colours there (a teal button, a segmented track, a switch): the 1.0.5 screenshot review's setup
card, where Look, "Use my Blender settings" and Continue showed through the language list (calibrated on that
capture: Continue matched at 0.05, Look at 0.02 and the switch at 0.18).

Pure NumPy over an ``(height, width, 3)`` 0-255 array whose row 0 is the window's bottom (Blender's window
coordinates); ``load`` reads a screenshot that way inside Blender.
"""

import numpy as np

TOLERANCE = 6      # 0-255 per channel: antialiased text is far off, compression is not (PNG is lossless).
SHARE = 0.97       # Of a covered control's probed pixels that must be the panel's fill.
EDGE_INSET = 14    # Points kept clear of the panel's rounded corners.


def lines(panel, row_h, pad, rows, s):
    """Label-free window rows of a list panel ``(x, y, w, h)`` (y up): 3 points off every row boundary."""
    x, y, w, h = panel
    top = y + h
    out = set()
    for row in range(rows + 1):
        boundary = top - pad - row * row_h
        for dy in (3 * s, -3 * s):
            line = int(round(boundary + dy))
            if y + 2 * s <= line <= top - 2 * s:
                out.add(line)
    return sorted(out)


def check(rgb, panel, row_h, pad, rows, s, covered):
    """``{"fill", "lines", "controls": {name: {"probed", "share"}}, "opaque"}`` for ``covered`` = [(name, rect)]."""
    height, width = rgb.shape[:2]
    x, y, w, h = panel
    x0, x1 = max(0, int(x + EDGE_INSET * s)), min(width, int(x + w - EDGE_INSET * s))
    rows_y = [line for line in lines(panel, row_h, pad, rows, s) if 0 <= line < height]
    probes = rgb[rows_y, x0:x1].reshape(-1, 3).astype(np.int32) if rows_y and x1 > x0 else np.zeros((0, 3), np.int32)
    if not len(probes):
        return {"fill": None, "lines": rows_y, "controls": {}, "opaque": False}
    colours, counts = np.unique(probes, axis=0, return_counts=True)
    fill = colours[int(np.argmax(counts))]
    controls = {}
    for name, (cx, cy, cw, ch) in covered:
        left, right = max(x0, int(cx)), min(x1, int(cx + cw))
        inside = [line for line in rows_y if cy <= line < cy + ch]
        if right <= left or not inside:
            continue
        pixels = rgb[inside, left:right].reshape(-1, 3).astype(np.int32)
        share = float(np.all(np.abs(pixels - fill) <= TOLERANCE, axis=1).mean())
        controls[name] = {"probed": int(len(pixels)), "share": round(share, 3)}
    overall = float(np.all(np.abs(probes - fill) <= TOLERANCE, axis=1).mean())
    return {"fill": [int(c) for c in fill], "lines": rows_y, "overall": round(overall, 3), "controls": controls,
            "opaque": bool(controls) and all(c["share"] >= SHARE for c in controls.values()) and overall >= SHARE}


def load(path):
    """A screenshot as ``(height, width, 3)`` 0-255, row 0 at the bottom (inside Blender)."""
    import bpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    return np.rint(pixels.reshape(height, width, 4)[:, :, :3] * 255).astype(np.uint8)
