# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""CLI discovery under a Finder launch's minimal PATH, and the tested-version policy.

Nothing here starts an official runtime: install trees are temporary files, the
login shell is a script written for the test, and the Codex process is a fixture.
"""

import contextlib
import io
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import child_process, claude_transport, cli_runtime, providers
from angelo_agent.cli_runtime import RUNTIMES
from claude_protocol_fixtures import NEWER, REQUIRES, TESTED, TOO_OLD  # The supported range, from RUNTIMES.
from angelo_agent.protocol import RpcError

FINDER_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def executable(path, text="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@unittest.skipIf(os.name == "nt", "macOS and Linux discovery; Windows has its own (test_native_windows_cli.py, "
                                   "and windows_cli_checks.py on a real Windows machine)")
class FinderLaunch(unittest.TestCase):
    """A temporary home and system tree stand in for /Users/x and /opt/homebrew; PATH is a Finder launch's."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home, self.system = self.root / "home", self.root / "opt/homebrew/bin"
        self.system.mkdir(parents=True)
        for value in (patch.object(Path, "home", return_value=self.home),
                      patch.object(cli_runtime, "SYSTEM_DIRECTORIES", (str(self.system),)),
                      patch.object(cli_runtime, "CODEX_BUNDLE", str(self.root / "no/such/codex")),
                      patch.dict(os.environ, {"PATH": FINDER_PATH}),
                      patch.object(cli_runtime, "login_shell_path", return_value="")):
            value.start()
            self.addCleanup(value.stop)
        for variable in ("NPM_CONFIG_PREFIX", "npm_config_prefix", "VOLTA_HOME", "ASDF_DATA_DIR", "PNPM_HOME",
                         "BUN_INSTALL", "N_PREFIX"):
            os.environ.pop(variable, None)


class DiscoveryTests(FinderLaunch):
    def test_installs_a_finder_launch_cannot_see_are_found(self):
        layouts = {
            "homebrew": lambda name: self.system / name,
            "native installer": lambda name: self.home / ".local/bin" / name,
            "npm global": lambda name: self.home / ".npm-global/bin" / name,
            "volta": lambda name: self.home / ".volta/bin" / name,
            "asdf": lambda name: self.home / ".asdf/shims" / name,
            "nvm": lambda name: self.home / ".nvm/versions/node/v20.11.1/bin" / name,
            "fnm": lambda name: self.home / ".local/share/fnm/node-versions/v22.1.0/installation/bin" / name,
        }
        for name in ("claude", "codex"):
            for label, place in layouts.items():
                with self.subTest(name=name, layout=label):
                    expected = executable(place(name))
                    self.assertEqual(providers.find_executable(name), expected)
                    Path(expected).unlink()

    def test_the_newest_node_version_wins_and_only_a_few_are_searched(self):
        versions = self.home / ".nvm/versions/node"
        old, new = executable(versions / "v18.19.0/bin/claude"), executable(versions / "v22.2.0/bin/claude")
        self.assertEqual(providers.find_executable("claude"), new)
        Path(new).unlink()
        self.assertEqual(providers.find_executable("claude"), old)
        for minor in range(cli_runtime.VERSION_DIRECTORIES + 2):
            (versions / f"v30.{minor}.0/bin").mkdir(parents=True)
        Path(old).unlink()
        executable(versions / "v18.19.0/bin/claude")  # Older than the newest six: outside the bounded search.
        with self.assertRaises(RpcError):
            providers.find_executable("claude")

    def test_path_and_the_configured_executable_come_first(self):
        listed = executable(self.root / "listed/claude")
        executable(self.system / "claude")
        configured = executable(self.root / "chosen/claude")
        with patch.dict(os.environ, {"PATH": FINDER_PATH + ":" + str(Path(listed).parent)}):
            self.assertEqual(providers.find_executable("claude"), listed)
            self.assertEqual(providers.find_executable("claude", configured), configured)
        self.assertEqual(providers.find_executable("claude"), str(self.system / "claude"))

    def test_a_mistyped_configured_path_is_named_in_the_error(self):
        with self.assertRaisesRegex(RpcError, "configured path /no/such/claude"):
            providers.find_executable("claude", "/no/such/claude")

    def test_the_login_shell_is_asked_only_after_every_fixed_location_missed(self):
        target = executable(self.root / "custom/bin/claude")
        with patch.object(cli_runtime, "login_shell_path", return_value=str(Path(target).parent)) as probe:
            executable(self.system / "codex")
            self.assertEqual(providers.find_executable("codex"), str(self.system / "codex"))
            probe.assert_not_called()
            self.assertEqual(providers.find_executable("claude"), target)
            probe.assert_called_once()

    def test_not_found_says_how_to_install_and_how_to_point_at_it(self):
        for name, command in (("claude", "npm install -g @anthropic-ai/claude-code"), ("codex", "npm install -g @openai/codex")):
            with self.subTest(name=name):
                with self.assertRaises(RpcError) as caught:
                    providers.find_executable(name)
                text = str(caught.exception)
                self.assertIn(command, text)
                self.assertIn(f"`which {name}`", text)
                self.assertIn("Executable", text)
                self.assertIn(str(self.system), text)  # What was searched, so the person can see what is missing.
                self.assertNotIn("set its path in Angelo preferences", text)  # The old dead end.

    def test_every_search_is_bounded_and_spawns_nothing_before_the_probe(self):
        with patch.object(cli_runtime.subprocess, "Popen", side_effect=AssertionError("spawned")):
            executable(self.system / "claude")
            self.assertEqual(providers.find_executable("claude"), str(self.system / "claude"))
        limit = (7 + len(cli_runtime.HOME_DIRECTORIES) + len(cli_runtime.SYSTEM_DIRECTORIES)
                 + cli_runtime.VERSION_DIRECTORIES * len(cli_runtime.VERSION_HOMES))
        self.assertLessEqual(len(cli_runtime.known_directories()), limit)


class PathForTheLaunchedCli(FinderLaunch):
    """An npm package's #!/usr/bin/env node needs node's directory on the child's PATH."""

    def test_the_directory_of_a_cli_found_off_path_is_appended_after_the_persons_path(self):
        tool = executable(self.home / ".nvm/versions/node/v20.11.1/bin/claude")
        env = cli_runtime.with_runtime_path({"PATH": FINDER_PATH, "KEEP": "1"}, tool)
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[:4], FINDER_PATH.split(":"))
        self.assertIn(str(Path(tool).parent), parts)
        self.assertIn(str(self.system), parts)
        self.assertEqual(env["KEEP"], "1")
        self.assertEqual(len(parts), len(set(parts)))

    def test_a_cli_found_on_path_leaves_the_environment_unchanged(self):
        tool = executable(self.root / "bin/claude")
        env = {"PATH": FINDER_PATH + ":" + str(Path(tool).parent)}
        self.assertEqual(cli_runtime.with_runtime_path(dict(env), tool), env)

    def test_the_launch_environments_use_it(self):
        tool = executable(self.system / "claude")
        command, env = providers.codex_command(tool, "codex_subscription", self.temp.name)
        self.assertIn(str(self.system), env["PATH"].split(os.pathsep))
        self.assertEqual(command[0], tool)


@unittest.skipIf(os.name == "nt", "The login-shell PATH probe is macOS and Linux only; Windows reads the PATH "
                                   "saved in its registry instead (test_native_windows_cli.py)")
class LoginShellProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "bin").mkdir()
        patcher = patch.object(cli_runtime, "_login_path", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def shell(self, body):
        return executable(self.root / "fake-shell", "#!/bin/sh\n" + body)

    def probe(self, body, timeout=20.0):  # Generous: a loaded machine (or a first-run scan of the new script) is slow.
        with patch.dict(os.environ, {"SHELL": self.shell(body)}):
            return cli_runtime.login_shell_path(timeout)

    def test_only_the_framed_absolute_existing_directories_are_kept(self):
        listed = f"{self.root}/bin:relative/bin:{self.root}/missing:{self.root}/bin"
        result = self.probe(f'echo noise from a profile; printf "__ANGELO_PATH__{listed}__ANGELO_PATH__"; echo more')
        self.assertEqual(result, f"{self.root}/bin")

    def test_the_answer_is_cached_for_the_process(self):
        self.assertEqual(self.probe(f'printf "__ANGELO_PATH__{self.root}/bin__ANGELO_PATH__"'), f"{self.root}/bin")
        with patch.dict(os.environ, {"SHELL": "/no/such/shell"}):
            self.assertEqual(cli_runtime.login_shell_path(), f"{self.root}/bin")

    def test_a_profile_that_waits_is_killed_with_its_children_at_the_timeout(self):
        marker = self.root / "pids"
        started = time.monotonic()
        result = self.probe(f'echo $$ > {marker}\nsleep 30 &\necho $! >> {marker}\nwait', timeout=1.5)
        self.assertEqual(result, "")
        self.assertLess(time.monotonic() - started, 6)
        pids = [int(line) for line in marker.read_text().split()]
        self.assertEqual(len(pids), 2)  # The shell and the process its profile left running.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(self.alive(pid) for pid in pids):
            time.sleep(.05)
        self.assertFalse([pid for pid in pids if self.alive(pid)])

    @staticmethod
    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_a_flood_of_output_is_cut_off(self):
        started = time.monotonic()
        self.assertEqual(self.probe("yes __ANGELO_PATH__"), "")
        self.assertLess(time.monotonic() - started, 5)

    def test_an_unusable_shell_means_no_extra_directories(self):
        with patch.dict(os.environ, {"SHELL": "relative-shell"}):
            self.assertEqual(cli_runtime.login_shell_path(), "")


class TestedVersionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(cli_runtime, "_noted", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_cli_records_its_tested_range(self):
        for name, spec in RUNTIMES.items():
            with self.subTest(name=name):
                self.assertLessEqual(spec["floor"], spec["ceiling"])
                self.assertTrue(spec["relies_on"] and spec["update"])
        # Claude Code's stable channel (the default Homebrew cask): the newest launch-checked and gated release.
        self.assertEqual(RUNTIMES["claude"]["floor"], (2, 1, 292))
        self.assertEqual(RUNTIMES["claude"]["ceiling"], (2, 1, 301))
        self.assertEqual(RUNTIMES["codex"]["floor"], (0, 160, 0))
        self.assertEqual(RUNTIMES["codex"]["ceiling"], (0, 162, 0), "the newest gated Codex release")
        self.assertEqual(claude_transport.MINIMUM_VERSION, RUNTIMES["claude"]["floor"])

    def test_codex_below_the_floor_or_unreadable_is_refused(self):
        agent = lambda version: {"userAgent": f"angelo_blender/{version} (Mac OS 26.3.0; arm64) unknown (angelo_blender; 0.1.0)"}
        self.assertEqual(cli_runtime.codex_version(agent("0.160.0")), (0, 160, 0))
        self.assertEqual(cli_runtime.codex_version(agent("0.171.2")), (0, 171, 2))
        self.assertEqual(cli_runtime.codex_version(agent("0.162.0-alpha.2")), (0, 162, 0), "the real 0.162 agent")
        self.assertEqual(cli_runtime.codex_version(agent("0.163.1+build.7")), (0, 163, 1))
        spaced = {"userAgent": "Angelo Blender/0.162.0-alpha.2 (Mac OS 26.3.0; arm64) unknown (Angelo Blender; 0.1.0)"}
        self.assertEqual(cli_runtime.codex_version(spaced), (0, 162, 0), "a client name with a space")
        self.assertIsNone(cli_runtime.newer_than_gated("codex", cli_runtime.codex_version(agent("0.162.0-alpha.2"))))
        for bad in (agent("0.159.9"), agent("0.99.0"), {}, {"userAgent": 7}, {"userAgent": "angelo_blender (no version)"},
                    {"userAgent": "angelo_blender/0.160 (Mac OS 26.3.0)"}, {"userAgent": "angelo_blender/0.160.0x"}, None):
            with self.subTest(bad=bad), self.assertRaises(RpcError) as caught:
                cli_runtime.codex_version(bad)
            self.assertIn("Angelo requires Codex 0.160.0 or newer", str(caught.exception))
            self.assertIn("npm install -g @openai/codex", str(caught.exception))

    def test_a_newer_runtime_is_only_logged_once_per_version(self):
        """Angelo follows the latest releases: newer is recorded in one quiet log line, never shown or refused."""
        for name in ("claude", "codex"):
            ceiling = RUNTIMES[name]["ceiling"]
            newer = (ceiling[:2] + (ceiling[2] + 1,), ceiling[:1] + (ceiling[1] + 1, 0), (ceiling[0] + 1, 0, 0))
            with self.subTest(name=name), logged() as log:
                self.assertIsNone(cli_runtime.newer_than_gated(name, ceiling))
                self.assertFalse(cli_runtime.note_newer(name, ceiling))
                for version in newer:
                    self.assertTrue(cli_runtime.note_newer(name, version))
                    self.assertFalse(cli_runtime.note_newer(name, version), "once per process and version")
            lines = log.getvalue().splitlines()
            self.assertEqual(len(lines), len(newer))
            for line, version in zip(lines, newer):
                self.assertEqual(line, "Angelo: %s %s is newer than the version Angelo's gates ran on (%s); running it "
                                 "as usual" % (RUNTIMES[name]["label"], cli_runtime.format_version(version),
                                               cli_runtime.format_version(ceiling)))
                for nag in ("downgrade", "install the tested", "misbehave"):
                    self.assertNotIn(nag, line)

    def test_codex_connect_refuses_below_the_floor_before_any_experimental_request(self):
        requests, error = self.codex_connect("angelo_blender/0.150.0 (Mac OS; arm64)", [])
        self.assertEqual(requests, ["initialize"])
        self.assertIn("Angelo requires Codex 0.160.0 or newer (found 0.150.0)", error)

    def codex_connect(self, user_agent, emitted):
        requests = []

        class FakeProcess:
            closed = SimpleNamespace(is_set=lambda: False)

            def __init__(self, *args, **kwargs):
                pass

            def request(self, method, params=None, **kwargs):
                requests.append(method)
                return {"userAgent": user_agent}

            notify = close = lambda self, *args, **kwargs: None

        controller = SimpleNamespace(state={"provider": "codex_subscription", "native_id": None}, api_key="",
                                     store=SimpleNamespace(directory="."), workspace=".", cancelled=False,
                                     emit=lambda kind, data: emitted.append(data), text_only=False, models=[])
        with patch.object(providers, "find_executable", return_value="/fixture/codex"), \
                patch.object(providers, "codex_command", return_value=(["/fixture/codex"], {})), \
                patch.object(providers, "JsonRpcProcess", FakeProcess):
            provider = providers.CodexProvider(controller, {})
            with self.assertRaises(RpcError) as caught:
                provider.connect()
        return requests, str(caught.exception)

    def test_codex_connect_runs_past_the_ceiling_and_shows_nothing(self):
        emitted = []
        with logged() as log:
            for _ in range(2):
                requests, error = self.codex_connect("angelo_blender/0.171.0 (Mac OS; arm64)", emitted)
                self.assertEqual(requests[:2], ["initialize", "account/read"])  # Not refused: the next request was made.
                self.assertIn("billing mode", error)  # The fixture is not signed in; that is its own, later check.
        self.assertEqual(emitted, [], "No row: no notice, warning or downgrade advice")
        self.assertEqual(len(log.getvalue().splitlines()), 1)
        self.assertIn("Codex 0.171.0 is newer than the version Angelo's gates ran on", log.getvalue())

    def test_claude_start_accepts_a_newer_runtime_and_refuses_an_older_one(self):
        for output, lines in ((TESTED, 0), (NEWER, 1), ("3.0.0 (Claude Code)\n", 1)):
            with self.subTest(output=output), patch.object(child_process, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=output)), logged() as log:
                version = claude_transport.require_supported_runtime("claude", {})
                self.assertEqual(cli_runtime.format_version(version), output.split()[0])
                cli_runtime.note_newer("claude", version)
            self.assertEqual(len(log.getvalue().splitlines()), lines)
        with patch.object(child_process, "run", return_value=SimpleNamespace(returncode=0, stdout=TOO_OLD)):
            with self.assertRaisesRegex(RpcError, REQUIRES + r" \(found " + TOO_OLD.split()[0].replace(".", r"\.") + r"\)"):
                claude_transport.require_supported_runtime("claude", {})


@contextlib.contextmanager
def logged():
    """Angelo's console output (stderr) while the block runs, worker threads included."""
    with contextlib.redirect_stderr(io.StringIO()) as log:
        yield log


class ClaudeNewerEndToEndTests(unittest.TestCase):
    """The real Controller and ClaudeProvider: a newer Claude Code runs as usual; the chat shows nothing about it."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from claude_protocol_fixtures import NativeInitializeFixture, completed
        from angelo_agent.chat_settings import normalize_models
        from angelo_agent.controller import Controller
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = Controller(self.temp.name)
        self.addCleanup(self.controller.close)
        self.controller.set_document(("scratch", 1))
        models = normalize_models([{"value": "sonnet", "displayName": "Claude Sonnet", "isDefault": True,
                                    "supportedEffortLevels": ["low", "medium", "high"]}], "claude")
        self.controller.choose_connection("claude_subscription", {"model": ""}, "", None, models, "sonnet")
        self.prompts, self.launches, self.version = [], [], NEWER.split()[0]

        class OfflineClaude(providers.ClaudeProvider):
            def connect(inner):
                inner.executable, inner.env = "claude", {"FIXTURE": "only"}
                return inner.controller.state["native_id"]

        def reply(process, event):
            content = event["message"]["content"]
            self.prompts.append(content if isinstance(content, str) else content[0]["text"])
            process.stdout.emit(completed())

        def launch(*args, **kwargs):
            self.launches.append(args)
            return NativeInitializeFixture(on_user=reply)

        for value in (patch("angelo_agent.controller.ClaudeProvider", OfflineClaude),
                      patch("angelo_agent.child_process.popen", side_effect=launch),
                      patch("angelo_agent.child_process.run",
                            side_effect=lambda *a, **k: SimpleNamespace(returncode=0, stdout=self.version + " (Claude Code)\n")),
                      patch.object(cli_runtime, "_noted", set())):
            value.start()
            self.addCleanup(value.stop)

    def turn(self, text):
        self.controller.send(text, {"objects": []})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.controller.pump_events()
            if not self.controller.busy and not (self.controller._worker and self.controller._worker.is_alive()):
                self.controller.pump_events()
                return
            time.sleep(.002)
        self.fail("Claude fixture turn did not finish")

    def rows(self, *roles):
        return [m for m in self.controller.state["messages"] if m.get("notice") or m.get("role") in roles]

    def test_a_newer_claude_runs_with_no_row_and_one_log_line(self):
        for version in (NEWER.split()[0], "2.9.0", "3.0.0"):  # The next release, a minor and a major ahead.
            with self.subTest(version=version), logged() as log:
                self.version = version
                self.controller.new_chat(("scratch", 1))
                self.turn("first")
                self.turn("second")
                self.assertEqual(self.rows("system", "error"), [], "No notice, warning, error or downgrade advice")
                self.assertEqual([m["role"] for m in self.controller.state["messages"]].count("assistant"), 2)
            lines = [line for line in log.getvalue().splitlines() if "newer than the version" in line]
            self.assertEqual(lines, ["Angelo: Claude Code %s is newer than the version Angelo's gates ran on (%s); "
                                     "running it as usual" % (version, TESTED.split()[0])])
        self.assertEqual(len(self.launches), 3, "One launch per chat, none refused")
        self.assertFalse(any("newer than" in prompt for prompt in self.prompts), "No model ever reads it")

    def test_a_runtime_inside_the_tested_range_logs_nothing(self):
        self.version = TESTED.split()[0]
        with logged() as log:
            self.turn("first")
        self.assertEqual(self.rows("system", "error"), [])
        self.assertNotIn("newer than", log.getvalue())

    def test_a_runtime_below_the_floor_never_launches(self):
        self.version = TOO_OLD.split()[0]
        self.turn("first")
        self.assertEqual(self.launches, [])
        errors = [m["text"] for m in self.controller.state["messages"] if m["role"] == "error"]
        found = "(found %s)" % TOO_OLD.split()[0]
        self.assertTrue(any(REQUIRES in text and found in text and "Update the official CLI" in text for text in errors),
                        errors)


if __name__ == "__main__":
    unittest.main()
