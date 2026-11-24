# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's attachment tiles never do picture work while drawing (1.0.8 audit P2). No Blender needed.

The audit of candidate 2360354055f found ``studio_media_tiles`` loading, decoding, resizing and uploading inside
its draw callback, and a movie poster skipping the 384-pixel limit (only a still image was scaled), kept at full
size in a 48-entry cache. Here: a draw only asks ``studio_tile_previews`` and queues; the timer's pump prepares;
every picture, a movie's first frame included, is held to ``SIDE`` pixels; the cache is bounded by GPU bytes.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import studio_tile_previews as tp  # noqa: E402

# Calls that read a file, decode or resize pixels, or make a GPU texture: never in a draw.
PICTURE_WORK = {"load", "from_image", "scale", "GPUTexture", "foreach_get", "decode", "upload", "pump", "Buffer"}


def fake_image(width, height, source):
    image = MagicMock(source=source)
    image.size = (width, height)
    image.pixels.foreach_get.side_effect = lambda out: out.__setitem__(slice(None), 0.5)
    return image


class Bounded(unittest.TestCase):
    def test_every_size_is_held_to_the_limit(self):
        for width, height in ((1280, 720), (1920, 1080), (4096, 2160), (720, 1280), (385, 10), (5000, 1)):
            with self.subTest(size=(width, height)):
                pixels, w, h = tp.bounded(np.zeros(width * height * 4, np.float32), width, height)
                self.assertLessEqual(max(w, h), tp.SIDE)
                self.assertEqual(len(pixels), w * h * 4)

    def test_a_picture_within_the_limit_is_kept_as_it_is(self):
        pixels = np.arange(300 * 200 * 4, dtype=np.float32)
        out, w, h = tp.bounded(pixels, 300, 200)
        self.assertEqual((w, h), (300, 200))
        self.assertIs(out, pixels)

    def test_shrinking_averages_whole_blocks(self):
        grid = np.zeros((800, 800, 4), np.float32)
        grid[:, ::2] = 1.0   # Alternate columns: an area filter gives grey, never stripes.
        out, w, h = tp.bounded(grid.reshape(-1), 800, 800)
        self.assertEqual((w, h), (266, 266))
        self.assertTrue(np.allclose(np.asarray(out).reshape(h, w, 4)[..., 0], 0.5, atol=0.2))


class Decode(unittest.TestCase):
    def decode(self, image):
        bpy = MagicMock()
        bpy.data.images.load.return_value = image
        with patch.dict(sys.modules, {"bpy": bpy}):
            result = tp.decode("/x/clip.mp4")
        bpy.data.images.remove.assert_called_once_with(image)   # Never left in the person's file.
        return result

    def test_a_movie_poster_is_held_to_the_same_limit_as_a_picture(self):
        for source in ("MOVIE", "FILE"):
            with self.subTest(source=source):
                _pixels, w, h = self.decode(fake_image(1920, 1080, source))
                self.assertLessEqual(max(w, h), tp.SIDE)

    def test_stored_values_are_read(self):
        image = fake_image(64, 64, "FILE")
        self.decode(image)
        self.assertIs(image.colorspace_settings.is_data, True)   # The configuration's data role (color_spaces).


class HeaderBoundAndTurn(unittest.TestCase):
    """Re-review of markup 4e712847d3b. S1: a 0.8 MiB flat 16384 x 16384 PNG passed the 5 MiB check and Blender
    decoded all of it (1 GiB, 4 GiB at 16 bits) before the pixel limit refused it, at every open, chip, Send and
    Resize. S3: a phone portrait (EXIF orientation 6) opened and was marked sideways."""

    def test_a_picture_over_the_pixel_limit_is_refused_by_its_header_before_any_decode(self):
        import struct
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, "flat.png")
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 16384, 16384)
                             + b"\x08\x02\x00\x00\x00" + b"\x00" * 64)
            bpy = MagicMock()
            with patch.dict(sys.modules, {"bpy": bpy}), self.assertRaises(ValueError) as caught:
                tp.decode(path)
            bpy.data.images.load.assert_not_called()
            self.assertEqual(str(caught.exception), tp.TOO_MANY_PIXELS)

    def test_a_known_format_whose_header_cant_be_read_is_refused_before_any_decode(self):
        """Re-review of b5808fd5a2e, S7: a JPEG with 5,000 comment segments before a 16K frame header outlasted the
        header walk, and decode handed it to Blender whole; a zero-size header was "too many pixels" (N6)."""
        import struct
        import tempfile
        comments = b"".join(b"\xff\xfe" + struct.pack(">H", 18) + b"x" * 16 for _ in range(5000))
        frame = b"\xff\xc2" + struct.pack(">H", 17) + bytes((8,)) + struct.pack(">HH", 16384, 16384) + b"\x03" + b"\x00" * 9
        png0 = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 0, 600) + b"\x08\x02\x00\x00\x00"
        with tempfile.TemporaryDirectory() as folder:
            for name, data, sentence in (("crafted.jpg", b"\xff\xd8" + comments + frame, tp.UNREADABLE),
                                         ("zero.png", png0 + b"\x00" * 64, tp.NO_PIXELS)):
                with self.subTest(name=name):
                    path = Path(folder, name)
                    path.write_bytes(data)
                    bpy = MagicMock()
                    with patch.dict(sys.modules, {"bpy": bpy}), self.assertRaises(ValueError) as caught:
                        tp.decode(path)
                    bpy.data.images.load.assert_not_called()
                    self.assertEqual(str(caught.exception), sentence)

    def test_each_exif_orientation_turns_the_pixels_as_the_standard_says(self):
        """Displayed (row, column), rows top first, from stored ones, for a 2 x 3 picture (EXIF 2.32, tag 0x0112)."""
        import numpy as np
        h, w = 2, 3
        stored = [[(r * w + c) for c in range(w)] for r in range(h)]
        expected = {1: lambda r, c: stored[r][c], 2: lambda r, c: stored[r][w - 1 - c],
                    3: lambda r, c: stored[h - 1 - r][w - 1 - c], 4: lambda r, c: stored[h - 1 - r][c],
                    5: lambda r, c: stored[c][r], 6: lambda r, c: stored[h - 1 - c][r],
                    7: lambda r, c: stored[h - 1 - c][w - 1 - r], 8: lambda r, c: stored[c][w - 1 - r]}
        rows_bottom_first = [stored[r] for r in reversed(range(h))]
        flat = np.array([[v, 0, 0, 1] for row in rows_bottom_first for v in row], dtype=np.float32).reshape(-1)
        for turn, value in expected.items():
            with self.subTest(turn=turn):
                out, ow, oh = tp.upright(flat, w, h, turn)
                self.assertEqual((ow, oh), (h, w) if turn >= 5 else (w, h))
                seen = np.asarray(out).reshape(oh, ow, 4)[::-1, :, 0]   # Top first again.
                self.assertEqual([[int(v) for v in row] for row in seen],
                                 [[value(r, c) for c in range(ow)] for r in range(oh)])


class Previews(unittest.TestCase):
    def make(self, decoder=None, budget=tp.GPU_BYTES):
        self.decoded, self.woken = [], []

        def decode(path):
            self.decoded.append(path)
            return (decoder or (lambda p: ([0.0] * (100 * 50 * 4), 100, 50)))(path)
        return tp.Previews(decoder=decode, uploader=lambda pixels, w, h: object(), wake=lambda: self.woken.append(1),
                           budget=budget)

    def test_asking_while_drawing_never_decodes(self):
        previews = self.make()
        self.assertIsNone(previews.texture("/a.png"))
        self.assertIsNone(previews.texture("/a.png"))
        self.assertEqual(self.decoded, [])          # Nothing read while drawing,
        self.assertEqual(len(self.woken), 1)        # only the timer woken, once.
        self.assertTrue(previews.pump(float("inf")))
        self.assertEqual(self.decoded, ["/a.png"])
        self.assertEqual(previews.texture("/a.png").width, 100)

    def test_the_cache_is_bounded_by_gpu_bytes(self):
        one = 100 * 50 * tp.TEXEL_BYTES
        previews = self.make(budget=3 * one)
        for index in range(5):
            previews.texture("/%d.png" % index)
        previews.pump(float("inf"))
        self.assertLessEqual(previews.used, 3 * one)
        self.assertIsNone(previews.texture("/0.png"))          # The least recently drawn went first,
        self.assertIsNotNone(previews.texture("/4.png"))       # the newest stays.

    def test_a_file_that_cant_be_shown_is_not_read_again_on_every_draw(self):
        def broken(path):
            raise OSError("unreadable")
        previews = self.make(decoder=broken)
        previews.texture("/bad.png")
        previews.pump(float("inf"))
        previews.texture("/bad.png")
        previews.pump(float("inf"))
        self.assertEqual(self.decoded, ["/bad.png"])

    def test_a_tick_stops_at_its_deadline(self):
        previews = self.make()
        for index in range(3):
            previews.texture("/%d.png" % index)
        previews.pump(0.0)   # Already past: nothing prepared, the queue kept.
        self.assertEqual(self.decoded, [])
        self.assertTrue(previews.pending())


class DrawPath(unittest.TestCase):
    """Every function of the tile module is drawing: none may do picture work, and no Blender at import."""

    def test_the_tile_module_never_reads_or_uploads_a_picture(self):
        tree = ast.parse((ADDON / "studio_media_tiles.py").read_text(encoding="utf-8"))
        calls = sorted({node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                        for node in ast.walk(tree) if isinstance(node, ast.Call)} & PICTURE_WORK)
        self.assertEqual(calls, [], "picture work in a draw path: prepare it in studio_tile_previews")
        imports = {alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names}
        self.assertFalse(imports & {"bpy", "gpu"}, "the tile module imports Blender at load")


if __name__ == "__main__":
    unittest.main()
