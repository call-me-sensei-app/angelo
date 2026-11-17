# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom endpoints, hardened after the independent break-it review (2026-11-13).

1. Losing admission (a seat removed, the offline grace over) ends what runs on every endpoint connection, whatever
   its credential, and the next send is refused (a ``credential: none`` endpoint holds no key to withdraw).
2. A kept key is bound to everything that decides where and how it travels: the origin, each format's path, the
   header and scheme that carry it, and the extra headers. Any change asks for it again, naming what changed; a
   name, model or profile edit does not.
3. A busy chat loses its copy of a withdrawn key at once and its runtime ends when its turn ends; prewarm never
   starts a runtime an endpoint can't send on.
4. A base URL's host is a hostname (LDH labels; an internationalized name in punycode) or an IP literal.
5. The offline grace counts from the last confirmed seat, not from when the app first saw the console unreachable.
6. Policy endpoints need the policy's ``Organization`` and run only for it.
7. Test connection's server messages never carry the key, whatever its shape in the server's reply.

Re-check of the fixes (2026-11-13): a key withdrawn mid-turn is still redacted from what that turn writes until its
runtime ends; a withdrawal also cancels the consultations other chats run on the connection; a host names one server
for every resolver (no IPv6 zone ID, no numeric-looking last label); the offline grace counts from when the seat's
token was issued, so a token another process refreshed earlier confirms the seat only as of then.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_waits import until  # noqa: E402
from test_native_custom_endpoints import HEX, KEY, _EndpointUiCase, description, view  # noqa: E402
from angelo_agent import (activity_rows, custom_endpoints, endpoint_check, endpoint_connections,  # noqa: E402
                          endpoint_routes, managed_policy, reply_overlay, runtime_prewarm, team_access_share)
from angelo_agent.endpoint_connections import mode_for  # noqa: E402

ORG = "2b8c4d1e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
MODE = mode_for(HEX, "openai-responses")


class _Case(_EndpointUiCase):
    def setUp(self):
        super().setUp()
        self.ended = []
        patcher = patch("angelo_agent.runtime_bindings.end_connection", side_effect=self.ended.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record_id(self, mode=MODE):
        return next(i for i, r in self.endpoint_ui.records().items() if r["mode"] == mode)


class AdmissionTests(_Case):
    def test_a_keyless_endpoint_stops_when_the_seat_goes_and_its_next_send_is_refused(self):
        self.refresh(now=0.0, credential="none")
        records, identity = sorted(self.endpoint_ui.records()), self.record_id()
        self.assertIsNone(self.endpoint_ui.send_refusal(MODE, identity))
        self.refresh(view(seat=False), now=10.0, credential="none")
        self.assertEqual(sorted(self.ended), records)   # Every connection's runtimes end, with no key to withdraw.
        self.assertEqual(self.endpoint_ui.send_refusal(MODE, identity).template, endpoint_connections.NO_SEAT)
        self.assertIsNone(self.endpoint_ui.send_refusal("codex_api", "personal-id"))   # Not an endpoint: not judged.
        self.ended.clear()
        self.refresh(view(seat=False), now=20.0, credential="none")
        self.assertEqual(self.ended, [])   # Ended once, not at every refresh.

    def test_send_asks_before_anything_runs(self):
        from angelo_agent.controller import Controller
        source = inspect.getsource(Controller.send)
        self.assertLess(source.index("self.require_endpoint_admitted()"), source.index("request_rows.sender("))
        self.refresh(view(seat=False), now=0.0, credential="none")
        chat = SimpleNamespace(state={"provider": MODE, "connection_id": self.record_id()})
        with self.assertRaisesRegex(RuntimeError, "needs a seat"):
            Controller.require_endpoint_admitted(chat)
        Controller.require_endpoint_admitted(SimpleNamespace(state={"provider": "codex_api", "connection_id": "x"}))

    def test_prewarm_never_starts_a_runtime_an_endpoint_cannot_send_on(self):
        self.refresh(view(seat=False), now=0.0, credential="none")
        chat = SimpleNamespace(provider=None, prewarmed=None, hosted_in_worker=False,
                               state={"provider": MODE, "connection_id": self.record_id()})
        self.assertIsNone(runtime_prewarm.start(chat, ("launch",), 0.0))


class BusyChatTests(_Case):
    def test_a_busy_chat_loses_the_key_at_once_and_its_runtime_ends_when_the_turn_ends(self):
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        provider = Mock()
        chat = SimpleNamespace(state={"connection_id": self.record_id()}, busy=True, provider=provider, api_key=KEY)
        self.ui._controllers = {"chat": chat}
        self.refresh(view(seat=False), now=10.0)
        self.assertEqual(chat.api_key, "")
        self.assertIs(chat.provider, provider)   # The running turn finishes on the process it started with.
        self.endpoint_ui.pump()
        self.assertIs(chat.provider, provider)
        chat.busy = False
        self.endpoint_ui.pump()
        self.assertIsNone(chat.provider)
        until(lambda: provider.close.called, what="the runtime's close")

    def test_a_key_withdrawn_mid_turn_stays_redacted_until_its_runtime_ends(self):
        from angelo_agent.controller import Controller
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        chat = Controller(folder.name)
        self.addCleanup(chat.close)
        chat.set_document(("withdrawn-key", 1))
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        chat.state["connection_id"], chat.api_key = self.record_id(), KEY
        chat.busy, chat.provider = True, Mock()
        self.ui._controllers = {"chat": chat}
        self.refresh(view(seat=False), now=10.0)
        self.assertEqual(chat.api_key, "")   # No later request starts with it ...
        # ... but what the running turn still writes (a reply, an error, a call's row) never holds it.
        self.assertNotIn(KEY, chat.redact("the server said " + KEY))
        self.assertNotIn(KEY, json.dumps(activity_rows.redacted(chat, {"result": {"echo": KEY}})))
        self.assertEqual(reply_overlay.drawable(chat, "partial " + KEY[:8]), "partial ")
        chat.busy = False
        self.endpoint_ui.pump()   # The turn ended: its runtime closes, and the redaction-only copy goes with it.
        self.assertIsNone(chat.provider)
        self.assertEqual(chat.redact(KEY), KEY)

    def test_a_withdrawal_cancels_consultations_on_the_connection_from_any_chat(self):
        self.refresh(now=0.0)
        on_endpoint, elsewhere = Mock(connection_id=self.record_id()), Mock(connection_id="personal-id")
        lead = SimpleNamespace(state={"connection_id": "personal-id"}, busy=True, provider=None, api_key="",
                               delegate_jobs={"call-1": on_endpoint, "call-2": elsewhere})
        self.ui._controllers = {"lead": lead}
        self.refresh(view(seat=False), now=10.0)
        on_endpoint.cancel.assert_called_once()
        elsewhere.cancel.assert_not_called()


class RouteBindingTests(_Case):
    """Each knob of an endpoint's route asks for the key again; the wording names what changed."""

    BASE = dict(extraHeaders={"x-route": "blue"})
    GEMINI = {"path": "/gemini/v1beta", "auth": {"header": "x-goog-api-key", "scheme": "none"}}

    def keyed(self):
        self.refresh(now=0.0, **self.BASE)
        self.endpoint_ui.set_key(HEX, KEY)
        self.assertTrue(self.endpoint_ui.has_key(HEX))

    def reason(self):
        return self.endpoint_ui.reason(self.endpoint_ui.records()[self.record_id()])

    def test_every_field_that_decides_where_the_key_goes_asks_for_it_again(self):
        cases = {
            "origin": (dict(baseUrl="https://elsewhere.example.test"), endpoint_routes.MOVED),
            "path": (dict(formats={"openai-responses": {"path": "/v2"}, "gemini": self.GEMINI}),
                     endpoint_routes.PATH_CHANGED),
            "header": (dict(formats={"openai-responses": {"path": "/v1", "auth": {"header": "x-api-key",
                                                                                    "scheme": "none"}},
                                     "gemini": self.GEMINI}), endpoint_routes.AUTH_CHANGED),
            "scheme": (dict(formats={"openai-responses": {"path": "/v1", "auth": {"header": "authorization",
                                                                                    "scheme": "none"}},
                                     "gemini": self.GEMINI}), endpoint_routes.AUTH_CHANGED),
            "extra header value": (dict(extraHeaders={"x-route": "green"}), endpoint_routes.HEADERS_CHANGED),
            "extra header added": (dict(extraHeaders={"x-route": "blue", "x-team": "a"}),
                                   endpoint_routes.HEADERS_CHANGED),
        }
        for label, (changes, template) in cases.items():
            with self.subTest(label):
                self.keyed()
                self.refresh(now=1.0, **dict(self.BASE, **changes))
                self.assertFalse(self.endpoint_ui.has_key(HEX))
                self.assertNotIn(KEY, self.ui._memory_keys.values())
                self.assertEqual(self.reason().template, template)

    def test_a_name_model_or_profile_edit_keeps_the_key(self):
        self.keyed()
        self.refresh(now=1.0, **dict(self.BASE, name="Renamed proxy", models=[
            {"id": "team-gpt-sol", "format": "openai-responses"},
            {"id": "new-model", "format": "openai-responses", "profile": "gpt-6.1-sol"}]))
        self.assertTrue(self.endpoint_ui.has_key(HEX))
        self.assertEqual(self.reason(), "")

    def test_a_saved_key_for_another_route_is_never_applied(self):
        self.refresh(now=0.0, **self.BASE)
        slot = self.endpoint_ui.key_slot(HEX)
        endpoint = endpoint_connections.find(HEX)
        moved = dict(endpoint_routes.route(endpoint), formats={"openai-responses": ["/v0", "authorization", "bearer"]})
        self.endpoint_ui.key_loaded(self.ui, slot, "load", "loaded", endpoint_routes.stored(moved, KEY))
        self.assertFalse(self.endpoint_ui.has_key(HEX))
        self.assertEqual(self.reason().template, endpoint_routes.PATH_CHANGED)
        self.endpoint_ui.key_loaded(self.ui, slot, "load", "loaded",
                                    endpoint_routes.stored(endpoint_routes.route(endpoint), KEY))
        self.assertTrue(self.endpoint_ui.has_key(HEX))


class HostTests(unittest.TestCase):
    def test_a_host_is_a_hostname_or_an_ip_literal(self):
        for bad in ("https://a\\b.example.test", "https://a,b.example.test", "https://exa_mple.test",
                    "https://-bad.example.test", "https://bad-.example.test", "https://" + "a" * 64 + ".test",
                    "https://bücher.example", "https://a..b.test", "https://example.test.",
                    "https://a" + ".b" * 127 + ".test",
                    # Numeric names the resolver rewrites to another address (the console refuses them too).
                    "https://127.1", "https://010.0.0.1", "https://0x7f.0.0.1", "https://1.2.3.999",
                    # A last label any resolver may read as a number: all digits, hex, octal or exponent-looking.
                    "https://example.123", "https://example.0x1f", "https://a.1e3", "https://example.08",
                    "https://2130706433", "https://0x7f000001", "https://1.2.3.4.5",
                    # An IPv6 zone ID names an interface, and its text can carry anything.
                    "https://[fe80::1%a,evil.example]/v1", "https://[fe80::1%25eth0]"):
            with self.subTest(bad=bad):
                self.assertIsNone(custom_endpoints.base_url(bad))
        for good in ("https://llm.example.test", "https://xn--bcher-kva.example", "https://10.0.0.5:8443",
                     "https://[2001:db8::1]", "http://localhost:4000", "http://127.0.0.1:4000", "https://llm-1.corp",
                     "https://LLM.Example.Test", "https://1password.example", "https://llm.123corp.example",
                     "https://[::1]:4000"):
            with self.subTest(good=good):
                self.assertIsNotNone(custom_endpoints.base_url(good))


class GraceTests(_Case):
    def test_the_grace_counts_from_the_last_confirmed_seat(self):
        self.refresh(view(seat_confirmed_at=0.0), now=0.0)
        offline = view(status="offline", seat_confirmed_at=0.0)
        self.refresh(offline, now=590.0)   # First seen offline at 590: the grace still ends 600 s after 0.
        self.assertTrue(endpoint_connections.admitted())
        self.assertTrue(self.endpoint_ui.grace_ended(now=endpoint_connections.OFFLINE_GRACE))
        self.refresh(offline, now=endpoint_connections.OFFLINE_GRACE)
        self.assertFalse(endpoint_connections.admitted())
        self.assertFalse(endpoint_connections.account_admits(view(status="offline", seat_confirmed_at=None), now=0.0))

    def test_a_fresh_token_confirms_the_seat(self):
        from angelo_agent.team_account import TeamAccount
        self.assertIn("seat_confirmed_at", inspect.getsource(TeamAccount._accept))


REFRESH = "refresh-token-0001"
ANSWER = {"access_token": "access-token-0001", "refresh_token": REFRESH, "expires_in": 600, "seat": True,
          "org": {"id": ORG, "name": "Northwind"}, "user": {"id": "user-1", "email": "a@example.test"}}


class SeatTimeTests(unittest.TestCase):
    """A token confirms the seat as of when the console issued it, not when this process adopted it."""

    def test_a_shared_token_carries_its_age(self):
        from angelo_agent.team_view import parse_tokens
        tokens = parse_tokens(ANSWER)
        self.assertEqual(team_access_share.decode(team_access_share.encode(tokens, 1000), REFRESH, 1300)["age"],
                         300)
        # Shared again by the process that adopted it: still dated from the console's answer.
        adopted = team_access_share.decode(team_access_share.encode(tokens, 1000), REFRESH, 1300)
        self.assertEqual(team_access_share.decode(team_access_share.encode(adopted, 1300), REFRESH, 1400)["age"],
                         400)
        # A record written before the issue time was kept: dated as early as its expiry allows (never later).
        legacy = json.loads(team_access_share.encode(tokens, 1000))
        legacy.pop("iat", None)
        self.assertGreaterEqual(team_access_share.decode(json.dumps(legacy), REFRESH, 1300)["age"], 300)

    def test_the_seat_is_confirmed_as_of_the_tokens_issue(self):
        from angelo_agent.team_account import TeamAccount
        from angelo_agent.team_view import parse_tokens
        clock = SimpleNamespace(wall=1_800_000_000.0, mono=5000.0)
        account = TeamAccount("https://console.example.test", vault=None, clock=lambda: clock.wall,
                              monotonic=lambda: clock.mono, policy=lambda: managed_policy.Policy())
        self.addCleanup(account.close)
        tokens = dict(parse_tokens(ANSWER), age=300)
        self.assertTrue(account._accept(tokens, account._epoch))
        self.assertEqual(account.view().seat_confirmed_at, 4700.0)
        self.assertTrue(account._accept(parse_tokens(ANSWER), account._epoch))   # The console's own answer: now.
        self.assertEqual(account.view().seat_confirmed_at, 5000.0)


class PolicyOrganizationTests(unittest.TestCase):
    def test_policy_endpoints_need_the_policys_organization_and_run_only_for_it(self):
        bare = managed_policy.combine([("test", {"CustomEndpoints": [description()]})])
        self.assertEqual((bare.custom_endpoints, bare.problems), ((), ("CustomEndpoints",)))
        tied = managed_policy.combine([("test", {"CustomEndpoints": [description()], "Organization": ORG})])
        self.assertEqual((len(tied.custom_endpoints), tied.problems), (1, ()))
        self.assertEqual(endpoint_connections.effective(view(org_id=ORG), tied)[0], tied.custom_endpoints)
        self.assertEqual(endpoint_connections.effective(view(org_id="another-org"), tied)[0], ())


class RedactionTests(unittest.TestCase):
    def test_the_key_never_survives_in_a_server_message_whatever_its_shape(self):
        key = 'sk-a\\b"c'
        for body in (json.dumps({"error": {"code": "bad", "detail": "refused " + key}}), json.dumps({"detail": key}),
                     json.dumps({"error": "refused " + key}), "plain " + key):
            with self.subTest(body=body):
                shown = endpoint_check._server_message(body, key)
                for form in (key, json.dumps(key)[1:-1], repr(key)[1:-1]):
                    self.assertNotIn(form, shown)


if __name__ == "__main__":
    unittest.main()
