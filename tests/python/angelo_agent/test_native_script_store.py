# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts by id: fetched again and rerun unchanged or patched (script_store), on the real Controller.

Recorded runs (friction scan G07) lost a script's text between calls and re-sent whole scripts for one-line fixes.
These pin the id, where the text comes from (this conversation's own transcript rows, never a collaborator's or
another chat's), the patch rules, that the host builds the whole code before validation, the invocation digest
and the approval card (so review and consent cover exactly what runs), the refusals that leave nothing pending,
the read-only fetch, and the Claude plan path. Blender stand-ins; no provider or profile.
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import script_store  # noqa: E402
from angelo_agent.approvals import approval_details  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.script_after import notes  # noqa: E402
from angelo_agent.script_store import ScriptStoreError, expand, patched, script_id  # noqa: E402
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, validate_call  # noqa: E402

BUILD = "base = 41\nscene.value = base\nresult = {'value': scene.value}\n"
FIXED = "base = 42\nscene.value = base\nresult = {'value': scene.value}\n"


def row(code, description="Build", **extra):
    return dict({"role": "activity", "call_id": "c" + script_id(code), "text": "", "tool": "angelo_execute_python",
                 "arguments": {"description": description, "code": code}, "status": "completed"}, **extra)


class Rules(unittest.TestCase):
    def test_the_id_is_the_texts_own_digest(self):
        self.assertRegex(script_id(BUILD), script_store.SCRIPT_ID)
        self.assertEqual(script_id(BUILD), script_id(str(BUILD)))
        self.assertNotEqual(script_id(BUILD), script_id(FIXED))

    def test_edits_apply_in_order_and_each_find_must_occur_once(self):
        self.assertEqual(patched(BUILD, [{"find": "base = 41", "replace": "base = 42"}]), FIXED)
        self.assertEqual(patched(BUILD, []), BUILD, "no edits reruns the base unchanged")
        # The second edit sees the first's result: '42' exists only after it.
        self.assertEqual(patched(BUILD, [{"find": "41", "replace": "42"}, {"find": "= 42", "replace": "= 43"}]),
                         BUILD.replace("41", "43"))
        for edits, needle in (([{"find": "scene.value", "replace": "x"}], "edits[0].find occurs 2 times"),
                              ([{"find": "= 7", "replace": "x"}], "edits[0].find occurs 0 times"),
                              ([{"find": "", "replace": "x"}], "edits[0] needs exactly find"),
                              ([{"find": "41"}], "edits[0] needs exactly find"),
                              ([{"find": "41", "replace": "4", "also": 1}], "edits[0] needs exactly find")):
            with self.subTest(edits=edits), self.assertRaisesRegex(ScriptStoreError, re.escape(needle)):
                patched(BUILD, edits)

    def test_an_overlapping_second_occurrence_counts(self):
        """Regression (F2): str.count missed overlapping matches, so the patch went to the first of two."""
        with self.assertRaisesRegex(ScriptStoreError, re.escape("edits[0].find occurs 2 times")):
            patched("total = f(g(h()))\n", [{"find": "))", "replace": ")) + 1"}])
        self.assertEqual(script_store.occurrences("aaaa", "aa"), 2)
        self.assertEqual(script_store.occurrences("abc", "abc"), 1)

    def test_the_call_must_be_built_before_validation(self):
        with self.assertRaisesRegex(ValueError, "needs code, or base"):
            validate_call("angelo_execute_python", {"description": "d", "base": script_id(BUILD), "edits": []})
        with self.assertRaisesRegex(ValueError, "needs code, or base"):
            validate_call("angelo_execute_python", {"description": "d"})
        self.assertEqual(validate_call("angelo_script", {"script": script_id(BUILD)}), {"script": script_id(BUILD)})
        for arguments in ({"script": "s123"}, {"script": script_id(BUILD), "more": 1}, {}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_call("angelo_script", arguments)

    def test_collaborators_never_read_this_conversations_scripts(self):
        self.assertNotIn("angelo_script", COLLABORATOR_TOOLS)

    def test_a_finished_script_result_names_its_id(self):
        window = SimpleNamespace(scene=SimpleNamespace(world=None, render=None))
        self.assertEqual(notes(window, {"success": True}, BUILD, None)["script"], script_id(BUILD))
        self.assertEqual(notes(window, {"error": "KeyError"}, BUILD, None)["script"], script_id(BUILD))


class Store(unittest.TestCase):
    def setUp(self):
        self.controller = SimpleNamespace(lock=__import__("threading").Lock(), state={"messages": [
            {"role": "user", "text": "build"}, row(BUILD),
            row("result = 1\n", collaborator={"name": "Other"}),
            {"role": "activity", "tool": "angelo_scene_program", "arguments": {"code": "result = 2\n"}}]})

    def test_a_base_from_this_conversation_is_built_into_code(self):
        built = expand(self.controller, "angelo_execute_python", {"description": "Fix", "base": script_id(BUILD),
                                                                  "edits": [{"find": "base = 41", "replace": "base = 42"}]})
        self.assertEqual(built, {"description": "Fix", "code": FIXED})
        self.assertEqual(expand(self.controller, "angelo_execute_python", {"description": "d", "code": "x = 1"}),
                         {"description": "d", "code": "x = 1"})
        self.assertEqual(expand(self.controller, "angelo_view", {"base": "anything"}), {"base": "anything"})

    def test_what_is_never_a_base(self):
        cases = [({"base": script_id("result = 1\n")}, "not a script this conversation sent"),  # A collaborator's.
                 ({"base": script_id("result = 2\n")}, "not a script this conversation sent"),  # Not a script row.
                 ({"base": "s00000000"}, "not a script this conversation sent"),
                 ({"base": script_id(BUILD), "code": BUILD}, "either code or base"),
                 ({"edits": []}, "edits apply to a base"),
                 ({"base": script_id(BUILD), "edits": [{}] * (script_store.MAX_EDITS + 1)}, "at most"),
                 ({"base": "S" + script_id(BUILD)[1:]}, "eight hex digits")]
        for arguments, needle in cases:
            with self.subTest(arguments=str(arguments)[:60]), self.assertRaisesRegex(ScriptStoreError, needle):
                expand(self.controller, "angelo_execute_python", dict({"description": "d"}, **arguments))

    def test_a_row_redacted_of_the_key_no_longer_matches_its_id(self):
        secret = "import os\nKEY = 'sk-live-123'\n"
        self.controller.state["messages"].append(row(secret.replace("sk-live-123", "[REDACTED]")))
        with self.assertRaisesRegex(ScriptStoreError, "held the connection's key"):
            expand(self.controller, "angelo_execute_python", {"description": "d", "base": script_id(secret)})

    def test_a_pending_script_that_held_the_key_is_never_a_base(self):
        secret = "KEY = 'sk-live-123'\nresult = 1\n"
        self.controller.api_key = "sk-live-123"
        self.controller.redact = lambda text: str(text).replace("sk-live-123", "[REDACTED]")
        self.controller.pending = {"c/1": SimpleNamespace(name="angelo_execute_python", parent_call=None,
                                                          arguments={"description": "d", "code": secret})}
        with self.assertRaisesRegex(ScriptStoreError, "held the connection's key"):
            expand(self.controller, "angelo_execute_python", {"description": "d", "base": script_id(secret)})
        self.controller.pending["c/1"].arguments = {"description": "d", "code": FIXED}
        self.assertEqual(script_store.find(self.controller, script_id(FIXED))[1], FIXED, "a pending call is read")

    def test_a_patch_never_builds_more_than_a_script_holds(self):
        with self.assertRaisesRegex(ScriptStoreError, re.escape("edits[0] makes the script longer than 32,768")):
            patched(BUILD, [{"find": "base = 41", "replace": "x" * 40000}])

    def test_two_texts_under_one_id_are_refused(self):
        self.controller.state["messages"].append(row(FIXED))
        original = script_store.script_id
        try:
            script_store.script_id = lambda code: "s0000000a"
            with self.assertRaisesRegex(ScriptStoreError, "two different scripts"):
                script_store.find(self.controller, "s0000000a")
        finally:
            script_store.script_id = original

    def test_the_fetch_returns_the_newest_rows_text(self):
        self.controller.state["messages"].append(row(BUILD, description="Build again"))
        result = script_store.read(self.controller, {"script": script_id(BUILD)})
        self.assertEqual((result["description"], result["code"], result["lines"]), ("Build again", BUILD, 4))
        self.assertTrue(result["read_only"] and not result["scene_changed"])


class OnTheController(unittest.TestCase):
    """The admission path: the built code is what the card shows, the digest binds and the executor runs."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = Controller(self.directory.name)
        self.controller.busy = True
        self.controller.set_document(("isolated",))
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executed = []

        def execute(name, arguments):
            self.executed.append((name, dict(arguments)))
            namespace = {"scene": SimpleNamespace(value=0), "result": None}
            exec(compile(arguments["code"], "<angelo-reviewed-script>", "exec"), namespace)
            return {"success": True, "result": namespace["result"]}
        self.executor = SimpleNamespace(precondition=Mock(return_value="current"), execute=execute)

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def pump(self):
        self.controller.pump_tools(self.executor, self.controller.document,
                                   apply_mutation=lambda call: self.controller.execute_tool(
                                       call, self.executor, self.controller.document),
                                   budget=ToolDispatchBudget(seconds=60))

    def run_script(self, identity, arguments):
        results = []
        self.controller.request_tool(identity, "angelo_execute_python", arguments, results.append)
        self.controller.pump_events()  # Its transcript row, as the main thread's timer adds it.
        call = next((c for c in self.controller.pending.values() if c.id.endswith("/" + identity)), None)
        if call is None:
            return None, results
        self.pump()
        self.controller.review_script(call)
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.controller.pump_events()
        return call, results

    def test_a_patch_is_reviewed_and_run_as_its_whole_code(self):
        self.run_script("first", {"description": "Build", "code": BUILD})
        call, results = self.run_script("second", {"description": "Fix the value", "base": script_id(BUILD),
                                                   "edits": [{"find": "base = 41", "replace": "base = 42"}]})
        self.assertEqual(call.arguments, {"description": "Fix the value", "code": FIXED})
        self.assertIn("base = 42", approval_details("angelo_execute_python", call.arguments))
        self.assertEqual(self.executed[-1][1]["code"], FIXED)
        self.assertEqual(results[0]["result"], {"value": 42})
        row = next(m for m in self.controller.state["messages"] if m.get("call_id", "").endswith("second"))
        self.assertEqual(row["arguments"]["code"], FIXED, "the transcript keeps the code that ran, the next base")

    def test_a_changed_patch_after_review_never_runs(self):
        self.run_script("first", {"description": "Build", "code": BUILD})
        results = []
        self.controller.request_tool("second", "angelo_execute_python", {
            "description": "Fix", "base": script_id(BUILD), "edits": [{"find": "base = 41", "replace": "base = 42"}]},
            results.append)
        call = next(c for c in self.controller.pending.values() if c.id.endswith("/second"))
        self.pump()
        self.controller.review_script(call)
        call.arguments["code"] = BUILD.replace("41", "666")  # Anything other than what was reviewed.
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.assertNotIn(BUILD.replace("41", "666"), [arguments["code"] for _, arguments in self.executed])

    def test_a_patch_delivered_again_is_a_replay_even_after_its_base_was_redacted(self):
        """Regression (F3): building the call again must not depend on the transcript as it is now."""
        self.run_script("first", {"description": "Build", "code": BUILD})
        patch_call = {"description": "Fix", "base": script_id(BUILD), "edits": [{"find": "base = 41", "replace": "base = 42"}]}
        self.run_script("second", patch_call)
        runs = len(self.executed)
        from angelo_agent import activity_rows
        activity_rows.bind_key(self.controller, "41")  # The base row now reads "[REDACTED]": its id no longer matches.
        results = []
        self.controller.request_tool("second", "angelo_execute_python", patch_call, results.append)
        self.assertTrue(results and results[0].get("replayed"), results)
        self.assertEqual(len(self.executed), runs, "nothing ran again")

    def test_a_refused_patch_leaves_nothing_pending_or_run(self):
        call, results = self.run_script("bad", {"description": "Fix", "base": "s00000000", "edits": []})
        self.assertIsNone(call)
        self.assertEqual(results[0]["error_code"], "VALIDATION")
        self.assertIn("not a script this conversation sent", results[0]["error"])
        self.assertEqual(self.executed, [])

    def test_the_fetch_tool_reads_this_conversations_transcript(self):
        self.run_script("first", {"description": "Build", "code": BUILD})
        results = []
        self.controller.request_tool("fetch", "angelo_script", {"script": script_id(BUILD)}, results.append)
        self.pump()
        self.assertEqual((results[0]["code"], results[0]["description"]), (BUILD, "Build"))
        self.assertEqual([name for name, _ in self.executed], ["angelo_execute_python"], "never the executor")

    def test_a_claude_plan_runs_the_built_code(self):
        from angelo_agent.providers import _plan_problems
        self.run_script("first", {"description": "Build", "code": BUILD})
        operation = {"tool": "angelo_execute_python", "arguments": {
            "description": "Fix", "base": script_id(BUILD), "edits": [{"find": "base = 41", "replace": "base = 42"}]}}
        problems, resolved = _plan_problems([operation], None, self.controller.resolve_call)
        self.assertEqual((problems, resolved[0]["arguments"]["code"]), ([], FIXED))
        bad = dict(operation, arguments=dict(operation["arguments"], edits=[{"find": "scene.value", "replace": ""}]))
        problems, _ = _plan_problems([bad], None, self.controller.resolve_call)
        self.assertIn("edits[0].find occurs 2 times", problems[0])


class PlanProgressRow(unittest.TestCase):
    def test_a_patch_names_its_step_and_its_edits_are_never_read_as_steps(self):
        import json
        from angelo_agent.plan_progress import PlanProgress
        edit = {"find": "ops = []", "replace": 'ops = [{"kind": "create_primitive", "name": "Fake"}]'}
        plan = {"operations": [{"tool": "angelo_execute_python", "arguments": {
            "description": "Fix the rail height", "base": script_id(BUILD), "edits": [edit, edit]}}]}
        text = json.dumps(plan)
        for size in (1, 7, 300):
            with self.subTest(chunk=size):
                progress = PlanProgress()
                for start in range(0, len(text), size):
                    progress.feed(text[start:start + size])
                self.assertEqual((progress.operations, progress.names), (0, ["Fix the rail height"]))


if __name__ == "__main__":
    unittest.main()
