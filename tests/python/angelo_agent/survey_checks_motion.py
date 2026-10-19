# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Motion result checks of the request survey (``survey_checks.KINDS``): a bounce and an orbit.

Both sample the evaluated world position of an object over a frame range, so
keyframes, a parent that turns, a constraint or a driver all count alike. The
scene's frame is restored before returning; nothing else is written.
"""

import math


def _positions(bpy, scene, obj, frames):
    """Evaluated world positions of ``obj`` at ``frames``; the scene's frame is restored."""
    original = scene.frame_current
    try:
        positions = []
        for frame in frames:
            scene.frame_set(frame)
            positions.append(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.translation.copy())
        return positions
    finally:
        scene.frame_set(original)


def strokes(values, threshold):
    """Rises and falls of at least ``threshold`` in a sampled curve (smaller wiggles are ignored)."""
    rises = falls = direction = 0
    low = high = values[0]
    for value in values[1:]:
        if direction <= 0 and value >= low + threshold:
            rises, direction, high = rises + 1, 1, value
        elif direction >= 0 and value <= high - threshold:
            falls, direction, low = falls + 1, -1, value
        if direction >= 0:
            high = max(high, value)
        if direction <= 0:
            low = min(low, value)
    return rises, falls


def _first(resolve, alias):
    found = resolve(alias)
    found = found if isinstance(found, list) else [found] if found is not None else []
    return found[0] if found else None


def check_bounces(bpy, scene, resolve, check):
    """The target goes up and comes down ``count`` times over the frames (height along world Z)."""
    obj = _first(resolve, check["target"])
    if obj is None:
        return False, {"reason": "object missing"}
    first, last = check["frames"]
    heights = [position.z for position in _positions(bpy, scene, obj, range(first, last + 1))]
    travel = max(heights) - min(heights)
    if travel < check.get("min_height", 0.05):
        return False, {"travel": round(travel, 4), "reason": "the object does not move up and down"}
    rises, falls = strokes(heights, travel * 0.25)
    # One bounce is a rise and a fall; a motion may start or end half-way through one.
    return min(rises, falls) == check["count"] and max(rises, falls) <= check["count"] + 1, {
        "rises": rises, "falls": falls, "travel": round(travel, 4)}


def check_orbit(bpy, scene, resolve, check):
    """The target circles the ``around`` object ``turns`` times over the scene's frame range (about world Z).

    The turn has to span the range: about half of it is made by the middle
    frame, and the object moves within both the first and the last eighth (an
    eased start or stop moves, however slowly; a hold does not). A full circle
    that ends early and then holds is not an orbit over the animation.
    """
    obj, pivot = _first(resolve, check["target"]), _first(resolve, check["around"])
    if obj is None or pivot is None:
        return False, {"reason": "object missing"}
    first, last = scene.frame_start, scene.frame_end
    samples = 48
    frames = sorted({round(first + (last - first) * index / samples) for index in range(samples + 1)})
    centre = pivot.matrix_world.translation.copy()
    positions = _positions(bpy, scene, obj, frames)
    angles = [math.atan2(position.y - centre.y, position.x - centre.x) for position in positions]
    radii = [math.hypot(position.x - centre.x, position.y - centre.y) for position in positions]
    turned, path = [0.0], 0.0
    for before, after in zip(angles, angles[1:]):
        step = (after - before + math.pi) % (2 * math.pi) - math.pi
        turned.append(turned[-1] + step)
        path += abs(step)
    way = -1.0 if turned[-1] < 0 else 1.0
    turned = [math.degrees(angle) * way for angle in turned]   # Degrees turned so far, in the orbit's own direction.
    degrees, eighth = turned[-1], max(1, len(turned) // 8)
    half, opening, closing = turned[len(turned) // 2], turned[eighth], degrees - turned[-1 - eighth]
    details = {"degrees": round(degrees, 1), "half_way": round(half, 1), "first_eighth": round(opening, 1),
               "last_eighth": round(closing, 1), "radius_min": round(min(radii), 3),
               "radius_max": round(max(radii), 3), "frames": [first, last]}
    wanted, tolerance = 360.0 * check["turns"], check.get("tolerance_deg", 25.0)
    steady = min(radii) > 0.05 and min(radii) >= 0.5 * max(radii)
    one_way = math.degrees(path) <= degrees * 1.15 + 1.0   # It does not swing back and forth.
    spans = abs(half - wanted / 2) <= 2 * tolerance and min(opening, closing) > wanted * 1e-4
    return abs(degrees - wanted) <= tolerance and steady and one_way and spans, details
