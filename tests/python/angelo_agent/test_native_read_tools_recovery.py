# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live read tools under the rules of failed plan steps and host-enforced lengths. No Blender.

Toolset 35 changed two things the read tools meet in a turn
(doc/angelo/agent_integration.rst, "Failed plan steps" and "Toolset 35"):

* A lookup's result is data and only its ``error`` is a failure. The read
  tools answer a wrong guess with ``found`` or ``readable`` false and a code,
  which is data: the rest of the plan runs and the model reads the answer. A
  tool error (the reader itself failed) is a failed lookup: it goes back to the
  model in the same turn, later lookups still run and later edits do not. A
  decision (the document changed, the turn stopped) still ends the turn.
* The Claude plan schema carries no ``maxLength``. ``validate_call`` enforces
  the three string lengths of the read tools before anything runs, in one line
  that names the limit and the length it got.

The real providers and controller run over the scripted stand-ins of
read_tools_stand_ins.py; tool results are canned.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import plan_recovery  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.data_path import MAX_PATH_CHARS  # noqa: E402
from angelo_agent.read_spec import MAX_FILE_CHARS, MAX_LIST_CHARS  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, native_plan_schema, validate_call  # noqa: E402
from read_tools_stand_ins import ClaudeStandIn, CodexStandIn, final, plan  # noqa: E402
from test_native_read_tools_delivery import FILE, OUTSIDE, READ, SOCKETS, SUMMARY, UNKNOWN, canned  # noqa: E402
from test_native_round_savings import committed, program  # noqa: E402

PROGRAM = "angelo_scene_program"
GUESS, SOCKET_PATH, BROKEN, CHANGED = ("scene.cycles.feature_set", 'data.node_groups["Rock"].nodes[0].inputs',
                                       "scene.broken", "scene.changed")
READER_FAILED = "The reader failed: RuntimeError"
DOCUMENT_CHANGED = "Document changed or turn stopped"
EDIT = (PROGRAM, program()["arguments"])
LIMITS = ((READ, "path", MAX_PATH_CHARS), (FILE, "file", MAX_FILE_CHARS), (FILE, "list", MAX_LIST_CHARS))


def execute(name, arguments):
    """Canned host results: the reader's answers, one read that fails, one the document changed under, one edit."""
    if name == READ and arguments["path"] == BROKEN:
        raise RuntimeError(READER_FAILED)  # The stand-in answers it as the controller does: an error result.
    if name == READ and arguments["path"] == CHANGED:
        return {"error": DOCUMENT_CHANGED}
    if name == PROGRAM:
        return committed()
    return canned(name, arguments)


def too_long(field, limit):
    return "%s is printable text of at most %d characters (this one has %d)" % (field, limit, limit + 1)


class ClaudeCase(unittest.TestCase):
    def setUp(self):
        self.claude = ClaudeStandIn(execute)
        self.addCleanup(self.claude.close)

    def tools_run(self):
        return [call[0] for call in self.claude.dispatcher.calls]

    def round(self, index):
        """``(first line, results envelope)`` of the prompt the model reads in round ``index``."""
        head, body = self.claude.prompts[index].split("\n", 1)
        return head, json.loads(body)["result"]


class AnswersAreDataTests(ClaudeCase):
    """``found`` or ``readable`` false with a code is an answer: it never stops a plan."""

    def test_both_tools_are_lookups_and_no_answer_is_a_lookup_problem(self):
        self.assertTrue(plan_recovery.is_lookup(READ) and plan_recovery.is_lookup(FILE))
        for answer in (SOCKETS, UNKNOWN, SUMMARY, OUTSIDE, canned(READ, {"path": "scene.objects.get('Cube')"})):
            self.assertIsNone(plan_recovery.lookup_problem(answer), answer)

    def test_a_wrong_guess_does_not_stop_the_plan_and_the_model_reads_the_answer(self):
        status = self.claude.run(
            "Read the feature set and that file, then add the cube.",
            plan((READ, {"path": GUESS}), (FILE, {"file": "/etc/passwd"}), EDIT, verify=True),
            plan(reply="scene.cycles has no feature_set; its settings are samples and device."))
        self.assertEqual(status, "completed")
        self.assertEqual(self.tools_run()[:3], [READ, FILE, PROGRAM], "The edit after the two answers ran")
        head, envelope = self.round(1)
        self.assertTrue(head.startswith("Host confirms these native operations completed"), head)
        self.assertIs(envelope["success"], True, "An answer with a code is not a failed step")
        results = envelope["operations"]
        self.assertEqual([result.get("code") for result in results[:2]], ["UNKNOWN_MEMBER", "OUTSIDE_WORKSPACE"])
        self.assertEqual((results[0]["members"], results[2]["status"]), (["samples", "device"], "committed"))
        self.assertFalse([row for row in self.claude.system_rows() if "stopped" in row or "rejected" in row])


class ToolErrorTests(ClaudeCase):
    """A read that failed is a failed lookup: back to the model in the same turn, with what else ran."""

    def test_a_tool_error_returns_to_the_model_later_lookups_run_and_later_edits_do_not(self):
        status = self.claude.run(
            "Read the settings and the file, then add the cube.",
            plan((READ, {"path": BROKEN}), (FILE, {}), EDIT, (READ, {"path": SOCKET_PATH})),
            plan(reply="The read failed, so nothing was added. Planted Orphan is unused."))
        self.assertEqual(status, "completed", "A failed read does not end the turn")
        self.assertEqual(self.tools_run()[:2], [READ, FILE], "The lookup after the failed one still ran")
        self.assertNotIn(PROGRAM, self.tools_run(), "The edit after a failed lookup did not run")
        head, envelope = self.round(1)
        self.assertIn("operations[0] (%s) failed: %s." % (READ, READER_FAILED), head)
        self.assertIn("A failed lookup changes nothing.", head)
        self.assertIn("operations[2..3] did not run.", head)
        self.assertIs(envelope["success"], False, "A plan with a failed lookup is not reported as a success")
        self.assertEqual([bool(result.get("error")) for result in envelope["operations"]], [True, False])
        self.assertEqual(envelope["operations"][1]["unused"]["items"], SUMMARY["unused"]["items"])
        self.assertNotIn(READER_FAILED, "\n".join(self.claude.system_rows()), "No terminal row: the turn went on")

    def test_a_document_change_under_a_read_still_ends_the_turn(self):
        status = self.claude.run("Read the settings.", plan((READ, {"path": CHANGED}), (FILE, {})))
        self.assertEqual(status, "interrupted")
        self.assertEqual((self.tools_run(), len(self.claude.prompts)), ([READ], 1), "Nothing else ran; no second round")
        self.assertEqual(self.claude.system_rows()[-1], DOCUMENT_CHANGED)


class LengthLimitTests(ClaudeCase):
    """The plan schema carries no lengths, so the host refuses an overlong text in one line before anything runs."""

    def test_the_claude_plan_has_no_length_and_the_declarations_keep_theirs(self):
        self.assertNotIn('"maxLength"', json.dumps(native_plan_schema()))
        for tool, field, limit in LIMITS:
            self.assertEqual(TOOL_MAP[tool]["inputSchema"]["properties"][field]["maxLength"], limit)

    def test_validate_call_names_the_limit_and_the_length_it_got(self):
        for tool, field, limit in LIMITS:
            with self.subTest(tool=tool, field=field):
                self.assertEqual(validate_call(tool, {field: "a" * limit})[field], "a" * limit)
                with self.assertRaises(ValueError) as refused:
                    validate_call(tool, {field: "a" * (limit + 1)})
                message = str(refused.exception)
                self.assertEqual(message, too_long(field, limit))
                self.assertNotIn("\n", message)
                self.assertLess(len(message), 100)

    def test_an_overlong_path_in_a_claude_plan_is_corrected_before_anything_runs(self):
        status = self.claude.run(
            "What are the sockets?",
            plan((READ, {"path": "scene." + "a" * (MAX_PATH_CHARS - 5)}), (READ, {"path": SOCKET_PATH})),
            plan((READ, {"path": SOCKET_PATH})),
            plan(reply="The socket is Planted_Scale_7."))
        self.assertEqual(status, "completed")
        self.assertEqual(self.tools_run()[0], READ)
        self.assertEqual([call[1]["path"] for call in self.claude.dispatcher.calls if call[0] == READ], [SOCKET_PATH],
                         "Nothing of the refused plan ran, its valid read included")
        refusal = "operations[0] (%s): %s" % (READ, too_long("path", MAX_PATH_CHARS))
        self.assertTrue(self.claude.prompts[1].startswith("The host rejected your last plan before running any"))
        self.assertEqual(self.claude.prompts[1].split("\n")[1:], [refusal], "One line, and the path is not echoed")
        self.assertTrue(any(refusal in row for row in self.claude.system_rows()))
        self.assertIn("Planted_Scale_7", self.claude.prompts[2])


class CodexTests(unittest.TestCase):
    """A Codex thread reads an error as a tool answer inside its turn; the controller validates every call."""

    def test_a_tool_error_is_a_failed_tool_call_the_model_reads_and_the_turn_goes_on(self):
        codex = CodexStandIn(execute)
        self.addCleanup(codex.close)
        status = codex.run("What are the sockets?", [
            ("tool", READ, {"path": BROKEN}), ("tool", READ, {"path": GUESS}), ("tool", READ, {"path": SOCKET_PATH}),
            final("The socket is Planted_Scale_7.")])
        self.assertEqual(status, "completed")
        answers = codex.answers()
        self.assertEqual([success for success, _answer in answers], [False, True, True],
                         "Only the error is a failed tool call; a wrong guess is an answer")
        self.assertEqual(answers[0][1]["error"], READER_FAILED)
        self.assertEqual((answers[1][1]["found"], answers[1][1]["code"]), (False, "UNKNOWN_MEMBER"))
        self.assertEqual(answers[2][1]["items"][0]["identifier"], "Planted_Scale_7")

    def test_the_controller_refuses_an_overlong_text_in_one_line_and_runs_nothing(self):
        # Every dynamic tool call (Codex, voice, external) is validated here before it is queued.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        controller = Controller(folder.name)
        self.addCleanup(controller.close)
        controller.set_document(("instance", "scratch.blend", "Scene"))
        controller.busy = True
        ran = SimpleNamespace(execute=lambda name, arguments: self.fail("A refused call ran"),
                              precondition=lambda name, arguments: self.fail("A refused call was bound"))
        for tool, field, limit in LIMITS:
            with self.subTest(tool=tool, field=field):
                answers = []
                controller.request_tool("long-" + field, tool, {field: "a" * (limit + 1)}, answers.append)
                controller.pump_tools(ran, controller.document, budget=ToolDispatchBudget(seconds=60))
                (answer,) = answers
                self.assertEqual(answer["error"], too_long(field, limit))
                self.assertEqual(answer["error_code"], "VALIDATION")
                # A refused call to a deferred tool also gets that tool's exact schema back, as any does.
                self.assertEqual(set(answer), {"error", "error_code"} | ({"exact_schemas"} if tool == FILE else set()))
                self.assertFalse(controller.pending)


if __name__ == "__main__":
    unittest.main()
