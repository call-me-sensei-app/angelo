# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker send admission uses the real host and Controller, never a provider account."""

import suite_isolation  # noqa: F401

from types import SimpleNamespace
from unittest.mock import patch
import uuid

from test_native_worker_chat_host import Fixture
from angelo_agent import goal_loop, goal_state, request_rows
from angelo_agent.refusal_codes import RefusalError


class ExplicitReferenceAdmissionTests(Fixture):
    def ready(self):
        self.assertTrue(self.open()["ok"])
        chat = self.chats.hosted(self.session)
        unrelated = {"id": "unrelated", "path": "unrelated-composer-only.png"}
        chat.attachments = [unrelated]
        return chat, unrelated

    def send(self, chat, **fields):
        return self.chats._send(chat, dict(text="Own request", attachments=[], **fields))

    def unchanged(self, chat, unrelated):
        self.assertEqual(chat.attachments, [unrelated])
        self.assertIs(chat.attachments[0], unrelated)

    def test_busy_refusal_keeps_unrelated_composer(self):
        chat, unrelated = self.ready()
        chat.busy = True
        try:
            with self.assertRaises(RuntimeError):
                self.send(chat)
            self.unchanged(chat, unrelated)
        finally:
            chat.busy = False

    def test_known_but_unowned_goal_refusal_keeps_unrelated_composer(self):
        chat, unrelated = self.ready()
        with self.assertRaises(ValueError):
            self.send(chat, host_kind=request_rows.GOAL, host_request=[])
        self.unchanged(chat, unrelated)

    def test_unknown_kind_refuses_before_staging(self):
        chat, unrelated = self.ready()
        with self.assertRaises(RefusalError):
            self.send(chat, host_kind="not-a-request-kind")
        self.unchanged(chat, unrelated)

    def test_invalid_reference_refusal_keeps_unrelated_composer(self):
        chat, unrelated = self.ready()
        with self.assertRaises(ValueError):
            self.chats._send(chat, {"text": "Own request", "attachments": [{"path": "missing.png"}]})
        self.unchanged(chat, unrelated)

    def test_event_without_host_request_refuses_without_becoming_personal(self):
        chat, unrelated = self.ready()
        with self.assertRaises(ValueError):
            self.send(chat, host_kind=request_rows.EVENT)
        self.unchanged(chat, unrelated)

    def test_person_receives_explicit_checked_snapshot_not_composer(self):
        chat, unrelated = self.ready()
        chat.references.directory.mkdir(parents=True, exist_ok=True)
        path = chat.references.directory / (str(uuid.uuid4()) + ".png")
        path.write_bytes(b"owned test snapshot")
        incoming = {"id": path.stem, "path": str(path), "mime": "image/png", "name": "own.png"}
        answer = self.chats._send(chat, {"text": "Own request", "attachments": [incoming]})
        self.assertEqual(answer, {"ok": True, "_epoch": chat._epoch,
                                  "_generation": chat._request_generation})
        self.assertTrue(chat._persons_send)
        self.assertFalse(chat._turn_takes_drafts)
        self.assertEqual(chat.turn_attachments, [incoming])
        self.assertIsNot(chat.turn_attachments[0], incoming)
        self.unchanged(chat, unrelated)
        self.run_turn()
        self.unchanged(chat, unrelated)

    def test_brief_does_not_take_or_retime_personal_composer(self):
        chat, unrelated = self.ready()
        chat.person_spoke_at = 17
        self.send(chat, host_kind=request_rows.WORKER_BRIEF)
        self.assertFalse(chat._persons_send)
        self.assertEqual(chat._request_kind, request_rows.WORKER_BRIEF)
        self.assertEqual(chat.person_spoke_at, 17)
        self.assertEqual(chat.turn_attachments, [])
        self.unchanged(chat, unrelated)
        self.run_turn()

    def test_release_worker_event_keeps_explicit_classification(self):
        chat, unrelated = self.ready()
        chat.person_spoke_at = 17
        self.send(chat, host_kind=request_rows.EVENT, host_request=[])
        self.assertFalse(chat._persons_send)
        self.assertEqual(chat._request_kind, request_rows.EVENT)
        self.assertEqual(chat.person_spoke_at, 17)
        self.assertEqual(chat.turn_attachments, [])
        self.unchanged(chat, unrelated)
        self.run_turn()

    def test_local_goal_admission_requires_exact_controller_not_equal_id(self):
        owner = SimpleNamespace(state={"id": "same-chat"}, attachments=[], external_client="")
        replacement = SimpleNamespace(state={"id": "same-chat"}, attachments=[], external_client="")
        goal = {"status": goal_state.ACTIVE}
        with patch.dict(goal_loop._sending, {"same-chat": owner}), \
                patch("angelo_agent.goal_loop.goal_of", return_value=goal), \
                patch("angelo_agent.goal_probes.worker_chat", return_value=False):
            self.assertEqual(request_rows.sender(owner, [], request_rows.GOAL),
                             ((), request_rows.GOAL, False))
            with self.assertRaises(ValueError):
                request_rows.sender(replacement, [], request_rows.GOAL)
