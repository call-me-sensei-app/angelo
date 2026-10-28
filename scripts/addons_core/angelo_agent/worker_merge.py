# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_workers_merge``: the lead brings its workers' finished parts into its own scene, as ordinary data.

Each part named is appended from its worker's file (``bpy.data.libraries.load``, never linked: the result is one
ordinary .blend, with no library and no private Main) into a new collection named after the part, and placed once per
**placement**. A repeated design is built once and placed many times: the first placement moves the appended part, every
further one is a copy of the whole part whose objects share their data with it (Blender's linked duplicates), with
parents, modifier targets and constraint targets kept inside the copy. A placement may swap materials (``materials``: a
material of the part -> a material of this scene or of the part, by name, or a **variant**: a copy of the part's
material with its own name and base colour, roughness or metallic) for that placement's copies only, so one design
carries several finishes while every copy still shares its mesh: ``worker_materials`` swaps every place the material
reaches a copy's evaluated result (its slots, a modifier's own material, Geometry Nodes, through per-swap copies of the
node groups), refuses what one placement cannot swap alone, and checks each copy by evaluating it (break-it P2-3). A
variant is made once per merge per part and reused by every placement of that part that names it (two parts' materials
of one name stay two materials, break-it E2E-1 M-1); the result names each variant as it was made.

Placements are world-space transforms of the part as its worker built it (about the world origin of its file). An
appended object has no world matrix until Blender evaluates it (it is not stored in the file), so a placement moves
each root from its own stored transform (``matrix_basis``), never from ``matrix_world`` (break-it E2E-1 H-1: every
part landed at the origin at unit scale); children and full Child Of followers keep theirs. A part keeps its
collections, each collection's own visibility, and its view layer's visibility (``view_state``: excluded and hidden
collections, hidden objects, which the worker reports with each save, since a view layer is never appended), for every
placement (E2E-1 M-2: a hidden helper showed and rendered). The whole merge is one approved call, applied inside
Angelo's Undo operator for approved changes, so it is one Undo step. A part merges only once its worker finished its
turn (the same predicate as the status wait, ``worker_sessions.merge_blocker``), bound to the exact saved file the
approval was given for; a merged worker stops (its part file stays in Angelo's state folder, so an Undo and a second
merge still find it). The result's per-part name lists share one budget (``SHOWN``), each cut list saying its total.
"""

import os
from contextlib import suppress
from math import isfinite

from . import parallel_sessions, worker_chat_lifecycle, worker_material_check, worker_materials
from . import worker_placement as placement_check

MAX_PLACEMENTS = 64
MAX_OBJECTS = 4000          # Objects one merge may add, copies included.
SHOWN = 120                 # Objects listed in the result ({name, type}; the model's copy keeps names only).
PLACEMENT = {"type": "object", "properties": {
    "location": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
                 "description": "XYZ Euler, radians."},
    "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
    "materials": {"type": "object", "additionalProperties": {"anyOf": [
        {"type": "string", "minLength": 1, "maxLength": 63},
        {"type": "object", "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 63},
            "base_color": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 4},
            "roughness": {"type": "number", "minimum": 0, "maximum": 1},
            "metallic": {"type": "number", "minimum": 0, "maximum": 1}},
         "required": ["name"], "additionalProperties": False}]},
                  "description": ("Material swaps for this placement only: a material of the part -> the name of a "
                                  "material of this scene or the part, or a variant {name, base_color?, roughness?, "
                                  "metallic?}: a copy of the part's material with those values.")}},
    "additionalProperties": False}
PART = {"type": "object", "properties": {
    "worker": {"type": "integer", "minimum": 1, "description": "The worker's number (angelo_workers_status)."},
    "placements": {"type": "array", "minItems": 1, "maxItems": MAX_PLACEMENTS, "items": PLACEMENT,
                   "description": "Where the part goes; one entry per copy. Omitted: once, where it was built."},
    "again": {"type": "boolean", "description": "A part merged into this scene before merges again: another copy."}},
    "required": ["worker"], "additionalProperties": False}


def validate(args):
    parts = args.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 16:
        raise ValueError("Give 1-16 parts to merge")
    clean, seen = [], set()
    for part in parts:
        if (not isinstance(part, dict) or not set(part) <= {"worker", "placements", "again"}
                or type(part.get("worker")) is not int or type(part.get("again", False)) is not bool):
            raise ValueError("Each part is {worker, placements?, again?}")
        if part["worker"] in seen:
            raise ValueError("Worker %d is given twice; give all its placements in one entry" % part["worker"])
        seen.add(part["worker"])
        placements = []
        for placement in part.get("placements") or [{}]:
            if not isinstance(placement, dict) or not set(placement) <= {"location", "rotation", "scale", "materials"}:
                raise ValueError("A placement is {location?, rotation?, scale?, materials?}")
            entry = {}
            for key, default in (("location", (0.0, 0.0, 0.0)), ("rotation", (0.0, 0.0, 0.0)), ("scale", (1.0, 1.0, 1.0))):
                value = placement.get(key, default)
                if (not isinstance(value, (list, tuple)) or len(value) != 3
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v) for v in value)):
                    raise ValueError("A placement's %s is three finite numbers" % key)
                entry[key] = [float(v) for v in value]   # JSON shapes: validating again changes nothing.
            if any(abs(v) < 1e-6 for v in entry["scale"]):
                raise ValueError("A placement's scale may not be zero")
            swaps = placement.get("materials") or {}
            if not isinstance(swaps, dict) or not all(isinstance(k, str) and k for k in swaps):
                raise ValueError("A placement's materials map a part's material names to swaps")
            entry["materials"] = {key: _swap_value(value) for key, value in sorted(swaps.items())}
            placements.append(entry)
        clean.append({"worker": part["worker"], "placements": placements, "again": part.get("again", False)})
    return {"parts": clean}


def _swap_value(value):
    if isinstance(value, str) and value.strip() and len(value) <= 63:
        return value
    if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip() \
            or len(value["name"]) > 63 or not set(value) <= {"name", "base_color", "roughness", "metallic"}:
        raise ValueError("A swap is a material name or {name, base_color?, roughness?, metallic?}")
    clean = {"name": value["name"]}
    colour = value.get("base_color")
    if colour is not None:
        if (not isinstance(colour, (list, tuple)) or len(colour) not in (3, 4) or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in colour)):
            raise ValueError("A variant's base_color is 3 or 4 numbers from 0 to 1")
        clean["base_color"] = [float(v) for v in colour] + ([1.0] if len(colour) == 3 else [])
    for key in ("roughness", "metallic"):
        if key in value:
            number = value[key]
            if not isinstance(number, (int, float)) or isinstance(number, bool) or not 0 <= number <= 1:
                raise ValueError("A variant's %s is a number from 0 to 1" % key)
            clean[key] = float(number)
    return clean


def _canonical(arguments):
    return tuple((part["worker"], part["again"], tuple((tuple(p["location"]), tuple(p["rotation"]), tuple(p["scale"]),
                                         tuple((k, worker_materials.key(v)) for k, v in sorted(p["materials"].items())))
                                        for p in part["placements"]))
                 for part in arguments["parts"])


def _working(chat):
    return "it is still working" if chat.busy or chat.pending else None


def part_refusal(number, chat, path, lead, blocker=None, again=False):
    """Why worker ``number``'s part cannot merge now, as a ``RefusalError``, or None: the merge's own admission of one
    part, which status and the lead's observation read too (``worker_facts``), so a part they call mergeable is one a
    merge takes and one they do not is one it refuses. ``chat``: the worker's chat, or None once it left Angelo.
    ``lead``: the lead's chat (its own scene). ``again``: the merge's own word that a part merged there merges anew."""
    from .refusal_codes import RefusalError
    why = (blocker or _working)(chat) if chat is not None else None
    if why is not None:
        return RefusalError("Worker %d's part is not ready to merge: %s; angelo_workers_status says when its turn has "
                            "ended" % (number, why), code="WRITER_BUSY")
    try:
        if not again:
            parallel_sessions.refuse_merged_again(number, path, lead)
    except RefusalError as refused:
        return refused
    if not os.path.isfile(path):
        return RefusalError("Worker %d has no saved part yet" % number, code="VALIDATION")
    from .worker_part_counts import UNKNOWN
    count = part_object_count(path)
    if count is UNKNOWN:   # Its live worker's lease is asked off the main thread, never waited for here.
        return RefusalError("Worker %d's part is still being checked: its worker has not yet let its saved file be "
                            "read; angelo_workers_status says when it is mergeable" % number, code="WRITER_BUSY")
    if count == 0:   # Its file is made when it launches: one that never saved an object is empty.
        from . import worker_sessions
        session = chat.state.get("id") if chat is not None else None
        why = worker_sessions.died.get(session) if session is not None else None
        return RefusalError("Worker %d's part file holds no objects%s, so a merge would add nothing" % (
            number, " (its worker ended before it saved any: %s)" % why[:160] if why else ""), code="VALIDATION")
    return None


def part_object_count(path):
    """How many objects a saved part file holds (``worker_part_counts.count``: never waits on a worker): a count,
    ``UNKNOWN`` while its live worker's lease is being asked (not mergeable yet), or None when it cannot be read here
    (then nothing is refused for it)."""
    from . import worker_part_counts
    return worker_part_counts.count(path)


def auto_merge_allowed(controller, arguments):
    """Whether Auto may run this merge without a card: never one whose ``again`` places a part merged into this scene
    before, which the person approves on its card (break-it E2E-20 R5-M-2: Auto ran it unseen)."""
    again = [part.get("worker") for part in (arguments or {}).get("parts") or ()
             if isinstance(part, dict) and part.get("again") is True]
    if not again:
        return True
    from . import worker_sessions
    parts = worker_sessions.parts_of(controller)
    return not any(number in parts and parallel_sessions.part_in_file(parts[number][1], controller) for number in again)


def refuse_away(lead, window=None):
    """A merge lands in ``lead``'s own scene only while ``window`` (else the main window, as the chat's gate reads it:
    ``document_scene.away``) shows it, never in another by any route (E2E-20 R5-M-1): else refused (AWAY's facts)."""
    if window is None:
        if getattr(lead, "document", None) is None:
            return
        from . import document_scene, ui
        if not document_scene.away(lead, ui.document_key()):
            return
        window = ui.main_window()
    scene, shown = parallel_sessions._document_scene(lead), getattr(window, "scene", None)
    if scene is None or scene != shown:
        from .refusal_codes import RefusalError
        raise RefusalError("This chat works on scene '%s'; the main window shows scene '%s'. A merge lands only in "
                           "its chat's own scene, while the window shows it, so nothing was merged" % (
                               getattr(scene, "name", "?"), getattr(shown, "name", "?")), code="STALE_CONTEXT")


def precondition(controller, arguments, parts, blocker=None):
    """``parts``: {number: (worker chat or None, part path)} of this lead. The exact saved files the merge binds to.
    ``blocker(chat)``: why that worker's part is not ready to merge, or None (``worker_sessions.merge_blocker``)."""
    from .refusal_codes import RefusalError
    arguments = validate(arguments)
    refuse_away(controller)
    bound, total = [], 0
    for part in arguments["parts"]:
        number = part["worker"]
        if number not in parts:
            raise RefusalError("This conversation has no worker %d; angelo_workers_status lists its workers" % number,
                               code="VALIDATION")
        chat, path = parts[number]
        refused = part_refusal(number, chat, path, controller, blocker, part["again"])
        if refused is not None:
            raise refused
        try:
            stat = os.stat(path)
        except OSError:
            raise RefusalError("Worker %d has no saved part yet" % number, code="VALIDATION") from None
        bound.append((number, stat.st_size, stat.st_mtime_ns))
        total += len(part["placements"])
    if total > MAX_PLACEMENTS * 4:
        raise RefusalError("At most %d placements in one merge" % (MAX_PLACEMENTS * 4), code="LIMIT_EXCEEDED")
    return ("merge", tuple(bound), _canonical(arguments))


def _matrix(placement):
    from mathutils import Euler, Matrix, Vector
    return Matrix.LocRotScale(Vector(placement["location"]), Euler(placement["rotation"]).to_quaternion(),
                              Vector(placement["scale"]))


def _remap(copy, mapping):
    """Object pointers of ``copy``'s modifiers and constraints that point inside the part point inside the copy."""
    for stack in (copy.modifiers, copy.constraints):
        for item in stack:
            for prop in item.bl_rna.properties:
                if prop.type == "POINTER" and getattr(prop, "fixed_type", None) is not None \
                        and prop.fixed_type.identifier == "Object" and not prop.is_readonly:
                    target = getattr(item, prop.identifier)
                    if target is not None and target in mapping:
                        setattr(item, prop.identifier, mapping[target])


LIMIT = 4096   # Names per kind a view state carries (as ``live_layout``).


def view_state(scene, view_layer):
    """The worker's view-layer visibility (its layout's ``view``, sent with each save): its collections excluded or
    hidden in the view layer and its objects hidden there. None of it is in what a merge appends."""
    excluded, hidden = [], []
    stack = list(view_layer.layer_collection.children)
    while stack:
        item = stack.pop()
        if item.exclude:
            excluded.append(item.collection.name)
        if item.hide_viewport:
            hidden.append(item.collection.name)
        stack.extend(item.children)
    objects = [obj.name for obj in scene.objects if obj.hide_get(view_layer=view_layer)]
    return {"excluded": excluded[:LIMIT], "hidden": hidden[:LIMIT], "objects": objects[:LIMIT]}


def clean_view(value):
    """A view state from the worker, checked (it crosses a process boundary); empty when it is not one."""
    clean = {}
    for key in ("excluded", "hidden", "objects"):
        items = value.get(key) if isinstance(value, dict) else None
        listed = items if isinstance(items, list) else []
        clean[key] = [item for item in listed if isinstance(item, str) and item][:LIMIT]
    return clean


def _tree(objects, collections, layout):
    """(top collections, objects directly in the scene) of the part as its worker's scene holds them: its layout's
    names when the worker reported them, else what nothing else holds (an object no collection holds is then
    taken as the scene's own)."""
    if layout is not None and not layout.get("truncated"):
        return ([collections[name] for name in layout["collections"] if name in collections],
                [objects[name] for name in layout["objects"] if name in objects])
    children = {child for collection in collections.values() for child in collection.children}
    held = {obj for collection in collections.values() for obj in collection.objects}
    return ([c for c in collections.values() if c not in children], [o for o in objects.values() if o not in held])


def _mirror(collection, instance, made):
    """``collection`` again for one copy of the part: its own visibility, its children, and the copy's objects."""
    if collection in made:
        return made[collection]
    import bpy
    copy = made[collection] = bpy.data.collections.new(collection.name)
    for flag in ("hide_viewport", "hide_render", "hide_select"):
        setattr(copy, flag, getattr(collection, flag))
    for obj in collection.objects:
        if obj in instance:
            copy.objects.link(instance[obj])
    for child in collection.children:
        copy.children.link(_mirror(child, instance, made))
    return copy


def _layer_collections(layer, found):
    for child in layer.children:
        found.setdefault(child.collection, []).append(child)
        _layer_collections(child, found)
    return found


def _identity(placement):
    return (placement["location"] == [0.0, 0.0, 0.0] and placement["rotation"] == [0.0, 0.0, 0.0]
            and placement["scale"] == [1.0, 1.0, 1.0])


def _hide(view_layer, hidden):
    """The workers' view-layer visibility on the lead's view layer: objects first (an excluded collection's objects
    have no Base to hide), then hidden and excluded collections."""
    for view, _collections, objects in hidden:
        for name in view["objects"]:
            obj = objects.get(name)
            if obj is not None:
                with suppress(RuntimeError):   # Only in collections this view layer excludes: no Base, nothing shows.
                    obj.hide_set(True, view_layer=view_layer)
    layers = _layer_collections(view_layer.layer_collection, {}) if any(v["hidden"] or v["excluded"]
                                                                        for v, _c, _o in hidden) else {}
    for key, field in (("hidden", "hide_viewport"), ("excluded", "exclude")):
        for view, collections, _objects in hidden:
            for name in view[key]:
                for item in layers.get(collections.get(name), ()):
                    setattr(item, field, True)


def _append(part, path, scene, collection_name, layout):
    """A part appended into a new collection and linked as built once per placement (every further one a copy of
    the part whose objects share its data). Nothing is placed yet: the first evaluation is the oracle."""
    import bpy
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        object_names, material_names = list(source.objects), list(source.materials)
        collection_names = list(source.collections)
        target.objects, target.materials = list(source.objects), list(source.materials)
        target.collections = list(source.collections)
    by_name = {name: obj for name, obj in zip(object_names, target.objects) if obj is not None}
    groups = {name: c for name, c in zip(collection_names, target.collections) if c is not None}
    objects = list(by_name.values())
    tops, loose = _tree(by_name, groups, layout)
    collection = bpy.data.collections.new(collection_name)
    scene.collection.children.link(collection)
    inside = set(objects)
    free = [obj for obj in objects if obj.parent not in inside]   # Each placed as a root, unless carried (``merge``).
    instances = [{obj: obj for obj in objects}]
    for _placement in part["placements"][1:]:   # Every copy from the part as built, before any swap or move.
        instance = {obj: obj.copy() for obj in objects}   # Shares each object's data: a linked duplicate.
        for original, copy in instance.items():
            if original.parent in instance:
                copy.parent = instance[original.parent]   # Setting a parent resets its inverse: keep the built one.
                copy.parent_type, copy.parent_bone = original.parent_type, original.parent_bone
                copy.matrix_parent_inverse = original.matrix_parent_inverse.copy()
            _remap(copy, instance)
        instances.append(instance)
    every_mirror = []
    for index, instance in enumerate(instances):
        mirrors = {c: c for c in groups.values()} if index == 0 else {}
        for top in tops:
            collection.children.link(_mirror(top, instance, mirrors) if index else top)
        for obj in loose:
            collection.objects.link(instance[obj])
        every_mirror.append(mirrors)
    return {"part": part, "by_name": by_name, "groups": groups, "objects": objects, "inside": inside, "free": free,
            "materials": {name: m for name, m in zip(material_names, target.materials) if m is not None},
            "built": {obj: obj.matrix_basis.copy() for obj in free},   # Stored data, not the world matrix (H-1).
            "as_built": {obj: placement_check.as_built(obj) for obj in free},   # Kept by a placed basis (E2E-15 H-1).
            "instances": instances, "mirrors": every_mirror, "collection": collection,
            "view": clean_view((layout or {}).get("view")),
            "renamed": {old: obj.name for old, obj in zip(object_names, target.objects)
                        if obj is not None and obj.name != old}}


def merge(lead, window, arguments, parts, name_of, layouts=None):
    """Append and place each part in ``lead``'s own scene, which ``window`` shows (``refuse_away``); the result's
    ``changes`` lists what was added. Every part is checked before any is appended, so a refusal changes nothing;
    whatever fails after the first append (a refused material swap, or any fault) removes every datablock the merge
    added and puts the scene's record back, so a merge is whole or not at all (break-it E2E-20 R5-L-1: a fault left a
    part unplaced, or an earlier part unrecorded, and a retry added a second copy). ``layouts``: {number: the worker's
    newest layout (``live_layout``), with its ``view``}, where known. Where each copy landed is checked without
    evaluating what the check does not read (``worker_placement``): the scene is evaluated only when a part holds an
    object whose world only evaluation gives, then twice whatever the number of parts (E2E-6 M-2, E2E-7 M-1)."""
    import time
    import bpy
    from .refusal_codes import RefusalError
    started = time.perf_counter()
    arguments = validate(arguments)
    refuse_away(lead, window)
    scene, total = window.scene, 0
    for part in arguments["parts"]:
        with bpy.data.libraries.load(str(parts[part["worker"]][1]), link=False) as (source, _target):
            names, count = set(source.materials), len(source.objects)   # Names only: nothing is loaded.
        if count == 0:   # The lead's own read under the lease decides, whatever a worker reported (part_counts).
            raise RefusalError("Worker %d's part file holds no objects, so a merge would add nothing" % part["worker"],
                               code="VALIDATION")
        worker_materials.check_names(part, names, part["worker"])
        total += count * len(part["placements"])
    if total > MAX_OBJECTS:
        raise RefusalError("One merge adds at most %d objects (this one %d); nothing was merged" % (MAX_OBJECTS, total),
                           code="LIMIT_EXCEEDED")
    before, unrecord = worker_material_check.ids(), None   # What a failure after the first append leaves.
    try:
        appended = [_append(part, parts[part["worker"]][1], scene, name_of(part["worker"]),
                            (layouts or {}).get(part["worker"])) for part in arguments["parts"]]
        unrecord = parallel_sessions.parts_merged([parts[part["worker"]][1] for part in arguments["parts"]], scene)
        result = _place(window, arguments, appended, before, unrecord)
    except BaseException as error:
        worker_material_check.remove_added(before)
        if unrecord is not None:
            unrecord()
        with suppress(AttributeError):
            error.partial_changes_possible = False   # Nothing of the merge stays.
        raise
    result["merge_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def _place(window, arguments, appended, before, unrecord):
    """``merge``'s placing, swaps and checks of the appended parts, and its answer."""
    swapping = any(placement["materials"] for part in arguments["parts"] for placement in part["placements"])
    added, merged = [], []
    hidden = []   # (view layer state, {worker collection name: lead collection}, {worker object name: lead object})
    checks = []   # What each placement must have done, checked once everything is placed.
    for item in appended:   # The workers' view state first: what a worker excluded is never evaluated (E2E-8 M-1).
        for index, instance in enumerate(item["instances"]):
            mirrors = item["mirrors"][index]
            hidden.append((item["view"], {name: mirrors[c] for name, c in item["groups"].items() if c in mirrors},
                           {name: instance[obj] for name, obj in item["by_name"].items()}))
    _hide(window.view_layer, hidden)
    # A refused swap removes what the merge added and puts the scene's record back (``merge`` does too, again).
    swaps = worker_materials.Swaps(appended, window.view_layer, before, unrecord) if swapping else None
    slow = [placement_check.needs_evaluation(item["objects"]) for item in appended]
    depsgraph = placement_check.evaluated(window.view_layer) if any(slow) else None   # As built: the oracle.
    if swaps is not None:
        swaps.as_built(depsgraph)   # What each copy a swap concerns shows as built, before any is swapped.
    built = [placement_check.built(item["objects"], needs, depsgraph) for item, needs in zip(appended, slow)]
    # Every part's worlds as built are read above, before any is placed.
    names, copies = {"lights": {}, "cameras": {}, "renamed": {}}, placement_check.COPIES
    for number, (item, needs, worlds) in enumerate(zip(appended, slow, built)):
        part, objects, instances = item["part"], item["objects"], item["instances"]
        # A full Child Of follower of the part is carried by its target, as a child by its parent: never moved itself.
        # Decided after the evaluation as built, which settles an inverse still pending (break-it E2E-11 L-2).
        roots, placed = [obj for obj in item["free"] if not placement_check.carried(obj, item["inside"])], []
        for index, (placement, instance) in enumerate(zip(part["placements"], instances)):
            if not _identity(placement):
                transform = _matrix(placement)
                for root in roots:
                    instance[root].matrix_basis = transform @ item["built"][root]
                    placement_check.keep_signs(instance[root], item["as_built"][root], transform)
                placed.append((index, transform, instance))
            added.extend({"name": copy.name, "type": copy.type} for copy in instance.values())   # As every tool.
        checks.append((objects, worlds, placed, roots, needs, item["as_built"]))
        merged.append({"worker": part["worker"], "collection": item["collection"].name, "objects": len(objects),
                       "placements": len(part["placements"]), "renamed": {}})
        # A light or camera a worker added to look at its part now lights or frames the lead's scene: each once.
        for kind in ("lights", "cameras"):
            names[kind][id(objects)] = [{"object": o.name, "copies": [i[o].name for i in instances[:copies]],
                                         "count": len(instances)} for o in objects if o.type == kind[:-1].upper()]
        names["renamed"][id(objects)] = sorted(item["renamed"].items())
    if swaps is not None:   # Once every copy is placed: what an unswapped copy shows is read placed first.
        swaps.apply_all()
    entries = {id(objects): entry for (objects, *_rest), entry in zip(checks, merged)}
    placed_now = placement_check.evaluated(window.view_layer) if depsgraph is not None else None   # Placed.
    if swaps is not None:
        swaps.check(placed_now)   # Evaluated: each swap where the part's material was, else refused and removed.
    for number, (item, entry) in enumerate(zip(appended, merged)):
        entry.update(swaps.report(number) if swaps is not None else {})
    warned, unmeasured, later = (   # A copy off now first, then by name.
        {key: [found[name] for name in sorted(found, key=lambda name: (not found[name].get("off_now"), name))]
         for key, found in table.items()} for table in placement_check.misplaced(checks, placed_now))
    room = SHOWN   # One budget for the name lists, by importance across parts (E2E-10 M-2, E2E-11 M-3, E2E-12 M-1).
    for field, table, why in (("placement_warnings", warned, None), ("renamed", names["renamed"], None),
                              ("moves_later", later, placement_check.MOVES_LATER),
                              ("not_measured", unmeasured, placement_check.NOT_MEASURED),
                              ("lights", names["lights"], None), ("cameras", names["cameras"], None)):
        for key, entry in entries.items():
            listed = table.get(key) or []
            if listed:   # Each object once, with up to 3 of its copies and how many in all; a cut list says its total.
                shown, room = listed[:room], max(0, room - len(listed))
                shown = dict(shown) if field == "renamed" else shown
                entry[field] = {"objects": shown, "why": why} if why else shown
                if len(shown) < len(listed):
                    entry[field + "_total"] = len(listed)
    return {"success": True, "scene_changed": True, "merged": merged,
            "changes": {"added": added[:SHOWN], "removed": [], "modified": [], "truncated": len(added) > SHOWN,
                        "scope": "Parts merged from their workers' files"},
            "note": "The parts are now ordinary objects of this scene; their workers stopped."}
