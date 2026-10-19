# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Controller delivers its captured context and cannot retire a newer owner."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.controller import Controller
from angelo_agent.providers import ClaudeProvider
from angelo_agent.routing import RuntimeContext


class LegacyProvider:
    def __init__(self):
        self.controller = None
        self.calls = []
        self.closed = False
    def bind_request(self, context):
        self.controller = context
    def run(self, native_id, text, observation=None):
        self.calls.append((native_id, text, observation))
        return "completed"
    def close(self):
        self.closed = True


class RuntimeEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("fixture-runtime", 17))
        self.c.state["native_id"] = "already-connected-thread"
        self.c._begin_metrics()
        self.c.busy = True
        self.context = self.c.runtime_context()

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def test_actual_claude_entry_receives_old_captured_context_after_warm_rebind(self):
        self.c.state["provider"] = self.context.state["provider"] = "claude_subscription"
        provider = ClaudeProvider(self.context, {})
        self.c.provider = provider
        connect = self.c._connect_runtime
        other = RuntimeContext(self.c, "claude_subscription", {}, "", None, self.c._epoch)
        def rebind_before_entry(context):
            native_id = connect(context)
            provider.bind_request(other)
            return native_id
        observation = {"objects": []}
        with patch.object(self.c, "_connect_runtime", side_effect=rebind_before_entry), \
             patch.object(provider, "run_request", wraps=provider.run_request) as entry, \
             patch.object(provider, "run", side_effect=AssertionError("Do not consult mutable run binding")), \
             patch.object(provider, "_native_call", side_effect=AssertionError("Old context cannot dispatch")) as native:
            self.c._run("Original captured request", observation)
        self.assertIs(entry.call_args.args[0], self.context)
        self.assertEqual(entry.call_args.args[1], "already-connected-thread")
        self.assertTrue(entry.call_args.args[2].startswith("Original captured request"))
        self.assertIs(entry.call_args.kwargs["observation"], observation)
        self.assertIs(provider.controller, other)
        native.assert_not_called()
        self.c.pump_events()
        self.assertEqual(self.c.status, "Interrupted")

    def test_legacy_provider_fallback_keeps_original_signature_and_observation(self):
        provider = self.c.provider = LegacyProvider()
        observation = {"scene": "Fixture"}
        self.c._run("Legacy request", observation)
        self.assertEqual(len(provider.calls), 1)
        native_id, text, supplied = provider.calls[0]
        self.assertEqual(native_id, "already-connected-thread")
        self.assertTrue(text.startswith("Legacy request"))
        self.assertIs(supplied, observation)
        self.assertIs(provider.controller, self.context)
        self.c.pump_events()
        self.assertEqual(self.c.status, "Completed")

    def test_retired_context_error_does_not_publish_auth_status_or_close_replacement(self):
        provider = LegacyProvider()
        replacement = LegacyProvider()
        replacement.run_request = Mock(return_value="completed")
        def retire_then_fail(context, native_id, text, observation=None):
            # Actual host recovery revokes the old epoch/document/transport.
            self.c.rebind_after_restore(("restored-runtime", 23), "fixture-recovery")
            self.c.provider = replacement
            raise RuntimeError("Authentication unauthorized in retired request")
        provider.run_request = retire_then_fail
        self.c.provider = provider
        self.c._run("Retired request", {})
        self.assertTrue(self.context.cancelled)
        self.assertIs(self.c.provider, replacement)
        self.assertFalse(replacement.closed)
        self.assertFalse(self.c.connection_error)
        self.assertFalse(self.c.setup_required)
        self.c.pump_events()
        self.assertFalse(self.c.setup_required)
        self.assertFalse(any(message.get("role") == "error" for message in self.c.state["messages"]))
        replacement.run_request.assert_not_called()

    def test_normal_initial_connect_failure_still_retires_its_fresh_provider(self):
        self.c.provider = None
        provider = LegacyProvider()
        def fail(context):
            self.c.provider = provider
            raise RuntimeError("Native startup failed")
        with patch.object(self.c, "_connect_runtime", side_effect=fail):
            self.c._run("Current request", {})
        self.assertTrue(provider.closed)
        self.assertIsNone(self.c.provider)
        self.c.pump_events()
        self.assertEqual(self.c.status, "Failed")


if __name__ == "__main__":
    unittest.main()
