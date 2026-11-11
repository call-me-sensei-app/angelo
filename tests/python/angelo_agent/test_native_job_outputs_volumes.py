# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Job outputs on volumes without an exclusive rename or hard links (exFAT, FAT, network shares).

Regressions (release-1010 audit re-check 3, P1, with the class around it):

* every job and web export was refused at start on exFAT on macOS, which refuses ``renameatx_np(RENAME_EXCL)``
  with ``ENOTSUP``: ``Staging.create`` checked the exclusive rename up front;
* publication made hard links only, which exFAT and FAT don't have, so even before that a job ran its whole work and
  then failed to publish. There the staged files are now moved into place (``job_output_links.publish_entry``), and
  an unpublish moves them back, so the staging folder stays whole whatever the publication meets;
* a publication that failed lost what the job wrote (``job_manager.kept_outputs``).

``Scenarios`` runs on: this host's file system with links refused (``NoLinks``), the Windows handle backend's stand-in
with links refused (``WindowsNoLinks``: its moves are ``rename_at`` without replace), the real Windows backend
(``RealWindowsNoLinks``, Windows only), and real exFAT, FAT, HFS+ and APFS disk images (``RealVolumes``, macOS
``hdiutil``). Elsewhere each is an environment skip.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ctypes
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import anchored_windows  # noqa: E402
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import exclusive_rename, job_manager, job_output_links, job_outputs, job_recovery  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402

POSIX_ONLY = environment_skip("POSIX descriptors and renames; the Windows backends run in their own classes")


PAIR_OBSERVATIONS = []


def logical_names(folder):
    """Payload inventory plus independently observed metadata; unknown dot files never disappear here."""
    raw = set(cf.list_names(folder))
    logical = job_output_links.names(folder)
    for name in sorted(raw - set(logical)):
        primary = name[2:]
        metadata = job_output_links.partner(folder, primary)
        assert metadata is not None and name == '._' + primary
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        try:
            import hashlib
            data = os.read(descriptor, 1024 * 1024 + 1)
            PAIR_OBSERVATIONS.append(dict(primary=primary, metadata=name, bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), identity=[metadata.st_dev, metadata.st_ino]))
        finally:
            os.close(descriptor)
    return logical


def list_payload(path):
    with cf._private_directory(path) as folder:
        return logical_names(folder)


def tree(root):
    found = []
    def walk(path, prefix=''):
        for name in list_payload(path):
            relative = prefix + name
            found.append(relative)
            if (path / name).is_dir() and not (path / name).is_symlink():
                walk(path / name, relative + '/')
    walk(root)
    return sorted(found)


def refusing(code):
    """An exclusive rename the file system refuses with ``code`` (a share: EINVAL, ENOTSUP; an old kernel: ENOSYS)."""
    def call(*_args):
        ctypes.set_errno(code)
        return -1
    return call, exclusive_rename.RENAME_EXCL


def refuse_links(*_args):
    raise OSError(errno.ENOTSUP, "Operation not supported")


class Scenarios:
    """Mixin: ``self.root`` is a fresh workspace folder on the volume under test."""

    def staging(self, relative, identity, **options):
        output = job_outputs.admit_output(FileWorkspace(self.root), relative)
        return job_outputs.Staging(self.root, output, identity, **options).create()

    def staged_set(self, identity):
        staging = self.staging("exports/asset.usda", identity)
        (staging.path / "asset.usda").write_text("@./textures/a.png@")
        (staging.path / "textures").mkdir()
        (staging.path / "textures/a.png").write_bytes(b"png")
        (staging.path / "textures/empty.mask").write_bytes(b"")   # An empty file too (FAT numbers those apart).
        return staging

    def test_a_folder_output_and_a_set_are_published_whole(self):
        frames = self.staging("renders/anim", "ja")
        (frames.path / "frame_0001.png").write_bytes(b"png1")
        (frames.path / "passes").mkdir()
        (frames.path / "passes/depth.exr").write_bytes(b"exr")
        self.assertEqual(frames.publish_folder(), self.root / "renders/anim")
        frames.close()
        asset = self.staged_set("jb")
        self.assertEqual(asset.publish_file("asset.usda"), self.root / "exports/asset.usda")
        asset.close()
        self.assertEqual(tree(self.root), ["exports", "exports/asset.usda", "exports/textures",
                                           "exports/textures/a.png", "exports/textures/empty.mask", "renders",
                                           "renders/anim",
                                           "renders/anim/frame_0001.png", "renders/anim/passes",
                                           "renders/anim/passes/depth.exr"])
        self.assertEqual((self.root / "renders/anim/passes/depth.exr").read_bytes(), b"exr")

    def test_a_name_taken_mid_set_moves_the_whole_set_back_then_into_its_folder(self):
        staging = self.staged_set("jw")
        real = job_output_links.publish_entry

        def then_theirs_appears(source, name, target, new_name, *args, **kwargs):
            identity = real(source, name, target, new_name, *args, **kwargs)
            if new_name == "asset.usda" and not (self.root / "exports/textures").exists():
                (self.root / "exports/textures").mkdir()   # Another program's folder, between two entries.
                (self.root / "exports/textures/mine.png").write_bytes(b"theirs")
            return identity
        with patch.object(job_outputs, "_publish_entry", then_theirs_appears):
            published = staging.publish_file("asset.usda")
        staging.close()
        self.assertEqual(published, self.root / "exports/asset-jw/asset.usda")
        self.assertEqual(tree(self.root / "exports"), ["asset-jw", "asset-jw/asset.usda", "asset-jw/textures",
                                                       "asset-jw/textures/a.png", "asset-jw/textures/empty.mask",
                                                       "textures", "textures/mine.png"])
        self.assertEqual((self.root / "exports/textures/mine.png").read_bytes(), b"theirs")

    def test_a_failure_mid_folder_leaves_the_staging_folder_whole(self):
        staging = self.staging("renders/anim", "jm")
        for number in range(1, 4):
            (staging.path / ("frame_%04d.png" % number)).write_bytes(b"png%d" % number)
        before = sorted(list_payload(staging.path))
        real, calls = job_output_links.publish_entry, []

        def fail_the_third(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 3:
                raise OSError(errno.EIO, "Input/output error")
            return real(*args, **kwargs)
        with patch.object(job_output_links, "publish_entry", fail_the_third), self.assertRaises(OSError):
            staging.publish_folder()
        self.assertEqual(sorted(list_payload(staging.path)), before, "every staged file is back")
        self.assertEqual((staging.path / "frame_0002.png").read_bytes(), b"png2")
        self.assertFalse((self.root / "renders/anim").exists(), "nothing stays published")
        self.assertEqual(job_manager.kept_outputs(staging), "renders/.angelo-job-jm", "kept, never lost")

    def test_an_unpublish_moves_back_only_the_files_it_moved(self):
        staging = self.staged_set("ju")
        staging.publish_file("asset.usda")
        moved = bool(staging._moved)   # This volume has no links: the staged files themselves were published.
        theirs = self.root / "exports/theirs.tmp"
        theirs.write_bytes(b"theirs")
        os.replace(theirs, self.root / "exports/textures/a.png")   # Another program replaced one published file.
        staging.unpublish()
        self.assertEqual(sorted(list_payload(staging.path)), [".angelo-job", "asset.usda", "textures"])
        self.assertEqual(sorted(list_payload(staging.path / "textures")), ["empty.mask"] if moved else
                         ["a.png", "empty.mask"], "its own files: in staging (moved back, or never moved)")
        self.assertEqual((staging.path / "asset.usda").read_text(), "@./textures/a.png@", "its own: back in staging")
        self.assertFalse((self.root / "exports/asset.usda").exists())
        self.assertTrue((self.root / "exports/textures/a.png").is_file(), "another's file is never moved or removed")
        self.assertEqual((self.root / "exports/textures/a.png").read_bytes(), b"theirs")
        staging.discard()

    def test_a_crash_leftover_is_published_whole_by_the_next_start(self):
        """A crash part way through an animation's publication: the frames moved are in the announced folder, whole;
        the next start's recovery publishes the rest into it."""
        staging = self.staging("renders/anim", "jc")
        for number in range(1, 4):
            (staging.path / ("frame_%04d.png" % number)).write_bytes(b"png%d" % number)
        real, calls = job_output_links.publish_entry, []

        def crash_at_the_third(*args, **kwargs):
            calls.append(args[1])
            if len(calls) == 3:
                raise KeyboardInterrupt("the app stops here")
            return real(*args, **kwargs)
        with patch.object(job_output_links, "publish_entry", crash_at_the_third), \
                patch.object(job_outputs.Staging, "unpublish", lambda self: None), \
                self.assertRaises(KeyboardInterrupt):
            staging.publish_folder()
        staging._chain.close()   # The process ended: nothing is held any more.
        moved = sorted(list_payload(self.root / "renders/anim"))   # Moved (whole), or links its failure removed.
        self.assertIn(len(moved), (0, 2))
        again = job_recovery._Reopened(self.root, self.root / "renders/anim", "jc").reopen()
        left = [name for name in logical_names(again.folder) if name != job_outputs.MARKER]
        path, linked, theirs = job_recovery.publish(again, left, into="anim")
        again.release()   # Links an earlier attempt made count as published; moved files are no longer staged.
        self.assertEqual((path, linked, theirs), (self.root / "renders/anim", len(left), 0))
        self.assertEqual(len(left), 3 if moved and (self.root / "renders/anim" / moved[0]).stat().st_nlink == 2
                         else 3 - len(moved))
        self.assertEqual(sorted(list_payload(path)), ["frame_0001.png", "frame_0002.png", "frame_0003.png"])
        self.assertEqual([(path / name).read_bytes() for name in sorted(list_payload(path))], [b"png1", b"png2", b"png3"])

    def test_a_failure_keeps_a_file_another_program_put_into_a_folder_it_made(self):
        """Break-it (H1, 9cec63257c2's class in a second path): a failure inside a folder publication removed the
        folders it had made with everything in them, another program's file too."""
        staging = self.staging("renders/anim", "jh")
        (staging.path / "frame_0001.png").write_bytes(b"png1")
        (staging.path / "passes").mkdir()
        for name in ("depth.exr", "normal.exr"):
            (staging.path / "passes" / name).write_bytes(b"exr")
        real, inside = job_output_links.publish_entry, []
        created_foreign = []

        def theirs_then_a_failure(source, name, *args, **kwargs):
            if name.endswith(".exr"):
                inside.append(name)
                if len(inside) == 2:   # Into the folder this publication made, then the next entry fails.
                    (self.root / "renders/anim/passes/notes.txt").write_bytes(b"theirs")
                    created_foreign.append(True)
                    raise OSError(errno.EIO, "Input/output error")
            return real(source, name, *args, **kwargs)
        with patch.object(job_output_links, "publish_entry", theirs_then_a_failure), self.assertRaises(OSError):
            staging.publish_folder()
        self.assertEqual(created_foreign, [True], "the intended second-entry fault actually ran")
        self.assertEqual(tree(self.root / "renders/anim"), ["passes", "passes/notes.txt"], "only theirs stays")
        self.assertEqual((self.root / "renders/anim/passes/notes.txt").read_bytes(), b"theirs")
        self.assertEqual(sorted(list_payload(staging.path / "passes")), ["depth.exr", "normal.exr"])
        self.assertEqual((staging.path / "frame_0001.png").read_bytes(), b"png1")
        staging.discard()


class NoLinkScenarios(Scenarios):
    """Scenarios only a volume without links has (it moves the staged files)."""

    def stuck_moves(self, staging):
        """A dependency's name taken after the main file moved, and no move back into staging that succeeds."""
        real_entry, real_move = job_output_links.publish_entry, job_output_links._move

        def a_dependency_name_taken(source, name, target, new_name, *args, **kwargs):
            identity = real_entry(source, name, target, new_name, *args, **kwargs)
            if new_name == "asset.usda":
                (self.root / "exports/textures").mkdir()   # Another program's, between two entries.
            return identity

        def never_back(source, name, target, new_name, *args):
            if target is staging.folder:
                raise OSError(errno.EIO, "Input/output error")
            return real_move(source, name, target, new_name, *args)
        stack = ExitStack()
        stack.enter_context(patch.object(job_outputs, "_publish_entry", a_dependency_name_taken))
        stack.enter_context(patch.object(job_output_links, "_move", never_back))
        return stack

    def test_a_set_whose_moved_file_cannot_go_back_is_never_published_again_and_says_what_stays(self):
        """Break-it (M4): a moved file that couldn't be moved back was swallowed; the set was published again into
        its folder without it, and the receipt said nothing was published."""
        staging = self.staged_set("jl")
        with self.stuck_moves(staging), self.assertRaises(OSError) as raised:
            staging.publish_file("asset.usda")
        self.assertIn("couldn't be moved back", str(raised.exception))
        self.assertEqual(tree(self.root / "exports"), [".angelo-job-jl", ".angelo-job-jl/.angelo-job",
                                                       ".angelo-job-jl/textures", ".angelo-job-jl/textures/a.png",
                                                       ".angelo-job-jl/textures/empty.mask", "asset.usda",
                                                       "textures"], "never published again into a folder")
        self.assertEqual((self.root / "exports/asset.usda").read_text(), "@./textures/a.png@", "stays, never lost")
        self.assertEqual(staging.left_published, ["exports/asset.usda"])
        kept = job_manager.kept_outputs(staging)
        self.assertEqual(job_manager.unpublished_receipt(staging, kept),
                         {"note": job_manager.KEPT_PARTLY, "kept_folder": "exports/.angelo-job-jl",
                          "left_published": ["exports/asset.usda"]})
        self.assertEqual(job_manager.unpublished_receipt(job_outputs.Staging(self.root, self.root / "x", "jy"), None),
                         {"note": job_manager.NOT_PUBLISHED})

    def test_a_web_export_that_stops_part_way_names_what_stays_and_keeps_the_rest(self):
        """Re-check N3: a web export's failed publication was discarded whole and said nothing of what stayed."""
        import angelo_agent
        before, bpy = set(vars(angelo_agent)), MagicMock()
        with patch.dict(sys.modules, {"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types,
                                      "bpy_extras": MagicMock()}):
            try:
                from angelo_agent import refusal_codes, web_host
                staging = self.staged_set("jv")
                with self.stuck_moves(staging), self.assertRaises(refusal_codes.RefusalError) as raised:
                    web_host._publish(None, staging, staging.output, {})
            finally:
                for name in set(vars(angelo_agent)) - before:
                    delattr(angelo_agent, name)
        self.assertEqual((raised.exception.code, raised.exception.details),
                         ("CLEANUP_FAILED", {"kept_folder": "exports/.angelo-job-jv",
                                             "left_published": ["exports/asset.usda"]}))
        self.assertIn("couldn't be moved back", str(raised.exception))
        self.assertTrue((self.root / "exports/.angelo-job-jv/textures/a.png").is_file(), "the rest: kept")

    def test_staging_on_another_volume_than_the_output_is_never_copied(self):
        staging = self.staging("renders/still.png", "jx")
        (staging.path / "frame_0001.png").write_bytes(b"png")
        real = job_output_links._device

        def another_device(folder):
            return real(folder) + (0 if folder is staging.folder else 1)
        with patch.object(job_output_links, "_device", another_device), self.assertRaises(OSError) as raised:
            staging.publish_file("frame_0001.png")
        self.assertEqual(raised.exception.errno, errno.EXDEV)
        self.assertEqual(list_payload(self.root / "renders"), [".angelo-job-jx"])
        self.assertEqual((staging.path / "frame_0001.png").read_bytes(), b"png", "kept whole in staging")
        staging.discard()


class WithoutLinks(NoLinkScenarios):
    """Scenarios on this host's file system, with links refused as exFAT and FAT refuse them."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-volumes-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        patcher = patch.object(cf, "link_into", refuse_links)
        patcher.start()
        self.addCleanup(patcher.stop)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class MovesRenumber(WithoutLinks, unittest.TestCase):
    """Windows FAT and exFAT number a file by its directory entry, so a move gives it another file ID, which the handle
    that moved it reports (break-it H1): every scenario with a move that renumbers the file it moves."""

    def setUp(self):
        super().setUp()
        real = job_output_links._move

        def renumbering(source, name, target, new_name, done=None):
            real(source, name, target, new_name, done)
            data = os.open(new_name, os.O_RDONLY, dir_fd=target)
            try:
                content = os.read(data, 1 << 20)
            finally:
                os.close(data)
            fresh = ".renumbered-" + new_name
            out = os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=target)
            try:
                os.write(out, content)
            finally:
                os.close(out)
            os.replace(fresh, new_name, src_dir_fd=target, dst_dir_fd=target)   # Another file ID, the same bytes.
            return os.stat(new_name, dir_fd=target, follow_symlinks=False)
        patcher = patch.object(job_output_links, "_move", renumbering)
        patcher.start()
        self.addCleanup(patcher.stop)


@unittest.skipIf(os.name == "nt", POSIX_ONLY)
class NoLinks(WithoutLinks, unittest.TestCase):
    def test_legacy_move_without_companion_proof_retains_publication(self):
        staging = self.staging("exports/asset.bin", "legacy")
        (staging.path / "asset.bin").write_bytes(b"payload")
        published = staging.publish_file("asset.bin")
        self.assertTrue(staging._moved)
        staging._moved[:] = [record[:3] for record in staging._moved]
        self.assertFalse(staging.unpublish())
        self.assertEqual(published.read_bytes(), b"payload")
        self.assertEqual(staging.left_published, ["exports/asset.bin"])
        staging.close()

    def test_native_xattr_volume_keeps_literal_dot_underscore_payload(self):
        staging = self.staging("renders/anim", "literal")
        (staging.path / "._literal.bin").write_bytes(b"ordinary-payload")
        published = staging.publish_folder()
        self.assertEqual((published / "._literal.bin").read_bytes(), b"ordinary-payload")
        self.assertEqual(tree(published), ["._literal.bin"])
        staging.close()


    def test_a_volume_without_the_exclusive_rename_runs_and_publishes_the_job(self):
        for label, call in (("no such call", False), ("ENOTSUP", refusing(errno.ENOTSUP)),
                            ("EINVAL", refusing(errno.EINVAL)), ("ENOSYS", refusing(errno.ENOSYS))):
            with self.subTest(label), patch.object(exclusive_rename, "_call", call):
                shutil.rmtree(self.root)
                self.root.mkdir()
                self.test_a_folder_output_and_a_set_are_published_whole()   # Refused at create() before.
                shutil.rmtree(self.root)
                self.root.mkdir()
                self.test_a_name_taken_mid_set_moves_the_whole_set_back_then_into_its_folder()

    def test_a_folder_another_put_at_the_name_in_place_is_never_published_into(self):
        """Without the exclusive rename a new folder is made in place: a non-empty folder found at its name afterwards
        is another's, left as it is; the output takes the next free name."""
        staging = self.staging("renders/anim", "jt")
        (staging.path / "frame_0001.png").write_bytes(b"png")
        theirs = self.root / "renders/theirs"
        theirs.mkdir()
        (theirs / "mine.txt").write_text("theirs")
        real_mkdir = os.mkdir

        def then_theirs_lands(name, mode=0o777, *, dir_fd=None):
            real_mkdir(name, mode, dir_fd=dir_fd)
            if name == "anim" and theirs.exists():
                os.rmdir(name, dir_fd=dir_fd)
                os.rename(theirs, self.root / "renders/anim")
        with patch.object(exclusive_rename, "_call", False), patch.object(exclusive_rename.os, "mkdir",
                                                                          then_theirs_lands):
            published = staging.publish_folder()
        staging.close()
        self.assertEqual(published, self.root / "renders/anim-jt")
        self.assertEqual(list_payload(self.root / "renders/anim"), ["mine.txt"], "their folder is left as it was")
        self.assertEqual(os.listdir(published), ["frame_0001.png"])

    def test_a_volume_that_reports_one_owner_for_every_folder_still_publishes(self):
        """Break-it (H2): a share mapping every owner to one (NFS all_squash) or a FAT volume of another login:
        a folder made in place read as another's, every name was refused and the job lost its output."""
        real_fstat = os.fstat

        def mapped(descriptor):
            info = real_fstat(descriptor)
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            values["st_uid"] = 1026
            return type("Mapped", (), values)()
        with patch.object(exclusive_rename, "_call", False), patch.object(exclusive_rename.os, "fstat", mapped):
            self.test_a_folder_output_and_a_set_are_published_whole()

    def test_every_name_taken_keeps_what_the_job_wrote(self):
        staging = self.staging("exports/asset.usda", "jk")
        (staging.path / "asset.usda").write_text("x")
        self.assertEqual(job_manager.kept_outputs(staging), "exports/.angelo-job-jk")
        self.assertIsNone(staging.folder, "let go of: nothing of it is held or removed any more")
        staging.discard()
        self.assertEqual(tree(self.root / "exports"), [".angelo-job-jk", ".angelo-job-jk/.angelo-job",
                                                       ".angelo-job-jk/asset.usda"])
        empty = self.staging("renders/still.png", "je")
        self.assertIsNone(job_manager.kept_outputs(empty))
        empty.discard()
        runners = (HERE.parents[2] / "scripts/addons_core/angelo_agent/job_runners.py").read_text(encoding="utf-8")
        self.assertIn("if (reason == job_outputs.TAKEN or left) and (kept := job_manager.kept_outputs(self.staging))",
                      runners)


@unittest.skipIf(os.name == "nt", environment_skip("on Windows RealWindowsNoLinks runs the real handle backend"))
class WindowsNoLinks(WithoutLinks, unittest.TestCase):
    """Windows FAT and exFAT: no links (``link_at`` ENOTSUP), a move is ``rename_at`` without replace."""

    def setUp(self):
        super().setUp()
        from fake_nt_files import FakeNtApi

        class NoLinkApi(FakeNtApi):
            def link_at(self, fd, directory, name):
                raise OSError(errno.ENOTSUP, "Operation not supported")
        self.api = NoLinkApi()
        patcher = patch.object(cf, "_windows_api", return_value=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        renames = [call for call in self.api.calls if call[0] == "rename_at"]
        self.assertFalse(any(call[2] for call in renames), "moves never replace: %r" % renames)


@unittest.skipUnless(os.name == "nt", environment_skip("the real Windows handle backend runs on Windows"))
class RealWindowsNoLinks(WithoutLinks, unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(anchored_windows.WindowsDirectory, "link_into", refuse_links)
        patcher.start()
        self.addCleanup(patcher.stop)


def _hdiutil(*arguments):
    return subprocess.run(["hdiutil", *arguments], capture_output=True, text=True, timeout=120)


_METADATA = (b"com.angelo.test.provenance", b"1")


def _set_metadata(path):
    """Give ``path`` an extended attribute, as macOS's provenance (com.apple.provenance) gives every file and folder a
    shipped, notarized Angelo writes: on FAT and exFAT the volume then keeps it in a real AppleDouble ``._`` partner."""
    libc = ctypes.CDLL(None, use_errno=True)
    name, value = _METADATA
    if libc.setxattr(os.fsencode(str(path)), name, value, len(value), 0, 0x0001) != 0:   # XATTR_NOFOLLOW
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))


def _set_metadata_fd(descriptor):
    libc = ctypes.CDLL(None, use_errno=True)
    name, value = _METADATA
    if libc.fsetxattr(descriptor, name, value, len(value), 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def provenance_on(device):
    """Patches that mark each folder and file created on ``device`` at its creation, as provenance does: the product
    records a staging folder's partner when it makes the folder, and a partner that appears later is foreign."""
    real_mkdir, real_open = os.mkdir, os.open

    def mark(descriptor):
        if os.fstat(descriptor).st_dev == device:
            _set_metadata_fd(descriptor)

    def mkdir(path, mode=0o777, *, dir_fd=None):
        real_mkdir(path, mode, dir_fd=dir_fd)
        descriptor = real_open(path, os.O_RDONLY, dir_fd=dir_fd)
        try:
            mark(descriptor)
        finally:
            os.close(descriptor)

    def open_(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if flags & os.O_CREAT and not os.path.basename(os.fsdecode(path)).startswith("._"):
            try:
                mark(descriptor)
            except BaseException:
                os.close(descriptor)
                raise
        return descriptor

    # The wrappers stand where the real functions are registered (``os.open in os.supports_dir_fd``, ...).
    sets = {}
    for registry in ("supports_dir_fd", "supports_fd", "supports_follow_symlinks", "supports_effective_ids"):
        current = getattr(os, registry)
        added = {wrapper for real, wrapper in ((real_mkdir, mkdir), (real_open, open_)) if real in current}
        sets[registry] = current | added
    return [patch.object(os, "mkdir", mkdir), patch.object(os, "open", open_)] + [
        patch.object(os, registry, value) for registry, value in sets.items()]


class OnVolume(Scenarios):
    """Each test on a small disk image of ``file_system``, attached without Finder and detached in cleanup."""
    file_system = None

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-volume-")
        self.addCleanup(temp.cleanup)
        # Resolved, as the folder below does: a person's volume mounts at /Volumes/<name>, never below macOS's /var
        # symlink, and snapshot storage refuses a directory link (checkpoint_filesystem).
        folder = Path(temp.name).resolve()
        image, mount = folder / "volume.dmg", folder / "mount"
        mount.mkdir()
        made = _hdiutil("create", "-quiet", "-size", "32m", "-fs", self.file_system, "-volname", "ANGELOTEST",
                        str(image))
        if made.returncode != 0:
            self.skipTest(environment_skip("hdiutil can't make %s here: %s" % (self.file_system, made.stderr[:200])))
        attached = _hdiutil("attach", "-quiet", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(image))
        if attached.returncode != 0:
            self.skipTest(environment_skip("hdiutil can't attach %s: %s" % (self.file_system, attached.stderr[:200])))
        self.addCleanup(lambda: _hdiutil("detach", "-quiet", str(mount)).returncode == 0
                        or _hdiutil("detach", "-quiet", "-force", str(mount)))
        self.root = mount / "workspace"
        self.root.mkdir()
        # Whether this process's own writes already carry metadata the volume keeps in ._ partners: a notarized,
        # downloaded Angelo's do (provenance); a development build's interpreter (macOS 26.6, Mac 2) writes none.
        probe = self.root / "probe"
        probe.write_bytes(b"")
        self.native_metadata = (self.root / "._probe").exists()
        probe.unlink()
        if (self.root / "._probe").exists():
            (self.root / "._probe").unlink()
        if not self.native_metadata:
            for patcher in provenance_on(self.root.stat().st_dev):
                patcher.start()
                self.addCleanup(patcher.stop)
        with cf._private_directory(self.root) as folder:
            observed = job_output_links.volume_info(folder)
        expected = {'ExFAT': 'exfat', 'MS-DOS': 'msdos', 'HFS+': 'hfs', 'APFS': 'apfs'}[self.file_system]
        self.assertEqual(observed['kind'], expected)
        PAIR_OBSERVATIONS.append(dict(test=self.id(), volume=observed))

    def put(self, path, data):
        """Write a file the way the shipped app does: with metadata unless this process already writes it."""
        path.write_bytes(data)
        if not self.native_metadata and not path.name.startswith("._"):
            _set_metadata(path)



class PairedScenarios:
    def test_paired_metadata_moves_with_payload_but_marker_is_never_payload(self):
        staging = self.staging('renders/anim', 'pair')
        self.put((staging.path / 'frame.png'), b'frame-data')
        source = staging.path / '._frame.png'
        self.assertTrue(source.is_file(), 'actual metadata exists on the mounted volume')
        before = source.read_bytes()
        import hashlib
        PAIR_OBSERVATIONS.append(dict(test=self.id(), phase="before", primary="frame.png",
            identity=[source.stat().st_dev, source.stat().st_ino], bytes=len(before),
            sha256=hashlib.sha256(before).hexdigest()))
        marker = staging.path / '._.angelo-job'
        self.assertTrue(marker.is_file())
        published = staging.publish_folder()
        self.assertEqual((published / 'frame.png').read_bytes(), b'frame-data')
        self.assertEqual((published / '._frame.png').read_bytes(), before)
        after = published / '._frame.png'
        PAIR_OBSERVATIONS.append(dict(test=self.id(), phase="after", primary="frame.png",
            identity=[after.stat().st_dev, after.stat().st_ino], bytes=len(before),
            sha256=hashlib.sha256(after.read_bytes()).hexdigest()))
        self.assertFalse(source.exists(), 'the actual partner moved, not independently published')
        self.assertFalse((published / '._.angelo-job').exists())
        self.assertFalse((published / '.angelo-job').exists())
        self.assertEqual(tree(published), ['frame.png'])
        staging.close()

    def test_destination_only_foreign_partner_is_preserved_when_source_has_none(self):
        staging = self.staging('exports/asset.bin', 'foreign-pair')
        payload = staging.path / 'asset.bin'
        self.put(payload, b'payload')
        companion = staging.path / '._asset.bin'
        if companion.exists():
            companion.unlink()  # Explicit fixture vector: source has no metadata; never a production remedy.
        foreign = self.root / 'exports/._asset.bin'
        self.put(foreign, b'foreign-not-AppleDouble')
        before = foreign.read_bytes()
        published = staging.publish_file('asset.bin')
        self.assertNotEqual(published, self.root / 'exports/asset.bin')
        self.assertEqual(foreign.read_bytes(), before)
        self.assertFalse((self.root / 'exports/asset.bin').exists())
        self.assertEqual(published.read_bytes(), b'payload')
        staging.close()

    def test_unknown_or_unpaired_staging_metadata_is_preserved_and_refuses(self):
        staging = self.staging('renders/anim', 'unknown-pair')
        foreign = staging.path / '._orphan'
        self.put(foreign, b'foreign-unpaired')
        with self.assertRaises((ValueError, OSError)):
            staging.publish_folder()
        self.assertEqual(foreign.read_bytes(), b'foreign-unpaired')
        self.assertFalse((self.root / 'renders/anim').exists())
        staging.close()
        self.assertEqual(foreign.read_bytes(), b'foreign-unpaired', 'unknown cleanup preserves the entry')

    def test_malformed_paired_metadata_is_preserved_before_mutation(self):
        staging = self.staging('renders/anim', 'malformed-pair')
        self.put((staging.path / 'frame.png'), b'frame-data')
        metadata = staging.path / '._frame.png'
        self.put(metadata, b'malformed-foreign-metadata')
        with self.assertRaises((ValueError, OSError)):
            staging.publish_folder()
        self.assertEqual((staging.path / 'frame.png').read_bytes(), b'frame-data')
        self.assertEqual(metadata.read_bytes(), b'malformed-foreign-metadata')
        self.assertFalse((self.root / 'renders/anim').exists())
        staging.close()
        self.assertEqual(metadata.read_bytes(), b'malformed-foreign-metadata')

    def test_successful_main_move_without_partner_completion_is_not_success(self):
        staging = self.staging('exports/asset.bin', 'partial-pair')
        payload = staging.path / 'asset.bin'
        self.put(payload, b'payload')
        metadata = (staging.path / '._asset.bin').read_bytes()
        real = exclusive_rename.rename
        injected = []
        def split_pair(source, name, target, new_name):
            real(source, name, target, new_name)
            if name == 'asset.bin':
                os.rename('._' + new_name, '._' + name, src_dir_fd=target, dst_dir_fd=source)
                injected.append(True)
        # These volumes normally use the owned-placeholder fallback; inject only that actual main move.
        real_replace = os.replace
        def split_replace(name, new_name, **options):
            real_replace(name, new_name, **options)
            if name == 'asset.bin':
                os.rename('._' + new_name, '._' + name,
                          src_dir_fd=options['dst_dir_fd'], dst_dir_fd=options['src_dir_fd'])
                injected.append(True)
        with patch.object(exclusive_rename, 'rename', split_pair), patch.object(os, 'replace', split_replace):
            with self.assertRaises((ValueError, OSError)):
                staging.publish_file('asset.bin')
        self.assertEqual(injected, [True], 'the primary moved and partner completion was independently undone')
        self.assertEqual((self.root / 'exports/asset.bin').read_bytes(), b'payload')
        self.assertEqual((staging.path / '._asset.bin').read_bytes(), metadata)
        self.assertEqual(staging.left_published, ['exports/asset.bin'])
        staging.close()


    def test_existing_staging_partner_refuses_creation_without_touching_foreign_bytes(self):
        (self.root / 'exports').mkdir()
        foreign = self.root / 'exports/._.angelo-job-existing-pair'
        self.put(foreign, b'foreign-staging-partner')
        with self.assertRaises((ValueError, OSError, job_manager.JobStartError)):
            self.staging('exports/asset.bin', 'existing-pair')
        self.assertEqual(foreign.read_bytes(), b'foreign-staging-partner')
        self.assertFalse((self.root / 'exports/.angelo-job-existing-pair').exists())

    def test_replaced_staging_partner_refuses_cleanup_before_payload_removal(self):
        staging = self.staging('exports/asset.bin', 'staging-pair')
        payload = staging.path / 'asset.bin'
        self.put(payload, b'kept-payload')
        target = staging.path.parent / ('._' + staging.name)
        original = target.read_bytes()
        other = staging.path.parent / 'other-staging-metadata'
        self.put(other, original)
        old = target.stat().st_ino
        os.replace(other, target)
        self.assertNotEqual(target.stat().st_ino, old)
        staging.close()
        self.assertEqual(payload.read_bytes(), b'kept-payload')
        self.assertEqual(target.read_bytes(), original)

    def test_late_destination_partner_before_move_is_preserved(self):
        staging = self.staging('exports/asset.bin', 'late-pair')
        self.put((staging.path / 'asset.bin'), b'payload')
        foreign = self.root / 'exports/._asset.bin'
        called = []
        def collision():
            self.put(foreign, b'late-foreign')
            called.append(True)
        with self.assertRaises(FileExistsError):
            job_output_links.publish_entry(staging.folder, 'asset.bin', staging.parent, 'asset.bin',
                                           staging._moved, before_move=collision)
        self.assertEqual(called, [True])
        self.assertEqual(foreign.read_bytes(), b'late-foreign')
        self.assertEqual((staging.path / 'asset.bin').read_bytes(), b'payload')
        self.assertFalse((self.root / 'exports/asset.bin').exists())
        staging.close()

    def test_replaced_published_companion_blocks_rollback_and_preserves_foreign_bytes(self):
        staging = self.staging('exports/asset.bin', 'swapped-pair')
        self.put((staging.path / 'asset.bin'), b'payload')
        published = staging.publish_file('asset.bin')
        target = published.parent / ('._' + published.name)
        original = target.read_bytes()
        other = published.parent / 'other-metadata'
        self.put(other, original)
        old = target.stat().st_ino
        os.replace(other, target)
        self.assertNotEqual(target.stat().st_ino, old, 'a different actual file owns the companion name')
        self.assertFalse(staging.unpublish())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(published.read_bytes(), b'payload')
        self.assertEqual(staging.left_published, ['exports/asset.bin'])
        staging.close()



ON_MACOS = unittest.skipUnless(sys.platform == "darwin" and shutil.which("hdiutil"),
                               environment_skip("real exFAT, FAT, HFS+ and APFS volumes are made with macOS hdiutil"))


@ON_MACOS
class ExFat(PairedScenarios, NoLinkScenarios, OnVolume, unittest.TestCase):
    file_system = "ExFAT"


@ON_MACOS
class Fat(PairedScenarios, NoLinkScenarios, OnVolume, unittest.TestCase):
    file_system = "MS-DOS"


@ON_MACOS
class Hfs(OnVolume, unittest.TestCase):
    file_system = "HFS+"


@ON_MACOS
class Apfs(OnVolume, unittest.TestCase):
    file_system = "APFS"


if __name__ == "__main__":
    unittest.main()
