# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed checks of the many-object scenarios (``many_object_scenes``), graded against the start plan.

* ``row``: the objects of a numbered row are evenly spaced at the row's own step, the start members unmoved and the
  new ones continuing it at either end, each the size of the first;
* ``toward``: an object moved a distance toward a point, straight at it or along the axis that leads to it;
* ``offset_all``: every start object a target matches moved by exactly ``offset`` (world metres);
* ``scaled_all``: every start object a target matches is ``factor`` times its start size, in place;
* ``turned``: an object turned by ``degrees`` about its vertical axis (either way), in place;
* ``unchanged``: every start object no turn names keeps its transform and, for a light, its power and colour.

Names, counts and positions come from the scenario's own plan, never from what an agent did.
"""

import math

import many_object_scenes as scenes


def _matches(name, target):
    import scenario_checks
    return scenario_checks.matches(name, target)


def _items(check):
    return [item for item in scenes.plan(check["scene"]) if _matches(item.name, check["target"])]


def _at(obj):
    return [round(float(value), 4) for value in obj.matrix_world.translation]


def _check_row(bpy, scene, depsgraph, check):
    """The row's members are evenly spaced at its own step, its start members unmoved; new members may continue it
    at either end ("beside the ones there, continuing the row" does not say which)."""
    start = sorted(_items(check), key=lambda item: item.location[check.get("axis", 0)])
    axis, tolerance, count = check.get("axis", 0), check.get("tolerance", 0.05), check["count"]
    step = [b - a for a, b in zip(start[0].location, start[1].location)]
    found = sorted((obj for obj in scene.objects if _matches(obj.name, check["target"]) and obj.type == "MESH"),
                   key=lambda obj: obj.matrix_world.translation[axis])
    size = list(start[0].size)
    problems = []
    if len(found) != count:
        problems.append("%d objects, not %d" % (len(found), count))
    for item in start:
        obj = scene.objects.get(item.name)
        if obj is None or math.dist(obj.matrix_world.translation, item.location) > tolerance:
            problems.append("%s moved or missing" % item.name)
    for before, after in zip(found, found[1:]):
        gap = [b - a for a, b in zip(before.matrix_world.translation, after.matrix_world.translation)]
        if math.dist(gap, step) > tolerance:
            problems.append("%s to %s is %s, not the row's step %s" % (before.name, after.name,
                                                                        [round(v, 3) for v in gap], step))
    for obj in found:
        if any(abs(a - b) > 0.02 * max(b, 0.01) for a, b in zip(obj.dimensions, size)):
            problems.append("%s is %s, not %s" % (obj.name, [round(v, 3) for v in obj.dimensions], size))
    return not problems, {"found": [obj.name for obj in found][:12], "at": [_at(obj) for obj in found][:12],
                          "problems": problems[:10]}


def _check_toward(bpy, scene, depsgraph, check):
    """An object moved ``distance`` metres toward ``point``: straight at it, or along the room axis that leads to it
    ("50 cm toward the door"), at the same height."""
    item = scenes.entry(check["scene"], check["target"]["name"])
    obj = scene.objects.get(item.name)
    if obj is None:
        return False, {"reason": "missing"}
    at, point, distance = obj.matrix_world.translation, check["point"], check["distance"]
    moved = math.dist(at, item.location)
    closer = math.dist(item.location[:2], point[:2]) - math.dist(at[:2], point[:2])
    level = abs(at[2] - item.location[2]) <= 0.02
    tolerance = check.get("tolerance", 0.05)
    passed = abs(moved - distance) <= tolerance and closer >= 0.8 * distance and level
    return passed, {"at": _at(obj), "moved": round(moved, 3), "closer": round(closer, 3), "level": level}


def _check_offset_all(bpy, scene, depsgraph, check):
    offset, tolerance, problems, seen = check["offset"], check.get("tolerance", 0.02), [], 0
    for item in _items(check):
        obj = scene.objects.get(item.name)
        if obj is None:
            problems.append((item.name, "missing"))
            continue
        seen += 1
        wanted = [item.location[k] + offset[k] for k in range(3)]
        if math.dist(obj.matrix_world.translation, wanted) > tolerance:
            problems.append((item.name, "at %s, not %s" % (_at(obj), [round(v, 3) for v in wanted])))
    return not problems and seen > 0, {"objects": seen, "problems": problems[:10]}


def _check_scaled_all(bpy, scene, depsgraph, check):
    factor, problems, seen = check["factor"], [], 0
    for item in _items(check):
        obj = scene.objects.get(item.name)
        if obj is None:
            problems.append((item.name, "missing"))
            continue
        seen += 1
        wanted = [value * factor for value in item.size]
        if any(abs(a - b) > check.get("tolerance", 0.03) * b for a, b in zip(obj.dimensions, wanted)):
            problems.append((item.name, "is %s, not %s" % ([round(v, 3) for v in obj.dimensions], wanted)))
        # In place: its origin where it was, or raised or lowered by the growth when its base was kept.
        if math.dist(obj.matrix_world.translation, item.location) > 0.02 + abs(factor - 1.0) * max(item.size) / 2:
            problems.append((item.name, "moved to %s" % _at(obj)))
    return not problems and seen > 0, {"objects": seen, "problems": problems[:10]}


def _check_turned(bpy, scene, depsgraph, check):
    item = scenes.entry(check["scene"], check["target"]["name"])
    obj = scene.objects.get(item.name)
    if obj is None:
        return False, {"reason": "missing"}
    start = obj.matrix_world.to_euler("XYZ")
    turned = math.degrees(start.z - item.rotation[2]) % 360.0
    off = min(abs(turned - check["degrees"]), abs(turned - (360.0 - check["degrees"])))
    flat = abs(start.x - item.rotation[0]) < 0.02 and abs(start.y - item.rotation[1]) < 0.02
    placed = math.dist(obj.matrix_world.translation, item.location) <= 0.03
    return off <= check.get("tolerance_deg", 3.0) and flat and placed, {
        "turned_deg": round(turned, 2), "level": flat, "in_place": placed}


def _check_unchanged(bpy, scene, depsgraph, check):
    changed, missing, checked = [], [], 0
    for item in scenes.plan(check["scene"]):
        if any(_matches(item.name, target) for target in check.get("exclude", ())):
            continue
        obj = scene.objects.get(item.name)
        if obj is None:
            missing.append(item.name)
            continue
        checked += 1
        why = []
        if math.dist(obj.matrix_world.translation, item.location) > 1e-3:
            why.append("location")
        rotation = obj.matrix_world.to_euler("XYZ")
        if any(abs(math.remainder(a - b, math.tau)) > 1e-3 for a, b in zip(rotation, item.rotation)):
            why.append("rotation")
        if item.kind == "box" and any(abs(a - b) > 1e-3 for a, b in zip(obj.dimensions, item.size)):
            why.append("size")
        if item.kind == "light" and obj.type == "LIGHT":
            _kind, energy, colour = item.light
            if abs(obj.data.energy - energy) > 1e-3 * max(1.0, energy):
                why.append("power")
            if any(abs(a - b) > 1e-3 for a, b in zip(obj.data.color, colour)):
                why.append("colour")
        if why:
            changed.append((item.name, why))
    return not changed and not missing, {"checked": checked, "changed": changed[:12], "missing": missing[:12]}


CHECKS = {"row": _check_row, "toward": _check_toward, "offset_all": _check_offset_all, "scaled_all": _check_scaled_all,
          "turned": _check_turned, "unchanged": _check_unchanged}
