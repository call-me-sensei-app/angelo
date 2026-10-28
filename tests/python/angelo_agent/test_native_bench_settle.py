# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BENCH-PREREG Amendment G: a pending worker-done event counts as not settled, in both arms, so a trial includes the
lead's resumed merge; a single-arm trial settles exactly as before. The trial ends when the lead's turn has ended and
``eval_parallel.settled`` holds (``eval_live_agent_session.on_running``); the count of events is reported only."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))

import eval_abnormal_end as end  # noqa: E402
import eval_parallel  # noqa: E402
from angelo_agent import worker_resume, worker_sessions  # noqa: E402


class SettleTests(unittest.TestCase):
    def setUp(self):
        worker_resume._batches.clear()
        self.addCleanup(worker_resume._batches.clear)
        self.lead = SimpleNamespace(state={"id": "lead", "messages": []}, busy=False, _worker=None)

    def arm(self, parallel):   # The trial's Angelo runs parallel edit (the parallel arm) or not (the single arm).
        patcher = mock.patch.object(eval_parallel, "on", lambda: parallel)
        patcher.start()
        self.addCleanup(patcher.stop)

    def done(self):   # The trial driver's end (on_running): the lead's turn over and settled.
        return end.turn_ended(self.lead) and eval_parallel.settled(None, self.lead)

    def test_a_lead_that_ended_early_settles_only_after_the_events_turn(self):
        self.arm(True)
        self.assertTrue(eval_parallel.PENDING_EVENT_UNSETTLED)
        with mock.patch.object(worker_sessions, "workers_of", lambda lead_id: ["w"]), \
                mock.patch.object(worker_sessions, "finished", lambda session: True), \
                mock.patch.object(worker_sessions, "_launching", []):
            worker_resume._batches["lead"] = True   # Its workers ended; the event has not fired yet.
            self.assertFalse(self.done())
            worker_resume.cancel("lead")   # It fires: the lead's turn starts in the same step (worker_resume._fire).
            self.lead.busy = True
            self.assertFalse(self.done())   # The event's turn and its merge run.
            self.lead.busy = False
            self.assertTrue(self.done())

    def test_a_single_trial_settles_as_before(self):
        self.arm(False)
        worker_resume._batches["lead"] = True   # Even a stray batch never holds a single-arm trial.
        self.assertTrue(self.done())

    def test_the_events_are_counted_reported_only(self):
        self.lead.state["messages"] = [{"role": "user", "text": "Build"}, {"role": "user", "text": "e",
                                       "request_kind": "angelo_event"}, {"role": "assistant", "text": "Merged"}]
        record = {}
        self.arm(True)
        eval_parallel.events(self.lead, 0, record)
        self.assertEqual(record, {"worker_done_events": 1})
        self.lead.state["messages"].append({"role": "user", "text": "e2", "request_kind": "angelo_event"})
        eval_parallel.events(self.lead, 0, record, stopped=3)   # The harness's timeout stop came at row 3.
        self.assertEqual(record, {"worker_done_events": 2, "worker_done_events_after_stop": 1})


class ReportTests(unittest.TestCase):
    def test_the_count_reaches_the_report_and_never_the_verdict(self):
        import json
        import bench_mcp_report
        import bench_parallel_report as report
        import test_native_bench_parallel as verdicts   # Its run fixture.
        task = {"task": "t", "kind": "scenario", "status": "completed", "worker_done_events": 2,
                "worker_done_events_after_stop": 1}
        record, = bench_mcp_report.angelo_records({"tasks": [task]}, 1, {})
        self.assertEqual((record["worker_done_events"], record["worker_done_events_after_stop"]), (2, 1))
        fixture = verdicts.VerdictTests()
        folder = fixture.run_folder({"single": [600, 620], "parallel": [300, 320]}, {"single": {}, "parallel": {}})
        before = report.verdict(folder)
        path = folder / "parallel" / report.RESULTS
        value = json.loads(path.read_text())
        value["records"][0]["worker_done_events"] = 1
        path.write_text(json.dumps(value))
        after = report.verdict(folder)
        self.assertEqual([row["worker_done_events"] for row in after["arms"]["parallel"]], [1, None])
        decided = ("ships", "ship_rule_holds", "complete", "missing", "faster", "speedup_median", "summary")
        self.assertEqual({key: after[key] for key in decided}, {key: before[key] for key in decided})
        self.assertIn("(reported only, never in the verdict; Amendment G): parallel 1: 1; parallel 2: None",
                      report.markdown(after))
        value["records"][1].update(worker_done_events=1, worker_done_events_after_stop=1)
        path.write_text(json.dumps(value))
        stopped = report.verdict(folder)
        self.assertEqual({key: stopped[key] for key in decided}, {key: before[key] for key in decided})
        self.assertIn("Trials whose event fired after a timeout stop (reported only; Clarification of G): 1",
                      report.markdown(stopped))


class SingleArmCountTests(unittest.TestCase):
    def test_a_single_trial_records_no_count(self):
        record = {}
        with mock.patch.object(eval_parallel, "on", lambda: False):
            eval_parallel.events(SimpleNamespace(state={"messages": []}), 0, record)
        self.assertEqual(record, {})


if __name__ == "__main__":
    unittest.main()
