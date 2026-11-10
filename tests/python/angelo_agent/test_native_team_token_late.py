# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rotated-token rules when a sign-in ends unsaved or a refresh answer arrives after ``close`` stopped waiting.

Contract: doc/angelo/ai_providers.rst, "A rotated token the store refuses". As in ``test_native_team_token_edges``
(whose fakes this uses), two ``TeamAccount`` objects stand for two Angelo windows sharing the sign-in record.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import threading
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent import team_account  # noqa: E402
from angelo_agent.team_errors import NotSent, SignInRequired, TeamError  # noqa: E402
from angelo_agent.team_http import http_transport  # noqa: E402
from angelo_agent.team_refresh_marks import RecordError  # noqa: E402
from fake_team_console import FakeVault  # noqa: E402
from test_native_team_account import AccountCase, wait_for  # noqa: E402
import test_native_team_token_edges as edges  # noqa: E402  The module only: its tests aren't collected here twice.


def gated(gate, presented):
    """A transport that holds a refresh's answer back while ``gate`` is clear (``presented`` is set then)."""
    def transport(method, url, headers, body, *args, **kwargs):
        if body and b"grant_type=refresh_token" in body and not gate.is_set():  # GETs have no body.
            presented.set()
            gate.wait(30)
        return http_transport(method, url, headers, body, *args, **kwargs)
    return transport


class EndAndLateAnswerTests(AccountCase):
    """A new sign-in ending while unsaved, a store that can't say what it holds, and refresh answers arriving after
    ``close`` stopped waiting for them."""

    def test_a_new_sign_in_that_ends_unsaved_leaves_the_older_ones_stored_token(self):
        shared, older, newer, newer_vault = edges.RoundThreeTests.two_sign_ins(self, "approval")
        newer_vault.deletes = True  # The store refuses new items only: a delete would land.
        older_token = shared.token
        with self.console.lock:  # The new sign-in's device is signed out in the console.
            self.console.revoked[self.console.refresh[newer._refresh_token]["grant"]] = "signed out"
        with self.assertRaises(SignInRequired):
            newer.access_token(stale=newer._access)
        self.assertEqual(shared.token, older_token, "the older sign-in's live token was deleted")
        self.refresh(older)
        self.assert_no_reuse()

    def test_a_replaced_token_is_never_revoked_while_the_store_cant_say_whether_it_holds_it(self):
        shared = FakeVault()
        vault = edges.Window(shared)
        older = self.account(vault=vault, record=True)
        self.sign_in(older)
        vault.failing, vault.kind = True, "timeout"
        self.refresh(older)
        late = older._refresh_token
        newer = self.account(vault=edges.Window(shared), record=True)
        self.sign_in(newer)
        shared.token = late  # The timed-out write landed, over the new sign-in.
        vault.failing, vault.unreadable = False, True
        older._task_save(older._epoch)
        self.assertEqual((older._unsaved, self.console.revoked), (False, {}), "a possibly stored token was revoked")
        self.refresh(newer)
        self.assert_no_reuse()

    def test_an_answer_after_close_stopped_waiting_is_handed_to_the_next_account(self):
        """``close`` waits a bounded time for a refresh's answer; one arriving later whose save fails is handed off
        (with the holder lock) like an unsaved token close saw, not held by the closed account for good."""
        gate, presented = threading.Event(), threading.Event()
        gate.set()
        shared = FakeVault()
        vault = edges.Window(shared)
        old = self.account(vault=vault, record=True, transport=gated(gate, presented))
        self.sign_in(old)
        vault.failing = True
        self.late_answer(old, gate, presented)  # Close stops waiting while the answer is still on its way.
        new_vault = edges.Window(shared)
        new_vault.failing = True
        new = self.account(vault=new_vault, record=True)
        self.assertTrue(new.restore())
        wait_for(lambda: new.view().status != "restoring", timeout=10)
        self.assertEqual(new.view().status, "signed_in", new.view().message)
        self.assertTrue(new._record.holder.held, "the other windows still wait for this one")
        new_vault.failing = False
        new._task_save(new._epoch)
        self.assertEqual((new._unsaved, shared.token), (False, new._refresh_token))
        self.assert_no_reuse()

    def late_answer(self, account, gate, presented):
        """Refresh ``account`` on a thread, close it while the answer is held back past close's wait, then let the
        answer through."""
        gate.clear()
        refreshing = threading.Thread(target=lambda: self.assertRaises(SignInRequired, account.access_token,
                                                                       account._access), daemon=True)
        refreshing.start()
        self.assertTrue(presented.wait(30))
        with unittest.mock.patch.object(team_account, "QUIT_REFRESH_WAIT", 0.3):
            account.close()
        gate.set()
        refreshing.join(30)
        self.assertTrue(account.join(10))

    def test_a_late_renewal_of_a_sign_in_not_recorded_yet_is_never_stored(self):
        """A sign-in whose generation couldn't be recorded stores nothing until it is: a renewal's answer that
        arrives after close stopped waiting is handed off unsaved, not written under the older generation."""
        gate, presented = threading.Event(), threading.Event()
        gate.set()
        shared = FakeVault()
        account = self.account(vault=edges.Window(shared), record=True, transport=gated(gate, presented))
        # Recording the generation fails (marks still work): the sign-in and the renewal's commit stay pending.
        account._record.bump = lambda: (_ for _ in ()).throw(RecordError("io", "disk"))
        self.sign_in(account)
        self.assertTrue(account._pending_gen)
        self.late_answer(account, gate, presented)
        self.assertIsNone(shared.token, "a renewal was stored before its sign-in's generation was recorded")

    def test_a_kept_token_still_stored_is_not_revoked_when_dropped(self):
        """A kept token (its mark couldn't be removed) that is still the older sign-in's stored token, dropped at a
        sign-in elsewhere: given up without revoking, like an unsaved one the store holds."""
        offline = [False]

        def transport(*args, **kwargs):
            if offline[0]:
                raise NotSent("offline")
            return http_transport(*args, **kwargs)
        shared = FakeVault()
        older = self.account(vault=edges.Window(shared), record=True, transport=transport)
        self.sign_in(older)
        older._record.unmark = lambda token: (_ for _ in ()).throw(RecordError("io", "disk"))
        offline[0] = True
        with self.assertRaises(TeamError):
            older.access_token(stale=older._access)
        self.assertEqual(older._kept_marked, shared.token)
        newer_vault = edges.Window(shared)
        newer_vault.failing = True  # The new sign-in isn't stored: the kept token is still the stored one.
        self.sign_in(self.account(vault=newer_vault, record=True))
        offline[0] = False
        with self.assertRaises(TeamError):  # Waits for the window holding the new sign-in.
            older.access_token(stale=older._access)
        wait_for(lambda: not older._pending, timeout=10)  # A revoke runs on the worker.
        self.assertEqual(self.console.revoked, {}, "the older sign-in's stored token was revoked")

    refresh, assert_no_reuse = edges.EdgeTests.refresh, edges.EdgeTests.assert_no_reuse


if __name__ == "__main__":
    unittest.main()
