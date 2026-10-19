# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check kinds for a castle brief (``oneshot-castle``): measured with rays and bounds, never the model's word.

Like every one-shot kind they ask only for what the brief states, however it
was built (one object or a hundred, primitives, modifiers or instances):

* ``round_tower``: horizontal rays from all round a stated centre meet a
  matching tower at nearly the same distance from its axis (round, not
  square), and rays from above meet a roof whose top is high in the middle
  and falls away towards the eaves (conical, not flat), in a colour family.
* ``crenellations``: rays straight down along the wall-walk lines; the runs
  where they meet something clearly higher than the walk are the merlons.
* ``encloses``: rays out from the courtyard in every direction meet the wall,
  a tower or the gatehouse: no gap a person could walk through above the gate.
* ``arch``: under the gatehouse, rays straight up meet its underside higher in
  the middle of the passage than near its sides, and a ray through the gate
  at head height meets nothing of the castle.
* ``supported``: every connected piece of the castle rests on the ground or
  touches a piece that does: nothing floats.
* ``apart``: the footprints of separate towers and of the keep do not overlap.
* ``frames``: the scene camera sees the whole of the matching objects, and
  they fill a stated share of the frame.
* ``tallest``: the highest point of one target is above every other's.

Every kind passes through objects that do not match its targets, but a
matching face sharing the surface it met still counts (``_shared_surface``). Ray origins
are nudged by ``JITTER`` so that no ray runs exactly along a mesh edge, where a
ray cast can slip between two triangles.
"""

import math

MAX_HOPS = 64
JITTER = (0.0137, 0.0071)
GAP = 1e-3      # Metres between bounds that still touch: float error, not a visible gap.
SHARED = 1e-3   # Metres along a ray within which a matching face is the same surface as a passed one.
MAX_FRAMED_POINTS = 20000


def _targets(check):
    return check.get("targets") or [check["target"]]


def _matches(names, targets):
    import scenario_checks
    return any(scenario_checks.matches(name, target) for name in names for target in targets)


def first_hit(scene, depsgraph, origin, direction, distance, targets, accept=None):
    """``(location, object, face index)`` of the first matching hit along a ray (one ``accept`` takes, when
    given), or None."""
    from mathutils import Vector
    start, step = Vector((origin[0] + JITTER[0], origin[1] + JITTER[1], origin[2])), Vector(direction).normalized()
    travelled, matching = 0.0, None
    for _ in range(MAX_HOPS):
        remaining = distance - travelled
        if remaining <= 0:
            return None
        hit, location, _normal, index, obj, _matrix = scene.ray_cast(depsgraph, start, step, distance=remaining)
        if not hit:
            return None
        if _matches([obj.original.name], targets) and (accept is None or accept(location)):
            return location, obj, index
        if matching is None:
            matching = [part for part in scene.objects if part.type == "MESH" and _matches([part.name], targets)]
        shared = _shared_surface(depsgraph, location, step, matching, accept)
        if shared is not None:
            return shared
        travelled += (location - start).length + 1e-4
        start = location + step * 1e-4
    return None


def _shared_surface(depsgraph, location, step, matching, accept):
    """A face of a ``matching`` mesh at the place where the ray met a face that does not match, or None.

    Two parts built on one outline share a surface: a curtain wall run on
    through the gatehouse and cut by the same arch has the gatehouse's arch
    underside exactly where its own is. The ray meets one of the two; stepping
    on past it must not skip the matching one (RC8 castle one-shot: one Angelo
    and one MCP trial read a round arch as "flat" this way). Only a face within
    ``SHARED`` of the hit counts, so nothing a non-matching part hides further
    along the ray is ever reported.
    """
    found = None
    for obj in matching:
        world = obj.evaluated_get(depsgraph).matrix_world
        inverse = world.inverted_safe()
        direction = inverse.to_3x3() @ step
        scale = direction.length
        if scale <= 0.0:
            continue
        hit, local, _normal, index = obj.ray_cast(inverse @ (location - step * SHARED), direction / scale,
                                                  distance=2 * SHARED * scale, depsgraph=depsgraph)
        point = world @ local
        if hit and (accept is None or accept(point)) and (
                found is None or (point - location).length < (found[0] - location).length):
            found = (point, obj.evaluated_get(depsgraph), index)
    return found


def _hit_colour(obj, index, families):
    """``(verdict, family)`` of the material on the face a ray met: whether that face surely shows one of the
    colour families (None when an unreadable part of its graph decides it), and its mean colour's family."""
    import oneshot_checks
    import scenario_checks
    import surface_reading
    try:
        slot = obj.data.polygons[index].material_index
        material = obj.material_slots[slot].material if slot < len(obj.material_slots) else None
        source = surface_reading.SurfaceSource(obj.data, [list(row) for row in obj.matrix_world], [slot], [index])
    except (AttributeError, IndexError, TypeError):
        return None, None
    if material is None:
        return False, None
    facts = oneshot_checks.surface_facts(getattr(material, "original", None) or material, source)
    verdict = scenario_checks.reading_verdict(facts, "base_color",
                                              lambda rgb: scenario_checks.colour_family(rgb) in families)
    colour = facts["base_color"]
    return verdict, None if colour is None else scenario_checks.colour_family(colour)


def _check_round_tower(bpy, scene, depsgraph, check):
    targets = _targets(check)
    cx, cy = check["centre"]
    reach, rays = float(check.get("reach", 14.0)), int(check.get("rays", 16))
    nearest = check["radius"][1] + 1.0   # A hit further out is another tower, passed through.
    around = lambda location: math.hypot(location[0] - cx, location[1] - cy) <= nearest
    # Parts the brief sets in the tower (its window): a ray meeting one has met the tower there, but the window's
    # face is not the tower's surface, so it never sets the measured radius.
    set_in = check.get("set_in", ())
    radii, skipped, met = [], 0, 0
    for index in range(rays):
        angle = 2 * math.pi * index / rays
        direction = (math.cos(angle), math.sin(angle), 0.0)
        # A bearing whose stated surface lies inside a part the brief sets across the tower (a gate tower inside
        # the gatehouse block) meets that part, not the tower: it is not asked.
        surface = (cx + check.get("nominal", 0.0) * direction[0], cy + check.get("nominal", 0.0) * direction[1])
        if any(x0 <= surface[0] <= x1 and y0 <= surface[1] <= y1 for x0, y0, x1, y1 in check.get("skip", ())):
            skipped += 1
            continue
        origin = (cx + reach * direction[0], cy + reach * direction[1], check["height"])
        found = first_hit(scene, depsgraph, origin, [-v for v in direction], reach, list(targets) + list(set_in),
                          around)
        if found is not None:
            met += 1
            if not _matches([found[1].original.name], set_in):
                radii.append(math.hypot(found[0][0] - cx, found[0][1] - cy))
    problems = []
    mean = sum(radii) / len(radii) if radii else 0.0
    asked = rays - skipped
    if met < asked - check.get("misses", 0):
        problems.append("%d of %d rays met no tower" % (asked - met, asked))
    elif not check["radius"][0] <= mean <= check["radius"][1]:
        problems.append("radius %.2f" % mean)
    spread = (max(radii) - min(radii)) / mean if radii and mean else math.inf
    if radii and spread > check.get("roundness", 0.2):
        problems.append("not round (spread %.2f)" % spread)
    radius = mean or check["radius"][0]
    heights, colour, colour_ok, roof_seen = [], None, None, False
    for fraction in (0.0, 0.5, 0.85):
        reach_out = fraction * radius
        samples = [(cx, cy)] if not fraction else [(cx + reach_out * math.cos(a), cy + reach_out * math.sin(a))
                                                   for a in (0.0, math.pi / 2, math.pi, 1.5 * math.pi)]
        found = [first_hit(scene, depsgraph, (x, y, 200.0), (0.0, 0.0, -1.0), 200.0, targets, around)
                 for x, y in samples]
        heights.append(None if any(hit is None for hit in found) else sum(hit[0][2] for hit in found) / len(found))
        if not fraction and found[0] is not None:
            colour_ok, colour = _hit_colour(found[0][1], found[0][2], check.get("roof_color") or ())
            roof_seen = True
    top, middle, eaves = heights
    if None in heights:
        problems.append("no roof seen from above")
    else:
        if not check["apex"][0] <= top <= check["apex"][1]:
            problems.append("top at %.2f" % top)
        if not top > middle > eaves or top - eaves < check.get("pointed", 2.0):
            problems.append("roof not pointed")
    undetermined = []
    if check.get("roof_color"):
        if not roof_seen or colour_ok is False:
            problems.append("roof colour %s" % colour)
        elif colour_ok is None:
            undetermined.append("inconclusive: roof colour %s" % colour)
    details = {"radius": round(mean, 3), "spread": None if not radii else round(spread, 3),
               "roof_heights": [None if h is None else round(h, 3) for h in heights],
               "roof_colour": colour, "problems": problems + undetermined}
    if undetermined and not problems:
        details["inconclusive"] = True
    return not problems and not undetermined, details


def merlon_runs(heights, rise):
    """How many separate runs of samples stand ``rise`` above the walk (the lower fifth of the samples)."""
    known = sorted(h for h in heights if h is not None)
    if not known:
        return 0, None
    walk = known[len(known) // 5]
    runs, inside = 0, False
    for height in heights:
        high = height is not None and height >= walk + rise
        runs += high and not inside
        inside = high
    return runs, walk


def _check_crenellations(bpy, scene, depsgraph, check):
    targets, step, total, lines = _targets(check), float(check.get("step", 0.1)), 0, []
    for x0, y0, x1, y1 in check["lines"]:
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        heights = []
        for index in range(int(length / step) + 1):
            best = None
            for across in check.get("across", (0.0,)):
                x, y = x0 + ux * index * step - uy * across, y0 + uy * index * step + ux * across
                found = first_hit(scene, depsgraph, (x, y, 200.0), (0.0, 0.0, -1.0), 200.0, targets)
                if found is not None and (best is None or found[0][2] > best):
                    best = found[0][2]
            heights.append(best)
        runs, walk = merlon_runs(heights, float(check.get("rise", 0.5)))
        total += runs
        lines.append({"merlons": runs, "walk": None if walk is None else round(walk, 2),
                      "missing": sum(h is None for h in heights)})
    low, high = check["count"]
    return low <= total <= high, {"merlons": total, "lines": lines}


def _check_encloses(bpy, scene, depsgraph, check):
    targets, (cx, cy, cz) = _targets(check), check["centre"]
    rays, gaps = int(check.get("rays", 72)), []
    for index in range(rays):
        angle = 2 * math.pi * (index + 0.31) / rays
        if first_hit(scene, depsgraph, (cx, cy, cz), (math.cos(angle), math.sin(angle), 0.0), check["distance"],
                     targets) is None:
            gaps.append(round(math.degrees(angle), 1))
    return not gaps, {"rays": rays, "gaps_deg": gaps[:24]}


def _check_arch(bpy, scene, depsgraph, check):
    targets, heights = _targets(check), {}
    for x in check["x"]:
        found = first_hit(scene, depsgraph, (x, check["y"], 0.3), (0.0, 0.0, 1.0), 60.0, targets)
        heights[x] = None if found is None else round(found[0][2], 3)
    problems = []
    crown = heights[min(check["x"], key=abs)]
    sides = [heights[x] for x in check["x"] if abs(x) >= check.get("side_from", 1.4)]
    if crown is None or None in sides:
        problems.append("no underside over the passage")
    else:
        if not check["crown"][0] <= crown <= check["crown"][1]:
            problems.append("crown at %.2f" % crown)
        if crown - max(sides) < check.get("rise", 0.4):
            problems.append("flat, not arched")
    clear = check.get("clear")
    blocked = None
    if clear:
        found = first_hit(scene, depsgraph, clear["origin"], clear["direction"], clear["distance"],
                          clear.get("targets") or targets)
        blocked = None if found is None else found[1].original.name
        if blocked:
            problems.append("the gateway is closed by %s" % blocked)
    return not problems, {"underside": {str(x): z for x, z in heights.items()}, "blocked_by": blocked,
                          "problems": problems}


def _boxes(depsgraph, target):
    import oneshot_form_checks
    return oneshot_form_checks.piece_bounds(depsgraph, target)


def _touch(a, b, gap):
    return all(a[0][axis] - gap <= b[1][axis] and b[0][axis] - gap <= a[1][axis] for axis in range(3))


def _check_supported(bpy, scene, depsgraph, check):
    """Every piece is reached from the ground through pieces whose bounds touch (within ``gap``).

    ``gap`` absorbs float error only: a piece that floats by any visible distance is not resting on anything.
    The ground is the top of ``ground_target``'s geometry when the scene has one (the brief's own ground, wherever
    its checks allow it to sit), else height 0."""
    try:
        boxes = _boxes(depsgraph, check["target"])
        floor = _boxes(depsgraph, check["ground_target"]) if check.get("ground_target") else []
    except ValueError as error:
        return False, {"reason": "inconclusive: %s" % error}
    gap = float(check.get("gap", GAP))
    ground = max([box[1][2] for box in floor] or [0.0]) + gap
    reached = [box[0][2] <= ground for box in boxes]
    queue = [index for index, flag in enumerate(reached) if flag]
    while queue:
        current = boxes[queue.pop()]
        for index, box in enumerate(boxes):
            if not reached[index] and _touch(current, box, gap):
                reached[index] = True
                queue.append(index)
    floating = [[round(v, 2) for v in boxes[index][0] + boxes[index][1]] for index, flag in enumerate(reached)
                if not flag]
    return bool(boxes) and not floating, {"pieces": len(boxes), "floating": len(floating),
                                          "floating_bounds": floating[:8]}


def _footprints(depsgraph, target, gap):
    import oneshot_form_checks
    return oneshot_form_checks.grouped(_boxes(depsgraph, target), gap)


def _check_apart(bpy, scene, depsgraph, check):
    """No two groups of separate targets (each tower, the keep) overlap by more than ``max_overlap`` in plan."""
    groups = []
    try:
        for target in check["targets"]:
            groups += _footprints(depsgraph, target, float(check.get("gap", 0.3)))
    except ValueError as error:
        return False, {"reason": "inconclusive: %s" % error}
    worst = 0.0
    for a in range(len(groups)):
        for b in range(a + 1, len(groups)):
            (low_a, high_a), (low_b, high_b) = groups[a], groups[b]
            width = min(high_a[0], high_b[0]) - max(low_a[0], low_b[0])
            depth = min(high_a[1], high_b[1]) - max(low_a[1], low_b[1])
            if width <= 0 or depth <= 0:
                continue
            smaller = min((high_a[0] - low_a[0]) * (high_a[1] - low_a[1]),
                          (high_b[0] - low_b[0]) * (high_b[1] - low_b[1]))
            worst = max(worst, width * depth / smaller if smaller > 0 else 1.0)
    return worst <= check.get("max_overlap", 0.1), {"groups": len(groups), "worst_overlap": round(worst, 3)}


def framed_points(depsgraph, target, limit=MAX_FRAMED_POINTS):
    """World-space points of the matching evaluated meshes (every n-th vertex, at most ``limit``)."""
    import numpy
    import scenario_checks
    meshes = [(instance.object.data, numpy.array(instance.matrix_world, dtype=numpy.float64))
              for instance in depsgraph.object_instances
              if instance.object.type == "MESH" and scenario_checks._instance_matches(instance, target)]
    total = sum(len(data.vertices) for data, _ in meshes)
    stride, points = max(1, total // limit), []
    for data, matrix in meshes:
        local = numpy.empty(len(data.vertices) * 3, numpy.float32)
        data.vertices.foreach_get("co", local)
        local = local.reshape(-1, 3)[::stride].astype(numpy.float64)
        points.append(local @ matrix[:3, :3].T + matrix[:3, 3])
    return numpy.concatenate(points) if points else numpy.empty((0, 3))


def _check_frames(bpy, scene, depsgraph, check):
    """The targets' vertices project inside the scene camera's frame, filling a share of it."""
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector
    camera = scene.camera
    points = framed_points(depsgraph, check["target"])
    if camera is None or not len(points):
        return False, {"reason": "no scene camera" if camera is None else "no matching geometry"}
    projected = [world_to_camera_view(scene, camera, Vector(point)) for point in points]
    margin = check.get("margin", 0.02)
    inside = all(-margin <= p.x <= 1 + margin and -margin <= p.y <= 1 + margin and p.z > 0 for p in projected)
    left, right = min(p.x for p in projected), max(p.x for p in projected)
    bottom, top = min(p.y for p in projected), max(p.y for p in projected)
    fill = max(right - left, top - bottom)
    return inside and check["fill"][0] <= fill <= check["fill"][1], {
        "camera": camera.name, "in_frame": inside, "fill": round(fill, 3), "points": len(projected),
        "frame": [round(left, 3), round(bottom, 3), round(right, 3), round(top, 3)]}


def _check_tallest(bpy, scene, depsgraph, check):
    import scenario_checks
    mine = scenario_checks.evaluated_bounds(bpy, depsgraph, check["target"])
    others = [scenario_checks.evaluated_bounds(bpy, depsgraph, target) for target in check["others"]]
    if mine is None or not any(others):
        return False, {"reason": "no matching geometry"}
    highest = max(bounds[1][2] for bounds in others if bounds)
    return mine[1][2] >= highest + check.get("margin", 0.0), {"top": round(mine[1][2], 3),
                                                              "highest_other": round(highest, 3)}


CHECKS = {"round_tower": _check_round_tower, "crenellations": _check_crenellations, "encloses": _check_encloses,
          "arch": _check_arch, "supported": _check_supported, "apart": _check_apart, "frames": _check_frames,
          "tallest": _check_tallest}
