# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.connections import ConnectionStore
from angelo_agent.storage import connection_directory
from angelo_agent.providers import profile_environment, ClaudeProvider
from angelo_agent.authentication import native_auth_command

A = os.path.abspath   # "/tmp/x" as an absolute path of this platform (C:\tmp\x on Windows)


class PersistentConnectionsTests(unittest.TestCase):
    def test_scratch_chat_directories_cannot_change_the_account_store(self):
        first = connection_directory({"ANGELO_STATE_DIR": "/tmp/scratch-a"}, "darwin", "/users/person")
        second = connection_directory({"ANGELO_STATE_DIR": "/tmp/scratch-b"}, "darwin", "/users/person")
        self.assertEqual(first, second)
        self.assertEqual(first, Path("/users/person/Library/Application Support/Angelo/connections"))

    def test_only_explicit_account_override_can_isolate_tests(self):
        self.assertEqual(connection_directory({"ANGELO_CONNECTIONS_DIR": A("/tmp/accounts-test")}), Path(A("/tmp/accounts-test")))
        with self.assertRaises(ValueError):
            connection_directory({"ANGELO_CONNECTIONS_DIR": "relative-path"})
        self.assertEqual(connection_directory({}, "linux", A("/users/person")), Path(A("/users/person/.local/state/Angelo/connections")))
        self.assertEqual(connection_directory({"LOCALAPPDATA": A("/c/Users/p/AppData/Local")}, "win32", A("/c/Users/p")),
                         Path(A("/c/Users/p/AppData/Local/Angelo/connections")))

    def test_deprecated_account_override_still_isolates_and_angelo_wins(self):
        self.assertEqual(connection_directory({"SENSEI_CONNECTIONS_DIR": A("/tmp/legacy-accounts")}), Path(A("/tmp/legacy-accounts")))
        self.assertEqual(connection_directory({"TOONLAB_CONNECTIONS_DIR": A("/tmp/older-accounts")}), Path(A("/tmp/older-accounts")))
        both = {"ANGELO_CONNECTIONS_DIR": A("/tmp/angelo-accounts"), "SENSEI_CONNECTIONS_DIR": A("/tmp/legacy-accounts"),
                "TOONLAB_CONNECTIONS_DIR": A("/tmp/older-accounts")}
        self.assertEqual(connection_directory(both), Path(A("/tmp/angelo-accounts")))
        with self.assertRaisesRegex(ValueError, "SENSEI_CONNECTIONS_DIR must be an absolute path"):
            connection_directory({"SENSEI_CONNECTIONS_DIR": "relative-path"})

    def test_recovery_retains_exact_uuid_and_cli_namespace_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = ConnectionStore(root / "legacy-preview/state")
            account = source.add("ANTHROPIC", "SUBSCRIPTION", "Claude", reuse_native=False)
            profile = source.directory / "claude-profile" / account["id"]
            profile.mkdir(parents=True)
            credential = profile / "credentials-do-not-open.fixture"
            credential.write_text("must never be read or copied")
            destination = ConnectionStore(root / "stable-accounts")
            original_read = Path.read_text
            def checked_read(path, *args, **kwargs):   # Its retained-profile list names folders, never reads them.
                self.assertIn(path.name, {"connections.json", "retained-native-profiles.json",
                                           "retained-native-profiles.last-good.json"})
                return original_read(path, *args, **kwargs)
            with patch.object(Path, "read_text", checked_read):
                recovered = destination.import_metadata(source.directory)
            self.assertEqual(recovered, [account["id"]])
            restored = ConnectionStore(destination.directory).records[account["id"]]
            self.assertEqual(restored["native_profile"], str(profile))
            self.assertTrue(credential.exists())
            self.assertFalse((destination.directory / "claude-profile").exists())
            self.assertEqual(destination.import_metadata(source.directory), [])

    def test_recovery_deduplicates_only_shared_default_cli_scope(self):
        with tempfile.TemporaryDirectory() as root:
            source, target = ConnectionStore(Path(root) / "old"), ConnectionStore(Path(root) / "new")
            source.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
            source.add("ANTHROPIC", "SUBSCRIPTION", "Claude A")
            source.add("ANTHROPIC", "SUBSCRIPTION", "Claude B")
            current = target.add("OPENAI", "SUBSCRIPTION", reuse_native=True)
            self.assertEqual(len(target.import_metadata(source.directory)), 2)
            self.assertIn(current["id"], target.records)
            self.assertEqual(len(target.records), 3)

    def test_native_signin_check_and_runtime_use_the_same_recovered_profile(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConnectionStore(root)
            account = store.add("ANTHROPIC", "SUBSCRIPTION", reuse_native=False)
            old_profile = Path(root) / "legacy/claude-profile" / account["id"]
            settings = {"auth_directory": str(Path(root) / "stable-accounts"), "connection_id": account["id"],
                        "native_profile": str(old_profile), "reuse_codex_login": False}
            with patch("angelo_agent.authentication.find_executable", return_value="/official/claude"):
                _, env = native_auth_command("claude_subscription", settings, str(Path(root) / "scratch-b"))
            runtime = profile_environment({}, "claude_subscription", settings["auth_directory"], False,
                                          account["id"], settings["native_profile"])
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], runtime["CLAUDE_CONFIG_DIR"])
            self.assertEqual(runtime["CLAUDE_CONFIG_DIR"], str(old_profile))

    def test_new_native_profiles_are_under_stable_accounts_not_each_scene(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConnectionStore(Path(root) / "stable-accounts")
            account = store.add("ANTHROPIC", "SUBSCRIPTION", reuse_native=False)
            settings = {"auth_directory": str(store.directory), "connection_id": account["id"], "reuse_codex_login": False}
            with patch("angelo_agent.authentication.find_executable", return_value="/official/claude"):
                _, first = native_auth_command("claude_subscription", settings, str(Path(root) / "scratch-a"))
                _, second = native_auth_command("claude_subscription", settings, str(Path(root) / "scratch-b"))
            self.assertEqual(first["CLAUDE_CONFIG_DIR"], second["CLAUDE_CONFIG_DIR"])
            self.assertTrue(first["CLAUDE_CONFIG_DIR"].startswith(str(store.directory)))

    def test_two_instances_cannot_drop_each_others_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = ConnectionStore(root), ConnectionStore(root)
            a = first.add("OPENAI", "SUBSCRIPTION")
            b = second.add("ANTHROPIC", "SUBSCRIPTION")
            self.assertEqual(set(ConnectionStore(root).records), {a["id"], b["id"]})
            first.records[a["id"]]["name"] = "Renamed account"
            first.save()
            self.assertEqual(set(ConnectionStore(root).records), {a["id"], b["id"]})
            self.assertEqual(ConnectionStore(root).records[a["id"]]["name"], "Renamed account")

    def test_remove_is_not_undone_by_an_older_instance(self):
        with tempfile.TemporaryDirectory() as root:
            first = ConnectionStore(root)
            account = first.add("ANTHROPIC", "SUBSCRIPTION")
            stale = ConnectionStore(root)
            first.remove(account["id"])
            stale.records[account["id"]]["name"] = "Stale edit"
            stale.save()
            self.assertNotIn(account["id"], ConnectionStore(root).records)

    def test_metadata_changes_are_visible_to_another_instance(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = ConnectionStore(root), ConnectionStore(root)
            account = first.add("ANTHROPIC", "SUBSCRIPTION")
            self.assertTrue(second.reload_if_changed())
            self.assertIn(account["id"], second.records)
            self.assertFalse(second.reload_if_changed())
            first.remove(account["id"])
            self.assertTrue(second.reload_if_changed())
            self.assertNotIn(account["id"], second.records)

    def test_one_corrupt_record_does_not_hide_later_connections(self):
        with tempfile.TemporaryDirectory() as root:
            store = ConnectionStore(root)
            account = store.add("ANTHROPIC", "SUBSCRIPTION")
            data = json.loads(store.path.read_text())
            data["connections"].insert(0, {"id": "broken"})
            store.path.write_text(json.dumps(data))
            self.assertIn(account["id"], ConnectionStore(root).records)


if __name__ == "__main__":
    unittest.main()
