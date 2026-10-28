# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a reviewed script's Boolean modifiers did to their objects: measured, never judged (``result["booleans"]``).

Recorded (1,106 one-shot trials, mostly Opus 5.5: 34 Boolean mistakes, 8 of them final
misses): a script adds a Boolean modifier and applies it, and the cutter, wound inward, turned the wrong way or
overlapping the block, takes the whole block or nothing. One castle's own result printed the gatehouse's 10 faces
while its gateway stayed closed; another's arch cut "had deleted the whole block". Nothing in the result said what
the Boolean did. Two readings now say it, one row shape:

* **Applied in the script** (``Applied``): while the script runs, and only then, ``bpy.ops`` builds
  ``object.modifier_apply`` as an observed callable. ``bpy.ops`` makes every operator callable through its module
  global ``_op_create_function`` (``scripts/modules/bpy/ops.py``), so replacing that global around the script's
  ``exec`` reaches each call the script makes, inside ``temp_override`` and with an ``EXEC_DEFAULT`` positional too;
  it is put back on exit, also when the script raises or exits. Before a call, the objects Blender applies to are
  resolved as ``modifier_apply_exec`` resolves them (``use_selected_objects``: the selected editable objects, else
  the context's ``object``, else the active object), with each one's Boolean modifiers the call may apply (``modifier``
  by name; without it Blender's exec applies none, and any Boolean that goes is still reported). Applying one
  modifier acts on the base mesh alone, even one that is not first in the stack, so the base mesh is measured before
  and after the call, in the world matrix Blender evaluated for it. ``applied`` is whether the modifier left the stack.
* **Live stacks** (``live``): after a completed script, each mesh object it added or modified, or whose Boolean
  operand (an object, or an object in an operand collection) it added or modified, whose enabled modifiers are all
  Booleans: its base mesh against its evaluated mesh. A stack of several names them in ``stack`` (``MAX_STACK``).
  An object in Edit Mode, with shape keys or with a deforming parent is skipped: its evaluated mesh is not its
  Booleans' alone.

A row names the object, its operand (``with``, or ``with_collection``), the operation and solver, ``applied`` or
``live``, and the face counts before and after; the world volumes (``volume_m3``) where the mesh is closed and wound
one way, else why not (``volume``); the world bounds only when they changed. At the extremes only, a plain ``fact``:
no faces left; the volume unchanged (and the faces, when they are) within a bound stated in m³ (``_unchanged_within``:
``TOLERANCE`` of the smaller of the object's and its operands' closed volume, so a small real cut never reads as
none); the share of the volume kept when it fell below half; a difference or intersection that grew, or a union that
shrank, which no Boolean of closed solids does; a negative volume (faces wound inward). A cutter wound inward under
the EXACT solver left a block's volume unchanged while its faces went from 6 to 10, the recorded castle's count.

An object the view layer does not evaluate (disabled in viewports, or in an excluded or viewport-disabled
collection) has no evaluated mesh and a matrix that may be stale: its live row and its applied row's world measures
say ``not evaluated`` instead (an applied row keeps its face counts, which are its mesh's own), and no fact is drawn
from it. Otherwise a live row on such an object read "volume and faces unchanged" from its unevaluated base
mesh while a render cut half of it.

``measure``: faces; world bounds from the vertices; the signed volume of each face's fan of triangles (the same for
any triangulation of a flat face) about the vertices' mean, times ``|det|`` of the world matrix's 3x3. Its sign is the
winding's: Blender's Boolean flips an operand whose transform is negative where the target's is not
(``mesh_boolean.cc``), so a mirrored object is not inside out. Closed: every edge has exactly two faces, which run it
in opposite directions. Closed parts that overlap count twice. Arrays are read through the mesh's own attributes
(``position``, ``.corner_vert``, ``.corner_edge``, ``.edge_verts``), RNA where one is missing.

Bounds: ``MAX_TRIANGLES`` per measured mesh (faces only past it), ``MAX_ROWS`` rows for both readings together
(``booleans_more`` counts the rest), ``BUDGET_SECONDS`` of this thread's CPU (``work_clock``) for each reading's
measuring (a row past it is counted, not measured), ``MAX_SCANNED`` scene objects searched for a changed operand.
Optional evidence: an error while measuring counts or drops that row and never fails the script; a failed or
rolled-back script gets nothing. The first measure in a process imports NumPy, if nothing did before. Not observed
(limits, doc/angelo/agent_execution.rst): ``object.convert``, meshes copied from an evaluated object
(``new_from_object``, ``to_mesh``; ``kit.cut`` proves its own), Edit Mode's ``mesh.intersect_boolean`` and ``bmesh``
operations, an apply made by another operator's C code.
"""

import math
from itertools import islice

from .object_bounds import rounded
from .scene_read_values import label
from .work_clock import WorkClock

OPERATOR = ("object", "modifier_apply")
MAX_ROWS = 8
MAX_TRIANGLES = 250000
MAX_STACK = 4   # A live stack's Booleans named in its row; ``stack_more`` counts the rest.
BUDGET_SECONDS = 0.1
MAX_SCANNED = 5000   # Scene objects read for an operand the script changed.
TOLERANCE = 1e-4   # Relative: 0.01% (``_unchanged_within``, and a growth or shrink worth stating).
TOLERANCE_FLOOR = 1e-12   # m³: rounding of an identical mesh.
UNCHANGED = "volume and faces unchanged (within %.2g m³)"
UNCHANGED_VOLUME = "volume unchanged (within %.2g m³)"
NEGATIVE = "a negative volume: its faces are wound inward"
NOT_EVALUATED = "not evaluated: disabled in viewports or excluded"
MAX_OPERAND_OBJECTS = 16   # A collection operand's meshes measured for its volume; past it, its size is unknown.
DEFORMING_PARENTS = frozenset({"ARMATURE", "LATTICE"})   # A parent that moves vertices as a modifier would.


def _read(attributes, name, key, count, dtype, fallback):
    import numpy
    values = numpy.empty(count, dtype=dtype)
    attribute = attributes.get(name)
    if attribute is not None:
        attribute.data.foreach_get(key, values)
    else:
        fallback(values)
    return values


def measure(mesh, matrix):
    """``{"faces", "bounds", "volume" | "why"}`` of ``mesh`` placed by ``matrix`` (module docstring)."""
    import numpy
    faces, corners, attributes = len(mesh.polygons), len(mesh.loops), mesh.attributes
    reading = {"faces": faces}
    if corners - 2 * faces > MAX_TRIANGLES:
        reading["why"] = "over %d triangles" % MAX_TRIANGLES
        return reading
    count = len(mesh.vertices)
    points = _read(attributes, "position", "vector", count * 3, numpy.float32,
                   lambda out: mesh.vertices.foreach_get("co", out)).reshape(count, 3).astype(numpy.float64)
    rows = numpy.array([[float(matrix[r][c]) for c in range(4)] for r in range(3)])
    if count:
        world = points @ rows[:, :3].T + rows[:, 3]
        reading["bounds"] = rounded([world.min(axis=0).tolist(), world.max(axis=0).tolist()])
    if not faces:
        reading["why"] = "no faces"
        return reading
    edge_count = len(mesh.edges)
    corner_edge = _read(attributes, ".corner_edge", "value", corners, numpy.int32,
                        lambda out: mesh.loops.foreach_get("edge_index", out))
    uses = numpy.bincount(corner_edge, minlength=edge_count)
    if (uses != 2).any():
        reading["why"] = "open mesh"
        return reading
    corner_vert = _read(attributes, ".corner_vert", "value", corners, numpy.int32,
                        lambda out: mesh.loops.foreach_get("vertex_index", out))
    ends = _read(attributes, ".edge_verts", "value", edge_count * 2, numpy.int32,
                 lambda out: mesh.edges.foreach_get("vertices", out)).reshape(edge_count, 2)
    forward = ends[corner_edge, 0] == corner_vert   # Each edge's two faces run it one each way: wound one way.
    if (numpy.bincount(corner_edge, weights=forward, minlength=edge_count) != 1).any():
        reading["why"] = "faces wound both ways"
        return reading
    starts = numpy.empty(faces, dtype=numpy.int64)
    mesh.polygons.foreach_get("loop_start", starts)
    sizes = numpy.diff(numpy.append(starts, corners))
    first = numpy.repeat(starts, sizes)
    index = numpy.arange(corners)
    inner = (index > first) & (index < numpy.repeat(starts + sizes - 1, sizes))   # A face's fan: (0, i, i + 1).
    centred = points - points.mean(axis=0)
    a, b, c = (centred[corner_vert[i]] for i in (first[inner], index[inner], index[inner] + 1))
    local = float(numpy.einsum("ij,ij->", a, numpy.cross(b, c))) / 6.0
    reading["volume"] = local * abs(float(numpy.linalg.det(rows[:, :3])))
    return reading


def _significant(values, digits=4):
    top = max(abs(value) for value in values)
    places = 3 if top == 0.0 else max(3, digits - 1 - math.floor(math.log10(top)))
    return [round(value, places) + 0.0 for value in values]


def _unchanged_within(before, after, operand):
    """The volume change below which the volume reads as unchanged, or None when no bound is sound.

    ``TOLERANCE`` of the smaller of the object's and its operands' closed volume: a Boolean changes at most its
    operand's volume, so a real cut by a small cutter (a bolt hole through a wall) changes the wall by far less than
    0.01% of the wall, yet by much more than 0.01% of the cutter. Without the operand's volume, only a mesh with the
    same face count is judged, against its own volume: a small cut and none read alike otherwise."""
    if operand is not None:
        return TOLERANCE * min(abs(before["volume"]), operand)
    if before["faces"] == after["faces"]:
        return TOLERANCE * abs(before["volume"])
    return None


def _facts(before, after, operations, operand=None):
    """The plain facts at the extremes (module docstring), in a fixed order; none in between. ``operations``: the
    Boolean operations that made ``after``; ``operand``: their operands' closed volume, or None. A difference or an
    intersection of closed solids never adds volume, and a union never removes any, so a change against that by more
    than ``TOLERANCE`` of the object's volume is stated with its size."""
    vb, va = before.get("volume"), after.get("volume")
    if before["faces"] and not after["faces"]:
        return ["no faces left"]
    found = []
    if vb is not None and va is not None:
        bound, change = _unchanged_within(before, after, operand), va - vb
        if bound is not None and abs(change) <= bound + TOLERANCE_FLOOR:
            found.append((UNCHANGED if before["faces"] == after["faces"] else UNCHANGED_VOLUME) % bound)
        elif 0.0 <= va < 0.5 * vb:
            found.append("kept %.2g%% of its volume" % (100.0 * va / vb))
        elif vb > 0.0 and change > TOLERANCE * vb and operations <= {"DIFFERENCE", "INTERSECT"}:
            found.append("volume grew by %.2g%%" % (100.0 * change / vb))
        elif vb > 0.0 and -change > TOLERANCE * vb and va > 0.0 and operations == {"UNION"}:
            found.append("volume shrank by %.2g%%" % (100.0 * -change / vb))
        if va < 0.0 or vb < 0.0:
            found.append(NEGATIVE)
    elif before["faces"] == after["faces"] and "bounds" in after and before.get("bounds") == after["bounds"]:
        found.append("faces and bounds unchanged")
    return found


def facts(before, after, operations, operand=None):
    """The measured part of a row: faces, volumes or why not, changed bounds, and the facts at the extremes
    (``operand``: the operands' closed volume, ``operand_volume``)."""
    out = {"faces": [before["faces"], after["faces"]]}
    volumes = [before.get("volume"), after.get("volume")]
    if any(value is not None for value in volumes):
        known = [value for value in volumes if value is not None]
        shown = iter(_significant(known))
        out["volume_m3"] = [next(shown) if value is not None else None for value in volumes]
    reasons = [reading.get("why") for reading in (before, after)]
    if reasons[0] and reasons[0] == reasons[1]:
        out["volume"] = reasons[0]
    elif any(reasons):
        out["volume"] = "; ".join("%s: %s" % (side, why) for side, why in zip(("before", "after"), reasons) if why)
    if before.get("bounds") != after.get("bounds"):
        out["bounds"] = [before.get("bounds"), after.get("bounds")]
    found = _facts(before, after, set(operations), operand)
    if found:
        out["fact"] = "; ".join(found)
    return out


def operand(modifier):
    """``{"with": name}``, ``{"with_collection": name}`` or ``{"with": None}``, with the operation and solver."""
    out = {}
    if getattr(modifier, "operand_type", "OBJECT") == "COLLECTION":
        out["with_collection"] = label(modifier.collection.name) if modifier.collection is not None else None
    else:
        out["with"] = label(modifier.object.name) if modifier.object is not None else None
    out.update(operation=str(modifier.operation), solver=str(modifier.solver))
    return out


def evaluated(obj, depsgraph):
    """``obj`` as ``depsgraph`` evaluated it, or None: an object a view layer does not evaluate (disabled in
    viewports, in an excluded or viewport-disabled collection) comes back as itself, its matrix and mesh unevaluated."""
    found = obj.evaluated_get(depsgraph)
    return None if found.as_pointer() == obj.as_pointer() else found


def _operand_meshes(modifier):
    if getattr(modifier, "operand_type", "OBJECT") == "COLLECTION":
        if modifier.collection is None:
            return None
        meshes = [obj for obj in modifier.collection.all_objects if obj.type == "MESH"]
        return meshes if 0 < len(meshes) <= MAX_OPERAND_OBJECTS else None
    return [modifier.object] if modifier.object is not None and modifier.object.type == "MESH" else None


def operand_volume(modifiers, depsgraph):
    """The summed closed volume of these Booleans' operands as Blender evaluated them (m³, unsigned), or None when any
    is missing, open, wound both ways, unevaluated or too large to read."""
    total = 0.0
    for modifier in modifiers:
        meshes = _operand_meshes(modifier)
        if meshes is None:
            return None
        for obj in meshes:
            found = evaluated(obj, depsgraph)
            volume = None if found is None else measure(found.data, found.matrix_world).get("volume")
            if volume is None:
                return None
            total += abs(volume)
    return total


class Rows:
    """One reading's rows, capped (``MAX_ROWS``), and its measuring budget (``BUDGET_SECONDS``)."""

    def __init__(self, clock=None, rows=(), more=0):
        self.rows, self.more = list(rows), int(more)
        self._clock, self._spent = clock or WorkClock(), 0.0

    def room(self):
        return len(self.rows) < MAX_ROWS

    def open(self):
        """Whether another row may be measured: room for it and budget left."""
        return self.room() and self._spent < BUDGET_SECONDS

    def measured(self, function, *args):
        started = self._clock()
        try:
            return function(*args)
        finally:
            self._spent += self._clock() - started

    def add(self, row):
        if self.room():
            self.rows.append(row)
        else:
            self.more += 1

    def outcome(self):
        out = {"booleans": self.rows} if self.rows else {}
        if self.more:
            out["booleans_more"] = self.more
        return out


class _Observed:
    """``object.modifier_apply`` with its Boolean outcome recorded; everything else is the operator's own."""

    __slots__ = ("_operator", "_applied")

    def __init__(self, operator, applied):
        self._operator, self._applied = operator, applied

    def __call__(self, *args, **kwargs):
        pending = self._applied.before(kwargs)
        try:
            return self._operator(*args, **kwargs)
        finally:
            self._applied.after(pending)

    def __getattr__(self, name):
        return getattr(self._operator, name)

    def __repr__(self):
        return repr(self._operator)


class Applied:
    """Observes ``bpy.ops.object.modifier_apply`` while a script runs (module docstring); a context manager."""

    def __init__(self, bpy, rows=None):
        self._bpy, self.rows, self._real = bpy, rows or Rows(), None
        self._active = False   # A callable the script kept past its end is the operator alone.

    def __enter__(self):
        ops = getattr(self._bpy, "ops", None)
        real = getattr(ops, "_op_create_function", None)
        if real is None:   # A stand-in bpy: nothing to observe.
            return self

        def create(module, function):
            made = real(module, function)
            return _Observed(made, self) if (module, function) == OPERATOR else made
        try:
            ops._op_create_function = create
        except Exception:  # noqa: BLE001  Optional evidence: the script runs as written and nothing is stated.
            return self
        self._real, self._active = real, True
        return self

    def __exit__(self, *_exception):
        self._active = False
        if self._real is not None:
            self._bpy.ops._op_create_function, self._real = self._real, None
        return False

    def _targets(self, kwargs):
        context = self._bpy.context
        if kwargs.get("use_selected_objects"):
            return list(getattr(context, "selected_editable_objects", None) or ())
        obj = getattr(context, "object", None) or getattr(context, "active_object", None)
        return [] if obj is None else [obj]

    def before(self, kwargs):
        """Each target with its Boolean candidates and, while rows are open, its base mesh measured. Never raises."""
        pending, depsgraph = [], None
        if not self._active:
            return pending
        try:
            name, targets = kwargs.get("modifier") or None, self._targets(kwargs)
        except Exception:  # noqa: BLE001  Optional evidence: the operator runs as the script wrote it.
            return pending
        for obj in targets:
            try:
                if obj.type != "MESH":
                    continue
                modifiers = [md for md in obj.modifiers if md.type == "BOOLEAN" and (name is None or md.name == name)]
                if not modifiers:
                    continue
                entry = [obj, label(obj.name), [[md.name, operand(md), None] for md in modifiers], None, None]
            except Exception:  # noqa: BLE001  Optional evidence: this object goes unobserved.
                continue
            pending.append(entry)
            if self.rows.open():
                try:
                    depsgraph = depsgraph or self._bpy.context.evaluated_depsgraph_get()   # As the operator does.
                    entry[3] = self.rows.measured(self._reading, obj, depsgraph, entry, modifiers)
                except Exception:  # noqa: BLE001  Unmeasured: an apply is then counted, not listed.
                    entry[3] = None
        return pending

    @staticmethod
    def _reading(obj, depsgraph, entry, modifiers):
        """The base mesh before the call, in the world matrix Blender evaluated, and each candidate's operand volume;
        faces only for an object the view layer does not evaluate: its stored matrix may be stale (and the operator
        cut in it), so no world measure is claimed."""
        found = evaluated(obj, depsgraph)
        if found is None:
            return {"faces": len(obj.data.polygons), "why": NOT_EVALUATED}
        entry[4] = found.matrix_world.copy()
        for candidate, modifier in zip(entry[2], modifiers):
            candidate[2] = operand_volume([modifier], depsgraph)
        return measure(obj.data, entry[4])

    def after(self, pending):
        """A row for the Boolean that went (``applied``) or, when none did, per candidate (not applied); an apply
        that cannot be measured is counted (``booleans_more``). Never raises. A row measured before the call is
        finished past the budget: its mesh is within MAX_TRIANGLES."""
        for obj, name, candidates, reading, matrix in pending:
            try:
                present = {md.name for md in obj.modifiers}
                went = [(identity, volume) for modifier, identity, volume in candidates if modifier not in present]
            except Exception:  # noqa: BLE001  Optional evidence: the object went with the call.
                continue
            if not went:
                for _modifier, identity, _volume in candidates:
                    self.rows.add(dict({"object": name}, **identity, applied=False))
                continue
            identity, volume = went[0]
            try:
                if reading is None or not self.rows.room():
                    raise LookupError
                after = ({"faces": len(obj.data.polygons), "why": NOT_EVALUATED} if matrix is None
                         else self.rows.measured(measure, obj.data, matrix))
                measured = facts(reading, after, [identity["operation"]], volume)
            except Exception:  # noqa: BLE001  Optional evidence: counted, not listed; the script goes on.
                self.rows.more += 1
                continue
            self.rows.add(dict({"object": name}, **identity, applied=True, **measured))

    def outcome(self):
        """``booleans`` and ``booleans_more`` for a completed script's result."""
        return self.rows.outcome()


def _live_stack(obj):
    """The enabled Boolean modifiers of a mesh object whose enabled modifiers are all Booleans, else None."""
    if obj.type != "MESH" or obj.mode != "OBJECT" or obj.data.shape_keys is not None:
        return None
    if obj.parent is not None and obj.parent_type in DEFORMING_PARENTS:
        return None
    enabled = [md for md in obj.modifiers if md.show_viewport]
    if not enabled or any(md.type != "BOOLEAN" for md in enabled):
        return None
    return enabled


def _uses_changed(modifier, changed):
    if getattr(modifier, "operand_type", "OBJECT") == "COLLECTION":
        return modifier.collection is not None and any(
            obj.name in changed for obj in modifier.collection.all_objects)
    return modifier.object is not None and modifier.object.name in changed


def _live_objects(scene, names, named=None):
    """The objects ``names`` lists (looked up in ``named``, a name index of the scene, when given), then (among the
    first MAX_SCANNED of the scene) those whose enabled Boolean's operand is one of them or holds one."""
    changed, get = set(names), scene.objects.get if named is None else named.get
    found = [obj for obj in (get(name) for name in dict.fromkeys(names)) if obj is not None]
    for obj in islice(scene.objects, MAX_SCANNED):
        modifiers = obj.modifiers   # Most objects have none: one read each.
        if modifiers and obj.name not in changed and any(
                md.type == "BOOLEAN" and md.show_viewport and _uses_changed(md, changed) for md in modifiers):
            found.append(obj)
    return found


def live(window, result, names, clock=None, named=None):
    """Add a ``live`` row per Boolean stack of the objects ``names`` lists, or whose operand it lists, to a completed
    script's ``result``, after its applied rows and within the same caps (module docstring). Never raises.
    ``named``: the scene's objects by name (``script_after.named``), when the caller built it."""
    if not names:
        return result
    rows = Rows(clock, result.get("booleans") or (), result.get("booleans_more") or 0)
    try:
        objects, depsgraph = _live_objects(window.scene, names, named), window.view_layer.depsgraph
    except Exception:  # noqa: BLE001  Optional evidence: the script's own result stands without it.
        objects = []
    for obj in objects:
        try:
            stack = _live_stack(obj)
            if stack is None:
                continue
            if not rows.open():
                rows.more += 1
                continue
            identities = [operand(md) for md in stack]
            row = dict({"object": label(obj.name)}, **identities[0]) if len(identities) == 1 else {
                "object": label(obj.name), "stack": identities[:MAX_STACK]}
            if len(identities) > MAX_STACK:
                row["stack_more"] = len(identities) - MAX_STACK
            found = evaluated(obj, depsgraph)
            if found is None:   # Its evaluated mesh is not there to read: no measure, and no fact, is claimed.
                rows.add(dict(row, live=True, volume=NOT_EVALUATED))
                continue
            matrix = found.matrix_world
            row.update(live=True, **facts(rows.measured(measure, obj.data, matrix),
                                          rows.measured(measure, found.data, matrix),
                                          [identity["operation"] for identity in identities],
                                          rows.measured(operand_volume, stack, depsgraph)))
            rows.add(row)
        except Exception:  # noqa: BLE001  Optional evidence: this row is dropped.
            continue
    result.pop("booleans", None)
    result.pop("booleans_more", None)
    result.update(rows.outcome())
    return result
