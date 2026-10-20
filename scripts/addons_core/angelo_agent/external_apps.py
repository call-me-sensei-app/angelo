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
moves it to a fresh conversation itself, because a model has no such command,
and the call that was running when the file changed says so.
"""

import time
import uuid

from .external_clients import APP_NAMES, TASKS, tools_for
from .external_work import ExternalWork
from .invocations import document_digest
from .ui_text import notice

TASK_ROWS = 12           # Progress rows a status returns (the latest ones).
TASK_TEXT = 1200         # Characters of one row's text.
CALLS_KEPT = 1024        # Call IDs remembered per host (external_apps: a delivered-again call never runs twice).
TASK_STATES = {"Completed": "done", "Failed": "failed", "Interrupted": "stopped", "Escalated": "done"}


def app_name(registry_record):
    return APP_NAMES.get((registry_record or {}).get("app"), APP_NAMES["other"])


class ExternalApps:
    """Mixed into ``ExternalHost``: ``self.env``, ``self.conversation``, ``self._reply``, ``self.apps``."""

    def _app_conversation(self, work):
        """The app's External conversation for the open document, rotated after a load; (controller, error)."""
        label = work.client.label
        controller = self.conversation(label, create=True)
        document = self.env.document_key()
        if controller.document_invalid or controller.document != document:
            if controller.pending or controller.busy or self._work_open(controller):
                return controller, ("stale", "The Blender file changed in Angelo; earlier work is still finishing. "
                                    "Retry shortly, then observe the scene again.")
            self.rotate(label, controller)
            controller = self.conversation(label, create=False)
        return controller, None

    def rotate(self, label, old):
        identity = self.create_conversation(label, previous=old.state["id"])
        old.emit("message", {"role": "system", **notice("A new file was opened; '{label}' continues in conversation "
                             "{identity}. This transcript stays readable.", label=label, identity=identity)})
        return identity

    def _work_open(self, controller):
        work = self._work.get(controller.state["id"])
        return work is not None and not work.closed

    # Tool level -------------------------------------------------------------------------------------

    def handle_app_call(self, work):
        payload, label = work.payload, work.client.label
        known = self._calls.get((label, payload["id"]))
        if known is not None:   # Delivered again (a waiting launcher asks every round): its answer, never a second run.
            return self._again(work, known, payload)
        controller, error = self._app_conversation(work)
        if error:
            return self._reply(work, controller, *error)
        identity = controller.state["id"]
        self._settle_task(identity)
        if identity in self._open:   # A phase-1 read of this label is finishing; it shares no request with an app.
            return self._reply(work, controller, "busy", "Earlier reads are finishing; retry shortly")
        task = self._tasks.get(identity)
        if task is not None and controller.busy and not self._work_open(controller):
            return self._reply(work, controller, "busy", "Angelo is running a task for this app; wait for it "
                               "(angelo_task_status) or stop it (angelo_task_stop) before calling tools")
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
                                                          tools_for(work.client.levels or ()))
        # Captured here on the main thread: the reply is sent from the call's worker thread.
        fields = {"document": document_digest(repr(self.env.document_key())), "conversation": identity,
                  "projection_roots": tuple(self.env.private_roots())}

        record = {"work": current, "fields": fields}
        self._calls[(label, payload["id"])] = record
        while len(self._calls) > CALLS_KEPT:
            self._calls.pop(next(iter(self._calls)))
        current.submit(("call", payload["id"]), payload["tool"], payload["arguments"], _responder(work, fields))
        work.admitted = True

    def _again(self, work, known, payload):
        """A call ID this host has had: wait on, or get, the first delivery's answer from the request that ran it."""
        known["work"].submit(("call", payload["id"]), payload["tool"], payload["arguments"],
                             _responder(work, known["fields"], replayed=True))
        work.admitted = True

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
            if controller.busy and not self._work_open(controller):
                controller.stop()
            return self._task_reply(work, identity)
        wait = work.payload.get("wait", 0)
        if wait and not self._task_terminal(identity):
            self._waiters.append((work, identity, time.monotonic() + wait))
            return None
        return self._task_reply(work, identity)

    def _task_start(self, work, label):
        controller, error = self._app_conversation(work)
        if error:
            return self._reply(work, controller, *error)
        identity = controller.state["id"]
        current = self._work.get(identity)
        if current is not None and not current.closed:
            if not current.finishable():
                return self._reply(work, controller, "busy", "This app's tool calls are still running; retry shortly")
            current.close("the app started a task")
            del self._work[identity]
        if controller.busy or controller.pending or identity in self._open:
            return self._reply(work, controller, "busy", "A task or call is still running for this app")
        text = work.payload.get("text", "")
        try:
            self.env.prepare_task(controller)
            start = len(controller.state["messages"])
            controller.send_external_task(text)
        except (RuntimeError, ValueError) as error:
            return self._reply(work, controller, "not_applied", str(error))
        self._tasks[identity] = {"id": str(uuid.uuid4()), "start": start, "started": time.monotonic(), "final": None}
        return self._task_reply(work, identity)

    def _task_terminal(self, identity):
        """Whether the app's task has ended: its turn (Auto's escalated re-run included) is no longer running."""
        controller = self.env.controllers().get(identity)
        task = self._tasks.get(identity)
        return (controller is None or task is None or task["final"] is not None or not controller.busy
                or self._work_open(controller))

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
        waiting = any(row.get("role") == "activity" and row.get("status") == "approval" for row in rows)
        self._settle_task(identity)
        if not self._task_terminal(identity):
            state = "waiting_for_approval_in_angelo" if waiting else "running"
        else:
            state = task["final"]
        # The agent's reply; a reply-less edit ends with the host's verified account instead (a system row).
        reply = next((row.get("text", "") for row in reversed(rows) if row.get("role") == "assistant"
                      and row.get("text")), "") or next((row.get("text", "") for row in reversed(rows)
                                                         if row.get("role") == "system" and row.get("host_summary")), "")
        result = {"task_id": task["id"], "state": state, "status": controller.status,
                  "elapsed_s": round(task.get("ended", time.monotonic()) - task["started"], 1),
                  "progress": [_row(row) for row in rows[-TASK_ROWS:] if _row(row)],
                  "reply": reply[-TASK_TEXT * 4:] if state != "running" else "",
                  "checkpoint": controller.request_checkpoint,
                  "connection": _route(controller)}
        code = "ok" if state in {"done", "running", "waiting_for_approval_in_angelo"} else (
            "not_applied" if state == "stopped" else "tool_failed")
        return self._reply(work, controller, code, state, result=result)


def _responder(work, fields, replayed=False):
    """The CLI answer for one delivery of an app call (called on the call's worker thread)."""
    def respond(value):
        failed = bool(isinstance(value, dict) and value.get("isError"))
        work.complete(dict(fields, code="tool_failed" if failed else "ok", result=value,
                           message="Completed" if not failed else "The call reported an error",
                           call={"replayed": replayed, "admitted": True}))
    return respond


def _row(row):
    role = row.get("role")
    if role == "activity":
        return {"tool": row.get("tool", ""), "status": row.get("status", "")}
    if role in {"assistant", "system", "error"} and row.get("text"):
        return {"role": role, "text": str(row["text"])[:TASK_TEXT]}
    return None


def _route(controller):
    """Which kind of the person's connections ran the task, never an account identifier."""
    from .provider_registry import TEAM_MODES, runtime_kind
    provider = controller.state.get("provider") or ""
    try:
        kind = runtime_kind(provider)
    except (KeyError, ValueError):
        kind = ""
    billing = ("team" if provider in TEAM_MODES else "API key" if provider.endswith("_api") else "subscription")
    return {"runtime": kind, "billing": billing, "model": (controller.state.get("options") or {}).get("model") or ""}


def waiting(controllers, active_id):
    """``(client name, session id)`` of External conversations with an approval card the person is not looking at."""
    found = []
    for controller in controllers:
        label = getattr(controller, "external_client", "")
        if not label or controller.state.get("id") == active_id:
            continue
        if any(call.approval and not call.executing and not call.automatic for call in list(controller.pending.values())):
            found.append((label, controller.state["id"]))
    return found
