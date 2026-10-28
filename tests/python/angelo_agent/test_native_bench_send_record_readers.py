# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every reader of a trial's ``eval-live-agent.json`` takes a record written at a send as no record (Note on F).

No model, Blender or network. Pinned here:

* (the class guard, that no reader reads the record raw, is ``test_native_bench_record_guard``);
* the readers outside the parallel bench, on both sides of a head-to-head: a cut Angelo trial whose record was written at
  its send is absent, as a cut MCP trial (no ``trial.json``) is, and as it was before records were written at send:
  final runs, the one-shot report and scorecard, the survey's fallback to the trials' own reports, measured results;
* the bench's ``gather`` and its per-trial stop check with such a record, and a set-aside attempt with no launch file;
* Windows: a replace refused while another process holds the record open is retried, and the launcher's end write then
  falls back to the in-place write, so a completed trial is never lost to a reader; a killed writer's temporary file is
  removed by the next write.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_integrity  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import bench_parallel_report as report  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_send_record as send_record  # noqa: E402
import test_native_bench_send_record as stamped  # noqa: E402  (its send and its launcher with a fake app)

NAME = send_record.NAME
def send(path, task_set="default", completed=()):
    """A record as a launcher killed after the send leaves it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped.send(path, live.tasks_for(task_set)[len(completed)], completed=completed)
    return path


class ReaderTests(unittest.TestCase):
    """A record written at a send reads as no record, as before; the end write reads as it always did."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def test_final_runs_take_an_interrupted_eval_step_as_not_run(self):
        import final_runs
        step = {"tool": "eval", "id": "smoke-gate7"}
        folder = self.folder / step["id"]
        self.assertEqual(final_runs.completed(step, folder), set())   # No record: run it (again).
        send(folder / NAME)
        self.assertEqual(final_runs.completed(step, folder), set())
        with self.assertRaises(FileNotFoundError):   # As with no record: a gate is never read from it.
            final_runs.gate_passes(self.folder, step)
        send_record.write(folder / NAME, {"trials": [{"tasks": [{}]}], "grading": {"passed": 7, "total": 7}})
        self.assertEqual(final_runs.completed(step, folder), {"angelo-1"})
        self.assertEqual(final_runs.gate_passes(self.folder, step), (7, 7))

    def test_a_cut_trial_is_absent_on_both_sides_of_the_one_shot_report_and_scorecard(self):
        import oneshot_report
        output = self.folder / "run"
        (output / "mcp-trial-01").mkdir(parents=True)   # MCP cut before its task finished: no trial.json.
        send(output / "angelo-trial-01" / NAME, "oneshot-chess")   # Angelo cut after its send.
        self.assertEqual(oneshot_report.trial_folders(output), [])
        (output / "mcp-trial-01" / "trial.json").write_text(json.dumps({"tasks": []}))
        send_record.write(output / "angelo-trial-01" / NAME, {"trials": [{"tasks": []}]})
        self.assertEqual([(side, number) for side, number, _, _ in oneshot_report.trial_folders(output)],
                         [("mcp", 1), ("angelo", 1)])

    def test_the_survey_reads_the_trials_own_reports_and_measured_results_find_no_record(self):
        import measured_results
        import survey_report
        directory = self.folder / "survey"
        rows = [{"task": "a", "kind": "edit", "status": "completed"}, {"task": "b", "kind": "edit", "status": "completed"}]
        (directory / "trial-01" / "qa").mkdir(parents=True)
        (directory / "trial-01" / "qa" / "trial.json").write_text(json.dumps({"trial": 1, "tasks": rows}))
        send(directory / NAME, completed=rows[:1])   # The record ends at the last send: one task.
        records = survey_report.load_records([directory])[0]
        self.assertEqual([record["task"] for record in records], ["a", "b"])   # The fallback, as with no record.
        with self.assertRaises(ValueError):
            measured_results._load(directory)

    def test_bench_integrity_and_the_gate_version_skip_it(self):
        import bench_mcp_gate
        output = self.folder / "bench"
        send(output / "angelo-trial-01" / NAME)
        self.assertEqual(bench_integrity.recorded_angelo(output), (None, {}, []))   # Nothing recorded, nothing unread.
        self.assertIsNone(bench_mcp_gate.recorded_version(output))   # It holds no trial, as with no record.


class BenchTests(unittest.TestCase):
    def test_gather_and_the_stop_check_take_it_as_no_record(self):
        output = Path(tempfile.mkdtemp())
        done = dict(live.tasks_for("default")[0], status="completed")
        send(output / "angelo-trial-01" / NAME, completed=[{"task": done["id"], "status": "completed"}])
        self.assertEqual(bench.gather(output, 1, task_list=live.tasks_for("default")), ([], []))
        self.assertFalse(bench.trial_done(output, "angelo", 1))
        ran = []

        def angelo_trial(args, out, trial):   # Killed after a later task's send: one task done, no end write.
            ran.append(trial)
            send(out / ("angelo-trial-%02d" % trial) / NAME, completed=[{"task": done["id"], "status": "completed"}])
            return {"side": "angelo", "trial": trial, "exit_code": "timeout"}
        fresh = Path(tempfile.mkdtemp()) / "out"
        fresh.mkdir()
        argv = ["--authorize-billed-runs", "--connection-id", "22222222-2222-4222-8222-222222222222", "--runtime",
                "codex", "--sides", "angelo", "--trials", "2", "--output", str(fresh), "--resume"]
        summary = {"summary": {"gate_versions": None, "totals": {}, "invalid_rate_limited": 0}}
        with mock.patch.object(bench, "preflight", side_effect=lambda args: ([], Path(args.output).resolve())), \
                mock.patch.object(bench, "run_angelo_trial", side_effect=angelo_trial), \
                mock.patch.object(bench, "finish", return_value=summary), \
                contextlib.redirect_stdout(io.StringIO()) as printed, contextlib.redirect_stderr(io.StringIO()):
            code = bench.main(argv)
        self.assertEqual((code, ran), (1, [1]), "The side stops, as when no record was left")
        self.assertIn('TRIAL angelo 1 {"failed": true', printed.getvalue())

    def test_an_attempt_with_no_launch_file_is_lost_and_its_message_is_read(self):
        fixture = stamped.VerdictTests()
        for arm in report.ARMS:
            for change, shown in ((None, []), (lambda entry: entry.update(prompt_sha256="0" * 64), [arm])):
                with self.subTest(arm=arm, wrong=bool(change)):
                    folder, attempt = fixture.cut_and_resumed("oneshot-chess", arm, change=change)
                    kept = folder / arm / attempt
                    (kept / "angelo-trial-02-launch.json").unlink()   # The whole process tree was killed.
                    result = report.verdict(folder)
                    self.assertEqual(result["lost"][arm], {"2": "ended with no record (no record)"})
                    self.assertEqual([entry["arm"] for entry in result["messages_not_shown"]], shown)
                    self.assertEqual(result["complete"], not shown)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / NAME
        send_record.write(self.path, {"trials": [], "earlier": True})
        sleep = mock.patch.object(send_record.time, "sleep")
        self.slept = sleep.start()
        self.addCleanup(sleep.stop)

    def refused(self, times):
        """``os.replace`` refused ``times`` times (a Windows sharing violation), then as it is."""
        real, calls = os.replace, []

        def replace(source, target):
            calls.append(target)
            if len(calls) <= times:
                raise PermissionError(13, "The process cannot access the file because it is being used")
            return real(source, target)
        return mock.patch.object(send_record.os, "replace", side_effect=replace), calls

    def test_a_refused_replace_is_retried(self):
        patch, calls = self.refused(2)
        with patch:
            send_record.write(self.path, {"trials": [1]})
        self.assertEqual((json.loads(self.path.read_text()), len(calls)), ({"trials": [1]}, 3))
        self.assertEqual(self.slept.call_count, 2)

    def test_the_end_write_falls_back_in_place_and_any_other_write_keeps_the_earlier_copy(self):
        patch, calls = self.refused(99)
        with patch:
            with self.assertRaises(PermissionError):   # The send-time write gives up; the earlier copy stays.
                send_record.write(self.path, {"trials": [2]})
            self.assertEqual(json.loads(self.path.read_text()), {"trials": [], "earlier": True})
            send_record.between_trials(self.path, {"trials": [3]}, 1)   # Evidence only: never raises.
            send_record.write(self.path, {"trials": [4]}, in_place_fallback=True)   # The launcher's end write.
        self.assertEqual(len(calls), 3 * send_record.REPLACE_TRIES)
        self.assertEqual(self.path.read_text(), json.dumps({"trials": [4]}, indent=2, default=str))
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), [NAME])

    def test_the_launchers_end_write_survives_a_reader_holding_the_record(self):
        def completed(env, trial, task):
            record = dict(trial.record, status="completed", **bench_integrity.trial_stamp(task))
            (Path(env["ANGELO_QA_DIR"]) / "trial.json").write_text(json.dumps(dict(trial.report, tasks=[record])))
            return 0
        real = os.replace

        def replace(source, target):   # A reader holds the record from the end write on.
            if Path(target).name == NAME and not send_record.in_progress(json.loads(Path(source).read_text())):
                raise PermissionError(32, "The process cannot access the file because it is being used")
            return real(source, target)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(send_record.os, "replace", replace):
            output, _ = stamped.LauncherTests().run_launcher(Path(directory), stamped.LauncherTests.app(completed))
            value = json.loads((output / NAME).read_text())
        self.assertFalse(send_record.in_progress(value))   # The end write, not the copy from the send.
        self.assertEqual(value["trials"][0]["tasks"][0]["status"], "completed")

    def test_a_killed_writers_temporary_file_is_removed_by_the_next_write(self):
        left = self.path.with_name(NAME + ".99999.partial")
        left.write_text("{\"trials\": [")
        send_record.write(self.path, {"trials": [5]})
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), [NAME])


if __name__ == "__main__":
    unittest.main()
