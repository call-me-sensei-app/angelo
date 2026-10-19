# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check kinds for a form the brief leaves to the model: how many separate things there are, however they are made.

A brief can ask for "a few thousand leaves", "at least five limbs" or "at
least four windows" without saying how to build them. These kinds count what
is there, in the evaluated scene:

* ``pieces``: the connected mesh pieces of the matching objects' own meshes
  plus the instances they make. Three thousand leaf cards in one mesh, three
  thousand instances of one leaf and three thousand leaf objects all count
  the same; a few smooth blobs do not, however many vertices they have.
  ``{"kind": "pieces", "target": ..., "count": [low, high]}``
* ``ring``: rays run upwards side by side on a cylinder around a vertical
  axis, like the bars of a round cage. A limb that spreads away from the
  axis has to pass between the bars somewhere, at whatever height it leaves:
  neighbouring rays that meet a matching object are one limb, a gap between
  them separates two. The cylinder with the most counts (``radii``), so a
  limb that divides close to the axis is not held against the tree. Rays
  merge two limbs that cross the cylinder at overlapping angles (one above
  the other), so with ``core`` the check also counts separate pieces of
  matching wood (mesh pieces and instances) that grow out of the axis,
  starting within ``core`` of it, and spread past the cylinder. Each count can
  only miss limbs (pieces miss limbs built as one mesh with the trunk), so the
  larger is the tree's.
  ``{"kind": "ring", "targets": [...], "centre": [x, y], "radii": [...],
  "from": z, "to": z, "count": [low, high], "core": metres}``
* ``groups``: mesh pieces of the matching objects whose bounds touch (within
  ``gap``) are one group: a window is a group of frame, bars and glass,
  whether that is one object or twenty. Groups whose longest side is inside
  ``size`` are counted.
  ``{"kind": "groups", "target": ..., "gap": metres, "size": [low, high],
  "count": [low, high]}``

* ``light_from``: the side a light shines from, as a compass bearing in the
  ground plane (0 is +X, 90 is +Y), within ``within_deg`` of the one asked.
  ``{"kind": "light_from", "target": ..., "azimuth_deg": 225, "within_deg": 60}``

Read-only. Meshes are read through NumPy (bundled with Blender); a mesh too
large to count fails as inconclusive rather than passing unread.
"""

import math

MAX_VERTICES = 8000000      # Beyond this a count is refused as inconclusive.
MAX_GROUPED_PIECES = 6000   # ``groups`` compares pieces pairwise.
MAX_ROUNDS = 400


def _array(collection, attribute, width, kind):
    """One attribute of every item of a mesh collection as a NumPy array (``foreach_get`` when there is one)."""
    import numpy
    flat = numpy.zeros(len(collection) * width, dtype=kind)
    if hasattr(collection, "foreach_get"):
        collection.foreach_get(attribute, flat)
    else:   # A stand-in mesh in a host test.
        flat[:] = [value for item in collection for value in getattr(item, attribute)]
    return flat.reshape(-1, width)


def island_labels(mesh):
    """``(label of each vertex, which vertices an edge uses)``: vertices joined by edges share a label."""
    import numpy
    count = len(mesh.vertices)
    edges = _array(mesh.edges, "vertices", 2, numpy.int32).astype(numpy.int64)
    labels = numpy.arange(count, dtype=numpy.int64)
    used = numpy.zeros(count, dtype=bool)
    if not len(edges):
        return labels, used
    first, second = edges[:, 0], edges[:, 1]
    used[first] = used[second] = True
    for _ in range(MAX_ROUNDS):
        # Every edge pulls both its ends to the smaller label, then each label follows its own (pointer jumping).
        low = numpy.minimum(labels[first], labels[second])
        pulled = labels.copy()
        numpy.minimum.at(pulled, first, low)
        numpy.minimum.at(pulled, second, low)
        pulled = pulled[pulled]
        if numpy.array_equal(pulled, labels):
            return labels, used
        labels = pulled
    raise ValueError("pieces did not settle in %d rounds" % MAX_ROUNDS)


def island_count(mesh):
    """How many connected pieces a mesh has (a vertex no edge uses is not a piece)."""
    import numpy
    labels, used = island_labels(mesh)
    return int(len(numpy.unique(labels[used])))


def _own_meshes(depsgraph, target):
    """``(name, evaluated mesh)`` of each matching object's own geometry (its instances are counted apart)."""
    import scenario_checks
    for instance in depsgraph.object_instances:
        obj = instance.object
        if not instance.is_instance and obj.type == "MESH" and scenario_checks.matches(obj.original.name, target):
            yield obj.original.name, obj.data


def _check_pieces(bpy, scene, depsgraph, check):
    """Connected mesh pieces of the matching objects' own meshes plus the instances they make."""
    import scenario_checks
    islands = vertices = 0
    seen = []
    for name, mesh in _own_meshes(depsgraph, check["target"]):
        vertices += len(mesh.vertices)
        if vertices > MAX_VERTICES:
            return False, {"reason": "inconclusive: more than %d vertices to count" % MAX_VERTICES, "objects": seen}
        islands += island_count(mesh)
        seen.append(name)
    _, made = scenario_checks._check_instances(bpy, scene, depsgraph, {"target": check["target"]})
    pieces = islands + made["instances"]
    low, high = check.get("count", (1, math.inf))
    return low <= pieces <= high, {"pieces": pieces, "mesh_pieces": islands, "instances": made["instances"],
                                   "vertices": vertices, "objects": sorted(seen)[:12]}


def runs(hits):
    """How many separate runs of hits a ring of rays has (the ring closes: the last ray neighbours the first)."""
    if all(hits):
        return 1 if hits else 0
    return sum(1 for index, hit in enumerate(hits) if hit and not hits[index - 1])


def pieces_from_core(depsgraph, targets, centre, core, radius, low_z, high_z):
    """``(count, pieces)``: connected pieces of matching geometry (instances included) that, between ``low_z``
    and ``high_z``, come within ``core`` of the vertical axis through ``centre`` and reach beyond ``radius``."""
    import numpy
    import scenario_checks
    cache, count, found, vertices = {}, 0, [], 0
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type != "MESH" or not any(scenario_checks._instance_matches(instance, target) for target in targets):
            continue
        mesh = obj.data
        key = mesh.as_pointer() if hasattr(mesh, "as_pointer") else id(mesh)
        if key not in cache:
            vertices += len(mesh.vertices)
            if vertices > MAX_VERTICES:
                raise ValueError("more than %d vertices to count" % MAX_VERTICES)
            labels, used = island_labels(mesh)
            cache[key] = (labels, used, _array(mesh.vertices, "co", 3, numpy.float32).astype(numpy.float64))
        labels, used, points = cache[key]
        matrix = numpy.array([list(row) for row in instance.matrix_world], dtype=numpy.float64)
        world = points @ matrix[:3, :3].T + matrix[:3, 3]
        distance = numpy.hypot(world[:, 0] - centre[0], world[:, 1] - centre[1])
        within = used & (world[:, 2] >= low_z) & (world[:, 2] <= high_z)
        starts = numpy.unique(labels[within & (distance < core)])
        reaches = set(numpy.unique(labels[within & (distance > radius)]).tolist())
        for label in starts.tolist():
            if label in reaches:
                count += 1
                if len(found) < 12:
                    piece = labels == label
                    found.append({"object": obj.original.name, "nearest": round(float(distance[piece].min()), 3),
                                  "furthest": round(float(distance[piece].max()), 3)})
    return count, found


def _check_ring(bpy, scene, depsgraph, check):
    """Separate matching things that cross a cylinder around a vertical axis: the most that any radius shows, or
    with ``core`` the separate pieces that grow out of the axis and spread past it, if more."""
    import scenario_checks
    centre, count = check.get("centre", (0.0, 0.0)), int(check.get("rays", 180))
    low_z, high_z = float(check["from"]), float(check["to"])
    targets = check.get("targets") or [check["target"]]
    found = {}
    for radius in check["radii"]:
        hits = []
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            origin = (centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle), low_z)
            hits.append(any(scenario_checks.probe(scene, depsgraph, origin, (0.0, 0.0, 1.0), high_z - low_z,
                                                  target)["hit"] for target in targets))
        found[str(radius)] = runs(hits)
    best = max(found.values(), default=0)
    details = {"separate_at_each_radius": found, "most": best, "rays": count}
    if "core" in check:
        try:
            pieces, seen = pieces_from_core(depsgraph, targets, centre, float(check["core"]), min(check["radii"]),
                                            low_z, high_z)
        except ValueError as error:
            return False, dict(details, reason="inconclusive: %s" % error)
        details.update(pieces_from_core=pieces, pieces=seen)
        best = details["most"] = max(best, pieces)
    low, high = check["count"]
    return low <= best <= high, details


def piece_bounds(depsgraph, target):
    """World-space ``(low, high)`` corners of every connected mesh piece of matching geometry, instances included."""
    import numpy
    import scenario_checks
    boxes, cache, vertices = [], {}, 0
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type != "MESH" or not scenario_checks._instance_matches(instance, target):
            continue
        mesh = obj.data
        key = mesh.as_pointer() if hasattr(mesh, "as_pointer") else id(mesh)
        if key not in cache:
            vertices += len(mesh.vertices)
            if vertices > MAX_VERTICES:
                raise ValueError("more than %d vertices to group" % MAX_VERTICES)
            labels, used = island_labels(mesh)
            points = _array(mesh.vertices, "co", 3, numpy.float32).astype(numpy.float64)
            _, index = numpy.unique(labels[used], return_inverse=True)
            cache[key] = (points[used], index, int(index.max()) + 1 if len(index) else 0)
        points, index, pieces = cache[key]
        if not pieces:
            continue
        matrix = numpy.array([list(row) for row in instance.matrix_world], dtype=numpy.float64)
        world = points @ matrix[:3, :3].T + matrix[:3, 3]
        low = numpy.full((pieces, 3), numpy.inf)
        high = numpy.full((pieces, 3), -numpy.inf)
        numpy.minimum.at(low, index, world)
        numpy.maximum.at(high, index, world)
        boxes += list(zip(low.tolist(), high.tolist()))
        if len(boxes) > MAX_GROUPED_PIECES:
            raise ValueError("more than %d pieces to group" % MAX_GROUPED_PIECES)
    return boxes


def grouped(boxes, gap):
    """``[(low, high)]`` of the groups of boxes that touch within ``gap`` (bounds of each group)."""
    parent = list(range(len(boxes)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            if all(boxes[a][0][axis] - gap <= boxes[b][1][axis] and boxes[b][0][axis] - gap <= boxes[a][1][axis]
                   for axis in range(3)):
                parent[root(a)] = root(b)
    groups = {}
    for index, (low, high) in enumerate(boxes):
        key = root(index)
        if key not in groups:
            groups[key] = [list(low), list(high)]
        else:
            groups[key][0] = [min(v, w) for v, w in zip(groups[key][0], low)]
            groups[key][1] = [max(v, w) for v, w in zip(groups[key][1], high)]
    return [(low, high) for low, high in groups.values()]


def _check_groups(bpy, scene, depsgraph, check):
    """Groups of touching mesh pieces (a window: frame, bars and glass) whose longest side is inside ``size``."""
    try:
        boxes = piece_bounds(depsgraph, check["target"])
    except ValueError as error:
        return False, {"reason": "inconclusive: %s" % error}
    groups = grouped(boxes, float(check.get("gap", 0.2)))
    sizes = sorted(round(max(high[axis] - low[axis] for axis in range(3)), 3) for low, high in groups)
    low, high = check.get("size", (0.0, math.inf))
    sized = [size for size in sizes if low <= size <= high]
    count_low, count_high = check["count"]
    return count_low <= len(sized) <= count_high, {"pieces": len(boxes), "groups": len(groups), "sized": len(sized),
                                                    "sizes": sizes[:40]}


def bearing_difference(first, second):
    """Degrees between two compass bearings (0 to 180)."""
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _check_light_from(bpy, scene, depsgraph, check):
    """A matching light shines from the stated side (a light shines along its own -Z, so it comes from its +Z)."""
    import scenario_checks
    seen = []
    for obj in scene.objects:
        if obj.type != "LIGHT" or not scenario_checks.matches(obj.name, check["target"]):
            continue
        source = obj.matrix_world.to_3x3().col[2]
        if math.hypot(source[0], source[1]) < 1e-4:
            seen.append({"name": obj.name, "azimuth_deg": None, "reason": "shines straight down"})
            continue
        azimuth = math.degrees(math.atan2(source[1], source[0])) % 360.0
        off = bearing_difference(azimuth, check["azimuth_deg"])
        seen.append({"name": obj.name, "azimuth_deg": round(azimuth, 1), "off_deg": round(off, 1)})
        if off <= check.get("within_deg", 45.0):
            return True, seen[-1]
    return False, {"lights": seen[:6] or "no matching light"}


CHECKS = {"pieces": _check_pieces, "ring": _check_ring, "groups": _check_groups, "light_from": _check_light_from}
