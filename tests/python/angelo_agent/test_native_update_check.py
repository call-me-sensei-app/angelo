# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update check (update_check.py, update_state.py) and its HTTP client (app_http.py).

The decision logic runs over an injected fetch and clock; the client runs against a
loopback server (nothing leaves the computer), including through a recording
HTTP proxy. Covered: the schedule (launch, daily, backoff, jitter), the switches
(machine policy, explicit offline, the person's own toggle), conditional requests,
every refusal leaving the stored state untouched, skip / later, restart with a
tampered cache, and a state write racing a check.
"""

import json
import ssl
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_fixtures as fx  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402
from angelo_agent import app_http, managed_policy, update_check, update_feed, update_state  # noqa: E402
from angelo_agent.app_http import NetworkError, Response  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.update_check import CURRENT, DISABLED, FAILED, OFFLINE, UNAVAILABLE, UPDATE, Checker  # noqa: E402

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp()
MAC = "macos-arm64"


class CheckCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.seed, self.keys = fx.new_key()
        self.answers, self.calls, self.now = [], [], NOW

    def feed(self, **kwargs):
        return fx.envelope(fx.payload(**kwargs), self.seed)

    def fetch(self, method, url, headers=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def reply(self, *answers):
        self.answers = list(answers)

    def checker(self, **kwargs):
        kwargs.setdefault("keys", self.keys)
        kwargs.setdefault("current", "1.0.0")
        kwargs.setdefault("platform", MAC)
        kwargs.setdefault("policy", Policy())
        return Checker(self.folder.name, fetch=self.fetch, clock=lambda: self.now, **kwargs)

    def ok(self, body=None, etag='"v1"', **kwargs):
        return Response(200, {"etag": etag}, body or self.feed(**kwargs))

    def state(self):
        return update_state.load(self.folder.name)


class ScheduleTests(unittest.TestCase):
    def state(self, **fields):
        return update_state.UpdateState(**fields)

    def test_first_ever_check_runs_at_launch(self):
        self.assertTrue(update_check.due(self.state(), NOW, launch=True))
        self.assertTrue(update_check.due(self.state(), NOW))

    def test_launch_skips_a_check_that_just_succeeded_or_just_failed(self):
        recent = self.state(last_attempt=NOW - 600, last_success=NOW - 600)
        self.assertFalse(update_check.due(recent, NOW, launch=True))
        old = self.state(last_attempt=NOW - 4000, last_success=NOW - 4000)
        self.assertTrue(update_check.due(old, NOW, launch=True))
        failed = self.state(last_attempt=NOW - 100, last_success=NOW - 99999, failures=1)
        self.assertFalse(update_check.due(failed, NOW, launch=True))
        self.assertTrue(update_check.due(self.state(last_attempt=NOW - 400, last_success=NOW - 99999, failures=1),
                                         NOW, launch=True))

    def test_the_daily_check_is_a_day_after_success_with_ten_percent_jitter(self):
        state = self.state(last_attempt=NOW, last_success=NOW)
        low, high = update_check.next_due(state, lambda: 0.0), update_check.next_due(state, lambda: 1.0)
        self.assertAlmostEqual(low - NOW, 0.9 * 86400)
        self.assertAlmostEqual(high - NOW, 1.1 * 86400)
        self.assertFalse(update_check.due(state, NOW + 80000, rng=lambda: 0.5))
        self.assertTrue(update_check.due(state, NOW + 95000, rng=lambda: 0.5))

    def test_failures_back_off_one_two_four_eight_then_twenty_four_hours(self):
        hours = [(update_check.next_due(self.state(last_attempt=1000, failures=n)) - 1000) / 3600 for n in range(1, 8)]
        self.assertEqual(hours, [1, 2, 4, 8, 24, 24, 24])


class SwitchTests(CheckCase):
    def test_the_machine_policy_stops_every_request_even_a_manual_one(self):
        self.reply(self.ok())
        policy = managed_policy.combine([("test", {"DisableUpdateCheck": True})])
        for manual in (False, True):
            result = self.checker(policy=policy).check(manual=manual)
            self.assertEqual((result.kind, result.detail), (DISABLED, "policy"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.checker(policy=policy).pending().kind, DISABLED)
        self.assertEqual(self.checker(policy=policy).crash_report_url(), "")

    def test_explicit_offline_mode_stops_every_request(self):
        self.reply(self.ok())
        result = self.checker(explicit_offline=True).check(manual=True)
        self.assertEqual((result.kind, result.detail, self.calls), (OFFLINE, "offline_mode", []))

    def test_the_persons_own_toggle_stops_automatic_checks_only(self):
        self.reply(self.ok())
        checker = self.checker()
        checker.set_automatic(False)
        self.assertEqual(checker.check().detail, "automatic_off")
        self.assertEqual(self.calls, [])
        self.assertEqual(checker.check(manual=True).kind, UPDATE)
        checker.set_automatic(True)
        self.assertEqual(checker.check().kind, UPDATE)

    def test_a_build_without_a_signing_key_cannot_check(self):
        self.reply(self.ok())
        result = self.checker(keys={}).check(manual=True)
        self.assertEqual((result.kind, result.detail, self.calls), (UNAVAILABLE, "no_keys", []))


class CheckTests(CheckCase):
    def test_a_newer_release_is_found_stored_and_offered(self):
        self.reply(self.ok())
        result = self.checker().check()
        self.assertEqual(result.kind, UPDATE)
        self.assertEqual((result.offer.release.version, result.skipped, result.snoozed), ("1.1.0", False, False))
        state = self.state()
        self.assertEqual((state.last_success, state.last_attempt, state.failures, state.last_sequence, state.etag),
                         (NOW, NOW, 0, 3, '"v1"'))
        self.assertEqual(self.calls[0][2]["Accept"], "application/json")
        self.assertNotIn("If-None-Match", self.calls[0][2])

    def test_the_second_check_is_conditional_and_a_304_reuses_the_verified_cache(self):
        self.reply(self.ok(), Response(304, {}, b""))
        checker = self.checker()
        checker.check()
        self.now += 90000
        result = checker.check()
        self.assertEqual((result.kind, result.offer.release.version), (UPDATE, "1.1.0"))
        self.assertEqual(self.calls[1][2]["If-None-Match"], '"v1"')
        self.assertEqual(self.state().last_success, self.now)

    def test_up_to_date_and_newer_than_the_feed(self):
        self.reply(self.ok())
        self.assertEqual(self.checker(current="1.1.0").check().kind, CURRENT)
        self.assertEqual(self.checker(current="1.2.0").check().kind, CURRENT)

    def test_offline_and_server_failures_are_quiet_results_that_count_toward_backoff(self):
        for error, kind in ((NetworkError(app_http.OFFLINE), OFFLINE), (NetworkError(app_http.TIMEOUT), OFFLINE),
                            (NetworkError(app_http.TLS), FAILED), (NetworkError(app_http.PROTOCOL), FAILED)):
            with self.subTest(error=error.kind):
                self.reply(error)
                self.assertEqual(self.checker().check().kind, kind)
        self.assertEqual(self.state().failures, 4)
        self.reply(Response(500, {}, b""))
        self.assertEqual(self.checker().check().detail, "http_500")
        self.reply(Response(404, {}, b""))
        self.assertEqual(self.checker().check().detail, "http_404")
        self.assertEqual(self.state().failures, 6)
        self.reply(self.ok())
        self.checker().check()
        self.assertEqual(self.state().failures, 0)

    def test_a_refused_feed_changes_nothing_that_counts(self):
        self.reply(self.ok())
        self.checker().check()
        stored = self.state()
        other, _ = fx.new_key()
        cases = {"bad_signature": fx.envelope(fx.payload(sequence=9), other), "rollback": self.feed(sequence=1),
                 "malformed": b"<html>captive portal</html>", "expired": self.feed(expires="2026-01-01T00:00:00Z")}
        for code, body in cases.items():
            with self.subTest(code=code):
                self.reply(self.ok(body, etag='"evil"'))
                result = self.checker().check()
                self.assertEqual((result.kind, result.detail), (FAILED, code))
                now = self.state()
                self.assertEqual((now.envelope, now.etag, now.last_sequence), (stored.envelope, stored.etag, 3))

    def test_a_captive_portal_or_wrong_host_is_not_an_offer(self):
        self.reply(self.ok(b'{"format": "angelo-update-feed", "version": 1, "key_id": "test-key"}'))
        self.assertEqual(self.checker().check().kind, FAILED)

    def test_the_environment_override_still_needs_a_signature(self):
        self.reply(self.ok())
        checker = self.checker(environ={"ANGELO_UPDATE_FEED_URL": "http://127.0.0.1:9/updates/v1/stable.json"})
        self.assertEqual(checker.check().kind, UPDATE)
        self.assertEqual(self.calls[0][1], "http://127.0.0.1:9/updates/v1/stable.json")

    def test_the_default_feed_is_the_stable_file_on_angelos_site(self):
        self.assertEqual(update_check.feed_url(Policy(), {}), "https://angelo.studio/updates/v1/stable.json")
        self.reply(self.ok())
        self.assertEqual(self.checker(environ={}).check().kind, UPDATE)
        self.assertEqual([c[1] for c in self.calls], ["https://angelo.studio/updates/v1/stable.json"])

    def test_an_organizations_mirror_wins_over_the_environment_and_still_needs_the_signature(self):
        mirror = Policy(update_feed_url="https://updates.northwind.example/angelo/stable.json", keys=frozenset({"UpdateFeedURL"}))
        environ = {"ANGELO_UPDATE_FEED_URL": "http://127.0.0.1:9/updates/v1/stable.json"}
        self.assertEqual(update_check.feed_url(mirror, environ), "https://updates.northwind.example/angelo/stable.json")
        self.reply(self.ok(fx.envelope(fx.payload(), fx.new_key()[0])))   # Signed by a key this build does not trust.
        result = self.checker(policy=mirror, environ=environ).check()
        self.assertEqual((result.kind, result.detail), (FAILED, "bad_signature"))
        self.assertEqual(self.calls[0][1], "https://updates.northwind.example/angelo/stable.json")

    def test_an_unusable_mirror_stops_the_check_instead_of_falling_back(self):
        policy = managed_policy.combine([("profile", {"UpdateFeedURL": "http://updates.northwind.example/stable.json"})])
        self.assertEqual((policy.update_feed_url, policy.problems), (managed_policy.INVALID, ("UpdateFeedURL",)))
        self.reply(self.ok())
        for manual in (False, True):
            result = self.checker(policy=policy).check(manual=manual)
            self.assertEqual((result.kind, result.detail), (DISABLED, "policy"))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.checker(policy=policy).crash_report_url(), "")


class ChoiceTests(CheckCase):
    def found(self, **kwargs):
        self.reply(self.ok(**kwargs))
        return self.checker().check().offer

    def test_later_hides_the_notice_for_three_days_but_a_manual_check_shows_it(self):
        offer = self.found()
        self.checker().snooze(offer)
        result = self.checker().check()
        self.assertTrue(result.snoozed and not result.skipped)
        self.assertFalse(self.checker().check(manual=True).snoozed)
        self.assertEqual(self.state().snoozed_version, "")
        self.checker().snooze(offer)
        self.now += 3 * 86400 + 1
        self.assertFalse(self.checker().check().snoozed)

    def test_skip_hides_one_version_until_a_newer_one(self):
        offer = self.found()
        self.checker().skip(offer)
        self.assertTrue(self.checker().check().skipped)
        self.reply(self.ok(version="1.2.0", sequence=4))
        newer = self.checker().check()
        self.assertEqual((newer.offer.release.version, newer.skipped), ("1.2.0", False))

    def test_a_critical_release_cannot_be_skipped_only_paused_for_a_day(self):
        document = fx.payload()
        document["latest"]["critical"] = True
        self.reply(self.ok(fx.envelope(document, self.seed)))
        offer = self.checker().check().offer
        self.checker().skip(offer)
        self.assertEqual(self.state().skipped_version, "")
        result = self.checker().check()
        self.assertTrue(result.snoozed and not result.skipped)
        self.now += 86400 + 1
        self.assertFalse(self.checker().check().snoozed)

    def test_a_choice_made_while_a_check_is_in_flight_is_not_lost(self):
        offer = self.found()
        checker = self.checker()

        def racing_fetch(method, url, headers=None, **kwargs):
            checker.skip(offer)   # The person clicks Skip while the worker waits for the network.
            return self.ok(version="1.1.0", sequence=5)

        checker.fetch = racing_fetch
        checker.check()
        state = self.state()
        self.assertEqual((state.skipped_version, state.last_sequence), ("1.1.0", 5))


class RestartTests(CheckCase):
    def test_the_offer_survives_a_restart_and_is_verified_again(self):
        self.reply(self.ok())
        self.checker().check()
        self.calls.clear()
        pending = self.checker().pending()
        self.assertEqual((pending.kind, pending.offer.release.version, self.calls), (UPDATE, "1.1.0", []))
        state = self.state()
        state.envelope = state.envelope.replace("A", "B", 40)   # A damaged or tampered cache never becomes an offer.
        update_state.save(self.folder.name, state)
        self.assertEqual(self.checker().pending().kind, CURRENT)
        self.assertEqual(self.checker(keys={}).pending().kind, CURRENT)

    def test_the_crash_report_endpoint_comes_only_from_a_verified_feed(self):
        self.assertEqual(self.checker().crash_report_url(), "")
        self.reply(self.ok(services={"crash_reports": "https://api.angelo.studio/v1/crash-reports"}))
        self.checker().check()
        self.assertEqual(self.checker().crash_report_url(), "https://api.angelo.studio/v1/crash-reports")
        other, other_keys = fx.new_key()
        self.assertEqual(self.checker(keys=other_keys).crash_report_url(), "")

    def test_a_damaged_state_file_reads_as_defaults(self):
        path = Path(self.folder.name) / update_state.STATE_FILE
        for text in ("not json", "[1, 2]", json.dumps({"automatic": "yes", "last_attempt": -5, "failures": True,
                                                       "envelope": 7, "last_sequence": 2.5})):
            path.write_text(text)
            self.assertEqual(update_state.load(self.folder.name), update_state.UpdateState())
        path.write_text(json.dumps({"automatic": False, "failures": 3, "last_attempt": 12, "skipped_version": "1.2.0"}))
        state = update_state.load(self.folder.name)
        self.assertEqual((state.automatic, state.failures, state.last_attempt, state.skipped_version),
                         (False, 3, 12.0, "1.2.0"))
        # A folder below a regular file cannot exist on any system (a root-level path is writable on Windows).
        self.assertFalse(update_state.save(str(path / "below"), state))


class ClientTests(unittest.TestCase):
    def test_a_get_returns_status_headers_and_body_and_sends_only_the_product_user_agent(self):
        with LoopbackServer() as server:
            server.route("/updates/v1/stable.json", body=b'{"ok": true}', headers={"ETag": '"x"'})
            response = app_http.request("GET", server.base + "/updates/v1/stable.json", headers={"If-None-Match": '"x"'})
            self.assertEqual((response.status, response.headers["etag"], response.body), (200, '"x"', b'{"ok": true}'))
            sent = server.requests[0].headers
            self.assertRegex(sent["user-agent"], r"^Angelo/\d+\.\d+\.\d+ \((macOS|Windows|Linux); \w+\)$")
            self.assertEqual(sorted(set(sent) - {"host", "accept-encoding", "connection"}),
                             ["if-none-match", "user-agent"])

    def test_http_errors_are_answers_not_exceptions(self):
        with LoopbackServer() as server:
            server.route("/304", status=304)
            server.route("/500", status=500, body=b"oops")
            self.assertEqual(app_http.request("GET", server.base + "/304").status, 304)
            self.assertEqual(app_http.request("GET", server.base + "/500").status, 500)
            self.assertEqual(app_http.request("GET", server.base + "/missing").status, 404)

    def test_the_answer_size_is_limited(self):
        with LoopbackServer() as server:
            server.route("/big", body=b"x" * 5000)
            with self.assertRaises(NetworkError) as caught:
                app_http.request("GET", server.base + "/big", limit=1000)
            self.assertEqual(caught.exception.kind, app_http.TOO_LARGE)

    def test_a_slow_server_times_out_and_a_closed_port_is_offline(self):
        with LoopbackServer() as server:
            server.route("/slow", body=b"late")
            server.delay = 1.5
            started = time.monotonic()
            with self.assertRaises(NetworkError) as caught:
                app_http.request("GET", server.base + "/slow", timeout=0.3)
            self.assertEqual(caught.exception.kind, app_http.TIMEOUT)
            self.assertLess(time.monotonic() - started, 1.3)
            port = server.port
        # Windows retries a refused loopback connection for about two seconds before it reports the refusal.
        with self.assertRaises(NetworkError) as caught:
            app_http.request("GET", "http://127.0.0.1:%d/x" % port, timeout=5)
        self.assertEqual(caught.exception.kind, app_http.OFFLINE)

    def test_a_get_follows_a_redirect_but_a_post_never_does(self):
        with LoopbackServer() as server:
            server.route("/old", status=302, headers={"Location": "/new"})
            server.route("/new", body=b"here")
            self.assertEqual(app_http.request("GET", server.base + "/old").body, b"here")
            posted = app_http.request("POST", server.base + "/old", body=b"report")
            self.assertEqual(posted.status, 302)
            self.assertEqual(server.count("/new"), 1)

    def test_a_redirect_to_a_downgraded_scheme_is_not_followed(self):
        with LoopbackServer() as server:
            server.route("/down", status=302, headers={"Location": "http://example.com/x"})
            self.assertEqual(app_http.request("GET", server.base + "/down").status, 302)

    def test_only_https_or_loopback_http_without_login_information(self):
        for url in ("http://example.com/x", "ftp://example.com/x", "file:///etc/passwd", "https://user:pw@example.com/x",
                    "https:///x", "", None, "https://" + "a" * 3000):
            with self.subTest(url=url):
                with self.assertRaises(NetworkError) as caught:
                    app_http.request("GET", url)
                self.assertEqual(caught.exception.kind, app_http.BAD_URL)

    def test_the_system_proxy_is_used(self):
        with LoopbackServer() as target, LoopbackServer() as proxy:
            proxy.route("http://127.0.0.1:%d/updates/v1/stable.json" % target.port, body=b"via proxy")
            proxy.routes["/updates/v1/stable.json"] = lambda request: (200, {}, b"via proxy")
            response = app_http.request("GET", target.base + "/updates/v1/stable.json", proxies={"http": proxy.base})
            self.assertEqual(response.body, b"via proxy")
            self.assertEqual(target.count(), 0)
            self.assertTrue(proxy.requests[0].path.startswith("http://127.0.0.1:%d/updates/v1/stable.json" % target.port))
            with patch("urllib.request.getproxies", return_value={"http": proxy.base}):
                self.assertEqual(app_http.request("GET", target.base + "/updates/v1/stable.json").body, b"via proxy")

    def test_a_certificate_failure_is_a_tls_error_not_offline(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("self-signed certificate"))
        with patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(NetworkError) as caught:
                app_http.request("GET", "https://angelo.studio/updates/v1/updates/v1/stable.json", context=ssl.create_default_context())
        self.assertEqual(caught.exception.kind, app_http.TLS)


if __name__ == "__main__":
    unittest.main()
