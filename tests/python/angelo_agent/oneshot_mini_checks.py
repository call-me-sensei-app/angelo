# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed checks for the mini one-shot tests: everyday requests that name nothing.

A mini is one short message on the factory scene ("add a stone footbridge at
the origin: a deck 4 m long..."). It gives no object names, so these kinds
find what was asked for by shape and measure it (``oneshot_checks`` registers
them with ``scenario_checks``):

* what was **built** is every geometry object except the factory scene's own
  cube as it was (2 m, at the origin, eight vertices, no modifier). A mini
  starts from the factory scene and does not ask for it to be cleared, so
  the untouched cube is looked past, never held against the result; a cube
  the agent reshaped into part of the build counts as built;
* ``plan``: the horizontal extent and centre of what was built;
* ``slab``: a walkable top surface of a stated plan size (rays cast down
  through it), flat or rising towards mid-span by a stated amount;
* ``members``: connected pieces of a stated shape, counted: upright square
  posts, long thin rails;
* ``bevel``: an edge bevel of a stated width and segment count, as a Bevel
  modifier or as bevelled base geometry (either way a person sees the same);
* ``modifier_settings``: a modifier of a type with stated values;
* ``light_rig``: lights of a type, each aimed at the subject, a stated number
  of them behind it as seen from the scene camera (more than about 107
  degrees round from the camera, so a fill well out to the side is not
  taken for a rim), the key stronger than the fill.

Read-only, like every check. Importable without Blender.
"""

import math

GEOMETRY = frozenset({"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES"})
MAX_VERTICES = 400000
MAX_HOPS = 64


def untouched_cube(obj):
    """The factory scene's own cube exactly as it was; anything else called Cube was worked on."""
    try:
        return (obj.name == "Cube" and obj.type == "MESH" and len(obj.data.vertices) == 8 and not len(obj.modifiers)
                and all(abs(value - 2.0) < 1e-3 for value in obj.dimensions)
                and all(abs(value) < 1e-3 for value in obj.location))
    except (AttributeError, TypeError):
        return False


def built_objects(scene):
    """Geometry objects the message added or changed (see the module docstring)."""
    return [obj for obj in scene.objects if obj.type in GEOMETRY and not untouched_cube(obj)]


def _built_names(scene):
    return {obj.name for obj in built_objects(scene)}


def _owner(instance):
    """The scene object an evaluated instance belongs to: itself, or the object that instances it."""
    if instance.is_instance and instance.parent is not None:
        return instance.parent.original.name
    return instance.object.original.name


def components(depsgraph, names):
    """World bounds ``(low, high)`` of every connected mesh piece of the named objects (instances included)."""
    pieces, total, cache = [], 0, {}
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type != "MESH" or _owner(instance) not in names:
            continue
        mesh = obj.data
        key = mesh.as_pointer()
        if key not in cache:
            total += len(mesh.vertices)
            if total > MAX_VERTICES:
                raise ValueError("too many vertices to separate pieces (%d)" % total)
            parent = list(range(len(mesh.vertices)))

            def root(index):
                while parent[index] != index:
                    parent[index] = parent[parent[index]]
                    index = parent[index]
                return index
            for edge in mesh.edges:
                a, b = root(edge.vertices[0]), root(edge.vertices[1])
                if a != b:
                    parent[a] = b
            groups = {}
            for vertex in mesh.vertices:
                groups.setdefault(root(vertex.index), []).append(vertex.co.copy())
            cache[key] = list(groups.values())
        matrix = instance.matrix_world
        for local in cache[key]:
            points = [matrix @ point for point in local]
            pieces.append(([min(p[axis] for p in points) for axis in range(3)],
                           [max(p[axis] for p in points) for axis in range(3)]))
    return pieces


def _extent(pieces):
    if not pieces:
        return None
    return ([min(low[axis] for low, _ in pieces) for axis in range(3)],
            [max(high[axis] for _, high in pieces) for axis in range(3)])


def _round(values, digits=3):
    return [round(float(value), digits) for value in values]


def _check_plan(bpy, scene, depsgraph, check):
    """The horizontal size (longer and shorter side) and centre of everything that was built."""
    bounds = _extent(components(depsgraph, _built_names(scene)))
    if bounds is None:
        return False, {"reason": "nothing was built"}
    low, high = bounds
    sides = sorted((high[0] - low[0], high[1] - low[1]), reverse=True)
    centre = [(low[axis] + high[axis]) / 2 for axis in range(2)]
    off = math.dist(centre, check.get("centre", (0.0, 0.0)))
    problems = [name for name, value in (("long", sides[0]), ("short", sides[1]))
                if name in check and not check[name][0] <= value <= check[name][1]]
    if off > check.get("radius", math.inf):
        problems.append("centre")
    return not problems, {"long": round(sides[0], 3), "short": round(sides[1], 3), "centre": _round(centre),
                          "off_centre": round(off, 3), "problems": problems}


def top_height(scene, depsgraph, x, y, names, ceiling=200.0):
    """Height of the first built surface a ray meets going straight down at (x, y), or None."""
    from mathutils import Vector
    start, down, travelled = Vector((x, y, ceiling)), Vector((0.0, 0.0, -1.0)), 0.0
    for _ in range(MAX_HOPS):
        hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, start, down,
                                                                       distance=2 * ceiling - travelled)
        if not hit:
            return None
        owners = {obj.original.name} | ({obj.parent.original.name} if obj.parent else set())
        if owners & names:
            return float(location.z)
        travelled += (location - start).length + 1e-4
        start = location + down * 1e-4
    return None


def _check_slab(bpy, scene, depsgraph, check):
    """A top surface ``size`` (length, width) around ``centre``, along whichever horizontal axis the build runs.

    ``cover``: least share of the sampled rays that meet it; ``rise``: allowed
    range of mid-span height minus the mean height of the two ends.
    """
    names = _built_names(scene)
    bounds = _extent(components(depsgraph, names))
    if bounds is None:
        return False, {"reason": "nothing was built"}
    low, high = bounds
    along_x = (high[0] - low[0]) >= (high[1] - low[1])
    length, width = check["size"]
    cx, cy = check.get("centre", (0.0, 0.0))
    stations, across = check.get("stations", 17), (-0.25, 0.0, 0.25)   # Away from the edges, where rails stand.
    profile, hits = [], 0
    for index in range(stations):
        s = (index / (stations - 1) - 0.5) * check.get("span", length - 0.24)
        row = []
        for share in across:
            t = share * width
            x, y = (cx + s, cy + t) if along_x else (cx + t, cy + s)
            row.append(top_height(scene, depsgraph, x, y, names))
        found = sorted(value for value in row if value is not None)
        hits += len(found)
        profile.append(found[len(found) // 2] if found else None)
    cover = hits / (stations * len(across))
    known = [value for value in profile if value is not None]
    ends = [value for value in (profile[0], profile[-1]) if value is not None]
    middle = profile[stations // 2]
    rise = None if middle is None or len(ends) < 2 else middle - sum(ends) / 2
    problems = []
    if cover < check.get("cover", 1.0):
        problems.append("cover")
    if "rise" in check and (rise is None or not check["rise"][0] <= rise <= check["rise"][1]):
        problems.append("rise")
    return not problems, {"axis": "x" if along_x else "y", "cover": round(cover, 3),
                          "rise": None if rise is None else round(rise, 3),
                          "top": [round(min(known), 3), round(max(known), 3)] if known else None, "problems": problems}


def _is_post(dims, check):
    """Upright and square in plan: taller than ``min_height``, slender, its two plan sides alike."""
    wide, narrow = max(dims[0], dims[1]), min(dims[0], dims[1])
    return (dims[2] >= check.get("min_height", 0.4) and wide <= check.get("max_side", 0.4)
            and wide <= 0.45 * dims[2] and narrow >= 0.7 * wide)


def _is_rail(dims, check):
    """Long and thin: ``min_length`` or more along one horizontal axis, small across, low in height."""
    long, across = max(dims[0], dims[1]), min(dims[0], dims[1])
    return (long >= check.get("min_length", 2.8) and across <= check.get("max_across", 0.3)
            and dims[2] <= check.get("max_height", 0.35))


SHAPES = {"post": _is_post, "rail": _is_rail}


def _check_members(bpy, scene, depsgraph, check):
    """How many connected pieces of what was built have the stated shape."""
    pieces = components(depsgraph, _built_names(scene))
    sizes = [[high[axis] - low[axis] for axis in range(3)] for low, high in pieces]
    found = [dims for dims in sizes if SHAPES[check["shape"]](dims, check)]
    low, high = check["count"]
    return low <= len(found) <= high, {"pieces": len(pieces), check["shape"] + "s": len(found),
                                       "sizes": [_round(dims) for dims in found[:12]]}


def bevel_of_mesh(mesh):
    """``(width, segments)`` of an all-edge bevel in a box's base mesh, read from its vertex coordinates.

    A bevelled box keeps its flat faces at the full half-size and gains a profile between a face and its
    neighbour: from the full half-size down to half-size minus the width, in one step per segment. The
    profile is read where an edge's strip ends (one coordinate at half-size minus the width), which leaves
    out the vertices inside the corner patches.
    """
    coordinates = [[abs(float(value)) for value in vertex.co] for vertex in mesh.vertices]
    if not coordinates:
        return 0.0, 0
    outer = max(point[0] for point in coordinates)
    inner = min(point[0] for point in coordinates if point[0] > outer / 2)
    if outer - inner <= 1e-5:
        return 0.0, 0
    profile = {round(point[0], 4) for point in coordinates
               if abs(point[2] - inner) < 2e-4 and point[1] >= inner - 2e-4}
    return round(outer - inner, 4), max(len(profile) - 1, 0)


def _check_bevel(bpy, scene, depsgraph, check):
    """An edge bevel of ``width`` (within ``tolerance``) and ``segments`` on the target: modifier or geometry."""
    import scenario_checks
    width, segments, tolerance = check["width"], check["segments"], check.get("tolerance", 0.002)
    seen = []
    for obj in scenario_checks.scene_objects(scene, check["target"]):
        for modifier in getattr(obj, "modifiers", ()):
            if modifier.type == "BEVEL" and modifier.show_viewport:
                seen.append({"object": obj.name, "route": "modifier", "width": round(float(modifier.width), 4),
                             "segments": int(modifier.segments)})
        if obj.type == "MESH":
            measured = bevel_of_mesh(obj.data)
            if measured[1]:
                seen.append({"object": obj.name, "route": "geometry", "width": measured[0], "segments": measured[1]})
    for entry in seen:
        if abs(entry["width"] - width) <= tolerance and entry["segments"] == segments:
            return True, entry
    return False, {"bevels": seen[:6] or "no bevel on a matching object"}


def _check_modifier_settings(bpy, scene, depsgraph, check):
    """A modifier of ``type`` that shows in the viewport and has every stated value."""
    import scenario_checks
    seen = []
    for obj in scenario_checks.scene_objects(scene, check["target"]):
        for modifier in getattr(obj, "modifiers", ()):
            if modifier.type != check["type"] or not modifier.show_viewport:
                continue
            values = {name: getattr(modifier, name, None) for name in check["values"]}
            seen.append({"object": obj.name, "modifier": modifier.name, "values": values})
            if all(type(values[name]) in (int, float, bool) and abs(values[name] - wanted) <= 1e-4
                   for name, wanted in check["values"].items()):
                return True, seen[-1]
    return False, {"modifiers": seen[:6] or "no %s modifier on a matching object" % check["type"]}


BEHIND_COSINE = -0.3   # A light is behind the subject when it stands more than about 107 degrees from the camera.


def _check_light_rig(bpy, scene, depsgraph, check):
    """Lights of ``type``: how many, each aimed at ``aim``, how many behind it from the scene camera, key over fill."""
    from mathutils import Vector
    aim = Vector(check.get("aim", (0.0, 0.0, 0.0)))
    viewer = scene.camera.matrix_world.translation if scene.camera is not None else None
    view = None if viewer is None or (viewer - aim).length < 1e-6 else (viewer - aim).normalized()
    lights = []
    for obj in scene.objects:
        if obj.type != "LIGHT" or obj.data.type != check["type"]:
            continue
        position = obj.matrix_world.translation
        towards = aim - position
        forward = -(obj.matrix_world.to_3x3().col[2]).normalized()
        distance = max(towards.length, 1e-6)
        lights.append({"name": obj.name, "energy": round(float(obj.data.energy), 3),
                       "aim_dot": round(forward.dot(towards.normalized()), 4),
                       "behind": None if view is None else (position - aim).normalized().dot(view) < BEHIND_COSINE,
                       "reach": float(obj.data.energy) / (distance * distance)})
    problems = []
    low, high = check["count"]
    if not low <= len(lights) <= high:
        problems.append("count")
    if any(light["aim_dot"] < check.get("min_dot", 0.9) for light in lights):
        problems.append("aim")
    if "behind" in check:
        behind = sum(light["behind"] is True for light in lights)
        if view is None or not check["behind"][0] <= behind <= check["behind"][1]:
            problems.append("behind the subject")
    front = sorted((light["reach"] for light in lights if light["behind"] is False), reverse=True)
    if "key_over_fill" in check and (len(front) < 2 or front[0] < check["key_over_fill"] * front[1]):
        problems.append("key no stronger than fill")
    for light in lights:
        light["reach"] = round(light["reach"], 3)
    return not problems, {"lights": lights[:8], "problems": problems}


CHECKS = {"plan": _check_plan, "slab": _check_slab, "members": _check_members, "bevel": _check_bevel,
          "modifier_settings": _check_modifier_settings, "light_rig": _check_light_rig}
