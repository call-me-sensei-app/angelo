# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed checks of the face and hair one-shot tests: a side's head and hair against the ground truth's.

Every kind reads the scene through ``face_truth.read`` (the same readings for
any build: sculpted, remeshed, Multires, Geometry Nodes, curves or particle
hair) and compares them with the truth's (``face_compare``). A check passes
when its errors are within the tolerance the brief states; its details carry
every number, in millimetres and in pixels of the reference pictures, so a
failed check says how far off each feature was. Readings are made once per
scene state and shared by every kind (``_reading``).

* ``face_landmarks``: the profile landmarks the truth has well defined.
* ``face_eyes``: both eye centres (3D distance).
* ``face_jaw``: the chin's bottom and the jaw line's mean.
* ``face_mouth``: both mouth corners and the mouth line's middle.
* ``face_outline``: one view's outline, 95th percentile of the edge distance.
* ``face_relief``: every region's surface distance (both ways), 90th percentile.
* ``hair_strands``: real strands: how many, rooted on the scalp, not inside the head.
* ``hair_shape``: per hair view, the hair's overlap with the truth's and the outline of head and hair.
* ``hairline``: the hairline over the forehead, mean error.
* ``hair_length``: the lowest hair at the back and the sides.
"""

import hashlib
import math

import numpy

# At import, never inside a check: Blender MCP's grading prelude puts this folder on sys.path only while it
# imports scenario_checks (bench_mcp_grading._scenario_prelude), so a later import from here would fail.
import face_casters
import face_compare
import face_truth

_READINGS = {}


def _fingerprint(bpy, depsgraph):
    """A cheap digest of the evaluated geometry and transforms: readings are shared while it holds."""
    sha = hashlib.sha256()
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES"}:
            continue
        sha.update(obj.original.name.encode() + obj.type.encode())
        sha.update(numpy.asarray(instance.matrix_world, dtype=numpy.float64).round(7).tobytes())
        data = obj.data
        if obj.type == "MESH" and data is not None:
            count = len(data.vertices)
            sha.update(str(count).encode())
            if count:
                positions = numpy.empty(count * 3, dtype=numpy.float32)
                data.vertices.foreach_get("co", positions)
                sha.update(positions[::max(1, len(positions) // 3000)].tobytes())
        elif obj.type == "CURVES" and data is not None:
            count = len(data.points)
            sha.update(str(count).encode())
            if count:
                positions = numpy.empty(count * 3, dtype=numpy.float32)
                data.attributes["position"].data.foreach_get("vector", positions)
                sha.update(positions[::max(1, len(positions) // 3000)].tobytes())
        for system in getattr(obj, "particle_systems", ()):
            sha.update(("%s:%d:%d" % (system.settings.type, len(system.particles), len(system.child_particles))).encode())
    return sha.hexdigest()


def _reading(bpy, scene, depsgraph, name):
    key = (name, scene.name, _fingerprint(bpy, depsgraph))
    if key not in _READINGS:
        _READINGS.clear()
        _READINGS[key] = face_truth.read(bpy, scene, depsgraph, name)
    return _READINGS[key]


def _pixels(name):
    import scenario_specs
    cameras = scenario_specs.module(name).CAMERAS
    return {view: cameras[view]["ortho_scale"] / 1280.0 for view in face_truth.HAIR_VIEWS if view in cameras}


def _within(value, limit):
    return value is not None and math.isfinite(value) and value <= limit


def _check_landmarks(bpy, scene, depsgraph, check):
    name = check["scenario"]
    truth = face_truth.truth(name)
    side = _reading(bpy, scene, depsgraph, name)["head"]
    errors = face_compare.landmarks(side.get("landmarks"), truth["head"]["landmarks"], truth["defined"],
                                    _pixels(name)["side"])
    limit = check["tolerance_mm"]
    worst = max((entry["error_mm"] for entry in errors.values()), default=math.inf)
    return all(_within(entry["error_mm"], limit) for entry in errors.values()), {
        "tolerance_mm": limit, "worst_mm": worst, "landmarks": errors}


def _check_eyes(bpy, scene, depsgraph, check):
    name = check["scenario"]
    truth = face_truth.truth(name)
    side = _reading(bpy, scene, depsgraph, name)["head"]
    pixels = _pixels(name)
    errors = face_compare.eyes(side["eyes"], truth["head"]["eyes"], pixels["front"], pixels["side"])
    limit = check["tolerance_mm"]
    return all(_within(entry["error_mm"], limit) for entry in errors.values()), {"tolerance_mm": limit, "eyes": errors}


def _check_jaw(bpy, scene, depsgraph, check):
    name = check["scenario"]
    truth = face_truth.truth(name)
    side = _reading(bpy, scene, depsgraph, name)["head"]
    errors = face_compare.jaw(side, truth["head"], _pixels(name)["front"])
    limit = check["tolerance_mm"]
    line = errors["line"]
    passed = (_within(errors["menton"]["error_mm"], limit) and _within(line["mean_mm"], limit)
              and line["found"] >= 0.8 * line["columns"])
    return passed, dict(errors, tolerance_mm=limit)


def _check_mouth(bpy, scene, depsgraph, check):
    name = check["scenario"]
    truth = face_truth.truth(name)
    side = _reading(bpy, scene, depsgraph, name)["head"]
    errors = face_compare.mouth(side.get("mouth"), truth["head"].get("mouth"), _pixels(name)["front"])
    if errors is None:
        return False, {"reason": "the ground truth has no mouth line to compare with"}
    limit = check["tolerance_mm"]
    return all(_within(entry["error_mm"], limit) for entry in errors.values()), {"tolerance_mm": limit,
                                                                                "points": errors}


def _check_outline(bpy, scene, depsgraph, check):
    name, view = check["scenario"], check["view"]
    truth = face_truth.truth(name)
    side = _reading(bpy, scene, depsgraph, name)["head"]
    errors = face_compare.outline(side.get(view), truth["head"][view], _pixels(name)[view])
    limit = check["tolerance_mm"]
    return _within(errors["p95_mm"], limit), dict(errors, view=view, tolerance_mm=limit)


def _check_relief(bpy, scene, depsgraph, check):
    name = check["scenario"]
    truth = face_truth.truth(name)
    errors = face_compare.relief(_reading(bpy, scene, depsgraph, name).get("relief"), truth["regions"])
    limit = check["tolerance_mm"]
    return all(_within(entry["p90_mm"], limit) for entry in errors.values()), {
        "tolerance_mm": limit, "statistic": "90th percentile of the surface distance, both ways", "regions": errors}


def _check_hair_strands(bpy, scene, depsgraph, check):
    """Real strands, rooted on the head's surface and not inside it (a sample of their points)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    strands, sources = face_casters.hair_strands(depsgraph, face_casters.name_matcher([check["target"]]))
    facts = {"strands": len(strands), "sources": sources}
    if len(strands) < check["min_strands"]:
        return False, dict(facts, reason="fewer than %d strands" % check["min_strands"])
    head = next((obj for obj in scene.objects if obj.type == "MESH" and obj.name == check["head"]), None)
    if head is None:
        return False, dict(facts, reason="no mesh named %r" % check["head"])
    tree = BVHTree.FromObject(head, depsgraph)
    world = head.matrix_world
    local = world.inverted()
    rng = numpy.random.default_rng(1)
    chosen = rng.choice(len(strands), size=min(2000, len(strands)), replace=False)
    rooted, inside, tested = 0, 0, 0
    for index in chosen:
        points = strands[index]
        location, normal, _face, distance = tree.find_nearest(local @ Vector(points[0].tolist()))
        if location is not None and distance * world.to_scale()[0] <= check["rooted_mm"] / 1000.0:
            rooted += 1
        for point in points[1::max(1, len(points) // 6)]:
            p = local @ Vector(point.tolist())
            location, normal, _face, _distance = tree.find_nearest(p)
            if location is not None:
                tested += 1
                inside += (p - location).dot(normal) < -1e-5
    facts.update(rooted_share=round(rooted / len(chosen), 4), inside_share=round(inside / max(1, tested), 4))
    passed = facts["rooted_share"] >= check["rooted_share"] and facts["inside_share"] <= check["inside_share"]
    return passed, facts


def _hair(bpy, scene, depsgraph, name):
    truth = face_truth.truth(name)["hair"]
    side = _reading(bpy, scene, depsgraph, name)["hair"]
    return face_compare.hair(side, truth, _pixels(name))


def _check_hair_shape(bpy, scene, depsgraph, check):
    errors = _hair(bpy, scene, depsgraph, check["scenario"])["views"]
    chosen = {view: errors[view] for view in check["views"]}
    passed = all(entry["hair_iou"] >= check["min_iou"] and _within(entry["outline_p95_mm"], check["tolerance_mm"])
                 for entry in chosen.values())
    return passed, {"min_iou": check["min_iou"], "tolerance_mm": check["tolerance_mm"], "views": chosen}


def _check_hairline(bpy, scene, depsgraph, check):
    errors = _hair(bpy, scene, depsgraph, check["scenario"])["hairline"]
    passed = _within(errors["mean_mm"], check["tolerance_mm"]) and errors["found"] >= 0.8 * errors["columns"]
    return passed, dict(errors, tolerance_mm=check["tolerance_mm"])


def _check_hair_length(bpy, scene, depsgraph, check):
    errors = _hair(bpy, scene, depsgraph, check["scenario"])["length"]
    return all(_within(entry["error_mm"], check["tolerance_mm"]) for entry in errors.values()), dict(
        errors, tolerance_mm=check["tolerance_mm"])


CHECKS = {"face_landmarks": _check_landmarks, "face_eyes": _check_eyes, "face_jaw": _check_jaw,
          "face_mouth": _check_mouth, "face_outline": _check_outline, "face_relief": _check_relief,
          "hair_strands": _check_hair_strands, "hair_shape": _check_hair_shape, "hairline": _check_hairline,
          "hair_length": _check_hair_length}
