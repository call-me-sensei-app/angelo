# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An animation render's finished frames survive a quit, a crash or a full disk. No Blender needed.

Regression (VFX audit A1, 2026-10-21): a quit or crash during an animation render
removed the job's staging folder at the next start, so every finished frame was
lost; stock Blender keeps them. A frame is kept only when it is whole: a torn file
(a crash or a full disk while Blender wrote it) is never published as a frame.

Every recovery test runs on this host's POSIX descriptors and on the Windows
handle backend through ``fake_nt_files.FakeNtApi``.
"""

import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import checkpoint_filesystem as cf  # noqa: E402
from angelo_agent import job_frame_check, job_outputs, job_recovery, job_resume  # noqa: E402
from angelo_agent import job_render_outputs as render_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402
from fake_nt_files import FakeNtApi  # noqa: E402
import job_frame_fixtures as fixtures  # noqa: E402


def check(data, extension):
    return job_frame_check.whole(lambda offset, count: data[offset:offset + count], len(data), extension)


class FrameCheck(unittest.TestCase):
    def test_png_and_jpeg_need_their_end(self):
        for data, extension in ((fixtures.png(), ".png"), (fixtures.jpeg(), ".jpg")):
            with self.subTest(extension=extension):
                self.assertIs(check(data, extension), True)
                for cut in (1, len(data) // 2, len(data) - 1):
                    self.assertIs(check(data[:cut], extension), False)
                self.assertIs(check(b"", extension), False)

    def test_openexr_needs_its_filled_offset_tables_and_last_chunk(self):
        for parts in (0, 2):
            with self.subTest(parts=parts):
                data = fixtures.exr(multipart=parts)
                self.assertIs(check(data, ".exr"), True)
                self.assertIs(check(fixtures.exr(multipart=parts, filled=False), ".exr"), False)   # Never closed.
                self.assertIs(check(data[:-3], ".exr"), False)   # The last chunk cut short.
                self.assertIs(check(data[:40], ".exr"), False)   # Cut inside the header.
        self.assertIs(check(b"not an exr at all", ".exr"), False)

    def test_what_cannot_be_checked_says_so(self):
        self.assertIsNone(check(fixtures.tiled_exr(), ".exr"))
        self.assertIsNone(check(b"II*\0 some tiff", ".tif"))
        self.assertIs(check(b"", ".tif"), False)   # No image format is empty.

    def test_the_frame_being_written_counts_only_when_proven_whole(self):
        outputs = [{"prefix": "frame_", "suffix": "", "extension": ".tif", "main": True, "labels": {}}]
        present = {"frame_0001.tif", "frame_0002.tif"}
        self.assertEqual(render_outputs.torn_frames(outputs, [1, 2], present, lambda path: None, strict_last=True),
                         {2})
        self.assertEqual(render_outputs.torn_frames(outputs, [1, 2], present, lambda path: None), set())
        self.assertEqual(render_outputs.torn_frames(outputs, [1, 2], present,
                                                    lambda path: path != "frame_0001.tif"), {1})
        read = []   # A plain render that ran in this app reads only its last frame: one file, not the sequence.
        render_outputs.torn_frames(outputs, [1, 2], present, lambda path: read.append(path), last_only=True)
        self.assertEqual(read, ["frame_0002.tif"])


PLAN = [{"prefix": "frame_", "suffix": "", "extension": ".png", "main": True, "labels": {}}]
NODE_PLAN = PLAN + [{"prefix": "Passes/Depth", "suffix": "", "extension": ".exr", "main": False,
                     "labels": {"node": "Passes", "slot": "Depth"}}]


class PosixRecovery(unittest.TestCase):
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-recovery-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        self.workspace = FileWorkspace(self.root)
        self.saves = 0
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def save(self):
        self.saves += 1

    def crashed(self, identity="j4", relative="renders/anim", frames=(1, 10), plan=PLAN, done=5, torn=True,
                legacy=False):
        """A render a previous run left: ``done`` whole frames, then (``torn``) one cut short, nothing held."""
        output = job_outputs.admit_output(self.workspace, relative)
        staging = job_outputs.Staging(self.root, output, identity).create()
        for frame in range(frames[0], frames[0] + done + (1 if torn else 0)):
            for item in plan:
                path = staging.path / render_outputs.file_name(item, frame)
                path.parent.mkdir(exist_ok=True)
                data = fixtures.png() if item["extension"] == ".png" else fixtures.exr()
                path.write_bytes(data[:len(data) // 2] if frame == frames[0] + done else data)
        staging._chain.close()   # The process ended: nothing is held any more.
        record = new_record(identity, "chat", "call", {"kind": "RENDER_ANIMATION", "output": relative,
                                                      "format": "PNG", "minutes": 60.0}, now=1.0)
        record.update(status="interrupted", reason="app_closed", detail={"staging": str(staging.path),
                                                                          "frames": list(frames)})
        if not legacy:
            record["detail"]["recovery"] = {"root": str(self.root), "output": str(output), "skip": False,
                                            "plan": plan, "frames": list(frames), "blend": "", "scene": "Scene"}
        return record, staging.path

    def names(self, folder):
        return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file())

    def test_finished_frames_are_kept_and_the_torn_one_is_not(self):
        record, staging = self.crashed()
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(self.names(self.root / "renders/anim"), ["frame_%04d.png" % n for n in range(1, 6)])
        self.assertFalse(staging.exists())
        receipt = record["receipt"]
        self.assertEqual((receipt["kept"], receipt["frames_total"], receipt["files"], receipt["torn"]),
                         ("5/10 frames", 10, 5, 1))
        self.assertEqual(receipt["note"], job_recovery.KEPT)
        self.assertEqual(record["detail"]["kept_frames"], [[1, 5]])
        self.assertNotIn("staging", record["detail"])
        self.assertIn("recovery", record["detail"])   # A resume renders the rest from it.
        # Relaunching again changes nothing.
        before = dict(record["receipt"])
        self.assertFalse(job_recovery.recover(record, self.save))
        self.assertEqual(record["receipt"], before)

    def test_a_taken_name_is_never_overwritten(self):
        (self.root / "renders/anim").mkdir(parents=True)
        (self.root / "renders/anim/frame_0001.png").write_bytes(b"the person's")
        record, _staging = self.crashed()
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual((self.root / "renders/anim/frame_0001.png").read_bytes(), b"the person's")
        self.assertEqual(record["receipt"]["output"], "renders/anim-j4")
        self.assertEqual(len(self.names(self.root / "renders/anim-j4")), 5)

    def test_a_full_disk_keeps_the_frames_for_the_next_start(self):
        record, staging = self.crashed()
        real, calls = cf.link_into, []

        def full(*args):
            calls.append(args)
            if len(calls) > 2:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(*args)
        with patch.object(cf, "link_into", full):
            self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(record["receipt"]["note"], job_recovery.NOT_YET)
        self.assertEqual(record["detail"]["recover_into"], "anim")
        self.assertTrue(staging.is_dir())   # Kept, marker and all.
        self.assertEqual(record["detail"]["staging"], str(staging))
        self.assertEqual(job_recovery.pending([record]), [record])
        # The next start goes on into the same folder: no second copy, nothing lost.
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(self.names(self.root / "renders"), ["anim/frame_%04d.png" % n for n in range(1, 6)])
        self.assertEqual(record["receipt"]["files"], 5)
        self.assertFalse(staging.exists())

    def test_a_failed_publication_published_later_no_longer_says_it_failed(self):
        record, _staging = self.crashed()
        record.update(status="failed", reason=job_recovery.RETAINED)
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual((record["status"], record["reason"], record["receipt"]["note"]),
                         ("failed", "", job_recovery.KEPT_LATER))

    def test_closing_after_publication_but_before_saving_is_reported_from_the_output(self):
        record, staging = self.crashed()
        saved_record = None

        def save():   # The record is saved with recover_into; the process ends right after the staging is removed.
            nonlocal saved_record
            saved_record = {key: (dict(value) if isinstance(value, dict) else value) for key, value in record.items()}
        job_recovery.recover(record, save)
        restarted = saved_record
        restarted["detail"]["staging"] = str(staging)   # The final save never happened.
        restarted["detail"]["recover_into"] = "anim"
        self.assertTrue(job_recovery.recover(restarted, self.save))
        self.assertEqual(restarted["receipt"]["kept"], "5/10 frames")
        self.assertNotIn("staging", restarted["detail"])

    def test_file_output_items_keep_whole_frames_only(self):
        record, _staging = self.crashed(plan=NODE_PLAN)
        self.assertTrue(job_recovery.recover(record, self.save))
        names = self.names(self.root / "renders/anim")
        self.assertEqual(len(names), 10)
        self.assertNotIn("Passes/Depth0006.exr", names)
        self.assertEqual(record["receipt"]["outputs"], [{"file": "frame_####.png"},
                                                        {"file": "Passes/Depth####.exr", "node": "Passes",
                                                         "slot": "Depth"}])

    def test_nothing_finished_publishes_nothing(self):
        record, staging = self.crashed(done=0)
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(record["receipt"]["note"], job_recovery.NONE_FINISHED)
        self.assertFalse(staging.exists())
        self.assertFalse((self.root / "renders/anim").exists())

    def test_a_link_in_staging_is_never_published_or_followed(self):
        if self.windows:
            self.skipTest("POSIX links")
        record, staging = self.crashed()
        (self.root / "secret.png").write_bytes(fixtures.png())
        (staging / "frame_0003.png").unlink()
        os.symlink(self.root / "secret.png", staging / "frame_0003.png")
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(self.names(self.root / "renders/anim"), ["frame_0001.png", "frame_0002.png",
                                                                  "frame_0004.png", "frame_0005.png"])
        self.assertTrue((self.root / "secret.png").exists())

    def test_a_folder_without_the_marker_is_never_touched(self):
        record, staging = self.crashed()
        (staging / job_outputs.MARKER).unlink()
        self.assertTrue(job_recovery.recover(record, self.save))   # Handled: left exactly as it is.
        self.assertTrue((staging / "frame_0001.png").exists())
        self.assertNotIn("staging", record["detail"])

    # Break-it review of 523ac24669e, finding 4: a drive not mounted at the next start, or a folder on the way
    # replaced by a link, dropped the record's staging key, so its frames stayed hidden for good.
    def test_an_unreachable_folder_is_kept_for_a_later_start(self):
        record, staging = self.crashed()
        away = self.root / "renders-away"
        os.rename(self.root / "renders", away)   # The drive or folder is not there now.
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(record["detail"]["staging"], str(staging))
        self.assertEqual(record["receipt"]["note"], job_recovery.UNREACHABLE)
        self.assertEqual(job_recovery.pending([record]), [record])
        os.rename(away, self.root / "renders")   # Back.
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(len(self.names(self.root / "renders/anim")), 5)

    # Finding 5a/5b: a save that failed silently, or a crash between making the folder and saving its name, split
    # the frames over two folders or left an empty one.
    def test_a_failed_save_publishes_nothing_and_a_made_folder_is_reused(self):
        record, staging = self.crashed()
        self.assertTrue(job_recovery.recover(record, lambda: False))
        self.assertFalse((self.root / "renders/anim").exists())   # Nothing made before the name was saved.
        self.assertTrue(staging.is_dir())
        record["detail"]["recover_into"] = "anim"
        (self.root / "renders/anim").mkdir()   # Made, then the process ended before any link.
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(sorted(os.listdir(self.root / "renders")), ["anim"])
        self.assertEqual(len(self.names(self.root / "renders/anim")), 5)

    # Finding 7: a record naming the staging folder itself as the folder to publish into deleted every frame.
    def test_a_record_never_publishes_into_a_staging_folder(self):
        record, staging = self.crashed()
        record["detail"]["recover_into"] = staging.name
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(len(self.names(self.root / "renders/anim")), 5)
        record, _staging = self.crashed(identity="j5", relative="renders/other")
        record["detail"]["recovery"]["frames"] = [1, 2_000_000]
        record["detail"]["frames"] = [1, 2_000_000]
        self.assertIsNone(job_recovery._usable(record["detail"]["recovery"]))   # Bounded like a job.

    def test_a_record_from_before_this_version_keeps_its_plain_frames(self):
        record, _staging = self.crashed(legacy=True)
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(record["receipt"]["kept"], "5/10 frames")

    def test_a_record_from_before_this_version_leaves_what_it_cannot_name(self):
        record, staging = self.crashed(legacy=True, plan=NODE_PLAN)
        self.assertTrue(job_recovery.recover(record, self.save))
        self.assertEqual(record["receipt"]["note"], job_recovery.LEFT_HIDDEN)
        self.assertTrue((staging / "frame_0001.png").exists() and (staging / "Passes/Depth0001.exr").exists())


    def test_the_rest_goes_into_the_same_folder_and_never_replaces_anything(self):
        record, _staging = self.crashed()
        job_recovery.recover(record, self.save)
        folder = self.root / "renders/anim"
        saved = record["detail"]["recovery"]
        self.assertEqual(job_resume.missing_frames(os.listdir(folder), saved), (6, 10, 5))
        request = job_resume.resume_request(record, 6, 10)
        self.assertEqual((request["output"], request["frame_start"], request["frame_end"], request["sequence"],
                          request["resume_of"], request["queue"]), ("renders/anim", 6, 10, [1, 10], "j4", True))
        (folder / "frame_0008.png").write_bytes(b"the person's")   # Appeared meanwhile: stays as it is.
        output = job_outputs.admit_output(self.workspace, "renders/anim")
        rest = job_outputs.Staging(self.root, output, "j9", skip_requested=True).create()
        files = ["frame_%04d.png" % n for n in range(6, 11)]
        for name in files:
            (rest.path / name).write_bytes(fixtures.png())
        path, linked, theirs = job_recovery.publish(rest, files, "anim")
        rest.close()
        self.assertEqual((path, linked, theirs), (folder, 4, 1))
        self.assertEqual((folder / "frame_0008.png").read_bytes(), b"the person's")
        self.assertEqual(len(os.listdir(folder)), 10)
        self.assertIsNone(job_resume.missing_frames(os.listdir(folder), saved))

    def test_the_rest_needs_the_same_file_scene_and_outputs(self):
        record, _staging = self.crashed()
        job_recovery.recover(record, self.save)
        saved = record["detail"]["recovery"]
        self.assertEqual(job_resume.resume_refusal(saved, blend="", scene="Scene"), job_resume.RESUME_UNSAVED)
        saved["blend"] = "/shots/sh010.blend"
        self.assertIsNone(job_resume.resume_refusal(saved, blend="/shots/sh010.blend", scene="Scene"))
        self.assertEqual(job_resume.resume_refusal(saved, blend="/shots/sh020.blend", scene="Scene"),
                         job_resume.RESUME_OTHER_FILE)
        self.assertEqual(job_resume.resume_refusal(saved, blend="/shots/sh010.blend", scene="Other"),
                         job_resume.RESUME_OTHER_FILE)
        self.assertEqual(job_resume.resume_refusal(saved, blend="/shots/sh010.blend", scene="Scene",
                                                     signature=render_outputs.signature(NODE_PLAN)),
                         job_resume.RESUME_CHANGED)
        record["detail"]["staging"] = "/left/for/the/next/start"   # Frames still waiting: nothing to resume yet.
        self.assertIsNone(job_resume.resume_facts(record))


class WindowsRecovery(PosixRecovery):
    windows = True


def render_runner(root, output, staging, plan, frames, identity="j4"):
    """A real RenderRunner over real staging, with bpy and job_ui stood in (only its finish path runs)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    stand_ins = {"bpy": MagicMock(), "bpy.app": MagicMock(), "bpy.app.handlers": MagicMock(),
                 "angelo_agent.job_ui": SimpleNamespace(keep_soon=lambda: None)}
    with patch.dict(sys.modules, stand_ins):
        from angelo_agent import job_runners
        runner = object.__new__(job_runners.RenderRunner)
    runner.request = {"kind": "RENDER_ANIMATION", "format": "PNG", "output": "renders/anim"}
    runner.plan = SimpleNamespace(outputs=plan, nodes=[item for item in plan if not item["main"]],
                                  single=len(plan) == 1, views=[])
    runner.staging, runner.overrides, runner.moved, runner.identity = staging, None, None, identity
    runner.start_frame, runner.end_frame = frames
    runner.total = frames[1] - frames[0] + 1
    runner.resume_into = runner.retained = runner.published = None
    runner.output, runner.workspace, runner.stop = output, FileWorkspace(root), lambda: None
    return runner, stand_ins


class PosixRenderEnd(unittest.TestCase):
    """Findings 1-3 of the break-it review: a render's own end removed every finished frame."""
    windows = False

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-end-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()
        if self.windows:
            patcher = patch.object(cf, "_windows_api", return_value=FakeNtApi())
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self, frames=6, plan=PLAN, relative="renders/anim"):
        output = job_outputs.admit_output(FileWorkspace(self.root), relative)
        staging = job_outputs.Staging(self.root, output, "j4").create()
        for frame in range(1, frames + 1):
            for item in plan:
                path = staging.path / render_outputs.file_name(item, frame)
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(fixtures.png() if item["extension"] == ".png" else fixtures.exr())
        runner, stand_ins = render_runner(self.root, output, staging, plan, (1, frames))
        return runner, stand_ins, staging

    def finish(self, runner, stand_ins, stopped=False):
        with patch.dict(sys.modules, stand_ins):
            return runner.finish(stopped)

    def frames_anywhere(self):
        return sorted(path.name for path in self.root.rglob("frame_*.png"))

    def test_a_stray_file_is_left_out_and_the_frames_are_published(self):
        runner, stand_ins, staging = self.render()
        (staging.path / ".DS_Store").write_bytes(b"finder")
        (staging.path / "._frame_0001.png").write_bytes(b"appledouble")
        status, _reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual(status, "done")
        self.assertEqual(len(os.listdir(self.root / "renders/anim")), 6)
        self.assertEqual(sorted(receipt["unexpected"]), [".DS_Store", "._frame_0001.png"])
        self.assertFalse(staging.path.exists())

    def test_a_moved_folder_keeps_the_frames_for_publication_where_it_went(self):
        if self.windows:
            self.skipTest("The real Windows backend refuses to move a held folder; the stand-in has no final path")
        runner, stand_ins, staging = self.render(relative="shot010/anim")
        os.rename(self.root / "shot010", self.root / "shot010_v2")
        runner._check_output_path()
        status, reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual((status, reason), ("failed", job_recovery.RETAINED))
        self.assertEqual(len(self.frames_anywhere()), 6)   # Nothing removed.
        self.assertEqual(Path(runner.retained), self.root / "shot010_v2" / staging.name)
        self.assertEqual(runner.recovery_update["output"], str(self.root / "shot010_v2/anim"))

    def test_more_files_than_a_walk_budget_are_published(self):
        with patch.object(job_outputs, "MAX_ENTRIES", 4):
            runner, stand_ins, _staging = self.render(frames=5, plan=NODE_PLAN)
            status, _reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual((status, receipt["files"]), ("done", 10))

    def test_every_name_taken_keeps_the_frames(self):
        runner, stand_ins, staging = self.render()
        with patch.object(job_recovery, "candidates", lambda *args: ["anim"]):
            (self.root / "renders/anim").mkdir()
            status, reason, receipt = self.finish(runner, stand_ins)
        self.assertEqual((status, reason, receipt["note"]), ("failed", job_recovery.RETAINED, job_recovery.NOT_YET))
        self.assertEqual(len(os.listdir(staging.path)), 7)   # Six frames and the marker, kept.


    def test_a_staging_folder_larger_than_a_walk_is_removed_whole(self):
        # vfx-sim-jobs' review: removing a job's own staging stopped at the walk budget and left it behind.
        output = job_outputs.admit_output(FileWorkspace(self.root), "renders/big")
        staging = job_outputs.Staging(self.root, output, "j9").create()
        (staging.path / "Passes").mkdir()
        for number in range(12):
            (staging.path / "Passes" / ("item%04d.exr" % number)).write_bytes(b"x")
        with patch.object(job_outputs, "MAX_ENTRIES", 4):
            staging.discard()
        self.assertFalse(staging.path.exists())
        leftover = job_outputs.Staging(self.root, output, "j10").create()
        (leftover.path / "Passes").mkdir()
        for number in range(12):
            (leftover.path / "Passes" / ("item%04d.exr" % number)).write_bytes(b"x")
        leftover._chain.close()
        with patch.object(job_outputs, "MAX_ENTRIES", 4):
            self.assertTrue(job_outputs.discard_path(leftover.path, "j10"))


class WindowsRenderEnd(PosixRenderEnd):
    windows = True


class Translated:
    def __enter__(self):
        from angelo_agent import ui_text
        self.ui_text, self.saved = ui_text, dict(ui_text._pgettext)
        ui_text._pgettext.update(iface=lambda message, context=None: "‹" + message + "›")

    def __exit__(self, *_):
        self.ui_text._pgettext.clear()
        self.ui_text._pgettext.update(self.saved)


class CardAndModel(unittest.TestCase):
    def record(self, **receipt):
        record = new_record("j4", "chat", "call", {"kind": "RENDER_ANIMATION", "output": "renders/anim",
                                                  "format": "PNG", "minutes": 60.0}, now=1.0)
        record.update(status="interrupted", reason="app_closed", started=1.0, ended=2.0,
                      receipt=dict({"kept": "12/120 frames", "frames_total": 120, "frames_kept": 12, "files": 24,
                                    "note": job_recovery.KEPT}, **receipt))
        record["detail"]["frames"] = [1, 120]
        return record

    def test_an_interrupted_render_says_how_much_exists_and_offers_the_rest(self):
        from angelo_agent import job_chat
        record = self.record()
        with Translated():
            text = job_chat.progress_text(record)
            rows = job_chat.buttons(record, True, (108, None))
        self.assertIn("‹Angelo closed during the render›", text)
        self.assertIn("12", text)
        self.assertIn("120", text)
        self.assertNotIn("24", text)   # Files are not frames: two files a frame here.
        self.assertEqual([row[1] for row in rows], ["job_open", "job_resume", "job_details"])
        self.assertIn("108", rows[1][0])
        self.assertIsNone(rows[1][3])
        disabled = job_chat.buttons(record, True, (108, job_resume.RESUME_OTHER_FILE))
        self.assertEqual(disabled[1][3], job_resume.RESUME_OTHER_FILE)
        self.assertEqual(job_chat.notice_text(record).rsplit(" · ", 1)[-1], "renders/anim")

    def test_long_buttons_wrap_inside_the_card(self):
        from angelo_agent import job_chat
        ops, _height = job_chat.card_layout(self.record(), 16, 600, 260, lambda text, width, size: [(text, None)],
                                            18, {}, has_output=True, resume=(108, None))
        for _kind, rect, *_rest in [op for op in ops if op[0] == "hit"]:
            self.assertLessEqual(rect[0] + rect[2], 16 + 260)

    def test_the_card_shows_why_the_rest_did_not_start(self):
        from angelo_agent import job_chat
        record = self.record()
        record["detail"]["resume_refused"] = job_resume.RESUME_CHANGED
        with Translated():
            ops, _height = job_chat.card_layout(record, 16, 600, 480, lambda text, width, size: [(text, None)], 18, {})
        self.assertIn("‹" + job_resume.RESUME_CHANGED + "›", [op[1] for op in ops if op[0] == "text"])

    def test_a_publication_waiting_for_the_next_start_says_so(self):
        from angelo_agent import job_chat
        with Translated():
            self.assertEqual(job_chat.progress_text(self.record(note=job_recovery.NOT_YET)),
                             "‹" + job_recovery.NOT_YET + "›")

    def test_the_agent_reads_what_was_kept_and_what_a_resume_finishes(self):
        from angelo_agent import job_store
        entry = job_store.context_entry(self.record())
        self.assertEqual((entry["status"], entry["kept"], entry["note"]),
                         ("interrupted", "12/120 frames", job_recovery.KEPT))
        resumed = new_record("j9", "chat", "", {"kind": "RENDER_ANIMATION", "output": "renders/anim",
                                                "resume_of": "j4", "sequence": [1, 120]}, now=3.0)
        resumed.update(status="done", receipt={"files": 108, "sequence": "120/120 frames"})
        entry = job_store.context_entry(resumed)
        self.assertEqual((entry["resumes"], entry["sequence"]), ("j4", "120/120 frames"))
        self.assertNotIn("root", json.dumps(entry))


class ManagerKeepsFrames(unittest.TestCase):
    def test_a_failed_finish_keeps_an_animations_frames_for_the_next_start(self):
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        staged = Path(folder.name) / ".angelo-job-j1"
        released = []

        class Staging:
            path, folder, published = staged, object(), None

        class Runner:
            identity, published, retained = None, None, None
            staging = Staging()

            def describe(self):
                return {}

            def recovery_facts(self):
                return {"root": folder.name, "output": str(Path(folder.name) / "anim"), "plan": PLAN,
                        "frames": [1, 4]}

            def start(self):
                pass

            def poll(self):
                return None

            def finish(self, stopped):
                raise RuntimeError("unexpected")

            def abandon(self):
                released.append("abandon")
        manager = JobManager(JobStore(folder.name))
        with patch.object(job_recovery, "release", lambda staging: released.append("release")):
            record = manager.submit("chat", "call", {"kind": "RENDER_ANIMATION", "output": "anim", "minutes": 5.0},
                                    Runner())
            manager.poll()
        self.assertEqual(record["status"], "failed")
        self.assertEqual(released, ["release"])   # Never abandon (remove) rendered frames.
        self.assertEqual(record["detail"]["staging"], str(staged))
        self.assertEqual(job_recovery.pending([record]), [record])


if __name__ == "__main__":
    unittest.main()
