# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread handling of connected MCP apps' calls and tasks (part of ``external_host``).

Tool level (``app_call``): the app's own model calls Angelo's tools; each call
joins the app's open ``external_work.ExternalWork`` request in its External
conversation, or opens one. Task level (``task``): one turn of Angelo's own agent
in that conversation, on the conversation's connection (the person's
subscription unless they chose another), started as a typed message is. The
app reads its progress and result with ``task status``; the person approves,
stops and undoes it in Angelo. An app can never approve, decline or change a
mode. See doc/angelo/external_mcp.rst.

A document load invalidates an External conversation as it does any chat. A
CLI client rotates explicitly (``angelo session new``); an app's next call
moves it to a fresh conversation itself, because a model has no such command;
until the app has read there, a read runs with ``MOVED`` and anything else does
not run (``MOVED_NOT_RUN``); the call that was running when the file changed
says so too.
"""

import time
import uuid

from .external_clients import APP_NAMES, READ_TOOLS, TASKS, tools_for
from .claude_direct import _error
from .claude_direct_notes import NOT_KEPT
from .external_work import ExternalWork, call_key
from .invocations import document_digest
from .document_offer import person_only
from .ui_text import model_text, notice

TASK_ROWS = 12           # Progress rows a status returns (the latest ones).
TASK_TEXT = 1200         # Characters of one row's text.
CALLS_KEPT = 1024        # Answered calls remembered per host with their request (a delivered-again call's answer).
EVICTED_KEPT = 65536     # IDs of answered calls forgotten past CALLS_KEPT, so asked again they are told "not kept".
TASK_STATES = {"Completed": "done", "Failed": "failed", "Interrupted": "stopped", "Escalated": "done"}
# After an app's conversation moved to what the window shows (another scene or file), until a read's answer has gone
# out there: a read runs with MOVED first in its answer; anything else does not run (MOVED_NOT_RUN), so no edit runs
# before the model has been told. The app's model reads both.
MOVED = ("Angelo's window now shows another scene or file, so this call went to a fresh External conversation on what "
         "it shows now. Observe the scene before relying on names or handles from earlier calls.")
MOVED_NOT_RUN = ("Angelo's window now shows another scene or file; this app moved to a fresh External conversation on "
                 "what it shows now, and this call did not run. Observe the scene, then retry.")


def work_tools(client):
    """The tools an app's request may run inside Angelo. Headless, that is the headless set, plus
    ``angelo_execute_python`` for an app with the python level: ``RequestService`` refuses that tool from the app
    itself (``allowed_tools``), so only ``angelo_run_script_file`` reaches it (``ExternalWork``), and it runs only
    under the person's script file approval (``trusted_scripts``); anything else needs an approval no one can give
    without the window, so it ends as ``needs_gui``."""
    from .external_clients import PYTHON
    headless = getattr(client, "headless", False)
    allowed = tools_for(client.levels or (), headless=headless)
    if headless and PYTHON in (client.levels or ()):
        allowed = allowed | {"angelo_execute_python"}
    return allowed


def app_name(registry_record):
    return APP_NAMES.get((registry_record or {}).get("app"), APP_NAMES["other"])


class ExternalApps:
    """Mixed into ``ExternalHost``: ``self.env``, ``self.conversation``, ``self._reply``, ``self.apps``."""

    # Whether an app's conversation moved to the shown document stays unobserved until it reads there (``MOVED``,
    # ``MOVED_NOT_RUN``). False where every document change is said by the call that caused it (``HeadlessHost``).
    announce_moves = True
    answers_held = False   # The host sends each answer from the call's worker (``ExternalWork``).

    def _app_conversation(self, work):
        """The app's External conversation for the shown document, rotated after a load or a switch to another scene
        (an External conversation follows what the window shows: ``document_scene``); (controller, error)."""
        label = work.client.label
        controller = self.conversation(label, create=True)
        document = self.env.document_key()
        if controller.document_invalid or controller.document != document:
            # Its request can never take another call (the conversation rotates): closed now if nothing of it is in
            # flight, so only work still running here makes the app wait.
            self.settle(controller, rotating=True)
            if controller.pending or controller.busy or self._work_open(controller):
                return controller, ("stale", "Angelo's window now shows another scene or file; earlier work is still "
                                    "finishing. Retry shortly, then observe the scene again.")
            self.rotate(label, controller)
            controller = self.conversation(label, create=False)
        return controller, None

    def rotate(self, label, old):
        identity = self.create_conversation(label, previous=old.state["id"])
        self._unobserved.discard(old.state["id"])
        if self.announce_moves:
            self._unobserved.add(identity)
        old.emit("message", {"role": "system", **notice("The window now shows another scene or file; '{label}' "
                             "continues in conversation {identity}. This transcript stays readable.", label=label,
                             identity=identity)})
        return identity

    def _work_open(self, controller):
        work = self._work.get(controller.state["id"])
        return work is not None and not work.closed

    def settle(self, controller, rotating=False):
        """Close what of ``controller``'s earlier work is over and has nothing in flight, exactly as the tick's sweeps
        would (``close_finished``: a CLI read with no call pending; ``pump_apps``: an app request that cannot take
        another call, ``finishable``), before a new call or command is admitted. An answer goes out before the main
        thread's next sweep, and the client's next call can be admitted ahead of that sweep: it must never read the
        answered work as still running. ``rotating``: the conversation is about to move to a fresh one (its document
        is no longer shown), so its request ends whether or not it could have taken another call: without the window
        a reload tells no conversation (``headless_host``), so ``usable`` cannot see it. Work still in flight stays.
        """
        identity = controller.state["id"]
        fence = self._open.get(identity)
        if fence is not None and not controller.pending:
            controller.close_external_read(fence)
            del self._open[identity]
        current = self._work.get(identity)
        if current is None:
            return
        if not current.closed and (rotating or not current.usable()) and current.finishable():
            current.close("the request ended")
        if current.closed:
            del self._work[identity]

    # Tool level -------------------------------------------------------------------------------------

    def handle_app_call(self, work):
        payload, label = work.payload, work.client.label
        known = self._calls.get((label, payload["id"]))
        if known is not None:   # Delivered again (a waiting launcher asks every round): its answer, never a second run.
            return self._again(work, known, payload)
        if (label, payload["id"]) in self._evicted:   # Ran, answered and forgotten: said so, never run again.
            return self._reply(work, None, "tool_failed", "The call reported an error", result=_error(NOT_KEPT),
                               call={"replayed": True, "admitted": True})
        controller, error = self._app_conversation(work)
        if error:
            return self._reply(work, controller, *error)
        identity = controller.state["id"]
        self._settle_task(identity)
        self.settle(controller)
        if identity in self._open:   # A phase-1 read of this label is finishing; it shares no request with an app.
            return self._reply(work, controller, "busy", "Earlier reads are finishing; retry shortly")
        if (self._tasks.get(identity) or {}).get("held_text") is not None:   # Never interleaved with its held task.
            return self._reply(work, controller, "busy", "This app's task waits for the person's tool in Angelo; "
                               "retry once it has started")
        task = self._tasks.get(identity)
        if task is not None and controller.busy and not self._work_open(controller):
            return self._reply(work, controller, "busy", "Angelo is running a task for this app; wait for it "
                               "(angelo_task_status) or stop it (angelo_task_stop) before calling tools")
        unobserved = identity in self._unobserved   # Moved here (by a call or a task start), not told by a read yet.
        if unobserved and payload["tool"] not in READ_TOOLS:
            return self._reply(work, controller, "stale", MOVED_NOT_RUN)
        current = self._work.get(identity)
        if current is not None and not current.usable():
            if not current.finishable():
                return self._reply(work, controller, "busy", "The previous request is finishing in Angelo; retry shortly")
            current.close("the request ended")
            current = None
        if current is None or current.closed:
            if controller.busy or controller.pending:
                return self._reply(work, controller, "busy", "This External conversation is finishing earlier work")
            record = self.apps().get(label, {})
            current = self._work[identity] = ExternalWork(controller, label, app_name(record),
                                                          work_tools(work.client), answers_held=self.answers_held)
        # Captured here on the main thread: the reply is sent from the call's worker thread.
        fields = {"document": document_digest(repr(self.env.document_key())), "conversation": identity,
                  "projection_roots": tuple(self.env.private_roots())}

        note = MOVED if unobserved else None
        record = {"work": current, "fields": fields, "note": note}
        self._calls[(label, payload["id"])] = record
        self._forget_answered()
        respond = self.responder(work, fields, current=current, tool=payload["tool"])
        if not self.answers_held:   # Held answers (headless) are marked when they go out (headless_answers).
            respond = _marked(record, respond)
        current.submit(call_key(payload["id"]), payload["tool"], payload["arguments"],
                       _noted(respond, note, lambda: self._unobserved.discard(identity)))
        work.admitted = True

    def _forget_answered(self):
        """Past CALLS_KEPT, forget the oldest answered calls, keeping their IDs (EVICTED_KEPT): a call still unanswered
        is never forgotten, since its re-ask must find it and never run it again."""
        while len(self._calls) > CALLS_KEPT:
            oldest = next((key for key, record in self._calls.items() if answered(record)), None)
            if oldest is None:
                return
            del self._calls[oldest]
            self._evicted[oldest] = None
            while len(self._evicted) > EVICTED_KEPT:
                self._evicted.pop(next(iter(self._evicted)))

    def known_call(self, label, call_id):
        """Whether this host has had the call (kept, or answered and forgotten): a delivery of it is a re-ask."""
        return (label, call_id) in self._calls or (label, call_id) in self._evicted

    def _again(self, work, known, payload):
        """A call ID this host has had: wait on, or get, the first delivery's answer from the request that ran it."""
        known["work"].submit(call_key(payload["id"]), payload["tool"], payload["arguments"],
                             _noted(self.responder(work, known["fields"], replayed=True, current=known["work"],
                                                   tool=payload["tool"]), known.get("note")))
        work.admitted = True

    def responder(self, work, fields, replayed=False, current=None, tool=None):
        """The answer for one delivery of an app call; the headless host saves edits before answering."""
        return _responder(work, fields, replayed=replayed)

    def finish_closing(self):
        """Close the requests access-off left running once nothing of theirs is in flight (never stuck busy)."""
        for current in list(self._closing):
            if current.closed or current.finishable():
                current.close("access for external apps was turned off")
                self._closing.remove(current)

    def pump_apps(self):
        """Main thread, each tick: close requests that ended or went quiet; answer task waiters."""
        self.finish_closing()
        now = time.monotonic()
        from . import edit_tool_hold
        for identity, task in list(self._tasks.items()):
            controller = self.env.controllers().get(identity)
            busy = controller is not None and (controller.busy or controller.pending or identity in self._open)
            if task.get("held_text") is not None and not edit_tool_hold.running() and not busy:
                self._start_held(identity, task)   # The admission an immediate start makes, once nothing holds it.
        for identity in list(self._tasks):
            self._settle_task(identity)
        for identity, current in list(self._work.items()):
            if current.closed:
                del self._work[identity]
                continue
            ended = not current.usable() or current.idle(now)
            if ended and current.finishable():
                current.close("the app finished" if current.usable() else "the request ended")
                del self._work[identity]
        for waiter in list(self._waiters):
            work, identity, deadline = waiter
            if self._task_terminal(identity) or now >= deadline or work.response is not None:
                self._waiters.remove(waiter)
                self._task_reply(work, identity)

    def close_apps(self, why):
        for current in list(self._work.values()):
            current.controller.cancel_tools(why)
            if current.finishable():
                current.close(why)
            else:
                self._closing.append(current)
        self._work.clear()
        for task in self._tasks.values():   # A task still waiting for the person's operation never starts later.
            if task.get("held_text") is not None:
                task.update(held_text=None, final="stopped", ended=time.monotonic())
        for work, identity, _deadline in self._waiters:
            self._task_reply(work, identity)
        self._waiters.clear()

    # Task level -------------------------------------------------------------------------------------

    def handle_task(self, work):
        action, label = work.payload.get("action"), work.client.label
        if TASKS not in (work.client.levels or ()):
            return self._reply(work, None, "refused", "The person has not allowed this app to run Angelo tasks "
                               "(Preferences > AI Providers > External AI apps)")
        if action == "start":
            return self._task_start(work, label)
        controller = self.conversation(label, create=False)
        identity = controller.state["id"] if controller is not None else None
        task = self._tasks.get(identity)
        if task is None or task["id"] != work.payload.get("task"):
            return self._reply(work, controller, "invalid", "No such task in this app's current conversation")
        if action == "stop":
            if task.get("held_text") is not None:   # Never started: it ends here.
                task.update(held_text=None, final="stopped", ended=time.monotonic())
            elif controller.busy and not self._work_open(controller):
                controller.stop()
            return self._task_reply(work, identity)
        wait = work.payload.get("wait", 0)
        if wait and not self._task_terminal(identity):
            self._waiters.append((work, identity, time.monotonic() + wait))
            return None
        return self._task_reply(work, identity)

    def _task_start(self, work, label):
        controller, error = self._app_conversation(work)   # Its agent observes the scene; the app is told later.
        if error:
            return self._reply(work, controller, *error)
        identity = controller.state["id"]
        self.settle(controller)
        current = self._work.get(identity)
        if current is not None and not current.closed:
            if not current.finishable():
                return self._reply(work, controller, "busy", "This app's tool calls are still running; retry shortly")
            current.close("the app started a task")
            del self._work[identity]
        held = (self._tasks.get(identity) or {}).get("held_text") is not None
        if controller.busy or controller.pending or identity in self._open or held:
            return self._reply(work, controller, "busy", "A task or call is still running for this app")
        from . import edit_tool_hold
        text = work.payload.get("text", "")
        if edit_tool_hold.running():   # Its start snapshots the document: it waits for the person's operation, as every
            # call does, and the app has its task now (break-it review 3, S4); ``pump_apps`` starts it.
            self._tasks[identity] = {"id": str(uuid.uuid4()), "start": len(controller.state["messages"]),
                                     "started": time.monotonic(), "final": None, "held_text": text}
            edit_tool_hold.note_waiting()
            return self._task_reply(work, identity)
        try:
            self.env.prepare_task(controller)
            start = len(controller.state["messages"])
            controller.send_external_task(text)
        except (RuntimeError, ValueError) as error:
            return self._reply(work, controller, "not_applied", str(error))
        self._tasks[identity] = {"id": str(uuid.uuid4()), "start": start, "started": time.monotonic(), "final": None}
        return self._task_reply(work, identity)

    def _start_held(self, identity, task):
        """A task that waited for the person's operation starts now, as it would have at once: its conversation still
        this app's, on the document it was given, with no work of its own open (break-it review 4, S-B)."""
        text, task["held_text"] = task["held_text"], None
        controller = self.env.controllers().get(identity)
        try:
            if controller is None or controller.document_invalid:
                raise RuntimeError("The task's conversation is gone")
            current = self._work.get(identity)
            if current is not None and not current.closed:
                current.close("the app started a task")
                del self._work[identity]
            self.env.prepare_task(controller)
            task["start"] = len(controller.state["messages"])
            controller.send_external_task(text)
        except (RuntimeError, ValueError):
            task.update(final="failed", ended=time.monotonic())

    def _task_terminal(self, identity):
        """Whether the app's task has ended: its turn (Auto's escalated re-run included) is no longer running."""
        controller = self.env.controllers().get(identity)
        task = self._tasks.get(identity)
        return (controller is None or task is None or task["final"] is not None
                or task.get("held_text") is None and (not controller.busy or self._work_open(controller)))

    def _settle_task(self, identity):
        """Record a task's final state once its turn ends, before anything later (a tool request) changes the chat."""
        task, controller = self._tasks.get(identity), self.env.controllers().get(identity)
        if task is None or task["final"] is not None or controller is None or not self._task_terminal(identity):
            return
        stopped = controller.cancelled or controller.status == "Interrupted"
        task["final"] = "stopped" if stopped else TASK_STATES.get(controller.status, "done")
        task["ended"] = time.monotonic()

    def _task_reply(self, work, identity):
        controller = self.env.controllers().get(identity)
        task = self._tasks.get(identity)
        if controller is None or task is None:
            return self._reply(work, controller, "not_applied", "The task's conversation is gone")
        rows = controller.state["messages"][task["start"]:]
        from .approval_wait import awaiting
        waiting = awaiting(controller) is not None   # The card itself, never an activity row that outlived it.
        self._settle_task(identity)
        if task.get("held_text") is not None:
            state = "waiting_for_person_in_angelo"   # The person's operation (``held``): it starts once that ends.
        elif not self._task_terminal(identity):
            state = "waiting_for_approval_in_angelo" if waiting else "running"
        else:
            state = task["final"]
        # The agent's reply; a reply-less edit ends with the host's verified account instead (a system row).
        reply = next((row.get("text", "") for row in reversed(rows) if row.get("role") == "assistant"
                      and row.get("text")), "") or next((row.get("text", "") for row in reversed(rows)
                                                         if row.get("role") == "system" and row.get("host_summary")), "")
        from .approval_wait import status_for_apps
        from .edit_tool_hold import seen
        result = {"task_id": task["id"], "state": state, "status": status_for_apps(controller),
                  **({"held": seen()} if task.get("held_text") is not None and seen() else {}),
                  "elapsed_s": round(task.get("ended", time.monotonic()) - task["started"], 1),
                  "progress": [_row(row) for row in rows[-TASK_ROWS:] if _row(row)],
                  "reply": reply[-TASK_TEXT * 4:] if state != "running" else "",
                  "checkpoint": controller.request_checkpoint,
                  "connection": _route(controller)}
        waiting_states = {"waiting_for_approval_in_angelo", "waiting_for_person_in_angelo"}
        code = "ok" if state in {"done", "running"} | waiting_states else (
            "not_applied" if state == "stopped" else "tool_failed")
        return self._reply(work, controller, code, state, result=result)


def answered(record):
    """Whether a call's answer went out: marked by ``_marked`` in the window, kept as ``answer`` without it."""
    return bool(record.get("answered")) or "answer" in record


def _marked(record, respond):
    def respond_and_mark(value):
        record["answered"] = True
        respond(value)
    return respond_and_mark


def _responder(work, fields, replayed=False):
    """The CLI answer for one delivery of an app call (called on the call's worker thread)."""
    def respond(value):
        failed = bool(isinstance(value, dict) and value.get("isError"))
        work.complete(dict(fields, code="tool_failed" if failed else "ok", result=value,
                           message="Completed" if not failed else "The call reported an error",
                           call={"replayed": replayed, "admitted": True}))
    return respond


def _noted(respond, note, sending=None):
    """``respond`` with ``note`` first in the MCP result's text; ``sending`` runs just before it goes out (any thread),
    so the client's next call never finds it unsent."""
    if not note:
        return respond

    def noted(value):
        if isinstance(value, dict) and isinstance(value.get("content"), list):
            value = dict(value, content=[{"type": "text", "text": note}, *value["content"]])
        if sending is not None:
            sending()
        respond(value)
    return noted


def _row(row):
    role = row.get("role")
    if role == "activity":
        return {"tool": row.get("tool", ""), "status": row.get("status", "")}
    if role in {"assistant", "system", "error"} and row.get("text") and not person_only(row):
        return {"role": role, "text": model_text(row)[:TASK_TEXT]}   # An external app is a model.
    return None


def _route(controller):
    """Which kind of the person's connections ran the task, never an account identifier."""
    from .provider_registry import TEAM_MODES, billing_kind, runtime_kind
    provider = controller.state.get("provider") or ""
    try:
        kind = runtime_kind(provider)
    except (KeyError, ValueError):
        kind = ""
    try:   # A custom endpoint is billed by the organization's own server: an API key's.
        billing = "team" if provider in TEAM_MODES else "API key" if billing_kind(provider) == "API_KEY" else "subscription"
    except ValueError:
        billing = "subscription"
    return {"runtime": kind, "billing": billing, "model": (controller.state.get("options") or {}).get("model") or ""}


def waiting(controllers, active_id, chat_shown=True):
    """External subset of unseen cards; a hidden active chat is unseen too."""
    from .approval_wait import unseen
    return [(controller.external_client, controller.state["id"])
            for controller in unseen(controllers, active_id, chat_shown)
            if getattr(controller, "external_client", "")]
