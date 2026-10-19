# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved API keys in the AI Providers flow, with a fake UI owner and key store.

The real operators are exercised in Blender by ``test_blender_saved_keys.py``;
here the glue and the operator source contracts run on any host OS.
"""

import ast
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core"
sys.path[:0] = [str(ADDON), str(HERE)]
from angelo_agent import saved_keys_ui
from angelo_agent.api_connections import clean_key
from angelo_agent.connections import ConnectionStore
from angelo_agent.key_store import MEMORY_ONLY, KeyStoreError
from angelo_agent.provider_registry import CODING, GENAI
from angelo_agent.saved_keys import account_name, profile_id
from test_native_saved_keys import KEY, OTHER, FakeBackend, ProbingBackend

UI_SOURCE = (ADDON / "angelo_agent/ui.py").read_text()
NEW_MODULES = [ADDON / "angelo_agent" / name for name in (
    "key_store.py", "keychain_macos.py", "credential_manager_windows.py", "secret_service_linux.py",
    "saved_keys.py", "saved_keys_ui.py")]


class FakeUI:
    """The attributes saved_keys_ui uses from ui.py."""

    def __init__(self, directory):
        self.store = ConnectionStore(directory)
        self._memory_keys, self.checks, self.replaced = {}, [], []
        self._api_connections = self

    def connection_directory(self):
        return str(self.store.directory)

    def connections(self):
        return self.store

    def replace_connection_key(self, record, secret):
        self._memory_keys[record["id"]] = clean_key(secret)
        self.replaced.append(record["id"])

    def start(self, record, api_key):  # ApiConnections.start
        self.checks.append((record["id"], api_key))


def settle(ui, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        saved_keys_ui.pump(ui)
        if not saved_keys_ui._vault._pending:
            return
        time.sleep(0.005)
    raise AssertionError("Keychain work did not finish")



class SavedKeysUiTests(unittest.TestCase):
    """Most cases assert macOS labels and Keychain status codes, so they pin ``platform="darwin"`` to pass on any host."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(saved_keys_ui.close)
        self.ui = FakeUI(Path(self.temp.name) / "accounts")
        self.backend = FakeBackend()
        store = self.ui.store
        self.claude = store.add("ANTHROPIC", "API_KEY", "Claude key")
        self.grok = store.add("XAI", "API_KEY", "Grok")
        self.images = store.add("GOOGLE", "API_KEY", "Gemini", group=GENAI)
        self.codex = store.add("OPENAI", "API_KEY", "OpenAI API")
        self.subscription = store.add("ANTHROPIC", "SUBSCRIPTION", "Claude", group=CODING)
        self.profile = profile_id(store.directory)

    def account(self, record):
        return account_name(record["id"], self.profile)

    def test_unavailable_platforms_stay_memory_only(self):
        self.assertFalse(saved_keys_ui.start(self.ui, background=False, environ={}, platform="sunos5"))
        self.assertFalse(saved_keys_ui.available())
        self.assertEqual(saved_keys_ui.unavailable_reason(), MEMORY_ONLY)
        self.assertEqual(saved_keys_ui.storage_label(self.grok, True), MEMORY_ONLY)
        saved_keys_ui.remember(self.grok, KEY)
        saved_keys_ui.forget(self.grok["id"])
        self.assertIs(saved_keys_ui.is_remembered(self.grok["id"]), False)
        self.assertIsNone(saved_keys_ui.missing_key_status(self.grok["id"]))
        with self.assertRaises(RuntimeError):
            saved_keys_ui.load(self.grok)

    def test_startup_loads_only_keys_this_app_holds(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        loaded = {call[1] for call in self.backend.calls}
        self.assertEqual(loaded, {self.account(r) for r in (self.claude, self.grok, self.images)})
        self.assertTrue(all(call[2] is False for call in self.backend.calls), "startup must never prompt")
        self.assertEqual(self.ui.replaced, [])

    def test_restart_restores_keys_through_the_typed_key_path(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        for record in (self.claude, self.grok):
            self.ui._memory_keys[record["id"]] = KEY
            saved_keys_ui.remember(record, KEY)
        settle(self.ui)
        self.assertEqual(saved_keys_ui.storage_label(self.grok, True), "Key saved in Keychain")
        saved_keys_ui.close()
        restarted = FakeUI(self.ui.store.directory)
        saved_keys_ui.start(restarted, background=False, platform="darwin", backend=self.backend)
        self.assertEqual(saved_keys_ui.missing_key_status(self.grok["id"]), "Loading saved key from Keychain…")
        settle(restarted)
        self.assertEqual(restarted._memory_keys, {self.claude["id"]: KEY, self.grok["id"]: KEY})
        self.assertCountEqual(restarted.replaced, [self.claude["id"], self.grok["id"]])
        # HTTP connections get the same non-billable check as a typed key; Claude doesn't have one.
        self.assertEqual(restarted.checks, [(self.grok["id"], KEY)])

    def test_another_profile_never_sees_the_key(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        saved_keys_ui.remember(self.grok, KEY)
        settle(self.ui)
        saved_keys_ui.close()
        other = FakeUI(Path(self.temp.name) / "test-profile")
        other.store.records[self.grok["id"]] = dict(self.grok)  # Recovered metadata keeps the UUID.
        other.store.save()
        saved_keys_ui.start(other, background=False, platform="darwin", backend=self.backend)
        settle(other)
        self.assertEqual(other._memory_keys, {})
        self.assertIs(saved_keys_ui.is_remembered(self.grok["id"]), False)

    def test_a_typed_key_wins_over_a_late_saved_key(self):
        self.backend.items[self.account(self.grok)] = OTHER
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        self.ui._memory_keys[self.grok["id"]] = KEY
        settle(self.ui)
        self.assertEqual(self.ui._memory_keys[self.grok["id"]], KEY)
        self.assertEqual(self.ui.replaced, [])

    def test_approval_needed_offers_an_explicit_prompting_load(self):
        self.backend.errors[self.account(self.claude)] = -25308
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        self.assertIn("Keychain approval needed", saved_keys_ui.missing_key_status(self.claude["id"]))
        self.assertTrue(saved_keys_ui.can_load(self.claude, False))
        self.assertFalse(saved_keys_ui.can_load(self.claude, True))
        self.assertFalse(saved_keys_ui.can_load(self.grok, False), "absent keys have nothing to load")
        self.backend.errors[self.account(self.claude)] = -25293
        saved_keys_ui.load(self.claude, interactive=True)
        settle(self.ui)
        self.assertTrue(self.backend.calls[-1][2])
        self.assertEqual(saved_keys_ui.missing_key_status(self.claude["id"]), "Keychain access denied · re-enter the key")

    def test_invalid_saved_value_is_rejected_like_a_typed_one(self):
        self.backend.items[self.account(self.grok)] = "not a key"
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        self.assertEqual(self.ui._memory_keys, {})
        self.assertIn("not a valid API key", saved_keys_ui.missing_key_status(self.grok["id"]))
        self.assertNotIn("not a key", saved_keys_ui.missing_key_status(self.grok["id"]))

    def test_failed_save_keeps_the_session_key_and_says_so(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        self.backend.write = lambda *args, **kwargs: (_ for _ in ()).throw(type("E", (Exception,), {"kind": "approval"})())
        self.ui._memory_keys[self.grok["id"]] = KEY
        saved_keys_ui.remember(self.grok, KEY)
        settle(self.ui)
        label = saved_keys_ui.storage_label(self.grok, True)
        self.assertIn("Not saved in Keychain", label)
        self.assertIn("kept in memory", label)

    def test_removal_failure_for_a_removed_connection_becomes_a_notice(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        self.backend.delete = lambda *args, **kwargs: (_ for _ in ()).throw(type("E", (Exception,), {"kind": "unavailable"})())
        saved_keys_ui.forget(self.grok["id"])
        self.ui.store.remove(self.grok["id"])
        settle(self.ui)
        notice = saved_keys_ui.notices()[0]
        self.assertIn(self.grok["id"], notice)
        self.assertIn("Angelo API key", notice)

    def test_rebuilt_app_save_and_removal_are_reported_honestly(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        self.backend.foreign.add(self.account(self.grok))
        self.ui._memory_keys[self.grok["id"]] = KEY
        saved_keys_ui.remember(self.grok, KEY)
        settle(self.ui)
        self.assertIn("needs approval to read it", saved_keys_ui.storage_label(self.grok, True))
        self.assertTrue(saved_keys_ui.can_load(self.grok, True), "approve this build for its own save")
        saved_keys_ui.forget(self.grok["id"])
        settle(self.ui)
        self.assertIn("Saved key erased", saved_keys_ui.storage_label(self.grok, True))
        self.backend.foreign.add(self.account(self.claude))
        self.backend.items[self.account(self.claude)] = KEY
        saved_keys_ui.forget(self.claude["id"])
        self.ui.store.remove(self.claude["id"])
        settle(self.ui)
        self.assertIn("was erased", saved_keys_ui.notices()[-1])
        self.assertEqual(self.backend.items[self.account(self.claude)], "")

    def test_linux_without_a_session_bus_stays_memory_only_and_says_why(self):
        self.assertFalse(saved_keys_ui.start(self.ui, background=False, environ={}, platform="linux"))
        reason = saved_keys_ui.unavailable_reason()
        self.assertIn("desktop session bus (D-Bus)", reason)
        self.assertTrue(reason.endswith("keys stay in memory."))
        self.assertEqual(saved_keys_ui.storage_label(self.grok, True), reason)
        self.assertIs(saved_keys_ui.is_remembered(self.grok["id"]), False)

    def test_disable_switch_turns_off_every_platform(self):
        for platform, title in (("darwin", "Keychain"), ("win32", "Credential Manager"), ("linux", "Keyring")):
            with self.subTest(platform=platform):
                environ = {"ANGELO_DISABLE_KEYCHAIN": "1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}
                self.assertFalse(saved_keys_ui.start(self.ui, background=False, environ=environ, platform=platform))
                self.assertEqual(saved_keys_ui.unavailable_reason(), "%s is turned off for this session "
                                 "(ANGELO_DISABLE_KEYCHAIN); keys stay in memory." % title)

    def test_store_wide_probe_failure_switches_the_session_to_memory_only(self):
        backend = ProbingBackend(KeyStoreError("unavailable", "No keyring service (Secret Service) is running "
                                                              "in this session", "dbus 2"))
        self.assertTrue(saved_keys_ui.start(self.ui, background=False, platform="linux", backend=backend))
        end = time.monotonic() + 5
        while saved_keys_ui.available() and time.monotonic() < end:
            saved_keys_ui.pump(self.ui)
            time.sleep(0.005)
        self.assertFalse(saved_keys_ui.available())
        self.assertEqual(saved_keys_ui.unavailable_reason(),
                         "No keyring service (Secret Service) is running in this session; keys stay in memory.")
        self.assertEqual(backend.calls, [], "the store was never read after its probe failed")
        self.assertIsNone(saved_keys_ui.missing_key_status(self.grok["id"]))
        saved_keys_ui.remember(self.grok, KEY)  # A no-op now, like any memory-only platform.
        self.assertEqual(backend.items, {})

    def test_labels_name_this_platforms_store(self):
        expected = {
            "linux": ("Loading saved key from your keyring…", "Key saved in your keyring",
                      "Keyring locked · choose Load Saved Key to unlock it, or re-enter the key",
                      "Not saved in your keyring (keyring locked or approval needed) · "
                      "key kept in memory for this session"),
            "win32": ("Loading saved key from Credential Manager…", "Key saved in Credential Manager",
                      "Credential Manager approval needed · choose Load Saved Key, or re-enter the key",
                      "Not saved in Credential Manager (credential manager locked or approval needed) · "
                      "key kept in memory for this session"),
            "darwin": ("Loading saved key from Keychain…", "Key saved in Keychain",
                       "Keychain approval needed · choose Load Saved Key, or re-enter the key",
                       "Not saved in Keychain (keychain locked or approval needed) · "
                       "key kept in memory for this session"),
        }
        for platform, (loading, saved, approval, not_saved) in expected.items():
            with self.subTest(platform=platform):
                backend = FakeBackend()
                saved_keys_ui.start(self.ui, background=False, platform=platform, backend=backend)
                self.assertEqual(saved_keys_ui.missing_key_status(self.grok["id"]), loading)
                settle(self.ui)
                saved_keys_ui.remember(self.grok, KEY)
                settle(self.ui)
                self.assertEqual(saved_keys_ui.storage_label(self.grok, True), saved)
                backend.errors[self.account(self.claude)] = -25308
                saved_keys_ui.load(self.claude)
                settle(self.ui)
                self.assertEqual(saved_keys_ui.missing_key_status(self.claude["id"]), approval)
                backend.write = lambda *args, **kwargs: (_ for _ in ()).throw(KeyStoreError("approval", "locked"))
                saved_keys_ui.remember(self.images, KEY)
                settle(self.ui)
                self.assertEqual(saved_keys_ui.storage_label(self.images, True), not_saved)
                saved_keys_ui.close()

    def test_a_key_too_long_for_the_store_stays_in_memory_and_says_why(self):
        backend = FakeBackend()
        backend.write = lambda *args, **kwargs: (_ for _ in ()).throw(KeyStoreError("too_long", "too long"))
        saved_keys_ui.start(self.ui, background=False, platform="win32", backend=backend)
        settle(self.ui)
        saved_keys_ui.remember(self.grok, KEY)
        settle(self.ui)
        self.assertEqual(saved_keys_ui.storage_label(self.grok, True),
                         "Not saved in Credential Manager (key too long) · key kept in memory for this session")

    def test_memory_only_choice_is_labelled(self):
        saved_keys_ui.start(self.ui, background=False, platform="darwin", backend=self.backend)
        settle(self.ui)
        saved_keys_ui.mark_memory_only(self.grok["id"])
        self.assertEqual(saved_keys_ui.storage_label(self.grok, True), "Key in memory only · enter it again after restarting")
        self.assertIsNone(saved_keys_ui.storage_label(self.codex, True), "Codex keeps its own keyring")
        self.assertIsNone(saved_keys_ui.storage_label(self.subscription, False))


def _class(name):
    return next(node for node in ast.parse(UI_SOURCE).body if isinstance(node, ast.ClassDef) and node.name == name)


def _calls(node):
    return {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}


class OperatorSourceTests(unittest.TestCase):
    def test_form_offers_remember_in_the_os_store_on_by_default(self):
        form = ast.unparse(_class("ANGELO_OT_new_connection"))
        self.assertIn("remember_key: BoolProperty(name=saved_keys_ui.TEXT.remember, default=True, "
                      "options={'SKIP_SAVE'}", form)
        self.assertIn("saved_keys_ui.store_text().location", form)
        self.assertIn("remember_saved_key(record, self.remember_key, bool(secret), bool(self.connection_id))", form)
        self.assertIn("saved_keys_ui.unavailable_reason()", form)

    def test_removal_deletes_the_saved_key(self):
        self.assertIn("saved_keys_ui.forget", _calls(_class("ANGELO_OT_connection_remove")))

    def test_only_the_explicit_load_operator_may_prompt(self):
        prompting = [node for node in ast.walk(ast.parse(UI_SOURCE)) if isinstance(node, ast.keyword)
                     and node.arg == "interactive" and getattr(node.value, "value", None) is True]
        self.assertEqual(len(prompting), 1)
        self.assertIn("interactive=True", ast.unparse(_class("ANGELO_OT_connection_load_key")))

    def test_keychain_code_never_shells_out_logs_or_prints(self):
        for path in NEW_MODULES:
            tree = ast.parse(path.read_text())
            imported = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                        for alias in node.names} | {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            with self.subTest(module=path.name):
                self.assertFalse(imported & {"subprocess", "logging", "os.system"})
                self.assertNotIn("print", _calls(tree))
                self.assertNotIn("add-generic-password", path.read_text())


if __name__ == "__main__":
    unittest.main()
