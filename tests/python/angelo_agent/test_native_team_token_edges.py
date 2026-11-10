# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rotated-token rules at their edges, with two team accounts in one process sharing the sign-in record.

Contract: doc/angelo/ai_providers.rst, "A rotated token the store refuses". Two ``TeamAccount`` objects stand for
two Angelo windows: they share the record and its holder lock in the test folder (OS file locks conflict between
separate handles in one process as between processes) and, where a test says so, one stored sign-in. The fake
console revokes a grant whose used refresh token is presented again, as the real one does.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import sys
import threading
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from host_test_policy import environment_skip  # noqa: E402
from angelo_agent import team_lock, team_refresh_marks, team_token_store  # noqa: E402
from angelo_agent.key_store import KeyStoreError  # noqa: E402
from angelo_agent.team_errors import NotSent, TeamError  # noqa: E402
from angelo_agent.team_http import http_transport  # noqa: E402
from angelo_agent.team_refresh_marks import NAME, RecordError  # noqa: E402
from fake_team_console import FakeVault  # noqa: E402
from test_native_team_account import AccountCase, wait_for  # noqa: E402

LOCKED = "Keychain needs your approval, or the keychain is locked"


class Window(FakeVault):
    """One window's view of a key store every window shares (``shared``), whose writes fail while ``failing``."""

    def __init__(self, shared):
        super().__init__()
        self.shared, self.failing, self.kind, self.unreadable = shared, False, "approval", False
        self.deletes = False  # Deletes still work while writes fail (a store refusing only new items).
        self.hold = None  # A ``threading.Event`` a write waits for (a store slow to answer); ``writing`` is set then.
        self.writing = threading.Event()

    def read(self):
        if self.unreadable:
            raise KeyStoreError("timeout", "The key store didn't respond")
        return self.shared.read()

    def write(self, token):
        if self.hold is not None:
            self.writing.set()
            self.hold.wait(30)
        if self.failing:
            raise KeyStoreError(self.kind, LOCKED)
        return self.shared.write(token)

    def delete(self):
        if self.failing and not self.deletes:
            raise KeyStoreError("approval", LOCKED)
        return self.shared.delete()


class Unusable(FakeVault):
    """A store unusable for the whole session (no key store service): every call fails at once."""

    def __init__(self):
        super().__init__()
        self.reason = "There's no key store on this computer"

    def read(self):
        raise KeyStoreError("unavailable", self.reason)

    write = delete = lambda self, *args: Unusable.read(self)


class EdgeTests(AccountCase):
    def refresh(self, account):
        account.access_token(stale=account._access)
        self.assertEqual(account.view().status, "signed_in")

    def assert_no_reuse(self):
        self.assertNotIn("refresh token reuse", self.console.revoked.values(), "a used token was presented again")

    def test_a_store_unusable_from_the_start_keeps_each_windows_sign_in_its_own(self):
        """No other window ever read this sign-in from the store, so it isn't held for them: a sign-in in another
        window doesn't give it up, and nothing waits for it."""
        first = self.account(vault=Unusable(), record=True)
        self.sign_in(first)
        self.assertEqual((first.view().stored, first._unsaved, first._record.holder.held), ("session", False, False))
        second = self.account(vault=FakeVault(), record=True)
        self.sign_in(second)  # A new generation.
        first._task_save(first._epoch)
        self.refresh(first)
        self.refresh(second)
        self.assertEqual(self.console.revoked, {}, "each window keeps its own sign-in, as before")

    def test_a_sign_in_that_waited_too_long_for_the_team_lock_is_kept_and_saved_later(self):
        account = self.account(lock_timeout=0.3, record=True)
        with team_lock.held(self.folder.name, 30):  # Another window holds the team lock for longer.
            self.sign_in(account)
        self.assertEqual(account.view().stored, "session")
        self.refresh(account)  # Its own new sign-in, never mistaken for an older one.
        account._task_save(account._epoch)
        self.assertEqual((account.view().stored, account._unsaved, account._pending_gen), ("saved", False, False))
        self.assertEqual(self.vault.token, account._refresh_token)
        self.refresh(account)
        self.assertEqual(self.console.revoked, {})

    def test_a_new_sign_in_whose_record_cant_be_written_is_kept_until_it_can(self):
        """A full disk fails the record and the store write: the new sign-in stays this window's, and is recorded
        and saved once space is free, never given up as a sign-in older than the record."""
        disk_full = [True]
        vault = Window(FakeVault())
        account = self.account(vault=vault, record=True)
        real = account._record._replace

        def replace(source, target):
            if disk_full[0]:
                raise OSError(28, "No space left on device")
            return real(source, target)
        account._record._replace = replace
        vault.failing = True
        self.sign_in(account)
        self.assertEqual((account._pending_gen, account._unsaved), (True, True))
        account._task_save(account._epoch)
        self.assertTrue(account._pending_gen, "still waiting for the disk")
        disk_full[0], vault.failing = False, False
        account._task_save(account._epoch)
        self.assertEqual((account._pending_gen, account._unsaved, account.view().stored), (False, False, "saved"))
        self.assertEqual(vault.shared.token, account._refresh_token)
        self.refresh(account)
        self.assertEqual(self.console.revoked, {}, "the sign-in the person just made was never revoked")

    def test_a_sign_in_the_record_cant_name_yet_is_never_stored_for_others_to_adopt(self):
        """With the record unwritable but the store fine, the new sign-in waits to be stored until its generation is
        recorded: another window adopting it under the older generation would later revoke it."""
        disk_full = [True]
        vault = Window(FakeVault())
        account = self.account(vault=vault, record=True)
        real = account._record._replace
        account._record._replace = lambda source, target: (_ for _ in ()).throw(OSError(28, "No space left")) \
            if disk_full[0] else real(source, target)
        self.sign_in(account)
        self.assertIsNone(vault.shared.token, "stored before the record names it")
        self.assertEqual((account._pending_gen, account._unsaved), (True, True))
        disk_full[0] = False
        account._task_save(account._epoch)
        self.assertEqual((account._pending_gen, account._unsaved, vault.shared.token),
                         (False, False, account._refresh_token))
        self.refresh(account)
        self.assertEqual(self.console.revoked, {})

    def test_a_kept_token_whose_mark_couldnt_be_removed_is_never_given_up(self):
        """Unsaved, then a refresh that never reached the console and a record that couldn't take the mark back:
        the token stays this window's (it was never presented), and is saved and renewed later."""
        offline = [False]

        def transport(*args, **kwargs):
            if offline[0]:
                raise NotSent("offline")
            return http_transport(*args, **kwargs)
        vault = Window(FakeVault())
        account = self.account(vault=vault, record=True, transport=transport)
        self.sign_in(account)
        vault.failing = True
        self.refresh(account)
        unsaved = account._refresh_token
        real_unmark = account._record.unmark
        account._record.unmark = lambda token: (_ for _ in ()).throw(RecordError("io", "disk"))
        offline[0] = True
        with self.assertRaises(TeamError):
            account.access_token(stale=account._access)
        self.assertEqual((account._kept_marked, account._refresh_token), (unsaved, unsaved))
        account._task_save(account._epoch)  # Still failing to save: kept, not given up.
        self.assertEqual((account._refresh_token, account._unsaved), (unsaved, True))
        offline[0], vault.failing, account._record.unmark = False, False, real_unmark
        account._task_save(account._epoch)
        self.assertEqual((account._unsaved, vault.shared.token), (False, unsaved))
        self.refresh(account)
        self.assertEqual(self.console.revoked, {})

    def test_an_add_on_reload_hands_an_unsaved_token_to_the_next_account(self):
        shared = FakeVault()
        old = self.account(vault=Window(shared), record=True)
        self.sign_in(old)
        old._stored.vault.failing = True
        self.refresh(old)  # Rotated; the store refused it.
        self.assertTrue(old._unsaved)
        old.close()  # The add-on's unregister; its last save fails too.
        self.assertTrue(old.join(10))
        vault = Window(shared)
        vault.failing = True
        new = self.account(vault=vault, record=True)
        self.assertTrue(new.restore())
        wait_for(lambda: new.view().status != "restoring", timeout=10)
        self.assertEqual(new.view().status, "signed_in", new.view().message)
        self.assertTrue(new._record.holder.held, "the other windows still wait for this one")
        old.close()  # A late second close of the old account releases nothing of the new one's.
        self.assertTrue(new._record.holder.held)
        vault.failing = False
        new._task_save(new._epoch)
        self.assertEqual((new.view().stored, new._unsaved, new._record.holder.held), ("saved", False, False))
        self.assertEqual(shared.token, new._refresh_token)
        self.assertEqual(self.console.revoked, {})

    def test_a_sign_in_in_another_window_wins_over_an_unsaved_token(self):
        shared = FakeVault()
        first = self.account(vault=Window(shared), record=True)
        self.sign_in(first)
        first._stored.vault.failing = True
        self.refresh(first)
        self.assertTrue(first._unsaved)
        second = self.account(vault=Window(shared), record=True)
        self.sign_in(second)  # The person signed in again in another window.
        first._stored.vault.failing = False
        first._task_save(first._epoch)
        self.assertEqual((first._unsaved, first._record.holder.held), (False, False))
        wait_for(lambda: len(self.console.revoked) == 1, timeout=10)  # The given-up token's grant is revoked.
        self.refresh(first)  # Takes the new stored sign-in.
        self.refresh(second)
        self.assertEqual(shared.token, second._refresh_token)
        self.assert_no_reuse()

    def test_a_damaged_record_is_replaced_by_a_sign_out_and_never_presents(self):
        account = self.account(record=True)
        self.sign_in(account)
        Path(self.folder.name, NAME).write_text("{damaged")
        before = len(self.console.refresh_requests())
        with self.assertRaises(TeamError):
            account.access_token(stale=account._access)
        self.assertEqual((account.view().status, len(self.console.refresh_requests())), ("offline", before))
        self.assertIn("Sign out, then sign in again", str(account.view().message))
        account.sign_out()
        wait_for(lambda: not account._pending, timeout=10)
        self.assertEqual(account._record.state()[1], frozenset(), "a new record")
        self.sign_in(account)
        self.refresh(account)
        self.assert_no_reuse()

    def test_quit_waits_only_while_a_token_is_being_presented(self):
        account = self.account(record=True)
        self.sign_in(account)
        with account._refresh_lock:  # Held by a refresh still waiting for the team lock: nothing presented yet.
            closing = threading.Thread(target=account.close)
            closing.start()
            closing.join(2.0)
            self.assertFalse(closing.is_alive(), "quit waited for a refresh that presented nothing")


class RoundThreeTests(AccountCase):
    """Edges a third review found: stale work, a mark whose folder sync fails, a store thread that can't start, an
    older sign-in's stored token after a new sign-in, and a session-only store at a team-lock timeout."""

    def unsaved_account(self):
        vault = Window(FakeVault())
        account = self.account(vault=vault, record=True)
        self.sign_in(account)
        vault.failing = True
        account.access_token(stale=account._access)
        self.assertTrue(account._unsaved)
        return account, vault

    def test_work_of_an_ended_sign_in_touches_nothing_of_the_current_one(self):
        account, _vault = self.unsaved_account()
        account._settle(account._epoch - 1)  # A refresh that began before a sign-out and sign-in.
        account._give_up("", account._epoch - 1, revoke=False)
        self.assertEqual((account._unsaved, account._record.holder.held), (True, True))

    @unittest.skipIf(os.name == "nt", environment_skip("Windows doesn't sync the folder"))
    def test_a_mark_whose_folder_sync_fails_is_still_a_mark(self):
        record = team_refresh_marks.RefreshRecord(self.folder.name, self.console.url)
        real_open = os.open

        fired = []

        def failing(path, *arguments, **keywords):   # Only the folder's sync: the record is read with os.open too.
            if os.fspath(path) == os.fspath(record.path.parent):
                fired.append(path)
                raise OSError(5, "I/O error")
            return real_open(path, *arguments, **keywords)
        with unittest.mock.patch.object(team_refresh_marks.os, "open", failing):
            record.mark("rt_live")  # The rename landed: never reported as unmarked.
        self.assertTrue(fired, "the folder's sync failed as injected")
        self.assertTrue(team_refresh_marks.marked("rt_live", record.state()[1]))

    def test_a_store_call_whose_thread_cant_start_is_a_failed_call(self):
        vault = team_token_store.TokenVault(FakeVault(), self.console.url, "default")
        with unittest.mock.patch.object(team_token_store.threading.Thread, "start",
                                        side_effect=RuntimeError("can't create new thread at interpreter shutdown")):
            with self.assertRaises(KeyStoreError):
                vault.write("rt")
            self.assertEqual(team_token_store.StoredSignIn(vault).write("rt")[0], "failed")

    def two_sign_ins(self, kind):
        """An older sign-in in one window; a newer one in another whose save fails with ``kind``."""
        shared = FakeVault()
        older = self.account(vault=Window(shared), record=True)
        self.sign_in(older)
        newer_vault = Window(shared)
        newer_vault.failing, newer_vault.kind = True, kind
        newer = self.account(vault=newer_vault, record=True)
        self.sign_in(newer)
        return shared, older, newer, newer_vault

    def test_a_new_sign_in_the_store_refuses_leaves_the_older_one_to_the_other_windows(self):
        """Refused outright or timed out, the new sign-in is recorded but not stored: the older one, still valid,
        stays the others' until the new one is saved; then they take the new one."""
        for kind in ("approval", "timeout"):
            with self.subTest(kind=kind):
                shared, older, newer, newer_vault = self.two_sign_ins(kind)
                self.assertTrue(newer._unsaved)
                self.refresh(older)  # The older sign-in keeps renewing.
                newer_vault.failing = False
                newer._task_save(newer._epoch)
                self.assertEqual((newer._unsaved, shared.token), (False, newer._refresh_token))
                self.refresh(older)  # Now takes the newer sign-in from the store.
                self.assertEqual(shared.token, older._refresh_token)
                self.refresh(newer)
                self.assert_no_reuse()

    def test_a_new_sign_in_renewing_unsaved_never_deletes_the_older_ones_stored_token(self):
        """The new sign-in's writes fail but its deletes work: a renewal's failed save deletes a used stored token
        only, never the older sign-in's live one the other windows renew from."""
        shared, older, newer, newer_vault = self.two_sign_ins("approval")
        newer_vault.deletes = True
        older_token = shared.token
        self.refresh(newer)  # Rotated; saving fails again.
        self.assertEqual(shared.token, older_token, "the older sign-in's live token was deleted")
        newer.close()
        self.refresh(older)
        self.assert_no_reuse()

    def test_a_late_landing_another_sign_in_replaced_is_given_up_without_revoking(self):
        """An older-sign-in window's timed-out write lands after a new sign-in elsewhere was stored: the others may
        renew it from the store now, so its window gives it up without revoking it (that would end it for all)."""
        shared = FakeVault()
        vault = Window(shared)
        older = self.account(vault=vault, record=True)
        self.sign_in(older)
        vault.failing, vault.kind = True, "timeout"
        self.refresh(older)  # T1' unsaved; its write "timed out".
        late = older._refresh_token
        newer = self.account(vault=Window(shared), record=True)
        self.sign_in(newer)  # Stored.
        shared.token = late  # The timed-out write lands after all, over the new sign-in.
        vault.failing = False
        older._task_save(older._epoch)
        self.assertEqual((older._unsaved, self.console.revoked), (False, {}), "the stored token was revoked")
        self.refresh(newer)  # Renews from the store: the older sign-in (the documented known limit).
        self.assert_no_reuse()

    def test_a_renewal_of_a_sign_in_not_recorded_yet_is_not_stored(self):
        """While a sign-in's generation isn't recorded, a renewal's answer stays unsaved too (another window would
        adopt it under the older generation); the next commit records and stores it."""
        shared = FakeVault()
        account = self.account(vault=Window(shared), record=True)
        self.sign_in(account)
        before = shared.token
        account._pending_gen = True  # As when the record couldn't be written at the sign-in.
        with account._refresh_lock:
            account._save_rotated({"refresh": "rt_renewed_while_pending", "access": "at", "expires_in": 600},
                                  account._epoch)
        self.assertEqual((shared.token, account._unsaved), (before, True))
        account._refresh_token = "rt_renewed_while_pending"
        account._task_save(account._epoch)
        self.assertEqual((shared.token, account._unsaved, account._pending_gen),
                         ("rt_renewed_while_pending", False, False))

    def test_quitting_with_a_new_sign_in_still_unsaved_leaves_the_older_one_to_the_others(self):
        """The new sign-in's write timed out; at quit, still unsaved, it ends with this window, and the older
        sign-in, still valid in the store, stays the other windows'."""
        shared, older, newer, newer_vault = self.two_sign_ins("timeout")
        newer.close()  # Its last save fails too.
        self.refresh(older)
        self.assert_no_reuse()

    def test_quit_waits_for_a_new_sign_in_being_stored(self):
        shared = FakeVault()
        vault = Window(shared)
        vault.hold = threading.Event()
        account = self.account(vault=vault, record=True)
        signing = threading.Thread(target=lambda: self.sign_in(account), daemon=True)
        signing.start()
        self.assertTrue(vault.writing.wait(30))
        closing = threading.Thread(target=account.close, daemon=True)
        closing.start()
        closing.join(0.5)
        self.assertTrue(closing.is_alive(), "quit didn't wait for the sign-in's store write")
        vault.hold.set()
        closing.join(30)
        signing.join(30)
        self.assertEqual(shared.token, account._refresh_token, "the new sign-in was stored before quitting")

    def test_quit_takes_back_the_mark_of_a_kept_token(self):
        """A kept token the console never used, whose mark couldn't be removed: quit removes it once more, so the
        other windows can still present it after this one is gone."""
        offline = [False]

        def transport(*args, **kwargs):
            if offline[0]:
                raise NotSent("offline")
            return http_transport(*args, **kwargs)
        account = self.account(record=True, transport=transport)
        self.sign_in(account)
        real_unmark = account._record.unmark
        account._record.unmark = lambda token: (_ for _ in ()).throw(RecordError("io", "disk"))
        offline[0] = True
        with self.assertRaises(TeamError):
            account.access_token(stale=account._access)
        kept = account._kept_marked
        self.assertTrue(kept and account._record.holder.held)
        account._record.unmark = real_unmark
        account.close()
        record = team_refresh_marks.RefreshRecord(self.folder.name, self.console.url)
        self.assertFalse(team_refresh_marks.marked(kept, record.state()[1]))
        self.assertFalse(account._record.holder is not None and account._record.holder.held)

    def test_a_session_only_store_at_a_team_lock_timeout_keeps_the_sign_in(self):
        account = self.account(vault=Unusable(), record=True, lock_timeout=0.3)
        with team_lock.held(self.folder.name, 30):
            self.sign_in(account)
        self.assertFalse(account._unsaved)
        account._task_save(account._epoch)
        self.refresh(account)
        self.assertEqual(self.console.revoked, {})

    refresh, assert_no_reuse = EdgeTests.refresh, EdgeTests.assert_no_reuse


if __name__ == "__main__":
    unittest.main()
