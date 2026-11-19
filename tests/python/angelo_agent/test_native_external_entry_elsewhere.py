# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's Angelo entry that starts another build's (or copy's, or profile's) server is said, and Connect fixes it.

The 2.0.1 internal test: Codex's entry still started 2.0.0's launcher from another folder, while the card showed
Codex as Connected with no Connect (acd0d465ee9: ``has_entry`` checks only that an entry exists). Each test names
what failed there. Every read is in a fresh temporary home; never an app's real settings. Contract:
doc/angelo/external_mcp.rst, "Two Angelo builds".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import external_setup  # noqa: E402

PYTHON = "/Applications/Angelo.app/Contents/Resources/5.3/python/bin/python3.13"
OLD_BUNDLE = "/Volumes/Older/Angelo 2.0.0/Angelo.app"
OLD_PYTHON = OLD_BUNDLE + "/Contents/Resources/5.3/python/bin/python3.13"
OLD_LAUNCHER = OLD_BUNDLE + "/Contents/Resources/scripts/angelo/angelo_mcp.py"
SHOWN = str(Path(OLD_BUNDLE))   # As elsewhere() names it: this platform's separators (Windows: \\).
# Where each app keeps its settings in a home (darwin), and the key its ``angelo`` server sits under.
JSON_APPS = {"claude_desktop": ("Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
             "cursor": (".cursor/mcp.json", "mcpServers"),
             "vscode": ("Library/Application Support/Code/User/mcp.json", "servers")}


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="smis-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.state = self.home / "state"
        self.where = dict(home=self.home, environ={}, platform="darwin")

    def elsewhere(self, app):
        return external_setup.elsewhere(app, PYTHON, self.state, **self.where)

    def old_args(self, app="codex", state=None):
        return ["-I", OLD_LAUNCHER, "--client", external_setup.DEFAULT_CLIENT[app], "--state-dir",
                str(state or self.state)]

    def write_codex(self, command, args):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(external_setup._codex_with("", command, args))
        return path

    def write_json(self, app, entry):
        relative, key = JSON_APPS[app] if app in JSON_APPS else (".claude.json", "mcpServers")
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({key: {"angelo": entry, "other": {"command": "x"}}}))
        return path

    def test_codex_s_entry_for_another_build_is_named_by_its_bundle_and_connect_replaces_it(self):
        """Failed on acd0d465ee9: no reader compared the launcher (``elsewhere`` did not exist)."""
        self.write_codex(OLD_PYTHON, self.old_args())
        self.assertEqual(self.elsewhere("codex"), SHOWN)
        external_setup.connect("codex", PYTHON, self.state, **self.where)
        self.assertEqual(self.elsewhere("codex"), "")
        table = tomllib.loads((self.home / ".codex" / "config.toml").read_text())["mcp_servers"]["angelo"]
        self.assertEqual(table["args"][1], str(external_setup.launcher_path()))

    def test_every_app_external_setup_writes_is_read_the_same_way(self):
        for app in ("claude_code", *JSON_APPS):
            with self.subTest(app=app):
                command, args = external_setup.server_command(app, PYTHON, self.state)
                entry = {"command": command, "args": args}
                if app == "vscode":
                    entry["type"] = "stdio"
                self.write_json(app, entry)
                self.assertEqual(self.elsewhere(app), "", "the entry Connect writes is this Angelo's")
                self.write_json(app, dict(entry, command=OLD_PYTHON, args=self.old_args(app)))
                self.assertEqual(self.elsewhere(app), SHOWN)

    def test_another_profile_and_a_server_that_is_no_angelo_launcher_count_as_elsewhere(self):
        self.assertEqual(self.elsewhere("codex"), "", "no entry")
        command, args = external_setup.server_command("codex", PYTHON, self.home / "other-profile")
        self.write_codex(command, args)
        self.assertEqual(self.elsewhere("codex"), str(external_setup.launcher_path()))
        self.write_codex("npx", ["-y", "some-other-mcp"])
        self.assertEqual(self.elsewhere("codex"), "npx")
        self.write_json("cursor", "not a table")
        self.assertEqual(self.elsewhere("cursor"), external_setup.SERVER)

    def test_an_empty_environment_is_the_one_meant_never_the_process_s_own(self):
        """Failed on acd0d465ee9: ``environ or os.environ`` read the process's CLAUDE_CONFIG_DIR for ``{}``."""
        self.write_json("claude_code", {"command": OLD_PYTHON, "args": self.old_args("claude_code")})
        elsewhere = Path(tempfile.mkdtemp(prefix="smis-cfg-"))
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(elsewhere)}):
            self.assertTrue(external_setup.has_entry("claude_code", **self.where))
            self.assertEqual(self.elsewhere("claude_code"), SHOWN)

    def test_reading_never_writes_any_settings_file(self):
        paths = [self.write_codex(OLD_PYTHON, self.old_args())]
        paths += [self.write_json(app, {"command": OLD_PYTHON, "args": self.old_args(app)})
                  for app in ("claude_code", *JSON_APPS)]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        listing = sorted(str(path) for path in self.home.rglob("*"))
        for app in ("claude_code", "codex", *JSON_APPS):
            self.elsewhere(app)
            external_setup.has_entry(app, **self.where)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, before)
        self.assertEqual(sorted(str(path) for path in self.home.rglob("*")), listing, "no backup or new file")


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blender_stand_in import StandIn
        cls.stand_in = StandIn(("ui", "external_ui", "external_clients", "external_setup", "providers_page_external",
                                "managed_policy"))
        cls.addClassCleanup(cls.stand_in.__exit__, None, None, None)
        cls.m = cls.stand_in.__enter__()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "state"

    def test_the_stand_in_hands_back_the_modules_the_code_reads(self):
        """Failed on a118df524b0 (Linux lane): external_ui read another external_setup than the one handed back, so
        a patch of it never reached Connect's work."""
        for name, module in self.m.items():
            for attribute, value in vars(module).items():
                if isinstance(value, type(sys)) and value.__name__.startswith("angelo_agent."):
                    with self.subTest(module=name, reads=attribute):
                        self.assertIs(value, sys.modules[value.__name__])

    def rows(self, elsewhere, connect=True):
        page, setup = self.m["providers_page_external"], self.m["external_setup"]
        if connect:
            self.m["external_clients"].AppRegistry(self.state).connect("codex")
        with mock.patch.object(setup, "installed", lambda app, *a, **k: True), \
                mock.patch.object(setup, "has_entry", lambda app, *a, **k: True), \
                mock.patch.object(setup, "elsewhere", lambda app, *a, **k: elsewhere if app == "codex" else ""):
            return {row["name"]: row for row in page._app_rows(self.state, False)}

    def test_a_connected_app_whose_entry_starts_another_build_offers_connect_and_says_why(self):
        """Failed on acd0d465ee9: Codex showed Connected, with no Connect, while its entry started 2.0.0."""
        codex = self.rows(OLD_BUNDLE)["Codex"]
        self.assertEqual(codex["chip"][0], "Starts another Angelo")
        self.assertIn(f"Codex's Angelo entry starts the MCP server in {OLD_BUNDLE}. Connect Codex points it at this "
                      "Angelo.", codex["lines"])
        self.assertEqual(codex["buttons"][0][:2], ("Connect Codex", "mcp_connect"))
        self.assertEqual(self.rows("")["Codex"]["chip"][0], "Connected")

    def test_an_app_not_connected_here_says_where_its_entry_points(self):
        codex = self.rows(OLD_BUNDLE, connect=False)["Codex"]
        self.assertIn(f"Codex's Angelo entry starts the MCP server in {OLD_BUNDLE}. Connect Codex points it at this "
                      "Angelo.", codex["lines"])
        self.assertEqual(codex["buttons"][0][:2], ("Connect Codex", "mcp_connect"))

    def test_connect_says_it_replaced_an_entry_that_started_another_build(self):
        ui_module = self.m["external_ui"]
        done = ui_module.connect_done("codex")
        self.assertEqual(done(""), ui_module.ADDED_SESSION)
        self.assertEqual(ui_module.connect_done("cursor")(""), ui_module.ADDED_RESTART)
        template, fields = done(OLD_BUNDLE)
        self.assertEqual((template, fields), (ui_module.UPDATED_SESSION, {"previous": OLD_BUNDLE}))
        self.addCleanup(ui_module.results.pop, "codex", None)
        ui_module.results["codex"] = (True, template, dict(fields, app="Codex"))
        self.assertEqual(ui_module.line("codex"), f"Updated Angelo in Codex: it started the MCP server in "
                         f"{OLD_BUNDLE}. Start a new Codex session to use it.")

    def test_connect_reads_where_the_entry_pointed_before_it_writes(self):
        """The click's own work: ``elsewhere`` first, then ``connect``; its result is the old place."""
        ui_module, setup = self.m["external_ui"], self.m["external_setup"]
        self.assertIs(ui_module.external_setup, setup, "the module Connect's work reads is the one patched")
        calls, works = [], []
        with mock.patch.object(setup, "elsewhere", lambda *a, **k: calls.append("elsewhere") or OLD_BUNDLE), \
                mock.patch.object(setup, "connect", lambda *a, **k: calls.append("connect") or "line"), \
                mock.patch.object(ui_module, "_state", lambda: self.state), \
                mock.patch.object(ui_module, "_in_background",
                                  lambda app, work, done, **k: works.append((work, done))), \
                mock.patch.object(ui_module, "_redraw", lambda: None), mock.patch.object(sys, "platform", "darwin"):
            with mock.patch.object(self.m["ui"], "preferences", lambda: SimpleNamespace(allow_local_cli=True)):
                operator = ui_module.ANGELO_OT_external_app_connect()
                operator.app, operator.report = "codex", lambda *a: None
                body = getattr(type(operator), "_granted_execute", None) or type(operator).execute
                self.assertEqual(body(operator, SimpleNamespace(preferences=SimpleNamespace(is_dirty=False))),
                                 {'FINISHED'})
            work, done = works[0]
            previous = work()   # The background work, run while the stand-ins are in place (never the real setup).
        self.assertEqual(previous, OLD_BUNDLE)
        self.assertEqual(calls, ["elsewhere", "connect"])
        self.assertEqual(done(previous)[0], ui_module.UPDATED_SESSION)


class ChatBarTests(unittest.TestCase):
    """The chat bar's menu never calls an app connected whose entry starts another build's server."""

    def test_an_entry_that_starts_another_build_is_not_connected(self):
        import angelo_agent
        from angelo_agent import chat_destinations as cd, external_clients
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        registry = external_clients.AppRegistry(root)
        registry.root.mkdir()
        registry.path.write_text(json.dumps({"apps": {"codex": {"app": "codex", "levels": ["READ"]}}}).ljust(1024))
        modules = {"ui": SimpleNamespace(state_directory=lambda: root), "external_ui": SimpleNamespace(results={})}
        stack.enter_context(mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in modules.items()}))
        for name, module in modules.items():
            stack.enter_context(mock.patch.object(angelo_agent, name, module, create=True))
        stack.enter_context(mock.patch.object(external_setup, "installed", return_value=True))
        stack.enter_context(mock.patch.object(external_setup, "has_entry", return_value=True))
        place = stack.enter_context(mock.patch.object(external_setup, "elsewhere", return_value=OLD_BUNDLE))
        stack.enter_context(mock.patch.object(cd, "_registry_seen", {}))
        self.assertEqual(cd._app_states(now=10)["codex"][0], "found")
        place.return_value = ""
        cd._registry_seen.clear()
        self.assertEqual(cd._app_states(now=20)["codex"][0], "connected")


if __name__ == "__main__":
    unittest.main()
