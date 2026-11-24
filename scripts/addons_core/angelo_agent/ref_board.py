# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reference board: a character's drawings (front, side, back, top or any named view), each with the camera it
was solved to, as data. NumPy only, never ``bpy``: built on the main thread from the scene's board and the images'
pixels, then read on any thread (``ref_fit``, hair fitting) without locks, since nothing changes it after it is made.

A view's camera is the dict ``view_silhouette.draw`` and ``view_outline.unproject`` take (``view_matrix``,
``projection_matrix``, ``details``), so a silhouette drawn at ``camera(view_id)`` and ``size(view_id)`` lines up with
the drawing pixel for pixel. ``mask`` is the drawing filled (its alpha when it is a cut-out, else its subject against
the plain background, ``view_outline.subject_mask``); ``strokes`` is the drawing's lines before any fill (darkness
against the ground, 0..1), which tells a hair's flow and a mirrored fringe apart where a filled outline can't.
``to_dict`` is the board without pixels: images are named by project path and sha256, never kept in it.
doc/angelo/reference_fit.rst is the contract.
"""

import copy
import re

import numpy

from . import view_outline

SCHEMA = 1
VIEW_ID = re.compile(r"^[a-z0-9_-]{1,32}$")
LANDMARK = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_LANDMARKS = 64
THRESHOLDS = ("iou_min", "p95_mm_max")
ALPHA_SUBJECT = 128   # A cut-out's pixel is the subject from this alpha up.


class BoardError(ValueError):
    """A board that can't be used; the message names the field."""


def _number(value, field, minimum=0.0, inclusive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise BoardError(field + " must be a number")
    if value < minimum or (not inclusive and value == minimum):
        raise BoardError("%s must be %s %s" % (field, "at least" if inclusive else "more than", minimum))
    return float(value)


def _thresholds(raw, field):
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - set(THRESHOLDS):
        raise BoardError(field + " may hold only " + ", ".join(THRESHOLDS))
    out = {}
    for name, value in raw.items():
        if value is not None:
            limit = _number(value, "%s.%s" % (field, name))
            if name == "iou_min" and limit > 1.0:
                raise BoardError(field + ".iou_min must be at most 1")
            out[name] = limit
        else:
            out[name] = None
    return out


def _camera(raw, field):
    if not isinstance(raw, dict):
        raise BoardError(field + " must be an object")
    for key in ("view_matrix", "projection_matrix"):
        matrix = numpy.asarray(raw.get(key), dtype=object)
        if matrix.shape != (4, 4):
            raise BoardError("%s.%s must be a 4x4 matrix" % (field, key))
        try:
            values = numpy.asarray(raw[key], dtype=numpy.float64)
        except (TypeError, ValueError):
            raise BoardError("%s.%s must hold numbers" % (field, key)) from None
        if not numpy.isfinite(values).all():
            raise BoardError("%s.%s must hold finite numbers" % (field, key))
    if not isinstance(raw.get("details", {}), dict):
        raise BoardError(field + ".details must be an object")
    return {"view_matrix": [[float(v) for v in row] for row in raw["view_matrix"]],
            "projection_matrix": [[float(v) for v in row] for row in raw["projection_matrix"]],
            "details": copy.deepcopy(raw.get("details", {}))}


def _view(raw, index):
    field = "views[%d]" % index
    if not isinstance(raw, dict):
        raise BoardError(field + " must be an object")
    view_id = raw.get("id")
    if not isinstance(view_id, str) or not VIEW_ID.match(view_id):
        raise BoardError(field + ".id must be 1-32 lowercase letters, digits, - or _")
    size = raw.get("size")
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 16384 for v in size)):
        raise BoardError(field + ".size must be [width, height] in pixels")
    view = {"id": view_id, "size": [int(size[0]), int(size[1])], "camera": _camera(raw.get("camera"), field + ".camera")}
    image = raw.get("image")
    if image is not None:
        if (not isinstance(image, dict) or not isinstance(image.get("path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(image.get("sha256", "")))):
            raise BoardError(field + ".image needs a path and a sha256")
        view["image"] = {"path": image["path"], "sha256": image["sha256"]}
    if raw.get("thresholds") is not None:
        view["thresholds"] = _thresholds(raw["thresholds"], field + ".thresholds")
    if raw.get("landmarks") is not None:
        view["landmarks"] = _landmarks(raw["landmarks"], field + ".landmarks", view["size"])
    return view


def _landmarks(raw, field, size):
    """Named points on the drawing, in pixels (column, row; top left is 0, 0), inside the picture."""
    if not isinstance(raw, dict) or len(raw) > MAX_LANDMARKS:
        raise BoardError("%s must be an object of at most %d names" % (field, MAX_LANDMARKS))
    out = {}
    for name, point in raw.items():
        if not isinstance(name, str) or not LANDMARK.match(name):
            raise BoardError(field + ": a name is 1-40 of a-z 0-9 _")
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)
                or not (0 <= point[0] <= size[0] and 0 <= point[1] <= size[1])):
            raise BoardError("%s.%s must be [column, row] inside the picture" % (field, name))
        out[name] = [float(point[0]), float(point[1])]
    return out


def _rgb_over_white(pixels):
    """(RGB float 0..1, alpha bool or None) of an RGB or RGBA 0-255 picture, a cut-out laid on white."""
    array = numpy.asarray(pixels)
    rgb = array[..., :3].astype(numpy.float32) / 255.0
    if array.shape[-1] < 4:
        return rgb, None
    alpha = array[..., 3].astype(numpy.float32) / 255.0
    return rgb * alpha[..., None] + (1.0 - alpha[..., None]), array[..., 3] >= ALPHA_SUBJECT


class Board:
    """A validated board and its views' pixels. Read-only once made."""

    def __init__(self, data, images):
        self._data = data
        self._views = {view["id"]: view for view in data["views"]}
        self._images = {}
        for view_id, pixels in (images or {}).items():
            if view_id not in self._views:
                continue
            array = numpy.asarray(pixels)
            width, height = self._views[view_id]["size"]
            if array.ndim != 3 or array.shape[:2] != (height, width) or array.shape[2] not in (3, 4):
                raise BoardError("the picture of view %s must be %dx%d RGB or RGBA" % (view_id, width, height))
            self._images[view_id] = array
        # Each view's normalized outline, made on first use. A board never changes, so a value is final; two threads
        # asking at once may both make it, and either result is the same.
        self._outlines = {}

    @classmethod
    def from_dict(cls, data, images=None):
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise BoardError("schema must be %d" % SCHEMA)
        unit_mm = _number(data.get("unit_mm", 1000.0), "unit_mm", 0.0, inclusive=False)
        views = data.get("views", [])
        if not isinstance(views, list):
            raise BoardError("views must be a list")
        clean = [_view(raw, index) for index, raw in enumerate(views)]
        ids = [view["id"] for view in clean]
        if len(set(ids)) != len(ids):
            raise BoardError("views: each id once")
        return cls({"schema": SCHEMA, "unit_mm": unit_mm,
                    "defaults": _thresholds(data.get("defaults") or {}, "defaults"), "views": clean}, images)

    def to_dict(self):
        return copy.deepcopy(self._data)

    @property
    def unit_mm(self):
        """Millimetres in one world unit (the scene's unit scale x 1000)."""
        return self._data["unit_mm"]

    def view_ids(self):
        return [view["id"] for view in self._data["views"]]

    def camera(self, view_id):
        return copy.deepcopy(self._views[view_id]["camera"])

    def size(self, view_id):
        width, height = self._views[view_id]["size"]
        return width, height

    def thresholds(self, view_id):
        merged = {name: None for name in THRESHOLDS}
        merged.update(self._data["defaults"])
        merged.update(self._views[view_id].get("thresholds", {}))
        return merged

    def mask(self, view_id):
        """The drawing filled, as drawn (``view_outline.normalized`` makes the outline both sides are compared by),
        or None when the view has no picture."""
        self._views[view_id]   # KeyError for an unknown view.
        pixels = self._images.get(view_id)
        if pixels is None:
            return None
        if pixels.shape[2] == 4:
            return pixels[..., 3] >= ALPHA_SUBJECT
        return view_outline.subject_mask(pixels)[0]

    def landmarks(self, view_id):
        """{name: (column, row)} in pixels, or {} when the view has none."""
        return {name: (point[0], point[1]) for name, point in self._views[view_id].get("landmarks", {}).items()}

    def reference_outline(self, view_id):
        """``view_outline.normalized(mask(view_id))``, made once per view and read-only (shared by threads), or None
        when the view has no picture. ``ref_fit`` compares against it, so a fit that scores many times pays once."""
        if view_id in self._outlines:
            return self._outlines[view_id]
        mask = self.mask(view_id)
        outline = None if mask is None else view_outline.normalized(mask)
        if outline is not None:
            outline = outline.copy()
            outline.flags.writeable = False
        self._outlines[view_id] = outline
        return outline

    def strokes(self, view_id):
        """The drawing's lines, 0 (ground) to 1 (black), float32: how much darker each pixel is than the ground (a
        cut-out is laid on white first), or None when the view has no picture."""
        self._views[view_id]
        pixels = self._images.get(view_id)
        if pixels is None:
            return None
        rgb, _alpha = _rgb_over_white(pixels)
        luminance = rgb @ numpy.array((0.2126, 0.7152, 0.0722), dtype=numpy.float32)
        border = numpy.concatenate((luminance[0], luminance[-1], luminance[:, 0], luminance[:, -1]))
        ground = max(float(numpy.median(border)), 1e-3)
        return numpy.clip((ground - luminance) / ground, 0.0, 1.0).astype(numpy.float32)
