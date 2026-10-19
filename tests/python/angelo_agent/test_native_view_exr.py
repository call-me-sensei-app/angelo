# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``view_exr``: the bounded OpenEXR reader behind render-pass views (doc/angelo/view_sources.rst).

Files are written to the OpenEXR layout by ``view_source_fakes.exr_bytes``
(single- and multi-part, NONE, RLE, ZIPS and ZIP, HALF and FLOAT) and read
back exactly; damaged and unsupported files are refused with their reason.
``test_blender_view_sources`` reads files Blender itself wrote.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import view_source_fakes as fakes  # noqa: E402
from angelo_agent import view_exr  # noqa: E402

WIDTH, HEIGHT = 37, 21  # Not a multiple of a ZIP chunk's sixteen lines.


def ramp(offset=0.0):
    return (numpy.arange(WIDTH * HEIGHT, dtype=numpy.float32).reshape(HEIGHT, WIDTH) / 7.0 + offset)


class ExrCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "render.exr"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data):
        self.path.write_bytes(data)
        return self.path

    def parts(self):
        with open(self.path, "rb") as handle:
            return view_exr.read_parts(handle)


class ReadTests(ExrCase):
    def test_every_supported_compression_reads_back_exactly_in_both_layouts(self):
        depth, red = numpy.floor(ramp() / 9.0), ramp(0.5)  # Runs of equal depth, so RLE really shrinks chunks.
        channels = {"ViewLayer.Depth.Z": (depth, "FLOAT"), "ViewLayer.Combined.R": (red, "HALF")}
        stored = len(fakes.exr_bytes([("ViewLayer", channels)], WIDTH, HEIGHT, "NONE", False))
        for compression in ("NONE", "RLE", "ZIPS", "ZIP"):
            for multipart in (True, False):
                with self.subTest(compression=compression, multipart=multipart):
                    data = fakes.exr_bytes([("ViewLayer", channels)], WIDTH, HEIGHT, compression, multipart)
                    if compression != "NONE":
                        self.assertLess(len(data), stored, "the compressed chunks are decoded, not stored ones")
                    self.write(data)
                    parts, found_multipart = self.parts()
                    self.assertEqual((len(parts), found_multipart, parts[0].size), (1, multipart, (WIDTH, HEIGHT)))
                    values = view_exr.read_channels(self.path, 0, ["ViewLayer.Depth.Z", "ViewLayer.Combined.R"])
                    self.assertTrue(numpy.array_equal(values["ViewLayer.Depth.Z"], depth), "FLOAT exactly")
                    self.assertTrue(numpy.array_equal(values["ViewLayer.Combined.R"], red.astype(numpy.float16)),
                                    "HALF exactly")

    def test_a_multi_part_file_reads_only_the_part_asked_for(self):
        parts = [("ViewLayer.Depth", {"ViewLayer.Depth.Z": (ramp(), "FLOAT")}),
                 ("ViewLayer.Normal", {"ViewLayer.Normal.%s" % axis: (ramp(index), "FLOAT")
                                       for index, axis in enumerate("XYZ")})]
        self.write(fakes.exr_bytes(parts, WIDTH, HEIGHT, "ZIP"))
        found, multipart = self.parts()
        self.assertTrue(multipart)
        self.assertEqual([(part.name, [name for name, *_ in part.channels]) for part in found],
                         [("ViewLayer.Depth", ["ViewLayer.Depth.Z"]),
                          ("ViewLayer.Normal", ["ViewLayer.Normal.X", "ViewLayer.Normal.Y", "ViewLayer.Normal.Z"])])
        values = view_exr.read_channels(self.path, 1, ["ViewLayer.Normal.Y"])
        self.assertEqual(list(values), ["ViewLayer.Normal.Y"])
        self.assertTrue(numpy.array_equal(values["ViewLayer.Normal.Y"], ramp(1)))
        with self.assertRaisesRegex(view_exr.ExrError, "has no channel 'ViewLayer.Depth.Z'"):
            view_exr.read_channels(self.path, 1, ["ViewLayer.Depth.Z"])

    def test_the_writer_makes_a_file_the_reader_and_blender_read(self):
        rows = numpy.random.default_rng(3).random((5, 9, 4)).astype(numpy.float32)
        view_exr.write_rgba(self.path, rows)
        values = view_exr.read_channels(self.path, 0, ["R", "G", "B", "A"])
        self.assertTrue(numpy.array_equal(numpy.stack([values[name] for name in "RGBA"], axis=-1), rows))


class RefusalTests(ExrCase):
    def refused(self, data, pattern, names=("Z",)):
        self.write(data)
        with self.assertRaisesRegex(view_exr.ExrError, pattern):
            view_exr.read_channels(self.path, 0, list(names))

    def test_unsupported_files_are_refused_with_the_reason(self):
        plain = {"Z": (ramp(), "FLOAT")}
        self.refused(b"\x89PNG\r\n\x1a\n" + bytes(32), "not OpenEXR")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "PIZ", multipart=True), "compressed with PIZ")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "NONE", multipart=False, flags=0x200), "tiled")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "NONE", multipart=False, flags=0x800), "deep data")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "NONE", sampling=(2, 2)), "sub-sampled")

    def test_damaged_files_fail_closed(self):
        whole = fakes.exr_bytes([("", {"Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "ZIP", multipart=False)
        for cut in (6, 40, len(whole) - 30):
            with self.subTest(cut=cut):
                self.refused(whole[:cut], "OpenEXR|not OpenEXR")
        self.write(whole)
        part = self.parts()[0][0]
        damaged = bytearray(whole)
        position = whole.index(int(part.offsets[0]).to_bytes(8, "little"))
        damaged[position:position + 8] = (len(whole) + 99).to_bytes(8, "little")
        self.refused(bytes(damaged), "offset lies outside the file")

    def test_a_picture_too_large_to_read_is_refused_before_allocating(self):
        data = bytearray(fakes.exr_bytes([("", {"Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "NONE", multipart=True))
        window = (0).to_bytes(4, "little") * 2 + (8999).to_bytes(4, "little") * 2
        start = data.index(b"dataWindow\0box2i\0") + len(b"dataWindow\0box2i\0") + 4
        data[start:start + 16] = window
        self.refused(bytes(data), "9000x9000: more than the 36 million pixels")


if __name__ == "__main__":
    unittest.main()
