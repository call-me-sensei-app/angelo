# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread glue of the team account (team_account_ui.py) and the team modules' hygiene.

The glue is imported with a minimal stand-in for ``bpy``. It must mirror a
current grant key into ``ui._memory_keys`` for usable team coding connections
only, renew only when chats are idle (the account's policy receives ``idle``),
withdraw keys and stop idle runtimes on sign-out, and keep team connections out
of the stored connections. The hygiene tests pin the size, license, bpy-free
core and the single loopback listener.
"""

import ast
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import team_connections  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402

CORE = ("team_account.py", "team_account_keys.py", "team_account_reads.py", "team_connections.py",
        "team_endpoints.py", "team_errors.py", "team_grants.py", "team_http.py", "team_lock.py",
        "team_org_policy.py", "team_page_art.py", "team_pages.py", "team_reads.py", "team_runtime.py",
        "team_signin.py", "team_token_store.py", "team_view.py", "managed_policy.py", "display_text.py",
        "connection_access.py", "team_usage_buffer.py", "team_usage_reports.py")
GRANT_KEY = "agk_" + "g" * 43


def import_glue():
    bpy = ModuleType("bpy")
    bpy.types = ModuleType("bpy.types")
    bpy.types.Operator = bpy.types.Panel = object
    bpy.props = ModuleType("bpy.props")
    bpy.props.EnumProperty = bpy.props.StringProperty = lambda **_: None
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    with patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}):
        sys.modules.pop("angelo_agent.team_account_ui", None)
        sys.modules.pop("angelo_agent.team_chat", None)
        sys.modules.pop("angelo_agent.preferences_ui", None)
        from angelo_agent import team_account_ui
    return team_account_ui


class FakeAccount:
    console_url = "https://console.angelo.studio"

    def __init__(self):
        self.state = AccountView(status="signed_in", org_id="org-1", org_name="Northwind Studio", user_id="user-1",
                                 seat=True, scopes=frozenset({"api", "generate"}), providers=("anthropic", "openai"),
                                 version=1)
        self.key, self.idle = GRANT_KEY, []

    def view(self):
        return self.state

    def grant_key(self):
        return self.key

    def refused_grant_key(self, key):
        self.refused = key

    def tick(self, idle):
        self.idle.append(idle)

    def refresh_providers(self, older_than=0.0):
        self.rechecks = getattr(self, "rechecks", 0) + 1
        return True

    def close(self):
        pass


class Controller:
    def __init__(self, connection_id, busy=False):
        self.state, self.busy, self.version, self.connection_error = {"connection_id": connection_id}, busy, 0, False
        self.provider, self.api_key, self.closed = SimpleNamespace(close=self.close_provider), GRANT_KEY, False

    def close_provider(self):
        self.closed = True


class GlueTests(unittest.TestCase):
    def setUp(self):
        self.glue = import_glue()
        self.account = FakeAccount()
        self.glue.close()
        self.glue._account = self.account
        self.addCleanup(self.glue.close)
        self.invalidated = []
        self.ui = SimpleNamespace(_controllers={}, _memory_keys={"personal": "kept"},
                                  catalogs=lambda: SimpleNamespace(invalidate=self.invalidated.append))
        patcher = patch.object(self.glue.preferences_ui, "redraw", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ids(self):
        return {r["mode"]: r["id"] for r in self.glue.records().values()}

    def test_grant_key_reaches_only_usable_team_coding_connections(self):
        self.assertTrue(self.glue.pump(self.ui))
        ids = self.ids()
        self.assertEqual(set(ids), {"claude_team", "codex_team", "openai_images_team"})
        self.assertEqual(self.ui._memory_keys[ids["claude_team"]], GRANT_KEY)
        self.assertEqual(self.ui._memory_keys[ids["codex_team"]], GRANT_KEY)
        self.assertNotIn(ids["openai_images_team"], self.ui._memory_keys, "Studio uses the access token")
        self.assertEqual(self.ui._memory_keys["personal"], "kept")
        self.assertFalse(self.glue.pump(self.ui), "nothing changed")
        self.assertEqual(self.account.idle, [True, True])
        self.assertTrue(self.glue.ready(next(r for r in self.glue.records().values() if r["mode"] == "claude_team")))
        self.assertIn(ids["openai_images_team"], self.glue.health_results())

    def test_renewal_is_offered_only_when_no_chat_is_running(self):
        self.ui._controllers = {"a": Controller(None, busy=True)}
        self.glue.pump(self.ui)
        self.assertEqual(self.account.idle, [False])

    def test_a_key_the_gateway_refused_mid_chat_is_replaced_once(self):
        self.glue.pump(self.ui)
        chat = Controller(self.ids()["claude_team"])
        chat.connection_error = True
        self.ui._controllers = {"a": chat}
        self.account.refused = None
        self.glue.pump(self.ui)
        self.assertEqual(self.account.refused, GRANT_KEY)
        self.account.refused, self.account.key = None, "agk_" + "n" * 43
        self.glue.pump(self.ui)
        self.assertIsNone(self.account.refused, "the chat still holds the old key until its next request: no loop")

    def test_the_first_run_card_offers_the_team_until_a_team_chat_can_start(self):
        self.glue.pump(self.ui)
        self.assertIsNone(self.glue.welcome_row(), "usable team coding connections: nothing to set up")
        self.account.state = replace(self.account.state, seat=False, version=2)
        self.glue.pump(self.ui)
        # The reason, for a member: contact the admin (team_access has every case).
        self.assertEqual(self.glue.welcome_row(), {"label": "No seat in Northwind Studio", "note": "Contact your admin",
                                                   "action": "welcome_team", "value": ""})
        self.account.state = AccountView(version=3)
        self.glue.pump(self.ui)
        self.assertEqual(self.glue.welcome_row()["note"], "Sign in with your organization account")
        self.account.state = AccountView(message="This computer was signed out of your Angelo team.", version=4)
        self.assertEqual(self.glue.welcome_row()["note"], "Signed out of your team · sign in again")
        for status, note in (("signing_in", "Finish signing in in your browser"),
                             ("restoring", "Restoring your team sign-in…"),
                             ("offline", "Couldn't reach Angelo · retrying every minute")):
            self.account.state = AccountView(status=status, version=5)
            self.assertEqual(self.glue.welcome_row()["note"], note)

    def test_a_team_only_policy_leaves_the_team_row_alone_on_the_card(self):
        from angelo_agent import managed_policy
        rows = [{"id": "team"}, {"id": "claude_subscription"}, {"id": "api_key"}]
        managed_policy.reset(managed_policy.Policy())
        self.addCleanup(managed_policy.reset)
        self.assertEqual(self.glue.team_only_options(rows), rows)
        managed_policy.reset(managed_policy.Policy(allow_personal_connections=False))
        self.assertEqual(self.glue.team_only_options(rows), [{"id": "team"}])

    def test_a_renewed_key_replaces_the_mirrored_one(self):
        self.glue.pump(self.ui)
        self.account.key = "agk_" + "h" * 43
        self.assertTrue(self.glue.pump(self.ui))
        self.assertEqual(self.ui._memory_keys[self.ids()["claude_team"]], self.account.key)

    def test_sign_out_withdraws_keys_and_stops_idle_team_runtimes(self):
        self.glue.pump(self.ui)
        ids = self.ids()
        idle, busy, personal = Controller(ids["claude_team"]), Controller(ids["codex_team"], busy=True), Controller("personal")
        self.ui._controllers = {"idle": idle, "busy": busy, "personal": personal}
        self.account.state = AccountView(status="signed_out", version=2)
        self.account.key = ""
        self.assertTrue(self.glue.pump(self.ui))
        self.assertEqual(self.glue.records(), {})
        self.assertNotIn(ids["claude_team"], self.ui._memory_keys)
        self.assertEqual(self.ui._memory_keys, {"personal": "kept"})
        self.assertIsNone(idle.provider)
        self.assertEqual(idle.api_key, "")
        self.assertIsNotNone(busy.provider, "a running turn finishes; the gateway refuses the key once revoked")
        self.assertIsNotNone(personal.provider)
        self.assertEqual(set(self.invalidated), set(ids.values()))

    def test_an_organization_policy_turning_personal_connections_off_ends_what_runs_on_them(self):
        from angelo_agent import codex_runtime, managed_policy, runtime_bindings
        managed_policy.reset(managed_policy.Policy())
        self.addCleanup(managed_policy.reset)
        managed_policy.set_organization_source(lambda: self.account.state.org_policy)
        ended, retired = [], []
        self.ui.connections = lambda: SimpleNamespace(records={"personal": {"mode": "codex_api"}})
        with patch.object(runtime_bindings, "end_personal", lambda: ended.append(True) or 1), \
                patch.object(codex_runtime, "retire_connection", retired.append):
            self.glue.pump(self.ui)
            self.assertEqual((ended, retired), ([], []))   # Allowed: nothing ends.
            self.account.state = replace(self.account.state, org_policy={"allowPersonalConnections": False}, version=2)
            self.assertTrue(self.glue.pump(self.ui))
        self.assertEqual((ended, retired), ([True], ["personal"]))   # At once, as a removal: workers' and window's.

    def test_losing_the_seat_keeps_the_rows_but_withdraws_the_key(self):
        self.glue.pump(self.ui)
        ids = self.ids()
        self.account.state = AccountView(**{**self.account.state.__dict__, "seat": False, "version": 3})
        self.glue.pump(self.ui)
        self.assertEqual(self.ids(), ids)
        self.assertNotIn(ids["claude_team"], self.ui._memory_keys)
        record = self.glue.find(ids["claude_team"])
        self.assertIn("don't have a seat", self.glue.status(record))
        with self.assertRaisesRegex(ValueError, "don.t have a seat"):
            self.glue.binding(self.glue.find(ids["openai_images_team"]))

    def test_an_invalid_origin_disables_the_account_with_its_reason(self):
        ui = SimpleNamespace(connection_directory=lambda: tempfile.gettempdir())
        self.assertFalse(self.glue.start(ui, background=True, environ={"ANGELO_CONSOLE_URL": "http://console.test"}))
        self.assertIsNone(self.glue.account())
        self.assertIn("ANGELO_CONSOLE_URL", self.glue.reason({"mode": "claude_team"}))

    def test_background_mode_never_restores(self):
        with tempfile.TemporaryDirectory() as folder:
            ui = SimpleNamespace(connection_directory=lambda: folder)
            self.assertTrue(self.glue.start(ui, background=True, environ={}, platform="darwin"))
            self.assertEqual(self.glue.account().view().status, "signed_out")
            self.assertEqual(self.glue.account().view().stored, "session")


class UiIntegrationTests(unittest.TestCase):
    """ui.py routes team records through team_account_ui (compiled one definition at a time)."""

    TREE = ast.parse((ADDON / "ui.py").read_text())

    def definition(self, name, namespace):
        node = next(n for n in self.TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
        namespace = dict(namespace)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "ui.py", "exec"), namespace)
        return namespace[name]

    def test_team_readiness_needs_the_glue_and_for_coding_a_mirrored_key(self):
        from angelo_agent.provider_registry import HTTP_MODES, is_coding, is_team
        record = {"id": "t", "mode": "claude_team", "method": "TEAM"}
        glue = SimpleNamespace(ready=lambda r: True)
        namespace = {"is_team": is_team, "is_coding": is_coding, "team_account_ui": glue, "HTTP_MODES": HTTP_MODES,
                     "_memory_keys": {}}
        ready = self.definition("connection_ready", namespace)
        self.assertFalse(ready(record))
        namespace["_memory_keys"]["t"] = GRANT_KEY
        self.assertTrue(self.definition("connection_ready", namespace)(record))
        images = {"id": "i", "mode": "openai_images_team", "method": "TEAM"}
        self.assertTrue(self.definition("connection_ready", namespace)(images))

    def test_team_generation_uses_the_team_binding(self):
        from angelo_agent import connection_access
        record = {"id": "t", "mode": "tripo_team"}
        stored = SimpleNamespace(connections=lambda: SimpleNamespace(records={}))
        team = SimpleNamespace(find=lambda identity: record, binding=lambda r: ("team binding", r["id"]),
                               signed_in=lambda: True)
        with patch.object(connection_access, "_ui", lambda: stored), patch.object(connection_access, "_team", lambda: team):
            self.assertEqual(connection_access.generation_binding("t"), ("team binding", "t"))

    def test_the_preference_enum_never_receives_a_team_mode(self):
        from angelo_agent import connection_access
        prefs = SimpleNamespace(provider="codex_subscription")
        ui = SimpleNamespace(preferences=lambda: prefs, PROVIDERS=[("codex_subscription",), ("claude_api",)])
        with patch.object(connection_access, "_ui", lambda: ui):
            connection_access.remember_preferred_mode("claude_team")
            self.assertEqual(prefs.provider, "codex_subscription")
            connection_access.remember_preferred_mode("claude_api")
        self.assertEqual(prefs.provider, "claude_api")


class HygieneTests(unittest.TestCase):
    def test_new_modules_are_small_licensed_and_the_core_is_bpy_free(self):
        for name in CORE + ("team_account_ui.py", "team_chat.py", "team_status_bar.py"):
            source = (ADDON / name).read_text()
            with self.subTest(module=name):
                self.assertLessEqual(len(source.splitlines()), 500)
                self.assertIn("SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.", source)
                self.assertIn("SPDX-License-Identifier: GPL-2.0-or-later", source)
                tree = ast.parse(source)
                imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                            for alias in node.names} | {node.module for node in ast.walk(tree)
                                                        if isinstance(node, ast.ImportFrom) and node.module}
                self.assertFalse(imported & {"subprocess", "logging"})
                calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Name)}
                self.assertNotIn("print", calls, "tokens and codes must never reach a log or console")
                if name in CORE:
                    self.assertNotIn("bpy", imported, "Main-thread callers pass Blender data in")

    def test_the_sign_in_callback_is_the_only_network_listener_and_binds_loopback(self):
        listeners = []
        for path in sorted(ADDON.glob("*.py")):
            source = path.read_text()
            # external_cli.py listens on a Unix-domain socket only (its own test forbids AF_INET there).
            if "AF_INET" in source or "TCPServer" in source or "HTTPServer" in source:
                listeners.append(path.name)
        # The sign-in callback, the live web preview's page server (doc/angelo/web_export.rst) and the Godot live
        # link's authenticated server (doc/angelo/godot_link.rst).
        self.assertEqual(listeners, ["engine_link_server.py", "team_signin.py", "web_preview_server.py"])
        source = (ADDON / "team_signin.py").read_text()
        self.assertIn('super().__init__(("127.0.0.1", 0), _Handler)', source)
        self.assertNotIn("0.0.0.0", source)
        self.assertNotIn('"localhost"', source)
        source = (ADDON / "web_preview_server.py").read_text()
        self.assertIn('super().__init__(("127.0.0.1", 0), _Handler)', source)
        self.assertNotIn("0.0.0.0", source)
        source = (ADDON / "engine_link_server.py").read_text()
        self.assertIn('self._listener.bind(("127.0.0.1", 0))', source)
        self.assertNotIn("0.0.0.0", source)

    def test_no_team_connection_can_be_created_through_the_store(self):
        source = (ADDON / "connections.py").read_text()
        self.assertIn('if method == TEAM:\n            raise ValueError', source)
        # A stored TEAM record is skipped on read, wherever the parsing lives (connections._parsed since 1.0.6).
        self.assertRegex(source, r'if record\["method"\] == TEAM:\n\s+continue')


if __name__ == "__main__":
    unittest.main()
