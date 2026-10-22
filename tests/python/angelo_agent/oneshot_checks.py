# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed checks the one-shot tests add to ``scenario_checks``: read-only and measured, never the model's word.

A one-shot brief leaves the design to the model, so its checks ask only for
what the brief states, however it was built:

* ``surface``: like ``material``, for materials a model designs itself. The
  colour, roughness and metallic are what the node graph computes over the
  faces that show the material (``surface_reading``): attributes and geometry
  per face corner, ramps by their stops' positions, mixes by their factors,
  node groups through their outputs. A texture the grader cannot evaluate
  leaves the colours a ramp or mix after it can show; a predicate holds when
  at least half the surface surely satisfies it, and a check that an
  unreadable part decides is ``inconclusive``: never passed, reported apart
  from a fail.
* ``visible``: the first thing a ray meets is the named object: a door or a
  window that can be seen from outside, not one buried in a wall.
* ``detail``: evaluated vertices of the object's own meshes plus the
  instances it makes: foliage that is many leaves, not one ball.
* ``hygiene``: a tidy file: no object of the default scene, no
  default-named object, a material on every mesh that renders, and few
  unused data-blocks (the factory file's own are not counted): those a save
  would write and a reopened file would show unused
  (``scenario_hygiene.saved_orphans``), so zero-user data a save drops
  never counts against a side that leaves it alone. Every scene test counts
  this way; ``"unused_counted": "session"`` counts the session's zero-user
  data instead, as the check did before.

``evaluate_outputs`` grades the still the brief asks for: found by name in
the side's own folders or where the scene's render path points, written
after the message was sent (a file from an earlier run never counts), and
at least the stated size. ``pixel_checks`` reads the statistics
``oneshot_job.py`` measured on a still: not empty, not black, not the
default scene. Nothing here is judged by a model.

Importable without Blender; ``scenario_checks`` is imported inside the
functions because it registers these kinds when it is loaded itself.
"""

import math
import os
import re

# Data-blocks without users in Blender's own factory file (stock 5.2.0 and Angelo 5.2.2, measured).
FACTORY_ORPHANS = {"materials": ("Dots Stroke",)}
# A still is "empty" below this spread of luminance, "black" below this mean, and "the default scene"
# when it differs from a render of the factory scene by less than this mean absolute luminance.
EMPTY_SPREAD, BLACK_MEAN, BLACK_SHARE, DEFAULT_DIFFERENCE = 0.012, 0.02, 0.98, 0.02
COLOUR_KEYS = ("color", "saturation", "value")


# ---------------------------------------------------------------- materials

def surface_facts(material, source=None):
    """``scenario_checks.material_facts``: colour, roughness and metallic read from what the node graph shows over
    the faces ``source`` names (``surface_reading``), and what an unreadable texture leaves possible."""
    import scenario_checks
    return scenario_checks.material_facts(material, source)


# ---------------------------------------------------------------- check kinds

def _surface_problems(facts, check):
    """``(problems, undetermined)`` of a surface check's predicates over a material's readings.

    The colour (family, saturation and brightness together), roughness and metallic each hold when at least
    half the material's surface surely satisfies them (``surface_reading.decide``); ``undetermined`` lists
    those an unreadable part of the graph decides.
    """
    import colorsys
    import scenario_checks
    problems, undetermined = [], []

    def judge(key, test, problem):
        verdict = scenario_checks.reading_verdict(facts, key, test)
        if verdict is False:
            problems.append(problem)
        elif verdict is None:
            undetermined.append("inconclusive: %s" % problem.split(" (")[0])
    colour = facts["base_color"]
    if {"color", "saturation", "value"} & set(check):
        def within(key, value):
            return key not in check or check[key][0] <= value <= check[key][1]

        def colour_ok(rgb):
            _hue, saturation, value = colorsys.rgb_to_hsv(*(max(0.0, v) for v in rgb[:3]))
            return (("color" not in check or scenario_checks.colour_family(rgb) in check["color"])
                    and within("saturation", saturation) and within("value", value))
        described = "unknown" if colour is None else "%s (saturation %.3g, brightness %.3g)" % (
            scenario_checks.colour_family(colour), *colorsys.rgb_to_hsv(*(max(0.0, v) for v in colour))[1:])
        judge("base_color", colour_ok, "base colour is %s" % described)
    for key in ("metallic", "roughness"):
        if key in check:
            low, high = check[key]
            judge(key, lambda value, low=low, high=high: low <= value <= high, "%s %s" % (key, facts[key]))
    if "emission_min" in check and facts["emission"] < check["emission_min"]:
        problems.append("emission %.3g" % facts["emission"])
    if "emission_color" in check:
        judge("emission_color", lambda rgb: scenario_checks.colour_family(rgb) in check["emission_color"],
              "emission colour")
    if "transmission_min" in check and facts["transmission"] < check["transmission_min"]:
        problems.append("transmission %.3g" % facts["transmission"])
    return problems, undetermined


def _evaluated_materials(obj, depsgraph):
    """Materials the object renders with: its slots, and what its modifiers (Geometry Nodes) set."""
    found = [slot.material for slot in obj.material_slots if slot.material is not None]
    try:
        evaluated = obj.evaluated_get(depsgraph)
        found += [slot.material for slot in evaluated.material_slots if slot.material is not None]
        found += [material for material in getattr(evaluated.data, "materials", ()) if material is not None]
    except (AttributeError, RuntimeError, TypeError):
        pass
    unique = {}
    for material in found:
        unique.setdefault(getattr(material, "name", id(material)), material)
    return list(unique.values())


def _instanced_materials(depsgraph, target):
    """``{object name: materials}`` of what each matching object instances (a leaf on its points).

    The thing that renders then carries the material, not the object that places it. Read while the
    instances are walked: an instance is only valid there.
    """
    import scenario_checks
    made = {}
    for instance in getattr(depsgraph, "object_instances", None) or ():
        if not instance.is_instance or instance.parent is None:
            continue
        name = instance.parent.original.name
        if not scenario_checks.matches(name, target):
            continue
        found = made.setdefault(name, {})
        if len(found) < 32:
            for slot in getattr(instance.object, "material_slots", ()):
                if slot.material is not None:
                    material = getattr(slot.material, "original", None) or slot.material
                    found.setdefault(material.name, material)
    return {name: list(found.values()) for name, found in made.items()}


def _surface_of(obj, depsgraph, check, instanced=()):
    """``(facts of the first material of the object that satisfies the check, or None; what was seen; verdict)``.

    ``verdict`` is True, False, or None when no material surely passes and one might (inconclusive). A
    material the object itself shows is read over its evaluated faces; one its instances show, as a whole.
    """
    import scenario_checks
    import surface_reading
    seen, verdicts = [], []
    own = _evaluated_materials(obj, depsgraph)
    for material in own + list(instanced):
        original = getattr(material, "original", None) or material
        source = surface_reading.object_source(obj, depsgraph, original) if material in own else None
        facts = surface_facts(original, source)
        problems, undetermined = _surface_problems(facts, check)
        verdict = False if problems else (None if undetermined else True)
        verdicts.append(verdict)
        seen.append({"material": original.name, "problems": problems + undetermined
                     + (facts["inconclusive"][:2] if undetermined else [])})
        if verdict:
            return {"material": original.name, "base_color": facts["base_color"], "roughness": facts["roughness"],
                    "metallic": facts["metallic"], "emission": round(facts["emission"], 4)}, seen, True
    return None, seen, scenario_checks.any_of(verdicts)


def _check_surface(bpy, scene, depsgraph, check):
    """Some material a matching object renders with satisfies every predicate.

    ``{"built": True}`` as the target is everything a mini's message built (``oneshot_mini_checks``);
    with ``"all": True`` every matching object must have such a material, not only one of them. A check an
    unreadable graph leaves undecided is inconclusive (never passed).
    """
    import scenario_checks
    if check["target"].get("built"):
        import oneshot_mini_checks
        objects = oneshot_mini_checks.built_objects(scene)
    else:
        objects = scenario_checks.scene_objects(scene, check["target"])
    made = {} if check["target"].get("built") else _instanced_materials(depsgraph, check["target"])
    seen, passed, without, undecided = [], None, [], []
    for obj in objects:
        found, looked, verdict = _surface_of(obj, depsgraph, check, made.get(obj.name, ()))
        seen += looked
        if verdict is None:
            undecided.append(obj.name)
        elif found is None:
            without.append(obj.name)
        elif passed is None:
            passed = found
        if found is not None and not check.get("all"):
            return True, found
    if passed is not None and not without and not undecided:
        return True, dict(passed, objects=len(objects))
    details = {"materials": seen[:8] or "no material on a matching object", "objects_without": without[:12]}
    if undecided and (not without or not check.get("all")):
        details.update(inconclusive=True, undecided=undecided[:12])
    return False, details


def _check_visible(bpy, scene, depsgraph, check):
    """The first object a ray meets is a matching one (or its child)."""
    import scenario_checks
    from mathutils import Vector
    origin, direction = Vector(check["origin"]), Vector(check["direction"]).normalized()
    hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, origin, direction,
                                                                   distance=check.get("distance", 200.0))
    if not hit:
        return False, {"hit": False}
    name = obj.original.name   # By its own name: a differently named child is another part.
    return scenario_checks.matches(name, check["target"]), {
        "hit": True, "first_object": name, "location": [round(float(v), 4) for v in location]}


def _check_detail(bpy, scene, depsgraph, check):
    """Evaluated vertices of the matching objects' own meshes plus the instances they make."""
    import scenario_checks
    _, own = scenario_checks._check_geometry(bpy, scene, depsgraph, {"target": check["target"]})
    _, made = scenario_checks._check_instances(bpy, scene, depsgraph, {"target": check["target"]})
    elements = own["vertices"] + made["instances"]
    low, high = check.get("elements", (1, math.inf))
    return low <= elements <= high, {"vertices": own["vertices"], "instances": made["instances"],
                                     "elements": elements, "objects": own["objects"]}


def _renders_itself(obj):
    """Whether the object's own geometry is in the render (not hidden, and not only an emitter of instances)."""
    if obj.hide_render:
        return False
    return getattr(obj, "show_instancer_for_render", True) or getattr(obj, "instance_type", "NONE") == "NONE"


def _own_faces(obj, depsgraph):
    """Faces of the object's own evaluated mesh: none when its modifiers leave only instances, which carry
    their own materials (a leaf placed on points)."""
    try:
        return len(obj.evaluated_get(depsgraph).data.polygons)
    except (AttributeError, RuntimeError, TypeError):
        return len(obj.data.polygons)


def _check_hygiene(bpy, scene, depsgraph, check):
    """A tidy file: see the module docstring. Every bound the check names must hold."""
    import scenario_hygiene
    leftovers = [name for name in check.get("absent", ()) if scene.objects.get(name) is not None]
    default_named = sorted(obj.name for obj in scene.objects if scenario_hygiene.DEFAULT_NAME.match(obj.name))
    bare = sorted(obj.name for obj in scene.objects if obj.type == "MESH" and _renders_itself(obj)
                  and _own_faces(obj, depsgraph) and not _evaluated_materials(obj, depsgraph))
    # What a save writes (scenario_hygiene.saved_orphans), unless a check asks for the session's count.
    saved = check.get("unused_counted", "saved") == "saved"
    found = scenario_hygiene.saved_orphans(bpy) if saved else scenario_hygiene.orphans(bpy)
    unused = {collection: [name for name in names if name not in FACTORY_ORPHANS.get(collection, ())]
              for collection, names in found.items()}
    unused = {collection: names for collection, names in unused.items() if names}
    count = sum(len(names) for names in unused.values())
    problems = []
    if leftovers:
        problems.append("default objects left")
    if len(default_named) > check.get("max_default_named", 0):
        problems.append("default-named objects")
    if len(bare) > check.get("max_without_material", 0):
        problems.append("meshes without a material")
    if count > check.get("max_unused", math.inf):
        problems.append("unused data-blocks")
    live = sum(len(names) for names in scenario_hygiene.orphans(bpy).values()) if saved else count
    return not problems, {"leftover_defaults": leftovers, "default_named": default_named[:12],
                          "meshes_without_material": bare[:12], "unused": count,
                          "unused_counted": "in a save" if saved else "in the session", "unused_in_session": live,
                          "unused_names": {key: names[:8] for key, names in unused.items()}, "problems": problems}


CHECKS = {"surface": _check_surface, "visible": _check_visible, "detail": _check_detail, "hygiene": _check_hygiene}
import oneshot_mini_checks  # noqa: E402  (the kinds of the mini tests: built things found by shape, not by name)
CHECKS.update(oneshot_mini_checks.CHECKS)
import oneshot_form_checks  # noqa: E402  (how many separate pieces, limbs or openings there are)
CHECKS.update(oneshot_form_checks.CHECKS)
import castle_checks  # noqa: E402  (round towers, battlements, an arch, nothing floating: oneshot-castle)
CHECKS.update(castle_checks.CHECKS)
import oneshot_opacity  # noqa: E402  (a stated wall, roof or slab has no see-through gaps; grader only)
CHECKS.update(oneshot_opacity.CHECKS)
import heavy_checks  # noqa: E402  (what the heavy scene's edits must read: scenario-heavy)
CHECKS.update(heavy_checks.CHECKS)
import many_object_checks  # noqa: E402  (scenario-shop, scenario-workshop: rows, offsets, an untouched rest)
CHECKS.update(many_object_checks.CHECKS)
import head_checks  # noqa: E402  (a head and its hair against the ground truth's, in the pictures' views)
CHECKS.update(head_checks.CHECKS)


# ---------------------------------------------------------------- the still the brief asks for

PATH_RUN = re.compile(r"[^\s\"'`<>|*?\\]+")   # A stretch of text a stated path can lie in.


def mentioned_paths(text, name):
    """Absolute or home-relative paths ending in ``name`` that a text states (a reply, a script), in order.

    In each stretch without spaces, quotes or shell characters: from its first ``/`` (with a ``~`` just before
    it) to the end of the last ``name`` after it, case-insensitively, as the pattern ``~?/[^...]*name`` finds it.
    It runs in linear time: a client's stream holds base64 images, megabytes without a space, where that
    pattern took minutes.
    """
    found, wanted = [], re.compile(re.escape(name), re.IGNORECASE)
    for match in PATH_RUN.finditer(str(text)):
        run = match.group(0)
        slash = run.find("/")
        last = None
        if slash >= 0:
            for last in wanted.finditer(run, slash + 1):
                pass
        if last is None:
            continue
        start = slash - 1 if slash and run[slash - 1] == "~" else slash
        path = os.path.expanduser(run[start:last.end()])
        if path not in found:
            found.append(path)
    return found[:50]


def output_candidates(name, roots, render_path="", mentioned=()):
    """Files that may be the named still: under the side's folders, where the scene's render path points, or
    at a path the side itself stated (``mentioned``: it was asked to say where the file is)."""
    import scenario_checks
    found = scenario_checks.find_outputs(name, roots)
    stem = os.path.splitext(name)[0].lower()
    direct = list(mentioned)
    if render_path:
        path = os.path.abspath(str(render_path))
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        direct += [path, os.path.join(folder, name)]
        try:
            direct += [os.path.join(folder, entry) for entry in sorted(os.listdir(folder))[:5000]
                       if os.path.splitext(entry)[0].lower() in (stem, stem + "0001")]
        except OSError:
            pass
    for path in direct:
        base = os.path.splitext(os.path.basename(path))[0].lower()
        if os.path.isfile(path) and base in (stem, stem + "0001") and path not in found:
            found.append(path)
    return found


def evaluate_outputs(outputs, roots, since=None, render_path="", said="", reply="", receipts=()):
    """A brief's required files as check results (``kind`` ``output``), like ``scenario_checks.evaluate_outputs``.

    ``since``: the wall-clock time the message was sent; an older file is
    another run's. ``render_path``: the scene's own render path, where a
    plain render writes. ``said``: what the side wrote (its reply, its
    scripts); a path it states there is looked at too. ``reply``: the turn's
    final reply and ``receipts``: Angelo's job records, which name the final
    render when it is not at the requested name (``final_render``: the file
    the agent names is judged; ``details["final"]`` says which and why). The
    passing file's full path is kept in ``path`` for the harness's later pixel
    check; reports print its name only.
    """
    import final_render
    import scenario_checks
    results = []
    for index, wanted in enumerate(outputs):
        entry = {"index": index, "kind": "output", "label": wanted.get("label", wanted["name"]), "passed": False}
        seen = []
        try:
            candidates = output_candidates(wanted["name"], roots, render_path, mentioned_paths(said, wanted["name"]))
            candidates += final_render.find(wanted["name"], list(roots) + _render_folders(render_path))
            candidates += final_render.stated(said, wanted["name"]) + final_render.stated(reply, wanted["name"])
            candidates = final_render.variants(wanted["name"], list(dict.fromkeys(candidates)))
            final = final_render.decide(wanted["name"], candidates, reply, receipts, since)
            for path in final_render.judged(candidates, final):
                facts = {"file": os.path.basename(path), "bytes": os.path.getsize(path)}
                problems = ["bytes"] if facts["bytes"] < wanted.get("min_bytes", 1) else []
                if since is not None and os.path.getmtime(path) < since - 2.0:
                    problems.append("written before this run")
                if "min_size" in wanted:
                    size = scenario_checks.image_size(path)
                    facts["size"] = list(size) if size else None
                    if size is None or size[0] < wanted["min_size"][0] or size[1] < wanted["min_size"][1]:
                        problems.append("size")
                facts["problems"] = problems
                seen.append(facts)
                if not problems:
                    entry.update(passed=True, details=dict(facts, final=final), path=path)
                    break
            else:
                entry["details"] = {"files": seen[:6] or "no file named %s" % final["file"], "folders": len(roots),
                                    "final": final}
        except OSError as error:
            entry["details"] = {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
        results.append(entry)
    return results


def _render_folders(render_path):
    """The folder the scene's render path points into, where a plain render writes (none when unset)."""
    if not render_path:
        return []
    path = os.path.abspath(str(render_path))
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    return [folder] if os.path.isdir(folder) else []


def with_outputs(result, outputs, roots, since=None, render_path="", said="", reply="", receipts=()):
    """``scenario_checks.evaluate``'s result with the brief's files appended; ``success`` needs both."""
    extra = evaluate_outputs(outputs or (), list(roots), since, render_path, said, reply, receipts)
    for entry in extra:
        entry["index"] += result["total"]
    results = result["results"] + extra
    return {"success": bool(result["success"]) and all(entry["passed"] for entry in extra),
            "passed": sum(entry["passed"] for entry in results), "total": len(results), "results": results}


# ---------------------------------------------------------------- pixels

def difference(first, second):
    """Mean absolute difference of two same-shape luminance thumbnails (0 identical, 1 opposite); else None."""
    if not first or not second or len(first) != len(second):
        return None
    return sum(abs(a - b) for a, b in zip(first, second)) / len(first)


def pixel_checks(stats, baselines, label):
    """Closed checks on one still's measured statistics (``oneshot_job.image_stats``).

    ``baselines``: thumbnails of the factory scene (``{name: thumbnail}``) the still must differ from.
    A still that could not be read fails every check; nothing is assumed.
    """
    readable = isinstance(stats, dict) and not stats.get("error") and stats.get("pixels")

    def entry(name, passed, details):
        return {"kind": "pixels", "label": "%s: %s" % (label, name), "passed": bool(readable and passed),
                "details": details if readable else {"error": (stats or {}).get("error", "no image")}}
    if not readable:
        return [entry(name, False, None) for name in ("not empty", "not black", "not the default scene")]
    distances = {name: difference(stats.get("thumbnail"), thumbnail) for name, thumbnail in (baselines or {}).items()}
    known = [value for value in distances.values() if value is not None]
    return [
        entry("not empty", stats["spread"] >= EMPTY_SPREAD, {"luminance_spread": stats["spread"],
                                                             "least": EMPTY_SPREAD}),
        entry("not black", stats["mean"] >= BLACK_MEAN and stats["dark_share"] < BLACK_SHARE,
              {"luminance_mean": stats["mean"], "dark_share": stats["dark_share"]}),
        entry("not the default scene", bool(known) and min(known) >= DEFAULT_DIFFERENCE,
              {"difference_from_default": {name: None if value is None else round(value, 4)
                                           for name, value in distances.items()}, "least": DEFAULT_DIFFERENCE}),
    ]
