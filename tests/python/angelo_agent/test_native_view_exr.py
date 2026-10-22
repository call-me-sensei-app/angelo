# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``view_exr``: the bounded OpenEXR reader behind render-pass views (doc/angelo/view_sources.rst).

Files are written to the OpenEXR layout by ``view_source_fakes.exr_bytes``
(single- and multi-part, NONE, RLE, ZIPS and ZIP, HALF and FLOAT) and read
back exactly; damaged files are refused with their reason. Parts it does not
decode itself (PIZ, PXR24, B44, DWA, HTJ2K, tiled) go to the child decoder of
``view_exr_codecs``; here that child is a stand-in script (a good answer, a
crash, a hang, a damaged answer), and ``view_exr_worker`` runs against a
stand-in library, so the mechanics are tested without OpenImageIO. The real
library reads the files Blender wrote in ``test_blender_exr_codecs`` and
``test_blender_view_sources``.
"""

import json
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

import numpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import view_source_fakes as fakes  # noqa: E402
from angelo_agent import view_exr, view_exr_codecs, view_exr_worker  # noqa: E402

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


class NoDecoder(ExrCase):
    """A build without the child decoder: parts that need it are refused with the reason."""

    def setUp(self):
        super().setUp()
        self.saved = (view_exr_codecs.PYTHON, view_exr_codecs.WORKER)
        view_exr_codecs.WORKER = Path(self.temp.name) / "missing_worker.py"

    def tearDown(self):
        view_exr_codecs.PYTHON, view_exr_codecs.WORKER = self.saved
        super().tearDown()


class RefusalTests(NoDecoder):
    def refused(self, data, pattern, names=("Z",)):
        self.write(data)
        with self.assertRaisesRegex(view_exr.ExrError, pattern):
            view_exr.read_channels(self.path, 0, list(names))

    def test_unsupported_files_are_refused_with_the_reason(self):
        plain = {"Z": (ramp(), "FLOAT")}
        self.refused(b"\x89PNG\r\n\x1a\n" + bytes(32), "not OpenEXR")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "PIZ", multipart=True),
                     "compressed with PIZ.*no decoder")
        self.refused(fakes.exr_bytes([("", plain)], WIDTH, HEIGHT, "NONE", multipart=False, flags=0x200),
                     "file is tiled.*no decoder")
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

    def test_the_size_limit_applies_before_any_decoder_runs(self):
        data = bytearray(fakes.exr_bytes([("", {"Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "PIZ", multipart=True))
        window = (0).to_bytes(4, "little") * 2 + (8999).to_bytes(4, "little") * 2
        start = data.index(b"dataWindow\0box2i\0") + len(b"dataWindow\0box2i\0") + 4
        data[start:start + 16] = window
        marker = Path(self.temp.name) / "ran"
        view_exr_codecs.WORKER = Path(self.temp.name) / "worker.py"
        view_exr_codecs.WORKER.write_text("open(%r, 'w').close()\n" % str(marker))
        view_exr_codecs.PYTHON = sys.executable
        self.refused(bytes(data), "9000x9000: more than the 36 million pixels")
        self.assertFalse(marker.exists(), "the library's process was not started for a picture over the limit")


WORKER = textwrap.dedent("""
    import json, os, sys, time
    import numpy
    mode = %(mode)r
    path, part, names, limit = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3]), int(sys.argv[4])
    W, H = %(width)d, %(height)d
    if mode == "crash":
        os._exit(139)
    if mode == "hang":
        time.sleep(60)
    if mode == "error":
        sys.stdout.write(json.dumps({"ok": False, "error": "open", "detail": "/private/path/leak.exr"}) + "\\n")
        sys.exit(0)
    if mode == "environment":
        sys.stdout.write(json.dumps({"ok": False, "error": "read", "environment": sorted(os.environ)}) + "\\n")
        sys.exit(0)
    if mode == "garbage":
        sys.stdout.buffer.write(b"\\x00\\xffnot json\\n")
        sys.exit(0)
    if mode == "no_newline":
        sys.stdout.buffer.write(b'{"ok": true}')
        sys.exit(0)
    width, height = (W + 1, H) if mode == "wrong_size" else (W, H)
    sys.stdout.write(json.dumps({"ok": True, "width": width, "height": height}) + "\\n")
    sys.stdout.flush()
    planes = [numpy.full((H, W), 10.0 * part + index, dtype="<f4") for index in range(len(names))]
    if mode == "short":
        planes = planes[:-1]
    for plane in planes:
        sys.stdout.buffer.write(plane.tobytes())
""")


class CodecTests(ExrCase):
    """Parts the reader does not decode go to the child decoder; whatever it does, the application goes on."""

    def setUp(self):
        super().setUp()
        self.saved = (view_exr_codecs.PYTHON, view_exr_codecs.WORKER, view_exr_codecs.TIMEOUT)
        view_exr_codecs.PYTHON = sys.executable

    def tearDown(self):
        view_exr_codecs.PYTHON, view_exr_codecs.WORKER, view_exr_codecs.TIMEOUT = self.saved
        super().tearDown()

    def worker(self, mode):
        script = Path(self.temp.name) / "worker.py"
        script.write_text(WORKER % {"mode": mode, "width": WIDTH, "height": HEIGHT})
        view_exr_codecs.WORKER = script

    def pizzed(self, **options):
        parts = [("ViewLayer.Depth", {"ViewLayer.Depth.Z": (ramp(), "FLOAT")}),
                 ("ViewLayer.Normal", {"ViewLayer.Normal.%s" % axis: (ramp(index), "FLOAT") for index, axis in enumerate("XYZ")})]
        return self.write(fakes.exr_bytes(parts, WIDTH, HEIGHT, options.pop("compression", "PIZ"), **options))

    def test_every_compression_but_the_four_goes_to_the_child_and_headers_still_come_from_the_file(self):
        for compression in ("PIZ", "PXR24", "B44", "B44A", "DWAA", "DWAB", "HTJ2K256", "HTJ2K32"):
            with self.subTest(compression=compression):
                self.pizzed(compression=compression)
                parts, multipart = self.parts()
                self.assertTrue(multipart)
                self.assertEqual([part.native for part in parts], [False, False])
                self.assertEqual([part.name for part in parts], ["ViewLayer.Depth", "ViewLayer.Normal"])
                self.assertEqual([name for name, *_ in parts[1].channels],
                                 ["ViewLayer.Normal.X", "ViewLayer.Normal.Y", "ViewLayer.Normal.Z"])
                self.assertEqual(parts[0].offsets, [], "no offset table is read for a part the child decodes")
        for compression in ("NONE", "RLE", "ZIPS", "ZIP"):
            self.pizzed(compression=compression)
            self.assertEqual([part.native for part in self.parts()[0]], [True, True], compression)

    def test_a_part_the_library_reads_does_not_hide_the_parts_after_it(self):
        """Offset tables follow one another: a non-native part's table is passed, not skipped (a review's defect 1)."""
        parts = [("A", {"A.Z": (ramp(1), "FLOAT")}), ("B", {"B.Z": (ramp(2), "FLOAT")}),
                 ("C", {"C.Z": (ramp(3), "FLOAT")}), ("D", {"D.Z": (ramp(4), "FLOAT")})]
        for compressions in (("PIZ", "ZIP", "ZIP", "ZIP"), ("ZIP", "PIZ", "ZIP", "ZIP"), ("ZIP", "ZIP", "ZIP", "DWAA"),
                             ("DWAA", "PIZ", "ZIP", "RLE"), ("PXR24", "PIZ", "B44", "ZIP")):
            with self.subTest(compressions=compressions):
                self.write(fakes.exr_bytes(parts, WIDTH, HEIGHT, compressions))
                found, _multipart = self.parts()
                self.assertEqual([part.native for part in found], [code in DECODED_HERE for code in compressions])
                for index, native in enumerate(part.native for part in found):
                    if native:
                        name = "%s.Z" % "ABCD"[index]
                        values = view_exr.read_channels(self.path, index, [name])
                        self.assertTrue(numpy.array_equal(values[name], ramp(index + 1)), (compressions, index))

    def test_when_a_table_cannot_be_passed_every_later_part_goes_to_the_library(self):
        parts = [("A", {"A.Z": (ramp(1), "FLOAT")}), ("B", {"B.Z": (ramp(2), "FLOAT")})]
        data = fakes.exr_bytes(parts, WIDTH, HEIGHT, ("PIZ", "ZIP"))
        # The first part names no chunkCount, so the length of its offset table is unknown.
        data = data.replace(b"chunkCount\0int\0", b"chunkCoun2\0int\0", 1)
        self.write(data)
        found, _multipart = self.parts()
        self.assertEqual([part.native for part in found], [False, False])

    def test_an_attribute_of_the_wrong_kind_is_no_attribute_and_the_file_is_refused_or_read_with_a_reason(self):
        from angelo_agent import view_passes
        plain = fakes.exr_bytes([("", {"Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "ZIP", multipart=False)
        multi = fakes.exr_bytes([("P", {"P.Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "ZIP")
        cases = {"channels as a string": plain.replace(b"channels\0chlist\0", b"channels\0string\0", 1),
                 "dataWindow as a float": plain.replace(b"dataWindow\0box2i\0", b"dataWindow\0float\0", 1),
                 "compression as a string": plain.replace(b"compression\0compression\0", b"compression\0stringxxxxxx\0", 1),
                 "chunkCount as a string": multi.replace(b"chunkCount\0int\0", b"chunkCount\0str\0", 1),
                 "type as an int": multi.replace(b"type\0string\0", b"type\0int\0\0\0", 1),
                 "name as an int": multi.replace(b"name\0string\0", b"name\0int\0\0\0", 1)}
        for label, data in cases.items():
            with self.subTest(label):
                self.write(data)
                try:
                    view_exr.read_channels(self.path, 0, ["Z"] if "P" not in label and "chunk" not in label else ["P.Z"])
                    view_passes.catalogue(str(self.path))
                except (view_exr.ExrError, RuntimeError):
                    pass   # A refusal with a reason; any other exception fails the subtest.

    def test_a_tiled_single_part_file_is_listed_and_handed_to_the_child(self):
        self.write(fakes.exr_bytes([("", {"Z": (ramp(), "FLOAT")})], WIDTH, HEIGHT, "ZIP", multipart=False, flags=0x200))
        part = self.parts()[0][0]
        self.assertEqual((part.kind, part.native), ("tiledimage", False))

    def test_the_childs_planes_come_back_by_name_for_the_part_asked_for(self):
        self.pizzed()
        self.worker("ok")
        values = view_exr.read_channels(self.path, 1, ["ViewLayer.Normal.Z", "ViewLayer.Normal.X"])
        self.assertEqual(list(values), ["ViewLayer.Normal.Z", "ViewLayer.Normal.X"])
        self.assertEqual((values["ViewLayer.Normal.Z"].shape, values["ViewLayer.Normal.Z"].dtype.name),
                         ((HEIGHT, WIDTH), "float32"))
        self.assertTrue((values["ViewLayer.Normal.Z"] == 10.0).all() and (values["ViewLayer.Normal.X"] == 11.0).all(),
                        "the stand-in labels plane i of part p as 10 p + i, in the order asked")

    def test_a_missing_channel_is_named_before_the_child_starts(self):
        self.pizzed()
        self.worker("crash")
        with self.assertRaisesRegex(view_exr.ExrError, "has no channel 'ViewLayer.Depth.Z'"):
            view_exr.read_channels(self.path, 1, ["ViewLayer.Depth.Z"])

    def test_nothing_the_child_does_escapes_as_anything_but_a_refusal(self):
        self.pizzed()
        view_exr_codecs.TIMEOUT = 1
        cases = {"crash": "decoder stopped while reading it", "hang": "took over 1 s", "garbage": "no usable answer",
                 "no_newline": "no usable answer", "error": "could not be read: the library could not open the file",
                 "wrong_size": "wrong size", "short": "wrong size"}
        for mode, text in cases.items():
            with self.subTest(mode=mode):
                self.worker(mode)
                with self.assertRaises(view_exr.ExrError) as caught:
                    view_exr.read_channels(self.path, 0, ["ViewLayer.Depth.Z"] if mode != "short" else ["ViewLayer.Depth.Z"])
                self.assertIn(text, str(caught.exception))
                self.assertNotIn("private", str(caught.exception), "the library's own text, which can hold paths, is not passed on")
        self.worker("short")
        with self.assertRaisesRegex(view_exr.ExrError, "wrong size"):
            view_exr.read_channels(self.path, 1, ["ViewLayer.Normal.X", "ViewLayer.Normal.Y"])

    def test_more_channels_than_the_limit_are_refused_before_the_child_starts(self):
        many = {"C%03d" % index: (ramp(), "HALF") for index in range(view_exr_codecs.MAX_NAMES + 1)}
        self.write(fakes.exr_bytes([("", many)], WIDTH, HEIGHT, "PIZ", multipart=True))
        self.worker("crash")
        with self.assertRaisesRegex(view_exr.ExrError, "at most 64 channels.*65 were asked for"):
            view_exr.read_channels(self.path, 0, sorted(many))

    def test_a_child_that_cannot_start_is_a_refusal(self):
        self.pizzed()
        self.worker("ok")
        view_exr_codecs.PYTHON = str(Path(self.temp.name) / "no-such-python")
        with self.assertRaisesRegex(view_exr.ExrError, "decoder could not be started"):
            view_exr.read_channels(self.path, 0, ["ViewLayer.Depth.Z"])

    def test_the_child_gets_a_short_environment_never_the_applications(self):
        import os
        self.pizzed()
        script = Path(self.temp.name) / "environment.py"
        out = Path(self.temp.name) / "environment.json"
        script.write_text(textwrap.dedent("""
            import json, os, sys
            open(%r, "w").write(json.dumps(sorted(os.environ)))
            sys.stdout.write(json.dumps({"ok": False, "error": "read"}) + "\\n")
        """ % str(out)))
        view_exr_codecs.WORKER = script
        secrets = {"ANGELO_TEST_PROVIDER_KEY": "sk-not-for-the-child", "ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y",
                   "GITHUB_TOKEN": "z", "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib", "LD_PRELOAD": "/tmp/evil.so"}
        saved = {name: os.environ.get(name) for name in secrets}
        os.environ.update(secrets)
        try:
            with self.assertRaises(view_exr.ExrError):
                view_exr.read_channels(self.path, 0, ["ViewLayer.Depth.Z"])
        finally:
            for name, value in saved.items():
                if value is None:
                    del os.environ[name]
                else:
                    os.environ[name] = value
        passed = json.loads(out.read_text())
        self.assertEqual(sorted(set(passed) & set(secrets)), [], "nothing the application holds reaches the child")
        self.assertLess(len(passed), 12, passed)

    def test_the_applications_peak_is_about_the_size_of_the_answer_not_several_times_it(self):
        """A review measured 4.6 times (pipe contents, a partition, a view, a copy); one buffer holds it once."""
        import tracemalloc
        width = height = 900
        data = fakes.exr_bytes([("", {name: (numpy.zeros((height, width), numpy.float32), "FLOAT") for name in "RGB"})],
                               width, height, "PIZ", multipart=True)
        self.write(data)
        self.worker("ok")
        script = Path(self.temp.name) / "worker.py"
        script.write_text(WORKER % {"mode": "ok", "width": width, "height": height})
        view_exr_codecs.WORKER = script
        output = width * height * 4 * 3
        tracemalloc.start()
        try:
            values = view_exr.read_channels(self.path, 0, ["R", "G", "B"])
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(values["R"].shape, (height, width))
        self.assertLess(peak, output * 1.5, "peak %.1f MB against %.1f MB of channels" % (peak / 1e6, output / 1e6))
        self.assertIs(values["R"].base, values["B"].base, "the planes are views of one buffer")

    def test_a_block_of_scanlines_wider_than_the_library_may_hold_is_refused_before_the_child_starts(self):
        wide = bytearray(fakes.exr_bytes([("", {name: (ramp(), "FLOAT") for name in "RGBAXYZW"})], WIDTH, HEIGHT, "DWAB",
                                         multipart=True))
        window = (0).to_bytes(4, "little") * 2 + (3_999_999).to_bytes(4, "little") + (0).to_bytes(4, "little")
        start = wide.index(b"dataWindow\0box2i\0") + len(b"dataWindow\0box2i\0") + 4
        wide[start:start + 16] = window   # 4 million wide, one row high: the 256-line block of eight channels is 32 GB.
        self.write(bytes(wide))
        marker = Path(self.temp.name) / "ran"
        view_exr_codecs.WORKER = Path(self.temp.name) / "worker.py"
        view_exr_codecs.WORKER.write_text("open(%r, 'w').close()\n" % str(marker))
        with self.assertRaisesRegex(view_exr.ExrError, "one block of its scanlines is .* MB wide"):
            view_exr.read_channels(self.path, 0, list("RGBAXYZW")[:4])
        self.assertFalse(marker.exists())

    def test_the_child_gets_the_path_and_names_as_plain_arguments(self):
        awkward = Path(self.temp.name) / "a folder; $(touch x) 'q' ü 日本"
        awkward.mkdir()
        self.path = awkward / "a b.exr"
        self.pizzed()
        script = Path(self.temp.name) / "echo.py"
        script.write_text(textwrap.dedent("""
            import json, sys
            sys.stdout.write(json.dumps({"ok": False, "error": "read", "argv": sys.argv[1:]}) + "\\n")
        """))
        view_exr_codecs.WORKER = script
        with self.assertRaises(view_exr.ExrError):
            view_exr.read_channels(self.path, 0, ["ViewLayer.Depth.Z"])
        self.assertFalse((Path(self.temp.name) / "x").exists())


FIXTURES = Path(__file__).resolve().parent / "exr_fixtures"
DECODED_HERE = frozenset({"NONE", "RLE", "ZIPS", "ZIP"})


class BlenderFileTests(unittest.TestCase):
    """The headers of files Blender 5.2 wrote (``test_blender_exr_codecs --write-fixtures``), through ``read_parts``.

    No library decodes them here (the host suite is dependency-free): that is ``test_blender_exr_codecs``, in the app.
    """

    def parts_of(self, name):
        with open(FIXTURES / name, "rb") as handle:
            return view_exr.read_parts(handle)

    def test_every_codec_blender_writes_is_listed_and_routed_to_the_right_decoder(self):
        seen = set()
        for path in sorted(FIXTURES.glob("plain*.exrbin")):
            with self.subTest(path.name):
                codec = path.stem.split("_", 1)[1]
                parts, multipart = self.parts_of(path.name)
                self.assertEqual((len(parts), multipart, parts[0].size, parts[0].kind), (1, False, (37, 21), "scanlineimage"))
                self.assertEqual([name for name, *_ in parts[0].channels], ["A", "B", "G", "R"])
                named = view_exr.compression_name(parts[0])
                self.assertTrue(named.startswith("HTJ2K") if codec == "HTJ2K" else named == codec, named)  # HTJ2K32 or 256.
                self.assertEqual(parts[0].native, codec in DECODED_HERE)
                seen.add(codec)
        self.assertEqual(seen, {"NONE", "RLE", "ZIPS", "ZIP", "PIZ", "PXR24", "B44", "B44A", "DWAA", "DWAB", "HTJ2K"})

    def test_a_multilayer_render_lists_its_passes_whatever_its_codec(self):
        files = sorted(FIXTURES.glob("layers*.exrbin"))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            with self.subTest(path.name):
                parts, multipart = self.parts_of(path.name)
                self.assertTrue(multipart)
                self.assertEqual([part.name for part in parts][:2], ["ViewLayer.Combined", "ViewLayer.Depth"])
                self.assertEqual([name for name, *_ in parts[1].channels], ["ViewLayer.Depth.Z"])
                self.assertTrue(any(".Crypto" in part.name for part in parts), "the Cryptomatte parts are listed")
                self.assertEqual({part.native for part in parts}, {path.stem.endswith("_ZIP")})

    def test_the_tiled_file_is_listed_as_tiled_and_left_to_the_library(self):
        parts, multipart = self.parts_of("tiled32_ZIP.exrbin")
        self.assertEqual((parts[0].kind, parts[0].native, multipart), ("tiledimage", False, False))

    def test_the_fast_path_reads_the_files_blender_wrote_with_its_own_decoder(self):
        expected = numpy.arange(WIDTH * HEIGHT, dtype=numpy.float32).reshape(HEIGHT, WIDTH) / 8.0
        for name in ("plain16_NONE", "plain16_RLE", "plain16_ZIPS", "plain16_ZIP", "plain32_NONE"):
            with self.subTest(name):
                values = view_exr.read_channels(FIXTURES / (name + ".exrbin"), 0, ["R", "G", "B", "A"])
                self.assertTrue(numpy.array_equal(values["R"], expected))
                self.assertTrue(numpy.array_equal(values["G"], expected + 1.0))
                self.assertTrue(numpy.array_equal(values["B"], expected * 0.5))
                self.assertTrue((values["A"] == 1.0).all())
        values = view_exr.read_channels(FIXTURES / "layers32_ZIP.exrbin", 1, ["ViewLayer.Depth.Z"])
        self.assertEqual(values["ViewLayer.Depth.Z"].shape, (HEIGHT, WIDTH))


class FakeReader:
    """A stand-in OpenImageIO reader: channel ``begin + i`` of part ``p`` holds ``100 p + channel`` (an integer
    channel, read as integers, holds the same number as an unsigned integer)."""

    def __init__(self, names, width=4, height=3, deep=False, subimages=2, formats=None):
        self.names, self.width, self.height, self.deep, self.subimages = names, width, height, deep, subimages
        self.formats, self.part, self.closed, self.calls = formats or ["float"] * len(names), None, False, []

    def seek_subimage(self, index, miplevel):
        self.part = index
        return 0 <= index < self.subimages

    def spec(self):
        return types.SimpleNamespace(width=self.width, height=self.height, deep=self.deep, channelnames=tuple(self.names),
                                     channelformats=tuple(self.formats), format="float")

    def read_image(self, subimage, miplevel, begin, end, kind):
        import numpy
        self.calls.append((begin, end, kind))
        planes = [numpy.full((self.height, self.width), 100.0 * self.part + channel) for channel in range(begin, end)]
        values = numpy.stack(planes, axis=-1)
        return values.astype(numpy.uint32 if kind == "uint" else numpy.float32)

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    """``view_exr_worker`` against a stand-in OpenImageIO: its answers, its limits, its codes."""

    def run_worker(self, reader, names, part=0, limit=1000):
        calls = []
        library = types.SimpleNamespace(attribute=lambda *args: calls.append(args),
                                        ImageInput=types.SimpleNamespace(open=lambda path: reader))
        sys.modules["OpenImageIO"] = library
        try:
            return view_exr_worker.decode("file.exr", part, names, limit), calls
        finally:
            del sys.modules["OpenImageIO"]

    def test_the_planes_come_back_in_the_order_asked_and_the_file_is_closed(self):
        reader = FakeReader(["A", "B", "G", "R"])
        result, calls = self.run_worker(reader, ["R", "B"], part=1)
        self.assertEqual((result["ok"], result["width"], result["height"]), (True, 4, 3))
        self.assertEqual([float(plane[0, 0]) for plane in result["planes"]], [103.0, 101.0])
        self.assertTrue(reader.closed)
        self.assertIn(("limits:imagesize_MB", 1024), calls, "the library's own size limit is lowered")

    def test_neighbouring_channels_are_one_read_and_an_integer_channel_is_read_as_integers(self):
        reader = FakeReader(["A", "B", "G", "ID", "R"], formats=["float", "float", "float", "uint", "float"])
        result, _ = self.run_worker(reader, ["B", "ID", "A", "G", "R"])
        self.assertEqual(reader.calls, [(3, 4, "uint"), (0, 3, "float"), (4, 5, "float")],
                         "channels 0 to 2 are one read; the integer channel and the one past it are their own")
        self.assertEqual([float(plane[0, 0]) for plane in result["planes"]], [1.0, 3.0, 0.0, 2.0, 4.0])
        self.assertEqual(result["planes"][1].dtype.str, "<f4", "an integer channel is returned as float32, as the own decoder does")

    def test_every_failure_is_a_code_never_the_librarys_text(self):
        names = ["R"]
        cases = [(None, "open"), (FakeReader(["R"], subimages=1), "subimage"), (FakeReader(["R"], deep=True), "deep"),
                 (FakeReader(["R"], width=100, height=100), "size"), (FakeReader(["G"]), "channel"),
                 (FakeReader(["R"], width=0), "size")]
        for reader, code in cases:
            with self.subTest(code=code):
                result, _ = self.run_worker(reader, names, part=1 if code == "subimage" else 0, limit=1000)
                self.assertEqual(result, {"ok": False, "error": code})

    def test_a_library_fault_on_a_damaged_file_is_a_refusal(self):
        class Broken(FakeReader):
            def read_image(self, *args):
                raise RuntimeError("EXR Error (/private/tmp/secret.exr): corrupt")
        reader = Broken(["R"])
        result, _ = self.run_worker(reader, ["R"])
        self.assertEqual(result, {"ok": False, "error": "read"})
        self.assertTrue(reader.closed)

    def test_the_command_line_is_checked(self):
        import io
        for argv in (["w"], ["w", "p", "x", "[]", "1"], ["w", "p", "0", "{}", "1"], ["w", "p", "0", "[1]", "1"]):
            with self.subTest(argv=argv):
                out = io.BytesIO()
                real = sys.stdout
                sys.stdout = types.SimpleNamespace(buffer=out, flush=lambda: None)
                try:
                    self.assertEqual(view_exr_worker.main(argv), 0)
                finally:
                    sys.stdout = real
                self.assertEqual(json.loads(out.getvalue().splitlines()[0]), {"ok": False, "error": "read"})



if __name__ == "__main__":
    unittest.main()
