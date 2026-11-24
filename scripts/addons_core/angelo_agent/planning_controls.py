# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode's tool and the person's controls (doc/angelo/plan_mode.rst, "Flow", "Contract", "Safety").

* ``execute``: ``angelo_plan`` (``BlenderExecutor``): ``START`` (only narrows) and ``PROPOSE`` (only while planning).
* ``start`` / ``revise``: the chat becomes Read only with ``planning`` set (narrowing: anything may).
* ``go`` / ``stop``: the person's Go on a plan card and the planning chip's close. Both can widen, so both are grant
  entries (refused inside agent code) that also require C to admit their operator (``angelo.plan_go``,
  ``angelo.plan_stop``) in the scope they run in: the person's ticketed press, never Angelo's automatic decision.
* ``observed_id``: the observation's ``plan_mode`` state field while a chat plans.
* ``cancel_waiting_jobs``: as planning starts, the chat's queued jobs that change things are cancelled, never held.

Planning is Read only (``chat_settings.stored_options``), so no mutation is refused here: every Read only site
refuses it. Main thread; no bpy at import.
"""

from . import grant_authority, grant_guard
from . import planning_state as S
from . import planning_text as T
from .chat_settings import clean_options, planning, stored_options
from .display_text import Text
from .planning_spec import PROPOSE, START, TOOL_NAME, validate_plan
from .ui_text import notice

OBSERVED = "plan_mode"   # Model-visible (never translated): the observation's state field while a chat plans.
PLANNING = "PLANNING"
GO_OPERATOR, STOP_OPERATOR = "angelo.plan_go", "angelo.plan_stop"
BUILD_SETTINGS = ("ASK", "AUTO_BOUNDED")   # What Go may switch to (never Read only: that is planning's own).


class Refusal(ValueError):
    """A press that did nothing, with its sentence for the person (``args[0]`` is a ``display_text.Text``)."""


def is_planning(controller):
    return planning(controller.state.get("options"))


JOBS_CANCELLED_BY = "planning"   # A cancelled job's reason (``job_chat``: "Cancelled: its chat started planning").


def cancel_waiting_jobs(controller):
    """Cancel the chat's queued background jobs that the agent's calls admitted and that change things, as planning
    starts. Work admitted under the chat's earlier setting never runs while it plans, and no press that ends planning
    starts it later: nothing waits on the chat's Python state (break-it reviews S1 and S2 of e327003b8c2). Kept: the
    person's own queued presses (a card's Bake, Make Video's renders, the missing frames: no ``call_id``; planning
    restricts the agent, re-read of 55525904e80 S1), a look the agent asked for (``RENDER_PREVIEW``, which answers its
    own read-only call) and a job already running. Each job's own row in the chat says why (``job_chat``). Returns
    their IDs."""
    from . import job_manager
    from .job_spec import PREVIEW
    jobs = job_manager.shared()
    chat = controller.state.get("id")
    if jobs is None or not chat:
        return []
    cancelled = []
    for record in list(jobs.queued()):
        if (record.get("session") == chat and record.get("call_id") and record.get("kind") != PREVIEW
                and record.get("status") == "queued"):
            jobs.cancel(record["id"], by=JOBS_CANCELLED_BY)
            cancelled.append(record["id"])
    return cancelled


def planning_started(controller):
    """On every route into planning (``controller_entries``, even when the chat's save then fails): the agent's queued
    changes are cancelled and kept for its next request (``START``'s note, Go's text: re-read NIT 2), and a job of the
    chat already running is named, since it still changes the scene (re-read S2)."""
    cancelled = cancel_waiting_jobs(controller)
    controller._plan_cancelled = list(getattr(controller, "_plan_cancelled", None) or ()) + cancelled
    from . import job_chat, job_manager
    from .job_spec import PREVIEW
    jobs = job_manager.shared()
    running = jobs.active() if jobs is not None else None
    if running is not None and running.get("session") == controller.state.get("id") and running["kind"] != PREVIEW:
        controller.emit("message", {"role": "system", **notice(T.JOB_STILL_RUNS, job=Text(job_chat.heading(running)))})


# The tool ----------------------------------------------------------------------------------------------------------

def _refuse_start(controller):
    """Why ``START`` may not start planning in this request, or None (plan_mode.rst, "The tool")."""
    from .request_rows import persons_send
    from .goal_probes import worker_chat
    if not persons_send(controller):
        return "This request is Angelo's own, not the person's: planning starts only from the person's request"
    if getattr(controller, "voice", None) is not None:
        return "A voice turn does not plan; the person can choose Plan first in the chat bar"
    if getattr(controller, "external_client", "") or worker_chat(controller):
        return "This chat does not plan"
    if goal_held(controller):   # Planning is Read only, which ends a goal: only the person may trade theirs for it.
        return ("A goal the person set is held in this chat, and planning would end it. Ask the person: they can "
                "start planning from the chat bar's + menu, which ends the goal")
    return None


def goal_held(controller):
    """Whether the chat holds a goal the person set (active, paused or proposed) or has one chosen for its next
    message: planning would end it (Read only holds no goal, goal_mode.rst)."""
    from . import goal_loop, goal_state
    goal = goal_loop.goal_of(controller)
    return (goal is not None and not goal_state.ended(goal)) or goal_loop.pending_goal(controller) is not None


def execute(controller, window, arguments):
    """``angelo_plan``. Refusals are ValueError (the tool's error result)."""
    args = validate_plan(TOOL_NAME, arguments)
    if args["action"] == START:
        refusal = _refuse_start(controller)
        if refusal:
            raise ValueError(refusal)
        started = start(controller)
        cancelled = getattr(controller, "_plan_cancelled", None) or []
        return {"status": PLANNING, "note": ("This chat is read only now. Research and inspect; end the planning "
                                             "turn with angelo_plan PROPOSE." if started else
                                             "Already planning: the chat stays read only until the person's Go.")
                + (" " + S.cancelled_sentence(cancelled) if started and cancelled else "")}
    if args["action"] == PROPOSE and not is_planning(controller):
        raise ValueError("NOT_PLANNING: this chat is not in plan mode. START it first, only when the person asked to "
                         "plan before building")
    plan = {key: args[key] for key in ("summary", "approaches", "recommended", "questions")}
    return {"status": S.SHOWN, "plan": plan,
            "note": "Angelo shows this plan as a card and the person chooses on it: end this turn now."}


# Starting and revising (narrowing) ---------------------------------------------------------------------------------

def _build_setting(controller):
    """The setting Go preselects: Auto only while the chat is in Auto and C holds it, else Ask."""
    mode = clean_options(controller.state.get("options"))["approval"]   # RequireAskForEdits reads Auto as Ask.
    return "AUTO_BOUNDED" if mode == "AUTO_BOUNDED" and grant_authority.chat_in_auto(controller.state["id"]) else "ASK"


def start(controller, said=T.STARTED, person=False):
    """Start planning in ``controller``'s chat (``START``; the person's + menu row and Revise plan with ``person``).
    False when it already plans. A goal the chat holds ends only on the person's own press (``person``): ``START``
    refuses first (``_refuse_start``), and the notice and the chip say the goal ended."""
    if is_planning(controller):
        return False
    ended_goal = person and goal_held(controller)
    # Narrowing first: a start refused on its way (inside agent code, a widening build setting) ends no goal (break-it
    # review nit 4).
    controller.set_options(approval="READ_ONLY", planning=True, plan_return=_build_setting(controller))
    if ended_goal:
        from . import goal_controls
        goal_controls.discard_pending(controller)
        goal_controls.stop(controller)   # As the chip's Stop goal: the person's own end of it.
    controller._plan_ended_goal = bool(ended_goal)
    controller.emit("message", {"role": "system", **notice(T.ENDED_GOAL if ended_goal else said)})
    return True


def revise(controller):
    """The plan chip's Revise plan (the person's press): planning again, so their next message revises the plan."""
    return start(controller, T.REVISING, person=True)


# Go and Stop planning (the person's press only) --------------------------------------------------------------------

def _admitted(idname):
    """Whether C admits ``idname`` in the scope this runs in: the person's press (never Angelo's own decision, which
    admits only the operators listed for it, and never while an agent script runs). No C module: never."""
    guard = grant_authority.GUARD
    return guard is not None and bool(guard.admitted(idname))


def _send(controller, text):
    """The person's send path (Enter, the send arrow): the connection prepared, the observation built."""
    from . import console
    console.send_message(controller, text)


def _check_go(controller, call_id, approach, approval):
    """The plan Go builds, or raises ``Refusal`` with the person's sentence."""
    if approval not in BUILD_SETTINGS:
        raise ValueError("Go switches to Ask or Auto bounded")
    if not is_planning(controller):
        raise Refusal(Text(T.NOT_PLANNING))
    live_id, plan = S.live_plan(controller.state["messages"], True)
    if live_id is None or live_id != call_id:
        raise Refusal(Text(T.STALE))
    if type(approach) is not int or not 0 <= approach < len(plan["approaches"]):
        raise ValueError("No such approach in this plan")
    if approval == "AUTO_BOUNDED":
        from . import managed_policy
        if managed_policy.current().require_ask_for_edits:
            raise Refusal(Text(T.NO_AUTO))
    if controller.turn_running():
        raise Refusal(Text(T.BUSY))
    from . import edit_tool_hold
    if edit_tool_hold.running():   # The person's send would wait in the tray, without its choice: Go waits instead.
        raise Refusal(Text(T.TOOL_BUSY))
    return plan


def go(controller, call_id, approach, approval, notes=""):
    """The person's Go on plan ``call_id``'s ``approach``: the chat leaves planning in ``approval`` and the approach,
    with ``notes`` (what the message box held), is sent as the person's request. Raises ``Refusal`` (said in the
    chat by the caller) or ``PermissionError`` outside the person's press."""
    if not _admitted(GO_OPERATOR):
        raise PermissionError(grant_guard.NOT_YOURS)
    plan = _check_go(controller, call_id, approach, approval)
    back = stored_options(controller.state.get("options")).get("plan_return", "ASK")
    edited = S.marked(stored_options(controller.state.get("options")).get("plan_draft"), notes)   # Edit's copy (N2b).
    controller.set_options(approval=approval, planning=False)   # In the press's scope: Auto is held in C as theirs.
    controller._plan_choice = S.choice_fields(call_id, approach, plan, approval, notes, edited)
    try:
        _send(controller, S.go_text(call_id, approach, plan, approval, notes, edited,
                                    cancelled=getattr(controller, "_plan_cancelled", None) or ()))
        controller._plan_cancelled = []   # Told once, with the request that builds.
    except Exception as error:   # noqa: BLE001 - refused: planning comes back as it was, and the person is told why.
        controller.set_options(approval="READ_ONLY", planning=True, plan_return=back)
        raise Refusal(Text(T.NOT_SENT, reason=error.args[0] if error.args and isinstance(error.args[0], Text)
                           else str(error)[:300])) from error
    finally:
        controller._plan_choice = None
    return True


def stop(controller):
    """The planning chip's close: planning ends in Ask, nothing is sent. Never the build setting: that is the chat's
    Python state, which a script can write, and a press widens only to what it shows and its ticket covers (break-it
    review M1). Auto is the person's own choice in the approval control afterwards."""
    if not _admitted(STOP_OPERATOR):
        raise PermissionError(grant_guard.NOT_YOURS)
    if not is_planning(controller):
        return False
    controller.set_options(approval="ASK", planning=False)
    controller.emit("message", {"role": "system", **T.stopped_notice()})
    return True


# What the agent sees -----------------------------------------------------------------------------------------------

def observed(controller):
    """The observation's state field while the chat plans (``turn_context``)."""
    return {OBSERVED: PLANNING} if controller is not None and is_planning(controller) else {}


def observed_id(chat_id):
    """``observed`` by chat id (``turn_context`` has no controller): the chat this process hosts, else nothing."""
    import sys
    ui = sys.modules.get(__package__ + ".ui")
    controllers = getattr(ui, "_controllers", None) or {}
    return observed(controllers.get(chat_id))


go = grant_guard.entry(go)
stop = grant_guard.entry(stop)
