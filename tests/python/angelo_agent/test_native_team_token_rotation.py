# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A rotated team token the OS store refuses (team_account_refresh.py, team_refresh_marks.py).

Contract: doc/angelo/ai_providers.rst, "A rotated token the store refuses". Three real Angelo processes share one
sign-in through the file vault (``FileVault``, an OS store stand-in) and the sign-in record; one of them rotates
the token while its store writes fail (a locked Keychain, a Credential Manager error). The others never present
the used token, so reuse detection never ends the grant; they wait, and take the token once the write succeeds.
On 1.0.9 code (delete the stored token after a failed write) each scenario ended the grant for every process.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import inspect
import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent.key_store import KeyStoreError  # noqa: E402
from angelo_agent.team_errors import TeamError  # noqa: E402
from fake_team_console import FakeVault  # noqa: E402
from test_native_team_account import AccountCase, FileVault, wait_for  # noqa: E402

try:  # The multi-process scenarios also run against 1.0.9 code (without the record), to show they failed there.
    from angelo_agent import team_refresh_marks
    from angelo_agent.team_errors import BAD_RECORD
    from angelo_agent.team_refresh_marks import RecordError, RefreshRecord
except ImportError:
    team_refresh_marks = BAD_RECORD = RecordError = RefreshRecord = None

LOCKED = "Keychain needs your approval, or the keychain is locked"
HOLD = 1.0  # seconds a held store write waits


class FlakyFileVault(FileVault):
    """``FileVault`` whose writes (and deletes) fail in one process while a flag file names it, as a locked
    Keychain or a Credential Manager error fails one Angelo process's calls. A ``hold-write`` flag makes a write
    wait ``HOLD`` seconds first (a store slow to answer), with a ``writing`` flag up while it waits."""

    def __init__(self, path, name):
        super().__init__(path)
        self.name = name

    def _failing(self, call):
        if os.path.exists("%s.fail-%s.%s" % (self.path, call, self.name)):
            raise KeyStoreError("approval", LOCKED)

    def write(self, token):
        self._failing("write")
        if os.path.exists("%s.hold-write.%s" % (self.path, self.name)):
            Path("%s.writing.%s" % (self.path, self.name)).touch()
            time.sleep(HOLD)
        return super().write(token)

    def delete(self):
        self._failing("delete")
        return super().delete()


# One Angelo process, driven by command files: ``<name>.<n>.cmd`` in, ``<name>.<n>.out`` (JSON) back.
_CHILD = """
import inspect, json, os, sys, threading, time
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from angelo_agent import team_lock
from angelo_agent.managed_policy import Policy
from angelo_agent.team_account import TeamAccount
from angelo_agent.team_errors import TeamError
from test_native_team_account import FileVault
from test_native_team_token_rotation import FlakyFileVault
folder, console, name = sys.argv[3], sys.argv[4], sys.argv[5]
extra = {"record_directory": folder} if "record_directory" in inspect.signature(TeamAccount).parameters else {}
account = TeamAccount(console, vault=FlakyFileVault(os.path.join(folder, "vault"), name),
                      access_vault=FileVault(os.path.join(folder, "access")),
                      process_lock=team_lock.factory(folder, 30), policy=Policy, **extra)
step = 0
while True:
    command_path = os.path.join(folder, "%s.%d.cmd" % (name, step))
    while not os.path.exists(command_path):  # The parent times each command; it removes the folder when done.
        if not os.path.isdir(folder):
            sys.exit(0)
        time.sleep(0.01)
    command = open(command_path).read().strip()
    result = "ok"
    if command == "restore":
        account.restore()
        while account.view().status == "restoring":
            time.sleep(0.01)
    elif command == "refresh":  # As when the access token expired, or the server refused it.
        try:
            account.access_token(stale=account._access)
        except TeamError as error:
            result = type(error).__name__
    elif command == "save":  # The minute's retry (``tick``).
        if hasattr(account, "_task_save"):
            account._task_save(account._epoch)
    elif command == "retry":  # The minute's retry of an offline account (``tick``).
        account._task_refresh(account._epoch)
    elif command == "refresh_and_quit":  # Quit while the console is still answering a refresh.
        def renew():
            try:
                account.access_token(stale=account._access)
            except Exception:  # The closing account doesn't hand out the answer; it stores it.
                pass
        renewing = threading.Thread(target=renew, daemon=True)
        renewing.start()
        deadline = time.monotonic() + 30
        while account._refresh_token and time.monotonic() < deadline:  # Cleared as the token is presented.
            time.sleep(0.005)
    elif command == "quit_while_saving":  # Quit after the console answered, while the store is still writing.
        threading.Thread(target=lambda: account.access_token(stale=account._access), daemon=True).start()
        while not os.path.exists(os.path.join(folder, "vault.writing." + name)):
            time.sleep(0.005)
    elif command == "sign_out":
        account.sign_out()
        while account._pending:
            time.sleep(0.01)
    view = account.view()
    out = {"result": result, "status": view.status, "message": str(view.message), "stored": view.stored,
           "save_problem": str(getattr(view, "save_problem", ""))}
    if command in {"quit", "refresh_and_quit", "quit_while_saving"}:
        account.close()  # As at quit (the add-on's unregister): a refresh in flight finishes; one more save.
    with open(os.path.join(folder, "%s.%d.tmp" % (name, step)), "w") as handle:
        json.dump(out, handle)
    os.replace(os.path.join(folder, "%s.%d.tmp" % (name, step)), os.path.join(folder, "%s.%d.out" % (name, step)))
    if command in {"quit", "refresh_and_quit", "quit_while_saving"}:
        break
    step += 1
"""


class RotationFailureTests(AccountCase):
    """Three real processes, one sign-in; the store refuses one process's rotated token."""

    def setUp(self):
        super().setUp()
        self.vault_path = os.path.join(self.folder.name, "vault")
        self.children, self.steps = {}, {}

    def spawn(self, name):
        child = subprocess.Popen([sys.executable, "-c", _CHILD, str(ROOT / "scripts/addons_core"), str(HERE),
                                  self.folder.name, self.console.url, name])
        self.children[name], self.steps[name] = child, 0
        self.addCleanup(self.reap, child)

    def reap(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(30)

    def do(self, name, command):
        step = self.steps[name]
        self.steps[name] += 1
        temporary = os.path.join(self.folder.name, "%s.%d.cmd.tmp" % (name, step))
        Path(temporary).write_text(command)
        os.replace(temporary, os.path.join(self.folder.name, "%s.%d.cmd" % (name, step)))
        out, deadline = Path(self.folder.name, "%s.%d.out" % (name, step)), time.monotonic() + 90
        while not out.exists():
            self.assertIsNone(self.children[name].poll(), "%s exited before answering %r" % (name, command))
            self.assertLess(time.monotonic(), deadline, "%s didn't answer %r" % (name, command))
            time.sleep(0.01)
        return json.loads(out.read_text())

    def break_store(self, name, *calls):
        for call in calls:
            Path("%s.fail-%s.%s" % (self.vault_path, call, name)).touch()

    def heal(self, name):
        for flag in Path(self.folder.name).glob("vault.fail-*.%s" % name):
            flag.unlink()

    def three_processes(self):
        """A stored sign-in that three processes restored: all hold the same refresh token in memory."""
        account = self.account(vault=FileVault(self.vault_path))
        self.sign_in(account)
        account.close()
        for name in ("p1", "p2", "p3"):
            self.spawn(name)
        for name in ("p1", "p2", "p3"):
            self.assertEqual(self.do(name, "restore")["status"], "signed_in")
        self.assertEqual(len(self.console.refresh_requests()), 1, "the first restore rotated; the others shared it")

    def rotate_while_failing(self, *calls):
        self.break_store("p1", *calls)
        before = len(self.console.refresh_requests())
        answer = self.do("p1", "refresh")
        self.assertEqual((answer["result"], answer["status"]), ("ok", "signed_in"), "the rotation itself succeeds")
        self.assertEqual(len(self.console.refresh_requests()), before + 1)
        self.assertEqual(answer["stored"], "failed")
        return answer

    def assert_told(self, answer):
        self.assertIn("needs your approval", answer["save_problem"], "the person is told why it wasn't saved")

    def assert_waiting(self, name):
        before = len(self.console.refresh_requests())
        answer = self.do(name, "refresh")
        self.assertEqual((len(self.console.refresh_requests()) - before, self.console.revoked), (0, {}),
                         "%s presented a token (the used one: reuse detection ended the grant)" % name)
        self.assertEqual((answer["result"], answer["status"]), ("TeamError", "offline"))
        self.assertIn("couldn't save it yet", answer["message"])

    def assert_everyone_renews(self):
        for name in ("p2", "p3", "p1"):
            answer = self.do(name, "refresh")
            self.assertEqual((answer["result"], answer["status"], answer["stored"]), ("ok", "signed_in", "saved"),
                             name)
        self.assertEqual(self.console.revoked, {})

    def test_a_failed_write_after_rotation_signs_no_process_out(self):
        self.three_processes()
        rotated = self.rotate_while_failing("write")
        self.assert_waiting("p2")
        self.assert_waiting("p3")
        self.assert_told(rotated)
        self.spawn("p4")  # A window opened meanwhile finds no stored token (the holder deleted the used one).
        self.assertEqual(self.do("p4", "restore")["status"], "offline")
        self.assertEqual(self.do("p4", "retry")["status"], "offline", "it keeps waiting for the live holder")
        self.assertEqual(self.do("p1", "save")["stored"], "failed", "still locked: kept in memory, retried")
        self.assertEqual(self.do("p1", "refresh")["status"], "signed_in", "the holder keeps renewing its own token")
        self.assert_waiting("p2")
        self.heal("p1")
        answer = self.do("p1", "save")
        self.assertEqual((answer["stored"], answer["save_problem"]), ("saved", ""))
        self.assert_everyone_renews()
        self.assertEqual(self.do("p4", "retry")["status"], "signed_in", "the waiting window took the saved token")

    def test_a_failed_write_and_delete_leave_the_used_token_stored_but_never_presented(self):
        self.three_processes()
        used = FileVault(self.vault_path).read()
        rotated = self.rotate_while_failing("write", "delete")
        self.assertEqual(FileVault(self.vault_path).read(), used, "the used token is still stored")
        self.assert_waiting("p2")
        self.assert_waiting("p3")
        self.assert_told(rotated)
        self.heal("p1")
        self.assertEqual(self.do("p1", "save")["stored"], "saved")
        self.assert_everyone_renews()

    def test_when_the_holder_quits_unsaved_the_others_ask_to_sign_in_without_presenting(self):
        self.three_processes()
        self.rotate_while_failing("write")
        self.assert_waiting("p2")
        self.do("p1", "quit")  # Its last save attempt fails too.
        self.assertEqual(self.children["p1"].wait(30), 0)
        before = len(self.console.refresh_requests())
        for name in ("p2", "p3"):
            answer = self.do(name, "refresh")
            deadline = time.monotonic() + 15
            while answer["status"] == "offline" and time.monotonic() < deadline:
                time.sleep(0.5)  # Windows may release a dead process's file lock a moment after it exits.
                answer = self.do(name, "retry")
            self.assertEqual(answer["status"], "signed_out", name)
            self.assertIn("Sign in again", answer["message"])
        self.spawn("p4")  # A later launch finds no usable token and presents nothing.
        self.assertEqual(self.do("p4", "restore")["status"], "signed_out")
        self.assertEqual(len(self.console.refresh_requests()), before, "no used token was presented")
        self.assertEqual(self.console.revoked, {})

    def test_quitting_while_the_console_answers_a_refresh_keeps_the_rotated_token(self):
        self.three_processes()
        presented = self.current_refresh_token()
        self.console.refresh_delay = 1.5
        before = len(self.console.refresh_requests())
        self.do("p1", "refresh_and_quit")
        self.assertEqual(self.children["p1"].wait(30), 0)
        self.assertEqual(len(self.console.refresh_requests()), before + 1, "the refresh in flight was presented")
        # The console finished answering p1 (it rotated the token) before anyone else asks.
        wait_for(lambda: self.console.refresh[presented]["used"], timeout=30)
        self.console.refresh_delay = 0
        self.assert_everyone_renews_except("p1")

    def test_quitting_while_the_store_saves_a_rotated_token_waits_for_it(self):
        """The process quits (as Angelo's add-on unregister does at quit) after the console answered, while the store
        is still writing: the worker is a daemon, so without waiting the process ends before the write and the used
        token stays stored for the next process to present (host-tests run 41904229192: four processes sharing a
        profile, three signed out by reuse detection)."""
        self.three_processes()
        Path("%s.hold-write.p1" % self.vault_path).touch()
        before = len(self.console.refresh_requests())
        self.do("p1", "quit_while_saving")
        self.assertEqual(self.children["p1"].wait(30), 0)
        self.assertEqual(len(self.console.refresh_requests()), before + 1, "the console rotated the token")
        self.assert_everyone_renews_except("p1")
        self.spawn("p4")  # A later launch restores the token the quitting process stored.
        self.assertEqual(self.do("p4", "restore")["status"], "signed_in")
        self.assertEqual(self.console.revoked, {})

    def assert_everyone_renews_except(self, gone):
        for name in ("p2", "p3"):
            answer = self.do(name, "refresh")
            self.assertEqual((answer["result"], answer["status"]), ("ok", "signed_in"), name)
        self.assertEqual(self.console.revoked, {}, "the token rotated while %s quit was stored, not lost" % gone)

    def test_a_sign_out_in_another_window_ends_the_unsaved_sign_in_too(self):
        self.three_processes()
        self.rotate_while_failing("write")
        self.assertEqual(self.do("p2", "sign_out")["status"], "signed_out")
        self.heal("p1")
        self.do("p1", "save")  # The record's generation changed: the unsaved token is given up, not saved.
        self.assertIsNone(FileVault(self.vault_path).read(), "the signed-out sign-in was not brought back")
        answer = self.do("p1", "refresh")
        self.assertEqual(answer["status"], "signed_out")
        self.assertEqual(self.do("p3", "refresh")["status"], "signed_out")


@unittest.skipIf(RefreshRecord is None, "1.0.9 code has no sign-in record")
class RecordTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def test_marks_are_fingerprints_never_tokens_and_unmark_reverts(self):
        record = RefreshRecord(self.folder.name, "https://console.example")
        record.mark("rt_secret_token_value")
        text = Path(self.folder.name, team_refresh_marks.NAME).read_text()
        self.assertNotIn("rt_secret_token_value", text)
        self.assertTrue(team_refresh_marks.marked("rt_secret_token_value", record.state()[1]))
        self.assertFalse(team_refresh_marks.marked("rt_other", record.state()[1]))
        record.unmark("rt_secret_token_value")
        self.assertEqual(record.state()[1], frozenset())

    def test_an_unreadable_record_fails_closed_until_a_sign_in_starts_a_new_one(self):
        record = RefreshRecord(self.folder.name, "https://console.example")
        Path(self.folder.name, team_refresh_marks.NAME).write_text("{not json")
        for call in (record.state, lambda: record.mark("rt")):
            with self.assertRaises(RecordError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "invalid")
        generation = record.bump()  # A sign-in or sign-out replaces it.
        self.assertEqual(record.state(), (generation, frozenset()))

    def test_other_consoles_entries_and_unknown_keys_are_kept_and_never_block(self):
        path = Path(self.folder.name, team_refresh_marks.NAME)
        path.write_text(json.dumps({"v": 1, "later": {"x": 1}, "accounts": {"https://other.example": {"gen": 5}}}))
        record = RefreshRecord(self.folder.name, "https://console.example")
        record.mark("rt")
        value = json.loads(path.read_text())
        self.assertEqual((value["later"], value["accounts"]["https://other.example"]), ({"x": 1}, {"gen": 5}))
        self.assertTrue(team_refresh_marks.marked("rt", record.state()[1]))

    def test_a_newer_format_fails_closed_and_a_sign_in_replaces_only_this_consoles_entry(self):
        path = Path(self.folder.name, team_refresh_marks.NAME)
        path.write_text(json.dumps({"v": 2, "accounts": {}}))
        record = RefreshRecord(self.folder.name, "https://console.example")
        with self.assertRaises(RecordError) as caught:
            record.state()
        self.assertEqual(caught.exception.kind, "invalid")
        other = {"gen": "g", "spent": {"fp": 1.0}}
        path.write_text(json.dumps({"v": 1, "accounts": {"https://console.example": {"gen": 3},
                                                         "https://other.example": other}}))
        generation = record.bump()
        self.assertEqual(json.loads(path.read_text())["accounts"]["https://other.example"], other)
        self.assertEqual(record.state(), (generation, frozenset()))

    def test_a_folder_that_cant_be_written_is_an_io_error(self):
        record = RefreshRecord(os.path.join(self.folder.name, "missing"), "https://console.example")
        with self.assertRaises(RecordError) as caught:
            record.mark("rt")
        self.assertEqual(caught.exception.kind, "io")

    def test_old_marks_are_pruned_and_consoles_are_separate(self):
        now = [1_000_000.0]
        record = RefreshRecord(self.folder.name, "https://console.example", clock=lambda: now[0])
        other = RefreshRecord(self.folder.name, "https://dev-console.example", clock=lambda: now[0])
        record.mark("rt_old")
        self.assertEqual(other.state()[1], frozenset())
        now[0] += team_refresh_marks.KEEP + 1
        record.mark("rt_new")
        self.assertEqual(len(record.state()[1]), 1)

    def test_a_replace_refused_for_a_moment_is_retried(self):
        calls = []

        def replace(source, target):
            calls.append(target)
            if len(calls) < 3:
                raise PermissionError(13, "sharing violation")
            os.replace(source, target)
        record = RefreshRecord(self.folder.name, "https://console.example", replace=replace, sleep=lambda _s: None)
        record.mark("rt")
        self.assertEqual(len(calls), 3)


@unittest.skipIf(RefreshRecord is None, "1.0.9 code has no sign-in record")
class SingleProcessTests(AccountCase):
    def test_a_record_that_cant_be_read_presents_nothing_and_says_so(self):
        account = self.account()
        account._record = RefreshRecord(self.folder.name, self.console.url)
        self.sign_in(account)
        Path(self.folder.name, team_refresh_marks.NAME).write_text("garbage")
        before = len(self.console.refresh_requests())
        with self.assertRaises(TeamError) as caught:
            account.access_token(stale=account._access)
        self.assertEqual(caught.exception.message.template, BAD_RECORD)
        self.assertEqual(len(self.console.refresh_requests()), before)
        self.assertEqual(account.view().status, "offline")

    def test_a_write_that_timed_out_but_landed_counts_as_saved(self):
        class Late(FakeVault):
            timing_out = True

            def write(self, token):
                super().write(token)  # It lands, but the call reported a timeout.
                if self.timing_out:
                    raise KeyStoreError("timeout", "The key store didn't respond")
                return True
        vault = Late()
        account = self.account(vault=vault)
        self.sign_in(account)
        self.assertEqual(account.view().stored, "failed")
        vault.timing_out = False
        vault.token = account._refresh_token  # The late write after the delete.
        with contextlib.suppress(Exception):
            account._task_save(account._epoch)
        self.assertEqual((account.view().stored, account._unsaved), ("saved", False))

    def test_an_unsaved_token_another_process_presented_is_given_up_unwritten(self):
        """A write reported as failed landed later; another process adopted and presented it, then saved its own
        newer token. The holder's retry must not write its used token over that one."""
        class Failing(FakeVault):
            def write(self, token):
                raise KeyStoreError("approval", "Keychain needs your approval, or the keychain is locked")
        vault = Failing()
        account = self.account(vault=vault)
        account._record = RefreshRecord(self.folder.name, self.console.url)
        self.sign_in(account)
        self.assertTrue(account._unsaved)
        account._record.mark(account._refresh_token)  # Presented by the other process.
        vault.token = "rt_newer_token_saved_by_the_other_process"
        account._task_save(account._epoch)
        self.assertFalse(account._unsaved)
        self.assertEqual(vault.token, "rt_newer_token_saved_by_the_other_process")
        self.assertFalse(account._record.holder.held, "the holder lock is released")


if __name__ == "__main__":
    unittest.main()
