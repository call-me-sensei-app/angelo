# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A billed run's profile is checked for the account it is signed in to, not only for its folder (provider_accounts).

Every sign-in here is a fixture (account_fixtures): a JWT built from made-up claims in a temporary folder. No real
``auth.json`` or ``.claude.json`` is opened, and nothing is sent.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import account_fixtures as fixtures  # noqa: E402
import provider_accounts as accounts  # noqa: E402
import provider_isolation as isolation  # noqa: E402
# The harnesses under test, imported with the module: their own imports (pyexpat's submodules among them) load here,
# not inside a test (run_host_tests fails a test that leaves a new module without a spec behind).
import bench_mcp_codex  # noqa: E402
import bench_parallel_grade  # noqa: E402
import codex_cache_fork_check  # noqa: E402

SECRETS = (fixtures.ACCESS, fixtures.REFRESH, fixtures.EVAL_ACCOUNT_ID, fixtures.OTHER_ID, fixtures.OTHER_EMAIL,
           fixtures.CLAUDE_UUID, fixtures.CLAUDE_EMAIL, "someone@", "jack@")


class Folder(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="accounts-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def refused(self, call, *args, contains=""):
        with self.assertRaises(accounts.AccountRefusal) as raised:
            call(*args)
        text = str(raised.exception)
        self.assertIn(contains, text)
        for secret in SECRETS:
            self.assertNotIn(secret, text, "only an 8-character prefix and a domain are ever shown")
        return text


class Codex(Folder):
    def setUp(self):
        super().setUp()
        self.profile = self.root / "codex-profile" / str(uuid.uuid4())

    def test_the_eval_account_passes_by_its_account_id_and_shows_only_its_prefix_and_domain(self):
        fixtures.codex_sign_in(self.profile)
        self.assertEqual(accounts.codex_account(self.profile),
                         {"account_id": "7684a4d7", "email_domain": "hyperbond.studio"})
        fixtures.codex_sign_in(self.profile, email=None, stored=None)   # The account id decides; no email needed.
        self.assertEqual(accounts.codex_account(self.profile), {"account_id": "7684a4d7"})

    def test_an_email_alone_never_decides(self):
        # One email signs in to several ChatGPT accounts (a team workspace): without its account id, Codex's account
        # is the backend's pick, so even the evaluation account's own email refuses.
        fixtures.codex_sign_in(self.profile, account_id=None)
        self.refused(accounts.codex_account, self.profile, contains="no ChatGPT account id")
        fixtures.codex_sign_in(self.profile, id_token=fixtures.jwt({"https://api.openai.com/profile": {
            "email": fixtures.EVAL_ACCOUNT_EMAIL}}), stored=None)
        self.refused(accounts.codex_account, self.profile, contains="email_domain hyperbond.studio")

    def test_another_account_is_refused_even_with_eval_accounts_email(self):
        fixtures.codex_sign_in(self.profile, fixtures.OTHER_ID, fixtures.EVAL_ACCOUNT_EMAIL)   # A team workspace.
        self.refused(accounts.codex_account, self.profile, contains="account_id 0badc0de")
        fixtures.codex_sign_in(self.profile, None, fixtures.OTHER_EMAIL)
        self.refused(accounts.codex_account, self.profile, contains="email_domain example.com")

    def test_missing_or_malformed_claims_refuse(self):
        fixtures.codex_sign_in(self.profile, None, None)
        self.refused(accounts.codex_account, self.profile, contains="no ChatGPT account id (no account claims)")
        for token in ("", "not-a-jwt", "a..c", "a.b.c.d", "a.!!!!.c", fixtures.jwt(b"\xff\xfe"),
                      fixtures.jwt(b"[1, 2]"), fixtures.jwt(b"not json"), 7):
            fixtures.codex_sign_in(self.profile, id_token=token)
            with self.subTest(token=token):
                self.refused(accounts.codex_account, self.profile)
        for text in ("", "[]", "{", '{"tokens": "x"}', '{"tokens": {}}', '{"OPENAI_API_KEY": null}'):
            (self.profile / "auth.json").write_text(text, encoding="utf-8")
            with self.subTest(text=text):
                self.refused(accounts.codex_account, self.profile)
        (self.profile / "auth.json").unlink()
        self.refused(accounts.codex_account, self.profile, contains="auth.json is missing")

    def test_a_stored_account_id_that_disagrees_or_an_api_key_refuses(self):
        fixtures.codex_sign_in(self.profile, stored=fixtures.OTHER_ID)
        self.refused(accounts.codex_account, self.profile, contains="disagrees")
        fixtures.codex_sign_in(self.profile, OPENAI_API_KEY="sk-fixture")
        self.refused(accounts.codex_account, self.profile, contains="API key")

    def test_a_sign_in_kept_outside_auth_json_refuses(self):
        fixtures.codex_sign_in(self.profile)
        for store in ("keyring", "auto"):
            (self.profile / "config.toml").write_text('cli_auth_credentials_store = "%s"\n' % store)
            with self.subTest(store=store):
                self.refused(accounts.codex_account, self.profile, contains="credential store")
        (self.profile / "config.toml").write_text('model = "x"\n[profiles.p]\ncli_auth_credentials_store = "keyring"\n')
        self.assertTrue(accounts.codex_account(self.profile))   # Only with --profile p: never a billed run's.
        (self.profile / "config.toml").write_text('cli_auth_credentials_store = "file"\n')
        self.assertTrue(accounts.codex_account(self.profile))
        (self.profile / "config.toml").write_text('cli_auth_credentials_store = [\n')
        self.refused(accounts.codex_account, self.profile, contains="config.toml")
        (self.profile / "config.toml").write_text('"cli_auth_\\u0063redentials_store" = "keyring"\n')   # Escaped.
        self.refused(accounts.codex_account, self.profile, contains="credential store")

    def test_a_read_during_codexs_in_place_rewrite_retries_then_refuses(self):
        path = fixtures.codex_sign_in(self.profile)
        whole = path.read_text()
        path.write_text(whole[:20])   # Codex truncates, then writes: a reader can meet the half.
        accounts._read_json(path, "auth.json", sleep=lambda _s: path.write_text(whole))
        path.write_text(whole[:20])
        with self.assertRaises(accounts.AccountRefusal):
            accounts._read_json(path, "auth.json", sleep=lambda _s: None)


class Claude(Folder):
    def setUp(self):
        super().setUp()
        self.profile = self.root / "claude-profile" / str(uuid.uuid4())
        fixtures.claude_sign_in(self.profile)

    def test_the_named_account_passes_by_email_or_uuid(self):
        shown = {"account_id": fixtures.CLAUDE_UUID[:8], "email_domain": "example.org"}
        for expected in (fixtures.CLAUDE_EMAIL, fixtures.CLAUDE_EMAIL.upper(), fixtures.CLAUDE_UUID,
                         fixtures.CLAUDE_UUID.upper(), " %s " % fixtures.CLAUDE_EMAIL):
            with self.subTest(expected=expected):
                self.assertEqual(accounts.claude_account(self.profile, expected), shown)

    def test_another_account_no_expected_account_or_no_sign_in_refuses(self):
        self.refused(accounts.claude_account, self.profile, fixtures.OTHER_EMAIL, contains="another Claude account")
        self.refused(accounts.claude_account, self.profile, fixtures.OTHER_ID, contains="another Claude account")
        for expected in (None, "", "  "):
            self.refused(accounts.claude_account, self.profile, expected, contains=accounts.CLAUDE_ACCOUNT_ENV)
        self.refused(accounts.claude_account, self.profile, "not-an-account", contains="neither")
        fixtures.claude_sign_in(self.profile, email=None, account_uuid=fixtures.CLAUDE_UUID)
        self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_EMAIL)   # Named by email: none to match.
        fixtures.claude_sign_in(self.profile, email=None, account_uuid=None)
        self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_UUID, contains="names no account")
        (self.profile / ".claude.json").write_text('{"numStartups": 1}')
        self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_UUID, contains="oauthAccount")
        (self.profile / ".claude.json").unlink()
        self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_UUID, contains="missing")

    def test_a_profile_that_could_bill_a_credential_of_its_own_refuses(self):
        # The signed-in account matches, but Claude Code would bill the profile's own key or token instead.
        for name, settings in (("settings.json", {"env": {"CLAUDE_CODE_OAUTH_TOKEN": "fixture"}}),
                               ("settings.local.json", {"env": {"ANTHROPIC_API_KEY": "fixture"}}),
                               ("settings.json", {"apiKeyHelper": "/bin/echo fixture"})):
            (self.profile / name).write_text(json.dumps(settings))
            with self.subTest(name=name, settings=settings):
                self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_EMAIL, contains=name)
            (self.profile / name).unlink()
        (self.profile / "settings.json").write_text(json.dumps({"env": {"DISABLE_AUTOUPDATER": "1"}, "model": "x"}))
        self.assertTrue(accounts.claude_account(self.profile, fixtures.CLAUDE_EMAIL))
        config = json.loads((self.profile / ".claude.json").read_text())
        (self.profile / ".claude.json").write_text(json.dumps(dict(config, primaryApiKey="fixture")))
        self.refused(accounts.claude_account, self.profile, fixtures.CLAUDE_EMAIL, contains="primaryApiKey")


class Stores(Folder):
    def subscription(self, family, sign_in, **claims):
        profile = self.root / "store" / family / str(uuid.uuid4())
        sign_in(profile, **claims)
        mode = family.replace("-profile", "_subscription")
        return {"id": profile.name, "mode": mode, "reuse_native": False, "native_profile": str(profile)}

    def test_api_key_and_team_stores_reach_no_personal_plan(self):
        store = fixtures.store(self.root / "store", {"id": str(uuid.uuid4()), "mode": "codex_api"},
                               {"id": str(uuid.uuid4()), "mode": "claude_api"})
        self.assertEqual(accounts.verify(None, store), {"connections": []})
        self.assertEqual(accounts.verify(None, self.root / "empty-team-profile"), {"connections": []})

    def test_each_subscription_is_checked_at_its_profile(self):
        good = self.subscription("codex-profile", fixtures.codex_sign_in)
        claude = self.subscription("claude-profile", fixtures.claude_sign_in)
        store = fixtures.store(self.root / "store", good, claude)
        self.assertEqual(len(accounts.verify(None, store, fixtures.CLAUDE_EMAIL)["connections"]), 2)
        self.refused(accounts.verify, None, store, None, contains="claude_subscription")
        bad = self.subscription("codex-profile", fixtures.codex_sign_in, account_id=fixtures.OTHER_ID)
        fixtures.store(store, good, bad)
        self.refused(accounts.verify, None, store, contains="another ChatGPT account")
        local = dict(good, native_profile=None)   # No saved profile: the store's own <family>/<id>.
        fixtures.store(store, local)
        self.assertEqual(len(accounts.verify(None, store)["connections"]), 1)
        fixtures.store(store, dict(good, reuse_native=True))
        self.refused(accounts.verify, None, store, contains="reuses the person's own sign-in")
        fixtures.store(store, dict(good, native_profile=str(self.root / "gone")))
        self.refused(accounts.verify, None, store, contains="no profile folder")

    def test_a_copy_the_app_reads_back_is_checked_and_a_broken_store_refuses(self):
        good = self.subscription("codex-profile", fixtures.codex_sign_in)
        bad = self.subscription("codex-profile", fixtures.codex_sign_in, account_id=fixtures.OTHER_ID)
        store = fixtures.store(self.root / "store", good)
        fixtures.store(store, bad, name="connections.json.20261105T000000Z.unreadable")
        self.refused(accounts.verify, None, store, contains="another ChatGPT account")
        (store / "connections.json.20261105T000000Z.unreadable").write_text("{")   # The app skips it too.
        self.assertEqual(len(accounts.verify(None, store)["connections"]), 1)
        (store / "connections.json").write_text("{")
        self.refused(accounts.verify, None, store, contains="cannot be parsed")
        fixtures.store(store, good)
        text = (store / "connections.json").read_text()
        (store / "connections.json").write_bytes(b"\xef\xbb\xbf" + text.encode())   # A BOM: the app reads it.
        self.assertEqual(len(accounts.verify(None, store)["connections"]), 1)
        upper = dict(bad, id=bad["id"].upper(), native_profile=None)   # The app's spelling of the id: lower case.
        fixtures.codex_sign_in(store / "codex-profile" / bad["id"], fixtures.OTHER_ID, fixtures.OTHER_EMAIL)
        fixtures.store(store, upper)
        self.refused(accounts.verify, None, store, contains="another ChatGPT account")
        fixtures.store(store, good)
        fixtures.codex_sign_in(store)
        self.refused(accounts.verify, None, store, contains="a sign-in of its own")


class OptInWiring(Folder):
    def test_the_store_names_are_the_apps_and_an_opted_in_process_never_uses_the_persons_store(self):
        from angelo_agent import product_env
        self.assertEqual(isolation.STORE_ENV, product_env.names("CONNECTIONS_DIR"))
        env = isolation.real_provider_environment({"HOME": "/Users/person"}, None, str(self.root), self.root / "home")
        self.assertEqual(env["ANGELO_CONNECTIONS_DIR"], str(self.root / "home" / "connections"))
        kept = isolation.real_provider_environment({"SENSEI_CONNECTIONS_DIR": "/s"}, None, "/p", self.root / "home")
        self.assertNotIn("ANGELO_CONNECTIONS_DIR", kept)

    def test_the_connection_line_shows_the_account_prefix_and_domain_only(self):
        profile = self.root / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(profile)
        value = "%s:%s" % (isolation.OPT_IN_KIND, profile)
        self.assertEqual(isolation.shown_account(value), {"account_id": "7684a4d7", "email_domain": "hyperbond.studio"})
        with self.assertRaises(isolation.Refusal) as raised:   # Never the value back: its field is an email.
            isolation.opt_in_profile({isolation.OPT_IN: "evaluation:%s|account=%s" % (profile, fixtures.CLAUDE_EMAIL)})
        self.assertNotIn(fixtures.CLAUDE_EMAIL, str(raised.exception))
        with self.assertRaises(isolation.Refusal) as raised:   # A misspelt field is refused, never echoed.
            isolation.opt_in_profile({isolation.OPT_IN: "%s:%s|acount=%s" % (isolation.OPT_IN_KIND, profile,
                                                                           fixtures.CLAUDE_EMAIL)})
        self.assertNotIn(fixtures.CLAUDE_EMAIL, str(raised.exception))
        for name in ("not-a-uuid", "x"):
            folder = self.root / "codex-profile" / name
            fixtures.codex_sign_in(folder)
            with self.assertRaises(isolation.Refusal):   # Else taken for a store: its account never read.
                isolation.opt_in_profile({isolation.OPT_IN: "%s:%s" % (isolation.OPT_IN_KIND, folder)}, set())


class Harnesses(Folder):
    """The billed harnesses outside the opt-in check the same accounts, at the folder each launch uses."""

    # Every file under tests/python and scripts/angelo that could bill a provider (BILLED_MARKERS), classified: the
    # check it calls, the checked harness it only launches, the opt-in entry it bills through, or why it bills no
    # personal plan. A new file with a marker fails here until it is classified.
    BILLED_MARKERS = re.compile(r"CODEX_HOME|CLAUDE_CONFIG_DIR|isolated_connection\(|--authorize-billed-runs|"
                                r"authorize_billed|\bBILLED\b|LIVE, billed|run_pilot\.py|--authorize-measured")
    A = "tests/python/angelo_agent/"
    CHECKED = {A + "bench_mcp_codex.py": 'isolation.checked("codex-profile"',
               A + "bench_mcp_trial.py": 'isolation.checked("claude-profile"',
               A + "bench_warm_up.py": 'isolation.checked("claude-profile"',
               A + "bench_mcp_replay.py": "isolation.checked(",
               A + "bench_parallel_grade.py": "isolation.check_billed(",
               A + "bench_mcp_vs_angelo.py": "isolation.check_billed(",
               A + "check_external_mcp_clients.py": "isolation.check_billed(",
               A + "check_mcp_discovery.py": 'isolation.checked("codex-profile"',
               A + "codex_cache_fork_check.py": "isolation.check_billed(",
               A + "codex_runtime_check.py": "dedicated_profile(args.codex_home",   # codex_cache_fork_check's check.
               A + "eval_live_agent.py": "isolation.connection_opt_in(connection)",
               A + "parallel_live_trial.py": "isolation.connection_opt_in(connection)",
               A + "measure_parts_check.py": "isolation.connection_opt_in(isolated_connection(",
               A + "measure_parts_ground.py": "isolation.connection_opt_in(isolated_connection(",
               A + "final_runs.py": 'isolation.check_billed([("codex-profile"',   # The pilot; others: harnesses.
               A + "test_blender_cross_provider_live.py": "provider_isolation.check_billed("}
    # Billed only through a checked harness or entry: what it launches, or the driver that loads it.
    LAUNCHES = {A + "bench_parallel_vs_single.py": "bench_mcp_vs_angelo.py",
                A + "engine_link_compare_session.py": "eval_live_agent_session"}
    # In-app drivers and LIVE suites that bill only through the opt-in their entry checks (profile and store).
    ENTRY = {A + "eval_live_agent_session.py", A + "parallel_live_session.py", A + "test_blender_chat_usage_live.py",
             A + "test_blender_shared_sessions.py"}
    UNBILLED = {
        A + "provider_isolation.py": "the isolation itself", A + "provider_accounts.py": "the check itself",
        A + "run_native_suites.py": "isolated profiles; the opt-in is checked", A + "run_host_tests.py": "isolated",
        A + "native_suite_other_files.py": "a classification list", A + "eval_live_tasks.py": "task definitions",
        A + "final_runs_load.py": "machine load only", A + "bench_mcp_method.py": "method text",
        A + "bench_workdirs.py": "working folders only",
        A + "claude_prompt_probe.py": "fresh profile, local endpoint", A + "claude_direct_probe.py": "dummy key",
        A + "codex_image_prefix_probe.py": "fresh profile, local endpoint", A + "codex_fork_probe.py": "fresh profile",
        A + "codex_stale_call_probe.py": "fresh profile, local endpoint", A + "codex_opening_check.py": "fresh profile",
        A + "codex_prompt_probe.py": "fresh profile, local endpoint", A + "prompt_route_probe.py": "dummy key",
        A + "eval_trace_export.py": "reads a finished trial's trace store; sends nothing",
        A + "fake_provider_cli.py": "the stand-in CLI", A + "scenario_dry_run.py": "fake models",
        A + "long_session_replay.py": "a scripted runtime", A + "parallel_dry_run.py": "a scripted model",
        A + "scenario_plan_replay.py": "scripted model output",
        A + "test_blender_eval_live_agent_dryrun.py": "a scripted runtime",
        A + "test_blender_survey_grading_dryrun.py": "a scripted runtime",
        A + "test_blender_program_stress.py": "a scripted runtime",
        A + "final_runs_report.py": "headroom reads rate limits only (no model request)",
        A + "team_gateway_probe.py": "a team gateway key, fresh home",
        A + "team_network_probe.py": "a team gateway, fresh home",
        A + "test_blender_release_features.py": "fresh profile, --version only",
        A + "test_blender_signin_reconnect.py": "auth launch and subprocess intercepted; loopback-only team owner, HTTP refuses",
        A + "test_blender_change_chip_details.py": "every process denied; startup status probes only attributed, never run",
        A + "windows_cli_checks.py": "auth status only",
        A + "update_carry_over_phases.py": "records each sign-in's status command and profile; sends no model request",
        A + "live_genai_smoke.py": "media providers' own API keys (pay per use), no coding subscription",
        A + "live_flow_smoke.py": "media providers' own API keys (pay per use), no coding subscription",
        A + "live_media_smoke.py": "media providers' own API keys (pay per use), no coding subscription",
        "scripts/angelo/release_checks.py": "a fresh CODEX_HOME for --version"}

    def test_every_file_that_could_bill_is_classified_and_calls_its_check(self):
        root = HERE.parents[2]
        kinds = (set(self.CHECKED), set(self.LAUNCHES), self.ENTRY, set(self.UNBILLED))
        self.assertEqual(sum(map(len, kinds)), len(set().union(*kinds)), "each file has one class")
        found = [path for folder in ("tests/python", "scripts/angelo") for path in sorted((root / folder).rglob("*.py"))
                 if not path.name.startswith("test_native_")
                 and self.BILLED_MARKERS.search(path.read_text(encoding="utf-8", errors="replace"))]
        self.assertGreater(len(found), 30, "the scan found the harnesses")
        for path in found:
            with self.subTest(file=path.relative_to(root).as_posix()):
                self.assertIn(path.relative_to(root).as_posix(), set().union(*kinds), "classify it: how is it checked?")
        for name, call in list(self.CHECKED.items()) + list(self.LAUNCHES.items()):
            with self.subTest(name=name):
                self.assertIn(call, (root / name).read_text(encoding="utf-8"))
        for name in self.ENTRY:
            with self.subTest(name=name):
                self.assertIn("import suite_isolation", (root / name).read_text(encoding="utf-8"))
        self.assertNotIn('"real_providers"', (HERE / "eval_live_agent.py").read_text(), "no profile path printed")

    def test_the_mcp_side_codex_profile_and_the_fork_checks_profile(self):
        home = self.root / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(home, fixtures.OTHER_ID, fixtures.OTHER_EMAIL)
        config = {"codex": sys.executable, "codex_home": str(home)}
        self.assertTrue(any("another ChatGPT account" in p for p in bench_mcp_codex.preflight(config)))
        with self.assertRaises(SystemExit) as raised:
            codex_cache_fork_check.dedicated_profile(str(home), home.name)
        self.assertIn("another ChatGPT account", str(raised.exception))
        with self.assertRaises(isolation.Refusal):   # Each launch again: a profile re-signed after the preflight.
            bench_mcp_codex.environment(config, self.root)
        self.assertEqual(bench_mcp_codex.environment(config, self.root, "http://127.0.0.1:9/v1")["CODEX_HOME"],
                         str(home), "a dry run's endpoint is local")
        fixtures.codex_sign_in(home)
        self.assertEqual(bench_mcp_codex.preflight(config), [])
        self.assertEqual(codex_cache_fork_check.dedicated_profile(str(home), home.name), str(home.resolve()))
        self.assertEqual(bench_mcp_codex.environment(config, self.root)["CODEX_HOME"], str(home))

    def test_the_claude_grader_needs_its_expected_account(self):
        profile = self.root / "claude-profile" / str(uuid.uuid4())
        fixtures.claude_sign_in(profile, email=fixtures.OTHER_EMAIL)
        argv = ["--authorize-billed-runs", "--run", str(self.root / "run"), "--claude-config-dir", str(profile)]
        for environ in ({}, {accounts.CLAUDE_ACCOUNT_ENV: fixtures.CLAUDE_EMAIL}):
            with self.subTest(environ=environ), mock.patch.dict("os.environ", environ), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bench_parallel_grade.main(argv)

    def test_a_launch_uses_the_absolute_folder_it_checked_and_a_dry_run_only_a_local_endpoint(self):
        home = self.root / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(home)
        with mock.patch("os.getcwd", return_value=str(self.root)):
            self.assertEqual(isolation.checked("codex-profile", Path("codex-profile") / home.name), str(home))
        fresh = str(self.root / "fresh")
        self.assertEqual(isolation.checked("codex-profile", fresh, fake="http://127.0.0.1:4100/v1"), fresh)
        with self.assertRaises(isolation.Refusal):
            isolation.checked("codex-profile", fresh, fake="https://api.example.com/v1")

    def test_a_harness_profile_is_dedicated_and_its_children_inherit_no_credential(self):
        person = self.root / "person"
        fixtures.codex_sign_in(person / ".codex")
        with mock.patch.object(isolation, "homes", return_value={person}), self.assertRaises(isolation.Refusal):
            isolation.check_billed([("codex-profile", person / ".codex")])
        inherited = {"PATH": "/bin", "OPENAI_API_KEY": "x", "CLAUDE_CODE_OAUTH_TOKEN": "y", "ANGELO_X_API_KEY": "z"}
        self.assertEqual(isolation.without_credentials(inherited), {"PATH": "/bin"})

if __name__ == "__main__":
    unittest.main()
