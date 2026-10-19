# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check kinds of the heavy-scene tasks (``scenario-heavy``): each needs geometry read, not just an edit made.

The truth comes from the scene itself and from the seeded plan
(``heavy_plan``), never from the model:

* ``summit``: the target stands (its lowest point within ``height`` of the
  ground) within ``radius`` of the terrain's highest vertex, read from the
  terrain's own mesh in world space.
* ``grounded``: every matching object's lowest point is within ``tolerance``
  of the terrain surface straight below its origin (a ray onto the terrain's
  own mesh), it has not moved sideways from its planned place, and its mesh
  is the planned one.
* ``reduced``: the target renders with ``ratio`` of its planned faces, or of
  its planned triangles (Decimate counts triangles), an applied reduction or a
  modifier alike, at its planned place and within ``shape`` of its planned size.
* ``outward``: every matching mesh is wound consistently (each edge walked
  once each way) with a positive signed volume, so every face points out;
  no vertex moved and the face count is the planned one.
* ``moved``: the target and each of ``with`` sit ``offset`` from their planned
  world places, otherwise unchanged.
* ``untouched``: every planned object no task names (``exclude``) still
  holds exactly what the plan made: transform, parent, materials, no
  modifiers, and CRC-32s of its positions and corner vertices. The target
  ``{"prefix": ""}`` matches every object, so the hygiene report counts none
  as unexpected.

Reads use attribute arrays (``foreach_get`` into NumPy), so a check of the
whole scene takes about a second.
"""

import math

import heavy_plan as plan
import heavy_scene_blend as blend


def _world_bounds(obj, depsgraph):
    from mathutils import Vector
    evaluated = obj.evaluated_get(depsgraph)
    corners = [evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box]
    return [min(c[axis] for c in corners) for axis in range(3)], [max(c[axis] for c in corners) for axis in range(3)]


def _terrain_summit(scene, name):
    """``(x, y, z)`` of the named terrain's highest vertex in world space, read from its own mesh."""
    import numpy
    terrain = scene.objects.get(name)
    if terrain is None or terrain.type != "MESH":
        return None
    positions = numpy.empty(len(terrain.data.vertices) * 3, numpy.float32)
    terrain.data.attributes["position"].data.foreach_get("vector", positions)
    points = positions.reshape(-1, 3).astype(numpy.float64)
    matrix = numpy.array(terrain.matrix_world, dtype=numpy.float64)
    world = points @ matrix[:3, :3].T + matrix[:3, 3]
    index = int(world[:, 2].argmax())
    return tuple(float(v) for v in world[index])


def _check_summit(bpy, scene, depsgraph, check):
    summit = _terrain_summit(scene, check["terrain"])
    target = scene.objects.get(check["target"]["name"])
    if summit is None or target is None:
        return False, {"reason": "no %s" % ("terrain" if summit is None else "target")}
    low, _ = _world_bounds(target, depsgraph)
    location = target.matrix_world.translation
    off = math.hypot(location.x - summit[0], location.y - summit[1])
    rise = low[2] - summit[2]
    passed = off <= check.get("radius", 3.0) and abs(rise) <= check.get("height", 0.3)
    return passed, {"summit": [round(v, 3) for v in summit], "target_at": [round(v, 3) for v in location],
                    "off_m": round(off, 3), "base_above_summit_m": round(rise, 3)}


def _check_grounded(bpy, scene, depsgraph, check):
    import scenario_checks
    terrain = scene.objects.get(check["ground"])
    if terrain is None:
        return False, {"reason": "no ground"}
    planned = {item.name: item for item in plan.plan() if scenario_checks.matches(item.name, check["target"])}
    problems, worst = [], 0.0
    for name, item in sorted(planned.items()):
        obj = scene.objects.get(name)
        if obj is None:
            problems.append((name, "missing"))
            continue
        location = obj.matrix_world.translation
        if math.hypot(location.x - item.location[0], location.y - item.location[1]) > check.get("sideways", 0.05):
            problems.append((name, "moved sideways"))
            continue
        ground = blend.surface_ray(bpy, terrain, location.x, location.y)
        low, _ = _world_bounds(obj, depsgraph)
        gap = None if ground is None else low[2] - ground
        if gap is None or abs(gap) > check.get("tolerance", 0.15):
            problems.append((name, "%s by %s m" % ("floats" if gap and gap > 0 else "sunk", None if gap is None
                                                    else round(gap, 3))))
        elif len(obj.data.vertices) != len(plan.geometry(item)[0]):
            problems.append((name, "its mesh changed"))
        worst = max(worst, abs(gap or 0.0))
    return not problems and len(planned) == check.get("count", len(planned)), {
        "objects": len(planned), "off": len(problems), "worst_gap_m": round(worst, 3), "examples": problems[:10]}


def _check_reduced(bpy, scene, depsgraph, check):
    import numpy
    item = plan.entry(check["target"]["name"])
    obj = scene.objects.get(item.name)
    if obj is None:
        return False, {"reason": "missing"}
    planned = plan.geometry(item)
    data = obj.evaluated_get(depsgraph).data
    faces, triangles = len(data.polygons), len(data.loop_triangles)
    # "Polygons" is read either way: faces (the statistics overlay) or triangles (Decimate's own face count).
    planned_triangles = int((planned[1] - 2).sum())
    ratio = faces / len(planned[1])
    triangle_ratio = triangles / planned_triangles
    size = [high - low for low, high in zip(*_world_bounds(obj, depsgraph))]
    points = planned[0].astype(numpy.float64)
    wanted = [float(points[:, axis].max() - points[:, axis].min()) for axis in range(3)]
    shape = max(abs(a - b) / b for a, b in zip(size, wanted))
    moved = math.dist(tuple(obj.matrix_world.translation), item.location)
    low, high = check["ratio"]
    halved = low <= ratio <= high or low <= triangle_ratio <= high
    passed = halved and shape <= check.get("shape", 0.05) and moved <= 0.01
    return passed, {"faces": faces, "planned_faces": len(planned[1]), "ratio": round(ratio, 4),
                    "triangles": triangles, "planned_triangles": planned_triangles,
                    "triangle_ratio": round(triangle_ratio, 4), "size_change": round(shape, 4),
                    "moved_m": round(moved, 4)}


def mesh_arrays(data):
    """``(positions (n, 3) float32, face sizes, corner vertices)`` of a live mesh, read through its arrays."""
    import numpy
    positions = numpy.empty(len(data.vertices) * 3, numpy.float32)
    data.attributes["position"].data.foreach_get("vector", positions)
    sizes = numpy.empty(len(data.polygons), numpy.int32)
    data.polygons.foreach_get("loop_total", sizes)
    corners = numpy.empty(len(data.loops), numpy.int32)
    data.attributes[".corner_vert"].data.foreach_get("value", corners)
    return positions.reshape(-1, 3), sizes, corners


def _check_outward(bpy, scene, depsgraph, check):
    import scenario_checks
    planned = [item for item in plan.plan() if scenario_checks.matches(item.name, check["target"])]
    seen, problems = {}, []
    for item in planned:
        obj = scene.objects.get(item.name)
        if obj is None or obj.type != "MESH":
            problems.append((item.name, "missing"))
            continue
        positions, sizes, corners = mesh_arrays(obj.data)
        consistent, volume = blend.wall_volume_ok(positions, sizes, corners)
        want = plan.geometry(item)
        kept = len(sizes) == len(want[1]) and blend.crc(positions) == blend.crc(want[0])
        seen[item.name] = {"consistent": consistent, "volume": round(volume, 2), "vertices_kept": kept}
        if not (consistent and volume > 0):
            problems.append((item.name, "faces point inwards"))
        if not kept:
            problems.append((item.name, "vertices or faces changed"))
    return not problems and bool(planned), {"meshes": seen, "problems": problems[:10]}


def _check_moved(bpy, scene, depsgraph, check):
    offset, problems = check["offset"], []
    names = [check["target"]["name"]] + list(check.get("with", ()))
    found = {}
    for name in names:
        obj, item = scene.objects.get(name), plan.entry(name)
        if obj is None:
            problems.append((name, "missing"))
            continue
        parent = plan.entry(item.parent).location if item.parent else (0.0, 0.0, 0.0)
        wanted = [parent[axis] + item.location[axis] + offset[axis] for axis in range(3)]
        at = list(obj.matrix_world.translation)
        found[name] = [round(v, 3) for v in at]
        if math.dist(at, wanted) > check.get("tolerance", 0.02):
            problems.append((name, "at %s, not %s" % (found[name], [round(v, 3) for v in wanted])))
        elif blend.mesh_facts(obj.data) != blend.expected(item)["mesh"]:
            problems.append((name, "its mesh changed"))
    return not problems, {"at": found, "problems": problems}


def _check_untouched(bpy, scene, depsgraph, check):
    import scenario_checks
    excluded = check.get("exclude", ())
    changed, missing, checked = [], [], 0
    for item in plan.plan():
        if any(scenario_checks.matches(item.name, target) for target in excluded):
            continue
        obj = scene.objects.get(item.name)
        if obj is None:
            missing.append(item.name)
            continue
        checked += 1
        if item.kind == "mesh":
            found = blend.differences(obj, item)
            if found:
                changed.append((item.name, found))
    extra = len(scene.objects) - checked - sum(1 for item in plan.plan() if scene.objects.get(item.name) is not None
                                               and any(scenario_checks.matches(item.name, t) for t in excluded))
    return not changed and not missing, {"checked": checked, "changed": changed[:12], "changed_count": len(changed),
                                         "missing": missing[:12], "added_objects": extra}


CHECKS = {"summit": _check_summit, "grounded": _check_grounded, "reduced": _check_reduced, "outward": _check_outward,
          "moved": _check_moved, "untouched": _check_untouched}
