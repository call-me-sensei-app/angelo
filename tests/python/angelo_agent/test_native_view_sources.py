# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view``'s Image data-block, UV layout and editor pictures on the host (doc/angelo/view_sources.rst).

``view_source_fakes`` stands in for Blender (an Image's buffer, the native
cache-only metadata helper, meshes with UV maps) and ``picture_fakes`` for
``imbuf``; ``view_spec``, ``view_finished``, ``view_image_data``, ``view_uv``,
``view_editor``, ``view_picture`` and the reference store are the real ones;
Blender's screenshot operator writes the planted window pixels. Each test reads
the delivered PNG back. ``test_blender_view_sources`` checks the same in the
compiled application against Blender's own buffers.
"""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_fakes  # noqa: E402
import view_source_fakes as fakes  # noqa: E402
from angelo_agent import view_editor, view_spec  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

LOOK = {"views": ["SCENE_CAMERA"], "shading": "RENDERED"}
MESH = "scn:" + "a" * 32 + ":4"
RED, GREEN = (255, 0, 0, 255), (0, 255, 0, 255)


def rows(width, height, colour=(120, 130, 140, 255)):
    pixels = numpy.empty((height, width, 4), dtype=numpy.uint8)
    pixels[...] = colour
    return pixels


class SourceCase(unittest.TestCase):
    def setUp(self):
        self.blender = fakes.Blender()
        self.temp = tempfile.TemporaryDirectory()
        self.references = ReferenceStore(Path(self.temp.name) / "state", "4c0a43a6-8f2a-4b8e-9b1f-1b7b0d1b3c55")
        job_ui = ModuleType("angelo_agent.job_ui")
        job_ui.manager = lambda create=True: SimpleNamespace(record=lambda _identity: None, active=lambda: None,
                                                             store=SimpleNamespace(records={}))
        self.modules = patch.dict(sys.modules, dict(self.blender.modules(), **{
            "angelo_agent.job_ui": job_ui, "angelo_agent.scene_queries": self.blender.queries}))
        self.modules.start()
        for name in ("view_finished", "view_image_data", "view_uv", "view_render_restore", "job_settings"):
            sys.modules.pop("angelo_agent." + name, None)
        self.finished = importlib.import_module("angelo_agent.view_finished")
        self.patches = [patch("angelo_agent.job_ui", job_ui, create=True),
                        patch("angelo_agent.scene_queries", self.blender.queries, create=True)]
        for item in self.patches:
            item.start()
        self.events = []
        self.controller = SimpleNamespace(state={"id": "chat"}, _epoch=1, _request_generation=1,
                                          emit=lambda kind, data=None: self.events.append((kind, data)))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.modules.stop()
        self.temp.cleanup()

    def show(self, image, **arguments):
        checked = view_spec.validate_view("angelo_view", dict(LOOK, image=image, **arguments))
        return self.finished.finished_image(self.blender.window, checked, self.references, self.controller)

    def delivered(self, result):
        return picture_fakes.read_png(Path(result["views"][0]["path"]).read_bytes())

    def reference_files(self):
        folder = self.references.directory
        return sorted(path.name for path in folder.glob("*")) if folder.exists() else []


class ImageDataTests(SourceCase):
    def test_an_eight_bit_image_is_shown_exactly_as_stored_from_its_cached_buffer(self):
        pixels = rows(64, 32)
        pixels[3, 5] = RED
        image = fakes.byte_image("Albedo", pixels, source="FILE", dirty=True)
        self.blender.images.append(image)
        result = self.show("IMAGE:Albedo", resolution=256)
        self.assertEqual(self.delivered(result).tolist(), pixels.tolist(), "8-bit pixels are delivered as stored")
        view = result["views"][0]
        self.assertEqual((view["framing"], view["shading"], view["width"], view["height"]), ("IMAGE_DATA", "RENDERED", 64, 32))
        self.assertEqual(result["coverage"], "IMAGE_DATA")
        self.assertEqual(result["rendered"]["image"], "Albedo")
        self.assertEqual((result["rendered"]["values"], result["rendered"]["float"], result["rendered"]["unsaved_changes"]),
                         ("DISPLAY", False, True))
        self.assertNotIn("path", str(result["rendered"]).lower().replace("source", ""), "no file path is reported")
        self.assertEqual((image.loaded, image.reads), (False, 1))
        self.assertTrue(result["read_only"] and not result["scene_changed"] and not result["uncertain"])
        self.assertIn("Image 'Albedo', 64x32.", result["note"])
        self.assertIn("pixels Blender holds now, unsaved paint and bakes included", result["note"])
        self.assertEqual(len(self.reference_files()), 1, "the private intermediate file is discarded")

    def test_an_image_not_in_memory_is_refused_and_never_loaded(self):
        image = fakes.byte_image("Wood", rows(8, 8), cached=False, source="FILE")
        self.blender.images.append(image)
        with self.assertRaisesRegex(RuntimeError, "not in memory, and an automatic read never loads an image file") as raised:
            self.show("IMAGE:Wood")
        self.assertIn("approved angelo_execute_python script", str(raised.exception))
        self.assertIn("EDITOR:IMAGE_EDITOR", str(raised.exception))
        self.assertEqual((image.loaded, image.reads), (False, 0))
        self.assertEqual(self.reference_files(), [])

    def test_a_bake_or_render_that_may_write_images_defers_the_read(self):
        image = fakes.byte_image("Bake Target", rows(8, 8))
        self.blender.images.append(image)
        self.blender.bpy.app.is_job_running = lambda kind: kind == "OBJECT_BAKE"
        with self.assertRaisesRegex(RuntimeError, "may be writing images; 'Bake Target' can be shown once it ends"):
            self.show("IMAGE:Bake Target")
        self.assertEqual(image.reads, 0)

    def test_a_build_without_the_cache_only_helper_refuses_instead_of_loading(self):
        image = fakes.byte_image("Wood", rows(8, 8))
        self.blender.images.append(image)
        self.blender.native._angelo_read = SimpleNamespace()
        with self.assertRaisesRegex(RuntimeError, "update the application"):
            self.show("IMAGE:Wood")
        self.assertEqual(image.reads, 0)

    def test_float_pixels_are_unpremultiplied_and_shown_through_the_srgb_transform(self):
        buffer = numpy.zeros((4, 8, 4), dtype=numpy.float32)
        buffer[...] = (0.5, 0.5, 0.5, 1.0)
        buffer[:, 4:] = (0.25, 0.25, 0.25, 0.5)  # Premultiplied: colour 0.5 at half opacity.
        self.blender.images.append(fakes.Image("Bake", buffer, is_float=True, color_space="Linear Rec.709"))
        result = self.show("IMAGE:Bake")
        picture = self.delivered(result)
        self.assertEqual(picture[0, 0, :3].tolist(), [188, 188, 188], "sRGB of linear 0.5")
        half = {tuple(pixel) for pixel in picture[:, 4:, :3].reshape(-1, 3).tolist()}
        self.assertEqual(half, {(196, 196, 196)}, "colour 0.5 at half opacity over the light checker square")
        self.assertEqual(result["rendered"]["transparent"], 0.5)
        self.assertIn("float pixels through the sRGB transform", result["note"])

    def test_non_colour_data_is_shown_as_stored_values_with_clipping_counted(self):
        buffer = numpy.zeros((2, 2, 4), dtype=numpy.float32)
        buffer[...] = (0.5, 0.5, 1.0, 1.0)
        buffer[0, 0] = (2.0, 0.5, 1.0, 1.0)
        self.blender.images.append(fakes.Image("Normal", buffer, is_float=True, color_space="Non-Color", is_data=True))
        result = self.show("IMAGE:Normal")
        self.assertEqual(self.delivered(result)[0, 0, :3].tolist(), [128, 128, 255], "no transform on data")
        self.assertEqual((result["rendered"]["values"], result["rendered"]["clipped"]), ("STORED", 0.25))
        self.assertIn("non-colour data, its stored values", result["note"])
        self.assertIn("25% of its pixels hold values outside 0 to 1", result["note"])

    def test_a_large_image_is_refused_before_its_pixels_are_copied(self):
        image = fakes.byte_image("Huge", rows(2, 2))
        image.metadata = lambda: {"size": [8192, 8192], "channels": 4, "is_float": False}
        self.blender.images.append(image)
        with self.assertRaisesRegex(RuntimeError, "8192x8192: more than the 4096 x 4096 pixels"):
            self.show("IMAGE:Huge")
        self.assertEqual(image.reads, 0)

    def test_names_and_kinds_that_are_not_shown_say_what_works(self):
        self.blender.images.extend([fakes.byte_image("Render Result", rows(2, 2), kind="RENDER_RESULT"),
                                    fakes.byte_image("Viewer Node", rows(2, 2), kind="COMPOSITING"),
                                    fakes.byte_image("Albedo", rows(2, 2))])
        for name, pattern in (("Missing", r"No image named 'Missing'. Images: 'Albedo'"),
                              ("Render Result", "use image RENDER_RESULT"),
                              ("Viewer Node", "compositor's Viewer image")):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, pattern):
                self.show("IMAGE:" + name)

    def test_what_the_image_editor_shows_differently_is_reported(self):
        self.blender.scene.display_settings.display_device = "Display P3"
        self.blender.images.append(fakes.byte_image("Mask", rows(4, 4), color_space="Linear Rec.709",
                                                    view_as_render=True, source="TILED", tiles=3))
        result = self.show("IMAGE:Mask")
        codes = [entry["code"] for entry in result["uncertainty"]]
        self.assertEqual(codes, ["image_color_approximate", "image_first_tile_only"])
        detail = result["uncertainty"][0]["detail"]
        for phrase in ("Display P3", "Linear Rec.709", "View as Render"):
            self.assertIn(phrase, detail)
        self.assertEqual(result["rendered"]["tiles"], 3)
        self.assertTrue(result["uncertain"])

    def test_large_images_are_downscaled_and_a_region_shows_full_detail(self):
        pixels = rows(2048, 1024, (255, 255, 255, 255))
        pixels[500:502, 1500:1502] = RED
        self.blender.images.append(fakes.byte_image("Detail", pixels))
        whole = self.show("IMAGE:Detail", resolution=512)
        self.assertEqual((whole["views"][0]["width"], whole["views"][0]["height"]), (512, 256))
        part = self.show("IMAGE:Detail", region=[0.7, 0.45, 0.76, 0.51])
        self.assertEqual(int(((self.delivered(part)[..., 1] == 0) & (self.delivered(part)[..., 0] == 255)).sum()), 4)


class UVLayoutTests(SourceCase):
    SQUARE = [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]

    def mesh(self, **options):
        obj = fakes.uv_mesh("Crate", [[0, 1, 2, 3]], self.SQUARE, **options)
        self.blender.objects[MESH] = obj
        return obj

    def test_the_layout_is_drawn_over_the_active_image_texture(self):
        texture = fakes.byte_image("Crate Albedo", rows(512, 512, GREEN))
        self.mesh(material=fakes.material(texture, active=0))
        result = self.show("UV", targets=[MESH], resolution=256)
        picture = self.delivered(result)
        self.assertEqual(picture.shape[:2], (256, 256))
        self.assertEqual(picture[64, 128, :3].tolist(), [255, 140, 0], "the top edge, v = 0.75")
        self.assertEqual(picture[128, 64, :3].tolist(), [255, 140, 0], "the left edge, u = 0.25")
        self.assertEqual(picture[128, 128, :3].tolist(), [0, 255, 0], "the texture inside the face")
        facts = result["rendered"]
        self.assertEqual((facts["uv_map"], facts["faces"], facts["edges"], facts["edges_drawn"]), ("UVMap", 1, 4, 4))
        self.assertEqual((facts["texture"], facts["texture_shown"], facts["texture_choice"]),
                         ("Crate Albedo", True, "active image node"))
        self.assertEqual((result["views"][0]["framing"], facts["uv_outside"], facts["edit_mode"]), ("UV_LAYOUT", 0.0, False))
        self.assertIn("UV map 'UVMap' of 'Crate', 256x256.", result["note"])
        self.assertIn("orange lines over image 'Crate Albedo'", result["note"])

    def test_a_texture_not_in_memory_is_not_loaded_and_the_layout_is_drawn_over_a_checkerboard(self):
        texture = fakes.byte_image("Crate Albedo", rows(64, 64, GREEN), cached=False, source="FILE")
        self.mesh(material=fakes.material(texture, active=0))
        result = self.show("UV", targets=[MESH], resolution=256)
        self.assertFalse(texture.loaded)
        self.assertEqual(texture.reads, 0)
        self.assertEqual(self.delivered(result)[1, 1, :3].tolist(), [204, 204, 204])
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["uv_texture_not_loaded"])
        self.assertFalse(result["rendered"]["texture_shown"])

    def test_a_texture_is_not_read_while_a_bake_may_write_it(self):
        texture = fakes.byte_image("Crate Albedo", rows(64, 64, GREEN))
        self.mesh(material=fakes.material(texture, active=0))
        self.blender.bpy.app.is_job_running = lambda kind: kind == "RENDER"
        result = self.show("UV", targets=[MESH], resolution=256)
        self.assertEqual([entry["code"] for entry in result["uncertainty"]], ["uv_texture_busy"])
        self.assertEqual(texture.reads, 0)

    def test_a_named_texture_and_a_layout_without_texture(self):
        self.blender.images.append(fakes.byte_image("Grid", rows(32, 16, RED)))
        self.mesh()
        named = self.show("UV:Grid", targets=[MESH], resolution=256)
        self.assertEqual((named["views"][0]["width"], named["views"][0]["height"]), (256, 128), "the texture's aspect")
        self.assertEqual(named["rendered"]["texture_choice"], "named")
        plain = self.show("UV", targets=[MESH], resolution=256)
        self.assertEqual((plain["rendered"]["texture"], plain["rendered"]["texture_choice"]), (None, "none"))
        self.assertIn("over a grey checkerboard", plain["note"])

    def test_edit_mode_reads_the_live_edit_mesh(self):
        self.mesh(edit=True)
        with patch.dict(sys.modules, {"bmesh": fakes.bmesh_module([self.SQUARE, [(0.0, 0.0), (2.0, 0.0), (0.0, 1.0)]])}):
            result = self.show("UV", targets=[MESH], resolution=256)
        facts = result["rendered"]
        self.assertEqual((facts["edit_mode"], facts["faces"], facts["edges"]), (True, 2, 7))
        self.assertEqual(facts["uv_outside"], round(1 / 7, 4), "one of seven face corners lies outside 0 to 1")
        self.assertIn("read from Edit Mode", result["note"])

    def test_a_region_draws_the_part_at_full_detail(self):
        self.mesh()
        result = self.show("UV", targets=[MESH], resolution=256, region=[0.0, 0.0, 0.5, 0.5])
        view = result["views"][0]
        self.assertEqual((view["width"], view["height"]), (256, 256))
        self.assertEqual(result["rendered"]["source_size"], [512, 512])
        self.assertEqual(self.delivered(result)[128, 200, :3].tolist(), [255, 140, 0], "v = 0.75 at the part's middle")

    def test_what_has_no_layout_is_refused(self):
        self.blender.objects[MESH] = SimpleNamespace(name="Lamp", type="LIGHT")
        with self.assertRaisesRegex(RuntimeError, "'Lamp' is a LIGHT"):
            self.show("UV", targets=[MESH])
        obj = self.mesh()
        obj.data.uv_layers.active = None
        with self.assertRaisesRegex(RuntimeError, "has no UV map"):
            self.show("UV", targets=[MESH])


MASK = [72, 72, 72]


class EditorTests(SourceCase):
    """What the person sees: Blender's screenshot of the window, masked, or of one editor."""

    def setUp(self):
        super().setUp()
        areas = [fakes.area("VIEW_3D", 0, 10, 120, 90), fakes.area("TEXT_EDITOR", 120, 10, 80, 50),
                 fakes.area("CONSOLE", 120, 60, 80, 40)]
        window = fakes.editor_window(200, 100, areas, [(200, 0, 0), (0, 200, 0), (0, 0, 200)])
        window.scene = self.blender.scene
        self.blender.window = window
        self.blender.bpy.context.window_manager.windows[:] = [window]

    def test_the_window_is_shown_with_code_consoles_and_the_status_bar_greyed_out(self):
        result = self.show("EDITOR", resolution=256)
        picture = self.delivered(result)
        self.assertEqual(picture.shape[:2], (100, 200))
        self.assertEqual(picture[50, 60, :3].tolist(), [200, 0, 0], "the 3D Viewport as drawn")
        for row, column, what in ((60, 160, "text editor"), (20, 160, "console"), (95, 60, "status bar")):
            self.assertEqual(picture[row, column, :3].tolist(), MASK, what)
        facts = result["rendered"]
        self.assertEqual((facts["editor"], facts["window_size"], facts["masked"]),
                         ("WINDOW", [200, 100], ["TEXT_EDITOR", "CONSOLE", "STATUSBAR"]))
        self.assertEqual(facts["editors"], ["VIEW_3D", "TEXT_EDITOR", "CONSOLE"])
        self.assertEqual((result["views"][0]["framing"], result["coverage"]), ("EDITOR", "EDITOR"))
        self.assertIn("The Blender window as the person sees it, 200x100.", result["note"])
        self.assertIn("Greyed out: TEXT_EDITOR, CONSOLE, STATUSBAR.", result["note"])
        self.assertIn("not render pixels", result["note"])
        self.assertIn("The person is told in the chat that this picture was sent to you.", result["note"])
        self.assertEqual(len(self.reference_files()), 1)
        (kind, row), = self.events
        self.assertEqual((kind, row["role"], row["notice"]), ("message", "system", True), "host-only: no model sees it")
        self.assertEqual(row["text"], "The agent was sent a picture of your window as you see it (3D Viewport), with any "
                                      "file paths shown there; text editors, consoles, the file browser and the status "
                                      "bar were greyed out.")
        self.assertEqual(row["i18n"], [view_editor.WINDOW_NOTICE, {"editors": "3D Viewport"}], "translatable at display")

    def test_one_editor_is_cropped_from_the_window(self):
        result = self.show("EDITOR:VIEW_3D", resolution=256)
        picture = self.delivered(result)
        self.assertEqual(picture.shape[:2], (90, 120))
        self.assertEqual({tuple(pixel) for pixel in picture[..., :3].reshape(-1, 3).tolist()}, {(200, 0, 0)})
        self.assertEqual((result["rendered"]["editor"], result["rendered"]["area_size"]), ("VIEW_3D", [120, 90]))
        self.assertIn("The VIEW_3D editor as the person sees it", result["note"])
        self.assertEqual([row["text"] for _kind, row in self.events],
                         ["The agent was sent a picture of your 3D Viewport as you see it, with any file paths shown "
                          "in it."])

    def test_the_largest_editor_of_a_type_in_any_window_is_found(self):
        small = fakes.area("IMAGE_EDITOR", 0, 0, 20, 20, ui_type="UV")
        large = fakes.area("IMAGE_EDITOR", 20, 0, 60, 40, ui_type="UV")
        other = fakes.editor_window(80, 40, [small, large], [(9, 9, 9), (0, 120, 255)])
        other.scene = self.blender.scene
        self.blender.bpy.context.window_manager.windows.append(other)
        result = self.show("EDITOR:UV")
        self.assertEqual(self.delivered(result)[5, 5, :3].tolist(), [0, 120, 255])
        self.assertIs(self.blender.screenshots[-1], other, "captured in the window that shows it")

    def test_editors_that_can_show_code_or_paths_are_refused_and_missing_ones_listed(self):
        with self.assertRaisesRegex(RuntimeError, "TEXT_EDITOR is not captured: text editors can show code"):
            self.show("EDITOR:TEXT_EDITOR")
        with self.assertRaisesRegex(RuntimeError, "No NODE_EDITOR editor is open. Open editors: VIEW_3D, TEXT_EDITOR"):
            self.show("EDITOR:NODE_EDITOR")
        self.assertEqual(self.blender.screenshots, [], "nothing was captured")
        self.assertEqual(self.events, [], "and nothing is announced")

    def test_no_capture_without_a_chat_that_can_announce_it(self):
        del self.controller.emit
        with self.assertRaisesRegex(RuntimeError, "announced to the person in the chat, and this conversation has none"):
            self.show("EDITOR")
        self.assertEqual((self.blender.screenshots, self.reference_files()), ([], []))

    def test_a_capture_that_fails_after_the_screenshot_is_not_announced(self):
        with patch("angelo_agent.view_picture.scaled", side_effect=RuntimeError("decoder failed")):
            with self.assertRaisesRegex(RuntimeError, "decoder failed"):
                self.show("EDITOR")
        self.assertEqual(self.events, [])

    def test_background_mode_and_a_modal_interaction_are_refused(self):
        self.blender.window.modal_operators = [SimpleNamespace(bl_idname="TRANSFORM_OT_translate")]
        with self.assertRaisesRegex(RuntimeError, "Finish the current Blender interaction"):
            self.show("EDITOR")
        self.blender.bpy.app.background = True
        with self.assertRaisesRegex(RuntimeError, "without a window"):
            self.show("EDITOR")
        self.assertEqual((self.blender.screenshots, self.reference_files()), ([], []))

    def test_a_screen_wider_than_the_encoder_is_reduced_in_whole_steps(self):
        wide = fakes.editor_window(5000, 20, [fakes.area("VIEW_3D", 0, 0, 5000, 20)], [(10, 200, 10)])
        wide.scene = self.blender.scene
        self.blender.window = wide
        result = self.show("EDITOR", resolution=1024)
        self.assertEqual(result["rendered"]["source_size"], [2500, 10])
        self.assertEqual(result["rendered"]["window_size"], [5000, 20])


class ValidationTests(unittest.TestCase):
    def validate(self, **arguments):
        return view_spec.validate_view("angelo_view", dict(LOOK, **arguments))

    def test_new_forms_validate_and_validation_is_idempotent(self):
        for arguments in ({"image": "IMAGE:Albedo"}, {"image": "UV", "targets": [MESH]}, {"image": "EDITOR"},
                          {"image": "RENDER_RESULT:Depth"}, {"image": "j3:View Layer.Normal", "file": "beauty.exr"},
                          {"image": "LAST_JOB:Depth", "frame": 4},
                          {"image": "EDITOR:ShaderNodeTree"},
                          {"image": "UV:Grid", "targets": [MESH]},
                          {"views": ["FRONT"], "shading": "WEIGHT", "targets": [MESH], "group": "Spine"},
                          {"views": ["ISO"], "shading": "ATTRIBUTE"}):
            with self.subTest(arguments=arguments):
                checked = self.validate(**arguments)
                self.assertEqual(view_spec.validate_view("angelo_view", checked), checked)

    def test_malformed_new_forms_are_refused_with_what_to_pass(self):
        for arguments, pattern in (
                ({"image": "IMAGE"}, "IMAGE:<name> names one Image"),
                ({"image": "IMAGE:" + "x" * 201}, "at most 200 printable"),
                ({"image": "IMAGE:a\nb"}, "image is RENDER_RESULT"),
                ({"image": "UV"}, "UV shows one mesh's UV layout"),
                ({"image": "UV", "targets": [MESH, MESH[:-1] + "5"]}, "UV shows one mesh's UV layout"),
                ({"image": "IMAGE:Albedo", "shading": "LIT"}, "use it with RENDERED shading"),
                ({"image": "RENDER_RESULT:"}, "image is RENDER_RESULT, LAST_JOB or the ID of a render job"),
                ({"image": "IMAGE:Albedo", "file": "a.png"}, "file names one output of a job"),
                ({"views": ["FRONT"], "shading": "WEIGHT"}, "WEIGHT draws the vertex weights of mesh targets"),
                ({"views": ["FRONT"], "shading": "SOLID", "group": "Spine"}, "group names the vertex group WEIGHT"),
                ({"views": ["FRONT"], "shading": "WEIGHT", "targets": [MESH], "group": ""}, "group")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, pattern):
                self.validate(**arguments)

    def test_split_image_names_the_source_and_its_name(self):
        self.assertEqual(view_spec.split_image("IMAGE:Wood: dark"), ("IMAGE", "Wood: dark"))
        self.assertEqual(view_spec.split_image("UV"), ("UV", None))
        self.assertEqual(view_spec.split_image("j12"), ("j12", None))


if __name__ == "__main__":
    unittest.main()
