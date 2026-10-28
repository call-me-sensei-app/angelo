# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The concurrent sessions the session rail shows: Angelo's own chat (the lead) and each worker chat.

Evidence: the maintainer (2026-10-24): "the UI should be thumbnail of each concurrent session on the left and the
active one is the one showing on full screen. And these thumbnail are screenshot of current state so user can
follow along." So every session gets a tile, the main one included, and switching a tile switches the view and the
chat together (``switch``).

This module keeps what the rail draws from, and nothing GPU: the sessions in order, which one is the lead, each
worker's live part (``live_view``, read when its chat binds and again after its worker saves) and which
pictures are out of date (``stale``; ``rail_pictures`` draws them). ``worker_routing`` calls ``bound``/``unbound``;
``remote_executor.part_listeners`` calls ``_part_saved`` after each worker save.
"""

import time

_order = []         # Worker chat session ids, in the order their workers started.
_ended = set()      # Worker chats whose worker is gone (unbound): their tile stays, showing the last saved part.
names = {}          # worker session id -> its short name on the rail (the part it builds), as the lead gave it.
sizes = {}          # worker session id -> [x, y, z] metres its newest save shows, as the worker measured it.
boxes = {}          # worker session id -> its world bounds [[min], [max]] in metres, measured with its size.
_merging = set()    # Worker sessions whose part is being merged into Angelo's own document (the lead's merge).
_merged = {}        # Worker session -> the part file its merge brought in: Merged while that part is in the file.
PART_KEY = "_angelo_parts_merged"   # The document scene's record of the parts merged into it (digests, no paths).
progress = {}       # worker session id -> {"steps": [titles], "done": n}: its own plan, as it reports it.
lead = None         # The session that is Angelo's own document chat (the main tile); None until a worker starts.
stale = {}          # session id -> time.monotonic() its picture went out of date (``rail_pictures`` clears it).
pending = {}        # worker session id -> time.monotonic() of its newest save not yet reloaded (``live_schedule``).
reloaded = {}       # worker session id -> time.monotonic() of its last reload.
last_input = 0.0    # time.monotonic() of the artist's last input event (the rail's input handler notes it).
held = False        # Fixtures: a tool holds the window (the timer reads it from the windows instead).
costs = {}          # worker session id -> seconds its last read and the draws after it took (live_schedule paces).
version = 0         # Bumped on every change the rail draws (tiles, states, pictures): ``rail_redraw`` reads it.
data_generation = 0  # Bumped by each change that can add or remove a collection (depsgraph update, Undo, Redo, load).
_present = [None, frozenset()]   # ((generation, a scene's session id), the parts it records as merged) as last read.
refresh_seconds = []   # Fixtures: each worker save's live reload + sync, seconds; the newest 256.
refresh_total = [0, 0.0]   # Every reload this launch and their seconds, never capped (fixtures' totals).
last_pumped = []    # The sessions the last ``pump`` read.


def _live_view():
    """``live_view``, or None where there is no Blender (host tests of the routing)."""
    try:
        from . import live_view
    except ImportError:
        return None
    return live_view


def _bump(session_id=None):
    global version
    version += 1
    if session_id is not None:
        stale.setdefault(session_id, time.monotonic())


def workers():
    """Worker chat session ids still shown, in start order."""
    return list(_order)


def sessions():
    """Every tile's session, the lead first. Empty while no worker exists (the rail is not drawn then)."""
    return ([lead] if lead is not None and lead not in _order else []) + list(_order) if _order else []


def is_worker(session_id):
    return session_id in _order


def ended(session_id):
    return session_id in _ended


def bound(controller, executor):
    """A chat was bound to a worker (``worker_routing.bind``): its live scene now, its tile from now on."""
    global lead
    from . import parallel_access
    if not parallel_access.enabled():
        return   # Parallel edit is not here (below the bar, or launched without it): no tile, no live part to read.
    session_id = controller.state["id"]
    if lead is None or lead == session_id:
        lead = _current_lead(exclude=session_id)
        if lead is not None:   # The main tile's first picture now, never only after the artist's next edit.
            stale.setdefault(lead, time.monotonic())
    part = getattr(getattr(executor, "worker", None), "part", None)
    live_view = _live_view()
    if part is not None and live_view is not None:
        live_view.attach(session_id, part)
    if session_id not in _order:
        _order.append(session_id)
    _ended.discard(session_id)
    _note_size(session_id, executor)
    _bump(session_id)


def _note_size(session_id, executor):
    """The newest save's size; none reported (an older worker) is unknown, never the previous save's (P1-4 L11)."""
    measured = getattr(executor, "part_layout", None) or {}
    for table, value in ((sizes, measured.get("size")), (boxes, measured.get("bounds"))):
        if value is not None:
            table[session_id] = value
        else:
            table.pop(session_id, None)


def unbound(controller):
    """The chat left its worker: the tile stays (its last saved part, marked ended) until it is dismissed."""
    session_id = controller.state["id"]
    if session_id in _order:
        _ended.add(session_id)
        _bump(session_id)


def merging(session_id):
    """The lead's merge of this worker's part has started (main thread): its tile says so until ``merged``."""
    if session_id in _order:
        _merging.add(session_id)
        _bump(session_id)


def merge_failed(session_id):
    """The merge did not happen: the tile shows the worker as it is again (the lead's chat says why)."""
    if session_id in _merging:
        _merging.discard(session_id)
        _bump(session_id)


def merged(session_id, part=None):
    """The part file ``part`` is in Angelo's document (one Undo step): the tile says Merged while a collection merged
    from it is in the file, so a Ctrl+Z that takes the part out takes the word too (break-it P1-6 M3)."""
    if session_id in _order:
        _merging.discard(session_id)
        _merged[session_id] = part
        _bump(session_id)


def is_merging(session_id):
    return session_id in _merging


def is_merged(session_id):
    if session_id not in _merged:
        return False
    return _merged[session_id] is None or part_in_file(_merged[session_id], lead_of(session_id))


def lead_of(session_id):
    """The lead chat of ``session_id``: a worker's, the lead that started it (``worker_sessions``; else the rail's), a
    lead's its own; None where there is no chat (no Blender). Its merges land in its own scene (``_document_scene``)."""
    try:
        from . import ui, worker_sessions
        found = worker_sessions._lead_of(session_id) or (lead if session_id in _order else session_id)
        return ui._controllers.get(found)
    except (ImportError, AttributeError):
        return None


def _part_key(path):
    """A part's record in Angelo's file: a digest of its part file's path, so the file holds no path of the person's
    (break-it E2E-20 R2-L-5: the path named their home folder and the lead chat)."""
    import hashlib
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:32]


def parts_merged(paths, scene):
    """The merge brought these part files into ``scene`` (the lead's document scene), inside its own Undo step, as
    soon as they are in it (break-it E2E-20 R4-L-1: a fault before the record left a part unrecorded): the scene
    records them, so the fact travels with the file (save, open, Restore, Undo restore), an Undo of the merge
    takes it out, and nothing done to the merged objects afterwards loses it. Break-it E2E-20 H-1: a runtime id every
    file read renews; R2-M-1 and R3-M-2: records on the collection, then on its objects, were lost when the person
    deleted the collection or joined an object into another; R3-L-1: object records went into exports. Returns what
    puts the earlier record back, for a refusal that removes everything the merge added."""
    record = scene.get(PART_KEY)
    earlier = dict(record) if record else None
    scene[PART_KEY] = dict(earlier or {}, **{_part_key(path): 1 for path in paths})
    data_changed()

    def restore():
        if earlier is not None:
            scene[PART_KEY] = earlier
        elif PART_KEY in scene:
            del scene[PART_KEY]
        data_changed()
    return restore


def part_in_file(path, lead):
    """Whether this part file was merged into ``lead``'s own scene (``lead``: its chat), and that merge is in it now."""
    return _part_key(path) in _in_file(lead)


def _document_scene(lead):
    """``lead``'s own scene, the one its merges land in (``document_scene.scene_of``), or None (gone): never the scene
    the window shows now (break-it E2E-20 R5-M-1: the person may show another), nor whichever a timer or another
    window's operator has as context (R3-M-1). A chat keyed while no window showed a scene (``ui.document_key``'s 0, a
    background Angelo) has its context's."""
    from . import document_scene
    document = getattr(lead, "document", None)
    if document is not None and not document[1]:
        import bpy
        return bpy.context.scene
    return document_scene.scene_of(lead)


def refuse_merged_again(number, path, lead):
    """A part merged into this scene merges again only when the merge says ``again``: a second merge of one worker made
    a second copy unasked (phase 1's live Sol trial on combined, 2026-10-25). The record outlives what Undo can reach
    (32 steps, a Restore) and whatever the person does to the copies, so ``again`` is the way to place it anew (break-it
    E2E-20 R4-M-2); an Undo of the merge takes the record out."""
    if part_in_file(path, lead):
        from .refusal_codes import RefusalError
        raise RefusalError("Worker %d's part was merged into this scene before; a merge that sets this part's \"again\" "
                           "adds another copy of it (one merge can place a part several times)" % number,
                           code="VALIDATION")


def _in_file(lead):
    """The parts ``lead``'s own scene records as merged. In a window, read again only after the file changed
    (``data_changed``) or for another scene, never once per tile per draw (P1-7 N3); a background Angelo draws no rail
    and fires no redraw's handlers, so it reads the scene each time (break-it E2E-20 H-1: its cache answered stale)."""
    try:
        import bpy
        scene = _document_scene(lead)
        if scene is None:
            return frozenset()   # Its scene is gone (an Undo brings it back): nothing of its merges is in the file.
        key = (data_generation, scene.session_uid)
        if bpy.app.background or _present[0] != key:
            record = scene.get(PART_KEY)
            _present[:] = [key, frozenset(str(name) for name in (record.keys() if record else ()))]
        return _present[1]
    except (ImportError, AttributeError, ReferenceError):
        return frozenset()


def data_changed(*_):
    """The file may have gained or lost a collection: ``is_merged`` reads the collections again. Also the handler of
    every change that can (``register``: a load, Undo, Redo, a depsgraph update), in a window and in background."""
    global data_generation
    data_generation += 1


data_changed._bpy_persistent = True   # bpy.app.handlers.persistent's mark, without bpy at import: survives a load.
_FILE_EVENTS = ("load_post", "undo_post", "redo_post", "depsgraph_update_post")


def note_progress(session_id, done, steps=None):
    """A worker reported its plan (``steps``, once) and how many are finished; its tile counts them. True when shown."""
    if session_id not in _order:
        return False
    from .worker_spec import clean_steps
    entry = progress.setdefault(session_id, {"steps": [], "done": 0})
    if steps and clean_steps(steps):
        entry["steps"] = clean_steps(steps)
    entry.update(done=min(int(done), len(entry["steps"])) if entry["steps"] else int(done), at=time.monotonic())
    _bump(session_id)
    return bool(entry["steps"])


def progress_of(session_id):
    """``(done, planned)`` of a worker's own plan; None before it gave one, and while its count is behind its work (a
    save since its last report, unless all were done: Director capture, a built part read "0 of 3 steps")."""
    entry = progress.get(session_id) or {}
    current = entry.get("done", 0) >= len(entry.get("steps", ())) or entry.get("saved", 0) <= entry.get("at", 0)
    return (entry["done"], len(entry["steps"])) if entry.get("steps") and current else None


def current_step(session_id):
    """The title of the step a worker is on (the first not done), or ""."""
    entry = progress.get(session_id)
    if not entry or entry["done"] >= len(entry["steps"]) or progress_of(session_id) is None:   # Behind: none.
        return ""
    return entry["steps"][entry["done"]]


def dismiss(session_id):
    """Remove a worker chat's tile and live scene (its chat and part file stay)."""
    live_view = _live_view()
    if session_id in _order:
        _order.remove(session_id)
    _ended.discard(session_id)
    names.pop(session_id, None)
    sizes.pop(session_id, None)
    boxes.pop(session_id, None)
    _merging.discard(session_id)
    _merged.pop(session_id, None)
    progress.pop(session_id, None)
    stale.pop(session_id, None)
    pending.pop(session_id, None)
    reloaded.pop(session_id, None)
    if live_view is not None:
        live_view.detach(session_id)
    _bump()


def _current_lead(exclude=None):
    """The active chat when it is not a worker's: Angelo's own document chat."""
    active = _active()
    return active if active is not None and active != exclude and active not in _order else lead


def note_active(session_id):
    """The chat shown now (``ui.show_session``): a non-worker chat becomes the lead tile while workers run."""
    global lead
    if session_id is not None and session_id not in _order and session_id != lead:
        lead = session_id
        _bump(session_id)


def lead_changed():
    """Angelo's own scene changed (a depsgraph update, coalesced by the rail): the lead's picture is out of date."""
    if lead is not None and _order:
        _bump(lead)


def _part_saved(executor):
    """A worker saved its part: its live part is read again when ``live_schedule`` allows."""
    state = getattr(getattr(executor, "studio_context", None), "state", None)
    session_id = state.get("id") if isinstance(state, dict) else None
    if session_id not in _order:
        return
    _note_size(session_id, executor)
    pending.setdefault(session_id, time.monotonic())
    progress.get(session_id, {})["saved"] = time.monotonic()   # Work since its last report: its count is behind.
    _ensure_timer()


def note_input(now=None):
    """The artist did something (an event reached the rail's input handler)."""
    global last_input
    last_input = time.monotonic() if now is None else now


def reload(session_id):
    """Read one worker's newest saved part again now; True when it did."""
    live_view = _live_view()
    pending.pop(session_id, None)
    if live_view is None or session_id not in _order:
        return False
    try:
        spent = live_view.refresh(session_id)
    except ValueError as error:   # The file is no part (no scene): reading it again would say the same.
        print("Angelo: live view refresh:", error)
        return False
    except (RuntimeError, OSError, ReferenceError) as error:   # A file mid-replace, a closed part: later, then.
        print("Angelo: live view refresh:", error)
        pending.setdefault(session_id, time.monotonic())
        return False
    reloaded[session_id] = time.monotonic()
    costs[session_id] = spent   # The draws that follow add theirs (note_draw).
    try:
        from . import live_view_draw
        if live_view_draw.view_of(session_id).auto:
            live_view_draw.refit(session_id)   # Framed on the part while the artist has not moved its view.
    except ImportError:
        pass
    refresh_seconds.append(spent)
    del refresh_seconds[:-256]
    refresh_total[0], refresh_total[1] = refresh_total[0] + 1, refresh_total[1] + spent
    _bump(session_id)
    return True


def note_draw(session_id, seconds):
    """A picture or live view of a worker's part was drawn after its read: part of what that read costs."""
    if session_id in costs:
        costs[session_id] += seconds


def pump(now=None, active=None, holding=None):
    """One scheduling step: reload the waiting parts ``live_schedule.batch`` picks (one, or more that fit in a frame
    together). Returns the first session read, or None; ``last_pumped`` holds them all."""
    from . import live_schedule
    now = time.monotonic() if now is None else now
    holding = held if holding is None else holding
    ready = {session: when for session, when in pending.items() if not _answering(session)}
    chosen = live_schedule.batch(ready, reloaded, active, now, now - last_input, holding, costs)
    last_pumped[:] = [session for session in chosen if reload(session)]
    return chosen[0] if chosen else None


def _answering(session_id):
    """The worker of this chat is answering a request that may write its part file (an edit; ``worker_dispatch``:
    Angelo no longer waits for it): its read waits, while the other parts' reads go on. Reads, pictures and a turn's
    preparation never hold the part back (break-it P1-8 P2: in a plan of an edit then a check, the edit reached the tile
    only after the check). Called by name, so a rename fails at import, never quietly (break-it P1-13 U5)."""
    try:
        from . import ui, worker_dispatch
    except ImportError:
        return False
    chat = ui._controllers.get(session_id)
    return chat is not None and worker_dispatch.writing(chat)


def flush():
    """Reload every waiting part now (background runs and tests, where no timer runs)."""
    for session_id in list(pending):
        reload(session_id)


def _active():
    try:
        from . import ui
        return ui._sessions.active if ui._sessions is not None else None
    except (ImportError, AttributeError):
        return None


def _tick():
    from . import live_schedule
    if not pending:
        return None
    try:
        pump(active=_active(), holding=_held_now())
        if last_pumped:   # Their pictures in this tick, and the rail drawn again: on screen without waiting.
            from . import rail_pictures, rail_redraw
            rail_pictures.refresh()
            rail_redraw.tick()
    except Exception as error:   # noqa: BLE001 - a timer that raises is dropped by Blender; this one keeps going.
        print("Angelo: live view schedule:", error)
    return live_schedule.next_poll(bool(last_pumped), bool(pending))


def _held_now():
    """A tool, a navigation or a modal edit holds one of Angelo's windows (``input_policy.canvas_blocking``), the
    interface is locked, or a render, bake or simulation job runs: a read and a picture both wait, however long
    (break-it P1-1 H1, P1-2 H2). ``work_guard._render`` is the one job check: Blender's own kinds and Angelo's
    ``_angelo_jobs`` for point caches, geometry-nodes bakes and fluids, which ``bpy.app.is_job_running`` cannot see."""
    try:
        import bpy
        from . import input_policy, live_windows, work_guard
        manager = bpy.context.window_manager
        if getattr(manager, "is_interface_locked", False):
            return True
        if any(input_policy.canvas_blocking(window) for window in live_windows.every_window(manager)):
            return True
        if work_guard._render(bpy):
            return True
        from _bpy import _angelo_jobs
        return any(_angelo_jobs.progress(scene, "FLUID") is not None for scene in bpy.data.scenes)
    except (ImportError, AttributeError, RuntimeError, TypeError, ValueError) as error:
        if _held_error[0] != repr(error):   # Said once per kind of failure, never every tick (P1-3 L9).
            _held_error[0] = repr(error)
            print("Angelo: live view held, cannot tell whether a job runs:", error)
        return True   # Unknown is held: a read can wait, a race cannot be undone.


_held_error = [None]


def _ensure_timer():
    try:
        import bpy
    except ImportError:
        return
    if bpy.app.background:
        return   # No timers run in a background Blender: ``flush`` reloads there.
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0, persistent=True)   # A file load keeps waiting reads.


def register():
    from . import live_view, remote_executor
    live_view.register()
    if _part_saved not in remote_executor.part_listeners:
        remote_executor.part_listeners.append(_part_saved)
    _file_handlers(add=True)


def _file_handlers(add):
    try:
        import bpy
    except ImportError:
        return
    for name in _FILE_EVENTS:
        handlers = getattr(bpy.app.handlers, name)
        if add and data_changed not in handlers:
            handlers.append(data_changed)
        elif not add and data_changed in handlers:
            handlers.remove(data_changed)


def unregister():
    global lead
    from . import live_view, remote_executor
    if _part_saved in remote_executor.part_listeners:
        remote_executor.part_listeners.remove(_part_saved)
    _file_handlers(add=False)
    live_view.unregister()
    try:
        import bpy
        if bpy.app.timers.is_registered(_tick):
            bpy.app.timers.unregister(_tick)
    except ImportError:
        pass
    for table in (_order, _ended, names, sizes, boxes, _merging, _merged, progress, stale, pending, reloaded, costs,
                  refresh_seconds, last_pumped):
        table.clear()
    refresh_total[:], _present[:], lead = [0, 0.0], [None, frozenset()], None
