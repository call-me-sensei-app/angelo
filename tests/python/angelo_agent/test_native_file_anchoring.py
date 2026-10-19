# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic private-fixture races at actual file-effect boundaries."""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import checkpoint_filesystem
from angelo_agent.checkpoint_filesystem import anchored_files_supported
from angelo_agent import files, checkpoints
from angelo_agent.checkpoints import CheckpointStore
from angelo_agent.files import FileWorkspace


@unittest.skipUnless(anchored_files_supported(), "descriptor-relative workspace I/O required")
class FileAnchoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-file-anchoring-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.workspace = FileWorkspace(self.project)
        record, path = self.store.reserve()
        path.write_bytes(b"synthetic host scene")
        self.snapshot = self.store.complete(record["id"])

    def tearDown(self):
        self.temp.cleanup()

    def mutate(self, name, path="notes.md", content="agent write"):
        arguments = {"path": path}
        if name == "angelo_write_file":
            arguments["content"] = content
        return self.workspace.execute(name, arguments, self.store, self.snapshot["id"])

    def assert_empty_journal(self):
        self.assertEqual(self.store.load(self.snapshot["id"])["files"], [])
        self.assertEqual(self.store.inspect()["issues"], [])

    def test_parent_symlink_swap_after_journal_refuses_write_and_delete_without_escape(self):
        for name in ("angelo_write_file", "angelo_delete_file"):
            with self.subTest(name=name):
                inside = self.project / name
                inside.mkdir()
                (inside / "notes.md").write_text("inside original")
                outside = self.root / (name + "-outside")
                outside.mkdir()
                (outside / "notes.md").write_text("outside original")
                original = self.store.journal
                def swap(*args, **kwargs):
                    result = original(*args, **kwargs)
                    inside.rename(inside.with_name(inside.name + "-moved"))
                    inside.symlink_to(outside, target_is_directory=True)
                    return result
                with patch.object(self.store, "journal", side_effect=swap), self.assertRaises((ValueError, OSError)):
                    self.mutate(name, str(inside.relative_to(self.project) / "notes.md"))
                self.assertEqual((outside / "notes.md").read_text(), "outside original")
                self.assertEqual((inside.with_name(inside.name + "-moved") / "notes.md").read_text(), "inside original")
                inside.unlink()
                self.assert_empty_journal()

    def test_read_symlink_swap_after_precondition_never_discloses_outside_bytes(self):
        target = self.project / "notes.md"
        target.write_text("inside")
        outside = self.root / "outside.txt"
        outside.write_text("outside private bytes")
        original = self.workspace.precondition
        def swap(*args, **kwargs):
            result = original(*args, **kwargs)
            target.unlink()
            target.symlink_to(outside)
            return result
        with patch.object(self.workspace, "precondition", side_effect=swap), self.assertRaises((ValueError, OSError)):
            self.workspace.execute("angelo_read_file", {"path": "notes.md", "start": 0, "length": 100})
        self.assertEqual(outside.read_text(), "outside private bytes")

    def test_failed_absent_publication_discards_journal_and_preserves_foreign_collision(self):
        target = self.project / "notes.md"
        def collision(folder, temporary, name):
            self.assertEqual(name, "notes.md")
            self.assertEqual(checkpoint_filesystem.directory_info(folder).st_ino, self.project.stat().st_ino)
            target.write_text("foreign collision")
            raise OSError("publication failed before any agent output")
        with patch.object(files, "publish_new_file", side_effect=collision), self.assertRaises(OSError):
            self.mutate("angelo_write_file")
        self.assert_empty_journal()
        self.assertEqual(self.store.restore_since(self.snapshot["id"]), 0)
        self.assertEqual(target.read_text(), "foreign collision")
        self.assertEqual(list(self.project.glob("*.tmp")), [])

    def test_actual_no_overwrite_publication_refuses_collision_created_during_staging(self):
        target = self.project / "notes.md"
        original = files.publish_new_file
        def collision(folder, temporary, name):
            target.write_text("foreign collision")
            return original(folder, temporary, name)
        with patch.object(files, "publish_new_file", side_effect=collision), self.assertRaises(FileExistsError):
            self.mutate("angelo_write_file")
        self.assert_empty_journal()
        self.assertEqual(target.read_text(), "foreign collision")

    def test_journal_committed_then_raised_is_cleaned_before_any_effect(self):
        original = self.store.journal
        def failed_acknowledgement(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("journal acknowledgement failed")
        with patch.object(self.store, "journal", side_effect=failed_acknowledgement), self.assertRaises(OSError):
            self.mutate("angelo_write_file")
        self.assertFalse((self.project / "notes.md").exists())
        self.assert_empty_journal()

    def test_failure_does_not_remove_an_earlier_applied_version(self):
        target = self.project / "notes.md"
        target.write_text("original")
        self.mutate("angelo_delete_file")
        before = self.store.load(self.snapshot["id"])["files"]
        with patch.object(files, "publish_new_file", side_effect=OSError("publish unavailable")), self.assertRaises(OSError):
            self.mutate("angelo_write_file")
        self.assertEqual(self.store.load(self.snapshot["id"])["files"], before)
        self.store.restore_since(self.snapshot["id"])
        self.assertEqual(target.read_text(), "original")

    def test_ambiguous_successful_publication_still_has_ready_recoverable_journal(self):
        target = self.project / "notes.md"
        original = files.publish_new_file
        def applied_then_failed(*args):
            original(*args)
            raise OSError("publication acknowledgement failed")
        with patch.object(files, "publish_new_file", side_effect=applied_then_failed), self.assertRaises(OSError) as caught:
            self.mutate("angelo_write_file")
        self.assertTrue(caught.exception.partial_changes_possible)
        self.assertEqual(target.read_text(), "agent write")
        self.assertEqual(self.store.load(self.snapshot["id"])["files"][0]["phase"], "ready")
        self.store.restore_since(self.snapshot["id"])
        self.assertFalse(target.exists())

    def test_temporary_creation_and_staging_fsync_failures_never_leave_restore_events(self):
        for failure in ("create", "fsync"):
            with self.subTest(failure=failure):
                if failure == "create":
                    original = files._open_file
                    def failed_open(folder, name, flags, mode=0o600):
                        if ".angelo-" in name:
                            raise OSError("temporary creation unavailable")
                        return original(folder, name, flags, mode)
                    manager = patch.object(files, "_open_file", side_effect=failed_open)
                else:
                    original = os.fsync
                    def failed_fsync(descriptor):
                        # Inject only the created file, never metadata/clock fds.
                        if os.fstat(descriptor).st_size == len(b"agent write"):
                            raise OSError("staging fsync unavailable")
                        return original(descriptor)
                    manager = patch.object(files.os, "fsync", side_effect=failed_fsync)
                with manager, self.assertRaises(OSError):
                    self.mutate("angelo_write_file")
                self.assert_empty_journal()
                self.assertFalse((self.project / "notes.md").exists())
                self.assertEqual(list(self.project.glob("*.tmp")), [])

    def test_direct_and_hidden_escape_paths_and_hardlinks_are_refused(self):
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        (self.project / "link.txt").symlink_to(outside)
        for path in ("../outside.txt", str(outside), ".env", "link.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.execute("angelo_read_file", {"path": path, "start": 0, "length": 100})
        os.link(outside, self.project / "hard.txt")
        with self.assertRaises(ValueError):
            self.workspace.execute("angelo_read_file", {"path": "hard.txt", "start": 0, "length": 100})

    def test_unsupported_anchored_platform_fails_before_journal_or_effect(self):
        with patch.object(os, "supports_dir_fd", set()), patch.object(checkpoint_filesystem, "_windows_api", return_value=None), \
                self.assertRaisesRegex(ValueError, "unavailable"):
            self.mutate("angelo_write_file")
        self.assert_empty_journal()
        self.assertFalse((self.project / "notes.md").exists())

    def test_normalized_relative_path_failure_is_also_cleaned(self):
        with patch.object(files, "publish_new_file", side_effect=OSError("publish unavailable")), self.assertRaises(OSError):
            self.mutate("angelo_write_file", "./notes.md")
        self.assert_empty_journal()


@unittest.skipUnless(os.name != "nt" and anchored_files_supported(), "POSIX descriptor-relative anchoring required")
class PrivateDirectoryAnchoringTests(unittest.TestCase):
    """The one-open macOS lookup (O_NOFOLLOW_ANY) and the per-component walk give the same outcomes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="angelo-anchor-", dir="/private/tmp" if Path("/private/tmp").is_dir() else None)
        self.root = Path(self.temp.name).resolve()
        (self.root / "real" / "child").mkdir(parents=True)
        (self.root / "real" / "file").write_text("not a folder")
        (self.root / "link").symlink_to(self.root / "real", target_is_directory=True)
        (self.root / "real" / "dangling").symlink_to(self.root / "missing", target_is_directory=True)

    def tearDown(self):
        self.temp.cleanup()

    def routes(self):
        yield "walk", patch.object(checkpoint_filesystem, "_NOFOLLOW_ANY", 0)
        if checkpoint_filesystem._NOFOLLOW_ANY:
            # The walk must not run for an existing folder on the one-open route.
            yield "one open", patch.object(checkpoint_filesystem, "_open_directory_at",
                                           side_effect=AssertionError("walked an existing folder"))

    def opened_identity(self, path, create=False, lookup_only=False):
        with checkpoint_filesystem._private_directory(path, create=create, lookup_only=lookup_only) as folder:
            info = os.fstat(folder)
            os.stat("..", dir_fd=folder)  # A lookup-only descriptor still anchors *at lookups.
            return info.st_dev, info.st_ino

    def test_links_and_files_in_any_component_are_refused_on_every_route(self):
        child = self.root / "real" / "child"
        for name, route in self.routes():
            with self.subTest(route=name), route:
                info = child.stat()
                self.assertEqual(self.opened_identity(child), (info.st_dev, info.st_ino))
                self.assertEqual(self.opened_identity(child, lookup_only=True), (info.st_dev, info.st_ino))
                for path in (self.root / "link" / "child", self.root / "link", self.root / "real" / "file",
                             self.root / "real" / "file" / "below", self.root / "real" / "dangling"):
                    for create in (False, True):
                        for lookup_only in (False, True):
                            with self.subTest(path=str(path), create=create, lookup_only=lookup_only), \
                                    self.assertRaises(ValueError):
                                self.opened_identity(path, create=create, lookup_only=lookup_only)
                self.assertFalse((self.root / "real" / "missing").exists())
                self.assertEqual(sorted(os.listdir(self.root / "real")), ["child", "dangling", "file"])

    def test_lexical_identity_check_gives_the_same_outcomes_with_a_search_only_lookup(self):
        child = self.root / "real" / "child"
        routes = [("read open", patch.object(checkpoint_filesystem, "_SEARCH", 0))]
        if checkpoint_filesystem._SEARCH:
            routes.append(("search only", patch.object(checkpoint_filesystem, "_private_directory",
                                                       side_effect=AssertionError("read-opened the path"))))
        held = os.open(child, os.O_RDONLY | os.O_DIRECTORY)
        other = os.open(self.root / "real", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name, route in routes:
                with self.subTest(route=name), route:
                    checkpoint_filesystem.assert_directory_identity(child, held)
                    with self.assertRaisesRegex(ValueError, "changed"):
                        checkpoint_filesystem.assert_directory_identity(child, other)
                    for path in (self.root / "link" / "child", self.root / "real" / "file", self.root / "real" / "dangling"):
                        with self.subTest(path=str(path)), self.assertRaises(ValueError):
                            checkpoint_filesystem.assert_directory_identity(path, held)
                    with self.assertRaises(FileNotFoundError):
                        checkpoint_filesystem.assert_directory_identity(self.root / "real" / "missing", held)
        finally:
            os.close(held)
            os.close(other)

    def test_missing_folders_raise_or_are_created_privately_on_every_route(self):
        for name, route in self.routes():
            missing = self.root / ("created-" + name.replace(" ", "-")) / "deeper"
            with self.subTest(route=name):
                with route, self.assertRaises(FileNotFoundError):
                    self.opened_identity(missing)
                self.assertFalse(missing.parent.exists())
                identity = self.opened_identity(missing, create=True)  # Creation always walks.
                info = missing.stat()
                self.assertEqual(identity, (info.st_dev, info.st_ino))
                self.assertEqual(info.st_mode & 0o777, 0o700)
                with route:
                    self.assertEqual(self.opened_identity(missing), identity)
                    self.assertEqual(self.opened_identity(missing, create=True), identity)


if __name__ == "__main__":
    unittest.main()
