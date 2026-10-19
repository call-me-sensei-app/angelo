# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.routing import RuntimeContext, shared_context, TOOLSET_VERSION
from angelo_agent.sessions import read_transcript, SessionStore
from angelo_agent.chat_settings import normalize_models, selected_model
from angelo_agent.chat_popovers import model_rows, popup_layout, model_row_rect
from angelo_agent.catalogs import ModelCatalogs
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, dynamic_tools, native_plan_schema, validate_call
from angelo_agent.delegation import NativeDelegate
from angelo_agent.approvals import approval_message

GPT = normalize_models([{"model": "gpt-test", "displayName": "GPT Test", "isDefault": True,
    "supportedReasoningEfforts": ["low", "high", "max"], "serviceTiers": [{"id": "priority"}]}])
CLAUDE = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
    "supportedEffortLevels": ["low", "medium", "high"]}], "claude")


class Executor:
    def __init__(self):
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return {"objects": [{"name": "Cube"}], "success": True}

    def precondition(self, name, arguments):
        return "stable"


class FakeProvider:
    instances = []
    wait = None
    child_tools = ()

    def __init__(self, context, settings):
        self.controller, self.settings = context, dict(settings)
        self.native_id, self.prompt, self.closed = None, "", False
        self.instances.append(self)

    def connect(self):
        if self.wait:
            self.wait.wait(2)
        self.native_id = self.controller.state.get("native_id") or str(uuid.uuid4())
        self.controller.emit("native_session", {"id": self.native_id})
        return self.native_id

    def run(self, native_id, text, observation=None):
        self.native_id, self.prompt = native_id, text
        self.captured = dict(self.controller.turn_options)
        for name, args in self.child_tools:
            ready = threading.Event()
            self.controller.request_tool(name, name, args, lambda result: ready.set())
            if not ready.wait(2):
                raise RuntimeError("Test tool callback timed out")
        self.controller.emit("message_complete", {"item": "reply", "text": "Advisory answer from " + self.controller.state["provider"]})
        return "completed"

    def stop(self):
        pass

    def close(self):
        self.closed = True


class RuntimeFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.a, self.b = str(uuid.uuid4()), str(uuid.uuid4())
        self.s1, self.s2 = {"model": "", "reuse_codex_login": True}, {"model": "", "reuse_codex_login": True}
        self.c.choose_connection("codex_subscription", self.s1, "", self.a, GPT, "gpt-test")
        self.executor = Executor()
        FakeProvider.instances, FakeProvider.wait, FakeProvider.child_tools = [], None, ()

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def pump(self, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.c.pump_events()
            self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            if not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.apply_next_connection()
                self.c.pump_events()
                return
            time.sleep(0.002)
        self.fail("Fake runtime did not finish")


class RoutingTests(RuntimeFixture):
    def test_roundtrip_keeps_one_chat_and_resumes_each_native_context(self):
        identity = self.c.state["id"]
        with patch("angelo_agent.controller.CodexProvider", FakeProvider), patch("angelo_agent.controller.ClaudeProvider", FakeProvider):
            self.c.send("GPT turn", {"objects": []})
            self.pump()
            gpt_native = self.c.state["native_id"]
            self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
            self.assertIsNone(self.c.state["native_id"])
            self.c.send("Claude turn", {"objects": []})
            self.pump()
            claude_native = self.c.state["native_id"]
            self.c.choose_connection("codex_subscription", self.s1, "", self.a, GPT, "gpt-test")
            self.assertEqual(self.c.state["native_id"], gpt_native)
            self.c.send("GPT return", {"objects": []})
            self.pump()
        self.assertEqual(self.c.state["id"], identity)
        self.assertNotEqual(gpt_native, claude_native)
        self.assertEqual([m["text"] for m in self.c.state["messages"] if m["role"] == "user"], ["GPT turn", "Claude turn", "GPT return"])
        returning = FakeProvider.instances[-1]
        self.assertIn("Claude turn", returning.prompt)
        self.assertNotIn('"text": "GPT turn"', returning.prompt, "Don't retransmit already-synced context on resume")
        self.assertEqual(returning.native_id, gpt_native)
        self.assertEqual(len(self.c.state["runtime_sessions"]), 3)  # includes the legacy unbound slot

    def test_connection_selected_during_connect_cannot_change_active_model_or_billing(self):
        gate = threading.Event()
        FakeProvider.wait = gate
        self.c.set_options(fast=True, effort="max")
        with patch("angelo_agent.controller.CodexProvider", FakeProvider):
            self.c.send("Keep this GPT turn intact", {})
            self.c.choose_connection("claude_api", self.s2, "secret-claude-key", self.b, CLAUDE, "sonnet")
            self.assertEqual(self.c.state["provider"], "codex_subscription")
            self.assertEqual(self.c.selection_provider, "claude_api")
            self.assertEqual(self.c.turn_options["model"], "gpt-test")
            self.assertTrue(self.c.turn_options["fast"])
            self.assertFalse(self.c.state["options"]["fast"])
            self.assertEqual(selected_model(self.c)["id"], "sonnet")
            gate.set()
            self.pump()
        self.assertEqual(FakeProvider.instances[0].captured["model"], "gpt-test")
        self.assertEqual(FakeProvider.instances[0].controller.api_key, "")
        self.assertEqual(self.c.state["provider"], "claude_api")
        self.assertEqual(self.c.api_key, "secret-claude-key")
        self.assertNotIn("secret-claude-key", self.c.store.path.read_text())

    def test_queued_choice_survives_restart_without_secrets_or_creating_a_chat(self):
        self.c.busy = True
        self.c.choose_connection("claude_api", self.s2, "never-serialize", self.b, CLAUDE, "sonnet")
        state = SessionStore(self.directory.name).load()
        self.assertEqual(state["next_route"], {"provider": "claude_api", "connection_id": self.b})
        self.assertEqual(state["id"], self.c.state["id"])
        self.assertEqual(state["options"]["model"], "sonnet")
        self.assertNotIn("never-serialize", self.c.store.path.read_text())
        self.c.busy = False

    def test_switch_preserves_references_checkpoint_and_tool_ledger(self):
        self.c.attachments = [{"name": "reference.png"}]
        self.c.request_checkpoint = "checkpoint"
        self.c.state["results"]["previous"] = {"document": repr(self.c.document), "result": {"success": True}}
        self.c.state["messages"] = [{"role": "user", "text": "Existing request", "checkpoint": "checkpoint"}]
        self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
        self.assertEqual(self.c.attachments, [{"name": "reference.png"}])
        self.assertIn("previous", self.c.state["results"])
        self.assertEqual(self.c.state["messages"][0]["checkpoint"], "checkpoint")

    def test_old_runtime_events_and_tool_calls_are_rejected_after_switch(self):
        old = RuntimeContext(self.c, "codex_subscription", self.s1, "", self.a, self.c._epoch)
        self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
        old.emit("native_session", {"id": "wrong-native-context"})
        self.c.emit("native_session", {"id": "late", "_epoch": old.epoch})
        self.c.pump_events()
        self.assertIsNone(self.c.state["native_id"])
        answers = []
        old.request_tool("late", "angelo_inspect_scene", {}, answers.append)
        self.assertIn("no longer owns", answers[0]["error"])
        self.assertFalse(self.c.pending)

    def test_idempotency_is_scoped_to_connection_and_native_session(self):
        self.c.busy = True
        first = RuntimeContext(self.c, "codex_subscription", self.s1, "", self.a, self.c._epoch)
        first.state["native_id"] = "gpt-native"
        replies = []
        first.request_tool("same-call", "angelo_inspect_scene", {}, replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        first.request_tool("same-call", "angelo_inspect_scene", {}, replies.append)
        self.assertTrue(replies[-1]["replayed"])
        self.c.busy = False
        self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
        self.c.busy = True
        other = self.c.runtime_context()
        other.state["native_id"] = "claude-native"
        other.request_tool("same-call", "angelo_inspect_scene", {}, replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        self.assertEqual(len(self.executor.calls), 2)
        self.assertNotIn("replayed", replies[-1])
        self.c.busy = False

    def test_restore_invalidates_all_provider_contexts_but_not_history(self):
        self.c.state["messages"] = [{"role": "user", "text": "Keep history"}]
        self.c.state["native_id"] = "gpt-native"
        self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
        self.c.state["native_id"] = "claude-native"
        self.c.invalidate_runtime()
        self.c.choose_connection("codex_subscription", self.s1, "", self.a, GPT, "gpt-test")
        self.assertIsNone(self.c.state["native_id"])
        self.assertEqual(self.c.state["messages"][0]["text"], "Keep history")

    def test_reauthentication_only_invalidates_the_affected_account(self):
        self.c.state["native_id"] = "gpt-native"
        self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "sonnet")
        self.c.state["native_id"] = "claude-native"
        self.c.invalidate_runtime(self.a)
        self.assertEqual(self.c.state["native_id"], "claude-native")
        self.c.choose_connection("codex_subscription", self.s1, "", self.a, GPT, "gpt-test")
        self.assertIsNone(self.c.state["native_id"])

    def test_unknown_model_never_uses_default_or_another_billing_account(self):
        with self.assertRaises(ValueError):
            self.c.choose_connection("claude_subscription", self.s2, "", self.b, CLAUDE, "not-real")
        self.assertEqual(self.c.state["provider"], "codex_subscription")

    def test_handoff_transfers_public_results_and_restore_not_reasoning(self):
        messages = [{"role": "thinking", "text": "Never transfer this"},
            {"role": "activity", "tool": "angelo_set_transform", "status": "completed", "result": {"location": [1, 2, 3]}, "reverted": True},
            {"role": "system", "text": "Restored request snapshot"}]
        context = shared_context(messages)
        self.assertIn("Restored request snapshot", context)
        self.assertIn('"reverted": true', context)
        self.assertNotIn("Never transfer", context)
        self.assertIn("NOT new requests", context)
        self.assertLess(len(shared_context([{"role": "assistant", "text": "a" * 8000}] * 80)), 24300)

    def test_persistence_drops_extra_fields_from_route_metadata(self):
        self.c.state["runtime_sessions"] = {self.a: {"native_id": "native", "synced_length": 2, "api_key": "never-write"}, "../../escape": {"native_id": "bad"}}
        self.c.state["next_route"] = {"provider": "claude_api", "connection_id": self.b, "token": "never-write"}
        self.c.save()
        raw = read_transcript(self.c.store.path)
        self.assertNotIn("never-write", str(raw))
        self.assertNotIn("../../escape", raw["runtime_sessions"])

    def test_duplicate_model_ids_have_one_qualified_selection_and_account_labels(self):
        models = [{**GPT[0], "connection_id": self.a, "connection_name": "Personal", "billing": "Subscription"},
                  {**GPT[0], "connection_id": self.b, "connection_name": "Work", "billing": "API billing"}]
        rows = model_rows(models, "gpt-test", self.b)
        self.assertEqual([r["value"] for r in rows if r["selected"]], [(self.b, "gpt-test")])
        self.assertEqual(rows[2]["subtitle"], "Work · API billing")
        layout = popup_layout("models", 531, 850, (280, 28, 170, 28), 2, rows=rows)
        self.assertEqual(layout["content_height"], 54 + 46 + 46)
        self.assertEqual(model_row_rect(layout, 2)[3], 46)

    def test_stale_catalog_result_cannot_restore_a_removed_account(self):
        catalog = ModelCatalogs(self.directory.name)
        catalog.invalidate(self.a)
        catalog.events.put((self.a, 0, GPT, ""))
        self.assertFalse(catalog.pump())
        self.assertNotIn(self.a, catalog.entries)


class DelegationTests(RuntimeFixture):
    # Reuse lifecycle helpers, not the routing tests above.
    def binding(self, identity):
        if identity == self.a:
            return {"provider": "codex_subscription", "settings": self.s1, "api_key": "", "name": "My ChatGPT", "models": GPT}
        if identity == self.b:
            return {"provider": "claude_subscription", "settings": self.s2, "api_key": "", "name": "My Claude", "models": CLAUDE}
        raise RuntimeError("Not connected")

    def consult(self, identity=None, model="sonnet", id="consult"):
        replies = []
        self.c.request_tool(id, "angelo_delegate", {"connection_id": identity or self.b, "model": model, "task": "Inspect this scene and advise; don't edit"}, replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        return replies, next((call for call in self.c.pending.values() if call.name == "angelo_delegate"), None)

    def finish_consultation(self, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.c.delegate_jobs:
            self.c.pump_events()
            self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
            time.sleep(0.003)
        self.c.pump_events()
        self.assertFalse(self.c.delegate_jobs)

    def test_both_directions_are_async_read_only_and_in_the_same_chat(self):
        identity = self.c.state["id"]
        self.c.delegation_resolver = self.binding
        with patch("angelo_agent.delegation.CodexProvider", FakeProvider), patch("angelo_agent.delegation.ClaudeProvider", FakeProvider):
            for target, model in ((self.b, "sonnet"), (self.a, "gpt-test")):
                self.c.busy = True
                self.c.set_options(approval="READ_ONLY", fast=True)
                replies, call = self.consult(target, model, id=target)
                self.assertFalse(replies)
                self.assertTrue(call.approval, "Read-only inference still needs billing approval")
                self.assertIn("Subscription", call.usage_label)
                FakeProvider.wait = threading.Event()
                start = time.monotonic()
                self.c.execute_tool(call, self.executor, self.c.document)
                self.assertLess(time.monotonic() - start, .1)
                FakeProvider.wait.set()
                self.finish_consultation()
                self.assertTrue(replies[0]["read_only"])
                self.assertIn("Advisory answer", replies[0]["reply"])
                self.assertFalse(FakeProvider.instances[-1].captured["fast"])
                self.assertEqual(FakeProvider.instances[-1].captured["approval"], "READ_ONLY")
                self.assertIsNone(self.c.state["native_id"], "Child context must not replace the lead context")
                self.c.busy = False
        self.assertEqual(self.c.state["id"], identity)
        self.assertEqual(sum(m.get("tool") == "angelo_delegate" for m in self.c.state["messages"]), 2)

    def test_child_cannot_mutate_execute_python_or_delegate(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        replies, call = self.consult()
        job = NativeDelegate(self.c, call, self.binding(self.b), {})
        for name, args in (("angelo_execute_python", {"code": "raise Exception()"}),
                           ("angelo_create_cube", {}), ("angelo_write_file", {}), ("angelo_delegate", {})):
            results = []
            job.context.request_tool("no", name, args, results.append)
            self.assertIn("read-only", results[0]["error"])
        self.assertEqual(set(t["name"] for t in dynamic_tools(COLLABORATOR_TOOLS)), COLLABORATOR_TOOLS)
        schema = native_plan_schema(COLLABORATOR_TOOLS)
        self.assertNotIn("angelo_execute_python", str(schema))
        self.assertNotIn("angelo_delegate", str(schema))
        self.assertFalse(job.context.turn_options["fast"])

    def test_child_reads_use_the_existing_main_thread_queue(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        replies, call = self.consult()
        FakeProvider.child_tools = (("angelo_inspect_scene", {}),)
        with patch("angelo_agent.delegation.ClaudeProvider", FakeProvider):
            self.c.execute_tool(call, self.executor, self.c.document)
            self.finish_consultation()
        self.assertIn("reply", replies[0])
        self.assertIn("Current native scene observation", FakeProvider.instances[-1].prompt)
        self.assertIn('"name":"Cube"', FakeProvider.instances[-1].prompt)
        self.assertEqual(len(self.executor.calls), 2, "One current observation and one child read, no MCP")

    def test_decline_never_starts_inference(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        replies, call = self.consult()
        self.c.decline_tool(call.id)
        self.assertIn("declined", replies[0]["error"])
        self.assertFalse(FakeProvider.instances)

    def test_double_approval_cannot_start_or_bill_two_collaborators(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        replies, call = self.consult()
        gate = FakeProvider.wait = threading.Event()
        with patch("angelo_agent.delegation.ClaudeProvider", FakeProvider):
            self.c.execute_tool(call, self.executor, self.c.document)
            self.c.execute_tool(call, self.executor, self.c.document)
            self.assertFalse(call.approval)
            self.assertEqual(self.c._delegations_this_turn, 1)
            self.assertEqual(len(self.executor.calls), 1)
            gate.set()
            self.finish_consultation()
        self.assertEqual(len(replies), 1)
        self.assertEqual(len(FakeProvider.instances), 1)

    def test_cancel_stops_consultation_and_rejects_late_results(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        replies, call = self.consult()
        gate = FakeProvider.wait = threading.Event()
        with patch("angelo_agent.delegation.ClaudeProvider", FakeProvider):
            self.c.execute_tool(call, self.executor, self.c.document)
            self.c.stop()
            self.assertIn("cancelled", replies[0]["error"])
            gate.set()
            self.finish_consultation()
        self.assertEqual(len(replies), 1)
        self.assertNotIn("reply", replies[0])
        self.c.busy = False

    def test_timeout_is_bounded_and_does_not_change_lead_connection(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        self.c.delegation_timeout = .02
        replies, call = self.consult()
        gate = FakeProvider.wait = threading.Event()
        with patch("angelo_agent.delegation.ClaudeProvider", FakeProvider):
            self.c.execute_tool(call, self.executor, self.c.document)
            self.finish_consultation()
            gate.set()
        self.assertIn("timed out", replies[0]["error"])
        self.assertEqual(self.c.state["connection_id"], self.a)
        self.c.busy = False

    def test_two_consultation_budget_and_unknown_connections_fail_closed(self):
        self.c.delegation_resolver = self.binding
        self.c.busy = True
        self.c._delegations_this_turn = 2
        replies, call = self.consult()
        self.assertIn("No further", replies[0]["error"])
        self.assertIsNone(call)
        self.c._delegations_this_turn = 0
        replies, call = self.consult(str(uuid.uuid4()), id="unavailable")
        self.assertIn("Not connected", replies[0]["error"])
        self.assertFalse(FakeProvider.instances)

    def test_delegation_schema_and_human_billing_approval(self):
        args = {"connection_id": self.b, "model": "sonnet", "task": "Evaluate the lighting"}
        validate_call("angelo_delegate", args)
        message = approval_message("angelo_delegate", args, "My Claude · Subscription")
        self.assertIn("My Claude", message["title"])
        self.assertIn("API billing", message["warning"])
        self.assertNotIn("{", message["body"])
        for invalid in ({**args, "connection_id": "../../escape"}, {**args, "task": "x" * 8001}, {**args, "model": ""}):
            with self.assertRaises(ValueError):
                validate_call("angelo_delegate", invalid)


if __name__ == "__main__":
    unittest.main()
