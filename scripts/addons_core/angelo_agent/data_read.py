# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_read_data``: one bounded, read-only data path of the open document.

The path (``data_path``) is resolved a step at a time against the installed
RNA definition: ``.name`` must be a property of the struct reached so far
(``bl_rna.properties``; functions are never called and Python attributes are
never fetched), ``[index]`` and ``["name"]`` select an item of a collection.
Every struct on the way is admitted by ``data_read_policy``, and every value
is encoded by ``data_read_rows`` and ``data_read_values``, which is where
paths, scripts and other withheld text stop. Add-on types registered in the
build (Cycles) resolve like Blender's own, because the walk never consults
``bpy.types`` by name.

The answer describes the place the path ends at:

* ``STRUCT``: its properties with types and current values, a page at a time;
* ``COLLECTION``: its length and a page of its items (name, identifier, type);
* ``PROPERTY``: one value, with an enum's choices and a number's hard range.

Bounds: 64 rows a call, 12,000 characters of rows and a two-second cooperative
deadline; whichever ends a page first sets ``truncated`` and ``next_offset``.
A path that does not resolve is an answer (``found`` false, a stable ``code``,
how far it resolved and the valid names there), not an error.

Main thread only. Nothing is evaluated, cached, written or retained, and the
reader refuses while a staged change's private datablocks are in the file.
Native metadata identifies Python getters without invoking them. Such reads
show explicitly labelled storage/default values, or a withheld value; computing
them remains available through approved scripts.
"""

import difflib
import time

from . import data_read_policy as policy, live_target
from . import data_read_values as values
from .data_read_access import Access, ReadUnavailable, Missing
from .data_path import ATTRIBUTE, INDEX, SCENE, DataPathError, parse, quote, render
from .data_read_rows import Rows
from .read_spec import validate_read

MAX_CHARS = 12000
DEADLINE_SECONDS = 2.0
MAX_SUGGESTIONS = 8
MAX_LISTED_NAMES = 160
MAX_SCANNED_KEYS = 4000
NEXT_PAGE = "More rows exist: read again with offset %d."
STAGING = ("A staged change is being prepared, and its private copies are in the file. Read again once it is "
           "applied, accepted or discarded.")


class _NotFound(Exception):
    """The path stopped; ``answer`` is what the model reads."""

    def __init__(self, code, message, **fields):
        super().__init__(message)
        self.answer = dict(found=False, code=code, message=message, **fields)


def _similar(name, names):
    return difflib.get_close_matches(name, names, n=MAX_SUGGESTIONS, cutoff=0.7)


class _Reader:
    window = live_target.WindowAttribute()   # Never a window a load or a close freed meanwhile (live_target).

    def __init__(self, bpy, window, args, handle=None, *, backend=None, epoch_owner=None):
        """Host tests inject a backend; production uses the native address ABI."""
        if epoch_owner is None:
            from . import scene_queries
            epoch_owner = scene_queries.document_epoch
        self.args, self.window = args, window
        self.backend = backend
        self.epoch_owner = epoch_owner
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.access = None
        self.rows = None
        self.root, self.steps, self.done = None, [], 0

    def resolved(self, extra=0):
        """The canonical spelling of the part of the path that resolved (``extra``: and the step being entered)."""
        return render(self.root, self.steps[:self.done + extra])

    # Resolution ----------------------------------------------------------------------------------------------

    def admit(self, value, *, entering=1):
        """Refuse a struct the policy does not read, naming the step that reached it."""
        refused = policy.refusal(value)
        if refused:
            raise _NotFound(refused[0], refused[1], resolved=self.resolved(entering))
        self.rows.note_struct(value)
        return value

    def member(self, holder, name, *, collection=False):
        """The RNA property ``name`` of a struct (or of a collection's own RNA), or an UNKNOWN_MEMBER answer."""
        if name in policy.DENIED_PROPERTIES:
            raise _NotFound("DENIED", policy.DENIED_STRUCTS["Struct"], resolved=self.resolved())
        description = self.access.member(holder, name)
        prop = description["property"]
        if prop is not None:
            return prop
        if description["function_exists"]:
            raise _NotFound("FUNCTION", "%s is a function; functions are never called. Read a property instead." % name,
                            resolved=self.resolved())
        names = [name for name in description["members"] if name not in policy.DENIED_PROPERTIES]
        hint = " A collection takes [index] or [\"name\"] to reach an item." if collection else ""
        raise _NotFound("UNKNOWN_MEMBER", "%s has no property %s.%s" % (self.resolved(), name, hint),
                        resolved=self.resolved(), member=name, similar=_similar(name, names),
                        members=names[:MAX_LISTED_NAMES], members_total=len(names))

    def item(self, collection, kind, key, prop=None):
        """One item of a collection by index or name, or an answer saying what the collection holds.

        ``prop``: the collection's own definition. Its items' names are listed only when
        they are plain labels (a packed image file is "named" by its file path).
        """
        try:
            return self.access.item(collection, kind, key)
        except Missing as missing:
            self.missing(missing, key=key, prop=prop, collection=collection)

    def missing(self, missing, *, key=None, prop=None, collection=None):
        """Render the native replay prefix, which may precede the Python walker."""
        address = missing.address
        steps = address[1] + address[2]
        resolved = render(address[0], steps[:missing.edge])
        code, length = missing.code, missing.length
        if key is None and missing.edge < len(steps):
            key = steps[missing.edge][1]
        if code == "INDEX_RANGE":
            message = "%s has %d items; index %s does not exist." % (resolved, length or 0, key)
            raise _NotFound(code, message, resolved=resolved, length=length)
        if code == "NO_NAMES":
            raise _NotFound(code, "%s is indexed by position only; use [index]." % resolved,
                            resolved=resolved, length=length)
        if code == "UNKNOWN_NAME":
            keys = []
            fixed = dict(prop.fixed_name) if prop is not None and prop.fixed_name else None
            if fixed and fixed["identifier"] in values.NAME_PROPERTIES and fixed["subtype"] not in values.PATH_SUBTYPES:
                for offset in range(0, min(length or 0, MAX_SCANNED_KEYS), 64):
                    items, _length, _more = self.access.items(collection, offset, min(64, MAX_SCANNED_KEYS-offset))
                    for _index, item in items:
                        if item is not None:
                            row = self.rows.reference(item, locate=False)
                            if "name" in row:
                                keys.append(row["name"])
                    if time.monotonic() > self.deadline:
                        break
            raise _NotFound(code, "%s has no item named %s." % (resolved, quote(key)), resolved=resolved,
                            name=key, similar=_similar(key, keys), names=keys[:MAX_SUGGESTIONS * 4], length=length)
        message = "%s is empty (None); the path cannot continue past it." % resolved if code == "NULL" else missing.message
        raise _NotFound(code, message, resolved=resolved)

    def enter(self, owner, prop):
        """The node a property leads to: a struct, a collection, None, or the property itself."""
        if prop.type == "COLLECTION":
            if policy.identity_only(owner):
                raise _NotFound("DENIED", policy.FILE_BACKED_REASON, resolved=self.resolved(1))
            rewriting = policy.collection_refusal(values.struct_names(owner.type), prop.identifier)
            if rewriting:
                raise _NotFound("DENIED", rewriting, resolved=self.resolved(1), length=self.access.value(owner, prop, derived=False).length)
            # Not fetched here: the native operation that next uses it (an item, a page or its
            # declarations) replays and checks the whole path, so walking stays one operation per step.
            return "COLLECTION", self.access.collection_at(owner, prop)
        if prop.type == "POINTER":
            denied = next((name for name in values.struct_names(prop.fixed_type) if name in policy.DENIED_STRUCTS), None)
            if denied:  # Not even fetched: a dependency graph, a preview, packed bytes.
                raise _NotFound("DENIED", policy.DENIED_STRUCTS[denied], resolved=self.resolved(1))
            target = self.access.value(owner, prop, derived=False)
            if target is None:
                return "NULL", None
            return "STRUCT", self.admit(target)
        return "PROPERTY", None

    def walk(self):
        """Resolve every step; returns ``(kind, target, owner, prop)`` for the place the path ends at."""
        current, kind, owner, prop = self.access.root(self.root), "STRUCT", None, None
        if self.root == SCENE:
            self.admit(current, entering=0)
        for step_kind, value in self.steps:
            if kind == "STRUCT":
                if step_kind != ATTRIBUTE:
                    raise _NotFound("NOT_A_COLLECTION", "%s is a struct; use .property, not [ ]." % self.resolved(),
                                    resolved=self.resolved())
                if current.is_data_root and value in policy.DATA_DENIED:
                    raise _NotFound("DENIED", policy.DATA_DENIED[value], resolved=self.resolved())
                owner, prop = current, self.member(current, value)
                kind, current = self.enter(owner, prop)
            elif kind == "COLLECTION":
                if step_kind == ATTRIBUTE:
                    owner, prop = current, self.member(current, value, collection=True)
                    kind, current = self.enter(owner, prop)
                else:
                    current, owner, prop = self.item(current, step_kind, value, prop), None, None
                    # An empty slot (a mesh's unset material) is a value, not a struct.
                    kind = "STRUCT" if current is not None and self.admit(current) is not None else "EMPTY"
            elif kind == "PROPERTY" and step_kind == INDEX and values.is_array(prop):
                kind, current = "ELEMENT", value
            else:
                what = "a value" if kind in ("PROPERTY", "ELEMENT") else "empty (None)"
                raise _NotFound("NOT_A_STRUCT" if kind in ("PROPERTY", "ELEMENT") else "NULL",
                                "%s is %s; the path cannot continue past it." % (self.resolved(), what),
                                resolved=self.resolved())
            self.done += 1
        return kind, current, owner, prop

    # Answers -------------------------------------------------------------------------------------------------

    def read(self):
        from . import native_programs
        if native_programs.runtime_is_staging():
            return {"found": False, "code": "STAGING", "message": STAGING}
        try:
            self.root, self.steps = parse(self.args["path"])
        except DataPathError as error:
            return {"found": False, "code": error.code, "position": error.position, "message": str(error)}
        try:
            self.access = Access(self.window, self.deadline, self.epoch_owner, backend=self.backend)
            self.rows = Rows(None, None, self.deadline, access=self.access)
            try:
                try:
                    result = self.answer(*self.walk())
                except Missing as stop:
                    self.missing(stop)
            except _NotFound as stop:
                result = stop.answer
            self.access.check()
            return result
        except ReadUnavailable as stop:
            resolved = self.resolved()
            if stop.address is not None and stop.edge is not None:
                resolved = render(stop.address[0], (stop.address[1] + stop.address[2])[:stop.edge])
            return {"found": False, "code": stop.code, "message": stop.message, "resolved": resolved}
        finally:
            if self.access is not None:
                self.access.close()

    def one_property(self, kind, target, owner, prop):
        """The answer for a path that ends at a value: one property, or one element of an array."""
        result = {"found": True, "read": self.resolved(), "kind": "PROPERTY"}
        if kind == "EMPTY":
            return dict(result, property={"type": "POINTER", "value": None})
        row = self.rows.property_row(owner, prop, names=values.struct_names(owner.type), detail=True, public=True)
        if kind != "ELEMENT" or "withheld" in row or not isinstance(row.get("value"), list):
            return dict(result, property=row)
        flat, length = row["value"], int(row.get("length", 0))
        if not -length <= target < length or not -len(flat) <= target < len(flat):
            return {"found": False, "code": "INDEX_RANGE", "resolved": self.resolved(), "length": length,
                    "message": "This value has %d elements; index %d is not among the first %d." % (length, target, len(flat))}
        return dict(result, property={"name": prop.identifier, "type": prop.type, "index": target, "value": flat[target]})

    def answer(self, kind, target, owner, prop):
        if kind not in ("STRUCT", "COLLECTION"):
            return self.one_property(kind, target, owner, prop)
        offset, limit, depth = self.args["offset"], self.args["limit"], self.args["depth"]
        result = {"found": True, "read": self.resolved(), "kind": kind}
        if kind == "STRUCT":
            rows, total, more = self.rows.struct_rows(target, offset, limit, depth=depth, budget=MAX_CHARS)
            result.update(struct=target.type.identifier, total=total, offset=offset, returned=len(rows),
                          properties=rows)
            if target.owner.exists and not target.owner.is_self and not target.is_data_root:
                result["owner"] = self.rows.reference(target, relation="owner")
        else:
            rows, total, more = self.rows.item_rows(target, offset, limit, depth=depth, budget=MAX_CHARS)
            fixed = prop.fixed_type if prop is not None else None
            result.update(struct=fixed.identifier if fixed else None, length=total, offset=offset,
                          returned=len(rows), items=rows)
            own = self.rows.collection_properties(target)
            if own:
                result["collection_properties"] = own
        next_offset = offset + len(rows) if more else None
        result.update(next_offset=next_offset, truncated=more)
        if more:
            result["note"] = NEXT_PAGE % next_offset
        return result


def execute(window, arguments):
    """Main-thread ``angelo_read_data`` dispatch. Returns an answer; raises only for malformed arguments."""
    import bpy
    from . import scene_queries
    args = validate_read("angelo_read_data", arguments)
    scene_queries._main_thread()
    result = _Reader(bpy, window, args, scene_queries.object_handle).read()
    result.update(read_only=True, scene_changed=False)
    return result
