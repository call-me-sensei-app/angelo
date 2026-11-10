# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every provider route waits for the host's answer, and a turn's end never contradicts the screen (1.0.8 B-007).

B-007: Sol's code-mode cell yielded while its script's approval card was up; after ~175 s of polls the model ended
its turn saying the build "is awaiting script approval", and the end revoked the card (Stopped, nothing ran). Each
route is pinned here against the host's own lifetimes (``tool_deadlines``), for every tool, never for one tool,
model or timing: Codex code mode (the runtime's hold list), the Codex plan route, Claude's plan and direct routes and
the MCP launcher for external apps. A turn that still ends with a waiting step leaves the host's notice under its
last words. Fake clocks throughout; nothing sleeps.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (claude_direct, codex_plan, external_mcp, providers, runtime_launch, session_rail,  # noqa: E402
                          steering, tool_deadlines, turn_end, worker_mirror_events)
from angelo_agent.controller import ToolDispatchBudget  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.tool_deadlines import PROVIDER_GRACE_SECONDS, TURN_ENDED, provider_seconds, tool_seconds  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, codex_tools  # noqa: E402
from test_native_tool_deadlines import Clock, RevocationFixture  # noqa: E402

NOTICE = "The turn ended while a step was still waiting, so it didn't run. Ask again if you still want it."


def declared_names(tools):
    """Every dynamic tool name a Codex thread declares, inside namespaces too."""
    for spec in tools:
        if spec.get("type") == "namespace":
            yield from declared_names(spec["tools"])
        else:
            yield spec["name"]


class CodexCodeModeTests(unittest.TestCase):
    def test_every_declared_tool_holds_its_cell_while_unanswered(self):
        """Any Angelo call can wait for the person (an approval, any edit in Ask mode), its scene or a job."""
        held = set(runtime_launch.HOLD_DYNAMIC_TOOLS)
        self.assertEqual(held, set(TOOL_MAP))
        for defer in (True, False):
            with self.subTest(defer=defer):
                self.assertLessEqual(set(declared_names(codex_tools(defer=defer))), held)

    def test_the_launch_passes_the_whole_list(self):
        with tempfile.TemporaryDirectory() as state:
            command, _env = runtime_launch.codex_command("codex", "codex_subscription", state)
        overrides = [value for flag, value in zip(command, command[1:]) if flag == "-c"]
        value = next(item.split("=", 1)[1] for item in overrides if item.startswith("code_mode_hold_dynamic_tools="))
        self.assertEqual(set(json.loads(value)), set(TOOL_MAP))
        # Every tool also runs one call at a time on a runtime that would otherwise run them in parallel
        # (agent_execution.rst, "Concurrent calls"): the host admits calls in the model's order, as on 0.163.
        serial = next(item.split("=", 1)[1] for item in overrides
                      if item.startswith(runtime_launch.SERIAL_TOOLS_KEY + "="))
        self.assertEqual(set(json.loads(serial)), set(TOOL_MAP))

    def test_one_hold_outlasts_every_first_lifetime(self):
        """Past the runtime's ceiling a `wait` holds again; no first lifetime needs it (a started job's may)."""
        self.assertGreater(runtime_launch.HOLD_CEILING_SECONDS, max(provider_seconds(name) for name in TOOL_MAP))


class CodexPlanRouteTests(unittest.TestCase):
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

        context = SimpleNamespace(request_tool=lambda call_id, tool, arguments, callback: calls.append(callback),
                                  tool_expiry=lambda call_id: expiry)
        with patch.object(codex_plan, "_current", return_value=True), \
                patch.object(codex_plan.threading, "Event", ScriptedEvent), \
                patch.object(tool_deadlines, "time", SimpleNamespace(monotonic=clock.monotonic)):
            try:
                return codex_plan._native_call(SimpleNamespace(), context, None, name, {}), clock.now
            except RpcError as error:
                return str(error), clock.now

    def test_a_step_still_being_approved_past_the_old_dispatch_bound_is_answered(self):
        for name in TOOL_MAP:
            with self.subTest(name=name):
                answer, at = self.native_call(name, answer_at=tool_seconds(name) - 5)
                self.assertEqual(answer, {"success": True})

    def test_an_unanswered_step_ends_only_after_the_hosts_expiry_and_its_grace(self):
        for name in ("angelo_execute_python", "angelo_scene_program", "angelo_studio_generate"):
            with self.subTest(name=name):
                message, at = self.native_call(name)
                self.assertEqual(message, "Native Blender approval/dispatch timed out")
                self.assertGreater(at, provider_seconds(name))

    def test_the_wait_follows_an_expiry_the_host_extended(self):
        answer, _ = self.native_call("angelo_flow_run", answer_at=2900, expiry=3000)
        self.assertEqual(answer, {"success": True})


class ChatWaitTests(unittest.TestCase):
    """Claude's plan and direct routes and external apps wait with the chat itself, which has no ``tool_expiry``:
    its pending call's live expiry still governs (break-it: they stopped at the first lifetime of a started job)."""

    def wait(self, wait_with, *, answer_at=None, expires=3000.0):
        clock, calls, pending = Clock(), [], {}

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

        def request_tool(call_id, tool, arguments, callback):
            calls.append(callback)
            pending["chat/" + call_id] = SimpleNamespace(expires=expires)   # A started job's longer bound.
        chat = SimpleNamespace(request_tool=request_tool, pending=pending, cancelled=False)
        with patch.object(providers.threading, "Event", ScriptedEvent), \
                patch.object(tool_deadlines, "time", SimpleNamespace(monotonic=clock.monotonic)):
            try:
                return wait_with(chat), clock.now
            except RpcError as error:
                return str(error), clock.now

    def claude(self, chat):
        provider = providers.ClaudeProvider(SimpleNamespace(cancelled=False), {})
        with patch.object(provider, "_current", return_value=True):
            return provider._native_call("angelo_workers_status", {}, owner=SimpleNamespace(controller=chat))

    def test_claudes_routes_wait_out_a_started_jobs_own_bound(self):
        answer, at = self.wait(self.claude, answer_at=2900)
        self.assertEqual(answer, {"success": True})
        self.assertGreaterEqual(at, 2900)
        message, at = self.wait(self.claude)
        self.assertGreater(at, 3000 + PROVIDER_GRACE_SECONDS)

    def test_the_chats_expiry_is_read_by_its_own_call_key(self):
        chat = SimpleNamespace(pending={"chat/a": SimpleNamespace(expires=10.0), "chat/ab": SimpleNamespace(expires=20.0)})
        self.assertEqual(tool_deadlines.call_expiry(chat, "ab"), 20.0)
        self.assertIsNone(tool_deadlines.call_expiry(chat, "b"))
        self.assertIsNone(tool_deadlines.call_expiry(SimpleNamespace(), "ab"))


class ClaudeDirectRouteTests(unittest.TestCase):
    def test_claude_code_never_times_out_or_backgrounds_a_call_the_host_still_holds(self):
        environment = claude_direct.SESSION_ENVIRONMENT
        self.assertGreater(int(environment["MCP_TOOL_TIMEOUT"]) / 1000, max(provider_seconds(name) for name in TOOL_MAP))
        self.assertEqual(environment["CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS"], "0")


class ExternalAppTests(unittest.TestCase):
    def waited(self, name):
        """How long the launcher waits on Angelo's "pending" before it tells the app it is still waiting."""
        clock = Clock()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        server = external_mcp.Server("fixture", directory.name)

        def request(request, wait):
            clock.now += wait
            return {"code": "pending"}
        with patch.object(server, "request", side_effect=request), \
                patch.object(server, "_cancelled_request", return_value=False), \
                patch.object(server, "_progress", return_value=None), \
                patch.object(external_mcp, "time", SimpleNamespace(monotonic=clock.monotonic)):
            result = server.call("identity", {"name": name, "arguments": {}})
        self.assertIn("Still waiting in Angelo", json.dumps(result))
        return clock.now

    def test_an_app_waits_at_least_as_long_as_angelo_holds_each_call(self):
        for name in ("angelo_execute_python", "angelo_studio_generate", "angelo_flow_run", "angelo_inspect_scene"):
            with self.subTest(name=name):
                self.assertGreaterEqual(self.waited(name), max(external_mcp.CALL_DEADLINE, provider_seconds(name)))


class TurnEndTests(RevocationFixture):
    def ended(self, *, answered_first=False, status="completed"):
        self.context.emit("message_complete", {"item": "reply", "text": "The build is awaiting script approval."})
        self.context.emit("finished", {"status": status})
        if answered_first:   # A UI tick answers the revoked call before the end is handled.
            self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.controller.pump_events()
        return self.controller.state["messages"]

    def notices(self, rows):
        return [row for row in rows if row.get("role") == "system" and row.get("text") == NOTICE]

    def test_a_turn_ending_on_an_unanswered_card_says_under_its_last_words_that_it_did_not_run(self):
        for answered_first in (False, True):
            with self.subTest(answered_first=answered_first):
                self.setUp()
                replies, call = self.awaiting_approval()
                rows = self.ended(answered_first=answered_first)
                self.assertEqual(len(self.notices(rows)), 1)
                self.assertEqual(rows[-1]["text"], NOTICE, "The host's account is the chat's last word")
                # Revoked by the end (answered on a tick) or cancelled when the end is handled: either way unrun.
                self.assertIn(replies[0]["error"], (TURN_ENDED, turn_end.ENDED))
                self.executor.execute.assert_not_called()

    def test_every_tool_waiting_at_its_card_gets_the_notice(self):
        cube = {"name": "Crate", "size": 1, "location": [0, 0, 0]}
        for name, arguments in (("angelo_execute_python", {"description": "Fixture", "code": "result = {}"}),
                                ("angelo_create_cube", cube)):
            with self.subTest(name=name):
                self.setUp()
                replies = []
                self.context.request_tool("w-" + name, name, arguments, replies.append)
                self.controller.pump_tools(self.executor, self.controller.document,
                                           budget=ToolDispatchBudget(seconds=60))
                waiting = [call for call in self.controller.pending.values() if call.approval]
                self.assertTrue(waiting and not replies, "Ask mode: the edit waits at its card")
                self.assertEqual(len(self.notices(self.ended())), 1)
                self.executor.execute.assert_not_called()

    def test_a_turn_with_nothing_waiting_adds_nothing(self):
        self.assertEqual(self.notices(self.ended()), [])

    def test_a_running_call_is_not_called_unrun(self):
        replies, call = self.awaiting_approval()
        call.approval, call.executing = False, True   # Approved and running when the turn ends.
        self.assertEqual(self.notices(self.ended()), [])

    def test_stop_keeps_its_own_account(self):
        replies, call = self.awaiting_approval()
        self.controller.stop()
        self.assertEqual(self.notices(self.ended(status="interrupted")), [])

    def test_the_notice_is_translatable_and_the_model_reads_what_happened(self):
        replies, call = self.awaiting_approval()
        row = self.notices(self.ended())[0]
        self.assertEqual(row["i18n"][0], NOTICE)
        self.assertEqual(row["model_text"], turn_end.MODEL_ACCOUNT)
        index = self.controller.state["messages"].index(row)
        synced = self.controller.state["runtime_sessions"][self.controller.context_key()]["synced_length"]
        self.assertLessEqual(synced, index, "Unsynced: the chat's next request carries it to its model")

    def test_the_turns_outcome_includes_the_notice_so_a_lead_reads_it(self):
        replies, call = self.awaiting_approval()
        self.controller.state["messages"].insert(0, {"role": "user", "text": "Build it."})
        rows = self.ended()
        self.assertEqual(self.controller.turn_outcome[3], len(rows))
        end, text = session_rail.turn_end(rows, outcome=self.controller.turn_outcome)
        self.assertEqual(end, "completed")
        self.assertIn("awaiting script approval", text)
        self.assertTrue(text.endswith(turn_end.MODEL_ACCOUNT), text)

    def test_a_dropped_end_never_lends_its_record_to_a_later_turn(self):
        replies, call = self.awaiting_approval()
        self.context.emit("finished", {"status": "completed"})   # Recorded under this request's fence...
        self.controller._epoch += 1                              # ...then a restore makes its event stale.
        self.controller.pump_events()
        self.controller.pump_tools(self.executor, self.controller.document, budget=ToolDispatchBudget(seconds=60))
        self.assertNotIn(call.id, self.controller.pending)
        self.controller._request_generation += 1
        self.controller.cancelled = False
        self.context = self.controller.runtime_context()
        self.assertEqual(self.notices(self.ended()), [], "Nothing waited in the later turn")

    def test_the_end_itself_says_a_step_never_ran_before_any_pump(self):
        """A hosted worker forwards ``finished`` to its lead at once, before its own pump adds the account row."""
        sent = []
        emit = self.controller.emit
        with patch.object(self.controller, "emit", side_effect=lambda kind, data=None: (sent.append((kind, data)),
                                                                                          emit(kind, data))[1]):
            replies, call = self.awaiting_approval()
            self.context.emit("finished", {"status": "completed"})
        self.assertIs(dict(sent)["finished"].get("ended_unrun"), True)
        self.setUp()
        with patch.object(self.controller, "emit", side_effect=lambda kind, data=None: (sent.append((kind, data)),
                                                                                          emit(kind, data))[1]):
            self.context.emit("finished", {"status": "completed"})
        self.assertNotIn("ended_unrun", sent[-1][1], "Nothing waited: no mark")

    def test_a_lead_reads_a_hosted_workers_account_before_its_row_arrives(self):
        rows = [{"role": "user", "text": "Build it."},
                {"role": "assistant", "text": "The build is awaiting script approval."}]
        mirror = SimpleNamespace(busy=True, pending={"x": None}, _request_metrics=None, _worker_request=None,
                                 cancelled=False, lock=threading.Lock(), state={"messages": rows},
                                 _thinking_messages={}, _thinking_times={}, _host_request=None, _turn_message="",
                                 thinking={}, status="", inbox=steering.Inbox(), version=0)   # A Controller's.
        worker_mirror_events.finish(mirror, {"status": "completed", "ended_unrun": True, "_epoch": 1, "_generation": 2})
        self.assertIs(mirror.ended_unrun, True)
        end, text = turn_end.lead_report(*session_rail.turn_end(rows, outcome=mirror.turn_outcome), mirror.ended_unrun)
        self.assertEqual(end, "completed")
        self.assertTrue(text.startswith("The build is awaiting script approval.") and text.endswith(turn_end.MODEL_ACCOUNT))
        rows.append({"role": "system", "turn_end": True, "text": NOTICE, "model_text": turn_end.MODEL_ACCOUNT})
        mirror.turn_outcome = mirror.turn_outcome[:3] + (len(rows),)   # A window's chat: the row is in the turn.
        _, again = turn_end.lead_report(*session_rail.turn_end(rows, outcome=mirror.turn_outcome), True)
        self.assertEqual(again.count(turn_end.MODEL_ACCOUNT), 1, "Said once")
        self.assertEqual(turn_end.lead_report("failed", "x", True), ("failed", "x"))

    def test_a_failed_turns_reason_is_never_the_account(self):
        rows = [{"role": "user", "text": "Build it."}, {"role": "system", "text": "The plan failed its check."},
                {"role": "system", "turn_end": True, "text": NOTICE, "model_text": turn_end.MODEL_ACCOUNT}]
        end, text = session_rail.turn_end(rows, outcome=("failed", False, False, len(rows)))
        self.assertEqual(end, "failed")
        self.assertIn("The plan failed its check.", text)

    def test_a_stop_that_arrived_for_a_hosted_chat_counts_as_stop(self):
        self.assertTrue(turn_end.stopped(SimpleNamespace(cancelled=False, stop_arrived=lambda: True)))
        self.assertFalse(turn_end.stopped(SimpleNamespace(cancelled=False, stop_arrived=lambda: False)))
        self.assertFalse(turn_end.stopped(SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
