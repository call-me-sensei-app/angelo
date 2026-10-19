# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows file backend against a real file system (Windows only; run by windows_agent_checks.py).

``test_native_windows_anchoring.py`` proves ``anchored_windows``' logic on any
host through a POSIX stand-in. These cases prove what only Windows can: NTFS
reparse points (symbolic links, junctions, a WOF-compressed file, a reparse tag
no driver handles), 8.3 aliases, paths longer than MAX_PATH, files another
process holds open with and without FILE_SHARE_DELETE, an ancestor renamed
under a held handle, retention deletion, and FAT32 (no hard links, no POSIX
rename or delete classes) on a virtual disk. They need an administrator (symbolic
links, ``fsutil``, ``diskpart``) and change nothing outside their own folder,
apart from the virtual disk they attach and detach. Contract:
doc/angelo/checkpoint_policy.rst, "Windows file anchoring".
"""

import contextlib
import ctypes
import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core")]
from angelo_agent import anchored_windows as aw  # noqa: E402
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.snapshot_retention import enforce_retention  # noqa: E402

CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL
SHARE_READ_WRITE, SHARE_ALL = 0x3, 0x7
UNHANDLED_TAG = 0x0000A55A   # A third-party tag no driver on the machine handles (not a name surrogate).
FSCTL_SET_REPARSE_POINT = 0x000900A4
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE]
kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]

# Another process holding a file or folder open (CreateFileW with the given share mode) until its stdin closes.
HOLDER = r"""
import ctypes, sys
from ctypes import wintypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.CreateFileW.restype = wintypes.HANDLE
k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
h = k.CreateFileW(sys.argv[1], 0x80000000, int(sys.argv[2]), None, 3, 0x02000000, None)
if h is None or h == wintypes.HANDLE(-1).value:
    print("error", ctypes.get_last_error(), flush=True)
    sys.exit(1)
print("ready", flush=True)
sys.stdin.readline()
"""


@contextlib.contextmanager
def held_open(path, share):
    """``path`` open in another process with ``share`` (SHARE_READ_WRITE: as Python's open() and most editors)."""
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(share)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    try:
        line = process.stdout.readline().strip()
        if line != "ready":
            raise AssertionError("the holder could not open %s: %s" % (path, line))
        yield process
    finally:
        try:
            process.stdin.write("\n")
            process.stdin.close()
        except OSError:
            pass
        process.wait(timeout=30)


def junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def short_name(path):
    buffer = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetShortPathNameW(str(path), buffer, 1024):
        return None
    return Path(buffer.value).name


def ensure_short_name(path, wanted):
    """The 8.3 alias of ``path``: the one the volume generated, else ``wanted`` set with fsutil (administrator)."""
    current = short_name(path)
    if current and current.upper() != path.name.upper():
        return current
    run = subprocess.run(["fsutil", "file", "setshortname", str(path), wanted], capture_output=True, text=True)
    current = short_name(path)
    if current and current.upper() != path.name.upper():
        return current
    raise unittest.SkipTest("no 8.3 alias on this volume (fsutil: %s)" % (run.stdout + run.stderr).strip())


def set_unhandled_reparse_point(path):
    """Give ``path`` a reparse point with a third-party tag no filter driver on this machine handles."""
    handle = kernel32.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        payload = b"angelo"
        guid = uuid.UUID("6f2a0d27-6b3e-4c8e-9a51-1c3f5e2b7d40").bytes_le
        buffer = (UNHANDLED_TAG.to_bytes(4, "little") + len(payload).to_bytes(2, "little") + b"\0\0" + guid + payload)
        returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(handle, FSCTL_SET_REPARSE_POINT, buffer, len(buffer), None, 0,
                                        ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def reparse_tag(path):
    """The entry's own reparse tag, read through the backend (os.lstat reports a WOF file's target instead)."""
    with cf._private_directory(Path(path).parent) as folder:
        fd, attributes, tag = folder._open(Path(path).name, aw.INFO_ACCESS, aw.FILE_OPEN, allow_link=True)
        os.close(fd)
    return tag if attributes & aw.FILE_ATTRIBUTE_REPARSE_POINT else 0


class NtfsCase(unittest.TestCase):
    def setUp(self):
        base = Path(os.environ.get("ANGELO_NTFS_ROOT") or tempfile.gettempdir())
        temp = tempfile.TemporaryDirectory(prefix="angelo-ntfs-", dir=base)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.assertIsNotNone(cf._windows_api(), "the real NtFileApi must load on Windows")

    def write_new(self, folder, name, data):
        with os.fdopen(cf._open_file(folder, name, CREATE), "wb") as handle:
            handle.write(data)


class ReparsePointTests(NtfsCase):
    def test_real_handles_and_the_dont_reparse_flag(self):
        with cf._private_directory(self.root / "state" / "nested", create=True) as folder:
            self.assertIsInstance(folder, aw.WindowsDirectory)
            cf._write_json(folder, "checkpoint.json", {"state": "READY"})
            cf._write_json(folder, "checkpoint.json", {"state": "PRUNED", "text": "é ✓ 日本"})
            self.assertEqual(cf._read_json(folder, "checkpoint.json")["state"], "PRUNED")
            self.assertEqual(cf.list_names(folder), ["checkpoint.json"])
            self.assertTrue(stat.S_ISDIR(cf.directory_info(folder).st_mode))
            self.assertTrue(folder.final_path().lower().endswith("\\state\\nested"))
        # Windows 10 1709 and later accept OBJ_DONT_REPARSE: the fallback must not have been taken here.
        self.assertEqual(cf._windows_api().dont_reparse, 0x1000)

    def test_directory_symlink_and_junction_in_the_chain_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, self.root / "dirlink", target_is_directory=True)
        junction(self.root / "junction", outside)
        for name in ("dirlink", "junction"):
            for create in (False, True):
                with self.subTest(link=name, create=create), self.assertRaisesRegex(ValueError, "cannot follow directory links"):
                    with cf._private_directory(self.root / name / "new", create=create):
                        pass
        self.assertEqual(list(outside.iterdir()), [])

    def test_link_entries_are_reported_never_followed_and_unlinked_themselves(self):
        target = self.root / "target.txt"
        target.write_text("outside")
        os.symlink(target, self.root / "filelink.txt")
        (self.root / "dir").mkdir()
        junction(self.root / "jct", self.root / "dir")
        with cf._private_directory(self.root) as folder:
            for name in ("filelink.txt", "jct"):
                with self.subTest(name=name):
                    self.assertTrue(stat.S_ISLNK(cf.entry_info(folder, name).st_mode))
            with self.assertRaisesRegex(ValueError, "invalid file/link"):
                cf._file_info(folder, "filelink.txt")
            for flags in (os.O_RDONLY, os.O_WRONLY | os.O_CREAT, os.O_WRONLY | os.O_TRUNC):
                with self.subTest(flags=flags), self.assertRaises(OSError) as raised:
                    cf._open_file(folder, "filelink.txt", flags)
                self.assertEqual(raised.exception.errno, errno.ELOOP)
            with self.assertRaises(FileExistsError):
                cf._open_file(folder, "filelink.txt", CREATE)
            # A truncating open must not have changed the link either (FILE_OVERWRITE would have emptied it).
            self.assertTrue((self.root / "filelink.txt").is_symlink())
            cf._unlink_file(folder, "filelink.txt")
            with self.assertRaises(OSError):   # A junction is a directory entry: the file delete refuses it.
                cf._unlink_file(folder, "jct")
        self.assertEqual(target.read_text(), "outside")
        self.assertFalse((self.root / "filelink.txt").exists())
        self.assertTrue((self.root / "dir").is_dir())

    def test_a_wof_compressed_file_reads_and_is_replaced_like_any_file(self):
        # compact /exe stores the file behind a WOF reparse point, which wof.sys hides even from an open with
        # FILE_OPEN_REPARSE_POINT (seen on Server 2022): the backend reads it as a regular file, the data intact.
        path = self.root / "compressed.txt"
        data = b"Angelo compresses well. " * 4096
        path.write_bytes(data)
        run = subprocess.run(["compact.exe", "/c", "/exe:xpress4k", str(path)], capture_output=True, text=True)
        if "[OK]" not in run.stdout:
            self.skipTest("compact /exe did not compress the file: " + (run.stdout + run.stderr).strip()[-300:])
        tag = reparse_tag(path)
        print("WOF-compressed file seen with reparse tag:", hex(tag), flush=True)   # A fact for the documentation.
        with cf._private_directory(self.root) as folder:
            self.assertTrue(stat.S_ISREG(cf.entry_info(folder, "compressed.txt").st_mode))
            with os.fdopen(cf._open_file(folder, "compressed.txt", os.O_RDONLY), "rb") as handle:
                self.assertEqual(handle.read(), data)
            if tag:   # Visible: a data reparse point, never written in place.
                with self.assertRaises(OSError) as raised:
                    cf._open_file(folder, "compressed.txt", os.O_RDWR)
                self.assertEqual(raised.exception.errno, errno.EPERM)
            self.write_new(folder, "next.tmp", b"replacement")
            cf._replace_file(folder, "next.tmp", "compressed.txt")
        self.assertEqual(path.read_bytes(), b"replacement")

    def test_a_reparse_tag_no_driver_handles_fails_closed(self):
        path = self.root / "unhandled.bin"
        path.write_bytes(b"payload")
        set_unhandled_reparse_point(path)
        self.assertEqual(reparse_tag(path), UNHANDLED_TAG)
        with cf._private_directory(self.root) as folder:
            self.assertFalse(stat.S_ISLNK(cf.entry_info(folder, "unhandled.bin").st_mode))
            with self.assertRaises(OSError):
                cf._open_file(folder, "unhandled.bin", os.O_RDONLY)
            cf._unlink_file(folder, "unhandled.bin")   # Removing it acts on the entry itself.
        self.assertFalse(path.exists())


class NameTests(NtfsCase):
    def test_short_name_aliases_are_refused_for_directories_and_files(self):
        (self.root / ".git").mkdir()
        secret = self.root / ".env-production"
        secret.write_text("TOKEN=not-a-real-token")
        directory_alias = ensure_short_name(self.root / ".git", "GIT~1")
        file_alias = ensure_short_name(secret, "ENVPRO~1")
        with cf._private_directory(self.root) as folder:
            for operation in (lambda: cf._open_file(folder, file_alias, os.O_RDONLY),
                              lambda: cf.entry_info(folder, file_alias), lambda: cf._unlink_file(folder, file_alias)):
                with self.assertRaisesRegex(ValueError, "Short"):
                    operation()
            with self.assertRaisesRegex(ValueError, "Short"):
                aw._open_component(cf._windows_api(), folder.fd, directory_alias, False)
        # A path written with an alias is expanded before the walk (GetLongPathNameW), then admitted by its real name.
        with cf._private_directory(self.root / directory_alias) as folder:
            self.assertTrue(folder.final_path().endswith("\\.git"))
        self.assertTrue(secret.exists())

    def test_names_windows_would_store_differently_are_refused(self):
        with cf._private_directory(self.root) as folder:
            for name in ("notes.md:hidden", "notes.md::$DATA", "NUL", "con.txt", "COM1.log", "lpt¹", "dot.",
                         "space ", "a|b", "a?b", "a*b", "tab\tname", "..", ""):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Windows can't use"):
                    cf._open_file(folder, name, CREATE)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unicode_names_case_and_large_directories(self):
        name = "Ünïcødé 日本 \U0001F3A8.txt"
        with cf._private_directory(self.root / "Folder", create=True) as folder:
            self.write_new(folder, name, b"x")
            self.assertTrue(stat.S_ISREG(cf.entry_info(folder, name.upper()).st_mode))   # NTFS: case-insensitive
            names = [("entry-%04d-" % index) + "n" * 150 for index in range(600)]   # > 64 KiB of directory records
            for entry in names:
                self.write_new(folder, entry, b"")
            listed = cf.list_names(folder)
        self.assertEqual(sorted(listed), sorted(names + [name]))
        with cf._private_directory(self.root / "FOLDER", within=self.root) as folder:
            self.assertTrue(folder.final_path().endswith("\\Folder"))

    def test_long_paths_beyond_max_path(self):
        deep = self.root.joinpath(*("component-%02d-" % index + "d" * 40 for index in range(9)))
        self.assertGreater(len(str(deep)), 400)
        # shutil.rmtree reaches past MAX_PATH only through the \\?\ prefix (long paths are off by default).
        self.addCleanup(shutil.rmtree, "\\\\?\\" + str(self.root / deep.relative_to(self.root).parts[0]), True)
        with cf._private_directory(deep, create=True) as folder:
            cf._write_json(folder, "checkpoint.json", {"depth": len(str(deep))})
            self.assertEqual(cf._read_json(folder, "checkpoint.json"), {"depth": len(str(deep))})
            self.assertEqual(cf.list_names(folder), ["checkpoint.json"])
        workspace = FileWorkspace(self.root)
        store = CheckpointStore(self.root.parent / (self.root.name + "-state"), str(uuid.uuid4()))
        self.addCleanup(shutil.rmtree, store.root, True)
        record, scene = store.reserve()
        scene.write_bytes(b"scene")
        snapshot = store.complete(record["id"])
        relative = str(deep.relative_to(self.root) / "notes.md")
        workspace.execute("angelo_write_file", {"path": relative, "content": "deep"}, store, snapshot["id"])
        read = workspace.execute("angelo_read_file", {"path": relative, "start": 0, "length": 10})
        self.assertEqual(read["text"], "deep")


class SharingTests(NtfsCase):
    """Another process holding a file: what POSIX never sees."""

    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()
        self.store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        self.workspace = FileWorkspace(self.project)

    def snapshot(self, data=b"synthetic host scene"):
        record, path = self.store.reserve()
        path.write_bytes(data)
        return self.store.complete(record["id"])

    def call(self, name, **arguments):
        return self.workspace.execute(name, arguments, self.store, self.snapshot()["id"])

    def test_a_file_open_without_share_delete_is_left_unchanged_and_reported(self):
        target = self.project / "open.txt"
        target.write_text("original")
        with held_open(target, SHARE_READ_WRITE):
            for name, arguments in (("angelo_write_file", {"content": "changed"}), ("angelo_delete_file", {})):
                with self.subTest(tool=name), self.assertRaises(OSError) as raised:
                    self.call(name, path="open.txt", **arguments)
                self.assertIn(raised.exception.winerror, (5, 32))   # access denied / sharing violation
                self.assertFalse(getattr(raised.exception, "partial_changes_possible", False))
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(sorted(path.name for path in self.project.iterdir()), ["open.txt"])   # No temporary left.
        self.assertEqual(self.store.inspect()["issues"], [])
        self.call("angelo_write_file", path="open.txt", content="after")   # Works once the other program closed it.
        self.assertEqual(target.read_text(), "after")

    def test_a_file_open_with_share_delete_is_replaced_and_deleted(self):
        target = self.project / "shared.txt"
        target.write_text("original")
        with held_open(target, SHARE_ALL):
            self.call("angelo_write_file", path="shared.txt", content="replaced")
            self.assertEqual(target.read_text(), "replaced")
        with held_open(target, SHARE_ALL):
            self.call("angelo_delete_file", path="shared.txt")
            self.assertFalse(target.exists())   # POSIX semantics: the name is gone while the other handle stays open.

    def test_an_ancestor_renamed_and_replaced_by_a_junction_under_a_held_handle(self):
        inner, outside = self.project / "a" / "b", self.root / "outside"
        inner.mkdir(parents=True)
        outside.mkdir()
        with cf._private_directory(inner, within=self.project) as folder:
            try:
                os.rename(self.project / "a", self.project / "a-moved")
            except OSError as error:
                renamed = "refused: %s" % error
            else:
                renamed = "renamed"
                junction(self.project / "a", outside)
            self.write_new(folder, "landed.txt", b"stays inside")
        print("ancestor rename under a held handle:", renamed, flush=True)   # A fact for the documentation.
        self.assertEqual(list(outside.iterdir()), [])
        landed = (self.project / "a-moved" / "b" / "landed.txt") if renamed == "renamed" else (inner / "landed.txt")
        self.assertEqual(landed.read_bytes(), b"stays inside", renamed)
        if renamed == "renamed":
            with self.assertRaises(ValueError):   # The next walk refuses the junction.
                self.workspace.execute("angelo_read_file", {"path": "a/b/landed.txt", "start": 0, "length": 5})

    def test_retention_deletion_with_a_snapshot_held_open(self):
        snapshots = [self.snapshot(bytes(4096)) for _ in range(4)]
        oldest = self.store.folder(snapshots[0]["id"]) / "scene.blend"
        with held_open(oldest, SHARE_READ_WRITE):
            try:
                summary = enforce_retention(self.store, {"max_count": 2})
                outcome = "completed: %s" % summary["removed"]
            except (OSError, ValueError) as error:
                outcome = "refused: %s" % error
        # Whatever happened while the file was held, nothing may be lost or left unaccounted for, and a later run
        # (the other program closed it) must reach the policy.
        summary = enforce_retention(self.store, {"max_count": 2})
        self.assertEqual(self.store.inspect()["issues"], [], outcome)
        self.assertTrue(summary["budget_met"], outcome)
        self.assertEqual([self.store.availability(item["id"])["available"] for item in snapshots],
                         [False, False, True, True], outcome)
        self.assertFalse(oldest.exists(), outcome)

    def test_retention_removes_snapshots_through_handles(self):
        snapshots = [self.snapshot(bytes(4096)) for _ in range(4)]
        summary = enforce_retention(self.store, {"max_bytes": 9000})
        self.assertTrue(summary["removed"])
        self.assertTrue(summary["budget_met"])
        self.assertEqual(self.store.inspect()["issues"], [])
        self.assertFalse(self.store.availability(snapshots[0]["id"])["available"])


class FileToolTests(NtfsCase):
    def test_write_read_list_delete_restore_and_links(self):
        project, outside = self.root / "project", self.root / "outside"
        project.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("outside")
        store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        workspace = FileWorkspace(project)

        def snapshot():
            record, path = store.reserve()
            path.write_bytes(b"scene")
            return store.complete(record["id"])

        first = snapshot()
        workspace.execute("angelo_write_file", {"path": "docs/notes.md", "content": "one"}, store, first["id"])
        second = snapshot()
        workspace.execute("angelo_write_file", {"path": "docs/notes.md", "content": "two é"}, store, second["id"])
        self.assertEqual(workspace.execute("angelo_read_file", {"path": "docs/notes.md", "start": 0, "length": 50})["text"],
                         "two é")
        self.assertEqual(workspace.execute("angelo_list_files", {"path": "docs"})["entries"],
                         [{"name": "notes.md", "directory": False, "bytes": 6}])
        workspace.execute("angelo_delete_file", {"path": "docs/notes.md"}, store, snapshot()["id"])
        store.restore_since(second["id"])
        self.assertEqual((project / "docs/notes.md").read_text(), "one")
        self.assertEqual(store.inspect()["issues"], [])
        junction(project / "jct", outside)
        os.symlink(outside / "secret.txt", project / "link.txt")
        for path in ("jct/secret.txt", "link.txt", "C:notes.md", "\\notes.md", "docs/notes.md:stream", "docs/NUL",
                     str(outside / "secret.txt")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                workspace.execute("angelo_read_file", {"path": path, "start": 0, "length": 10})
        with self.assertRaises(ValueError):
            workspace.execute("angelo_write_file", {"path": "jct/new.txt", "content": "x"}, store, snapshot()["id"])
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["secret.txt"])

    def test_publication_replacement_readonly_and_removal(self):
        with cf._private_directory(self.root) as folder:
            self.write_new(folder, "staged.tmp", b"first")
            cf.publish_new_file(folder, "staged.tmp", "out.txt")
            self.assertEqual(cf.entry_info(folder, "out.txt").st_nlink, 2)
            with self.assertRaises(FileExistsError):
                cf.publish_new_file(folder, "staged.tmp", "out.txt")
            cf._unlink_file(folder, "staged.tmp")
            cf.chmod_file(folder, "out.txt", 0o444)
            self.assertFalse(cf.entry_info(folder, "out.txt").st_mode & stat.S_IWUSR)
            self.write_new(folder, "next.tmp", b"second")
            cf._replace_file(folder, "next.tmp", "out.txt")   # RENAME_IGNORE_READONLY: replaces a read-only file
            cf.chmod_file(folder, "out.txt", 0o444)
            cf._unlink_file(folder, "out.txt")                 # DISPOSITION_IGNORE_READONLY
        self.assertEqual(list(self.root.iterdir()), [])


class JobOutputTests(NtfsCase):
    """Background-job staging and publication (doc/angelo/long_jobs.rst) on real handles."""

    def test_set_publication_new_folders_and_a_junction_before_publishing(self):
        from angelo_agent import job_outputs
        workspace, outside = self.root / "workspace", self.root / "outside"
        workspace.mkdir()
        outside.mkdir()
        output = job_outputs.admit_output(FileWorkspace(workspace), "exports/asset.usda")
        staging = job_outputs.Staging(workspace, output, "j1").create()
        (staging.path / "asset.usda").write_text("@./textures/a.png@")
        (staging.path / "textures").mkdir()
        (staging.path / "textures" / "a.png").write_bytes(b"png")
        self.assertEqual(staging.publish_file("asset.usda"), output)   # mkdir FILE_CREATE + cross-folder links.
        staging.close()                                                  # Deletes staging folders through handles.
        self.assertEqual(sorted(p.name for p in (workspace / "exports").iterdir()), ["asset.usda", "textures"])
        later = job_outputs.admit_output(FileWorkspace(workspace), "renders/still.png")
        staging = job_outputs.Staging(workspace, later, "j2").create()
        (staging.path / "frame_0001.png").write_bytes(b"png")
        # The job holds its output folder without delete sharing, so on NTFS the folder cannot be moved away and
        # replaced by a junction while the job runs: the system refuses the swap the POSIX tests stage.
        with self.assertRaises(PermissionError):
            os.rename(workspace / "renders", workspace / "renders-moved")
        self.assertEqual(staging.publish_file("frame_0001.png"), later)
        staging.close()
        self.assertEqual((list(outside.iterdir()), (workspace / "renders" / "still.png").read_bytes()), ([], b"png"))
        # Before the job starts nothing is held: a junction put in place of the output's folder after admission is
        # refused when the job starts (as test_native_long_jobs_outputs checks with a POSIX link).
        admitted = job_outputs.admit_output(FileWorkspace(workspace), "linked/still.png")
        junction(workspace / "linked", outside)
        with self.assertRaises(job_outputs.OutputMoved):
            job_outputs.Staging(workspace, admitted, "j3").create()
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_taken_name_gets_the_next_free_one_case_insensitively(self):
        """1.0: a job never replaces a file. On NTFS a name taken in another case is taken (FILE_CREATE and the link
        collide), so a still, a folder and a set each go to the next free name and nothing existing changes."""
        from angelo_agent import job_outputs
        workspace = self.root / "workspace"
        workspace.mkdir()
        files = FileWorkspace(workspace)
        (workspace / "STILL.png").write_bytes(b"theirs")
        (workspace / "still-J4.png").write_bytes(b"theirs too")
        (workspace / "ANIM").mkdir()
        (workspace / "textures").mkdir()
        published = []
        for relative, main, extra, folder in (("still.png", "main", (), False), ("anim", None, (), True),
                                              ("asset.usda", "main", ("textures",), False)):
            output = job_outputs.admit_output(files, relative)
            staging = job_outputs.Staging(workspace, output, "j4", os.path.lexists(output) or any(
                (workspace / name).exists() for name in extra), relative.endswith(".usda")).create()
            (staging.path / (main or "frame_0001.png")).write_bytes(b"new")
            for name in extra:
                (staging.path / name).mkdir()
                (staging.path / name / "a.png").write_bytes(b"png")
            staging.publish_folder() if folder else staging.publish_file(main)
            published.append(staging.relative_output())
            staging.close()
        self.assertEqual(published, ["still-j4-2.png", "anim-j4", "asset-j4/asset.usda"])
        self.assertEqual(((workspace / "STILL.png").read_bytes(), (workspace / "still-J4.png").read_bytes()),
                         (b"theirs", b"theirs too"))
        self.assertEqual(list((workspace / "textures").iterdir()), [])


class Fat32Tests(unittest.TestCase):
    """A FAT32 volume: no hard links, no POSIX rename/delete classes, unstable file IDs."""

    @classmethod
    def setUpClass(cls):
        cls.vhd = Path(os.environ.get("ANGELO_NTFS_ROOT") or tempfile.gettempdir()) / ("angelo-fat32-%s.vhdx" % uuid.uuid4().hex[:8])
        used = {item[0].upper() for item in os.listdrives()} if hasattr(os, "listdrives") else set()
        letters = [letter for letter in "TUVWXYZ" if letter not in used]
        if not letters:
            raise unittest.SkipTest("no free drive letter for the FAT32 virtual disk")
        cls.letter = letters[0]
        script = ('create vdisk file="{0}" maximum=128 type=expandable\nselect vdisk file="{0}"\nattach vdisk\n'
                  'create partition primary\nformat fs=fat32 quick label=ANGELOFAT\nassign letter={1}\n').format(cls.vhd, cls.letter)
        cls.attached = False
        result = cls._diskpart(script)
        cls.attached = Path(cls.letter + ":\\").is_dir()
        if not cls.attached:
            cls._detach()
            raise unittest.SkipTest("diskpart could not attach a FAT32 virtual disk: " + result[-400:])

    @classmethod
    def _diskpart(cls, script):
        path = Path(tempfile.gettempdir()) / ("angelo-diskpart-%s.txt" % uuid.uuid4().hex[:8])
        path.write_text(script, encoding="ascii")
        try:
            run = subprocess.run(["diskpart", "/s", str(path)], capture_output=True, text=True, timeout=180)
            return run.stdout + run.stderr
        finally:
            path.unlink()

    @classmethod
    def _detach(cls):
        if cls.vhd.exists():
            cls._diskpart('select vdisk file="{0}"\ndetach vdisk\n'.format(cls.vhd))
            for _ in range(10):
                try:
                    cls.vhd.unlink()
                    break
                except OSError:
                    time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls._detach()

    def setUp(self):
        self.root = Path(self.letter + ":\\") / ("case-" + uuid.uuid4().hex[:8])
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_storage_uses_the_legacy_classes(self):
        with cf._private_directory(self.root / "state", create=True) as folder:
            cf._write_json(folder, "checkpoint.json", {"state": "READY"})
            cf._write_json(folder, "checkpoint.json", {"state": "PRUNED"})   # rename-over: legacy FileRenameInformation
            self.assertEqual(cf._read_json(folder, "checkpoint.json"), {"state": "PRUNED"})
            with os.fdopen(cf._open_file(folder, "old.bin", CREATE), "wb") as handle:
                handle.write(b"x")
            cf._unlink_file(folder, "old.bin")                                  # legacy FileDispositionInformation
            self.assertEqual(cf.list_names(folder), ["checkpoint.json"])

    def test_new_file_publication_is_refused_and_overwrites_work(self):
        project = self.root / "project"
        project.mkdir()
        (project / "existing.txt").write_text("before")
        store = CheckpointStore(self.root / "state", str(uuid.uuid4()))
        workspace = FileWorkspace(project)
        record, scene = store.reserve()
        scene.write_bytes(b"scene")
        snapshot = store.complete(record["id"])
        with self.assertRaises(OSError) as raised:
            workspace.execute("angelo_write_file", {"path": "new.txt", "content": "x"}, store, snapshot["id"])
        # The errors Studio export and the file tools name as "this file system cannot publish".
        self.assertIn(raised.exception.errno, (errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV), repr(raised.exception))
        self.assertEqual(sorted(path.name for path in project.iterdir()), ["existing.txt"])
        record, scene = store.reserve()
        scene.write_bytes(b"scene")
        workspace.execute("angelo_write_file", {"path": "existing.txt", "content": "after"}, store,
                          store.complete(record["id"])["id"])
        self.assertEqual((project / "existing.txt").read_text(), "after")
        self.assertEqual(store.inspect()["issues"], [])
