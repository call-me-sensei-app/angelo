# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native child environment hygiene, per-session prompt-cache TTL and route receipts.

Dependency-free: no runtime process, account or network. Claude launches use
the stdio fixture; consultations use a scripted stand-in provider.
"""

import os
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
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, completed  # noqa: E402
from test_native_provider_usage import RecordingRPC, codex_breakdown, token_update  # noqa: E402
from test_native_routing import CLAUDE, GPT, Executor  # noqa: E402
from angelo_agent import provider_registry, runtime_environment  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.providers import CodexProvider, child_environment, profile_environment  # noqa: E402
from angelo_agent.sessions import clean_runtime_sessions  # noqa: E402
from angelo_agent.telemetry import RequestMetrics, cache_gap, clean_receipt  # noqa: E402

ROUTING = {
    # Model choice, aliases, effort and fallback.
    "ANTHROPIC_MODEL": "claude-fable-5-1", "ANTHROPIC_DEFAULT_OPUS_MODEL": "x", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "x",
    "ANTHROPIC_DEFAULT_MODEL": "x", "ANTHROPIC_SMALL_FAST_MODEL": "x", "CLAUDE_CODE_SUBAGENT_MODEL": "x",
    "ANTHROPIC_CUSTOM_MODEL_OPTION": "x", "CLAUDE_CODE_EFFORT_LEVEL": "max", "MAX_THINKING_TOKENS": "1",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
    # Caching and context.
    "DISABLE_PROMPT_CACHING": "1", "DISABLE_PROMPT_CACHING_OPUS": "1", "DISABLE_PROMPT_CACHING_1H": "1",
    "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h",
    "FORCE_PROMPT_CACHING_5M": "1", "ENABLE_PROMPT_CACHING_1H": "1", "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "100000",
    "ENABLE_PROMPT_CACHING_1H_BEDROCK": "1",
    # Undocumented (Claude Code 2.1.301): message breakpoints at 5m under a 1h system prompt.
    "CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL": "5m",
    # Providers, endpoints and request content.
    "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "CLAUDE_CODE_USE_FOUNDRY": "1",
    "ANTHROPIC_VERTEX_PROJECT_ID": "p", "ANTHROPIC_BEDROCK_BASE_URL": "u", "AWS_BEARER_TOKEN_BEDROCK": "t",
    "ANTHROPIC_CUSTOM_HEADERS": "x: y", "CLAUDE_CODE_EXTRA_BODY": "{}", "OPENAI_ORGANIZATION": "org",
    "OPENAI_PROJECT": "proj", "CODEX_OSS_BASE_URL": "http://localhost", "CODEX_ACCESS_TOKEN": "t",
    # An enclosing agent session.
    "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s",
    # Session context Angelo leaves out ("0" would force it back on).
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0", "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "0",
    # Plan streaming (claude_stream): the runtime's default, never an inherited "0".
    "CLAUDE_CODE_ENABLE_FINE_GRAINED_TOOL_STREAMING": "0",
}
NETWORK = {"HTTPS_PROXY": "http://proxy:3128", "https_proxy": "http://proxy:3128", "NO_PROXY": "localhost",
           "NODE_EXTRA_CA_CERTS": "/etc/ca.pem", "SSL_CERT_FILE": "/etc/ca.pem", "CODEX_CA_CERTIFICATE": "/etc/ca.pem",
           "CLAUDE_CODE_CLIENT_CERT": "/etc/client.pem", "PATH": "/usr/bin", "HOME": "/Users/artist"}


class EnvironmentTests(unittest.TestCase):
    def test_routing_caching_and_session_variables_never_reach_a_child(self):
        with patch.dict(os.environ, dict(ROUTING, **NETWORK), clear=True):
            for mode in ("claude_subscription", "claude_api", "claude_team", "codex_subscription", "grok_api"):
                with self.subTest(mode=mode):
                    env = child_environment(mode, "" if mode.endswith("_subscription") else "explicit-key")
                    self.assertFalse(set(ROUTING) & set(env), set(ROUTING) & set(env))
                    # Windows has one case-insensitive variable (os.environ spells it upper case) per name.
                    network = NETWORK if os.name != "nt" else {key.upper(): value for key, value in NETWORK.items()}
                    self.assertEqual({key: env[key] for key in network}, network)

    def test_matching_is_case_insensitive_as_on_windows(self):
        self.assertTrue(runtime_environment.dropped("claude_code_prompt_cache_ttl"))
        self.assertTrue(runtime_environment.dropped("Anthropic_Default_Sonnet_Model_Name"))
        self.assertFalse(runtime_environment.dropped("CLAUDE_CODE_GIT_BASH_PATH"))
        self.assertFalse(any(runtime_environment.dropped(name) for name in runtime_environment.NETWORK))

    def test_ttl_is_applied_only_from_angelos_own_decision(self):
        self.assertEqual(runtime_environment.apply_prompt_cache_ttl({}, "5m"), {"CLAUDE_CODE_PROMPT_CACHE_TTL": "5m"})
        self.assertEqual(runtime_environment.apply_prompt_cache_ttl({"A": "1"}, None), {"A": "1"})
        with self.assertRaises(ValueError):
            runtime_environment.apply_prompt_cache_ttl({}, "30m")

    def test_an_isolated_codex_profile_keeps_its_own_sqlite_state(self):
        with tempfile.TemporaryDirectory() as directory:
            isolated = profile_environment({"CODEX_SQLITE_HOME": "/Users/artist/.codex"}, "codex_api", directory,
                                           False, str(uuid.uuid4()))
            self.assertNotIn("CODEX_SQLITE_HOME", isolated)
            # Reusing the person's own Codex sign-in keeps their own state location.
            reused = profile_environment({"CODEX_SQLITE_HOME": "/Users/artist/.codex"}, "codex_subscription",
                                         directory, True, str(uuid.uuid4()))
            self.assertEqual(reused["CODEX_SQLITE_HOME"], "/Users/artist/.codex")


class ClaudeLaunchTtlTests(ClaudeAdapterFixture, unittest.TestCase):
    def test_launch_sets_the_sessions_ttl_and_a_change_relaunches(self):
        self.host.prompt_cache_ttl = "5m"
        self.turn(None, "First")
        self.assertEqual(self.popen_mock.call_args.kwargs["env"]["CLAUDE_CODE_PROMPT_CACHE_TTL"], "5m")
        self.assertEqual(self.provider.env, {"FIXTURE": "only"}, "the connection's base environment is not mutated")
        self.host.prompt_cache_ttl = "1h"
        self.popen_mock.return_value = NativeInitializeFixture(
            on_user=lambda process, _: process.stdout.emit(completed()))
        self.turn("fixture-native", "Second")
        self.assertEqual(self.popen_mock.call_count, 2)
        self.assertEqual(self.popen_mock.call_args.kwargs["env"]["CLAUDE_CODE_PROMPT_CACHE_TTL"], "1h")

    def test_no_decision_leaves_claude_codes_default(self):
        self.turn(None, "First")
        self.assertNotIn("CLAUDE_CODE_PROMPT_CACHE_TTL", self.popen_mock.call_args.kwargs["env"])
        self.assertNotIn("CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK", self.popen_mock.call_args.kwargs["env"],
                         "people keep Claude Code's refusal fallback")

    def test_measurement_runs_stay_on_the_requested_model(self):
        self.host.single_model = True
        self.turn(None, "First")
        self.assertEqual(self.popen_mock.call_args.kwargs["env"]["CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK"], "1")

    def test_every_session_leaves_out_auto_memory_and_the_git_snapshot(self):
        """Both made up about half of a new chat's first cache write (4,009 tokens measured), so neither is sent."""
        # A connection environment that would force both back on: the session's own values win.
        self.provider.env = {"FIXTURE": "only", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0",
                             "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "0"}
        self.host.prompt_cache_ttl = "1h"
        self.turn(None, "First")
        env = self.popen_mock.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"], "1")
        self.assertEqual(env["CLAUDE_CODE_PROMPT_CACHE_TTL"], "1h")
        self.assertEqual(self.provider.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "0",
                         "the connection's base environment is not mutated")
        self.assertEqual(runtime_environment.session_environment({}, object()), runtime_environment.SESSION_CONTEXT)


class SessionTtlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.connection = str(uuid.uuid4())
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.connection, CLAUDE, "sonnet")

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def entry(self):
        return self.c.state["runtime_sessions"].setdefault(self.connection, {})

    def test_new_sessions_follow_the_session_kind_and_a_harness_override(self):
        self.assertEqual(self.c.session_cache_ttl(), "1h")
        self.c.session_kind = "autonomous"
        self.assertEqual(self.c.session_cache_ttl(), "5m")
        self.c.session_kind, self.c.prompt_cache_ttl_override = "interactive", "5m"
        self.assertEqual(self.c.session_cache_ttl(), "5m")
        self.assertEqual(self.c.runtime_context().prompt_cache_ttl, "5m")

    def claude_routes(self):
        """Every Claude route, with Claude Code's own main-conversation default on it.

        A route whose billing method has no entry here fails the test: whoever
        adds one decides its TTL.
        """
        defaults = {"SUBSCRIPTION": "1h", "API_KEY": "5m", provider_registry.TEAM: "5m"}
        routes = {mode: defaults[spec["method"]] for mode, spec in provider_registry.SPECS.items()
                  if spec["runtime"] == "claude"}
        self.assertLessEqual({"claude_subscription", "claude_api", "claude_team"}, set(routes))
        return routes

    def use(self, mode):
        key = "" if mode == "claude_subscription" else "test-key"
        self.c.choose_connection(mode, {"model": ""}, key, str(uuid.uuid4()), CLAUDE, "sonnet")

    def test_new_sessions_match_claude_codes_default_on_every_claude_route(self):
        """Interactive: 1h on a subscription, 5m on an API key or a team's gateway; runs without pauses: 5m."""
        for mode, interactive in self.claude_routes().items():
            with self.subTest(mode=mode):
                self.use(mode)
                self.assertIsNone(self.c.state["native_id"])
                self.c.session_kind = "interactive"
                self.assertEqual(self.c.session_cache_ttl(), interactive)
                self.assertEqual(self.c.runtime_context().prompt_cache_ttl, interactive)
                for kind in ("autonomous", "benchmark", "subagent"):
                    self.c.session_kind = kind
                    self.assertEqual(self.c.session_cache_ttl(), "5m")
                self.c.session_kind = "interactive"

    def test_each_route_launches_with_its_own_ttl_whatever_the_person_inherited(self):
        """The whole launch environment: inherited TTL variables never reach the session, Angelo's value does."""
        inherited = {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "ENABLE_PROMPT_CACHING_1H": "1",
                     "FORCE_PROMPT_CACHING_5M": "1", "CLAUDE_CODE_MESSAGES_PROMPT_CACHE_TTL": "5m",
                     "PATH": "/usr/bin"}
        for mode, interactive in self.claude_routes().items():
            with self.subTest(mode=mode), patch.dict(os.environ, inherited, clear=True):
                self.use(mode)
                env = runtime_environment.session_environment(child_environment(mode, self.c.api_key),
                                                              self.c.runtime_context())
                self.assertEqual(env["CLAUDE_CODE_PROMPT_CACHE_TTL"], interactive)
                self.assertFalse((set(inherited) - {"PATH", "CLAUDE_CODE_PROMPT_CACHE_TTL"}) & set(env))

    def test_an_existing_key_billed_session_keeps_its_recorded_ttl(self):
        """Chats started while Angelo chose 1h on every route keep it: a mid-session switch partly misses the cache."""
        for mode in ("claude_api", "claude_team"):
            with self.subTest(mode=mode):
                self.use(mode)
                self.c.state["native_id"] = "native-" + mode
                self.c.state["runtime_sessions"][self.c.context_key()] = {"native_id": "native-" + mode,
                                                                         "toolset_version": 18, "cache_ttl": "1h"}
                self.assertEqual(self.c.session_cache_ttl(), "1h", "the recorded TTL wins over today's policy")
                self.assertEqual(self.c.runtime_context().prompt_cache_ttl, "1h")
                self.c.remember_runtime()
                entry = self.c.state["runtime_sessions"][self.c.context_key()]
                self.assertEqual(entry["cache_ttl"], "1h", "a session never changes TTL")
                entry.pop("cache_ttl")
                self.assertEqual(self.c.session_cache_ttl(), "5m", "an unrecorded one was written at Claude Code's 5m")
                self.c.state["native_id"] = None
                self.assertEqual(self.c.session_cache_ttl(), "5m", "a new session on the route follows today's policy")

    def test_a_resumed_session_keeps_its_recorded_or_previous_ttl(self):
        self.c.state["native_id"] = "native-1"
        self.entry().update(native_id="native-1", cache_ttl="5m")
        self.assertEqual(self.c.session_cache_ttl(), "5m", "recorded TTL wins over today's policy")
        self.entry().pop("cache_ttl")
        self.assertEqual(self.c.session_cache_ttl(), "1h", "a subscription session from before kept Claude Code's 1h")
        self.c.state["provider"] = "claude_api"
        self.c.state["runtime_sessions"]["claude_api"] = {"native_id": "native-1"}
        self.c.state["connection_id"] = None
        self.assertEqual(self.c.session_cache_ttl(), "5m", "API-key sessions were written at 5 minutes")
        self.c.state["provider"] = "codex_subscription"
        self.assertIsNone(self.c.session_cache_ttl())

    def test_the_ttl_is_recorded_with_the_new_native_session_and_persists(self):
        self.c.prompt_cache_ttl_override = "5m"
        self.c.runtime_context()
        self.c.emit("native_session", {"id": "native-2", "model": "claude-sonnet-5-5"})
        self.c.pump_events()
        self.assertEqual(self.entry()["cache_ttl"], "5m")
        self.c.prompt_cache_ttl_override = "1h"
        self.c.remember_runtime()
        self.assertEqual(self.entry()["cache_ttl"], "5m", "a session never changes TTL")
        cleaned = clean_runtime_sessions({self.connection: dict(self.entry(), toolset_version=18)})
        self.assertEqual(cleaned[self.connection]["cache_ttl"], "5m")
        self.assertNotIn("cache_ttl", clean_runtime_sessions(
            {self.connection: {"native_id": "n", "toolset_version": 18, "cache_ttl": "30m"}})[self.connection])


class RouteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.connection = str(uuid.uuid4())
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.connection, CLAUDE, "")
        self.c.set_options(effort="minimal", fast=False)

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def begin(self):
        self.c.turn_options = dict(self.c.state["options"])
        self.c._begin_metrics()
        self.c._record_route()
        return self.c._request_metrics

    def test_route_names_mode_model_effort_sent_ttl_and_gap(self):
        route = self.begin().snapshot()["route"]
        self.assertEqual(route, {"mode": "claude_subscription", "requested_model": "", "effort": "low",
                                 "fast": False, "cache_ttl": "1h", "cache_gap": "new_session",
                                 "auto": False, "escalation": 0, "single_model": False})
        self.c.single_model_runs = True
        self.assertTrue(self.begin().snapshot()["route"]["single_model"])
        self.assertTrue(self.c.runtime_context().single_model)

    def test_cache_gaps_are_bucketed_per_native_session(self):
        self.assertEqual(cache_gap(None, None, 10), "new_session")
        self.assertEqual(cache_gap(None, "n", 10), "unknown")
        self.assertEqual(cache_gap(("other", 0), "n", 10), "unknown")
        self.assertEqual(cache_gap(("n", 0), "n", 299), "under_5m")
        self.assertEqual(cache_gap(("n", 0), "n", 300), "5m_to_1h")
        self.assertEqual(cache_gap(("n", 0), "n", 3600), "over_1h")
        # A finished model request records when the session was last used.
        self.c.state["native_id"] = "native-3"
        self.begin()
        self.c.emit("finished", {"status": "completed"})
        self.c.pump_events()
        self.assertEqual(self.begin().snapshot()["route"]["cache_gap"], "under_5m")

    def test_codex_routes_name_the_effort_codex_sends(self):
        self.c.choose_connection("codex_subscription", {"model": ""}, "", str(uuid.uuid4()), GPT, "gpt-test")
        self.c.set_options(effort="medium")
        route = self.begin().snapshot()["route"]
        self.assertEqual((route["mode"], route["requested_model"], route["effort"]), ("codex_subscription", "gpt-test", "low"))
        self.assertNotIn("cache_ttl", route)

    def test_receipts_keep_only_allowlisted_route_values(self):
        base = {"status": "completed", "phases": {"total_ms": 1}}
        receipt = clean_receipt(dict(base, route={"mode": "claude_api", "requested_model": "claude-haiku-5-5",
                                                  "effort": "low", "fast": False, "cache_ttl": "5m",
                                                  "cache_gap": "under_5m", "served_models": ["claude-haiku-5-5"]}))
        self.assertEqual(receipt["route"]["served_models"], ["claude-haiku-5-5"])
        hostile = clean_receipt(dict(base, route={"mode": "anything", "requested_model": "has spaces in it",
                                                  "effort": "turbo", "cache_ttl": "30m", "cache_gap": 5,
                                                  "served_models": ["ok-model", "bad model", {}]}))
        self.assertEqual(hostile["route"], {"served_models": ["ok-model"]})

    def test_served_models_come_from_provider_usage(self):
        metrics = RequestMetrics(runtime="claude")
        metrics.count("provider_turn_requests")
        metrics.provider_usage({"input_tokens": 10, "models": ["claude-opus-5-5", "claude-opus-5-5", "bad model"]})
        self.assertEqual(metrics.finish("completed")["route"], {"served_models": ["claude-opus-5-5"]})


class CodexServedModelTests(unittest.TestCase):
    def test_the_turns_model_is_named_in_its_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            c = Controller(directory)
            c.set_document(("factory-runtime", 17))
            c.state["native_id"] = "native-thread"
            c._begin_metrics()
            c.busy, c.cancelled = True, False
            context = c.runtime_context()
            provider = CodexProvider(context, {})
            provider.rpc = RecordingRPC()
            provider.bind_request(context)
            provider._usage.thread_started("native-thread")

            def start(method, parameters, **kwargs):
                provider._event("thread/tokenUsage/updated", token_update("native-thread", "t", codex_breakdown(100, 0, 5)))
                provider._event("turn/completed", {"threadId": "native-thread", "turn": {
                    "id": "t", "status": "completed", "durationMs": 10}})
                return {"turn": {"id": "t"}}
            provider.rpc.request = start
            with patch("angelo_agent.providers.codex_turn_options",
                       return_value={"model": "gpt-6-luna", "effort": "low", "serviceTierForTurn": "default"}):
                self.assertEqual(provider.run("native-thread", "Offline fixture"), "completed")
            self.assertEqual(c._request_metrics.snapshot()["route"]["served_models"], ["gpt-6-luna"])
            provider.close()
            c.close()


class ScriptedChild:
    """A consultation runtime that reports two provider turns of usage."""

    def __init__(self, context, settings):
        self.controller = context

    def connect(self):
        return "child-native"

    def run(self, native_id, text, observation=None):
        self.controller.record_provider_usage({"input_tokens": 3000, "cached_input_tokens": 0,
                                               "cache_write_input_tokens": 2900, "output_tokens": 40,
                                               "cost_usd": 0.0006, "models": ["claude-haiku-5-5"]})
        self.controller.record_provider_usage({"input_tokens": 3100, "cached_input_tokens": 2900,
                                               "cache_write_input_tokens": 150, "output_tokens": 60, "cost_usd": 0.0002})
        self.controller.emit("message_complete", {"item": "reply", "text": "Audit B and Audit D differ"})
        return "completed"

    def stop(self):
        pass

    def close(self):
        pass


class ConsultationReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.c = Controller(self.directory.name)
        self.c.set_document(("scratch", 1))
        self.lead, self.executor = str(uuid.uuid4()), Executor()
        self.c.choose_connection("claude_subscription", {"model": ""}, "", self.lead, CLAUDE, "sonnet")
        haiku = [dict(CLAUDE[0], id="claude-haiku-5-5", name="Haiku", default=False)]
        self.c.delegation_resolver = lambda identity: {"provider": "claude_subscription", "settings": {}, "api_key": "",
                                                       "name": "My Claude", "models": haiku}

    def tearDown(self):
        self.c.close()
        self.directory.cleanup()

    def test_consultation_usage_is_attributed_to_the_parent_request(self):
        self.c.turn_options = dict(self.c.state["options"])
        self.c._begin_metrics()
        self.c.busy = True
        replies = []
        self.c.request_tool("consult", "angelo_delegate", {"connection_id": self.lead, "model": "claude-haiku-5-5",
                                                           "task": "Which Audit bevels differ?"}, replies.append)
        self.c.pump_tools(self.executor, self.c.document, budget=ToolDispatchBudget(seconds=60))
        call = next(call for call in self.c.pending.values() if call.name == "angelo_delegate")
        with patch("angelo_agent.delegation.ClaudeProvider", ScriptedChild):
            self.c.execute_tool(call, self.executor, self.c.document)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and self.c.delegate_jobs:
                self.c.pump_events()
                time.sleep(0.003)
        self.assertIn("reply", replies[0])
        receipt = self.c._request_metrics.finish("completed")
        self.assertNotIn("usage", receipt, "the lead's own usage never includes the consultation")
        consultation, = receipt["consultations"]
        self.assertEqual({key: consultation[key] for key in ("mode", "requested_model", "effort", "cache_ttl")},
                         {"mode": "claude_subscription", "requested_model": "claude-haiku-5-5", "effort": "low",
                          "cache_ttl": "5m"})
        self.assertEqual(consultation["served_models"], ["claude-haiku-5-5"])
        self.assertEqual(consultation["usage"]["turns"], 2)
        self.assertEqual(consultation["usage"]["cache_write_input_tokens"], 3050)
        self.assertAlmostEqual(consultation["usage"]["cost_usd"], 0.0008)
        self.assertNotIn("Audit", str(receipt), "receipts carry no task or reply text")
        self.c.telemetry.record(receipt)
        report = self.c.metrics_report()["consultations"]
        self.assertEqual((report["consultations"], report["usage"]["output_tokens"]["total"]), (1, 100))

    def test_metrics_bound_consultations_and_ignore_late_usage(self):
        metrics = RequestMetrics()
        for index in range(6):
            metrics.consultation_usage("c%d" % index, {"mode": "claude_api"}, {"input_tokens": 1})
        metrics.finish("completed")
        metrics.consultation_usage("late", {"mode": "claude_api"}, {"input_tokens": 1})
        self.assertEqual(len(metrics.consultations), 4)


if __name__ == "__main__":
    unittest.main()
