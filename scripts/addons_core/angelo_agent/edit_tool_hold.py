# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own work on the person's scene waits while the person's operation is in progress.

A modal operation (``input_policy.editing_tools``: Blender's own "not in a valid state to save" rule, any window's
modal operator without MODAL_PASSIVE that is not Angelo's) keeps its edit half-made until it ends. Python reading
the object under it can drop that edit: on stock Blender 5.3.0 a read-only ``bmesh.from_edit_mesh`` read of the
edited object while an edge crease ran lost the crease when the tool was confirmed (learn-l4, 2026-11-13), and a
whole-document snapshot taken then saves a state Blender itself refuses to autosave. Angelo reads, edits and
snapshots the scene on Blender's main thread between the operation's events, so each such step waits here:

* **The tool pump** (``Controller._pump_tools``, ``hold``): no tool call, approval, native program poll or commit,
  or continuation observation runs. Calls keep their place; their lifetimes stop running while they wait (as
  ``document_scene.hold`` does for a scene not shown), never turned into an expiry, a cancel or a failure. The
  provider's own waits pause too (``tool_deadlines.held``).
* **A call run directly** (``Controller._execute_tool``, ``wait``): a person's Approve click, or any route that
  reaches execution, stops before anything of the call runs (its click mark kept); execution returns
  ``EXECUTION_WAITING`` and the Undo operator pushes no step. The pump applies it again through the same approval
  route once the operation has ended (``resume``), as ``merge_hold`` does for a merge.
* **Snapshots and turn starts**: a voice turn's snapshot event waits with every event after it (``defers``); a
  message, Angelo's follow-up, a goal round or a worker-done event waits for readiness (``console.send_message``,
  ``chat_pending``, ``worker_resume._blender_busy``); an external app's task start gets its task at once and starts
  once the operation ends; Accept on a retained preview and Restore say to finish the operation first.
* **Every other main-thread callback** (``guarded``, the one choke point, or a ``ticked`` function whose data stages
  hold): a job's start and steps, the texture-set bake chain, pending imports, Flow and Media Studio work, the File >
  Export > Web wait. A person's own job submit queues and their Library Add defers; the writers' last-line refusal
  (``refuse_under``) applies inside Angelo's own work only. ``test_native_edit_tool_inventory`` lists every timer,
  handler, msgbus subscription, draw handler, deferral-helper call and modal operator as guarded or reviewed.
* Any other message delivery is retried on Blender's timer, keeping its Stop count (``deferred``).

A hold is never silent: the chat's live row says "Waiting for you to finish {tool}" while that chat has work held
(its tray included), and the status bar says "Angelo is waiting for you to finish {tool}" wherever work waits
(``edit_tool_status``). A call held before its first read tells the model so once it ran (``WAITED``); an external
app's pending answer names the operation (``seen``). A worker Blender's chat reads its own document in its own process
and never waits here.

A modal operation Blender drives from a job (a bake or render, the person's or Angelo's own) ends only on the first
event after its job; while one is in progress its window gets a slow timer event (``_nudge``). A file load or
unregister removes those timers (``clear``).
"""

import queue
import time

from .ui_text import iface_, n_

WAITING = n_("Waiting for you to finish {tool}")   # The live row while a chat's work waits for the person.
UNREADABLE = n_("the tool")   # input_policy.UNREADABLE_TOOL: an operation assumed where windows could not be read.
WAITED = ("The person was using {tool} in Angelo when this call was due, so it waited until they "  # i18n: model-facing
          "finished using it; anything it read or changed is from after that.")   # i18n: read by the model
EXECUTION_WAITING = {"tool_waiting": True}   # What execution returns for a call that waits: nothing ran (no Undo step).
RETRY_SECONDS = 0.1   # A deferred delivery asks again this often while the operation is in progress.
NUDGE_SECONDS = 0.5   # A window with an operation in progress gets a timer event this often (``_nudge``).
SHOW_AFTER = 1.0   # Seconds a hold with waiting work lasts before the status bar names it (``edit_tool_status``).
_nudges = {}   # window as_pointer() -> its timer (never the window itself: live_target)


def _window_manager():
    try:
        import bpy
    except ImportError:
        return None
    return getattr(getattr(bpy, "context", None), "window_manager", None)


def _nudge(window_manager, windows):
    """One slow window timer per window with an operation in progress, removed when none is. Its TIMER events reach
    the window's modal handlers, so a job-driven operation whose job has ended (Blender ends those on the first event
    after the job: the person's bake or render, and Angelo's own) ends with no person at the machine (break-it
    reviews 2026-11-13, M4, S4, F3)."""
    if window_manager is None or not hasattr(window_manager, "event_timer_add") or not windows and not _nudges:
        return
    from . import live_windows
    try:
        live = {window.as_pointer() for window in live_windows.every_window(window_manager)}
        wanted = {window.as_pointer(): window for window in windows if window is not None}
        for pointer in [pointer for pointer in _nudges if pointer not in wanted]:
            timer = _nudges.pop(pointer)
            if pointer in live:   # A closed window's timers were freed with it.
                window_manager.event_timer_remove(timer)
        for pointer, window in wanted.items():
            if pointer not in _nudges:
                _nudges[pointer] = window_manager.event_timer_add(NUDGE_SECONDS, window=window)
    except Exception:  # noqa: BLE001 - a nudge is help, never a reason to stop the predicate.
        pass


def _scan():
    """The person's operation in progress now, or None (``input_policy.operations``; unreadable fails closed).
    Every window with an operation in progress, Angelo's own included, is nudged. None outside Blender."""
    window_manager = _window_manager()
    if window_manager is None:
        return None
    from .input_policy import UNREADABLE_TOOL, operations
    try:
        found = operations(window_manager)
    except Exception as error:  # noqa: BLE001 - freed or unreadable RNA: wait rather than read under a running tool.
        found = [(None, UNREADABLE_TOOL, False)]
        _unreadable(error)
    if any(name == UNREADABLE_TOOL for _window, name, _own in found):
        from .input_policy import LAST_UNREADABLE
        holding = _unreadable(LAST_UNREADABLE[0])
        found = [entry for entry in found if entry[1] != UNREADABLE_TOOL or holding]
    else:
        _unreadable_since["at"] = None
    _nudge(window_manager, [window for window, _name, _own in found])
    named = [name for _window, name, own in found if not own]   # A named operation before an unreadable one (N2).
    _seen["tool"] = next((name for name in named if name != UNREADABLE_TOOL), named[0] if named else None)
    _status(_seen["tool"], window_manager)
    return _seen["tool"]


UNREADABLE_SECONDS = 10.0   # Past this, an unreadable window or operator no longer holds (break-it review 3, S6).
_unreadable_since = {"at": None, "reported": set()}   # Since when, and which kinds of error were reported.


def _unreadable(error):
    """An unreadable window or operator: reported once, and True while it may still hold. It holds (fail closed:
    never read under what might be a running tool) for ``UNREADABLE_SECONDS``, then no longer, so a broken add-on
    operator never holds Angelo's work forever; the console says why."""
    now = time.monotonic()
    if _unreadable_since["at"] is None:
        _unreadable_since["at"] = now
    if error is not None and type(error).__name__ not in _unreadable_since["reported"]:   # Each new kind once.
        _unreadable_since["reported"].add(type(error).__name__)
        from .pump_guard import report_defect
        report_defect("reading the person's operation in progress", error)   # i18n: console log line
    return now - _unreadable_since["at"] < UNREADABLE_SECONDS


_tick = {"depth": 0, "tool": None}
_waiting = {"since": None, "clear": None, "work": False, "shown": None}   # This hold: its first read, the last read
# before it (no operation then), whether work waits, the line shown.


def note_waiting():
    """Some of Angelo's work waits for the person's operation now (a guarded tick, a chat, the tray, a job, a task)."""
    _waiting["work"] = True


def status_tool():
    """The operation the status bar names (``edit_tool_status``), or None: a hold with waiting work, past a moment."""
    since = _waiting["since"]
    if since is None or not _waiting["work"] or time.monotonic() - since < SHOW_AFTER:
        return None
    return _seen["tool"]


def before_hold(at):
    """Whether ``at`` (a ``time.monotonic()`` reading, or None) is known to come before the person's operation began:
    the last read that found no operation was at or after it. Work queued then is what the operation defers; a change
    made after that read is the operation's own (a drag's), which waits for its end anyway (break-it review 7, S3).
    One gap by intent: work queued in the one scan interval between that read and the first that found the operation
    counts as the operation's own, so the status line stays silent for it (it still runs when the operation ends)."""
    clear = _waiting["clear"]
    return at is not None and _waiting["since"] is not None and clear is not None and at <= clear


def tool_name(tool):
    """``tool`` as the interface shows it: Blender's own operator name in its operator context."""
    return iface_(tool) if tool == UNREADABLE else iface_(tool, "Operator")   # i18n: Blender's operator msgctxt


def _status(tool, window_manager):
    """Track the hold for the status line and redraw the status bars when the line changes."""
    if tool is None:
        _waiting.update(since=None, work=False, clear=time.monotonic())
    elif _waiting["since"] is None:
        _waiting["since"] = time.monotonic()
    shown = status_tool()
    if shown != _waiting["shown"]:   # Shown, or cleared when the hold ends.
        _waiting["shown"] = shown
        _redraw_status_bars()


def _redraw_status_bars():
    """The status bar is a global area, outside ``Screen.areas``: the job notifier redraws it (``job_ui.redraw``),
    else the message bus key it subscribes to (``team_account_ui.redraw_status_bars``). Break-it review 5, F2."""
    try:
        from _bpy import _angelo_jobs
        _angelo_jobs.notify()
        return
    except (ImportError, AttributeError):
        pass
    try:
        import bpy
        bpy.msgbus.publish_rna(key=(bpy.types.Window, "view_layer"))
    except Exception:  # noqa: BLE001 - outside Blender, or no window yet: nothing to redraw.
        pass
_seen = {"tool": None}   # The last read, for another thread (the external service's pending answers): a name or None.


def seen():
    """The person's operation as the main thread last read it (any thread may ask), or None. Never an unreadable
    window: an app's deadline is not paused for that (S6)."""
    from .input_policy import UNREADABLE_TOOL
    return None if _seen["tool"] == UNREADABLE_TOOL else _seen["tool"]


class tick:
    """``with tick() as tool:`` one read of the person's operation for everything inside (the UI pump's chats, a
    guarded callback): the predicate walks every window's modal operators once per tick, not once per chat (S2)."""

    def __enter__(self):
        if _tick["depth"] == 0:
            _tick["tool"] = _scan()
        _tick["depth"] += 1
        return _tick["tool"]

    def __exit__(self, *_exc):
        _tick["depth"] -= 1
        return False


def _tool():
    """The person's operation in progress now (this tick's read inside ``tick``), or None."""
    return _tick["tool"] if _tick["depth"] else _scan()


def running():
    """Whether the person's operation is in progress now."""
    return _tool() is not None


def ticked(function):
    """``function`` (a timer callback that reaches many chats, the UI pump) inside one ``tick``."""
    def run(*args, **kwargs):
        with tick():
            return function(*args, **kwargs)
    run.__name__, run.__wrapped__ = getattr(function, "__name__", "ticked"), function
    return run


def _own():
    from .input_policy import LAST_LIVE, angelo_started
    before = set(LAST_LIVE[0]) if _tick["depth"] and LAST_LIVE[0] is not None else None   # This tick's read.
    return angelo_started(_window_manager(), before)


def guarded(function=None, retry=RETRY_SECONDS, waiting=False):
    """``function`` as a timer callback (or a card's ``after`` step) through the one choke point for Angelo's own
    main-thread work that may touch Blender data: while the person's operation is in progress it does nothing and
    asks again in ``retry`` seconds (a timer keeps its place); otherwise it runs, and a modal operator it starts is
    Angelo's own (``input_policy.angelo_started``). Never an app handler: its return value is ignored, so a held call
    would be lost (a handler only marks, and a guarded timer acts). ``test_native_edit_tool_inventory`` lists every
    timer, handler, msgbus subscription, draw handler and deferral helper call as guarded or reviewed. ``waiting``
    (True, or a function asked while held) says that real work of Angelo's waits here (a queued job, a pending drop,
    a running Flow): only then does the status bar name the operation (break-it review 5, F1). As a decorator with
    arguments: ``@guarded(waiting=...)``."""
    if function is None:
        return lambda inner: guarded(inner, retry, waiting)

    def run(*args, **kwargs):
        with tick() as tool:
            if tool is not None:
                try:
                    if waiting is True or callable(waiting) and waiting():
                        note_waiting()
                except Exception:  # noqa: BLE001 - a predicate that raises never drops the timer (review 6, N4),
                    note_waiting()   # and counts as work: unknown work is shown, never hidden (review 7, S4).
                return retry
            with _own(), angelo_work():
                return function(*args, **kwargs)
    run.__name__ = getattr(function, "__name__", "guarded")
    run.__wrapped__ = function
    return run


_angelo_work = [0]   # Depth of Angelo's own work on the main thread (a guarded callback, an agent tool or script).


class angelo_work:
    """``with angelo_work():`` around Angelo's own work (``guarded``, ``script_document.run``): what ``refuse_under``
    guards. The person's own click is never refused there (break-it review 4, M2): it queues or defers itself."""

    def __enter__(self):
        _angelo_work[0] += 1

    def __exit__(self, *_exc):
        _angelo_work[0] -= 1
        return False


def refuse_under(where, error=RuntimeError):
    """The last line at a writer (an import, a job's start, an export): inside Angelo's own work, raise ``error``
    rather than write while the person's operation is in progress. Every such route waits first, so reaching it held
    means a gate was missed (break-it reviews 3, S3, and 4, M2). Read by the model, never shown to the person."""
    if _angelo_work[0] and running():
        raise error("%s waits for the person's operation to end; nothing was changed" % where)   # i18n: model-facing


def clear():
    """A file load (``ui.document_load_pre``, while the old window manager is still valid) or unregister: remove the
    nudge timers and forget Angelo's own operators, which the load frees (S3)."""
    window_manager = _window_manager()
    if window_manager is not None and hasattr(window_manager, "event_timer_remove"):
        for timer in _nudges.values():
            try:
                window_manager.event_timer_remove(timer)
            except Exception:  # noqa: BLE001 - already freed with its window.
                pass
    _nudges.clear()
    from .input_policy import _OWN
    _OWN.clear()


def _local(chat):
    """A chat that works on this Blender's scene (a worker's chat reads the worker's document in its own process)."""
    return not getattr(chat, "remote_executor", None)


def _held_before_read(call):
    """A call the hold keeps from its first read: queued and not bound yet, or stopped at its run (never one waiting
    for the person's own Approve, nor staged or running work, whose reads came before)."""
    if getattr(call, "tool_waits", False):
        return True
    return not (getattr(call, "approval", False) or getattr(call, "executing", False)
                or getattr(call, "authorized", False) or getattr(call, "precondition", None) is not None)


def _blocked(call):
    """A pending call the hold keeps from running: not one that only waits for the person's Approve (review 6, S5)."""
    return (not getattr(call, "approval", False) or getattr(call, "tool_waits", False)
            or getattr(call, "automatic", False))


def _has_work(chat):
    """Calls the hold keeps from running, queued calls, an observation request, or the tray's due messages."""
    tools, inbox = getattr(chat, "tools", None), getattr(chat, "inbox", None)
    return bool(any(_blocked(call) for call in list((getattr(chat, "pending", None) or {}).values()))
                or getattr(chat, "_observation_requests", None)
                or tools is not None and not tools.empty() or inbox is not None and inbox.has_due())


def hold(chat, scene_held):
    """``Controller._pump_tools``, each pump: True while ``chat``'s work waits for the person's operation. Pending
    calls' lifetimes do not run while it waits (unless the scene hold already moved them), and each it keeps from its
    first read is marked. ``scene_held``: the chat already waits for its scene (``document_scene.hold``), first."""
    tool = None if scene_held or not _local(chat) else _tool()
    now, last = time.monotonic(), getattr(chat, "_tool_held_at", None)
    chat._tool_held, chat._tool_held_at = tool, (now if tool is not None else None)
    chat._tool_waiting = tool if tool is not None and _has_work(chat) else None   # What the live row says.
    if chat._tool_waiting is not None:
        note_waiting()
    if tool is None:
        return scene_held
    for call in list(getattr(chat, "pending", {}).values()):
        if not getattr(call, "waited_for_tool", "") and _held_before_read(call):
            call.waited_for_tool = tool
        if last is not None and getattr(call, "expires", None) is not None:
            call.expires += now - last   # Its lifetime does not run while it waits.
    return True


def wait(chat, call):
    """At a call's execution: True (the call marked to resume) while the person's operation is in progress; False,
    with the mark cleared, when it may run now, or when it is no longer pending or already runs (execution decides)."""
    if getattr(chat, "pending", {}).get(call.id) is not call or getattr(call, "executing", False):
        return False
    tool = _tool() if _local(chat) else None
    call.tool_waits = tool is not None
    if tool is not None and not getattr(call, "waited_for_tool", ""):
        call.waited_for_tool = tool
    return call.tool_waits


def resumable(chat, call):
    """A call stopped at execution for an operation that has ended: the pump applies it again."""
    return getattr(call, "tool_waits", False) and not call.executing and getattr(chat, "_tool_held", None) is None


def resume(chat, call, executor, document, apply_mutation, budget):
    """The pump's step for a stopped call: run it again through its approval route (True), once the operation ended."""
    if not resumable(chat, call):
        return False
    budget.admissions += 1
    call.tool_waits = False
    if chat._tool_available(call, document):
        budget.executions += 1
        if apply_mutation is not None and getattr(call, "approval", False):
            apply_mutation(call)   # The Undo operator bound to its chat, as the click that stopped here.
        else:
            chat.execute_tool(call, executor, document)
    return True


def noted(call, result):
    """``result`` with the fact for the model that ``call`` waited for the person's operation before its first read,
    when it did and then ran (a call cancelled, expired or declined while it waited never ran: no fact)."""
    tool = getattr(call, "waited_for_tool", "")
    if not tool or not getattr(call, "executing", False) or not isinstance(result, dict) or "waited" in result:
        return result
    return {**result, "waited": WAITED.format(tool=tool)}


def shown(tool):
    """``WAITING`` for ``tool``, translated: Blender's own operator name in its operator context."""
    return iface_(WAITING, tool=tool_name(tool))


def _snapshots(chat, kind, data):
    """Whether the chat event ``kind`` captures a request snapshot when it is handled (``Controller._pump_events``)."""
    if not getattr(chat, "checkpoint_factory", None):
        return False
    return kind == "voice_request" or (kind == "message" and data.get("role") == "user" and not data.get("checkpoint")
                                       and bool(getattr(chat, "voice", None)))


def next_event(chat):
    """``Controller._pump_events``' next event: one held by ``defers`` first, once nothing is in progress; while
    something still is, none (every later event waits behind it, so the order holds). Raises ``queue.Empty``."""
    held = vars(chat).get("_tool_events")
    if held:
        if running():
            raise queue.Empty
        return held.pop(0)
    return chat.events.get_nowait()


def defers(chat, kind, data):
    """True, keeping the event first in line, when handling it now would snapshot the document under the person's
    operation (a voice turn's request snapshot); the pump stops reading events until it ends."""
    if not _local(chat) or not _snapshots(chat, kind, data) or not running():
        return False
    vars(chat).setdefault("_tool_events", []).insert(0, (kind, data))
    note_waiting()
    return True


def _later(function):
    import bpy
    bpy.app.timers.register(function, first_interval=RETRY_SECONDS)


def deferred(function):
    """True when the person's operation is in progress: ``function`` (a delivery's capture and send, carrying its
    own Stop count) is retried on Blender's timer once it has ended, and the caller does nothing now. False when
    nothing is: the caller goes on."""
    if not running():
        return False
    note_waiting()

    def retry():
        if running():
            return RETRY_SECONDS
        function()
        return None
    _later(retry)
    return True
