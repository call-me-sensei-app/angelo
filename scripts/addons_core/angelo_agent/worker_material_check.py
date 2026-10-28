# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The evaluated check of a placement's material swaps (``worker_materials``), and the removal of a refused merge.

A ``View`` reads, from one evaluation, what each object shows: its own result element by element (faces, curves,
points, grease pencil strokes of each layer's current frame, a volume, and the geometry its node groups instance,
nested references included), by Blender's own rule for an evaluated object's material (an object-linked slot with a
material wins at its index, an index past the list takes its last entry), and the objects it instances: a collection
(nested instancers included), a particle system's object or collection, an object or collection its node groups
instance. An instance shows its object's own result, whatever the path to it, so each object is read once per
evaluation and compared once per swap; the set an object reaches is kept once per object (once per collection for
the objects instancing it), walked level by level only past Blender's limit, and the path only built for a refusal
(break-it P2 M4: per-path work made an instance-heavy merge 21 times slower). Instancing deeper
than Blender's own limit (``DEPTH``), and an instanced object the evaluation does not hold, are unread: a swap there is
refused, never passed. ``judge`` compares a copy as built with the same copy swapped: the swap's material exactly
where the part's was, and nothing else changed (break-it P2: an empty instancing a collection was skipped, so its
unswapped objects passed). ``ids`` and ``remove_added``: a merge refused after it appended removes every datablock it
added (appended data, copies, variants, node group copies), so nothing of it stays in the lead's file.
"""

DEPTH = 8           # Blender's own instance recursion limit (``MAX_DUPLI_RECUR``); anything deeper is unread.
UNREAD = "unread"   # An entry's kind when the check could not read it: a swap there is refused, never passed.
GEOMETRY = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "GREASEPENCIL", "CURVES", "POINTCLOUD", "VOLUME"})


def _shown_codes(materials, overrides, indices, names):
    """Each element's material (as a pointer, 0 for none): Blender's own rule (``BKE_object_material_get_eval``): an
    index past the list is clamped to its last entry, and an object-linked slot with a material wins at its index."""
    import numpy
    shown = [overrides.get(index) or (material.original if material is not None else None)   # Not its evaluated copy.
             for index, material in enumerate(materials)]
    for material in shown:
        if material is not None:
            names.setdefault(material.as_pointer(), material.name)
    if not shown:
        return numpy.zeros(len(indices), dtype=numpy.int64)
    table = numpy.array([m.as_pointer() if m is not None else 0 for m in shown], dtype=numpy.int64)
    return table[numpy.clip(indices, 0, len(shown) - 1)]


def _indices(attributes, count, domain):
    import numpy
    values = numpy.zeros(count, dtype=numpy.int32)
    attribute = attributes.get("material_index")
    if count and attribute is not None and attribute.domain == domain and attribute.data_type == "INT":
        attribute.data.foreach_get("value", values)
    return values


def _geometry(geometry, overrides, names, label, entries, edges, level):
    """Append ``(label, codes, is_material)`` for each part of an evaluated GeometrySet (a nested GeometrySet inline,
    one level deeper), and to ``edges`` ``(how, object, levels)`` for each object or collection it instances."""
    import bpy
    import numpy

    def add(what, materials, attributes, count, domain):
        entries.append(((label + " " + what).strip(), _shown_codes(materials, overrides, _indices(
            attributes, count, domain), names), True))
    if level > DEPTH:
        entries.append((label, None, UNREAD))
        return
    if geometry.mesh is not None:
        add("faces", geometry.mesh.materials, geometry.mesh.attributes, len(geometry.mesh.polygons), "FACE")
    if geometry.curves is not None:
        add("curves", geometry.curves.materials, geometry.curves.attributes, len(geometry.curves.curves), "CURVE")
    if geometry.pointcloud is not None:
        cloud = geometry.pointcloud
        add("points", cloud.materials, cloud.attributes, len(cloud.points), "POINT")
    if geometry.volume is not None:
        entries.append(((label + " volume").strip(), _shown_codes(geometry.volume.materials, overrides, [0], names),
                        True))
    pencil = geometry.grease_pencil
    for layer in pencil.layers if pencil is not None else ():
        frame = layer.current_frame()
        if frame is not None and frame.drawing is not None:
            drawing = frame.drawing
            add("strokes of layer %r" % layer.name, pencil.materials, drawing.attributes, len(drawing.strokes), "CURVE")
    points = geometry.instances_pointcloud()
    if points is None:
        return
    attribute = points.attributes.get(".reference_index")
    if attribute is not None:   # Which reference each instance shows: the same before and after a swap.
        values = numpy.zeros(len(points.points), dtype=numpy.int32)
        attribute.data.foreach_get("value", values)
        entries.append(((label + " instance references").strip(), values, False))
    for index, reference in enumerate(geometry.instance_references()):
        inner = ("%s instance %d" % (label, index)).strip()
        if isinstance(reference, bpy.types.GeometrySet):   # Shown with the owner's object-linked slots.
            _geometry(reference, overrides, names, inner, entries, edges, level + 1)
        else:   # An object, or a collection's objects, each shown with its own slots.
            for other in [reference] if isinstance(reference, bpy.types.Object) else list(reference.all_objects):
                edges.append((inner, other, level + 1))


def _picked(settings, objects):
    """The members a particle system instances from its collection's ``objects``: every one with the whole collection
    or without counts, else those whose count is above zero (Blender's ``make_duplis_particle_system``; break-it
    P2R2 L-1: a member weighted zero refused a merge). A weight names its object as ``"<name>: <count>"``."""
    if settings.use_whole_collection or not settings.use_collection_count:
        return objects
    counted = {weight.name.rsplit(": ", 1)[0]: weight.count for weight in settings.instance_weights}
    return [obj for obj in objects if counted.get(obj.name, 1) > 0]   # Unlisted: Blender adds it with count 1.


def instanced(obj, collections=True, members=None):
    """[(how, [objects])]: what ``obj`` shows besides its own geometry: the collection it instances (all its objects,
    child collections' included; ``collections`` False leaves it out) and each particle system's instanced object or
    the members it picks from its collection (``_picked``), unless the system shows nothing (no particles, or its
    modifier off in viewports and renders). ``members``: a dict that keeps one list per collection, shared by every
    object instancing it."""
    found = []

    def listed(collection):
        if members is None:
            return list(collection.all_objects)
        key = ("members", collection.as_pointer())
        if key not in members:
            members[key] = list(collection.all_objects)
        return members[key]
    collection = obj.instance_collection if collections and obj.instance_type == "COLLECTION" else None
    if collection is not None:
        found.append(("collection %r" % collection.name, listed(collection)))
    particles = list(getattr(obj, "particle_systems", ()))
    systems = {modifier.particle_system.as_pointer(): modifier for modifier in getattr(obj, "modifiers", ())
               if modifier.type == "PARTICLE_SYSTEM"} if particles else {}
    for system in particles:
        settings, modifier = system.settings, systems.get(system.as_pointer())
        if settings.count == 0 or (modifier is not None and not (modifier.show_viewport or modifier.show_render)):
            continue
        if settings.render_type == "OBJECT" and settings.instance_object is not None:
            found.append(("particle system %r" % system.name, [settings.instance_object]))
        elif settings.render_type == "COLLECTION" and settings.instance_collection is not None:
            found.append(("particle system %r" % system.name, _picked(settings, listed(settings.instance_collection))))
    return found


def _original(obj):
    return getattr(obj, "original", None) or obj


class View:
    """What objects show in one evaluation (``depsgraph``), read when asked and kept: read every object a check needs
    before the scene changes. ``names``: pointer -> name for messages, filled with each material read. Edges are kept
    as pointers, a collection's members once per collection, and the objects an object reaches once per object (with
    its longest instance chain); only a chain longer than ``DEPTH`` is walked level by level."""

    def __init__(self, depsgraph, names, known=None):
        self.depsgraph, self.names = depsgraph, names
        self.objects, self.own, self.edges, self.reaches, self.sets = {}, {}, {}, {}, {}
        self.closures, self.members, self.groups = {}, {}, {}   # Groups: per object, the collections it instances.
        self.member_names = {}
        self.known = {} if known is None else known   # Object -> original pointer: the same in every evaluation.
        self.shown, self.unreadable = {}, {}   # Per object: the materials it shows; per reach set: what is unread.

    def pointer(self, obj):
        """The original's pointer, kept per object (a datablock hashes by its pointer, without reading a property)."""
        found = self.known.get(obj)
        if found is None:
            original = _original(obj)
            found = self.known[obj] = original.as_pointer()
            self.objects.setdefault(found, original)
        elif found not in self.objects:
            self.objects[found] = _original(obj)
        return found

    def read(self, obj):
        """The object's pointer, its own result read (None when the evaluation does not hold it) and its edges."""
        pointer = self.pointer(obj)
        self._load(pointer)
        return pointer

    def _load(self, pointer):
        if pointer in self.own:
            return
        import bpy
        original = self.objects[pointer]
        copy = original.evaluated_get(self.depsgraph)
        entries, edges = [], []
        evaluated = copy.is_evaluated
        if evaluated:
            kind = copy.type
            if kind in GEOMETRY:
                overrides = {index: slot.material for index, slot in enumerate(original.material_slots)
                             if slot.link == "OBJECT" and slot.material is not None}
                _geometry(bpy.types.GeometrySet.from_evaluated_object(copy), overrides, self.names, "", entries,
                          edges, 0)
            collection = original.instance_collection if original.instance_type == "COLLECTION" else None
            if collection is not None:
                self.groups[pointer] = [self._members(collection)]
            if kind == "MESH":   # Only a mesh carries particle systems.
                edges += [(how, other, 1) for how, others in instanced(original, collections=False)
                          for other in others]
        self.own[pointer] = entries if evaluated else None
        self.edges[pointer] = [(how, self.pointer(other), levels) for how, other, levels in edges]

    def _members(self, collection):
        key = collection.as_pointer()
        if key not in self.members:
            self.members[key] = [self.pointer(other) for other in collection.all_objects]
            self.member_names[key] = collection.name   # Read once, for a refusal's path.
        return key

    def links(self, pointer):
        """Every edge of ``pointer``: ``(how, object pointer, levels)``, a collection's members included."""
        self._load(pointer)
        return self.edges[pointer] + [("collection %r" % self.member_names[key], member, 1)
                                      for key in self.groups.get(pointer, ()) for member in self.members[key]]

    def evaluated(self, obj):
        return self.own[self.read(obj)] is not None

    def _closure(self, pointer):
        """(every object ``pointer`` reaches, its longest instance chain in levels), once per object; a collection's
        once per collection, shared by every object instancing it."""
        if pointer not in self.closures:
            self.closures[pointer] = (frozenset(), DEPTH + 1)   # A loop of instances counts as too deep.
            self._load(pointer)
            parts = [self._group_closure(key) for key in self.groups.get(pointer, ())]
            if self.edges[pointer] or not parts:
                parts.append(self._closure_of(self.edges[pointer]))
            if len(parts) == 1:   # Mostly an empty instancing one collection: that collection's own set.
                self.closures[pointer] = (self.sets.setdefault(parts[0][0], parts[0][0]), parts[0][1])
            else:
                reached = frozenset().union(*(found for found, _height in parts))
                self.closures[pointer] = (self.sets.setdefault(reached, reached), max(h for _found, h in parts))
        return self.closures[pointer]

    def _group_closure(self, key):
        if ("group", key) not in self.closures:
            self.closures[("group", key)] = (frozenset(), DEPTH + 1)
            found, height = self._closure_of([("", member, 1) for member in self.members[key]])
            self.closures[("group", key)] = (self.sets.setdefault(found, found), height)
        return self.closures[("group", key)]

    def _closure_of(self, edges):
        reached, height, merged = set(), 0, set()
        for _how, inner, levels in edges:
            reached.add(inner)
            more, below = self._closure(inner)
            height = max(height, levels + below)
            if id(more) not in merged:   # Objects reaching the same set: merged once.
                merged.add(id(more))
                reached |= more
        return frozenset(reached), height

    def reach(self, pointer, depth=0):
        """(objects reached within ``DEPTH``, objects first met deeper), as pointers."""
        reached, height = self._closure(pointer)
        return (reached, frozenset()) if depth + height <= DEPTH else self._levels(pointer, depth)

    def _levels(self, pointer, depth):
        key = (pointer, depth)
        if key not in self.reaches:
            self.reaches[key] = (frozenset(), frozenset())   # A loop of instances ends at the depth limit anyway.
            reached, deeper = set(), set()
            for _how, inner, levels in self.links(pointer):
                self._load(inner)
                if depth + levels > DEPTH:
                    deeper.add(inner)
                    continue
                reached.add(inner)
                more, beyond = self.reach(inner, depth + levels)
                reached |= more
                deeper |= beyond
            reached, deeper = frozenset(reached), frozenset(deeper - reached)
            self.reaches[key] = (self.sets.setdefault(reached, reached), self.sets.setdefault(deeper, deeper))
        return self.reaches[key]

    def path(self, start, target):
        """The labelled path from ``start`` to ``target`` (both pointers), for a refusal: ``how (name) how (name)``."""
        from collections import deque
        queue, seen = deque([(start, "", 0)]), {start}
        while queue:
            pointer, label, depth = queue.popleft()
            for how, inner, levels in self.links(pointer):
                step = ("%s %s (%s)" % (label, how, self.names.get(inner, self.objects[inner].name))).strip()
                if inner == target:
                    return step
                if inner not in seen and depth + levels <= DEPTH:
                    seen.add(inner)
                    queue.append((inner, step, depth + levels))
        return "its instances"


def _swap_key(swap):
    return tuple(sorted((a.as_pointer(), b.as_pointer()) for a, b in swap.items()))


def unread(view, obj, start=None):
    """What ``obj`` shows that ``view`` could not read (instances deeper than ``DEPTH``, an instanced object the
    evaluation does not hold), by its path, or None."""
    start = view.read(obj) if start is None else start   # Its pointer, when the caller knows it.
    reached, deeper = view.reach(start)
    if deeper:
        return "%s nest deeper than the check reads (%d levels)" % (view.path(start, min(deeper)), DEPTH)
    unreadable = _unreadable(view, reached)
    for pointer, path in ((start, ""), *((p, None) for p in (sorted(unreadable, key=lambda p: view.objects[p].name)
                                                             if unreadable else ()))):
        if pointer == start and not any(kind == UNREAD for _label, _codes, kind in view.own[start] or ()):
            continue
        path = view.path(start, pointer) if path is None else path
        if view.own[pointer] is None:
            return "%s is not evaluated in this view layer" % path
        label = next(label for label, _codes, kind in view.own[pointer] if kind == UNREAD)
        return "%s nest deeper than the check reads (%d levels)" % (("%s %s" % (path, label)).strip()
                                                                    or "its instances", DEPTH)
    return None


def _unreadable(view, reached):
    if reached not in view.unreadable:
        view.unreadable[reached] = [p for p in reached if view.own[p] is None or any(
            kind == UNREAD for _label, _codes, kind in view.own[p])]
    return view.unreadable[reached]


def judge(before, after, obj, swap, names, cache, start=None):
    """Why ``obj`` evaluated in ``after`` is not ``before`` with ``swap`` applied (its own result and every object it
    instances), or None; ``unread`` first. ``cache``: {(object pointer, swap): verdict}, shared by every copy."""
    if ("swap", id(swap)) not in cache:   # The swap's key once per check (its dict lives through the check).
        cache[("swap", id(swap))] = (swap, _swap_key(swap))
    start, key = before.read(obj) if start is None else start, cache[("swap", id(swap))][1]
    if ("judged", start, key) not in cache:
        cache[("judged", start, key)] = _judge(before, after, obj, start, swap, key, names, cache)
    return cache[("judged", start, key)]


def _judge(before, after, obj, start, swap, key, names, cache):
    if start not in after.own:
        after.read(obj)
    was, now = before.reach(start)[0], after.reach(start)[0]
    if was != now:
        changed = sorted(before.objects.get(p, after.objects.get(p)).name for p in was ^ now)
        return "the objects it instances changed (%s)" % ", ".join(changed[:6])
    why = mismatch(before.own[start], after.own[start], swap, names)
    if why is not None:
        return why
    verdict = cache.get((was, key))
    if verdict is None:
        wrong = [p for p in was if _differs(before, after, p, swap, key, names, cache)]
        verdict = cache[(was, key)] = min(wrong, key=lambda p: before.objects[p].name) if wrong else 0
    if verdict:
        return mismatch(before.own[verdict], after.own[verdict], swap, names, before.path(start, verdict))
    return None


def _differs(before, after, pointer, swap, key, names, cache):
    if (pointer, key) not in cache:
        cache[(pointer, key)] = mismatch(before.own[pointer], after.own[pointer], swap, names) is not None
    return cache[(pointer, key)]


def materials(view, obj):
    """Every material pointer ``obj`` shows in ``view``, its instances' included (each object, and each set of
    objects reached, read once)."""
    start, found = view.read(obj), set()
    reached = view.reach(start)[0]
    if reached not in view.shown:
        view.shown[reached] = frozenset().union(*(_shown(view, pointer) for pointer in reached))
    return found.union(_shown(view, start), view.shown[reached])


def _shown(view, pointer):
    import numpy
    if pointer not in view.shown:
        view.shown[pointer] = frozenset(int(code) for _label, codes, kind in view.own[pointer] or ()
                                        if kind is True and codes is not None for code in numpy.unique(codes))
    return view.shown[pointer]


def mismatch(before, after, swap, names, prefix=""):
    """Why ``after`` (one result, swapped) is not ``before`` with ``swap`` applied element by element, or None.
    ``prefix``: the path to the instanced object these entries are, for the message."""
    import numpy
    pointers = {a.as_pointer(): b.as_pointer() for a, b in swap.items()}
    if [label for label, *_ in before] != [label for label, *_ in after]:
        return "its evaluated geometry changed (%s)" % ", ".join(sorted({label for label, *_ in before}
                                                                         ^ {label for label, *_ in after}))
    for (label, was, is_material), (_label, now, _kind) in zip(before, after):
        if was is None or now is None:   # Unread (``unread``): the caller refuses it before comparing.
            continue
        label = ("%s %s" % (prefix, label)).strip()
        if len(was) != len(now):
            return "its %s changed in number (%d, then %d)" % (label, len(was), len(now))
        expected = was.copy()
        if is_material is True:
            for source, target in pointers.items():
                expected[was == source] = target
        wrong = numpy.flatnonzero(expected != now)
        if len(wrong):
            first = wrong[0]
            return "%d of its %s show %s where %s belongs" % (
                len(wrong), label, names.get(int(now[first]), "no material"), names.get(int(expected[first]),
                                                                                       "no material"))
    return None


def where(obj, owned=None):
    """Where a material can reach ``obj``'s result besides its slots (a refusal says it). ``owned``: the objects of
    ``obj``'s placement, so what it instances from outside them is said."""
    modifiers = [m.name for m in obj.modifiers if m.type != "PARTICLE_SYSTEM"]
    shown = ["%s%s" % (how, ", whose objects this placement does not own" if owned is not None
                       and any(other not in owned for other in others) else "") for how, others in instanced(obj)]
    reasons = (["what it instances: %s" % "; ".join(shown)] if shown else []) + (
        ["its modifiers (%s)" % ", ".join(modifiers)] if modifiers else [])
    return "it comes from " + (" and ".join(reasons) if reasons else "what it instances"
                               if obj.instance_type != "NONE" else "its data")


def ids():
    """Every datablock's pointer now: what a merge refused after appending removes is what is not in this."""
    import bpy
    return {item.as_pointer() for name in _kinds(bpy) for item in getattr(bpy.data, name)}


def _kinds(bpy):
    return [prop.identifier for prop in bpy.types.BlendData.bl_rna.properties
            if prop.type == "COLLECTION" and prop.identifier not in {"all_ids", "window_managers", "screens",
                                                                     "workspaces"}]


def remove_added(before):
    """Remove every datablock not in ``before`` (``ids``): a refused merge leaves nothing of itself."""
    import bpy
    added = [item for name in _kinds(bpy) for item in getattr(bpy.data, name) if item.as_pointer() not in before]
    if added:
        bpy.data.batch_remove(added)
