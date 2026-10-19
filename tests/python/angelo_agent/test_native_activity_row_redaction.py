# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A tool call's row never holds the connection's key (activity_rows); Stop says what it stopped (stop_notice).

Dependency-free: the controller's own rows, saves and handoffs with a made-up
key; no Blender, provider process or model. Before activity_rows, a key in a
call's input (a script that sets it, a plan's operations) or in a result the
runtime wrote was stored in the call's row as given, so it was saved, handed
off to a later context and drawn when the row was opened. The call itself still
runs with exactly what it was given.
"""

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.claude_stream import RoundStream  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402
from angelo_agent.stop_notice import stop_notice_for  # noqa: E402

KEY = "sk-test-fake-0123456789"  # Not a credential: a made-up key; no credential is read.
SCRIPT = {"description": "Use the key", "code": 'client = Client(api_key="%s")\nprint("ok")' % KEY}
PLAN = {"operations": [{"tool": "angelo_scene_search", "arguments": {"query": KEY}}], "reply": "Searching."}


def leaks(text):
    """Every run of five characters of the key in ``text``."""
    return sorted({KEY[start:start + 5] for start in range(len(KEY) - 4)} & {
        text[start:start + 5] for start in range(len(text) - 4)})


class ChatFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.controller = self.open()

    def tearDown(self):
        self.controller.close()
        self.directory.cleanup()

    def open(self):
        controller = Controller(self.directory.name)
        controller.set_document(("row-redaction", 1))
        controller.busy = True
        controller.state["options"]["approval"] = "AUTO_BOUNDED"
        return controller

    def rows(self):
        self.controller.pump_events()
        return [message for message in self.controller.state["messages"] if message["role"] == "activity"]

    def saved(self):
        self.controller.save()
        return self.controller.store.path.read_text(encoding="utf-8")

    def assert_nowhere(self):
        """Not in a row (what opening it draws), the save, or a handoff built from the rows."""
        self.assertEqual(leaks(json.dumps(self.rows(), ensure_ascii=False)), [], "Rows hold no key")
        self.assertEqual(leaks(self.saved()), [], "The save holds no key")
        self.assertEqual(leaks(handoff(self.controller.state["messages"])[0]), [], "The handoff holds no key")


class RowRedactionTests(ChatFixture, unittest.TestCase):
    def test_a_script_calls_row_holds_no_key_and_the_call_runs_what_it_was_given(self):
        self.controller.api_key = KEY
        self.controller.request_tool("script", "angelo_execute_python", dict(SCRIPT), [].append)
        (row,) = self.rows()
        self.assertIn('api_key="[REDACTED]"', row["arguments"]["code"], "The opened row shows where the key was")
        (call,) = self.controller.pending.values()
        self.assertEqual(call.arguments, SCRIPT, "The call keeps exactly what it was given")
        self.assert_nowhere()
        self.controller.cancel_tools("The user cancelled this turn; no operation was applied")
        (row,) = self.rows()
        self.assertEqual((row["status"], row["result"]["error_code"]), ("failed", "CANCELLED"))
        self.assert_nowhere()

    def test_a_claude_plan_calls_row_and_the_runtimes_verdict_hold_no_key(self):
        self.controller.api_key = KEY
        stream, plans = RoundStream(), {}
        for event in ({"type": "assistant", "message": {"content": [
                          {"type": "tool_use", "name": "StructuredOutput", "id": "t0", "input": PLAN}]}},
                      {"type": "user", "message": {"content": [
                          {"type": "tool_result", "tool_use_id": "t0", "content": "Echoed " + KEY}]}}):
            for kind, data in stream.events(event, plans):
                self.controller.emit(kind, data)
        (row,) = self.rows()
        self.assertEqual(row["tool"], "StructuredOutput")
        self.assertEqual(row["arguments"]["operations"][0]["arguments"]["query"], "[REDACTED]")
        self.assertEqual(row["result"]["content"], "Echoed [REDACTED]")
        self.assertEqual(plans["t0"], PLAN, "The plan the host runs is the one the model wrote")
        self.assert_nowhere()

    def test_codex_and_helper_rows_hold_no_key_in_any_string_or_mapping_key(self):
        self.controller.api_key = KEY
        self.controller.emit("activity", {"id": "web/1", "tool": "web_search", "arguments": {"query": KEY}})
        self.controller.emit("activity", {"id": "web/1", "status": "completed", "result": {KEY: ["x " + KEY, 3]}})
        self.controller.emit("activity", {"id": "help/1", "tool": "angelo_scene_search", "arguments": {"query": KEY},
                                          "parent_call": "help", "collaborator": "Helper", "status": "running"})
        search, helper = self.rows()
        self.assertEqual(search["result"], {"[REDACTED]": ["x [REDACTED]", 3]})
        self.assertEqual((helper["parent_call"], helper["collaborator"]), ("help", "Helper"))
        self.assert_nowhere()

    def test_a_value_without_the_key_is_stored_as_it_came(self):
        arguments = {"query": "Cube"}
        self.controller.emit("activity", {"id": "a", "tool": "angelo_scene_search", "arguments": arguments})
        self.assertIs(self.rows()[0]["arguments"], arguments, "No key bound: as before")
        self.controller.api_key = KEY
        self.controller.emit("activity", {"id": "b", "tool": "angelo_scene_search", "arguments": arguments})
        self.assertIs(self.rows()[1]["arguments"], arguments, "No key in it: as before")

    def test_binding_a_key_redacts_it_from_rows_a_chat_saved_before(self):
        self.controller.state["messages"].append({"role": "activity", "call_id": "old", "text": "", "status": "completed",
                                                  "tool": "angelo_execute_python", "arguments": dict(SCRIPT),
                                                  "result": {"success": True, "stdout": KEY}})
        self.controller.save()
        self.controller.close()
        self.controller = self.open()  # The same chat, read back from its save.
        self.assertIn(KEY, json.dumps(self.rows()), "A chat saved before this release can hold it")
        self.controller.api_key = KEY  # What ui.py does when it opens the chat, or a send binds its connection.
        self.assert_nowhere()


class StopNoticeTests(ChatFixture, unittest.TestCase):
    def test_a_reply_only_round_stops_without_claiming_a_script_ran(self):
        stopped = stop_notice_for(self.controller)
        self.controller.stop()
        self.controller.emit("message", {"role": "system", **stopped})
        self.controller.pump_events()
        self.assertEqual(self.controller.state["messages"][-1]["text"], "Stopped")
        self.assertEqual(self.controller.status, "Stopping")

    def test_a_queued_call_is_cancelled_and_the_notice_says_only_stopped(self):
        self.controller.request_tool("script", "angelo_execute_python", dict(SCRIPT), [].append)
        self.assertEqual(stop_notice_for(self.controller)["text"], "Stopped")

    def test_a_running_script_keeps_the_notice_that_it_cannot_be_interrupted(self):
        self.controller.request_tool("script", "angelo_execute_python", dict(SCRIPT), [].append)
        next(iter(self.controller.pending.values())).executing = True
        self.assertIn("legacy script cannot be interrupted", stop_notice_for(self.controller)["text"])

    def test_the_stop_operator_reads_the_notice_before_it_stops(self):
        tree = ast.parse((ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text(encoding="utf-8"))
        operator = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "ANGELO_OT_stop")
        calls = [ast.unparse(node.func) for node in ast.walk(operator) if isinstance(node, ast.Call)]
        self.assertLess(calls.index("stop_notice_for"), calls.index("controller.stop"))


if __name__ == "__main__":
    unittest.main()
