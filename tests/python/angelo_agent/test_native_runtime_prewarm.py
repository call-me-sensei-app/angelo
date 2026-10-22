# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The runtime started while a chat's message is written (runtime_prewarm); no process, credentials or inference.

The real Controller, ClaudeProvider and CodexProvider run. Claude Code is the
stdio fixture (``claude_protocol_fixtures``) and its version and sign-in probes
are canned; Codex is a fake app-server. What is pinned: nothing is sent before
the send (no user message, thread or turn); the send adopts the runtime instead
of launching another; a change to what the launch depends on, an emptied draft,
an idle draft, a new chat, a closed chat and another chat's draft each close it;
and a failed prewarm shows nothing.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from claude_protocol_fixtures import NativeInitializeFixture, completed  # noqa: E402
from angelo_agent import debug_thinking, providers, runtime_prewarm  # noqa: E402
from angelo_agent.chat_settings import normalize_models  # noqa: E402
from angelo_agent.cli_runtime import RUNTIMES, format_version  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from test_native_codex_runtime import FakeProcess, threads_counter, wait_for  # noqa: E402

CLAUDE = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                            "supportedEffortLevels": ["low", "medium", "high"]}], "claude")
SUPPORTED = format_version(RUNTIMES["claude"]["floor"]) + " (Claude Code)\n"
SIGNED_IN = json.dumps({"loggedIn": True, "subscriptionType": "max", "authMethod": "claude.ai"})


class ClaudePrewarmTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.launches, self.probes, self.version, self.probe_delay = [], [], SUPPORTED, 0.0
        self.chats = []
        self.c = self.chat()
        for value in (patch.object(providers, "find_executable", return_value="claude"),
                      patch("angelo_agent.child_process.popen", side_effect=self.launch),
                      patch("angelo_agent.child_process.run", side_effect=self.probe)):
            value.start()
            self.addCleanup(value.stop)

    def chat(self):
        controller = Controller(self.directory.name)
        self.addCleanup(controller.close)
        controller.set_document(("scratch", 1))
        controller.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.chats.append(controller)
        runtime_prewarm.observe(controller, "", self.chats, 0.0)  # The new chat's empty composer.
        return controller

    def launch(self, *args, **kwargs):
        process = NativeInitializeFixture(on_user=lambda process, _event: process.stdout.emit(completed()))
        process.launched_with = (list(args[0]) if args else [], dict(kwargs.get("env") or {}))
        self.launches.append(process)
        return process

    def probe(self, command, *args, **kwargs):
        self.probes.append(command[1:])
        if command[1:] == ["auth", "status"]:
            return SimpleNamespace(returncode=0, stdout=SIGNED_IN)
        time.sleep(self.probe_delay)
        return SimpleNamespace(returncode=0, stdout=self.version)

    def observe(self, controller, draft, now=0.0):
        runtime_prewarm.observe(controller, draft, self.chats, now)

    def warm(self, controller):
        self.assertTrue(wait_for(lambda: controller.provider is not None and controller.provider.has_warm_runtime()),
                        "The prewarmed session never became warm")

    def send(self, controller, text):
        controller.send(text, {"objects": []})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            controller.pump_events()
            if not controller.busy and not (controller._worker and controller._worker.is_alive()):
                controller.pump_events()
                return
            time.sleep(.002)
        self.fail("Claude fixture turn did not finish")

    def closed(self, process):
        return wait_for(lambda: process.poll() is not None)

    def test_typing_launches_the_session_and_nothing_is_sent_until_the_send(self):
        self.observe(self.c, "Make a 2 m cube")
        self.warm(self.c)
        process = self.launches[0]
        self.assertIsNotNone(process.initialized_schema)  # Initialized: the plan schema and instructions are in.
        time.sleep(.1)
        self.observe(self.c, "Make a 2 m cube named Box", now=1.0)
        self.assertFalse(process.user_written.is_set(), "No message before the send: nothing billed")
        self.assertEqual([m["type"] for m in process.stdin.messages], ["control_request"])
        self.send(self.c, "Make a 2 m cube named Box")
        self.assertEqual(len(self.launches), 1, "The send adopted the session instead of launching another")
        self.assertTrue(process.user_written.is_set())
        self.assertEqual(self.probes.count(["auth", "status"]), 1, "The sign-in check ran once, in the prewarm")
        self.assertEqual(self.c._request_metrics.counts.get("runtime_prewarmed"), 1)
        self.assertTrue(self.c._request_metrics.warm)
        self.assertIsNone(self.c.prewarmed)

    def test_a_send_during_the_launch_waits_for_it_rather_than_launching_another(self):
        self.probe_delay = .3
        self.observe(self.c, "Make a cube")
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 1)
        self.assertTrue(self.launches[0].user_written.is_set())

    def test_an_options_change_closes_the_session_and_the_next_tick_launches_a_matching_one(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        self.c.set_options(approval="ASK")   # An option the launch matches; effort is not one.
        self.observe(self.c, "Make a cube", now=1.0)
        self.assertTrue(self.closed(first), "The session launched for the old options was closed")
        self.warm(self.c)
        self.assertEqual(len(self.launches), 2)
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 2)
        self.assertFalse(first.user_written.is_set())
        self.assertTrue(self.launches[1].user_written.is_set())

    def test_another_connection_closes_the_session_and_launches_one_for_the_new_connection(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        # As AI Providers' record gives it (ui.connection_settings): its own profile, which a key launch requires
        # (runtime_environment.require_angelo_profile, from the web-tools fix).
        identity = "6f1c1f9e-5c43-4f61-9a43-0d3d2b8d7c10"
        self.c.choose_connection("claude_api", {"model": "", "reuse_codex_login": False, "connection_id": identity},
                                 "fixture-key", identity, CLAUDE, "sonnet")
        self.assertTrue(self.closed(first))
        self.observe(self.c, "Make a cube", now=1.0)
        self.warm(self.c)
        self.assertEqual(len(self.launches), 2)
        self.assertEqual(self.c.prewarmed.key[1], "claude_api")
        self.assertFalse(first.user_written.is_set())
        # The key route's launch is the web-tools one: never bare, web tools on, in the connection's own profile.
        command, env = self.launches[1].launched_with
        self.assertNotIn("--bare", command)
        self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
        self.assertTrue(env["CLAUDE_CONFIG_DIR"].endswith(identity), env.get("CLAUDE_CONFIG_DIR"))

    def test_a_key_chat_without_its_own_profile_never_launches_and_the_send_says_why(self):
        # A key typed into the legacy per-mode operator has no connection record (require_angelo_profile).
        self.c.choose_connection("claude_api", {"model": ""}, "fixture-key", None, CLAUDE, "sonnet")
        self.observe(self.c, "Make a cube")
        self.assertTrue(wait_for(lambda: self.c.provider is None or not self.c.provider._launch.running))
        self.assertEqual(self.launches, [])
        self.send(self.c, "Make a cube")
        errors = [m["text"] for m in self.c.state["messages"] if m.get("role") == "error"]
        self.assertTrue(errors and "AI Providers" in errors[-1], errors)
        self.assertEqual(self.launches, [])

    @patch.object(debug_thinking, "SHOWN_TO_EVERYONE", False)   # Debug mode's own effect, with the display off.
    def test_debug_thinking_is_part_of_the_launch_key(self):
        # A diagnostic run's thinking display is a launch argument (debug_thinking): the tick that sees it switch
        # releases the runtime launched without it, and the next one carries it, which the send then adopts.
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        self.assertNotIn("--thinking-display", first.launched_with[0])
        self.c.debug_thinking = True
        self.observe(self.c, "Make a cube", now=1.0)
        self.assertTrue(self.closed(first))
        self.warm(self.c)
        command = self.launches[1].launched_with[0]
        self.assertEqual(command[command.index("--thinking-display") + 1], "summarized")
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 2)
        self.assertFalse(first.user_written.is_set())
        self.assertTrue(self.launches[1].user_written.is_set())

    def test_a_runtime_launched_without_summaries_is_never_used_for_a_debug_send(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        self.c.debug_thinking = True   # The send comes before the next tick.
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 2, "Relaunched for the debug run")
        self.assertFalse(first.user_written.is_set())
        command = self.launches[1].launched_with[0]
        self.assertEqual(command[command.index("--thinking-display") + 1], "summarized")

    def test_the_cache_ttl_is_part_of_the_launch_key(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        ttl = self.c.session_cache_ttl()
        other = "5m" if ttl == "1h" else "1h"
        with patch.object(Controller, "session_cache_ttl", return_value=other):
            self.observe(self.c, "Make a cube", now=1.0)
            self.assertTrue(self.closed(first))
            self.warm(self.c)
        self.assertEqual(self.launches[1].launched_with[1].get("CLAUDE_CODE_PROMPT_CACHE_TTL"), other)

    def test_the_team_library_tools_are_part_of_the_launch_key(self):
        # Offered only while the team account is signed in (team_library_access): the plan schema differs.
        from angelo_agent import team_library_access
        with patch.object(team_library_access, "enabled", return_value=False):
            self.observe(self.c, "Make a cube")
            self.warm(self.c)
        first = self.launches[0]
        with patch.object(team_library_access, "enabled", return_value=True):
            self.observe(self.c, "Make a cube", now=1.0)
            self.assertTrue(self.closed(first))
            self.warm(self.c)
            self.assertEqual(len(self.launches), 2)
        self.assertNotEqual(json.dumps(first.initialized_schema, sort_keys=True),
                            json.dumps(self.launches[1].initialized_schema, sort_keys=True))

    def test_a_change_no_tick_saw_is_caught_at_the_send(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first = self.launches[0]
        self.c.set_options(approval="ASK")  # The send comes before the next tick.
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 2, "The launch no longer matched the request: relaunched")
        self.assertTrue(self.closed(first))
        self.assertFalse(first.user_written.is_set())
        self.assertTrue(self.launches[1].user_written.is_set())

    def test_an_effort_change_keeps_the_session_and_the_send_applies_it_before_the_message(self):
        """Effort is a request parameter: Claude Code changes a running session's (claude_transport.apply_effort)."""
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        first, launch_key = self.launches[0], self.c.prewarmed.key
        self.assertIn(["--effort", "low"], [first.launched_with[0][i:i + 2] for i in range(len(first.launched_with[0]))])
        self.c.set_options(effort="high")
        self.observe(self.c, "Make a cube", now=1.0)
        self.assertEqual(self.c.prewarmed.key, launch_key, "Effort is no input of the launch key")
        self.assertIsNone(first.poll(), "The session started for another effort stays")
        self.send(self.c, "Make a cube")
        self.assertEqual(len(self.launches), 1, "No relaunch for an effort change")
        self.assertEqual(first.flag_settings, [{"effortLevel": "high"}])
        kinds = [m["type"] if m["type"] != "control_request" else m["request"]["subtype"] for m in first.stdin.messages]
        self.assertLess(kinds.index("apply_flag_settings"), kinds.index("user"), "Set before the message")
        self.assertEqual(self.c.provider._transport.effort, "high")

    def test_the_same_effort_sends_no_change(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        self.send(self.c, "Make a cube")
        self.assertEqual(self.launches[0].flag_settings, [])

    def test_a_session_that_refuses_or_ignores_the_change_is_relaunched_with_the_effort(self):
        from angelo_agent import claude_transport
        for answer, effort in (("error", "high"), (None, "medium")):   # A new chat keeps the last chat's options.
            with self.subTest(answer=answer), patch.object(claude_transport, "EFFORT_SECONDS", .2):
                self.launches.clear()
                chat = self.chat()
                self.observe(chat, "Make a cube")
                self.warm(chat)
                first = self.launches[0]
                first.flag_settings_answer = answer
                chat.set_options(effort=effort)
                self.send(chat, "Make a cube")
                self.assertEqual(first.flag_settings, [{"effortLevel": effort}])
                self.assertTrue(self.closed(first))
                self.assertFalse(first.user_written.is_set())
                self.assertEqual(len(self.launches), 2)
                command = self.launches[1].launched_with[0]
                self.assertEqual(command[command.index("--effort") + 1], effort)
                self.assertTrue(self.launches[1].user_written.is_set())

    def test_a_session_launched_for_another_native_session_is_never_used_for_this_one(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        provider, first = self.c.provider, self.launches[0]
        context = runtime_prewarm.context(self.c)
        context.state["native_id"] = "another-session"
        from angelo_agent.claude_transport import ClaudeRequest
        provider.bind_request(context)
        provider._turn("another-session", "Hello", owner=ClaudeRequest(context))
        self.assertEqual(len(self.launches), 2)
        self.assertFalse(first.user_written.is_set())

    def test_emptying_the_draft_closes_the_session(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        self.observe(self.c, "  ", now=1.0)
        self.assertIsNone(self.c.prewarmed)
        self.assertIsNone(self.c.provider)
        self.assertTrue(self.closed(self.launches[0]))
        self.assertFalse(self.launches[0].user_written.is_set())

    def test_an_idle_draft_is_released_and_a_changing_one_is_kept(self):
        self.observe(self.c, "Make a cube", now=0.0)
        self.warm(self.c)
        self.observe(self.c, "Make a cube and", now=runtime_prewarm.IDLE_SECONDS - 1)
        self.observe(self.c, "Make a cube and", now=2 * runtime_prewarm.IDLE_SECONDS - 2)
        self.assertIsNotNone(self.c.prewarmed, "Writing keeps it")
        self.observe(self.c, "Make a cube and", now=2 * runtime_prewarm.IDLE_SECONDS)
        self.assertIsNone(self.c.prewarmed)
        self.assertTrue(self.closed(self.launches[0]))
        self.observe(self.c, "Make a cube and", now=2 * runtime_prewarm.IDLE_SECONDS + 1)
        self.assertIsNone(self.c.provider, "An unchanged draft starts nothing again")
        self.assertEqual(len(self.launches), 1)
        # A closed chat editor (no draft shown) is bound by the same idle time, never kept.
        self.observe(self.c, "Make a cube and a sphere", now=1000.0)
        self.warm(self.c)
        self.observe(self.c, None, now=1000.0 + runtime_prewarm.IDLE_SECONDS)
        self.assertIsNone(self.c.prewarmed)

    def test_a_new_chat_or_a_closed_chat_closes_the_session(self):
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        self.c.new_chat(("scratch", 1))
        self.assertTrue(self.closed(self.launches[0]))
        self.observe(self.c, "", now=1.0)
        self.assertIsNone(self.c.prewarmed)
        self.observe(self.c, "Make a sphere", now=2.0)
        self.warm(self.c)
        self.c.close()
        self.assertTrue(self.closed(self.launches[1]))

    def test_writing_in_a_second_chat_releases_the_first_chats_session(self):
        other = self.chat()
        self.observe(self.c, "Make a cube")
        self.warm(self.c)
        self.observe(other, "Make a sphere", now=1.0)
        self.assertIsNone(self.c.prewarmed)
        self.assertIsNone(self.c.provider)
        self.assertTrue(self.closed(self.launches[0]))
        self.warm(other)
        self.assertEqual(len([c for c in self.chats if c.prewarmed is not None]), runtime_prewarm.MAX_PREWARMED)
        self.send(other, "Make a sphere")
        self.assertEqual(len(self.launches), 2)
        self.assertTrue(self.launches[1].user_written.is_set())

    def test_a_draft_only_shown_starts_nothing_until_it_is_written(self):
        restored = Controller(self.directory.name)
        self.addCleanup(restored.close)
        restored.set_document(("scratch", 1))
        restored.choose_connection("claude_subscription", {"model": ""}, "", None, CLAUDE, "sonnet")
        self.chats.append(restored)
        self.observe(restored, "A draft restored at launch")
        self.observe(restored, "A draft restored at launch", now=1.0)
        self.assertIsNone(restored.provider)
        self.observe(restored, "A draft restored at launch, edited", now=2.0)
        self.warm(restored)

    def test_no_runtime_starts_for_a_chat_that_cannot_send_now(self):
        cases = {"busy": lambda c: setattr(c, "busy", True),
                 "external client": lambda c: setattr(c, "external_client", "Terminal"),
                 "no connection": lambda c: setattr(c, "settings", {}),
                 "queued connection change": lambda c: setattr(c, "_next_binding", {"provider": "claude_api"}),
                 "changed document": lambda c: setattr(c, "document_invalid", True)}
        for name, change in cases.items():
            with self.subTest(name):
                chat = self.chat()
                change(chat)
                self.observe(chat, "Make a cube")
                self.assertIsNone(chat.prewarmed)
                self.assertIsNone(chat.provider)
        self.assertEqual(self.launches, [])

    def test_a_failed_prewarm_shows_nothing_and_the_send_reports_it(self):
        floor = RUNTIMES["claude"]["floor"]
        self.version = format_version(floor[:2] + (floor[2] - 1,)) + " (Claude Code)\n"
        self.observe(self.c, "Make a cube")
        self.assertTrue(wait_for(lambda: not self.c.provider._launch.running))
        self.c.pump_events()
        self.assertEqual([m for m in self.c.state["messages"] if m.get("role") in {"error", "system"}], [])
        self.assertEqual(self.launches, [])
        self.send(self.c, "Make a cube")
        errors = [m["text"] for m in self.c.state["messages"] if m.get("role") == "error"]
        self.assertTrue(errors and "Claude Code" in errors[-1] and "or newer" in errors[-1], errors)
        self.assertEqual(self.launches, [])


class CodexPrewarmTests(unittest.TestCase):
    """A team Codex chat has an app-server of its own: the prewarm starts it; the thread waits for the send."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.processes, self.environment = [], {"FIXTURE": "1"}
        self.answers = {"initialize": {"userAgent": "angelo_blender/" + format_version(RUNTIMES["codex"]["floor"])
                                       + " (Mac OS; arm64)"},
                        "config/read": {"config": {}}, "thread/start": threads_counter("chat")}

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            self.processes.append(FakeProcess(on_event, on_request, self.answers))
            return self.processes[-1]
        for value in (patch.object(providers, "find_executable", return_value="/fixture/codex"),
                      patch.object(providers, "codex_command",
                                   side_effect=lambda *a, **k: (["/fixture/codex", "app-server"], dict(self.environment))),
                      patch.object(providers, "JsonRpcProcess", process)):
            value.start()
            self.addCleanup(value.stop)

    def chat(self, mode="codex_team"):
        controller = Controller(self.temp.name)
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider=mode, native_id=None, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": "connection", "model": ""}
        controller.api_key = "fixture-grant"
        runtime_prewarm.observe(controller, "", [controller], 0.0)
        return controller

    def started(self, controller):
        runtime_prewarm.observe(controller, "Make a cube", [controller], 0.0)
        self.assertIsNotNone(controller.prewarmed)
        self.assertTrue(wait_for(lambda: not controller.provider._launch.running))
        return controller.provider

    def test_only_the_app_server_starts_and_the_send_opens_the_thread_on_it(self):
        controller = self.chat()
        provider = self.started(controller)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0].methods(), ["initialize"], "No thread and no turn before the send")
        self.assertTrue(provider.needs_connect)
        controller._begin_metrics()
        native = controller._connect_runtime(controller.runtime_context())
        self.assertEqual(native, "chat-1")
        self.assertEqual(len(self.processes), 1, "The send adopted the app-server")
        self.assertEqual(self.processes[0].methods(), ["initialize", "config/read", "thread/start"])
        self.assertNotIn("turn/start", self.processes[0].methods())

    def test_a_chat_with_a_thread_still_connects_its_prewarmed_app_server(self):
        controller = self.chat()
        controller.state["native_id"] = "earlier-thread"
        self.answers["thread/resume"] = {"thread": {"id": "earlier-thread"}}
        self.started(controller)
        controller._begin_metrics()
        self.assertEqual(controller._connect_runtime(controller.runtime_context()), "earlier-thread")
        self.assertEqual(self.processes[0].methods(), ["initialize", "config/read", "thread/resume"])

    def test_a_changed_launch_closes_the_prewarmed_app_server(self):
        controller = self.chat()
        self.started(controller)
        self.environment = {"FIXTURE": "2"}  # Its key or profile changed after the prewarm.
        controller._begin_metrics()
        controller._connect_runtime(controller.runtime_context())
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(self.processes[0].closed.is_set())
        self.assertEqual(self.processes[0].methods(), ["initialize"])

    def test_a_gateway_that_starts_naming_zstd_replaces_the_prewarmed_app_server(self):
        # What the gateway decodes is learned by a request, so it is not in the tick's key; the connect compares the
        # whole launch (runtime_launch.launch_binding), and a team route that may now compress gets a new app-server.
        from angelo_agent import gateway_capabilities, team_endpoints, team_runtime
        encodings = [frozenset()]
        command = lambda *a, **k: (["/fixture/codex", "app-server", "-c",  # noqa: E731
                                    "compression=%s" % team_runtime.request_compression("codex_team")],
                                   dict(self.environment))
        with patch.object(providers, "codex_command", side_effect=command), \
                patch.object(team_endpoints, "gateway_origin", return_value=SimpleNamespace(url="https://gateway.test")), \
                patch.object(gateway_capabilities, "request_encodings", side_effect=lambda origin: encodings[0]):
            controller = self.chat()
            self.started(controller)
            encodings[0] = frozenset({"zstd"})
            controller._begin_metrics()
            controller._connect_runtime(controller.runtime_context())
        self.assertEqual(len(self.processes), 2)
        self.assertTrue(self.processes[0].closed.is_set())
        self.assertEqual(self.processes[0].methods(), ["initialize"])

    def test_emptying_the_draft_closes_the_app_server(self):
        controller = self.chat()
        self.started(controller)
        runtime_prewarm.observe(controller, "", [controller], 1.0)
        self.assertIsNone(controller.provider)
        self.assertTrue(wait_for(self.processes[0].closed.is_set))

    def test_a_chatgpt_plan_chat_starts_nothing_its_shared_app_server_is_started_by_its_opening(self):
        controller = self.chat("codex_subscription")
        runtime_prewarm.observe(controller, "Make a cube", [controller], 0.0)
        self.assertIsNone(controller.prewarmed)
        self.assertIsNone(controller.provider)
        self.assertEqual(self.processes, [])


if __name__ == "__main__":
    unittest.main()
