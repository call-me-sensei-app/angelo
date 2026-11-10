# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A save's ``save_post`` handlers name the file it wrote as the save identified it, in the built app.

Run through ``run_native_suites.py --include test_blender_saved_file`` or an isolated Angelo with --background
--factory-startup --python-exit-code 1 --python <this file> -- <receipt.json>.

Blender writes ``<file>@`` and renames it over the file, then runs ``save_post``: a handler's stat of the path comes
after the rename, so a file another program put there in between (a sync client restoring an older copy) was taken
for the save (stat before read, release-1010 audit). The save identifies the finished temporary file before the
rename (``BlendFileWriteParams::written``) and hands it to its handlers (``_bpy._angelo_saved``, ``document_file_identity``):

* the identity is the one ``os.stat`` reports for the file written (device, file number, size, modification time,
  macOS birth time), and there is none outside a save's handlers or for another path;
* with the path replaced between the rename and the add-on's handlers, ``document_identity`` binds the document to
  the file the save wrote, never to the one put there (``test_native_document_identity`` holds the rule against a
  stand-in);
* with ``<file>@`` replaced by another program while Blender writes it (release-1010 audit re-check 3, P2: the file
  was held by its name after the writer closed it, and the other program's file was renamed over the document), the
  save is refused and every file on disk, version backups included, is left as it was;
* a save inside a ``save_post`` handler (a backup add-on) that writes the same path leaves the handlers after it the
  file now there, never the outer save's, which it replaced (break-it M2).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background instance"
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

RESULTS = {"cases": {}}


def fields(info):
    found = [int(info.st_dev), int(info.st_ino), int(info.st_size), int(info.st_mtime_ns)]
    if sys.platform == "darwin":   # os.stat's float birth time (macOS has no st_birthtime_ns).
        found.append(info.st_birthtime)
    return found


class SavedFile(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-saved-file-"))
        self.path = str(self.folder / "document.blend")
        self.seen = []

    def tearDown(self):
        for handler in list(bpy.app.handlers.save_post):
            if getattr(handler, "_saved_file_test", False):
                bpy.app.handlers.save_post.remove(handler)

    def handler(self, function, first=True):
        function._saved_file_test = True
        handler = bpy.app.handlers.persistent(function)
        if first:
            bpy.app.handlers.save_post.insert(0, handler)
        else:
            bpy.app.handlers.save_post.append(handler)

    def test_the_identity_is_the_written_file_s_as_os_stat_reports_it(self):
        from _bpy import _angelo_saved
        self.assertIsNone(_angelo_saved.saved_file(), "none outside a save's handlers")

        from angelo_agent import document_file_identity, document_identity
        same = []

        def record(filepath, *_args):
            self.seen.append((filepath, _angelo_saved.saved_file(), os.stat(filepath)))
            same.append((document_identity._identity_of(document_file_identity.written(filepath)),
                         document_identity.file_identity(filepath)))
        self.handler(record)
        bpy.ops.wm.save_as_mainfile(filepath=self.path)
        (filepath, native, info), = self.seen
        self.assertEqual(native[0], self.path)
        # The identity a save binds equals the one a later load reads of the same file (document_identity.same_file).
        self.assertEqual(same[0][0], same[0][1])
        self.assertTrue(document_identity.same_file(*same[0]))
        self.assertEqual(fields_of_native(native), fields(info), "the file written, in os.stat's values")
        RESULTS["cases"]["identity"] = {"native": list(native[1:]), "stat": fields(info)}
        self.assertIsNone(_angelo_saved.saved_file(), "none once its handlers ran")

    def test_a_file_put_at_the_path_after_the_rename_is_never_taken_for_the_save(self):
        from angelo_agent import document_file_identity, document_identity
        written, after = [], []

        def another_program(filepath, *_args):   # Runs first: replaces the file Blender just renamed into place.
            written.append(fields(os.stat(filepath)))
            other = self.folder / "restored.blend"
            other.write_bytes(Path(filepath).read_bytes())   # A copy: the same document, another file.
            os.replace(other, filepath)

        def addon(filepath, *_args):   # Runs last, as the add-on's handler does.
            found = document_file_identity.written(filepath)
            after.append((found, document_identity.file_identity(filepath)))
        self.handler(another_program, first=True)
        self.handler(addon, first=False)
        bpy.ops.wm.save_as_mainfile(filepath=self.path)
        (found, at_path), = after
        self.assertIsNotNone(found, "the save named the file it wrote")
        self.assertEqual(fields(found), written[0], "the file the save wrote")
        self.assertNotEqual(document_identity._identity_of(found), at_path, "not the file put there after it")
        RESULTS["cases"]["replaced"] = {"written": written[0], "at_path": at_path,
                                        "bound": document_identity._identity_of(found)}

    def test_a_save_of_another_path_and_a_copy_name_their_own_file(self):
        from _bpy import _angelo_saved

        def record(filepath, *_args):
            self.seen.append((filepath, _angelo_saved.saved_file(), os.stat(filepath)))
        self.handler(record)
        copy = str(self.folder / "copy.blend")
        bpy.ops.wm.save_as_mainfile(filepath=copy, copy=True)
        (filepath, native, info), = self.seen
        self.assertEqual((native[0], fields_of_native(native)), (copy, fields(info)))


# Another program: replaces ``<file>@`` with its own file as soon as Blender has created it, then says so.
WATCHER = r"""
import os, sys, time
at, theirs, said = sys.argv[1:4]
end = time.monotonic() + 30
while time.monotonic() < end:
    if os.path.exists(at):
        try:
            os.replace(theirs, at)
        except OSError:
            continue
        open(said, "w").close()
        break
"""


class Interleaved(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-saved-file-"))
        self.path = str(self.folder / "document.blend")

    def tearDown(self):
        for handler in list(bpy.app.handlers.save_post):
            if getattr(handler, "_saved_file_test", False):
                bpy.app.handlers.save_post.remove(handler)

    def handler(self, function):
        function._saved_file_test = True
        bpy.app.handlers.save_post.append(bpy.app.handlers.persistent(function))

    def test_a_save_whose_temporary_file_another_program_replaced_is_refused_and_changes_nothing(self):
        from _bpy import _angelo_saved
        import bmesh
        mesh = bpy.data.meshes.new("Heavy")   # A save long enough for the other program to see its temporary file.
        shape = bmesh.new()
        bmesh.ops.create_grid(shape, x_segments=700, y_segments=700, size=1.0)
        shape.to_mesh(mesh)
        shape.free()
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("Heavy", mesh))
        bpy.ops.wm.save_as_mainfile(filepath=self.path, compress=False)   # The document, and its version backups.
        seen = []
        self.handler(lambda filepath, *_args: seen.append((_angelo_saved.saved_file(), os.stat(filepath))))
        outcomes = []
        for attempt in range(12):
            theirs, said = self.folder / ("theirs-%d" % attempt), self.folder / ("said-%d" % attempt)
            theirs_bytes = b"another program's file"
            theirs.write_bytes(theirs_bytes)
            leftover = Path(self.path + "@")   # A refused save leaves the other program's file there.
            if leftover.exists():
                leftover.unlink()
            before = self.document_files()
            seen.clear()
            watcher = subprocess.Popen([sys.executable, "-I", "-c", WATCHER, self.path + "@", str(theirs), str(said)])
            try:
                time.sleep(0.3)   # Polling before the save starts.
                try:
                    bpy.ops.wm.save_as_mainfile(filepath=self.path, compress=False)
                    refusal = None
                except RuntimeError as error:
                    refusal = str(error)
            finally:
                watcher.kill()
                watcher.wait()
            if not said.exists():
                continue
            after = self.document_files()
            document = after.get("document.blend")
            outcomes.append({"refused": refusal, "unchanged": after == before,
                             "document_is_theirs": document == theirs_bytes,
                             "named_at_path": bool(seen) and seen[0][0] is not None
                             and fields_of_native(seen[0][0]) == fields(seen[0][1])})
            if len(outcomes) >= 3:
                break
        RESULTS["cases"]["temporary_replaced"] = outcomes
        if not outcomes and os.name == "nt":   # Windows may refuse to replace a file another handle holds open:
            return                             # the other program then never replaced it during a save at all.
        self.assertTrue(outcomes, "the other program never replaced the temporary file during a save")
        for outcome in outcomes:
            refusal = outcome["refused"]
            if refusal is None:   # Saved: the other program's file in its place only in the moment after the check,
                if outcome["document_is_theirs"]:   # and then never named as the save's (and a warning said so).
                    self.assertFalse(outcome["named_at_path"], outcome)
            else:   # Refused before the version backups: every file on disk as it was.
                self.assertIn("the file on disk was not changed", refusal)
                self.assertTrue(outcome["unchanged"], outcome)
        if os.name == "nt":   # The writer's handle shares no delete (writefile.cc): the other program replaces the
            # temporary file only after the save's own rename took it, so no save sees it replaced; none may be theirs.
            self.assertFalse(any(outcome["document_is_theirs"] for outcome in outcomes), outcomes)
            return
        self.assertTrue(any(outcome["refused"] for outcome in outcomes), outcomes)

    def document_files(self):   # The document and its version backups, never the temporary file.
        return {path.name: path.read_bytes() for path in self.folder.glob("document.blend*")
                if not path.name.endswith("@")}

    def test_a_nested_save_of_the_same_path_leaves_the_handlers_after_it_the_file_now_there(self):
        from _bpy import _angelo_saved
        nested, later = [], []

        def backup_add_on(filepath, *_args):   # First: saves the same file again from its save_post handler.
            if not nested:
                nested.append(True)
                bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=True)

        def after_it(filepath, *_args):
            if len(later) < 2:
                later.append((filepath, _angelo_saved.saved_file(), os.stat(filepath)))
        self.handler(backup_add_on)
        self.handler(after_it)
        bpy.ops.wm.save_as_mainfile(filepath=self.path)
        inner, outer = later   # The nested save's handlers run first, inside the outer one's.
        self.assertEqual(fields_of_native(inner[1]), fields(inner[2]))
        self.assertIsNotNone(outer[1], "the outer handlers still name a file")
        self.assertEqual(fields_of_native(outer[1]), fields(outer[2]), "the file now at the path: the nested save's")
        RESULTS["cases"]["nested_same_path"] = {"outer": fields_of_native(outer[1]), "at_path": fields(outer[2])}


def fields_of_native(native):
    found = list(native[1:5])
    if sys.platform == "darwin":   # Nanoseconds, made the float os.stat reports.
        found.append(native[5] // 1_000_000_000 + (native[5] % 1_000_000_000) * 1e-9)
    return found


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    receipt = Path(arguments[0]) if arguments else Path(os.environ["ANGELO_QA_DIR"]) / "saved-file.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                for case in (SavedFile, Interleaved)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt.write_text(json.dumps({"success": result.wasSuccessful(), "tests": result.testsRun,
                                   "failures": [str(test) for test, _trace in result.failures],
                                   "errors": [str(test) for test, _trace in result.errors],
                                   "numbers": RESULTS, "blender": bpy.app.version_string}, indent=2) + "\n")
    print("RECEIPT " + receipt.read_text())
    if not result.wasSuccessful():
        raise AssertionError("A save's handlers did not name the file it wrote; see the receipt")
