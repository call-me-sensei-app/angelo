# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A brand picture is on the GPU while a surface shows it and never held as a float copy beside its texture.

an-blender53's first-open memory on 9d7a54eab7f: the welcome banner's float pixels (11.7 MB, welcome_screen.py:56)
stayed in memory for the whole session at every start, a second reference kept them for the AI Providers page, and
the showcase held its 23 MB still beside its texture. Here: the holder's lifecycle with a stand-in GPU, and every
surface that draws one of the two pictures wired to it. The real read and draw: test_blender_color_config (the
values under every colour configuration) and test_blender_start_panel (no copy traced once drawn or closed).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import brand_picture  # noqa: E402


class FakeGPU:
    def __init__(self):
        self.made = []
        self.types = SimpleNamespace(GPUTexture=self.texture, Buffer=lambda kind, shape, data: (kind, shape, data))

    def texture(self, size, format, data):
        self.made.append((size, format, data[1]))
        return ("texture", size)


def picture():
    item = brand_picture.Picture("banner.png")
    reads = []

    def read(path):
        reads.append(path)
        return [0.5] * 24, (3, 2)
    return item, reads, patch.object(brand_picture, "read", read)


class PictureLifecycleTests(unittest.TestCase):
    def test_the_first_draw_makes_the_texture_and_drops_the_float_copy(self):
        item, reads, reading = picture()
        gpu = FakeGPU()
        with reading:
            item.show("welcome")
        self.assertTrue(item.ready and item.holds_pixels())
        texture = item.texture(gpu)
        self.assertEqual(gpu.made, [((3, 2), 'RGBA16F', [24])])
        self.assertTrue(item.ready, "Drawn from the GPU from now on")
        self.assertFalse(item.holds_pixels(), "No float copy beside the texture")
        self.assertIs(item.texture(gpu), texture)
        self.assertEqual(len(gpu.made), 1, "One texture, however many draws")
        self.assertEqual(len(reads), 1)

    def test_the_last_surface_to_close_lets_it_go_and_the_next_one_reads_it_again(self):
        item, reads, reading = picture()
        gpu = FakeGPU()
        with reading:
            item.show("welcome")
            item.show("about")
            item.texture(gpu)
            item.hide("welcome")
            self.assertTrue(item.ready, "Another surface still shows it")
            self.assertEqual(len(reads), 1, "Shared: read once")
            item.hide("about")
            self.assertFalse(item.ready)
            self.assertIsNone(item.texture(gpu), "Let go: nothing to draw until a surface shows it again")
            item.show("about")
        self.assertEqual(len(reads), 2)
        self.assertIsNotNone(item.texture(gpu))

    def test_a_missing_file_is_read_once_and_leaves_the_plain_fallback(self):
        item = brand_picture.Picture("missing.png")
        calls = []

        def read(path):
            calls.append(path)
            raise OSError("missing")
        with patch.object(brand_picture, "read", read):
            item.show("welcome")
            item.prepare()
        self.assertEqual((item.ready, item.failed, len(calls)), (False, True, 1))
        self.assertIsNone(item.texture(FakeGPU()))

    def test_release_forgets_the_surfaces(self):
        item, reads, reading = picture()
        with reading:
            item.show("welcome")
        item.release()
        self.assertEqual((item.ready, item.shown_by()), (False, frozenset()))


def tree(name):
    return ast.parse((ADDON / name).read_text(encoding="utf-8"))


def calls_in(node):
    return {ast.unparse(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)}


def function(module, name, cls=None):
    body = tree(module).body
    if cls:
        body = next(node for node in body if isinstance(node, ast.ClassDef) and node.name == cls).body
    return next(node for node in body if isinstance(node, ast.FunctionDef) and node.name == name)


class SurfaceWiringTests(unittest.TestCase):
    def test_no_surface_reads_a_brand_picture_through_an_image_datablock(self):
        """A datablock added while Preferences is open closes a dialog over it; imbuf adds none."""
        for name in ("welcome_screen.py", "connect_hero.py", "providers_page_model.py", "providers_page_draw.py",
                     "about_card.py", "brand_picture.py"):
            source = (ADDON / name).read_text(encoding="utf-8")
            self.assertNotIn("images.load(", source, name)
            self.assertNotIn("foreach_get", source, name)

    def test_cards_show_their_pictures_while_open(self):
        host = calls_in(tree("card_host.py"))
        self.assertTrue({"kit.opened", "kit.closed"} <= host and not {"card.opened", "state.card.closed"} & host,
                        "card_host opens and closes every card with its pictures (card_kit.opened / closed)")
        self.assertIn("picture.show", calls_in(function("card_kit.py", "opened")))
        self.assertIn("picture.hide", calls_in(function("card_kit.py", "closed")))
        for module, cls in (("about_card.py", "AboutCard"), ("setup_card.py", "SetupCard"),
                            ("start_card.py", "StartCard")):
            body = next(node for node in tree(module).body if isinstance(node, ast.ClassDef) and node.name == cls).body
            self.assertTrue(any(isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pictures"
                                                                      for t in node.targets) for node in body), cls)

    def test_the_welcome_screen_the_page_and_the_showcase_let_go_when_they_close(self):
        self.assertIn("welcome_screen.BANNER_PICTURE.show", calls_in(function("intro.py", "invoke", "ANGELO_OT_intro")))
        self.assertIn("welcome_screen.BANNER_PICTURE.hide", calls_in(function("intro.py", "_close")))
        self.assertIn("SHOWCASE.show", calls_in(function("connect_hero.py", "_show")))
        self.assertIn("SHOWCASE.hide", calls_in(function("connect_hero.py", "_release")))
        page = "ANGELO_OT_providers_page_input"
        self.assertIn("providers_page_model.show_art", calls_in(function("providers_page_host.py", "invoke", page)))
        self.assertIn("providers_page_model.hide_art", calls_in(function("providers_page_host.py", "cancel", page)))
        self.assertIn("self.cancel", calls_in(function("providers_page_host.py", "_finish", page)),
                      "A closed Preferences window ends the page's input through cancel, not modal; modal's end too")

    def test_the_start_panel_reads_its_previews_when_it_opens_and_frees_them_when_it_closes(self):
        self.assertIn("fc.thumbnail", calls_in(function("start_card.py", "opened", "StartCard")))
        self.assertIn("fc.release", calls_in(function("start_card.py", "closed", "StartCard")))
        self.assertNotIn("thumbnail", ast.unparse(function("splash_setup.py", "open_start")))
        image = ast.unparse(function("firstlaunch_controls.py", "_image"))
        self.assertIn("entry['pixels'] = None", image, "A tile's preview keeps no float copy beside its texture")

    def test_nothing_reads_the_art_at_start_up(self):
        """Read when a surface shows it, never by a start-up timer that keeps it for a page nobody opened."""
        self.assertNotIn("preload", (ADDON / "quick_connect.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
