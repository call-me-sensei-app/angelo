# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every picture Angelo draws shows the colours of its file: the shader matches where the texels come from.

A texture made by ``gpu.texture.from_image`` holds scene-linear texels (Blender decodes an sRGB file as it uploads
it), so it is drawn with ``IMAGE_SCENE_LINEAR_TO_REC709_SRGB``; with the plain ``IMAGE`` shader the linear values
reach the screen as they are and the picture shows darker and more saturated (an orange photo shows red: Media
Studio's grid, its Generation Flows previews and its chat cards before 1.0.8). A texture of stored pixel values
(``Image.pixels`` of a PNG or WebP, decoded PNG bytes, a .blend preview, a drawn icon) or of a colour-managed
offscreen already holds what the screen shows, so it is drawn with ``IMAGE`` or ``IMAGE_COLOR`` (or
``draw_texture_2d``) and never converted again.

Every add-on module that makes a GPU texture or offscreen, or draws one, is in exactly one of the classes below, so
a new picture surface has to say where its texels come from. The Studio grid and the photo drop card were found by
their screenshots (1.0.8); this pins the class, not those two surfaces.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"

SCENE_LINEAR_SHADERS = {"IMAGE_SCENE_LINEAR_TO_REC709_SRGB", "IMAGE_COLOR_SCENE_LINEAR_TO_REC709_SRGB"}
STORED_SHADERS = {"IMAGE", "IMAGE_COLOR"}

# Textures from gpu.texture.from_image: module -> the module whose image shader draws them.
SCENE_LINEAR = {
    "studio_draw": "studio",   # Media Studio's pictures (grid, chat cards, Generation Flows): core._image_shader.
    "photo_drop": "photo_drop",   # The photo drop card's dropped photo (_draw_texture).
}
# Texels are the stored, display-encoded values of the picture.
STORED = {
    "asset_browser_states": "drawn icon pixels",
    "asset_shelf_add": "drawn icon pixels",
    "base_mesh_thumbs": "PNG bytes decoded by the add-on",
    "connect_hero": "Image.pixels of a WebP read as Non-Color",
    "firstlaunch_controls": ".blend preview pixels",
    "library_host": "Image.pixels of a PNG, JPEG or WebP thumbnail read as Non-Color",
    "library_overlay_draw": "library_host's thumbnail textures",
    "fab_overlay": "library_host's thumbnail textures (Fab result and details pictures)",
    "native_preview_images": "Image.pixels of a PNG read as Non-Color",
    "providers_page_draw": "the welcome banner's pixels (welcome_screen)",
    "shelf_empty": "asset_shelf_add's icon texture",
    "studio_media_tiles": "studio_tile_previews' textures (stored values)",
    "studio_tile_previews": "Image.pixels of a picture or a movie's first frame read as Non-Color, bounded",
    "welcome_art": "drawn icon pixels",
    "welcome_screen": "Image.pixels of a PNG read as Non-Color",
}
# Offscreens that already hold what the screen shows: the interface drawn into them, or a colour-managed 3D draw.
DISPLAY = {
    "live_view_draw": "live_view.native_draw (colour-managed offscreen draw)",
    "marble_frames": "the chat region's own interface",
    "rail_pictures": "live_view.native_draw (colour-managed offscreen draw)",
    "session_rail_draw": "rail_pictures' offscreens",
}
# Offscreens read back as pixels, never drawn as a picture.
READ_BACK = {"program_preview", "sculpt", "view_capture", "view_first_draw", "view_motion", "view_silhouette",
             "view_weights"}


def _name(node):
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None


def _uses(tree):
    """What a module does with GPU pictures: ``from_image``, ``texture`` (made from pixels or an offscreen),
    ``draw_texture_2d`` and the image builtins it asks for."""
    found = {"from_image": False, "texture": False, "draw_texture_2d": False, "shaders": set()}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        if name == "from_image" and isinstance(node.func, ast.Attribute) and _name(node.func.value) == "texture":
            found["from_image"] = True
        elif name in ("GPUTexture", "GPUOffScreen"):
            found["texture"] = True
        elif name == "draw_texture_2d":
            found["draw_texture_2d"] = True
        elif name == "from_builtin" and node.args and isinstance(node.args[0], ast.Constant):
            if str(node.args[0].value).startswith("IMAGE"):
                found["shaders"].add(node.args[0].value)
    return found


def _modules():
    for path in sorted(ADDON.rglob("*.py")):
        name = ".".join(path.relative_to(ADDON).with_suffix("").parts)
        yield name, _uses(ast.parse(path.read_text(encoding="utf-8"), str(path)))


class PictureColourTests(unittest.TestCase):
    def setUp(self):
        self.modules = dict(_modules())

    def test_every_picture_module_says_where_its_texels_come_from(self):
        classes = (set(SCENE_LINEAR), set(STORED), set(DISPLAY), READ_BACK)
        for name, found in self.modules.items():
            if not (found["from_image"] or found["texture"] or found["draw_texture_2d"] or found["shaders"]):
                continue
            homes = [c for c in classes if name in c] + ([name] if name in SCENE_LINEAR.values() else [])
            with self.subTest(module=name):
                self.assertTrue(homes, "%s makes or draws a GPU picture: add it to one class of this test with the "
                                       "source of its texels, and draw it with that class's shader" % name)
        for listed in set().union(SCENE_LINEAR, SCENE_LINEAR.values(), STORED, DISPLAY, READ_BACK):
            with self.subTest(listed=listed):
                self.assertIn(listed, self.modules, "%s is listed here but is no longer in the add-on" % listed)

    def test_from_image_textures_are_drawn_back_to_srgb(self):
        for name, found in self.modules.items():
            if found["from_image"]:
                with self.subTest(module=name):
                    self.assertIn(name, SCENE_LINEAR, "%s uploads a picture with gpu.texture.from_image (scene-linear "
                                                      "texels): draw it with IMAGE_SCENE_LINEAR_TO_REC709_SRGB" % name)
        for name, shader_module in SCENE_LINEAR.items():
            with self.subTest(module=name):
                self.assertTrue(self.modules[name]["from_image"], "%s no longer uses gpu.texture.from_image" % name)
                self.assertFalse(self.modules[name]["draw_texture_2d"],
                                 "draw_texture_2d uses the plain IMAGE shader: linear texels would show too dark")
                drawn_with = self.modules[shader_module]["shaders"] | self.modules[name]["shaders"]
                self.assertTrue(drawn_with, "%s's pictures are drawn with no image shader of %s" % (name, shader_module))
                self.assertLessEqual(drawn_with, SCENE_LINEAR_SHADERS,
                                     "%s's scene-linear pictures are drawn with %s" % (name, sorted(drawn_with)))

    def test_stored_and_display_pictures_are_not_converted_again(self):
        for name in set(STORED) | set(DISPLAY):
            with self.subTest(module=name):
                self.assertLessEqual(self.modules[name]["shaders"], STORED_SHADERS,
                                     "%s holds display values: a scene-linear shader would wash it out" % name)
                self.assertFalse(self.modules[name]["from_image"])


if __name__ == "__main__":
    unittest.main()
