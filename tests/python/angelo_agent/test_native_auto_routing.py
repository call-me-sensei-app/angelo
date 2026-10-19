# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Auto routing behind its measured gate, per-model native contexts, escalation,
the plan's low-confidence flag (toolset 19) and standing helper consent.

Dependency-free: scripted stand-in runtimes, no process, account or network.
The measured results used here are invented test data, not benchmark results.
"""

import copy
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture  # noqa: E402
from test_native_routing import Executor  # noqa: E402
from angelo_agent import model_profiles, model_routing  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from angelo_agent.tool_specs import COLLABORATOR_TOOLS, native_plan_schema  # noqa: E402

CATALOG = normalize_models([{"value": "default", "displayName": "Default", "isDefault": True},
                            {"value": "claude-opus-5-5", "displayName": "Opus"},
                            {"value": "claude-sonnet-5-5", "displayName": "Sonnet"},
                            {"value": "haiku", "displayName": "Haiku"}], "claude")


def results(pass_rate, cost, tasks=40):
    return {kind: {"tasks": tasks, "pass_rate": pass_rate, "cost_usd": cost, "p75_ms": 5000.0,
                   "source": "test-fixture", "date": "2026-10-08"} for kind in model_routing.AUTO_KINDS}


PARITY = {"claude-opus-5-5": {"low": results(1.0, 0.03)}, "claude-haiku-5-5": {"low": results(0.99, 0.001)}}


def plan_result(reply="Done", operations=(), verify=False, conf=None):
    plan = {"reply": reply, "operations": list(operations), "verify": verify}
    if conf is not None:
        plan["conf"] = conf
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": "fixture-native",
            "structured_output": plan}


class ConfidenceFieldTests(unittest.TestCase):
    def test_the_field_is_optional_and_only_low(self):
        for schema in (native_plan_schema(), native_plan_schema(COLLABORATOR_TOOLS)):
            self.assertEqual(schema["properties"]["conf"], {"enum": ["low"]})
            # An edit's reply is optional since the agent stack's toolset 21.
            self.assertEqual(schema["required"], ["operations"])

    def test_measured_cost_of_the_field(self):
        # Unbilled measurement (doc/angelo/model_routing.rst, "Plan confidence"):
        # the schema grows 24 bytes and stays inside its pinned budget.
        compact = json.dumps(native_plan_schema(), separators=(",", ":"), ensure_ascii=False).encode()
        without = copy.deepcopy(native_plan_schema())
        without["properties"].pop("conf")
        self.assertEqual(len(compact) - len(json.dumps(without, separators=(",", ":"), ensure_ascii=False).encode()), 24)
        self.assertEqual(len(',"conf":"low"'), 13, "bytes a low-confidence plan adds to its output")


class ConfidenceRuntimeTests(ClaudeAdapterFixture, unittest.TestCase):
    def run_plan(self, plan, escalate):
        self.host.escalate_on_low_confidence = escalate
        self.process.on_user = lambda process, _: process.stdout.emit(plan)
        return self.provider.run_request(self.host, None, "Make a castle", observation={})

    def test_low_confidence_escalates_before_anything_runs_only_under_auto(self):
        self.assertEqual(self.run_plan(plan_result(conf="low"), True), "escalated")
        self.host.request_tool.assert_not_called()
        self.assertTrue(any("low confidence" in data.get("text", "") for kind, data in self.host.events))

    def test_without_auto_the_flag_changes_nothing(self):
        self.assertEqual(self.run_plan(plan_result(conf="low"), False), "completed")
        self.popen_mock.return_value = NativeInitializeFixture()
        self.assertEqual(self.run_plan(plan_result(), True), "completed")

    def test_another_value_is_an_invalid_plan(self):
        with self.assertRaisesRegex(RpcError, "Invalid native plan"):
            self.run_plan(plan_result(conf="high"), True)


class AutoPolicyTests(unittest.TestCase):
    def profile(self, measured, billing="SUBSCRIPTION"):
        return model_profiles.capability_profile(str(uuid.uuid4()), "claude", billing, CATALOG,
                                                 measured=measured)

    def test_profiles_list_only_this_connections_catalog_under_its_ids(self):
        profile = self.profile({})
        self.assertEqual({m["id"] for m in profile["models"]}, {"claude-opus-5-5", "claude-sonnet-5-5", "haiku"})
        self.assertIsNone(model_profiles.capability_profile("c", "codex", "SUBSCRIPTION", CATALOG))

    def test_no_measured_pair_keeps_default(self):
        self.assertIsNone(model_routing.auto_route(self.profile({})))
        self.assertIsNone(model_routing.auto_route(self.profile({"claude-haiku-5-5": {"low": results(1.0, 0.001)}})))

    def test_parity_moves_work_to_the_cheaper_tier_with_a_ladder(self):
        route = model_routing.auto_route(self.profile(PARITY))
        self.assertEqual((route.model, route.tier, route.cache_ttl), ("haiku", "fast", "1h"))
        # A key-billed connection's Auto route carries Claude Code's 5-minute default there.
        self.assertEqual(model_routing.auto_route(self.profile(PARITY, "API_KEY")).cache_ttl, "5m")
        self.assertEqual([rung.model for rung in route.ladder], ["claude-sonnet-5-5", "claude-opus-5-5"])

    def test_below_the_bar_or_at_the_top_keeps_default(self):
        worse = dict(PARITY, **{"claude-haiku-5-5": {"low": results(0.9, 0.001)}})
        self.assertIsNone(model_routing.auto_route(self.profile(worse)))
        one_kind = {"claude-opus-5-5": PARITY["claude-opus-5-5"],
                    "claude-haiku-5-5": {"low": {"scene_edit": results(1.0, 0.001)["scene_edit"]}}}
        self.assertIsNone(model_routing.auto_route(self.profile(one_kind)), "parity on every Auto kind")

    def test_a_much_slower_cheaper_model_is_skipped(self):
        slow = copy.deepcopy(PARITY)
        slow["claude-haiku-5-5"]["low"]["scene_read"]["p75_ms"] = 5000.0 * 1.25
        self.assertEqual(model_routing.auto_route(self.profile(slow)).model, "haiku", "exactly 25% slower is allowed")
        slow["claude-haiku-5-5"]["low"]["scene_read"]["p75_ms"] = 5000.0 * 1.26
        self.assertIsNone(model_routing.auto_route(self.profile(slow)))
        three = dict(slow, **{"claude-sonnet-5-5": {"low": results(1.0, 0.017)}})
        self.assertEqual(model_routing.auto_route(self.profile(three)).model, "claude-sonnet-5-5")
        unmeasured = copy.deepcopy(PARITY)
        del unmeasured["claude-haiku-5-5"]["low"]["scene_edit"]["p75_ms"]
        self.assertIsNone(model_routing.auto_route(self.profile(unmeasured)), "latency must be measured")

    def test_repeated_escalations_hold_a_tier_up_or_return_to_default(self):
        self.assertIsNone(model_routing.auto_route(self.profile(PARITY), recent_escalations=2))
        three = dict(PARITY, **{"claude-sonnet-5-5": {"low": results(1.0, 0.017)}})
        self.assertEqual(model_routing.auto_route(self.profile(three), recent_escalations=2).model, "claude-sonnet-5-5")


class ScriptedRuntime:
    """Claude stand-in: Haiku plans with low confidence, every other model completes."""
    instances = []

    def __init__(self, context, settings):
        self.controller = context
        self.instances.append(self)

    def connect(self):
        native = self.controller.state.get("native_id") or "native-" + str(uuid.uuid4())[:8]
        self.controller.emit("native_session", {"id": native})
        return native

    def run_request(self, context, native_id, text, observation=None):
        self.model, self.prompt = context.turn_options["model"], text
        self.escalate = context.escalate_on_low_confidence
        context.record_provider_turn()
        context.record_provider_accepted()
        if self.model == "haiku" and self.escalate:
            context.emit("message", {"role": "system", "text": "low confidence"})
            return "escalated"
        context.emit("message_complete", {"item": "reply", "text": "Answered by " + str(self.model or "default")})
        return "completed"

    def stop(self):
        pass

    def close(self):
        pass


class ControllerAutoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.connection = str(uuid.uuid4())
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.connection, CATALOG, "")
        ScriptedRuntime.instances = []
        self.patch = patch("angelo_agent.controller.ClaudeProvider", ScriptedRuntime)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.c.close()
        self.directory.cleanup()

    def send_and_wait(self, text="How many cubes?"):
        self.c.send(text, {"objects": []})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.c.pump_events()
            if not self.c.busy and not (self.c._worker and self.c._worker.is_alive()):
                self.c.pump_events()
                return
            time.sleep(0.002)
        self.fail("Scripted runtime did not finish")

    def test_without_measurements_default_behaves_as_before(self):
        self.send_and_wait()
        self.assertEqual(ScriptedRuntime.instances[-1].model, "")
        self.assertFalse(ScriptedRuntime.instances[-1].escalate)
        receipt = self.c.telemetry.receipts[-1]
        self.assertEqual((receipt["route"]["auto"], receipt["route"]["requested_model"]), (False, ""))

    def test_pins_and_fast_mode_switch_auto_off(self):
        with patch.dict(model_profiles.MEASURED, PARITY):
            self.c.set_options(model="claude-sonnet-5-5")
            self.assertIsNone(self.c._auto_route(dict(self.c.state["options"])))
            self.c.set_options(model="", fast=True)
            self.assertIsNone(self.c._auto_route(dict(self.c.state["options"])))
            self.c.set_options(fast=False)
            self.assertEqual(self.c._auto_route(dict(self.c.state["options"])).model, "haiku")

    def test_low_confidence_escalates_once_in_the_stronger_models_own_context(self):
        with patch.dict(model_profiles.MEASURED, PARITY):
            self.send_and_wait("Build a castle")
        haiku, sonnet = ScriptedRuntime.instances[-2:]
        self.assertEqual((haiku.model, sonnet.model), ("haiku", "claude-sonnet-5-5"))
        self.assertEqual(sonnet.prompt.split("\n", 1)[0], "Build a castle")
        users = [m for m in self.c.state["messages"] if m.get("role") == "user"]
        self.assertEqual(len(users), 1, "the escalation adds no user message")
        self.assertTrue(any("Auto asked claude-sonnet-5-5" in m.get("text", "") for m in self.c.state["messages"]))
        first, second = list(self.c.telemetry.receipts)[-2:]
        self.assertEqual((first["status"], first["route"]["requested_model"], first["route"]["escalation"]),
                         ("escalated", "haiku", 0))
        self.assertEqual((second["status"], second["route"]["requested_model"], second["route"]["escalation"]),
                         ("completed", "claude-sonnet-5-5", 1))
        # Each model has its own native context on the connection.
        sessions = self.c.state["runtime_sessions"]
        self.assertEqual(sessions[self.connection]["model"], "haiku")
        self.assertEqual(sessions[self.connection + "#claude-sonnet-5-5"]["model"], "claude-sonnet-5-5")
        self.assertNotEqual(sessions[self.connection]["native_id"],
                            sessions[self.connection + "#claude-sonnet-5-5"]["native_id"])

    def test_a_possibly_committed_request_only_suggests(self):
        self.c._turn_route = model_routing.Route(self.connection, "haiku", "fast", "low", "1h", True, False, "t",
                                                 (model_routing.Rung("claude-opus-5-5", "strong", "low"),))
        self.c._turn_mutated = True
        with patch.object(self.c, "_start_turn") as start:
            self.c._after_turn({"status": "failed", "trigger": "host_check_failed"})
        start.assert_not_called()
        self.assertIn("nothing is retried automatically", self.c.state["messages"][-1]["text"])
        self.c._turn_mutated = False
        self.c._after_turn({"status": "failed"})  # No quality trigger: nothing at all.
        self.assertEqual(self.c._recent_escalations, [False, False])

    def test_switching_models_resumes_each_models_own_context(self):
        self.send_and_wait()
        base = self.c.state["native_id"]
        self.c.set_options(model="claude-opus-5-5")
        self.send_and_wait()
        opus = self.c.state["native_id"]
        self.assertNotEqual(base, opus)
        self.assertEqual(self.c.state["context_slot"], "claude-opus-5-5")
        self.c.set_options(model="")
        self.send_and_wait()
        self.assertEqual(self.c.state["native_id"], base, "returning to a model resumes its context")
        self.assertIsNone(self.c.state["context_slot"])
        # Persisted with the chat and reloaded intact.
        self.c.save()
        loaded = SessionStore(self.directory.name).load()
        self.assertEqual(loaded["runtime_sessions"][self.connection + "#claude-opus-5-5"]["native_id"], opus)
        self.assertEqual(loaded["runtime_sessions"][self.connection]["model"], "default")
        self.assertIsNone(loaded["context_slot"])
        # Re-authentication forgets every context of the connection.
        self.c.invalidate_runtime(self.connection)
        self.assertFalse(any(key.startswith(self.connection) for key in self.c.state["runtime_sessions"]))


class HelperConsentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.lead, self.other, self.executor = str(uuid.uuid4()), str(uuid.uuid4()), Executor()
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.lead, CATALOG, "claude-opus-5-5")
        self.c.delegation_resolver = lambda identity: {"provider": "claude_subscription", "settings": {}, "api_key": "",
                                                       "name": "Claude", "models": CATALOG}

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def consult(self, connection, model):
        self.c.busy = True
        self.c.request_tool(str(uuid.uuid4()), "angelo_delegate",
                            {"connection_id": connection, "model": model, "task": "audit"}, lambda result: None)
        with patch("angelo_agent.delegation.ClaudeProvider", ScriptedRuntime):
            self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        call = next(call for call in self.c.pending.values() if call.name == "angelo_delegate")
        started = call.id in self.c.delegate_jobs
        for job in list(self.c.delegate_jobs.values()):
            job.cancel("test cleanup")
        self.c.pending.clear()
        return started, call

    def test_same_connection_fast_helper_runs_under_standing_consent(self):
        self.c.helper_consent = True
        started, call = self.consult(self.lead, "haiku")
        self.assertTrue(started)
        self.assertIn("standing consent", call.usage_label)

    def test_everything_else_still_asks(self):
        self.c.helper_consent = True
        for connection, model in ((self.other, "haiku"), (self.lead, "claude-opus-5-5"), (self.lead, "claude-sonnet-5-5")):
            with self.subTest(connection=connection, model=model):
                started, call = self.consult(connection, model)
                self.assertFalse(started)
        self.c.helper_consent = False
        started, call = self.consult(self.lead, "haiku")
        self.assertFalse(started, "the setting turns standing consent off")


class MeasuredResultsTests(unittest.TestCase):
    def test_runs_become_reviewable_measured_entries(self):
        import measured_results
        def record(task, kind, passed=True, status="completed", wall=4000.0, cost=0.01):
            return {"side": "angelo", "task": task, "kind": kind, "status": status, "host_check_passed": passed,
                    "wall_ms": wall, "provider_usage": {"cost_usd": cost}, "models": ["claude-haiku-5-5"]}
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory) / "haiku-5task"
            run.mkdir()
            (run / "bench-mcp-vs-angelo.json").write_text(json.dumps({
                "meta": {"plan": {"model": "claude-haiku-5-5", "effort": "low"}},
                "records": [record("cube", "edit"), record("bevel", "edit", passed=False),
                            record("move", "edit", status="skipped", passed=None), record("accept", "preview"),
                            record("question", "read_only", cost=0.002),
                            {"side": "mcp", "task": "cube", "kind": "edit", "host_check_passed": False}]}))
            entries = measured_results.measured([run], date="2026-10-08")
        edit = entries["claude-haiku-5-5"]["low"]["scene_edit"]
        self.assertEqual((edit["tasks"], edit["pass_rate"], edit["source"]), (3, round(2 / 3, 4), "haiku-5task"))
        self.assertEqual(entries["claude-haiku-5-5"]["low"]["scene_read"]["cost_usd"], 0.002)
        profile = model_profiles.capability_profile("c", "claude", "SUBSCRIPTION", CATALOG, measured=entries)
        self.assertIsNone(model_routing.auto_route(profile), "3 tasks are far below MIN_TRIALS")


if __name__ == "__main__":
    unittest.main()
