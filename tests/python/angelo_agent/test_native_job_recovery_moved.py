# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An animation's recovery after an earlier attempt moved some of its files (a volume without hard links). No Blender.

Regression (break-it M1 of the stat-before-read branch, 2026-11-05): on exFAT, FAT and shares a publication moves
the staged files instead of linking them. An attempt that failed part way (a File Output pass it couldn't move) left
the frames it had moved only in its announced folder. The next start counted only staging, so a frame whose main
image had moved read as unfinished: its passes were dropped and thrown away with staging, and the receipt counted
none of the moved frames. Runs on this host's POSIX descriptors and on the Windows handle backend
(``fake_nt_files.FakeNtApi``), links refused as those volumes refuse them.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import job_outputs, job_recovery  # noqa: E402
from angelo_agent import job_render_outputs as render_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
import job_frame_fixtures as fixtures  # noqa: E402

PLAN = [{"prefix": "frame_", "suffix": "", "extension": ".png", "main": True, "labels": {}},
        {"prefix": "Passes/Depth", "suffix": "", "extension": ".exr", "main": False,
         "labels": {"node": "Passes", "slot": "Depth"}}]


def refuse_links(*_args):
    raise OSError(errno.ENOTSUP, "Operation not supported")


class PosixMovedEarlier(unittest.TestCase):
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-recovery-moved-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        patchers = [patch.object(cf, "link_into", refuse_links)]
        if self.windows:
            patchers.append(patch.object(cf, "_windows_api", return_value=FakeNtApi()))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def interrupted(self, moved, done=6, frames=(1, 10)):
        """A render that finished ``done`` frames, then an attempt that moved ``moved`` (relative paths) into its
        announced folder ``anim`` before it failed; nothing held."""
        output = job_outputs.admit_output(FileWorkspace(self.root), "renders/anim")
        staging = job_outputs.Staging(self.root, output, "jr").create()
        for frame in range(frames[0], frames[0] + done):
            for item in PLAN:
                path = staging.path / render_outputs.file_name(item, frame)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(fixtures.png() if item["extension"] == ".png" else fixtures.exr())
        staging._chain.close()
        into = self.root / "renders/anim"
        (staging.path / job_recovery.MOVED_NOTE).write_bytes("".join(path + "\n" for path in moved).encode())   # As MoveNote: LF, never CRLF   # As before a move.
        for relative in moved:
            (into / relative).parent.mkdir(parents=True, exist_ok=True)
            os.rename(staging.path / relative, into / relative)
        record = new_record("jr", "chat", "call", {"kind": "RENDER_ANIMATION", "output": "renders/anim",
                                                   "format": "PNG", "minutes": 60.0}, now=1.0)
        record.update(status="failed", reason="", detail={
            "staging": str(staging.path), "frames": list(frames), "recover_into": "anim",
            "recovery": {"root": str(self.root), "output": str(output), "skip": False, "plan": PLAN,
                         "frames": list(frames), "blend": "", "scene": "Scene"}})
        return record, staging.path

    def names(self, folder):
        return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())

    def test_frames_an_earlier_attempt_moved_are_kept_with_the_passes_still_staged(self):
        moved = ["frame_%04d.png" % frame for frame in range(1, 6)] + ["Passes/Depth%04d.exr" % frame
                                                                        for frame in range(1, 5)]
        record, staging = self.interrupted(moved)
        self.assertTrue(job_recovery.recover(record, lambda: True))
        names = self.names(self.root / "renders/anim")
        self.assertEqual(names, sorted(["frame_%04d.png" % frame for frame in range(1, 7)]
                                       + ["Passes/Depth%04d.exr" % frame for frame in range(1, 7)]),
                         "frame 5's pass, still staged, is published beside its moved image, never thrown away")
        self.assertEqual((record["receipt"]["kept"], record["receipt"]["frames_kept"]), ("6/10 frames", 6))
        self.assertEqual(record["detail"]["kept_frames"], [[1, 6]])
        self.assertFalse(staging.exists(), "removed only once every kept file is published")

    def test_a_whole_staged_folder_moved_earlier_is_still_published(self):
        moved = ["Passes/Depth%04d.exr" % frame for frame in range(1, 7)]
        record, staging = self.interrupted(moved)
        os.rmdir(staging / "Passes")   # Every file of it moved, and the folder gone since.
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(len(self.names(self.root / "renders/anim")), 12)
        self.assertEqual(record["receipt"]["frames_kept"], 6)

    def test_moved_files_whose_folder_was_renamed_leave_staging_kept_and_named(self):
        """Confirming review (L-6): the announced folder renamed since, so its moved images can't be found; their
        frames' passes, still staged, were thrown away with staging."""
        moved = ["frame_%04d.png" % frame for frame in range(1, 7)]
        record, staging = self.interrupted(moved)
        os.rename(self.root / "renders/anim", self.root / "renders/anim_renamed")
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(record["receipt"], {"note": job_recovery.MOVED_ELSEWHERE,
                                             "kept_folder": "renders/.angelo-job-jr"})
        self.assertEqual(len(os.listdir(staging / "Passes")), 6, "the passes: kept")

    def test_another_program_s_file_at_a_planned_name_the_job_never_moved_is_never_its_frame(self):
        """Re-check N1: only files the job's note says it moved count; the person's own render of a frame into the
        same folder is neither counted nor taken for the job's."""
        record, staging = self.interrupted(["frame_0001.png"], done=4)
        theirs = self.root / "renders/anim/frame_0005.png"
        theirs.write_bytes(b"the person's render")   # A planned name, never staged, never moved by the job.
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(record["receipt"]["frames_kept"], 4)
        self.assertEqual(record["detail"]["kept_frames"], [[1, 4]])
        self.assertEqual(theirs.read_bytes(), b"the person's render")

    def test_the_note_is_written_before_each_move_and_never_published(self):
        record, staging = self.interrupted([], done=3)
        moved_at_move = []
        real = job_recovery.publish_entry

        def watching(source, name, *args, before_move=None, **kwargs):
            def noted():
                before_move()
                moved_at_move.append((staging / job_recovery.MOVED_NOTE).read_text().split())   # Still staged.
            return real(source, name, *args, before_move=noted, **kwargs)
        with patch.object(job_recovery, "publish_entry", watching):
            self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertTrue(moved_at_move and "frame_0001.png" in moved_at_move[0], moved_at_move)
        self.assertNotIn(job_recovery.MOVED_NOTE, os.listdir(self.root / "renders/anim"))
        self.assertEqual(record["receipt"]["frames_kept"], 3)

    def test_a_planned_name_in_the_folder_that_is_still_staged_is_never_counted_twice(self):
        record, staging = self.interrupted(["frame_0001.png"])
        theirs = self.root / "renders/anim/frame_0002.png"
        theirs.write_bytes(b"another program's")   # Staged still: the staged frame is ours, theirs is left.
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(theirs.read_bytes(), b"another program's")
        self.assertEqual(record["receipt"]["present"], 1)
        self.assertEqual(record["receipt"]["frames_kept"], 6)
        self.assertEqual(record["receipt"]["kept_folder"], "renders/.angelo-job-jr", "its frame under that name: kept")
        self.assertTrue((staging / "frame_0002.png").is_file())


    def test_a_render_that_finds_another_file_at_a_frame_name_keeps_its_own_staged(self):
        """The same class at a render's own end (job_render_keep): a name in the folder it finishes held another
        file, and the staged frame under it was removed with staging."""
        from test_native_job_recovery import render_runner
        output = job_outputs.admit_output(FileWorkspace(self.root), "renders/anim")
        staging = job_outputs.Staging(self.root, output, "jk").create()
        for frame in range(1, 4):
            (staging.path / ("frame_%04d.png" % frame)).write_bytes(fixtures.png())
        runner, stand_ins = render_runner(self.root, output, staging, PLAN[:1], (1, 3), identity="jk")
        (self.root / "renders/anim").mkdir()
        (self.root / "renders/anim/frame_0002.png").write_bytes(b"another program's")
        runner.resume_into = "anim"   # The folder of the render it finishes.
        with patch.dict(sys.modules, stand_ins):
            _status, _reason, receipt = runner.finish(False)
        self.assertEqual((receipt["present"], receipt["kept_folder"]), (1, "renders/.angelo-job-jk"))
        self.assertEqual((self.root / "renders/anim/frame_0002.png").read_bytes(), b"another program's")
        self.assertEqual((staging.path / "frame_0002.png").read_bytes(), fixtures.png(), "its own frame: kept")


@unittest.skipIf(os.name == "nt", environment_skip("the Windows stand-in runs on POSIX; Windows runs the real backend"))
class WindowsMovedEarlier(PosixMovedEarlier):
    windows = True


if __name__ == "__main__":
    unittest.main()
