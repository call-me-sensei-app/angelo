# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic Claude authority replacement at actual dispatch boundaries."""

from dataclasses import FrozenInstanceError
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, SUPPORTED
from angelo_agent.claude_transport import ClaudeRequest, ClaudeTransport, check_command_budget
from angelo_agent.protocol import RpcError
from angelo_agent.request_fences import RequestFence


CUBE = {"tool": "angelo_create_cube", "arguments": {
    "name": "Old request cube", "size": 1, "location": [0, 0, 0]}}
SCRIPT = {"tool": "angelo_execute_python", "arguments": {
    "description": "Old request script", "code": "pass"}}


class ClaudeRequestFenceTests(ClaudeAdapterFixture, unittest.TestCase):
    def replace_request(self, *, install_warm=False):
        self.host._request_generation += 1
        self.provider.bind_request(self.host)
        owner = ClaudeRequest(self.host)
        # Reproduce the reviewer's old shared-owner overwrite too. It must be
        # irrelevant to every execution path, not a hidden source of authority.
        self.provider._request_owner = owner
        if install_warm:
            process = NativeInitializeFixture()
            transport = ClaudeTransport(process, owner)
            transport.initialized.set()
            self.provider._transport, self.provider.process = transport, process
            self.provider.runtime_options = self.provider._runtime_binding(self.host)
            self.provider.initialized = transport.initialized
            self.provider.initialization_response = {"new_request": True}
            self.provider.done = threading.Event()
            self.provider.result = {"new_request": True}
        return owner

    def record_dispatch(self):
        dispatched = []
        def request(identity, name, args, callback):
            dispatched.append((name, args, self.host._request_generation))
            callback({"success": True})
        self.host.request_tool = request
        return dispatched

    def test_exact_review_owner_swap_between_run_check_and_native_batch_dispatch(self):
        for operation in (CUBE, SCRIPT):
            with self.subTest(tool=operation["tool"]):
                dispatched = self.record_dispatch()
                self.provider._turn = Mock(return_value={"reply": "Old plan", "operations": [operation], "verify": False})
                with patch("angelo_agent.providers._ClaudeSculptEvidence.invalidate",
                           side_effect=self.replace_request):
                    self.assertEqual(self.provider.run(None, "Old request", observation={}), "interrupted")
                self.assertEqual(dispatched, [])
                self.assertEqual(self.host.events, [])
                self.popen_mock.assert_not_called()
                self.run_mock.assert_not_called()

    def test_run_captures_one_frozen_owner_for_every_model_and_native_call(self):
        dispatched = self.record_dispatch()
        owners = []
        def plan(_native, _text, *, owner):
            owners.append(owner)
            return {"reply": "Plan", "operations": [CUBE], "verify": False}
        actual = self.provider._native_call
        def native(name, args, *, owner):
            owners.append(owner)
            return actual(name, args, owner=owner)
        self.provider._turn, self.provider._native_call = plan, native
        self.assertEqual(self.provider.run(None, "Create", observation={}), "completed")
        self.assertEqual(len(dispatched), 1)
        self.assertIs(owners[0], owners[1])
        with self.assertRaises(FrozenInstanceError):
            owners[0].generation = self.host._request_generation + 1

    def test_replacement_just_before_native_call_cannot_adopt_new_owner(self):
        dispatched = self.record_dispatch()
        actual = self.provider._native_call
        self.provider._turn = Mock(return_value={"reply": "Old plan", "operations": [CUBE], "verify": False})
        def paused(name, args, *, owner):
            self.replace_request()
            return actual(name, args, owner=owner)
        self.provider._native_call = paused
        self.assertEqual(self.provider.run(None, "Old request", observation={}), "interrupted")
        self.assertEqual(dispatched, [])

    def test_replacement_just_before_model_turn_does_not_start_or_submit(self):
        actual = self.provider._turn
        def paused(native_id, text, *, owner):
            self.replace_request()
            return actual(native_id, text, owner=owner)
        self.provider._turn = paused
        self.assertEqual(self.provider.run(None, "Old request", observation={}), "interrupted")
        self.popen_mock.assert_not_called()
        self.run_mock.assert_not_called()
        self.host.record_runtime_start.assert_not_called()

    def test_replacement_just_before_start_keeps_new_warm_transport_untouched(self):
        actual = self.provider._start
        def paused(native_id, *, owner):
            self.replace_request(install_warm=True)
            return actual(native_id, owner=owner)
        self.provider._start = paused
        self.assertIsNone(self.turn(None, "Old request"))
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertEqual(self.provider.initialization_response, {"new_request": True})
        self.assertEqual(self.provider.result, {"new_request": True})
        self.assertFalse(self.provider.done.is_set())
        self.popen_mock.assert_not_called()
        self.run_mock.assert_not_called()
        self.host.record_runtime_start.assert_not_called()

    def test_replacement_after_version_probe_prevents_old_launch(self):
        def version(*_args, **_kwargs):
            self.replace_request(install_warm=True)
            return SimpleNamespace(returncode=0, stdout=SUPPORTED)
        self.run_mock.side_effect = version
        self.assertIsNone(self.turn(None, "Old request"))
        self.popen_mock.assert_not_called()
        self.assertTrue(self.provider.has_warm_runtime())
        self.host.record_runtime_start.assert_not_called()

    def test_replacement_after_argv_check_prevents_old_popen(self):
        def budget(command):
            check_command_budget(command)
            self.replace_request(install_warm=True)
        with patch("angelo_agent.claude_transport.check_command_budget", side_effect=budget):
            self.assertIsNone(self.turn(None, "Old request"))
        self.popen_mock.assert_not_called()
        self.assertTrue(self.provider.has_warm_runtime())

    def test_popen_ownership_loss_closes_only_unadmitted_old_process(self):
        def spawn(*_args, **_kwargs):
            self.replace_request(install_warm=True)
            return self.process
        self.popen_mock.side_effect = spawn
        self.assertIsNone(self.turn(None, "Old request"))
        self.assertIsNotNone(self.process.poll())
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertIsNot(self.provider.process, self.process)
        self.assertFalse(self.process.user_written.is_set())
        self.assertEqual(self.provider.initialization_response, {"new_request": True})

    def test_replacement_before_warm_user_write_cannot_submit_or_close_new_transport(self):
        self.start(None)
        actual = self.provider._write
        def paused(message, transport, *, owner, cleanup=False):
            if message["type"] == "user":
                self.replace_request(install_warm=True)
            return actual(message, transport, owner=owner, cleanup=cleanup)
        self.provider._write = paused
        self.assertIsNone(self.turn(None, "Old request"))
        self.assertFalse(self.process.user_written.is_set())
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertEqual(self.provider.result, {"new_request": True})
        self.assertFalse(self.provider.done.is_set())
        self.assertEqual(self.host.events, [])
        self.host.record_provider_turn.assert_not_called()
        self.host.record_model_round.assert_not_called()

    def test_old_write_start_and_cleanup_cannot_touch_new_lease_on_same_warm_process(self):
        # Launched for the session the new request continues: an unused session serves only its own (_turn).
        self.start("fixture-native")
        transport = self.provider._transport
        old = transport.owner
        new = self.replace_request()
        self.assertIs(self.provider._transport, transport)
        self.provider._close_transport(transport, owner=old, interrupt=True)
        self.assertIs(self.provider.process, self.process)
        self.assertFalse(transport.retired.is_set())
        self.assertIsNone(self.start("old-native", owner=old))
        with self.assertRaisesRegex(RpcError, "no longer owns"):
            self.provider._write({"type": "user", "message": {"role": "user", "content": "Old"}},
                                 transport, owner=old)
        self.assertFalse(self.process.user_written.is_set())
        self.assertEqual(self.turn("fixture-native", "New request", owner=new)["reply"], "Verified")
        self.popen_mock.assert_called_once()
        self.run_mock.assert_called_once()

    def test_old_restart_cleanup_cannot_close_a_rebound_warm_process(self):
        self.start(None)
        self.host.turn_options["fast"] = True   # A launch setting (--settings); effort is applied per turn.
        actual = self.provider._close_transport
        replaced = False
        def paused(transport, **kwargs):
            nonlocal replaced
            if not replaced:
                replaced = True
                self.replace_request()
            return actual(transport, **kwargs)
        self.provider._close_transport = paused
        self.assertIsNone(self.turn(None, "Old request"))
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertIsNone(self.process.poll())
        self.popen_mock.assert_called_once()

    def test_old_failed_readback_emits_no_feedback_into_new_request(self):
        self.provider._turn = Mock(return_value={"reply": "Old plan", "operations": [CUBE], "verify": False})
        def native(_name, _args, *, owner):
            self.replace_request()
            return {"success": False, "status": "fail"}
        self.provider._native_call = native
        self.assertEqual(self.provider.run(None, "Old request", observation={}), "interrupted")
        self.assertEqual(self.host.events, [])

    def test_captured_public_entry_context_cannot_adopt_rebound_provider_context(self):
        old = SimpleNamespace(host=self.host, request_fence=RequestFence.capture(self.host), cancelled=False)
        self.replace_request(install_warm=True)
        self.assertEqual(ClaudeRequest(old).generation, old.request_fence.generation)
        self.assertFalse(ClaudeRequest(old).owns_request())
        self.assertEqual(self.provider.run_request(old, "old-native", "Old worker", observation={}), "interrupted")
        self.assertTrue(self.provider.has_warm_runtime())
        self.assertEqual(self.provider.initialization_response, {"new_request": True})
        self.popen_mock.assert_not_called()
        self.assertFalse(self.provider.process.user_written.is_set())


if __name__ == "__main__":
    unittest.main()
