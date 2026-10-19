# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Product rename: deprecated environment spellings, the data-folder move and retained native profiles.

Dependency-free. Every data folder is under a temporary fake HOME; the real
``~/Library/Application Support/Sensei`` and ``Angelo`` folders are never read.
The pre-rename names below are deliberate legacy fixtures.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (change_feed, cli_client, data_migration, external_cli, external_host,  # noqa: E402
                           folder_moves, product_env, storage)
from angelo_agent.authentication import native_auth_command  # noqa: E402
from angelo_agent.external_cli import TransportUnavailable  # noqa: E402
from angelo_agent.provider_registry import SPECS  # noqa: E402
from angelo_agent.providers import child_environment, profile_environment  # noqa: E402
from angelo_agent.studio_state import library_directory  # noqa: E402

ACCOUNT = "3f7c0f5e-58f5-4cf1-8a57-0f0d6b8a2c11"
A = os.path.abspath   # "/tmp/q" as an absolute path of this platform (C:\tmp\q on Windows)


class ProductEnvironmentTests(unittest.TestCase):
    def test_angelo_wins_and_both_pre_rename_spellings_are_deprecated_fallbacks(self):
        self.assertEqual(product_env.names("CONNECTIONS_DIR"),
                         ("ANGELO_CONNECTIONS_DIR", "SENSEI_CONNECTIONS_DIR", "TOONLAB_CONNECTIONS_DIR"))
        self.assertEqual(product_env.lookup("X", {"SENSEI_X": "old"}), ("old", "SENSEI_X"))
        self.assertEqual(product_env.lookup("X", {"TOONLAB_X": "older"}), ("older", "TOONLAB_X"))
        self.assertEqual(product_env.lookup("X", {"SENSEI_X": "old", "TOONLAB_X": "older"}), ("old", "SENSEI_X"))
        self.assertEqual(product_env.lookup("X", {"SENSEI_X": "old", "TOONLAB_X": "older", "ANGELO_X": "new"}),
                         ("new", "ANGELO_X"))
        self.assertEqual(product_env.lookup("X", {}), (None, None))
        self.assertEqual(product_env.get("X", {}, "default"), "default")

    def test_empty_values_count_as_unset_like_the_build_script(self):
        self.assertEqual(product_env.lookup("X", {"ANGELO_X": "", "SENSEI_X": "old"}), ("old", "SENSEI_X"))
        self.assertEqual(product_env.get("X", {"ANGELO_X": "", "SENSEI_X": ""}, "default"), "default")

    def test_flags_require_exactly_one_and_angelo_zero_overrides_sensei_one(self):
        self.assertTrue(product_env.flag("F", {"SENSEI_F": "1"}))
        self.assertFalse(product_env.flag("F", {"ANGELO_F": "0", "SENSEI_F": "1"}))
        self.assertTrue(product_env.flag("F", {"TOONLAB_F": "1"}))
        self.assertFalse(product_env.flag("F", {"ANGELO_F": "0", "TOONLAB_F": "1"}))
        self.assertFalse(product_env.flag("F", {"ANGELO_F": "true"}))

    def test_clear_and_deprecated_report(self):
        environ = {"ANGELO_F": "1", "SENSEI_F": "1", "TOONLAB_F": "1", "SENSEI_G": "", "TOONLAB_STATE_DIR": "/x"}
        self.assertEqual(product_env.deprecated_in_use(environ), ["SENSEI_F", "TOONLAB_F", "TOONLAB_STATE_DIR"])
        product_env.clear("F", environ)
        self.assertEqual(environ, {"SENSEI_G": "", "TOONLAB_STATE_DIR": "/x"})

    def test_every_reader_accepts_the_deprecated_spelling(self):
        self.assertEqual(external_cli.default_runtime_directory({"SENSEI_CLI_RUNTIME_DIR": A("/tmp/q")}, "darwin", 1),
                         Path(A("/tmp/q")))
        with self.assertRaisesRegex(TransportUnavailable, "SENSEI_CLI_RUNTIME_DIR must be an absolute path"):
            external_cli.default_runtime_directory({"SENSEI_CLI_RUNTIME_DIR": "relative"}, "linux", 1)
        self.assertEqual(external_host.kill_switch_reason({"SENSEI_DISABLE_LOCAL_CLI": "1"}),
                         "Disabled by SENSEI_DISABLE_LOCAL_CLI=1")
        self.assertEqual(external_host.kill_switch_reason({"ANGELO_DISABLE_LOCAL_CLI": "1"}),
                         "Disabled by ANGELO_DISABLE_LOCAL_CLI=1")
        self.assertIsNone(external_host.kill_switch_reason({"ANGELO_DISABLE_LOCAL_CLI": "0", "SENSEI_DISABLE_LOCAL_CLI": "1"}))
        self.assertEqual(external_host.KILL_SWITCH, "ANGELO_DISABLE_LOCAL_CLI")
        with patch.dict(os.environ, {"SENSEI_LIBRARY_DIR": A("/tmp/legacy-library")}, clear=True):
            self.assertEqual(library_directory(), Path(A("/tmp/legacy-library")))
        with patch.dict(os.environ, {"SENSEI_LIBRARY_DIR": "relative"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SENSEI_LIBRARY_DIR"):
                library_directory()
        with patch.dict(os.environ, {"SENSEI_TURN_CONTEXT": "legacy"}, clear=True):
            self.assertTrue(change_feed.legacy_turn_context())
        with patch.dict(os.environ, {"SENSEI_TURN_CONTEXT": "legacy", "ANGELO_TURN_CONTEXT": "feed"}, clear=True):
            self.assertFalse(change_feed.legacy_turn_context())

    def test_cli_client_label_falls_back_to_sensei_client(self):
        with tempfile.TemporaryDirectory() as state, contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()):
            cli_client.main(["--state-dir", state, "status"], environ={"SENSEI_CLIENT": "not a label!"})
            invalid = json.loads(out.getvalue())
        self.assertEqual(invalid["code"], "invalid")
        self.assertIn("client label", invalid["message"], "the deprecated SENSEI_CLIENT label was read")
        with tempfile.TemporaryDirectory() as state, contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            cli_client.main(["--state-dir", state, "status"],
                            environ={"ANGELO_CLIENT": "agent", "SENSEI_CLIENT": "not a label!"})
            unavailable = json.loads(out.getvalue())
        self.assertEqual(unavailable["code"], "unavailable", "the valid ANGELO_CLIENT label won")
        self.assertIn("Angelo is not running", unavailable["message"])
        self.assertTrue(err.getvalue().startswith("angelo: unavailable"))
        self.assertEqual(cli_client.parser().prog, "angelo")

    def test_provider_key_variables_use_the_angelo_prefix_and_inherited_ones_are_dropped(self):
        keys = {spec["env_key"] for spec in SPECS.values() if spec.get("env_key")}
        self.assertEqual(keys, {"ANGELO_XAI_API_KEY", "ANGELO_MINIMAX_API_KEY", "ANGELO_KIMI_API_KEY",
                                "ANGELO_TEAM_GRANT_KEY"})
        inherited = {name: "leak" for key in keys
                     for name in (key, key.replace("ANGELO_", "SENSEI_"), key.replace("ANGELO_", "TOONLAB_"))}
        with patch.dict(os.environ, inherited):
            env = child_environment("codex_subscription")
        self.assertFalse(set(inherited) & set(env))

    def test_scripts_outside_the_add_on_use_the_same_names(self):
        startup = (ROOT / "scripts/startup/angelo_default_layout.py").read_text()
        self.assertIn('product_env.flag("DISABLE_DEFAULT_CHAT")', startup)
        self.assertNotIn("os.environ", startup, "the opt-out has one owner: product_env")
        build = (ROOT / "scripts/angelo/build_native.sh").read_text()
        self.assertIn("${ANGELO_NATIVE_BUILD_DIR:-${SENSEI_NATIVE_BUILD_DIR:-${TOONLAB_NATIVE_BUILD_DIR:-", build)
        self.assertIn("$SOURCE/../build-angelo-native}", build)
        self.assertIn("${ANGELO_BUILD_JOBS:-${SENSEI_BUILD_JOBS:-${TOONLAB_BUILD_JOBS:-", build)
        signing = (ROOT / "scripts/angelo/dev_codesign.sh").read_text()
        self.assertIn("${ANGELO_DEV_SIGN_ID:-${SENSEI_DEV_SIGN_ID:-${TOONLAB_DEV_SIGN_ID:-", signing)
        for script in ("angelo_branding.mjs", "angelo_letters.mjs"):
            branding = (ROOT / "build_files/utils" / script).read_text()
            self.assertIn("process.env.ANGELO_NODE_MODULES || process.env.SENSEI_NODE_MODULES"
                          " || process.env.TOONLAB_NODE_MODULES", branding)


class DataFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-migration-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.legacy = self.home / "Library/Application Support/Sensei"
        self.current = self.home / "Library/Application Support/Angelo"

    def migrate(self, environ=None, platform="darwin"):
        return data_migration.migrate_legacy_data(environ or {}, platform, self.home)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def files(self, root):
        return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}

    def test_default_folder_is_angelo_on_every_platform(self):
        self.assertEqual(storage.data_directory({}, "darwin", self.home), self.current)
        self.assertEqual(storage.data_directory({}, "linux", self.home), self.home / ".local/state/Angelo")
        self.assertEqual(storage.data_directory({"XDG_STATE_HOME": A("/x/state")}, "linux", self.home), Path(A("/x/state/Angelo")))
        self.assertEqual(storage.data_directory({"LOCALAPPDATA": A("/c/Local")}, "win32", self.home), Path(A("/c/Local/Angelo")))
        self.assertEqual(storage.data_directory({}, "darwin", self.home, storage.LEGACY_DATA_FOLDER), self.legacy)

    def test_nothing_to_move(self):
        self.assertEqual(self.migrate().status, "nothing")
        self.assertFalse(self.current.exists())

    def test_isolated_account_store_never_touches_the_default_folders(self):
        self.write(self.legacy / "connections/connections.json", "{}")
        for variable in ("ANGELO_CONNECTIONS_DIR", "SENSEI_CONNECTIONS_DIR"):
            with self.subTest(variable=variable):
                result = self.migrate({variable: A("/tmp/isolated")})
                self.assertEqual(result.status, "isolated")
                self.assertIn(variable, result.message)
                self.assertTrue((self.legacy / "connections/connections.json").is_file())
                self.assertFalse(self.current.exists())

    def test_moves_everything_without_profiles_and_removes_only_empty_folders(self):
        before = {"connections/connections.json": '{"version": 3}', "connections/connections.lock": "",
                  "media-studio/assets/a/record.json": "{}", "notes.txt": "keep me"}
        for name, text in before.items():
            self.write(self.legacy / name, text)
        result = self.migrate()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertEqual(self.files(self.current), before)
        self.assertFalse(self.legacy.exists(), "only the emptied folders were removed")
        self.assertEqual(result.retained, ())
        self.assertEqual(self.migrate().status, "nothing")

    def test_native_profiles_stay_at_their_exact_path_and_keep_resolving(self):
        self.write(self.legacy / "connections/connections.json", "{}")
        self.write(self.legacy / f"connections/claude-profile/{ACCOUNT}/.claude.json", "signed in")
        self.write(self.legacy / "connections/codex-profile/codex_api/auth.json", "key ref")
        self.write(self.legacy / "media-studio/library.json", "{}")
        result = self.migrate()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertEqual(self.files(self.current), {"connections/connections.json": "{}", "media-studio/library.json": "{}"})
        self.assertEqual(self.files(self.legacy / "connections"), {f"claude-profile/{ACCOUNT}/.claude.json": "signed in",
                                                                   "codex-profile/codex_api/auth.json": "key ref"})
        note = (self.legacy / data_migration.NOTE_NAME).read_text()
        self.assertIn(str(self.current), note)
        self.assertIn("connections/claude-profile", note)
        again = self.migrate()
        self.assertEqual(again.status, "nothing")
        self.assertEqual(len(again.retained), 2)
        state_dir = str(storage.connection_directory({}, "darwin", self.home))
        resolve = lambda family, name: storage.native_profile_directory(state_dir, family, name, {}, "darwin", self.home)
        self.assertEqual(resolve("claude-profile", ACCOUNT), self.legacy / f"connections/claude-profile/{ACCOUNT}")
        self.assertEqual(resolve("codex-profile", "codex_api"), self.legacy / "connections/codex-profile/codex_api")
        fresh = str(uuid.uuid4())
        self.assertEqual(resolve("claude-profile", fresh), self.current / f"connections/claude-profile/{fresh}")
        (self.current / f"connections/claude-profile/{ACCOUNT}").mkdir(parents=True)
        self.assertEqual(resolve("claude-profile", ACCOUNT), self.current / f"connections/claude-profile/{ACCOUNT}")
        other = storage.native_profile_directory("/tmp/isolated", "claude-profile", fresh, {}, "darwin", self.home)
        self.assertEqual(other, Path("/tmp/isolated/claude-profile") / fresh)

    def test_runtime_and_sign_in_check_pass_the_retained_path_to_the_native_cli(self):
        self.write(self.legacy / f"connections/claude-profile/{ACCOUNT}/.claude.json", "signed in")
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / ".local/state"),
                                     "LOCALAPPDATA": str(self.home / "AppData/Local")}, clear=True), \
                patch.object(Path, "home", return_value=self.home):
            legacy = storage.data_directory(folder=storage.LEGACY_DATA_FOLDER) / f"connections/claude-profile/{ACCOUNT}"
            if legacy != self.legacy / f"connections/claude-profile/{ACCOUNT}":
                self.write(legacy / ".claude.json", "signed in")  # Linux/Windows host: same layout, other root.
            state_dir = str(storage.connection_directory())
            runtime = profile_environment({}, "claude_subscription", state_dir, False, ACCOUNT, None)
            settings = {"auth_directory": state_dir, "connection_id": ACCOUNT, "reuse_codex_login": False}
            with patch("angelo_agent.authentication.find_executable", return_value="/official/claude"):
                _, check = native_auth_command("claude_subscription", settings, "/tmp/scratch")
        self.assertEqual(runtime["CLAUDE_CONFIG_DIR"], str(legacy))
        self.assertEqual(check["CLAUDE_CONFIG_DIR"], str(legacy))

    def test_conflict_moves_nothing_and_explains(self):
        old = self.write(self.legacy / "connections/connections.json", '{"old": 1}')
        new = self.write(self.current / "connections/connections.json", '{"new": 1}')
        self.write(self.legacy / "media-studio/a.json", "{}")
        result = self.migrate()
        self.assertEqual(result.status, "conflict")
        self.assertTrue(result.needs_attention)
        self.assertIn(str(new), result.message)
        self.assertEqual((old.read_text(), new.read_text()), ('{"old": 1}', '{"new": 1}'))
        self.assertTrue((self.legacy / "media-studio/a.json").is_file(), "nothing moved")
        self.assertFalse((self.current / "media-studio").exists())

    def test_a_file_that_appears_after_the_conflict_check_is_never_replaced(self):
        source = self.write(self.legacy / "notes.txt", "old")
        target = self.write(self.current / "notes.txt", "new")
        with self.assertRaises(FileExistsError):
            folder_moves.move_entry(source, target)
        self.assertEqual((source.read_text(), target.read_text()), ("old", "new"))
        folder = self.write(self.legacy / "media-studio/a.json", "old").parent
        self.write(self.current / "media-studio/b.json", "new")
        with self.assertRaises(OSError):
            folder_moves.move_entry(folder, self.current / "media-studio")
        self.assertEqual(self.files(self.current / "media-studio"), {"b.json": "new"})
        self.assertEqual(self.files(folder), {"a.json": "old"})

    def test_a_file_where_the_angelo_folder_belongs_is_a_conflict(self):
        self.write(self.legacy / "connections/connections.json", "{}")
        self.write(self.current, "not a folder")
        self.assertEqual(self.migrate().status, "conflict")
        self.assertTrue((self.legacy / "connections/connections.json").is_file())

    def test_an_interrupted_move_resumes_without_conflict_or_loss(self):
        self.write(self.legacy / "connections/connections.json", "{}")
        self.write(self.legacy / "media-studio/a.json", "asset")
        self.write(self.legacy / "z-last.txt", "z")
        real_move, calls = folder_moves.move_entry, []

        def failing_move(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            real_move(source, target)

        with patch.object(folder_moves, "move_entry", failing_move):
            interrupted = self.migrate()
        self.assertEqual(interrupted.status, "failed")
        self.assertIn("resumes the move on its next launch", interrupted.message)
        self.assertEqual(len(interrupted.moved), 1)
        resumed = self.migrate()
        self.assertEqual(resumed.status, "migrated", resumed.message)
        self.assertEqual(self.files(self.current), {"connections/connections.json": "{}", "media-studio/a.json": "asset",
                                                    "z-last.txt": "z"})
        self.assertFalse(self.legacy.exists())

    def test_a_symlinked_legacy_folder_is_left_alone(self):
        target = Path(self.temp.name) / "elsewhere"
        self.write(target / "connections/connections.json", "{}")
        self.legacy.parent.mkdir(parents=True)
        self.legacy.symlink_to(target)
        self.assertEqual(self.migrate().status, "nothing")
        self.assertTrue((target / "connections/connections.json").is_file())

    def test_linux_and_windows_folders_move_the_same_way(self):
        for platform, environ, base in (("linux", {}, self.home / ".local/state"),
                                        ("win32", {"LOCALAPPDATA": str(self.home / "Local")}, self.home / "Local")):
            with self.subTest(platform=platform):
                self.write(base / "Sensei/connections/connections.json", platform)
                self.assertEqual(self.migrate(environ, platform).status, "migrated")
                self.assertEqual((base / "Angelo/connections/connections.json").read_text(), platform)
                self.assertFalse((base / "Sensei").exists())

    def test_startup_records_attention_only_for_conflicts_and_failures(self):
        with patch.object(data_migration, "migrate_legacy_data",
                          return_value=data_migration.MigrationResult("conflict", "Both exist")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            data_migration.migrate_on_startup()
        self.assertEqual(data_migration.attention_message(), "Both exist")
        self.assertIn("Angelo data folder: Both exist", out.getvalue())
        with patch.object(data_migration, "migrate_legacy_data",
                          return_value=data_migration.MigrationResult("nothing", "No folder")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            data_migration.migrate_on_startup()
        self.assertIsNone(data_migration.attention_message())
        self.assertEqual(out.getvalue(), "")
        with patch.object(data_migration, "migrate_legacy_data", side_effect=TypeError("defect")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(data_migration.migrate_on_startup().status, "failed")
        self.assertIn("TypeError: defect", data_migration.attention_message())


if __name__ == "__main__":
    unittest.main()
