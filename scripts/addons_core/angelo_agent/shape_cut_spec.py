# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut's one description of a cut and the solids it makes. No Blender here.

The hand tool (``shape_cut_tool``), the agent's ``shape_cut`` operation and tests all describe a cut with the same
dict, checked by ``validate`` and turned into solids by ``solids``, so a cut drawn by hand and one asked for in chat
take one code path. Lengths are metres in the surface frame (``shape_cut_geometry.Frame``): ``center`` is where the
shape sits, ``rotation`` turns it in the surface's plane. doc/angelo/shape_cut.rst is the contract.
"""

import math

from . import shape_cut_geometry as geo

SHAPES = ("BOX", "CIRCLE", "POLYGON")
OPERATIONS = ("CUT", "UNION", "INTERSECT", "SLICE", "INSET", "PANEL")
THROUGH = "THROUGH"
MAX_COPIES = 400        # An array's copies at most (20 x 20), each a Boolean operand's island.
MAX_LENGTH = 1000.0     # Metres; anything larger is a unit mistake.
MIN_LENGTH = 1e-5
LIFT = 0.05             # A cutter's top sits this fraction of its size above the surface (never coplanar).
EMBED = 0.001           # An added solid sinks this fraction of its size into the surface, so it fuses.
SEAM = 0.0005           # An inset's cup grows this fraction of its size past the opening, so it overlaps.
_KEYS = frozenset({"shape", "size", "radius", "segments", "points", "center", "rotation", "operation", "depth",
                   "width", "array"})   # Read-only, as every spec table a validator reads (read_only).


def _length(value, what, low=MIN_LENGTH):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a length in metres" % what)
    if not low <= value <= MAX_LENGTH:
        raise ValueError("%s must be %g to %g m" % (what, low, MAX_LENGTH))
    return float(value)


def _point(value, what):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) for c in value)):
        raise ValueError("%s must be [u, v] in metres" % what)
    if any(abs(c) > MAX_LENGTH for c in value):
        raise ValueError("%s must be within %g m" % (what, MAX_LENGTH))
    return (float(value[0]), float(value[1]))


def _count(value, what, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError("%s must be a whole number from %d to %d" % (what, low, high))
    return value


def validate(spec):
    """The canonical cut; ValueError naming the first thing that is wrong."""
    if not isinstance(spec, dict):
        raise ValueError("a cut must be an object")
    extra = set(spec) - _KEYS
    if extra:
        raise ValueError("a cut does not take: " + ", ".join(sorted(extra)))
    shape = spec.get("shape", "BOX")
    if shape not in SHAPES:
        raise ValueError("shape must be one of " + ", ".join(SHAPES))
    operation = spec.get("operation", "CUT")
    if operation not in OPERATIONS:
        raise ValueError("operation must be one of " + ", ".join(OPERATIONS))
    out = {"shape": shape, "operation": operation, "center": _point(spec.get("center", (0.0, 0.0)), "center"),
           "rotation": 0.0, "size": None, "radius": None, "segments": None, "points": None, "width": None,
           "array": None}
    rotation = spec.get("rotation", 0.0)
    if isinstance(rotation, bool) or not isinstance(rotation, (int, float)) or not math.isfinite(rotation):
        raise ValueError("rotation must be degrees")
    out["rotation"] = float(rotation) % 360.0
    needs = {"BOX": "size", "CIRCLE": "radius", "POLYGON": "points"}[shape]
    for key in ("size", "radius", "segments", "points"):
        if key in spec and key != needs and not (shape == "CIRCLE" and key == "segments"):
            raise ValueError("a %s does not take %s" % (shape, key))
    if needs not in spec:
        raise ValueError("a %s needs %s" % (shape, needs))
    if shape == "BOX":
        size = spec["size"]
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise ValueError("size must be [width, height] in metres")
        out["size"] = (_length(size[0], "the width"), _length(size[1], "the height"))
    elif shape == "CIRCLE":
        out["radius"] = _length(spec["radius"], "radius")
        out["segments"] = _count(spec.get("segments", 32), "segments", geo.MIN_SEGMENTS, geo.MAX_SEGMENTS)
    else:
        points = spec["points"]
        if not isinstance(points, (list, tuple)):
            raise ValueError("points must list [u, v] corners")
        out["points"] = tuple(_point(p, "each point") for p in points)
        geo.polygon(out["points"])   # Refuses a crossing, flat or too-short outline now, not at build time.
    depth = spec.get("depth", THROUGH if operation == "SLICE" else None)
    if depth is None:
        raise ValueError("%s needs depth (metres%s)" % (operation, ", or THROUGH" if operation != "UNION" else ""))
    if depth == THROUGH:
        if operation in ("UNION", "INSET", "PANEL"):
            raise ValueError("%s needs a depth in metres, not THROUGH" % operation)
        out["depth"] = THROUGH
    else:
        out["depth"] = _length(depth, "depth")
    if operation in ("INSET", "PANEL"):
        if "width" not in spec:
            raise ValueError("%s needs width (%s, metres)" % (
                operation, "the wall's thickness" if operation == "INSET" else "the groove's width"))
        out["width"] = _length(spec["width"], "width")
    elif "width" in spec:
        raise ValueError("only INSET and PANEL take width")
    if "array" in spec:
        out["array"] = _array(spec["array"])
    base = outline(out)
    if out["width"] is not None:
        geo.inset(base, out["width"])   # Too small or too sharp for the width: refused here.
    if out["array"] is not None:
        _check_spacing(base, out)
    return out


def _array(value):
    if not isinstance(value, dict) or set(value) - {"columns", "rows", "spacing"} or "spacing" not in value:
        raise ValueError("array must be {columns, rows, spacing: [u, v]}")
    columns = _count(value.get("columns", 1), "array.columns", 1, MAX_COPIES)
    rows = _count(value.get("rows", 1), "array.rows", 1, MAX_COPIES)
    if columns * rows > MAX_COPIES:
        raise ValueError("an array makes at most %d copies" % MAX_COPIES)
    spacing = _point(value["spacing"], "array.spacing")
    if any(s < 0 for s in spacing):
        raise ValueError("array.spacing must not be negative")
    return {"columns": columns, "rows": rows, "spacing": spacing}


def _check_spacing(base, spec):
    """Copies must not touch: each step must clear the shape's extent along it (copies that overlap make one cutter
    cross itself, which no solver takes as a solid)."""
    array = spec["array"]
    xs, ys = [p[0] for p in base], [p[1] for p in base]
    for count, step, extent, axis in ((array["columns"], array["spacing"][0], max(xs) - min(xs), "u"),
                                      (array["rows"], array["spacing"][1], max(ys) - min(ys), "v")):
        if count > 1 and step <= extent + MIN_LENGTH:
            raise ValueError("array copies overlap along %s: spacing %.6g m must exceed the shape's %.6g m"
                             % (axis, step, extent))


def outline(spec):
    """The shape's outline in the frame: centred on ``center`` (on the array's centre), turned by ``rotation``."""
    if spec["shape"] == "BOX":
        w, h = spec["size"]
        local = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    elif spec["shape"] == "CIRCLE":
        local = geo.circle((0.0, 0.0), spec["radius"], spec["segments"])
    else:
        local = geo.polygon(spec["points"])
    angle = math.radians(spec["rotation"])
    c, s = math.cos(angle), math.sin(angle)
    cx, cy = spec["center"]
    return geo.polygon([(cx + x * c - y * s, cy + x * s + y * c) for x, y in local])


def size_of(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def through_depth(frame, corners):
    """How deep a THROUGH cut must reach: past the farthest of the target's world bound ``corners`` below the frame."""
    below = max(-frame.to_local(corner)[2] for corner in corners)
    return max(below, 0.0)


def solids(spec, through=None):
    """The solids a cut is made of, in the frame: dicts of ``role`` (DIFFERENCE, UNION or INTERSECT on the target,
    or PIECE: INTERSECT on the slice's new object), ``verts`` and ``faces`` (closed, outward), for the array's first
    copy; ``copies`` lists every copy's offset from it. ``through`` is ``through_depth`` for a THROUGH cut."""
    base = outline(spec)
    scale = size_of(base)
    lift = LIFT * scale
    depth = spec["depth"]
    if depth == THROUGH:
        if through is None:
            raise ValueError("a THROUGH cut needs the target's depth below the surface")
        depth = through + lift
    array = spec["array"]
    copies = [(0.0, 0.0)]
    if array:
        offsets = geo.grid_offsets(array["columns"], array["rows"], *array["spacing"])
        first = offsets[0]
        base = [(x + first[0], y + first[1]) for x, y in base]
        copies = [(o[0] - first[0], o[1] - first[1]) for o in offsets]
    operation = spec["operation"]
    out = []
    if operation == "UNION":
        out.append(("UNION",) + geo.prism(base, depth, -EMBED * scale))
    elif operation == "PANEL":
        out.append(("DIFFERENCE",) + geo.ring(base, geo.inset(base, spec["width"]), lift, -depth))
    elif operation == "INSET":
        width = spec["width"]
        out.append(("DIFFERENCE",) + geo.prism(base, lift, -depth))
        out.append(("UNION",) + geo.ring(geo.outset(base, SEAM * scale), geo.inset(base, width), 0.0,
                                         -(depth + width), -depth))
    else:
        verts, faces = geo.prism(base, lift, -depth)
        if operation == "SLICE":
            out.append(("DIFFERENCE", verts, faces))
            out.append(("PIECE", verts, faces))
        else:
            out.append(("DIFFERENCE" if operation == "CUT" else "INTERSECT", verts, faces))
    return [{"role": role, "verts": verts, "faces": faces, "copies": copies} for role, verts, faces in out]
