# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team sign-in: PKCE, state and the loopback callback listener's lifecycle (team_signin.py).

The listener is Angelo's only network listener: bound to 127.0.0.1 only while
a sign-in waits for the browser, closed once the browser saw the outcome, on a
cancel or at the timeout. The callback redirects to a clean /result (the code
never stays in the address bar), which shows the outcome the app reports. These
tests drive the real listener with real loopback requests.
"""

import base64
import hashlib
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import team_signin  # noqa: E402
from angelo_agent.team_signin import LoopbackListener  # noqa: E402


DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Never through a configured proxy.


def get(url, timeout=5):
    try:
        with DIRECT.open(url, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def refused(port):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) != 0


class PkceTests(unittest.TestCase):
    def test_s256_challenges_and_fresh_pairs(self):
        # S256: BASE64URL(SHA256(ASCII(verifier))) without padding (RFC 7636 section 4.2).
        verifier = "a" * 43
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(team_signin.challenge_for(verifier), expected)
        self.assertNotIn("=", expected)
        pairs = {team_signin.pkce_pair() for _ in range(20)}
        self.assertEqual(len(pairs), 20)
        for verifier, challenge in pairs:
            self.assertEqual(len(verifier), 43)
            self.assertRegex(verifier, r"^[A-Za-z0-9_-]{43}$")
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            self.assertEqual(challenge, expected)
        states = {team_signin.new_state() for _ in range(20)}
        self.assertEqual(len(states), 20)
        self.assertTrue(all(len(state) == 22 for state in states))

    def test_authorize_url_matches_the_contract(self):
        url = team_signin.authorize_url("https://console.angelo.studio", "http://127.0.0.1:5000/callback",
                                        "challenge", "state-1", "Mika's MacBook")
        parsed = urlsplit(url)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ("https", "console.angelo.studio", "/oauth/authorize"))
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.assertEqual(query, {"client_id": "angelo-desktop", "redirect_uri": "http://127.0.0.1:5000/callback",
                                 "response_type": "code", "code_challenge": "challenge",
                                 "code_challenge_method": "S256", "state": "state-1",
                                 "scope": "api generate offline_access", "device_name": "Mika's MacBook"})

    def test_a_managed_organization_is_sent_as_a_hint(self):
        url = team_signin.authorize_url("https://console.angelo.studio", "http://127.0.0.1:5000/callback",
                                        "challenge", "state-1", "Mac", org_hint="6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f")
        self.assertEqual(parse_qs(urlsplit(url).query)["org_hint"], ["6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f"])

    def test_device_name_is_the_short_printable_host_name(self):
        self.assertEqual(team_signin.device_name("Mikas-MacBook.local"), "Mikas-MacBook")
        self.assertEqual(team_signin.device_name("\x00\x01"), "this computer")
        self.assertEqual(len(team_signin.device_name("x" * 300)), 64)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


RAW = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def raw(url):
    """``(status, headers, body)`` without following a redirect."""
    try:
        with RAW.open(url, timeout=5) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()


class ListenerTests(unittest.TestCase):
    def listener(self, timeout=30):
        listener = LoopbackListener("expected-state", timeout=timeout, issuer="https://console.angelo.studio").start()
        self.addCleanup(listener.close)
        return listener

    def callback(self, listener, **values):
        values.setdefault("state", "expected-state")
        values.setdefault("iss", "https://console.angelo.studio")
        return listener.redirect_uri + "?" + "&".join("%s=%s" % item for item in values.items())

    def test_binds_loopback_ipv4_only_on_a_random_port(self):
        listener = self.listener()
        host, port = listener._server.socket.getsockname()
        self.assertEqual(host, "127.0.0.1")
        self.assertGreater(port, 0)
        self.assertEqual(listener.redirect_uri, "http://127.0.0.1:%d/callback" % port)
        self.assertEqual(listener._server.socket.family, socket.AF_INET)
        other = self.listener()
        self.assertNotEqual(other.port, port)

    def test_the_code_leaves_the_address_bar_and_the_result_page_shows_the_outcome(self):
        listener = self.listener()
        status, headers, body = raw(self.callback(listener, code="the-code"))
        self.assertEqual((status, headers["Location"], body), (303, "/result", ""))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(listener.wait(5), {"code": "the-code"})
        self.assertTrue(listener.listening, "it stays for the browser's /result")
        threading.Timer(0.2, lambda: listener.finish("signed_in", device="Mika's Mac", org="Northwind")).start()
        status, headers, body = raw("http://127.0.0.1:%d/result" % listener.port)
        self.assertEqual(status, 200)
        self.assertIn("You&#x27;re signed in", body)
        self.assertIn("<b>Northwind</b>", body)
        self.assertNotIn("the-code", body)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        wait_until(lambda: not listener.listening and refused(listener.port))  # Closed once the browser saw it.

    def test_wrong_path_state_or_issuer_shows_a_page_and_keeps_waiting(self):
        listener = self.listener()
        self.assertEqual(raw("http://127.0.0.1:%d/other" % listener.port)[0], 404)
        for values in ({"code": "x", "state": "wrong"}, {"code": "x", "iss": "https://elsewhere.example"},
                       {"code": "x", "iss": ""}, {"state": "expected-state"}):
            status, headers, _body = raw(self.callback(listener, **values))
            self.assertEqual((status, headers["Location"]), (303, "/mismatch"), values)
        status, _headers, body = raw("http://127.0.0.1:%d/mismatch" % listener.port)
        self.assertEqual(status, 400)
        self.assertIn("This sign-in doesn&#x27;t match", body)
        request = urllib.request.Request(self.callback(listener, code="x"), method="POST", data=b"")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            DIRECT.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 405)
        self.assertTrue(listener.listening)
        self.assertEqual(raw(self.callback(listener, code="good"))[0], 303)
        self.assertEqual(listener.wait(5), {"code": "good"})
        self.assertEqual(raw(self.callback(listener, code="again"))[1]["Location"], "/already")
        self.assertIn("already complete", raw("http://127.0.0.1:%d/already" % listener.port)[2])
        self.assertEqual(listener.wait(5), {"code": "good"}, "only the first answer counts")

    def test_browser_cancel_is_reported(self):
        listener = self.listener()
        self.assertEqual(raw(self.callback(listener, error="access_denied"))[0], 303)
        self.assertEqual(listener.wait(5), {"error": "access_denied"})
        listener.finish("denied")
        self.assertIn("Sign-in cancelled", raw("http://127.0.0.1:%d/result" % listener.port)[2])

    def test_a_result_the_app_never_reports_says_it_is_still_finishing(self):
        listener = self.listener()
        raw(self.callback(listener, code="c"))
        with patch.object(team_signin, "OUTCOME_WAIT", 0.2):
            self.assertIn("Finishing your sign-in", raw("http://127.0.0.1:%d/result" % listener.port)[2])

    def test_timeout_closes_the_listener(self):
        listener = self.listener(timeout=0.3)
        started = time.monotonic()
        self.assertEqual(listener.wait(), {"error": "timeout"})
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(listener.listening)
        self.assertTrue(refused(listener.port))

    def test_close_cancels_and_frees_the_port(self):
        listener = self.listener()
        port = listener.port
        listener.close()
        listener.close()  # Idempotent.
        self.assertEqual(listener.wait(1), {"error": "cancelled"})
        self.assertTrue(refused(port))

    def test_no_request_is_logged(self):
        import contextlib
        import io
        listener = self.listener()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            raw(self.callback(listener, code="secret-code", state="wrong"))
            raw(self.callback(listener, code="secret-code"))
            listener.wait(5)
        self.assertNotIn("secret-code", stderr.getvalue())


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


if __name__ == "__main__":
    unittest.main()
