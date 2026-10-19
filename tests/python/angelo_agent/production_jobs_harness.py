# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The phase loop the production job suites share: approve-and-run through the real executor, checks, a receipt.

A suite builds one ``Harness``, registers ``harness.run(step)`` and writes its
phases as ``step(harness)``, which returns the seconds until its next call.
Requests go through ``BlenderExecutor`` and ``job_host`` with a stand-in
conversation (no provider, account or network), exactly as an approved call
does: ``precondition`` (the approval's facts), then ``execute``.
"""

import json
import os
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

MAX_STOP_SECONDS = 15.0  # Cycles stops between samples; simulation bakes between frames.
PHASE_SECONDS = 150


class Harness:
    def __init__(self, marker, report, default_dir, *, timeout=420):
        from angelo_agent.files import FileWorkspace
        self.marker, self.timeout = marker, timeout
        self.out = Path(os.environ.get("ANGELO_QA_DIR", default_dir))
        self.out.mkdir(parents=True, exist_ok=True)
        self.workspace = self.out / "workspace"
        self.workspace.mkdir(exist_ok=True)
        self.report = self.out / report
        self.state = {"phase": "boot", "success": False, "checks": {}, "stop_latency_s": {}, "overhead_s": {},
                      "fixture": "Real Blender jobs on tiny scenes; stand-in conversation, no provider, account or network"}
        self.ctx = {"facts": {}, "approvals": {}}
        self.samples = {}
        self.started = self.entered = time.monotonic()
        self.controller = SimpleNamespace(files=FileWorkspace(self.workspace), state={"id": "suite-chat"},
                                          executing_call=SimpleNamespace(id="call-1"))

    # The app -------------------------------------------------------------

    @staticmethod
    def window():
        return bpy.context.window_manager.windows[0]

    def executor(self):
        from angelo_agent.blender_tools import BlenderExecutor
        value = BlenderExecutor(self.window())
        value.studio_context = self.controller
        return value

    @staticmethod
    def jobs():
        from angelo_agent import job_ui
        return job_ui.manager()

    def rebind_workspace(self, root):
        """Bind the conversation's file workspace as the app does once the project is saved (ui.prepare)."""
        from angelo_agent.files import FileWorkspace
        self.workspace = Path(root)
        self.controller.files = FileWorkspace(self.workspace)

    # Requests ------------------------------------------------------------

    def start_job(self, arguments):
        """Approve-and-run as the controller does; keeps the approval's facts and text for the checks."""
        from angelo_agent.approvals import approval_message
        facts = json.loads(self.executor().precondition("angelo_job_start", arguments)[2])
        approval = approval_message("angelo_job_start", dict(arguments, _facts=facts))
        result = self.executor().execute("angelo_job_start", arguments)
        record = self.jobs().record(result["job"])
        self.ctx["facts"][record["id"]], self.ctx["approvals"][record["id"]] = facts, approval
        return record, result

    def refused(self, arguments):
        """The refusal an approval would never be offered for (its text), or None when the request is admitted."""
        try:
            self.executor().precondition("angelo_job_start", arguments)
        except Exception as error:
            return str(error)
        return None

    def cancel(self, record):
        self.ctx["cancel_at"] = time.monotonic()
        self.jobs().cancel(record["id"], by="user")

    def stopped(self, label):
        seconds = round(time.monotonic() - self.ctx["cancel_at"], 3)
        self.state["stop_latency_s"][label] = seconds
        self.check(label + "_stopped_within_bound", seconds < MAX_STOP_SECONDS)

    def running_with_progress(self, record):
        """True once the job runs with some progress; a job that ended first fails the phase (it was too fast)."""
        if ended(record):
            raise AssertionError("%s ended before it could be cancelled: %s" % (record["id"], record["status"]))
        return record["status"] == "running" and record["progress"] > 0.0

    def jobs_block(self):
        """What the next message of this chat tells a model about its jobs (the same text on every runtime)."""
        from angelo_agent import context_ledger
        text = context_ledger.TurnBlocks().jobs("suite-chat")
        return json.loads(text[len(context_ledger.JOBS_HEADER):]) if text else []

    def sample(self):
        record = self.jobs().active()
        if record is not None:
            self.samples.setdefault(record["id"], []).append(round(record["progress"], 4))

    def progress_ok(self, record, label):
        """Progress stayed in 0..1 and only grew; the receipt keeps how many samples were seen and their maximum."""
        values = self.samples.get(record["id"], [])
        self.state.setdefault("progress", {})[label] = {"samples": len(values), "max": max(values, default=None)}
        self.check(label + "_progress_in_range_and_growing", bool(values) and all(0.0 <= value <= 1.0 for value in values)
                   and all(b >= a for a, b in zip(values, values[1:])))

    # Checks and the receipt ---------------------------------------------

    def check(self, name, value):
        self.state["checks"][name] = bool(value)
        if not value:
            print(self.marker, "check failed:", name, flush=True)

    def hidden_left(self):
        return sorted(str(path.relative_to(self.workspace)) for path in self.workspace.rglob(".angelo-job-*"))

    def go(self, phase):
        self.state["phase"] = phase
        self.entered = time.monotonic()

    def finish(self, error=None):
        if error:
            self.state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
        else:
            self.state.update(phase="complete", success=bool(self.state["checks"]) and all(self.state["checks"].values()))
        self.state["elapsed_seconds"] = round(time.monotonic() - self.started, 1)
        self.report.write_text(json.dumps(self.state, indent=2, default=str))
        print(self.marker, json.dumps({"success": self.state["success"], "phase": self.state["phase"]}), flush=True)
        bpy.ops.wm.quit_blender()

    def run(self, step):
        """The timer callback of a suite: one phase step at a time, a receipt whatever happens."""
        def tick():
            try:
                assert time.monotonic() - self.started < self.timeout, "The suite timed out in " + self.state["phase"]
                if time.monotonic() - self.entered > PHASE_SECONDS:
                    raise AssertionError("Phase %s did not advance in %d s" % (self.state["phase"], PHASE_SECONDS))
                self.sample()
                return step(self)
            except Exception as error:
                self.finish(error)
                return None
        return tick


def ended(record):
    return record["status"] in {"done", "failed", "cancelled", "interrupted"}


def job_seconds(record):
    """Wall time of one job from its start to its end."""
    return round(record["ended"] - record["started"], 3)
