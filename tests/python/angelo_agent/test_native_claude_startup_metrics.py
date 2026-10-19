# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual deferred Claude launch accounting; no inference or auth discovery."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, REQUIRES, SUPPORTED, TOO_OLD, completed
from angelo_agent.protocol import RpcError


class ClaudeStartupMetricTests(ClaudeAdapterFixture, unittest.TestCase):
    def test_actual_cold_launch_is_measured_once_and_warm_round_adds_no_launch(self):
        self.assertFalse(self.provider.has_warm_runtime())
        with patch("angelo_agent.providers.time.monotonic", side_effect=[100.0, 100.125]):
            self.turn(None, "Cold")
        self.host.record_runtime_start.assert_called_once_with(125.0, cold=True)
        self.assertTrue(self.provider.has_warm_runtime())
        self.turn("fixture-native", "Warm")
        self.host.record_runtime_start.assert_called_once()
        self.popen_mock.assert_called_once()
        self.assertEqual(self.host.record_provider_turn.call_count, 2)
        self.assertEqual(self.host.record_model_round.call_count, 2)

    def test_stop_retirement_is_not_reported_as_a_warm_next_launch(self):
        self.turn(None, "Original")
        self.provider.stop()
        self.assertFalse(self.provider.has_warm_runtime())
        newer = NativeInitializeFixture(on_user=lambda process, _: process.stdout.emit(completed()))
        self.popen_mock.return_value = newer
        self.host._request_generation += 1
        self.provider.bind_request(self.host)
        self.host.record_runtime_start.reset_mock()
        with patch("angelo_agent.providers.time.monotonic", side_effect=[101.0, 101.25]):
            self.turn("fixture-native", "After Stop")
        self.host.record_runtime_start.assert_called_once_with(250.0, cold=True)
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertEqual(self.popen_mock.call_count, 2)

    def test_startup_failure_is_measured_but_never_counted_as_a_model_round(self):
        self.run_mock.return_value = SimpleNamespace(returncode=0, stdout=TOO_OLD)
        with patch("angelo_agent.providers.time.monotonic", side_effect=[100.0, 100.5]):
            with self.assertRaisesRegex(RpcError, REQUIRES):
                self.turn(None, "Unsupported runtime")
        self.host.record_runtime_start.assert_called_once_with(500.0, cold=True)
        self.assertFalse(self.provider.has_warm_runtime())
        self.host.record_provider_turn.assert_not_called()
        self.host.record_model_round.assert_not_called()
        self.popen_mock.assert_not_called()

    def test_stale_startup_callback_does_not_update_new_request_metrics(self):
        def version(*_args, **_kwargs):
            self.host._request_generation += 1
            self.provider.bind_request(self.host)
            return SimpleNamespace(returncode=0, stdout=SUPPORTED)
        self.run_mock.side_effect = version
        self.assertIsNone(self.turn(None, "Original"))
        self.host.record_runtime_start.assert_not_called()
        self.assertFalse(self.provider.has_warm_runtime())
        self.popen_mock.assert_not_called()

    def test_discovery_initialization_is_not_inference_or_active_request_startup(self):
        self.provider.connect = Mock(return_value=None)
        self.provider.list_models()
        self.assertTrue(self.provider.has_warm_runtime())
        self.host.record_runtime_start.assert_not_called()
        self.host.record_provider_turn.assert_not_called()
        self.host.record_model_round.assert_not_called()

    def test_live_but_uninitialized_or_retired_process_is_not_warm(self):
        self.start(None)
        transport = self.provider._transport
        self.assertTrue(self.provider.has_warm_runtime())
        transport.initialized.clear()
        self.assertFalse(self.provider.has_warm_runtime())
        transport.initialized.set()
        transport.retire("Fixture retirement")
        self.assertFalse(self.provider.has_warm_runtime())


if __name__ == "__main__":
    unittest.main()
