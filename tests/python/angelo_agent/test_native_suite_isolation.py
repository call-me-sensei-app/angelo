# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No test process sees a real provider login unless it opts in (provider_isolation, suite_isolation).

On 2026-11-05 Blender suites started by hand (``Angelo --background --python test_blender_parallel_merge.py``, not
through run_native_suites.py) ran the app's bundled Codex on the person's own ``~/.codex``: real turns on the main
ChatGPT account. The isolation now lives in every suite's own first import, so these tests start files the way a person
does, with a home holding real-looking sign-ins, and check what the file then sees.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parents[2] / "scripts/addons_core"))
import account_fixtures as fixtures  # noqa: E402  (made-up sign-ins: no real token)
import provider_isolation as isolation  # noqa: E402
import platform_env  # noqa: E402

HOST_ENTRY, BLENDER_ENTRY = isolation.HOST_ENTRY, isolation.BLENDER_ENTRY
SECRETS = {"OPENAI_API_KEY": "sk-fixture", "ANTHROPIC_API_KEY": "fixture", "CLAUDE_CODE_OAUTH_TOKEN": "fixture",
           "CODEX_ACCESS_TOKEN": "fixture", "ANGELO_XAI_API_KEY": "fixture", "CODEX_SQLITE_HOME": "/real/sqlite"}
# What a file sees after its first import: printed as JSON by PROBE, run in place of the rest of the file.
PROBE = """
import json as _json, os as _os
from pathlib import Path as _Path
_codex = _Path(_os.environ.get("CODEX_HOME") or _Path.home() / ".codex")
_claude = _Path(_os.environ.get("CLAUDE_CONFIG_DIR") or _Path.home() / ".claude")
print("PROBE" + _json.dumps({"environ": dict(_os.environ), "home": str(_Path.home()),
      "codex_login": (_codex / "auth.json").exists(), "claude_login": (_claude / ".credentials.json").exists(),
      "home_codex_login": (_Path.home() / ".codex" / "auth.json").exists(),
      "home_claude_login": (_Path.home() / ".claude" / ".credentials.json").exists(),
      "satisfied": __import__("provider_isolation").satisfied(dict(_os.environ))}))
"""


def entry_statements(path):
    """The statements of ``path`` after its docstring and ``__future__`` imports, as long as the entry."""
    body = ast.parse(path.read_text(encoding="utf-8")).body
    index = 1 if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
        and isinstance(body[0].value.value, str) else 0
    while index < len(body) and isinstance(body[index], ast.ImportFrom) and body[index].module == "__future__":
        index += 1
    return body, index


def person_home(root):
    """A home folder as a person's: signed in to Codex and Claude Code (fixture files, no real credential)."""
    home = Path(root) / "person"
    for path in (home / ".codex" / "auth.json", home / ".claude" / ".credentials.json", home / ".claude.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    return home


def hand_environment(home, **extra):
    """The environment of a suite started by hand from that person's shell."""
    env = platform_env.essential_environ(PATH=os.environ.get("PATH", ""), **SECRETS, **extra)
    env.update(platform_env.home_environ(home))
    env.update(CODEX_HOME=str(home / ".codex"), CLAUDE_CONFIG_DIR=str(home / ".claude"))
    tmp = Path(home).parent / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp))
    return env


class EveryFileStartsIsolated(unittest.TestCase):
    def test_every_suite_host_test_and_billed_driver_starts_with_the_entry(self):
        files = isolation.preamble_files(HERE)
        names = {path.name for path in files}
        self.assertIn("test_blender_parallel_merge.py", names)   # The file of 2026-11-05.
        self.assertIn("test_native_suite_isolation.py", names)
        self.assertTrue(set(isolation.PREAMBLE_DRIVERS) <= names)
        self.assertGreater(len([name for name in names if name.startswith("test_blender_")]), 300)
        missing = []
        for path in files:
            expected = ast.parse(HOST_ENTRY if path.name.startswith("test_native_") else BLENDER_ENTRY).body
            body, start = entry_statements(path)
            found = body[start:start + len(expected)]
            if [ast.dump(node) for node in found] != [ast.dump(node) for node in expected]:
                missing.append(path.name)
        self.assertEqual(missing, [], "these files do not import suite_isolation first")

    def test_the_perf_ceilings_digest_is_unchanged_by_the_entry_alone(self):
        import perf_ratchet
        path = HERE / "benchmark_blender_snapshot_cost.py"
        self.assertIn(path.name, perf_ratchet.MEASURED_HARNESS)
        text = path.read_text(encoding="utf-8")
        self.assertIn("\n" + BLENDER_ENTRY, text)
        without = Path(tempfile.mkdtemp(prefix="digest-")) / path.name
        self.addCleanup(lambda: __import__("shutil").rmtree(without.parent, ignore_errors=True))
        without.write_bytes(text.replace("\n" + BLENDER_ENTRY, "", 1).encode("utf-8"))
        self.assertEqual(perf_ratchet.measured_bytes(path), perf_ratchet.measured_bytes(without))
        changed = without.parent / "changed.py"
        changed.write_bytes(text.replace("\n" + BLENDER_ENTRY, "\nimport this_moves_a_ceiling\n", 1).encode("utf-8"))
        self.assertNotEqual(perf_ratchet.measured_bytes(changed), perf_ratchet.measured_bytes(without),
                            "any other change still counts")

    def test_the_billed_harness_and_both_runners_use_it(self):
        for name in ("run_host_tests.py",):
            body, start = entry_statements(HERE / name)
            self.assertEqual(ast.dump(body[start]), ast.dump(ast.parse(HOST_ENTRY).body[0]), name)
        for name in ("run_native_suites.py", "eval_live_agent.py", "parallel_live_trial.py"):
            self.assertIn("provider_isolation", (HERE / name).read_text(encoding="utf-8"), name)
            self.assertNotIn("import suite_isolation", (HERE / name).read_text(encoding="utf-8"),
                             "%s reads the caller's profile and must not isolate itself" % name)

    def test_credentials_cover_every_variable_the_product_withholds(self):
        from angelo_agent.runtime_environment import CREDENTIALS
        self.assertEqual(sorted(name for name in CREDENTIALS if not isolation.credential(name)), [])
        for name in SECRETS:
            self.assertTrue(isolation.credential(name), name)
        for name in ("PATH", "HOME", "ANGELO_QA_DIR", "HTTPS_PROXY", "ANGELO_EVAL_MODEL"):
            self.assertFalse(isolation.credential(name), name)


class HandStartedFiles(unittest.TestCase):
    """A file started by hand (plain Python here; inside Blender the same entry relaunches) with a person's home."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hand-start-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.home = person_home(self.root)

    def probe(self, name, env):
        """Run ``name``'s own lines up to its first import, then PROBE, as that file (its ``__file__``)."""
        path = HERE / name
        body, start = entry_statements(path)
        expected = len(ast.parse(HOST_ENTRY if name.startswith("test_native_") else BLENDER_ENTRY).body)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        entry = "".join(lines[:body[start + expected - 1].end_lineno])
        script = self.root / ("probe-" + name)
        # A host test's folder is on sys.path however it is started (unittest, pytest, the runner, the file
        # itself); Blender's --python adds none, so a Blender file's own entry must put it there.
        header = "__file__ = %r\n" % str(path)
        if name.startswith("test_native_"):
            header += "import sys\nsys.path.insert(0, %r)\n" % str(HERE)
        script.write_text(header + entry + PROBE, encoding="utf-8")
        result = subprocess.run([sys.executable, "-S", str(script)], env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.split("PROBE", 1)[1])

    def assert_isolated(self, seen):
        env = seen["environ"]
        self.assertFalse(isolation.inside(seen["home"], self.home), "HOME is the person's")
        for key in ("codex_login", "claude_login", "home_codex_login", "home_claude_login"):
            self.assertFalse(seen[key], key)
        for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.assertFalse(isolation.inside(env[name], self.home), name)
            self.assertTrue(isolation.inside(env[name], env[isolation.MARKER]), name)
        self.assertEqual([name for name in env if isolation.credential(name)], [])
        self.assertEqual((env["ANGELO_DISABLE_KEYCHAIN"], env["ANGELO_AGENT_RUNTIME"]), ("1", "off"))
        self.assertNotIn("HOMEDRIVE", env)
        self.assertTrue(seen["satisfied"])

    def test_a_blender_suite_started_by_hand_sees_no_codex_or_claude_login(self):
        self.assert_isolated(self.probe("test_blender_parallel_merge.py", hand_environment(self.home)))

    def test_a_host_test_and_a_billed_driver_started_by_hand_see_none_either(self):
        for name in ("test_native_suite_runner.py", "eval_live_agent_session.py", "benchmark_blender_batch.py"):
            with self.subTest(name=name):
                self.assert_isolated(self.probe(name, hand_environment(self.home)))

    def test_the_isolated_profile_is_made_in_the_suites_qa_folder_when_it_has_one(self):
        qa = self.root / "qa"
        seen = self.probe("test_blender_parallel_merge.py", hand_environment(self.home, ANGELO_QA_DIR=str(qa)))
        self.assert_isolated(seen)
        self.assertTrue(isolation.inside(seen["environ"]["HOME"], qa))

    def test_the_keychain_opt_in_keeps_the_home_but_no_login(self):
        seen = self.probe("test_blender_keychain_live.py", hand_environment(self.home, ANGELO_QA_LIVE_KEYCHAIN="1"))
        env = seen["environ"]
        self.assertTrue(isolation.inside(env["HOME"], self.home))
        self.assertFalse(seen["codex_login"] or seen["claude_login"])
        self.assertEqual((env["ANGELO_DISABLE_KEYCHAIN"], env["ANGELO_AGENT_RUNTIME"]), ("1", "off"))
        self.assertEqual([name for name in env if isolation.credential(name)], [])

    def test_an_invalid_real_provider_opt_in_stops_the_file(self):
        for value in ("eval-profile:" + str(self.home / ".codex"), "eval-profile:", "yes",
                      "account1-profile:" + str(self.root)):
            with self.subTest(value=value), self.assertRaises(AssertionError) as raised:
                self.probe("test_blender_parallel_merge.py",
                           hand_environment(self.home, ANGELO_TEST_REAL_PROVIDERS=value))
            self.assertIn("suite_isolation: refused", str(raised.exception))

    def test_the_eval_account_opt_in_pins_its_profile_and_nothing_else(self):
        profile = self.root / "bench" / "eval-account" / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(profile)
        seen = self.probe("test_blender_parallel_merge.py", hand_environment(
            self.home, ANGELO_TEST_REAL_PROVIDERS="eval-profile:" + str(profile)))
        env = seen["environ"]
        self.assertEqual(env["CODEX_HOME"], str(profile))
        self.assertFalse(isolation.inside(env["CLAUDE_CONFIG_DIR"], self.home))
        self.assertFalse(seen["claude_login"])
        self.assertEqual([name for name in env if isolation.credential(name)], [])
        self.assertTrue(seen["satisfied"])
        self.assertFalse(isolation.inside(env["ANGELO_CONNECTIONS_DIR"], self.home), "never the person's own store")

    def test_a_hand_started_suite_opted_in_with_another_accounts_profile_stops(self):
        # 2026-11-05: the opt-in checked the folder, not the account it is signed in to.
        profile = self.root / "bench" / "eval-account" / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(profile, fixtures.OTHER_ID, fixtures.OTHER_EMAIL)
        with self.assertRaises(AssertionError) as raised:
            self.probe("test_blender_parallel_merge.py", hand_environment(
                self.home, ANGELO_TEST_REAL_PROVIDERS="eval-profile:" + str(profile)))
        self.assertIn("suite_isolation: refused", str(raised.exception))
        self.assertIn("another ChatGPT account (account_id 0badc0de, email_domain example.com", str(raised.exception))
        for secret in (fixtures.ACCESS, fixtures.REFRESH, fixtures.OTHER_ID, fixtures.OTHER_EMAIL):
            self.assertNotIn(secret, str(raised.exception))


class InsideBlender(unittest.TestCase):
    def test_a_hand_started_blender_suite_relaunches_isolated_and_exits_with_its_status(self):
        root = Path(tempfile.mkdtemp(prefix="relaunch-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        home = person_home(root)
        environ = hand_environment(home)
        calls = []
        argv = ["/Applications/Angelo.app/Contents/MacOS/Angelo", "--background", "--python", "test_blender_x.py"]
        with mock.patch.object(isolation, "in_blender", return_value=True), \
                mock.patch.object(isolation, "relaunch",
                                  side_effect=lambda env: calls.append((env, isolation.satisfied(env))) or 3), \
                mock.patch.object(isolation.os, "_exit", side_effect=SystemExit) as exit_:
            with self.assertRaises(SystemExit):
                isolation.ensure(environ)
        exit_.assert_called_once_with(3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][isolation.RELAUNCHED], "1")
        self.assertTrue(calls[0][1], "the relaunched suite finds its environment isolated")
        self.assertTrue(isolation.inside(environ["HOME"], home), "the caller's process is not changed in place")
        command = []
        self.assertEqual(isolation.relaunch({"A": "1"}, argv=argv, binary=argv[0],
                                            call=lambda c, env: command.append((c, env)) or -9), 1)
        self.assertEqual(command, [(argv, {"A": "1"})])

    def test_a_relaunched_suite_never_relaunches_again(self):
        root = Path(tempfile.mkdtemp(prefix="loop-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        environ = hand_environment(person_home(root), ANGELO_TEST_RELAUNCHED="1")
        with mock.patch.object(isolation, "in_blender", return_value=True), \
                mock.patch.object(isolation, "relaunch") as relaunch, self.assertRaises(SystemExit):
            isolation.ensure(environ)
        relaunch.assert_not_called()

    def test_only_blenders_own_python_counts_as_blender(self):
        self.assertFalse(isolation.in_blender())   # A host test's stand-in bpy is never Blender.


class OptIn(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="opt-in-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.home = person_home(self.root)
        self.people = {self.home}

    def value(self, path):
        return {isolation.OPT_IN: "%s:%s" % (isolation.OPT_IN_KIND, path)}

    def test_never_the_persons_own_sign_in_nor_a_folder_holding_the_home(self):
        for path in (self.home / ".codex", self.home / ".claude", self.home, self.root, self.home / ".codex" / "x"):
            path.mkdir(parents=True, exist_ok=True)
            with self.subTest(path=path), self.assertRaises(isolation.Refusal):
                isolation.opt_in_profile(self.value(path), self.people)

    def test_a_named_absolute_existing_signed_in_profile_only(self):
        codex = self.root / "codex-profile" / str(uuid.uuid4())
        codex.mkdir(parents=True)
        for environ in ({isolation.OPT_IN: ""}, {isolation.OPT_IN: str(codex)}, self.value("relative/path"),
                        self.value(self.root / "missing"), {isolation.OPT_IN: "evaluation:" + str(codex)},
                        self.value(codex)):   # The last: Codex's sign-in file is missing.
            with self.subTest(environ=environ), self.assertRaises(isolation.Refusal):
                isolation.opt_in_profile(environ, self.people)
        fixtures.codex_sign_in(codex)
        self.assertEqual(isolation.opt_in_profile(self.value(codex), self.people), ("codex-profile", str(codex)))
        claude = self.root / "claude-profile" / str(uuid.uuid4())
        fixtures.claude_sign_in(claude)
        self.assertEqual(isolation.opt_in_profile(self.value(str(claude) + "|account=" + fixtures.CLAUDE_EMAIL),
                                                  self.people), ("claude-profile", str(claude)))
        store = self.root / "api-key-store"
        store.mkdir()
        self.assertEqual(isolation.opt_in_profile(self.value(store), self.people), (None, str(store)))
        self.assertIsNone(isolation.opt_in_profile({}, self.people))

    def test_the_opted_in_environment_pins_one_profile_and_leaves_the_other_empty(self):
        claude = self.root / "claude-profile" / str(uuid.uuid4())
        fixtures.claude_sign_in(claude)
        base = dict(hand_environment(self.home), ANGELO_DISABLE_KEYCHAIN="0")
        value = "%s:%s|account=%s" % (isolation.OPT_IN_KIND, claude, fixtures.CLAUDE_UUID.upper())
        with mock.patch.object(isolation, "homes", return_value=self.people):
            env = isolation.opt_in_environment(base, value, self.root / "empty")
            self.assertTrue(isolation.satisfied(env, self.people))
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(claude))
        self.assertEqual(env["CODEX_HOME"], str(self.root / "empty" / ".codex"))
        self.assertFalse((Path(env["CODEX_HOME"]) / "auth.json").exists())
        self.assertEqual(env["HOME"], base["HOME"], "the login Keychain: the harness's own home")
        self.assertEqual(env["ANGELO_DISABLE_KEYCHAIN"], "0", "the harness decides (an API key in place)")
        self.assertEqual([name for name in env if isolation.credential(name)], [])
        self.assertFalse(isolation.satisfied(dict(env, CODEX_HOME=str(self.home / ".codex")), self.people))

    def test_a_billed_connection_opts_in_with_its_dedicated_profile_never_a_native_sign_in(self):
        codex = self.root / "connections" / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(codex)
        with mock.patch.object(isolation, "homes", return_value=self.people):
            self.assertEqual(isolation.connection_opt_in({"id": "c", "reuse_native": False, "profile": str(codex)}),
                             "%s:%s" % (isolation.OPT_IN_KIND, codex))
            store = self.root / "api-key-store"
            store.mkdir()
            self.assertEqual(isolation.connection_opt_in({"id": "k", "reuse_native": False, "in_place": str(store)}),
                             "%s:%s" % (isolation.OPT_IN_KIND, store))
            for connection in ({"id": "n", "reuse_native": True, "profile": str(codex)}, {"id": "p", "profile": None},
                               {"id": "h", "reuse_native": False, "profile": str(self.home / ".codex")}):
                with self.subTest(connection=connection), self.assertRaises(isolation.Refusal):
                    isolation.connection_opt_in(connection)

    def test_a_profile_signed_in_to_another_account_is_refused_whatever_its_folder_is_called(self):
        # 2026-11-05: a folder named eval-account can be re-signed into any account a browser holds.
        codex = self.root / "eval-account" / "codex-profile" / str(uuid.uuid4())
        for claims in ({"account_id": fixtures.OTHER_ID, "email": fixtures.EVAL_ACCOUNT_EMAIL},
                       {"account_id": None, "email": fixtures.OTHER_EMAIL}, {"account_id": None, "email": None}):
            fixtures.codex_sign_in(codex, **claims)
            with self.subTest(claims=claims), self.assertRaises(isolation.Refusal):
                isolation.opt_in_profile(self.value(codex), self.people)
            with self.subTest(claims=claims), mock.patch.object(isolation, "homes", return_value=self.people), \
                    self.assertRaises(isolation.Refusal):
                isolation.connection_opt_in({"id": "c", "reuse_native": False, "profile": str(codex)})
        (codex / "auth.json").write_text('{"tokens": {"id_token": "not.a-token"}}', encoding="utf-8")
        with self.assertRaises(isolation.Refusal):
            isolation.opt_in_profile(self.value(codex), self.people)

    def test_a_claude_profile_is_checked_against_the_account_its_opt_in_names(self):
        claude = self.root / "claude-profile" / str(uuid.uuid4())
        fixtures.claude_sign_in(claude, email=fixtures.OTHER_EMAIL, account_uuid=fixtures.OTHER_ID)
        for value in (str(claude), "%s|account=%s" % (claude, fixtures.CLAUDE_EMAIL),
                      "%s|account=%s" % (claude, fixtures.CLAUDE_UUID), "%s|account=" % claude):
            with self.subTest(value=value), self.assertRaises(isolation.Refusal):
                isolation.opt_in_profile(self.value(value), self.people)
        with mock.patch.object(isolation, "homes", return_value=self.people), self.assertRaises(isolation.Refusal):
            isolation.connection_opt_in({"id": "c", "reuse_native": False, "profile": str(claude)}, environ={})
        fixtures.claude_sign_in(claude)
        with mock.patch.object(isolation, "homes", return_value=self.people):
            self.assertEqual(isolation.connection_opt_in(
                {"id": "c", "reuse_native": False, "profile": str(claude)},
                environ={"ANGELO_TEST_CLAUDE_ACCOUNT": fixtures.CLAUDE_EMAIL}),
                "%s:%s|account=%s" % (isolation.OPT_IN_KIND, claude, fixtures.CLAUDE_EMAIL))

    def test_every_subscription_in_the_opted_in_processs_store_is_checked(self):
        codex = self.root / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(codex)
        other = self.root / "store" / "codex-profile" / str(uuid.uuid4())
        fixtures.codex_sign_in(other, fixtures.OTHER_ID, fixtures.OTHER_EMAIL)
        store = fixtures.store(self.root / "store", {"id": other.name, "mode": "codex_subscription",
                                                     "reuse_native": False, "native_profile": str(other)})
        base = dict(hand_environment(self.home), ANGELO_CONNECTIONS_DIR=str(store))
        with mock.patch.object(isolation, "homes", return_value=self.people), self.assertRaises(isolation.Refusal):
            isolation.opt_in_environment(base, "%s:%s" % (isolation.OPT_IN_KIND, codex), self.root / "empty")
        fixtures.codex_sign_in(other)
        with mock.patch.object(isolation, "homes", return_value=self.people):
            env = isolation.opt_in_environment(base, "%s:%s" % (isolation.OPT_IN_KIND, codex), self.root / "empty")
            self.assertTrue(isolation.satisfied(env, self.people))
            self.assertFalse(isolation.satisfied({k: v for k, v in env.items() if k != "ANGELO_CONNECTIONS_DIR"},
                                                 self.people), "an opted-in process names its store")

    def test_an_unmarked_or_partial_isolation_is_not_taken_for_one(self):
        home = isolation.prepare(self.root / "iso" / "home")
        env = isolation.isolated_environment(hand_environment(self.home), home)
        self.assertTrue(isolation.satisfied(env, self.people))
        (home / isolation.MARKER_FILE).unlink()
        self.assertFalse(isolation.satisfied(env, self.people), "a home no isolation made")
        isolation.prepare(home)
        for change in ({"ANGELO_AGENT_RUNTIME": ""}, {"ANGELO_DISABLE_KEYCHAIN": "0"}, {"OPENAI_API_KEY": "x"},
                       {"CODEX_HOME": str(self.home / ".codex")}, {"HOME": str(self.home)},
                       {isolation.MARKER: str(self.root)}):
            with self.subTest(change=change):
                self.assertFalse(isolation.satisfied(dict(env, **change), self.people))
        self.assertFalse(isolation.satisfied(dict(env, **{isolation.MARKER: str(self.home)}), self.people))

    def test_a_worker_angelo_starts_from_an_isolated_suite_is_isolated_in_place(self):
        # Regression: a suite that serves as Angelo's worker (worker_python, concurrent_edit, parallel_resume) got
        # worker_process's environment, which kept no marker, so it relaunched: the relaunch closed its channel
        # descriptor and ran under another process id than the one Angelo started and stops.
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
        from angelo_agent import worker_process
        home = isolation.prepare(self.root / "iso" / "home")
        suite = isolation.isolated_environment(hand_environment(self.home), home)
        worker = worker_process.worker_environment(suite, self.root / "private", sys.platform)
        self.assertEqual(worker[isolation.MARKER], str(home))
        self.assertTrue(isolation.satisfied(worker, self.people))
        self.assertFalse(isolation.satisfied({k: v for k, v in worker.items() if k != "ANGELO_HEADLESS"}, self.people),
                         "only Angelo's own child may leave the runtime on and the logins at their defaults")
        for change in ({"OPENAI_API_KEY": "x"}, {"CODEX_HOME": str(self.home / ".codex")}, {"HOME": str(self.home)},
                       {"ANGELO_DISABLE_KEYCHAIN": "0"}):
            with self.subTest(change=change):
                self.assertFalse(isolation.satisfied(dict(worker, **change), self.people))
        self.assertNotIn(isolation.MARKER, worker_process.worker_environment(hand_environment(self.home),
                                                                             self.root / "private", sys.platform))


if __name__ == "__main__":
    unittest.main()
