# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image`` and ``file`` on the host: one output of a job that published several.

The fixture of ``test_native_view_finished`` (a stand-in Blender and ``imbuf``)
with job records shaped as the production runners write them: a stereo still
with File Output items, a stereo animation, a baked normal map, a UDIM bake.
Each test plants picture files in a job's published folder and reads the
delivered PNG back. The resolution of a reported name to a file is the real
code (``job_store.published_file`` and ``published_frames``).

The compiled suite ``test_blender_job_output_views`` does the same with files
real jobs wrote, with OpenEXR values and with the payload each runtime hands a
model.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import job_manager, job_store, request_path, view_picture, view_spec  # noqa: E402
from angelo_agent.job_manager import JobManager  # noqa: E402
from angelo_agent.job_spec import BAKE_TYPES, DATA_MAPS  # noqa: E402
from angelo_agent.job_store import JobStore, mark_shown  # noqa: E402
from angelo_agent.telemetry import RequestMetrics  # noqa: E402
from test_native_long_jobs import FakeRunner  # noqa: E402
from test_native_request_path import view, view_result  # noqa: E402
from test_native_view_finished import GREY, RED, FinishedCase, picture  # noqa: E402

IMAGE = "dat:" + "c" * 32 + ":IMAGE:41"
BLUE, FLAT = (0, 0, 255, 255), (128, 128, 255, 255)
STEREO = [{"file": "frame_0001_L.png", "view": "left"}, {"file": "frame_0001_R.png", "view": "right"},
          {"file": "Passes/Mask0001_L.png", "node": "Passes", "slot": "Mask", "view": "left"},
          {"file": "Passes/Mask0001_R.png", "node": "Passes", "slot": "Mask", "view": "right"},
          {"file": "Layers/layers0001.exr", "node": "Layers", "layers": ["Beauty", "Mask"], "views": ["left", "right"]}]
VIEW = dict({"targets": [], "views": ["SCENE_CAMERA"], "shading": "RENDERED"})


class OutputCase(FinishedCase):
    """``FinishedCase`` whose job stand-in resolves outputs with the job store's own code."""

    def setUp(self):
        super().setUp()
        job_ui = sys.modules["angelo_agent.job_ui"]
        job_ui.output_file = lambda record, name=None, frame=None: job_store.published_file(
            job_ui.output_path(record), record, name, frame)
        job_ui.output_frames = lambda record, name: job_store.published_frames(job_ui.output_path(record), record, name)

    def published(self, folder, files, outputs, kind="RENDER_STILL", identity="j3", **changes):
        """A finished job whose published folder holds ``files`` ({relative name: RGBA colour}) and reports ``outputs``."""
        for name, colour in files.items():
            self.file("%s/%s" % (folder, name), picture(64, 36, colour))
        record = {"request": {"output": "renders/" + folder}, "receipt": {"outputs": outputs, "output": "renders/" + folder},
                  "detail": {"engine": "CYCLES", "samples": 64, "output_path": str(self.folder / folder)}}
        for key, value in changes.items():
            record[key] = dict(record.get(key, {}), **value) if isinstance(value, dict) else value
        return self.job(self.folder / folder, kind=kind, identity=identity, **record)

    def stereo(self):
        return self.published("shot", {"frame_0001_L.png": GREY, "frame_0001_R.png": RED, "Passes/Mask0001_L.png": BLUE,
                                       "Passes/Mask0001_R.png": GREY}, STEREO)

    def colours(self, result):
        return {tuple(pixel) for pixel in self.delivered(result).reshape(-1, 4).tolist()}

    def refusal(self, image="j3", **checked):
        with self.assertRaises(RuntimeError) as caught:
            self.show(image, **checked)
        return str(caught.exception)


class ContractTests(unittest.TestCase):
    def checked(self, **arguments):
        return view_spec.validate_view("angelo_view", dict(VIEW, **arguments))

    def test_file_names_one_output_of_a_job(self):
        checked = self.checked(image="j3", file="Passes/Mask0001_R.png")
        self.assertEqual((checked["image"], checked["file"]), ("j3", "Passes/Mask0001_R.png"))
        self.assertEqual(self.checked(image="j3", file="frame_####_R.png", frame=4)["frame"], 4)
        alias = self.checked(image="LAST_JOB", file="frame_####_R.png", frame=4)
        self.assertEqual((alias["image"], alias["file"], alias["frame"]), ("LAST_JOB", "frame_####_R.png", 4))

    def test_file_needs_a_job_and_a_bounded_name(self):
        for arguments, message in (
                ({"file": "frame_0001_R.png"}, "pass that job's ID or LAST_JOB as image"),
                ({"image": "RENDER_RESULT", "file": "frame_0001_R.png"}, "pass that job's ID or LAST_JOB as image"),
                ({"image": "j3", "file": ""}, "(this one has 0)"),
                ({"image": "j3", "file": 7}, "(this one has 0)"),
                ({"image": "j3", "file": "a\nb.png"}, "printable"),
                ({"image": "j3", "file": "a" * (view_spec.MAX_FILE + 9)},
                 "at most %d printable characters (this one has %d)" % (view_spec.MAX_FILE, view_spec.MAX_FILE + 9))):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError) as caught:
                self.checked(**arguments)
            self.assertIn(message, str(caught.exception))
            self.assertNotIn("\n", str(caught.exception))

    def test_the_schema_states_the_limit_the_host_enforces(self):
        properties = view_spec.VIEW_TOOLS[0]["inputSchema"]["properties"]
        self.assertEqual(properties["file"], {"type": "string", "maxLength": view_spec.MAX_FILE})

    def test_the_maps_shown_without_a_transform_are_bake_types(self):
        self.assertTrue(DATA_MAPS <= set(BAKE_TYPES))
        self.assertTrue({"NORMAL", "POSITION", "ROUGHNESS"} <= DATA_MAPS)
        self.assertFalse(DATA_MAPS & {"COMBINED", "DIFFUSE", "EMIT"})


class StoredValuesTests(unittest.TestCase):
    def test_values_from_zero_to_one_are_shown_as_they_are(self):
        values = numpy.zeros((2, 3, 4), dtype=numpy.float32)
        values[..., :3] = (0.5, 0.25, 1.0)
        values[..., 3] = 1.0
        values[0, 0, :3] = (-0.5, 2.0, 0.0)  # Outside: clipped and counted.
        values[1, 2, :3] = (float("nan"), 0.0, float("inf"))
        rows, clipped = view_picture.stored_bytes(values)
        self.assertEqual(rows.dtype, numpy.uint8)
        self.assertEqual(tuple(rows[0, 1]), (128, 64, 255, 255), "No transfer function: 0.5 is 128, not sRGB's 188")
        self.assertEqual(tuple(rows[0, 0]), (0, 255, 0, 255))
        self.assertEqual(tuple(rows[1, 2]), (0, 0, 255, 255))
        self.assertEqual(clipped, round(2 / 6, 4))
        self.assertIn("33% of its pixels hold values outside 0 to 1", view_picture.clipped_note(clipped))
        self.assertEqual(view_picture.clipped_note(0.0), "")


class RenderOutputTests(OutputCase):
    def test_one_view_of_a_stereo_still_is_shown(self):
        self.stereo()
        right = self.show(file="frame_0001_R.png")
        left = self.show(file="frame_0001_L.png")
        self.assertEqual(self.colours(right), {RED}, "The planted red is in the right view")
        self.assertEqual(self.colours(left), {GREY}, "and not in the left")
        self.assertEqual(right["rendered"], {"job": "j3", "output": "renders/shot", "file": "frame_0001_R.png",
                                             "source": "CURRENT_OUTPUT_FILE",
                                             "view": "right", "engine": "CYCLES", "samples": 64, "complete": True,
                                             "job_complete": True,
                                             "source_size": [64, 36]})
        self.assertIn("View right from job j3 (renders/shot/frame_0001_R.png), 64x36.", right["note"])
        self.assertIn("current output file", right["note"])
        self.assertNotIn("Only the combined image", right["note"])
        self.assertEqual(self.blender.saved, [], "An 8-bit picture gets no second view transform")

    def test_one_file_output_item_is_shown(self):
        self.stereo()
        result = self.show(file="Passes/Mask0001_L.png")
        self.assertEqual(self.colours(result), {BLUE})
        self.assertEqual((result["rendered"]["node"], result["rendered"]["slot"], result["rendered"]["view"]),
                         ("Passes", "Mask", "left"))
        self.assertIn("File Output Passes: Mask, view left from job j3", result["note"])

    def test_a_region_of_one_output(self):
        self.stereo()
        result = self.show(file="frame_0001_R.png", region=[0.0, 0.0, 0.5, 0.5])
        self.assertEqual(result["rendered"]["region_pixels"], [32, 18])

    def test_without_file_the_refusal_names_the_outputs(self):
        self.stereo()
        message = self.refusal()
        self.assertTrue(message.startswith("Job j3 wrote several pictures: pass file with one of its outputs: "))
        for part in ("frame_0001_L.png (view left)", "frame_0001_R.png (view right)",
                     "Passes/Mask0001_R.png (File Output Passes: Mask, view right)"):
            self.assertIn(part, message)

    def test_a_long_list_is_bounded(self):
        outputs = [{"file": "Passes/Item%d0001.png" % number, "node": "Passes", "slot": "Item%d" % number}
                   for number in range(20)]
        self.published("many", {}, outputs)
        message = self.refusal()
        self.assertIn("and 12 more in its outputs", message)
        self.assertLess(len(message), 900)

    def test_only_a_name_the_job_reported_is_a_file(self):
        self.stereo()
        (self.folder / "secret.png").write_bytes((self.folder / "shot/frame_0001_R.png").read_bytes())
        for name in ("frame_0001_X.png", "../secret.png", str(self.folder / "secret.png"), "Passes", "frame_0001_r.png"):
            with self.subTest(name=name):
                message = self.refusal(file=name)
                self.assertIn("Job j3 reports no output", message)
                self.assertIn("frame_0001_R.png (view right)", message)

    def test_a_file_moved_since_is_said(self):
        self.stereo()
        (self.folder / "shot/frame_0001_R.png").unlink()
        self.assertIn("frame_0001_R.png of job j3 was moved or deleted", self.refusal(file="frame_0001_R.png"))

    def test_a_link_in_place_of_an_output_is_not_followed(self):
        self.stereo()
        target = self.folder / "shot/frame_0001_R.png"
        target.unlink()
        target.symlink_to(self.folder / "shot/frame_0001_L.png")
        self.assertIn("moved or deleted", self.refusal(file="frame_0001_R.png"))

    def test_several_layers_or_views_in_one_openexr_are_refused_with_what_works(self):
        self.stereo()
        message = self.refusal(file="Layers/layers0001.exr")
        self.assertIn("holds the layers Beauty, Mask in one OpenEXR file, and a view shows one picture", message)
        self.assertIn("a File Output item each", message)
        self.published("multi", {}, [{"file": "frame_0001.exr", "views": ["left", "right"]},
                                     {"file": "Passes/Mask0001.png", "node": "Passes", "slot": "Mask"}], identity="j4")
        self.assertIn("holds the views left, right in one OpenEXR file", self.refusal("j4", file="frame_0001.exr"))

    def test_a_job_that_has_not_ended_or_is_anothers(self):
        self.stereo()
        self.records["j3"]["status"] = "running"
        pending = self.show(file="frame_0001_R.png")
        self.assertIsNone(pending.poll())
        self.assertEqual(self.reference_files(), [])
        pending.cancel()
        self.records["j3"].update(status="done", session="other")
        self.assertIn("No job j3 in this conversation", self.refusal(file="frame_0001_R.png"))

    def test_a_still_takes_no_frame(self):
        self.stereo()
        self.assertIn("is one picture, not a frame of an animation; leave frame out",
                      self.refusal(file="frame_0001_R.png", frame=1))


class WaitOutputTests(OutputCase):
    def test_last_job_file_stays_bound_when_a_newer_render_is_started(self):
        self.stereo()
        record = self.records["j3"]
        record["status"] = "running"
        pending = self.show("LAST_JOB", file="frame_0001_R.png")
        self.assertIsNone(pending.poll())
        self.assertEqual(self.reference_files(), [])
        self.published("new", {"frame_0001_R.png": BLUE}, [{"file": "frame_0001_R.png"}], identity="j4")
        record["status"] = "done"
        result = pending.poll()
        self.assertEqual((result["rendered"]["job"], self.colours(result)), ("j3", {RED}))
        self.assertEqual(self.colours(self.show("LAST_JOB", file="frame_0001_R.png")), {BLUE})

    def test_only_the_terminal_receipt_admits_the_selected_file(self):
        self.stereo()
        record = self.records["j3"]
        record.update(status="running", receipt={})
        pending = self.show("LAST_JOB", file="new.png")
        self.assertIsNone(pending.poll())
        self.file("shot/new.png", picture(64, 36, BLUE))
        record.update(status="done", receipt={"outputs": [{"file": "new.png"}]})
        result = pending.poll()
        self.assertEqual((result["rendered"]["file"], self.colours(result)), ("new.png", {BLUE}))

    def test_a_stale_or_unreported_file_after_wait_returns_no_picture(self):
        for chosen in ("frame_0001_R.png", "../outside.png", "unreported.png"):
            with self.subTest(file=chosen):
                self.stereo()
                record = self.records["j3"]
                record["status"] = "running"
                pending = self.show("LAST_JOB", file=chosen)
                record.update(status="done", receipt={"outputs": [{"file": "frame_0001_L.png"}]})
                result = pending.poll()
                self.assertEqual(result["views"], [])
                self.assertIn("reports no output", result["note"])
                self.assertEqual(self.reference_files(), [])

    def test_an_output_replaced_by_a_link_while_waiting_is_not_followed(self):
        self.stereo()
        record = self.records["j3"]
        record["status"] = "running"
        pending = self.show("LAST_JOB", file="frame_0001_R.png")
        path = self.folder / "shot/frame_0001_R.png"
        path.unlink()
        path.symlink_to(self.folder / "shot/frame_0001_L.png")
        record["status"] = "done"
        result = pending.poll()
        self.assertEqual(result["views"], [])
        self.assertIn("moved or deleted", result["note"])
        self.assertEqual(self.reference_files(), [])

    def test_last_job_ignores_other_conversations_and_bakes(self):
        self.stereo()
        self.published("foreign", {"other.png": BLUE}, [{"file": "other.png"}], identity="j90", session="other")
        self.published("bake", {"normal.png": FLAT}, [{"file": "normal.png"}], identity="j91", kind="BAKE_TEXTURE")
        result = self.show("LAST_JOB", file="frame_0001_R.png")
        self.assertEqual((result["rendered"]["job"], self.colours(result)), ("j3", {RED}))

    def test_latest_failed_render_does_not_fall_back_to_an_older_picture(self):
        self.stereo()
        self.job(self.folder / "failed", identity="j4", status="failed", reason="Synthetic render failure")
        result = self.show("LAST_JOB", file="frame_0001_R.png")
        self.assertEqual((result["job"]["id"], result["job"]["status"], result["views"]), ("j4", "failed", []))
        self.assertIn("Synthetic render failure", result["note"])


class AnimationOutputTests(OutputCase):
    def animation(self, kept=(1, 2, 3)):
        files = {}
        for frame, colour in zip((1, 2, 3), (GREY, RED, BLUE)):
            if frame in kept:
                files["frame_%04d_L.png" % frame] = GREY
                files["frame_%04d_R.png" % frame] = colour
        outputs = [{"file": "frame_####_L.png", "view": "left"}, {"file": "frame_####_R.png", "view": "right"}]
        return self.published("walk", files, outputs, kind="RENDER_ANIMATION", detail={"frames": [1, 3]})

    def test_a_frame_of_one_views_pattern(self):
        self.animation()
        result = self.show(file="frame_####_R.png", frame=2)
        self.assertEqual(self.colours(result), {RED})
        self.assertEqual((result["rendered"]["frame"], result["rendered"]["frames"], result["rendered"]["frame_count"]),
                         (2, [1, 3], 3))
        self.assertIn("View right from job j3 (renders/walk/frame_0002_R.png)", result["note"])
        self.assertEqual(self.colours(self.show(file="frame_####_R.png", frame=3)), {BLUE})

    def test_the_first_kept_frame_without_a_number(self):
        self.animation(kept=(2, 3))
        result = self.show(file="frame_####_R.png")
        self.assertEqual((self.colours(result), result["rendered"]["frame"], result["rendered"]["frames"]), ({RED}, 2, [2, 3]))

    def test_a_missing_frame_names_the_ones_kept(self):
        self.animation(kept=(1, 2))
        self.assertIn("Job j3 has no frame 3 of frame_####_R.png: it wrote 2 frames, 1 to 2",
                      self.refusal(file="frame_####_R.png", frame=3))
        self.assertIn("no frame 9", self.refusal(file="frame_####_R.png", frame=9))

    def test_without_file_the_refusal_names_the_patterns(self):
        self.animation()
        message = self.refusal(frame=2)
        self.assertIn("wrote several pictures a frame: pass file (and frame for ####)", message)
        self.assertIn("frame_####_R.png (view right)", message)

    def test_frames_are_the_jobs_own(self):
        self.animation()
        self.file("walk/frame_0007_R.png", picture(64, 36, RED))    # Not one of the job's frames.
        self.file("walk/frame_00002_R.png", picture(64, 36, RED))   # Not a name Blender gives frame 2.
        record = self.records["j3"]
        base = self.folder / "walk"
        self.assertEqual(job_store.published_frames(base, record, "frame_####_R.png"), [1, 2, 3])
        self.assertEqual(job_store.published_frames(base, record, "frame_0001_R.png"), [])
        self.assertEqual(job_store.published_frames(base, record, "other_####.png"), [])
        self.assertIsNone(job_store.published_file(base, record, "frame_####_R.png", 7))


class BakeOutputTests(OutputCase):
    def bake(self, bake_type="NORMAL", files=None, outputs=None, **changes):
        files = {"Low_Normal.png": FLAT} if files is None else files
        outputs = ([{"image": IMAGE, "name": "Low Normal", "map": bake_type, "file": "Low_Normal.png"}]
                   if outputs is None else outputs)
        return self.published("bakes", files, outputs, kind="BAKE_TEXTURE", identity="j5",
                              request={"bake_type": bake_type}, receipt={"images": ["Low Normal"]}, **changes)

    def test_a_normal_map_is_shown_as_stored_and_the_result_says_so(self):
        self.bake()
        stored = picture(64, 36, FLAT)
        stored[10:20, 30:40] = (200, 90, 220, 255)  # The planted defect.
        self.file("bakes/Low_Normal.png", stored)
        self.scene.view_settings.view_transform, self.scene.view_settings.exposure = "AgX", 2.0
        result = self.show("j5", file="Low_Normal.png", resolution=768)
        self.assertTrue(numpy.array_equal(self.delivered(result), stored), "Byte for byte what the job published")
        self.assertEqual(self.blender.saved, [], "No view transform, whatever the scene's is")
        self.assertEqual(result["rendered"], {"job": "j5", "output": "renders/bakes", "file": "Low_Normal.png",
                                              "source": "CURRENT_OUTPUT_FILE",
                                              "map": "NORMAL", "name": "Low Normal", "values": "STORED",
                                              "complete": True, "source_size": [64, 36]})
        self.assertIn("NORMAL map of Low Normal from job j5 (renders/bakes/Low_Normal.png), 64x36.", result["note"])
        self.assertIn("It is non-colour data (a NORMAL map): its stored values are shown as they are, with no view "
                      "transform.", result["note"])
        self.assertIn("including any edits made to that file since the bake", result["note"])
        self.assertNotIn("as it was rendered then", result["note"])

    def test_the_only_published_file_needs_no_name(self):
        self.bake()
        result = self.show("j5")
        self.assertEqual((result["rendered"]["file"], result["rendered"]["values"]), ("Low_Normal.png", "STORED"))

    def test_a_colour_map_is_a_picture(self):
        self.bake("EMIT", {"Skin.png": RED}, [{"image": IMAGE, "name": "Skin", "map": "EMIT", "file": "Skin.png"}])
        result = self.show("j5", file="Skin.png")
        self.assertNotIn("values", result["rendered"])
        self.assertNotIn("non-colour", result["note"])
        self.assertIn("EMIT map of Skin from job j5", result["note"])

    def test_one_udim_tile_is_shown(self):
        outputs = [{"image": IMAGE, "name": "Skin", "tile": 1001, "map": "NORMAL", "file": "Skin.1001.png"},
                   {"image": IMAGE, "name": "Skin", "tile": 1002, "map": "NORMAL", "file": "Skin.1002.png"},
                   {"image": IMAGE, "name": "Skin", "tile": 1003, "file": "Skin.1003.png"}]
        self.bake(files={"Skin.1001.png": FLAT, "Skin.1002.png": RED, "Skin.1003.png": GREY}, outputs=outputs)
        defect = self.show("j5", file="Skin.1002.png")
        self.assertEqual(self.colours(defect), {RED}, "The planted tile")
        self.assertEqual(self.colours(self.show("j5", file="Skin.1001.png")), {FLAT})
        self.assertEqual((defect["rendered"]["tile"], defect["rendered"]["map"], defect["rendered"]["values"]),
                         (1002, "NORMAL", "STORED"))
        self.assertIn("Tile 1002 of Skin (NORMAL map) from job j5 (renders/bakes/Skin.1002.png)", defect["note"])
        untouched = self.show("j5", file="Skin.1003.png")
        self.assertEqual(untouched["rendered"]["values"], "STORED", "The same image: the tile the bake left is data too")
        self.assertIn("Tile 1003 of Skin from job j5", untouched["note"])
        message = self.refusal("j5")
        self.assertIn("baked several images or tiles: pass file with one of its outputs", message)
        self.assertIn("Skin.1002.png (tile 1002 of Skin (NORMAL map))", message)

    def test_a_bake_without_an_output_published_nothing_to_show(self):
        self.bake(files={}, outputs=[{"image": IMAGE, "name": "Low Normal", "map": "NORMAL"}])
        for arguments in ({}, {"file": "Low_Normal.png"}):
            message = self.refusal("j5", **arguments)
            self.assertIn("Job j5 baked into Low Normal and published no file: it had no output", message)
            self.assertIn("Start the bake with an output folder", message)

    def test_a_bake_that_has_not_ended_or_did_not_finish(self):
        self.bake(files={}, outputs=[], status="running")
        self.assertIn("Job j5 has not finished (running)", self.refusal("j5"))
        self.assertIn("Job j5 has not finished (running)", self.refusal("j5", file="Low_Normal.png"))
        for status in ("cancelled", "failed", "interrupted"):
            self.records["j5"]["status"] = status
            self.assertEqual(self.refusal("j5"), "Job j5 published no image (%s)" % status)
            self.assertEqual(self.refusal("j5", file="Low_Normal.png"), "Job j5 published no image (%s)" % status)


class ShownTests(unittest.TestCase):
    """A view of one output marks its job as shown, with that output; the receipt counts a ``job:<id>`` picture."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.manager = JobManager(JobStore(self.folder.name))
        job_manager.install(self.manager)
        self.addCleanup(job_manager.install, None)
        runner = FakeRunner(outcome=("done", "", {"files": 4, "outputs": STEREO[:4]}))
        self.record = self.manager.submit("chat", "call-1", {"kind": "RENDER_STILL", "output": "renders/shot.png",
                                                             "minutes": 30.0}, runner)
        runner.ended = True
        self.manager.poll()
        self.metrics = RequestMetrics("claude", session_id="chat")
        host = SimpleNamespace(_request_metrics=self.metrics)
        self.context = SimpleNamespace(host=host, request_fence=SimpleNamespace(matches=lambda other: other is host))

    def look(self, identity, name):
        """A view result as ``view_job_output`` shapes it, dispatched and then delivered inside a provider turn."""
        picture = view(identity, "RENDERED", "FINISHED_IMAGE")
        rendered = {"job": "j1", "output": "renders/shot", "file": name, "complete": True}
        request_path.dispatched(self.context, "angelo_view", {"image": "j1", "file": name}, lambda result: None)(
            view_result(picture, rendered=rendered))
        return picture

    def test_each_output_shown_is_kept_once_and_every_picture_is_counted(self):
        right = self.look("r1", "frame_0001_R.png")
        self.assertEqual(self.record["shown"], {}, "Returned by the tool, not yet handed to the model")
        request_path.pictures(self.context, [right], True)
        request_path.accepted(self.context)
        self.assertEqual(self.record["shown"]["files"], ["frame_0001_R.png"])
        request_path.pictures(self.context, [self.look("r2", "Passes/Mask0001_L.png"),
                                             self.look("r3", "frame_0001_R.png")], False)
        shown = self.record["shown"]
        self.assertEqual((shown["images"], shown["files"]), (3, ["frame_0001_R.png", "Passes/Mask0001_L.png"]))
        self.assertIn("image_at", shown)
        self.assertTrue(self.manager.store.save())
        self.assertEqual(JobStore(self.folder.name).get("j1")["shown"], shown)

    def test_the_receipt_counts_job_pictures_and_names_no_file(self):
        request_path.pictures(self.context, [self.look("r1", "frame_0001_R.png"), self.look("r2", "frame_0001_L.png")], True)
        request_path.accepted(self.context)
        path = self.metrics.snapshot()["path"]
        self.assertEqual(path["images"], [[1, None, {"job:j1": 2}]])
        self.assertNotIn("frame_0001", json.dumps(self.metrics.snapshot()))
        self.assertEqual(request_path.clean_path(path)["images"], [[1, None, {"job:j1": 2}]],
                         "The kind a stored receipt keeps")

    def test_only_an_output_the_job_reported_is_kept(self):
        store = self.manager.store
        self.assertTrue(mark_shown(store, "j1", "image", file="../elsewhere.png"))
        self.assertTrue(mark_shown(store, "j1", "image", file=7))
        self.assertTrue(mark_shown(store, "j1", "image"))
        self.assertEqual((self.record["shown"]["images"], "files" in self.record["shown"]), (3, False))
        for number in range(40):
            mark_shown(store, "j1", "image", file=STEREO[number % 4]["file"])
        self.assertEqual(self.record["shown"]["files"], [entry["file"] for entry in STEREO[:4]])
        self.assertNotIn("shown", job_store.context_entry(self.record), "The jobs block is what it was")

    def test_a_job_with_one_picture_is_marked_without_a_file(self):
        picture = view("r9", "RENDERED", "FINISHED_IMAGE")
        request_path.dispatched(self.context, "angelo_view", {"image": "j1"}, lambda result: None)(
            view_result(picture, rendered={"job": "j1", "output": "renders/final.png"}))
        jobs = self.metrics.path.pictures([picture], staged=False)
        self.assertEqual((jobs, jobs.files), (["j1"], [None]))


if __name__ == "__main__":
    unittest.main()
