# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every picture the viewer makes is written within the chat's image limit (``picture_budget``).

Break-it of 2298d608dd1, M1: the marked picture, a Resize and the 3D view's capture were encoded with no size budget,
so a phone photo (a 1.9 MB JPEG) became a 6.23 MiB marked PNG that the reference store refused at Send ("Invalid image
reference"), and Resize wrote 7.7 to 15.7 MiB pictures the composer could not send. No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import struct
import sys
import tempfile
import unittest
import uuid
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import markup_doc as md, markup_store as ms, picture_budget  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_png import encode_png  # noqa: E402


def photo(width, height, alpha=False):
    """RGBA8 rows like a photo's: smooth light with fine grain, which a PNG compresses as poorly as a camera's."""
    import numpy
    rng = numpy.random.default_rng(20261120)
    y, x = numpy.mgrid[0:height, 0:width].astype(numpy.float32)
    rows = numpy.empty((height, width, 4), numpy.float32)
    rows[..., 0] = 128 + 90 * numpy.sin(x / 211.0) * numpy.cos(y / 173.0)
    rows[..., 1] = 110 + 70 * numpy.sin((x + y) / 301.0)
    rows[..., 2] = 100 + 60 * numpy.cos(x / 97.0 - y / 131.0)
    rows[..., :3] += rng.normal(0, 14, (height, width, 3)).astype(numpy.float32)
    rows[..., 3] = 255 if not alpha else 128 + 127 * numpy.sin(x / 50.0)
    return numpy.clip(rows, 0, 255).astype(numpy.uint8)


def png_size(data):
    """(width, height) from a PNG's IHDR."""
    return struct.unpack(">II", data[16:24])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ReferenceStore(Path(self.folder.name) / "state", str(uuid.uuid4()))

    def test_a_marked_up_photo_is_sent_within_the_limit(self):
        width, height = 4032, 3024
        rows = photo(width, height)
        data, w, h = picture_budget.encode_within(rows.tobytes(), width, height, self.store.image_limit)
        self.assertLessEqual(len(data), self.store.image_limit)
        self.assertEqual(png_size(data), (w, h))
        factor = width // w
        self.assertEqual((w, h), (width // factor, height // factor), "one whole factor on both sides")
        self.assertLess(w, width, "this photo needed stepping down")
        source = Path(self.folder.name) / "photo.png"
        source.write_bytes(encode_png(rows[::8, ::8].tobytes(), width // 8, height // 8))
        original = self.store.import_images([source])[0]
        marked = self.store.new_observation("photo.png (marked up)")
        Path(marked["path"]).write_bytes(data)
        doc = md.MarkupDoc(original["id"], (width, height))
        doc.add_comment(at=(10, 10), text="here")
        sent = ms.freeze(self.store, doc, original, marked)   # The store's own check: refused before the fix.
        self.assertEqual(sent[0]["id"], marked["id"])

    def test_a_picture_with_transparency_keeps_it_within_the_limit(self):
        width, height = 3000, 2000
        data, w, h = picture_budget.encode_within(photo(width, height, alpha=True).tobytes(), width, height,
                                                  self.store.image_limit, alpha=True)
        self.assertLessEqual(len(data), self.store.image_limit)
        self.assertEqual(data[25], 6, "colour type 6: the alpha is kept")

    def test_a_picture_that_fits_is_written_as_it_is(self):
        rows = photo(640, 480)
        data, w, h = picture_budget.encode_within(rows.tobytes(), 640, 480, self.store.image_limit)
        self.assertEqual((w, h), (640, 480))
        self.assertEqual(data, encode_png(rows.tobytes(), 640, 480))

    def test_a_limit_nothing_can_meet_is_refused_never_written_over(self):
        with self.assertRaises(ValueError):
            picture_budget.encode_within(photo(64, 64).tobytes(), 64, 64, 60)

    def test_every_picture_the_viewer_writes_goes_through_the_budget(self):
        """The 3D view's capture, Send's marked picture, Resize and Erase's source: no writer encodes on its own."""
        def body(module, name):
            tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
            return ast.unparse(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name))
        for module, name in (("markup_ui.py", "capture_view"), ("markup_send.py", "send"),
                             ("markup_edit_host.py", "_encode_then")):
            with self.subTest(writer=name):
                code = body(module, name)
                self.assertIn("encode_within(", code)
                self.assertNotIn("encode_png(", code)
                self.assertNotIn("rgba_png(", code)


if __name__ == "__main__":
    unittest.main()
