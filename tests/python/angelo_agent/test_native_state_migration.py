# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pre-rename ``toonlab-agent`` state folder moves to ``angelo-agent`` without loss.

Dependency-free. A legacy-shaped profile is written by the real stores under
``<config>/toonlab-agent`` (so every recorded absolute path, token and journal
root is exactly what a pre-rename build produced), then migrated. Synthetic
.blend bytes; test_blender_legacy_migration.py repeats this with the app.
The pre-rename folder name is a deliberate legacy fixture.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from snapshot_fixtures import blend_v1, scene_blocks  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import folder_moves, state_migration, state_paths, storage  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.sessions import read_transcript, SessionStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.task_memory import new_memory, scope_token  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class StateFolderMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-state-migration-")
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name).resolve() / "config"
        self.legacy = self.config / state_paths.LEGACY_STATE_FOLDER
        self.current = self.config / state_paths.STATE_FOLDER
        self.session = str(uuid.uuid4())

    def migrate(self, environ=None):
        return state_migration.migrate_state_folder(self.config, environ or {})

    def seed(self):
        """A pre-rename profile: chat with brief and attachment, deduplicated snapshot, file journal."""
        store = CheckpointStore(self.legacy, self.session)
        data = blend_v1(scene_blocks(objects=8, array_bytes=256 * 1024))
        workspace = self.legacy / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        (workspace / "notes.md").write_text("before the request")
        record, path = store.reserve(str(workspace / "scratch.blend"))
        path.write_bytes(data)
        record = store.complete(record["id"])
        ingest_snapshot(store, record["id"])
        FileWorkspace(workspace).execute("angelo_write_file", {"path": "notes.md", "content": "written by the agent"},
                                         store, record["id"])
        reference = ReferenceStore(self.legacy, self.session)
        source = Path(self.temp.name) / "reference.png"
        source.write_bytes(PNG)
        attached = reference.import_images([source])[0]
        sessions = SessionStore(self.legacy, self.session)
        state = sessions.new("claude_subscription")
        memory = new_memory(str(self.legacy.resolve()))
        memory["brief"]["objective"] = "Keep the pre-rename brief"
        state.update(native_id="claude-session-before-rename", toolset_version=14, task_memory=memory,
                     messages=[{"role": "user", "text": "Look at this", "references": [attached]}],
                     runtime_sessions={"claude_subscription": {"native_id": "claude-session-before-rename",
                                                               "synced_length": 1, "toolset_version": 14}})
        sessions.save(state)
        (self.legacy / "codex-profile/codex_api").mkdir(parents=True)
        (self.legacy / "codex-profile/codex_api/auth.json").write_text("{}")
        return record, data, attached

    def test_moves_everything_and_every_recorded_identity_still_works(self):
        record, data, attached = self.seed()
        result = self.migrate()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertIn("1 snapshot records updated", result.message)
        self.assertEqual(sorted(p.name for p in self.legacy.iterdir()), ["README-Angelo.txt", "codex-profile"])
        self.assertFalse((self.current / state_migration.MARKER).exists())
        with patch.dict(os.environ, {}, clear=True):
            # Chat, brief and attachment: the brief is re-scoped, never dropped.
            state = SessionStore(self.current, self.session).load()
            self.assertEqual(state["task_memory"]["brief"]["objective"], "Keep the pre-rename brief")
            self.assertEqual(state["task_memory"]["scope"]["profile"], scope_token(str(self.current)))
            # A pre-rename native context is never resumed; the chat hands its history over instead.
            self.assertIsNone(state["native_id"])
            self.assertIsNone(state["runtime_sessions"]["claude_subscription"]["native_id"])
            stored = state["messages"][0]["references"][0]
            self.assertTrue(stored["path"].startswith(str(self.legacy)), "the record itself is not rewritten")
            self.assertEqual(ReferenceStore(self.current, self.session).checked_path(stored).read_bytes(), PNG)
            # The deduplicated snapshot still restores byte for byte from its moved store.
            store = CheckpointStore(self.current, self.session)
            self.assertEqual(store.load(record["id"])["storage"], "store")
            _, recovered = store.recovery_copy(record["id"])
            self.assertEqual(recovered.read_bytes(), data)
            # The file journal follows the moved default workspace.
            plan = store.restore_plan(record["id"])
            self.assertEqual([str(target) for target, _backup, _entry in plan], [str(self.current / "workspace/notes.md")])
            self.assertEqual(store.restore_since(record["id"]), 1)
            self.assertEqual((self.current / "workspace/notes.md").read_text(), "before the request")
            self.assertEqual(store.inspect()["issues"], [])
            # Native CLI profiles stay at their original path and keep resolving.
            self.assertEqual(storage.native_profile_directory(self.current, "codex-profile", "codex_api"),
                             self.legacy / "codex-profile/codex_api")
            self.assertEqual(storage.native_profile_directory(self.current, "claude-profile", "x"),
                             self.current / "claude-profile/x")

    def test_a_brief_from_any_other_profile_is_still_dropped(self):
        self.seed()
        self.migrate()
        sessions = SessionStore(self.current, self.session)
        state = read_transcript(sessions.path)
        state["task_memory"]["scope"]["profile"] = scope_token("/some/other/profile")
        sessions.path.write_text(json.dumps(state))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sessions.load()["task_memory"]["brief"]["objective"], "")
        state["task_memory"]["scope"]["profile"] = scope_token(str(self.legacy))
        sessions.path.write_text(json.dumps(state))
        with patch.dict(os.environ, {"ANGELO_STATE_DIR": str(self.current)}, clear=True):
            self.assertEqual(sessions.load()["task_memory"]["brief"]["objective"], "",
                             "an explicit state folder has no pre-rename sibling")

    def test_an_explicit_state_folder_is_never_migrated(self):
        self.seed()
        for variable in ("ANGELO_STATE_DIR", "SENSEI_STATE_DIR", "TOONLAB_STATE_DIR"):
            with self.subTest(variable=variable):
                result = self.migrate({variable: "/tmp/isolated-state"})
                self.assertEqual(result.status, "isolated")
                self.assertIn(variable, result.message)
        self.assertTrue((self.legacy / "sessions").is_dir())
        self.assertFalse(self.current.exists())

    def test_nothing_to_move_and_a_second_run_is_a_no_op(self):
        self.assertEqual(self.migrate().status, "nothing")
        self.seed()
        self.assertEqual(self.migrate().status, "migrated")
        before = sorted(str(p) for p in self.current.rglob("*"))
        self.assertEqual(self.migrate().status, "nothing")
        self.assertEqual(sorted(str(p) for p in self.current.rglob("*")), before)

    def test_conflicting_state_moves_nothing_and_reports_both_folders(self):
        self.seed()
        (self.current / "sessions").mkdir(parents=True)
        (self.current / "sessions/newer.json").write_text("{}")
        result = self.migrate()
        self.assertEqual(result.status, "conflict")
        self.assertTrue(result.needs_attention)
        self.assertIn(str(self.legacy), result.message)
        self.assertIn(str(self.current / "sessions"), result.message)
        self.assertTrue((self.legacy / "checkpoints").is_dir(), "nothing moved")
        self.assertFalse((self.current / "checkpoints").exists())
        self.assertFalse((self.current / state_migration.MARKER).exists())

    def test_an_empty_angelo_folder_is_not_a_conflict(self):
        self.seed()
        self.current.mkdir(parents=True)
        self.assertEqual(self.migrate().status, "migrated")

    def test_an_interrupted_move_and_relocation_resume_without_loss(self):
        record, data, _attached = self.seed()
        real_move, calls = folder_moves.move_entry, []

        def failing_move(source, target):
            calls.append(source)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            real_move(source, target)

        with patch.object(folder_moves, "move_entry", failing_move):
            interrupted = self.migrate()
        self.assertEqual(interrupted.status, "failed")
        self.assertIn("resumes the move on its next launch", interrupted.message)
        self.assertEqual(len(interrupted.moved), 2)
        self.assertTrue((self.current / state_migration.MARKER).is_file())
        with patch.object(state_migration, "relocate_snapshot_records", side_effect=OSError("disk busy")):
            relocation = self.migrate()
        self.assertEqual(relocation.status, "failed")
        self.assertIn("updating its snapshot records stopped", relocation.message)
        self.assertTrue((self.current / state_migration.MARKER).is_file(), "kept until every record is rewritten")
        self.assertEqual(self.migrate().status, "migrated")
        self.assertFalse((self.current / state_migration.MARKER).exists())
        store = CheckpointStore(self.current, self.session)
        self.assertEqual(store.recovery_copy(record["id"])[1].read_bytes(), data)
        self.assertEqual(store.restore_since(record["id"]), 1)

    def test_pre_ordered_journals_keep_their_recorded_roots(self):
        self.seed()
        identity = str(uuid.uuid4())
        folder = self.legacy / "checkpoints" / self.session / identity
        folder.mkdir(mode=0o700)
        legacy_record = {"version": 1, "id": identity, "created": 10, "original_path": str(self.legacy / "workspace/a.blend"),
                         "files": [{"root": str(self.legacy / "workspace"), "path": "old.md", "existed": False,
                                    "backup": "b.bin", "mode": 384}], "state": "READY"}
        (folder / "checkpoint.json").write_text(json.dumps(legacy_record))
        self.migrate()
        moved = json.loads((self.current / "checkpoints" / self.session / identity / "checkpoint.json").read_text())
        self.assertEqual(moved["files"], legacy_record["files"], "legacy timing evidence is never rewritten")
        self.assertEqual(moved["original_path"], str(self.current / "workspace/a.blend"))

    def test_relocate_only_rewrites_paths_inside_the_old_folder(self):
        legacy, current = Path("/c/toonlab-agent"), Path("/c/angelo-agent")   # Recorded paths use the platform's form.
        self.assertEqual(state_paths.relocate(str(legacy / "workspace"), legacy, current), str(current / "workspace"))
        self.assertEqual(state_paths.relocate(str(legacy), legacy, current), str(current))
        other = str(Path("/c/toonlab-agent-other/x"))
        self.assertEqual(state_paths.relocate(other, legacy, current), other)
        self.assertEqual(state_paths.relocate(str(Path("/projects/scene")), legacy, current), str(Path("/projects/scene")))

    def test_legacy_sibling_only_for_the_default_folder_name_without_override(self):
        self.assertEqual(state_paths.legacy_sibling(self.current, {}), self.legacy)
        self.assertIsNone(state_paths.legacy_sibling(self.current, {"TOONLAB_STATE_DIR": "/x"}))
        self.assertIsNone(state_paths.legacy_sibling(self.config / "elsewhere", {}))

    def test_references_never_resolve_outside_their_conversation_folder(self):
        store = ReferenceStore(self.current, self.session)
        store.directory.mkdir(parents=True)
        name = str(uuid.uuid4()) + ".png"
        (store.directory / name).write_bytes(PNG)
        self.assertEqual(store.checked_path({"path": "/anywhere/else/" + name}), store.directory / name)
        for path in ("/etc/passwd", "", "..", str(store.directory.parent / ("x" + name))):
            with self.subTest(path=path), self.assertRaises(ValueError):
                store.checked_path({"path": path})


if __name__ == "__main__":
    unittest.main()
