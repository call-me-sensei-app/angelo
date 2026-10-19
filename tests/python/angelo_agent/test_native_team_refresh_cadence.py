# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How often a computer rotates the team sign-in (team_account.py, team_access_share.py).

A process refreshes only when it holds no access token with more than
``ACCESS_MARGIN`` left (or the server refused it), whatever it reads; every
Angelo process on the computer shares the latest access token through the OS
store, so N processes started within one token's lifetime cause at most one
refresh; and a shared token is used only with the refresh token it came with,
never when it was refused, expired or another account's.
"""

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import team_access_share, team_lock  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.team_account import ACCESS_MARGIN, TeamAccount  # noqa: E402
from angelo_agent.team_errors import SignInRequired  # noqa: E402
from fake_team_console import OTHER_ORG_ID, FakeVault  # noqa: E402
from test_native_team_account import AccountCase, wait_for  # noqa: E402

LIFETIME = 600  # the console's (and the fake's) access token lifetime


class CadenceCase(AccountCase):
    def setUp(self):
        super().setUp()
        self.shared = FakeVault()

    def process(self, policy=None, access_vault="shared"):
        """One Angelo process on this computer: the same stores and team lock, its own memory."""
        account = TeamAccount(self.console.url, vault=self.vault,
                              access_vault=self.shared if access_vault == "shared" else access_vault,
                              process_lock=team_lock.factory(self.folder.name, 5), clock=self.clock.time,
                              monotonic=self.clock.monotonic, policy=lambda: policy or self.policy)
        self.addCleanup(account.close)
        return account

    def start(self, policy=None):
        """A process starting with the stored sign-in, as at launch."""
        account = self.process(policy)
        self.assertTrue(account.restore())
        wait_for(lambda: account.view().status not in {"restoring"})
        return account

    def refreshes(self):
        return len(self.console.refresh_requests())

    def settle(self, account):
        wait_for(lambda: not account._pending)


class SingleProcessTests(CadenceCase):
    def test_a_process_refreshes_only_near_expiry_whatever_it_reads(self):
        account = self.process()
        self.sign_in(account)
        self.settle(account)
        for _ in range((LIFETIME - ACCESS_MARGIN) // 30 - 1):  # Up to 89 s before expiry.
            self.clock.advance(30)
            account.access_token()
            account.tick(idle=True)
            account.refresh_providers()
            account.refresh_usage()
            account.api_get("/api/me/devices")
            self.settle(account)
        self.assertEqual(self.refreshes(), 0, "providers, usage, devices and ticks reuse the access token")
        self.clock.advance(40)  # 49 s left: under the margin.
        account.access_token()
        self.assertEqual(self.refreshes(), 1)
        self.clock.advance(LIFETIME - ACCESS_MARGIN - 1)
        account.access_token()
        self.assertEqual(self.refreshes(), 1, "the new token is good for another lifetime")

    def test_a_queued_refresh_is_skipped_when_a_caller_already_refreshed(self):
        account = self.process()
        self.sign_in(account)
        account._task_refresh(account._epoch)  # A restore's refresh that waited behind a caller's.
        self.assertEqual(self.refreshes(), 0)
        self.assertTrue(account.view().signed_in)

    def test_a_refused_token_is_replaced_once(self):
        account = self.process()
        self.sign_in(account)
        token = account.access_token()
        self.assertNotEqual(account.access_token(stale=token), token)
        self.assertEqual(self.refreshes(), 1)


class SharedAccessTests(CadenceCase):
    def test_process_starts_within_a_token_lifetime_refresh_at_most_once(self):
        first = self.process()
        self.sign_in(first)
        starts = [self.start() for _ in range(3)]
        for _ in range(4):
            self.clock.advance(100)  # Within the first token's lifetime: 400 s.
            starts.append(self.start())
        self.assertEqual(self.refreshes(), 0, "every start used the sign-in's shared access token")
        views = {(a.view().status, a.view().org_id, a.view().email, a.view().seat, a.view().scopes) for a in starts}
        self.assertEqual(views, {("signed_in", first.view().org_id, first.view().email, True, first.view().scopes)})
        self.assertEqual({a.access_token() for a in starts}, {first.access_token()})
        self.clock.advance(LIFETIME - 400 - ACCESS_MARGIN + 1)  # The shared token is now under the margin.
        later = [self.start() for _ in range(5)]
        self.assertEqual(self.refreshes(), 1, "the first start after it refreshed once; the others used its token")
        self.assertEqual(len({a.access_token() for a in later}), 1)
        self.assertEqual(self.console.revoked, {})

    def test_a_cold_start_of_many_processes_at_once_refreshes_once(self):
        self.sign_in(self.process(access_vault=None))  # Stored refresh token, nothing shared yet.
        accounts = [self.process() for _ in range(6)]
        self.console.refresh_delay = 0.1
        threads = [threading.Thread(target=a.restore) for a in accounts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        wait_for(lambda: all(a.view().signed_in for a in accounts), timeout=15)
        self.assertEqual(self.refreshes(), 1)
        self.assertEqual(self.console.revoked, {})

    def test_running_processes_take_turns_without_rotating_twice(self):
        first = self.process()
        self.sign_in(first)
        second = self.start()
        self.clock.advance(LIFETIME - ACCESS_MARGIN + 1)
        first.access_token()
        second.access_token()  # Its token also nears expiry: it takes the one the first just shared.
        self.assertEqual(self.refreshes(), 1)
        self.assertEqual(first.access_token(), second.access_token())
        self.assertEqual(first._refresh_token, second._refresh_token, "the live refresh token was adopted too")

    def test_a_shared_token_is_never_used_when_refused_or_for_another_sign_in(self):
        first = self.process()
        self.sign_in(first)
        token = first.access_token()
        second = self.start()
        self.assertNotEqual(second.access_token(stale=token), token)
        self.assertEqual(self.refreshes(), 1, "the refused token was not taken back from the store")
        stored = json.loads(self.shared.token)
        self.assertNotEqual(stored["access"], token)
        for value in ("not json", json.dumps(dict(stored, fp="0" * 32)), json.dumps(dict(stored, v=2)),
                      json.dumps(dict(stored, exp=int(self.clock.time()) - 1))):
            with self.subTest(value=value[:30]):
                self.shared.token = value
                self.start()
        self.assertEqual(self.refreshes(), 5, "unreadable, another sign-in's, a future format's or expired: refreshed")

    def test_a_far_future_expiry_is_capped(self):
        first = self.process()
        self.sign_in(first)
        stored = json.loads(self.shared.token)
        self.shared.token = json.dumps(dict(stored, exp=int(self.clock.time()) + 10 * 86400))
        account = self.start()
        self.assertEqual(self.refreshes(), 0)
        self.clock.advance(team_access_share.LIFETIME_CAP - ACCESS_MARGIN + 1)
        account.access_token()
        self.assertEqual(self.refreshes(), 1, "never trusted past LIFETIME_CAP")

    def test_the_managed_policy_still_decides_for_a_shared_answer(self):
        self.sign_in(self.process())
        refused = self.start(policy=Policy(organization=OTHER_ORG_ID, keys=frozenset({"Organization"})))
        self.assertEqual(refused.view().status, "signed_out")
        self.assertIn("requires a different Angelo team", refused.view().message)
        self.assertIsNone(self.shared.token, "a refused sign-in leaves nothing to share")

    def test_sign_out_and_an_ended_sign_in_erase_the_shared_token(self):
        first = self.process()
        self.sign_in(first)
        self.assertIsNotNone(self.shared.token)
        first.sign_out()
        wait_for(lambda: self.shared.token is None)
        again = self.process()
        self.sign_in(again)
        self.assertIsNotNone(self.shared.token)
        self.console.revoke_all()
        self.clock.advance(LIFETIME)
        with self.assertRaises(SignInRequired):
            again.access_token()
        self.assertIsNone(self.shared.token)

    def test_nothing_is_shared_unless_the_store_holds_the_refresh_token(self):
        class Unreadable(FakeVault):
            def write(self, token):
                super().write(token)
                return False  # macOS: another build owns the item.
        self.vault = Unreadable()
        self.sign_in(self.process())
        self.assertIsNone(self.shared.token)

    def test_a_value_too_large_for_windows_is_not_shared(self):
        tokens = {"access": "a" * 1500, "refresh": "r" * 43, "expires_in": 600, "seat": True, "scopes": frozenset({"api"}),
                  "org_id": OTHER_ORG_ID, "org_name": "N" * 200, "user_id": "u", "email": "e" * 200,
                  "org_policy": {"allowVoice": True, "adminContact": "c" * 200}}
        self.assertIsNone(team_access_share.encode(tokens, 0))
        vault = FakeVault("an older answer")
        team_access_share.SharedAccess(vault).write(tokens)
        self.assertIsNone(vault.token, "the older answer is not left in its place")
        small = dict(tokens, access="a" * 700, org_name="Northwind", email="mika@northwind.example",
                     org_policy={"adminContact": ""})
        self.assertLessEqual(len(team_access_share.encode(small, 0).encode()), team_access_share.MAX_BYTES)


_CHILD = """
import json, os, sys, time
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from angelo_agent import team_lock
from angelo_agent.managed_policy import Policy
from angelo_agent.team_account import TeamAccount
from test_native_team_account import FileVault
folder, console = sys.argv[3], sys.argv[4]
account = TeamAccount(console, vault=FileVault(os.path.join(folder, "vault")),
                      access_vault=FileVault(os.path.join(folder, "access")),
                      process_lock=team_lock.factory(folder, 30), policy=Policy)
while not os.path.exists(os.path.join(folder, sys.argv[5])):
    time.sleep(0.005)
account.restore()
deadline = time.monotonic() + 20
while account.view().status == "restoring" and time.monotonic() < deadline:
    time.sleep(0.01)
account.drain()
print(json.dumps({"status": account.view().status}))
"""


class ProcessStartTests(AccountCase):
    """Real Angelo processes starting with one stored sign-in, as test trials and extra windows do."""

    def wave(self, name, processes=4):
        here, addons = str(Path(__file__).resolve().parent), str(ROOT / "scripts/addons_core")
        children = [subprocess.Popen([sys.executable, "-c", _CHILD, addons, here, self.folder.name, self.console.url,
                                      name], stdout=subprocess.PIPE, text=True) for _ in range(processes)]
        time.sleep(0.5)
        Path(self.folder.name, name).touch()
        return [json.loads(child.communicate(timeout=60)[0].strip().splitlines()[-1])["status"] for child in children]

    def test_process_starts_within_ten_minutes_cause_at_most_one_refresh(self):
        from test_native_team_account import FileVault
        account = self.account(vault=FileVault(os.path.join(self.folder.name, "vault")))
        self.sign_in(account)  # Signed in without sharing: the first start must refresh.
        account.close()
        self.console.refresh_delay = 0.2
        self.assertEqual(self.wave("go-1"), ["signed_in"] * 4)
        self.assertEqual(self.wave("go-2"), ["signed_in"] * 4)
        self.assertEqual(len(self.console.refresh_requests()), 1, "8 process starts, one rotation")
        self.assertEqual(self.console.revoked, {})


if __name__ == "__main__":
    unittest.main()
