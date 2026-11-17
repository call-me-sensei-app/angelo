# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A newer Blender base's first launch moves the earlier version's agent state over without loss.

Dependency-free. An earlier version's profile is written by the real stores under ``<product>/5.2/config/angelo-agent``
(so every recorded absolute path, token and journal root is what that build produced), then carried into
``<product>/5.3``. The break-it cases: a crash or a full disk mid-move, an earlier partial copy, two installs side by
side (another Angelo open, two first launches at once), links, retained sign-in profiles and Windows path lengths.
``test_blender_version_carry_over.py`` repeats it with the app (the release gate).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import json
import ntpath
import os
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from snapshot_fixtures import blend_v1, scene_blocks  # noqa: E402
import platform_env  # noqa: E402
from platform_env import link_text  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
from angelo_agent.checkpoint_filesystem import anchored_files_supported  # noqa: E402
from angelo_agent import (carry_over_moves, carry_over_processes as processes, carry_over_records,  # noqa: E402
                          folder_moves, settings_import, state_paths, storage, version_carry_over as carry)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.sessions import SessionCollection, SessionStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.task_memory import new_memory, scope_token  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
ALONE = lambda: False   # noqa: E731 - no other Angelo runs
UNREADABLE = environment_skip(
    "POSIX permissions as an ordinary user (Windows keeps only a read-only bit; root reads anything)")


def inventory(folder):
    """{relative path: bytes or link target} of every file and link under ``folder``."""
    found = {}
    for path in sorted(Path(folder).rglob("*")) if Path(folder).exists() else ():
        if path.is_symlink():
            found[str(path.relative_to(folder))] = ("link", os.readlink(path))
        elif path.is_file() and path.name != "checkpoint.lock":
            found[str(path.relative_to(folder))] = path.read_bytes()
    return found


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative storage required")
class VersionCarryOverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-carry-over-")
        self.addCleanup(self.temp.cleanup)
        self.product = Path(self.temp.name).resolve() / "Angelo"
        self.old_config, self.config = self.product / "5.2/config", self.product / "5.3/config"
        self.old, self.new = self.old_config / state_paths.STATE_FOLDER, self.config / state_paths.STATE_FOLDER
        self.session = str(uuid.uuid4())
        patcher = platform_env.cleared()   # No ANGELO_*/BLENDER_USER_* from the caller.
        patcher.start()
        self.addCleanup(patcher.stop)
        carry._ran = False

    def assertMovedAway(self):
        """Only the pointer and the person's choices for the earlier version stay in the old folder."""
        self.assertEqual(set(inventory(self.old)) - {carry.NOTE_NAME, *carry.LEFT_BEHIND}, set())

    def carry(self, environ=None, others=ALONE):
        return carry.carry_over(self.config, environ or {}, others_running=others)

    def seed(self, folder=None):
        """An earlier version's profile: a chat with brief, attachment and native context, a deduplicated snapshot
        with a file journal, a background job and a worker chat that name places in the folder, preferences beside."""
        folder = folder or self.old
        store = CheckpointStore(folder, self.session)
        data = blend_v1(scene_blocks(objects=8, array_bytes=256 * 1024))
        workspace = folder / "workspace"
        workspace.mkdir(parents=True, mode=0o700)
        (workspace / "notes.md").write_text("before the request")
        record, path = store.reserve(str(workspace / "scratch.blend"))
        path.write_bytes(data)
        record = store.complete(record["id"])
        ingest_snapshot(store, record["id"])
        FileWorkspace(workspace).execute("angelo_write_file", {"path": "notes.md", "content": "written by the agent"},
                                         store, record["id"])
        source = Path(self.temp.name) / "reference.png"
        source.write_bytes(PNG)
        attached = ReferenceStore(folder, self.session).import_images([source])[0]
        chats = SessionCollection(folder)
        sessions = SessionStore(folder, self.session)
        state = sessions.new("claude_subscription")
        memory = new_memory(str(folder.resolve()))
        memory["brief"]["objective"] = "Keep the earlier version's brief"
        state.update(native_id=str(uuid.uuid4()), task_memory=memory, updated=time.time() - 60,
                     messages=[{"role": "user", "text": "Look at this", "references": [attached]}],
                     runtime_sessions={"claude_subscription": {"native_id": str(uuid.uuid4()), "synced_length": 1,
                                                               "toolset_version": 40}})
        sessions.save(state)
        chats.update(state)
        chats.save()
        (folder / "jobs.json").write_text(json.dumps({"version": 1, "jobs": [{"id": "j1", "detail": {
            "staging": str(folder / "workspace/render-staging"),
            "recovery": {"root": str(folder / "workspace"), "output": str(Path(self.temp.name) / "elsewhere.png")}}}]}))
        (folder / "worker-chats.json").write_text(json.dumps({"c1": str(folder / "parallel/lead/w1/part.blend")}))
        (folder / "intro.json").write_text(json.dumps({"seen": True, "choice": "skip"}))
        (folder.parent / "userpref.blend").write_bytes(b"BLENDER-v502 preferences")
        return record, data, attached

    # The ordinary update -----------------------------------------------------------------------------------------

    def test_moves_everything_by_rename_and_every_recorded_identity_still_works(self):
        record, data, attached = self.seed()
        before = inventory(self.old)
        snapshot = next(self.old.glob("snapshot-store/**/*.bin"), None) or next(
            p for p in self.old.rglob("*") if p.is_file() and p.stat().st_size > 64 * 1024)
        inode = snapshot.stat().st_ino
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertMovedAway()
        self.assertEqual(set(inventory(self.new)), set(before))
        moved = self.new / snapshot.relative_to(self.old)
        self.assertEqual(moved.stat().st_ino, inode, "moved, never copied")
        self.assertEqual((self.old_config / "userpref.blend").read_bytes(), b"BLENDER-v502 preferences",
                         "the earlier version keeps its own preferences")
        written = carry.read_record(self.config)
        self.assertEqual((written["status"], written["from"], written["from_version"]), ("done", str(self.old), "5.2"))
        # Chat, brief, attachment; a native context started in the old working folder is never resumed.
        state = SessionStore(self.new, self.session).load()
        self.assertEqual(state["task_memory"]["brief"]["objective"], "Keep the earlier version's brief")
        self.assertEqual(state["task_memory"]["scope"]["profile"], scope_token(str(self.new.resolve())))
        self.assertIsNone(state["native_id"])
        self.assertEqual(state["runtime_sessions"], {})
        self.assertEqual(state["messages"][0]["text"], "Look at this", "the history is kept for the fresh context")
        stored = state["messages"][0]["references"][0]
        self.assertEqual(ReferenceStore(self.new, self.session).checked_path(stored).read_bytes(), PNG)
        self.assertIn(self.session, SessionCollection(self.new).records)
        # Snapshots restore byte for byte and the file journal follows the moved workspace.
        store = CheckpointStore(self.new, self.session)
        self.assertEqual(store.recovery_copy(record["id"])[1].read_bytes(), data)
        plan = store.restore_plan(record["id"])
        self.assertEqual([str(target) for target, _b, _e in plan], [str(self.new / "workspace/notes.md")])
        self.assertEqual(store.restore_since(record["id"]), 1)
        self.assertEqual((self.new / "workspace/notes.md").read_text(), "before the request")
        self.assertEqual(store.inspect()["issues"], [])
        # Every other record that named a place in the old folder follows; a path elsewhere stays.
        jobs = json.loads((self.new / "jobs.json").read_text())["jobs"][0]["detail"]
        self.assertEqual(jobs["staging"], str(self.new / "workspace/render-staging"))
        self.assertEqual(jobs["recovery"]["root"], str(self.new / "workspace"))
        self.assertEqual(jobs["recovery"]["output"], str(Path(self.temp.name) / "elsewhere.png"))
        self.assertEqual(json.loads((self.new / "worker-chats.json").read_text()),
                         {"c1": str(self.new / "parallel/lead/w1/part.blend")})

    def test_a_chat_used_after_the_move_resumes_its_new_native_context(self):
        self.seed()
        self.carry()
        sessions = SessionStore(self.new, self.session)
        state = sessions.load()
        state.update(native_id="started-after-the-move", updated=time.time() + 1)
        sessions.save(state)
        self.assertEqual(SessionStore(self.new, self.session).load()["native_id"], "started-after-the-move")

    def test_the_newest_earlier_version_of_any_number_is_the_source(self):
        self.seed(self.product / "4.5/config/angelo-agent")
        self.seed(self.product / "5.1/config/angelo-agent")
        (self.product / "5.4/config/angelo-agent/sessions").mkdir(parents=True)   # Newer: never a source.
        (self.product / "5.2/config").mkdir(parents=True)                       # Holds no state: skipped.
        (self.product / "notes").mkdir()
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertEqual(carry.read_record(self.config)["from_version"], "5.1")
        self.assertTrue((self.product / "4.5/config/angelo-agent/sessions").is_dir(), "only the newest moves")
        self.assertTrue((self.product / "5.4/config/angelo-agent/sessions").is_dir())

    def test_nothing_found_is_no_verdict_an_earlier_state_found_later_still_comes(self):
        self.assertEqual(self.carry().status, "nothing")
        self.assertIsNone(carry.read_record(self.config))
        self.seed()   # A disk attached again, or Angelo 1.0 installed later.
        self.assertEqual(self.carry().status, "migrated")

    def test_explicit_folders_are_never_carried(self):
        self.seed()
        for variable in ("ANGELO_STATE_DIR", "BLENDER_USER_CONFIG", "BLENDER_USER_RESOURCES"):
            with self.subTest(variable=variable):
                self.assertEqual(self.carry({variable: "/elsewhere"}).status, "isolated")
        flat = Path(self.temp.name) / "config"
        self.assertEqual(carry.carry_over(flat, {}, others_running=ALONE).status, "nothing")
        self.assertTrue((self.old / "sessions").is_dir())
        self.assertFalse(self.config.exists())

    # Break-it cases ----------------------------------------------------------------------------------------------

    def test_a_crash_or_full_disk_mid_move_resumes_without_loss(self):
        self.seed()
        self.new.mkdir(parents=True)   # Per-entry moves, so the failure lands between renames.
        (self.new / "updates.json").write_text("{}")
        before = inventory(self.old)
        real, calls = folder_moves.move_entry, []

        def failing(source, target):
            calls.append(source)
            if len(calls) == 3:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)

        with patch.object(folder_moves, "move_entry", failing):
            stopped = self.carry()
        self.assertEqual(stopped.status, "failed")
        self.assertIn("Nothing was deleted", stopped.message)
        self.assertEqual(carry.read_record(self.config)["status"], "moving")
        merged = {**inventory(self.old), **inventory(self.new)}
        self.assertTrue(set(before) <= set(merged), "every file is in one of the two folders")
        with patch.object(carry_over_records, "update", return_value=(0, ["jobs.json"])):   # A full disk for records.
            self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(carry.read_record(self.config)["pending"], ["jobs.json"], "named until it is rewritten")
        self.assertEqual(self.carry().status, "nothing", "a later launch rewrites it quietly")
        self.assertEqual(carry.read_record(self.config)["status"], "done")
        self.assertEqual(carry.read_record(self.config)["pending"], [])
        self.assertMovedAway()
        after = inventory(self.new)
        for name in before:
            if not name.endswith(".json"):
                self.assertEqual(after[name], before[name], name)
        self.assertEqual(SessionStore(self.new, self.session).load()["task_memory"]["brief"]["objective"],
                         "Keep the earlier version's brief")

    def test_a_full_disk_before_the_first_rename_moves_nothing(self):
        self.seed()
        before = inventory(self.old)
        with patch.object(carry, "write_record", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            result = self.carry()
        self.assertEqual(result.status, "failed")
        self.assertEqual(inventory(self.old), before)
        self.assertFalse(self.new.exists())

    def test_an_earlier_partial_copy_is_merged_and_the_old_side_of_a_clash_is_kept(self):
        """Blender's own import ran first (from a build without this module) and stopped part-way, then that build
        wrote a new chat and changed one file."""
        self.seed()
        shutil.copytree(self.old / "sessions", self.new / "sessions")
        shutil.copy2(self.old / "conversations.json", self.new / "conversations.json")
        (self.new / "intro.json").write_text(json.dumps({"seen": True, "choice": "tour"}))
        newer = SessionStore(self.new, str(uuid.uuid4()))
        newer_state = newer.new("codex_subscription")
        newer.save(newer_state)
        chats = SessionCollection(self.new)
        chats.update(newer_state)
        chats.save()
        listed = set(SessionCollection(self.old).records)
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertIn("earlier copies replaced by their originals", result.message)
        self.assertEqual(json.loads((self.new / "intro.json").read_text())["choice"], "tour", "the newer side wins")
        kept = self.new / "kept-from-5.2"
        self.assertEqual(json.loads((kept / "intro.json").read_text())["choice"], "skip", "never lost")
        self.assertEqual(set(SessionCollection(self.new).records), listed | {newer_state["id"]},
                         "both versions' chats are listed")
        self.assertTrue((self.new / "checkpoints").is_dir())
        self.assertMovedAway()

    def test_another_angelo_open_defers_and_this_session_uses_the_earlier_folder(self):
        self.seed()
        before = inventory(self.old)
        deferred = self.carry(others=lambda: True)
        self.assertEqual(deferred.status, carry.DEFERRED)
        self.assertIn("Quit every Angelo", deferred.message)
        self.assertEqual(carry.deferred_source, self.old, "this session works on the earlier folder in place")
        self.assertEqual(inventory(self.old), before)
        self.assertIsNone(carry.read_record(self.config))
        self.assertFalse(self.new.exists(), "the new folder gains no state of its own")
        self.assertEqual(self.carry().status, "migrated")

    def test_a_deferred_launch_works_on_the_earlier_folder_in_this_process_only(self):
        self.seed()
        with patch.object(carry, "_others_running", lambda: True), patch.object(carry, "last_result", None), \
                patch.object(state_paths, "session_redirect", None):
            result = carry.carry_over_on_startup(self.config)
            self.assertEqual(result.status, carry.DEFERRED)
            self.assertEqual(state_paths.session_redirect, self.old)
            self.assertNotIn("ANGELO_STATE_DIR", os.environ, "never carried to a child or an updated app")
            self.assertTrue(state_paths.earlier_state_folders(self.old, {}), "the default folder's behaviour stays")
            again = carry.carry_over_on_startup(self.config)   # A preference read registers the add-on again.
            self.assertEqual(again.status, carry.DEFERRED, "the launch's own result is kept for its notice")
        self.assertEqual(self.carry().status, "migrated", "the next launch with no other Angelo moves it all")

    def test_registering_again_keeps_this_launchs_result(self):
        self.seed()
        with patch.object(carry, "_others_running", ALONE), patch.object(carry, "last_result", None):
            self.assertEqual(carry.carry_over_on_startup(self.config).status, "migrated")
            self.assertEqual(carry.carry_over_on_startup(self.config).status, "migrated")
            self.assertEqual(carry.carry_over(self.config, {}, ALONE).status, "nothing")

    def test_only_another_angelo_counts_never_this_ones_children(self):
        rows = [(1, 0, "launchd"), (500, 1, "Angelo"), (501, 500, "Angelo"), (502, 501, "codex"), (503, 500, "Angelo")]
        self.assertFalse(processes.others_of(rows, "/Applications/Angelo.app/Contents/MacOS/Angelo", 500),
                         "Cycles' GPU listing and workers are this Angelo's own")
        self.assertTrue(processes.others_of(rows + [(900, 1, "Angelo")], "/x/Angelo.app/Contents/MacOS/Angelo", 500))
        windows = [(4, 0, "System"), (10, 4, "Angelo.exe"), (11, 4, "ANGELO.EXE"), (12, 4, "Angelo-launcher.exe")]
        self.assertTrue(processes.others_of(windows, "C:\\Program Files\\Angelo\\Angelo.exe", 10))
        self.assertFalse(processes.others_of(windows[:1] + windows[2:3], "C:\\Program Files\\Angelo\\Angelo.exe", 11))
        bpy = type(sys)("bpy")
        bpy.app = type("App", (), {"binary_path": "/Applications/Angelo.app/Contents/MacOS/Angelo"})()
        with patch.dict(sys.modules, {"bpy": bpy}):
            with patch.object(processes, "process_table", side_effect=OSError("The process list read as empty")):
                self.assertTrue(carry._others_running(), "an unreadable list counts as another Angelo")
            with patch.object(processes, "process_table", return_value=[(os.getpid(), 1, "Angelo")]):
                self.assertFalse(carry._others_running())
        self.assertTrue(any(pid == os.getpid() for pid, _parent, _name in processes.process_table()),
                        "this platform's list includes this process")

    def test_a_move_already_under_way_finishes_with_renames_only_while_another_angelo_runs(self):
        self.seed()
        self.new.mkdir(parents=True)
        (self.new / "intro.json").write_text("{}")
        real, calls = folder_moves.move_entry, []

        def stop_once(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError(errno.ENOSPC, "No space left on device")
            real(source, target)
        with patch.object(folder_moves, "move_entry", stop_once):
            self.assertEqual(self.carry().status, "failed")
        with patch.object(folder_moves, "move_entry", side_effect=OSError(errno.EXDEV, "cross")):
            self.assertEqual(self.carry(others=lambda: True).status, "failed", "never copied while another runs")
        self.assertEqual(self.carry(others=lambda: True).status, "migrated")

    def test_two_first_launches_at_once_move_once(self):
        self.seed()
        self.config.mkdir(parents=True)
        with carry.exclusive(self.config / carry.LOCK, 1), patch.object(carry, "LOCK_WAIT", 0.2):
            self.assertEqual(self.carry().status, carry.DEFERRED)
        self.assertTrue((self.old / "sessions").is_dir())
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(self.carry().status, "nothing")

    def test_links_move_as_links_and_are_never_followed(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("not Angelo's")
        self.seed()
        (self.old / "workspace/linked").symlink_to(outside, target_is_directory=True)
        self.new.mkdir(parents=True)
        (self.new / "workspace").mkdir()
        (self.new / "workspace/linked").symlink_to(Path(self.temp.name), target_is_directory=True)
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(link_text(self.new / "kept-from-5.2/workspace/linked"), str(outside))
        self.assertEqual(link_text(self.new / "workspace/linked"), str(Path(self.temp.name)))
        self.assertEqual((outside / "secret.txt").read_text(), "not Angelo's")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["secret.txt"])

    def test_a_linked_state_folder_moves_as_its_link(self):
        elsewhere = Path(self.temp.name) / "big-disk/angelo-agent"
        self.seed(elsewhere)
        self.old_config.mkdir(parents=True)
        self.old.symlink_to(elsewhere, target_is_directory=True)
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue(self.new.is_symlink())
        self.assertEqual(link_text(self.new), str(elsewhere))
        self.assertTrue((elsewhere / "sessions").is_dir())

    def test_native_sign_in_profiles_never_move_and_keep_resolving(self):
        self.seed()
        identity = str(uuid.uuid4())
        for family in state_paths.NATIVE_PROFILES:
            (self.old / family / identity).mkdir(parents=True)
            (self.old / family / identity / "auth.json").write_text("{}")
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertEqual(len(result.retained), 2)
        self.assertEqual(sorted(p.name for p in self.old.iterdir() if p.name != "intro.json"), sorted(
            state_paths.NATIVE_PROFILES +
                                                                               (carry.NOTE_NAME,)))
        for family in state_paths.NATIVE_PROFILES:
            self.assertEqual(storage.native_profile_directory(self.new, family, identity), self.old / family / identity)
        self.assertEqual(storage.native_profile_directory(self.new, "claude-profile", "new-one"),
                         self.new / "claude-profile/new-one")
        self.assertEqual(self.carry().status, "nothing", "a folder holding only retained profiles is not a source")
        self.assertFalse(carry.holds_state(self.old))

    def test_a_retained_pre_rename_profile_of_an_earlier_version_keeps_resolving(self):
        legacy = self.old_config / state_paths.LEGACY_STATE_FOLDER / "codex-profile/codex_api"
        legacy.mkdir(parents=True)
        self.seed()
        self.carry()
        self.assertEqual(storage.native_profile_directory(self.new, "codex-profile", "codex_api"), legacy)

    def test_a_brief_from_any_other_profile_is_still_dropped(self):
        self.seed()
        sessions = SessionStore(self.old, self.session)
        state = sessions.load()
        state["task_memory"]["scope"]["profile"] = scope_token("/some/other/profile")
        sessions.save(state)
        self.carry()
        self.assertEqual(SessionStore(self.new, self.session).load()["task_memory"]["brief"]["objective"], "")

    def test_windows_paths_use_the_extended_length_form(self):
        with patch.object(carry_over_moves, "os") as fake:
            fake.name, fake.path = "nt", ntpath
            deep = "C:\\Users\\a\\AppData\\Roaming\\Angelo\\5.3\\config\\angelo-agent\\" + "x\\" * 140 + "f.bin"
            self.assertEqual(carry_over_moves.long_path(deep), "\\\\?\\" + deep)
            self.assertEqual(carry_over_moves.long_path("\\\\server\\share\\a"), "\\\\?\\UNC\\server\\share\\a")
            self.assertEqual(carry_over_moves.long_path("\\\\?\\C:\\a"), "\\\\?\\C:\\a")

    def test_every_filesystem_call_of_a_move_uses_the_long_form(self):
        """No plain path reaches the operating system: ``long_path`` gives an equivalent but different spelling
        (Windows' own ``\\\\?\\`` form; on POSIX, where it changes nothing, a ``//`` stand-in), and every call the
        merge makes must receive that spelling."""
        self.seed()
        shutil.copytree(self.old / "sessions", self.new / "sessions")
        (self.new / "intro.json").write_text("{}")
        names = ("lstat", "listdir", "rename", "replace", "unlink", "link", "makedirs", "rmdir")
        seen, real = [], {name: getattr(os, name) for name in names}

        def spy(name):
            def call(path, *args, **kwargs):
                seen.append((name, os.fspath(path)))
                if name in {"rename", "replace", "link"}:
                    seen.append((name, os.fspath(args[0])))
                return real[name](path, *args, **kwargs)
            return call
        if os.name == "nt":   # "//C:\\..." names no file there: the real form, which the product sends.
            spelled, prefix = carry_over_moves.long_path, "\\\\?\\"
        else:
            spelled, prefix = (lambda path: "//" + os.path.abspath(str(path)).lstrip("/")), "//"
        mover, top = carry_over_moves.Mover(self.new / "kept-from-5.2"), sorted(os.listdir(self.old))
        with patch.object(carry_over_moves, "long_path", spelled), patch.multiple(
                os, **{name: spy(name) for name in names}):
            mover.merge_top(self.old, self.new, top)
        self.assertGreater(mover.copies + mover.moved + mover.kept, 3)
        plain = [(name, path) for name, path in seen if not path.startswith(prefix)]
        self.assertEqual(plain, [])

    def test_r1_both_sides_with_their_own_snapshots_keep_each_store_whole(self):
        """A build without the carry-over used the new folder (its own chat and snapshot): the snapshot system is
        one unit, so the one modified last stays whole and the other is kept whole, never mixed."""
        record, data, _ = self.seed()
        old_chat, new_chat = self.session, str(uuid.uuid4())
        time.sleep(0.05)
        self.session = new_chat
        newer, newer_data, _ = self.seed(self.new)
        self.session = old_chat
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        kept = self.new / "kept-from-5.2"
        self.assertTrue((kept / "snapshot-store").is_dir() and (kept / "checkpoints").is_dir(), "one unit kept whole")
        units = {old_chat: (record, data), new_chat: (newer, newer_data)}
        here = {path.name for path in (self.new / "checkpoints").iterdir()}
        self.assertEqual(len(here & set(units)), 1, "never two stores mixed in one folder")
        for chat, (snapshot, content) in units.items():
            root = self.new if chat in here else kept
            store = CheckpointStore(root, chat)
            self.assertEqual(store.recovery_copy(snapshot["id"])[1].read_bytes(), content, "restores where it is")
        self.assertEqual(CheckpointStore(self.new, next(iter(here & set(units)))).inspect()["issues"], [])
        self.assertTrue(set(SessionCollection(self.new).records) >= {old_chat, new_chat}, "both chats are listed")

    def test_r2_a_finished_copy_gives_way_to_the_originals(self):
        record, data, _ = self.seed()
        shutil.copytree(self.old, self.new, symlinks=True)   # Blender's own copy_prev of the state (copy2).
        original = next(p for p in self.old.rglob("*.json") if "sessions" in p.parts)
        inode = original.stat().st_ino
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual((self.new / original.relative_to(self.old)).stat().st_ino, inode, "the original's identity")
        store = CheckpointStore(self.new, self.session)
        self.assertEqual(store.recovery_copy(record["id"])[1].read_bytes(), data)
        self.assertEqual(store.inspect()["issues"], [])
        self.assertFalse((self.new / "kept-from-5.2").exists(), "nothing clashed")

    def test_r3_an_interrupted_copy_of_a_chat_gives_way_to_the_whole_one(self):
        self.seed()
        (self.new / "sessions").mkdir(parents=True)
        source = self.old / "sessions" / (self.session + ".json")
        raw = source.read_bytes()
        (self.new / "sessions" / source.name).write_bytes(raw[: len(raw) // 2])
        self.assertEqual(self.carry().status, "migrated")
        state = SessionStore(self.new, self.session).load()
        self.assertEqual(state["messages"][0]["text"], "Look at this")
        self.assertEqual((self.new / "sessions" / source.name).read_bytes(), raw)

    def test_r4_a_chat_continued_in_the_earlier_version_after_a_copy_wins(self):
        self.seed()
        shutil.copytree(self.old, self.new, symlinks=True)
        sessions = SessionStore(self.old, self.session)
        state = sessions.load()
        state["messages"].append({"role": "user", "text": "Later, in Angelo 1.0"})
        state["updated"] = time.time()
        sessions.save(state)
        self.assertEqual(self.carry().status, "migrated")
        texts = [m.get("text") for m in SessionStore(self.new, self.session).load()["messages"]]
        self.assertIn("Later, in Angelo 1.0", texts)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, UNREADABLE)
    def test_r5_an_unreadable_earlier_folder_is_never_taken_for_none(self):
        self.seed()
        os.chmod(self.product, 0o300)
        try:
            result = self.carry()
        finally:
            os.chmod(self.product, 0o700)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(carry.read_record(self.config))
        self.assertEqual(self.carry().status, "migrated")

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, UNREADABLE)
    def test_an_unreadable_record_is_never_taken_for_a_damaged_one(self):
        self.seed()
        self.carry()
        (self.old / "sessions").mkdir(parents=True)   # Angelo 1.0 used again after the move.
        os.chmod(self.config / carry.RECORD, 0)
        try:
            self.assertEqual(self.carry().status, "failed")
        finally:
            os.chmod(self.config / carry.RECORD, 0o600)
        self.assertFalse(list(self.config.glob(carry.RECORD + ".damaged-*")))
        self.assertEqual(self.carry().status, "nothing", "once per version")
        self.assertTrue((self.old / "sessions").is_dir())

    def test_a_newer_angelos_record_is_left_alone(self):
        self.seed()
        self.config.mkdir(parents=True)
        newer = json.dumps({"version": carry.RECORD_VERSION + 1, "status": "merged", "into": str(self.new)})
        (self.config / carry.RECORD).write_text(newer)
        self.assertEqual(self.carry().status, "nothing")
        self.assertEqual((self.config / carry.RECORD).read_text(), newer)
        self.assertTrue((self.old / "sessions").is_dir())

    def test_a_linked_user_folder_path_finishes(self):
        real = Path(self.temp.name) / "real-home"
        (real / "Angelo").mkdir(parents=True)
        linked = Path(self.temp.name) / "linked-home"
        linked.symlink_to(real, target_is_directory=True)
        self.product = linked / "Angelo"
        self.old_config, self.config = self.product / "5.2/config", self.product / "5.3/config"
        self.old, self.new = self.old_config / "angelo-agent", self.config / "angelo-agent"
        record, data, _ = self.seed()
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(CheckpointStore(self.new.resolve(), self.session).recovery_copy(record["id"])[1].read_bytes(),
                         data)
        self.assertIsNotNone(state_paths.carry_over_record(self.new), "the record is found by either spelling")

    def test_a_store_started_afresh_never_hides_the_whole_history(self):
        record, data, _ = self.seed()
        (self.new / "checkpoint-seal").mkdir(parents=True)
        (self.new / "checkpoint-seal/key").write_bytes(os.urandom(32))   # Newer, but nearly empty.
        self.assertEqual(self.carry().status, "migrated")
        self.assertEqual(CheckpointStore(self.new, self.session).recovery_copy(record["id"])[1].read_bytes(), data)
        self.assertTrue((self.new / "kept-from-5.2/checkpoint-seal/key").is_file())

    def test_a_chat_that_was_already_here_keeps_its_native_context(self):
        self.seed()
        here = SessionStore(self.new, str(uuid.uuid4()))
        state = here.new("claude_subscription")
        state.update(native_id="started-here", updated=time.time() - 30)
        here.save(state)
        self.carry()
        self.assertEqual(SessionStore(self.new, state["id"]).load()["native_id"], "started-here")
        self.assertIsNone(SessionStore(self.new, self.session).load()["native_id"])

    def test_r6_a_damaged_record_is_kept_aside_and_the_move_finishes(self):
        self.seed()
        self.config.mkdir(parents=True)
        (self.config / carry.RECORD).write_text('{"status": "movi')
        self.assertEqual(self.carry().status, "migrated")
        self.assertTrue(list(self.config.glob(carry.RECORD + ".damaged-*")))

    def test_r7_a_linked_folder_and_an_existing_kept_folder(self):
        elsewhere = Path(self.temp.name) / "big/angelo-agent"
        self.seed(elsewhere)
        self.old_config.mkdir(parents=True)
        self.old.symlink_to(elsewhere, target_is_directory=True)
        (self.new / "kept-from-5.2").mkdir(parents=True)
        (self.new / "conversations.json").write_text(json.dumps({"version": 1, "sessions": []}))
        result = self.carry()
        self.assertEqual(result.status, "migrated", result.message)
        self.assertIn(self.session, SessionCollection(self.new).records)

    def test_r8_a_linked_folder_merges_into_existing_state_through_its_link(self):
        elsewhere = Path(self.temp.name) / "big/angelo-agent"
        record, data, _ = self.seed(elsewhere)
        self.old_config.mkdir(parents=True)
        self.old.symlink_to(elsewhere, target_is_directory=True)
        self.new.mkdir(parents=True)
        (self.new / "conversations.json").write_text(json.dumps({"version": 1, "sessions": []}))
        self.assertEqual(self.carry().status, "migrated")
        self.assertIn(self.session, SessionCollection(self.new).records)
        self.assertEqual(CheckpointStore(self.new, self.session).recovery_copy(record["id"])[1].read_bytes(), data)
        self.assertTrue(self.old.is_symlink(), "the link itself is left as it was")

    def test_a_move_to_another_disk_is_copied_verified_then_removed(self):
        record, data, _ = self.seed()
        before = inventory(self.old)
        real, calls = folder_moves.move_entry, []

        def cross(source, target):
            calls.append(source)
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        with patch.object(folder_moves, "move_entry", cross):
            self.assertEqual(self.carry().status, "migrated")
        self.assertTrue(calls)
        self.assertMovedAway()
        after = inventory(self.new)
        for name, content in before.items():
            if not name.endswith(".json"):
                self.assertEqual(after[name], content, name)
        self.assertFalse(list(self.config.rglob(carry_over_moves.CARRYING + "*")))
        self.assertEqual(CheckpointStore(self.new, self.session).recovery_copy(record["id"])[1].read_bytes(), data)
        self.assertIs(folder_moves.move_entry, real)

    def test_a_place_inside_a_retained_profile_is_never_rewritten(self):
        self.seed()
        (self.old / "codex-profile/x").mkdir(parents=True)
        (self.old / "pointers.json").write_text(json.dumps({"profile": str(self.old / "codex-profile/x"),
                                                            "work": str(self.old / "workspace")}))
        self.carry()
        moved = json.loads((self.new / "pointers.json").read_text())
        self.assertEqual(moved, {"profile": str(self.old / "codex-profile/x"), "work": str(self.new / "workspace")})

    # Beside the move ---------------------------------------------------------------------------------------------

    def test_the_settings_copy_leaves_the_state_and_its_records_out(self):
        self.seed()
        (self.old_config / state_paths.CARRY_OVER_RECORD).write_text("{}")
        (self.old_config / carry.LOCK).write_text("")
        (self.old_config / "startup.blend").write_bytes(b"BLENDER startup")
        # A Vulkan start that failed with the earlier version's build and driver says nothing about this one.
        (self.old_config / settings_import.GPU_STARTUP_RECORD).write_text("vulkan failed\n")
        (self.old.parent.parent / settings_import.BACKUP_FOLDER).mkdir()
        (self.old.parent.parent / "scripts/addons").mkdir(parents=True)
        target = Path(self.temp.name) / "copy"
        root = self.old.parent.parent
        shutil.copytree(root, target, symlinks=True, ignore=settings_import.previous_ignore(root))
        self.assertEqual(sorted(p.name for p in (target / "config").iterdir()), ["startup.blend", "userpref.blend"])
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["config", "scripts"])

    def test_earlier_state_folders_name_every_place_the_state_lived(self):
        (self.product / "5.1").mkdir(parents=True)
        (self.product / "5.2").mkdir(parents=True)
        (self.product / "6.0").mkdir(parents=True)
        self.config.mkdir(parents=True)
        moved = Path(self.temp.name).resolve() / "moved/from/angelo-agent"   # A full path on every platform.
        (self.config / state_paths.CARRY_OVER_RECORD).write_text(json.dumps(
            {"status": "done", "from": str(moved), "into": str(self.new), "at": 5.0, "chats": ["c1"]}))
        found = state_paths.earlier_state_folders(self.new, {})
        self.assertEqual(found, [self.config / "toonlab-agent",   # This version's pre-rename folder first.
                                 moved, moved.parent / "toonlab-agent",
                                 self.old, self.old_config / "toonlab-agent",
                                 self.product / "5.1/config/angelo-agent", self.product / "5.1/config/toonlab-agent"])
        self.assertEqual(state_paths.carried_chats(self.new), {"c1": 5.0})
        self.assertEqual(state_paths.earlier_state_folders(self.new, {"ANGELO_STATE_DIR": "/x"}), [])


class UpdateNoticeTests(unittest.TestCase):
    """The first launch's one notice (``version_carry_over_ui.notice_text``) for every outcome."""

    def setUp(self):
        bpy = type(sys)("bpy")
        bpy.context = type("Context", (), {"preferences": None})()
        bpy.app = type("App", (), {"handlers": type("Handlers", (), {"exit_pre": [], "persistent": staticmethod(
            lambda function: function)})()})()
        patcher = patch.dict(sys.modules, {"bpy": bpy, "addon_utils": type(sys)("addon_utils")})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(ROOT / "scripts/modules"))
        for name in ("angelo_agent.version_carry_over_ui", "angelo_agent.settings_import_apply"):
            sys.modules.pop(name, None)
            self.addCleanup(sys.modules.pop, name, None)
        from angelo_agent import version_carry_over_ui
        self.ui = version_carry_over_ui
        patch.object(carry, "last_kept_folder", None).start()
        self.addCleanup(patch.stopall)

    def notice(self, status, outcome=None):
        return self.ui.notice_text(folder_moves.MigrationResult(status, "details"), outcome)

    def test_each_outcome_says_what_happened(self):
        imported = {"app": "Angelo", "version": "5.2", "failed": [], "errors": []}
        icon, title, body, calm = self.notice("migrated", imported)
        self.assertEqual((icon, calm), ("check", True))
        self.assertIn("is ready", title)
        self.assertEqual(body, self.ui.READY_ALL)
        self.assertEqual(self.notice("migrated")[2], self.ui.READY_CHATS)
        self.assertEqual(self.notice("nothing", imported)[2], self.ui.READY_SETTINGS)
        self.assertIsNone(self.notice("nothing"), "a launch with nothing to bring says nothing")
        self.assertIsNone(self.notice("isolated"))
        self.assertEqual(self.notice(carry.DEFERRED, imported)[1:3], (self.ui.WAITING_TITLE, self.ui.WAITING_BODY))
        self.assertEqual(self.notice("failed")[1:3], (self.ui.STOPPED_TITLE, self.ui.STOPPED_BODY))
        self.assertEqual(self.notice("migrated", dict(imported, exception="boom"))[0], "alert")

    def test_kept_items_and_add_ons_that_did_not_load_are_named(self):
        carry.last_kept_folder = "kept-from-5.2"
        icon, _title, body, calm = self.notice("migrated", {"failed": ["Broken Tool"], "errors": []})
        self.assertEqual((icon, calm), ("alert", False))
        self.assertIn("kept-from-5.2", body)
        self.assertIn("Broken Tool", body)


if __name__ == "__main__":
    unittest.main()
