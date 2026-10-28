# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parallel-agents benchmark's runner across stops and resumes, and the verdict's pairs, without a model.

Pinned here: the two arms run byte-identical arguments but the one switch, interleaved with the first arm
alternating; the runner owns the arguments that decide trials; each arm warms up right before its own first trial of
a run; a stop (the bench's 1 a broken side, 2 a refusal, 3 a provider limit, a warm-up's included; 4 a failed
preflight) launches nothing after it and is written to ``interleave.json`` (break-it E2E-10 M-3, E2E-11 L-3). Run
again, the schedule continues where it stopped: a finished trial never runs again, the arms still alternate, the
journal keeps every earlier step, a run with other arguments is refused, and a step that did not finish its own
trial stops the run (E2E-11 M-4); a trial the harness ended before its message is set aside and run again (E2E-12
M-3). The verdict compares only the trials held by both arms, in one direction: a parallel failure outside a pair
(a broken piece, a stall, a cap or a crash that left no record) counts against parallel, and nothing outside a pair
relaxes parallel's bar (E2E-11 M-4, L-4, E2E-12 H-2). A lost trial (no task record), in either arm, is a failed trial
of that arm, every class 0 and its wall clock the cap, compared with the other arm's trial of the same number,
whatever its rerun did, and never INCOMPLETE (BENCH-PREREG Amendment C6).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[2] / "scripts/addons_core"))
import bench_mcp_vs_angelo as bench  # noqa: E402
import bench_parallel_report as report  # noqa: E402
import bench_parallel_vs_single as runner  # noqa: E402
import test_native_bench_parallel as verdicts  # noqa: E402  (its fixtures; its tests run there)


class FakeBench:
    """``bench_mcp_vs_angelo`` as the runner drives it: a trial step runs the arm's first unfinished trial and leaves
    its receipt, unless rate limited (3: never finished). ``codes``: {(arm, trial or "warm-up"): code}, each used once,
    so a step run again after the stop succeeds."""

    def __init__(self, codes=None, receipts=True, silent=()):
        self.codes, self.receipts, self.silent, self.steps = dict(codes or {}), receipts, set(silent), []

    def __call__(self, command, **_kwargs):
        arm = "parallel" if "--angelo-parallel-agents" in command else "single"
        if "--warm-up-only" in command:
            self.steps.append((arm, "warm-up"))
            return mock.Mock(returncode=self.codes.pop((arm, "warm-up"), 0))
        out, trials = Path(command[command.index("--output") + 1]), int(command[command.index("--trials") + 1])
        trial = next(n for n in range(1, trials + 1) if not bench.trial_done(out, "angelo", n))
        code = self.codes.pop((arm, trial), 0)
        self.steps.append((arm, trial))
        if code != 3 and self.receipts:
            receipt = out / ("angelo-trial-%02d" % trial) / "eval-live-agent.json"
            receipt.parent.mkdir(parents=True, exist_ok=True)
            silent = (arm, trial) in self.silent   # The harness ended it before its message (each once).
            self.silent.discard((arm, trial))
            receipt.write_text(json.dumps({"trials": [{"aborted": "nothing was sent", "tasks": []} if silent else {}]}))
            code = 1 if silent else code
        return mock.Mock(returncode=code)


def run(folder, fake, trials, *extra):
    with mock.patch.object(runner.subprocess, "run", fake):
        return runner.main(["--authorize-billed-runs", "--task-set", "t", "--output", folder, "--trials", str(trials),
                            *extra])


def journal(folder):
    return json.loads((Path(folder) / "interleave.json").read_text())


class RunnerTests(unittest.TestCase):
    def test_the_arms_differ_only_by_the_switch_and_alternate(self):
        args, rest = runner.parse(["--authorize-billed-runs", "--task-set", "oneshot-chess", "--trials", "4",
                                   "--output", "/r", "--", "--model", "m", "--effort", "medium"])
        single, parallel = runner.command(args, rest, "single"), runner.command(args, rest, "parallel")
        self.assertEqual([part for part in parallel if part != "--angelo-parallel-agents"],
                         [part.replace("/r/single", "/r/parallel") for part in single])
        self.assertEqual([runner.order(n, "single") for n in (1, 2, 3)],
                         [("single", "parallel"), ("parallel", "single"), ("single", "parallel")])
        self.assertIn("--warm-up-only", runner.command(args, rest, "single", warm_up=True))
        self.assertIn("--max-new-trials", single)

    def test_the_worker_cap_is_the_runners_and_reaches_the_parallel_arm_only(self):
        args, rest = runner.parse(["--authorize-billed-runs", "--task-set", "oneshot-chess", "--trials", "2",
                                   "--output", "/r", "--worker-cap", "1", "--", "--model", "m"])
        single, parallel = runner.command(args, rest, "single"), runner.command(args, rest, "parallel")
        self.assertNotIn("--angelo-worker-cap", single)
        self.assertEqual(parallel[parallel.index("--angelo-worker-cap") + 1], "1")
        self.assertNotIn("--angelo-worker-cap", runner.command(runner.parse(
            ["--authorize-billed-runs", "--task-set", "t", "--output", "/r"])[0], [], "parallel"))
        with tempfile.TemporaryDirectory() as folder:   # A resume with another cap would mix the schedule.
            self.assertEqual(run(folder, FakeBench(), 1), 0)
            self.assertEqual(run(folder, mock.Mock(side_effect=AssertionError("launched")), 1, "--worker-cap", "1"),
                             runner.REFUSED)
            older = journal(folder)
            older.pop("worker_cap")   # A journal from before the cap: no cap, so the same run resumes.
            (Path(folder) / "interleave.json").write_text(json.dumps(older))
            self.assertEqual(run(folder, FakeBench(), 1), 0)
        from angelo_agent.parallel_capability import MAX_WORKERS
        for asked, recorded in ((1, 1), (MAX_WORKERS + 5, MAX_WORKERS), (0, None)):   # Never more than the most.
            self.assertEqual(bench.worker_cap(SimpleNamespace(angelo_parallel_agents=True, angelo_worker_cap=asked)),
                             recorded)
        self.assertIsNone(bench.worker_cap(SimpleNamespace(angelo_parallel_agents=False, angelo_worker_cap=1)))

    def test_the_arguments_that_decide_trials_are_the_runners(self):
        for forbidden in ("--sides", "--angelo-parallel-agents", "--trials", "--resume", "--angelo-worker-cap"):
            with self.subTest(forbidden=forbidden), self.assertRaises(SystemExit):
                runner.parse(["--authorize-billed-runs", "--task-set", "t", "--output", "/r", "--", forbidden, "1"])

    def test_each_arms_warm_up_runs_right_before_its_own_first_trial(self):
        fake = FakeBench()
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(run(folder, fake, 2), 0)
        self.assertEqual(fake.steps, [("single", "warm-up"), ("single", 1), ("parallel", "warm-up"), ("parallel", 1),
                                      ("parallel", 2), ("single", 2)])

    def test_a_step_the_bench_stopped_stops_the_run_with_its_code_on_record(self):
        for stop, code in ((("parallel", 1), 3), (("parallel", 1), 1), (("parallel", 1), 2),
                           (("parallel", "warm-up"), 3)):
            fake = FakeBench({stop: code})
            with self.subTest(stop=stop, code=code), tempfile.TemporaryDirectory() as folder:
                self.assertEqual(run(folder, fake, 3), code)
                self.assertEqual(fake.steps[-1], stop)   # Nothing launched after it.
                self.assertEqual(journal(folder)["steps"][-1]["code"], code)

    def test_a_failed_preflight_stops_before_the_step_with_its_own_code_on_record(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(runner.subprocess, "run") as fake:
            fake.return_value = mock.Mock(returncode=1, stdout="unfit")
            code = runner.main(["--authorize-billed-runs", "--task-set", "t", "--output", folder, "--trials", "1",
                                "--preflight", "false"])
            self.assertEqual(code, runner.PREFLIGHT)
            self.assertEqual(fake.call_count, 1)   # The preflight only: no arm ran.
            self.assertEqual(journal(folder)["steps"][-1], {"stopped": "preflight failed before single warm-up",
                                                            "code": runner.PREFLIGHT})

    def test_run_again_the_schedule_continues_and_the_arms_still_alternate(self):
        fake = FakeBench({("single", 2): 3})
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(run(folder, fake, 5), 3)
            self.assertEqual(fake.steps, [("single", "warm-up"), ("single", 1), ("parallel", "warm-up"),
                                          ("parallel", 1), ("parallel", 2), ("single", 2)])
            fake.steps.clear()
            self.assertEqual(run(folder, fake, 5), 0)
            self.assertEqual(fake.steps, [("single", "warm-up"), ("single", 2), ("single", 3), ("parallel", "warm-up"),
                                          ("parallel", 3), ("parallel", 4), ("single", 4), ("single", 5),
                                          ("parallel", 5)])
            steps = journal(folder)["steps"]
            self.assertEqual(sum("run_started" in step for step in steps), 2)   # Appended, never rewritten.
            self.assertIn({"stopped": "single trial 2 exited 3", "code": 3}, steps)

    def test_a_run_whose_arguments_differ_is_refused_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(run(folder, FakeBench(), 1), 0)
            before = (Path(folder) / "interleave.json").read_text()
            fake = FakeBench()
            self.assertEqual(run(folder, fake, 2), runner.REFUSED)
            self.assertEqual((fake.steps, (Path(folder) / "interleave.json").read_text()), ([], before))

    def test_a_trial_that_sent_nothing_is_set_aside_and_run_again(self):
        fake = FakeBench(silent={("parallel", 1)})
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(run(folder, fake, 2), 1)
            fake.steps.clear()
            self.assertEqual(run(folder, fake, 2), 0)
            self.assertEqual(fake.steps[:2], [("parallel", "warm-up"), ("parallel", 1)])
            self.assertTrue(list((Path(folder) / "parallel" / "interrupted").glob("angelo-trial-01-*")))
            self.assertTrue(any("before its message" in step.get("set_aside", "") for step in journal(folder)["steps"]))

    def test_a_step_that_did_not_finish_its_own_trial_stops_the_run(self):
        fake = FakeBench(receipts=False)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(run(folder, fake, 2), runner.REFUSED)
            self.assertEqual(fake.steps, [("single", "warm-up"), ("single", 1)])


class SplitRequestTests(unittest.TestCase):
    """The approved request (maintainer, 2026-10-25) reaches only the forced parallel arm's message, byte-identical for
    every case, and the record's prompt SHA-256 is of the text sent; the worker cap travels to that arm only."""

    def test_only_the_parallel_arms_message_asks_to_split(self):
        import eval_parallel
        tasks = [{"id": "brief", "prompt": "Build the room."}, {"id": "other", "prompt": "Build the car."}]
        with mock.patch.object(eval_parallel, "ENABLED", False):
            self.assertIs(eval_parallel.split_request(tasks), tasks)
        with mock.patch.object(eval_parallel, "ENABLED", True):
            sent = eval_parallel.split_request(tasks)
        self.assertEqual([task["prompt"] for task in sent],
                         [task["prompt"] + "\n\n" + eval_parallel.PARALLEL_REQUEST for task in tasks])
        self.assertEqual(tasks[0]["prompt"], "Build the room.")   # The scenario's own task is never changed.
        stamp = bench.integrity.trial_stamp(sent[0])
        self.assertEqual((stamp["prompt_sha256"], stamp["parallel_request"]),
                         (bench.integrity.prompt_sha256(sent[0]["prompt"]), eval_parallel.PARALLEL_REQUEST))
        self.assertNotIn("parallel_request", bench.integrity.trial_stamp(tasks[0]))

    def test_amendment_ds_sentence_follows_the_same_prospective_common_message_in_both_arms(self):
        """BENCH-PREREG Amendment D: the sentence, byte for byte, with its registered SHA-256; for every registered
        test the single arm sends the common message and the parallel arm that message, a blank line and the
        sentence. The original scenario brief remains byte-identical; QL1 is a prospective common addition."""
        import hashlib
        import eval_live_tasks as live
        import eval_parallel
        import scenario_specs as specs
        frozen = ("Split this work between yourself and the workers: build your own pieces while they build theirs, "
                  "and hand out more as they finish.").encode("utf-8")
        digest = "5ecc54171d8a9b199eaa8cd9ce0dd59005fdce27101740f512252994491e0270"
        self.assertEqual(eval_parallel.PARALLEL_REQUEST.encode("utf-8"), frozen)
        self.assertEqual((hashlib.sha256(frozen).hexdigest(), eval_parallel.FROZEN_SHA256), (digest, digest))
        for name in specs.ONESHOT_PARALLEL:
            with self.subTest(test=name):
                briefs = [task["prompt"].encode("utf-8") for task in live.tasks_for("oneshot-" + name)]
                sent = {}
                for arm, enabled in (("single", False), ("parallel", True)):
                    with mock.patch.object(eval_parallel, "ENABLED", enabled):
                        sent[arm] = eval_parallel.split_request(live.tasks_for("oneshot-" + name))
                self.assertEqual([task["prompt"].encode("utf-8") for task in sent["single"]], briefs)
                for task in sent["single"]:
                    stamp = bench.integrity.trial_stamp(task)
                    self.assertNotIn("parallel_request", stamp)
                    self.assertEqual(stamp["prompt_sha256"], hashlib.sha256(task["prompt"].encode()).hexdigest())
                self.assertEqual([task["prompt"].encode("utf-8") for task in sent["parallel"]],
                                 [brief + b"\n\n" + frozen for brief in briefs])
        import eval_quality_loop
        chess = specs.tasks("oneshot_chess")[0]["prompt"].encode("utf-8")
        self.assertEqual(live.tasks_for("oneshot-chess")[0]["prompt"],
                         chess.decode("utf-8") + "\n\n" + eval_quality_loop.DIRECTIVE)
        self.assertEqual(hashlib.sha256(chess).hexdigest()[:16], "e450976e6940f780")   # As registered (BENCH-PREREG).

    def test_the_worker_cap_reaches_the_parallel_arm_only(self):
        import eval_parallel
        on = SimpleNamespace(parallel_agents=True, parallel_as_shipped=False, worker_cap=1)
        off = SimpleNamespace(parallel_agents=False, parallel_as_shipped=False, worker_cap=1)
        shipped = SimpleNamespace(parallel_agents=False, parallel_as_shipped=True, worker_cap=1)
        self.assertEqual(eval_parallel.environment(on)["ANGELO_PARALLEL_WORKER_CAP"], "1")
        self.assertEqual(eval_parallel.environment(off)["ANGELO_PARALLEL_WORKER_CAP"], "")
        self.assertEqual(eval_parallel.environment(shipped)["ANGELO_PARALLEL_WORKER_CAP"], "")


class PairTests(unittest.TestCase):
    task, run_folder = verdicts.VerdictTests.task, verdicts.VerdictTests.run_folder
    plan = staticmethod(verdicts.VerdictTests.plan)

    def test_only_paired_trials_are_compared_and_an_unpaired_failure_still_counts(self):
        same = {"King": [3, 3]}
        walls = {"single": [600, 620], "parallel": [300, 320, 350]}

        def unpaired(king):   # Single's trial 3 never ran (an invalid one owed a rerun), so parallel's has no pair.
            return self.plan(self.run_folder(walls, {"single": same, "parallel": {"King": king}}), 2)
        result = report.verdict(unpaired([3, 3, 4]))
        self.assertEqual((result["paired"], result["unpaired"]), ([1, 2], {"single": [], "parallel": [3]}))
        self.assertEqual(result["summary"]["parallel"]["wall_max_s"], 320.0)   # Its wall is not compared ...
        self.assertTrue(result["ships"])
        self.assertIn("single one never relaxes parallel's bar): single none; parallel [3]", report.markdown(result))
        broken = report.verdict(unpaired([3, 3, 1]))
        self.assertEqual(broken["trials_with_a_broken_piece"], {"single": [], "parallel": [3]})   # ... its failure is.
        self.assertFalse(broken["ships"])

    def test_a_lost_trial_is_a_failed_trial_of_its_arm_compared_with_the_other_arms(self):
        """Amendment C6, the same for both arms: a trial lost before any task record scores 0 on every class, its wall
        clock is the cap, it is paired with the other arm's trial of the same number, and the run is complete."""
        walls, same = {"single": [600, 620, 640], "parallel": [300, 320, 350]}, {"King": [3, 3, 3]}

        def lose(*arms):
            folder = self.run_folder({arm: walls[arm][:2] if arm in arms else walls[arm] for arm in report.ARMS},
                                     {arm: {"King": same["King"][:2] if arm in arms else same["King"]}
                                      for arm in report.ARMS}, trials=3)
            for arm in arms:
                self.launch(folder, arm, 3, 1)   # Launched, then no task record: lost.
            return report.verdict(folder)
        for arms, broken in ((("single",), {"single": [3], "parallel": []}),
                             (("parallel",), {"single": [], "parallel": [3]}),
                             (("single", "parallel"), {"single": [3], "parallel": [3]})):
            with self.subTest(lost=arms):
                result = lose(*arms)
                self.assertEqual((result["complete"], result["missing"], result["paired"], result["unpaired"]),
                                 (True, [], [1, 2, 3], {"single": [], "parallel": []}))
                self.assertEqual(result["trials_with_a_broken_piece"], broken)
                for arm in arms:
                    row = result["arms"][arm][-1]
                    self.assertEqual((row["trial"], row["status"], row["valid"], row["wall_s"], row["wall_capped"]),
                                     (3, "lost", True, 3600.0, True))
                    self.assertEqual(result["summary"][arm]["wall_max_s"], 3600.0)
                    self.assertEqual(result["lost"][arm], {"3": "no task recorded (exit 1)"})
                    self.assertEqual({g["score"] for g in self.grades(result, arm, 3)}, {0})
                    self.assertEqual(result["grades_by_class"]["King"][arm], 3)   # Median of 3, 3, 0.
                self.assertNotIn("INCOMPLETE", report.markdown(result))
                self.assertIn("compared with the other arm's trial of the same number", report.markdown(result))
        self.assertTrue(lose("single")["ships"])   # Parallel faster, never worse, the floor 0 <= 1.
        self.assertFalse(lose("parallel")["ships"])   # A broken piece the single arm never had.
        both = lose("single", "parallel")
        self.assertEqual((both["floor_holds"], both["faster"], both["ships"]), (True, True, True))

    def test_a_lost_trials_rerun_never_stands_in_for_it(self):
        """E2E-13 H-3 under C6: the attempt a resume set aside is the trial, whatever its rerun did. Its rerun's
        grades never replace its zeros, a single rerun's stall never counts, and a parallel rerun's stall still does."""
        same = {"King": [3, 3]}
        folder = self.run_folder({"single": [600, 610], "parallel": [300, 310]}, {"single": same, "parallel": same})
        self.launch(folder, "single", 1, 1, kept=True)
        self.launch(folder, "parallel", 2, 1, kept=True)
        for arm in report.ARMS:
            results = json.loads((folder / arm / report.RESULTS).read_text())
            results["records"][0 if arm == "single" else 1]["main_loop"]["main_loop_late_ms_max"] = 9000.0
            (folder / arm / report.RESULTS).write_text(json.dumps(results))
        result = report.verdict(folder)
        self.assertEqual((result["paired"], result["complete"]), ([1, 2], True))
        self.assertEqual(result["trials_with_a_broken_piece"], {"single": [1], "parallel": [2]})
        self.assertEqual(({g["score"] for g in self.grades(result, "single", 1)},
                          {g["score"] for g in self.grades(result, "parallel", 2)}), ({0}, {0}))
        self.assertEqual((result["summary"]["single"]["main_loop_max_ms"], result["summary"]["parallel"]["main_loop_max_ms"]),
                         (50.0, 9000.0))   # Only the parallel rerun's freeze counts against its arm.
        rerun = next(row for row in result["arms"]["single"] if row["trial"] == 1 and row["status"] != "lost")
        self.assertEqual((rerun["valid"], rerun["counted"], rerun["rerun_of_lost"]), (False, False, True))
        self.assertIn("(a lost trial's rerun, not compared)", report.markdown(result))

    @staticmethod
    def grades(result, arm, trial):
        """The scores the verdict used for one trial: re-read from its own grade list."""
        return [g for g in result["graded"] if g["arm"] == arm and g["trial"] == trial]

    def launch(self, folder, arm, trial, code, kept=False):
        """A trial's launch record as the bench writes it, in its arm's folder or kept by a resume."""
        place = folder / arm / ("interrupted/angelo-trial-%02d-20261025-090000" % trial if kept else "")
        place.mkdir(parents=True, exist_ok=True)
        (place / ("angelo-trial-%02d-launch.json" % trial)).write_text(json.dumps({"exit_code": code}))
        return place

    def test_a_trial_the_bench_cap_cut_is_a_failed_trial_whatever_its_rerun_did(self):
        same = {"King": [3, 3]}
        folder = self.run_folder({"single": [600, 610], "parallel": [300, 310]}, {"single": same, "parallel": same})
        self.launch(folder, "parallel", 1, "timeout", kept=True)
        result = report.verdict(folder)   # C6: compared with single's trial 1 as a failed trial.
        self.assertEqual((result["lost"]["parallel"], result["paired"]), ({"1": "cut by the bench's cap"}, [1, 2]))
        self.assertEqual(result["trials_with_a_broken_piece"]["parallel"], [1])
        self.assertFalse(result["ships"])
        self.assertEqual(result["set_aside"]["parallel"], {"1": ["cap"]})

    def test_c6_pairs_cap_cuts_and_resume_set_asides_as_failed_trials_in_both_arms(self):
        """The maintainer's C6 clarification (2026-10-25): "C6 covers all three, same as C1-C4: (a) crash with no task
        record, (b) outer-cap cut, (c) resume set-aside, in both arms, paired." (a) is pinned above; here (b) and (c),
        in each arm alone and in both: a failed trial of its arm, every class 0, the cap its wall clock, paired."""
        same = {"King": [3, 3]}
        for code, why in (("timeout", "cut by the bench's cap"), (1, "ended with no record (exit 1)")):
            for arms in (("single",), ("parallel",), ("single", "parallel")):
                with self.subTest(code=code, arms=arms):
                    folder = self.run_folder({"single": [600, 610], "parallel": [300, 310]},
                                             {"single": same, "parallel": same})
                    for arm in arms:
                        self.launch(folder, arm, 1, code, kept=True)
                    result = report.verdict(folder)
                    self.assertEqual((result["complete"], result["paired"]), (True, [1, 2]))
                    for arm in report.ARMS:
                        self.assertEqual(result["lost"][arm], {"1": why} if arm in arms else {})
                        self.assertEqual(result["trials_with_a_broken_piece"][arm], [1] if arm in arms else [])
                    for arm in arms:
                        row = next(row for row in result["arms"][arm] if row["trial"] == 1 and row["status"] == "lost")
                        self.assertEqual((row["valid"], row["wall_s"], row["wall_capped"]), (True, 3600.0, True))
                        self.assertEqual({g["score"] for g in self.grades(result, arm, 1)}, {0})
                    self.assertNotIn("INCOMPLETE", report.markdown(result))

    def test_nothing_outside_a_pair_relaxes_parallels_bar(self):
        """E2E-12 H-2's cases: each would have shipped; none does."""
        same, broken_first = {"King": [3, 3, 3]}, {"King": [1, 3, 3]}
        walls = {"single": [600, 610, 620], "parallel": [300, 310, 320]}
        stall = self.run_folder({"single": [600, 610], "parallel": [300, 310, 320]},   # S1: a freeze, unpaired.
                                {"single": {"King": [3, 3]}, "parallel": same})
        records = json.loads((stall / "parallel" / report.RESULTS).read_text())
        records["records"][2]["main_loop"]["main_loop_late_ms_max"] = 9000.0
        (stall / "parallel" / report.RESULTS).write_text(json.dumps(records))
        self.assertEqual(report.verdict(stall)["smoothness_blocker"], ["main_loop_max_ms"])
        unpaired = self.run_folder({"single": [600, 610, 620], "parallel": [300, 310]},   # S2: single's, unpaired.
                                   {"single": {"King": [3, 3, 1]}, "parallel": {"King": [1, 3]}})
        self.assertEqual(report.verdict(unpaired)["trials_with_a_broken_piece"], {"single": [], "parallel": [1]})
        cut = self.run_folder(walls, {"single": same, "parallel": broken_first})   # S3: single's cut, unpaired.
        self.launch(cut, "single", 4, "timeout", kept=True)   # No parallel trial 4: outside a pair.
        self.assertEqual(report.verdict(cut)["trials_with_a_broken_piece"], {"single": [], "parallel": [1]})
        self.assertFalse(report.verdict(cut)["ships"])
        crash = self.run_folder({"single": [600, 610], "parallel": [300]},   # S5: a crash with no record.
                                {"single": {"King": [3, 3]}, "parallel": {"King": [3]}})
        self.launch(crash, "parallel", 2, 1)
        result = report.verdict(crash)
        self.assertEqual((result["lost"]["parallel"], result["trials_with_a_broken_piece"]["parallel"]),
                         ({"2": "no task recorded (exit 1)"}, [2]))
        self.assertFalse(result["ships"])

    def test_an_attempt_a_resume_set_aside_counts_unless_an_exclusion_voided_it(self):
        """A crash whose receipt never came, run again by a resume, stays a failed trial (E2E-13 H-3); a provider
        limit is the pre-registered exclusion and is not counted."""
        same = {"King": [3, 3]}
        crash = self.run_folder({"single": [600, 610], "parallel": [300, 310]}, {"single": same, "parallel": same})
        self.launch(crash, "parallel", 1, 1, kept=True)
        result = report.verdict(crash)
        self.assertEqual(result["lost"]["parallel"], {"1": "ended with no record (exit 1)"})
        self.assertFalse(result["ships"])
        limited = self.run_folder({"single": [600, 610], "parallel": [300, 310]}, {"single": same, "parallel": same})
        place = self.launch(limited, "parallel", 1, 0, kept=True)
        (place / "angelo-trial-01").mkdir()
        (place / "angelo-trial-01" / "eval-live-agent.json").write_text(json.dumps({"trials": [{"invalid": "rate"}]}))
        result = report.verdict(limited)
        self.assertEqual((result["lost"]["parallel"], result["set_aside"]["parallel"]), ({}, {"1": ["provider limit"]}))
        self.assertTrue(result["ships"])

    def test_a_moved_aside_workspace_is_no_attempt_and_a_lost_trials_rerun_still_counts(self):
        """E2E-14 L-4: --angelo-stable-workspace moves the workspace aside before a trial (no attempt of it); a lost
        parallel trial's rerun still counts against parallel (its longest stall)."""
        same = {"King": [3, 3]}
        folder = self.run_folder({"single": [600, 610], "parallel": [300, 310]}, {"single": same, "parallel": same})
        (folder / "parallel" / "interrupted" / "angelo-trial-02-20261025-090000" / "angelo-state").mkdir(parents=True)
        self.assertEqual((report.verdict(folder)["lost"]["parallel"], report.verdict(folder)["ships"]), ({}, True))
        self.launch(folder, "parallel", 1, "timeout", kept=True)   # Trial 1 was cut; its rerun froze the lead window.
        results = json.loads((folder / "parallel" / report.RESULTS).read_text())
        results["records"][0]["main_loop"]["main_loop_late_ms_max"] = 9000.0
        (folder / "parallel" / report.RESULTS).write_text(json.dumps(results))
        self.assertEqual(report.verdict(folder)["summary"]["parallel"]["main_loop_max_ms"], 9000.0)

    def test_a_trial_the_harness_ended_before_its_message_is_invalid_never_lost(self):
        folder = self.run_folder({"single": [600, 610], "parallel": [300]},
                                 {"single": {"King": [3, 3]}, "parallel": {"King": [3]}})
        place = self.launch(folder, "parallel", 2, 1)
        (place / "angelo-trial-02").mkdir()
        (place / "angelo-trial-02" / "eval-live-agent.json").write_text(json.dumps(
            {"trials": [{"aborted": "The team connection was not ready; nothing was sent", "tasks": []}]}))
        result = report.verdict(folder)   # Invalid: owed a rerun, so the evidence is not complete yet.
        self.assertEqual((result["lost"]["parallel"], result["missing"]), ({}, ["parallel trial 2: never finished"]))
        self.assertFalse(result["ships"])

    def test_incomplete_evidence_is_named_and_never_ships(self):
        """Audit P1's cases against a complete five-trial control: a missing trial, grade class, wall clock or stall
        measurement, or an unscored grade item, each named; the verdict is incomplete and never ships."""
        walls = {"single": [600] * 5, "parallel": [300] * 5}

        def verdict(change=None, parallel_trials=5):
            folder = self.run_folder({"single": walls["single"], "parallel": walls["parallel"][:parallel_trials]},
                                     {"single": {}, "parallel": {}}, trials=5)
            if change:
                change(folder)
            return report.verdict(folder)

        def edit(folder, name, apply):
            path = folder / ("grades.json" if name == "grades" else "parallel/" + report.RESULTS)
            data = json.loads(path.read_text())
            apply(data)
            path.write_text(json.dumps(data))

        def no_wall(data):
            data["records"][4].pop("send_to_idle_ms", None)
            data["records"][4]["status"] = "error"
        cases = {
            None: [],
            "trial": ["parallel trial 5: never finished"],
            "class": ["%s trial %d: grade of Board" % (arm, n) for arm in ("single", "parallel") for n in range(1, 6)],
            "wall": ["parallel trial 5: wall clock"],
            "stall": ["parallel trial 5: main-loop p95", "parallel trial 5: longest stall"],
            "item": ["parallel trial 5: grade of King"]}
        def without_board(data):
            data["grades"] = [g for g in data["grades"] if g["part"] != "Board"]

        def unscored(data):
            for g in data["grades"]:
                if (g["arm"], g["trial"], g["part"]) == ("parallel", 5, "King"):
                    g["score"] = None
        changes = {"class": lambda f: edit(f, "grades", without_board), "wall": lambda f: edit(f, "results", no_wall),
                   "stall": lambda f: edit(f, "results", lambda d: d["records"][4].update(main_loop={})),
                   "item": lambda f: edit(f, "grades", unscored)}
        for case, expected in cases.items():
            with self.subTest(case=case):
                result = verdict(changes.get(case), parallel_trials=4 if case == "trial" else 5)
                self.assertEqual((result["missing"], result["complete"], result["ships"]),
                                 (expected, not expected, not expected))
        self.assertIn("INCOMPLETE", report.markdown(verdict(parallel_trials=4)))


if __name__ == "__main__":
    unittest.main()
