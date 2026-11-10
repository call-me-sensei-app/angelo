# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal's rounds: how a goal starts, what a turn's end does, and the window's step that measures it and sends
the next round as Angelo's own request (doc/angelo/goal_mode.rst, "Setting a goal", "Rounds", "Stopping").

* ``request_started`` (``Controller.send``): a round starts. Only the person's own message (no ``host_request``)
  starts a goal chosen in the chat bar, pauses an active one, or lets a proposed one lapse; every request Angelo makes
  itself (a goal's round, the worker-done event, Continue) does none of these.
* ``turn_ended`` (``Controller._after_turn``'s ends, never while Auto re-runs a turn): the round's working time and
  spend are counted toward the goal, whatever ended it; a completed turn makes the goal *due*, a stopped or failed
  one ends it with its origin.
* ``step`` (the window's update, ``ui.pump`` through ``pump``): an Angelo-made round past the budget is stopped; a due
  goal whose chat is idle, shown, on the same document and has no draft is measured, its round recorded and, while it
  goes on, the next round sent. A defect in one chat's goal ends that goal, never the window's update.

The tool, the observation and the person's controls are ``goal_controls``'s.

The person's words always go first: a due goal waits while the composer holds a draft or the person has messages
waiting to be sent (``controller.inbox``, steer-and-queue), and a goal pauses when the person spoke during its round
(``controller.person_spoke_at`` later than the round's start: a steer, set where the person's Enter lands).

Spend is counted from a per-goal mark of the usage ledger (``_marks``), never per request: what a goal's workers use
between two rounds counts in the next one. A running round's limit check adds the request's live usage
(``usage_meter.in_progress``), so a spend limit stops a round within about one model call.

Main thread. No bpy at import; Blender, the usage ledger and the chat's settings are read through ``goal_measure``
and ``goal_probes``, which tests replace.
"""

import time

from . import goal_state as G
from . import goal_probes as P
from . import goal_text

REQUEST_KIND = "angelo_goal"
CONTINUE_KIND = "angelo_continue"   # The person's Continue on a stopped turn (a host request the person clicked).
OBSERVED = "held_goal"   # Model-visible (never translated): the observation's state field while a goal is held.
# Why a goal ended, for the ones Angelo states (``goal_state``; worded for the person by ``goal_text``).
INTERRUPTED, CONNECTION_CHANGED, READ_ONLY = G.INTERRUPTED, G.CONNECTION_CHANGED, G.READ_ONLY
STOPPED_BY_PERSON, DOCUMENT_CHANGED = G.STOPPED_BY_PERSON, G.DOCUMENT_CHANGED
# Why a due goal waits (``waiting_reason``; the chip shows the draft one).
WAITING_DRAFT, WAITING_BUSY, WAITING_HELD, WAITING_AWAY = "draft", "busy", "held", "away"
WAITING_QUEUED = "queued"   # The person's messages wait to be sent (or are paused after a Stop): theirs go first.

_due = {}         # chat id -> the finished round's facts, waiting to be measured and continued
_continue = set()   # chat ids to continue without recording a round (Resume, Start)
_rounds = {}      # chat id -> the running request's start (kind, monotonic start, the goal it ran under)
_origins = {}     # chat id -> who stopped the running round ("person", or ("budget", which))
_pending = {}     # chat id -> a goal chosen in the chat bar, not sent yet (the composer's, like a chosen tool)
_documents = {}   # chat id -> the document its requests ran on (one goal per document)
_waiting = {}     # chat id -> why its due goal waits
_marks = {}       # chat id -> (tokens, usd) of the usage ledger when the goal's spend was last counted
_sending = {}  # chat id -> the exact controller whose next round awaits owned delivery


def _reset():
    """Forget the module's in-memory state (tests; a new session of the add-on)."""
    for table in (_due, _rounds, _origins, _pending, _documents, _waiting, _marks):
        table.clear()
    for chats in (_continue, _sending):
        chats.clear()


# The goal and its store --------------------------------------------------------------------------------------------

def _store(controller):
    from .goal_store import GoalStore
    return GoalStore.shared(controller.telemetry.directory)


def goal_of(controller):
    """The chat's held goal (any status), or None."""
    return _store(controller).get(controller.state["id"])


def _save(controller, goal):
    _store(controller).put(controller.state["id"], goal)
    controller.version = getattr(controller, "version", 0) + 1


def _notify(controller, message):
    controller.emit("message", {"role": "system", **message})


def _forget(chat_id):
    """A goal that ended or was dropped: nothing of it waits to run."""
    _due.pop(chat_id, None)
    _continue.discard(chat_id)
    _waiting.pop(chat_id, None)
    _marks.pop(chat_id, None)
    _sending.pop(chat_id, None)


def _end(controller, goal, status, reason):
    chat_id = controller.state["id"]
    if not G.ended(goal):
        G.end(goal, status, reason)
    _save(controller, goal)
    _forget(chat_id)
    _notify(controller, goal_text.end_notice(goal))


def _pause(controller, goal, reason):
    _charge(controller, goal)   # What its workers used so far counts; the person's own turns from here do not.
    G.pause(goal, reason)
    _save(controller, goal)
    _forget(controller.state["id"])
    _notify(controller, goal_text.pause_notice(goal))


def pending_goal(controller):
    return _pending.get(controller.state["id"])


def holds_goal(controller):
    """Whether this chat's goal will continue it: active, or due (the harness waits on it as on a worker event)."""
    goal = goal_of(controller)
    return G.running(goal) or controller.state["id"] in _due or controller.state["id"] in _continue


# Spend ---------------------------------------------------------------------------------------------------------------

def _mark(controller):
    _marks[controller.state["id"]] = P.spent(controller)


def _since_mark(controller):
    """``(tokens, usd)`` used since the goal's spend was last counted (0 for a plan's dollars: usage_view.billing)."""
    tokens, usd = P.spent(controller)
    mark = _marks.get(controller.state["id"], (tokens, usd))
    used_usd = max(0.0, usd - mark[1]) if P.paid(controller) else 0.0
    return max(0, tokens - mark[0]), used_usd


def _charge(controller, goal, working_ms=0):
    """Count what was used since the mark toward ``goal``, and move the mark: ``(tokens, usd)`` counted."""
    tokens, usd = _since_mark(controller)
    G.charge(goal, working_ms, tokens, usd)
    _mark(controller)
    return tokens, usd


# A round's start and end -------------------------------------------------------------------------------------------

def _kind(person, host_kind):
    if person or host_kind == CONTINUE_KIND:   # Continue is the person's own click on their stopped request.
        return G.PERSON
    return G.AUTOMATIC if host_kind == REQUEST_KIND else G.WORKER_EVENT


def request_started(controller, text, host_kind=None, person=None, *, use_composer_choices=True):
    """``Controller.send``: a request starts. ``person`` is whether it is the person's own message (no
    ``host_request``); ``host_kind`` names Angelo's own request (``angelo_goal``, ``angelo_event``,
    ``angelo_continue``). Only the person's message changes a goal here. A retry is still the person's message,
    but ``use_composer_choices=False`` leaves a newer composer choice untouched."""
    chat_id = controller.state["id"]
    person = (host_kind is None) if person is None else bool(person)
    _documents[chat_id] = getattr(controller, "document", None)
    goal = goal_of(controller)
    if person and not getattr(controller, "external_client", ""):
        pending = _pending.pop(chat_id, None) if use_composer_choices else None
        if pending is not None:
            refusal = _other_holder(controller, _documents[chat_id])
            if refusal:   # Another chat took this document's goal meanwhile: this message goes alone.
                _notify(controller, goal_text.held_elsewhere_notice())
            else:
                if goal is not None and not G.ended(goal):
                    G.end(goal, G.STOPPED, STOPPED_BY_PERSON)   # The person chose another goal in the chat bar.
                _stamp(controller, pending)
                pending["objective"] = str(text)[:G.OBJECTIVE_MAX]
                pending["status"] = G.ACTIVE
                _save(controller, pending)
                _mark(controller)
                goal = pending
        elif G.running(goal):
            carried = _due.pop(chat_id, None)
            if carried is not None:   # A finished round not measured yet: what it used counts, unmeasured.
                G.charge(goal, carried["working_ms"], carried["tokens"], carried["usd"])
            _pause(controller, goal, G.PAUSED_BY_MESSAGE)
        elif goal is not None and goal.get("status") == G.PROPOSED:
            G.lapse(goal)
            _save(controller, goal)
            _notify(controller, goal_text.end_notice(goal))
    _sending.pop(chat_id, None)
    carried = _due.pop(chat_id, None)   # A host request that won the idle moment: the last round's time carries.
    _origins.pop(chat_id, None)
    if G.running(goal) and chat_id not in _marks:
        _mark(controller)
    _rounds[chat_id] = {"kind": _kind(person, host_kind), "started": time.monotonic(), "carried": carried,
                        "goal": goal["id"] if G.running(goal) else None}


def _stamp(controller, goal):
    """The connection and billing a goal runs on, read at its Send (never at the + menu: the person may switch
    connection before sending). Spend defaults follow the billing it starts on."""
    paid = P.paid(controller)
    if goal.get("connection") is not None and bool(goal["budget"].get("usd") is not None) != paid \
            and not goal.get("spend_chosen"):
        goal["budget"]["usd"] = G.SPEND_USD if paid else None
    goal["connection"] = [controller.state.get("connection_id"), controller.state["provider"]]
    goal.pop("spend_chosen", None)


def turn_ended(controller, data):
    """A request's message ended (``Controller._after_turn``): count what it used, then decide what comes next."""
    chat_id = controller.state["id"]
    started = _rounds.pop(chat_id, None)
    origin = _origins.pop(chat_id, None)
    goal = goal_of(controller)
    if started is None or goal is None:
        return
    # The person's words came into this turn after it started (a steer): their direction may have changed.
    spoke = (getattr(controller, "person_spoke_at", None) or 0) > started["started"]
    working_ms = int((time.monotonic() - started["started"]) * 1000)
    carried = started.get("carried") or {}   # A finished round the step had not measured yet.

    def charge_all():   # Every way but the next round's record: what this turn and the carried round used counts.
        G.charge(goal, carried.get("working_ms", 0), carried.get("tokens", 0), carried.get("usd", 0.0))
        _charge(controller, goal, working_ms)

    if not G.running(goal):
        if started.get("goal") == goal.get("id"):   # It ran under the goal, which the agent paused or ended in it.
            charge_all()
            _save(controller, goal)
            _marks.pop(chat_id, None)   # Counted to here; a later goal (or Resume) marks afresh.
        return
    status = data.get("status")
    if origin == "person":
        charge_all()
        return _end(controller, goal, G.STOPPED, STOPPED_BY_PERSON)
    if isinstance(origin, tuple) and origin[0] == "budget":
        tokens, usd = _since_mark(controller)
        _mark(controller)
        G.record_round(goal, kind=started["kind"], working_ms=working_ms + carried.get("working_ms", 0), measure_ms=0,
                       tokens=tokens + carried.get("tokens", 0), usd=usd + carried.get("usd", 0.0), measurement=None,
                       scene_changed=False)
        return _end_if_running(controller, goal, G.BUDGET, origin[1])
    if status == "interrupted" or getattr(controller, "cancelled", False):   # Angelo stopped it, however it ended.
        charge_all()
        return _end(controller, goal, G.STOPPED, INTERRUPTED)
    if (data.get("not_sent") is True and started["kind"] == G.PERSON and goal["origin"] == G.CHAT_BAR
            and not goal["rounds"]):
        _store(controller).remove(chat_id)   # The person's message went back to the box: its goal goes back too.
        _forget(chat_id)
        goal["status"] = G.PENDING
        _pending[chat_id] = goal
        return
    if status == "failed":
        charge_all()
        return _end(controller, goal, G.FAILED, G.NOT_SENT if data.get("not_sent") is True else G.TURN_ERROR)
    tokens, usd = _since_mark(controller)
    _mark(controller)
    _due[chat_id] = {"kind": started["kind"], "working_ms": working_ms + carried.get("working_ms", 0),
                     "tokens": tokens + carried.get("tokens", 0), "usd": usd + carried.get("usd", 0.0),
                     "scene_changed": bool(getattr(controller, "_turn_mutated", False)) or carried.get("scene_changed",
                                                                                                     False)}
    if spoke:   # The person's words came into this turn: their direction may have changed.
        finished = _due.pop(chat_id)
        G.charge(goal, finished["working_ms"], finished["tokens"], finished["usd"])
        _pause(controller, goal, G.PAUSED_BY_MESSAGE)


def _end_if_running(controller, goal, status, reason):
    if G.ended(goal):   # ``record_round`` ended it already (on a limit of its own).
        _save(controller, goal)
        _forget(controller.state["id"])
        _notify(controller, goal_text.end_notice(goal))
        return
    _end(controller, goal, status, reason)


# The window's step -------------------------------------------------------------------------------------------------

def waiting_reason(controller):
    return _waiting.get(controller.state["id"])


def _idle(controller):
    worker = getattr(controller, "_worker", None)
    return not (controller.busy or controller.pending or (worker is not None and worker.is_alive())
                or getattr(controller, "voice", None) is not None)


def _ready(controller, ui, window):
    """Why a due goal must wait, or None: the worker-done event's conditions, plus a draft being written."""
    if not _idle(controller) or not P.connected(controller, ui):
        return WAITING_BUSY
    if getattr(controller, "inbox", None):   # The person's queued or paused messages: never a round before them.
        return WAITING_QUEUED
    if P.blender_busy(window):
        return WAITING_HELD
    if P.away(controller, ui):
        return WAITING_AWAY
    if P.live_draft(controller, ui).strip():
        return WAITING_DRAFT
    return None


def _over_round_budget(controller, goal):
    """A limit reached while a round Angelo sent runs: ``which`` or None. The person's own turns are never stopped
    by a goal's budget; a goal's round and a worker-done round are."""
    started = _rounds.get(controller.state["id"])
    if started is None or started["kind"] == G.PERSON or started.get("goal") != goal.get("id"):
        return None
    budget, spent = goal["budget"], goal["spent"]
    working = spent["working_ms"] + (time.monotonic() - started["started"]) * 1000
    if working >= budget["minutes"] * 60_000:
        return "minutes"
    tokens, usd = _since_mark(controller)
    live_tokens, live_usd = P.live_usage(controller)
    usd += live_usd if P.paid(controller) else 0.0
    if budget.get("usd") is not None and spent["usd"] + usd >= budget["usd"]:
        return "usd"
    if budget.get("tokens") is not None and spent["tokens"] + tokens + live_tokens >= budget["tokens"]:
        return "tokens"
    return None


def step(ui):
    """Main thread, each window update. Cheap when no goal is due or running."""
    chats = getattr(ui, "_controllers", None) or {}
    for chat_id, controller in list(chats.items()):
        if chat_id in _rounds and chat_id not in _origins and controller.busy:
            _guarded(controller, _stop_over_budget, controller)
        elif chat_id not in _due and chat_id not in _continue and chat_id not in _sending and _idle(controller):
            _guarded(controller, _pause_orphan, controller)
    for chat_id in list(_due) + [chat_id for chat_id in list(_continue) if chat_id not in _due]:
        controller = chats.get(chat_id)
        goal = goal_of(controller) if controller is not None else None
        if not G.running(goal):
            _forget(chat_id)
            continue
        _guarded(controller, _step_one, ui, controller, goal)



def pump(ui, held):
    """Prepare cached goal chips, then ``step`` unless layouts settle (``held``). Loading never starts a round.
    A defect never stops the window's update."""
    try:
        for controller in (getattr(ui, "_controllers", None) or {}).values():
            _store(controller).prepare(controller)
        if not held:
            step(ui)
    except Exception as error:  # noqa: BLE001 - a goal's defect never stops the window's update.
        from . import pump_guard
        pump_guard.report_defect("a goal's round", error, str)   # i18n: a log line, never drawn


def goal_round(controller):
    """Whether the request running in this chat is a goal's round (``request_rows``: its numbers for the row)."""
    return (_rounds.get((getattr(controller, "state", None) or {}).get("id")) or {}).get("kind") == G.AUTOMATIC

def _guarded(controller, function, *args):
    """One chat's goal work; a defect ends that goal (with why) and leaves nothing of it to run again."""
    try:
        function(*args)
    except Exception as error:  # noqa: BLE001 - one goal's defect never stops the window's update or other chats.
        chat_id = controller.state["id"]
        goal = goal_of(controller)
        running_round = goal is not None and _round_running(controller, goal)
        _forget(chat_id)
        if goal is not None and not G.ended(goal):
            goal["error"] = (str(error) or type(error).__name__)[:200]
            _end(controller, goal, G.FAILED, G.GOAL_DEFECT)
        if running_round:   # Its round must not run on without the goal's limits: it stops as Stop does.
            controller.stop("The goal ended; no further operation was applied")
        from . import pump_guard
        pump_guard.report_defect("a goal's round", error, str)   # i18n: a log line, never drawn


def _stop_over_budget(controller):
    goal = goal_of(controller)
    which = _over_round_budget(controller, goal) if G.running(goal) else None
    if which is not None:
        _origins[controller.state["id"]] = ("budget", which)
        controller.stop("The goal's budget is reached; no further operation was applied")


def _pause_orphan(controller):
    """An active goal no round runs for and none is due (its chat was opened again, or a send failed before its turn
    ran): it waits for the person's Resume instead of being held with nothing to continue it."""
    goal = goal_of(controller)
    if G.running(goal):
        _rounds.pop(controller.state["id"], None)
        _pause(controller, goal, G.PAUSED_BY_IDLE)


def _step_one(ui, controller, goal):
    chat_id = controller.state["id"]
    window = ui.main_window()
    waiting = _ready(controller, ui, window)
    _waiting[chat_id] = waiting
    if waiting is not None:
        return
    _waiting.pop(chat_id, None)
    finished = _due.pop(chat_id, None)
    _continue.discard(chat_id)
    _advance(ui, controller, goal, window, finished)


def _measure(controller, goal, window):
    """``(measurement, milliseconds)``: None for a goal the agent judges."""
    if G.judged(goal):
        return None, 0
    from . import goal_measure
    started = time.perf_counter()
    try:
        measurement = goal_measure.measure(controller, goal, window)
    except Exception as error:  # noqa: BLE001 - an unmeasured round, with why; never a stopped chat.
        measurement = {"measured": False, "reason": str(error)[:200] or type(error).__name__}
    return measurement, int((time.perf_counter() - started) * 1000)


def _document_changed(controller, ui):
    """Another document is open than the goal's requests ran on (a file opened, reloaded or reverted)."""
    if getattr(controller, "document_invalid", False) or getattr(controller, "awaited_file", None):
        return True
    ran_on = _documents.get(controller.state["id"])
    return ran_on is not None and ran_on != P.document_now(ui)


def _advance(ui, controller, goal, window, finished):
    """Measure, record ``finished`` (None: Resume or Start, no round to record), decide and send the next round."""
    if P.approval(controller) == "READ_ONLY":
        return _end(controller, goal, G.STOPPED, READ_ONLY)
    if _document_changed(controller, ui):   # Before measuring: another file's scene must never pass this goal.
        return _end(controller, goal, G.STOPPED, DOCUMENT_CHANGED)
    connection = goal.get("connection")
    if getattr(controller, "_next_binding", None) or (
            connection and list(connection) != [controller.state.get("connection_id"), controller.state["provider"]]):
        # Never a round on a connection it did not start on.
        return _end(controller, goal, G.STOPPED, CONNECTION_CHANGED)
    measurement, measure_ms = _measure(controller, goal, window)
    if finished is not None:
        changed = goal.pop("measure_changed_in_round", False)
        status, _reason = G.record_round(goal, kind=finished["kind"], working_ms=finished["working_ms"],
                                         measure_ms=measure_ms, tokens=finished["tokens"], usd=finished["usd"],
                                         measurement=measurement, scene_changed=finished["scene_changed"],
                                         measure_changed=changed)
        _save(controller, goal)
        if status != G.ACTIVE:
            _forget(controller.state["id"])
            return _notify(controller, goal_text.end_notice(goal))
    elif G.over_budget(goal) is not None:
        return _end(controller, goal, G.BUDGET, G.over_budget(goal)[0])
    _send_round(ui, controller, goal, measurement)


def _send_round(ui, controller, goal, measurement):
    from . import worker_routing
    message = goal_text.continuation(goal, measurement)
    controller._host_facts = goal_text.round_facts(goal, measurement)   # The row's numbers (request_rows).
    failures = []
    _sending[controller.state["id"]] = controller   # Until its request starts (``request_started``), or the goal ends.
    try:
        ui.prepare_controller(controller)
        worker_routing.deliver(controller, ui.main_window(), message, lambda turn: controller.send(
            message, observation=turn.observation, recovery_evidence=turn.recovery_evidence, host_request=[],
            host_kind=REQUEST_KIND), failures.append)
    except Exception as error:  # noqa: BLE001 - said in the chat; the goal ends with why.
        failures.append(error)
    for error in failures[:1]:
        _rounds.pop(controller.state["id"], None)
        _sending.pop(controller.state["id"], None)
        if G.running(goal):
            goal["error"] = str(error)[:200]   # Shown with the notice as data (goal_text.end_notice).
            _end(controller, goal, G.FAILED, G.SEND_FAILED)


def _other_holder(controller, document):
    """Why this chat may not hold a goal on ``document``: another chat's held goal there, or None. Every chat this
    process hosts is asked, with the document it works on now, so the rule holds after a restart and a reload."""
    if document is None:
        return None
    chats = P.live_chats()
    for chat_id in set(chats) | set(_documents):
        if chat_id == controller.state["id"]:
            continue
        other = chats.get(chat_id)
        if other is not None and getattr(other, "document", None) is not None:
            held_document = other.document
        else:
            held_document = _documents.get(chat_id)
        if held_document != document:
            continue
        goal = _store(controller).get(chat_id)   # One state folder holds every chat's goal.
        if G.running(goal):   # At most one ACTIVE goal per document: a paused or proposed one elsewhere waits for
            return "another chat holds this document's goal; one goal per document"   # its own Start or Resume.
    return None


def _round_running(controller, goal):
    """A round of this goal runs now (one it was active for when it started)."""
    started = _rounds.get(controller.state["id"])
    return bool(controller.busy and started is not None and started.get("goal") == goal.get("id") and G.running(goal))

