# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of the Blender MCP side's answer check (``bench_mcp_answers``) and its place in both MCP runtimes.

The MCP side must grade a question exactly as the Angelo side does: truth
computed before the prompt, the answer line read in the whole turn against it,
the scene unchanged, the harness's fixture undone whatever happened. Both
runtimes' ``run_task`` are executed here against a stand-in socket and stream;
the code the driver sends to Blender is executed for real by
``test_blender_gate_answers.py``.
"""

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_answers as answer_check  # noqa: E402
import bench_mcp_codex  # noqa: E402
import bench_mcp_grading as grading  # noqa: E402
import bench_mcp_trial  # noqa: E402
import eval_answers  # noqa: E402
import eval_gate  # noqa: E402
import eval_grading  # noqa: E402
import eval_live_tasks as live  # noqa: E402
import survey_fixtures  # noqa: E402
import survey_truth  # noqa: E402

TRUTH = {"accept": [["Brass Marker", "Cube"]]}
RIGHT, WRONG = "Two meshes.\nMESHES: Brass Marker, Cube", "One mesh.\nMESHES: Cube"
UNDO = [{"remove_object": "Brass Marker"}]


def said(*texts):
    return [{"role": "assistant", "text": text} for text in texts]


class Client:
    """The add-on socket: answers each request by what its code asks for, and keeps the order of requests."""

    def __init__(self, truth=TRUTH, unchanged=True, fail=(), restored=None, present=True):
        self.truth, self.unchanged, self.fail, self.restored, self.present = truth, unchanged, fail, restored, present
        self.requests, self.codes = [], []

    def settle(self):
        pass

    def execute(self, code):
        kind = next(name for name, marker in (("setup", "bench_mcp_answers.setup("),
                                              ("restore", "bench_mcp_answers.restore("),
                                              ("grade", "_host_check"), ("snapshot", "'digest'")) if marker in code)
        self.requests.append(kind)
        self.codes.append(code)
        if kind in self.fail:
            raise RuntimeError("the add-on did not answer")
        return {"setup": {"steps": ["fixture:probe_object"], "undo": list(UNDO), "truth": self.truth},
                "restore": {"restored": len(UNDO) if self.restored is None else self.restored},
                "snapshot": {"digest": "d", "eval_object_present": self.present, "objects": []},
                "grade": {"host_check": {"scene_unchanged": self.unchanged}, "host_check_passed": self.unchanged}}[kind]


def claude_turn(client, *texts, tool_between=False):
    """A stand-in Claude Code stream whose turn says ``texts`` (assistant messages), then ends with the last."""
    events = []
    for index, text in enumerate(texts):
        events.append((0.1, {"type": "assistant", "message": {"id": "m%d" % index, "content": [
            {"type": "text", "text": text}]}}))
        if tool_between and index == 0:
            events.append((0.2, {"type": "assistant", "message": {"id": "t", "content": [
                {"type": "tool_use", "id": "u", "name": "mcp__blender__get_objects_summary", "input": {}}]}}))
    result = {"type": "result", "subtype": "success", "is_error": False, "result": texts[-1] if texts else ""}
    stream = mock.Mock(process=object(), spawned=0.)
    stream.send.side_effect = lambda *args: client.requests.append("send")
    stream.collect.return_value = (events + [(0.4, result)], result, None)
    usage = mock.Mock()
    usage.turn.return_value = {}
    return stream, usage


def codex_turn(client, *texts):
    """A stand-in Codex stream whose turn completes with ``texts`` as its agent messages."""
    events = [(0.1, {"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "a%d" % index,
                                                                     "text": text}}})
              for index, text in enumerate(texts)]
    completed = {"turn": {"status": "completed"}}
    stream = mock.Mock(thread_id="thread", spawned=0., unexpected_requests=[])
    stream.send.side_effect = lambda *args: client.requests.append("send")
    stream.collect.return_value = (events + [(0.4, {"method": "turn/completed", "params": completed})], completed, None)
    usage = mock.Mock()
    usage.finish_turn.return_value = {}
    return stream, usage


RUNTIMES = {"claude": (bench_mcp_trial, claude_turn), "codex": (bench_mcp_codex, codex_turn)}


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.question, self.edit = live.tasks_for("default")[:2]

    def graded(self, rows, stopped=False, **client):
        client, record = Client(**client), {}
        made = answer_check.before(client, grading, self.question, ("default", 40), record)
        snapshot = client.execute(grading.snapshot_code())
        record.update(client.execute(grading.grade_code(self.question["id"], snapshot["digest"])))
        answer_check.after(client, grading, self.question, record, made, rows, stopped=stopped)
        return record, client.requests

    def state(self, record, status="completed"):
        return eval_grading.state(dict(record, kind="read_only", status=status), self.question)

    def test_a_task_without_an_answer_costs_no_request(self):
        client, record = Client(), {"host_check_passed": True}
        self.assertIsNone(answer_check.before(client, grading, self.edit, ("default", 40), record))
        answer_check.after(client, grading, self.edit, record, None, said("Done."))
        answer_check.undo(client, grading, None)
        self.assertEqual((client.requests, record), ([], {"host_check_passed": True}))

    def test_the_question_passes_only_on_the_right_answer_with_the_scene_unchanged(self):
        record, requests = self.graded(said(RIGHT))
        self.assertEqual(requests, ["setup", "snapshot", "grade", "restore"], "truth first, fixture undone last")
        self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["outcome"],
                          record["host_check"]["scene_unchanged"], record["harness_fixture"]),
                         (True, "right", True, ["fixture:probe_object"]))
        self.assertNotIn("harness_fixture_errors", record)
        self.assertEqual(self.state(record), "passed")
        for rows, outcome in ((said(WRONG), "wrong"), (said("I cannot see the scene."), "no_answer"),
                              (said(""), "empty"), ([], "empty")):
            record, requests = self.graded(rows)
            self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["outcome"], requests[-1]),
                             (False, outcome, "restore"))
            self.assertEqual(self.state(record), "failed")
        changed, _ = self.graded(said(RIGHT), unchanged=False)
        self.assertEqual((changed["host_check_passed"], changed["host_check"]["answer"]["outcome"]), (False, "right"))

    def test_a_turn_stopped_at_its_time_limit_never_passes(self):
        record, requests = self.graded(said(RIGHT), stopped=True)
        self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["outcome"],
                          record["host_check"]["answer"]["turn"], requests[-1]),
                         (False, "right", answer_check.STOPPED, "restore"))
        self.assertEqual(self.state(record, "timeout"), "failed")
        self.assertIs(self.graded(said(RIGHT), stopped=True, truth={})[0]["host_check_passed"], False)

    def test_without_ground_truth_the_run_is_ungraded_never_passed(self):
        for client, reason in (({"truth": {"ungraded": "the object the question names is missing"}}, "missing"),
                               ({"truth": {}}, "no ground truth"), ({"fail": ("setup",)}, "could not be computed")):
            with self.subTest(reason=reason):
                record, requests = self.graded(said(RIGHT), **client)
                self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["outcome"]),
                                 (None, "ungraded"))
                self.assertIn(reason, record["host_check"]["answer"]["reason"])
                self.assertEqual(requests[-1], "restore")
                self.assertEqual(self.state(record), "ungraded")
        record, _ = self.graded(said(RIGHT), truth={}, unchanged=False)
        self.assertIs(record["host_check_passed"], False, "a changed scene still fails")

    def test_a_fixture_that_was_not_undone_is_recorded_and_nothing_raises(self):
        record, requests = self.graded(said(RIGHT), fail=("restore",))
        self.assertEqual((record["host_check_passed"], record["harness_fixture_errors"], requests[-1]),
                         (True, ["the add-on did not answer"], "restore"))
        # Blender answers, but says it undid nothing: the next task would run on a scene the harness changed.
        record, _ = self.graded(said(RIGHT), restored=0)
        self.assertEqual(record["harness_fixture_errors"], ["0 of 1 harness fixtures were undone"])
        self.assertNotIn("harness_fixture_errors", self.graded(said(RIGHT), restored=1)[0])

    def test_a_defect_of_the_check_leaves_the_question_ungraded_and_the_fixture_undone(self):
        for broken in ({"truth": {"accept": None}}, {"truth": "not a truth"}):
            with self.subTest(**broken):
                record, requests = self.graded(said(RIGHT), **broken)
                self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["outcome"],
                                  requests[-1]), (None, "ungraded", "restore"))
        with mock.patch.object(answer_check.answers, "grade", side_effect=KeyError("form")):
            record, requests = self.graded(said(RIGHT))
            changed, _ = self.graded(said(RIGHT), unchanged=False)
        self.assertEqual((record["host_check_passed"], requests[-1], changed["host_check_passed"]),
                         (None, "restore", False))
        self.assertIn("the answer check failed (KeyError", record["host_check"]["answer"]["reason"])

    def test_the_whole_turn_is_read_as_the_angelo_side_reads_it(self):
        # The answer in an earlier message, a tool call, then a closing remark: both sides read the same rows.
        client = Client()
        stream, _ = claude_turn(client, RIGHT, "Nothing was changed.", tool_between=True)
        events, result, _ = stream.collect.return_value
        rows = answer_check.claude_rows(events, result)
        self.assertEqual(rows, said(RIGHT, "Nothing was changed."))
        angelo = eval_answers.grade(self.question["answer"], TRUTH["accept"],
                                    said(RIGHT) + [{"role": "activity", "tool": "x"}] + said("Nothing was changed."))
        other = eval_answers.grade(self.question["answer"], TRUTH["accept"], rows)
        self.assertEqual([(side["outcome"], side["lenient"]) for side in (angelo, other)],
                         [("right", ["text after the answer line"])] * 2)
        self.assertEqual(answer_check.claude_rows([], {"result": RIGHT}), said(RIGHT), "only a result line")
        self.assertEqual(answer_check.claude_rows([], None), [])
        long = "x" * 3000 + "\n" + RIGHT
        self.assertEqual(answer_check.claude_rows([(0.1, {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking."}]}})], {"result": long}), said("Looking.", long))
        codex, _ = codex_turn(client, "Let me look.", RIGHT)
        self.assertEqual(answer_check.codex_rows(codex.collect.return_value[0]), said("Let me look.", RIGHT))
        self.assertEqual(answer_check.codex_rows([]), [])


class RunTaskTests(unittest.TestCase):
    """Both runtimes' ``run_task``, executed: what they ask Blender for, in which order, around the prompt."""

    def run_task(self, runtime, task, *texts, config=None, **client):
        module, turn = RUNTIMES[runtime]
        client = Client(**client)
        stream, usage = turn(client, *texts)
        record = module.run_task(task, client, stream, usage, dict({"task_timeout": 60}, **(config or {})), 1)
        return record, client

    def test_the_truth_is_asked_before_the_digest_and_the_prompt_and_the_fixture_is_undone_last(self):
        question = live.tasks_for("default")[0]
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                record, client = self.run_task(runtime, question, "x" * 3000 + "\n" + RIGHT)
                self.assertEqual(client.requests, ["setup", "snapshot", "send", "grade", "restore"])
                self.assertEqual((record["status"], record["host_check_passed"],
                                  record["host_check"]["answer"]["outcome"]), ("completed", True, "right"))
                self.assertEqual(len(record["result"]["reply"]), 1500, "the kept reply is cut; the graded one is not")
                self.assertIn("json.loads('[{\"remove_object\": \"Brass Marker\"}]')", client.codes[-1],
                              "the restore is told what to undo")
                wrong, _ = self.run_task(runtime, question, WRONG)
                self.assertEqual((wrong["host_check_passed"], wrong["host_check"]["answer"]["outcome"]),
                                 (False, "wrong"))

    def test_the_question_s_own_task_set_is_the_one_blender_is_asked_about(self):
        cube = live.tasks_for("long", 7)[-1]
        truth = {"accept": [[1.0, 2.0, 1.0, -1.75, -1.5, 0.75]], "detail": {"object": "Eval Cube"}}
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                record, client = self.run_task(runtime, cube, "DIMENSIONS: 1 2 1; LOCATION: -1.75 -1.5 0.75",
                                               config={"task_set": "long", "long_tasks": 7}, truth=truth)
                self.assertIn("live.tasks_for('long', 7) if t['id'] == 'question_05'", client.codes[0])
                self.assertEqual((record["host_check_passed"], record["host_check"]["answer"]["parsed"]),
                                 (True, "exactly"))

    def test_a_skipped_question_undoes_its_fixture_and_sends_nothing(self):
        cube = live.tasks_for("long", 7)[-1]
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                record, client = self.run_task(runtime, cube, RIGHT, present=False)
                self.assertEqual((record["status"], client.requests), ("skipped", ["setup", "snapshot", "restore"]))

    def test_a_question_whose_turn_hit_the_time_limit_fails_whatever_it_answered(self):
        question = live.tasks_for("default")[0]
        for runtime, (module, turn) in RUNTIMES.items():
            with self.subTest(runtime=runtime):
                client = Client()
                stream, usage = turn(client, RIGHT)
                finished = stream.collect.return_value
                stream.collect.return_value = None
                stream.collect.side_effect = [([], None, "deadline"), finished]   # Stopped, then its last words.
                record = module.run_task(question, client, stream, usage, {"task_timeout": 60}, 1)
                self.assertEqual((record["status"], record["host_check_passed"],
                                  record["host_check"]["answer"]["outcome"], record["host_check"]["answer"]["turn"]),
                                 ("timeout", False, "right", answer_check.STOPPED))
                self.assertEqual(client.requests[-1], "restore")
                stream.interrupt.assert_called_once()

    def test_an_edit_asks_blender_for_nothing_more_than_before(self):
        edit = live.tasks_for("default")[1]
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                record, client = self.run_task(runtime, edit, "Done.", present=True)
                self.assertEqual(client.requests, ["snapshot", "send", "grade"])
                self.assertNotIn("answer", record["host_check"])

    def test_both_runtimes_record_the_gate_version_on_every_record(self):
        for module in (bench_mcp_trial, bench_mcp_codex):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertIn('record.update({"gate_version": version} if version else {})', source)
            self.assertIn('"gate_version": version', source)


class BlenderSideTests(unittest.TestCase):
    """``setup`` and ``restore`` as they run inside Blender, with the fixture and truth modules patched."""

    def tearDown(self):
        answer_check._RESTORE.clear()

    def test_fixtures_are_made_before_the_truth_and_undone_once(self):
        calls, task = [], live.tasks_for("default")[0]
        apply = lambda bpy, scene, fixture: (calls.append("fixture") or ["fixture:probe_object"],
                                             lambda: calls.append("undone"))
        truth = lambda bpy, scene, spec: calls.append("truth") or dict(TRUTH, spec=spec)
        with mock.patch.object(survey_fixtures, "apply", apply), mock.patch.object(survey_truth, "truth", truth):
            made = answer_check.setup(None, None, task)
            self.assertEqual((calls, made["steps"], made["undo"], made["truth"]["spec"]), (
                ["fixture", "truth"], ["fixture:probe_object"], UNDO, {"kind": "object_names", "type": "MESH"}))
            self.assertEqual((answer_check.restore(None, None, made["undo"]), calls[-1]), (1, "undone"))
            self.assertEqual(calls.count("undone"), 1)
            answer_check.setup(None, None, task)
            answer_check.setup(None, None, task)   # A question whose restore never came: nothing is left behind.
            self.assertEqual(calls.count("fixture") - calls.count("undone"), 1)

    def test_a_state_that_cannot_be_made_is_a_problem_not_a_truth(self):
        def apply(bpy, scene, fixture):
            raise survey_fixtures.FixtureError("an object named Brass Marker already exists")
        with mock.patch.object(survey_fixtures, "apply", apply):
            made = answer_check.setup(None, None, live.tasks_for("default")[0])
        self.assertIn("already exists", made["problem"])
        self.assertNotIn("truth", made)

    def test_blender_undoes_from_what_the_driver_was_told_when_its_own_record_is_gone(self):
        removed = []
        plate = SimpleNamespace(data=SimpleNamespace(users=0, name="Brass Marker"))
        cube = SimpleNamespace(location=[0.25, -1.5, 0.75])
        objects = {"Eval Cube": cube}
        bpy = SimpleNamespace(data=SimpleNamespace(
            objects=SimpleNamespace(get={"Brass Marker": plate}.get, remove=lambda obj: removed.append("object")),
            meshes=SimpleNamespace(remove=lambda mesh: removed.append("mesh"))))
        scene = SimpleNamespace(objects=dict(objects))
        undo = [{"remove_object": "Brass Marker"}, None, {"object": "Eval Cube", "location": [0.0, -1.0, 0.0]}]
        self.assertEqual(answer_check.restore(bpy, scene, undo), 2)
        self.assertEqual((removed, cube.location), (["object", "mesh"], [0.0, -1.0, 0.0]))
        gone = SimpleNamespace(data=SimpleNamespace(objects=SimpleNamespace(get=lambda name: None)))
        self.assertEqual(answer_check.restore(gone, SimpleNamespace(objects={}), undo[:1]), 0,
                         "nothing to undo is counted as nothing undone")
        # Its own record comes first, and then what the driver was told is not applied a second time.
        calls = []
        answer_check._RESTORE.append(lambda: calls.append("own"))
        self.assertEqual((answer_check.restore(bpy, scene, undo), calls), (1, ["own"]))

    def test_what_undoes_each_gate_fixture_is_told_to_the_driver(self):
        cube = SimpleNamespace(location=(1.0, 2.0, 3.0))
        scene = SimpleNamespace(objects={"Eval Cube": cube})
        opening, question = live.tasks_for("long", 7)[0], live.tasks_for("long", 7)[-1]
        self.assertEqual(answer_check._undo_data(scene, opening["fixtures"][0]),
                         {"remove_object": eval_gate.PROBE_MESH})
        self.assertEqual(answer_check._undo_data(scene, question["fixtures"][0]),
                         {"object": "Eval Cube", "location": [1.0, 2.0, 3.0]})
        self.assertIsNone(answer_check._undo_data(SimpleNamespace(objects={}), question["fixtures"][0]))
        self.assertIsNone(answer_check._undo_data(scene, {"kind": "scene_values", "values": {}}))

    def test_the_code_sent_to_blender_compiles_and_leaves_the_grading_code_as_it_was(self):
        for code in (grading.answer_setup_code("read_only_question"), grading.answer_restore_code(),
                     grading.answer_restore_code(UNDO), grading.answer_setup_code("question_05", "long", 40)):
            ast.parse(code)
            self.assertIn("    import bench_mcp_answers\n", code)
        self.assertIn("bench_mcp_answers.restore(bpy, _scene, _undo)", grading.answer_restore_code(UNDO))
        self.assertNotIn("bench_mcp_answers", grading.grade_code("read_only_question", []))
        self.assertNotIn("bench_mcp_answers", grading.snapshot_code())
        self.assertEqual(eval_gate.PROBE_MESH, "Brass Marker")


if __name__ == "__main__":
    unittest.main()
