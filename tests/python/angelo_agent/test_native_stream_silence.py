# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A model stream that goes silent is re-sent by the agent runtime before the host gives up on the turn.

The host ends a turn after ``turn_activity.IDLE_SECONDS`` with nothing from the runtime, as an error, and never re-sends
a turn that already ran a tool (``codex_retry``). The runtime re-sends a request itself when its stream has been idle
for the provider entry's ``stream_idle_timeout_ms``, up to ``stream_max_retries`` times, and says so (``error`` with
``willRetry``). On the Gemini wire that limit was 900 s with no retry, longer than the host's 600 s. So a stream that
hung after a tool result was never re-sent: the person waited 10 minutes and got an error, though the edit had
committed. That happened in 9 of 742 Gemini gate turns (2.0.0 and 2.1.0, Carbon and Neon), and in none of 1,470 Claude
and ChatGPT turns. Every provider entry Angelo writes now carries its wire's stream rules
(``provider_registry.STREAM_SETTINGS``).

Pinned here, from the configuration and the host's own constant, never a list of providers:

* every Codex provider entry Angelo writes (registry modes, team modes and each custom-endpoint format on the Codex
  runtime) has an idle limit under the host's and at least one retry, and every wire the runtime may run has rules;
* (pins of existing behaviour) a runtime retry notice keeps the host waiting (it resets the host's silence clock),
  and silence alone does not; a runtime retry rules out the host's own resend; a tool call before a runtime retry
  reaches the host once.
  The runtime's retry re-sends the turn's recorded history, the call and its output included (codex
  ``session/turn.rs`` ``run_sampling_request``: ``clone_history`` and ``executed_tool_calls``), so the model sees the
  call as done.

Out of scope: the runtime's built-in OpenAI provider (ChatGPT sign-in, OpenAI API key), which Angelo writes no entry
for (the runtime's Responses rules apply: 300 s, 5 retries), and Claude Code, which sends keep-alives and retries on
its own. Since the fork's 2.1.0 pin the same limit also bounds the wait for a response to start (doc/angelo/
ai_providers.rst, "Silent model streams").
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import agent_runtime, custom_endpoints, provider_registry, team_runtime, turn_activity  # noqa: E402
from angelo_agent.endpoint_connections import mode_for  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from test_native_codex_turn_errors import RECONNECTING, THREAD, ProviderFixture, answered, failed  # noqa: E402
from test_native_custom_endpoints import HEX, Endpoints, description  # noqa: E402

HOST_IDLE_MS = turn_activity.IDLE_SECONDS * 1000


def fields(mode):
    """The ``model_providers.<mode>`` fields Angelo launches ``mode`` with, as values."""
    prefix = "model_providers.%s." % mode
    with mock.patch.object(team_runtime, "base_url", lambda mode: "https://gateway.example.test/" + mode), \
            mock.patch.object(team_runtime, "request_compression", lambda mode: None):
        entry = team_runtime.model_provider_entry(mode)
    return {key[len(prefix):]: json.loads(value) for key, value in entry.items()}


def codex_formats():
    return sorted(fmt for fmt, runtime in custom_endpoints.RUNTIMES.items() if runtime == "codex")


class EveryProviderEntryOutlastsNoHostTimeout(unittest.TestCase):
    def check(self, mode, entry):
        idle, retries = entry.get("stream_idle_timeout_ms"), entry.get("stream_max_retries")
        self.assertIsInstance(idle, int, "%s: no stream idle limit of its own (the runtime's wire default)" % mode)
        self.assertIsInstance(retries, int, "%s: no stream retry count of its own" % mode)
        self.assertLess(idle, HOST_IDLE_MS, "%s: the runtime re-sends only after the host has given up" % mode)
        self.assertGreater(idle, 0, mode)
        self.assertGreaterEqual(retries, 1, "%s: a silent stream is never re-sent" % mode)

    def test_every_registry_and_team_mode(self):
        modes = sorted(provider_registry.CUSTOM_PROVIDER_MODES)
        self.assertTrue(modes)
        for mode in modes:
            with self.subTest(mode=mode):
                self.check(mode, fields(mode))

    def test_every_custom_endpoint_format_on_the_codex_runtime(self):
        hosted = max(rules["stream_idle_timeout_ms"] for rules in provider_registry.STREAM_SETTINGS.values())
        with Endpoints(description()):
            for fmt in codex_formats():
                with self.subTest(format=fmt):
                    mode = mode_for(HEX, fmt)
                    entry = fields(mode)
                    self.assertEqual(entry["wire_api"], custom_endpoints.FORMATS[fmt][0])
                    self.check(mode, entry)
                    # An organization's own server may hold its headers through a long prefill: its limit
                    # outlasts every hosted wire's, and a re-send (which repeats the prefill) happens once.
                    self.assertGreaterEqual(entry["stream_idle_timeout_ms"], hosted)
                    self.assertEqual(entry["stream_max_retries"], 1)

    def test_every_wire_the_runtime_may_run_has_rules(self):
        wires = {agent_runtime.DEFAULT_WIRE} | set(agent_runtime.PIN.get("wire_apis", ()))
        wires |= {custom_endpoints.FORMATS[fmt][0] for fmt in codex_formats()}
        wires |= {spec.get("wire_api", agent_runtime.DEFAULT_WIRE) for spec in provider_registry.SPECS.values()
                  if spec.get("runtime") == "codex"}
        for wire in sorted(wires):
            with self.subTest(wire=wire):
                settings = provider_registry.stream_settings(wire)
                self.assertLess(settings["stream_idle_timeout_ms"], HOST_IDLE_MS)
                self.assertGreaterEqual(settings["stream_max_retries"], 1)

    def test_a_wire_without_rules_is_refused_rather_than_left_to_the_runtime(self):
        with self.assertRaises(KeyError):
            provider_registry.stream_settings("a-wire-nobody-wrote")


def pause(seconds):
    """A stretch with nothing from the runtime (``ScriptedRPC`` runs a callable where an event would be)."""
    return ("pause", lambda: time.sleep(seconds))


RETRY = ("error", {"error": RECONNECTING, "willRetry": True, "threadId": THREAD, "turnId": "turn-1"})
# What the runtime sends when its own re-sends are used up: the turn fails with a transient error, one the host would
# otherwise resend once (codex_retry: an overloaded provider, nothing ran).
GAVE_UP = {"message": "stream disconnected before completion: idle timeout waiting for Gemini (360 s without data)",
           "codexErrorInfo": "serverOverloaded", "additionalDetails": None, "misalignment": None}


class RuntimeRetryAndTheHost(ProviderFixture):
    """Pins of behaviour that already held before this change (green on 93d6a3d3336), never red tests.

    The host's silence limit is scaled down to 3 s. Each quiet gap below is 1 s, a third of the limit, so a loaded
    machine cannot stretch a gap into a give-up; the turns run 4 s or more in all, past the limit, so a host that
    ignored the notices would give up."""

    LIMIT, GAP = 3, 1.0

    def setUp(self):
        super().setUp()
        fast = mock.patch.multiple(turn_activity, IDLE_SECONDS=self.LIMIT, POLL_SECONDS=.05)
        fast.start()
        self.addCleanup(fast.stop)

    def test_a_retry_notice_resets_the_hosts_silence_clock(self):
        stretches = [step for _ in range(4) for step in (RETRY, pause(self.GAP))]
        rpc = self.script(lambda turn: stretches + answered(turn))
        outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed", "each retry notice is a sign of life")
        self.assertEqual(len(rpc.turn_starts()), 1)

    def test_silence_alone_still_ends_the_turn(self):
        self.script(lambda turn: [pause(self.LIMIT + 1.5)] + answered(turn))
        outcome, _ = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertIn("nothing came from the model", str(outcome))

    def test_after_a_runtime_retry_the_host_sends_no_turn_of_its_own(self):
        """A turn the runtime re-sent and that then failed anyway is not resent by the host, though nothing ran and
        its error is one the host resends otherwise (FailedTurnTests.test_an_overloaded_provider_is_resent_once):
        retries never stack. Fails if the willRetry notice stops ruling the host's resend out."""
        rpc = self.script(lambda turn: failed(turn, GAVE_UP, before=[RETRY, pause(.2), RETRY]), answered)
        outcome, _ = self.run_request()
        self.assertIsInstance(outcome, RpcError)
        self.assertEqual(len(rpc.turn_starts()), 1, "the runtime already re-sent; the host sends nothing more")

    def test_a_tool_call_before_a_runtime_retry_reaches_the_host_once(self):
        """The runtime's re-send carries the finished call in its history, so nothing re-delivers it: the host sees
        the call once and sends no turn of its own. A pin of the host's side; the runtime's side is its own
        (codex session/turn.rs run_sampling_request)."""
        def call(turn):
            def arrive():
                self.p._request(7, "item/tool/call", {"threadId": THREAD, "turnId": turn, "callId": "c1",
                                                      "tool": "angelo_inspect_scene", "arguments": {}},
                                transport=self.p._transport_token)
            return [("item/tool/call", arrive), RETRY, pause(.2), RETRY] + answered(turn)

        rpc = self.script(call)
        with mock.patch("angelo_agent.codex_delivery.dispatch_dynamic_call") as dispatch:
            outcome, _ = self.run_request()
        self.assertEqual(outcome, "completed")
        self.assertEqual(dispatch.call_count, 1)
        self.assertEqual(len(rpc.turn_starts()), 1)


if __name__ == "__main__":
    unittest.main()
