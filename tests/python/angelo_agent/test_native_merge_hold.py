# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's own merge waits while that chat has another scene edit bound and not yet run (``merge_hold``).

Recorded (C3 early town-centre diagnostic): the lead's background merge loop merged a worker's part while the lead's
own script waited between its approval request and its run; the script was refused STALE_REVISION and the hold that
followed refused every later merge. Pinned here, through the real Controller pump and execution path with a fake
executor whose scene revision moves when a merge runs: the merge waits and runs after the script, whichever way each
was approved; the same holds for other bound edits (a typed transform, a staged program), never across chats, and
never between two merges. No Blender.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import controller as controller_module, worker_spec  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402

MERGE = worker_spec.PREFIX + "merge"
SCRIPT = {"description": "Replace a part", "code": "result = {}"}
MERGED = {"success": True, "scene_changed": True, "merged": [{"worker": 1, "collection": "Part", "objects": 3,
                                                                 "placements": 1, "renamed": {}}],
          "changes": {"added": [{"name": "Part body", "type": "MESH"}], "removed": [], "modified": []}}


class Scene:
    """A fake executor: its revision is what every bound edit's approval request captures; a merge moves it."""

    def __init__(self):
        self.revision, self.ran = 0, []

    def precondition(self, name, arguments):
        return ("scene", self.revision) if name != MERGE else ("merge", tuple(p["worker"] for p in arguments["parts"]))

    def execute(self, name, arguments):
        self.ran.append(name)
        if name == MERGE:
            self.revision += 1
            return dict(MERGED)
        return {"success": True, "scene_changed": True}


class MergeHoldFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tools = patch.dict(controller_module.TOOL_MAP, {tool["name"]: tool for tool in worker_spec.WORKER_TOOLS})
        tools.start()
        self.addCleanup(tools.stop)
        validate = controller_module.validate_call
        checker = patch("angelo_agent.controller.validate_call", side_effect=lambda name, args, **kw: (
            worker_spec.validate_worker(name, args) if name in worker_spec.WORKER_NAMES else validate(name, args, **kw)))
        checker.start()
        self.addCleanup(checker.stop)
        self.directory = directory.name
        self.c = self.chat()
        self.scene = Scene()

    def chat(self, mode="AUTO_BOUNDED"):
        c = Controller(self.directory, session_id=str(uuid.uuid4()))   # Each chat its own file (session_guard).
        self.addCleanup(c.close)
        c.busy = True
        c.set_document(("scratch",))
        c.state["options"]["approval"] = mode
        c.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        return c

    def request(self, identity, name, arguments, c=None):
        c = c or self.c
        replies = []
        c.request_tool(identity, name, arguments, replies.append)
        return replies, c.pending[c.state["id"] + "/" + identity]

    def pump(self, c=None, times=3):
        c = c or self.c
        for _ in range(times):
            c.pump_tools(self.scene, c.document, budget=ToolDispatchBudget(seconds=60),
                         apply_mutation=lambda call: c.execute_tool(call, self.scene, c.document))

    def approve(self, call, c=None):
        """The person's click on the card: the review, then the execution the Undo operator runs."""
        c = c or self.c
        if call.name == "angelo_execute_python":
            c.review_script(call)
        c.execute_tool(call, self.scene, c.document)


class OwnMergeWaitsTests(MergeHoldFixture):
    def test_an_auto_merge_waits_for_the_chats_own_script_and_runs_after_it(self):
        script_replies, script = self.request("script", "angelo_execute_python", SCRIPT)
        self.pump()
        self.assertTrue(script.approval and script.precondition is not None)
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]})
        self.pump()
        self.assertEqual(self.scene.ran, [], "the merge changed the scene under the chat's own bound script")
        self.assertFalse(merge_replies)
        self.approve(script)
        self.assertNotEqual(script_replies[0].get("error_code"), "STALE_REVISION", script_replies)
        self.assertNotIn("recovery_required", script_replies[0])
        self.pump()
        self.assertEqual(self.scene.ran, ["angelo_execute_python", MERGE])
        self.assertTrue(merge_replies and merge_replies[0].get("success"), merge_replies)
        self.assertIsNone(self.c.recovery_barrier.pending)

    def test_a_merge_the_person_approved_waits_and_runs_once_the_script_is_declined(self):
        c = self.chat("ASK")
        _, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c)
        self.approve(merge, c)   # Clicked first: nothing runs, the call stays pending and approved.
        self.assertEqual(self.scene.ran, [])
        self.assertTrue(merge.merge_waits and merge.id in c.pending and not merge_replies)
        self.pump(c)
        self.assertEqual(self.scene.ran, [], "still waiting while the script is bound")
        c.decline_tool(script.id)
        self.pump(c)
        self.assertEqual(self.scene.ran, [MERGE])
        self.assertTrue(merge_replies[0].get("success"), merge_replies)
        self.assertFalse(merge.automatic, "a person's approval stays the person's")

    def test_other_bound_edits_hold_a_merge_too(self):
        # Held out from the recorded script case: a typed transform awaiting its card holds the merge the same way.
        c = self.chat("ASK")
        replies, edit = self.request("move", "angelo_set_transform", {"object": "Cube", "location": [1, 2, 3]}, c)
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c)
        self.approve(merge, c)
        self.assertEqual(self.scene.ran, [])
        self.approve(edit, c)
        self.assertNotEqual(replies[0].get("error_code"), "STALE_REVISION", replies)
        self.pump(c)
        self.assertEqual(self.scene.ran, ["angelo_set_transform", MERGE])

    def test_merges_never_wait_for_each_other_nor_for_another_chat(self):
        other = self.chat()
        self.request("script", "angelo_execute_python", SCRIPT, other)
        self.pump(other)
        first, _ = self.request("merge-1", MERGE, {"parts": [{"worker": 1}]})
        second, _ = self.request("merge-2", MERGE, {"parts": [{"worker": 2}]})
        self.pump(times=4)
        self.assertEqual(self.scene.ran, [MERGE, MERGE])
        self.assertTrue(first[0].get("success") and second[0].get("success"))

    def test_a_merge_that_waits_past_its_own_deadline_says_why_and_merges_nothing(self):
        script_replies, script = self.request("script", "angelo_execute_python", SCRIPT)
        self.pump()
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]})
        self.pump()
        merge.expires = 0   # The script is still under review when the merge's own lifetime ends.
        self.pump()
        self.assertEqual(merge_replies[0]["error_code"], "EXPIRED")
        self.assertIn("waited for another scene edit of this chat", merge_replies[0]["error"])
        self.approve(script)
        self.assertEqual(self.scene.ran, ["angelo_execute_python"])
        self.assertNotIn("error_code", script_replies[0])
        again, _ = self.request("merge-again", MERGE, {"parts": [{"worker": 1}]})   # Nothing of the part was used.
        self.pump(times=1)
        self.assertEqual(self.scene.ran, ["angelo_execute_python", MERGE])
        self.assertTrue(again[0].get("success"))

    def test_a_merge_with_nothing_bound_runs_at_once(self):
        replies, _ = self.request("merge", MERGE, {"parts": [{"worker": 1}]})
        self.pump(times=1)
        self.assertEqual(self.scene.ran, [MERGE])
        self.assertTrue(replies[0].get("success"))


class BlockerTests(unittest.TestCase):
    """The predicate itself, over every call state a pending edit can be in."""

    def call(self, name="angelo_execute_python", **state):
        values = {"precondition": ("scene", 0), "executing": False, "committing": False, "mutation_started": False,
                  "merge_waits": False, "arguments": SCRIPT}
        values.update(state)
        return SimpleNamespace(name=name, **values)

    def blocker(self, *others):
        from angelo_agent import merge_hold
        merge = self.call(MERGE, arguments={"parts": [{"worker": 1}]})
        controller = SimpleNamespace(pending={str(index): call for index, call in enumerate((merge,) + others)})
        with patch("angelo_agent.request_recovery.scene_mutation",
                   side_effect=lambda name, args=None: name not in {"angelo_inspect_scene"}):
            return merge_hold.blocker(controller, merge)

    def test_bound_work_holds_a_merge_until_its_live_change_begins(self):
        self.assertIsNotNone(self.blocker(self.call()))
        # MH2-M-3: staging sets executing before its live commit; a prepared commit sets committing first.
        for state in ({"executing": True}, {"committing": True}, {"executing": True, "committing": True}):
            with self.subTest(state=state):
                self.assertIsNotNone(self.blocker(self.call("angelo_scene_program", arguments={}, **state)))
        for state in ({"precondition": None}, {"mutation_started": True}):
            with self.subTest(state=state):
                self.assertIsNone(self.blocker(self.call(**state)))
        self.assertIsNone(self.blocker(self.call("angelo_inspect_scene")))
        self.assertIsNone(self.blocker(self.call(MERGE)))
        self.assertIsNone(self.blocker())


class StagedProgramTests(unittest.TestCase):
    """A real staged native program (``ProgramJob`` over the shared fake backend) holds its chat's merge from its
    approval through every staging poll to its commit (break-it MH2-M-3)."""

    def setUp(self):
        from agent_runtime_fixtures import AgentRuntimeFixture

        class Runtime(AgentRuntimeFixture, unittest.TestCase):
            def runTest(self):
                pass
        self.runtime = Runtime()
        self.runtime.setUp()
        self.addCleanup(self.runtime.tearDown)
        tools = patch.dict(controller_module.TOOL_MAP, {tool["name"]: tool for tool in worker_spec.WORKER_TOOLS})
        tools.start()
        self.addCleanup(tools.stop)
        validate = controller_module.validate_call
        checker = patch("angelo_agent.controller.validate_call", side_effect=lambda name, args, **kw: (
            worker_spec.validate_worker(name, args) if name in worker_spec.WORKER_NAMES else validate(name, args, **kw)))
        checker.start()
        self.addCleanup(checker.stop)
        executor = self.runtime.executor
        executor.execute = lambda name, arguments: (executor.direct.append(name), dict(MERGED))[1]

    def test_a_merge_waits_through_staging_and_runs_after_the_commit(self):
        runtime, c = self.runtime, self.runtime.c
        program_replies, program = runtime.enqueue()
        runtime.pump(c, commit=False)
        self.assertTrue(program.staging)
        merge_replies = []
        c.request_tool("merge", MERGE, {"parts": [{"worker": 1}]}, merge_replies.append)
        job = runtime.ready(c)   # Every staging poll until it is ready to commit, merges pumped between them.
        self.assertTrue(job.ready_to_commit)
        for _ in range(3):   # Staged and not yet committed: the pump has room for other work.
            runtime.pump(c, commit=False)
        self.assertNotIn(MERGE, runtime.executor.direct, "the merge landed between the program's staging polls")
        for _ in range(10):
            runtime.pump(c)
            if merge_replies:
                break
        self.assertEqual(runtime.executor.backend().commits, 1)
        self.assertTrue(program_replies and not program_replies[0].get("error"), program_replies)
        self.assertEqual(runtime.executor.direct, [MERGE])
        self.assertTrue(merge_replies[0].get("success"), merge_replies)


class OperatorFixture(MergeHoldFixture):
    """Through the real approval and Undo operators (ui.py's own classes): a waiting merge pushes no Undo step, and
    one admitted to run pushes its step, whichever route reached it (break-it MH-M-3: a second click after the blocker
    was gone ran the merge, but a stale wait mark made the Undo operator cancel its step)."""

    def operators(self, c):
        from test_native_dispatch_fixes import ui_class
        from angelo_agent import script_errors
        from angelo_agent.tool_specs import STUDIO_NAMES
        outcomes = []
        names = {"__name__": "angelo_agent.ui", "__package__": "angelo_agent", "get_controller": lambda: c, "_controllers": {c.state["id"]: c}, "main_window": lambda: None,
                 "BlenderExecutor": lambda window: self.scene, "document_key": lambda: c.document,
                 "STUDIO_NAMES": STUDIO_NAMES, "script_errors": script_errors,
                 "document_scene": SimpleNamespace(refused=lambda operator, controller: False)}
        apply_class = ui_class("ANGELO_OT_apply_approved_tool", dict(names))

        def apply(_mode, _undo, call_id, session_id):
            operator = apply_class()
            operator.call_id, operator.session_id, operator.report = call_id, session_id, Mock()
            outcome = operator.execute(self.context)
            outcomes.append(outcome)   # FINISHED: Blender pushes this operator's Undo step; CANCELLED: none.
            return outcome
        bpy = SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(apply_approved_tool=apply, review_tool=Mock())),
                              context=SimpleNamespace(temp_override=lambda **_: __import__("contextlib").nullcontext()))
        approve_class = ui_class("ANGELO_OT_approve", dict(names, bpy=bpy))

        def click(call):
            operator = approve_class()
            operator.call_id, operator.session_id, operator.report = call.id, c.state["id"], Mock()
            return operator.execute(self.context)
        return click, outcomes

    def setUp(self):
        super().setUp()
        self.context = SimpleNamespace(preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=True)))



class ApprovalOperatorTests(OperatorFixture):
    def test_a_second_click_after_the_blocker_is_gone_runs_the_merge_with_its_undo_step(self):
        c = self.chat("ASK")
        click, outcomes = self.operators(c)
        _, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        merge_replies, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c, times=1)
        click(merge)
        self.assertEqual((outcomes, self.scene.ran), ([{'CANCELLED'}], []), "waiting: nothing ran, no Undo step")
        c.decline_tool(script.id)
        click(merge)   # Before the pump resumes it.
        self.assertEqual(self.scene.ran, [MERGE])
        self.assertEqual(outcomes[-1], {'FINISHED'}, "the merge ran without its Undo step")
        self.assertFalse(merge.merge_waits)
        self.assertTrue(merge_replies[0].get("success"))
        self.pump(c)
        self.assertEqual(self.scene.ran, [MERGE], "the pump never runs it twice")

    def test_the_pump_resumes_a_waiting_merge_through_the_operators_with_its_undo_step(self):
        c = self.chat("ASK")
        click, outcomes = self.operators(c)
        _, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        _, merge = self.request("merge", MERGE, {"parts": [{"worker": 1}]}, c)
        self.pump(c, times=1)
        click(merge)
        c.decline_tool(script.id)
        c.pump_tools(self.scene, c.document, budget=ToolDispatchBudget(seconds=60), apply_mutation=click)
        self.assertEqual((self.scene.ran, outcomes), ([MERGE], [{'CANCELLED'}, {'FINISHED'}]))


if __name__ == "__main__":
    unittest.main()
