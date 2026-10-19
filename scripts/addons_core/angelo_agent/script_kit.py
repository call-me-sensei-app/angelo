# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact building blocks and self-checks for reviewed scripts: the ``kit`` name in every script's namespace.

Made for the mistakes recorded build scripts made (doc/angelo/performance.rst, "Castle one-shot misses"):
a boolean cutter wound inward cut nothing; an EXACT boolean on a mesh of overlapping boxes deleted most of it;
repeated units overshot their span; and a self-check that measured the wrong thing let each pass. So:

* every solid is closed and wound outward by construction (``box``, ``cylinder``, ``prism``, ``row``);
* ``cut`` subtracts from the target's mesh on copies (``script_kit_geometry.cut_mesh``) and changes nothing until
  the result is proven: it raises, so the host rolls the script back, for a target or cutter it cannot prove (not a
  closed solid, in Edit Mode, with a modifier that makes or moves geometry, a cutter something else uses) and when
  the cutter does not overlap the target, nothing would be left, the target would lose extent where the cutter does
  not reach or grow, or the volume removed is not the overlap's; ``arch`` cuts an arched opening with it and checks
  its crown;
* ``row`` places evenly spaced units strictly inside the span it is given;
* ``parent`` parents parts without moving them, after an evaluation (``script_kit_parent``);
* ``check``, ``check_bounds``, ``check_ray`` and ``check_supported`` record measured values beside their targets
  (a part is joined to the ground by contacts with the group's other parts or any other visible surface:
  ``script_kit_support``). A check never raises: one it cannot make is recorded as failed (or, when it is too large
  to measure, unmeasured) with its reason. The executor returns ``outcome()``: ``self_checks`` (the first passing
  and failing checks with their values, capped, then failing ones by name) and, for the host only,
  ``self_checks_passed`` and ``self_checks_failed`` (the names of the others); for a Claude plan a failing check
  comes back to the model once before the turn can end, as information, never a gate (``self_checks.SelfChecks``).

No new privilege: the kit only does what the reviewed script itself may do, with the same restore point. A script
can still record what it likes: its checks are a reliability aid for the model's own work, not a security boundary.
"""

import math

from . import script_kit_geometry as geometry
from . import script_kit_parent as parents
from . import script_kit_support as support
from .script_kit_values import _as_list, _axis, _capped, _hide, _live, _number, _rounded, _sequence, _spent_still, \
    _vector, _visible

MAX_PASSING = 32        # Passing checks listed with their values; the rest by name for the host (self_checks_passed).
MAX_FAILING = 32        # Failing checks listed with their values; the rest by name, up to MAX_LISTED.
MAX_LISTED = 160        # Past it, failing checks reach the host by name only (self_checks_failed).
MAX_HOST_NAMES = 1024   # Names of each kind for the host only; failing ones past it are held open under OVERFLOW.
OVERFLOW = "kit: more failing checks than listed"
MAX_UNITS = 10000
EPSILON = 1e-4
NOT_GEOMETRY = frozenset({"EMPTY", "LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE", "ARMATURE", "LATTICE"})


class ScriptKit:
    def __init__(self, bpy, scene):
        self._bpy, self._scene = bpy, scene
        self.checks, self.passed_names, self.failed_names, self.unlisted_passes, self.overflow = [], [], [], 0, 0
        self._passing = self._failing = 0
        self._repeats, self._labels, self._spent, self.kept = {}, set(), [], []

    # ---------------------------------------------------------------- solids (closed, wound outward)

    def _object(self, name, vertices, faces, mat=None, smooth=False):
        bpy = self._bpy
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([tuple(float(c) for c in v) for v in vertices], [], [tuple(f) for f in faces])
        geometry.outward(mesh)
        if mat is not None:
            mesh.materials.append(self._material(mat))
        if smooth:
            for polygon in mesh.polygons:
                polygon.use_smooth = abs(polygon.normal.z) < 0.99
        obj = bpy.data.objects.new(name, mesh)
        self._scene.collection.objects.link(obj)
        return obj

    def box(self, name, lo, hi, mat=None):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        vertices = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        return self._object(name, vertices, faces, mat)

    def cylinder(self, name, xy, radius, z0, z1, top_radius=None, segments=48, mat=None):
        """A closed cylinder, frustum or (``top_radius`` 0) cone standing on ``z0``."""
        top = radius if top_radius is None else top_radius
        n = max(3, int(segments))

        def ring(r, z):
            return [(xy[0] + r * math.cos(2 * math.pi * i / n), xy[1] + r * math.sin(2 * math.pi * i / n), z)
                    for i in range(n)]
        vertices, faces = ring(radius, z0), [tuple(range(n))]
        if top > 0:
            vertices += ring(top, z1)
            faces += [tuple(range(n, 2 * n))] + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        else:
            vertices.append((xy[0], xy[1], z1))
            faces += [(i, (i + 1) % n, n) for i in range(n)]
        return self._object(name, vertices, faces, mat, smooth=True)

    def prism(self, name, points, axis, start, end, mat=None):
        """A simple polygon (any winding) in the plane across ``axis`` ('X': (y, z), 'Y': (x, z), 'Z': (x, y)),
        extruded from ``start`` to ``end`` along it."""
        lift = {"X": lambda u, v, w: (w, u, v), "Y": lambda u, v, w: (u, w, v), "Z": lambda u, v, w: (u, v, w)}[
            _axis(axis, "XYZ")]
        n = len(points)
        vertices = [lift(u, v, start) for u, v in points] + [lift(u, v, end) for u, v in points]
        faces = [tuple(range(n)), tuple(range(n, 2 * n))] + [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        return self._object(name, vertices, faces, mat)

    def row(self, name, start, end, axis, at, z0, unit, gap, mat=None):
        """Evenly spaced boxes (posts, slats, columns) strictly inside [start, end] along 'X' or 'Y', centred on
        ``at`` across it and standing on ``z0``; ``unit`` is (length along the run, depth, height) and ``gap`` the
        space between units. One object. ``unit`` and ``gap`` have no defaults: any default would be one request's
        sizes, silently given to every script that left them out."""
        axis = _axis(axis, "XY")
        length, depth, height = (float(v) for v in unit)
        along = 0 if axis == "X" else 1   # A point given for an end: its coordinate along the run.
        start, end = sorted(float(v[along]) if _sequence(v) else float(v) for v in (start, end))
        if _sequence(at):
            at = at[1 - along]
        span = end - start
        if span + EPSILON < length:
            raise ValueError("kit.row: the span %.3f is shorter than one unit (%.3f)" % (span, length))
        count = max(1, int((span + gap + EPSILON) // (length + gap)))
        if count > MAX_UNITS:
            raise ValueError("kit.row: %d units is more than %d" % (count, MAX_UNITS))
        first = start + (span - (count * length + (count - 1) * gap)) / 2 + length / 2
        vertices, faces = [], []
        for index in range(count):
            middle = first + index * (length + gap)
            if axis == "X":
                lo, hi = (middle - length / 2, at - depth / 2, z0), (middle + length / 2, at + depth / 2, z0 + height)
            else:
                lo, hi = (at - depth / 2, middle - length / 2, z0), (at + depth / 2, middle + length / 2, z0 + height)
            base = len(vertices)
            vertices += [(x, y, z) for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
            faces += [tuple(base + i for i in face) for face in
                      ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))]
        return self._object(name, vertices, faces, mat)

    # ---------------------------------------------------------------- openings

    def cut(self, target, cutter):
        """Subtract ``cutter`` from ``target``'s mesh (modifiers that keep the shape stay and run once); raise,
        changing nothing, when the cut cannot be proven (script_kit_geometry.cut_mesh). The cutter is hidden and
        deleted when the script ends (``finish``), so one cutter can cut several targets."""
        bpy = self._bpy
        target, cutter = self._object_named(target), self._object_named(cutter)
        depsgraph = bpy.context.evaluated_depsgraph_get()   # A move set just before the cut reaches matrix_world.
        for obj in (target, cutter):   # One Blender does not evaluate keeps its old position: never cut there.
            if self._scene not in obj.users_scene or obj.evaluated_get(depsgraph).as_pointer() == obj.as_pointer():
                raise ValueError("kit.cut: %r is not evaluated in this scene (disabled in viewports, in an excluded or "
                                 "only instanced collection, or another scene): enable it first" % obj.name)
        from .material_sharing import kept  # Data nothing keeps (a replaced material, say) uses no cutter.
        users = [user.name for user in kept(bpy.data.user_map(subset=[cutter]).get(cutter, ()))
                 if user != cutter and not isinstance(user, (bpy.types.Collection, bpy.types.Scene))
                 and not (isinstance(user, bpy.types.Object) and user.parent == cutter)]
        if users:
            raise ValueError("kit.cut: the cutter %r is also used by %r: give kit a cutter of its own"
                             % (cutter.name, users[0]))
        mesh = geometry.cut_mesh(bpy, self._scene, target, cutter)
        old, name = target.data, target.data.name
        target.data = mesh
        if target.data != mesh:   # Never delete the cutter of a cut that did not land.
            bpy.data.meshes.remove(mesh)
            raise ValueError("kit.cut: %r kept its mesh; cut in Object Mode" % target.name)
        if old.users == 0:
            bpy.data.meshes.remove(old)
        mesh.name = name
        for child in list(cutter.children):   # Children keep where they are.
            world = child.matrix_world.copy()
            child.parent = None
            child.matrix_world = world
        _hide(cutter)   # Out of rays, bounds and renders until it goes, still evaluated so it can move and cut again.
        if all(spent is not cutter for spent in self._spent):
            self._spent.append(cutter)
        return target

    def finish(self):
        """At the end of a script (the executor calls it whether the script ran or raised): delete the cutters its
        cuts used and left as they were, hidden and used by nothing else. One the script removed is skipped; one it
        took back (shown again, rendered, or used by another object) stays and is named in ``kept``. Deleting a
        cutter at its first cut left the script a removed object for its next."""
        bpy = self._bpy
        for cutter in self._spent:
            try:
                if not _spent_still(bpy, cutter):
                    if not cutter.hide_render:   # Taken back as a part: shown in the viewport as well.
                        _hide(cutter, False)
                    self.kept.append(cutter.name)
                    continue
                mesh = cutter.data
                for child in list(cutter.children):   # Parented to it after its cut: children keep their place.
                    world = child.matrix_world.copy()
                    child.parent = None
                    child.matrix_world = world
                bpy.data.objects.remove(cutter, do_unlink=True)
            except ReferenceError:
                continue
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        self._spent = []

    def arch(self, target, center, width, spring, through, axis="Y", base=None, segments=24):
        """Cut a round-arched opening ``width`` wide through ``target`` along ``axis`` ('X' or 'Y') between the two
        values of ``through``, centred on ``center`` across it; straight sides up to ``spring``, crown at
        spring + width / 2. Checks the crown, measured from below."""
        axis = _axis(axis, "XY")
        target = self._object_named(target)
        if _sequence(center):   # A point: its coordinate across the opening.
            center = center[0 if axis == "Y" else 1]
        center, width, spring, segments = float(center), float(width), float(spring), int(segments)
        if segments < 2:
            raise ValueError("kit.arch: segments must be 2 or more")
        lo, _hi = self.bounds(target)
        floor = lo[2] - 1.0 if base is None else float(base)
        r = width / 2.0
        points = [(center - r, floor), (center + r, floor)] + [
            (center + r * math.cos(math.pi * i / segments), spring + r * math.sin(math.pi * i / segments))
            for i in range(segments + 1)]
        cutter = self.prism(target.name + " arch cutter", points, axis, through[0], through[1])
        try:
            self.cut(target, cutter)
        except Exception:
            mesh = cutter.data
            self._bpy.data.objects.remove(cutter, do_unlink=True)
            self._bpy.data.meshes.remove(mesh)
            raise
        middle = (float(through[0]) + float(through[1])) / 2.0
        bottom = max(floor, lo[2])
        z = bottom + min(0.05, 0.25 * max(spring - bottom, 0.0))
        origin = (center, middle, z) if axis == "Y" else (middle, center, z)
        hit = self._ray_object(target, origin, (0.0, 0.0, 1.0))
        # A ray up the middle meets the crown exactly with an even number of segments, else the chord below it.
        sag = r * (1.0 - math.cos(math.pi / (2 * segments))) if segments % 2 else 0.0
        self.check("%s arch crown at %s %.3f" % (target.name, "X" if axis == "Y" else "Y", center),
                   None if hit is None else hit[2], spring + r, sag + max(0.002, 0.01 * r))
        return target

    def parent(self, child, parent):
        """Parent ``child`` (an object or a name, or a list of them) to ``parent`` keeping where each is in the world
        (``parent`` None: unparent in place): the scene is evaluated first, so a move set just before counts
        (script_kit_parent). Returns the child (or the list). Raises, changing nothing, for an object Blender does
        not evaluate here, a parenting loop or a parent without an inverse."""
        many = not isinstance(child, str) and (_sequence(child) or hasattr(child, "__next__"))
        children = [self._object_named(item) for item in (list(child) if many else [child])]
        target = None if parent is None else self._object_named(parent)
        parents.parent_keep_world(self._bpy, self._scene, children, target)
        return children if many else children[0]

    # ---------------------------------------------------------------- measurements and self-checks

    def check(self, name, measured, target, tolerance=0.05, reason=None):
        """Record a measured value (a number or a list) beside its target; it fails beyond tolerance, or when a value
        is missing or not finite, there is no target at all, or the tolerance is not a finite number of 0 or more."""
        values, goals, tol = _as_list(measured), _as_list(target), _number(tolerance)
        problem = reason
        if all(g is None for g in goals):
            problem = problem or "no target"
        elif len(values) != len(goals):
            problem = problem or "%d values for %d targets" % (len(values), len(goals))
        elif any(v is None for v, g in zip(values, goals) if g is not None):
            problem = problem or "not measured, or not a finite number"
        if tol is None or tol < 0:
            problem, tol = problem or "the tolerance is not a finite number of 0 or more", None
        passed = problem is None and all(abs(v - g) <= tol for v, g in zip(values, goals) if g is not None)
        return self._record(name, {"measured": _rounded(measured), "target": _rounded(target), "tolerance": tol,
                                   "passed": passed}, problem)

    def bounds(self, *items):
        """World bounds ((min x, y, z), (max x, y, z)) of visible objects with geometry, or of every such object
        whose name starts with a given text, as evaluated (modifiers included) from their world-space vertices."""
        depsgraph = self._bpy.context.evaluated_depsgraph_get()
        found = []
        for obj in self._objects(items):
            if obj.type in NOT_GEOMETRY:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            if obj.type in geometry.GEOMETRY:
                mesh = evaluated.to_mesh()
                try:
                    box = geometry.world_bounds(mesh, evaluated.matrix_world) if mesh is not None else None
                finally:
                    evaluated.to_mesh_clear()
            else:   # Volumes, point clouds and the like: their bound box.
                corners = [evaluated.matrix_world @ _vector(corner) for corner in evaluated.bound_box]
                box = (tuple(min(p[a] for p in corners) for a in range(3)),
                       tuple(max(p[a] for p in corners) for a in range(3)))
            if box is not None:
                found.append(box)
        if not found:
            raise ValueError("kit.bounds: no object with geometry matches %r" % (items,))
        return (tuple(min(b[0][a] for b in found) for a in range(3)),
                tuple(max(b[1][a] for b in found) for a in range(3)))

    def check_bounds(self, name, items, lo=None, hi=None, tolerance=0.05):
        """Check world bounds against targets; None skips a value (lo/hi are 3 values or None)."""
        try:
            mlo, mhi = self.bounds(*(items if isinstance(items, (list, tuple)) else [items]))
        except ValueError as error:
            return self._record(name, {"measured": None, "target": [_rounded(lo), _rounded(hi)],
                                       "tolerance": _number(tolerance), "passed": False}, str(error))
        measured, target = [], []
        for got, want in ((mlo, lo), (mhi, hi)):
            if want is not None:
                for a in range(3):
                    if want[a] is not None:
                        measured.append(got[a])
                        target.append(want[a])
        return self.check(name, measured, target or [None], tolerance)

    def check_ray(self, name, origin, direction, distance, tolerance=0.05, prefix=None):
        """Cast a ray; check the distance to its first hit (on an object starting with ``prefix`` when given), or
        with ``distance`` None that it hits nothing (an open passage)."""
        entry = {"measured": None, "target": "no hit" if distance is None else _rounded(distance),
                 "tolerance": _number(tolerance), "passed": False}
        start, step = _as_list(origin), _as_list(direction)
        if len(start) != 3 or len(step) != 3 or None in start + step or sum(v * v for v in step) < EPSILON ** 2:
            return self._record(name, entry, "the ray needs a finite origin and direction")
        if prefix is not None and not self._objects([prefix]):
            return self._record(name, entry, "no visible object starts with %r" % prefix)
        hit = self._ray_scene(start, step, prefix)
        if hit == "unmeasured":
            return self._record(name, dict(entry, passed=None), "over 64 other surfaces on the ray: not measured")
        if distance is not None:
            return self.check(name, None if hit is None else hit[0], distance, tolerance)
        return self._record(name, dict(entry, measured=None if hit is None else _rounded(hit[0]), passed=hit is None),
                            None)

    def check_supported(self, name, prefix, ground=0.0, tolerance=0.02, max_vertices=2000000, max_parts=20000,
                        max_objects=250000):
        """Every loose part of the objects starting with ``prefix`` (as evaluated: modifiers, curves and text
        included) is joined to ``ground`` by a chain of contacts within ``tolerance``: through the group's parts
        (their bounds touch) and through any other visible geometry (script_kit_support); measured is the number of
        floating parts, target 0. A failure names the support looked for and, lowest first, the floating parts, each
        with the nearest surface below it, the gap and the lowest point its contacts reach. Parts that all rest on
        the ground or on each other are recorded as before, without reading the rest of the scene."""
        depsgraph = self._bpy.context.evaluated_depsgraph_get()
        parts, names, counted = [], [], 0
        ground, tolerance = _number(ground), _number(tolerance)
        if ground is None or tolerance is None or tolerance < 0:
            return self._record(name, {"measured": None, "target": 0, "tolerance": 0, "passed": False},
                                "ground and tolerance must be finite numbers, the tolerance 0 or more")
        objects = [o for o in self._objects([prefix]) if o.type in geometry.GEOMETRY]
        if not objects:
            return self._record(name, {"measured": None, "target": 0, "tolerance": 0, "passed": False},
                                "no object with geometry starts with %r" % prefix)
        for obj in objects:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                counted += len(mesh.vertices)
                if counted > max_vertices:
                    return self._record(name, {"measured": None, "target": 0, "tolerance": 0, "passed": None},
                                        "over %d vertices: not measured" % max_vertices)
                found = geometry.loose_parts(mesh, evaluated.matrix_world)
            finally:
                evaluated.to_mesh_clear()
            parts += found
            names += [obj.name] * len(found)
            if len(parts) > max_parts:
                return self._record(name, {"measured": None, "target": 0, "tolerance": 0, "passed": None},
                                    "over %d parts: not measured" % max_parts)
        near = geometry.touching_pairs(parts, tolerance)
        grounded = [i for i, part in enumerate(parts) if part[0][2] <= ground + tolerance]
        supported, lowest = support.reached(near, grounded, len(parts)), {}
        if not all(supported):   # Through the rest of the scene: script_kit_support.
            labels = support.components(near, [i for i, s in enumerate(supported) if not s])
            floating = {}
            for i, label in labels.items():
                floating.setdefault(label, []).append(parts[i])
            try:
                lowest = support.scene_support(depsgraph, prefix, floating, [p for p, s in zip(parts, supported) if s],
                                               ground, tolerance, max_vertices - counted, max_parts - len(parts),
                                               max_objects)
            except support.OverLimit as error:
                return self._record(name, {"measured": None, "target": 0, "tolerance": 0, "passed": None}, str(error))
            lowest = {i: lowest[label] for i, label in labels.items()}
            supported = [s or lowest[i] is None for i, s in enumerate(supported)]
        floating = sorted((i for i, s in enumerate(supported) if not s), key=lambda i: parts[i][0][2])
        entry = {"measured": len(floating), "target": 0, "tolerance": 0, "passed": not floating}
        if floating:   # What was looked for and, lowest first, which parts float over what: no diagnosis round.
            entry["support"] = ("a chain of contacts down to ground=%s through the group's parts or any other visible "
                                "surface, each within tolerance=%s" % (_rounded(ground), _rounded(tolerance)))
            entry["floating"] = [self._floating(depsgraph, prefix, parts, names, i, ground, lowest[i])
                                 for i in floating[:8]]
        return self._record(name, entry, None)

    def _floating(self, depsgraph, prefix, parts, names, index, ground, lowest):
        """A floating part's row: its object, the nearest surface below it (another object, a part of the group, or
        the ground plane when that is nearer) and the gap down to it, the lowest point its contacts reach, and its
        bounds."""
        lo, hi = parts[index]

        def own(name, location):   # The part's own surface, which every ray below it starts on.
            return name == names[index] and all(lo[a] - EPSILON <= location[a] <= hi[a] + EPSILON for a in range(3))
        below = support.nearest_below(self._scene, depsgraph, parts[index], own, lambda n: n.startswith(prefix))
        if below is None or below[1] < ground:
            below = ("ground z=%s" % _rounded(ground), ground)
        return {"object": names[index][:64], "below": below[0][:64], "gap": _rounded(lo[2] - below[1]),
                "reaches_down_to": _rounded(lowest), "bounds": [_rounded(list(lo)), _rounded(list(hi))]}

    def outcome(self):
        """What the executor returns (module docstring): ``self_checks`` for the model and the host, and for the host
        only ``self_checks_passed`` and ``self_checks_failed``, the names past the list. Failing checks past even
        those are held open under OVERFLOW, which no script records passing."""
        checks = list(self.checks)
        failed = list(self.failed_names) + ([OVERFLOW] if self.overflow else [])
        if failed:
            checks.append({"name": "kit: failing checks not listed", "measured": len(self.failed_names) + self.overflow,
                           "target": 0, "tolerance": 0, "passed": None,
                           "reason": "fix the listed checks and record every check again"})
        out = dict({"self_checks": checks} if checks else {}, **({"kit_kept_cutters": self.kept} if self.kept else {}))
        out.update({key: names for key, names in (("self_checks_passed", self.passed_names),
                                                  ("self_checks_failed", failed)) if names})
        return out

    def _record(self, name, entry, reason):
        """Keep a check under a name unique within the script (a repeat gets " (2)"). Listed with their values: the
        first MAX_PASSING passing and MAX_FAILING failing checks; then failing ones by name up to MAX_LISTED; every
        other one by name for the host (``outcome``)."""
        if reason is not None:
            entry = dict(entry, passed=False if entry.get("passed") is not None else None, reason=str(reason)[:200])
        label, passed = self._label(name), entry["passed"]
        if passed and (self._passing >= MAX_PASSING or len(self.checks) >= MAX_LISTED):
            if len(self.passed_names) < MAX_HOST_NAMES:
                self.passed_names.append(label)
            else:
                self.unlisted_passes += 1   # Conservative: a check this pass would close stays open.
            return True
        if len(self.checks) >= MAX_LISTED:
            if passed is False:
                if len(self.failed_names) < MAX_HOST_NAMES:
                    self.failed_names.append(label)
                else:
                    self.overflow += 1
            return passed
        if passed:
            self._passing += 1
        elif passed is False:
            self._failing += 1
            if self._failing > MAX_FAILING:
                entry = {key: entry[key] for key in ("passed", "reason") if key in entry}
        self.checks.append(dict({"name": label}, **{key: _capped(value) for key, value in entry.items()}))
        return passed

    def _label(self, name):
        base = str(name)[:110]   # Room for a repeat's suffix within the gate's 120 characters.
        label, count = base, self._repeats.get(base, 1)
        while label in self._labels:
            count += 1
            label = "%s (%d)" % (base, count)
        self._repeats[base] = count
        self._labels.add(label)
        return label

    def _object_named(self, item):
        """An object, or the scene object of that exact name. A removed object is refused here: Blender's own calls
        (``user_map``) crash on one."""
        if isinstance(item, str):
            obj = self._scene.objects.get(item)
            if obj is None:
                raise ValueError("kit: no object named %r in the scene" % item)
            return obj
        return _live(item, self._bpy.types.Object, "object")

    def _material(self, item):
        if isinstance(item, str):
            material = self._bpy.data.materials.get(item)
            if material is None:
                raise ValueError("kit: no material named %r" % item)
            return material
        return _live(item, self._bpy.types.Material, "material")

    def _objects(self, items):
        found = []
        for item in items:
            if isinstance(item, str):   # A prefix: the visible objects it starts (a hidden cutter is not built).
                found += [o for o in self._scene.objects if o.name.startswith(item) and _visible(o)]
            elif item is not None:
                found.append(_live(item, self._bpy.types.Object, "object"))
        return found

    def _ray_scene(self, origin, direction, prefix):
        depsgraph = self._bpy.context.evaluated_depsgraph_get()
        start, step, travelled = _vector(origin), _vector(direction).normalized(), 0.0
        for _ in range(64):
            hit, location, _normal, _index, obj, _matrix = self._scene.ray_cast(depsgraph, start, step)
            if not hit:
                return None
            travelled += (location - start).length
            if prefix is None or obj.original.name.startswith(prefix):
                return travelled, tuple(location)
            start = location + step * EPSILON
            travelled += EPSILON
        return "unmeasured"

    def _ray_object(self, obj, origin, direction):
        depsgraph = self._bpy.context.evaluated_depsgraph_get()
        world = obj.evaluated_get(depsgraph).matrix_world
        inverse = world.inverted_safe()
        local_direction = inverse.to_3x3() @ _vector(direction)
        hit, location, _normal, _index = obj.ray_cast(inverse @ _vector(origin), local_direction.normalized(),
                                                      depsgraph=depsgraph)
        return tuple(world @ location) if hit else None
