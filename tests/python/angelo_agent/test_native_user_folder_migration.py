# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's agent state moves out of the folder a stock Blender also uses, without loss.

Dependency-free. A profile is written by the real stores under a temporary home's
``<Blender folder>/5.2/config/angelo-agent`` (what a build from before the application
identity change produced), then ``scripts/angelo/migrate_user_folder.py`` moves it.
Every path is under a temporary directory: the real Blender and Angelo folders are
never touched. See doc/angelo/app_identity.rst, "Migrating existing settings".
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/modules"))
sys.path.insert(0, str(HERE))
import test_native_state_migration as base  # noqa: E402
from angelo_agent import state_migration, storage  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from angelo_agent.task_memory import scope_token  # noqa: E402

_spec = importlib.util.spec_from_file_location("migrate_user_folder", ROOT / "scripts/angelo/migrate_user_folder.py")
migrate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate)


@unittest.skipUnless(anchored_files_supported(), "descriptor- or handle-relative storage required")
class UserFolderMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-user-folder-migration-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        support = self.home / "Library/Application Support"
        self.legacy = support / "Blender/5.2/config/angelo-agent"
        self.current = support / "Angelo/5.2/config/angelo-agent"
        self.session = str(uuid.uuid4())

    def seed(self):
        return base.StateFolderMigrationTests.seed(self)

    def run_migration(self, apply=True, environ=None):
        return migrate.plan_and_run("darwin", self.home, environ or {}, "5.2", apply)

    def test_folders_are_blender_for_the_old_and_angelo_for_the_new(self):
        old, new = migrate._roots("darwin", self.home, {}, "5.2")
        self.assertEqual(old, self.home / "Library/Application Support/Blender/5.2")
        self.assertEqual(new, self.home / "Library/Application Support/Angelo/5.2")
        old, new = migrate._roots("win32", self.home, {"APPDATA": str(self.home / "Roaming")}, "5.2")
        self.assertEqual(old, self.home / "Roaming/Blender Foundation/Blender/5.2")
        self.assertEqual(new, self.home / "Roaming/Angelo/5.2")
        old, new = migrate._roots("linux", self.home, {}, "5.2")
        self.assertEqual((old, new), (self.home / ".config/blender/5.2", self.home / ".config/angelo/5.2"))

    def test_a_dry_run_changes_nothing(self):
        self.seed()
        before = sorted(str(p) for p in self.home.rglob("*"))
        status, message = self.run_migration(apply=False)
        self.assertEqual(status, "planned", message)
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)

    def test_moves_state_and_every_recorded_identity_still_works(self):
        record, data, attached = self.seed()
        # Blender's own files beside it: never opened, moved or deleted.
        blender_config = self.legacy.parent
        (blender_config / "userpref.blend").write_bytes(b"BLENDER-userpref")
        (blender_config / "startup.blend").write_bytes(b"BLENDER-startup")
        # The account folder (connections, the team usage buffer and its lock) shares the product folder with
        # the version folder the state moves into; it was Angelo's already and stays exactly where it is.
        connections = self.home / "Library/Application Support/Angelo/connections"
        connections.mkdir(parents=True)
        (connections / "team-usage.json").write_text('{"owners": {}}')
        (connections / "team-usage.lock").write_bytes(b"")
        status, message = self.run_migration()
        self.assertEqual(status, "migrated", message)
        self.assertEqual(sorted(p.name for p in self.legacy.iterdir()), ["README-Angelo.txt", "codex-profile"])
        self.assertEqual((blender_config / "userpref.blend").read_bytes(), b"BLENDER-userpref")
        self.assertEqual((blender_config / "startup.blend").read_bytes(), b"BLENDER-startup")
        self.assertFalse((self.current / state_migration.MARKER).exists())
        with patch.dict(os.environ, {}, clear=True):
            state = SessionStore(self.current, self.session).load()
            self.assertEqual(state["task_memory"]["brief"]["objective"], "Keep the pre-rename brief",
                             "the brief follows the chat to the new folder")
            self.assertEqual(state["task_memory"]["scope"]["profile"], scope_token(str(self.current)))
            store = CheckpointStore(self.current, self.session)
            self.assertEqual(store.recovery_copy(record["id"])[1].read_bytes(), data)
            plan = store.restore_plan(record["id"])
            self.assertEqual([str(target) for target, _backup, _entry in plan],
                             [str(self.current / "workspace/notes.md")])
            self.assertEqual(store.restore_since(record["id"]), 1)
            self.assertEqual(store.inspect()["issues"], [])
            # The next request's snapshot allocates on the moved clock (validated against the moved history).
            later, path = store.reserve(str(self.current / "workspace/scratch.blend"))
            path.write_bytes(data)
            store.complete(later["id"])
            self.assertEqual(store.inspect()["issues"], [])
            # The retained native CLI profile keeps its exact path, as in the rename migration.
            self.assertEqual((self.legacy / "codex-profile/codex_api/auth.json").read_text(), "{}")
        self.assertEqual(sorted(p.name for p in connections.iterdir()), ["team-usage.json", "team-usage.lock"])
        self.assertEqual((connections / "team-usage.json").read_text(), '{"owners": {}}')

    def test_a_second_run_is_a_no_op(self):
        self.seed()
        self.assertEqual(self.run_migration()[0], "migrated")
        before = sorted(str(p) for p in self.home.rglob("*"))
        self.assertEqual(self.run_migration()[0], "nothing")
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)

    def test_existing_angelo_state_is_a_conflict_and_nothing_moves(self):
        self.seed()
        (self.current / "sessions").mkdir(parents=True)
        (self.current / "sessions/newer.json").write_text("{}")
        status, message = self.run_migration()
        self.assertEqual(status, "conflict", message)
        self.assertTrue((self.legacy / "checkpoints").is_dir())
        self.assertFalse((self.current / "checkpoints").exists())

    def test_explicit_blender_folders_mean_nothing_to_migrate(self):
        self.seed()
        for variable in migrate.OVERRIDE_VARIABLES:
            status, message = self.run_migration(environ={variable: "/somewhere/explicit"})
            self.assertEqual(status, "isolated", message)
        self.assertTrue((self.legacy / "sessions").is_dir())

    def test_an_interrupted_move_resumes_even_after_every_entry_moved(self):
        record, data, _attached = self.seed()
        with patch.object(state_migration, "relocate_snapshot_records", side_effect=OSError("disk busy")):
            status, message = self.run_migration()
        self.assertEqual(status, "failed", message)
        self.assertTrue((self.current / state_migration.MARKER).is_file(), "kept until every record is rewritten")
        self.assertEqual(sorted(p.name for p in self.legacy.iterdir()), ["README-Angelo.txt", "codex-profile"])
        status, message = self.run_migration(apply=False)
        self.assertEqual(status, "planned", message)
        status, message = self.run_migration()
        self.assertEqual(status, "migrated", message)
        self.assertFalse((self.current / state_migration.MARKER).exists())
        with patch.dict(os.environ, {}, clear=True):
            store = CheckpointStore(self.current, self.session)
            self.assertEqual(store.recovery_copy(record["id"])[1].read_bytes(), data)
            self.assertEqual(store.restore_since(record["id"]), 1)

    def test_nothing_to_migrate_without_angelo_state(self):
        (self.legacy.parent).mkdir(parents=True)
        (self.legacy.parent / "userpref.blend").write_bytes(b"BLENDER-userpref")
        self.assertEqual(self.run_migration()[0], "nothing")
        self.assertEqual([p.name for p in self.legacy.parent.iterdir()], ["userpref.blend"])


if __name__ == "__main__":
    unittest.main()
