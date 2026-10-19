# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account against an in-process fake console (team_account.py, team_token_store.py).

Sign-in through the real loopback listener (with RFC 9207 ``iss``), token
exchange, strict single-flight refresh that never presents a refresh token
twice (also across real processes), adoption of another instance's rotated
token, the managed policy's organization and email checks, revoke on
sign-out, grant keys shared between windows and their renewal policy,
providers/available and the refresh token's OS-store item (through a fake
backend; never a file). No real console or key store is used.
"""

import html
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.error
from types import SimpleNamespace
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import key_store, managed_policy, team_lock, team_token_store  # noqa: E402
from angelo_agent.key_store import KeyStoreError  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_errors import SignInRequired, TeamError  # noqa: E402
from angelo_agent.team_http import http_transport  # noqa: E402
from fake_team_console import ORG_ID, OTHER_ORG_ID, FakeConsole, FakeVault  # noqa: E402

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def browse(url):
    """What a browser shows after following the callback's redirect: ``(status, final URL, page)``."""
    try:
        with DIRECT.open(url, timeout=30) as response:
            return response.status, response.geturl(), html.unescape(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, error.geturl(), html.unescape(error.read().decode())


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class Clock:
    def __init__(self):
        self.wall, self.mono = 1_800_000_000.0, 1000.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += seconds
        self.mono += seconds


class AccountCase(unittest.TestCase):
    def setUp(self):
        self.console = FakeConsole()
        self.addCleanup(self.console.close)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.clock = Clock()
        self.vault = FakeVault()
        self.policy = Policy()
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)

    def account(self, vault=None, transport=http_transport, memory_only=False, grant_vault=None, lock_timeout=5):
        vault = None if memory_only else (self.vault if vault is None else vault)
        account = TeamAccount(self.console.url, vault=vault, grant_vault=grant_vault, transport=transport,
                              process_lock=team_lock.factory(self.folder.name, lock_timeout),
                              clock=self.clock.time, monotonic=self.clock.monotonic, policy=lambda: self.policy)
        self.addCleanup(self.stop, account)
        return account

    @staticmethod
    def stop(account):
        """Close ``account`` and wait for its worker: a task still running after the test would open the lock file
        in the folder the test is removing (seen as "Directory not empty" on a loaded machine)."""
        account.close()
        if account._worker is not None:
            account._worker.join(10)

    def sign_in(self, account, **callback):
        url = account.begin_sign_in(device="Test computer")
        status, final, page = browse(self.console.callback(url, **callback))
        self.assertEqual((status, final.rsplit("/", 1)[-1]), (200, "result"), "the code left the address bar")
        self.assertIn("You're signed in" if self.console.seat else "without a seat", page)
        self.assertIn("Northwind Studio", page)
        wait_for(lambda: account.view().signed_in and account.view().providers is not None)
        return url

    def current_refresh_token(self):
        with self.console.lock:
            return next(t for t, e in reversed(self.console.refresh.items()) if not e["used"])


class SignInTests(AccountCase):
    def test_sign_in_keeps_the_access_token_in_memory_and_the_refresh_token_in_the_store(self):
        account = self.account()
        url = self.sign_in(account)
        self.assertTrue(url.startswith(self.console.url + "/oauth/authorize?"))
        view = account.view()
        self.assertEqual((view.org_name, view.email, view.seat), ("Northwind Studio", "mika@northwind.example", True))
        self.assertEqual(view.providers, ("anthropic", "google", "openai", "tripo"), "unknown ids are dropped")
        self.assertEqual(view.stored, "saved")
        self.assertEqual(self.vault.token, self.current_refresh_token())
        exchange = next(r for r in self.console.requests if r[2] and r[2].get("grant_type") == "authorization_code")
        self.assertEqual(set(exchange[2]), {"grant_type", "code", "code_verifier", "client_id", "redirect_uri"})
        self.assertTrue(exchange[2]["redirect_uri"].startswith("http://127.0.0.1:"))
        access = account.access_token()
        self.assertNotIn(access, repr(view))
        self.assertNotIn(self.vault.token, repr(view))
        for path in Path(self.folder.name).rglob("*"):  # Only the lock file; never a token in a file.
            self.assertNotIn(self.vault.token.encode(), path.read_bytes())
            self.assertNotIn(access.encode(), path.read_bytes())
        self.assertEqual(account.redact("x %s y" % access), "x [REDACTED] y")

    def test_browser_cancel_and_cancel_button_leave_nothing_signed_in(self):
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        status, _final, page = browse(self.console.callback(url, decision="deny"))
        self.assertIn("Sign-in cancelled", page)
        wait_for(lambda: account.view().status == "signed_out")
        self.assertEqual(account.view().message, "Sign-in was canceled in the browser.")
        account.begin_sign_in(device="Test computer")
        account.cancel_sign_in()
        wait_for(lambda: account.view().status == "signed_out")
        self.assertEqual(account.view().message, "Sign-in canceled.")
        self.assertIsNone(self.vault.token)

    def test_an_expired_code_is_refused_without_signing_in(self):
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        callback = self.console.callback(url)
        status, _final, page = browse(callback.replace("code=code_", "code=not-issued_"))
        self.assertEqual(status, 400)
        self.assertIn("This sign-in expired", page)
        wait_for(lambda: account.view().status == "signed_out")
        self.assertIn("didn't accept this sign-in", account.view().message)

    def test_a_callback_without_the_consoles_issuer_is_refused(self):
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        callback = self.console.callback(url)
        for forged in (callback.replace("iss=", "other="), callback.replace("iss=http", "iss=https")):
            status, final, page = browse(forged)
            self.assertEqual((status, final.rsplit("/", 1)[-1]), (400, "mismatch"))
            self.assertIn("This sign-in doesn't match", page)
        self.assertEqual(account.view().status, "signing_in", "a mix-up answer never ends the sign-in")
        self.assertEqual(browse(callback)[0], 200)
        wait_for(lambda: account.view().signed_in)


class RefreshTests(AccountCase):
    def test_concurrent_callers_share_one_refresh_and_the_token_rotates(self):
        account = self.account()
        self.sign_in(account)
        first_refresh, first_access = self.vault.token, account.access_token()
        self.clock.advance(600)  # The access token expired.
        self.console.refresh_delay = 0.3
        results, threads = [], [threading.Thread(target=lambda: results.append(account.access_token()))
                                for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(self.console.refresh_requests()), 1)
        self.assertEqual(len(set(results)), 1)
        self.assertNotEqual(results[0], first_access)
        self.assertNotEqual(self.vault.token, first_refresh)
        self.assertTrue(self.console.refresh[first_refresh]["used"])
        self.assertFalse(self.console.revoked)

    def test_a_refused_access_token_is_refreshed_once(self):
        account = self.account()
        self.sign_in(account)
        old = account.access_token()
        new = account.access_token(stale=old)
        self.assertNotEqual(new, old)
        self.assertEqual(account.access_token(stale=old), new, "another caller's refresh is reused")
        self.assertEqual(len(self.console.refresh_requests()), 1)

    def test_a_lost_answer_ends_the_sign_in_instead_of_presenting_the_token_again(self):
        account = self.account()
        self.sign_in(account)
        self.clock.advance(600)
        self.console.fail_next_refresh = "drop"
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertEqual(account.view().status, "signed_out")
        self.assertIn("connection dropped", account.view().message)
        self.assertIsNone(self.vault.token)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertEqual(len(self.console.refresh_requests()), 1, "the used token was never presented again")
        self.assertFalse(self.console.revoked)

    def test_an_unprocessed_refusal_keeps_the_token(self):
        account = self.account()
        self.sign_in(account)
        self.clock.advance(600)
        token = self.vault.token
        self.console.fail_next_refresh = 503
        with self.assertRaises(TeamError) as caught:
            account.access_token()
        self.assertNotIsInstance(caught.exception, SignInRequired)
        account.access_token()
        requests = self.console.refresh_requests()
        self.assertEqual([r[2]["refresh_token"] for r in requests], [token, token])
        self.assertFalse(self.console.revoked)
        self.assertEqual(account.view().status, "signed_in")

    def test_an_unreachable_console_keeps_the_token(self):
        from angelo_agent.team_errors import NotSent

        def offline(*args, **kwargs):
            raise NotSent("offline")
        account = self.account()
        self.sign_in(account)
        account._transport = offline
        self.clock.advance(600)
        with self.assertRaisesRegex(TeamError, "Couldn't reach"):
            account.access_token()
        account._transport = http_transport
        self.assertTrue(account.access_token())
        self.assertFalse(self.console.revoked)

    def test_another_instance_rotated_token_is_adopted(self):
        first = self.account()
        self.sign_in(first)
        second = self.account()
        self.assertTrue(second.restore())
        wait_for(lambda: second.view().signed_in)
        self.assertEqual(self.vault.token, self.current_refresh_token())
        self.clock.advance(600)
        self.assertTrue(first.access_token(), "the first instance used the second's newer token")
        self.assertFalse(self.console.revoked)
        self.assertEqual(len(self.console.refresh_requests()), 2)

    def test_a_revoked_grant_signs_out_and_erases_the_stored_token(self):
        account = self.account()
        self.sign_in(account)
        self.console.revoke_all()
        self.clock.advance(600)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertEqual(account.view().status, "signed_out")
        self.assertIn("signed out of your Angelo team", account.view().message)
        self.assertIsNone(self.vault.token)

    def test_a_removed_member_is_told_so_and_the_grant_ends(self):
        account = self.account()
        self.sign_in(account)
        self.console.remove_member()
        self.clock.advance(600)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertEqual(account.view().status, "signed_out")
        self.assertIn("no longer a member", account.view().message)
        self.assertIn("Contact your admin", account.view().message)
        self.assertEqual(set(self.console.revoked.values()), {"member removed"})
        self.assertIsNone(self.vault.token)

    def test_another_window_holding_the_lock_never_makes_this_one_present_its_token(self):
        account = self.account(lock_timeout=0.3)
        self.sign_in(account)
        self.clock.advance(600)
        before = len(self.console.refresh_requests())
        with team_lock.held(self.folder.name):
            with self.assertRaisesRegex(TeamError, "Another Angelo window") as caught:
                account.access_token()
        self.assertNotIsInstance(caught.exception, SignInRequired)
        self.assertEqual(len(self.console.refresh_requests()), before, "nothing was presented")
        self.assertTrue(account.access_token(), "the kept token works once the lock is free")
        self.assertFalse(self.console.revoked)

    def test_a_failed_save_never_leaves_a_used_token_to_present(self):
        class Failing(FakeVault):
            def write(self, token):
                self.calls.append("write")
                raise KeyStoreError("failed", "Key store error")

        class Stuck(Failing):
            def delete(self):
                self.calls.append("delete")
                raise KeyStoreError("failed", "Key store error")
        for vault in (Failing("rt_stale_token_of_an_earlier_grant"), Stuck("rt_stale_token_of_an_earlier_grant")):
            with self.subTest(vault=type(vault).__name__):
                account = self.account(vault=vault)
                self.sign_in(account)
                self.assertEqual(account.view().stored, "failed")
                self.assertIn("delete", vault.calls)
                before = len(self.console.refresh_requests())
                self.clock.advance(600)
                self.assertTrue(account.access_token())
                presented = self.console.refresh_requests()[before][2]["refresh_token"]
                self.assertNotEqual(presented, "rt_stale_token_of_an_earlier_grant")

    def test_restore_without_a_stored_token_stays_signed_out(self):
        account = self.account()
        self.assertTrue(account.restore())
        wait_for(lambda: account.view().status == "signed_out")
        self.assertEqual(self.console.requests, [])
        self.assertFalse(self.account(memory_only=True).restore(), "memory-only: nothing to restore")


class SignOutAndLateResultTests(AccountCase):
    def test_sign_out_forgets_at_once_then_revokes_and_erases(self):
        account = self.account()
        self.sign_in(account)
        account.sign_out()
        self.assertEqual(account.view().status, "signed_out")
        self.assertEqual(account.grant_key(), "")
        wait_for(lambda: self.vault.token is None and self.console.revoked)
        revoke = next(r for r in self.console.requests if r[1] == "/api/oauth/revoke")
        self.assertEqual(set(revoke[2]), {"token", "client_id"})
        with self.assertRaises(SignInRequired):
            account.access_token()

    def test_a_late_result_of_an_abandoned_sign_in_is_revoked(self):
        account = self.account()
        self.sign_in(account)
        with self.console.lock:
            self.console.grants["grant-late"] = {"org": ORG_ID, "device": "", "scopes": {"api"}}
            late = self.console._issue("grant-late")["refresh_token"]
        tokens = {"access": "acc.late-access-token-value", "refresh": late, "expires_in": 600,
                  "seat": True, "scopes": frozenset({"api"}), "org_id": ORG_ID, "org_name": "N", "user_id": "u",
                  "email": ""}
        account.sign_out()
        self.assertFalse(account._accept(tokens, account._epoch - 1))
        wait_for(lambda: "grant-late" in self.console.revoked)
        self.assertEqual(account.view().status, "signed_out", "the late result never signed in")


class GrantKeyTests(AccountCase):
    def test_grant_key_is_fetched_with_json_and_renewed_by_policy(self):
        account = self.account()
        self.sign_in(account)
        self.assertTrue(account.grant_key_due(idle=False), "no key yet: always due")
        account.tick(idle=True)
        wait_for(lambda: account.grant_key())
        key = account.grant_key()
        self.assertRegex(key, r"^agk_[A-Za-z0-9_-]{43}$")
        request = next(r for r in self.console.requests if r[1] == "/api/oauth/grant-key")
        self.assertEqual(request[3]["Content-Type"], "application/json")
        self.assertTrue(request[3]["Authorization"].startswith("Bearer eyJ"))
        self.assertFalse(account.grant_key_due(idle=True))
        self.clock.advance(43200 - 3 * 3600)  # 3 h left.
        self.assertFalse(account.grant_key_due(idle=True))
        self.clock.advance(3600 + 600)        # 1 h 50 min left: renew at an idle point only.
        self.assertTrue(account.grant_key_due(idle=True))
        self.assertFalse(account.grant_key_due(idle=False))
        self.clock.advance(6000)              # 10 min left: renew even mid-turn.
        self.assertTrue(account.grant_key_due(idle=False))
        self.assertEqual(account.grant_key(), key, "the old key stays valid until it expires")
        self.clock.advance(600)
        self.assertEqual(account.grant_key(), "", "never hand out a key in its last minute")

    def test_no_seat_or_no_coding_provider_means_no_grant_key(self):
        self.console.seat = False
        account = self.account()
        self.sign_in(account)
        self.assertFalse(account.grant_key_due(idle=True))
        account._enqueue("grant_key")
        wait_for(lambda: account.view().grant_error)
        self.assertIn("don't have a seat", account.view().grant_error)
        self.assertIn("Contact your admin for access", account.view().grant_error)
        self.console.seat, self.console.providers = True, ["tripo", "meshy"]
        other = self.account(vault=FakeVault())
        self.sign_in(other)
        self.assertFalse(other.grant_key_due(idle=True))


class ConsoleApiTests(AccountCase):
    def test_api_get_retries_once_after_401_then_signs_out(self):
        account = self.account()
        self.sign_in(account)
        seen = []

        def refusing(method, url, headers=None, body=None, timeout=20):
            if url.endswith("/api/orgs/%s/library/stats" % ORG_ID):
                seen.append(headers["Authorization"])
                return 401, {}, b'{"error": {"code": "UNAUTHENTICATED", "message": "expired"}}'
            return http_transport(method, url, headers, body, timeout)
        account._transport = refusing
        with self.assertRaises(SignInRequired):
            account.api_get("/api/orgs/%s/library/stats" % ORG_ID)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1], "the retry used a refreshed token")
        self.assertEqual(account.view().status, "signed_out")

    def test_the_persons_own_usage_is_read_for_the_account_menu(self):
        account = self.account()
        self.sign_in(account)
        account.tick(idle=True)
        wait_for(lambda: account.view().usage)
        self.assertEqual(account.view().usage["tokens"], 1250000)
        self.assertEqual(account.view().usage["streak"], 3)
        self.assertFalse(account.refresh_usage(), "at most once a minute")
        self.clock.advance(61)
        self.assertTrue(account.refresh_usage())
        account.sign_out()
        self.assertIsNone(account.view().usage)

    def test_api_get_only_takes_console_api_paths(self):
        account = self.account()
        for path in ("/oauth/authorize", "https://elsewhere/api/x", "/api/../x", "/api//x"):
            with self.assertRaises(ValueError):
                account.api_get(path)

    def test_a_non_member_gets_no_providers(self):
        account = self.account()
        self.sign_in(account)
        with account._lock:
            account._update(org_id=OTHER_ORG_ID)
        account.refresh_providers()
        wait_for(lambda: account.view().providers == ())
        self.assertIn("no longer a member", account.view().providers_error)


class PolicyTests(AccountCase):
    def test_a_sign_in_to_another_organization_is_refused_and_revoked(self):
        self.policy = Policy(organization=OTHER_ORG_ID, keys=frozenset({"Organization"}))
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        status, _final, page = browse(self.console.callback(url))
        self.assertEqual(status, 403)
        self.assertIn("This team isn't allowed here", page)
        self.assertIn("requires a different Angelo team", page)
        wait_for(lambda: account.view().status == "signed_out" and account.view().message)
        self.assertIn("requires a different Angelo team", account.view().message)
        wait_for(lambda: self.console.revoked)
        self.assertEqual(set(self.console.revoked.values()), {"signed out"})
        self.assertIsNone(self.vault.token)

    def test_the_email_domain_is_checked_and_the_right_account_signs_in(self):
        self.policy = Policy(email_domains=("hyperbond.example",), keys=frozenset({"EmailDomains"}))
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        self.assertIn("@hyperbond.example", browse(self.console.callback(url))[2])
        wait_for(lambda: account.view().status == "signed_out" and account.view().message)
        self.assertIn("@hyperbond.example", account.view().message)
        self.policy = Policy(organization=ORG_ID, email_domains=("northwind.example",),
                             keys=frozenset({"Organization", "EmailDomains"}))
        self.sign_in(account)

    def test_an_invalid_managed_value_stops_sign_in_before_the_browser(self):
        self.policy = managed_policy.combine([("profile", {"Organization": "not-a-uuid"})])
        account = self.account()
        with self.assertRaisesRegex(RuntimeError, "invalid Organization setting"):
            account.begin_sign_in(device="Test computer")
        self.assertEqual(account.view().status, "signed_out")
        self.assertEqual(self.console.requests, [])

    def test_a_policy_set_after_sign_in_ends_the_stored_sign_in_at_its_next_refresh(self):
        account = self.account()
        self.sign_in(account)
        self.policy = Policy(organization=OTHER_ORG_ID, keys=frozenset({"Organization"}))
        self.clock.advance(600)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertIn("requires a different Angelo team", account.view().message)
        wait_for(lambda: self.console.revoked)


class OrganizationPolicyTests(AccountCase):
    """The console's app policy and error_reason, and a console without them."""

    def test_the_organizations_policy_tightens_the_machine_policy_and_survives_offline(self):
        from angelo_agent.team_org_policy import OrganizationPolicy
        self.console.policy = {"allowPersonalConnections": False, "allowLocalCliAccess": True, "allowVoice": "no",
                               "allowCollaborators": True, "adminContact": "it@northwind.example", "future": 1}
        account = self.account()
        self.sign_in(account)
        self.assertEqual(account.view().org_policy, {"allowPersonalConnections": False, "allowLocalCliAccess": True,
                                                     "allowVoice": False, "allowCollaborators": True,
                                                     "adminContact": "it@northwind.example"})
        org = OrganizationPolicy(account, self.folder.name)
        managed_policy.set_organization_source(org.current)
        policy = managed_policy.current()
        self.assertFalse(policy.allow_personal_connections or policy.allow_voice)
        self.assertTrue(policy.allow_local_cli_access and policy.managed)
        self.assertEqual(policy.admin_contact, "it@northwind.example")
        org.remember(account.view())
        restarted = OrganizationPolicy(SimpleNamespace(console_url=account.console_url,
                                                       view=lambda: SimpleNamespace(signed_in=False, status="offline")),
                                       self.folder.name)
        self.assertFalse(restarted.current()["allowPersonalConnections"], "going offline never lifts it")
        account.sign_out()
        org.remember(account.view())
        self.assertFalse(os.path.exists(org.path), "signing out forgets it")
        self.assertIsNone(org.current())

    def test_a_console_without_a_policy_leaves_the_machine_policy(self):
        account = self.account()
        self.sign_in(account)
        self.assertIsNone(account.view().org_policy)
        managed_policy.set_organization_source(lambda: account.view().org_policy)
        self.assertEqual(managed_policy.current(), managed_policy.machine())

    def test_error_reason_names_the_cause_and_a_console_without_it_still_does(self):
        for reasons in (True, False):
            with self.subTest(error_reasons=reasons):
                self.console.error_reasons, self.console.member = reasons, True
                account = self.account(vault=FakeVault())
                self.sign_in(account)
                self.console.remove_member()
                self.clock.advance(600)
                with self.assertRaises(SignInRequired):
                    account.access_token()
                self.assertIn("no longer a member", account.view().message)
        self.console.error_reasons, self.console.member = True, True
        account = self.account(vault=FakeVault())
        self.sign_in(account)
        token = account._refresh_token
        self.clock.advance(600)
        account.access_token()
        account._refresh_token = token  # Presenting a used token again: reuse detection.
        account._access = ""
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.assertIn("used twice", account.view().message)


class SharedGrantKeyTests(AccountCase):
    def test_windows_share_one_key_and_a_refused_key_is_replaced(self):
        shared = FakeVault()
        first = self.account(grant_vault=shared)
        self.sign_in(first)
        first.tick(idle=True)
        wait_for(lambda: first.grant_key())
        second = self.account(grant_vault=shared)
        self.assertTrue(second.restore())
        wait_for(lambda: second.view().signed_in and second.view().providers is not None)
        second.tick(idle=True)
        wait_for(lambda: second.grant_key())
        self.assertEqual(second.grant_key(), first.grant_key(), "the second window reused the first's key")
        self.assertEqual(len(self.console.issued_keys()), 1)
        self.assertNotIn(first.grant_key(), repr(first.view()))
        second.refused_grant_key(second.grant_key())
        self.assertEqual(second.grant_key(), "")
        self.assertTrue(second.grant_key_due(idle=False))

    def test_a_shared_key_of_another_person_is_never_adopted(self):
        from angelo_agent.team_grants import encode
        shared = FakeVault(encode("agk_" + "x" * 43, self.clock.time() + 40000, (ORG_ID, "someone-else")))
        account = self.account(grant_vault=shared)
        self.sign_in(account)
        account.tick(idle=True)
        wait_for(lambda: account.grant_key())
        self.assertNotEqual(account.grant_key(), "agk_" + "x" * 43)
        self.assertEqual(len(self.console.issued_keys()), 1)

    def test_sign_out_erases_the_shared_key(self):
        shared = FakeVault()
        account = self.account(grant_vault=shared)
        self.sign_in(account)
        account.tick(idle=True)
        wait_for(lambda: account.grant_key())
        account.sign_out()
        wait_for(lambda: shared.token is None)


_CHILD = """
import json, os, sys, time
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from angelo_agent import team_lock
from angelo_agent.managed_policy import Policy
from angelo_agent.team_account import TeamAccount
from test_native_team_account import FileVault
import contextlib
folder, console, locked = sys.argv[3], sys.argv[4], sys.argv[5] == "1"
lock = team_lock.factory(folder, 30) if locked else contextlib.nullcontext
account = TeamAccount(console, vault=FileVault(os.path.join(folder, "vault")), process_lock=lock, policy=Policy)
while not os.path.exists(os.path.join(folder, "go")):
    time.sleep(0.005)
account.restore()
deadline = time.monotonic() + 20
while account.view().status in {"restoring"} and time.monotonic() < deadline:
    time.sleep(0.01)
account.drain()  # As at exit: the rotated token is stored before the process ends.
print(json.dumps({"status": account.view().status}))
"""


class FileVault:
    """A ``TokenVault`` stand-in that several test processes share (a file in a temporary folder)."""

    def __init__(self, path):
        self.path, self.reason = path, ""

    def read(self):
        try:
            with open(self.path) as handle:
                return handle.read() or None
        except FileNotFoundError:
            return None

    def write(self, token):
        with open(self.path + ".tmp", "w") as handle:
            handle.write(token)
        os.replace(self.path + ".tmp", self.path)
        return True

    def delete(self):
        try:
            os.remove(self.path)
        except FileNotFoundError:
            return False
        return True


class MultiProcessRefreshTests(AccountCase):
    """Real Angelo processes restoring one stored sign-in at the same moment."""

    def race(self, locked, processes=4):
        account = self.account(vault=FileVault(os.path.join(self.folder.name, "vault")))
        self.sign_in(account)
        account.close()
        self.console.refresh_delay = 0.2
        here, addons = str(Path(__file__).resolve().parent), str(ROOT / "scripts/addons_core")
        children = [subprocess.Popen([sys.executable, "-c", _CHILD, addons, here, self.folder.name, self.console.url,
                                      "1" if locked else "0"], stdout=subprocess.PIPE, text=True)
                    for _ in range(processes)]
        time.sleep(0.5)  # Every child imported and waits for the go file.
        Path(self.folder.name, "go").touch()
        return [json.loads(child.communicate(timeout=60)[0].strip().splitlines()[-1])["status"] for child in children]

    def test_concurrent_processes_rotate_in_turn_and_never_reuse_a_token(self):
        statuses = self.race(locked=True)
        self.assertEqual(statuses, ["signed_in"] * 4)
        self.assertEqual(self.console.revoked, {}, "no refresh token was presented twice")
        self.assertEqual(len(self.console.refresh_requests()), 4)

    def test_without_the_lock_the_same_race_trips_reuse_detection(self):
        statuses = self.race(locked=False)  # Control: proves the test above can fail.
        self.assertIn("refresh token reuse", self.console.revoked.values())
        self.assertIn("signed_out", statuses)


class TokenVaultTests(unittest.TestCase):
    class Backend:
        def __init__(self):
            self.items, self.service, self.comment, self.calls, self.held = {}, "Angelo API key", "", [], []

        def read(self, account, interactive=False):
            self.held.append(key_store.STORE_CALLS.locked())
            self.calls.append(("read", account, interactive))
            return self.items.get((self.service, account))

        def write(self, account, secret, label, interactive=False):
            self.calls.append(("write", account, label, interactive))
            self.items[(self.service, account)] = secret
            return True

        def delete(self, account, interactive=False):
            self.calls.append(("delete", account, interactive))
            return self.items.pop((self.service, account), None) is not None

    def test_the_refresh_token_is_a_separate_item_kind_per_console_and_profile(self):
        backend = self.Backend()
        with unittest.mock.patch.object(key_store, "open_store", return_value=(backend, "")):
            opened, reason = team_token_store.open_backend({}, "darwin", False)
        self.assertIs(opened, backend)
        self.assertEqual((backend.service, backend.comment, reason),
                         ("studio.hyperbond.angelo.team-sign-in", team_token_store.ACCOUNT_COMMENT, ""))
        vault = team_token_store.TokenVault(backend, "https://console.angelo.studio", "profile-a")
        vault.write("rt_secret")
        self.assertEqual(vault.read(), "rt_secret")
        self.assertTrue(all(held for held in backend.held), "every call holds STORE_CALLS")
        self.assertTrue(all(call[-1] is False for call in backend.calls), "never interactive")
        self.assertEqual(vault.label, "Angelo team sign-in · console.angelo.studio")
        other_console = team_token_store.TokenVault(backend, "http://localhost:5280", "profile-a")
        other_profile = team_token_store.TokenVault(backend, "https://console.angelo.studio", "profile-b")
        self.assertIsNone(other_console.read())
        self.assertIsNone(other_profile.read())
        self.assertTrue(vault.account.endswith("@profile-a"))
        self.assertTrue(vault.delete())
        self.assertIsNone(vault.read())

    def test_store_policy_reasons_name_the_sign_in(self):
        with unittest.mock.patch.object(key_store, "open_store", return_value=(None, "Keychain is turned off for this "
                                        "session (ANGELO_DISABLE_KEYCHAIN); keys stay in memory.")):
            backend, reason = team_token_store.open_backend({}, "darwin", False)
        self.assertIsNone(backend)
        self.assertIn("your team sign-in lasts until Angelo quits", reason)

    def test_a_hung_store_times_out_and_an_unavailable_one_switches_to_memory(self):
        release = threading.Event()

        class Hung(self.Backend):
            def read(self, account, interactive=False):
                release.wait(5)
                return None
        vault = team_token_store.TokenVault(Hung(), "https://console.angelo.studio", "p", timeout=0.2)
        with self.assertRaises(KeyStoreError) as caught:
            vault.read()
        self.assertEqual(caught.exception.kind, "timeout")
        release.set()

        class Unavailable(self.Backend):
            def probe(self):
                raise KeyStoreError("unavailable", "No keyring service")
        vault = team_token_store.TokenVault(Unavailable(), "https://console.angelo.studio", "p")
        with self.assertRaises(KeyStoreError):
            vault.read()
        self.assertEqual(vault.reason, "No keyring service")
        with self.assertRaises(KeyStoreError):
            vault.write("rt")

    def test_another_builds_item_is_erased_when_it_cant_be_deleted(self):
        class Owned(self.Backend):
            def delete(self, account, interactive=False):
                raise KeyStoreError("approval", "needs approval")

            def erase(self, account, interactive=False):
                self.items[(self.service, account)] = ""
                return True
        backend = Owned()
        vault = team_token_store.TokenVault(backend, "https://console.angelo.studio", "p")
        vault.write("rt_secret")
        self.assertTrue(vault.delete())
        self.assertIsNone(vault.read(), "an erased item reads as absent")


if __name__ == "__main__":
    unittest.main()
