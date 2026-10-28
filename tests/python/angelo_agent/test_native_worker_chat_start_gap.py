# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's start of an autonomous call against a revocation (security re-review r2, the start gap): the
call's last start check (``worker_chat_hosted._tool_available``) passes under the chat's lock and the lock is let go
before ``executing`` is set. One rule (round 2 integration of R2c and R2g, the strictest of the two): the call starts
only through ``tool_deadlines.started``, under that lock and only while unrevoked. A revocation in the gap (the
reader's ``worker_consent.apply_consent``) refuses it with nothing run and says so; one after its start finds it
started: it runs, and its result says what it changed, never that nothing ran. A revocation before the check still
stops it. Deterministic: each revocation is applied at an exact step, with the chat's real classes. No Blender.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import worker_consent  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.worker_chat_hosted import hosted_class  # noqa: E402
from test_native_worker_consent import HostedFixture  # noqa: E402

Hosted = hosted_class(Controller)


class StartGap(HostedFixture):
    def hosted(self):
        folder = Path(self.directory.name) / "chat"
        folder.mkdir()
        chat = Hosted(str(folder))
        self.addCleanup(chat.close)
        chat.chat_host = SimpleNamespace(arrivals=None, forward=lambda *args: None, journal_written=lambda chat: None)
        chat.busy = True
        chat.state["options"]["approval"] = "AUTO_BOUNDED"
        chat.set_document(("worker-part", 7))
        chat.request_checkpoint = "9cf4e420-dcdb-4f45-bc8a-d9d439ce9aac"
        worker_consent.apply_consent(chat, 1, {"auto": True})
        return chat

    def revoke_at(self, chat, when):
        """Apply a narrowing lease when ``when(kind, data)`` first holds during ``chat``'s emits."""
        emit, fired = chat.emit, []

        def emitting(kind, data=None):
            if not fired and when(kind, data or {}):
                fired.append(worker_consent.apply_consent(chat, 2, {"auto": False}))
            return emit(kind, data)
        chat.emit = emitting
        return fired

    def test_a_revocation_after_the_last_start_check_refuses_the_call_with_nothing_run(self):
        chat = self.hosted()
        call, results = self.request(chat)
        authorized, checks, fired = chat.require_script_authorized, [], []

        def gap(checked):   # ``_execute_tool`` asks it once more after the last start check, before the start.
            checks.append(checked)
            if checked is call and checks.count(call) == 2:
                fired.append(worker_consent.apply_consent(chat, 2, {"auto": False}))
            return authorized(checked)
        chat.require_script_authorized = gap
        self.pump(chat)
        self.assertEqual(fired, [{"epoch": 2}])   # Applied in the gap: after the last check, before ``executing``.
        self.assertEqual(self.part.ran, [])
        self.assertTrue(call.revoked)
        self.assertTrue(results and results[0].get("error_code") == "CANCELLED" and results[0].get("revoked"), results)

    def test_a_revocation_after_the_start_finds_the_call_started_and_its_result_tells_the_change(self):
        chat = self.hosted()
        call, results = self.request(chat)
        fired = self.revoke_at(chat, lambda kind, data: kind == "activity" and data.get("id") == call.id
                               and data.get("status") == "running")
        self.pump(chat)
        self.assertEqual(fired, [{"epoch": 2}])   # Applied in the gap, before ``executing``.
        self.assertFalse(chat.consent_lease.granted_auto())
        self.assertEqual(self.part.ran, ["angelo_create_cube"])
        self.assertFalse(call.revoked)
        self.assertTrue(results and results[0].get("error_code") != "CANCELLED", results)
        self.assertTrue(results[0].get("scene_changed", True))

    def test_a_revocation_before_the_start_check_still_stops_the_call(self):
        chat = self.hosted()
        call, results = self.request(chat)
        worker_consent.apply_consent(chat, 2, {"auto": False})
        self.pump(chat)
        self.assertEqual(self.part.ran, [])
        self.assertFalse(results and results[0].get("success") is True, results)

    def test_a_start_that_fails_its_check_leaves_no_mark_and_the_next_check_decides_again(self):
        chat = self.hosted()
        call, _results = self.request(chat)
        chat.busy = False   # The window's own check refuses it (its turn ended).
        self.assertFalse(chat._tool_available(call, chat.document))
        self.assertFalse(call.executing or call.committing)
        self.assertTrue(worker_consent.revocable(call))


if __name__ == "__main__":
    unittest.main()
