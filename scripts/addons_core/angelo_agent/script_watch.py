# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a reviewed script did that Blender's Undo cannot be trusted to take back (``script_errors``).

The host claims ``main_data_restored`` only after Undo reloads the file from the
restore point it made before the script. Two things can make that claim false
although the restore point's marker is reached:

* **The script moved the Undo history or loaded a file.** Undo, Redo or a load
  can bring the restore point's marked state back into the scene, and a step
  the script then pushes carries the marker with the script's changes in it.
  ``HistoryWatch`` notices Undo, Redo and file loads while the script runs
  (``bpy.app.handlers``), and a handler the script removed counts as moved. A
  marker still in the live scene after the script means the same. Computed
  operator names cannot hide either; the static name scan cannot see them.
* **The script changed data Undo does not reload.** Memfile Undo leaves linked
  library data as it is, both the few properties a linked ID lets you change
  (a linked object's modifier visibility, for example) and editable assets
  such as the paint brushes Blender links from its asset library.
  ``linked_state`` digests exactly those values, per linked ID, before the
  script and after the reload; ``linked_unchanged`` drops the claim on any
  difference. An ID linked during the script is admitted only as an editable
  asset Blender reports unchanged since it was read (``has_unsaved_changes``,
  the brush a paint mode links on entry); any other new linked ID is a change.

Neither proves the absence of an effect. Arbitrary Python can still reach the
host's own frames and forge evidence; the result never claims more than Main
data, and interface data (``INTERFACE``) is outside that scope by definition.
Main thread only.
"""

import hashlib
import json

# The handlers that fire when the history moves or a file loads; Undo itself fires none on a push.
HISTORY_HANDLERS = ("undo_pre", "undo_post", "redo_pre", "redo_post", "load_pre", "load_post")
LINKED = ("The script changed linked library data or an editable asset (a brush, for example), or there was too "
          "much of it to verify; Blender's Undo does not reload it, so the host put back what the script left.")
# What Blender's memfile Undo does not reload, whatever the restore point: stated, never claimed restored.
INTERFACE = ("window_manager", "workspaces", "screens")
INTERFACE_NOTE = ("Interface data is outside Main data: the window manager, workspaces and screens, their custom "
                  "properties included, are not reloaded by Undo.")
MAX_DEPTH, MAX_VALUES = 3, 100000
_SIMPLE = frozenset({"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"})
# Not read: an asset's preview thumbnail (2.7 MB of pixels on a brush, ~0.8 s to read) and other
# dynamic-length arrays, which are pixel buffers, not settings.
_UNREAD_TYPES = frozenset({"ImagePreview"})
_PLANS = {}


class HistoryWatch:
    """Notices Undo, Redo and file loads between ``start`` and ``stop``."""

    def __init__(self, bpy):
        self.bpy, self.moved, self._handlers = bpy, False, {}

        def seen(*_args):
            self.moved = True

        # Persistent: a file load would otherwise remove it before load_post could fire.
        self._seen = bpy.app.handlers.persistent(seen)

    def start(self):
        for name in HISTORY_HANDLERS:
            handlers = getattr(self.bpy.app.handlers, name, None)
            if handlers is not None:
                handlers.append(self._seen)
                self._handlers[name] = handlers
        return self

    def stop(self):
        """Whether the history moved or a handler was taken away; idempotent."""
        for name, handlers in self._handlers.items():
            present = any(item is self._seen for item in handlers)
            if not present:
                self.moved = True  # The script removed it: what it hid is unknown.
            while any(item is self._seen for item in handlers):
                handlers.remove(self._seen)
        self._handlers = {}
        return self.moved


def _value(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if hasattr(value, "bl_rna"):
        return [value.bl_rna.identifier, getattr(value, "name", "")]  # An ID or struct reference, by identity.
    try:
        return [_value(item) for item in value]
    except TypeError:
        return str(value)


def _is_id(struct):
    while struct is not None:
        if struct.identifier == "ID":
            return True
        struct = struct.base
    return False


def _unread(prop):
    if prop.type in ("POINTER", "COLLECTION"):
        return getattr(prop.fixed_type, "identifier", None) in _UNREAD_TYPES
    return bool(getattr(prop, "is_array", False)) and not getattr(prop, "array_length", 1)


def _plan(rna, everything):
    """``(values, nested)`` property names worth reading on structs of this RNA type (cached per type).

    ``values``: simple properties and ID references a script could change
    (every one of an editable asset, else only the library-editable ones).
    ``nested``: pointers and collections of non-ID structs that may hold such values.
    """
    key = (rna.identifier, everything)
    if key in _PLANS:
        return _PLANS[key]
    _PLANS[key] = ((), ())  # A recursive type contributes nothing while its own plan is being made.
    values, nested = [], []
    for prop in rna.properties:
        name = prop.identifier
        if name == "rna_type" or _unread(prop):
            continue
        if prop.type in _SIMPLE or prop.type in ("POINTER", "COLLECTION") and _is_id(prop.fixed_type):
            if everything or prop.is_library_editable:
                values.append(name)
        elif prop.type in ("POINTER", "COLLECTION") and prop.fixed_type is not None:
            if everything or any(_plan(prop.fixed_type, everything)):
                nested.append(name)
    _PLANS[key] = (tuple(values), tuple(nested))
    return _PLANS[key]


def _walk(struct, everything, depth, out, budget):
    values, nested = _plan(struct.bl_rna, everything)
    for name in values:
        budget[0] -= 1
        if budget[0] < 0:
            raise OverflowError
        try:
            out.append((depth, name, _value(getattr(struct, name))))
        except (AttributeError, ReferenceError, RuntimeError):
            continue
    if depth >= MAX_DEPTH:
        return
    for name in nested:
        try:
            item = getattr(struct, name)
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if item is None:
            continue
        for index, child in enumerate(item if hasattr(item, "__iter__") and not hasattr(item, "bl_rna") else [item]):
            budget[0] -= 1
            if budget[0] < 0:
                raise OverflowError
            if hasattr(child, "bl_rna"):
                out.append((depth, name, index, child.bl_rna.identifier))
                _walk(child, everything, depth + 1, out, budget)


def linked_state(bpy):
    """``{linked ID: (digest, pristine)}`` of what a script could change in linked data, or None when there
    is too much to read.

    The digest covers every value of an editable linked asset and only the
    library-editable values of other linked IDs; local data is Main data, which
    the restore point holds. ``pristine``: an editable asset Blender reports
    unchanged since it was read from its library.
    """
    blocks, budget = {}, [MAX_VALUES]
    if not len(getattr(bpy.data, "libraries", ())):
        return blocks  # Nothing is linked: no ID to read (a scene of 5000 local objects costs ~3 ms to skip).
    try:
        for collection in ("objects", "brushes", "materials", "node_groups", "collections", "meshes", "lights",
                           "cameras", "worlds", "scenes", "images", "palettes", "textures", "actions", "armatures",
                           "curves", "grease_pencils", "particles", "linestyles", "masks", "movieclips", "sounds"):
            for block in getattr(bpy.data, collection, ()):
                if getattr(block, "library", None) is None:
                    continue
                everything, rows = bool(getattr(block, "is_editable", False)), []
                _walk(block, everything, 0, rows, budget)
                encoded = json.dumps([everything, rows], default=str, separators=(",", ":"))
                # Keyed by ID: a reload may list the same IDs in another order.
                blocks[json.dumps([collection, block.name, block.library.filepath])] = (
                    hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    everything and getattr(block, "has_unsaved_changes", None) is False)
    except OverflowError:
        return None
    return blocks


def linked_unchanged(before, after):
    """Whether every linked ID of ``before`` reads the same in ``after``, and any new one is a pristine asset.

    Entering a paint mode links its brush from Blender's asset library, and
    Undo keeps it: that alone changes nothing the script could have edited.
    """
    if before is None or after is None:
        return False
    if any(after.get(key, (None,))[0] != digest for key, (digest, _pristine) in before.items()):
        return False
    return all(pristine for key, (_digest, pristine) in after.items() if key not in before)
