# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The client against each shape of the console contract.

Every ``invalid_grant`` ``error_reason`` the console sends (and the same refusal from today's production console,
which has no ``error_reason`` and only the descriptions), the organization's app policy as token answers carry it
(all five fields, ``adminContact`` null or text) and a console without it, and the allow screen's ``org_hint`` and
``login_hint``. The fake console follows the console's grant and OAuth error answers for each case.
"""

import json
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import managed_policy  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.team_errors import SignInRequired, oauth_error  # noqa: E402
from fake_team_console import CLIENT_ID, ORG_ID, USED_CODE  # noqa: E402
from test_native_team_account import DIRECT, AccountCase, browse, wait_for  # noqa: E402

ENDED = "signed out of your Angelo team (from the console"
REMOVED = "no longer a member of this Angelo team"
LAPSED = "expired after a long time unused"
REUSED = "for safety: its sign-in was used twice"


def post_token(console, form):
    """``(status, body)`` of a token request sent straight to the fake console."""
    request = urllib.request.Request(console.url + "/api/oauth/token", data=urllib.parse.urlencode(form).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with DIRECT.open(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class OAuthErrorTests(unittest.TestCase):
    """``oauth_error`` for every reason in INVALID_GRANT_REASONS, and for the descriptions alone."""

    def test_every_reason_and_every_description_alone(self):
        cases = [  # (error_reason, error_description, expected with the reason, expected without it)
            ("signed_out", "This sign-in has ended. Sign in again.", ENDED, ENDED),
            ("member_removed", "You're no longer a member of this organization.", REMOVED, REMOVED),
            ("expired", "This sign-in has ended. Sign in again.", LAPSED, ENDED),
            ("reuse_detected", "This sign-in has ended. Sign in again.", REUSED, ENDED),
            ("code_reused", "This sign-in has ended. Sign in again.", REUSED, ENDED),
            ("code_reused", USED_CODE, REUSED, ENDED),
            ("expired", "Unknown or expired code.", LAPSED, ENDED),
            ("a_future_reason", "This sign-in has ended. Sign in again.", ENDED, ENDED),
        ]
        for reason, description, with_reason, without in cases:
            with self.subTest(reason=reason, description=description):
                body = {"error": "invalid_grant", "error_description": description, "error_reason": reason}
                error = oauth_error(400, json.dumps(body).encode())
                self.assertIsInstance(error, SignInRequired)
                self.assertIn(with_reason, error.message)
                self.assertEqual(error.reason, reason if reason != "a_future_reason" else "")
                del body["error_reason"]
                error = oauth_error(400, json.dumps(body).encode())
                self.assertIn(without, error.message)
                self.assertEqual(error.reason, "member_removed" if without == REMOVED else "",
                                 "a console without error_reason names only a removed member, by its description")

    def test_a_malformed_reason_reads_as_signed_out(self):
        for reason in ([], {"x": 1}, 7, None, ""):
            with self.subTest(reason=reason):
                body = {"error": "invalid_grant", "error_description": "x", "error_reason": reason}
                error = oauth_error(400, json.dumps(body).encode())
                self.assertIn(ENDED, error.message)
                self.assertEqual(error.reason, "")

    def test_a_reason_is_never_read_from_another_error(self):
        body = {"error": "invalid_request", "error_description": "Invalid token request.", "error_reason": "expired"}
        error = oauth_error(400, json.dumps(body).encode())
        self.assertNotIsInstance(error, SignInRequired)
        self.assertIn("invalid_request", error.message)


class RefreshRefusalTests(AccountCase):
    """Each way the console ends a sign-in, at the app's next renewal, with and without ``error_reason``."""

    def ended(self, start, expected, reasons):
        self.console.error_reasons, self.console.member = reasons, True
        self.console.refresh_lifetime, self.console.revoked = None, {}
        account = self.account(vault=self.vault)
        prepare = start(account)
        # The sign-in's background work (providers, grant key) must finish before the clock moves; otherwise it
        # can renew the access token in between and the renewal under test never happens (seen on Windows).
        wait_for(lambda: not account._pending)
        self.clock.advance(600)
        if prepare:
            prepare()
        with self.assertRaises(SignInRequired):
            account.access_token()
        view = account.view()
        self.assertEqual(view.status, "signed_out")
        self.assertIn(expected, view.message)
        self.assertIsNone(self.vault.token, "the ended sign-in leaves nothing in the key store")
        return account

    def test_each_revocation_reads_as_its_reason(self):
        def revoked(reason):
            def start(account):
                self.sign_in(account)
                self.console.revoke_all(reason)
            return start

        def removed(account):
            self.sign_in(account)
            self.console.remove_member()

        def lapsed(account):
            self.console.refresh_lifetime = 0  # The console's 60 days, passed.
            self.sign_in(account)

        def reused(account):
            self.sign_in(account)
            token = account._refresh_token
            self.clock.advance(600)
            account.access_token()  # Rotates: ``token`` is now a used one.

            def present_again():
                account._refresh_token, account._access = token, ""
            return present_again

        cases = [  # (case, start, with error_reason, a console without it)
            ("Your devices", revoked("signed out"), ENDED, ENDED),
            ("an admin", revoked("admin"), ENDED, ENDED),
            ("newer sign-ins", revoked("replaced by newer sign-ins"), ENDED, ENDED),
            ("left the organization", revoked("left the organization"), REMOVED, ENDED),
            ("removed by the directory", revoked("removed by the directory"), REMOVED, ENDED),
            ("a manual revoke", revoked("database cleanup"), ENDED, ENDED),
            ("member removed", removed, REMOVED, REMOVED),
            ("60 days unused", lapsed, LAPSED, ENDED),
            ("refresh token reuse", reused, REUSED, ENDED),
        ]
        for name, start, with_reason, without in cases:
            for reasons, expected in ((True, with_reason), (False, without)):
                with self.subTest(case=name, error_reasons=reasons):
                    self.vault.token = None
                    self.ended(start, expected, reasons)

    def test_a_replayed_code_ends_the_sign_in_it_made(self):
        for reasons, expected in ((True, REUSED), (False, ENDED)):
            with self.subTest(error_reasons=reasons):
                captured = {}

                def start(account):
                    url = account.begin_sign_in(device="Test computer")
                    captured["callback"] = self.console.callback(url)
                    browse(captured["callback"])
                    # Also the providers read that follows a sign-in on the account's worker: left running, it can
                    # renew the access token after the clock moves and before the replay ends the sign-in.
                    wait_for(lambda: account.view().signed_in and account.view().providers is not None)

                    def replay():
                        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(captured["callback"]).query))
                        redirect = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["redirect_uri"][0]
                        status, body = post_token(self.console, {
                            "grant_type": "authorization_code", "code": query["code"], "code_verifier": "v" * 43,
                            "client_id": CLIENT_ID, "redirect_uri": redirect})
                        self.assertEqual((status, body["error"], body["error_description"]),
                                         (400, "invalid_grant", USED_CODE))
                        self.assertEqual(body.get("error_reason"), "code_reused" if reasons else None)
                        self.assertIn("code reuse", self.console.revoked.values())
                    return replay

                self.ended(start, expected, reasons)


class ExchangeRefusalTests(AccountCase):
    def test_a_code_the_console_doesnt_accept_shows_the_expired_page(self):
        for reasons in (True, False):
            with self.subTest(error_reasons=reasons):
                self.console.error_reasons = reasons
                account = self.account()
                url = account.begin_sign_in(device="Test computer")
                status, _final, page = browse(self.console.callback(url).replace("code=code_", "code=not-issued_"))
                self.assertEqual(status, 400)
                self.assertIn("This sign-in expired", page)
                wait_for(lambda: account.view().status == "signed_out" and account.view().message)
                self.assertIn("didn't accept this sign-in", account.view().message)

    def test_leaving_the_organization_before_the_exchange_says_so(self):
        self.console.error_reasons = True
        account = self.account()
        url = account.begin_sign_in(device="Test computer")
        callback = self.console.callback(url)
        self.console.member = False
        status, _final, page = browse(callback)
        self.assertEqual(status, 502)
        self.assertIn("no longer a member", page)
        self.assertNotIn("This sign-in expired", page)
        wait_for(lambda: account.view().status == "signed_out" and account.view().message)
        self.assertIn(REMOVED, account.view().message)
        self.assertIn("Contact your admin", account.view().message)


class AppPolicyShapeTests(AccountCase):
    """``policy`` in token answers: the console's ``readAppPolicy`` shape (every field) and what the app makes of it."""

    def test_an_organization_that_never_set_a_policy_blocks_nothing(self):
        self.console.app_policy({})
        self.assertEqual(self.console.policy, {"allowPersonalConnections": True, "allowLocalCliAccess": True,
                                               "allowVoice": True, "allowCollaborators": True, "adminContact": None})
        account = self.account()
        self.sign_in(account)
        self.assertEqual(account.view().org_policy, {"allowPersonalConnections": True, "allowLocalCliAccess": True,
                                                     "allowVoice": True, "allowCollaborators": True,
                                                     "adminContact": ""})
        managed_policy.set_organization_source(lambda: account.view().org_policy)
        policy = managed_policy.current()
        self.assertTrue(policy.allow_personal_connections and policy.allow_local_cli_access and policy.allow_voice
                        and policy.allow_collaborators)
        self.assertEqual((policy.admin_contact, policy.organization_keys), ("", frozenset()))

    def test_every_renewal_carries_the_current_policy(self):
        self.console.app_policy({"adminContact": "  IT help desk, ext. 4400  "})
        account = self.account()
        self.sign_in(account)
        self.assertEqual(account.view().org_policy["adminContact"], "IT help desk, ext. 4400",
                         "a contact the console accepts is shown as it is")
        self.console.app_policy({"allowPersonalConnections": False, "allowVoice": False, "adminContact": None})
        self.clock.advance(600)
        account.access_token()
        self.assertEqual(account.view().org_policy, {"allowPersonalConnections": False, "allowLocalCliAccess": True,
                                                     "allowVoice": False, "allowCollaborators": True,
                                                     "adminContact": ""})
        managed_policy.set_organization_source(lambda: account.view().org_policy)
        policy = managed_policy.current()
        self.assertFalse(policy.allow_personal_connections or policy.allow_voice)
        self.assertEqual(policy.organization_keys, frozenset({"allowPersonalConnections", "allowVoice"}))

    def test_a_contact_the_console_would_refuse_is_not_shown(self):
        account = self.account()
        for contact in ("‮evil.example", "it@x.example second line", "tab\there", "x" * 201):
            with self.subTest(contact=contact[:20]):
                self.console.policy = {"allowPersonalConnections": True, "allowLocalCliAccess": True,
                                       "allowVoice": True, "allowCollaborators": True, "adminContact": contact}
                account.sign_out()
                self.sign_in(account)
                shown = account.view().org_policy["adminContact"]
                self.assertEqual(shown, "x" * 200 if contact == "x" * 201 else "")

    def test_a_missing_switch_keeps_the_default_and_a_malformed_one_is_off(self):
        self.console.policy = {"allowVoice": "no", "adminContact": 7}
        account = self.account()
        self.sign_in(account)
        self.assertEqual(account.view().org_policy, {"allowPersonalConnections": True, "allowLocalCliAccess": True,
                                                     "allowVoice": False, "allowCollaborators": True,
                                                     "adminContact": ""})

    def test_the_machine_policy_reads_admin_contact_by_the_same_rule(self):
        fields, problems = managed_policy.parse({"AdminContact": "IT help desk"})
        self.assertEqual((fields.get("admin_contact"), problems), ("IT help desk", []))
        fields, problems = managed_policy.parse({"AdminContact": "‮evil.example"})
        self.assertNotIn("admin_contact", fields)
        self.assertTrue(problems)


class AuthorizeHintTests(AccountCase):
    def test_org_hint_is_the_managed_organization(self):
        self.policy = Policy(organization=ORG_ID, keys=frozenset({"Organization"}))
        account = self.account()
        self.sign_in(account)
        self.assertEqual(self.console.authorize_hints, [(ORG_ID, "")])

    def test_login_hint_names_the_account_the_console_signed_out_and_only_that(self):
        account = self.account()
        self.sign_in(account)
        email = account.view().email
        self.console.revoke_all("signed out")
        self.clock.advance(600)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.sign_in(account)
        self.assertEqual(self.console.authorize_hints, [("", ""), ("", email)])
        account.sign_out()  # The person chose to sign out: the next sign-in may be anyone.
        self.sign_in(account)
        self.assertEqual(self.console.authorize_hints[-1], ("", ""))

    def test_an_account_the_policy_refused_is_never_hinted(self):
        account = self.account()
        self.sign_in(account)
        self.console.revoke_all("signed out")
        self.clock.advance(600)
        with self.assertRaises(SignInRequired):
            account.access_token()
        self.policy = Policy(email_domains=("hyperbond.example",), keys=frozenset({"EmailDomains"}))
        url = account.begin_sign_in(device="Test computer")
        self.assertIn("login_hint=", url)
        browse(self.console.callback(url))
        wait_for(lambda: account.view().status == "signed_out" and "@hyperbond.example" in account.view().message)
        self.policy = Policy()
        self.assertNotIn("login_hint=", account.begin_sign_in(device="Test computer"))


if __name__ == "__main__":
    unittest.main()
