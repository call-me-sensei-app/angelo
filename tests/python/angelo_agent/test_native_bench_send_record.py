# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each task's prompt digest is stamped in the trial's own record at its send (BENCH-PREREG, Note on F).

No model, Blender or network: the in-app driver's send is called on a stand-in trial, the launcher runs with a fake
app process, and the bench's own ``trial_done``, ``set_aside`` and verdict read the folders it leaves.

Pinned here, the same for both arms and every registered test: the record written at the send holds the task in flight
with its stamp in ``unfinished_task``, never in ``tasks``; a launcher killed after the send leaves it, the bench still
takes that launch as unfinished (it is set aside on resume and run again, as before), and Amendment F reads the set-aside
attempt's message: a correct one passes, a wrong one is named and the verdict is INCOMPLETE. A write cut short leaves the
earlier copy whole. What the verdict decides, and which current trials are checked, is unchanged: a crashed or lost trial
is still C6's, a current lost trial is never checked whatever its stamp (the maintainer: "only set-aside attempts read the
stamp"), and a completed task's record is as before.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_integrity  # noqa: E402
import bench_messages  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import bench_parallel_report as report  # noqa: E402
import eval_abnormal_end as end  # noqa: E402
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import eval_parallel  # noqa: E402
import eval_send_record as send_record  # noqa: E402
import test_native_bench_messages as messages  # noqa: E402  (its run fixture and its F2 cases)
import test_native_bench_parallel_runs as runs  # noqa: E402

TESTS, ARMS = messages.TESTS, report.ARMS
DECIDED = messages.DECIDED + ("ships", "complete", "missing", "lost", "set_aside", "messages_not_shown")


def arm_tasks(arm, task_set):
    """The tasks the in-app driver sends in ``arm`` (``eval_parallel.begin``'s split, the launcher's own switch)."""
    with mock.patch.object(eval_parallel, "ENABLED", arm == "parallel"):
        return eval_parallel.split_request(live.tasks_for(task_set))


def stand_in(task, number=1, completed=()):
    """The in-app driver's trial at a task's send: its record of that task and its report so far."""
    return SimpleNamespace(phase="send", record={"trial": number, "task": task["id"], "kind": task["kind"],
                                                 "source": task["source"], "approved": [], "declined": []},
                           report={"trial": number, "tasks": list(completed), "success": False})


def send(path, task, number=1, completed=(), change=None):
    """The in-app send of ``task`` with the record at ``path``; ``change`` then edits its in-flight entry."""
    trial = stand_in(task, number, completed)
    with mock.patch.dict(os.environ, {send_record.ENV: str(path)}):
        send_record.at_send(trial, task, number)
    if change is not None:
        value = json.loads(Path(path).read_text())
        change(value["trials"][-1]["unfinished_task"])
        Path(path).write_text(json.dumps(value))
    return trial


def decided(result):
    return {key: result[key] for key in DECIDED}


class RecordAtSendTests(unittest.TestCase):
    def test_the_record_holds_the_task_in_flight_with_its_stamp_never_in_tasks_in_both_arms(self):
        for task_set in TESTS:
            for arm in ARMS:
                with self.subTest(test=task_set, arm=arm), tempfile.TemporaryDirectory() as folder:
                    task, path = arm_tasks(arm, task_set)[0], Path(folder) / send_record.NAME
                    send(path, task)
                    value = json.loads(path.read_text())
                    self.assertTrue(send_record.in_progress(value))
                    first, = value["trials"]
                    self.assertEqual(first["tasks"], [])   # The bench's rows come from tasks: never the one in flight.
                    self.assertEqual(first["unfinished_task"], dict(
                        trial=1, task=task["id"], kind=task["kind"], phase="send", **bench_integrity.trial_stamp(task)))
                    digest, request = bench_messages.registered(task_set)[arm][0]   # F1's registered message.
                    self.assertEqual(first["unfinished_task"]["prompt_sha256"], digest)
                    self.assertEqual(first["unfinished_task"].get("parallel_request"), request)
                    self.assertEqual("parallel_request" in first["unfinished_task"], arm == "parallel")
                    self.assertEqual(list(Path(folder).iterdir()), [path])   # No temporary file left.

    def test_a_later_send_keeps_the_completed_tasks_as_recorded_and_the_launchers_earlier_trials(self):
        tasks = arm_tasks("single", "default")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / send_record.NAME
            earlier = {"trial": 1, "tasks": [{"task": "x", "status": "completed"}], "exit_code": 0}
            send_record.between_trials(path, {"plan": {}, "trials": [earlier]}, 1)
            done = dict({"trial": 2, "task": tasks[0]["id"], "status": "completed"}, **bench_integrity.trial_stamp(
                tasks[0]))
            send(path, tasks[1], number=2, completed=[copy.deepcopy(done)])
            value = json.loads(path.read_text())
            self.assertEqual(value["trials"][0], earlier)
            self.assertEqual(value["trials"][1]["tasks"], [done])
            self.assertEqual(value["trials"][1]["unfinished_task"]["task"], tasks[1]["id"])
            self.assertEqual(value[send_record.IN_PROGRESS]["trial"], 2)

    def test_the_send_leaves_the_task_record_as_it_was_and_completes_with_the_same_stamp(self):
        for arm in ARMS:
            task = arm_tasks(arm, "oneshot-chess")[0]
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as folder:
                trial = stand_in(task)
                before = copy.deepcopy(trial.record)
                with mock.patch.dict(os.environ, {send_record.ENV: str(Path(folder) / send_record.NAME)}):
                    send_record.at_send(trial, task, 1)
                self.assertEqual(trial.record, before)   # The completed record gets task_extras's stamp, as before.
                self.assertEqual(send_record.stamp(trial), bench_integrity.trial_stamp(task))
                trial.record = {"task": "a warm-up"}   # Another record in flight: no stamp of this task.
                self.assertEqual(send_record.stamp(trial), {})

    def test_the_driver_writes_it_before_the_request_goes_out(self):
        source = (HERE / "eval_live_agent_session.py").read_text()
        body = source[source.index("    def on_send(self):"):source.index("    def offer_collaborators(self):")]
        self.assertLess(body.index("eval_send_record.at_send(self, task, TRIAL)"), body.index("self.sent, self.progress"))
        self.assertLess(body.index("self.sent, self.progress"), body.index("self.controller.send("))
        self.assertLessEqual(len(source.splitlines()), 500)

    def test_a_crashed_apps_unfinished_task_keeps_its_stamp_in_the_launchers_end_write(self):
        for arm in ARMS:
            task = arm_tasks(arm, "oneshot-parlour")[0]
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as folder:
                qa = Path(folder) / "qa"
                qa.mkdir()
                trial = send(Path(folder) / send_record.NAME, task)
                trial.phase, trial.controller = "running", SimpleNamespace(cancelled=False, busy=True)
                end.refresh(trial, qa)   # The in-flight snapshot the app keeps while the task runs.
                left = end.collect(folder, {"tasks": []}, 1)[end.UNFINISHED]   # The app crashed; the launcher reads it.
                self.assertEqual({key: left.get(key) for key in ("task", "prompt_sha256", "parallel_request")},
                                 {"task": task["id"], "prompt_sha256": bench_integrity.prompt_sha256(task["prompt"]),
                                  "parallel_request": task.get("parallel_request")})
                trial.report["error"] = "boom"   # Or the driver finished with it in flight.
                end.unfinished(trial, qa)
                self.assertEqual(trial.report[end.UNFINISHED]["prompt_sha256"], left["prompt_sha256"])


class LauncherTests(unittest.TestCase):
    """The launcher with a fake app: the path it passes, a kill after the send, a kill during the end write."""

    def run_launcher(self, folder, app, trials=1, during_end_write=None):
        from angelo_agent.connections import ConnectionStore
        source = folder / "source"
        identity = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION")["id"]
        binary = folder / "Angelo"
        binary.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(binary, 0o755)
        output, launched, real_run = folder / "out", [], subprocess.run

        def fake_run(command, *args, **kwargs):   # The app only; anything else (git) runs as it is.
            if not command or str(command[0]) != str(binary):
                return real_run(command, *args, **kwargs)
            launched.append(kwargs["env"])
            return subprocess.CompletedProcess(command, app(kwargs["env"], len(launched)))
        patches = [mock.patch.object(subprocess, "run", fake_run)]
        if during_end_write is not None:
            patches.append(mock.patch.object(Path, "write_text", during_end_write("write_text", Path.write_text)))
            patches.append(mock.patch.object(os, "replace", during_end_write("replace", os.replace)))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for patch in patches:
                stack.enter_context(patch)
            try:
                eval_live_agent.main(["--authorize-billed-runs", "--connection-id", identity, "--trials", str(trials),
                                      "--blender", str(binary), "--output", str(output),
                                      "--source-connections-dir", str(source), "--allow-addon-mismatch"])
            except SystemExit as killed:
                self.assertEqual(str(killed), "killed")
        return output, launched

    @staticmethod
    def app(sends):
        """A fake app that sends its first task (the driver's own send) and then does ``sends(env, path, task)``."""
        def run(env, number):
            task = live.tasks_for("default")[0]
            path = Path(env[send_record.ENV])
            with mock.patch.dict(os.environ, {send_record.ENV: str(path)}):
                trial = stand_in(task, number)
                send_record.at_send(trial, task, number)
            return sends(env, trial, task)
        return run

    def test_a_launcher_killed_after_the_send_leaves_the_record_and_the_bench_runs_the_trial_again(self):
        def killed(env, trial, task):
            raise SystemExit("killed")
        with tempfile.TemporaryDirectory() as directory:
            output, launched = self.run_launcher(Path(directory), self.app(killed))
            self.assertEqual(launched[0][send_record.ENV], str(output.resolve() / send_record.NAME))
            value = json.loads((output / send_record.NAME).read_text())
            self.assertTrue(send_record.in_progress(value))
            self.assertEqual(value["trials"][0]["unfinished_task"]["prompt_sha256"],
                             bench_integrity.prompt_sha256(live.tasks_for("default")[0]["prompt"]))
            # The bench's own view of that launch, as when no record was left: not done, so a resume sets it aside.
            bench_folder = Path(directory) / "arm"
            shutil.move(str(output), str(bench_folder / "angelo-trial-01"))
            self.assertFalse(bench.trial_done(bench_folder, "angelo", 1))
            self.assertFalse(send_record.finished(bench_folder / "angelo-trial-01" / send_record.NAME))
            self.assertEqual(bench.set_aside(bench_folder, "angelo", 1), ["angelo-trial-01"])

    def test_a_kill_during_the_end_write_leaves_the_earlier_copy_whole(self):
        def completed(env, trial, task):
            record = dict(trial.record, status="completed", **bench_integrity.trial_stamp(task))
            (Path(env["ANGELO_QA_DIR"]) / "trial.json").write_text(json.dumps(dict(trial.report, tasks=[record])))
            return 0

        def cut(kind, real):
            def write(*args, **kwargs):   # The end write: the launcher is killed half way through it.
                if kind == "write_text" and Path(args[0]).name == send_record.NAME:   # A write in place.
                    real(args[0], args[1][:len(args[1]) // 2])
                    raise SystemExit("killed")
                if kind == "replace" and Path(args[1]).name == send_record.NAME and not send_record.in_progress(
                        json.loads(Path(args[0]).read_text())):   # Every byte written, before the rename.
                    raise SystemExit("killed")
                return real(*args, **kwargs)
            return write
        with tempfile.TemporaryDirectory() as directory:
            output, _ = self.run_launcher(Path(directory), self.app(completed), during_end_write=cut)
            value = json.loads((output / send_record.NAME).read_text())   # Readable: the copy written at the send.
            self.assertTrue(send_record.in_progress(value))
            self.assertEqual(value["trials"][0]["unfinished_task"]["task"], live.tasks_for("default")[0]["id"])
        with tempfile.TemporaryDirectory() as directory:   # Not killed: the end write as the launcher always wrote it.
            output, _ = self.run_launcher(Path(directory), self.app(completed))
            text = (output / send_record.NAME).read_text()
            value = json.loads(text)
            self.assertEqual(text, json.dumps(value, indent=2, default=str))
            self.assertFalse(send_record.in_progress(value))
            self.assertTrue(send_record.finished(output / send_record.NAME))
            self.assertEqual(value["trials"][0]["tasks"][0]["prompt_sha256"],
                             bench_integrity.prompt_sha256(live.tasks_for("default")[0]["prompt"]))
            self.assertEqual(sorted(path.name for path in output.glob(send_record.NAME + "*")), [send_record.NAME])

    def test_a_trials_record_at_its_send_starts_from_the_launchers_earlier_trials(self):
        def completed(env, trial, task):
            if trial.record["trial"] == 2:
                raise SystemExit("killed")
            record = dict(trial.record, status="completed", **bench_integrity.trial_stamp(task))
            (Path(env["ANGELO_QA_DIR"]) / "trial.json").write_text(json.dumps(dict(trial.report, tasks=[record])))
            return 0
        with tempfile.TemporaryDirectory() as directory:
            output, launched = self.run_launcher(Path(directory), self.app(completed), trials=2)
            value = json.loads((output / send_record.NAME).read_text())
            self.assertEqual(len(launched), 2)
            self.assertEqual([entry.get("exit_code") for entry in value["trials"]], [0, None])   # The launcher's first.
            self.assertEqual(value["trials"][1]["unfinished_task"]["trial"], 2)


class VerdictTests(unittest.TestCase):
    """Amendment F reads a set-aside attempt's message from its record at send; nothing else changes."""
    fixture = messages.RegisteredMessageTests()

    def cut_and_resumed(self, task_set, arm, trial=2, change=None, record=True):
        """A run where ``arm``'s trial ``trial`` was cut by the bench's cap after its send, then resumed: the bench's
        own ``trial_done`` and ``set_aside`` move the attempt to ``interrupted/`` and the rerun's record stays."""
        folder = self.fixture.run_folder(task_set, 1)
        place, rerun = folder / arm, Path(tempfile.mkdtemp()) / "rerun"
        shutil.move(str(place / ("angelo-trial-%02d" % trial)), str(rerun))
        (place / ("angelo-trial-%02d" % trial)).mkdir()
        if record:
            send(place / ("angelo-trial-%02d" % trial) / send_record.NAME, arm_tasks(arm, task_set)[0], change=change)
        (place / ("angelo-trial-%02d-launch.json" % trial)).write_text(json.dumps({"exit_code": "timeout"}))
        self.assertFalse(bench.trial_done(place, "angelo", trial))   # Not finished: the resume sets it aside.
        moved = bench.set_aside(place, "angelo", trial)
        self.assertEqual(sorted(moved), ["angelo-trial-%02d" % trial, "angelo-trial-%02d-launch.json" % trial])
        shutil.move(str(rerun), str(place / ("angelo-trial-%02d" % trial)))
        attempt, = (place / "interrupted").iterdir()
        return folder, "interrupted/" + attempt.name

    def test_the_resumed_attempts_message_is_read_a_correct_one_passes_and_a_wrong_one_is_named(self):
        for task_set in TESTS:
            for arm in ARMS:
                folder, attempt = self.cut_and_resumed(task_set, arm)
                control = report.verdict(folder)
                with self.subTest(test=task_set, arm=arm, case="correct"):
                    self.assertEqual((control["complete"], control["missing"], control["messages_not_shown"]),
                                     (True, [], []))
                    self.assertEqual(control["lost"][arm], {"2": "cut by the bench's cap"})   # C6, as before.
                for case, (wrong_arm, change) in self.fixture.wrong(task_set).items():
                    if wrong_arm != arm:
                        continue
                    with self.subTest(test=task_set, arm=arm, case=case):
                        folder, attempt = self.cut_and_resumed(task_set, arm, change=change)
                        result = report.verdict(folder)
                        self.assertEqual((result["complete"], result["ships"]), (False, False))
                        entry, = result["messages_not_shown"]
                        self.assertEqual((entry["arm"], entry["trial"], entry["attempt"]), (arm, 2, attempt))
                        self.assertTrue(all(why.startswith("task 1 (in flight): ") for why in entry["why"]), entry)
                        self.assertEqual(entry["recorded"][0]["in_flight"], arm_tasks(arm, task_set)[0]["id"])
                        line, = result["missing"]
                        self.assertTrue(line.startswith("%s trial 2, the attempt a resume set aside as %s: does not "
                                                        "show its arm's registered message" % (arm, attempt)), line)
                        self.assertIn("**Verdict: INCOMPLETE: does not ship**", report.markdown(result))
                        same = {key for key in DECIDED if key not in ("complete", "ships", "missing",
                                                                      "messages_not_shown")}
                        self.assertEqual({key: result[key] for key in same}, {key: control[key] for key in same})

    def test_a_crashed_attempt_is_still_lost_and_the_verdict_is_the_one_without_the_record(self):
        for task_set in TESTS[:2]:
            for arm in ARMS:
                with self.subTest(test=task_set, arm=arm):
                    without = report.verdict(self.cut_and_resumed(task_set, arm, record=False)[0])
                    stamped = report.verdict(self.cut_and_resumed(task_set, arm)[0])
                    self.assertEqual(decided(stamped), decided(without))
                    self.assertEqual(stamped["lost"][arm], {"2": "cut by the bench's cap"})
                    self.assertIn(2, stamped["trials_with_a_broken_piece"][arm])

    def test_a_current_lost_trial_is_never_checked_whatever_its_stamp_in_both_arms(self):
        pair = runs.PairTests()
        for task_set in TESTS[:2]:
            for arm in ARMS:
                for shape in ("the launcher's end write", "the record at send"):
                    results = []
                    for stamp in (None, "wrong"):
                        walls = {name: messages.WALLS[name] + ([] if name == arm else [640]) for name in ARMS}
                        folder = messages.with_cap(self.fixture.fixture.run_folder(
                            walls, {"single": {}, "parallel": {}}, trials=3, task_set=task_set), 1)
                        place = pair.launch(folder, arm, 3, "timeout" if shape == "the record at send" else 1)
                        path = place / "angelo-trial-03" / send_record.NAME
                        path.parent.mkdir()
                        task = arm_tasks(arm, task_set)[0]
                        send(path, task, change=lambda entry: entry.update(prompt_sha256="0" * 64, parallel_request=(
                            "another sentence")) if stamp else None)
                        value = json.loads(path.read_text())
                        if stamp is None:
                            for key in ("prompt_sha256", "parallel_request", "test_code"):
                                value["trials"][0]["unfinished_task"].pop(key, None)
                        if shape == "the launcher's end write":   # The app crashed; the launcher wrote at its exit.
                            value.pop(send_record.IN_PROGRESS)
                            value["trials"][0]["unfinished_task"][end.KEY] = {"reason": "app_exit"}
                        path.write_text(json.dumps(value))
                        results.append(report.verdict(folder))
                    with self.subTest(test=task_set, arm=arm, shape=shape):
                        self.assertEqual(results[1]["messages_not_shown"], [])   # F3, as frozen: never checked.
                        self.assertEqual(decided(results[1]), decided(results[0]))
                        self.assertIn("3", results[1]["lost"][arm])
                        self.assertEqual(bench_messages.unshown(folder, {arm: [1, 2]}), [])


if __name__ == "__main__":
    unittest.main()
