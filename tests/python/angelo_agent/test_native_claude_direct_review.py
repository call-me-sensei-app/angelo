# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The direct-tools route's break-it review findings (2026-10-19), each pinned by the reproduction that showed it.

Every test asserts the right behaviour; each failed on the route as first built (claude_direct.py). Unbilled
stand-ins only (claude_protocol_fixtures, the DirectFixture of test_native_claude_direct_tools).
"""

import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from claude_protocol_fixtures import ClaudeAdapterFixture  # noqa: E402
from test_native_claude_direct_tools import DirectFixture, answering, mcp  # noqa: E402
from angelo_agent import claude_direct, claude_evidence  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

CUBE = ("angelo_create_cube", {"name": "A", "size": 1, "location": [0, 0, 0]})
SEARCH = ("angelo_scene_search", {"query": "Bridge"})
JOB = ("angelo_job_start", {"kind": "RENDER_STILL", "output": "out.png"})


class Base(ClaudeAdapterFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.host.claude_direct_tools = True

    def run_calls(self, calls, results, reply=""):
        self.process = DirectFixture(calls, reply)
        self.popen_mock.return_value = self.process
        self.host.request_tool = answering(list(results)) if not callable(results) else Mock(side_effect=results)
        return self.provider.run_request(self.host, None, "Make a bridge", observation={})

    def texts(self):
        return [" ".join(item.get("text", "") for item in answer["result"]["content"]) for answer in self.process.answers]

    def rows(self):
        return [data.get("text") for kind, data in self.host.events if kind == "message" and data.get("role") == "system"]


def edited(self):   # An edit leaves a kept view to capture again, as one asked for earlier would.
    self.edited = True
    self.dirty.add("kept")


class ToolSchemaTests(unittest.TestCase):
    def test_every_tool_has_an_object_root_the_api_takes(self):
        """R1: Claude Code dropped angelo_apply_recipe, whose schema had a oneOf at its root."""
        for tool in claude_direct.tool_list():
            with self.subTest(tool=tool["name"]):
                schema = tool["inputSchema"]
                self.assertEqual(schema.get("type"), "object")
                self.assertFalse({"oneOf", "anyOf", "allOf"} & set(schema))

    def test_a_root_union_keeps_each_variants_fields_and_what_goes_with_what(self):
        recipe = {tool["name"]: tool for tool in claude_direct.tool_list()}["angelo_apply_recipe"]["inputSchema"]
        names = recipe["properties"]["recipe"]["enum"]
        self.assertIn("bevel-and-shade", names)
        titles = [form["title"] for form in recipe["properties"]["parameters"]["anyOf"]]
        self.assertEqual(titles, ["recipe: %s" % name for name in names])
        self.assertEqual(set(recipe["required"]), {"recipe", "target", "parameters", "mode"})


class EndingTests(Base):
    def test_a_correction_carries_the_exact_schemas(self):
        """R2: the plan route's correction prompt carries them; the call's result now does too."""
        failed = {"error": "Unsupported job settings.", "error_code": "VALIDATION", "scene_changed": False}
        self.assertEqual(self.run_calls([JOB], [failed]), "completed")
        value = json.loads(self.process.answers[0]["result"]["content"][-1]["text"])
        self.assertIn("angelo_job_start", json.dumps(value["exact_schemas"]))
        self.assertFalse(any("corrected plan" in row for row in self.rows()), "no plan wording for the person")

    def test_the_call_cap_fails_the_request(self):
        """R3: past the cap the request ends and fails, as the plan route's round limit does."""
        rows = {"success": True, "objects": [], "total": 0}
        with mock.patch.object(claude_direct, "MAX_CALLS", 2), self.assertRaisesRegex(RpcError, "limit of 2 tool calls"):
            self.run_calls([SEARCH] * 4, [dict(rows) for _ in range(2)], reply="All done.")
        self.assertEqual(self.host.request_tool.call_count, 2)
        self.assertIn("already ended", self.texts()[-1])

    def test_a_host_error_reaches_the_model_redacted(self):
        """R4"""
        self.host.redact = lambda text: str(text).replace("sk-SECRET", "[redacted]")
        with self.assertRaises(RpcError):
            self.run_calls([SEARCH, SEARCH], lambda *a, **k: (_ for _ in ()).throw(RpcError("upstream said key sk-SECRET")))
        self.assertNotIn("sk-SECRET", " ".join(self.texts()))
        self.assertIn("[redacted].", " ".join(self.texts()))

    def test_only_successful_edits_reach_the_closing_account(self):
        """R5"""
        state = claude_direct.DirectRequest(Mock(_current=lambda owner: True), self.host, Mock(), Mock())
        failed = {"error": "x", "error_code": "VALIDATION", "scene_changed": False, "native_dispatch_wait_ms": 1}
        state.calls = [({"tool": "angelo_scene_program", "arguments": {}}, failed),
                       ({"tool": "angelo_scene_program", "arguments": {}}, {"success": True})]
        state.completions = [None, None]
        calls, completions = state.edits()
        self.assertEqual([result for _call, result in calls], [{"success": True}])
        self.assertEqual(len(completions), 1)

    def test_a_failed_only_edit_and_an_empty_reply_claim_nothing(self):
        """R6: no closing account when no edit succeeded."""
        failed = {"error": "Unsupported job settings.", "error_code": "VALIDATION", "scene_changed": False,
                  "native_dispatch_wait_ms": 1}
        self.assertEqual(self.run_calls([JOB], [failed], reply=""), "completed")
        self.assertFalse(any("completed" in row.lower() for row in self.rows()), self.rows())

    def test_a_refresh_decision_says_the_request_ended_in_that_call(self):
        """R7"""
        ok = {"success": True, "scene_changed": True}
        with mock.patch.object(claude_evidence.ClaudeVisualEvidence, "refresh",
                               lambda self, call, current: "Document changed or turn stopped"), \
                mock.patch.object(claude_evidence.ClaudeVisualEvidence, "invalidate", edited):
            self.assertEqual(self.run_calls([CUBE, CUBE], [ok]), "interrupted")
        self.assertEqual(self.host.request_tool.call_count, 1)
        self.assertIn("The request ended with this call", self.texts()[0])
        self.assertIn("may be in the scene", self.texts()[0])

    def test_a_call_that_may_have_changed_the_scene_is_never_told_nothing_ran(self):
        """R2 of the report (its finding 2)."""
        failed = {"error": "Commit failed midway.", "error_code": "INTERNAL_ERROR", "scene_changed": True,
                  "partial_changes_possible": True}
        self.run_calls([CUBE, CUBE], [failed])
        first, second = self.texts()
        self.assertNotIn("did not run", first)
        self.assertNotIn("The results of what ran", first)
        self.assertIn("may be in the scene", first)
        self.assertIn("this call did not run", second)

    def test_a_host_error_after_dispatch_says_the_edit_may_have_applied(self):
        """R10"""
        ok = {"success": True, "scene_changed": True}

        def boom(self, call, current):
            raise RpcError("Native visual observation did not return every requested image.")
        with mock.patch.object(claude_evidence.ClaudeVisualEvidence, "refresh", boom), \
                mock.patch.object(claude_evidence.ClaudeVisualEvidence, "invalidate", edited), \
                self.assertRaises(RpcError):
            self.run_calls([CUBE], [ok])
        self.assertIn("may be in the scene", self.texts()[0])
        self.assertNotIn("did not run", self.texts()[0])


class ReReviewTests(Base):
    """The re-review of the fixes (same date)."""

    def test_an_applied_but_unproven_edit_with_an_empty_reply_gets_the_plain_notice(self):
        """RR1: as the plan route does; only when an edit succeeded (R6 stays fixed)."""
        ok = {"success": True, "scene_changed": True, "native_dispatch_wait_ms": 1}
        self.assertEqual(self.run_calls([CUBE], [ok], reply=""), "completed")
        self.assertTrue(self.rows())

    def test_a_failed_edit_stating_no_change_fields_may_have_changed_the_scene(self):
        """RR2"""
        self.run_calls([CUBE], [{"error": "Blender raised RuntimeError in the operator."}])
        self.assertIn("may be in the scene", self.texts()[0])

    def test_a_declined_edit_did_not_run(self):
        self.run_calls([CUBE], [{"error": "The user declined this change."}])
        self.assertIn("the person declined it", self.texts()[0])

    def test_a_read_only_call_that_raised_changed_nothing(self):
        """RR3"""
        def boom(*_a, **_k):
            raise RpcError("Native visual observation did not return every requested image.")
        with self.assertRaises(RpcError):
            self.run_calls([SEARCH], boom)
        self.assertIn("changed nothing", self.texts()[0])
        self.assertNotIn("may be in the scene", self.texts()[0])

    def test_root_fields_beside_a_union_are_kept(self):
        """RR5"""
        from angelo_agent import claude_direct_schema
        schema = {"type": "object", "properties": {"shared": {"type": "string"}, "kind": {}}, "required": ["shared"],
                  "oneOf": [{"type": "object", "properties": {"kind": {"const": "a"}, "x": {"type": "number"}}},
                            {"type": "object", "properties": {"kind": {"const": "b"}, "y": {"type": "number"}}}]}
        out = claude_direct_schema.object_root(schema)
        self.assertEqual(out["properties"]["shared"], {"type": "string"})
        self.assertEqual(out["properties"]["kind"], {"enum": ["a", "b"]})
        self.assertEqual(out["required"], ["shared"])


class TransportTypeTests(Base):
    """Windows A/B round 1 (2026-10-20): a deferred tool's numbers arrived as strings on this route."""

    def test_a_deferred_tools_string_numbers_are_decoded_by_its_exact_schema(self):
        from angelo_agent.claude_direct_schema import typed_arguments
        sent = {"kind": "RENDER_STILL", "output": "60", "frame_start": "60", "resolution": "[1920, 1080]",
                "queue": "true", "samples": "lots", "minutes": "1.5"}
        typed = typed_arguments("angelo_job_start", sent)
        self.assertEqual(typed, dict(sent, frame_start=60, resolution=[1920, 1080], queue=True, minutes=1.5))
        self.assertEqual(typed["output"], "60", "a string property stays a string")
        self.assertEqual(typed["samples"], "lots", "what is not JSON of the declared type is left for validation")
        self.assertIs(typed_arguments("angelo_scene_search", {"limit": "5"})["limit"], "5", "core tools are typed")

    def test_the_call_runs_with_the_decoded_values(self):
        self.run_calls([("angelo_job_start", {"kind": "RENDER_STILL", "output": "out.png", "frame_start": "60"})],
                       [{"job": "j1", "status": "running", "kind": "RENDER_STILL"}])
        self.assertEqual(self.host.request_tool.call_args[0][2]["frame_start"], 60)


class DeliveryTests(unittest.TestCase):
    def state(self):
        return claude_direct.DirectRequest(SimpleNamespace(_current=lambda owner: True),
                                           SimpleNamespace(references=Mock(), allowed_tools=None), Mock(), Mock())

    def test_a_delivery_seen_before_never_runs_again_and_gets_the_answer(self):
        """P1, RR4: while the call runs a repeat waits for its result; after, it gets the result at once."""
        state, answers = self.state(), []
        self.assertTrue(state.admit("cc-1", lambda value: answers.append(("first", value))))
        self.assertFalse(state.admit("cc-1", lambda value: answers.append(("during", value))))
        self.assertTrue(state.admit("cc-2", answers.append))
        state.answered("cc-1", "result")
        self.assertFalse(state.admit("cc-1", lambda value: answers.append(("after", value))))
        self.assertEqual(answers, [("first", "result"), ("during", "result"), ("after", "result")])

    def test_the_channel_drops_a_repeated_call(self):
        state, written = self.state(), []
        state.call = Mock(return_value={"content": [], "isError": False})
        owner = SimpleNamespace(controller=SimpleNamespace(allowed_tools=None), same_request=lambda other: True)
        turn = SimpleNamespace(owner=owner)
        transport = SimpleNamespace(lock=threading.RLock(), turn=turn, owner=owner)
        state.owner = owner
        provider = SimpleNamespace(_direct=state, _write=lambda message, *a, **k: written.append(message),
                                   _current=lambda *a: True)
        event = mcp(7, "tools/call", {"name": "angelo_scene_search", "arguments": {}})
        claude_direct.answer(provider, transport, event)
        for thread in threading.enumerate():
            if thread.name == "AngeloClaudeCall":
                thread.join(5)
        claude_direct.answer(provider, transport, event)   # The same call again, after its answer.
        self.assertEqual(state.call.call_count, 1)
        self.assertEqual(len(written), 2, "every delivery is answered")
        self.assertEqual(written[0]["response"]["response"], written[1]["response"]["response"])

    def test_calls_run_in_the_order_they_arrived(self):
        """P2: a later arrival never runs before an earlier one."""
        state, order = self.state(), []
        state._call = lambda name, arguments: order.append(name) or {"content": [], "isError": False}
        tickets = [state.arrived() for _ in range(5)]
        threads = [threading.Thread(target=state.call, args=("call-%d" % ticket, {}, ticket))
                   for ticket in reversed(tickets)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ["call-%d" % ticket for ticket in tickets])


if __name__ == "__main__":
    unittest.main()
