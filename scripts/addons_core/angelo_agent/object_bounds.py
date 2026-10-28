# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An object's world-space bounds as a result states them: ``[[min x, y, z], [max x, y, z]]`` to 1 mm (finer for
a small object).

Recorded (GPT 6.1 Sol): a script's result reported the vertex count of
the ground it added but not its size, and a 200 m ground stood under a brief of 16 m until the grader measured it.
A reviewed script's ``after`` rows (``script_after``) and a program's geometry rows (``program_model_result``)
state these bounds for every object with geometry they add or change: the axis-aligned box of the object's
evaluated bounding box in world space (``Object.bound_box`` through ``matrix_world``, as program deltas read it).
Duck-typed reads; no Blender import.

An object's own box leaves its instances out: a Geometry Nodes instancer read 0, a curve leaving only instances its
control points padded by their radius, a collection instance nothing (bounds audit, 2026-10-25). Every reader that
states an object's size states ``instances`` beside it for an object that makes them (``instance_fact``): the world box
of what they show, read in one native pass (``_bpy._angelo_live.instance_bounds``) the way the live part's size reads
each object's own geometry, and ``own_geometry: false`` when the object shows none itself. (A count was dropped: an
object's own components drawn as duplis made it wrong in ways bounds never are, REVIEW-INSTANCES-2.) Each instance
counts as its own box turned by its matrix: an upper bound, up to about 1.4 times a round instance's own width over
when it is turned 45 degrees (exact would read every vertex of every instance on each pass).
"""

import math

GEOMETRY_TYPES = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "CURVES", "POINTCLOUD", "VOLUME",
                            "GREASEPENCIL"})
# The base data's elements: with none, and an all-zero box, the object has no geometry (``world_bounds``).
ELEMENTS = {"MESH": "vertices", "CURVE": "splines", "SURFACE": "splines", "CURVES": "curves", "POINTCLOUD": "points",
            "VOLUME": "grids"}
DIGITS = 3


def rounded(bounds, digits=DIGITS):
    """``bounds`` (two 3-vectors) rounded, or None when any value is not a finite number.

    To ``digits`` decimals (1 mm), or more for a small object, so its largest extent keeps three significant
    digits: a 0.4 mm part of a millimetre-scale model would otherwise read as a point.
    """
    try:
        values = [[float(value) for value in corner] for corner in bounds]
    except (TypeError, ValueError):
        return None
    if len(values) != 2 or any(len(corner) != 3 for corner in values):
        return None
    if not all(math.isfinite(value) for corner in values for value in corner):
        return None
    extent = max(high - low for low, high in zip(*values))
    if extent > 0.0:
        digits = max(digits, 2 - math.floor(math.log10(extent)))
    return [[round(value, digits) + 0.0 for value in corner] for corner in values]  # -0.0 reads as 0.0.


def world_bounds(obj, digits=DIGITS, depsgraph=None):
    """The world-space bounds of ``obj``'s evaluated bounding box, rounded, or None (no geometry, unreadable).

    With ``depsgraph``, a Geometry Nodes object whose output holds only instances has no geometry of its own, while
    the original's data still holds the base mesh (otherwise a 0.1 m base cube widened a box of
    instances that never render it): its evaluated box, which leaves instances out, is Blender's all-zero fallback.
    Only such an object is asked, and never through the evaluated data, whose read evaluates a deferred mesh in
    full (a 2-level Subdivision Surface 0.26 s an object). The box itself is the
    original's, which Blender keeps as the evaluated geometry's: an evaluated curve's own box adds each point's
    radius, 1 m on every side by default."""
    if getattr(obj, "type", None) not in GEOMETRY_TYPES or not own_box_known(obj, depsgraph):
        return None
    try:
        evaluated = (obj.evaluated_get(depsgraph) if depsgraph is not None
                     and any(modifier.type == "NODES" for modifier in obj.modifiers) else None)
        if evaluated is not None:
            if not any(float(value) for point in evaluated.bound_box for value in point):
                return None
        # An empty base whose box is Blender's all-zero fallback has no geometry; a box a modifier (Geometry Nodes on
        # an empty mesh, the usual procedural pattern) evaluated is its geometry's.
        elif (obj.type in ELEMENTS and not len(getattr(obj.data, ELEMENTS[obj.type]))
                and not any(float(value) for point in obj.bound_box for value in point)):
            return None
        matrix = obj.matrix_world
        rows = [[float(matrix[row][column]) for column in range(4)] for row in range(3)]
        corners = [[sum(rows[axis][index] * corner[index] for index in range(3)) + rows[axis][3] for axis in range(3)]
                   for corner in ([float(value) for value in point] for point in obj.bound_box)]
    except (AttributeError, TypeError, ValueError, IndexError, ReferenceError):
        return None
    if len(corners) != 8:
        return None
    return rounded([[min(point[axis] for point in corners) for axis in range(3)],
                    [max(point[axis] for point in corners) for axis in range(3)]], digits)


CURVE_TYPES = frozenset({"CURVE", "SURFACE", "FONT"})


def own_box_known(obj, depsgraph):
    """False for a curve, surface or text that ``depsgraph`` does not evaluate (hidden, never updated): with no
    evaluated cache, Blender's box is its data's, every point padded by its radius (1 m by default, a bevel scale)."""
    if depsgraph is None or getattr(obj, "type", None) not in CURVE_TYPES:
        return True
    try:
        return bool(obj.evaluated_get(depsgraph).is_evaluated)
    except (AttributeError, TypeError, RuntimeError, ReferenceError):
        return False


def makes_instances(obj):
    """Whether ``obj`` can make instances (Geometry Nodes, an instancing type, particles): a cheap settings test."""
    try:
        return (getattr(obj, "instance_type", "NONE") != "NONE"
                or any(modifier.type in {"NODES", "PARTICLE_SYSTEM"} for modifier in obj.modifiers))
    except (AttributeError, TypeError, ReferenceError):
        return False


def instance_facts(depsgraph, objects, digits=DIGITS, limit=None):
    """``{session_uid: fact}`` for each of ``objects`` whose instances show geometry in ``depsgraph`` (see
    ``InstanceFacts.of``), each expanded alone: the cost is what those objects make, never the scene's instances. None
    when the depsgraph was not evaluated since its change; False when it cannot be read here (no native reader, not
    on the main thread). Bounds rounded as ``rounded`` rounds them, or exact with ``digits=None``; ``limit``
    instances an object may make (``LIMIT``; 0: none)."""
    try:
        import _bpy
        found = _bpy._angelo_live.instance_bounds(depsgraph, list(objects), LIMIT if limit is None else limit)
    except (ImportError, AttributeError, TypeError, ReferenceError, RuntimeError):
        return False
    if found is None:
        return None
    facts = {}
    for uid, value in found.items():
        if value[0] is None:   # Past the limit or hair: unknown, but whether it shows geometry itself is known.
            facts[uid] = dict(HAIR if value[2:] == ("hair",) else TOO_MANY, own_geometry=bool(value[1]))
            continue
        low, high, owns = value
        fact = {"bounds": rounded([low, high], digits) if digits is not None else [list(low), list(high)]}
        if not owns:
            fact["own_geometry"] = False
        facts[uid] = fact
    return facts


def states_own_box(fact):
    """Whether a reader states an object's own size beside its instance ``fact``: not when it shows only instances,
    and not when they could not be read unless the pass knew it shows geometry of its own (its own box might
    otherwise be only its data's)."""
    if fact is None:
        return True
    if "unknown" in fact:
        return fact.get("own_geometry") is True
    return fact.get("own_geometry", True)


# Instances one object may make in a read: past it its fact is unknown (its own size kept when its geometry was seen).
# Each object asked is expanded alone; see agent_execution.rst for the measured cost.
LIMIT = 1000000
NOT_EVALUATED = {"unknown": "the scene was not evaluated since its last change"}
UNAVAILABLE = {"unknown": "instances cannot be read here (no reader in this build, or not on Blender's main thread)"}
TOO_MANY = {"unknown": "more than %d instances; kit.bounds in a script reads them all" % LIMIT}
HAIR = {"unknown": "an instance draws hair, whose strands are not read"}
HIDDEN = {"unknown": "its instances are not read while it is hidden"}


# Facts kept across reads until the scene changes (the coordinator's latency rule, 2026-10-25): a turn that reads the
# same instancer again, or reads it after nothing changed, pays nothing. Kept by the update count of the evaluation
# they were read from (``_bpy._angelo_live.update_count``): Blender gives a depsgraph a new count, one no evaluation of
# any depsgraph shares, each time it evaluates a change (an edit, a frame change, Undo, Redo, a file load, Restore),
# and none while it is not evaluated since its change, so a kept fact is served only from the evaluation it describes.
# No change handlers to miss a change (a script can clear a handler list, REVIEW-INSTANCES-LATENCY M2) and the headless
# child keeps them too.
_cache = {}
CACHE_LIMIT = 4096
_MISSING = object()


def _update_count(depsgraph):
    """The count of ``depsgraph``'s last evaluation, or None (not evaluated since its change, no reader, not here)."""
    try:
        import _bpy
        return _bpy._angelo_live.update_count(depsgraph)
    except (ImportError, AttributeError, TypeError, ReferenceError, RuntimeError):
        return None


def _cached(depsgraph, uid, digits, limit):
    """``(True, fact)`` from the cache when it may be served, else ``(False, key)``; the key None keeps nothing."""
    count = _update_count(depsgraph)
    if not count:
        return False, None
    pointer = getattr(depsgraph, "as_pointer", None)
    key = (pointer() if callable(pointer) else id(depsgraph), count, uid, digits, limit)
    fact = _cache.get(key, _MISSING)   # Read once: never a check and a read apart (REVIEW-INSTANCES-LATENCY L3).
    return (True, fact) if fact is not _MISSING else (False, key)


class InstanceFacts:
    """Instance facts for one read pass: each object that can make instances is expanded once, alone, when a reader
    first asks about it (most passes ask about none)."""

    def __init__(self, depsgraph, digits=DIGITS, limit=None):
        self._depsgraph, self._digits, self._limit, self._facts = depsgraph, digits, limit, {}

    def own_box_known(self, obj):
        """``own_box_known`` in this pass's depsgraph."""
        return own_box_known(obj, self._depsgraph)

    def of(self, obj):
        """``{"bounds"[, "own_geometry": False]}``: the world box of what ``obj``'s instances show; ``{"unknown":
        why[, "own_geometry"]}`` when they cannot be read (no reader then states a size of its own for it unless the
        pass saw it shows geometry itself, never its data's box, REVIEW-INSTANCES M1); None when it makes none."""
        uid = getattr(obj, "session_uid", None)
        if self._depsgraph is None or uid is None or not makes_instances(obj):
            return None
        if uid not in self._facts:
            hit, key = _cached(self._depsgraph, uid, self._digits, self._limit)
            if hit:
                self._facts[uid] = key
                return key
            found = instance_facts(self._depsgraph, [obj], self._digits, self._limit)
            if found is None or found is False:
                return dict(NOT_EVALUATED if found is None else UNAVAILABLE)
            self._facts[uid] = found.get(uid) or _hidden_fact(obj, self._depsgraph)
            if key is not None:
                if len(_cache) >= CACHE_LIMIT:
                    _cache.clear()
                _cache[key] = self._facts[uid]
        return self._facts[uid]


OWN_COMPONENTS = (("mesh", "vertices"), ("curves", "points"), ("pointcloud", "points"), ("volume", "grids"),
                  ("grease_pencil", "layers"))   # A mesh object's set always has a mesh, empty when it shows none.


def _holds(component, field):
    try:
        return component is not None and len(getattr(component, field)) > 0
    except (AttributeError, TypeError, RuntimeError, ReferenceError):
        return component is not None


def _hidden_fact(obj, depsgraph):
    """For an object the native pass made no fact of: None when it makes none, else ``HIDDEN`` when it is hidden in
    the depsgraph's view layer, which that pass does not expand (its own eye, Disable in Viewports, a collection
    disabled in viewports), so a hidden instancer is never stated as making none and sized by its data's box
    (REVIEW-HIDDEN-FLAGS-2 M2): its evaluated geometry decides when it was evaluated, its settings otherwise; its own
    size is kept only when that geometry shows some of its own."""
    try:
        if obj.visible_get(view_layer=depsgraph.view_layer) is not False:
            return None   # Shown: the native pass decided it makes none.
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return None   # Not known to be hidden: the native pass's answer stands.
    try:
        evaluated = obj.evaluated_get(depsgraph)
        instancing = getattr(obj, "instance_type", "NONE") != "NONE" or any(
            modifier.type == "PARTICLE_SYSTEM" for modifier in obj.modifiers)
        if not instancing and evaluated.is_evaluated:
            geometry = evaluated.evaluated_geometry()
            if geometry.instances_pointcloud() is None:
                return None   # Evaluated, and its geometry holds no instances.
            own = any(_holds(getattr(geometry, kind, None), field) for kind, field in OWN_COMPONENTS)
            return dict(HIDDEN, own_geometry=own)
        return dict(HIDDEN)
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return dict(HIDDEN)


def instance_fact(obj, depsgraph):
    """``obj``'s instance facts read alone (one native pass), or None."""
    return InstanceFacts(depsgraph).of(obj)
