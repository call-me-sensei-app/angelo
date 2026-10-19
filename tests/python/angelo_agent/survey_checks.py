# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Result checks for request-survey edits that the add-on's closed check kinds cannot grade.

A survey request is phrased as a person types it ("make the sphere look like
clear glass"), so its result is often a look, a shape or a motion, not a named
value. ``evaluate(bpy, scene, checks, resolve)`` grades a task's
``result_checks`` on the scene after the turn, read-only (motion checks step
frames and restore the original), and returns the same shape as
``scenario_checks.evaluate``. Each check measures the result, never which tool
produced it; ``details`` keeps the measured values of every check.

Two families share one list:

* the add-on's closed kinds (``validation_spec.CHECK_KINDS``: modifier values,
  markers, rig binding...) run through ``scene_validation.verify`` on the
  resolved targets, with ``"$modifier:TYPE"`` standing for the name the agent
  gave the target's first modifier of that type;
* the kinds below (``KINDS``), implemented here and in
  ``survey_checks_motion``.

``resolve(alias)`` maps a check's target to objects: ``$eval``, ``$scene``,
objects an earlier task created (``$sphere``), or ``$new``, the objects this
turn created. ``bpy`` is passed in, so this module imports without Blender.
"""

import colorsys
import math

import scenario_checks
import scenario_hygiene
import survey_checks_motion as motion
import survey_truth

MAX_VERTICES = 400000
# Kinds that step frames: graded after a turn, never probed before one.
MOTION_KINDS = frozenset({"bounces", "orbit"})
MODIFIER_NAME = "$modifier:"
PROCEDURAL_TEXTURES = frozenset({"TEX_NOISE", "TEX_WAVE", "TEX_VORONOI", "TEX_MUSGRAVE", "TEX_GRADIENT", "TEX_MAGIC",
                                 "TEX_CHECKER", "TEX_BRICK", "TEX_WHITE_NOISE", "TEX_GABOR"})
IMAGE_TEXTURES = frozenset({"TEX_IMAGE", "TEX_ENVIRONMENT"})


def _objects(resolve, alias, kind=None):
    found = resolve(alias)
    found = found if isinstance(found, list) else [found] if found is not None else []
    return [obj for obj in found if kind is None or getattr(obj, "type", None) == kind]


def _evaluated_mesh(bpy, obj):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    if len(mesh.vertices) > MAX_VERTICES:
        raise ValueError("too many vertices to read (%d)" % len(mesh.vertices))
    return mesh


def topology(mesh):
    """Connected pieces of a mesh: vertex, edge and face counts, whether every edge joins exactly two faces
    (``closed``), the Euler characteristic V - E + F, and the piece's vertex indices."""
    parent = list(range(len(mesh.vertices)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index
    edges = [tuple(edge.vertices) for edge in mesh.edges]
    for first, second in edges:
        a, b = root(first), root(second)
        if a != b:
            parent[a] = b
    uses = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            uses[key] = uses.get(key, 0) + 1
    pieces = {}
    for index in range(len(parent)):
        pieces.setdefault(root(index), {"vertices": 0, "edges": 0, "faces": 0, "closed": True, "indices": []})
        pieces[root(index)]["vertices"] += 1
        pieces[root(index)]["indices"].append(index)
    for first, second in edges:
        piece = pieces[root(first)]
        piece["edges"] += 1
        if uses.get((min(first, second), max(first, second)), 0) != 2:
            piece["closed"] = False
    for polygon in mesh.polygons:
        pieces[root(polygon.vertices[0])]["faces"] += 1
    for piece in pieces.values():
        piece["euler"] = piece["vertices"] - piece["edges"] + piece["faces"]
    return list(pieces.values())


def _summary(pieces):
    return [{key: piece[key] for key in ("vertices", "edges", "faces", "closed", "euler")} for piece in pieces[:8]]


def _check_material_look(bpy, scene, resolve, check):
    """Some material on a target is transmissive and smooth enough (clear glass)."""
    seen = []
    for obj in _objects(resolve, check["target"]):
        for material in scenario_checks.object_materials(obj):
            facts = scenario_checks.material_facts(material)
            # A roughness the grader cannot read is let through, as before; one it can is held to the bound.
            smooth = None if "roughness" not in facts["_readings"] else scenario_checks.reading_verdict(
                facts, "roughness", lambda value: value <= check.get("roughness_max", 1.0))
            ok = facts["transmission"] >= check.get("transmission_min", 0.0) and smooth is not False
            seen.append({"material": material.name, "transmission": round(facts["transmission"], 3),
                         "roughness": facts["roughness"]})
            if ok:
                return True, seen[-1]
    return False, {"materials": seen[:8] or "no material on the target"}


def _is_hue(color, hue, saturation_min=0.5, value_min=0.4):
    h, s, v = colorsys.rgb_to_hsv(*(max(0.0, float(c)) for c in color[:3]))
    return hue[0] <= h * 360 <= hue[1] and s >= saturation_min and v >= value_min


def _check_vertex_color(bpy, scene, resolve, check):
    """The highest vertices carry the hue in a colour attribute and the lowest do not."""
    for obj in _objects(resolve, check["target"], "MESH"):
        mesh = obj.data
        heights = [vertex.co.z for vertex in mesh.vertices]
        if not heights or not mesh.color_attributes:
            continue
        low, high = min(heights), max(heights)
        band = max((high - low) * 0.05, 1e-6)
        seen = []
        for attribute in mesh.color_attributes:
            per_vertex = {}
            if attribute.domain == "POINT":
                per_vertex = {index: [tuple(item.color)] for index, item in enumerate(attribute.data)}
            elif attribute.domain == "CORNER":
                for loop, item in zip(mesh.loops, attribute.data):
                    per_vertex.setdefault(loop.vertex_index, []).append(tuple(item.color))
            share = lambda indices: (sum(all(_is_hue(c, check["hue"]) for c in per_vertex.get(i, [(0, 0, 0)]))
                                         for i in indices) / max(1, len(indices)))
            top = share([i for i, z in enumerate(heights) if z >= high - band])
            bottom = share([i for i, z in enumerate(heights) if z <= low + band])
            seen.append({"attribute": attribute.name, "top_share": round(top, 3), "bottom_share": round(bottom, 3)})
            if top >= 0.9 and bottom <= 0.1:
                return True, seen[-1]
        return False, {"attributes": seen}
    return False, {"reason": "the target has no colour attribute"}


def _check_mesh_genus(bpy, scene, resolve, check):
    """A target mesh is one closed surface of the given genus (1: a torus)."""
    seen = []
    for obj in _objects(resolve, check["target"], "MESH"):
        pieces = topology(_evaluated_mesh(bpy, obj))
        seen.append({"object": obj.name, "pieces": _summary(pieces)})
        whole = pieces[0] if len(pieces) == 1 else None
        if whole and whole["closed"] and whole["faces"] and whole["euler"] == 2 - 2 * check["genus"]:
            return True, seen[-1]
    return False, {"objects": seen[:6] or "no mesh among the targets"}


def _world_extent(bpy, obj, axis, evaluated):
    if evaluated:
        points = survey_truth.box_corners(bpy, obj)
    else:
        points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    values = [point[axis] for point in points]
    return max(values) - min(values) if values else 0.0


def _check_array_span(bpy, scene, resolve, check):
    """An Array modifier of ``count`` copies widens a target along a world axis by (count - 1) x spacing."""
    axis, wanted, seen = "xyz".index(check["axis"]), (check["count"] - 1) * check["spacing"], []
    for obj in _objects(resolve, check["target"], "MESH"):
        arrays = [modifier for modifier in obj.modifiers if modifier.type == "ARRAY" and modifier.show_viewport]
        grown = _world_extent(bpy, obj, axis, True) - _world_extent(bpy, obj, axis, False)
        seen.append({"object": obj.name, "array_counts": [modifier.count for modifier in arrays],
                     "grown": round(grown, 4)})
        if (any(modifier.count == check["count"] for modifier in arrays)
                and abs(grown - wanted) <= check.get("tolerance", 0.05)):
            return True, seen[-1]
    return False, {"objects": seen[:6] or "object missing"}


def _check_through_hole(bpy, scene, resolve, check):
    """A closed piece of the target has a handle (genus 1 or more) and a ray along the axis passes through it."""
    from mathutils import Vector
    axis = "xyz".index(check["axis"])
    others = [a for a in range(3) if a != axis]
    for obj in _objects(resolve, check["target"], "MESH"):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = _evaluated_mesh(bpy, obj)
        pieces = topology(mesh)
        inverse = obj.matrix_world.inverted()
        direction = Vector([1.0 if a == axis else 0.0 for a in range(3)])
        seen = {"object": obj.name, "pieces": _summary(pieces)}
        for piece in pieces:
            if not piece["closed"] or piece["euler"] > 0 or piece["vertices"] < 8:
                continue
            points = [obj.matrix_world @ mesh.vertices[index].co for index in piece["indices"]]
            low = [min(point[a] for point in points) for a in range(3)]
            high = [max(point[a] for point in points) for a in range(3)]
            hits = misses = 0
            for u in range(21):
                for v in range(21):
                    origin = [0.0, 0.0, 0.0]
                    origin[axis] = low[axis] - 1.0
                    for a, step in zip(others, (u, v)):   # The central 70% of the piece's silhouette.
                        origin[a] = low[a] + (high[a] - low[a]) * (0.15 + 0.7 * step / 20)
                    hit = obj.ray_cast(inverse @ Vector(origin), (inverse.to_3x3() @ direction).normalized(),
                                       depsgraph=depsgraph)[0]
                    hits, misses = hits + hit, misses + (not hit)
            seen.update(handle_piece={key: piece[key] for key in ("vertices", "euler")}, ray_hits=hits,
                        ray_misses=misses)
            if hits and misses:
                return True, seen
        return False, seen
    return False, {"reason": "object missing"}


def _check_world_look(bpy, scene, resolve, check):
    """The background near the horizon is (or is not) grey, at a given brightness (its colour value).

    ``world_facts`` reads what the world shows, strength included, so the brightness is the horizon colour's
    largest channel; grey means at least half the horizon's directions are grey (``band_verdict``). A sky the
    grader cannot read is inconclusive.
    """
    if scene.world is None:
        return False, {"reason": "the scene has no world"}
    facts = scenario_checks.world_facts(scene.world, scenario_checks.render_engine(scene),
                                        scenario_checks.world_background.scene_camera_view(scene))
    grey = scenario_checks.band_verdict(facts, "horizon", ["grey"])
    color = facts["color"]
    if color is None or grey is None:
        return False, {"reason": "inconclusive: %s" % (facts.get("unread") or "the background cannot be read"),
                       "sky": facts["sky"]}
    brightness = max(color)
    low, high = check["brightness"]
    return grey == check["grey"] and low <= brightness <= high, {
        "color": color, "families": facts["horizon"]["families"], "brightness": round(brightness, 4)}


def _check_camera_frame(bpy, scene, resolve, check):
    """The target fills the active camera's frame without leaving it."""
    for obj in _objects(resolve, check["target"]):
        measured = survey_truth.camera_frame(bpy, scene, obj)
        ok = (measured["in_front"] and measured["margin"] >= -check.get("tolerance", 0.03)
              and measured["fill"] >= check["min_fill"])
        return ok, measured
    return False, {"reason": "object missing"}


def _surface_nodes(tree, depth=0):
    """Node types that feed a material's surface output, following links backwards (groups are opened)."""
    output = next((node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output), None)
    pending = [link.from_node for link in output.inputs["Surface"].links] if output is not None else []
    seen, types = set(), set()
    while pending:
        node = pending.pop()
        if node.name in seen:
            continue
        seen.add(node.name)
        types.add(node.type)
        if node.type == "GROUP" and node.node_tree is not None and depth < 4:
            types.update(inner.type for inner in node.node_tree.nodes)
        pending.extend(link.from_node for socket in node.inputs for link in socket.links)
    return types


def _check_procedural_material(bpy, scene, resolve, check):
    """A target's material surface is driven by a procedural texture and by no image file."""
    seen = []
    for obj in _objects(resolve, check["target"]):
        for material in scenario_checks.object_materials(obj):
            types = _surface_nodes(material.node_tree) if material.node_tree is not None else set()
            seen.append({"material": material.name, "procedural": sorted(types & PROCEDURAL_TEXTURES),
                         "images": sorted(types & IMAGE_TEXTURES)})
            if types & PROCEDURAL_TEXTURES and not types & IMAGE_TEXTURES:
                return True, seen[-1]
    return False, {"materials": seen[:8] or "no material on the target"}


def _check_scatter_count(bpy, scene, resolve, check):
    """Generated instances in the scene (or, when realized, loose pieces of a node-modified mesh) number in a range."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    instances = sum(1 for instance in depsgraph.object_instances if instance.is_instance)
    candidates = {"instances": instances}
    for obj in scene.objects:
        if obj.type == "MESH" and any(modifier.type == "NODES" for modifier in obj.modifiers):
            mesh = obj.evaluated_get(depsgraph).data
            if len(mesh.vertices) <= MAX_VERTICES:
                candidates["pieces:" + obj.name[:40]] = len(topology(mesh))
    low, high = check["range"]
    # A realized scatter may keep the surface it grew on as one more piece.
    return any(low <= count <= high or low <= count - 1 <= high for count in candidates.values()), candidates


def _collides(obj):
    return any(modifier.type == "COLLISION" for modifier in obj.modifiers) and getattr(obj.collision, "use", True)


def _check_cloth_over(bpy, scene, resolve, check):
    """A subdivided mesh with Cloth physics lies above the collider and over it, and the collider has Collision."""
    colliders = _objects(resolve, check["collider"])
    if not colliders:
        return False, {"reason": "collider missing"}
    collider = colliders[0]
    box = survey_truth.box_corners(bpy, collider)
    top = max(point.z for point in box)
    seen = {"collider": collider.name, "collider_has_collision": _collides(collider), "cloths": []}
    for obj in scene.objects:
        if obj.type != "MESH" or obj == collider or not any(modifier.type == "CLOTH" for modifier in obj.modifiers):
            continue
        points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        if not points:
            continue
        over = all(min(p[axis] for p in points) < max(c[axis] for c in box)
                   and max(p[axis] for p in points) > min(c[axis] for c in box) for axis in range(2))
        entry = {"object": obj.name, "vertices": len(points), "lowest": round(min(p.z for p in points), 4),
                 "over_the_collider": over}
        seen["cloths"].append(entry)
        if (seen["collider_has_collision"] and over and len(points) >= check.get("min_vertices", 25)
                and entry["lowest"] >= top - 0.01):
            return True, seen
    return False, seen


def _check_cache_baked(bpy, scene, resolve, check):
    """A physics modifier's point cache is baked over the frame range."""
    seen = []
    for obj in _objects(resolve, check["target"]) or [o for o in scene.objects if o.type == "MESH"]:
        for modifier in obj.modifiers:
            if modifier.type != check["modifier"]:
                continue
            cache = modifier.point_cache
            seen.append({"object": obj.name, "baked": bool(cache.is_baked),
                         "frames": [cache.frame_start, cache.frame_end]})
            if cache.is_baked and cache.frame_start <= check["frames"][0] and cache.frame_end >= check["frames"][1]:
                return True, seen[-1]
    return False, {"caches": seen[:6] or "no such physics modifier"}


def _check_no_orphans(bpy, scene, resolve, check):
    """Named unused data-blocks are gone and no data-block is left without a user."""
    left = scenario_hygiene.orphans(bpy)
    named = [name for name in check.get("absent", ()) if bpy.data.materials.get(name) is not None]
    return not left and not named, {"orphans": {key: names[:8] for key, names in left.items()}, "still_present": named}


KINDS = {"material_look": _check_material_look, "vertex_color": _check_vertex_color, "mesh_genus": _check_mesh_genus,
         "array_span": _check_array_span, "through_hole": _check_through_hole, "world_look": _check_world_look,
         "camera_frame": _check_camera_frame, "procedural_material": _check_procedural_material,
         "scatter_count": _check_scatter_count, "cloth_over": _check_cloth_over, "cache_baked": _check_cache_baked,
         "no_orphans": _check_no_orphans, "bounces": motion.check_bounces, "orbit": motion.check_orbit}


def _closed_check(bpy, scene, resolve, check):
    """One of the add-on's closed check kinds, through its own verifier, on the resolved targets."""
    from angelo_agent.scene_validation import VerificationContext, verify
    bound, targets = dict(check), {}
    for key in ("target", "armature", "expected"):
        alias = bound.get(key)
        if isinstance(alias, str) and alias.startswith("$"):
            found = scene if alias == "$scene" else next(iter(_objects(resolve, alias)), None)
            if found is None:
                return False, {"reason": "object missing", "alias": alias}
            targets[alias] = found
    owner = targets.get(bound.get("target"))
    for key in ("name", "modifier"):
        value = bound.get(key)
        if isinstance(value, str) and value.startswith(MODIFIER_NAME):
            kind = value[len(MODIFIER_NAME):]
            bound[key] = next((m.name for m in getattr(owner, "modifiers", ()) if m.type == kind), "missing " + kind)
    result = verify(VerificationContext(targets, list(scene.objects)), [bound])
    item = result["checks"][0]
    return bool(result["success"]), {"status": item["status"], "observed": item.get("observed"),
                                     "reason": item.get("reason")}


def is_closed_kind(kind):
    from angelo_agent.validation_spec import CHECK_KINDS
    return kind in CHECK_KINDS


def evaluate(bpy, scene, checks, resolve):
    """Every result check of one task against the live scene; ``success`` only when all pass."""
    results = []
    for index, check in enumerate(checks):
        entry = {"index": index, "kind": check["kind"], "label": check.get("label", check["kind"])}
        try:
            run = KINDS.get(check["kind"]) or _closed_check
            passed, details = run(bpy, scene, resolve, check)
            entry.update(passed=bool(passed), details=details)
            if not passed and scenario_checks.is_inconclusive(details):
                entry["inconclusive"] = True   # Undecided: never passed, reported apart (eval_grading).
        except Exception as error:  # A broken scene must fail its check, never the grader.
            entry.update(passed=False, details={"error": "%s: %s" % (type(error).__name__, str(error)[:300])})
        results.append(entry)
    return {"success": bool(results) and all(entry["passed"] for entry in results),
            "passed": sum(entry["passed"] for entry in results), "total": len(results), "results": results}
