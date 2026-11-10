# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
"""Fit to Mesh: the one measurement the export, the Game Physics panel and Edit Fitted Size share.

``rest_bounds(obj)`` is the bounds, in the object's own (Blender) axes, of the geometry a glTF export writes for it:

* not deformed by an armature: the evaluated geometry (``bound_box``), which is what an export that applies
  modifiers writes (the engine link, the Godot link, the web export and the team library all do);
* deformed by an armature: the rest pose the skin binds, not the pose of the current frame. With no other modifier
  that is the mesh's own vertices; with others, the object evaluated with its Armature modifiers off, the way the
  exporter itself evaluates a skinned mesh it applies modifiers to.

Only the armature case costs anything (a pass over the vertices, or an evaluation), so the panel never measures in
its draw: ``cached_bounds`` answers from a per-object cache or asks a timer to measure (``request``). The
depsgraph-update handler only notes which cached objects had a geometry update; a timer then compares what the
measurement depends on (``signature``: the data, a digest of the vertices, the modifier stack) and re-measures
only on a real change. A pose is not in it, so animation playback measures nothing, and an export, which turns
Armature modifiers off and back within one operator, changes nothing. The export and Edit Fitted Size always
measure afresh.
"""

try:
    from . import shapes
except ImportError:   # Imported on its own (the host tests).
    import shapes

KINDS = ("evaluated", "vertices", "rest")
_cache = {}      # Object.session_uid -> (signature, (minimum, maximum), the object)
_pending = {}    # Object.session_uid -> object name, to measure on the next timer tick
_suspect = {}    # Object.session_uid -> whether its data was updated too, for ``settle`` to check
_SIMPLE = frozenset({"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"})
_measuring = [False]   # Set while ``rest_bounds`` switches modifiers itself: the updates that causes are its own.
animation = [0]   # Counts Action updates, loads, undos and redos: a key for caches of what animation moves.


def armature_deformed(obj):
    """Whether an armature deforms ``obj`` in the viewport (an enabled Armature modifier with an armature)."""
    return any(modifier.type == "ARMATURE" and modifier.show_viewport and modifier.object is not None
               for modifier in getattr(obj, "modifiers", ()))


def kind(obj):
    """How ``rest_bounds`` measures ``obj``: ``evaluated``, ``vertices`` (its mesh's own) or ``rest`` (re-evaluated)."""
    if not armature_deformed(obj):
        return "evaluated"
    others = any(modifier.show_viewport and modifier.type != "ARMATURE" for modifier in obj.modifiers)
    return "rest" if others or obj.type != "MESH" else "vertices"


def points_bounds(points):
    """(minimum, maximum) of an (n, 3) array of points, or None when there are none."""
    if len(points) == 0:
        return None
    return tuple(float(value) for value in points.min(axis=0)), tuple(float(value) for value in points.max(axis=0))


def _vertex_bounds(mesh):
    import numpy as np
    points = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", points)
    return points_bounds(points.reshape(-1, 3))


def _rest_evaluated_bounds(obj):
    """The object evaluated with its Armature modifiers off (restored before returning), as the exporter does."""
    import bpy
    switched = [modifier for modifier in obj.modifiers if modifier.type == "ARMATURE" and modifier.show_viewport]
    _measuring[0] = True
    try:
        for modifier in switched:
            modifier.show_viewport = False
        bpy.context.evaluated_depsgraph_get()
        return shapes.bounds(obj.bound_box)
    finally:
        try:
            for modifier in switched:
                modifier.show_viewport = True
            bpy.context.evaluated_depsgraph_get()
        finally:
            _measuring[0] = False


def rest_bounds(obj):
    """(minimum, maximum) of the geometry an export writes for ``obj``, in its own axes (see the module notes)."""
    how = kind(obj)
    if how == "evaluated":
        return shapes.bounds(obj.bound_box)
    if how == "vertices":
        return _vertex_bounds(obj.data) or shapes.bounds(obj.bound_box)
    return _rest_evaluated_bounds(obj)


def fitted(obj, settings):
    """The primitive ``settings`` fit to ``obj`` now (``rest_bounds``), computed whatever it costs."""
    bounds = rest_bounds(obj)
    if kind(obj) != "evaluated":
        store(obj, bounds)
    return shapes.fit_bounds(settings.shape, *bounds, settings.axis)


# --- The panel's cache.

def _plain(value):
    """A copy of an RNA value that compares by value later (arrays and matrices read live data)."""
    if hasattr(value, "as_pointer"):
        return value.as_pointer()
    if isinstance(value, (str, bytes, int, float, bool, set, frozenset)) or value is None:
        return value
    try:
        return tuple(_plain(item) for item in value)
    except TypeError:
        return repr(value)


def _vertices_digest(data):
    """A digest of a mesh's vertex positions (None for other data): what ``vertices`` measures."""
    vertices = getattr(data, "vertices", None)
    if not hasattr(vertices, "foreach_get"):
        return None
    import hashlib
    import numpy as np
    points = np.empty(len(vertices) * 3, dtype=np.float32)
    vertices.foreach_get("co", points)
    return hashlib.blake2b(points.tobytes(), digest_size=16).digest()


def signature(obj, vertices=True):
    """What ``rest_bounds`` of an armature-deformed object depends on: its data, every modifier's settings and (with
    ``vertices``) a digest of its mesh's vertices; a pose is not among them. Compared with ``==``. It runs when
    measuring and in ``settle``, never in a draw or the depsgraph handler; only the digest reads every vertex."""
    data = getattr(obj, "data", None)
    stack = tuple((modifier.type, tuple(_plain(getattr(modifier, prop.identifier, None))
                                        for prop in modifier.bl_rna.properties
                                        if prop.type in _SIMPLE or prop.type == "POINTER"))
                  for modifier in obj.modifiers)
    count = len(data.vertices) if hasattr(data, "vertices") else None
    head = (data.as_pointer() if data is not None else None, count, stack)
    return head + (_vertices_digest(data) if vertices else None,)


def store(obj, bounds):
    _cache[obj.session_uid] = (signature(obj), bounds, obj)


def cached_bounds(obj):
    """``rest_bounds`` without measuring in a draw: at once when it's free, else the cached value, else None (a timer
    measures it, ``request``, and redraws)."""
    if kind(obj) == "evaluated":
        return shapes.bounds(obj.bound_box)
    entry = _cache.get(obj.session_uid)
    if entry is not None:
        return entry[1]
    request(obj)
    return None


def request(obj):
    _pending[obj.session_uid] = obj.name
    _schedule(_measure_pending)


def _measure_pending():
    import bpy
    pending = dict(_pending)
    _pending.clear()
    for uid, name in pending.items():
        obj = bpy.data.objects.get(name)
        if obj is None or obj.session_uid != uid or kind(obj) == "evaluated":
            continue
        try:
            store(obj, rest_bounds(obj))
        except (ReferenceError, RuntimeError):
            continue
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in ("PROPERTIES", "VIEW_3D"):
                area.tag_redraw()
    return None


def invalidate(updates):
    """Act on a depsgraph's ``updates`` at no cost: a cached object whose geometry, or whose data's geometry, was
    updated becomes a suspect that a timer checks (``settle``). Free when nothing is cached."""
    if not _cache:
        return
    data, objects = set(), set()
    for update in updates:
        if update.is_updated_geometry:
            item = getattr(update.id, "original", update.id)
            data.add(item.as_pointer())
            if type(item).__name__ == "Object":
                objects.add(item.session_uid)
    for uid, (stored, _bounds, obj) in _cache.items():
        if uid in objects or stored[0] in data:   # Its data's own update may mean new vertices: check those too.
            _suspect[uid] = _suspect.get(uid, False) or stored[0] in data
    if _suspect:
        _schedule(settle)


def settle():
    """Drop and re-measure each suspect whose data, vertices or modifier stack really changed. A pose changes none of
    them, and neither does a switch undone within one operator (an export turns Armature modifiers off and back)."""
    suspects = dict(_suspect)
    _suspect.clear()
    for uid, touched in suspects.items():
        entry = _cache.get(uid)
        if entry is None:
            continue
        stored, _bounds, obj = entry
        try:   # An object-only update (a pose, every frame of playback) compares no vertices.
            current = signature(obj, touched)   # Data with no vertices to compare (a curve) counts as changed.
            changed = current[:3] != stored[:3] or (touched and (current[3] is None or current[3] != stored[3]))
        except ReferenceError:   # The object is gone (deleted, or undone).
            del _cache[uid]
            continue
        if changed:
            del _cache[uid]
            request(obj)
    return None


def _schedule(function):
    import bpy
    if not bpy.app.timers.is_registered(function):
        bpy.app.timers.register(function, first_interval=0.0)


def clear():
    _cache.clear()
    _pending.clear()
    _suspect.clear()


def _on_depsgraph(_scene, depsgraph):
    if depsgraph.id_type_updated('ACTION'):
        animation[0] += 1
    if _cache and not _measuring[0]:
        invalidate(depsgraph.updates)


def _on_load(*_args):   # A file load, an undo or a redo replaces the objects the cache holds.
    animation[0] += 1
    clear()


def register():
    import bpy
    from bpy.app.handlers import persistent
    global _on_depsgraph, _on_load
    _on_depsgraph, _on_load = persistent(_on_depsgraph), persistent(_on_load)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    for handlers in _replacing(bpy):
        handlers.append(_on_load)


def _replacing(bpy):
    return bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post


def unregister():
    import bpy
    for handlers, function in [(bpy.app.handlers.depsgraph_update_post, _on_depsgraph)] + [
            (handlers, _on_load) for handlers in _replacing(bpy)]:
        if function in handlers:
            handlers.remove(function)
    for function in (_measure_pending, settle):
        if bpy.app.timers.is_registered(function):
            bpy.app.timers.unregister(function)
    clear()
