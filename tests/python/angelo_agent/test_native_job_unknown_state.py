# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render's kept frames are never removed because a file could not be read: unknown is never absent. No Blender.

Regression (release audit of 1.0.4/1.0.5, P1): ``job_recovery`` took a folder or file it could not list, stat or
open (EIO, EACCES) for "absent" or "not whole", found no finished frame and removed the staging folder of a crashed
render with every finished frame in it. The audit's repro: 5 whole frames and 1 torn one, with EIO or EACCES at the
listing, the metadata or the frame's open; all six lost the frames. The same shape was in the render's own end
(``job_render_keep``), the marker read (a folder taken for another job's and forgotten) and the earlier attempt's
folder (``is_dir`` False on EACCES). P2: a valid OpenEXR file whose header is larger than ``MAX_EXR_HEADER`` read
as torn, although the contract says such a file is not checked (None).

Every case runs on this host's POSIX descriptors and on the Windows handle backend (``fake_nt_files``). The errno
values the fix was developed on are EIO and EACCES; the held-out ones (EPERM, ESTALE, ETIMEDOUT, ENXIO) and a real
``chmod 000`` were added after it, as were the node-folder, marker, earlier-folder and render-end call sites.
"""

import errno
import os
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import job_frame_check, job_outputs, job_recovery  # noqa: E402
from angelo_agent import job_render_outputs as render_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402
import job_frame_fixtures as fixtures  # noqa: E402
from test_native_job_recovery import render_runner  # noqa: E402

PLAN = [{"prefix": "frame_", "suffix": "", "extension": ".png", "main": True, "labels": {}}]
NODE_PLAN = PLAN + [{"prefix": "Passes/Depth", "suffix": "", "extension": ".exr", "main": False,
                     "labels": {"node": "Passes", "slot": "Depth"}}]
DEVELOPED = (errno.EIO, errno.EACCES)
HELD_OUT = (errno.EPERM, errno.ESTALE, errno.ETIMEDOUT, errno.ENXIO)
FRAME = "frame_0003.png"


def failing(original, number, when):
    """``original`` raising ``OSError(number)`` for the calls ``when(*arguments)`` picks."""
    def call(*arguments, **keywords):
        if when(*arguments):
            raise OSError(number, os.strerror(number))
        return original(*arguments, **keywords)
    return call


def injections(number, frame=FRAME):
    """Where a read can fail: the staging folder's listing, one frame's metadata, one frame's open."""
    listing = failing(cf.list_names, number, lambda folder: frame in _names(folder))
    metadata = failing(cf.entry_info, number, lambda folder, name: name == frame)
    opening = failing(cf._open_file, number, lambda folder, name, *rest: name == frame)
    return {"listing": patch.object(cf, "list_names", listing), "metadata": patch.object(cf, "entry_info", metadata),
            "open": patch.object(cf, "_open_file", opening)}


_LIST = cf.list_names


def _names(folder):
    try:
        return _LIST(folder)
    except (OSError, ValueError):
        return []


class _Recovery:
    """A workspace and a crashed render's staging folder (a mixin)."""
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-unknown-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        self.workspace = FileWorkspace(self.root)
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def crashed(self, plan=PLAN, done=5, identity="j4"):
        """A render a previous run left: ``done`` whole frames, then one cut short, nothing held."""
        output = job_outputs.admit_output(self.workspace, "renders/anim")
        staging = job_outputs.Staging(self.root, output, identity).create()
        for frame in range(1, done + 2):
            for item in plan:
                path = staging.path / render_outputs.file_name(item, frame)
                path.parent.mkdir(exist_ok=True)
                data = fixtures.png() if item["extension"] == ".png" else fixtures.exr()
                path.write_bytes(data[:len(data) // 2] if frame == done + 1 else data)
        staging._chain.close()
        record = new_record(identity, "chat", "call", {"kind": "RENDER_ANIMATION", "output": "renders/anim",
                                                      "format": "PNG", "minutes": 60.0}, now=1.0)
        record.update(status="interrupted", reason="app_closed", detail={"staging": str(staging.path),
                                                                          "frames": [1, 10]})
        record["detail"]["recovery"] = {"root": str(self.root), "output": str(output), "skip": False, "plan": plan,
                                        "frames": [1, 10], "blend": "", "scene": "Scene"}
        return record, staging.path

    def files(self, folder):
        return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())

    def assert_kept(self, record, staging, before):
        self.assertEqual(self.files(staging), before, "every staged file is still there")
        self.assertFalse((self.root / "renders/anim").exists(), "nothing published from an unknown state")
        self.assertEqual(record["detail"]["staging"], str(staging), "the record still points at its frames")
        self.assertEqual(job_recovery.pending([record]), [record], "the next start tries again")

    def assert_published_later(self, record):
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(self.files(self.root / "renders/anim"), ["frame_%04d.png" % n for n in range(1, 6)])
        self.assertEqual((record["receipt"]["kept"], record["receipt"]["torn"]), ("5/10 frames", 1))


class _RecoveryCases(_Recovery):
    def test_an_unreadable_listing_metadata_or_frame_keeps_every_frame(self):
        for number in DEVELOPED + HELD_OUT:
            for where in ("listing", "metadata", "open"):
                with self.subTest(errno=errno.errorcode[number], where=where):
                    self.setUp()
                    record, staging = self.crashed()
                    before = self.files(staging)
                    with injections(number)[where]:
                        self.assertTrue(job_recovery.recover(record, lambda: True))
                    self.assert_kept(record, staging, before)
                    self.assertEqual(record["receipt"]["note"], job_recovery.UNREAD)
                    self.assertEqual(record["receipt"]["error"], os.strerror(number))
                    self.assert_published_later(record)   # Readable again: published, the torn one dropped.

    def test_an_unreadable_node_folder_keeps_every_frame(self):
        record, staging = self.crashed(plan=NODE_PLAN)
        before = self.files(staging)
        entering = failing(cf._private_child, errno.EIO, lambda folder, name: name == "Passes")
        with patch.object(cf, "_private_child", entering):
            self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assert_kept(record, staging, before)

    def test_a_node_folder_never_made_is_still_no_files(self):
        record, staging = self.crashed(plan=NODE_PLAN, done=0)
        for path in (staging / "Passes").iterdir():
            path.unlink()
        (staging / "Passes").rmdir()
        (staging / "frame_0001.png").unlink()
        self.assertTrue(job_recovery.recover(record, lambda: True))   # Nothing finished: removed, as before.
        self.assertEqual(record["receipt"]["note"], job_recovery.NONE_FINISHED)
        self.assertFalse(staging.exists())

    def test_an_unreadable_marker_is_never_taken_for_another_jobs_folder(self):
        record, staging = self.crashed()
        before = self.files(staging)
        marker = failing(cf._open_file, errno.EIO, lambda folder, name, *rest: name == job_outputs.MARKER)
        with patch.object(cf, "_open_file", marker):
            self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assert_kept(record, staging, before)
        self.assertEqual(record["receipt"]["note"], job_recovery.UNREACHABLE)
        self.assert_published_later(record)

    def test_an_earlier_attempts_folder_that_cant_be_read_keeps_the_record(self):
        record, staging = self.crashed()
        record["detail"]["recover_into"] = "anim"
        for path in staging.iterdir():   # An earlier attempt published, then removed the staging folder.
            path.unlink()
        staging.rmdir()
        published = self.root / "renders/anim"
        published.mkdir()
        for frame in range(1, 6):
            (published / ("frame_%04d.png" % frame)).write_bytes(fixtures.png())
        for case, target in (("stat", "os.stat"), ("listing", "list")):
            with self.subTest(case=case):
                if case == "stat":
                    real = os.stat
                    hook = patch("angelo_agent.job_recovery_read.os.stat", failing(
                        real, errno.EACCES,
                        lambda path, *rest: isinstance(path, (str, os.PathLike)) and Path(path) == published))
                else:
                    hook = patch.object(cf, "iter_names", failing(cf.iter_names, errno.EIO, lambda folder: True))
                with hook:
                    self.assertTrue(job_recovery.recover(record, lambda: True))
                self.assertEqual(record["receipt"]["note"], job_recovery.UNREACHABLE)
                self.assertIn("staging", record["detail"], "never reported as fewer frames than exist")
        self.assertTrue(job_recovery.recover(record, lambda: True))   # Readable: says what is there.
        self.assertEqual(record["receipt"]["kept"], "5/10 frames")
        self.assertNotIn("staging", record["detail"])

class PosixRecoveryUnknown(_RecoveryCases, unittest.TestCase):
    pass


class WindowsRecoveryUnknown(_RecoveryCases, unittest.TestCase):
    windows = True


@unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX permissions as an ordinary user")
class RealPermissions(_Recovery, unittest.TestCase):
    """A real EACCES from the kernel (``chmod 000``), not an injected one."""

    def test_a_real_unreadable_frame_and_folder(self):
        for target in ("frame", "folder"):
            with self.subTest(target=target):
                self.setUp()
                record, staging = self.crashed(plan=NODE_PLAN)
                path = staging / (FRAME if target == "frame" else "Passes")
                before = self.files(staging)
                path.chmod(0)
                try:
                    self.assertTrue(job_recovery.recover(record, lambda: True))
                    self.assertEqual(record["receipt"]["note"], job_recovery.UNREAD)
                finally:
                    path.chmod(0o700 if target == "folder" else 0o600)
                self.assert_kept(record, staging, before)


class PosixRenderEndUnknown(unittest.TestCase):
    """The render's own end (``job_render_keep``): an unreadable staging folder is kept, never "no frame finished"."""
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-unknown-end-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_an_unreadable_staging_folder_keeps_the_frames_for_the_next_start(self):
        for number in DEVELOPED + HELD_OUT:
            for where in ("listing", "metadata", "open"):
                with self.subTest(errno=errno.errorcode[number], where=where):
                    output = job_outputs.admit_output(FileWorkspace(self.root), "renders/anim")
                    staging = job_outputs.Staging(self.root, output, "j4").create()
                    for frame in range(1, 7):
                        (staging.path / ("frame_%04d.png" % frame)).write_bytes(fixtures.png())
                    runner, stand_ins = render_runner(self.root, output, staging, PLAN, (1, 6))
                    path = staging.path
                    # The end of a plain render reads only its last frame (``last_only``).
                    with patch.dict(sys.modules, stand_ins), injections(number, "frame_0006.png")[where]:
                        status, reason, receipt = runner.finish(False)
                    self.assertEqual((status, reason, receipt["note"]), ("failed", job_recovery.RETAINED,
                                                                         job_recovery.UNREAD))
                    self.assertTrue(runner.retained.replace("\\", "/").endswith("/" + staging.name))
                    self.assertEqual(len([name for name in os.listdir(path) if name.endswith(".png")]), 6)
                    self.assertFalse((self.root / "renders/anim").exists())
                    for name in os.listdir(path):
                        os.unlink(path / name)
                    path.rmdir()


    def test_a_resumed_render_counts_its_own_frames_too(self):
        """Review of this change: the count ran before staging was removed, while each published file still had a
        second name, so this render's own frames were left out (pre-existing since the count was added)."""
        output = job_outputs.admit_output(FileWorkspace(self.root), "renders/anim")
        published = self.root / "renders/anim"
        published.mkdir(parents=True)
        for frame in range(1, 4):   # An earlier attempt's frames, already there.
            (published / ("frame_%04d.png" % frame)).write_bytes(fixtures.png())
        staging = job_outputs.Staging(self.root, output, "j4").create()
        for frame in range(4, 7):
            (staging.path / ("frame_%04d.png" % frame)).write_bytes(fixtures.png())
        runner, stand_ins = render_runner(self.root, output, staging, PLAN, (4, 6))
        runner.resume_into, runner.request = "anim", dict(runner.request, sequence=[1, 6])
        with patch.dict(sys.modules, stand_ins):
            status, _reason, receipt = runner.finish(False)
        self.assertEqual((status, receipt["sequence"]), ("done", "6/6 frames"))


class WindowsRenderEndUnknown(PosixRenderEndUnknown):
    windows = True


def large_header_exr(size=1 << 20, multipart=0):
    """A valid scanline OpenEXR file with an extra string attribute of ``size`` bytes, its offsets moved with it."""
    extra = fixtures._attribute("comments", "string", b"c" * size)
    row = b"\x00\x3c" * 4
    if multipart:
        head = b"\x76\x2f\x31\x01" + struct.pack("<i", 2 | 0x1000)
        for part in range(multipart):
            head += fixtures._header(4, 3, extra=extra + fixtures._attribute("name", "string", b"layer%d" % part)
                                     + fixtures._attribute("type", "string", b"scanlineimage")
                                     + fixtures._attribute("chunkCount", "int", struct.pack("<i", 3))) + b"\0"
        head += b"\0"
        position, tables, chunks = len(head) + 8 * 3 * multipart, b"", b""
        for part in range(multipart):
            for y in range(3):
                tables += struct.pack("<Q", position)
                piece = struct.pack("<iii", part, y, len(row)) + row
                chunks += piece
                position += len(piece)
        return head + tables + chunks
    head = b"\x76\x2f\x31\x01" + struct.pack("<i", 2) + fixtures._header(4, 3, extra=extra) + b"\0"
    position, table, chunks = len(head) + 8 * 3, b"", b""
    for y in range(3):
        table += struct.pack("<Q", position)
        piece = struct.pack("<ii", y, len(row)) + row
        chunks += piece
        position += len(piece)
    return head + table + chunks


def check(data):
    return job_frame_check.whole(lambda offset, count: data[offset:offset + count], len(data), ".exr")


class LargeHeader(unittest.TestCase):
    def test_a_header_larger_than_what_is_read_is_not_checked_never_torn(self):
        data = large_header_exr()
        self.assertEqual(len(data), 1048945)   # The audit's repro: a complete 1,048,945-byte image.
        self.assertIsNone(check(data))
        for size, multipart in ((3 << 20, 0), (1 << 20, 2)):   # Held out: larger, and a multipart file.
            with self.subTest(size=size, multipart=multipart):
                self.assertIsNone(check(large_header_exr(size, multipart)))

    def test_a_file_read_whole_is_still_checked(self):
        small = large_header_exr(size=(1 << 20) - 4096)   # Header under the limit: read whole, checked.
        self.assertIs(check(small), True)
        self.assertIs(check(small[:-3]), False)   # Last chunk cut short.
        self.assertIs(check(small[:(1 << 20) - 8192]), False)   # Cut inside a header that was read whole.
        self.assertIs(check(fixtures.exr()[:40]), False)
        bad = bytearray(fixtures.exr())
        struct.pack_into("<i", bad, bad.index(b"compression\0compression\0") + 24, -1)
        self.assertIs(check(bytes(bad)), False, "a malformed size inside what was read is still torn")

    def test_recovery_applies_its_unknown_format_policy(self):
        outputs = [{"prefix": "frame_", "suffix": "", "extension": ".exr", "main": True, "labels": {}}]
        present = {"frame_0001.exr", "frame_0002.exr"}
        answers = {path: check(large_header_exr()) for path in present}
        self.assertEqual(render_outputs.torn_frames(outputs, [1, 2], present, answers.get), set(),
                         "kept: an unchecked frame is never torn")
        self.assertEqual(render_outputs.torn_frames(outputs, [1, 2], present, answers.get, strict_last=True), {2},
                         "only the frame being written when the render stopped needs proof")


class FrameEntry(unittest.TestCase):
    def test_an_unreadable_file_or_one_that_changed_under_it_raises(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp).resolve()
            (folder / "frame_0001.png").write_bytes(fixtures.png())
            with cf._private_directory(folder) as held:
                self.assertIs(job_frame_check.check_entry(held, "frame_0001.png"), True)
                for number in DEVELOPED + HELD_OUT:
                    with self.subTest(errno=errno.errorcode[number]), patch.object(
                            cf, "_open_file", failing(cf._open_file, number, lambda *a: True)):
                        with self.assertRaises(OSError):
                            job_frame_check.check_entry(held, "frame_0001.png")
                if os.name != "nt":
                    # The scan passes only regular files: a link here means the name changed under it (unknown).
                    os.symlink(folder / "frame_0001.png", folder / "frame_0002.png")
                    with self.assertRaises(OSError):
                        job_frame_check.check_entry(held, "frame_0002.png")
                    if os.geteuid() != 0:
                        os.chmod(folder / "frame_0001.png", 0)
                        try:
                            with self.assertRaises(PermissionError):
                                job_frame_check.check_entry(held, "frame_0001.png")
                        finally:
                            os.chmod(folder / "frame_0001.png", stat.S_IRUSR | stat.S_IWUSR)


if __name__ == "__main__":
    unittest.main()
