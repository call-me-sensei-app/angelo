# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""BENCH-PREREG Amendment F in the parallel verdict, without a model or Blender.

Pinned here: each trial's task record (``eval-live-agent.json``, ``bench_integrity.trial_stamp``) must show it was sent
its arm's registered message: the single arm the scenario's brief and no ``parallel_request``; the parallel arm the
brief, a blank line, then Amendment D's sentence, and that sentence. A trial that does not (another or no digest, a
missing or extra request, Amendment A's old sentence) is named with what it recorded and the verdict is INCOMPLETE; it
is never a failed trial of its arm. A lost trial keeps C6's treatment. One check for every registered test and every
worker cap: it names no test, and a test added to the registry is checked without a code change.

The Clarification of F (maintainer-approved 2026-10-25): every attempt a resume set aside under an arm's
``interrupted/`` folder that left a task record is checked the same way, in both arms, whatever the resume set it aside
for (a provider limit, the bench's cap, a crash after its task was recorded); a wrong one is named with the attempt it
was and the verdict is INCOMPLETE, every decided figure unchanged. A correct one, an attempt with no task record (none,
or one that records no task: C6's lost trial), one that sent nothing (rule 2), a moved-aside workspace and a warm-up add
nothing. An attempt's record that cannot be read cannot show its message and is named.
"""
import hashlib
import json
import re
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import bench_messages
import bench_parallel_report as report
import eval_parallel
import eval_provider_limits as limits
import scenario_specs
import test_native_bench_parallel as verdicts   # Its run fixture; its tests run there.
import test_native_bench_parallel_runs as runs

# Amendment A's sentence, replaced by D's (its registered digest, BENCH-PREREG Amendment A).
A_SENTENCE = ("Split this work across workers: give each worker its own pieces to build, and hand out more as they "
              "finish.")
A_SENTENCE_SHA256 = "e383f10cca9eedd2298e6e0374543c8f6443b126658d15342b9d3f7c8917b185"
TESTS = ("oneshot-chess", "oneshot-parlour", "oneshot-roadster", "oneshot-outfit")   # Chess and the held-out tests.
CAPS = (1, 2, 3)   # Amendment B1 and B4's runs.
WALLS = {"single": [600, 620], "parallel": [300, 320]}
DECIDED = ("paired", "unpaired", "lost", "crashed", "grades_by_class", "trials_with_a_broken_piece", "floor_holds",
           "quality_misses", "faster", "speedup_median", "exercised", "treatment_applied", "summary")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def brief(task_set):
    import eval_live_tasks
    return eval_live_tasks.tasks_for(task_set)[0]["prompt"]


def edit_record(folder, arm, trial, change):
    path = folder / arm / ("angelo-trial-%02d" % trial) / "eval-live-agent.json"
    value = json.loads(path.read_text())
    change(value["trials"][0]["tasks"][0])
    path.write_text(json.dumps(value))


def with_cap(folder, cap):
    """Every parallel record says the worker cap that applied (Amendment B1): the check never reads it."""
    path = folder / "parallel" / report.RESULTS
    value = json.loads(path.read_text())
    for record in value["records"]:
        record["parallel"] = dict(record.get("parallel") or {}, worker_limit=cap)
    path.write_text(json.dumps(value))
    return folder


OTHER_SENTENCE = "Split this work between the workers however you like."   # Neither A's nor D's (F2).


class RegisteredMessageTests(unittest.TestCase):
    fixture = verdicts.VerdictTests()

    def run_folder(self, task_set, cap):
        return with_cap(self.fixture.run_folder(WALLS, {"single": {}, "parallel": {}}, task_set=task_set), cap)

    def wrong(self, task_set):
        """{case: (arm, change)}: each way F2 names a record that does not show its arm's registered message."""
        text = brief(task_set)
        return {
            "a single trial with a parallel_request": ("single", lambda r: r.update(
                parallel_request=eval_parallel.PARALLEL_REQUEST)),
            "a single trial sent the parallel message": ("single", lambda r: r.update(
                prompt_sha256=sha(text + "\n\n" + eval_parallel.PARALLEL_REQUEST))),
            "a parallel trial with Amendment A's old sentence": ("parallel", lambda r: r.update(
                prompt_sha256=sha(text + "\n\n" + A_SENTENCE), parallel_request=A_SENTENCE)),
            "a parallel trial without its request": ("parallel", lambda r: r.pop("parallel_request")),
            "a parallel trial sent the single message": ("parallel", lambda r: r.update(prompt_sha256=sha(text))),
            "a missing digest (single)": ("single", lambda r: r.pop("prompt_sha256")),
            "a missing digest (parallel)": ("parallel", lambda r: r.pop("prompt_sha256")),
            "a wrong digest (single)": ("single", lambda r: r.update(prompt_sha256=sha(text + " "))),
            "a wrong digest (parallel)": ("parallel", lambda r: r.update(prompt_sha256="0" * 64)),
            "another test's brief": ("single", lambda r: r.update(prompt_sha256=sha(brief(
                next(other for other in TESTS if other != task_set))))),
            "a parallel trial sent another sentence, its digest matching": ("parallel", lambda r: r.update(
                prompt_sha256=sha(text + "\n\n" + OTHER_SENTENCE), parallel_request=OTHER_SENTENCE)),
        }

    def test_the_old_sentence_is_amendment_as_and_the_new_one_ds(self):
        self.assertEqual(sha(A_SENTENCE), A_SENTENCE_SHA256)
        self.assertEqual((sha(eval_parallel.PARALLEL_REQUEST), eval_parallel.FROZEN_SHA256),
                         (bench_messages.D_SENTENCE_SHA256, bench_messages.D_SENTENCE_SHA256))
        for task_set in TESTS:   # The message composed from F1's words is the one the harness sends.
            self.assertEqual(eval_parallel.with_request(brief(task_set), eval_parallel.PARALLEL_REQUEST),
                             brief(task_set) + "\n\n" + eval_parallel.PARALLEL_REQUEST)

    def test_a_correct_run_is_unchanged(self):
        for task_set in TESTS:
            for cap in CAPS:
                with self.subTest(test=task_set, cap=cap):
                    result = report.verdict(self.run_folder(task_set, cap))
                    self.assertEqual((result["complete"], result["missing"], result["ships"]), (True, [], True))
                    self.assertEqual(result["messages_not_shown"], [])
                    text = report.markdown(result)
                    self.assertIn("**Verdict: ships**", text)
                    self.assertIn("registered message (Amendment F): yes.", text)

    def test_a_trial_not_shown_its_registered_message_is_named_and_the_verdict_is_incomplete(self):
        for task_set in TESTS:
            for cap in CAPS:
                control = report.verdict(self.run_folder(task_set, cap))
                for case, (arm, change) in self.wrong(task_set).items():
                    for trial in (1, 2):
                        with self.subTest(test=task_set, cap=cap, case=case, trial=trial):
                            folder = self.run_folder(task_set, cap)
                            edit_record(folder, arm, trial, change)
                            result = report.verdict(folder)
                            self.assertEqual((result["complete"], result["ships"]), (False, False))
                            shown = [(entry["arm"], entry["trial"]) for entry in result["messages_not_shown"]]
                            self.assertEqual(shown, [(arm, trial)])
                            named = [line for line in result["missing"] if line.startswith(
                                "%s trial %d: does not show its arm's registered message" % (arm, trial))]
                            self.assertEqual(len(named), 1, result["missing"])
                            recorded = result["messages_not_shown"][0]["recorded"][0]
                            self.assertIn(json.dumps(recorded), named[0])   # Named with what it recorded.
                            text = report.markdown(result)
                            self.assertIn("**Verdict: INCOMPLETE: does not ship**", text)
                            self.assertIn("%s trial %d: does not show" % (arm, trial), text)
                            # Not a failed trial of its arm: every other figure is the correct run's.
                            self.assertEqual({key: result[key] for key in DECIDED},
                                             {key: control[key] for key in DECIDED})

    def test_a_lost_trial_keeps_c6s_treatment_and_a_trial_that_sent_nothing_is_not_checked(self):
        pair = runs.PairTests()
        for task_set in TESTS[:2]:
            for arm in report.ARMS:
                with self.subTest(test=task_set, lost=arm):
                    walls = {name: WALLS[name] + ([] if name == arm else [640]) for name in report.ARMS}
                    folder = with_cap(self.fixture.run_folder(walls, {"single": {}, "parallel": {}}, trials=3,
                                                              task_set=task_set), 1)
                    place = pair.launch(folder, arm, 3, 1)   # Launched, then no task record: lost (C6).
                    (place / "angelo-trial-03").mkdir()   # Whatever its folder holds is never read as a message.
                    (place / "angelo-trial-03" / "eval-live-agent.json").write_text(json.dumps(
                        {"trials": [{"tasks": [{"prompt_sha256": "0" * 64}]}]}))
                    result = report.verdict(folder)
                    self.assertEqual((result["complete"], result["missing"], result["messages_not_shown"]),
                                     (True, [], []))
                    self.assertEqual(result["lost"][arm], {"3": "no task recorded (exit 1)"})
                    self.assertIn(3, result["trials_with_a_broken_piece"][arm])
                    row = result["arms"][arm][-1]
                    self.assertEqual((row["trial"], row["status"], row["wall_s"]), (3, "lost", 3600.0))
                    self.assertNotIn("INCOMPLETE", report.markdown(result))
        folder = self.fixture.run_folder({"single": [600, 610], "parallel": [300]}, {"single": {}, "parallel": {}})
        place = pair.launch(folder, "parallel", 2, 1)   # Rule 2: the harness ended it before its message.
        (place / "angelo-trial-02").mkdir()
        (place / "angelo-trial-02" / "eval-live-agent.json").write_text(json.dumps(
            {"trials": [{"aborted": "The team connection was not ready; nothing was sent", "tasks": []}]}))
        result = report.verdict(folder)
        self.assertEqual((result["messages_not_shown"], result["missing"]), ([], ["parallel trial 2: never finished"]))
        self.assertEqual(bench_messages.unshown(folder, {"parallel": [2]}), [])   # F3: never checked, by its own skip.

    def test_a_test_added_to_the_registry_is_checked_without_a_code_change(self):
        name, task_set = "oneshot_registry_probe", "oneshot-registry-probe"
        probe = types.ModuleType("scenario_" + name)
        probe.TURNS = [{"id": "brief", "title": "Probe", "prompt": "Build a probe: a sphere on a cube.",
                        "references": [], "checks": []}]
        probe.GRADE_CLASSES = {"Probe": ("hero",)}
        probe.LIMITS = {"wall_s": 1800}
        with mock.patch.dict(sys.modules, {probe.__name__: probe}), \
                mock.patch.object(scenario_specs, "ONESHOT_NAMES", scenario_specs.ONESHOT_NAMES + (name,)), \
                mock.patch.object(scenario_specs, "ONESHOT_TASK_SETS", scenario_specs.ONESHOT_TASK_SETS + (task_set,)):
            right = report.verdict(self.run_folder(task_set, 2))
            self.assertEqual((right["complete"], right["messages_not_shown"], right["ships"]), (True, [], True))
            for case, (arm, change) in self.wrong(task_set).items():
                with self.subTest(case=case):
                    folder = self.run_folder(task_set, 2)
                    edit_record(folder, arm, 1, change)
                    result = report.verdict(folder)
                    self.assertEqual([(e["arm"], e["trial"]) for e in result["messages_not_shown"]], [(arm, 1)])
                    self.assertFalse(result["complete"] or result["ships"])

    def test_an_edited_sentence_in_the_harness_is_never_taken_for_amendment_ds(self):
        """The registered sentence is pinned by its frozen digest: a harness constant edited to anything else, even
        when every parallel record agrees with it, shows no trial its registered message."""
        with mock.patch.object(eval_parallel, "PARALLEL_REQUEST", A_SENTENCE):
            folder = self.run_folder("oneshot-chess", 1)   # Stamped by the harness's own path, with the edited text.
            result = report.verdict(folder)
        self.assertEqual([(e["arm"], e["trial"]) for e in result["messages_not_shown"]],
                         [("parallel", 1), ("parallel", 2)])
        self.assertFalse(result["complete"] or result["ships"])

    def test_the_check_names_no_test_and_reads_no_cap(self):
        source = Path(bench_messages.__file__).read_text()
        for test in scenario_specs.ONESHOT_IDS + scenario_specs.SCENARIO_IDS:
            for spelling in {test, test.replace("-", "_")}:
                self.assertIsNone(re.search(r"(?<![A-Za-z_])%s(?![A-Za-z_])" % re.escape(spelling), source), spelling)
        for key in ("worker_cap", "worker_limit", "wall_s", "GRADE_CLASSES"):
            self.assertNotIn(key, source)
        # D's digest is its only pinned digest: no test's brief is copied in as a digest.
        self.assertEqual(re.compile(r"[0-9a-f]{64}").findall(source), [bench_messages.D_SENTENCE_SHA256])


STAMP = "20261025-090000"   # The bench's set_aside names an attempt's folder with the time it was set aside.
NO_TASK_RECORD = ("no task record", "a trial folder without its record", "a record of no task (a crash mid-task)",
                  "a record of no trial", "sent nothing (rule 2)", "a moved-aside workspace", "a warm-up")
KINDS = {   # What a resume set an attempt with a task record aside for: {kind: (launch exit code, record fields)}.
    "a provider limit": (3, {"invalid": limits.INVALID, "aborted": "provider limit"}),   # Rule 1: run again.
    "the bench's cap": ("timeout", {}),   # Lost (C6): a failed trial of its arm.
    "a crash after its task was recorded": (1, {}),   # Lost (C6) too.
}


def set_aside_attempt(folder, arm, trial, code, record=None, stamp=STAMP):
    """An attempt a resume set aside as the bench leaves it: its launch record and, unless None, its task record (an
    object, or text as written)."""
    place = folder / arm / "interrupted" / ("angelo-trial-%02d-%s" % (trial, stamp))
    place.mkdir(parents=True)
    (place / ("angelo-trial-%02d-launch.json" % trial)).write_text(json.dumps({"exit_code": code}))
    if record is not None:
        (place / ("angelo-trial-%02d" % trial)).mkdir()
        (place / ("angelo-trial-%02d" % trial) / "eval-live-agent.json").write_text(
            record if isinstance(record, str) else json.dumps(record))
    return "interrupted/" + place.name


def stamped(arm, task_set, kind, change=None):
    """A set-aside attempt's task record: its arm's stamped tasks (``change`` applied to the first) and the kind's."""
    tasks = verdicts.sent(arm, task_set)
    if change:
        change(tasks[0])
    return dict(KINDS[kind][1], tasks=tasks)


class SetAsideAttemptTests(unittest.TestCase):
    """The Clarification of F, against the same run with the attempt's record correct."""
    messages = RegisteredMessageTests()

    def run_with(self, task_set, cap, arm, trial, kind, change=None):
        folder = self.messages.run_folder(task_set, cap)
        attempt = set_aside_attempt(folder, arm, trial, KINDS[kind][0], {"trials": [stamped(arm, task_set, kind,
                                                                                            change)]})
        return folder, attempt

    def test_a_set_aside_attempt_not_shown_its_registered_message_makes_the_run_incomplete_in_both_arms(self):
        for task_set in TESTS:
            for cap in CAPS:
                for kind in KINDS:
                    controls = {(arm, trial): report.verdict(self.run_with(task_set, cap, arm, trial, kind)[0])
                                for arm in report.ARMS for trial in (1, 2)}
                    for index, (case, (arm, change)) in enumerate(self.messages.wrong(task_set).items()):
                        trial = 1 + index % 2
                        with self.subTest(test=task_set, cap=cap, kind=kind, case=case, trial=trial):
                            folder, attempt = self.run_with(task_set, cap, arm, trial, kind, change)
                            result, control = report.verdict(folder), controls[(arm, trial)]
                            self.assertEqual((control["complete"], control["messages_not_shown"]), (True, []))
                            self.assertEqual((result["complete"], result["ships"]), (False, False))
                            self.assertEqual([(e["arm"], e["trial"], e["attempt"])
                                              for e in result["messages_not_shown"]], [(arm, trial, attempt)])
                            line = "%s trial %d, the attempt a resume set aside as %s: does not show its arm's " \
                                   "registered message (Amendment F)" % (arm, trial, attempt)
                            self.assertEqual(len(result["missing"]), 1, result["missing"])
                            self.assertTrue(result["missing"][0].startswith(line), result["missing"])
                            self.assertIn(json.dumps(result["messages_not_shown"][0]["recorded"]), result["missing"][0])
                            text = report.markdown(result)
                            self.assertIn("**Verdict: INCOMPLETE: does not ship**", text)
                            self.assertIn(line, text)
                            # Never a failed trial of its arm: every decided figure is the correct attempt's run's.
                            self.assertEqual({key: result[key] for key in DECIDED + ("set_aside",)},
                                             {key: control[key] for key in DECIDED + ("set_aside",)})

    def test_every_attempt_is_checked_and_a_current_trial_is_named_apart(self):
        folder = self.messages.run_folder("oneshot-chess", 2)
        right = set_aside_attempt(folder, "parallel", 1, 3, {"trials": [stamped("parallel", "oneshot-chess",
                                                                                "a provider limit")]})
        wrong = set_aside_attempt(folder, "parallel", 1, "timeout", {"trials": [stamped(
            "parallel", "oneshot-chess", "the bench's cap", lambda r: r.pop("parallel_request"))]}, "20261025-100000")
        edit_record(folder, "single", 2, lambda r: r.update(prompt_sha256="0" * 64))
        result = report.verdict(folder)
        self.assertEqual([(e["arm"], e["trial"], e.get("attempt")) for e in result["messages_not_shown"]],
                         [("single", 2, None), ("parallel", 1, wrong)])
        self.assertNotIn(right, "\n".join(result["missing"]))
        self.assertTrue(result["missing"][0].startswith("single trial 2: does not show"), result["missing"])
        self.assertFalse(result["complete"] or result["ships"])

    def test_a_correct_attempt_and_an_attempt_without_a_task_record_add_nothing(self):
        same = ("a provider limit", "sent nothing (rule 2)", "a moved-aside workspace", "a warm-up")   # Not lost.
        for task_set in TESTS:
            for cap in CAPS:
                base = report.verdict(self.messages.run_folder(task_set, cap))
                for arm in report.ARMS:
                    for case in tuple(KINDS) + NO_TASK_RECORD:
                        with self.subTest(test=task_set, cap=cap, arm=arm, case=case):
                            folder = self.messages.run_folder(task_set, cap)
                            self.add(folder, arm, task_set, case)
                            result = report.verdict(folder)
                            self.assertEqual((result["complete"], result["missing"], result["messages_not_shown"]),
                                             (True, [], []))
                            text = report.markdown(result)
                            self.assertNotIn("INCOMPLETE", text)
                            self.assertIn("Each trial and set-aside attempt sent its arm's registered message "
                                          "(Amendment F): yes.", text)
                            if case in same:   # Excluded, or no attempt: the run is the one without it.
                                self.assertEqual({key: result[key] for key in DECIDED + ("ships",)},
                                                 {key: base[key] for key in DECIDED + ("ships",)})
                            else:   # C6: a lost trial of its arm, never INCOMPLETE.
                                self.assertIn("1", result["lost"][arm])

    @staticmethod
    def add(folder, arm, task_set, case):
        """Trial 1's attempt of ``case`` set aside in ``arm``: a correct one of a kind, or one with no task record."""
        if case in KINDS:
            set_aside_attempt(folder, arm, 1, KINDS[case][0], {"trials": [stamped(arm, task_set, case)]})
        elif case == "no task record":
            set_aside_attempt(folder, arm, 1, 1)
        elif case == "a trial folder without its record":
            set_aside_attempt(folder, arm, 1, 1, {})
            (folder / arm / "interrupted" / ("angelo-trial-01-" + STAMP) / "angelo-trial-01" / "eval-live-agent.json"
             ).unlink()
        elif case == "a record of no task (a crash mid-task)":
            set_aside_attempt(folder, arm, 1, 1, {"trials": [{"unfinished_task": {"task": "brief", "phase": "running"},
                                                              "tasks": []}]})
        elif case == "a record of no trial":
            set_aside_attempt(folder, arm, 1, "timeout", {"trials": []})
        elif case == "sent nothing (rule 2)":
            set_aside_attempt(folder, arm, 1, 1, {"trials": [{"aborted": "nothing was sent", "tasks": []}]})
        elif case == "a moved-aside workspace":
            (folder / arm / "interrupted" / ("angelo-trial-01-" + STAMP) / "angelo-state").mkdir(parents=True)
        else:   # A warm-up as the bench leaves it (warm-up/, never a trial), its record showing no registered message.
            place = folder / arm / "warm-up" / "angelo-01"
            place.mkdir(parents=True)
            (place / "eval-live-agent.json").write_text(json.dumps({"trials": [{"tasks": [dict(
                verdicts.sent(arm, task_set)[0], prompt_sha256="0" * 64)]}]}))
            (folder / arm / "warm-up" / "WARM-UP-01.json").write_text(json.dumps({"warm_up": True}))

    def test_an_attempts_record_that_cannot_be_read_is_named(self):
        """It cannot say it records no task, so it cannot show its message: named, never passed over."""
        cases = {"not JSON": "{\"trials\": [", "empty (a write cut short)": "", "a list": [1],
                 "trials an object": {"trials": {"a": 1}},
                 "tasks a number": {"trials": [{"tasks": 5}]}, "a task not an object": {"trials": [{"tasks": [1]}]},
                 "a task without its digest": {"trials": [{"tasks": [{"task": "brief"}]}]}}
        for arm in report.ARMS:
            for case, record in cases.items():
                with self.subTest(arm=arm, case=case):
                    folder = self.messages.run_folder("oneshot-roadster", 3)
                    attempt = set_aside_attempt(folder, arm, 2, "timeout", record)
                    result = report.verdict(folder)
                    self.assertEqual([(e["arm"], e["trial"], e["attempt"]) for e in result["messages_not_shown"]],
                                     [(arm, 2, attempt)])
                    self.assertFalse(result["complete"] or result["ships"])
                    self.assertEqual(result["lost"][arm], {"2": "cut by the bench's cap"})   # C6 unchanged.
                    if case != "a task without its digest":   # The others show no task: named as unreadable.
                        self.assertIn("recorded no readable task record", "\n".join(result["missing"]))


if __name__ == "__main__":
    unittest.main()
