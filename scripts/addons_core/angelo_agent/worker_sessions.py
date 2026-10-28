# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead starts workers: each part a worker Blender, a chat bound to it, and its brief as the first message.

``angelo_workers_start`` (``worker_spec``) runs here: an Ask chat shows its card first (``worker_approval``); Auto
starts at once, bounded by ``MAX_AUTO_STARTS`` parts per request (parts past the free places wait). For each part:

1. a part file in the lead's working folder in Angelo's state folder (``parallel/<lead chat>/<n>-<name>/``),
   never beside the person's own file while phase 1 cannot merge parts back;
2. a worker Blender launched on it **off the main thread** (``WorkerProcess.start`` is standard library only and
   waits up to two minutes for the worker to say it is ready, break-it P1-1 M3), then, back on the main thread:
3. a new chat that the person's view does not switch to, with the lead's connection, model options, approval
   mode (a worker of an Ask chat asks; its approval shows on its tile) and chat trust (``worker_trust``), bound to
   that worker (``worker_routing.adopt``: its live part and tile, ``parallel_sessions``);
4. the brief sent as the chat's first message with the person's references, observed on the worker's document, so
   the worker's own model turn builds the part (its base instructions say what a worker is and has:
   ``worker_spec.WORKER_BRIEFING``). A brief that cannot be sent stops the worker again and is reported, never left
   running without a brief.

At most ``parallel_access.worker_limit`` (the person's Workers at once, ``parallel_setting``, else this machine's
count from ``parallel_capability``) run or launch at once, whoever asked: parts past it wait their turn, and a done
worker frees its place once its part is saved (``worker_queue``). The start timer survives a file load (it is
persistent) and ends with the add-on (``release``): a worker that is still launching then is stopped as soon as it is
ready, and nothing queued starts. A load that invalidates a lead stops its workers
(``worker_queue.document_replaced``).
``angelo_workers_status`` (``worker_status``) reads each worker's state (``failed`` with why when its worker ended under
it), its newest reply and what its part holds, and each queued part's place in line; ``worker_facts`` adds whether
each part is in this scene and how long each took.
"""

import re
import threading
import time
from pathlib import Path

from .worker_status import SIZE_NOTE, _size  # noqa: F401 - a part's size in its status (``status``)

# Model-visible (never translated): the worker's first message, only its part; what every worker is told is its base
# instructions (``worker_spec.WORKER_BRIEFING``), cached across workers.
BRIEF = "Part: {name}.\n\n{brief}"
REPLY_LIMIT, OBJECT_LIMIT = 1500, 60
POLL = 0.1
# Parts one request of the lead may start in Auto without a card: the most one merge takes. Past it a start asks, as
# in an Ask chat: Auto bounds billed worker sessions, not only how many run at once (break-it E2E-2 M3).
MAX_AUTO_STARTS = 16

_started = {}    # lead chat session id -> [worker chat session ids], in start order
_launching = []  # {"lead", "part", "number", "worker", "thread", "error"} still starting (any lead)
failures = {}    # "<lead>/<n>" -> why that part did not start (status reports it)
died = {}        # worker chat session id -> why its worker ended under it (remote_executor._died; status, merge)
_parts = {}      # worker chat session id -> (number, part name, part file): kept after the worker ends (merge)
_numbers = {}    # lead chat session id -> the last worker number it issued: one numbering, never reused (P1-6 H2)
_requests = {}   # lead chat session id -> (its request's message index, parts started in that request)
_layouts = {}    # worker chat session id -> its newest layout (``live_layout``): a merge after Undo still has it
_released = [False]


def workers_of(lead_id):
    return list(_started.get(lead_id, ()))


def _slug(name):
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:32] or "part"


def _running():
    """Worker chats with a live worker: not ended, not parked (``worker_queue``: a done one's stopped)."""
    from . import parallel_sessions, worker_queue
    return [s for s in parallel_sessions.workers() if not parallel_sessions.ended(s) and s not in worker_queue.parked]


def precondition(controller, name, arguments):
    from . import parallel_access, worker_dispatch, worker_routing
    worker_dispatch.reclaim()   # This family reads and changes worker chats: no lane runs on while it does (g3b).
    from .refusal_codes import RefusalError
    from .worker_spec import PREFIX, PROGRESS
    if name == PREFIX + "status":
        return None
    if name == PREFIX + "merge":
        if controller is None or worker_routing.worker_snapshots(controller):
            raise RefusalError("A worker does not merge parts; the lead does", code="UNSUPPORTED")
        from . import worker_merge
        return worker_merge.precondition(controller, arguments, parts_of(controller), merge_blocker)
    if name == PROGRESS:
        if controller is None or not parallel_access.worker_chat(controller):
            raise RefusalError("Only a worker reports its part's progress", code="UNSUPPORTED")
        return None
    if not parallel_access.enabled():
        raise RefusalError("This Angelo's parallel agents are not enabled", code="UNSUPPORTED")
    if controller is None or worker_routing.worker_snapshots(controller):
        raise RefusalError("A worker does not start workers; the lead assigns parts", code="UNSUPPORTED")
    from . import worker_paths   # Windows: refused before anything starts when its files' paths would be too long.
    worker_paths.check_start(controller, arguments["parts"], _numbers.get(controller.state["id"], 0) + 1, _slug)
    # Bound to what was asked, never to how many run now (P1-1 L3): parts past the free places wait (worker_queue).
    return ("workers", tuple(part["name"] for part in arguments["parts"]))


def _person_request(controller):
    """The person's request a start belongs to (its row itself, never an index a row inserted before it shifts, EVENT
    R2-L-4): Angelo's own turn (``worker_resume``) continues the request before it, so its starts count on that
    request's Auto budget (break-it EVENT H-3)."""
    from .request_rows import person_request
    return next((id(row) for row in reversed(controller.state.get("messages") or ()) if person_request(row)), None)


def auto_start_allowed(controller, arguments):
    """Whether Auto may start these parts without a card (``MAX_AUTO_STARTS`` per request of the person)."""
    request, started = _requests.get(controller.state["id"], (None, 0))
    if request != _person_request(controller):
        started = 0
    return started + len((arguments or {}).get("parts") or ()) <= MAX_AUTO_STARTS


def _count_starts(controller, count):
    request = _person_request(controller)
    previous, started = _requests.get(controller.state["id"], (None, 0))
    _requests[controller.state["id"]] = (request, (started if previous == request else 0) + count)


def merge_blocker(chat):
    """Why ``chat``'s part is not ready to merge, or None. The status wait's own predicate (``finished``): its brief
    reached it and its turn ended, nothing queued on its lane (break-it E2E-2 M2, E2E-1 M-4)."""
    session = chat.state["id"]
    started = _parts.get(session)
    if started is not None and "%s/%d" % (_lead_of(session), started[0]) in failures:
        return "it never started (%s)" % failures["%s/%d" % (_lead_of(session), started[0])][:120]
    from . import worker_queue
    if session in worker_queue._resuming:   # A merge now would end the chat and lose the message (E2E-20 M-2).
        return "a message to it waits for its worker to start again"
    if finished(session):
        return worker_queue.save_problem(session)
    if session in died:
        return "its worker ended (%s) and its chat is still ending its turn" % died[session][:120]
    if not any(m.get("role") == "user" for m in chat.state.get("messages") or ()):
        return "its brief has not reached it yet"
    if awaiting_person(session):
        return "it waits for the person's approval of one of its edits"
    return "it is still working"


def _lead_of(session):
    return next((lead for lead, sessions in _started.items() if session in sessions), None)


def _last_turn_failed(chat):
    """Whether the worker's last turn did not complete: its last request receipt's status (every ending writes one:
    completed, failed, interrupted), so a stop after a streamed line counts too (break-it E2E-5 M-3). A chat with no
    receipt yet falls back to its rows: an error row ends it, or a stop before any reply."""
    session = chat.state.get("id")
    receipts = [r for r in list(getattr(getattr(chat, "telemetry", None), "receipts", None) or ())
                if isinstance(r, dict) and "kind" not in r and r.get("session_id") == session]
    if receipts:
        return receipts[-1].get("status") != "completed"
    rows = list(chat.state.get("messages") or ())
    since = next((index for index in range(len(rows) - 1, -1, -1) if rows[index].get("role") == "user"), -1)
    ends = [row.get("role") for row in rows[since + 1:] if row.get("role") in ("assistant", "error")]
    return (ends[-1] == "error") if ends else getattr(chat, "cancelled", False) is True


def keep_layout(session_id, layout):
    """A worker chat's newest layout, kept as it leaves its worker (``worker_routing.unbind``): a merge after that
    still knows its tree and view-layer state (break-it E2E-3 L-6)."""
    if layout is not None:
        _layouts[session_id] = layout


def layouts_of(lead):
    """{worker number: its newest layout} of ``lead``'s workers, kept after a worker ends (a merge after Undo)."""
    from . import ui, worker_routing
    found = {}
    for session in _started.get(lead.state["id"], ()):
        executor = worker_routing.bound(ui._controllers.get(session)) if session in ui._controllers else None
        if executor is not None and getattr(executor, "part_layout", None) is not None:
            _layouts[session] = executor.part_layout
        if session in _parts and session in _layouts:
            found[_parts[session][0]] = _layouts[session]
    return found


def parts_of(lead):
    """{worker number: (its chat while it is still in Angelo, or None; its part file)} of ``lead``'s workers."""
    from . import ui
    found = {}
    for session in _started.get(lead.state["id"], ()):
        if session in _parts:
            number, _name, path = _parts[session]
            found[number] = (ui._controllers.get(session), path)
    return found


def execute(controller, name, arguments, window=None):
    from .worker_spec import PREFIX, PROGRESS
    from .worker_dispatch import reclaim
    reclaim()   # As ``precondition``: off the main thread (a worker's progress, on its lane) it does nothing.
    if name == PREFIX + "status":
        from . import worker_status
        return worker_status.call(controller, arguments)
    if name == PREFIX + "merge":
        return _merge(controller, arguments, window)
    if name == PROGRESS:
        from . import parallel_sessions
        shown = parallel_sessions.note_progress(controller.state["id"], arguments["done"], arguments.get("steps"))
        return {"success": True, "shown": shown}
    lead_id = controller.state["id"]
    # A worker's number is given at launch and kept: status, merge, the merge card and the tiles all use it, in
    # whatever order workers become ready, and a failed start never frees its number (break-it P1-6 H2).
    first = _numbers.get(lead_id, 0) + 1
    # Every part's folder and command first: one that cannot be prepared starts none (break-it E2E-2 L1).
    items = [_prepare(controller, part, first + index) for index, part in enumerate(arguments["parts"])]
    from . import worker_references
    references = worker_references.taken(controller)   # This request's images now, never the next one's (E2E-16 M-3).
    for item in items:
        item["references"] = references
    _numbers[lead_id] = first + len(items) - 1
    _count_starts(controller, len(items))
    from . import worker_queue, worker_resume   # Each starts while a place is free, the rest wait their turn (a
    answer = worker_queue.admit(controller, items)   # background Angelo finishes the starts, sends the briefs there).
    worker_resume.started(controller, [item["number"] for item in items])   # A batch (worker_resume).
    return answer


def _prepare(lead, part, number):
    """One part's folder and worker command (main thread), not started yet."""
    from . import ui, worker_paths, worker_routing
    import uuid
    # A folder of its own each time: a number restarts with Angelo, and a new worker never starts from an old part.
    folder = worker_paths.part_folder(ui.state_directory(), lead.state["id"], number, _slug(part["name"]),
                                      uuid.uuid4().hex[:8])
    item = {"lead": lead, "part": part, "number": number, "error": None, "done": threading.Event()}
    item["worker"] = worker_routing.launch(folder / "part.blend")   # Not started: a queued part holds no Blender.
    return item


def _launch(item, take=True):
    """Start one prepared part's worker on a thread of its own: a worker launched ahead when one is free (taken only
    now, so a queued part never holds one: break-it E2E-19A H-1, M-4), else its own launch. ``take`` False: the
    item's worker is already a running one (``worker_pool.reuse``)."""
    if take:
        from . import worker_pool
        item["worker"] = worker_pool.take(item["worker"].part, item["lead"]) or item["worker"]

    def run():
        try:
            from .worker_routing import ready
            ready(item["worker"])   # Started and greeted here: its adoption sends nothing on the main thread.
        except BaseException as error:   # noqa: BLE001 - reported on the main thread.
            item["error"] = error
        finally:
            item["done"].set()
    item["thread"] = threading.Thread(target=run, daemon=True, name="AngeloWorkerLaunch")
    _launching.append(item)
    item["thread"].start()


def _tick():
    """Main thread: bind each worker that became ready, in launch order; then ``worker_queue``'s step (done workers
    free their places, what waits starts). Runs while either has anything left."""
    for item in [item for item in _launching if item["done"].is_set()]:
        _launching.remove(item)
        try:
            _adopt(item)
        except Exception as error:   # noqa: BLE001 - one part that cannot start never stops the others.
            failures["%s/%d" % (item["lead"].state["id"], item["number"])] = str(error)[:400]
            print("Angelo: worker start:", error)
    from . import worker_chat_lifecycle, worker_queue, worker_resume
    worker_chat_lifecycle.finish_opens()   # A': each chat whose open was answered is briefed, or ends (``_brief``).
    if worker_queue.tick() or _launching or worker_chat_lifecycle.opening():
        return POLL
    return worker_resume.WAIT_POLL if worker_resume.waiting() else None   # Only a batch waits: slowly (EVENT L-2).


def _adopt(item):
    from . import parallel_sessions, worker_routing
    worker, lead = item["worker"], item["lead"]
    if item["error"] is not None:
        worker_routing.stop_later(worker)   # Never waited for on the main thread (break-it E2E-2 M1).
        raise item["error"]
    from . import worker_queue
    if _released[0] or not worker_queue.current(lead):   # Angelo ended, or a load replaced the lead's document,
        worker_routing.stop_later(worker)                   # while it launched: nothing of it binds (audit, 948d877).
        if not _released[0]:
            raise RuntimeError(worker_queue.REPLACED)
        return
    controller, binding = None, False
    try:   # One guard: a launched worker never outlives a chat that could not be made or bound (audit of 34f111d:
        controller = _new_chat(lead)   # a failed chat left it running, untracked); ``_tick`` records why.
        parallel_sessions.note_active(lead.state["id"])   # The lead's tile is the main one.
        binding = True   # From here ``adopt`` stops the worker itself when it cannot bind.
        worker_routing.adopt(controller, worker, defer=True)   # A': its chat.open goes off the main thread.
    except BaseException:
        if not binding:
            worker_routing.stop_later(worker)   # Off the main thread, never waited for here.
        if controller is not None:
            _forget_chat(controller.state["id"])
        raise
    session_id = controller.state["id"]
    parallel_sessions.names[session_id] = item["part"]["name"]
    _parts[session_id] = (item["number"], item["part"]["name"], Path(item["worker"].part))
    _started.setdefault(lead.state["id"], []).append(session_id)   # Shown starting until its brief is sent.
    from . import worker_chat_lifecycle
    if not worker_chat_lifecycle.when_open(controller, lambda error: _brief(controller, item, error)):
        _brief(controller, item, None)   # Nothing to wait for (A' off): briefed at once, as before.


def _brief(controller, item, error):
    """Main thread, once the chat opened in its worker (at once with A' off): its brief. One that could not open
    (``error``: it already left its worker, which stops) or be sent says why, in the chat and in its status."""
    from . import worker_chat_lifecycle, worker_references, worker_routing
    lead, key = item["lead"], "%s/%d" % (item["lead"].state["id"], item["number"])
    if error is not None:
        failures[key] = str(error)[:400]
        worker_chat_lifecycle.tell(controller, error)
        return
    try:
        text = BRIEF.format(name=item["part"]["name"], brief=item["part"]["brief"])
        text += worker_references.attach(controller, lead, item.get("references") or ())
        text += __import__(f"{__package__}.worker_schemas", fromlist=["block"]).block(controller, lead, item["part"])
        _send(controller, text, key)
    except BaseException as failure:
        worker_routing.unbind(controller, worker_routing.END_NOT_BRIEFED)   # Never a worker running without its brief.
        failures[key] = str(failure)[:400]
        raise


def finished(session_id):
    """The worker chat got its brief and its turn ended (idle, nothing pending), or it left Angelo; one no longer bound
    to a worker once its turn ended (break-it E2E-15 M-2: one whose worker died mid-turn was finished at once)."""
    from . import ui, worker_dispatch, worker_routing
    chat = ui._controllers.get(session_id)
    if chat is None:
        return True
    from . import worker_queue
    if session_id in worker_queue._resuming:   # A message to it waits for its worker to start again: it has work.
        return False
    lane = worker_dispatch.lane(chat, create=False)
    idle = not chat.busy and not chat.pending and not (lane is not None and (lane.actions or lane.busy))
    if worker_routing.bound(chat) is None and not worker_routing.hosted(chat):   # A mirror waits for its brief too.
        return idle
    return idle and any(m.get("role") == "user" for m in chat.state.get("messages") or ())


def awaiting_person(session_id):
    """The worker chat holds a card only the person can answer (its approval mode would not run it by itself). The
    lead's wait keeps waiting then (the person sees the card on the worker's tile, and answering the wait at once
    only cost the lead a billed round per poll, break-it E2E-3 M-2); the merge refusal says so."""
    from . import ui
    chat = ui._controllers.get(session_id)
    return chat is not None and any(
        getattr(call, "approval", False) and not getattr(call, "executing", False) and not chat._automatic_script(call)
        and not chat._auto_bounded(call) for call in list(chat.pending.values()))


def _wait(lead, seconds):
    """A deferred status that answers once any worker becomes done (``worker_status.wait``)."""
    from . import worker_status
    return worker_status.wait(lead, seconds)


def _merge(lead, arguments, window):
    """Merge on the main thread, inside the approved-change Undo operator; merged workers stop."""
    from . import parallel_sessions, worker_merge, worker_merge_gate, worker_routing
    parts, layouts = parts_of(lead), layouts_of(lead)
    names = {_parts[session][0]: _parts[session][1] for session in _started.get(lead.state["id"], ()) if session in _parts}
    sessions, paths = worker_merge_gate.targets(lead, arguments)
    for session in sessions:
        parallel_sessions.merging(session)   # Each tile says Merging, then Merged once its part is in.
    try:   # A': read under the holds and leases asked before it ran (worker_merge_gate, C10); off, nothing is sent.
        with worker_merge_gate.guard(lead, sessions, paths):
            result = worker_merge.merge(lead, window, arguments, parts, lambda n: names.get(n, "Part %d" % n), layouts)
    except BaseException as error:   # Refused for another merge under way: its tiles stay that merge's.
        for session in () if getattr(error, "merging_elsewhere", False) else sessions:
            parallel_sessions.merge_failed(session)
        raise
    import bpy
    dead = {_parts[session][0]: died[session] for session in _started.get(lead.state["id"], ())
            if session in _parts and session in died}   # Whatever its turn's receipt says (break-it E2E-15 M-2).
    for entry in result.get("merged") or ():   # The host admits a turn that ended in an error; the lead hears it.
        chat = parts.get(entry["worker"], (None,))[0]
        if entry["worker"] in dead:
            entry["warning"] = "This worker ended before it finished (%s); its part is its last save." % (
                dead[entry["worker"]])
        elif chat is not None and _last_turn_failed(chat):
            entry["warning"] = "This worker's last turn ended in an error or was stopped; its part may be incomplete."
    collections = {entry["worker"]: entry.get("collection") for entry in result.get("merged") or ()}
    for session in sessions:
        part = str(_parts[session][2]) if bpy.data.collections.get(collections.get(_parts[session][0]) or "") else None
        parallel_sessions.merged(session, part)
    from . import merge_framing, worker_queue
    merge_framing.after_merge(lead, window, collections.values())   # The view framed once on what it brought.
    for part in arguments["parts"]:
        chat = parts[part["worker"]][0]
        if chat is not None and worker_routing.bound(chat) is not None:
            worker_routing.unbind(chat, worker_routing.END_MERGED)
        elif chat is not None:   # Parked (its worker stopped when done): ended too, never relaunched (19A M-3).
            worker_queue.end_parked(chat.state["id"], worker_queue.MERGED, worker_routing.END_MERGED)
    return result


def _forget_chat(session_id):
    from . import ui
    ui._controllers.pop(session_id, None)


def _new_chat(lead):
    """A new chat with the lead's connection and options, made without switching the person's view."""
    from . import ui, worker_chat_wiring, worker_trust
    from .connection_access import find_connection
    from .controller import Controller
    sessions = ui._sessions
    shown = sessions.active
    identity = sessions.create(lead.state.get("provider") or "codex_subscription")
    sessions.activate(shown)   # ``create`` makes it active; the person stays on their chat.
    controller = Controller(ui.state_directory(), session_id=identity)
    controller.state["connection_id"] = lead.state.get("connection_id")
    controller.state["options"] = dict(lead.state.get("options") or {})
    controller.api_key = getattr(lead, "api_key", "")
    controller.delegation_resolver = ui.resolve_collaborator
    worker_trust.link(lead, controller)   # It follows the lead's Trust scripts for this chat (Auto only).
    record = find_connection(controller.state.get("connection_id"))
    if record and ui.connection_ready(record):
        controller.settings = ui.connection_settings(record["mode"], record=record)
        worker_chat_wiring.catalog(controller, lead)   # Off: ``refresh_models``; A': a loaded one, never a listing.
    controller.save()
    controller = worker_chat_wiring.mirror_of(controller, lead)   # A': it runs in its worker; Angelo keeps a mirror.
    ui._controllers[identity] = controller
    return controller


def _send(controller, text, key):
    """The brief as the chat's first message, observed on the worker's document (``console.execute``'s path), on
    the chat's lane (``worker_dispatch``); a brief that cannot be sent stops its worker and is reported."""
    from . import ui, worker_facts, worker_routing
    ui.prepare_controller(controller)

    def failed(error):
        failures[key] = str(error)[:400]
        print("Angelo: worker brief:", error)
        worker_routing.unbind(controller, worker_routing.END_NOT_BRIEFED)   # Never a worker running without its brief.
    worker_routing.deliver(controller, ui.main_window(), text, lambda turn: (worker_facts.briefed(controller),
        controller.send(text, observation=turn.observation, recovery_evidence=turn.recovery_evidence)),
        failed)   # Its clock (``worker_facts``) starts as it is sent, before its request's own clock.


def status(controller):
    """Every worker the lead started: state, newest reply, what its part holds."""
    from . import live_view, parallel_sessions, session_rail, session_rail_draw, ui
    lead_id = controller.state["id"] if controller is not None else None
    states = {tile.session: tile for tile in session_rail_draw.states()} if parallel_sessions.sessions() else {}
    found, prefix = [], str(lead_id) + "/"
    never = {int(key.rsplit("/", 1)[1]) for key in failures if key.startswith(prefix)}
    for session_id in _started.get(lead_id, ()):
        number = _parts[session_id][0] if session_id in _parts else None
        if number in never:
            continue   # Listed once, as failed_to_start with why, never also as ended (break-it E2E-4 L-5).
        worker = ui._controllers.get(session_id)
        tile = states.get(session_id)
        summary = live_view.summary(session_id) or {}
        turn, reply = session_rail_draw.turn_end(worker) if worker is not None else ("", "")   # How its turn ended.
        state = ({"state": "failed", "error": died[session_id]} if session_id in died   # E2E-15 M-2.
                 else {"state": tile.kind if tile is not None else session_rail.ENDED})
        found.append({"worker": number, "name": parallel_sessions.names.get(session_id, ""), **state, "turn": turn,
                      "reply": reply[-REPLY_LIMIT:],
                      "objects": list(summary.get("objects") or [])[:OBJECT_LIMIT],
                      **_size(session_id, summary),
                      "saves": live_view.revision(session_id) or 0})
    starting = [{"worker": item["number"], "name": item["part"]["name"], "state": "starting"} for item in _launching
                if item["lead"] is controller]
    failed = [{"worker": int(key.rsplit("/", 1)[1]), "state": "failed_to_start", "error": why}
              for key, why in failures.items() if key.startswith(prefix)]
    from . import worker_facts, worker_status
    queued, places = worker_status.queued(controller)   # Each with its place in line; places while any waits.
    workers = sorted(found + starting + failed + queued, key=lambda entry: entry["worker"] or 0)
    return worker_facts.annotate(lead_id, dict({"workers": workers, "checked_at": round(time.time(), 3)},   # In scene?
                                               **({"places": places} if places else {})))


def cancel_starts(lead=None):
    """Workers still launching (``lead``'s, or every lead's) are never bound or briefed: each stops once it answers,
    from a thread of its own, never waited for here."""
    for item in [item for item in _launching if lead is None or item["lead"] is lead]:
        _launching.remove(item)
        threading.Thread(target=lambda item=item: (item["done"].wait(), item["worker"].stop()), daemon=True,
                         name="AngeloWorkerLaunchStop").start()


def release():
    """The add-on ends: no queued start binds after this, and launching workers stop once ready."""
    import bpy
    from . import worker_queue
    _released[0] = True
    cancel_starts()
    worker_queue.release()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)


def reset_for_register():
    _released[0] = False
