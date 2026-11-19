# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Copy setup gives a snippet that works (external_mcp.rst, "Connecting"), run through its operator.

Found by the showcase pilot on 06259fa: with neither Claude Code nor Codex installed, the card offered only Copy
setup, which copied a launcher command for a client Angelo never registered, so the launcher answered "This app is not
connected to Angelo" with no tools. The person's click on Copy setup now connects what it copies, as Connect does
(``external_copy``); only that click grants (``grant_guard``; test_native_grant_operators). The Copy setup break-it
review (2026-11-14) adds:

* S1: connected means a record and a readable key, and Copy setup repairs one whose key went missing;
  for another MCP app too, whose row stays (the second review, S5);
* S2: one client per copied app (the apps Angelo knows have theirs; each other MCP app gets its own);
* S3: an app copied before it was installed offers Connect again;
* S4: at most one button per action on a row;
* N1: Windows refuses Copy setup as it refuses Connect;
* N2: Disconnect says whether it removed anything from the app;
* N4: the snippet in the form each app's settings take;
* N6: a connection that can't be saved leaves nothing behind.

The operator and the card run against a stand-in for Blender (``blender_stand_in``); the launcher is real.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from blender_stand_in import StandIn  # noqa: E402

MODULES = ("ui", "external_ui", "external_clients", "external_mcp", "external_setup", "providers_page_external",
           "managed_policy")


def client_of(entry):
    args = entry["args"]
    return args[args.index("--client") + 1]


class Recorder:
    def __init__(self):
        self.reports = []

    def __call__(self, kind, message):
        self.reports.append((set(kind), message))


class CopyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stand_in = StandIn(MODULES)
        cls.addClassCleanup(cls.stand_in.__exit__, None, None, None)   # Even when entering it fails part way.
        cls.m = cls.stand_in.__enter__()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "state"
        self.prefs = SimpleNamespace(allow_local_cli=False)
        policy = self.m["managed_policy"]
        policy.reset(policy.Policy())
        self.addCleanup(policy.reset)
        self.background = []
        for target, name, value in ((self.m["external_ui"], "_state", lambda: self.state),
                                    (self.m["ui"], "preferences", lambda: self.prefs),
                                    (self.m["external_ui"], "_in_background",
                                     lambda *a, **k: self.background.append((a, k))),
                                    (self.m["external_ui"], "_redraw", lambda: None),
                                    (sys, "platform", "darwin")):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def registry(self):
        return self.m["external_clients"].AppRegistry(self.state)

    def run_operator(self, name, **properties):
        """The operator's own body, as the person's click runs it (past grant_guard, which has its own test)."""
        cls = getattr(self.m["external_ui"], name)
        op = cls()
        op.report, op.client = Recorder(), ""
        for key, value in properties.items():
            setattr(op, key, value)
        context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=""),
                                  preferences=SimpleNamespace(is_dirty=False))
        body = getattr(cls, "_granted_execute", None) or cls.execute
        return body(op, context), context, op

    def copy(self, app, client=""):
        result, context, op = self.run_operator("ANGELO_OT_external_app_copy_setup", app=app, client=client)
        self.assertEqual(result, {'FINISHED'}, op.report.reports)
        return context.window_manager.clipboard

    def rows(self, installed=False, has_entry=False):
        page, setup = self.m["providers_page_external"], self.m["external_setup"]
        with patch.object(setup, "installed", lambda app, *a, **k: installed or app == "other"), \
                patch.object(setup, "has_entry", lambda app, *a, **k: has_entry):
            return page._app_rows(self.state, False)

    def served(self, client):
        server = self.m["external_mcp"].Server(client, str(self.state))
        return (server.answer("initialize", {})["instructions"] != self.m["external_mcp"].NOT_CONNECTED
                and bool(server.answer("tools/list", {})["tools"]))


class ReproductionTests(CopyCase):
    def test_the_copied_snippet_names_a_client_the_launcher_serves(self):
        """Fails on 06259fa: the snippet named a client Angelo never registered."""
        for app in ("claude_code", "claude_desktop", "cursor", "other"):
            with self.subTest(app=app):
                entry = json.loads(self.copy(app))["mcpServers"]["angelo"]
                self.assertTrue(self.served(client_of(entry)))
                self.assertTrue(self.prefs.allow_local_cli, "access turned on, as Connect does")


class SnippetTests(CopyCase):
    def test_each_app_gets_the_form_its_settings_take_and_never_a_key(self):
        codex = tomllib.loads(self.copy("codex"))["mcp_servers"]["angelo"]
        vscode = json.loads(self.copy("vscode"))["servers"]["angelo"]
        cursor = json.loads(self.copy("cursor"))["mcpServers"]["angelo"]
        self.assertEqual(vscode["type"], "stdio")
        for entry in (codex, vscode, cursor):
            key = self.registry().key(client_of(entry))
            self.assertIsNotNone(key)
            self.assertTrue({"command", "args"} <= set(entry))
            self.assertFalse({k for k in entry if "key" in k.lower() or "token" in k.lower()})
            for form in (key.hex(), key.hex().upper(), __import__("base64").b64encode(key).decode()):
                self.assertFalse(any(form in str(value) for value in [entry["command"], *entry["args"]]))


class ClientTests(CopyCase):
    def test_each_other_mcp_app_is_its_own_client_and_revoked_alone(self):
        first = client_of(json.loads(self.copy("other"))["mcpServers"]["angelo"])
        second = client_of(json.loads(self.copy("other"))["mcpServers"]["angelo"])
        self.assertEqual((first, second), ("mcp-app", "mcp-app-2"))
        self.assertNotEqual(self.registry().key(first), self.registry().key(second))
        again = client_of(json.loads(self.copy("other", client=second))["mcpServers"]["angelo"])
        self.assertEqual(again, second, "copying again for that app keeps its client")
        self.run_operator("ANGELO_OT_external_app_disconnect", app="other", client=second)
        self.assertEqual((self.served(first), self.served(second)), (True, False))

    def test_copying_again_keeps_the_key(self):
        self.copy("cursor")
        key = self.registry().key("cursor")
        self.copy("cursor")
        self.assertEqual(self.registry().key("cursor"), key)

    def test_a_record_whose_key_went_missing_is_not_connected_and_copy_setup_repairs_it(self):
        """S1: the card read Connected and the launcher answered not connected."""
        self.copy("cursor")
        (self.state / "mcp" / "keys" / "cursor").unlink()
        self.assertNotIn("cursor", self.registry().snapshot())
        rows = self.rows()
        self.assertNotIn("chip", next(row for row in rows if row["name"] == "Cursor"))
        self.copy("cursor")
        self.assertTrue(self.served("cursor"))

    def test_a_generic_app_whose_key_went_missing_keeps_its_row_and_is_repaired_or_removed(self):
        """r2 S5: its row disappeared, the stale record stayed, and its pasted snippet answered not connected."""
        page = self.m["providers_page_external"]
        self.copy("other")
        (self.state / "mcp" / "keys" / "mcp-app").unlink()
        row = next(row for row in self.rows() if row["buttons"] and row["buttons"][0][2] == "other/mcp-app")
        self.assertIn(page.KEY_MISSING, row["lines"])
        self.assertNotIn("chip", row)
        self.assertEqual([button[1] for button in row["buttons"]], ["mcp_copy", "mcp_disconnect"])
        again = client_of(json.loads(self.copy("other", client="mcp-app"))["mcpServers"]["angelo"])
        self.assertEqual(again, "mcp-app", "the same client, repaired")
        self.assertTrue(self.served("mcp-app"))
        (self.state / "mcp" / "keys" / "mcp-app").unlink()
        self.run_operator("ANGELO_OT_external_app_disconnect", app="other", client="mcp-app")
        self.assertEqual(self.registry().load(), {})


class CardTests(CopyCase):
    def test_no_row_has_two_buttons_for_one_action(self):
        """S4: a Connect whose settings write failed showed Copy setup twice."""
        ui_module = self.m["external_ui"]
        self.copy("codex")
        self.copy("other")
        with patch.dict(ui_module.results, {"codex": (False, ui_module.NOT_CHANGED, {"app": "Codex", "reason": "x"}),
                                            "claude_code": (False, ui_module.NOT_CHANGED,
                                                            {"app": "Claude Code", "reason": "x"})}):
            for installed in (False, True):
                for row in self.rows(installed=installed):
                    actions = [button[1] for button in row["buttons"]]
                    self.assertEqual(len(actions), len(set(actions)), (row["name"], actions))

    def test_an_app_copied_before_it_was_installed_offers_connect_again(self):
        """S3: once Codex is installed without Angelo's entry, Connect writes it."""
        self.copy("codex")
        codex = next(row for row in self.rows(installed=True, has_entry=False) if row["name"] == "Codex")
        self.assertIn("mcp_connect", [button[1] for button in codex["buttons"]])
        codex = next(row for row in self.rows(installed=True, has_entry=True) if row["name"] == "Codex")
        self.assertNotIn("mcp_connect", [button[1] for button in codex["buttons"]])

    def test_every_copied_mcp_app_has_its_own_row_and_another_can_be_added(self):
        self.copy("other")
        self.copy("other")
        names = [row["name"] for row in self.rows()]
        self.assertIn("MCP app 2", names)
        self.assertEqual(names.count("MCP app"), 2, "the first one's row, and the row that adds another")
        for name in ("Claude Desktop", "Cursor", "VS Code"):
            self.assertIn(name, names)


class RefusalTests(CopyCase):
    def test_disconnect_says_whether_it_removed_anything_from_the_app(self):
        """N2: "Removed Angelo from MCP app." when nothing of the app's was touched."""
        ui_module = self.m["external_ui"]
        self.copy("other")
        self.run_operator("ANGELO_OT_external_app_disconnect", app="other", client="mcp-app")
        (_app, _work, done), _kw = self.background[-1]
        self.assertEqual((done(True), done(False)), (ui_module.REMOVED, ui_module.DISCONNECTED_ONLY))
        self.assertFalse(self.served("mcp-app"))

    def test_windows_refuses_copy_setup_as_it_refuses_connect(self):
        """N1."""
        with patch.object(sys, "platform", "win32"):
            for name, app in (("ANGELO_OT_external_app_copy_setup", "other"),
                              ("ANGELO_OT_external_app_connect", "codex")):
                result, _context, _op = self.run_operator(name, app=app)
                self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(self.registry().load(), {})
        self.assertFalse(self.prefs.allow_local_cli)

    def test_a_connection_that_cant_be_saved_leaves_nothing_behind(self):
        """N6: an OSError used to escape as a traceback and could leave a key without its record."""
        clients = self.m["external_clients"]
        original = clients.AppRegistry._save

        def failing(registry, apps):
            raise OSError("disk full")
        with patch.object(clients.AppRegistry, "_save", failing):
            result, _context, op = self.run_operator("ANGELO_OT_external_app_copy_setup", app="cursor")
        self.assertEqual(result, {'CANCELLED'})
        self.assertIn(self.m["external_ui"].NOT_SAVED, [message for _kind, message in op.report.reports])
        self.assertEqual((self.registry().load(), self.registry().key("cursor")), ({}, None))
        self.assertIsNotNone(original)

    def test_under_a_policy_that_turns_access_off_nothing_is_registered(self):
        policy = self.m["managed_policy"]
        policy.reset(policy.Policy(allow_local_cli_access=False))
        result, _context, _op = self.run_operator("ANGELO_OT_external_app_copy_setup", app="codex")
        self.assertEqual((result, self.registry().load()), ({'CANCELLED'}, {}))


class KeyFileTests(unittest.TestCase):
    """An app's key file is private on every platform: POSIX by owner and mode, Windows (no owner ids or mode bits in
    ``stat``) by its profile folder, with a link at the name refused. VM CI d9b7fed: Windows had no ``os.geteuid``."""

    def setUp(self):
        import os
        from angelo_agent import external_cli_protocol
        self.os, self.protocol = os, external_cli_protocol
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.key = self.folder / "cursor"
        self.key.write_text("ab" * 32)
        os.chmod(self.key, 0o600)

    def without_owner_ids(self):
        """``os`` as Windows has it: no ``geteuid``."""
        import contextlib
        saved = getattr(self.os, "geteuid", None)

        @contextlib.contextmanager
        def gone():
            if saved is not None:
                del self.os.geteuid
            try:
                yield
            finally:
                if saved is not None:
                    self.os.geteuid = saved
        return gone()

    def test_a_key_is_read_where_owner_ids_exist_and_where_they_do_not(self):
        self.assertEqual(self.protocol.read_key(self.key), bytes.fromhex("ab" * 32))
        with self.without_owner_ids():
            self.assertEqual(self.protocol.read_key(self.key), bytes.fromhex("ab" * 32))

    @unittest.skipIf(sys.platform == "win32", "[environment] POSIX mode bits (Windows has none)")
    def test_a_key_others_can_read_is_refused_where_modes_exist(self):
        self.os.chmod(self.key, 0o644)
        self.assertIsNone(self.protocol.read_key(self.key))

    @unittest.skipIf(sys.platform == "win32", "[environment] making a link needs a privilege on Windows")
    def test_a_link_at_the_name_is_refused_without_owner_ids(self):
        link = self.folder / "linked"
        link.symlink_to(self.key)
        self.assertIsNone(self.protocol.read_key(link))   # POSIX: O_NOFOLLOW.
        with self.without_owner_ids():
            self.assertFalse(self.protocol._private_file(link, self.os.stat(link)))   # Windows: lstat.


if __name__ == "__main__":
    unittest.main()
