# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Hairstyles of the hair one-shot tests: strands grown on a ``head_designs`` head (NumPy; Blender only to build).

``strands(design, style)`` grows every strand of a style from roots on the
scalp: each step leaves the scalp along its normal at first, then follows the
style's combing direction and gravity, and is pushed back out of the head
whenever it would enter it (``head_sdf.distance``), with a clearance that
grows from the root to the tip (the hair's volume). Seeded, so the same style
always has the same strands. ``hair_object`` makes them one Blender hair
curves object.

The styles are stand-ins for what a person might ask for, not a look a
model must reproduce by itself: the graders compare a side's hair with the
ground truth's through the same rasteriser (``face_grading``).

* ``short``: a side-parted short crop; the parting on the person's left
  (+X), the top about 5 cm long and combed across, the sides and back short.
* ``long``: straight hair parted in the middle, falling past the jaw to
  about 22 cm below the eyes, over the ears and beside the cheeks.
"""

import math

import numpy

import head_sdf
import scenario_build_kit as kit

CENTRE = numpy.array((0.0, 0.012, 0.024))   # The portrait's cranium centre: every scalp ray starts here.
# The hairline as height (z) against the bearing from the face (degrees; 0 at the front, 180 at the nape).
HAIRLINE = ((0.0, 0.066), (20.0, 0.064), (40.0, 0.056), (55.0, 0.042), (70.0, 0.030), (90.0, 0.030),
            (110.0, 0.022), (135.0, -0.018), (160.0, -0.048), (180.0, -0.058))
STYLES = {
    "short": {"count": 14000, "seed": 11, "points": 10, "part_x": 0.028},
    "long": {"count": 16000, "seed": 23, "points": 32, "grow": 76, "part_x": 0.0, "end_z": -0.222, "step": 0.0055},
}
ROOT_RADIUS, TIP_RADIUS = 0.00034, 0.00014
COLOUR = (0.075, 0.045, 0.026)


def bearing(points):
    """Degrees from the front (-Y) around Z, 0..180 on either side."""
    return numpy.degrees(numpy.arctan2(numpy.abs(points[:, 0]), -(points[:, 1] - CENTRE[1])))


def hairline_z(degrees):
    xs, zs = zip(*HAIRLINE)
    return numpy.interp(degrees, xs, zs)


def _unit(vectors):
    return vectors / numpy.maximum(numpy.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)


def _surface(design, directions, low=0.03, high=0.16, steps=40):
    """Where rays from ``CENTRE`` along ``directions`` leave the head (bisection on the distance)."""
    lo = numpy.full(len(directions), low)
    hi = numpy.full(len(directions), high)
    for _ in range(steps):
        mid = (lo + hi) * 0.5
        inside = head_sdf.distance(design, CENTRE + directions * mid[:, None]) < 0
        lo, hi = numpy.where(inside, mid, lo), numpy.where(inside, hi, mid)
    return CENTRE + directions * ((lo + hi) * 0.5)[:, None]


def roots(design, count, seed):
    """``count`` roots spread evenly over the scalp (above the hairline, never on an ear), shuffled by ``seed``."""
    total = count * 4
    index = numpy.arange(total) + 0.5
    polar = numpy.arccos(1.0 - 2.0 * index / total)
    turn = math.pi * (1.0 + 5 ** 0.5) * index
    directions = numpy.column_stack((numpy.cos(turn) * numpy.sin(polar), numpy.sin(turn) * numpy.sin(polar),
                                     numpy.cos(polar)))
    directions = directions[directions[:, 2] > -0.75]
    points = _surface(design, directions)
    ear = (numpy.abs(points[:, 0]) > 0.062) & (points[:, 2] < 0.028) & (numpy.abs(points[:, 1] - 0.010) < 0.034)
    scalp = points[(points[:, 2] > hairline_z(bearing(points))) & ~ear]
    rng = numpy.random.default_rng(seed)
    return scalp[rng.permutation(len(scalp))[:count]]


def _push_out(design, points, clearance):
    """Move points that are closer to the head than ``clearance`` out along the gradient."""
    for _ in range(2):
        value = head_sdf.distance(design, points)
        short = value < clearance
        if not short.any():
            break
        grad = _unit(head_sdf.gradient(design, points[short]))
        points[short] += grad * (clearance[short] - value[short])[:, None]
    return points


def _comb_short(points, part_x):
    """Across the top away from the parting (front half only), then down and back on the sides and the back."""
    top = numpy.clip((points[:, 2] - 0.050) / 0.030, 0.0, 1.0) * (points[:, 1] < 0.045)
    away = numpy.where(points[:, 0] >= part_x, 1.0, -1.0)
    across = _unit(numpy.column_stack((away, numpy.full(len(points), 0.45), numpy.zeros(len(points)))))
    side = numpy.clip((numpy.abs(points[:, 0]) - 0.035) / 0.03, 0.0, 1.0) * numpy.sign(points[:, 0])
    down = _unit(numpy.column_stack((side * 0.15, numpy.full(len(points), 0.55), numpy.full(len(points), -1.0))))
    return _unit(across * top[:, None] + down * (1.0 - top[:, None]))


def _comb_long(points):
    """Out from the middle parting: front strands sweep sideways until they are clear of the face, then fall."""
    count = len(points)
    outward = numpy.where(points[:, 0] >= 0, 1.0, -1.0)
    top = numpy.clip((points[:, 2] - 0.060) / 0.030, 0.0, 1.0)
    face = (points[:, 1] < 0.0) & (numpy.abs(points[:, 0]) < 0.070) & (points[:, 2] > -0.005)
    sweep = numpy.maximum(top, face.astype(float))[:, None]
    sideways = numpy.column_stack((outward, numpy.where(points[:, 1] > 0.03, 0.6, 0.15), numpy.full(count, -0.25)))
    return _unit(sideways * sweep + numpy.array((0.0, 0.0, -1.0)) * (1.0 - sweep))


def _frame_face(points):
    """Long hair falls beside the face, never across it: in front of the cheeks it keeps |x| >= 0.072."""
    front = (points[:, 1] < -0.030) & (points[:, 2] < 0.030)
    narrow = front & (numpy.abs(points[:, 0]) < 0.072)
    points[narrow, 0] = numpy.where(points[narrow, 0] >= 0, 0.072, -0.072)
    return points


def _along_scalp(design, points, direction):
    """``direction`` with its component into or out of the head removed (hair lies on the scalp)."""
    normal = _unit(head_sdf.gradient(design, points))
    return _unit(direction - numpy.sum(direction * normal, axis=1, keepdims=True) * normal)


def strands(design, style):
    """``(positions (N, K, 3) float32, radii (K,) float32)`` of every strand of a style."""
    spec = STYLES[style]
    rng = numpy.random.default_rng(spec["seed"])
    start = roots(design, spec["count"], spec["seed"])
    normal = _unit(head_sdf.gradient(design, start))
    count, points = len(start), spec.get("grow", spec["points"])
    if style == "short":
        top = numpy.clip((start[:, 2] - 0.045) / 0.035, 0.0, 1.0)
        length = (0.020 + 0.030 * top) * rng.uniform(0.85, 1.15, count)
        steps = numpy.repeat((length / (points - 1))[:, None], points - 1, axis=1)
        reach, end = 0.008, None
    else:
        steps = numpy.full((count, points - 1), spec["step"]) * rng.uniform(0.92, 1.08, count)[:, None]
        reach, end = 0.010, spec["end_z"] + rng.uniform(-0.005, 0.005, count)
    path = numpy.empty((count, points, 3))
    path[:, 0] = start
    current = start.copy()
    for index in range(1, points):
        t = index / (points - 1)
        if style == "short":
            lift = max(0.0, 1.0 - 2.5 * t)
            comb = _along_scalp(design, current, _comb_short(current, spec["part_x"]))
            direction = _unit(normal * lift + comb)
        else:
            lift = max(0.0, 1.0 - 12.0 * t)
            near = (head_sdf.distance(design, current) < 0.014)[:, None]
            falling = _along_scalp(design, current, _comb_long(current))
            direction = _unit(normal * lift + numpy.where(near, falling, numpy.array((0.0, 0.0, -1.0))))
        moved = current + direction * steps[:, index - 1][:, None]
        moved = _push_out(design, moved, numpy.full(count, 0.0012 + reach * min(1.0, (1.5 if style == "short"
                                                                                       else 8.0) * t)))
        if end is not None:
            moved = _frame_face(moved)
            moved = numpy.where((current[:, 2] > end)[:, None], moved, current)   # A strand stops at its end height.
        current = moved
        path[:, index] = current
    if style == "long":
        path = _resample(path, spec["points"])
    radii = numpy.linspace(ROOT_RADIUS, TIP_RADIUS, path.shape[1]).astype(numpy.float32)
    return path.astype(numpy.float32), radii


def _resample(path, points):
    """Strands resampled to ``points`` evenly along their own length (one that stopped early holds its end)."""
    count = path.shape[0]
    segments = numpy.linalg.norm(numpy.diff(path, axis=1), axis=2)
    lengths = numpy.concatenate((numpy.zeros((count, 1)), numpy.cumsum(segments, axis=1)), axis=1)
    output = numpy.empty((count, points, 3))
    for strand in range(count):
        wanted = numpy.linspace(0.0, lengths[strand, -1], points)
        for axis in range(3):
            output[strand, :, axis] = numpy.interp(wanted, lengths[strand], path[strand, :, axis])
    return output


def hair_object(bpy, name, positions, radii, material=None, surface=None):
    """One hair curves object holding every strand (same point count each), with a radius per point."""
    count, points, _ = positions.shape
    curves = bpy.data.hair_curves.new(name)
    curves.add_curves([points] * count)
    curves.position_data.foreach_set("vector", numpy.ascontiguousarray(positions, dtype=numpy.float32).ravel())
    radius = curves.attributes.get("radius") or curves.attributes.new("radius", "FLOAT", "POINT")
    radius.data.foreach_set("value", numpy.tile(radii, count).astype(numpy.float32))
    if surface is not None:
        curves.surface = surface
    obj = bpy.data.objects.new(name, curves)
    bpy.context.scene.collection.objects.link(obj)
    if material is not None:
        curves.materials.append(material)
    return obj


def build_hair(bpy, design, style, name="Hair", surface=None):
    """The style's strands as a hair curves object with a dark brown material."""
    positions, radii = strands(design, style)
    material = kit.principled(bpy, "Hair Brown", COLOUR, roughness=0.45)
    return hair_object(bpy, name, positions, radii, material, surface)
