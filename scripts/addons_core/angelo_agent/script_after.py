# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a reviewed script: the handles and revisions of what it changed, so the next step needs no lookup.

A script's result named what it added and modified, but not by handle, and
said nothing of the scene's settings revision. A step that continued with a
typed program therefore needed a search first (one model round), and since
the turn context states the scene's settings revision a program bound to it
after a script that touched the World or a render setting was refused as
stale: the host itself held the old revision.

``attach`` adds ``after`` to a completed script's result, on the main thread
where the script just ran:

* ``scene_settings``: the scene entry of the turn context (handle, settings
  revision, scene camera) as it is now; its revision is never left out;
* ``objects``: one row per object the script added or modified (the
  executor's own read-back, ``changes``), with its handle, its world-space
  ``bounds`` when it has geometry (``object_bounds``: its size and place, to
  1 mm), ``instances`` when it makes them (the world box of what they show; no
  ``bounds`` when it shows no geometry itself) and its exact revision, at most
  ``MAX_OBJECTS``. A read-back after an edit gives every row within
  the typed programs' input limits a revision (``revision_budget``:
  ``READ_BACK_SECONDS`` bounds only a pathological one). Rows left out are
  counted (``omitted``); an object without a revision states why
  (``revision_omitted``) and cannot be bound by inference. The rows are read
  as one pass (``revision_pass``): each revision is the one ``object_revision``
  gives, and the datablocks the objects share are read once. The rows, the
  Booleans and the motion below find their objects in one name index
  (``named``) instead of a walk of the scene per name.

A completed script also gets ``renamed_by_blender`` when Blender gave any data-block it created or named another
name than the one asked for ("Camera.001" beside the factory "Camera"): ``script_renames``, from the names
``names`` read before the script and observed while it ran (``script_rename_watch``).

It then adds ``live`` rows to ``booleans`` (``script_booleans.live``): each Boolean stack the script left on an
object it added or modified, or whose operand it changed, base mesh against evaluated mesh, after the rows of the
Booleans the script applied (the executor's ``script_booleans.Applied``).

The request context records these like a committed program's post-commit
revisions (``context_ledger.observe_committed``), so an inferred binding
later in the same turn is the script's own result and still fails closed on
anything changed after it. A failed or rolled-back script gets nothing.

Every finished script, completed or raised, also gets (``notes``):

* ``script``: its id (``script_store.script_id``), by which ``angelo_script`` returns its code and a later script
  patches or reruns it (``base`` and ``edits``);
* ``world``: what the scene's World shows now (``world_surface.shows``), when the script changed it
  (``world_before`` is the World's fingerprint from before the script): the Sky type and sun elevation, or
  the flat colour and its colour family, so a silent fallback is never mistaken for the sky it replaced;
* ``api_notes``: the Blender 4.x names its code used that 5.2 renamed or removed (``script_api_notes``).

Bounded, read-only, on the script path only: a typed program never pays for it.
"""

import contextlib

from . import script_api_notes, script_booleans, script_document, script_renames, visibility_facts, world_surface
from .object_bounds import InstanceFacts, states_own_box, world_bounds
from .scene_read_values import label
from .work_clock import WorkClock

MAX_OBJECTS = 24
_UNREADABLE = (ReferenceError, RuntimeError, AttributeError, TypeError, ValueError, KeyError)


def world_before(window):
    """The scene World's fingerprint before a script, or None when it cannot be read."""
    try:
        return world_surface.fingerprint(window.scene)
    except Exception:  # noqa: BLE001  Optional evidence: the script runs and reports without it.
        return None


def notes(window, result, code, world):
    """Add ``script`` (its id, script_store), ``world`` (when the script changed it) and ``api_notes`` to a
    finished script's ``result``."""
    from .script_store import script_id
    result["script"] = script_id(code)  # Fetched (angelo_script) or patched (base and edits) by this id.
    try:
        shown = world_surface.changed(window.scene, world)
    except Exception:  # noqa: BLE001  Optional evidence: the script's own result stands without it.
        shown = None
    if shown is not None:
        result["world"] = shown
    result.update(script_api_notes.notes(code))
    return result


def named(scene):
    """``scene``'s objects by name, the first of each name as ``scene.objects.get`` finds it, from one walk: each get
    walks the scene's objects itself (about 15 µs at 1,536), and one read-back looks up a hundred names or more."""
    index = {}
    for obj in scene.objects:
        index.setdefault(obj.name, obj)
    return index


def collecting(bpy, scene):
    """The objects Blender's depsgraph updates show changed while a script runs (``change_feed.collecting``)."""
    from .change_feed import collecting
    handlers = getattr(getattr(getattr(bpy, "app", None), "handlers", None), "depsgraph_update_post", None)
    return collecting(handlers=handlers if isinstance(handlers, list) else None, scene=scene)


class Before:
    """What a script's result is measured against, read just before it runs: the World it starts from
    (``world_before``) and the names already taken (``script_renames.before``); while the context is entered, the
    objects Blender's updates show the script changed (``collecting``)."""

    def __init__(self, window, bpy):
        self.world, self.names = world_before(window), script_renames.before()
        self.touched, self._collecting = None, collecting(bpy, window.scene)

    def __enter__(self):
        self.touched = self._collecting.__enter__()
        return self

    def __exit__(self, *exception):
        self._collecting.__exit__(*exception)
        return False

    def attach(self, window, result, code):
        """``attach`` with what was read before and while the script ran."""
        return attach(window, result, code=code, world=self.world, names=self.names, touched=self.touched)


class Observing:
    """While the script's own code runs, and only then: the names it assigns and what files it reads bring in
    (``script_rename_watch``, for ``renamed_by_blender``; only with a ``Before``'s names) and each Boolean it applies,
    measured before and after (``script_booleans.Applied``). ``bpy.ops`` is put back however the script ends.
    A script that replaced the document ends in ``script_document.Replaced`` instead, with ``output``'s text."""

    def __init__(self, bpy, prior=None, output=None):
        from . import script_rename_watch
        self.applied = script_booleans.Applied(bpy)
        self._watch = script_rename_watch.watching(bpy, getattr(prior, "names", None))
        self._stack, self._output, self._document = None, output, script_document.Watch(bpy)

    def __enter__(self):
        self._document.start()   # Before the script: whether it replaces the document (load_post).
        try:
            stack = contextlib.ExitStack()
            with stack:
                stack.enter_context(self._watch)
                stack.enter_context(self.applied)
                self._stack = stack.pop_all()
        except BaseException:
            self._document.stop()   # No handler outlives a script that never started.
            raise
        return self

    def __exit__(self, kind, error, trace):
        stack, self._stack = self._stack, None
        try:
            return stack.__exit__(kind, error, trace) if stack is not None else False
        finally:   # bpy.ops is back; nothing held from before a replaced document is read again (script_document).
            if self._document.stop():
                output = "" if self._output is None else self._output.getvalue()
                raise script_document.Replaced(output, None if error is None or isinstance(
                    error, script_document.Replaced) else type(error).__name__)


def attach(window, result, clock=None, *, code=None, world=None, names=None, touched=None):
    """Add ``after`` (and ``renamed_by_blender``, from ``script_renames.before``'s ``names``) to a completed
    script's ``result`` in place, and ``notes`` to any finished one; returns it. ``touched``: the names
    ``collecting`` gathered while it ran."""
    if isinstance(result, dict) and code is not None:
        notes(window, result, code, world)
    changes = result.get("changes") if isinstance(result, dict) else None
    if not isinstance(changes, dict) or result.get("success") is not True or result.get("error"):
        return result
    script_renames.attach(result, names)  # Optional evidence of its own, outside the read-back's budget below.
    from . import scene_queries, turn_context
    from .revision_budget import READ_BACK_SECONDS
    from .revision_pass import RevisionPass
    clock = WorkClock() if clock is None else clock
    started, stats = clock(), {"revisions": 0}
    names = [row.get("name") for key in ("added", "modified") for row in changes.get(key) or ()
             if isinstance(row, dict)]
    index = None   # The scene's objects by name (named), for the rows, the Booleans and the motion below.
    try:
        scene_queries.invalidate(window)  # A script is an edit of unknown extent.
        scene = window.scene
        settings = turn_context._scene_settings(scene)  # Never left out (revision_budget).
        rows, depsgraph = [], getattr(getattr(window, "view_layer", None), "depsgraph", None)  # Evaluated boxes.
        instances, hidden = InstanceFacts(depsgraph), visibility_facts.Pass(getattr(window, "view_layer", None))
        index = named(scene)
        revisions = RevisionPass(window)   # One pass: nothing is edited between the rows' reads.
        for name in dict.fromkeys(name for name in names if isinstance(name, str)):
            obj = index.get(name)
            if obj is None:
                continue
            if len(rows) == MAX_OBJECTS:
                break
            row = {"name": label(obj.name), "type": obj.type, "handle": scene_queries.object_handle(obj),
                   **hidden.entry(obj)}  # How it is hidden, if it is: it exists, wherever it shows.
            bounds = world_bounds(obj, depsgraph=depsgraph)  # Its size and place in the world (object_bounds).
            fact = instances.of(obj)  # What its instances show: its own box leaves them out.
            if bounds is not None and states_own_box(fact):
                row["bounds"] = bounds
            if fact is not None:
                row["instances"] = fact
            turn_context._revision(window, obj, row, started, clock, stats, READ_BACK_SECONDS,
                                   "cooperative_read_back_budget", revisions)
            rows.append(row)
        after = {"scene_settings": settings, "objects": rows}
        omitted = len(set(names)) - len(rows)
        if omitted > 0:
            after["omitted"] = omitted
        result["after"] = after
    except _UNREADABLE:
        result.pop("after", None)  # Optional evidence: the script's own result stands without it.
    # Boolean stacks the script left live, after the ones it applied (script_booleans); never raises.
    script_booleans.live(window, result, [name for name in dict.fromkeys(names) if isinstance(name, str)],
                         named=index)
    motion(window, result, changes, touched, index)
    return result


def motion(window, result, changes, touched=None, index=None):
    """Add ``motion`` (``motion_facts.after_edit``) for the objects the script added or changed that move in time:
    those its change list names, then those Blender's own updates show it changed (``touched``, and its pending
    update evaluated here, as the read would first), which the change list's caps can leave out. ``index``: the
    scene's objects by name (``named``), when the caller built it."""
    from .change_feed import collecting
    from .motion_facts import after_edit, changed_names
    try:
        names = changed_names(changes)
        if touched is not None:
            with collecting(touched):
                window.view_layer.update()  # The script's own pending update: Sampler.open would run it anyway.
            names += sorted(set(touched) - set(names))
        found = after_edit(window, names) if index is None else after_edit(
            window, objects=[index[name] for name in names if index.get(name) is not None])
    except Exception:  # noqa: BLE001  Optional evidence: the script's own result stands without it.
        found = None
    if found:
        result["motion"] = found
