# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed, construction-agnostic host checks for the scenario benchmarks.

Every scenario turn (``scenario_specs``) carries a list of checks derived from
its ground-truth scene. ``evaluate(bpy, scene, checks)`` runs them inside
Blender, read-only, and returns ``{"success": bool, "results": [...]}`` with
the measured values of every check. The same code grades both benchmark sides:
in-process in Angelo's trial driver, and over the MCP add-on's socket in stock
Blender (``bench_mcp_grading.scenario_grade_code``).

The checks measure what a person sees, never how it was built. A lamp array is
found by ray probes at the expected spacing, whether it is an Array modifier,
duplicates or Geometry Nodes instances; sizes are world-space bounds of the
evaluated geometry including instances. Objects are matched by the names the
prompts state: ``{"name": "Valley"}`` (also ``Valley.001``) or
``{"prefix": "Lamp"}`` (every object whose name starts with it, and every
instance of such an object), or as a whole thing: ``{"whole": "Parked Van"}`` (the object of that name, the parts
named with it as a word prefix, and everything parented under them, so a box measures the whole van however it is
built: one mesh, a body with named parts, or an Empty with children). Textured inputs that a closed check cannot read
fail with an ``inconclusive`` reason rather than passing silently.

Only ``frame_set`` changes state (motion checks sample frames); the original
frame is restored before returning. Nothing else is written.

A turn may also name files the session must have produced (``outputs``: a
finished render). They are facts about the session's folders, not the scene,
so ``evaluate_outputs`` takes the folders from the driver that owns them and
is never part of the end-of-session pass on the saved scene. A file is found
by name, its size read from its header; hidden folders (a background job's
staging) never count.
"""

import colorsys
import math
import os
import re
import struct

import shader_graph
import surface_reading
import world_background

MAX_PROBE_HOPS = 64
MAX_PART_VERTICES = 400000
MAX_COUNTED_INSTANCES = 5000000
MAX_OUTPUT_DEPTH = 4
MAX_OUTPUT_ENTRIES = 20000
_SUFFIX = re.compile(r"\.\d{3,}$")
# The sky a world shows is sampled in two bands, each five 3-degree rings of eight directions 45 degrees apart:
# the lowest 15 degrees above the horizon (what a level camera sees behind the scene) and the top 15 (overhead).
HORIZON_ELEVATIONS_DEG = (1.5, 4.5, 7.5, 10.5, 13.5)
ZENITH_ELEVATIONS_DEG = (76.5, 79.5, 82.5, 85.5, 88.5)
SKY_AZIMUTHS_DEG = (0, 45, 90, 135, 180, 225, 270, 315)
MIN_BAND_SHARE = 0.5   # A band shows a colour family when at least half of its directions show it.


# ---------------------------------------------------------------- matching

def matches(name, target):
    """Whether an object name matches a check target (``name``: exact or Blender's .001 copy; ``prefix``, with
    ``word`` the prefix must end a word: 'Castle Wall' then admits 'Castle Wall 2', not 'Castle Wallflower';
    ``any``: one of several targets, e.g. a wall and the windows the brief sets in it; ``except``: targets a
    match must not also be, e.g. every part of the castle but its keep; ``whole``: the whole thing a brief names,
    by name its exact name, a Blender copy, or a part named with it as a word prefix ('Parked Van Cabin'), and,
    through ``object_matches``, every object parented under one of those)."""
    if any(matches(name, each) for each in target.get("except", ())):
        return False
    if "any" in target:
        return any(matches(name, each) for each in target["any"])
    if "whole" in target:
        whole = target["whole"]
        return name.startswith(whole) and (len(name) == len(whole) or name[len(whole)] in " ._-")
    if "prefix" in target:
        prefix = target["prefix"]
        return name.startswith(prefix) and (not target.get("word") or len(name) == len(prefix)
                                            or name[len(prefix)] in " ._-")
    return name == target["name"] or _SUFFIX.sub("", name) == target["name"]


def _has_whole(target):
    return "whole" in target or any(_has_whole(each) for each in target.get("any", ()))


def object_matches(obj, target):
    """Whether an object matches a target: by its own name, and for a ``whole`` target also when an object it is
    parented under matches (an Empty named for the thing counts its children; a body's parented parts count)."""
    if matches(obj.name, target):
        return True
    if not _has_whole(target) or any(matches(obj.name, each) for each in target.get("except", ())):
        return False
    parent = obj.parent
    while parent is not None:
        if matches(parent.name, target):
            return True
        parent = parent.parent
    return False


def target_text(target):
    if "any" in target:
        text = " or ".join(target_text(each) for each in target["any"])
    elif "whole" in target:
        text = "'%s' (with its parts named '%s ...' and everything parented under them)" % (target["whole"],
                                                                                              target["whole"])
    else:
        text = target.get("name") or (target.get("prefix", "") + "*")
    if target.get("except"):
        text += " but not " + " or ".join(target_text(each) for each in target["except"])
    return text


def looked_for(target):
    """What a target admits, in words: an exact name also admits Blender's own copy names ('Pier', 'Pier.001')."""
    if "name" in target and not ("any" in target or "prefix" in target):
        text = "an object named '%s' or a Blender copy of it ('%s.001')" % (target["name"], target["name"])
        return text + (" but not " + " or ".join(target_text(each) for each in target["except"])
                       if target.get("except") else "")
    return "an object named " + target_text(target)


def _instance_matches(instance, target):
    objects = [instance.object.original]
    if instance.is_instance and instance.parent is not None:
        objects.append(instance.parent.original)
    return any(object_matches(obj, target) for obj in objects)


def scene_objects(scene, target):
    return [obj for obj in scene.objects if object_matches(obj, target)]


def _vector(values):
    return [round(float(v), 4) for v in values]


# ---------------------------------------------------------------- geometry

def evaluated_bounds(bpy, depsgraph, target):
    """World-space min/max of the evaluated geometry (instances included) of matching objects, or None."""
    from mathutils import Vector
    low, high = [math.inf] * 3, [-math.inf] * 3
    found = False
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME",
                            "GREASEPENCIL"} or not _instance_matches(instance, target):
            continue
        matrix = instance.matrix_world
        for corner in obj.bound_box:
            point = matrix @ Vector(corner)
            for axis in range(3):
                low[axis], high[axis] = min(low[axis], point[axis]), max(high[axis], point[axis])
            found = True
    return (low, high) if found else None


def probe(scene, depsgraph, origin, direction, distance, target):
    """First hit of a matching object along a ray, passing through any other geometry."""
    from mathutils import Vector
    start, step = Vector(origin), Vector(direction).normalized()
    travelled, first_other = 0.0, None
    for _ in range(MAX_PROBE_HOPS):
        remaining = distance - travelled
        if remaining <= 0:
            break
        hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, start, step, distance=remaining)
        if not hit:
            break
        if object_matches(obj.original, target):   # By its own name (a ``whole`` target also by its parents).
            return {"hit": True, "object": obj.original.name, "location": _vector(location),
                    "distance": round(travelled + (location - start).length, 4)}
        first_other = first_other or obj.original.name
        travelled += (location - start).length + 1e-4
        start = location + step * 1e-4
    # A miss says what the ray looked for, so a reader (or the model asked why) can tell a misnamed part from a gap.
    return {"hit": False, "looked_for": looked_for(target), "passed_through": first_other}


def mesh_parts(depsgraph, target, region_min, region_max):
    """Connected mesh pieces of matching evaluated geometry whose centres lie inside a world box.

    Each instance counts its own pieces; returns (pieces, total vertices read).
    A piece is a set of vertices joined by edges (union-find over the evaluated mesh).
    """
    pieces, total = [], 0
    cache = {}
    for instance in depsgraph.object_instances:
        obj = instance.object
        if obj.type != "MESH" or not _instance_matches(instance, target):
            continue
        mesh = obj.data
        key = mesh.as_pointer()
        if key not in cache:
            count = len(mesh.vertices)
            total += count
            if total > MAX_PART_VERTICES:
                raise ValueError("too many vertices to count pieces (%d)" % total)
            parent = list(range(count))

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
        for local_points in cache[key]:
            points = [matrix @ point for point in local_points]
            low = [min(p[axis] for p in points) for axis in range(3)]
            high = [max(p[axis] for p in points) for axis in range(3)]
            centre = [(low[axis] + high[axis]) / 2 for axis in range(3)]
            if all(region_min[axis] <= centre[axis] <= region_max[axis] for axis in range(3)):
                pieces.append({"centre": _vector(centre), "size": round(max(high[a] - low[a] for a in range(3)), 4)})
    return pieces


# ---------------------------------------------------------------- materials and colours

def colour_family(rgb):
    """``warm`` (orange/yellow/red), ``blue`` (cyan to blue), ``green``, ``grey`` or ``other`` for a linear RGB colour."""
    r, g, b = (max(0.0, float(v)) for v in rgb[:3])
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    if saturation < 0.12 or value <= 0.0:
        return "grey"
    degrees = hue * 360
    if degrees < 70 or degrees >= 330:
        return "warm"
    if degrees < 165:
        return "green"
    if degrees < 260:
        return "blue"
    return "other"


def _socket_value(socket):
    """A number input's own value, or a Value node's; None when anything else drives it (emission strength)."""
    if not socket.is_linked:
        return float(socket.default_value)
    node = socket.links[0].from_node
    if node.type == "VALUE":
        return float(node.outputs[0].default_value)
    return None


def shading_nodes(tree):
    """The nodes a material renders with: those its active Material Output's Surface reaches through links the
    renderer follows (every node when no output has its Surface linked, as before)."""
    nodes = list(tree.nodes)
    output = shader_graph.active_output(tree, None, "OUTPUT_MATERIAL")
    if output is None:
        return nodes
    try:
        surface = output.inputs["Surface"]
    except (KeyError, TypeError):
        return nodes
    reached = shader_graph.upstream(shader_graph.incoming(tree), output.name, surface)[0]
    return reached or nodes


def material_facts(material, source=None):
    """Closed facts about a material's surface: base colour, roughness, metallic, emission and transmission.

    Colour, roughness and metallic are read from what the node graph computes over the faces that show the
    material (``surface_reading``; ``source`` names them, see ``surface_reading.object_source``): per element
    from attributes and geometry, a ramp by its stops' positions, and what an unreadable texture leaves
    possible. ``_readings`` keeps those readings for the checks; ``base_color``, ``roughness``, ``metallic``
    and ``emission_color`` are their area-weighted means, for reporting (None when part is unknown), and
    ``inconclusive`` names what could not be read.
    """
    facts = {"name": material.name, "base_color": None, "roughness": None, "metallic": None, "emission": 0.0,
             "emission_color": None, "transmission": 0.0, "inconclusive": [], "_readings": {}}
    tree = material.node_tree if getattr(material, "use_nodes", True) else None
    nodes = shading_nodes(tree) if tree is not None else []
    readings = facts["_readings"]

    def keep(key, node, identifier):
        if key not in readings:
            readings[key] = surface_reading.read(material, node, identifier, source)

    principled = [n for n in nodes if n.type == "BSDF_PRINCIPLED"]
    for node in principled:
        keep("base_color", node, "Base Color")
        keep("roughness", node, "Roughness")
        keep("metallic", node, "Metallic")
        strength = _socket_value(node.inputs["Emission Strength"])
        colour_reading = surface_reading.read(material, node, "Emission Color", source)
        emission_colour = surface_reading.mean_rgb(colour_reading)
        if strength and emission_colour and max(emission_colour) > 0:
            luminance = strength * max(emission_colour)
            if luminance > facts["emission"]:
                facts["emission"], readings["emission_color"] = luminance, colour_reading
        transmission = _socket_value(node.inputs["Transmission Weight"])
        facts["transmission"] = max(facts["transmission"], transmission or 0.0)
    for node in nodes:
        if node.type == "EMISSION":
            strength = _socket_value(node.inputs["Strength"])
            colour_reading = surface_reading.read(material, node, "Color", source)
            colour = surface_reading.mean_rgb(colour_reading)
            if strength and colour and strength * max(colour) > facts["emission"]:
                facts["emission"], readings["emission_color"] = strength * max(colour), colour_reading
        elif (node.type in {"BSDF_DIFFUSE", "BSDF_GLOSSY", "BSDF_GLASS", "BSDF_REFRACTION"}
              and "base_color" not in readings):
            keep("base_color", node, "Color")
            if node.type in {"BSDF_GLOSSY", "BSDF_GLASS", "BSDF_REFRACTION"}:
                keep("roughness", node, "Roughness")
            if node.type in {"BSDF_GLASS", "BSDF_REFRACTION"}:
                facts["transmission"] = 1.0
    if "base_color" not in readings and not principled and not facts["emission"]:
        readings["base_color"] = surface_reading.Reading([(tuple(float(v) for v in list(material.diffuse_color)[:3]),
                                                           1.0)])
        if "roughness" not in readings:
            readings["roughness"] = surface_reading.Reading([(float(material.roughness), 1.0)])
    for key, reader in (("base_color", surface_reading.mean_rgb), ("emission_color", surface_reading.mean_rgb),
                        ("roughness", surface_reading.mean_value), ("metallic", surface_reading.mean_value)):
        if key in readings:
            facts[key] = reader(readings[key])
            if readings[key].unknown_nodes:
                facts["inconclusive"].append("%s reads %s" % (key.replace("_", " "),
                                                             ", ".join(readings[key].unknown_nodes[:3])))
    return facts


def reading_verdict(facts, key, test):
    """True, False or None (inconclusive) for a test over a material reading; False when it has none."""
    reading = facts.get("_readings", {}).get(key)
    if reading is None:
        return False
    return surface_reading.decide(reading, test)


def public_facts(facts):
    """The facts without the private readings, for a check's details."""
    return {key: value for key, value in facts.items() if not key.startswith("_")}


def object_materials(obj):
    return [slot.material for slot in obj.material_slots if slot.material is not None]


# ---------------------------------------------------------------- check kinds

def _check_object(bpy, scene, depsgraph, check):
    found = scene_objects(scene, check["target"])
    wanted = check.get("type")
    typed = [obj for obj in found if wanted is None or obj.type == wanted]
    passed = bool(typed) if check.get("exists", True) else not found
    return passed, {"found": sorted(obj.name for obj in found)[:12], "types": sorted({obj.type for obj in found})}


def _check_bbox(bpy, scene, depsgraph, check):
    bounds = evaluated_bounds(bpy, depsgraph, check["target"])
    if bounds is None:
        return False, {"reason": "no matching geometry"}
    low, high = bounds
    tolerance = check.get("tolerance", 0.25)
    problems = []
    for label, measured, expected in (("min", low, check.get("min")), ("max", high, check.get("max"))):
        for axis in range(3):
            if expected is not None and expected[axis] is not None and abs(measured[axis] - expected[axis]) > tolerance:
                problems.append("%s.%s" % (label, "xyz"[axis]))
    size = [high[a] - low[a] for a in range(3)]
    for axis, rule in enumerate(check.get("size") or ()):
        if rule is not None and not rule[0] <= size[axis] <= rule[1]:
            problems.append("size.%s" % "xyz"[axis])
    return not problems, {"min": _vector(low), "max": _vector(high), "size": _vector(size), "off": problems}


def _check_probe(bpy, scene, depsgraph, check):
    result = probe(scene, depsgraph, check["origin"], check["direction"], check.get("distance", 200.0), check["target"])
    if check.get("expect", "hit") == "miss":
        return not result["hit"], result
    if not result["hit"]:
        return False, result
    axis, (low, high) = check.get("axis"), check.get("range", (-math.inf, math.inf))
    value = result["location"]["xyz".index(axis)] if axis else result["distance"]
    result["measured"] = value
    return low <= value <= high, result


def _check_parts(bpy, scene, depsgraph, check):
    pieces = mesh_parts(depsgraph, check["target"], check["region_min"], check["region_max"])
    low, high = check.get("size", (0.0, math.inf))
    sized = [piece for piece in pieces if low <= piece["size"] <= high]
    count_low, count_high = check["count"]
    return count_low <= len(sized) <= count_high, {"pieces_in_region": len(pieces), "sized": len(sized),
                                                    "sizes": sorted(p["size"] for p in pieces)[:40]}


def _material_predicates(facts, check):
    """``(problems, undetermined)`` of the material check's predicates (``undetermined``: could go either way)."""
    problems, undetermined = [], []
    verdicts = []
    if "color" in check:
        verdicts.append(("base colour is %s" % (colour_family(facts["base_color"]) if facts["base_color"] else
                                                "unknown"),
                         reading_verdict(facts, "base_color", lambda rgb: colour_family(rgb) in check["color"])))
    if "roughness_max" in check:
        verdicts.append(("roughness %s" % facts["roughness"],
                         reading_verdict(facts, "roughness", lambda value: value <= check["roughness_max"])))
    if "emission_min" in check:
        verdicts.append(("emission %.3g" % facts["emission"], facts["emission"] >= check["emission_min"]))
    if "emission_color" in check:
        verdicts.append(("emission colour", reading_verdict(facts, "emission_color", lambda rgb: colour_family(rgb)
                                                            in check["emission_color"])))
    for problem, verdict in verdicts:
        if verdict is False:
            problems.append(problem)
        elif verdict is None:
            undetermined.append("inconclusive: " + problem.split(" is ")[0].split(" %")[0])
    return problems, undetermined


def any_of(verdicts):
    """Three-valued "some passes": True if any is True, else None if any is None, else False."""
    verdicts = list(verdicts)
    return True if True in verdicts else None if None in verdicts else False


def _check_material(bpy, scene, depsgraph, check):
    """Some material on a matching object satisfies every predicate; inconclusive when none surely does and
    one might (an unreadable part of its graph decides it)."""
    seen, verdicts = [], []
    for obj in scene_objects(scene, check["target"]):
        for material in object_materials(obj):
            facts = material_facts(material, surface_reading.object_source(obj, depsgraph, material))
            problems, undetermined = _material_predicates(facts, check)
            verdict = False if problems else (None if undetermined else True)
            verdicts.append(verdict)
            seen.append({"material": material.name, "problems": problems + undetermined + facts["inconclusive"][:2]})
            if verdict:
                return True, {"material": material.name, "base_color": facts["base_color"],
                              "roughness": facts["roughness"], "emission": round(facts["emission"], 4)}
    details = {"materials": seen[:8] or "no material on a matching object"}
    if any_of(verdicts) is None:
        details["inconclusive"] = True
    return False, details


def light_elevation(obj):
    """Degrees above the horizon of the direction the light comes FROM (a Sun shines along its local -Z)."""
    direction = obj.matrix_world.to_3x3().col[2].normalized()   # Towards the light.
    return math.degrees(math.asin(max(-1.0, min(1.0, direction.z))))


def _check_light(bpy, scene, depsgraph, check):
    candidates = [obj for obj in scene.objects if obj.type == "LIGHT"
                  and (check.get("target") is None or matches(obj.name, check["target"]))
                  and (check.get("type") is None or obj.data.type == check["type"])]
    seen = []
    for obj in candidates:
        light = obj.data
        facts = {"name": obj.name, "type": light.type, "energy": round(light.energy, 4),
                 "color": _vector(light.color), "elevation_deg": round(light_elevation(obj), 2)}
        problems = []
        if "elevation_deg" in check and not check["elevation_deg"][0] <= facts["elevation_deg"] <= check["elevation_deg"][1]:
            problems.append("elevation")
        if "energy" in check and not check["energy"][0] <= light.energy <= check["energy"][1]:
            problems.append("energy")
        if "color" in check and colour_family(light.color) not in check["color"]:
            problems.append("colour is %s" % colour_family(light.color))
        if "location" in check:
            distance = (obj.matrix_world.translation - _as_vector(check["location"])).length
            facts["distance"] = round(distance, 3)
            if distance > check.get("radius", 1.0):
                problems.append("location")
        facts["problems"] = problems
        seen.append(facts)
        if not problems:
            return True, facts
    return False, {"lights": seen[:6] or "no matching light"}


def _as_vector(values):
    from mathutils import Vector
    return Vector(values)


def _sky_band(graph, elevations):
    """What one band of sky shows: ``(summary, samples)``, ``samples`` being ``[(radiance, 1.0)]`` per direction
    and ``summary`` the mean linear colour, how many directions were read and each colour family's share
    (``varies``: a direction whose possible colours span families; ``unknown``: one the grader cannot read).

    A sky drawn in camera-relative coordinates is sampled only in the directions the camera can see.
    """
    views = [world_background.direction(elevation, azimuth) for elevation in elevations
             for azimuth in SKY_AZIMUTHS_DEG]
    samples = [(value, 1.0) for value in graph.radiances(views) if value is not world_background.Unseen]
    if not samples:
        return None, []
    counts = {}
    for value, _ in samples:
        found = {colour_family(member.rgb) for member in shader_graph.members(value)}
        family = "unknown" if not found else found.pop() if len(found) == 1 else "varies"
        counts[family] = counts.get(family, 0) + 1
    summary = {"color": surface_reading.mean_rgb([(_shader_rgb(value), weight) for value, weight in samples]),
               "directions": len(samples),
               "families": {family: round(count / len(samples), 3) for family, count in sorted(counts.items())}}
    return summary, samples


def _shader_rgb(value):
    """A radiance result with its Shaders replaced by their colours (for averaging)."""
    if value is shader_graph.UNKNOWN:
        return value
    if isinstance(value, shader_graph.Spread):
        return shader_graph.Spread(member.rgb for member in value.values)
    return value.rgb


def band_verdict(facts, band, families):
    """True when at least ``MIN_BAND_SHARE`` of a band's directions surely show one of the colour families,
    False when that surely fails, None when what the grader cannot read decides it."""
    samples = facts.get("_bands", {}).get(band) or []
    if not samples:
        return None if facts.get("_unseen") else False
    return surface_reading.decide(samples, lambda shader: colour_family(shader.rgb) in families, MIN_BAND_SHARE)


def world_facts(world, engine=None, camera=None):
    """What a world shows a camera, read from the graph its active World Output uses (``world_background``).

    ``sky``: the Sky texture that output's Surface reaches, with its sun's elevation (a Sky texture that is
    not linked to it shows nothing and is not reported). ``horizon`` and ``zenith``: the colour the sky shows
    in the lowest and the top 15 degrees, evaluated per direction (gradients, ramps by their stops'
    positions, mixes and strengths all included): the mean colour and the share of directions of each colour
    family (``_sky_band``). ``color`` is the horizon's mean colour. What the grader cannot read (a Sky or
    image texture in the colour path) is named in ``unread``; ``_bands`` keeps the
    samples for ``band_verdict``. ``engine`` is ``scene.render.engine``, which picks the output the renderer
    uses; ``camera`` is ``world_background.scene_camera_view(scene)``, for camera-relative skies.
    """
    facts = {"world": world.name if world else None, "sky": None, "color": None, "strength": None,
             "horizon": None, "zenith": None, "_bands": {}}
    if world is None:
        return facts
    graph = world_background.WorldGraph(world, engine, camera)
    used = graph.used_nodes()
    sky = next((node for node in used if node.type == "TEX_SKY"), None)
    if sky is not None:
        facts["sky"] = {"type": sky.sky_type, "sun_elevation_deg": round(math.degrees(sky.sun_elevation), 2)}
    background = next((node for node in used if node.type == "BACKGROUND"), None)
    if background is not None:
        facts["strength"] = _socket_value(background.inputs["Strength"])
    try:
        for band, elevations in (("horizon", HORIZON_ELEVATIONS_DEG), ("zenith", ZENITH_ELEVATIONS_DEG)):
            facts[band], facts["_bands"][band] = _sky_band(graph, elevations)
            if facts[band] is None:
                facts["_unseen"] = True
    except shader_graph.Unreadable as error:
        facts["unread"] = "the sky's colour cannot be read: %s" % error
        facts["horizon"] = facts["zenith"] = None
        facts["_bands"] = {}
        facts["_unseen"] = True
    facts["color"] = facts["horizon"]["color"] if facts["horizon"] else None
    unread = sorted("%s %r" % (node.type, node.name) for node in used
                    if not shader_graph.evaluable(node) and node.type not in {"TEX_COORD", "NEW_GEOMETRY",
                                                                              "LIGHT_PATH"})
    if unread and "unread" not in facts:
        facts["unread"] = "the sky's colour reads %s" % ", ".join(unread[:3])
    return facts


def render_engine(scene):
    return getattr(getattr(scene, "render", None), "engine", None)


def _check_world(bpy, scene, depsgraph, check):
    """The world's sky: a Sky texture the output uses with the sun in a range, or colour families it shows.

    ``sky_or_color`` and ``color`` are read near the horizon, ``zenith_color`` overhead (``world_facts``). A
    colour the grader cannot read leaves the check inconclusive, never passed.
    """
    facts = world_facts(scene.world, render_engine(scene), world_background.scene_camera_view(scene))
    problems, undetermined = [], []

    def colour(band, families, problem):
        verdict = band_verdict(facts, band, families) if families else False
        if verdict is False:
            problems.append(problem)
        elif verdict is None:
            undetermined.append("inconclusive: %s" % problem)
    if "sky_or_color" in check:
        sky = facts["sky"]
        low, high = check.get("sun_elevation_deg", (-90, 90))
        if not (sky is not None and low <= sky["sun_elevation_deg"] <= high):
            colour("horizon", check["sky_or_color"],
                   "neither a low-sun Sky texture nor a %s background" % "/".join(check["sky_or_color"]))
    if "color" in check:
        colour("horizon", check["color"], "background colour")
    if "zenith_color" in check:
        colour("zenith", check["zenith_color"], "colour overhead")
    if "strength" in check and (facts["strength"] is None or not check["strength"][0] <= facts["strength"] <= check["strength"][1]):
        problems.append("strength")
    details = public_facts(facts)
    details["problems"] = problems + undetermined
    if undetermined and not problems:
        details["inconclusive"] = True
    return not problems and not undetermined, details


def _check_camera(bpy, scene, depsgraph, check):
    found = [obj for obj in scene_objects(scene, check["target"]) if obj.type == "CAMERA"]
    if not found:
        return False, {"reason": "no camera named %s" % target_text(check["target"])}
    obj = found[0]
    matrix = obj.matrix_world
    forward = -(matrix.to_3x3().col[2]).normalized()
    facts = {"name": obj.name, "location": _vector(matrix.translation), "lens": round(obj.data.lens, 3),
             "active": scene.camera == obj, "type": obj.data.type}
    problems = []
    if check.get("active") and not facts["active"]:
        problems.append("not the scene camera")
    if "location" in check:
        facts["distance"] = round((matrix.translation - _as_vector(check["location"])).length, 3)
        if facts["distance"] > check.get("radius", 0.75):
            problems.append("location")
    if "look_at" in check:
        wanted = (_as_vector(check["look_at"]) - matrix.translation).normalized()
        facts["aim_dot"] = round(forward.dot(wanted), 4)
        if facts["aim_dot"] < check.get("min_dot", 0.97):
            problems.append("aim")
    if "lens" in check and not check["lens"][0] <= obj.data.lens <= check["lens"][1]:
        problems.append("lens")
    if "sensor" in check:
        facts["sensor"] = round(obj.data.sensor_width, 3)
        if not check["sensor"][0] <= obj.data.sensor_width <= check["sensor"][1]:
            problems.append("sensor")
    if "dof" in check or "fstop" in check or "focus_distance" in check:
        dof = obj.data.dof
        focus = dof.focus_object
        distance = ((focus.matrix_world.translation - matrix.translation).length if focus is not None
                    else float(dof.focus_distance))
        facts.update(dof=bool(dof.use_dof), fstop=round(float(dof.aperture_fstop), 3),
                     focus_distance=round(distance, 3), focus_object=focus.name if focus is not None else None)
        if bool(dof.use_dof) != bool(check.get("dof", True)):
            problems.append("depth of field")
        if "fstop" in check and not check["fstop"][0] <= dof.aperture_fstop <= check["fstop"][1]:
            problems.append("f-stop")
        if "focus_distance" in check and not check["focus_distance"][0] <= distance <= check["focus_distance"][1]:
            problems.append("focus distance")
    facts["problems"] = problems
    return not problems, facts


def _check_motion(bpy, scene, depsgraph, check):
    """World bounds of matching geometry at given frames (the frame is restored afterwards)."""
    original = scene.frame_current
    samples, problems = {}, []
    try:
        for frame, expected in sorted(check["frames"].items(), key=lambda item: int(item[0])):
            scene.frame_set(int(frame))
            graph = bpy.context.evaluated_depsgraph_get()
            bounds = evaluated_bounds(bpy, graph, check["target"])
            if bounds is None:
                problems.append("frame %s: no geometry" % frame)
                continue
            low, high = bounds
            centre = [(low[a] + high[a]) / 2 for a in range(3)]
            samples[str(frame)] = {"centre": _vector(centre), "min_z": round(low[2], 4)}
            tolerance = check.get("tolerance", 0.5)
            for axis, value in enumerate(expected.get("centre", (None, None, None))):
                if value is not None and abs(centre[axis] - value) > tolerance:
                    problems.append("frame %s centre.%s" % (frame, "xyz"[axis]))
            if "min_z" in expected and abs(low[2] - expected["min_z"]) > check.get("z_tolerance", 0.2):
                problems.append("frame %s min_z" % frame)
    finally:
        scene.frame_set(original)
    return not problems, {"samples": samples, "problems": problems}


def _resolve(scene, path):
    value = scene
    for part in path.split("."):
        value = getattr(value, part)
    return value


def _check_scene(bpy, scene, depsgraph, check):
    measured, problems = {}, []
    for path, expected in check["values"].items():
        try:
            value = _resolve(scene, path)
        except AttributeError:
            value = None
        if hasattr(value, "name") and not isinstance(value, (str, int, float, bool)):
            value = value.name
        measured[path] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
        allowed = expected if isinstance(expected, list) else [expected]
        if isinstance(value, float):
            ok = any(isinstance(a, (int, float)) and abs(value - a) <= 1e-4 for a in allowed)
        else:
            ok = value in allowed
        if not ok:
            problems.append(path)
    return not problems, {"values": measured, "problems": problems}


def _check_armature(bpy, scene, depsgraph, check):
    rigs = [obj for obj in scene_objects(scene, check["target"]) if obj.type == "ARMATURE"]
    if not rigs:
        return False, {"reason": "no armature named %s" % target_text(check["target"])}
    rig = rigs[0]
    bones = [bone.name for bone in rig.data.bones]
    problems = []
    low, high = check.get("bones", (1, 10000))
    if not low <= len(bones) <= high:
        problems.append("bone count")
    for group in check.get("bones_like", ()):
        if not any(re.search(pattern, name, re.IGNORECASE) for name in bones for pattern in group):
            problems.append("no bone like %s" % "/".join(group))
    deformed = [obj.name for obj in scene.objects if obj.type == "MESH" and _deformed_by(obj, rig)]
    if check.get("skins") and not any(matches(name, check["skins"]) for name in deformed):
        problems.append("%s is not deformed by the rig" % target_text(check["skins"]))
    return not problems, {"rig": rig.name, "bones": len(bones), "deformed_meshes": deformed[:8], "problems": problems}


def _deformed_by(obj, rig):
    if any(m.type == "ARMATURE" and m.object == rig for m in obj.modifiers):
        return bool(obj.vertex_groups)
    return obj.parent == rig and obj.parent_type in {"BONE", "ARMATURE"}


def _check_gait(bpy, scene, depsgraph, check):
    """A walk: matching geometry advances along an axis while two foot probes alternate.

    ``feet``: two world-X lanes (left, right). At each sampled frame the lowest
    evaluated point of the matching geometry within each lane is measured; a
    walk lifts the feet in turn, so the higher foot must change sides at least
    ``alternations`` times, and the body must travel ``travel`` along ``axis``.
    """
    original = scene.frame_current
    frames = list(range(check["frames"][0], check["frames"][1] + 1, check.get("step", 2)))
    axis = "xyz".index(check.get("axis", "y"))
    heights, centres = [], []
    try:
        for frame in frames:
            scene.frame_set(frame)
            graph = bpy.context.evaluated_depsgraph_get()
            lanes = [math.inf, math.inf]
            low_all, high_all = [math.inf] * 3, [-math.inf] * 3
            for instance in graph.object_instances:
                obj = instance.object
                if obj.type != "MESH" or not _instance_matches(instance, check["target"]):
                    continue
                mesh, matrix = obj.data, instance.matrix_world
                for vertex in mesh.vertices:
                    point = matrix @ vertex.co
                    for a in range(3):
                        low_all[a], high_all[a] = min(low_all[a], point[a]), max(high_all[a], point[a])
                    for lane, (x_low, x_high) in enumerate(check["feet"]):
                        if x_low <= point.x <= x_high:
                            lanes[lane] = min(lanes[lane], point.z)
            heights.append(lanes)
            centres.append((low_all[axis] + high_all[axis]) / 2 if low_all[axis] < math.inf else None)
    finally:
        scene.frame_set(original)
    if any(value is None for value in centres) or any(math.inf in pair for pair in heights):
        return False, {"reason": "no geometry in a foot lane on some frame"}
    leader = [0 if left > right + check.get("lift", 0.02) else 1 if right > left + check.get("lift", 0.02) else None
              for left, right in heights]
    changes, last = 0, None
    for value in leader:
        if value is not None:
            changes += last is not None and value != last
            last = value
    travel = centres[-1] - centres[0]
    problems = []
    if changes < check.get("alternations", 2):
        problems.append("feet alternate %d times" % changes)
    low, high = check.get("travel", (-math.inf, math.inf))
    if not low <= travel <= high:
        problems.append("travel %.2f" % travel)
    return not problems, {"alternations": changes, "travel": round(travel, 3), "problems": problems,
                          "max_lift": round(max(abs(a - b) for a, b in heights), 3)}


def _check_keyframed(bpy, scene, depsgraph, check):
    """A matching object or its data has an Action whose keys span the frame range."""
    found = scene_objects(scene, check["target"])
    for obj in found:
        for owner in (obj, obj.data):
            data = getattr(owner, "animation_data", None)
            action = data.action if data else None
            if action is None:
                continue
            frames = action.frame_range
            if frames[0] <= check["span"][0] + 0.5 and frames[1] >= check["span"][1] - 0.5:
                return True, {"object": obj.name, "action": action.name, "range": _vector(frames)}
    return False, {"objects": [obj.name for obj in found][:8], "reason": "no Action spanning %s" % (check["span"],)}


def _check_count(bpy, scene, depsgraph, check):
    found = [obj for obj in scene_objects(scene, check["target"])
             if check.get("type") is None or obj.type == check["type"]]
    low, high = check["count"]
    return low <= len(found) <= high, {"count": len(found), "names": sorted(obj.name for obj in found)[:16]}


def _check_modifier(bpy, scene, depsgraph, check):
    """Enough matching objects carry a modifier of one of the given types that renders (``show_render``)."""
    types = check["type"] if isinstance(check["type"], (list, tuple)) else [check["type"]]
    holders = {}
    for obj in scene_objects(scene, check["target"]):
        found = [m.type for m in getattr(obj, "modifiers", ()) if m.type in types and m.show_render]
        if found:
            holders[obj.name] = found
    low, high = check.get("count", (1, math.inf))
    return low <= len(holders) <= high, {"objects": dict(sorted(holders.items())[:12]), "count": len(holders)}


def _output_link(tree, socket_name):
    """Whether the tree's active output node has ``socket_name`` linked."""
    outputs = [n for n in tree.nodes if n.type in {"OUTPUT_MATERIAL", "OUTPUT_WORLD"}]
    outputs.sort(key=lambda n: not getattr(n, "is_active_output", True))
    return any(socket_name in n.inputs and n.inputs[socket_name].is_linked for n in outputs[:1])


def displacement_facts(material):
    """Whether a material displaces geometry: a linked Displacement output and a method other than bump only."""
    tree = material.node_tree if getattr(material, "use_nodes", True) else None
    return {"material": material.name, "linked": tree is not None and _output_link(tree, "Displacement"),
            "method": getattr(material, "displacement_method", "BUMP")}


def _check_displacement(bpy, scene, depsgraph, check):
    """Some material on a matching object has true displacement (``methods``: DISPLACEMENT or BOTH by default)."""
    methods = check.get("methods", ("DISPLACEMENT", "BOTH"))
    seen = []
    for obj in scene_objects(scene, check["target"]):
        for material in object_materials(obj):
            facts = displacement_facts(material)
            seen.append(facts)
            if facts["linked"] and facts["method"] in methods:
                return True, facts
    return False, {"materials": seen[:8] or "no material on a matching object"}


def _volume_material(material):
    tree = material.node_tree if material is not None and getattr(material, "use_nodes", True) else None
    return tree is not None and _output_link(tree, "Volume")


def _check_volume(bpy, scene, depsgraph, check):
    """A volume a renderer shows: a matching object that is, or evaluates to, a volume, or whose material
    (authored, or set by its Geometry Nodes) has a Volume shader; ``{"world": True}`` asks the world for one."""
    if check.get("world"):
        world = scene.world
        tree = world.node_tree if world is not None else None
        linked = tree is not None and _output_link(tree, "Volume")
        return linked, {"world": world.name if world else None, "volume_linked": linked}
    found = {}
    for obj in scene_objects(scene, check["target"]):
        if obj.type == "VOLUME":
            found[obj.name] = "volume object"
        elif any(_volume_material(material) for material in object_materials(obj)):
            found[obj.name] = "volume material"
    for instance in depsgraph.object_instances if depsgraph is not None else ():
        if not _instance_matches(instance, check["target"]):
            continue
        obj = instance.object
        name = (instance.parent.original.name if instance.is_instance and instance.parent is not None
                else obj.original.name)
        if name in found:
            continue
        if obj.type == "VOLUME":
            found[name] = "evaluated volume"
        elif any(_volume_material(slot.material) for slot in getattr(obj, "material_slots", ())):
            found[name] = "evaluated volume material"
    low, high = check.get("count", (1, math.inf))
    return low <= len(found) <= high, {"volumes": dict(sorted(found.items())[:12]), "count": len(found)}


def _check_instances(bpy, scene, depsgraph, check):
    """Evaluated instances (Geometry Nodes, particles, collection instances) made by matching objects."""
    count, sources = 0, set()
    for instance in depsgraph.object_instances:
        if not instance.is_instance or instance.parent is None:
            continue
        parent = instance.parent.original.name
        if not matches(parent, check["target"]):
            continue
        count += 1
        if len(sources) < 12:
            sources.add(parent)
        if count >= MAX_COUNTED_INSTANCES:
            break
    low, high = check.get("count", (1, math.inf))
    return low <= count <= high, {"instances": count, "from": sorted(sources),
                                  "capped": count >= MAX_COUNTED_INSTANCES}


def _check_geometry(bpy, scene, depsgraph, check):
    """Size of the evaluated mesh geometry of matching objects themselves (their instances are not counted)."""
    vertices = faces = 0
    seen = []
    for instance in depsgraph.object_instances:
        obj = instance.object
        if instance.is_instance or obj.type != "MESH" or not matches(obj.original.name, check["target"]):
            continue
        vertices += len(obj.data.vertices)
        faces += len(obj.data.polygons)
        seen.append(obj.original.name)
    problems = []
    for key, value in (("vertices", vertices), ("faces", faces)):
        low, high = check.get(key, (0, math.inf))
        if not low <= value <= high:
            problems.append(key)
    return bool(seen) and not problems, {"objects": sorted(seen)[:12], "vertices": vertices, "faces": faces,
                                         "problems": problems or (["no matching mesh"] if not seen else [])}


def image_size(path):
    """``(width, height)`` from a PNG, JPEG or OpenEXR file's header, or None (nothing is decoded)."""
    with open(path, "rb") as handle:
        head = handle.read(1 << 16)
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    if head[:2] == b"\xff\xd8":
        position = 2
        while position + 9 < len(head) and head[position] == 0xFF:
            marker, length = head[position + 1], struct.unpack(">H", head[position + 2:position + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", head[position + 5:position + 9])
                return width, height
            position += 2 + length
        return None
    if head[:4] == b"\x76\x2f\x31\x01":
        marker = head.find(b"dataWindow\x00box2i\x00")
        if marker >= 0:
            start = marker + len(b"dataWindow\x00box2i\x00") + 4
            x_min, y_min, x_max, y_max = struct.unpack("<iiii", head[start:start + 16])
            return x_max - x_min + 1, y_max - y_min + 1
    return None


def find_outputs(name, roots):
    """Files called ``name`` (case-insensitive) under ``roots``, not in hidden folders, shallow first."""
    found, visited = [], 0
    for root in roots:
        root = os.path.abspath(str(root))
        for folder, folders, files in os.walk(root):
            depth = 0 if folder == root else os.path.relpath(folder, root).count(os.sep) + 1
            folders[:] = sorted(d for d in folders if not d.startswith(".")) if depth < MAX_OUTPUT_DEPTH else []
            visited += len(files)
            found += [os.path.join(folder, f) for f in sorted(files) if f.lower() == name.lower()]
            if visited > MAX_OUTPUT_ENTRIES:
                break
    return found


def evaluate_outputs(outputs, roots, reply="", receipts=()):
    """A turn's required files against the session's output folders, as check results (``kind`` ``output``).

    ``outputs``: ``[{"name", "label", "min_size": [w, h] (images), "min_bytes"}]``;
    a file passes when it satisfies every bound. The file judged is the one
    the agent names as final (``final_render``: the turn's ``reply``, then
    Angelo's job ``receipts``, then the requested name); ``details["final"]``
    says which and why.
    """
    import final_render
    results = []
    for index, wanted in enumerate(outputs):
        entry = {"index": index, "kind": "output", "label": wanted.get("label", wanted["name"]), "passed": False}
        seen = []
        try:
            candidates = final_render.find(wanted["name"], roots, MAX_OUTPUT_DEPTH, MAX_OUTPUT_ENTRIES)
            final = final_render.decide(wanted["name"], candidates, reply, receipts)
            for path in final_render.judged(candidates, final):
                facts = {"file": os.path.basename(path), "bytes": os.path.getsize(path)}
                problems = ["bytes"] if facts["bytes"] < wanted.get("min_bytes", 1) else []
                if "min_size" in wanted:
                    size = image_size(path)
                    facts["size"] = list(size) if size else None
                    if size is None or size[0] < wanted["min_size"][0] or size[1] < wanted["min_size"][1]:
                        problems.append("size")
                facts["problems"] = problems
                seen.append(facts)
                if not problems:
                    entry.update(passed=True, details=dict(facts, final=final))
                    break
            else:
                entry["details"] = {"files": seen[:6] or "no file named %s" % final["file"], "folders": len(roots),
                                    "final": final}
        except OSError as error:
            entry["details"] = {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
        results.append(entry)
    return results


def with_outputs(result, outputs, roots, reply="", receipts=()):
    """``evaluate``'s result with a turn's required files appended; ``success`` needs both."""
    if not outputs:
        return result
    extra = evaluate_outputs(outputs, roots, reply, receipts)
    for entry in extra:
        entry["index"] += result["total"]
    results = result["results"] + extra
    return {"success": bool(result["success"]) and all(entry["passed"] for entry in extra),
            "passed": sum(entry["passed"] for entry in results), "total": len(results), "results": results}


CHECKS = {"object": _check_object, "bbox": _check_bbox, "probe": _check_probe, "parts": _check_parts,
          "material": _check_material, "light": _check_light, "world": _check_world, "camera": _check_camera,
          "motion": _check_motion, "scene": _check_scene, "armature": _check_armature, "gait": _check_gait,
          "keyframed": _check_keyframed, "count": _check_count, "modifier": _check_modifier,
          "displacement": _check_displacement, "volume": _check_volume, "instances": _check_instances,
          "geometry": _check_geometry}
# The kinds the one-shot tests add (surface, visible, detail, hygiene): registered here so every grader has them.
import oneshot_checks  # noqa: E402
CHECKS.update(oneshot_checks.CHECKS)


def is_inconclusive(details):
    """Whether a check that did not pass could not be decided (the grader cannot read what decides it): marked
    so, a reason that says so, or the closed verifier's own ``inconclusive`` status."""
    if not isinstance(details, dict):
        return False
    return (details.get("inconclusive") is True or details.get("status") == "inconclusive"
            or str(details.get("reason", "")).startswith("inconclusive"))


def evaluate(bpy, scene, checks):
    """Every check of one turn against the live scene; ``success`` only when all pass.

    A check that could not be decided is not passed and is marked ``inconclusive`` (``is_inconclusive``):
    reports show it apart from a measured fail, and it never counts as a pass.
    """
    depsgraph = bpy.context.evaluated_depsgraph_get()
    results = []
    for index, check in enumerate(checks):
        entry = {"index": index, "kind": check["kind"], "label": check.get("label", check["kind"])}
        try:
            passed, details = CHECKS[check["kind"]](bpy, scene, depsgraph, check)
            entry.update(passed=bool(passed), details=details)
            if not passed and is_inconclusive(details):
                entry["inconclusive"] = True
        except Exception as error:  # A broken scene must fail its check, never the grader.
            entry.update(passed=False, details={"error": "%s: %s" % (type(error).__name__, str(error)[:300])})
        results.append(entry)
        depsgraph = bpy.context.evaluated_depsgraph_get()   # Motion checks re-evaluate frames.
    return {"success": bool(results) and all(entry["passed"] for entry in results),
            "passed": sum(entry["passed"] for entry in results), "total": len(results), "results": results}
