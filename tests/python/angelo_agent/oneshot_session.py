# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot duties of Angelo's trial driver, inside the app (``scenario_session.for_trial``).

A one-shot test is a scenario session with one turn and these rules on top:

* **One message.** The chat's ``send`` is wrapped when the brief's
  references are attached: a second call raises, whoever makes it. The
  driver has one task, so it never tries; the wrapper makes that a property
  of the session instead of a habit of the task list.
* **Approvals.** Whatever only edits the scene is approved, as a person
  building a scene would: the typed scene tools (sculpting and preview
  acceptance included), reviewed Python and the render job. Files, accounts
  and billed generators stay declined (``approval_decision``). The driver
  counts every approval and the receipt times the wait.
* **Cost cap.** A timer reads the request's model usage once a second and
  prices it at list prices (``oneshot_metrics.cap_cost``); past the cap it
  presses Stop and records why. That usage is summed over the request's
  model calls, so for a model with a per-request price step whose sum
  exceeds the step the cap uses an upper bound (marked ``bound``), never
  no cap. The time cap is the driver's own task timeout.
* **The still.** The brief's file is looked for in the chat's workspace,
  the trial's temporary folders, where the scene's render path points and at
  any path the agent itself stated; it must have been written after the
  message was sent, and is copied into the trial folder for the harness's
  pixel check.
* **The path.** The message's chat rows are kept as compact facts
  (``oneshot_metrics.summarize_rows``) for the report.

Nothing here changes the add-on: it reads the controller's state as the
driver already does. Importable without Blender; ``bpy`` is passed in.
"""

import json
import os
import shutil
import tempfile
import time

import oneshot_checks
import oneshot_metrics as metrics
import oneshot_produced
import scenario_checks
import scenario_hygiene
import scenario_session
import scenario_specs

COST_CAP_ENV = "ANGELO_EVAL_COST_CAP"
WATCH_INTERVAL_S = 1.0
ONE_MESSAGE = "A one-shot test sends exactly one message; a second one was refused"
# Never approved, whatever else changes: they touch files, accounts, other chats or billed generators.
NEVER = ("angelo_studio_", "angelo_flow_", "angelo_team_")


def approvable():
    """Tools the tester approves: typed scene edits, reviewed Python and the render job."""
    from angelo_agent import flow_spec, tool_specs
    return (tool_specs.BOUNDED_MUTATIONS - flow_spec.FLOW_EDITS) | {"angelo_execute_python", "angelo_job_start"}


def approval_decision(tool_name, python="approve"):
    """``approve`` for what only edits the scene (or renders it as a job); everything else is declined."""
    allowed = approvable() - (set() if python == "approve" else {"angelo_execute_python"})
    return "approve" if tool_name in allowed and not tool_name.startswith(NEVER) else "decline"


def once(send, state):
    """``send`` wrapped so that it can be called once; ``state["messages"]`` counts the calls made."""
    def guarded(*args, **kwargs):
        if state.get("messages"):
            state["refused"] = state.get("refused", 0) + 1
            raise RuntimeError(ONE_MESSAGE)
        state["messages"] = 1
        state["sent_wall"] = time.time()
        return send(*args, **kwargs)
    return guarded


def running_usage(controller):
    """The request's model usage so far, or None.

    An add-on that offers ``telemetry.running_usage`` is asked (its finished provider turns plus the one in
    progress). An older one is read as before: the receipt in progress, else Codex's running thread total.
    """
    reader = getattr(getattr(controller, "telemetry", None), "running_usage", None)
    if callable(reader):
        usage = reader(controller.state["id"])
        if isinstance(usage, dict) and usage.get("input_tokens"):
            return dict(usage)
    found = []
    request = getattr(controller, "_request_metrics", None)
    if isinstance(getattr(request, "usage", None), dict) and request.usage.get("input_tokens"):
        found.append(dict(request.usage))
    tracker = getattr(getattr(controller, "provider", None), "_usage", None)
    if isinstance(getattr(tracker, "total", None), dict) and tracker.total.get("input_tokens"):
        found.append(dict(tracker.total, requests=getattr(tracker, "updates", None)))
    return max(found, key=lambda usage: usage.get("input_tokens") or 0) if found else None


class OneShotSession(scenario_session.ScenarioSession):
    def __init__(self, task_set, out, python_approvals="approve", demo=None, stage_copies=False):
        super().__init__(task_set, out, python_approvals, demo, stage_copies)
        self.limits = scenario_specs.limits(self.name)
        self.cost_cap = float(os.environ.get(COST_CAP_ENV) or 0) or self.limits["cost_usd"]
        self.model = os.environ.get("ANGELO_EVAL_MODEL", "")
        self.state = {"messages": 0, "stop": None, "watch": {}}
        self.bpy = self.controller = None
        self.first_row, self.finished = 0, False
        self.report["oneshot"] = {"limits": dict(self.limits), "cost_cap_usd": self.cost_cap}

    def begin(self, bpy, window):
        self.bpy = bpy
        return super().begin(bpy, window)

    def attach(self, controller, task, record):
        """The brief's references; from here on the chat can be sent to once, and its usage is watched."""
        if self.controller is None:
            self.controller, self.first_row = controller, len(controller.state["messages"])
            controller.send = once(controller.send, self.state)
            if self.bpy is not None:
                self.bpy.app.timers.register(self.watch, first_interval=WATCH_INTERVAL_S)
        super().attach(controller, task, record)

    def decide(self, tool_name):
        return approval_decision(tool_name, self.python_approvals)

    def price(self, usage):
        """``(USD, bound)`` of the usage so far (``oneshot_metrics.cap_cost``): the running usage is summed over the
        request's model calls, so a model with a price step is capped on an upper bound (``bound`` "upper").
        Unsplit cache writes are priced at the chat's session TTL (5 minutes on an API-key or team route), or at
        1 hour when the controller does not say."""
        route = getattr(self.controller, "turn_options", None) or {}
        session_ttl = getattr(self.controller, "session_cache_ttl", None)
        ttl = (session_ttl() if callable(session_ttl) else None) or "1h"
        return metrics.cap_cost(usage, self.model or route.get("model"), ttl)

    def watch(self):
        """Once a second while the message is worked on: the usage so far against the cost cap."""
        controller = self.controller
        if self.finished or controller is None:
            return None
        usage = running_usage(controller)
        if usage:
            cost, bound = self.price(usage)
            # ``cost_usd`` is the list price (None when unknown); an upper bound is kept apart, as the cap's own.
            self.state["watch"] = {"usage": usage, "cost_usd": None if bound else cost,
                                   "requests": usage.get("requests")}
            self.state["watch"].update({"cap_cost_usd": cost, "cap_bound": bound} if bound else {})
            if cost is not None and cost >= self.cost_cap and self.state["stop"] is None and controller.busy:
                self.state["stop"] = {"reason": "cost_cap", "cost_usd": cost, "cap_usd": self.cost_cap,
                                      **({"bound": bound} if bound else {})}
                controller.stop()
        return WATCH_INTERVAL_S

    def output_roots(self, workspace):
        roots = [workspace, getattr(self.bpy.app, "tempdir", None) if self.bpy else None, tempfile.gettempdir(),
                 str(self.out)]
        return [root for root in dict.fromkeys(roots) if root]

    def grade(self, bpy, scene, task, record, workspace=None, window=None):
        """The brief's closed checks, its still, the state digest and the message's path facts."""
        rows = self.controller.state["messages"][self.first_row:] if self.controller is not None else []
        # The agent was asked to say where the still is: its reply, scripts and job requests may name the path.
        said = "\n".join("%s %s" % (row.get("text") or "", json.dumps(row.get("arguments") or "", default=str))
                         for row in rows if isinstance(row, dict) and row.get("role") in ("assistant", "activity"))
        roots, render_path = self.output_roots(workspace), bpy.path.abspath(scene.render.filepath)
        result = scenario_checks.evaluate(bpy, scene, task["scenario_checks"])
        record["jobs"] = self.job_records()
        # The render judged is the one the agent names as final: its reply, then the jobs' receipts (final_render).
        result = oneshot_checks.with_outputs(result, task.get("scenario_outputs"), roots,
                                             self.state.get("sent_wall"), render_path, said,
                                             scenario_session.final_reply(rows), record["jobs"])
        record["host_check"] = result
        record["state_digest"] = scenario_hygiene.state_digest(bpy, scene)
        self.report["digests"].append(record["state_digest"])
        record["oneshot"] = {"user_messages": sum(row.get("role") == "user" for row in rows if isinstance(row, dict)),
                             "sends": self.state["messages"], "refused_sends": self.state.get("refused", 0),
                             "sent_wall": self.state.get("sent_wall"),   # As the MCP side's: a re-grade needs it.
                             "rows": metrics.summarize_rows(rows), "stop": self.state["stop"],
                             "watch": self.state["watch"], "cost_cap_usd": self.cost_cap,
                             "still": self.keep_still(result),
                             # Every image file the run wrote, whatever it is called: a missing still is then
                             # "rendered something else" or "rendered nothing", not a guess.
                             "produced": oneshot_produced.images(roots, self.state.get("sent_wall"),
                                                                 render_path, said)}
        if self.stage_copies and window is not None:
            self.stage_copy(bpy, window, task)
        return result["success"]

    def keep_still(self, result):
        """A copy of the still that passed, inside the trial folder: Blender's own temporary folder is gone later."""
        path = next((entry.get("path") for entry in result["results"] if entry.get("kind") == "output"
                     and entry.get("passed")), None)
        if not path:
            return None
        try:
            return shutil.copy2(path, self.out / ("agent-final" + os.path.splitext(path)[1].lower()))
        except OSError as error:
            return "failed: %s" % str(error)[:200]

    def finish(self, bpy, window, probe=True):
        self.finished = True
        return super().finish(bpy, window, probe)
