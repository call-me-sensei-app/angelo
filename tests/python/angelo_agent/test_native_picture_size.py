# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A picture's size, read the way the viewer opens it (``picture_size``).

Break-it of 2298d608dd1, S9: the size was read from the first 64 KiB only, so a JPEG whose frame header comes after a
large EXIF (with its embedded preview), XMP, Photoshop or ICC block did not open ("This picture's size can't be
read"). A JPEG's segments are walked by seeking to its frame header. No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import picture_size  # noqa: E402


def segment(marker, payload):
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def jpeg(width, height, before=b""):
    """A JPEG's header: SOI, ``before`` (segments), a baseline frame header, then the scan's start."""
    frame = segment(0xC0, bytes((8,)) + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + before + frame + segment(0xDA, b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00") + b"\x00" * 64


class SizeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, name, data):
        path = Path(self.folder.name) / name
        path.write_bytes(data)
        return path

    def test_a_jpeg_whose_frame_header_comes_after_a_large_exif_block_opens(self):
        exif = segment(0xE1, b"Exif\x00\x00" + b"\x11" * 60000)   # An embedded preview's worth of metadata.
        icc = segment(0xE2, b"ICC_PROFILE\x00" + b"\x22" * 60000)
        path = self.write("phone.jpg", jpeg(4032, 3024, exif + icc))
        self.assertGreater(path.stat().st_size, 65536)
        self.assertEqual(picture_size.picture_size(path), (4032, 3024))

    def test_png_webp_and_a_small_jpeg_read_from_their_header(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 800, 600) + b"\x08\x06\x00\x00\x00"
        self.assertEqual(picture_size.picture_size(self.write("a.png", png + b"\x00" * 32)), (800, 600))
        self.assertEqual(picture_size.picture_size(self.write("b.jpg", jpeg(320, 240))), (320, 240))

    def test_a_picture_without_a_readable_size_is_refused(self):
        for name, data in (("c.jpg", b"\xff\xd8" + segment(0xE1, b"x" * 10)), ("d.png", b"not a picture"),
                           ("e.jpg", jpeg(0, 10))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                picture_size.picture_size(self.write(name, data))


def exif(turn, order="<"):
    """An APP1 Exif segment whose first directory holds the orientation tag (0x0112, SHORT) = ``turn``."""
    mark = b"II" if order == "<" else b"MM"
    tiff = mark + struct.pack(order + "HI", 42, 8) + struct.pack(order + "H", 1)
    tiff += struct.pack(order + "HHI", 0x0112, 3, 1) + struct.pack(order + "HH", turn, 0) + struct.pack(order + "I", 0)
    return segment(0xE1, b"Exif\x00\x00" + tiff)


class BoundAndOrientationTests(SizeTests):
    """Re-review of markup 4e712847d3b. S1: the viewer opened any picture up to 16,384 a side, so a flat 16K PNG
    was decoded whole. S3: a phone portrait (orientation 6) opened sideways."""

    def test_a_picture_over_the_pixel_limit_does_not_open(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 16384, 16384) + b"\x08\x02\x00\x00\x00"
        with self.assertRaises(ValueError):
            picture_size.picture_size(self.write("flat.png", png + b"\x00" * 32))
        self.assertEqual(picture_size.header_size(self.write("flat2.png", png + b"\x00" * 32)), (16384, 16384))

    def test_a_jpegs_exif_orientation_is_read_and_its_size_is_as_seen(self):
        for order in ("<", ">"):
            for turn, seen in ((1, (4032, 3024)), (3, (4032, 3024)), (6, (3024, 4032)), (8, (3024, 4032))):
                with self.subTest(order=order, turn=turn):
                    path = self.write("p%d.jpg" % turn, jpeg(4032, 3024, exif(turn, order) + segment(0xE2, b"x" * 40000) * 2))
                    self.assertEqual(picture_size.orientation(path), turn)
                    self.assertEqual(picture_size.picture_size(path), seen)

    def test_each_refusal_says_why_in_a_translated_sentence(self):
        """Re-review of b5808fd5a2e, S6: every refusal said "This picture's size can't be read", in English, also for
        a size read and found too large."""
        from angelo_agent import studio_tile_previews as tp
        def png(w, h):
            return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x02\x00\x00\x00"
        for name, data, sentence in (("u.png", b"not a picture", picture_size.SIZE_UNREADABLE),
                                     ("z.jpg", jpeg(0, 10), tp.NO_PIXELS),
                                     ("w.png", png(20000, 100), picture_size.TOO_WIDE),
                                     ("m.png", png(10000, 7000), tp.TOO_MANY_PIXELS)):
            with self.subTest(name=name), self.assertRaises(ValueError) as caught:
                picture_size.picture_size(self.write(name, data + b"\x00" * 32))
            self.assertEqual(str(caught.exception), sentence)

    def test_no_orientation_is_upright(self):
        png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 800, 600) + b"\x08\x06\x00\x00\x00"
        self.assertEqual(picture_size.orientation(self.write("a.png", png)), 1)
        self.assertEqual(picture_size.orientation(self.write("b.jpg", jpeg(320, 240))), 1)
        self.assertEqual(picture_size.orientation(self.write("c.jpg", jpeg(320, 240, exif(9)))), 1, "out of range")
        self.assertEqual(picture_size.orientation(Path(self.folder.name) / "missing.jpg"), 1)


if __name__ == "__main__":
    unittest.main()
