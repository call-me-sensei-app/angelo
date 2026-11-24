# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lines a mark is drawn with, in picture pixels: one source for the viewer (``markup_draw``) and the flattened
picture (``markup_flatten``), so what the person saw is what the model gets (2.1.0, doc/angelo/attachment_markup.rst).

``lines(item, size, pending=False)`` turns a mark, a mark being drawn (``markup_tools.Session.pending``) or a comment
region into polylines ``(points, closed)``; ``width`` is a mark's line width in picture pixels. No Blender here.
"""

import math

from . import markup_doc as md, markup_message as mm

SEGMENTS = 72   # An ellipse's outline, enough to stay round on a 4K flatten.


def ellipse(box, segments=SEGMENTS):
    x, y, w, h = box
    rx, ry = w / 2.0, h / 2.0
    cx, cy = x + rx, y + ry
    return [[cx + rx * math.cos(2 * math.pi * i / segments), cy + ry * math.sin(2 * math.pi * i / segments)]
            for i in range(segments)]


def rectangle(box):
    x, y, w, h = box
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def arrow(start, end, head):
    """The shaft and the two sides of the head, the head ``head`` long at 28 degrees either side."""
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    head = min(head, 0.6 * math.hypot(end[0] - start[0], end[1] - start[1]))
    sides = [[end[0] - head * math.cos(angle + s), end[1] - head * math.sin(angle + s)] for s in (0.49, -0.49)]
    return [([list(start), list(end)], False), ([sides[0], list(end), sides[1]], False)]


def width(mark, size):
    return mm.STROKE_PX[mark["width"]] * md.scale_of(size)


def head(mark, size):
    w = width(mark, size)
    return max(4.0 * w, 12.0 * md.scale_of(size))


def box_of(start, end):
    """The box a drag from ``start`` to ``end`` spans, whichever way it went."""
    return [min(start[0], end[0]), min(start[1], end[1]), abs(end[0] - start[0]), abs(end[1] - start[1])]


def lines(item, size, offset=(0.0, 0.0)):
    """A mark's (or a pending drag's) polylines in picture pixels, moved by ``offset``; [] for a text mark."""
    dx, dy = offset
    kind = item["kind"]
    if kind == "pen":
        result = [([[x + dx, y + dy] for x, y in item["points"]], False)]
    elif kind == "arrow":
        start, end = item["start"], item["end"]
        result = arrow([start[0] + dx, start[1] + dy], [end[0] + dx, end[1] + dy], head(item, size))
    elif kind in ("ellipse", "rect", "comment"):
        box = item.get("box") or box_of(item["start"], item["end"])
        box = [box[0] + dx, box[1] + dy, box[2], box[3]]
        result = [(ellipse(box) if kind == "ellipse" else rectangle(box), True)]
    else:
        result = []
    return result


def bounds(item, size):
    """A mark's bounding box in picture pixels (the selection's outline)."""
    if item["kind"] == "text":
        return md.text_box(item, size)
    points = [p for polyline, _closed in lines(item, size) for p in polyline]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
