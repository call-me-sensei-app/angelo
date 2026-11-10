# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake every map at once without Blender: the request, the packing presets, finishing the sums and the file writers.

The writing queue and the memory the units really use are
``test_native_bake_maps_memory.py``; the bakes themselves, their numbers against
references and the job in the running app are
``test_blender_bake_maps_correctness.py`` and ``test_blender_bake_maps_job.py``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import struct
import sys
import unittest
import zlib
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import bake_maps_encode as encode  # noqa: E402
from angelo_agent import bake_maps_pack as pack  # noqa: E402
from angelo_agent import bake_maps_spec as spec  # noqa: E402
from angelo_agent.job_spec import JOB_TOOLS, JobRequestError, fields_by_kind, validate_job  # noqa: E402

A, B, C = ("scn:" + letter * 32 + ":1" for letter in "abc")


def start(person=True, **fields):
    return validate_job("angelo_job_start", dict(kind="BAKE_MAPS", **fields), person=person)


def read_png(data):
    """(width, height, depth, colour type, rows as uint8 bytes) of a PNG written with filter 0 or 2."""
    assert data[:8] == encode.PNG_SIGNATURE
    offset, chunks = 8, {}
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        assert struct.unpack(">I", data[offset + 8 + length:offset + 12 + length])[0] == zlib.crc32(kind + body)
        chunks.setdefault(kind, b"")
        chunks[kind] += body
        offset += 12 + length
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    raw = numpy.frombuffer(zlib.decompress(chunks[b"IDAT"]), dtype=numpy.uint8).reshape(height, -1)
    rows = raw[:, 1:].copy()
    for index in range(height):
        if raw[index, 0] == 2:
            rows[index] = rows[index] + rows[index - 1]
    return width, height, depth, colour, rows, chunks


class RequestTests(unittest.TestCase):
    def test_target_or_targets_and_defaults(self):
        request = start(target=A, output="maps")
        self.assertEqual(request["targets"], [A])
        self.assertNotIn("target", request)
        self.assertEqual((request["packing"], request["format"], request["margin"], request["resolution"]),
                         ("SEPARATE", "PNG", spec.DEFAULT_MARGIN, spec.DEFAULT_RESOLUTION))
        self.assertEqual(start(targets=[A, B], output="m")["targets"], [A, B])

    def test_refusals_name_the_fix(self):
        cases = [(dict(output="m"), "target"), (dict(target=A, targets=[B], output="m"), "not both"),
                 (dict(target=A), "output"), (dict(targets=[A, A], output="m"), "once"),
                 (dict(targets=[A, B], sources=[C], output="m"), "exactly one target"),
                 (dict(target=A, sources=[A], output="m"), "other objects"),
                 (dict(target=A, output="m", maps=["GLOSS"]), "BASE_COLOR"),
                 (dict(target=A, output="m", depth=32), "PNG files are 8 or 16"),
                 (dict(target=A, output="m", format="JPEG"), "PNG or OPEN_EXR"),
                 (dict(target=A, output="m", margin=999), "margin"),
                 (dict(target=A, output="m", bake_type="AO"), "does not take")]
        for fields, message in cases:
            with self.subTest(fields=fields), self.assertRaisesRegex(JobRequestError, message):
                start(**fields)

    def test_maps_keep_channel_order_and_high_to_low_fields(self):
        request = start(target=A, output="m", maps=["NORMAL", "BASE_COLOR"], sources=[B], cage=C, extrusion=0.1)
        self.assertEqual(request["maps"], ["BASE_COLOR", "NORMAL"])
        self.assertEqual((request["sources"], request["cage"], request["extrusion"]), ([B], C, 0.1))

    def test_declaration_states_the_kind_and_its_fields(self):
        schema = JOB_TOOLS[0]["inputSchema"]
        self.assertIn("BAKE_MAPS", schema["properties"]["kind"]["enum"])
        for name in ("packing", "material"):
            self.assertIn(name, schema["properties"])
        for name in spec.CARD_FIELDS:   # The card's own: accepted from the person, never offered to an agent.
            self.assertNotIn(name, schema["properties"])
            with self.assertRaisesRegex(JobRequestError, "does not take"):
                start(person=False, target=A, output="m", **{name: [B] if name == "targets" else 8})
        self.assertIn("BAKE_MAPS: output* resolution samples format target* sources", fields_by_kind())
        self.assertIn("BAKE_MAPS", JOB_TOOLS[0]["description"])


class PackingTests(unittest.TestCase):
    def names(self, packing, channels):
        return [name for name, _slots, _colour in spec.files(packing, channels)]

    def test_presets(self):
        everything = spec.CHANNELS
        self.assertEqual(self.names("SEPARATE", everything), ["BaseColor", "Metallic", "Roughness", "Normal",
                                                              "Emission", "Alpha", "AO", "Height"])
        self.assertEqual(self.names("ORM", ["BASE_COLOR", "ROUGHNESS"]), ["BaseColor", "ORM"])
        self.assertEqual(self.names("GLTF", ["BASE_COLOR", "NORMAL"]),
                         ["BaseColor", "OcclusionRoughnessMetallic", "Normal"])
        self.assertEqual(self.names("UNITY", ["BASE_COLOR"]), ["BaseMap", "MaskMap"])
        unreal = dict((name, slots) for name, slots, _colour in spec.files("UNREAL", ["NORMAL"]))
        self.assertEqual(unreal["Normal"][1], ("NORMAL", 1, "flip"))   # DirectX green.
        gltf = dict((name, slots) for name, slots, _colour in spec.files("GLTF", ["NORMAL", "ALPHA"]))
        self.assertEqual(gltf["Normal"][1], ("NORMAL", 1, ""))
        self.assertEqual(len(gltf["BaseColor"]), 4)
        self.assertEqual(dict((n, s) for n, s, _c in spec.files("UNITY", []))["MaskMap"][3], ("ROUGHNESS", 0, "invert"))

    def test_depths_and_names(self):
        self.assertEqual(spec.depth_of("Normal", {"format": "PNG"}), 16)
        self.assertEqual(spec.depth_of("BaseColor", {"format": "PNG"}), 8)
        self.assertEqual(spec.depth_of("BaseColor", {"format": "OPEN_EXR"}), 16)
        self.assertEqual(spec.depth_of("BaseColor", {"format": "PNG", "depth": 16}), 16)
        self.assertEqual(spec.file_name(spec.safe_stem("Crate/01 "), "Normal", ".png", 1002), "Crate_01_Normal.1002.png")

    def test_objects_whose_names_clean_or_fold_alike_never_share_a_file(self):
        """ "Hull A", "Hull_A" and "hull_a" clean to one stem, and case-folding file systems (macOS, Windows) treat
        "Hull_A" and "hull_a" as one file: each object gets its own stem, and the set's files are all distinct."""
        names = ["Hull A", "Hull_A", "hull_a", "Hull A.001", "Crate"]
        found = spec.stems(names)
        self.assertEqual(len({stem.lower() for stem in found}), len(names), found)
        self.assertEqual(found[0], "Hull_A")
        written = spec.set_files("ORM", ".png", [(stem, ["BASE_COLOR", "NORMAL"], [1001, 1002]) for stem in found])
        paths = [entry[3].lower() for entry in written]
        self.assertEqual(len(paths), len(set(paths)))
        with self.assertRaisesRegex(spec.BakeMapsError, "rename one"):
            spec.set_files("ORM", ".png", [("Hull_A", ["BASE_COLOR"], []), ("hull_a", ["BASE_COLOR"], [])])


class FinishTests(unittest.TestCase):
    shape = (4, 4)

    def test_weight_division_alpha_and_defaults(self):
        weight = numpy.zeros((4, 4, 1), numpy.float32)
        weight[:2] = 0.5
        base = numpy.full((4, 4, 3), 0.2, numpy.float32) * weight
        channels, _facts = pack.finish({"BASE_COLOR": base, "WEIGHT": weight,
                                        "TRANSPARENCY": numpy.full((4, 4, 1), 0.25, numpy.float32)}, self.shape, "PNG")
        numpy.testing.assert_allclose(channels["BASE_COLOR"][:2], 0.2, atol=1e-6)
        numpy.testing.assert_allclose(channels["BASE_COLOR"][2:], spec.DEFAULTS["BASE_COLOR"], atol=1e-6)
        numpy.testing.assert_allclose(channels["ALPHA"], 0.75)
        constant, _facts = pack.finish({"ROUGHNESS": (0.4, 0.4, 0.4), "WEIGHT": (0.5, 0.5, 0.5),
                                        "TRANSPARENCY": (0.0, 0.0, 0.0)}, self.shape, "PNG")
        self.assertEqual(constant["ROUGHNESS"], (0.8, 0.8, 0.8))
        self.assertEqual(constant["ALPHA"], (1.0, 1.0, 1.0))

    def test_hdr_emission_and_height_ranges(self):
        emission = numpy.zeros((4, 4, 3), numpy.float32)
        emission[0, 0] = (4.0, 2.0, 0.0)
        channels, facts = pack.finish({"EMISSION": emission}, self.shape, "PNG")
        self.assertEqual(facts["emission_strength"], 4.0)
        numpy.testing.assert_allclose(channels["EMISSION"][0, 0], (1.0, 0.5, 0.0))
        _channels, exr = pack.finish({"EMISSION": emission}, self.shape, "OPEN_EXR")
        self.assertNotIn("emission_strength", exr)
        height = numpy.linspace(-0.2, 0.6, 16, dtype=numpy.float32).reshape(4, 4, 1)
        channels, facts = pack.finish({"HEIGHT": height}, self.shape, "PNG")
        rebuilt = (channels["HEIGHT"] - facts["height"]["midlevel"]) * facts["height"]["scale"]
        numpy.testing.assert_allclose(rebuilt, height, atol=1e-5)

    def test_a_constant_height_keeps_its_offset(self):
        """A constant or flat displacement height (positive or negative) is rebuilt as itself, never as zero."""
        for value in (0.25, -0.3, 0.0):
            for image_format in ("PNG", "OPEN_EXR"):
                for height in ((value,) * 3, numpy.full((4, 4, 1), value, numpy.float32)):
                    with self.subTest(value=value, format=image_format, array=not isinstance(height, tuple)):
                        channels, facts = pack.finish({"HEIGHT": height}, self.shape, image_format)
                        stored = channels["HEIGHT"]
                        stored = stored[0] if isinstance(stored, tuple) else float(stored.ravel()[0])
                        settings = facts["height"]
                        self.assertAlmostEqual((stored - settings["midlevel"]) * settings["scale"], value, places=5)

    def test_compose_transforms_and_constants(self):
        channels = {"ROUGHNESS": numpy.full((4, 4, 1), 0.3, numpy.float32), "METALLIC": (1.0, 1.0, 1.0)}
        pixels = pack.compose(channels, spec.UNITY_MASK, self.shape)
        numpy.testing.assert_allclose(pixels[0, 0], (1.0, spec.DEFAULTS["AO"], 0.0, 0.7), atol=1e-6)


class EncodeTests(unittest.TestCase):
    def setUp(self):
        self.pixels = numpy.random.default_rng(3).random((5, 7, 4), dtype=numpy.float32)

    def test_png_depths_channels_and_orientation(self):
        for channels, depth in ((1, 8), (3, 8), (4, 16), (3, 16)):
            with self.subTest(channels=channels, depth=depth):
                data = encode.png_bytes(self.pixels[..., :channels].copy(), depth)
                width, height, got_depth, colour, rows, _chunks = read_png(data)
                self.assertEqual((width, height, got_depth, colour), (7, 5, depth, encode.PNG_COLOUR_TYPES[channels]))
                values = rows.view(">u2") if depth == 16 else rows
                values = values.reshape(5, 7, channels).astype(numpy.float64) / (65535 if depth == 16 else 255)
                numpy.testing.assert_allclose(values[::-1], self.pixels[..., :channels], atol=0.5 / 255 + 1e-6)

    def test_png_colour_is_srgb_but_alpha_is_not(self):
        data = encode.png_bytes(self.pixels.copy(), 8, colour=True)
        _w, _h, _d, _c, rows, chunks = read_png(data)
        self.assertIn(b"sRGB", chunks)
        values = rows.reshape(5, 7, 4)[::-1].astype(numpy.float64) / 255
        numpy.testing.assert_allclose(values[..., :3], encode.srgb_encode(self.pixels[..., :3]), atol=0.5 / 255 + 1e-6)
        numpy.testing.assert_allclose(values[..., 3], self.pixels[..., 3], atol=0.5 / 255 + 1e-6)

    def test_exr_zip_blocks_decode_to_the_pixels(self):
        for depth, dtype in ((16, "<f2"), (32, "<f4")):
            with self.subTest(depth=depth):
                tall = numpy.random.default_rng(4).random((37, 9, 3), dtype=numpy.float32)
                data = encode.exr_bytes(tall, depth)
                self.assertEqual(struct.unpack("<i", data[:4])[0], encode.EXR_MAGIC)
                header_end = _exr_header_end(data)
                blocks = (37 + 15) // 16
                offsets = struct.unpack("<%dQ" % blocks, data[header_end:header_end + 8 * blocks])
                rows = []
                for offset in offsets:
                    start, size = struct.unpack("<ii", data[offset:offset + 8])
                    body = data[offset + 8:offset + 8 + size]
                    lines = min(16, 37 - start)
                    expected = lines * 3 * 9 * (2 if depth == 16 else 4)
                    raw = body if size == expected else _unzip(body)
                    rows.append(numpy.frombuffer(raw, dtype=dtype).reshape(lines, 3, 9))
                planar = numpy.concatenate(rows)   # (h, channels B G R, w), top row first.
                image = numpy.stack([planar[:, 2], planar[:, 1], planar[:, 0]], axis=-1)[::-1]
                numpy.testing.assert_allclose(image, tall, atol=1e-3 if depth == 16 else 0)


class CardAndTextTests(unittest.TestCase):
    def test_the_cards_choices_make_a_request_the_job_accepts(self):
        request = spec.card_request(4096, "UNITY", "PNG", 16, True, {"NORMAL", "AO"}, True, "Crate")
        self.assertEqual((request["packing"], request["resolution"], request["depth"], request["maps"]),
                         ("UNITY", [4096, 4096], 16, ["NORMAL", "AO"]))
        self.assertEqual(request["output"], "Texture sets/Crate maps")
        checked = validate_job("angelo_job_start", dict(request, targets=[A, B]), person=True)
        self.assertTrue(checked["queue"] and checked["material"])
        exr = spec.card_request(4096, "UNITY", "OPEN_EXR", 16, True, None, True, "Crate")
        self.assertNotIn("depth", exr)   # 16 bits is a PNG choice; OpenEXR is half by default.
        self.assertNotIn("maps", exr)    # Every map the materials use.

    def test_approval_lines_say_what_is_baked_and_that_nothing_is_overwritten(self):
        from angelo_agent import bake_maps_text
        facts = {"targets": ["Crate"], "channels": {"Crate": ["BASE_COLOR", "NORMAL"]}, "files": 3,
                 "tiles": {"Crate": [1001, 1002]}, "approximated": ["Toon"], "material": True}
        lines = bake_maps_text.approval_lines(facts, {"packing": "UNREAL", "format": "PNG"})
        self.assertIn("Crate: Base color · Normal", lines[0])
        self.assertTrue(any("Unreal Engine" in line for line in lines))
        self.assertTrue(any("1001, 1002" in line for line in lines))
        self.assertTrue(any("Toon" in line for line in lines))
        self.assertTrue(lines[-1].startswith("Bakes into temporary images"))
        self.assertEqual(bake_maps_text.title(facts), "Bake the texture set of Crate")


def _exr_header_end(data):
    """The offset just past an EXR header: attributes (name, type, size, value) until an empty name."""
    offset = 8
    while data[offset] != 0:
        offset = data.index(b"\0", offset) + 1   # name
        offset = data.index(b"\0", offset) + 1   # type
        size = struct.unpack("<i", data[offset:offset + 4])[0]
        offset += 4 + size
    return offset + 1


def _unzip(body):
    coded = numpy.frombuffer(zlib.decompress(body), dtype=numpy.uint8).astype(numpy.int16)
    split = numpy.cumsum(numpy.concatenate([coded[:1], coded[1:] - 128])) % 256
    half = (len(split) + 1) // 2
    out = numpy.empty(len(split), dtype=numpy.uint8)
    out[0::2], out[1::2] = split[:half], split[half:]
    return out.tobytes()


if __name__ == "__main__":
    unittest.main()
