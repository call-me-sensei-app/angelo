# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render passes and view layers in ``angelo_view`` on the host (doc/angelo/view_sources.rst).

The choice of layer and pass, what each data pass looks like, and the two
flows: Blender's Render Result saved once as a multilayer OpenEXR (the
stand-in Blender writes the file the scene's format asks for, and the test
checks that format was set for that save only), and a job's multilayer output
read through the private copy. ``rendered_view_case.FinishedCase`` supplies
the stand-ins; ``view_exr`` reads files ``view_source_fakes.exr_bytes`` wrote.
"""

import sys
import unittest
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rendered_view_fakes  # noqa: E402
import view_source_fakes as fakes  # noqa: E402
from angelo_agent import view_passes  # noqa: E402
from rendered_view_case import FinishedCase  # noqa: E402
from test_native_view_job_output import OutputCase  # noqa: E402

WIDTH, HEIGHT = 40, 20


def depth_rows():
    """Depth 2 on the left half, 6 on the right, no hit (Cycles' 1e10) in the top-left corner."""
    rows = numpy.full((HEIGHT, WIDTH), 2.0, numpy.float32)
    rows[:, WIDTH // 2:] = 6.0
    rows[:4, :4] = 1e10
    return rows


def multilayer(layers=("ViewLayer",)):
    """What Blender writes for a render with depth, normal, an object index and Cryptomatte: one part a pass."""
    parts = [("Composite.Combined", {"Composite.Combined.%s" % c: (numpy.full((HEIGHT, WIDTH), 0.5, numpy.float32),
                                                                   "HALF") for c in "RGBA"})]
    for layer in layers:
        parts += [
            ("%s.Combined" % layer, {"%s.Combined.%s" % (layer, c): (numpy.full((HEIGHT, WIDTH), v, numpy.float32), "HALF")
                                     for c, v in zip("RGBA", (0.2, 0.4, 0.6, 1.0))}),
            ("%s.Depth" % layer, {"%s.Depth.Z" % layer: (depth_rows(), "FLOAT")}),
            ("%s.Normal" % layer, {"%s.Normal.%s" % (layer, c): (numpy.full((HEIGHT, WIDTH), v, numpy.float32), "FLOAT")
                                   for c, v in zip("XYZ", (1.0, 0.0, -1.0))}),
            ("%s.IndexOB" % layer, {"%s.IndexOB.X" % layer: (numpy.where(numpy.arange(WIDTH) < 10, 0.0, 3.0)
                                                             [None, :].repeat(HEIGHT, 0).astype(numpy.float32), "FLOAT")}),
        ]
    return fakes.exr_bytes(parts, WIDTH, HEIGHT, "ZIP")


class ChoiceTests(unittest.TestCase):
    FOUND = {("ViewLayer", "Depth"): (1, {}), ("View Layer.001", "Depth"): (2, {}), ("ViewLayer", "Normal"): (3, {}),
             ("Composite", "Combined"): (0, {}), ("ViewLayer", "Combined"): (4, {})}

    def test_a_pass_on_the_active_layer_or_the_only_layer_that_has_it(self):
        self.assertEqual(view_passes.choose(self.FOUND, "Depth", "ViewLayer"), ("ViewLayer", "Depth"))
        self.assertEqual(view_passes.choose(self.FOUND, "Normal", "Other"), ("ViewLayer", "Normal"))
        self.assertEqual(view_passes.choose(self.FOUND, "View Layer.001.Depth", "ViewLayer"), ("View Layer.001", "Depth"))
        self.assertEqual(view_passes.choose(self.FOUND, "Combined", "ViewLayer"), ("ViewLayer", "Combined"))
        self.assertEqual(view_passes.choose(self.FOUND, "Composite.Combined", "ViewLayer"), ("Composite", "Combined"))
        with self.assertRaisesRegex(RuntimeError, r"Several view layers have Depth: name one as <layer>.Depth"):
            view_passes.choose(self.FOUND, "Depth", "Other")
        with self.assertRaisesRegex(RuntimeError, r"no pass Mist. It holds: Composite.Combined, View Layer.001.Depth"):
            view_passes.choose(self.FOUND, "Mist", "ViewLayer")

    def test_channel_names_split_into_layer_pass_and_channel(self):
        self.assertEqual(view_passes.split_channel("View Layer.001.Depth.Z"), ("View Layer.001", "Depth", "Z"))
        self.assertEqual(view_passes.split_channel("Combined.R"), ("", "Combined", "R"))
        self.assertEqual(view_passes.split_channel("R"), ("", "", "R"))


class DataPassTests(unittest.TestCase):
    def test_depth_runs_from_white_near_to_black_far_and_no_hit_is_black(self):
        rgb, facts, how = view_passes.data_rows("Depth", {"Z": depth_rows()})
        self.assertEqual(facts, {"mapping": "RANGE", "range": [2.0, 6.0]})
        self.assertEqual((rgb[10, 5, 0], rgb[10, 30, 0], rgb[0, 0, 0]), (1.0, 0.0, 0.0))
        self.assertIn("near white to far black between 2 and 6, no hit black", how)

    def test_normals_positions_and_ids_have_stated_mappings(self):
        normal, facts, _ = view_passes.data_rows("Normal", {c: numpy.full((2, 2), v, numpy.float32)
                                                            for c, v in zip("XYZ", (1.0, 0.0, -1.0))})
        self.assertEqual((normal[0, 0].tolist(), facts["mapping"]), ([1.0, 0.5, 0.0], "HALF_PLUS_HALF"))
        grid = numpy.arange(4, dtype=numpy.float32).reshape(2, 2)
        position, facts, _ = view_passes.data_rows("Position", {"X": grid, "Y": grid * 2, "Z": grid * 0})
        self.assertEqual((position[1, 1].tolist(), facts["range"][1]), ([1.0, 1.0, 0.0], [0.0, 6.0]))
        ids, facts, _ = view_passes.data_rows("IndexOB", {"X": numpy.array([[0.0, 3.0], [3.0, 5.0]], numpy.float32)})
        self.assertEqual((ids[0, 0].tolist(), facts["ids"]), ([0.0, 0.0, 0.0], 2))
        self.assertEqual(ids[0, 1].tolist(), ids[1, 0].tolist(), "one colour per ID")
        self.assertNotEqual(ids[0, 1].tolist(), ids[1, 1].tolist())
        crypto = {"R": numpy.array([[1.5, 2.5]], numpy.float32), "G": numpy.array([[1.0, 0.5]], numpy.float32),
                  "B": numpy.zeros((1, 2), numpy.float32), "A": numpy.zeros((1, 2), numpy.float32)}
        colours, facts, how = view_passes.data_rows("CryptoObject00", crypto)
        self.assertEqual((facts["mapping"], facts["ids"]), ("ID_COLOURS", 2))
        lower = view_passes.data_rows("CryptoObject00", {name.lower(): plane for name, plane in crypto.items()})
        self.assertEqual(lower[1], facts, "Blender writes Cryptomatte's channels as r, g, b, a")
        self.assertTrue(numpy.array_equal(lower[0], colours))
        self.assertTrue((colours[0, 1] < colours[0, 1].max() + 1).all() and colours[0, 0].max() > 0.25)
        vector, facts, _ = view_passes.data_rows("Vector", {c: numpy.full((1, 1), 2.0, numpy.float32) for c in "XYZW"})
        self.assertEqual((facts["mapping"], facts["clipped"]), ("STORED", 1.0))


class RenderResultPassTests(FinishedCase):
    def setUp(self):
        super().setUp()
        self.blender.window.view_layer.name = "ViewLayer"
        media = dict(rendered_view_fakes.ImageSettings.MEDIA, MULTI_LAYER_IMAGE=("OPEN_EXR_MULTILAYER",))
        depths = dict(rendered_view_fakes.ImageSettings.DEPTHS, OPEN_EXR_MULTILAYER=("16", "32"))
        self.patch_attributes = [(rendered_view_fakes.ImageSettings, "MEDIA", media),
                                 (rendered_view_fakes.ImageSettings, "DEPTHS", depths)]
        self.saved_attributes = [(owner, name, getattr(owner, name)) for owner, name, _ in self.patch_attributes]
        for owner, name, value in self.patch_attributes:
            setattr(owner, name, value)
        self.during = []
        original = self.blender.save_render

        def save_render(image, filepath, scene=None):
            settings = self.blender.scene.render.image_settings
            if settings.file_format == "OPEN_EXR_MULTILAYER":  # Blender writes every layer and pass.
                self.during.append((settings.file_format, settings.color_depth, settings.exr_codec))
                Path(filepath).write_bytes(multilayer(self.layers))
                return None
            return original(image, filepath, scene)
        self.blender.save_render = save_render
        self.layers = ("ViewLayer",)

    def tearDown(self):
        for owner, name, value in self.saved_attributes:
            setattr(owner, name, value)
        super().tearDown()

    def test_a_data_pass_of_the_render_result_is_read_from_one_private_save(self):
        before = self.scene.snapshot()
        result = self.show("RENDER_RESULT:Depth", resolution=256)
        self.assertEqual(self.during, [("OPEN_EXR_MULTILAYER", "32", "ZIP")])
        self.assertEqual(self.scene.snapshot(), before, "the image format is put back exactly")
        picture = self.delivered(result)
        self.assertEqual(picture.shape[:2], (HEIGHT, WIDTH))
        self.assertEqual((picture[10, 5, :3].tolist(), picture[10, 30, :3].tolist(), picture[0, 0, :3].tolist()),
                         ([255, 255, 255], [0, 0, 0], [0, 0, 0]))
        facts = result["rendered"]
        self.assertEqual((facts["layer"], facts["render_pass"], facts["mapping"], facts["range"], facts["values"]),
                         ("ViewLayer", "Depth", "RANGE", [2.0, 6.0], "STATED"))
        self.assertIn("pass Depth of view layer ViewLayer", result["note"])
        self.assertIn("A data pass, shown as near white to far black between 2 and 6", result["note"])
        self.assertEqual(len(self.reference_files()), 1, "the saved OpenEXR and intermediates are discarded")

    def test_an_unset_output_colour_space_that_blender_names_is_reported_precisely(self):
        class Space(rendered_view_fakes.Struct):
            def assign(self, name, value):
                if name == "name" and value == "":
                    raise TypeError("enum \"\" not found")  # RNA cannot unset it again.
                super().assign(name, value)
        self.scene.render.image_settings.values["linear_colorspace_settings"] = Space(
            _rna_type="ColorManagedInputColorspaceSettings", name="")
        result = self.show("RENDER_RESULT:Depth")
        self.assertEqual([entry["code"] for entry in result["side_effects"]], ["output_color_space_named"])
        named = self.scene.render.image_settings.linear_colorspace_settings.name
        self.assertTrue(named)
        self.assertIn("unset; Blender named its default (%s)" % named, result["side_effects"][0]["detail"])
        again = self.show("RENDER_RESULT:Depth")
        self.assertEqual(again["side_effects"], [], "named now, it is put back exactly")

    def test_a_colour_pass_goes_through_blenders_own_display_copy(self):
        result = self.show("RENDER_RESULT:Combined")
        facts = result["rendered"]
        self.assertEqual((facts["layer"], facts["render_pass"], facts["values"], facts["preview"]),
                         ("ViewLayer", "Combined", "DISPLAY", "DISPLAY_REFERRED"))
        self.assertIn("A colour pass, shown through the scene's view transform", result["note"])
        self.assertEqual(self.blender.images[0].type, "RENDER_RESULT")
        self.assertEqual(len(self.blender.images), 1, "the temporary image of the display copy is removed")

    def test_a_missing_pass_names_what_the_render_holds_and_stereo_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "The render has no pass Mist. It holds: Composite.Combined"):
            self.show("RENDER_RESULT:Mist")
        self.layers = ("ViewLayer", "Night")
        with self.assertRaisesRegex(RuntimeError, "Several view layers have Depth"):
            self.blender.window.view_layer.name = "Other"
            self.show("RENDER_RESULT:Depth")
        self.assertEqual(self.show("RENDER_RESULT:Night.Depth")["rendered"]["layer"], "Night")
        self.scene.render.use_multiview = True
        with self.assertRaisesRegex(RuntimeError, "stereo views"):
            self.show("RENDER_RESULT:Depth")
        self.assertEqual(len(self.reference_files()), 1)


class JobPassTests(FinishedCase):
    def test_a_pass_of_a_jobs_multilayer_output(self):
        self.blender.window.view_layer.name = "ViewLayer"
        self.job(self.file("final.exr", multilayer()))
        result = self.show("j3:IndexOB")
        facts = result["rendered"]
        self.assertEqual((facts["job"], facts["render_pass"], facts["ids"], facts["source"]),
                         ("j3", "IndexOB", 1, "CURRENT_OUTPUT_FILE"))
        picture = self.delivered(result)
        self.assertEqual(picture[5, 2, :3].tolist(), [0, 0, 0], "index 0 is black")
        self.assertGreater(int(picture[5, 30, :3].max()), 60)
        self.assertIn("It reads the job's current output file", result["note"])
        self.job(self.file("plain.png", numpy.zeros((4, 4, 4), numpy.uint8)))
        with self.assertRaisesRegex(RuntimeError, "is one picture, not a multilayer OpenEXR"):
            self.show("j3:Depth")


class FileOutputPassTests(OutputCase):
    def test_a_pass_of_a_file_output_nodes_multilayer_item(self):
        self.blender.window.view_layer.name = "ViewLayer"
        mask = numpy.zeros((HEIGHT, WIDTH), numpy.float32)
        mask[:, :10] = 1.0
        outputs = [{"file": "Layers/layers0001.exr", "node": "Layers", "layers": ["Beauty", "Mask"]}]
        self.published("shot", {}, outputs)
        self.file("shot/Layers/layers0001.exr", fakes.exr_bytes(
            [("", {"Beauty.R": (mask, "HALF"), "Beauty.G": (mask, "HALF"), "Beauty.B": (mask, "HALF"),
                   "Mask.V": (mask, "FLOAT")})], WIDTH, HEIGHT, "ZIPS", multipart=False))
        refusal = self.refusal(file="Layers/layers0001.exr")
        self.assertIn("name one pass of its layers as image j3:<pass>", refusal)
        result = self.show("j3:Mask", file="Layers/layers0001.exr")
        facts = result["rendered"]
        self.assertEqual((facts["render_pass"], facts["file"], facts["node"], facts["mapping"]),
                         ("Mask", "Layers/layers0001.exr", "Layers", "RANGE"))
        self.assertEqual(self.delivered(result)[5, 2, :3].tolist(), [255, 255, 255], "a value pass: highest white")
        self.assertIn("lowest black to highest white between 0 and 1", result["note"])


if __name__ == "__main__":
    unittest.main()
