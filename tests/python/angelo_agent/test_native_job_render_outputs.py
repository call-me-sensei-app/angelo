# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The files a render job plans and how it checks them, without Blender.

``job_render_outputs`` names every file of a render before it starts (per view,
per compositor File Output item) and sorts what was written afterwards. The
names against real renders, and Blender's own extension for every format, are
checked by ``test_blender_production_renders.py``.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_render_outputs as outputs  # noqa: E402
from angelo_agent.approvals import approval_message  # noqa: E402

STEREO = [("left", "_L"), ("right", "_R")]


def node(name="Passes", folder=None, items=(("Beauty", True),), multilayer=False, views_format="INDIVIDUAL",
         extension=".png"):
    return {"folder": folder or name, "name": name, "multilayer": multilayer, "extension": extension,
            "views_format": views_format,
            "items": [{"name": item, "extension": extension, "views_format": views_format, "linked": linked}
                      for item, linked in items]}


def names(plan, frame=1):
    return [outputs.file_name(output, frame) for output in plan]


class PlanTests(unittest.TestCase):
    def test_a_plain_render_is_one_file_a_frame(self):
        plan = outputs.plan(".png", [], "INDIVIDUAL", [])
        self.assertEqual(names(plan, 12), ["frame_0012.png"])
        self.assertEqual(outputs.entries(plan), [{"file": "frame_####.png"}])
        self.assertEqual(names(plan, -5), ["frame_-0005.png"])  # Blender pads after the sign.
        self.assertEqual(names(plan, 12345), ["frame_12345.png"])

    def test_each_view_gets_its_file_unless_the_format_combines_them(self):
        apart = outputs.plan(".png", STEREO, "INDIVIDUAL", [])
        self.assertEqual(names(apart), ["frame_0001_L.png", "frame_0001_R.png"])
        self.assertEqual(outputs.entries(apart, 1), [{"file": "frame_0001_L.png", "view": "left"},
                                                     {"file": "frame_0001_R.png", "view": "right"}])
        for combined in ("STEREO_3D", "MULTIVIEW"):
            together = outputs.plan(".exr", STEREO, combined, [])
            self.assertEqual(outputs.entries(together, 1), [{"file": "frame_0001.exr", "views": ["left", "right"]}])
        one = outputs.plan(".png", [("left", "_L")], "INDIVIDUAL", [])
        self.assertEqual(names(one), ["frame_0001_L.png"])  # One enabled view still carries its suffix.

    def test_file_output_items_follow_their_own_format(self):
        plan = outputs.plan(".png", STEREO, "INDIVIDUAL", [
            node(items=(("Beauty", True), ("Mask", True), ("Loose", False))),
            node("Layers", multilayer=True, extension=".exr", items=(("Beauty", True), ("Mask", False))),
            node("Combined", views_format="STEREO_3D", items=(("Anaglyph", True),))])
        self.assertEqual(names(plan), [
            "frame_0001_L.png", "frame_0001_R.png", "Passes/Beauty0001_L.png", "Passes/Beauty0001_R.png",
            "Passes/Mask0001_L.png", "Passes/Mask0001_R.png", "Layers/layers0001_L.exr", "Layers/layers0001_R.exr",
            "Combined/Anaglyph0001.png"])
        entries = outputs.entries(plan)
        self.assertIn({"file": "Passes/Mask####_R.png", "node": "Passes", "slot": "Mask", "view": "right"}, entries)
        self.assertIn({"file": "Layers/layers####_L.exr", "node": "Layers", "layers": ["Beauty", "Mask"],
                       "view": "left"}, entries)
        self.assertIn({"file": "Combined/Anaglyph####.png", "node": "Combined", "slot": "Anaglyph",
                       "views": ["left", "right"]}, entries)
        self.assertEqual([output["main"] for output in plan][:3], [True, True, False])
        # A multi-layer node with no image input writes nothing, like an unlinked item.
        self.assertEqual(names(outputs.plan(".png", [], "INDIVIDUAL", [
            node("Layers", multilayer=True, extension=".exr", items=(("Beauty", False),))])), ["frame_0001.png"])

    def test_what_cannot_be_named_in_advance_is_refused_with_the_fix(self):
        cases = [
            (lambda: outputs.plan(".png", [("left", "_V"), ("right", "_V")], "INDIVIDUAL", []), "its own file suffix"),
            (lambda: outputs.plan(".png", [("left", "_#"), ("right", "_R")], "INDIVIDUAL", []), "its own file suffix"),
            (lambda: outputs.plan(".png", [], "INDIVIDUAL", [node(items=(("A", True), ("A", True)))]), "distinct name"),
            (lambda: outputs.plan(".png", [], "INDIVIDUAL", [node(items=(("Pass#", True),))]), "distinct name"),
            (lambda: outputs.plan(".png", [], "INDIVIDUAL", [node(items=(("{blend_name}", True),))]), "distinct name"),
            (lambda: outputs.plan(".png", [], "INDIVIDUAL", [node(items=(("", True),))]), "distinct name"),
            (lambda: outputs.plan(".png", STEREO, "INDIVIDUAL", [
                node(items=tuple(("Item%d" % n, True) for n in range(40)))]), "at most 64"),
        ]
        for call, message in cases:
            with self.subTest(message=message), self.assertRaises(outputs.PlanError) as caught:
                call()
            self.assertIn(message, str(caught.exception))
        # The same suffix is fine when the views share one file, and an unlinked item's name never matters.
        outputs.plan(".exr", [("left", "_V"), ("right", "_V")], "MULTIVIEW", [node(items=(("Pass#", False),))])

    def test_node_folders_are_file_safe_and_unique(self):
        taken = set()
        self.assertEqual([outputs.folder_name(name, taken) for name in
                          ("Passes", "passes", "Inner Out", "../../etc", "", "frame_0001.png", "日本語")],
                         ["Passes", "passes-2", "Inner_Out", "etc", "FileOutput", "node-frame_0001.png",
                          "FileOutput-2"])

    def test_a_queued_jobs_plan_has_a_signature(self):
        first = outputs.plan(".png", STEREO, "INDIVIDUAL", [node()])
        self.assertEqual(outputs.signature(first), outputs.signature(outputs.plan(".png", STEREO, "INDIVIDUAL", [node()])))
        for other in (outputs.plan(".png", [], "INDIVIDUAL", [node()]), outputs.plan(".png", STEREO, "INDIVIDUAL", []),
                      outputs.plan(".exr", STEREO, "INDIVIDUAL", [node()]),
                      outputs.plan(".png", STEREO, "INDIVIDUAL", [node(folder="Other")])):
            self.assertNotEqual(outputs.signature(first), outputs.signature(other))


class SortTests(unittest.TestCase):
    def setUp(self):
        self.plan = outputs.plan(".png", STEREO, "INDIVIDUAL", [node(items=(("Beauty", True), ("Flat", True)))])

    def written(self, frames, slots=("frame_", "Passes/Beauty")):
        return [outputs.file_name(output, frame) for frame in frames for output in self.plan
                if output["prefix"] in slots]

    def test_finished_frames_are_those_whose_own_image_exists(self):
        found = outputs.sort_files(self.plan, 1, 3, self.written([1, 2, 3]))
        self.assertEqual((found["frames"], found["drop"], found["unexpected"]), ([1, 2, 3], [], []))
        self.assertEqual([self.plan[index]["prefix"] for index in found["silent"]], ["Passes/Flat", "Passes/Flat"])

    def test_an_unfinished_frames_files_are_dropped_whole(self):
        present = self.written([1]) + ["frame_0002_L.png", "Passes/Beauty0002_L.png", "Passes/Beauty0002_R.png"]
        found = outputs.sort_files(self.plan, 1, 3, present)
        self.assertEqual(found["frames"], [1])
        self.assertEqual(found["drop"], ["Passes/Beauty0002_L.png", "Passes/Beauty0002_R.png", "frame_0002_L.png"])

    def test_a_file_the_plan_does_not_name_is_never_accepted(self):
        present = self.written([1]) + ["frame_0001.png", "Passes/Depth0001_L.png", "elsewhere/frame_0001_L.png",
                                       "frame_0004_L.png"]
        self.assertEqual(outputs.sort_files(self.plan, 1, 3, present)["unexpected"],
                         ["Passes/Depth0001_L.png", "elsewhere/frame_0001_L.png", "frame_0001.png", "frame_0004_L.png"])

    def test_own_image_names_map_to_their_frames(self):
        self.assertEqual(outputs.main_names(self.plan, 7, 8), {
            "frame_0007_L.png": 7, "frame_0007_R.png": 7, "frame_0008_L.png": 8, "frame_0008_R.png": 8})


class ApprovalTests(unittest.TestCase):
    def test_the_card_lists_each_file_with_what_it_is(self):
        plan = outputs.plan(".png", STEREO, "INDIVIDUAL", [
            node(), node("Layers", multilayer=True, extension=".exr", views_format="MULTIVIEW", items=(("Beauty", True),))])
        facts = {"engine": "CYCLES", "resolution": [1920, 1080], "samples": 64, "frames": [1, 1], "output": "renders/shot",
                 "outputs": outputs.entries(plan, 1)}
        body = approval_message("angelo_job_start", {"kind": "RENDER_STILL", "output": "renders/shot.png",
                                                     "_facts": facts})["body"].split("\n")
        start = body.index("Writes renders/shot in this chat's file workspace (new; nothing is overwritten)")
        self.assertEqual(body[start + 1:start + 6], [
            "frame_0001_L.png · View left", "frame_0001_R.png · View right",
            "Passes/Beauty0001_L.png · File Output Passes: Beauty · View left",
            "Passes/Beauty0001_R.png · File Output Passes: Beauty · View right",
            "Layers/layers0001.exr · File Output Layers: layers Beauty · Views left, right in one file"])

    def test_a_long_list_is_counted_not_cut_silently(self):
        many = [{"file": "Passes/Item%d####.png" % n, "node": "Passes", "slot": "Item%d" % n} for n in range(20)]
        body = approval_message("angelo_job_start", {"kind": "RENDER_ANIMATION", "output": "renders/walk", "_facts": {
            "frames": [1, 24], "outputs": many}})["body"].split("\n")
        self.assertIn("Passes/Item11####.png · File Output Passes: Item11", body)
        self.assertNotIn("Passes/Item12####.png · File Output Passes: Item12", body)
        self.assertIn("More files: 8", body)
        plain = approval_message("angelo_job_start", {"kind": "RENDER_STILL", "output": "renders/hero.png", "_facts": {
            "frames": [1, 1]}})["body"]
        self.assertIn("Writes renders/hero.png in this chat's file workspace", plain)  # A plain still: as before.


if __name__ == "__main__":
    unittest.main()
