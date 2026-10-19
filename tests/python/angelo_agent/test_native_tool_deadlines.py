# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approval and plan timeouts agree, and a call approved after its turn ended is revoked and never runs.

The Claude plan used to wait a flat 190 s for any native call while the host gave a reviewed Python script
600 s, so a person still reading the code ended the whole turn at 190 s. The wait now follows the host's own
expiry. Time is a fake clock throughout; nothing sleeps.
"""

import ast
import inspect
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import providers, tool_deadlines
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.protocol import RpcError
from angelo_agent.tool_deadlines import (APPROVAL_SECONDS, MEDIA_SECONDS, PROVIDER_GRACE_SECONDS, PYTHON_REVIEW_SECONDS,
                                         TURN_ENDED, tool_seconds)
from angelo_agent.tool_specs import TOOL_MAP
from test_native_dispatch_fixes import ui_class

PYTHON = {"description": "Fixture", "code": "result = {}"}


def controller_expiry_methods():
    """Inspect the concrete method selected by Controller's production MRO."""
    methods = {}
    for name in ("_execute_tool", "approve_tool", "_commit_native_job", "_tool_available"):
        owner = next(cls for cls in Controller.__mro__ if name in cls.__dict__)
        methods[name] = ast.parse(textwrap.dedent(inspect.getsource(owner.__dict__[name]))).body[0]
    return methods


def assert_expiry_checks(case, methods):
    calls = lambda name: {node.func.attr for node in ast.walk(methods[name])
                          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    for name in ("_execute_tool", "approve_tool", "_commit_native_job"):
        with case.subTest(method=name):
            case.assertIn("_tool_available", calls(name))
    case.assertIn("expire_tool", calls("_tool_available"))
    first = methods["_tool_available"].body[0]
    case.assertIn("expire_tool", ast.dump(first))


class Clock:
    """Seconds since the call was made; waiting on the event is what advances it."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


class WaitFixture(unittest.TestCase):
    """ClaudeProvider._native_call against a host stub that answers when the fake clock reaches ``answer_at``."""

    def native_call(self, name, *, answer_at=None, expiry=None):
        clock, calls = Clock(), []

        class ScriptedEvent:
            def __init__(self):
                self.flag = False

            def set(self):
                self.flag = True

            def wait(self, timeout=None):
                clock.now += timeout
                if answer_at is not None and clock.now >= answer_at and calls and not self.flag:
                    calls[0]({"success": True})
                return self.flag

        controller = SimpleNamespace(request_tool=lambda call_id, tool, arguments, callback: calls.append(callback),
                                     tool_expiry=lambda call_id: expiry)
        provider = providers.ClaudeProvider(SimpleNamespace(cancelled=False), {})
        owner = SimpleNamespace(controller=controller)
        with patch.object(provider, "_current", return_value=True), \
                patch.object(providers.threading, "Event", ScriptedEvent), \
                patch.object(tool_deadlines, "time", SimpleNamespace(monotonic=clock.monotonic)):
            try:
                return provider._native_call(name, {}, owner=owner), clock.now
            except RpcError as error:
                return str(error), clock.now


class WaitTests(WaitFixture):
    def test_the_deadline_table_is_the_hosts_lifetimes(self):
        self.assertEqual(tool_seconds("angelo_execute_python"), PYTHON_REVIEW_SECONDS)
        self.assertEqual(tool_seconds("angelo_inspect_scene"), APPROVAL_SECONDS)
        self.assertEqual(tool_seconds("angelo_studio_generate"), MEDIA_SECONDS)
        self.assertEqual(tool_seconds("angelo_studio_enhance"), APPROVAL_SECONDS)
        self.assertEqual(tool_seconds("angelo_flow_run"), MEDIA_SECONDS)
        self.assertEqual({tool_seconds(name) for name in TOOL_MAP}, {APPROVAL_SECONDS, PYTHON_REVIEW_SECONDS, MEDIA_SECONDS})

    def test_the_host_gives_each_call_exactly_the_tables_lifetime(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(directory.name)
        self.addCleanup(controller.close)
        controller.busy = True
        controller.set_document(("scratch",))
        for index, (name, arguments) in enumerate((("angelo_execute_python", PYTHON), ("angelo_inspect_scene", {}))):
            controller.request_tool(f"call-{index}", name, arguments, lambda result: None)
            call = controller.pending[controller.state["id"] + f"/call-{index}"]
            self.assertAlmostEqual(call.expires - call.queued_at, tool_seconds(name), delta=1)

    def test_a_script_still_being_reviewed_after_190_seconds_does_not_end_the_turn(self):
        answer, at = self.native_call("angelo_execute_python", answer_at=420)
        self.assertEqual(answer, {"success": True})
        self.assertGreaterEqual(at, 420)

    def test_an_unanswered_script_ends_the_wait_only_after_the_hosts_expiry_and_a_grace(self):
        message, at = self.native_call("angelo_execute_python")
        self.assertEqual(message, "Native Blender approval/dispatch timed out")
        self.assertGreater(at, PYTHON_REVIEW_SECONDS + PROVIDER_GRACE_SECONDS)
        self.assertLess(at, PYTHON_REVIEW_SECONDS + PROVIDER_GRACE_SECONDS + 5)

    def test_other_calls_keep_their_shorter_life(self):
        message, at = self.native_call("angelo_inspect_scene")
        self.assertEqual(message, "Native Blender approval/dispatch timed out")
        self.assertLess(APPROVAL_SECONDS + PROVIDER_GRACE_SECONDS, at)
        self.assertLess(at, APPROVAL_SECONDS + PROVIDER_GRACE_SECONDS + 5)

    def test_the_wait_follows_an_expiry_the_host_extended(self):
        """A started media job or Flow run replaces its call's expiry with its own, longer bound."""
        answer, at = self.native_call("angelo_flow_run", answer_at=2900, expiry=3000)
        self.assertEqual(answer, {"success": True})
        message, at = self.native_call("angelo_flow_run", expiry=3000)
        self.assertGreater(at, 3000 + PROVIDER_GRACE_SECONDS)
        self.assertLess(at, 3000 + PROVIDER_GRACE_SECONDS + 5)

    def test_the_provider_never_waits_less_than_the_host_keeps_a_call(self):
        for name in TOOL_MAP:
            with self.subTest(name=name):
                message, at = self.native_call(name)
                self.assertGreater(at, tool_seconds(name))


class RevocationFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.addCleanup(self.controller.close)
        self.controller.busy = True
        self.controller.set_document(("scratch",))
        self.controller.state["options"]["approval"] = "ASK"
        self.controller.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        self.executor = SimpleNamespace(precondition=Mock(return_value="unchanged"), execute=Mock(return_value={"success": True}))
        self.context = self.controller.runtime_context()

    def awaiting_approval(self, name="angelo_execute_python", arguments=PYTHON, identity="script"):
        replies = []
        self.context.request_tool(identity, name, arguments, replies.append)
        call = next(call for key, call in self.controller.pending.items() if key.endswith("/" + identity))
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.assertTrue(call.approval and not call.revoked)
        self.controller.review_script(call)  # Human read the exact code while this turn owned it.
        return replies, call

    def approve_operator(self, call):
        bpy = SimpleNamespace(ops=SimpleNamespace(angelo=SimpleNamespace(review_tool=Mock())))
        operator = ui_class("ANGELO_OT_approve", {"get_controller": lambda: self.controller, "bpy": bpy})()
        operator.call_id, operator.session_id, operator.report = call.id, "", Mock()
        context = SimpleNamespace(preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=True)))
        return operator.execute(context), operator


class RevocationTests(RevocationFixture):
    def test_an_approval_while_the_turn_is_live_runs(self):
        """The control: the same call, approved in time, executes, so the revocation tests below mean something."""
        replies, call = self.awaiting_approval()
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.execute.assert_called_once()
        self.assertEqual(replies, [dict(replies[0], success=True)])

    def test_a_call_approved_after_its_turn_ended_never_runs(self):
        replies, call = self.awaiting_approval()
        self.context.emit("finished", {"status": "failed"})  # The worker's last act: the UI has not handled it yet.
        self.assertIn(call.id, self.controller.pending)  # The UI thread has not seen the event...
        self.assertEqual(call.revoked, TURN_ENDED)  # ...but the call is already revoked.
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.execute.assert_not_called()
        self.assertEqual((replies[0]["error"], replies[0]["revoked"]), (TURN_ENDED, True))
        self.assertNotIn("expired", replies[0])
        self.assertNotIn(call.id, self.controller.pending)

    def test_the_approve_button_after_the_turn_ended_runs_nothing(self):
        replies, call = self.awaiting_approval()
        self.context.emit("finished", {"status": "failed"})
        outcome, operator = self.approve_operator(call)
        self.assertEqual(outcome, {"CANCELLED"})
        self.executor.execute.assert_not_called()
        self.assertEqual(replies[0]["error"], TURN_ENDED)
        operator.report.assert_called_once()

    def test_every_other_route_to_execution_is_closed_too(self):
        for route in ("execute_tool", "_execute_tool", "pump"):
            with self.subTest(route=route):
                replies, call = self.awaiting_approval(identity="route-" + route)
                self.context.emit("finished", {"status": "interrupted"})
                if route == "pump":
                    self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
                else:
                    getattr(self.controller, route)(call, self.executor, self.controller.document)
                self.executor.execute.assert_not_called()
                self.assertEqual(replies[0]["error"], TURN_ENDED)
                ended = self.context
                self.controller._begin_metrics()  # Each route gets a new request, never a reopened ended scope.
                self.context = self.controller.runtime_context()
                self.assertIsNot(self.context, ended)
                self.assertTrue(ended.program_completions.closed)

    def test_the_legacy_apply_operator_after_the_turn_ended_runs_nothing(self):
        replies, call = self.awaiting_approval()
        self.context.emit("finished", {"status": "failed"})
        bpy = SimpleNamespace(ops=SimpleNamespace())
        operator = ui_class("ANGELO_OT_apply_approved_tool", {"get_controller": lambda: self.controller, "bpy": bpy})()
        operator.call_id, operator.session_id, operator.report = call.id, "", Mock()
        context = SimpleNamespace(preferences=SimpleNamespace(edit=SimpleNamespace(use_global_undo=True)))
        self.assertEqual(operator.execute(context), {"CANCELLED"})
        self.executor.execute.assert_not_called()
        self.assertEqual(replies[0]["error"], TURN_ENDED)

    def test_every_place_execution_can_start_asks_the_expiry_check_first(self):
        """The structure the revocation relies on: these all reach _tool_available, whose first question is expire_tool."""
        assert_expiry_checks(self, controller_expiry_methods())

    def test_expiry_structure_rejects_missing_and_late_checks(self):
        for body in ("return True", "ready = True\n    return self.expire_tool(call)"):
            with self.subTest(body=body):
                methods = controller_expiry_methods()
                methods["_tool_available"] = ast.parse(
                    "def _tool_available(self, call, document):\n    " + body).body[0]
                with self.assertRaises(AssertionError):
                    assert_expiry_checks(self, methods)

    def test_the_finished_event_afterwards_answers_nothing_twice(self):
        replies, call = self.awaiting_approval()
        self.context.emit("finished", {"status": "failed"})
        self.controller.pump_events()  # cancel_tools meets a call the revocation already answered, or answers it once.
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(len(replies), 1)
        self.executor.execute.assert_not_called()
        self.assertFalse(self.controller.busy)

    def test_a_running_call_is_not_interrupted(self):
        replies, call = self.awaiting_approval()
        call.executing = True
        self.context.emit("finished", {"status": "failed"})
        self.assertEqual(call.revoked, "")

    def test_only_the_ended_requests_calls_are_revoked(self):
        old_replies, old = self.awaiting_approval(identity="old")
        self.controller._request_generation += 1  # A newer request of the same chat.
        newer = self.controller.runtime_context()
        self.assertIsNot(newer, self.context)
        new_replies = []
        newer.request_tool("new", "angelo_execute_python", PYTHON, new_replies.append)
        new = next(call for key, call in self.controller.pending.items() if key.endswith("/new"))
        self.context.emit("finished", {"status": "interrupted"})
        self.assertEqual((old.revoked, new.revoked), (TURN_ENDED, ""))

    def test_a_worker_that_ends_the_turn_revokes_through_the_real_run_path(self):
        """Controller._run ends every path with context.emit('finished'); a provider that returns leaves nothing approvable."""
        admitted = []

        class Provider:
            close = stop = staticmethod(lambda: None)

            def run_request(provider, context, native_id, text, observation=None):
                context.request_tool("late", "angelo_execute_python", PYTHON, admitted.append)
                return "failed"  # The turn ends with the script still waiting for the person.

        self.controller.provider = Provider()
        self.controller.cancelled = False
        with patch.object(self.controller, "_connect_runtime", return_value="native"), \
                patch.object(self.controller, "_context_blocks", return_value="text"):
            self.controller._run("do it")
        call = next(call for key, call in self.controller.pending.items() if key.endswith("/late"))
        self.assertEqual(call.revoked, TURN_ENDED)
        call.approval = True
        self.controller.execute_tool(call, self.executor, self.controller.document)
        self.executor.execute.assert_not_called()
        self.assertEqual(admitted[0]["error"], TURN_ENDED)


if __name__ == "__main__":
    unittest.main()
