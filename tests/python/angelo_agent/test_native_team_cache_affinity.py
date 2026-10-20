# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team-scoped cache-affinity keys for Codex team chats (codex_team_keys, codex_connect, team_reads, team_account).

The console's ``cacheAffinitySecret`` is parsed strictly and kept in memory for the organization and person it
was read for; a non-member answer, a sign-out or another person drops it. Every member of one team gets the same
key for the same model, toolset and static prefix; another team, model, toolset or prefix gets another, and so
does the same organization under another secret. The member, the machine, the working directory and the effort
are never inputs, and a team key never equals a subscription key. A team chat on its own agent-runtime process is
checked once, then starts its text thread with the key; with no secret, on the person's own Codex, for voice and
on resume no key is sent, and a process that fails the check is refused before any thread starts.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (agent_runtime, cache_affinity, codex_connect, codex_runtime, codex_team_keys,  # noqa: E402
                          providers, team_reads)
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.team_view import SIGNED_IN, AccountView  # noqa: E402
from fake_team_console import OTHER_ORG_ID  # noqa: E402
from test_native_cache_affinity import KEY, PINNED_AGENT, STOCK, executable_file, patched_fork  # noqa: E402
from test_native_codex_runtime import FakeProcess, PoolCase, Provider, threads_counter  # noqa: E402
from test_native_team_account import AccountCase, wait_for  # noqa: E402
from angelo_agent.team_errors import NotSent  # noqa: E402
from angelo_agent.team_http import http_transport  # noqa: E402

SECRET, OTHER_SECRET = "ab" * 32, "cd" * 32
START = {"model": "gpt-6.1-sol", "baseInstructions": "Angelo", "dynamicTools": [{"name": "t"}],
         "modelProvider": "codex_team", "sandbox": "read-only", "approvalPolicy": "on-request"}


class Team:
    """What ``codex_team_keys.account`` reads from a signed-in team account."""

    def __init__(self, org="org-1", user="user-1", secret=SECRET, seat=True):
        self._view = AccountView(status=SIGNED_IN, org_id=org, user_id=user, seat=seat,
                                 scopes=frozenset({"api", "generate"}))
        self.secret = secret

    def view(self):
        return self._view

    def cache_affinity_secret(self):
        return (self._view.org_id, bytes.fromhex(self.secret)) if self.secret else None


def team_key(team, start=START, mode="codex_team", options=None):
    """The key a chat of ``team``'s member would send for ``start``."""
    provider = Provider(None, options=options)
    found = codex_team_keys.account(mode, team, environ={})
    return None if found is None else codex_connect._derive(found[0], found[1], start, provider.controller)


class ParseTests(unittest.TestCase):
    def test_only_64_lowercase_hexadecimal_digits_are_a_secret(self):
        self.assertEqual(team_reads.cache_affinity_secret({"data": {"providers": [], "cacheAffinitySecret": SECRET}}),
                         bytes.fromhex(SECRET))
        for value in (None, "", SECRET.upper(), SECRET[:-2], SECRET + "00", " " + SECRET, 7, ["ab"], {"x": 1},
                      "zz" * 32):
            with self.subTest(value=value):
                self.assertIsNone(team_reads.cache_affinity_secret({"data": {"cacheAffinitySecret": value}}))
        self.assertIsNone(team_reads.cache_affinity_secret({"data": {"providers": ["openai"]}}), "An older console")
        self.assertIsNone(team_reads.cache_affinity_secret({"cacheAffinitySecret": SECRET}), "Outside data")


class TeamKeyTests(unittest.TestCase):
    def test_members_of_one_team_share_one_key_whatever_their_machine_folder_or_effort(self):
        key = team_key(Team(user="mika"))
        self.assertRegex(key, KEY)
        self.assertEqual(key, team_key(Team(user="ren")), "Another member, the same key")
        self.assertEqual(key, team_key(Team(user="ren"), dict(START, cwd="/Users/ren/elsewhere")))
        self.assertEqual(key, team_key(Team(), options={"model": "gpt-6.1-sol", "effort": "high", "fast": False,
                                                        "approval": "AUTO_BOUNDED"}), "Effort is never an input")

    def test_another_team_secret_model_toolset_or_prefix_gets_another_key(self):
        key = team_key(Team())
        others = {
            "another organization, same secret": team_key(Team(org="org-2")),
            "same organization, rotated secret": team_key(Team(secret=OTHER_SECRET)),
            "another model": team_key(Team(), dict(START, model="gpt-6-astra")),
            "another instruction prefix": team_key(Team(), dict(START, baseInstructions="Angelo + team rules")),
            "other tools": team_key(Team(), dict(START, dynamicTools=[])),
        }
        with patch("angelo_agent.routing.TOOLSET_VERSION", 10_000):
            others["another toolset"] = team_key(Team())
        for name, other in others.items():
            with self.subTest(name):
                self.assertRegex(other, KEY)
                self.assertNotEqual(key, other)
        self.assertEqual(len(set(others.values())), len(others))

    def test_a_team_key_is_never_a_subscription_key_and_holds_nothing_readable(self):
        team = team_key(Team(org="org-1"))
        with patch.dict(os.environ, {"ANGELO_CACHE_KEY_SECRET": SECRET}):
            personal = codex_connect._derive(("codex_subscription", "org-1"), None, START, Provider(None).controller)
            same_secret = codex_connect._derive(("codex_subscription", "org-1"), bytes.fromhex(SECRET), START,
                                                Provider(None).controller)
        self.assertNotEqual(team, personal)
        self.assertNotEqual(team, same_secret, "The mode is an input: never a subscription key, even under one secret")
        for readable in ("org-1", "user-1", SECRET):
            self.assertNotIn(readable, team)

    def test_without_a_team_seat_or_secret_there_is_no_key(self):
        self.assertIsNone(team_key(Team(secret=None)), "An older console sends no secret")
        self.assertIsNone(team_key(Team(seat=False)), "No seat: no team route")
        self.assertIsNone(codex_team_keys.account("codex_team", None, environ={}), "Not signed in")
        signed_out = Team()
        signed_out._view = AccountView()
        self.assertIsNone(team_key(signed_out))
        for mode in ("codex_subscription", "codex_api", "claude_team", "grok_team", "minimax_team", "kimi_team"):
            self.assertIsNone(codex_team_keys.account(mode, Team(), environ={}), mode)
        self.assertEqual(codex_team_keys.MODES, frozenset({"codex_team"}))

    def test_a_secret_for_another_organization_is_never_used(self):
        stale = Team(org="org-1")
        stale.cache_affinity_secret = lambda: ("org-2", bytes.fromhex(SECRET))
        self.assertIsNone(codex_team_keys.account("codex_team", stale, environ={}))

    def test_the_measurement_override_replaces_the_console_secret_and_must_be_a_secret(self):
        environ = {"ANGELO_TEAM_CACHE_KEY_SECRET": OTHER_SECRET}
        found = codex_team_keys.account("codex_team", Team(secret=None), environ=environ)
        self.assertEqual(found, (("codex_team", "org-1"), bytes.fromhex(OTHER_SECRET)), "Also without a console secret")
        self.assertEqual(codex_team_keys.account("codex_team", Team(), environ=environ)[1], bytes.fromhex(OTHER_SECRET))
        self.assertIsNone(codex_team_keys.account("codex_team", Team(seat=False), environ=environ), "Still a team")
        for value in ("", "abc", "g" * 64):
            with self.assertRaises(RpcError):
                codex_team_keys.account("codex_team", Team(), environ={"ANGELO_TEAM_CACHE_KEY_SECRET": value})


def team_provider(process, team, kind="bundled", native=None):
    provider = Provider(process, native)
    provider.controller.state["provider"] = "codex_team"
    provider._runtime_kind = kind
    provider.team = team
    return provider


class OpenTeamThreadTests(unittest.TestCase):
    def setUp(self):
        self.answers = {"thread/start": threads_counter("chat"), "thread/fork": patched_fork,
                        "thread/resume": lambda p: {"thread": {"id": p["threadId"]}}}
        self.team = Team()
        lookup = patch("angelo_agent.team_library_access.account", lambda: self.team)
        lookup.start()
        self.addCleanup(lookup.stop)
        cleared = patch.dict(os.environ)
        cleared.start()
        self.addCleanup(cleared.stop)
        os.environ.pop("ANGELO_TEAM_CACHE_KEY_SECRET", None)

    def process(self):
        return FakeProcess(None, None, self.answers)

    def open(self, provider, fork=True):
        return codex_connect.open_thread(provider, dict(START, cwd="/w"), lambda: START["dynamicTools"], fork=fork)

    def test_a_team_chat_is_checked_once_then_starts_keyed_threads(self):
        process = self.process()
        provider = team_provider(process, self.team)
        self.assertEqual(self.open(provider)["thread"]["id"], "chat-1")
        self.assertEqual(process.methods(), ["thread/fork", "thread/fork", "thread/start"])
        key = process.calls[-1][1]["cacheAffinityKey"]
        self.assertEqual(key, team_key(self.team))
        self.assertTrue(provider._team_keyed)
        self.open(provider)
        self.assertEqual(process.methods()[3:], ["thread/start"], "Checked once per process")
        self.assertEqual(process.calls[-1][1]["cacheAffinityKey"], key)
        other = self.process()
        self.open(team_provider(other, Team(user="ren")))
        self.assertEqual(other.calls[-1][1]["cacheAffinityKey"], key, "Another member's process sends the same key")

    def test_no_secret_own_codex_voice_or_resume_send_no_key_and_no_check(self):
        cases = {"no secret": (Team(secret=None), "bundled", None, True),
                 "the person's own Codex": (self.team, "own", None, True),
                 "voice": (self.team, "bundled", None, False),
                 "resume": (self.team, "bundled", "thread-9", True)}
        for name, (team, kind, native, fork) in cases.items():
            with self.subTest(name):
                self.team = team
                process = self.process()
                provider = team_provider(process, team, kind, native)
                self.open(provider, fork)
                self.assertNotIn("thread/fork", process.methods())
                self.assertFalse(any("cacheAffinityKey" in (params or {}) for _, params in process.calls))
                self.assertFalse(provider._team_keyed)

    def test_a_process_that_fails_the_check_is_refused_before_any_thread(self):
        for answer, verdict in ((STOCK, cache_affinity.NOT_PATCHED), (RpcError("Agent process exited"),
                                                                       cache_affinity.EXITED)):
            with self.subTest(verdict):
                self.answers["thread/fork"] = answer
                process = self.process()
                with self.assertRaises(cache_affinity.CheckFailed) as caught:
                    self.open(team_provider(process, self.team))
                self.assertEqual(caught.exception.verdict, verdict)
                self.assertNotIn("thread/start", process.methods())
                probes = len(process.calls)
                with self.assertRaises(cache_affinity.CheckFailed):
                    self.open(team_provider(process, self.team))
                self.assertEqual(len(process.calls), probes, "The verdict is kept for the process: nothing is sent")

    def test_the_receipt_says_keyed_only_for_a_keyed_team_thread(self):
        routes = []
        fence = SimpleNamespace(matches=lambda host: True)
        for team in (self.team, Team(secret=None)):
            self.team = team
            provider = team_provider(self.process(), team)
            provider.controller.host = SimpleNamespace(_request_metrics=SimpleNamespace(
                set_route=lambda **fields: routes.append(fields)))
            provider.controller.request_fence = fence
            self.open(provider)
        self.assertEqual([r["cache_affinity"] for r in routes], [True, False])
        self.assertEqual({r["agent_runtime"] for r in routes}, {"bundled"})

    def test_angelos_own_secrets_reach_no_native_child(self):
        from angelo_agent import runtime_launch
        inherited = {"ANGELO_TEAM_CACHE_KEY_SECRET": SECRET, "ANGELO_CACHE_KEY_SECRET": SECRET,
                     "SENSEI_CACHE_KEY_SECRET": SECRET, "angelo_some_secret": "x", "PATH": "/usr/bin",
                     "MY_TOOL_SECRET": "kept: not Angelo's", "ANGELO_STATE_DIR": "/s"}
        with patch.dict(os.environ, inherited, clear=True):
            env = runtime_launch.child_environment("codex_subscription")
        self.assertFalse([name for name in env if name.upper().startswith(("ANGELO_", "SENSEI_"))
                          and name.upper().endswith("SECRET")])
        self.assertEqual((env.get("MY_TOOL_SECRET"), env.get("ANGELO_STATE_DIR")), ("kept: not Angelo's", "/s"))

    def test_team_chats_stay_off_the_shared_app_server(self):
        self.assertNotIn("codex_team", codex_runtime.ELIGIBLE_MODES, "The grant key is fixed per process")


class TeamProviderTests(PoolCase):
    """CodexProvider.connect for ``codex_team`` on the agent runtime, against fake app-servers (one per chat)."""

    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settle_before_cleanup(self.temp.name)
        self.processes, self.team = [], None
        self.answers = {"initialize": {"userAgent": PINNED_AGENT}, "config/read": {"config": {}},
                        "thread/start": threads_counter("chat"), "thread/fork": patched_fork}
        files = agent_runtime.layout() or {"required": ["codex", "codex-code-mode-host"]}
        codex = executable_file(self.temp.name, files["required"][0])
        executable_file(self.temp.name, files["required"][1])

        def process(command, *, env=None, cwd=None, spawn=None, on_event=None, on_request=None):
            self.processes.append(FakeProcess(on_event, on_request, self.answers))
            return self.processes[-1]
        for value in (patch.object(providers, "find_executable", return_value=codex),
                      patch.object(providers, "codex_command", return_value=([codex, "app-server"], {})),
                      patch.object(providers, "JsonRpcProcess", process),
                      patch.object(providers.agent_runtime, "is_agent_runtime", return_value=True)):
            value.start()
            self.addCleanup(value.stop)

    def started(self):
        return [params for process in self.processes for method, params in process.calls
                if method == "thread/start"][-1]

    def chat(self, team, folder):
        self.team = team
        controller = Controller(str(folder))
        self.addCleanup(controller.close)
        controller.set_document(("doc", 1))
        controller.state.update(provider="codex_team", native_id=None, toolset_version=TOOLSET_VERSION)
        controller.settings = {"connection_id": "team", "model": "gpt-6.1-sol"}
        controller.api_key = "agk_" + "k" * 43
        controller._begin_metrics()
        context = controller.runtime_context()
        provider = providers.CodexProvider(context, context.settings)
        self.addCleanup(provider.close)
        return provider

    def test_two_members_chats_each_start_on_their_own_process_with_the_teams_key(self):
        models = [{"id": "gpt-6.1-sol", "default": True, "efforts": ["low"], "default_effort": "low", "access": "team"}]
        with patch("angelo_agent.team_library_access.account", lambda: self.team), \
                patch("angelo_agent.team_runtime.coding_models", lambda mode, key: [dict(m) for m in models]):
            keys = []
            for user in ("mika", "ren"):
                folder = tempfile.TemporaryDirectory()
                self.addCleanup(folder.cleanup)
                self.settle_before_cleanup(folder.name)
                self.chat(Team(user=user), Path(folder.name) / user).connect()
                keys.append(self.started().get("cacheAffinityKey"))
            self.chat(Team(org="org-2", secret=OTHER_SECRET), Path(folder.name) / "other").connect()
            keys.append(self.started().get("cacheAffinityKey"))
        self.assertEqual(len(self.processes), 3, "Team chats keep their own app-server")
        self.assertRegex(keys[0], KEY)
        self.assertEqual(keys[0], keys[1], "Two members, one key")
        self.assertNotEqual(keys[0], keys[2], "Another team, another key")
        for process in self.processes:
            self.assertEqual(process.methods()[:3], ["initialize", "config/read", "thread/fork"])
            self.assertNotIn("account/read", process.methods(), "A team route reads no Codex account")


class AccountSecretTests(AccountCase):
    def test_the_secret_is_kept_for_its_member_and_dropped_when_membership_or_sign_in_ends(self):
        self.console.cache_affinity_secret = SECRET
        account = self.account()
        self.sign_in(account)
        wait_for(lambda: account.cache_affinity_secret() is not None)
        org, secret = account.cache_affinity_secret()
        self.assertEqual((org, secret), (account.view().org_id, bytes.fromhex(SECRET)))
        self.assertNotIn(SECRET, repr(account.view()), "Never in the view")
        self.assertEqual(account.redact("x %s y" % SECRET), "x [REDACTED] y")
        self.assertEqual(codex_team_keys.account("codex_team", account, environ={})[0], ("codex_team", org))

        self.console.cache_affinity_secret = OTHER_SECRET  # Rotated by an administrator: the next read has it.
        account.refresh_providers()
        wait_for(lambda: account.cache_affinity_secret()[1] == bytes.fromhex(OTHER_SECRET))

        with account._lock:
            account._update(seat=False)
        self.assertIsNone(account.cache_affinity_secret(), "No seat: not handed out")
        with account._lock:
            account._update(seat=True, user_id="someone-else")
        self.assertIsNone(account.cache_affinity_secret(), "Read for another person")
        with account._lock:
            account._update(user_id=account._affinity[1])
        self.assertIsNotNone(account.cache_affinity_secret())

        self.console.member = False  # Removed from the team: the console answers 404.
        account.refresh_providers()
        wait_for(lambda: account.view().providers == ())
        self.assertIsNone(account.cache_affinity_secret())
        self.assertIsNone(account._affinity)

    def test_an_older_console_sends_none_and_sign_out_forgets_it(self):
        account = self.account()
        self.sign_in(account)
        self.assertIsNone(account.cache_affinity_secret(), "An older console")
        self.console.cache_affinity_secret = SECRET
        account.refresh_providers()
        wait_for(lambda: account.cache_affinity_secret() is not None)
        account.sign_out()
        self.assertIsNone(account.cache_affinity_secret())
        self.assertIsNone(account._affinity)

    def test_a_refused_token_drops_the_secret_even_when_the_refresh_cannot_reach_the_console(self):
        """A removed member: the console refuses the app's token (401) and the refresh never arrives (offline)."""
        offline = {"on": False}

        def transport(method, url, headers=None, body=None, **kwargs):
            if offline["on"] and url.endswith("/api/oauth/token"):
                raise NotSent("network down")
            return http_transport(method, url, headers, body)
        self.console.cache_affinity_secret = SECRET
        account = self.account(transport=transport)
        self.sign_in(account)
        wait_for(lambda: account.cache_affinity_secret() is not None)
        self.console.revoke_all("member removed")
        offline["on"] = True
        account.refresh_providers()
        wait_for(lambda: account.view().providers_error)
        self.assertTrue(account.view().signed_in, "Still signed in: the refresh never reached the console")
        self.assertIsNone(account.cache_affinity_secret())
        self.assertIsNone(account._affinity)

    def test_the_measurement_override_is_redacted_too(self):
        account = self.account()
        with patch.dict(os.environ, {"ANGELO_TEAM_CACHE_KEY_SECRET": OTHER_SECRET.upper()}):
            self.assertEqual(account.redact("a %s b %s" % (OTHER_SECRET.upper(), OTHER_SECRET)),
                             "a [REDACTED] b [REDACTED]")

    def test_a_secret_read_for_another_organization_is_not_handed_out(self):
        self.console.cache_affinity_secret = SECRET
        account = self.account()
        self.sign_in(account)
        wait_for(lambda: account.cache_affinity_secret() is not None)
        with account._lock:
            account._update(org_id=OTHER_ORG_ID)
        self.assertIsNone(account.cache_affinity_secret())


if __name__ == "__main__":
    unittest.main()
