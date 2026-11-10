# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A headless session's save_post handlers name the file the save wrote, never a stat of its path afterwards.

Regressions (release-1010 break-it after 922426cda8f, M1; audit re-check 3, P3 and L2):

* ``HeadlessHost.track_in_process_save`` and ``open_read``'s save note took ``identity(path)`` after the save's
  rename, so a version another program put at the name in between became the session's: an unedited-copy removal
  then deleted it, or the next save replaced it unnoticed;
* the note counted a save of any file, not only the one being opened;
* ``headless_files.place`` named the bytes it wrote by a stat of the temporary name after the close: a file put at
  that name in between was placed and taken for the session's.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import headless_files  # noqa: E402
from angelo_agent import headless_working_copy as working_copy  # noqa: E402
from angelo_agent.headless_host import HeadlessHost  # noqa: E402
from save_identity_stand_in import saving  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402


def replace(path, data):   # A save by Blender or another program: a new file renamed over the name.
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


class SavedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="angelo-saved-id-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.output = self.root / "scene.angelo-20261105-010000.blend"
        self.output.write_bytes(b"BLENDER the session's output")

    def opening(self, during_open):
        handlers = SimpleNamespace(persistent=lambda function: function, save_post=[])

        def open_mainfile(filepath, load_ui, use_scripts):
            during_open(Path(filepath), handlers)
            return {"FINISHED"}
        return SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(open_mainfile=open_mainfile)),
                               app=SimpleNamespace(handlers=handlers))

    def test_a_version_put_at_the_name_after_an_add_on_s_save_during_the_open_reads_as_a_change(self):
        def add_on_saves_then_another_program_writes(path, handlers):
            replace(path, b"BLENDER as Blender holds it, saved")
            with saving(path):   # The save names the file it wrote; then a sync client restores another version.
                replace(path, b"BLENDER another program's version")
                for handler in list(handlers.save_post):
                    handler(str(path))
        identity = working_copy.open_read(self.opening(add_on_saves_then_another_program_writes), self.output)
        self.assertNotEqual(identity, headless_files.identity(self.output), "the other version is never vouched for")
        self.assertFalse(working_copy.discard(self.output, self.output.with_name("scene.blend"), identity))
        self.assertEqual(self.output.read_bytes(), b"BLENDER another program's version", "never removed")

    def test_a_save_of_another_file_during_the_open_vouches_for_nothing(self):
        other = self.root / "backup.blend"

        def add_on_saves_a_copy_elsewhere(path, handlers):
            other.write_bytes(b"BLENDER a backup")
            replace(path, b"BLENDER another program's version")   # Not this process's save of the file.
            with saving(other):
                for handler in list(handlers.save_post):
                    handler(str(other))
        identity = working_copy.open_read(self.opening(add_on_saves_a_copy_elsewhere), self.output)
        self.assertNotEqual(identity, headless_files.identity(self.output))

    def test_the_open_is_identified_by_the_file_blender_read(self):
        """Release-1010 break-it (M7): a version put at the path and moved away again around the read passed the stats
        before and after the open; the load now names the file it read (``_bpy._angelo_saved.read_file``)."""
        import types
        other = self.root / "other.blend"
        other.write_bytes(b"BLENDER read instead")
        read_as = os.stat(other)
        native = types.ModuleType("_bpy")
        native._angelo_saved = SimpleNamespace(read_file=lambda: (str(self.output), read_as.st_dev, read_as.st_ino,
                                                                  read_as.st_size, read_as.st_mtime_ns, 0))
        with patch.dict(sys.modules, {"_bpy": native}):
            identity = working_copy.open_read(self.opening(lambda _path, _handlers: None), self.output)
        self.assertEqual(identity, headless_files.settled_identity(read_as), "the file read")
        self.assertNotEqual(identity, headless_files.identity(self.output), "reads as changed: never removed")

    def test_an_in_process_save_of_the_working_copy_takes_the_identity_the_save_gave(self):
        source = self.root / "scene.blend"
        source.write_bytes(b"BLENDER the person's scene")
        host = SimpleNamespace(working=self.output, source=source, output=self.output,
                               working_identity=None, output_identity=None,
                               bpy=SimpleNamespace(data=SimpleNamespace(filepath=str(self.output))))
        replace(self.output, b"BLENDER saved by the person's add-on")
        written = headless_files.identity(self.output)
        with saving(self.output):
            replace(self.output, b"BLENDER another program's version")   # Between the rename and the handler.
            HeadlessHost.track_in_process_save(host, str(self.output))
        self.assertEqual(host.working_identity, written, "the file the save wrote")
        self.assertEqual(host.output_identity, written)
        self.assertNotEqual(host.working_identity, headless_files.identity(self.output))

    @unittest.skipIf(os.name == "nt", environment_skip("place renames onto the claimed name: POSIX semantics"))
    def test_a_file_put_at_the_temporary_name_before_the_rename_is_never_placed(self):
        staged, target = self.root / "staged.blend", self.root / "scene.angelo-out.blend"
        staged.write_bytes(b"BLENDER the session's save")
        target.write_bytes(b"")
        fsync, theirs = os.fsync, self.root / "theirs.blend"
        theirs.write_bytes(b"BLENDER another program's file")

        def then_theirs_lands(descriptor):
            fsync(descriptor)
            temporary = next(self.root.glob(".scene.angelo-out.blend.*.tmp"))
            os.replace(theirs, temporary)
        with patch.object(headless_files.os, "fsync", then_theirs_lands), \
                self.assertRaises(headless_files.FileProblem):
            headless_files.place(staged, target)
        self.assertEqual(target.read_bytes(), b"", "nothing placed")
        left = list(self.root.glob(".scene.angelo-out.blend.*.tmp"))
        self.assertEqual([path.read_bytes() for path in left], [b"BLENDER another program's file"],
                         "another program's file is never removed")


    def test_a_placed_file_a_rename_gives_another_number_is_still_the_one_written(self):
        """Break-it (M5): Windows FAT and exFAT number a file by its directory entry, so the rename gave the placed
        file another number than its stat before it: every headless save then read its own output as changed."""
        staged, target = self.root / "staged.blend", self.root / "scene.angelo-out.blend"
        staged.write_bytes(b"BLENDER the session's save")
        rename, fstat = headless_files.shared_replace.replace, os.fstat
        renumbered = [None]

        def descriptor_stat(descriptor):
            info = fstat(descriptor)
            if renumbered[0] and (info.st_dev, info.st_ino) == renumbered[0][:2]:
                return renumbered[0][2]   # A FAT-style descriptor observes its file's new number too.
            return info

        def renumbering(source, destination, later=None):   # The same bytes and time under another number.
            rename(source, destination)
            info, fresh = os.stat(destination), Path(str(destination) + ".fresh")
            fresh.write_bytes(Path(destination).read_bytes() if later is None else later)
            os.utime(fresh, ns=(info.st_atime_ns, info.st_mtime_ns))
            rename(fresh, destination)   # As the product renames (Windows: over the save's held, delete-sharing handle).
            renumbered[0] = (info.st_dev, info.st_ino, os.stat(destination)) if later is None else None
        fsync = os.fsync

        def whole_microseconds(descriptor):   # A time the copy keeps exactly (macOS sets times in microseconds).
            fsync(descriptor)
            if os.utime in os.supports_fd:   # Windows keeps a time to 100 ns, as a copy's time does.
                stamp = os.fstat(descriptor).st_mtime_ns // 1000 * 1000
                os.utime(descriptor, ns=(stamp, stamp))
        with patch.object(headless_files, "WINDOWS", True), patch.object(headless_files.shared_replace, "replace", renumbering), \
                patch.object(headless_files.os, "fsync", whole_microseconds), \
                patch.object(headless_files.os, "fstat", side_effect=descriptor_stat):
            written = headless_files.place(staged, target)
            self.assertEqual(written, headless_files.identity(target), "its own file: equal")
            renumbered[0] = None
            other = self.root / "scene.angelo-out-2.blend"   # A new name: a rename never replaces on Windows.
            with patch.object(headless_files.shared_replace, "replace",
                              lambda source, destination: renumbering(source, destination, b"BLENDER theirs")):
                written = headless_files.place(staged, other)
        self.assertNotEqual(written, headless_files.identity(other), "another program's bytes read as a change")

    def replaced_with_the_same_stamp(self, deny_read=False):
        staged, target = self.root / "staged.blend", self.root / "scene.angelo-out.blend"
        staged.write_bytes(b"BLENDER " + b"a" * 16)
        other = b"BLENDER " + b"b" * 16
        rename = headless_files.shared_replace.replace

        def then_replaced(source, destination):
            rename(source, destination)
            info = os.stat(destination)
            fresh = self.root / "another.blend"
            fresh.write_bytes(other)
            os.utime(fresh, ns=(info.st_atime_ns, info.st_mtime_ns))
            rename(fresh, destination)
            self.assertNotEqual(os.stat(destination).st_ino, info.st_ino)
            self.assertEqual(os.stat(destination).st_size, info.st_size)
            self.assertEqual(os.stat(destination).st_mtime_ns, info.st_mtime_ns)

        opener, fsync = headless_files.shared_replace.open_shared, os.fsync

        def copyable_stamp(descriptor):
            fsync(descriptor)
            if os.utime in os.supports_fd:
                stamp = os.fstat(descriptor).st_mtime_ns // 1000 * 1000
                os.utime(descriptor, ns=(stamp, stamp))

        with patch.object(headless_files, "WINDOWS", True), \
                patch.object(headless_files.os, "fsync", side_effect=copyable_stamp), \
                patch.object(headless_files.shared_replace, "replace", side_effect=then_replaced) as placed, \
                patch.object(headless_files.shared_replace, "open_shared",
                             side_effect=PermissionError("owner cannot read") if deny_read else opener) as opened:
            written = headless_files.place(staged, target)
        placed.assert_called_once()
        self.assertEqual(target.read_bytes(), other)
        self.assertNotEqual(written, headless_files.identity(target), "matching size and time are not ownership")
        opened.assert_called_once()

    def test_same_size_and_time_replacement_never_becomes_the_written_file(self):
        self.replaced_with_the_same_stamp()

    def test_permission_fallback_never_accepts_a_different_inode_with_the_same_stamp(self):
        self.replaced_with_the_same_stamp(deny_read=True)

    def test_a_platform_without_nonblock_can_hold_its_published_file(self):
        staged, target = self.root / "staged.blend", self.root / "scene.angelo-out.blend"
        staged.write_bytes(b"BLENDER the session's save")
        with patch.dict(vars(os)):
            vars(os).pop("O_NONBLOCK", None)
            written = headless_files.place(staged, target)
        self.assertEqual(written, headless_files.identity(target))

    def test_permission_fallback_still_identifies_its_unchanged_file(self):
        staged, target = self.root / "staged.blend", self.root / "scene.angelo-out.blend"
        staged.write_bytes(b"BLENDER the session's save")
        with patch.object(headless_files.shared_replace, "open_shared", side_effect=PermissionError("no read")):
            written = headless_files.place(staged, target)
        self.assertEqual(written, headless_files.identity(target))


if __name__ == "__main__":
    unittest.main()
