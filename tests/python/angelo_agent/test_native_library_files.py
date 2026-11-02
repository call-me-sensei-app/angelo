# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Checks of the Angelo Library's downloaded files before Blender reads them (library_files.py): multi-file glTF
references, Radiance HDR headers, texture maps, and the KTX2 adapter for ToonLab's rocks. Crafted files only."""

import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path

import team_library_fixtures as f
from angelo_agent import library_files as files
from angelo_agent.team_library_files import Refused


def hdr(width, height, magic=b"#?RADIANCE"):
    return magic + b"\nFORMAT=32-bit_rle_rgbe\n\n-Y %d +X %d\n" % (height, width) + bytes(16)


class FilesCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def write(self, name, data):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return path


class GltfTests(FilesCase):
    def gltf(self, **document):
        return dict({"asset": {"version": "2.0"}, "buffers": [{"uri": "m.bin", "byteLength": 8}],
                     "images": [{"uri": "textures/d.png"}]}, **document)

    def resources(self):
        return {"m.bin": self.write("m.bin", bytes(8)), "textures/d.png": self.write("textures/d.png", f.png())}

    def test_references_to_its_own_files_are_admitted(self):
        facts = files.check_gltf(self.write("m.gltf", self.gltf()), self.resources())
        self.assertEqual(facts["textures"], 1)

    def test_any_reference_outside_its_listed_files_is_refused(self):
        self.write("secret.bin", bytes(8))
        for document in (self.gltf(buffers=[{"uri": "../secret.bin", "byteLength": 8}]),
                         self.gltf(buffers=[{"uri": "/etc/passwd", "byteLength": 8}]),
                         self.gltf(images=[{"uri": "https://evil.example/x.png"}]),
                         self.gltf(images=[{"uri": "secret.bin"}])):
            with self.subTest(document=document), self.assertRaises(Refused):
                files.check_gltf(self.write("m.gltf", document), self.resources())

    def test_truncated_data_unknown_extensions_and_huge_accessors(self):
        cases = (self.gltf(buffers=[{"uri": "m.bin", "byteLength": 9}]),
                 self.gltf(extensionsRequired=["KHR_texture_basisu"]),
                 self.gltf(accessors=[{"count": 10 ** 9, "type": "VEC4", "componentType": 5126}]),
                 dict(self.gltf(), asset={"version": "1.0"}))
        for document in cases:
            with self.subTest(document=document), self.assertRaises(Refused):
                files.check_gltf(self.write("m.gltf", document), self.resources())

    def test_images_in_data_buffers_are_checked_and_unlocatable_images_refused(self):
        hdr = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 46000 +X 46000\n" + bytes(64)
        uri = "data:application/octet-stream;base64," + base64.b64encode(hdr).decode()
        views = [{"buffer": 1, "byteOffset": 0, "byteLength": len(hdr)}]
        document = self.gltf(buffers=[{"uri": "m.bin", "byteLength": 8}, {"uri": uri, "byteLength": len(hdr)}],
                             bufferViews=views, images=[{"bufferView": 0}])
        with self.assertRaises(Refused):
            files.check_gltf(self.write("m.gltf", document), self.resources())
        outside = self.gltf(bufferViews=[{"buffer": 5, "byteLength": 8}], images=[{"bufferView": 0}])
        with self.assertRaises(Refused):
            files.check_gltf(self.write("m.gltf", outside), self.resources())

    def test_a_data_prefix_the_importer_opens_as_a_path_is_refused(self):
        document = self.gltf(buffers=[{"uri": "data:x/../m.bin", "byteLength": 8}])
        with self.assertRaises(Refused):
            files.check_gltf(self.write("m.gltf", document), self.resources())

    def test_texture_bounds_apply_to_side_files(self):
        resources = self.resources()
        resources["textures/d.png"] = self.write("textures/d.png", f.png_header(20000, 10))
        with self.assertRaises(Refused):
            files.check_gltf(self.write("m.gltf", self.gltf()), resources)


class HdrTests(FilesCase):
    def test_standard_headers_and_bounds(self):
        self.assertEqual(files.check_hdr(self.write("a.hdr", hdr(2048, 1024))), (2048, 1024))
        self.assertEqual(files.check_hdr(self.write("b.hdr", hdr(64, 32, b"#?RGBE"))), (64, 32))
        overlong = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n" + b"A" * 128 + b"\n-Y 46000 +X 46000\n\n-Y 16 +X 16\n"
        for data in (hdr(20000, 10), hdr(16384, 16384), b"#?RADIANCE\nno blank line", b"PNG", hdr(0, 5), overlong):
            with self.subTest(data=data[:20]), self.assertRaises(Refused):
                files.check_hdr(self.write("c.hdr", data))


class ItemTests(FilesCase):
    def test_texture_maps_must_be_images(self):
        item = {"kind": "texture", "files": [{"role": "base_color", "name": "base_color.png"},
                                             {"role": "roughness", "name": "roughness.png"}]}
        paths = {"base_color.png": self.write("base_color.png", f.png()), "roughness.png": self.write("roughness.png", b"<svg/>")}
        with self.assertRaises(Refused):
            files.check_item(item, paths)
        paths["roughness.png"] = self.write("roughness.png", f.png())
        self.assertEqual(sorted(files.check_item(item, paths)["maps"]), ["base_color", "roughness"])

    def test_a_ktx2_rock_is_imported_from_a_copy_without_its_ktx2_images(self):
        document = {"asset": {"version": "2.0"}, "extensionsUsed": ["KHR_texture_basisu"],
                    "extensionsRequired": ["KHR_texture_basisu"],
                    "images": [{"bufferView": 0, "mimeType": "image/ktx2"}],
                    "textures": [{"extensions": {"KHR_texture_basisu": {"source": 0}}}],
                    "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}, "normalTexture": {"index": 0}}],
                    "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}], "buffers": [{"byteLength": 8}]}
        rock = self.write("rock.glb", f.glb(document, binary=b"\xabKTX 20\xbb"))
        plain = files._plain_glb(rock)
        self.assertEqual(plain.name, files.PLAIN_GLB)
        length = struct.unpack_from("<I", plain.read_bytes(), 12)[0]
        stripped = json.loads(plain.read_bytes()[20:20 + length])
        self.assertNotIn("images", stripped)
        self.assertNotIn("extensionsRequired", stripped)
        self.assertEqual(stripped["materials"], [{"pbrMetallicRoughness": {}}])

    def test_a_plain_glb_is_checked_as_the_team_library_checks_it(self):
        good = self.write("model.glb", f.glb())
        self.assertEqual(files._plain_glb(good), good)
        with self.assertRaises(Refused):
            files._plain_glb(self.write("bad.glb", f.glb(images=[f.png_header(30000, 30000)])))


if __name__ == "__main__":
    unittest.main()
