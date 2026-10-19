# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed ``curve_counts`` predicate: points and curves of curve-like data, characters and lines of text.

Counts what the stroke, hair-curve, curve and text operators of
``operator_run`` change and no other check reads:

* Grease Pencil: the stroke points and strokes of every layer's drawing shown
  at the current frame (the drawings those operators edit);
* hair curves: their points and curves;
* curves: control points (Bezier and NURBS) and splines;
* text: characters and lines of the body (a line break counts once);
* lattices: their points.

``positions`` states a curve's only spline's control points (local, in order;
Bezier points without their handles), which ``curve_points`` derives.

Each stated count is an inclusive range; a count the target's type does not
have fails with ``applies: False``. Read-only; no evaluation.
"""

COUNTS = ("points", "curves", "characters", "lines")


def _grease_pencil(data, reader):
    points = curves = 0
    for layer in data.layers:
        reader.spend(1)
        frame = layer.current_frame()
        drawing = frame.drawing if frame is not None else None
        if drawing is None:
            continue
        position = drawing.attributes.get("position")
        points += len(position.data) if position is not None else 0
        curves += max(len(drawing.curve_offsets) - 1, 0)
    return {"points": points, "curves": curves}


def counts(obj, reader):
    """The counts ``obj`` has (only those its type has), read from its original data."""
    data = obj.data
    if obj.type == "GREASEPENCIL":
        return _grease_pencil(data, reader)
    if obj.type == "CURVES":
        return {"points": len(data.points), "curves": len(data.curves)}
    if obj.type == "CURVE":
        reader.spend(len(data.splines))
        return {"points": sum(len(spline.bezier_points) + len(spline.points) for spline in data.splines),
                "curves": len(data.splines)}
    if obj.type == "FONT":
        body = data.body
        return {"characters": len(body), "lines": body.count("\n") + 1}
    if obj.type == "LATTICE":
        return {"points": len(data.points)}
    return {}


def positions(obj, reader):
    """The local points of a curve's only spline, in order (Bezier control points without handles); else None."""
    if obj.type != "CURVE" or len(obj.data.splines) != 1:
        return None
    spline = obj.data.splines[0]
    points = spline.bezier_points if spline.type == "BEZIER" else spline.points
    reader.spend(len(points))
    return [[float(value) for value in tuple(point.co)[:3]] for point in points]


def evaluate_curve_counts(reader, obj, check):
    """Return (passed, bounded metrics) for one canonical curve_counts check."""
    from .validation_reader import values_near
    found = counts(obj, reader)
    stated = [name for name in COUNTS if name in check]
    if any(name not in found for name in stated):
        return False, {**found, "applies": False, "type": obj.type}
    passed = all(check[name]["minimum"] <= found[name] <= check[name]["maximum"] for name in stated)
    observed = {**found, "scope": "ORIGINAL_DATA_CURRENT_FRAME" if obj.type == "GREASEPENCIL" else "ORIGINAL_DATA"}
    if "positions" in check:
        points = positions(obj, reader)
        if points is None:
            return False, {**observed, "positions_apply": False}
        passed = passed and values_near(points, check["positions"], check.get("tolerance"))
        observed["positions"] = points[:16]
    return passed, observed
