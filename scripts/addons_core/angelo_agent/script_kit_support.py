# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Support through the rest of the scene for ``kit.check_supported`` (script_kit).

A part is supported when a chain of contacts joins it to the ground: through the group's own parts (their bounds
touch, as before) and through any other geometry the scene shows: a car on a deck on piers, a cup on a table on its
legs, a frame on a wall that stands on the floor. Support is never one contact deep: a wheel touches its car's body,
so a car floating in the air would pass a check of its wheels alone.

Only the components (parts whose bounds touch) the group cannot hold up are searched, outward from their parts.
Other objects and instances count when they are visible, rendered and drawn solid, and never the group's own objects
or the instances they make. Contacts are read from surfaces, never from an object's bounds (a terrain's or a room's
would hold everything inside them): a part touches an object when the part's bounds, grown by the tolerance, meet one
of its triangles; two objects touch when a triangle of each meets the grown bounds of a triangle of the other. An
object is grounded when its lowest point is within the tolerance of the ground, or it touches a part of the group that
is. The search stops once every component is joined to something grounded, or nothing new touches them.

A floating part's row names the nearest surface straight below it and the gap, and the lowest point its chain of
contacts reaches, so a fix needs no diagnosis round.

Bounded: the vertices and objects read count against the check's own limits, the objects and instances listed
against ``objects``, and a contact test past MAX_PAIRS comparisons stops the search; past any of them the check is
unmeasured, never passed.
"""

from . import script_kit_geometry as geometry
from .script_kit_contacts import (MAX_PAIRS, _near_corners, _overlapping, boxes_meeting_corners,  # noqa: F401
                                  boxes_meeting_surface, triangles_cross_box)   # (re-exported for the tests)
from .script_kit_values import _visible
from .object_bounds import InstanceFacts

NOT_SOLID = frozenset({"WIRE", "BOUNDS"})   # Drawn as a frame (cutters, guides, helpers): never a support.
CHUNK = 262144         # Triangles gathered at once while indexing a surface (about 19 MB).
MAX_STEPS = 64         # Surfaces a ray below a part passes (its own, unrendered ones) before it stops.
EPSILON = 1e-4
# The rays below a floating part: its footprint's centre and four points just inside its corners.
FOOTPRINT = ((0.5, 0.5), (0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9))


class OverLimit(Exception):
    """The search would pass one of its limits: the check is not measured."""


def scene_support(depsgraph, prefix, floating, seeds, ground, tolerance, vertices, reads, objects):
    """Whether each floating component is joined to the ground through the scene.

    ``floating`` maps a component's label to its parts' world bounds; ``seeds`` are the bounds of the group's parts
    already held up. Returns {label: None when the component is supported, else the lowest z its contacts reach}.
    Raises OverLimit (its text is the check's reason)."""
    import numpy
    labels = sorted(floating)
    parts = _Parts([floating[label] for label in labels], tolerance)
    seed_lo = numpy.array([box[0] for box in seeds], dtype=float).reshape(-1, 3) - tolerance
    seed_hi = numpy.array([box[1] for box in seeds], dtype=float).reshape(-1, 3) + tolerance
    graph = _Graph()
    for n, label in enumerate(labels):
        graph.add(("part", n), min(box[0][2] for box in floating[label]), False)
    scene = _Scene(prefix, tolerance, vertices, reads)

    def join(number, surface, frontier):
        """Join a read object to what it touches: the open floating parts (``frontier`` None, the first round) or
        the frontier's objects. True when it joined a set it was not in."""
        node, joined = ("object", number), False
        if node not in graph:
            grounded = surface.lo[2] <= ground + tolerance or _touches_boxes(seed_lo, seed_hi, surface)
            graph.add(node, surface.lo[2], grounded)
        if frontier is None:
            near = parts.reaching(surface.lo, surface.hi)
            met = _met(parts.lo[near], parts.hi[near], surface.points, surface.triangles, surface.name)
            for n in set(parts.part_of[near[met]].tolist()):
                joined = graph.union(("part", n), node) or joined
            if joined:
                parts.refresh(graph)
            return joined
        for other in frontier:
            if not graph.same(("object", other), node) and surfaces_meet(scene.read[other], surface, tolerance):
                joined = graph.union(("object", other), node) or joined
        if joined:
            parts.refresh(graph)
        return joined

    # The first round, while the scene is listed: what touches the floating parts; it ends once all are grounded.
    first = []
    for number, surface in scene.list(depsgraph, objects, parts.wanted):
        if join(number, surface, None):
            first.append(number)
        if parts.region is None:
            break
    frontier = [number for number in first if not graph.grounded(("object", number))]
    while frontier and parts.region is not None:   # Then outward, one ring of contacts at a time.
        reaching = scene.reaching(frontier)   # Lowest first: what may ground a set is tried first.
        reached = []
        for number, surface in scene.fetch(depsgraph, sorted(reaching, key=scene.bottom)):
            if join(number, surface, reaching[number]):
                reached.append(number)
                if parts.region is None:
                    break
        frontier = [n for n in reached if not graph.grounded(("object", n))]
    return {label: None if graph.grounded(("part", n)) else graph.lowest(("part", n))
            for n, label in enumerate(labels)}


def surfaces_meet(a, b, tolerance):
    """Whether two read surfaces come within ``tolerance``, taken only where their own bounds overlap: a triangle of
    each meets the grown bounds of a triangle of the other. Both ways, because a large slanted triangle's bounds (a
    low-poly slope's) hold much it never comes near; any real contact passes both."""
    import numpy
    lo = numpy.maximum(a.lo, b.lo) - tolerance
    hi = numpy.minimum(a.hi, b.hi) + tolerance
    if (lo > hi).any():
        return False
    near_a, near_b = a.corners(a.near(lo, hi)), b.corners(b.near(lo, hi))
    if not len(near_a) or not len(near_b):
        return False
    for boxes, surface in ((near_a, near_b), (near_b, near_a)):
        met = boxes_meeting_corners(boxes.min(axis=1) - tolerance, boxes.max(axis=1) + tolerance, surface, first=True)
        if met is None:
            raise OverLimit("over %d comparisons between %r and %r: not measured" % (MAX_PAIRS, a.name, b.name))
        if not met.any():
            return False
    return True


def nearest_below(scene, depsgraph, box, own, group):
    """The highest surface straight below a floating part, as (object name, z) or None: rays down from five points
    of its footprint, from its lowest height. A ray passes the part's own surface (``own(name, location)``) and, of
    other objects, those that never support (unrendered or drawn as a frame); a part of the group (``group(name)``)
    stops it, supported or not, so a stack's upper part names the part it sits on."""
    from mathutils import Vector
    (x0, y0, z0), (x1, y1, _z1) = box
    down, best = Vector((0.0, 0.0, -1.0)), None
    for fx, fy in FOOTPRINT:
        start = Vector((x0 + fx * (x1 - x0), y0 + fy * (y1 - y0), z0))
        for _ in range(MAX_STEPS):
            hit, location, _normal, _index, obj, _matrix = scene.ray_cast(depsgraph, start, down)
            if not hit:
                break
            name = obj.original.name
            if own(name, location) or not group(name) and (obj.hide_render or obj.display_type in NOT_SOLID):
                start = location + down * EPSILON
                continue
            if best is None or location.z > best[1]:
                best = (name, location.z)
            break
    return best


def reached(near, seeds, count):
    """Which of ``count`` parts are reached from ``seeds`` through touching parts (``near``)."""
    found = [False] * count
    for i in seeds:
        found[i] = True
    frontier = list(seeds)
    while frontier:
        for j in near[frontier.pop()]:
            if not found[j]:
                found[j] = True
                frontier.append(j)
    return found


def components(near, members):
    """A label for each of ``members`` (a part index to its component's first part): parts whose bounds touch,
    directly or through others, share one."""
    labels = {}
    for start in members:
        if start in labels:
            continue
        labels[start], frontier = start, [start]
        while frontier:
            for j in near[frontier.pop()]:
                if j not in labels:
                    labels[j] = start
                    frontier.append(j)
    return labels


class _Parts:
    """The floating parts' bounds grown by the tolerance, the component of each, and which are still open (their
    component not yet joined to the ground), with the region around those: a first, cheap test for each object."""

    def __init__(self, components, tolerance):
        import numpy
        boxes = [(n, box) for n, members in enumerate(components) for box in members]
        self.lo = numpy.array([box[0] for _n, box in boxes], dtype=float).reshape(-1, 3) - tolerance
        self.hi = numpy.array([box[1] for _n, box in boxes], dtype=float).reshape(-1, 3) + tolerance
        self.part_of, self.count = numpy.array([n for n, _box in boxes], dtype=int), len(components)
        self.open, self.region = numpy.ones(len(boxes), dtype=bool), None
        self._set_region()

    def refresh(self, graph):
        import numpy
        still = numpy.array([not graph.grounded(("part", n)) for n in range(self.count)], dtype=bool)
        self.open = still[self.part_of]
        self._set_region()

    def wanted(self, box_lo, box_hi):
        """Whether an object's bounds reach an open part."""
        region = self.region
        if region is None or any(box_lo[a] > region[1][a] or box_hi[a] < region[0][a] for a in range(3)):
            return False
        return bool(len(self.reaching(box_lo, box_hi)))

    def reaching(self, box_lo, box_hi):
        """The open parts whose grown bounds overlap the box."""
        import numpy
        return numpy.flatnonzero(self.open & _overlapping(self.lo, self.hi, box_lo, box_hi))

    def _set_region(self):
        self.region = None if not self.open.any() else (self.lo[self.open].min(axis=0).tolist(),
                                                         self.hi[self.open].max(axis=0).tolist())


class _Surface:
    """A supporting object's evaluated surface in world space: its points, triangles (vertex indices) and bounds, and
    an index of its triangles' bounds, built when first asked: sorted by least x, with triangles over four times the
    median width in x kept apart and always compared. So an object touching a terrain reads only the terrain's
    triangles near it."""

    __slots__ = ("name", "points", "triangles", "lo", "hi", "_low", "_high", "_order", "_keys", "_reach", "_wide")

    def __init__(self, name, points, triangles):
        self.name, self.points, self.triangles = name, points, triangles
        self.lo, self.hi = points.min(axis=0), points.max(axis=0)
        self._low = None

    def near(self, lo, hi):
        """The indices of the triangles whose bounds overlap the box (``lo``, ``hi``)."""
        import numpy
        if self._low is None:
            self._index()
        start = numpy.searchsorted(self._keys, lo[0] - self._reach, side="left")
        end = numpy.searchsorted(self._keys, hi[0], side="right")
        found = numpy.concatenate((self._order[start:end], self._wide))
        return found[((self._low[found] <= hi) & (self._high[found] >= lo)).all(axis=1)]

    def corners(self, indices):
        return self.points[self.triangles[indices]]

    def _index(self):
        import numpy
        lows, highs = [], []
        for start in range(0, len(self.triangles), CHUNK):
            corners = self.points[self.triangles[start:start + CHUNK]]
            lows.append(corners.min(axis=1))
            highs.append(corners.max(axis=1))
        self._low, self._high = numpy.concatenate(lows), numpy.concatenate(highs)
        width = self._high[:, 0] - self._low[:, 0]
        wide = width > 4.0 * float(numpy.median(width))
        narrow = numpy.flatnonzero(~wide)
        self._wide = numpy.flatnonzero(wide)
        self._order = narrow[numpy.argsort(self._low[narrow, 0], kind="stable")]
        self._keys = self._low[self._order, 0]
        self._reach = float(width[narrow].max()) if len(narrow) else 0.0


class _Scene:
    """The other visible geometry: listed once with its world bounds, read when the search reaches it."""

    def __init__(self, prefix, tolerance, vertices, reads):
        self._prefix, self._tolerance, self._vertices, self._reads = prefix, tolerance, vertices, reads
        self.sources, self.read, self._lo, self._hi, self._objects, self._looks = [], {}, None, None, 0, 0

    def list(self, depsgraph, objects, wanted):
        """List every possible support with its bounds; yield (number, surface) for those ``wanted(lo, hi)`` takes,
        read as they are listed (an instance exists only while it is listed)."""
        import numpy
        lows, highs, owners = [], [], {}
        self._objects = self._looks = objects   # Later rounds may look at as many again.
        for index, instance in enumerate(depsgraph.object_instances):
            if index >= objects:
                raise OverLimit("over %d objects and instances to search for support: not measured" % objects)
            obj = instance.object
            # An instance renders as its instancer says: hiding the instanced object from renders is how Object Info
            # and particle instancing are set up (REVIEW-OVERLAP-A-2 M3, as the native helper rule since H1); a
            # collection instance's member hidden from renders is left out, as render picks members (round 2 L3).
            if obj.type not in geometry.GEOMETRY or obj.display_type in NOT_SOLID or obj.hide_render and (
                    not instance.is_instance or getattr(instance.parent, "instance_type", "") == 'COLLECTION'):
                continue
            owner = (instance.parent if instance.is_instance else obj).original
            key = owner.as_pointer()
            if key not in owners:   # One look per instancer: a scatter's thousands of instances share it.
                owners[key] = not owner.name.startswith(self._prefix) and not owner.hide_render and _visible(owner)
            if not owners[key]:
                continue
            matrix = instance.matrix_world
            box = _world_box(obj.bound_box, matrix)
            number = len(self.sources)
            self.sources.append((index, None if instance.is_instance else obj.original, owner.name[:64]))
            lows.append(box[0])
            highs.append(box[1])
            if wanted(box[0], box[1]):
                surface = self._surface(number, obj, matrix)
                if surface is not None:
                    yield number, surface
        self._lo = numpy.array(lows, dtype=float).reshape(-1, 3) - self._tolerance
        self._hi = numpy.array(highs, dtype=float).reshape(-1, 3) + self._tolerance

    def reaching(self, frontier):
        """{support: the frontier objects it may touch}: listed supports whose grown bounds overlap a frontier
        object's bounds and one of its triangles' (a terrain's bounds alone would take everything on it)."""
        import numpy
        found = {}
        if self._lo is None:
            return found
        for other in frontier:
            surface = self.read[other]
            near = numpy.flatnonzero((self._lo <= surface.hi).all(axis=1) & (self._hi >= surface.lo).all(axis=1))
            self._looks -= len(near)
            if self._looks < 0:
                raise OverLimit("over %d objects and instances to search for support: not measured" % self._objects)
            for number in near.tolist():
                if number != other and len(surface.near(self._lo[number], self._hi[number])):
                    found.setdefault(number, []).append(other)
        return found

    def bottom(self, number):
        return float(self._lo[number][2])

    def fetch(self, depsgraph, numbers):
        """(number, surface) for each of ``numbers`` with a surface, read once: objects directly, instances while
        the scene is listed again."""
        by_index = {}
        for number in numbers:
            if number in self.read:
                if self.read[number] is not None:
                    yield number, self.read[number]
                continue
            index, original, _name = self.sources[number]
            if original is None:
                by_index[index] = number
                continue
            evaluated = original.evaluated_get(depsgraph)
            surface = self._surface(number, evaluated, evaluated.matrix_world)
            if surface is not None:
                yield number, surface
        if by_index:
            for index, instance in enumerate(depsgraph.object_instances):
                number = by_index.pop(index, None)
                if number is not None:
                    surface = self._surface(number, instance.object, instance.matrix_world)
                    if surface is not None:
                        yield number, surface
                if not by_index:
                    break

    def _surface(self, number, obj, matrix):
        self._reads -= 1
        if self._reads < 0:
            raise OverLimit("over the part limit with the objects near floating parts: not measured")
        try:
            mesh = obj.to_mesh()
        except RuntimeError:   # A type Blender cannot turn into a mesh here: it has no surface to rest on.
            self.read[number] = None
            return None
        try:
            self._vertices -= len(mesh.vertices)
            if self._vertices < 0:
                raise OverLimit("over the vertex limit with the objects near floating parts: not measured")
            surface = None
            if len(mesh.vertices) and len(mesh.loop_triangles):
                surface = _Surface(self.sources[number][2], geometry.world_points(mesh, matrix), _triangles(mesh))
        finally:
            obj.to_mesh_clear()
        self.read[number] = surface
        return surface


class _Graph:
    """Union-find over the floating components and the objects they reach: each set knows whether it is grounded and
    the lowest point it reaches."""

    def __init__(self):
        self._parent, self._grounded, self._lowest = {}, {}, {}

    def __contains__(self, node):
        return node in self._parent

    def add(self, node, lowest, grounded):
        self._parent[node], self._lowest[node], self._grounded[node] = node, lowest, grounded

    def find(self, node):
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def same(self, a, b):
        return self.find(a) == self.find(b)

    def union(self, a, b):
        """Join two sets; True when they were apart."""
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        self._parent[b] = a
        self._grounded[a] = self._grounded[a] or self._grounded[b]
        self._lowest[a] = min(self._lowest[a], self._lowest[b])
        return True

    def grounded(self, node):
        return self._grounded[self.find(node)]

    def lowest(self, node):
        return self._lowest[self.find(node)]


def _met(lo, hi, points, triangles, name):
    met = boxes_meeting_surface(lo, hi, points, triangles)
    if met is None:
        raise OverLimit("over %d comparisons on %r: not measured" % (MAX_PAIRS, name))
    return met


def _touches_boxes(lo, hi, surface):
    """Whether a surface meets any of the boxes (the group's held-up parts, grown)."""
    import numpy
    near = numpy.flatnonzero(_overlapping(lo, hi, surface.lo, surface.hi))
    return bool(len(near)) and bool(_met(lo[near], hi[near], surface.points, surface.triangles, surface.name).any())


def _triangles(mesh):
    import numpy
    flat = numpy.empty(len(mesh.loop_triangles) * 3, dtype=numpy.int32)
    mesh.loop_triangles.foreach_get("vertices", flat)
    return flat.reshape(-1, 3)


def _world_box(corners, matrix):
    """World bounds of a local bound box (Blender's corner 0 is its least, corner 6 its greatest) under ``matrix``:
    the transformed centre plus the extents through the matrix's absolute values. Written out: it runs once for each
    object and instance listed, and a scatter has tens of thousands."""
    a, b = corners[0], corners[6]
    (m00, m01, m02, m03), (m10, m11, m12, m13), (m20, m21, m22, m23), _row = matrix
    cx, cy, cz = (a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5, (a[2] + b[2]) * 0.5
    hx, hy, hz = abs(b[0] - a[0]) * 0.5, abs(b[1] - a[1]) * 0.5, abs(b[2] - a[2]) * 0.5
    x = m00 * cx + m01 * cy + m02 * cz + m03
    y = m10 * cx + m11 * cy + m12 * cz + m13
    z = m20 * cx + m21 * cy + m22 * cz + m23
    ex = abs(m00) * hx + abs(m01) * hy + abs(m02) * hz
    ey = abs(m10) * hx + abs(m11) * hy + abs(m12) * hz
    ez = abs(m20) * hx + abs(m21) * hy + abs(m22) * hz
    return (x - ex, y - ey, z - ez), (x + ex, y + ey, z + ez)


def no_bounds(bpy, scene, items):
    """``kit.bounds``' error when nothing matches ``items``: with the hidden objects a prefix starts, which it leaves
    out, and how each is hidden (``visibility_facts``): they exist, though it measures none of them."""
    import json
    from .object_bounds import makes_instances
    from .script_kit import NOT_GEOMETRY
    from .visibility_facts import MAX_NAMES, Pass
    message = "kit.bounds: no object with geometry matches %r" % (items,)
    try:
        hidden = Pass(bpy.context.view_layer)
        found = [obj for item in items if isinstance(item, str) for obj in scene.objects
                 if obj.name.startswith(item) and not _visible(obj)
                 and (obj.type not in NOT_GEOMETRY or makes_instances(obj))]   # What it would measure if shown.
        named = [(obj.name, hidden.of(obj)) for obj in found[:MAX_NAMES]]   # Facts only for those it names.
    except (AttributeError, TypeError, RuntimeError, ReferenceError):
        return message
    shown = "; ".join("%r %s" % (name[:64], json.dumps(facts or {})) for name, facts in named)
    more = " and %d more" % (len(found) - MAX_NAMES) if len(found) > MAX_NAMES else ""
    return message + ("; hidden, so not measured: %s%s" % (shown, more) if found else "")


def instance_facts(depsgraph):
    """Exact instance facts for ``kit.bounds`` (``object_bounds``): one native pass, only if an object makes them,
    with no limit at all (a script asked for this box)."""
    return InstanceFacts(depsgraph, digits=None, limit=0)


def add_instances(obj, instances, found):
    """Add the world box of what ``obj``'s instances show to ``found``; False when it shows no geometry of its own
    (its own box would be its data's: a curve's control points padded by their radius). Raises ValueError, with the
    reason, when they could not be read (never a box short of what it shows)."""
    fact = instances.of(obj)
    if fact is None:
        return True
    if "unknown" in fact:   # Never a box short of what it shows: say so (REVIEW-INSTANCES-4 M-A).
        raise ValueError("kit.bounds: the instances of %r could not be read: %s" % (getattr(obj, "name", obj),
                                                                                    fact["unknown"]))
    found.append((tuple(fact["bounds"][0]), tuple(fact["bounds"][1])))
    return fact.get("own_geometry", True)
