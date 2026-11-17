# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A model runtime and an endpoint's key program start only from what no script can change in place (guard piece
10 MUST-A, lane 5; doc/angelo/script_confinement.rst, "Angelo's own I/O").

A chat's provider lives as long as the chat, holding its settings dict, and a custom endpoint is a frozen dataclass
that ``object.__setattr__`` still changes. A script that reached either could choose what Angelo starts unconfined:
the runtime's profile folder (``CODEX_HOME`` / ``CLAUDE_CONFIG_DIR``, whose configuration names programs the
runtime runs), the runtime's program and its environment, or a key program's argv. So:

* a profile folder must lie in Angelo's private folders, checked (by C) before anything is made in it;
* the program is Preferences' pick (``cli_runtime.chosen``) or the CLI found by its fixed name, never a path from
  settings or the provider, and the environment is built again for every start;
* a key program's argv is read from this computer's policy when it runs, never from the endpoint Angelo holds.

Runs outside Blender: C's ``judge_path`` is stood in for by a fixed private root.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import dataclasses
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import claude_protocol_fixtures  # noqa: E402
import platform_env  # noqa: E402
from host_waits import until  # noqa: E402
from angelo_agent import (agent_runtime, authentication, cli_runtime, custom_endpoints, endpoint_keys,  # noqa: E402
                          managed_policy, owned_paths, runtime_environment, runtime_launch)
from angelo_agent.protocol import RpcError  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
CONNECTION = "6f1c2d3e-4a5b-4c6d-8e7f-90a1b2c3d4e5"


def executable(folder, name):
    """A stand-in CLI named as the platform finds one by its name: ``name.exe`` on Windows (``shutil.which`` looks
    only for PATHEXT's extensions there), ``name`` with its execute bit elsewhere."""
    path = Path(folder) / (name + ".exe" if os.name == "nt" else name)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def same_file_name(found, expected):
    """Windows' lookups may spell the extension in PATHEXT's case (``codex.EXE``); the file is the same."""
    return os.path.normcase(str(found)) == os.path.normcase(str(expected))


class Folders(unittest.TestCase):
    """A private root C would keep (Angelo's own folders) and a folder of the person's (a workspace)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.elsewhere, True)
        root = os.path.realpath(self.root)

        def judge_path(path, write=False):
            path = os.path.realpath(path)
            return "private" if path == root or path.startswith(root + os.sep) else ""

        patcher = mock.patch.object(owned_paths, "_GUARD", SimpleNamespace(judge_path=judge_path))
        patcher.start()
        self.addCleanup(patcher.stop)


class ProfileFolderTests(Folders):
    def test_a_new_profile_outside_the_private_folders_is_refused_and_nothing_is_made(self):
        for mode, family in (("codex_api", "codex-profile"), ("claude_api", "claude-profile")):
            with self.subTest(mode=mode), self.assertRaises(ValueError):   # storage.ProfileOutside
                runtime_launch.profile_environment({}, mode, str(self.elsewhere), False, CONNECTION)
            self.assertFalse((self.elsewhere / family).exists(), "nothing may be made where C refuses")

    def test_a_saved_profile_outside_the_private_folders_is_refused(self):
        # The form a saved profile has (<family>-profile/<id>), in a folder a script can write.
        planted = self.elsewhere / "x" / "codex-profile" / CONNECTION
        planted.mkdir(parents=True)
        (planted / "config.toml").write_text('notify = ["/bin/sh", "-c", "echo"]\n')
        with self.assertRaises(ValueError):   # storage.ProfileOutside
            runtime_launch.profile_environment({}, "codex_subscription", str(self.root), False, CONNECTION,
                                               str(planted))

    def test_a_claude_launch_with_its_profile_outside_is_refused_at_the_last_gate(self):
        outside = {"CLAUDE_CONFIG_DIR": str(self.elsewhere / "claude-profile" / CONNECTION)}
        with self.assertRaises(RpcError):
            runtime_environment.require_angelo_profile("claude_api", outside, CONNECTION)
        inside = {"CLAUDE_CONFIG_DIR": str(self.root / "claude-profile" / CONNECTION)}
        runtime_environment.require_angelo_profile("claude_api", inside, CONNECTION)

    def test_a_profile_in_the_private_folders_is_made_and_used(self):
        env = runtime_launch.profile_environment({}, "codex_api", str(self.root), False, CONNECTION)
        home = self.root / "codex-profile" / CONNECTION
        self.assertEqual(env["CODEX_HOME"], str(home))
        self.assertTrue(home.is_dir())


class ProgramTests(Folders):
    def setUp(self):
        super().setUp()
        self.path = self.root / "bin"   # Where the CLI is found by its fixed name (PATH, which no script changes).
        self.path.mkdir()
        self.addCleanup(lambda: cli_runtime.choose())   # Preferences' picks back to none.

    def test_a_claude_start_runs_the_found_program_with_a_fresh_environment(self):
        from angelo_agent.claude_transport import ClaudeRequest
        from angelo_agent.providers import ClaudeProvider
        chosen = executable(self.path, "claude")
        host = claude_protocol_fixtures.host()
        host.store, host.api_key = SimpleNamespace(directory=str(self.root)), ""
        planted = executable(self.elsewhere, "claude")
        provider = ClaudeProvider(host, {"claude_path": planted, "auth_directory": str(self.root)})
        self.addCleanup(provider.close)
        # What a script could leave on a provider that connected earlier: another program, another environment.
        provider.executable, provider.env = planted, {"NODE_OPTIONS": "--require " + planted}
        provider.settings["claude_path"] = planted
        process = claude_protocol_fixtures.NativeInitializeFixture()
        with platform_env.cleared(PATH=str(self.path), HOME=str(self.root)), \
                mock.patch("angelo_agent.cli_windows.git_bash", return_value=str(self.root / "bash.exe")), \
                mock.patch("angelo_agent.child_process.run", return_value=SimpleNamespace(
                    returncode=0, stdout=claude_protocol_fixtures.SUPPORTED)), \
                mock.patch("angelo_agent.child_process.popen", return_value=process) as popen:
            provider._start(None, owner=ClaudeRequest(host))
        argv, options = popen.call_args.args[0], popen.call_args.kwargs
        self.assertTrue(same_file_name(argv[0], chosen), (argv[0], chosen))
        self.assertNotIn("NODE_OPTIONS", options["env"])

    def sign_in_command(self, settings):
        with mock.patch.object(agent_runtime, "executable", return_value=None), \
                platform_env.cleared(PATH=str(self.path), HOME=str(self.root)):
            command, _env = authentication.native_auth_command(
                "codex_subscription", dict(settings, auth_directory=str(self.root)), str(self.root))
        return command[0]

    def test_a_sign_in_check_runs_the_found_program_not_the_settings_one(self):
        found = executable(self.path, "codex")
        command = self.sign_in_command({"codex_path": executable(self.elsewhere, "codex")})
        self.assertTrue(same_file_name(command, found), (command, found))

    def test_preferences_pick_is_what_every_launch_reads(self):
        on_path = executable(self.path, "codex")
        picked = executable(self.root, "codex")   # The person's file, chosen in Preferences (ui.connection_settings).
        cli_runtime.choose(codex_path=picked)
        command = self.sign_in_command({"codex_path": executable(self.elsewhere, "codex")})
        self.assertTrue(same_file_name(command, picked), (command, picked))
        cli_runtime.choose()   # Cleared in Preferences: the CLI found by its name again.
        command = self.sign_in_command({})
        self.assertTrue(same_file_name(command, on_path), (command, on_path))

    def test_every_runtime_program_lookup_takes_preferences_pick(self):
        """The class, not one site: every ``find_executable`` call in the add-on passes ``chosen(<its name>)`` (or
        nothing), so no settings dict or attribute can name the program."""
        wrong = []
        for path in sorted(PACKAGE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "find_executable" not in text:   # Only the few modules that name it are parsed.
                continue
            for node in ast.walk(ast.parse(text)):
                if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None))
                        == "find_executable"):
                    continue
                keyword = next((item.value for item in node.keywords if item.arg == "configured"), None)
                configured = node.args[1] if len(node.args) > 1 else keyword
                ok = configured is None or (isinstance(configured, ast.Call)
                                            and getattr(configured.func, "id", None) == "chosen"
                                            and ast.dump(configured.args[0]) == ast.dump(node.args[0]))
                if not ok:
                    wrong.append("%s:%d" % (path.name, node.lineno))
        self.assertEqual(wrong, [])


class EndpointKeyProgramTests(unittest.TestCase):
    def endpoint(self, command):
        description = {"id": "1f2e3d4c-5b6a-4987-a6b5-c4d3e2f1a0b9", "name": "Proxy", "baseUrl": "https://llm.test/",
                       "formats": {"openai-responses": {"path": "/v1"}}, "credential": "command",
                       "command": command, "refreshSeconds": 600,
                       "models": [{"id": "house-model", "format": "openai-responses"}]}
        (found,), _ = custom_endpoints.endpoints([description], "policy")
        return found

    def run_key_program(self, held, policy):
        seen = []

        def run(argv, **_options):
            seen.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="short-lived-key\n")
        with mock.patch.object(managed_policy, "load", return_value=policy):
            self.assertTrue(endpoint_keys.run_command(held, run))
            until(lambda: not endpoint_keys.running(held.id), what="the key program's job")
        self.addCleanup(endpoint_keys.forget)
        return seen, endpoint_keys.take_finished(now=0.0)

    def test_a_key_program_changed_in_place_runs_the_policys_program(self):
        named = self.endpoint(["/opt/sso/key", "--llm"])
        held = dataclasses.replace(named)   # The endpoint Angelo keeps, changed in place by a script:
        object.__setattr__(held, "command", ("/bin/sh", "-c", "echo planted"))
        policy = managed_policy.Policy(custom_endpoints=(named,), organization="org-1")
        seen, finished = self.run_key_program(held, policy)
        self.assertEqual(seen, [["/opt/sso/key", "--llm"]])
        self.assertEqual(finished, [(named.id, "short-lived-key", "", 600)])

    def test_an_endpoint_the_policy_no_longer_names_runs_nothing(self):
        held = self.endpoint(["/opt/sso/key"])
        seen, finished = self.run_key_program(held, managed_policy.Policy())
        self.assertEqual(seen, [])
        self.assertEqual([(identity, key) for identity, key, _problem, _lifetime in finished], [(held.id, None)])


if __name__ == "__main__":
    unittest.main()
