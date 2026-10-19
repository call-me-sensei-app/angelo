# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Jobs inside a Claude plan: what their approval says about the build, and a cancel the next step can rely on.

* A job started after the plan's own script failed a self-check renders that build; nothing holds it (tools aid,
  never handicap), but its approval states the checks failing now, as a fact (``self_checks.SelfChecks.sharer`` ->
  ``job_host.precondition`` -> ``job_approvals``).
* A cancel leaves a running job stopping for up to a few seconds, so a fix after it in the same plan met a busy
  scene (WRITER_BUSY). The plan now lets the job end, within ``plan_recovery.STOP_WAIT``, before its next step.
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_protocol_fixtures import host  # noqa: E402
from angelo_agent import job_host, plan_recovery  # noqa: E402
from angelo_agent.job_approvals import message  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402

BUILD = {"tool": "angelo_execute_python", "arguments": {"description": "Build the castle.", "code": "result = 1"}}
STILL = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "castle-final.png"}}
CANCEL = {"tool": "angelo_job_cancel", "arguments": {"job": "j1"}}
FAILING = {"success": True, "result": 1, "output": "", "self_checks": [
    {"name": "crown", "measured": 3.5, "target": 5.5, "tolerance": 0.05, "passed": False}]}


def plan(*operations, reply=""):
    return {"reply": reply, "operations": list(operations), "verify": False}


class OpenChecksOnTheApprovalTests(unittest.TestCase):
    def run_plan(self, plans, results):
        context = host()
        context.allowed_tools = None
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        seen = {}

        def native(tool, arguments, owner=None):
            seen.setdefault(tool, list(getattr(context, "open_self_checks", ())))
            return results.pop(0) if results else {"context_version": 2, "objects": []}
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=plans)
        provider._native_call = Mock(side_effect=native)
        provider.run(None, "Build a castle and render it", observation={})
        return context, seen

    def test_a_job_after_a_failing_script_carries_the_checks_to_its_approval(self):
        context, seen = self.run_plan([plan(BUILD, STILL), plan(reply="Rendered; the crown check is still open.")],
                                      [FAILING, {"success": True, "job": "j1", "status": "queued"}])
        self.assertEqual(seen["angelo_job_start"], ["crown: measured 3.5, target 5.5 (tolerance 0.05)"])
        self.assertEqual(context.open_self_checks, [])   # Cleared after the plan: no later approval inherits them.

    def test_a_passing_build_carries_nothing(self):
        passing = {"success": True, "result": 1, "output": "", "self_checks": [
            {"name": "crown", "measured": 5.5, "target": 5.5, "tolerance": 0.05, "passed": True}]}
        _context, seen = self.run_plan([plan(BUILD, STILL)], [passing, {"success": True, "job": "j1"}])
        self.assertEqual(seen["angelo_job_start"], [])

    def test_the_precondition_and_the_card_state_them_as_a_fact(self):
        runner = types.SimpleNamespace(describe=lambda: {}, output=None, workspace=None)
        jobs = types.SimpleNamespace(admit=lambda request: None)
        job_ui = types.ModuleType("angelo_agent.job_ui")
        job_ui.manager = lambda: jobs
        controller = types.SimpleNamespace(open_self_checks=["crown: measured 3.5, target 5.5 (tolerance 0.05)"])
        window = types.SimpleNamespace(scene=types.SimpleNamespace(session_uid=7))
        with patch.object(job_host, "_admit", lambda *args: ({"kind": "RENDER_STILL"}, runner)), \
                patch.dict(sys.modules, {"angelo_agent.job_ui": job_ui}), \
                patch("angelo_agent.job_ui", job_ui, create=True), \
                patch("angelo_agent.render_extensions.facts", lambda runner: {}):
            facts = job_host.precondition(controller, window, "angelo_job_start", STILL["arguments"])[2]
        import json
        facts = json.loads(facts)
        self.assertEqual(facts["open_checks"], controller.open_self_checks)
        body = message("angelo_job_start", dict(STILL["arguments"], format="PNG", _facts=facts))["body"]
        self.assertIn("These self-checks of the agent's scripts fail now: crown: measured 3.5", body)


class CancelThenFixTests(unittest.TestCase):
    def manager(self, statuses):
        record = {"id": "j1", "status": "stopping"}
        ticks = iter(statuses)

        def poll(identity):
            record["status"] = next(ticks, record["status"])
            return record
        return types.SimpleNamespace(record=poll)

    def test_a_cancel_lets_its_job_end_before_the_next_step(self):
        jobs = self.manager(["stopping", "stopping", "cancelled"])
        waits = []
        with patch("angelo_agent.job_manager.shared", return_value=jobs):
            result = plan_recovery.until_stopped({"job": "j1", "status": "stopping"}, lambda: True,
                                                 wait=waits.append)
        self.assertEqual((result["status"], len(waits)), ("cancelled", 2))

    def test_the_wait_is_bounded_and_ends_when_the_request_does(self):
        jobs = self.manager([])   # Never ends.
        clock = iter(range(100)).__next__
        with patch("angelo_agent.job_manager.shared", return_value=jobs):
            result = plan_recovery.until_stopped({"job": "j1", "status": "stopping"}, lambda: True,
                                                 wait=lambda seconds: None, clock=clock, limit=5)
            self.assertEqual(result["status"], "stopping")   # Past the bound the plan goes on as before.
            stopped = plan_recovery.until_stopped({"job": "j1", "status": "stopping"}, lambda: False,
                                                  wait=lambda seconds: None)
        self.assertEqual(stopped["status"], "stopping")
        self.assertEqual(plan_recovery.until_stopped({"job": "j1", "status": "cancelled"}, lambda: True),
                         {"job": "j1", "status": "cancelled"})   # A queued job: cancelled at once, no wait.

    def test_in_a_plan_the_fix_runs_after_the_cancel_has_ended(self):
        order, jobs = [], self.manager(["cancelled"])
        fix = {"tool": "angelo_execute_python", "arguments": {"description": "Fix.", "code": "result = 2"}}

        def call(operation):
            order.append((operation["tool"], jobs.record("j1")["status"] if operation is fix else None))
            return {"job": "j1", "status": "stopping"} if operation is CANCEL else {"success": True, "output": ""}
        evidence = types.SimpleNamespace(invalidate=lambda: None, pin_cameras=lambda *a: None)
        with patch("angelo_agent.job_manager.shared", return_value=jobs):
            run = plan_recovery.run_operations([CANCEL, fix], call, lambda: True, evidence,
                                               types.SimpleNamespace(tool_images=lambda result: []))
        self.assertEqual(order, [("angelo_job_cancel", None), ("angelo_execute_python", "cancelled")])
        self.assertEqual(run.results[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
