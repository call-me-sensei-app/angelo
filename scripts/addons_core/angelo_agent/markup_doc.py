# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture's markup: the person's marks and comments on an attached picture, in that picture's pixels (2.1.0).

doc/angelo/attachment_markup.rst. The viewer (``markup_overlay``) turns the person's drags into these commands; what
reaches Angelo's agent is built from the document by ``markup_message``. No Blender here.

* **Coordinates** are the original picture's pixels, origin top-left, y down: the same at any zoom, in the drawing
  sent to the model and in a reopened draft. Every mark is kept inside the picture.
* **Marks**: ``pen`` (``points``), ``arrow`` (``start``, ``end``), ``ellipse`` and ``rect`` (``box`` = x, y, w, h),
  ``text`` (``at``, its top-left, and ``text``). Each has a named ``colour`` (``COLOURS``) and a ``width`` level
  (``WIDTHS``); ``markup_message`` turns a level into pixels for the picture's size.
* **Comments** are a point (``at``) or a region (``box``) with the person's note. Their number is their place in the
  list, so removing one renumbers the rest, as the pins drawn on the picture do.
* **Undo** is the document's own stack of inverse edits (``MAX_HISTORY``), never Blender's: marking up a picture
  never touches the scene's history. A command that changes nothing is not a step. The instructions field is a text
  field with its own editing, not on this stack.
* **Bounds**: ``MAX_MARKS``, ``MAX_COMMENTS``, ``MAX_POINTS`` per stroke (after simplification), text lengths. A
  draft read back from disk is checked whole (``from_dict``); a damaged one is refused, never half-loaded.
"""

import copy
import math
import uuid

SCHEMA = 1
KINDS = ("pen", "arrow", "ellipse", "rect", "text")
# Named colours, red first (the reference's default). A colour's name goes to the model and into its tooltip, so
# colour is never the only cue. RGBA in 0..1.
COLOURS = {"red": (0.93, 0.26, 0.21, 1.0), "amber": (1.0, 0.72, 0.18, 1.0), "teal": (0.15, 0.74, 0.75, 1.0),
           "blue": (0.27, 0.52, 0.96, 1.0), "white": (1.0, 1.0, 1.0, 1.0), "black": (0.05, 0.05, 0.05, 1.0)}
DEFAULT_COLOUR = "red"
WIDTHS = (1, 2, 3)
DEFAULT_WIDTH = 2
MAX_MARKS = 200
MAX_COMMENTS = 20
MAX_POINTS = 2000
MAX_TEXT = 500
MAX_COMMENT = 1000
MAX_INSTRUCTIONS = 4000
MAX_HISTORY = 200
MAX_SIDE = 16384
SIMPLIFY_PX = 0.75
TEXT_PX = 24          # A text mark's height at a picture whose long edge is 1024 px or less (``text_box``).
TEXT_ADVANCE = 0.6    # An estimate of a glyph's width, in heights: hit testing only, never drawing.


def reference_id(value):
    """The canonical UUID a picture's reference id must be (``ReferenceStore``), else ValueError."""
    try:
        canonical = str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Not a picture's reference id") from None
    if canonical != value:
        raise ValueError("Not a picture's reference id")
    return canonical


def scale_of(size):
    """How many picture pixels one level of width or text height is worth: 1 up to a 1024 px long edge."""
    return max(1.0, max(size) / 1024.0)


def text_box(mark, size):
    """A text mark's box (estimated, for hits): its top-left ``at``, one line high."""
    height = TEXT_PX * scale_of(size)
    return [mark["at"][0], mark["at"][1], TEXT_ADVANCE * height * max(1, len(mark["text"])), height * 1.25]


def simplify(points, tolerance=SIMPLIFY_PX):
    """A stroke within ``tolerance`` of itself with as few points as practical, in bounded time.

    Ramer–Douglas–Peucker is quadratic on a stroke whose farthest point keeps landing beside an end (a scribble), so
    a long stroke is simplified in ``CHUNK``-point pieces (their joins kept) and then whole once the result is short:
    at most ``len(points) * CHUNK`` distance checks, whatever the person drew."""
    if len(points) <= CHUNK:
        return _rdp(points, tolerance)
    pieces = []
    for start in range(0, len(points) - 1, CHUNK - 1):
        piece = _rdp(points[start:start + CHUNK], tolerance)
        pieces.extend(piece if not pieces else piece[1:])
    return _rdp(pieces, tolerance) if len(pieces) <= CHUNK else pieces


CHUNK = 256


def _rdp(points, tolerance):
    """Ramer–Douglas–Peucker without recursion."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (ax, ay), (bx, by) = points[first], points[last]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        far, index = -1.0, None
        for i in range(first + 1, last):
            px, py = points[i]
            gap = (abs(dy * px - dx * py + bx * ay - by * ax) / length) if length else math.hypot(px - ax, py - ay)
            if gap > far:
                far, index = gap, i
        if index is not None and far > tolerance:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return [point for point, kept in zip(points, keep) if kept]


def _cap(points, limit=MAX_POINTS):
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def _number(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("A position must be a number")
    return round(value, 1)


def _point(value, size):
    x, y = value
    return [min(max(_number(x), 0.0), float(size[0])), min(max(_number(y), 0.0), float(size[1]))]


def _box(value, size):
    """x, y, w, h with a drag written backwards turned round, clipped to the picture; None when it has no area."""
    x, y, w, h = (_number(v) for v in value)
    x0, x1 = sorted((x, x + w))
    y0, y1 = sorted((y, y + h))
    (x0, y0), (x1, y1) = _point((x0, y0), size), _point((x1, y1), size)
    return [x0, y0, round(x1 - x0, 1), round(y1 - y0, 1)] if x1 > x0 and y1 > y0 else None


def _clean(text, limit):
    text = str(text or "")
    return text[:limit]


def _style(colour, width):
    if colour not in COLOURS:
        raise ValueError("Unknown markup colour %r" % (colour,))
    if width not in WIDTHS:
        raise ValueError("Unknown markup width %r" % (width,))
    return colour, width


def normal_mark(kind, size, fields, *, drawn=True):
    """A mark's stored form from a tool's fields, or None when it would show nothing (an empty stroke or box). A
    stroke just ``drawn`` is simplified; one read back from a draft is only checked, so a draft reads back exactly."""
    if kind not in KINDS:
        raise ValueError("Unknown markup tool %r" % (kind,))
    colour, width = _style(fields.get("colour", DEFAULT_COLOUR), fields.get("width", DEFAULT_WIDTH))
    mark = {"kind": kind, "colour": colour, "width": width}
    if kind == "pen":
        points = [_point(p, size) for p in fields.get("points") or ()]
        if not points:
            return None
        mark["points"] = _cap(simplify(points) if drawn else points)
    elif kind == "arrow":
        start, end = _point(fields["start"], size), _point(fields["end"], size)
        if start == end:
            return None
        mark["start"], mark["end"] = start, end
    elif kind in ("ellipse", "rect"):
        box = _box(fields["box"], size)
        if box is None:
            return None
        mark["box"] = box
    else:
        text = _clean(fields.get("text"), MAX_TEXT)
        if not text.strip():
            return None
        mark["at"], mark["text"] = _point(fields["at"], size), text
    return mark


def normal_comment(size, fields):
    at, box = fields.get("at"), fields.get("box")
    if (at is None) == (box is None):
        raise ValueError("A comment is at a point or on a region, one of them")
    comment = {"at": _point(at, size)} if at is not None else {"box": _box(box, size)}
    if comment.get("box", True) is None:
        raise ValueError("A comment's region has no area")
    comment["text"] = _clean(fields.get("text"), MAX_COMMENT)
    return comment


def _distance_to_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = 0.0 if not (dx or dy) else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


class MarkupDoc:
    def __init__(self, image, size):
        self.image = reference_id(image)
        w, h = (int(v) for v in size)
        if not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
            raise ValueError("A picture's size must be 1 to %d pixels a side" % MAX_SIDE)
        self.size = (w, h)
        self.marks, self.comments = [], []
        self.instructions = ""
        self.next_id = 1
        self._undo, self._redo = [], []

    # -- reading ---------------------------------------------------------------------------------------------------

    def _find(self, identity):
        for kind in ("marks", "comments"):
            for index, item in enumerate(getattr(self, kind)):
                if item["id"] == identity:
                    return kind, index, item
        raise KeyError(identity)

    def mark(self, identity):
        kind, _index, item = self._find(identity)
        if kind != "marks":
            raise KeyError(identity)
        return item

    def comment(self, identity):
        kind, _index, item = self._find(identity)
        if kind != "comments":
            raise KeyError(identity)
        return item

    def number(self, identity):
        """A comment's number as drawn on its pin: its place in the list, from 1."""
        kind, index, _item = self._find(identity)
        if kind != "comments":
            raise KeyError(identity)
        return index + 1

    @property
    def empty(self):
        return not self.marks and not self.comments and not self.instructions.strip()

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    @property
    def history_size(self):
        return len(self._undo)

    def hit(self, point, tolerance):
        """The topmost mark under ``point`` (picture pixels): on a stroke, an arrow or a shape's outline within
        ``tolerance``, or anywhere on a text's words. None elsewhere. The eraser and the select tool use it."""
        for mark in reversed(self.marks):
            if self._hits(mark, point, tolerance):
                return mark["id"]
        return None

    def _hits(self, mark, point, tolerance):
        kind = mark["kind"]
        if kind == "pen":
            points = mark["points"]
            if len(points) == 1:
                return math.hypot(point[0] - points[0][0], point[1] - points[0][1]) <= tolerance
            return any(_distance_to_segment(point, a, b) <= tolerance for a, b in zip(points, points[1:]))
        if kind == "arrow":
            return _distance_to_segment(point, mark["start"], mark["end"]) <= tolerance
        if kind == "text":
            x, y, w, h = text_box(mark, self.size)
            return x <= point[0] <= x + w and y <= point[1] <= y + h
        x, y, w, h = mark["box"]
        if kind == "rect":
            inside = x - tolerance <= point[0] <= x + w + tolerance and y - tolerance <= point[1] <= y + h + tolerance
            deep = x + tolerance < point[0] < x + w - tolerance and y + tolerance < point[1] < y + h - tolerance
            return inside and not deep
        rx, ry = w / 2.0, h / 2.0
        cx, cy = x + rx, y + ry
        # Distance to the outline, estimated along the ray from the centre (exact on a circle, close on an ellipse).
        angle = math.atan2((point[1] - cy) / ry, (point[0] - cx) / rx)
        ex, ey = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
        return math.hypot(point[0] - ex, point[1] - ey) <= tolerance

    # -- commands (each one undo step) -----------------------------------------------------------------------------

    def add_mark(self, kind, **fields):
        """Add a mark from a tool's fields; its id, or None when it would show nothing (no step then)."""
        mark = normal_mark(kind, self.size, fields)
        if mark is None:
            return None
        if len(self.marks) >= MAX_MARKS:
            raise ValueError("A picture takes at most %d marks" % MAX_MARKS)
        mark = {"id": self._new_id("m"), **mark}
        self._do(("insert", "marks", len(self.marks), mark))
        return mark["id"]

    def add_comment(self, *, at=None, box=None, text=""):
        if len(self.comments) >= MAX_COMMENTS:
            raise ValueError("A picture takes at most %d comments" % MAX_COMMENTS)
        comment = {"id": self._new_id("c"), **normal_comment(self.size, {"at": at, "box": box, "text": text})}
        self._do(("insert", "comments", len(self.comments), comment))
        return comment["id"]

    def remove(self, identity):
        kind, index, _item = self._find(identity)
        self._do(("delete", kind, index))

    def move(self, identity, dx, dy):
        """Move a mark or comment by (dx, dy) picture pixels, whole, stopping at the picture's edges."""
        kind, index, item = self._find(identity)
        xs, ys = self._extent(item)
        dx = min(max(_number(dx), -min(xs)), self.size[0] - max(xs))
        dy = min(max(_number(dy), -min(ys)), self.size[1] - max(ys))
        if not (dx or dy):
            return
        moved = copy.deepcopy(item)
        for key in ("points",):
            if key in moved:
                moved[key] = [[round(x + dx, 1), round(y + dy, 1)] for x, y in moved[key]]
        for key in ("start", "end", "at"):
            if key in moved:
                moved[key] = [round(moved[key][0] + dx, 1), round(moved[key][1] + dy, 1)]
        if "box" in moved:
            moved["box"] = [round(moved["box"][0] + dx, 1), round(moved["box"][1] + dy, 1)] + moved["box"][2:]
        self._do(("replace", kind, index, moved))

    def restyle(self, identity, *, colour=None, width=None):
        kind, index, item = self._find(identity)
        if kind != "marks":
            raise KeyError(identity)
        colour, width = _style(colour or item["colour"], width or item["width"])
        if (colour, width) != (item["colour"], item["width"]):
            self._do(("replace", kind, index, {**item, "colour": colour, "width": width}))

    def set_text(self, identity, text):
        """A comment's note or a text mark's words. A text mark left blank is removed (it would show nothing)."""
        kind, index, item = self._find(identity)
        if kind == "marks" and item["kind"] != "text":
            raise KeyError(identity)
        text = _clean(text, MAX_COMMENT if kind == "comments" else MAX_TEXT)
        if kind == "marks" and not text.strip():
            self._do(("delete", kind, index))
        elif text != item["text"]:
            self._do(("replace", kind, index, {**item, "text": text}))

    def retract(self):
        """Take back the last step for good, with no redo: a pin the person abandoned before writing its note."""
        if self._undo:
            self._apply(self._undo.pop())

    def undo(self):
        if self._undo:
            self._redo.append(self._apply(self._undo.pop()))

    def redo(self):
        if self._redo:
            self._undo.append(self._apply(self._redo.pop()))

    # -- the stack -------------------------------------------------------------------------------------------------

    def _new_id(self, prefix):
        identity = "%s%d" % (prefix, self.next_id)
        self.next_id += 1
        return identity

    def _extent(self, item):
        if "points" in item:
            points = item["points"]
        elif "box" in item:
            x, y, w, h = item["box"]
            points = [[x, y], [x + w, y + h]]
        elif "start" in item:
            points = [item["start"], item["end"]]
        else:   # A point comment, or a text mark: its anchor stays inside.
            points = [item["at"]]
        return [p[0] for p in points], [p[1] for p in points]

    def _do(self, edit):
        self._undo.append(self._apply(edit))
        del self._undo[:-MAX_HISTORY]
        self._redo.clear()

    def _apply(self, edit):
        """Apply one edit; its inverse."""
        op, kind, index = edit[:3]
        items = getattr(self, kind)
        if op == "insert":
            items.insert(index, edit[3])
            return ("delete", kind, index)
        if op == "delete":
            return ("insert", kind, index, items.pop(index))
        old, items[index] = items[index], edit[3]
        return ("replace", kind, index, old)

    # -- saving ----------------------------------------------------------------------------------------------------

    def to_dict(self):
        return {"schema": SCHEMA, "image": self.image, "size": list(self.size), "marks": copy.deepcopy(self.marks),
                "comments": copy.deepcopy(self.comments), "instructions": self.instructions,
                "next_id": self.next_id}

    @classmethod
    def from_dict(cls, data):
        """A saved draft, checked whole; ValueError for anything damaged or foreign (never half-loaded)."""
        try:
            if data.get("schema") != SCHEMA:
                raise ValueError("A markup draft of another version")
            doc = cls(data["image"], data["size"])
            marks, comments = data.get("marks", []), data.get("comments", [])
            if not isinstance(marks, list) or not isinstance(comments, list):
                raise ValueError("A damaged markup draft")
            if len(marks) > MAX_MARKS or len(comments) > MAX_COMMENTS:
                raise ValueError("A markup draft over its bounds")
            seen = set()
            for item in marks:
                mark = normal_mark(item["kind"], doc.size, item, drawn=False)
                if mark is None:
                    raise ValueError("A markup draft with an empty mark")
                doc.marks.append({"id": str(item["id"]), **mark})
                seen.add(str(item["id"]))
            for item in comments:
                doc.comments.append({"id": str(item["id"]), **normal_comment(doc.size, item)})
                seen.add(str(item["id"]))
            if len(seen) != len(doc.marks) + len(doc.comments):
                raise ValueError("A markup draft with a repeated id")
            doc.instructions = _clean(data.get("instructions", ""), MAX_INSTRUCTIONS)
            doc.next_id = max(int(data.get("next_id", 1)), 1 + len(seen))
            return doc
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("A damaged markup draft") from error
