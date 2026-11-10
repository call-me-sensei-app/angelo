# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A held goal's tool and the person's controls (doc/angelo/goal_mode.rst, "Setting a goal", "Stopping").

* ``execute``: ``angelo_goal`` (``BlenderExecutor``), with the refusals that keep a goal the person's.
* The person's controls (the chip and card, ``chat_goal``; the + menu, ``chat_add_nav``): ``choose``, ``set_option``,
  ``start``, ``resume``, ``stop``, ``person_stopped`` (the chat's Stop, ``stop_notice.person_stop``), ``allow_measure``,
  ``unlock``, ``dismiss``, ``discard_pending``.

The goal's rounds, its state in this process and its turn ends are ``goal_loop``'s; this module changes a goal only
through it. Main thread; no bpy at import.
"""

from . import goal_loop as L
from . import goal_probes as P
from . import goal_state as G
from . import goal_text
from types import SimpleNamespace
from .goal_spec import TOOL_NAME, validate_goal


# The tool ----------------------------------------------------------------------------------------------------------

def _summary(goal, measurement=None, note=""):
    result = {"status": goal["status"], "objective": goal["objective"], "check": goal["check"],
              "pass_at": goal["pass_at"], "budget": dict(goal["budget"]), "spent": dict(goal["spent"]),
              "rounds": len(goal["rounds"]), "measure": goal.get("measure")}
    if measurement is not None:
        result["measurement"] = measurement
    if note:
        result["note"] = note
    return result

def _refuse_new_goal(controller):
    """Why ``SET`` may not create a goal in this request, or None (doc "Setting a goal")."""
    from .request_rows import persons_send
    if not persons_send(controller):
        return "This request is Angelo's own, not the person's: a goal starts only from the person's request"
    if getattr(controller, "voice", None) is not None:
        return "A voice turn holds no goal; the person can choose one in the chat bar"
    if getattr(controller, "external_client", "") or P.worker_chat(controller):
        return "This chat holds no goals"
    if P.approval(controller) == "READ_ONLY":
        return "This chat is read only: nothing could change toward a goal"
    document = L._documents.get(controller.state["id"], getattr(controller, "document", None))
    return L._other_holder(controller, document)

def _apply_measure(controller, goal, measure, window):
    """The agent's measurement: never another kind of check than the person chose in the chat bar."""
    kind = G.SCENE if "checks" in measure else G.REFERENCE
    if goal["origin"] == G.CHAT_BAR and goal["check"] != kind:
        chosen = "the agent's own judgment" if goal["check"] == G.JUDGED else "a match with the attached picture"
        return {"error_code": "PERSON_CHOSE", "error": "The person chose how this goal is checked (%s); its check "
                "stays as chosen" % chosen, "check": goal["check"]}
    try:
        G.replace_measure(goal, measure)
    except G.Locked as locked:
        L._save(controller, goal)
        return {"error_code": "LOCKED", "error": str(locked), "measure": goal["measure"]}
    goal["check"] = kind
    goal["measure_changed_in_round"] = True
    return None

def execute(controller, window, arguments):
    """``angelo_goal``. Refusals are ValueError (the tool's error result); a locked measurement is a result."""
    args = validate_goal(TOOL_NAME, arguments)
    goal = L.goal_of(controller)
    held = goal is not None and not G.ended(goal)
    if args["action"] == "SET":
        if not held:
            if "objective" not in args:
                raise ValueError("No goal is held in this chat: SET with objective holds one")
            refusal = _refuse_new_goal(controller)
            if refusal:
                raise ValueError(refusal)
            goal = G.new(args["objective"], G.WORDS, paid=P.paid(controller),
                         proposed=P.approval(controller) == "ASK",
                         connection=[controller.state.get("connection_id"), controller.state["provider"]])
            L._documents[controller.state["id"]] = L._documents.get(controller.state["id"],
                                                                 getattr(controller, "document", None))
        note = ""
        if "measure" in args:
            refused = _apply_measure(controller, goal, args["measure"], window)
            if refused is not None:
                return refused
        elif held:
            note = "A goal is already held; its objective, threshold and budget are the person's"
        L._save(controller, goal)
        if not held:
            L._mark(controller)   # A new goal counts from now, never from an earlier goal's mark.
        measurement, _ms = L._measure(controller, goal, window)
        if not held:
            L._notify(controller, goal_text.set_notice(goal))
        return _summary(goal, measurement, note)
    if not held:
        raise ValueError("No goal is held in this chat")
    if args["action"] == "MEASURE":
        if G.judged(goal):
            return _summary(goal, note="No measurement is set: the goal is judged by you")
        return _summary(goal, L._measure(controller, goal, window)[0])
    if goal["status"] not in (G.ACTIVE, G.PAUSED):
        raise ValueError("The goal is %s; only a held goal stops" % goal["status"])
    if goal["status"] == G.PAUSED and args["reason"] == "NEEDS_INPUT":
        return _summary(goal, note="The goal is paused already")
    if goal["status"] == G.PAUSED:
        G.resume(goal)
    G.agent_stop(goal, args["reason"], args["message"])
    L._save(controller, goal)
    L._due.pop(controller.state["id"], None)
    L._notify(controller, goal_text.pause_notice(goal) if goal["status"] == G.PAUSED else goal_text.end_notice(goal))
    return _summary(goal)


# The person's controls ---------------------------------------------------------------------------------------------

class HeldElsewhere(ValueError):
    """Another chat holds this document's goal (one goal per document)."""

def _check_document(controller, ui=None):
    import sys
    ui = ui or sys.modules.get(__package__ + ".ui")
    document = P.document_now(ui) if ui is not None and hasattr(ui, "document_key") else getattr(
        controller, "document", None)
    refusal = L._other_holder(controller, document)
    if refusal:
        raise HeldElsewhere(refusal)

class NoGoalHere(ValueError):
    """This chat holds no goals: a worker's chat or a connected app's conversation (as ``SET`` refuses)."""

def choose(controller, check=G.REFERENCE):
    """The + menu's goal: a pending goal in the composer, started by the Send it goes with."""
    if getattr(controller, "external_client", "") or P.worker_chat(controller):
        raise NoGoalHere("This chat holds no goals")
    _check_document(controller)
    L._pending[controller.state["id"]] = G.new("", G.CHAT_BAR, check=check, paid=P.paid(controller),
                                             connection=[controller.state.get("connection_id"),
                                                         controller.state["provider"]])
    L._pending[controller.state["id"]]["status"] = G.PENDING
    controller.version = getattr(controller, "version", 0) + 1
    return L._pending[controller.state["id"]]

def set_option(controller, field, value):
    """The goal card's controls: the check (a goal not sent yet), pass threshold and budget limits."""
    goal = L._pending.get(controller.state["id"]) or L.goal_of(controller)
    if goal is None or G.ended(goal):
        return
    pending = goal is L._pending.get(controller.state["id"])
    if field == "check":
        if pending and value in (G.REFERENCE, G.JUDGED):
            goal["check"] = value
        return
    if field == "pass_at":
        G.set_pass_at(goal, value)
    elif field in ("minutes", "stall_rounds") and type(value) is int and value > 0:
        goal["budget"][field] = value
    elif field in ("usd", "tokens") and (value is None or type(value) in (int, float) and value > 0):
        goal["budget"][field] = value
        if pending:
            goal["spend_chosen"] = True   # The person's choice: billing read at Send keeps it (``_stamp``).
    else:
        return
    if not pending:
        L._save(controller, goal)

def discard_pending(controller):
    L._pending.pop(controller.state["id"], None)
    controller.version = getattr(controller, "version", 0) + 1

def start(controller):
    """The chip's Start on a proposed goal (Ask)."""
    goal = L.goal_of(controller)
    _check_document(controller)
    G.start(goal)
    L._save(controller, goal)
    L._mark(controller)
    L._continue.add(controller.state["id"])   # Sent once the chat is idle (``_ready``); a turn's end makes it due.

def resume(controller):
    goal = L.goal_of(controller)
    _check_document(controller)
    G.resume(goal)
    L._save(controller, goal)
    L._mark(controller)
    L._continue.add(controller.state["id"])   # Sent once the chat is idle (``_ready``); a turn's end makes it due.

def stop(controller):
    """The chip's Stop goal: the goal ends; a round of it that runs stops with it. The person's own turn under a
    paused or proposed goal is not the goal's, and goes on."""
    goal = L.goal_of(controller)
    if goal is None or G.ended(goal):
        return
    if L._round_running(controller, goal):
        L._origins[controller.state["id"]] = "person"   # The round's end ends the goal (``turn_ended``).
        controller.stop()
        return
    L._end(controller, goal, G.STOPPED, L.STOPPED_BY_PERSON)

def person_stopped(controller):
    """The chat's Stop (``ANGELO_OT_stop``), before ``Controller.stop``: the goal ends with the person's Stop."""
    goal = L.goal_of(controller)
    if goal is None or G.ended(goal):
        return
    if L._round_running(controller, goal):
        L._origins[controller.state["id"]] = "person"   # The round's end ends the goal (``turn_ended``).
        return
    L._end(controller, goal, G.STOPPED, L.STOPPED_BY_PERSON)

def allow_measure(controller):
    goal = L.goal_of(controller)
    G.allow_measure_request(goal)
    goal["measure_changed_in_round"] = True
    L._save(controller, goal)

def unlock(controller):
    goal = L.goal_of(controller)
    G.unlock(goal)
    L._save(controller, goal)

def dismiss(controller):
    """The ended chip's close: the goal is forgotten."""
    goal = L.goal_of(controller)
    if goal is not None and G.ended(goal):
        L._store(controller).remove(controller.state["id"])
        L._forget(controller.state["id"])
        controller.version = getattr(controller, "version", 0) + 1


# What the agent sees -----------------------------------------------------------------------------------------------

def observed(controller, goal=None):
    """The observation's state field while a goal runs: what is held, never its progress (sent when it changes)."""
    goal = goal if goal is not None else L.goal_of(controller) if controller is not None else None
    if not G.running(goal):
        return {}
    measure = {key: value for key, value in (goal.get("measure") or {}).items() if key != "locked"} or None
    limits = {key: value for key, value in goal["budget"].items() if value is not None}
    return {L.OBSERVED: {"objective": goal["objective"], "check": goal["check"], "pass_at": goal["pass_at"],
                       "limits": limits, "measure": measure}}


def observed_id(chat_id):
    """``observed`` by chat id (``turn_context``, which has no controller): the goal from whichever state folder's
    store holds it. One folder per process in the app; none at all before any goal was read."""
    from . import goal_store
    for store in list(goal_store._STORES.values()):
        goal = store.get(chat_id)
        if goal is not None:
            return observed(SimpleNamespace(state={"id": chat_id}), goal)
    return {}


def admits_round(controller, host_request):
    """Only the held goal's own scheduled continuation may use the main goal request kind."""
    return (host_request is not None and not getattr(controller, "external_client", "")
            and not P.worker_chat(controller) and L._sending.get(controller.state["id"]) is controller
            and G.running(L.goal_of(controller)))
