# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether the document changed while a look or a strip ran, and what its result may then claim.

A RENDERED look and a motion strip put back what they set. After every one,
``SceneCheck`` compares the document with its state before, leaving out the
settings the look set and put back (its restore checks and reports those). How
much it reads depends on whether Python from add-ons can run meanwhile
(``SceneCheck(window, deep=...)``, ``render_extensions``):

* **None can** (``deep`` false): every look and strip in the Read-only and Ask
  approval modes, and in the Auto modes when no add-on handles depsgraph
  updates; add-on render or frame-change handlers, scripted drivers and add-on
  engines refuse a view in every mode. The view then runs only Blender's and
  Angelo's code: a render and a frame change evaluate copies of the data, and
  the view's own writes are put back by construction. The check confirms that
  at little cost, mostly in native reads: the data-blocks of ``COLLECTIONS``
  by session UID (added or removed), the bound scene's settings, render, view
  and display settings and custom properties, and every object's transform and
  render visibility. That also finds a frame change undoing an unkeyed edit,
  and the person adding, deleting or moving things while a look renders. What
  it leaves out (links, mesh positions, other data-blocks' settings) neither a
  render nor the view's own writes change; a strip's frame changes do
  re-evaluate animation, which, as scrubbing does, can put an unkeyed edit of
  another animated setting back to its keyed value.
* **Add-on Python can** (``deep`` true: the depsgraph-update handlers the
  Auto modes allow). It can change anything, so the check also reads each
  scene object's data, parent, materials, modifiers and mesh vertex positions
  (a CRC-32 of each mesh's position attribute, within ``MAX_VERTICES`` in
  all), and every data-block's writable native settings, data-block pointers
  and custom properties. Before
  the second reading it lets the depsgraph evaluate what the restore tagged
  (``ViewLayer.update``), which runs the handlers that update would have run
  anyway on the next event, so their changes fall inside the comparison.
  Blender runs handlers only in the first pass of an update, so their own
  edits never show as depsgraph updates: only reading the data finds them.

Numbers, booleans and enums are read a whole collection at a time
(``foreach_get``, one call per property) and compared byte for byte; only when
something differs is it mapped back to data-blocks to name them. Strings,
data-block pointers and custom properties are read one by one, within
``MAX_READS``. Never read: an add-on's property groups (their getters are
Python) and arrays without a fixed length (an image's pixels). Not compared:
animation curves, node links and pixels. So ``claims`` says a scene is
unchanged only when the check found nothing and no add-on Python ran; with
add-on Python and no difference found, ``scene_changed`` is null, never false.
``benchmark_blender_view_scene_check.py`` measures both kinds of check, and the
instruction ratchet keeps them from growing (doc/angelo/performance.rst, "Scene
check after views"). Main thread only; ``bpy`` is imported where used.
Model-facing English.
"""

import zlib
from array import array
from itertools import islice

COLLECTIONS = ("objects", "meshes", "curves", "materials", "worlds", "lights", "cameras", "node_groups",
               "collections", "scenes", "textures", "images", "actions", "armatures", "grease_pencils", "particles",
               "lattices", "metaballs", "volumes", "pointclouds", "hair_curves", "speakers", "lightprobes", "texts",
               "linestyles", "masks", "movieclips", "sounds", "cache_files", "fonts")
NOT_DOCUMENT_IMAGES = frozenset({"RENDER_RESULT", "COMPOSITING"})
NATIVE = {"BOOLEAN": "b", "INT": "i", "ENUM": "i", "FLOAT": "f"}  # foreach_get item types (an enum is its value).
TAG = "_angelo_job_restore"  # job_settings.TAG: the look's own restore record.
MAX_OBJECTS = 10_000      # Objects whose links are read one by one.
MAX_VERTICES = 10_000_000  # Mesh positions summed, in all.
MAX_READS = 200_000       # Values read one by one.
MAX_VALUES = 4_000_000    # Values read a collection at a time.
MAX_ARRAY = 16
MAX_LISTED = 8

_plans = {}  # (RNA struct identifier, property count) -> (native, python): what is read of that struct.


class _Budget:
    def __init__(self):
        self.reads, self.values, self.exceeded = MAX_READS, MAX_VALUES, False

    def take(self, reads=1, values=0):
        self.reads, self.values = self.reads - reads, self.values - values
        self.exceeded = self.exceeded or self.reads < 0 or self.values < 0
        return not self.exceeded


def _is_id(struct):
    while struct is not None:
        if struct.identifier == "ID":
            return True
        struct = getattr(struct, "base", None)
    return False


def _plan(rna):
    """``(native, python)`` of a struct's writable native properties: ``(name, item type, length)`` read a
    collection at a time, and ``(name, is_data_block)`` read one by one (strings, data-block pointers)."""
    if rna is None:
        return (), ()
    key = (rna.identifier, len(rna.properties))  # An add-on registering a property makes a new plan.
    plan = _plans.get(key)
    if plan is None:
        native, python = [], []
        for prop in rna.properties:
            if prop.identifier in {"rna_type", "name"} or getattr(prop, "is_runtime", False) or prop.is_readonly:
                continue  # Add-on (Python) properties are never read: their getters would run. Names: inventory.
            if prop.type in NATIVE:
                length = prop.array_length if getattr(prop, "is_array", False) else 1  # Enums have no arrays.
                if 0 < length <= MAX_ARRAY:  # An array without a fixed length (Image.pixels) has length 0.
                    native.append((prop.identifier, NATIVE[prop.type], length))
            elif prop.type == "STRING":
                python.append((prop.identifier, False))
            elif prop.type == "POINTER" and _is_id(getattr(prop, "fixed_type", None)):
                python.append((prop.identifier, True))
        plan = _plans[key] = (tuple(native), tuple(python))
    return plan


def _flat(value):
    """A detached tuple of an array, vector or matrix (they read live data), or the value itself."""
    if isinstance(value, (set, frozenset)):  # An enum with several choices.
        return tuple(sorted(value))
    if isinstance(value, str) or not hasattr(value, "__len__"):
        return value
    return tuple(item for row in value
                 for item in (row if hasattr(row, "__len__") and not isinstance(row, str) else (row,)))


def _get(owner, name, is_id=False):
    try:
        value = getattr(owner, name)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return "<unreadable>"
    return getattr(value, "session_uid", None) if is_id else _flat(value)


def _custom(owner, budget, skip=()):
    try:
        keys = list(owner.keys())
    except (AttributeError, TypeError):
        return ()
    values = []
    for key in keys:
        if key in skip or not budget.take():
            continue
        value = owner[key]
        for convert in ("to_dict", "to_list"):  # Group and array properties: their plain contents.
            if hasattr(value, convert):
                value = getattr(value, convert)()
                break
        values.append((key, repr(value)[:512]))
    return tuple(values)


def _column(collection, name, code, length, budget, count=None):
    """One property of every item, as bytes (None when Blender cannot read it a collection at a time).

    ``count``: the collection's length when the caller has it (counting a data-block list walks it).
    """
    count = len(collection) if count is None else count
    if not count:
        return b""
    if not budget.take(0, count * length):
        return None
    buffer = array(code, bytes(array(code).itemsize * count * length))
    try:
        collection.foreach_get(name, buffer)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return buffer.tobytes()


def _settings(scene, budget):
    """``{path: value}`` of the bound scene's own settings and custom properties (the restore tag left out)."""
    values = {}
    for prefix, owner in (("", scene), ("render.", getattr(scene, "render", None)),
                          ("view_settings.", getattr(scene, "view_settings", None)),
                          ("display_settings.", getattr(scene, "display_settings", None))):
        native, python = _plan(getattr(owner, "bl_rna", None))
        budget.take(len(native) + len(python))
        values.update((prefix + name, _get(owner, name)) for name, _code, _length in native)
        values.update((prefix + name, _get(owner, name, is_id)) for name, is_id in python)
    values.update(("[%r]" % key, value) for key, value in _custom(scene, budget, skip={TAG}))
    return values


def _inventory(data, collections=COLLECTIONS):
    """``{collection: (session UIDs as bytes, names, UIDs left out)}`` of ``collections`` (``script_renames`` reads
    some): the Render Result and Viewer are not the document's, so a look that makes them adds nothing."""
    found = {}
    for name in collections:
        collection = getattr(data, name, None)
        if collection is None:
            continue
        names = tuple(collection.keys())  # Their count spares the column a walk of the list to count it.
        uids = _column(collection, "session_uid", "q", 1, _Budget(), len(names)) or b""
        skip = frozenset(image.session_uid for image in collection
                         if getattr(image, "type", "") in NOT_DOCUMENT_IMAGES) if name == "images" else frozenset()
        found[name] = (uids, names if uids else (), skip)
    return found


def _uids(entry):
    return array("q", entry[0]).tolist()


def _objects(data, scene, deep, budget):
    """Every object's transform and render visibility (``bpy.data.objects``, in the inventory's order), and
    with ``deep`` each scene object's links and mesh positions."""
    objects = getattr(data, "objects", ())
    reading = {"matrices": _column(objects, "matrix_world", "f", 16, budget),
               "hidden": _column(objects, "hide_render", "b", 1, budget), "links": {}, "complete": True}
    if deep:
        reading["complete"] = _links(getattr(scene, "objects", ()), reading["links"], budget)
    return reading


def _positions(mesh):
    """A CRC-32 of a mesh's vertex positions, read in one native copy of the position attribute."""
    import numpy
    attribute = mesh.attributes.get("position")
    if attribute is None:
        return None
    buffer = numpy.empty(len(attribute.data) * 3, numpy.float32)
    attribute.data.foreach_get("vector", buffer)
    return zlib.crc32(buffer)


def _links(objects, links, budget):
    """Each scene object's data, parent, materials, modifiers and mesh positions (a mesh once); False when
    ``MAX_OBJECTS`` or ``MAX_VERTICES`` left some out."""
    sums, vertices, complete = {}, MAX_VERTICES, len(objects) <= MAX_OBJECTS
    for obj in islice(objects, MAX_OBJECTS):
        mesh = getattr(obj, "data", None) if getattr(obj, "type", "") == "MESH" else None
        if mesh is not None and mesh.session_uid not in sums:
            count = len(mesh.vertices)
            fits, complete = count <= vertices, complete and count <= vertices
            sums[mesh.session_uid] = _positions(mesh) if fits else None
            vertices -= count if fits else 0
        links[obj.session_uid] = (
            _get(obj, "data", True), _get(obj, "parent", True),
            tuple(_get(slot, "material", True) for slot in getattr(obj, "material_slots", ())),
            tuple((m.name, m.type, m.show_viewport, m.show_render) for m in getattr(obj, "modifiers", ())),
            sums.get(getattr(mesh, "session_uid", None)))
        budget.take(4)
    return complete


def _item_rna(data, name):
    """The struct every item of ``bpy.data.<name>`` is (a light's subtype adds properties), or None."""
    try:
        return data.bl_rna.properties[name].fixed_type
    except (AttributeError, KeyError):
        return None


def _subtype(block, base, native, python):
    """What a subtype adds to its collection's struct (a spot light's cone), read one by one."""
    rna = getattr(block, "bl_rna", None)
    if rna is None or base is not None and rna.identifier == base.identifier:
        return ()
    own_native, own_python = _plan(rna)
    return (tuple(_get(block, entry[0]) for entry in own_native if entry not in native)
            + tuple(_get(block, name, is_id) for name, is_id in own_python if (name, is_id) not in python))


def _deep(data, inventory, budget):
    """``{collection: (plan, columns, rows)}``: the native settings of every data-block a collection at a time,
    and per session UID its strings, data-block pointers, a subtype's own settings and custom properties."""
    import bpy
    values = {}
    for name, entry in inventory.items():
        collection, base = getattr(data, name), _item_rna(data, name)
        native, python = _plan(base)
        base_class = getattr(getattr(bpy, "types", None), getattr(base, "identifier", ""), None)
        columns = tuple(_column(collection, prop, code, length, budget) for prop, code, length in native)
        rows = {}
        for block, uid in zip(collection, _uids(entry)):
            extra = () if type(block) is base_class else _subtype(block, base, native, python)
            rows[uid] = (tuple(_get(block, prop, is_id) for prop, is_id in python), extra, _custom(block, budget))
            if not budget.take(len(python) + len(extra) + 1):
                break
        values[name] = (native, columns, rows)
        if budget.exceeded:
            break
    return values


class SceneCheck:
    """A reading of the document before a look or strip, compared by ``finish`` after it put back what it set.

    ``deep``: Python from add-ons can run during the view (its depsgraph-update handlers, Auto modes only).
    """

    def __init__(self, window, *, deep=False):
        import bpy
        self.window, self.scene, self.deep = window, window.scene, deep
        self.before = self._read(getattr(bpy, "data", None))

    def _read(self, data):
        budget = _Budget()
        inventory = _inventory(data)
        objects = _objects(data, self.scene, self.deep, budget)
        reading = {"inventory": inventory, "settings": _settings(self.scene, budget), "objects": objects,
                   "deep": _deep(data, inventory, budget) if self.deep else {}}
        reading["complete"] = objects["complete"] and not budget.exceeded
        return reading

    def finish(self, own=()):
        """``{"changes": [...], "complete": bool}``; ``own``: setting paths the look set and put back itself."""
        import bpy
        if self.deep:
            try:
                self.window.view_layer.update()  # What the restore tagged, and the handlers it runs, happen now.
            except (AttributeError, ReferenceError, RuntimeError):
                pass
        after = self._read(getattr(bpy, "data", None))
        return {"changes": compare(self.before, after, own, getattr(self.scene, "session_uid", None)),
                "complete": self.before["complete"] and after["complete"]}


def _differ(old, new):
    return old != new and repr(old) != repr(new)  # A NaN read twice is the same setting.


def _kind(collection):
    return collection[:-2] if collection.endswith("shes") else collection[:-1]


def _names(inventory, collection):
    entry = inventory.get(collection)
    return dict(zip(_uids(entry), entry[1])) if entry else {}


def _cells(native, columns, uids):
    """``{uid: {property index: bytes}}``: each data-block's share of the columns Blender could read."""
    cells = {uid: {} for uid in uids}
    for index, ((_name, code, length), column) in enumerate(zip(native, columns)):
        size = array(code).itemsize * length
        if column is None or len(column) != size * len(uids):
            continue
        for position, uid in enumerate(uids):
            cells[uid][index] = column[position * size:(position + 1) * size]
    return cells


def _object_rows(reading):
    objects = reading["objects"]
    matrices, hidden = objects["matrices"] or b"", objects["hidden"] or b""
    rows = {uid: (matrices[index * 64:index * 64 + 64], hidden[index:index + 1], objects["links"].get(uid))
            for index, uid in enumerate(_uids(reading["inventory"].get("objects", (b"",))))}
    rows.update((uid, (None, None, link)) for uid, link in objects["links"].items() if uid not in rows)
    return rows


def _added_removed(before, after):
    added, removed = [], []
    for collection in COLLECTIONS:
        old, new = before.get(collection), after.get(collection)
        if old is None or new is None or old == new:
            continue
        skip, old_ids, new_ids = old[2] | new[2], _names(before, collection), _names(after, collection)
        added += ["%s %s" % (_kind(collection), name) for uid, name in new_ids.items()
                  if uid not in old_ids and uid not in skip]
        removed += ["%s %s" % (_kind(collection), name) for uid, name in old_ids.items()
                    if uid not in new_ids and uid not in skip]
    return added, removed


def _changed_blocks(before, after, bound):
    """Names of the data-blocks in both readings whose settings, pointers or custom properties differ."""
    changed = []
    for collection, (native, old_columns, old_rows) in before["deep"].items():
        _native, new_columns, new_rows = after["deep"].get(collection, (native, (), {}))
        old_entry, new_entry = before["inventory"][collection], after["inventory"][collection]
        if old_columns == new_columns and old_rows == new_rows and old_entry == new_entry:
            continue
        old_ids, new_ids = _names(before["inventory"], collection), _names(after["inventory"], collection)
        old_cells, new_cells = _cells(native, old_columns, list(old_ids)), _cells(native, new_columns, list(new_ids))
        for uid in (old_ids.keys() & new_ids.keys()) - old_entry[2] - new_entry[2] - {bound}:
            cells = old_cells[uid].keys() & new_cells[uid].keys()
            if (old_ids[uid] != new_ids[uid] or any(old_cells[uid][i] != new_cells[uid][i] for i in cells)
                    or (uid in old_rows and uid in new_rows and _differ(old_rows[uid], new_rows[uid]))):
                changed.append(new_ids[uid])
    return changed


def compare(before, after, own=(), bound=None):
    """Readable differences, at most ``MAX_LISTED`` of each kind; ``bound``: the bound scene's session UID."""
    added, removed = _added_removed(before["inventory"], after["inventory"])
    changes = ["added " + label for label in added[:MAX_LISTED]]
    changes += ["removed " + label for label in removed[:MAX_LISTED]]
    own = set(own)
    settings = [path for path in sorted(set(before["settings"]) | set(after["settings"]))
                if path not in own and _differ(before["settings"].get(path), after["settings"].get(path))]
    changes += ["scene setting " + path for path in settings[:MAX_LISTED]]
    old, new = before["objects"], after["objects"]
    if (any(old[key] != new[key] for key in ("matrices", "hidden", "links"))
            or before["inventory"].get("objects") != after["inventory"].get("objects")):
        old_rows, new_rows = _object_rows(before), _object_rows(after)
        names = {**_names(before["inventory"], "objects"), **_names(after["inventory"], "objects")}
        moved = [names.get(uid, "?") for uid in old_rows.keys() | new_rows.keys()
                 if _differ(old_rows.get(uid), new_rows.get(uid))]
        changes += ["object " + name for name in sorted(moved)[:MAX_LISTED]]
    changes += ["data-block " + name for name in sorted(_changed_blocks(before, after, bound))[:MAX_LISTED]]
    return changes


def claims(result, check, extension_handlers=(), unknown=False):
    """Make ``result`` say what the check found: never that the scene is untouched when that is not known.

    ``check``: ``SceneCheck.finish``'s answer, or None when no check ran (a view made at once on the main
    thread, ``view_policy``, whose Python runs after it, or a stopped look). ``extension_handlers``: labels
    of the add-on Python the view ran (Auto modes): depsgraph-update handlers, and message-bus subscribers
    to the settings it set, which Blender calls after each write, also after the view put them back and
    so after the check. ``unknown``: this build cannot list message-bus subscribers.
    """
    changes = (check or {}).get("changes") or []
    uncertainty = result.setdefault("uncertainty", [])
    if extension_handlers:
        result.setdefault("side_effects", []).append({"code": "extension_handlers_ran", "detail": (
            "Python from add-ons ran for this view's own updates and settings (the Auto approval mode allows "
            "it, as for edits): " + ", ".join(list(extension_handlers)[:MAX_LISTED]))})
    if unknown:
        uncertainty.append({"code": "message_bus_unknown", "detail": (
            "This build cannot list message-bus subscribers: Python an add-on subscribed to the settings this "
            "view set and put back may have run, and may change the scene after this result")})
    if changes:
        result["scene_changed"] = True
        result["changed_during_view"] = changes
        uncertainty.append({"code": "scene_changed_during_view", "detail": (
            "The document changed while this view was made, by add-on Python or by the person: "
            + "; ".join(changes) + ". Observe it again before relying on earlier views")})
    elif extension_handlers or unknown:
        result["scene_changed"] = None
        if extension_handlers and check is None:  # A view made at once, or stopped: nothing compared.
            uncertainty.append({"code": "scene_change_unverified", "detail": (
                "Add-on Python ran, or runs after this view, in answer to the settings it set for a moment and put "
                "back, and can change the scene: observe it again before relying on it")})
        elif extension_handlers:
            uncertainty.append({"code": "scene_change_unverified", "detail": (
                "No change was found in the data that can be read exactly, but add-on Python ran and can change "
                "data that is not compared (animation, node links, pixels, add-on settings), and message-bus "
                "subscribers run once more after the view put its settings back, after this check")})
    elif check is not None and not check.get("complete"):
        uncertainty.append({"code": "scene_check_partial", "detail": (
            "The scene is large: the check after this view compared only part of it")})
    if uncertainty:
        result["uncertain"] = True
    return result
