# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script that raised: where it failed and how often a request may correct one.

No model, process or Blender: the scripts run in this interpreter under the
executor's file name. What the host undoes is in test_native_script_rollback.py.
"""

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import script_errors
from angelo_agent.script_errors import SCRIPT_CORRECTIONS, ScriptBudget


def raised(code):
    """The error a script raises when it runs as the executor runs it."""
    try:
        exec(compile(code, script_errors.SCRIPT_FILE, "exec"), {"__name__": "__angelo_reviewed_script__"})
    except Exception as error:
        return error
    raise AssertionError("The script did not raise")


class LocationTests(unittest.TestCase):
    """The model used to get ``str(error)`` and nothing else."""

    def test_an_error_names_its_type_its_line_and_the_source_of_that_line(self):
        code = "import math\n\ndef ring(count):\n    step = math.sin.tau / count\n    return step\n\nradius = 2\nring(8)\n"
        failure = script_errors.describe(raised(code), code)
        self.assertEqual(failure["error"],
                         "AttributeError: 'builtin_function_or_method' object has no attribute 'tau' (line 4)")
        self.assertEqual((failure["line"], failure["source"]), (4, "step = math.sin.tau / count"))
        self.assertEqual(failure["traceback"], ["line 8, in <module>: ring(8)", "line 4, in ring: step = math.sin.tau / count"])

    def test_a_key_error_is_more_than_a_quoted_name(self):
        code = "inputs = {'Size': 1}\ninputs['Scale'] = inputs['Scale'] * 2\n"
        failure = script_errors.describe(raised(code), code)
        self.assertEqual(failure["error"], "KeyError: 'Scale' (line 2)")
        self.assertEqual(failure["source"], "inputs['Scale'] = inputs['Scale'] * 2")

    def test_only_the_scripts_own_frames_are_listed(self):
        code = "import json\n\ndef load(text):\n    return json.loads(text)\n\nload('{')\n"
        failure = script_errors.describe(raised(code), code)
        self.assertEqual(failure["line"], 4, "The script's own call into the library, not the library's line")
        self.assertEqual(failure["traceback"], ["line 6, in <module>: load('{')", "line 4, in load: return json.loads(text)"])
        self.assertNotIn("decoder.py", json.dumps(failure))
        self.assertNotIn(str(Path(json.__file__).parent), json.dumps(failure))

    def test_an_error_from_outside_the_script_has_no_location(self):
        self.assertEqual(script_errors.describe(ValueError("no 3D viewport"), "result = 1"), {"error": "ValueError: no 3D viewport"})
        self.assertEqual(script_errors.describe(RuntimeError(), "result = 1"), {"error": "RuntimeError"})

    def test_every_part_is_bounded(self):
        code = "def down(depth):\n    return down(depth + 1)\n\ndown(0)  # " + "x" * 400 + "\n"
        failure = script_errors.describe(raised(code), code)
        self.assertEqual(len(failure["traceback"]), script_errors.MAX_FRAMES)
        self.assertEqual(failure["traceback"][-1], "line 2, in down: return down(depth + 1)")
        long = script_errors.describe(raised("raise ValueError('y' * 5000)"), "raise ValueError('y' * 5000)")
        self.assertLessEqual(len(long["error"]), script_errors.MAX_ERROR)
        self.assertTrue(long["error"].endswith("(line 1)"), "The line survives the cut")
        wide = "value = [" + "1, " * 200 + "][500]\n"
        self.assertLessEqual(len(script_errors.describe(raised(wide), wide)["source"]), script_errors.MAX_SOURCE)


class ReadBackTests(unittest.TestCase):
    """A result over the limit used to come back as ``{"truncated": true}`` with the data gone."""

    def test_a_result_within_the_limit_is_returned_as_data(self):
        value = {"object": "Island", "vertices": 4226, "bounds": [[-22.0, -15.0, -19.85], [22.0, 15.0, 2.4]]}
        self.assertEqual(script_errors.read_back(value), {"result": value})
        self.assertEqual(script_errors.read_back(None), {"result": None})

    def test_a_result_over_the_limit_keeps_its_head_and_says_what_was_cut(self):
        value = {"pines": [{"name": "Pine %04d" % index, "height": 4.5} for index in range(900)]}
        text = json.dumps(value, allow_nan=False, ensure_ascii=False)
        self.assertGreater(len(text), script_errors.RESULT_LIMIT)
        kept = script_errors.read_back(value)["result"]
        self.assertIs(kept["truncated"], True)
        head = kept["head"]
        self.assertEqual(head, text[:len(head)])
        self.assertIn("Pine 0000", head)
        self.assertGreater(len(head), script_errors.RESULT_LIMIT // 2, "Most of the budget is data")
        self.assertEqual((kept["characters"], kept["omitted_characters"]), (len(text), len(text) - len(head)))
        self.assertIn("first %d" % len(head), kept["note"])
        self.assertLessEqual(len(json.dumps(kept, ensure_ascii=False)), script_errors.RESULT_LIMIT)

    def test_a_result_that_is_not_json_does_not_turn_a_finished_script_into_a_failed_one(self):
        for value in ({"location": object()}, {"ratio": float("nan")}, {1, 2}):
            with self.subTest(value=value):
                kept = script_errors.read_back(value)
                self.assertIsNone(kept["result"])
                self.assertIn("ran to its end", kept["result_error"])


def failure(main_data_restored=True):
    return {"error": "KeyError: 'Scale' (line 2)", "script_error": True, "rolled_back": False,
            "main_data_restored": main_data_restored, "partial_changes_possible": True}


class BudgetTests(unittest.TestCase):
    """Both runtimes dispatch a script through one request context, which counts the ones that raised."""

    def setUp(self):
        self.rows, self.budget = [], ScriptBudget()

    def run_script(self, result):
        answers = []
        receive = self.budget.admit(lambda kind, data: self.rows.append((kind, data)), answers.append)
        if receive is not None:
            receive(result)
        return receive is not None, answers[0]

    def test_a_script_that_ran_is_passed_on_untouched(self):
        result = {"success": True, "result": 1}
        self.assertEqual(self.run_script(result), (True, result))
        self.assertEqual(self.run_script({"error": "The user declined this operation. Do not repeat it."})[1]["error"][:8], "The user")
        self.assertEqual((self.rows, self.budget.raised), ([], 0))

    def test_the_person_reads_what_happened_to_the_scene(self):
        self.run_script(failure())
        self.run_script(failure(main_data_restored=False))
        self.assertEqual([data["text"] for _, data in self.rows], [
            "The script stopped with an error. Blender data was restored, but image pixels and external effects were not "
            "verified:\nKeyError: 'Scale' (line 2)",
            "The script stopped with an error. Some changes may remain; inspect the scene before continuing:\n"
            "KeyError: 'Scale' (line 2)"])
        self.assertTrue(all(kind == "message" and data["role"] == "system" and data["i18n"] for kind, data in self.rows))

    def test_a_request_corrects_a_bounded_number_of_scripts_then_runs_no_more(self):
        for _ in range(SCRIPT_CORRECTIONS):
            ran, answer = self.run_script(failure())
            self.assertTrue(ran)
            self.assertNotIn("script_corrections_used", answer, "The model may correct this one")
        ran, answer = self.run_script(failure())
        self.assertTrue(ran, "The script after the last correction still runs")
        self.assertIs(answer["script_corrections_used"], True)
        self.assertEqual(self.rows[-1][1]["text"], "This request's script corrections are used up; nothing more was tried.")
        rows = len(self.rows)
        ran, answer = self.run_script({"success": True})
        self.assertFalse(ran, "No further script is dispatched in this request")
        self.assertEqual((answer["scene_changed"], answer["partial_changes_possible"]), (False, False))
        self.assertIn("%d script corrections" % SCRIPT_CORRECTIONS, answer["error"])
        self.assertEqual(len(self.rows), rows, "Said once")

    def test_scripts_that_succeed_do_not_spend_the_budget(self):
        for _ in range(SCRIPT_CORRECTIONS * 3):
            self.assertTrue(self.run_script({"success": True})[0])
        self.assertEqual(self.budget.raised, 0)


if __name__ == "__main__":
    unittest.main()
