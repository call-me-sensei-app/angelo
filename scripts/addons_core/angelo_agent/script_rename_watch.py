# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""While a reviewed script runs: the names it asked for and the data-blocks it brought in from files.

``script_renames`` judges a name by its form when it does not know the name the script asked for. Two things tell
it more, and both are only known while the script runs (``watching``, around the script's ``exec`` alone, put back
however the script ends):

* **Names a script assigns** (``Names.asks``): ``bpy.types.ID.__setattr__`` observes every ``<data-block>.name = ...``
  (and ``setattr(block, "name", ...)``) and records the name asked for beside the name Blender gave. A rename into a
  taken name can leave a name unchanged (``objects["Gate.001"].name = "Gate"`` while another "Gate" exists keeps
  "Gate.001"), which no comparison of names can see, and a numbered name the script chose itself is then known to be
  its own. A data-block costs this one call of Python per attribute the script sets on it.
* **Data-blocks brought in from files** (``Names.from_files``): an operator that reads a file (one with a
  ``filepath``, ``directory``, ``files`` or ``filename`` property: an import, ``wm.append``, ``image.open``) and an
  append or link through ``bpy.data.libraries.load`` name what they create after the file, which may itself hold
  "Rock" and "Rock.001". The session UIDs of the covered kinds are read before and after each such call
  (``bpy.ops`` builds operators through its module global ``_op_create_function``, as ``scripts/modules/bpy/ops.py``
  defines it; ``libraries.load`` is a method of ``bpy.types.BlendDataLibraries``); what the call created is never
  judged by its name's form.

An observer that cannot be put in place, or a reading that fails, leaves ``Names.unsure`` set, and the result then
states nothing. Main thread only.
"""

import contextlib
from array import array

from .view_scene_check import COLLECTIONS, _Budget, _column

FILE_PROPERTIES = frozenset({"filepath", "directory", "files", "filename"})


class Names:
    """One script's names: the reading before it (``script_renames.before``) and what ``watching`` saw."""

    def __init__(self, reading):
        self.reading = reading
        self.asks = {}            # session UID -> (data-block, name asked for, name Blender gave), the last one.
        self.from_files = set()   # Session UIDs of data-blocks a file-reading call created.
        self.unsure = False       # An observer was missing or failed: nothing is stated.


def watching(bpy, names):
    """A context manager observing the script for ``names`` (a no-op without them)."""
    return contextlib.nullcontext() if names is None else _Watch(bpy, names)


def _uids(bpy):
    """Every session UID of the covered kinds, or None when one cannot be read."""
    found = set()
    for kind in COLLECTIONS:
        collection = getattr(bpy.data, kind, None)
        if collection is None:
            continue
        column = _column(collection, "session_uid", "q", 1, _Budget())
        if column is None:
            return None
        found.update(array("q", column))
    return found


class _Watch:
    def __init__(self, bpy, names):
        self._bpy, self._names, self._restore = bpy, names, []
        self.active = False   # Only while the script runs: a call it kept records into no finished script's names.

    def __enter__(self):
        self.active = True
        try:
            self._operators()
            self._library_loads()
            self._name_assignments()
        except Exception:  # noqa: BLE001  Optional evidence: the script runs as written, and nothing is stated.
            self._names.unsure = True
        return self

    def __exit__(self, *_exception):
        self.active = False
        while self._restore:
            try:
                self._restore.pop()()
            except Exception:  # noqa: BLE001  Never the script's failure.
                self._names.unsure = True
        return False

    def brought_in(self, run):
        """Run a file-reading call, recording the data-blocks it created (only while the script runs)."""
        if not self.active:
            return run()
        before = _uids(self._bpy)
        try:
            return run()
        finally:
            after = _uids(self._bpy)
            if before is None or after is None:
                self._names.unsure = True
            else:
                self._names.from_files.update(after - before)

    def asked(self, block, name):
        try:
            self._names.asks[block.session_uid] = (block, name, block.name)
        except Exception:  # noqa: BLE001  An ask that cannot be read is judged by the name's form.
            pass

    def _operators(self):
        ops = self._bpy.ops
        real, reads = ops._op_create_function, {}

        def create(module, function):
            made = real(module, function)
            key = (module, function)
            if key not in reads:
                try:
                    reads[key] = not FILE_PROPERTIES.isdisjoint(made.get_rna_type().properties.keys())
                except Exception:  # noqa: BLE001  No such operator: calling it fails as written.
                    reads[key] = False
            return _FileCall(made, self, module, function) if reads[key] else made

        ops._op_create_function = create
        self._restore.append(lambda: setattr(ops, "_op_create_function", real))

    def _library_loads(self):
        kind = self._bpy.types.BlendDataLibraries
        real, active = kind.__dict__["load"], [True]

        def load(*args, **kwargs):
            if not active[0]:   # Kept by the script past its end: whatever is installed now, never this watch again.
                return self._bpy.data.libraries.load(*args, **kwargs)
            # ``bpy.data.libraries`` binds a method of its type only when it is Blender's own (``pyrna``'s collection
            # getattr): this one is called unbound, so it fetches the bound original, as the script would have.
            kind.load = real
            try:
                bound = self._bpy.data.libraries.load
            finally:
                kind.load = load
            return _Load(bound(*args, **kwargs), self)

        def restore():
            active[0] = False
            kind.load = real

        kind.load = load
        self._restore.append(restore)

    def _name_assignments(self):
        kind = self._bpy.types.ID
        own = kind.__dict__.get("__setattr__")
        real = kind.__setattr__

        def setattr_(block, attribute, value):
            real(block, attribute, value)
            if attribute == "name":
                self.asked(block, value)

        kind.__setattr__ = setattr_
        self._restore.append(lambda: setattr(kind, "__setattr__", own) if own is not None
                             else delattr(kind, "__setattr__"))


class _FileCall:
    """A file-reading operator: called as written, its new data-blocks recorded."""

    def __init__(self, made, watch, module=None, function=None):
        self._made, self._watch, self._name = made, watch, (module, function)

    def __call__(self, *args, **kwargs):
        if not self._watch.active and self._name[0] is not None:   # Kept past its script: as bpy.ops builds it now
            # (not the observed callable), so a later script's watch, if any, sees it, and this one records nothing.
            return self._watch._bpy.ops._op_create_function(*self._name)(*args, **kwargs)
        return self._watch.brought_in(lambda: self._made(*args, **kwargs))

    def __getattr__(self, attribute):
        return getattr(self._made, attribute)

    def __repr__(self):
        return repr(self._made)


class _Load:
    """``bpy.data.libraries.load(...)``: its context, whose exit appends or links, recorded."""

    def __init__(self, inner, watch):
        self._inner, self._watch = inner, watch

    def __enter__(self):
        return self._inner.__enter__()

    def __exit__(self, *exception):
        return self._watch.brought_in(lambda: self._inner.__exit__(*exception))

    def __getattr__(self, attribute):
        return getattr(self._inner, attribute)
