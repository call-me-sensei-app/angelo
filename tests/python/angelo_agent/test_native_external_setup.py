# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adding Angelo to an app's MCP settings touches only Angelo's entry (external_setup).

Every test writes into a fresh temporary home; never the real ~/.codex, ~/.claude.json or app folders.
Contract: doc/angelo/external_mcp.rst, "Connecting an app".
"""

import json
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import external_setup
from angelo_agent.external_setup import SetupError

PYTHON = "/Applications/Angelo.app/Contents/Resources/5.2/python/bin/python3.13"
CODEX_BEFORE = """model = "gpt-6.1-sol"

[mcp_servers.other]
command = "other-server"
args = ["--flag"]

[profiles.fast]
model = "x"
"""


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="smcp-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.state = self.home / "state"
        self.environ = {}

    def connect(self, app, **extra):
        return external_setup.connect(app, PYTHON, self.state, home=self.home, environ=self.environ,
                                      platform="darwin", **extra)

    def disconnect(self, app, **extra):
        return external_setup.disconnect(app, home=self.home, environ=self.environ, platform="darwin", **extra)

    def test_the_command_runs_the_bundled_launcher_isolated_with_this_apps_name(self):
        command, args = external_setup.server_command("codex", PYTHON, self.state)
        self.assertEqual(command, PYTHON)
        self.assertEqual(args[0], "-I")
        self.assertTrue(args[1].endswith("scripts/angelo/angelo_mcp.py"))
        self.assertEqual(args[2:], ["--client", "codex", "--state-dir", str(self.state)])
        self.assertTrue(Path(args[1]).is_file(), "the launcher ships next to the add-on")

    def test_codex_keeps_everything_else_and_round_trips(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir()
        path.write_text(CODEX_BEFORE)
        self.connect("codex")
        data = tomllib.loads(path.read_text())
        self.assertEqual(data["model"], "gpt-6.1-sol")
        self.assertEqual(data["mcp_servers"]["other"]["args"], ["--flag"])
        self.assertEqual(data["profiles"]["fast"]["model"], "x")
        entry = data["mcp_servers"]["angelo"]
        self.assertEqual(entry["command"], PYTHON)
        self.assertGreaterEqual(entry["tool_timeout_sec"], 900)
        self.assertEqual((path.parent / "config.toml.angelo-backup").read_text(), CODEX_BEFORE)
        self.connect("codex")   # Again: one entry, never two.
        self.assertEqual(path.read_text().count("[mcp_servers.angelo]"), 1)
        self.assertTrue(external_setup.has_entry("codex", home=self.home, environ=self.environ))
        self.assertTrue(self.disconnect("codex"))
        after = tomllib.loads(path.read_text())
        self.assertNotIn("angelo", after["mcp_servers"])
        self.assertEqual(after["profiles"]["fast"]["model"], "x")
        self.assertFalse(self.disconnect("codex"))

    def test_codex_home_is_respected(self):
        self.environ = {"CODEX_HOME": str(self.home / "elsewhere")}
        self.connect("codex")
        self.assertTrue((self.home / "elsewhere" / "config.toml").is_file())
        self.assertFalse((self.home / ".codex").exists())

    def test_codex_entry_in_an_unexpected_form_is_never_rewritten(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir()
        original = 'mcp_servers = { angelo = { command = "x" } }\n'
        path.write_text(original)
        with self.assertRaises(SetupError):
            self.connect("codex")
        self.assertEqual(path.read_text(), original)

    def test_json_apps_get_their_own_shape_and_keep_other_servers(self):
        cases = {"claude_desktop": (self.home / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers"),
                 "cursor": (self.home / ".cursor/mcp.json", "mcpServers"),
                 "vscode": (self.home / "Library/Application Support/Code/User/mcp.json", "servers")}
        for app, (path, key) in cases.items():
            with self.subTest(app=app):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"keep": 1, key: {"blender": {"command": "uvx", "args": ["blmcp"]}}}))
                self.connect(app)
                data = json.loads(path.read_text())
                self.assertEqual(data["keep"], 1)
                self.assertIn("blender", data[key], "Blender MCP's own entry is never touched")
                self.assertEqual(data[key]["angelo"]["command"], PYTHON)
                self.assertEqual(data[key]["angelo"].get("type"), "stdio" if app == "vscode" else None)
                self.assertTrue(self.disconnect(app))
                self.assertEqual(set(json.loads(path.read_text())[key]), {"blender"})

    def test_a_settings_file_with_comments_is_left_alone(self):
        path = self.home / "Library/Application Support/Code/User/mcp.json"
        path.parent.mkdir(parents=True)
        original = '{\n  // my servers\n  "servers": {}\n}\n'
        path.write_text(original)
        with self.assertRaises(SetupError):
            self.connect("vscode")
        self.assertEqual(path.read_text(), original)

    def test_claude_code_uses_its_own_command_at_user_scope(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        self.connect("claude_code", run=run, which=lambda name: "/usr/local/bin/claude" if name == "claude" else None)
        self.assertEqual(calls[0][1:], ["mcp", "remove", "--scope", "user", "angelo"])
        self.assertEqual(calls[1][1:6], ["mcp", "add", "--scope", "user", "angelo"])
        self.assertEqual(calls[1][6:9], ["--", PYTHON, "-I"])
        with self.assertRaises(SetupError):
            self.connect("claude_code", run=run, which=lambda name: None)

    def test_a_linked_settings_file_stays_a_link(self):
        repo = self.home / "dotfiles"
        repo.mkdir()
        (repo / "mcp.json").write_text('{"mcpServers": {"other": {"command": "x"}}}')
        (self.home / ".cursor").mkdir()
        (self.home / ".cursor" / "mcp.json").symlink_to(repo / "mcp.json")
        self.connect("cursor")
        self.assertTrue((self.home / ".cursor" / "mcp.json").is_symlink())
        self.assertEqual(set(json.loads((repo / "mcp.json").read_text())["mcpServers"]), {"other", "angelo"})

    def test_json_angelo_cannot_write_back_exactly_is_left_alone(self):
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir()
        for original in ('{"mcpServers": {"a": {"command": "a"}}, "mcpServers": {"b": {"command": "b"}}}',
                         '{"mcpServers": {"other": {"command": "x", "timeout": 1e400}}}'):
            with self.subTest(original=original):
                path.write_text(original)
                with self.assertRaises(SetupError):
                    self.connect("cursor")
                self.assertEqual(path.read_text(), original)

    def test_codex_look_alike_lines_and_the_next_tables_comment_are_kept(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir()
        inside = "developer_instructions = \'\'\'\n[mcp_servers.angelo]\ncommand = \"x\"\n\'\'\'\n"
        path.write_text(inside)
        self.assertFalse(external_setup.has_entry("codex", home=self.home, environ=self.environ))
        with self.assertRaises(SetupError):
            self.connect("codex")
        self.assertEqual(path.read_text(), inside)
        path.write_text('[mcp_servers.angelo]\ncommand = "old"\n\n# My profiles\n[profiles.fast]\nmodel = "x"\n')
        self.connect("codex")
        self.assertIn("# My profiles", path.read_text())
        self.assertTrue(self.disconnect("codex"))
        self.assertIn("# My profiles\n[profiles.fast]", path.read_text())

    def test_the_snippet_is_valid_json_and_names_only_angelo(self):
        data = json.loads(external_setup.snippet("other", PYTHON, self.state, "mcp-app"))
        self.assertEqual(list(data["mcpServers"]), ["angelo"])
        self.assertIn("mcp-app", data["mcpServers"]["angelo"]["args"])


if __name__ == "__main__":
    unittest.main()
