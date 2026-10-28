# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render job's deliverable options (VFX audit A2), without Blender: formats, frame-name patterns and frame step.

Stock Blender writes the scene's own format and file name, with the frame number
padded to the length of the name's last '#' run, on the scene's frame step. A job
now can too (``format`` SCENE, ``name``, ``frame_step``), and every place that
names or counts frames follows the same rules: the plan, the check of what was
written, recovery after a crash, rendering the rest, and the outputs the agent
reads back.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import job_outputs, job_recovery, job_resume, job_spec, job_store  # noqa: E402
from angelo_agent import job_render_outputs as render_outputs  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402
import job_frame_fixtures as fixtures  # noqa: E402


def start(**fields):
    return job_spec.validate_job("angelo_job_start", dict({"kind": "RENDER_ANIMATION", "output": "shots/sh010"},
                                                          **fields))


class Requests(unittest.TestCase):
    def test_the_scenes_own_format(self):
        # One enum item for every format Blender writes (multilayer OpenEXR, TIFF, DPX...): the scene's own image
        # settings, which property_set writes. Claude's and Codex's instructions carry this schema on every turn.
        self.assertEqual(start(format="SCENE")["format"], "SCENE")
        self.assertEqual(job_spec.JOB_TOOLS[0]["inputSchema"]["properties"]["format"]["enum"],
                         ["PNG", "JPEG", "OPEN_EXR", "SCENE"])
        still = job_spec.validate_job("angelo_job_start", {"kind": "RENDER_STILL", "output": "plate",
                                                           "format": "SCENE"})
        self.assertEqual(still["output"], "plate")   # Its suffix is the scene's, checked at admission.

    def test_a_frame_name_pattern_ends_in_its_padding(self):
        for good in ("sh010_v003.####", "plate-a.#", "A.########"):
            self.assertEqual(start(name=good)["name"], good)
        for bad in ("sh010", "sh010.####.exr", "####", "../x.####", "a{b}.####", "a.#########", "a b.####",
                    "con.####", "LPT1.##"):
            with self.subTest(name=bad), self.assertRaises(job_spec.JobRequestError):
                start(name=bad)

    def test_names_that_become_devices_or_extensions_are_refused(self):
        # Break-it review of 553ea27d7b3: COM# wrote COM1.exr ... on Windows; x.jp# wrote x.exr at frame 2.
        for bad in ("COM#", "lpt##", "CON.shot.####", "nul.#", "x.jp#", "plate.jp##"):
            with self.subTest(name=bad), self.assertRaises(job_spec.JobRequestError):
                start(name=bad)
        for good in ("COMP.####", "console.##", "x.jpg_##", "x.jpeg.####", "com_shot.#"):
            self.assertEqual(start(name=good)["name"], good)

    def test_frame_step_is_the_scenes_and_counts_the_frames(self):
        self.assertEqual(job_spec.frame_count(1, 10, 3), 4)   # 1, 4, 7, 10.
        self.assertEqual(job_spec.check_frames(1, 20_000, step=2), 10_000)   # Within the bound on a step of 2.
        with self.assertRaises(job_spec.JobRequestError):
            job_spec.check_frames(1, 20_002, step=2)
        with self.assertRaises(job_spec.JobRequestError):   # Not a request field: the scene's own, as F12.
            start(frame_step=2)

    def test_a_still_takes_no_name(self):
        for field in ({"name": "a.####"},):
            with self.subTest(field=field), self.assertRaises(job_spec.JobRequestError):
                job_spec.validate_job("angelo_job_start", dict({"kind": "RENDER_STILL", "output": "a"}, **field))


class TiffCheck(unittest.TestCase):
    def check(self, data):
        from angelo_agent import job_frame_check
        return job_frame_check.whole(lambda offset, count: data[offset:offset + count], len(data), ".tif")

    def test_a_tiff_needs_its_directory_and_every_strip(self):
        for order in ("<", ">"):
            for big in (False, True):
                with self.subTest(order=order, big=big):
                    data = fixtures.tiff(order, big)
                    self.assertIs(self.check(data), True)
                    for cut in range(1, len(data)):
                        self.assertIs(self.check(data[:cut]), False, cut)
        self.assertIs(self.check(fixtures.tiff(directory_at_end=False)), False)   # Never linked: not closed.

    def test_a_malformed_tiff_is_never_read_at_its_declared_size(self):
        import struct
        for count in (2 ** 63, 2 ** 46, 70000):   # Break-it review: OverflowError and MemoryError escaped.
            data = b"II" + struct.pack("<HHHQ", 43, 8, 0, 16) + struct.pack("<Q", count) + b"\0" * 32
            self.assertIs(self.check(data), False)


class Names(unittest.TestCase):
    def test_padding_and_stem_name_every_file_as_blender_does(self):
        main = render_outputs.plan(".exr", [], "INDIVIDUAL", [], "sh010_v003.", 3)[0]
        self.assertEqual(render_outputs.file_name(main, 7), "sh010_v003.007.exr")
        self.assertEqual(render_outputs.file_name(main, 12345), "sh010_v003.12345.exr")   # More digits when needed.
        self.assertEqual(render_outputs.pattern(main), "sh010_v003.###.exr")
        default = render_outputs.plan(".png", [], "INDIVIDUAL", [])[0]
        self.assertEqual(render_outputs.file_name(default, 7), "frame_0007.png")   # Unchanged for every old job.
        self.assertNotEqual(render_outputs.signature([main]), render_outputs.signature([dict(main, digits=4)]))

    def test_a_file_output_folder_never_takes_a_frame_files_name(self):
        # Break-it review: a node named sh010.0001.exr made a folder at frame 1's file name.
        self.assertEqual(render_outputs.folder_name("SH010.0001.exr", set(), "sh010."), "node-SH010.0001.exr")
        self.assertEqual(render_outputs.folder_name("frame_x", set()), "node-frame_x")

    def test_a_step_names_only_the_frames_on_it(self):
        main = render_outputs.plan(".png", [], "INDIVIDUAL", [])[0]
        names = render_outputs.main_names([main], 1, 10, 3)
        self.assertEqual(sorted(names.values()), [1, 4, 7, 10])
        found = render_outputs.sort_files([main], 1, 10, {"frame_0001.png", "frame_0004.png", "frame_0002.png"}, 3)
        self.assertEqual((found["frames"], found["unexpected"]), ([1, 4], ["frame_0002.png"]))

    def test_the_reported_pattern_reads_back_with_any_padding_and_step(self):
        record = new_record("j2", "chat", "call", {"kind": "RENDER_ANIMATION", "output": "shots/sh010"}, now=1.0)
        record["detail"].update(frames=[1, 10], frame_step=3)
        record["receipt"] = {"outputs": [{"file": "sh010_v003.##.exr"}, {"file": "Passes/Depth####.exr"}]}
        self.assertEqual(job_store.output_file_name(record, "sh010_v003.##.exr", 4), "sh010_v003.04.exr")
        self.assertIsNone(job_store.output_file_name(record, "sh010_v003.##.exr", 5))   # Off its step.
        self.assertEqual(job_store.output_file_name(record, "Passes/Depth####.exr", 10), "Passes/Depth0010.exr")
        self.assertEqual(job_store.frame_run("a.b/c##d.exr"), ("a.b/c", 2, "d.exr"))
        self.assertIsNone(job_store.frame_run("plain.exr"))


class SteppedRecovery(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="angelo-job-deliverables-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "workspace"
        self.root.mkdir()

    def test_a_stepped_named_render_keeps_its_whole_frames_and_resumes_on_its_grid(self):
        plan = [{"prefix": "sh010_v003.", "suffix": "", "extension": ".png", "main": True, "labels": {},
                 "digits": 3}]
        output = job_outputs.admit_output(FileWorkspace(self.root), "shots/sh010")
        staging = job_outputs.Staging(self.root, output, "j4").create()
        for frame in (1, 3, 5):
            (staging.path / ("sh010_v003.%03d.png" % frame)).write_bytes(fixtures.png())
        (staging.path / "sh010_v003.007.png").write_bytes(fixtures.png()[:30])   # Torn by the crash.
        staging._chain.close()
        record = new_record("j4", "chat", "call", {"kind": "RENDER_ANIMATION", "output": "shots/sh010",
                                                  "name": "sh010_v003.###", "frame_step": 2, "minutes": 60.0}, now=1.0)
        record.update(status="interrupted", reason="app_closed")
        record["detail"].update(staging=str(staging.path), frames=[1, 11], recovery={
            "root": str(self.root), "output": str(output), "skip": False, "plan": plan, "frames": [1, 11],
            "step": 2, "blend": "/shots/sh010.blend", "scene": "Scene"})
        self.assertTrue(job_recovery.recover(record, lambda: True))
        self.assertEqual(sorted(os.listdir(self.root / "shots/sh010")),
                         ["sh010_v003.001.png", "sh010_v003.003.png", "sh010_v003.005.png"])
        self.assertEqual((record["receipt"]["kept"], record["receipt"]["torn"]), ("3/6 frames", 1))
        self.assertEqual(record["detail"]["kept_frames"], [[1, 5]])
        saved = record["detail"]["recovery"]
        self.assertEqual(job_resume.missing_frames(os.listdir(self.root / "shots/sh010"),
                                                   job_recovery._usable(saved)), (7, 11, 3))
        request = job_resume.resume_request(record, 7, 11)
        self.assertEqual((request["name"], request["frame_step"], request["frame_start"]), ("sh010_v003.###", 2, 7))

    def test_records_from_before_step_and_padding_read_as_before(self):
        saved = {"root": "/w", "output": "/w/anim", "frames": [1, 4],
                 "plan": [{"prefix": "frame_", "suffix": "", "extension": ".png", "main": True}]}
        usable = job_recovery._usable(saved)
        self.assertEqual((usable["step"], usable["plan"][0]["digits"]), (1, 4))
        self.assertIsNone(job_recovery._usable(dict(saved, step=0)))
        self.assertIsNone(job_recovery._usable(dict(saved, plan=[dict(saved["plan"][0], digits=9)])))

    def test_a_changed_scene_format_refuses_the_rest(self):
        saved = {"blend": "/a.blend", "scene": "S", "plan": [], "format": {"file_format": "OPEN_EXR",
                                                                           "codec": "DWAA"}}
        self.assertIsNone(job_resume.resume_refusal(saved, blend="/a.blend", scene="S",
                                                    image_format={"file_format": "OPEN_EXR", "codec": "DWAA"}))
        self.assertEqual(job_resume.resume_refusal(saved, blend="/a.blend", scene="S",
                                                   image_format={"file_format": "OPEN_EXR", "codec": "PIZ"}),
                         job_resume.RESUME_CHANGED)


if __name__ == "__main__":
    unittest.main()
