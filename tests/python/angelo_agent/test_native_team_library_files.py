# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Untrusted team-library files: allow-list, size caps, decompression bounds, traversal (team_library_files.py).

Every case is a crafted file from team_library_fixtures. Hostile variants must
be refused before any decoder runs: the FBX zlib bomb is refused after
inflating at most its declared length plus one byte.
"""

import os
import tempfile
import unittest
import unittest.mock
import zlib

import team_library_fixtures as f
from angelo_agent import team_library_files as files
from angelo_agent.team_library_files import Refused, inspect


class FilesCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def write(self, data, name="download"):
        path = os.path.join(self.folder.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def refused(self, data, text, kind=""):
        with self.assertRaises(Refused) as caught:
            inspect(self.write(data), kind)
        self.assertIn(text, str(caught.exception))
        return str(caught.exception)


class AllowListTests(FilesCase):
    def test_admitted_formats_are_sniffed_from_content_not_names(self):
        cases = {"png": f.png(), "jpeg": f.jpeg_header(800, 600), "webp": f.webp_header(640, 480), "glb": f.glb(),
                 "fbx": f.fbx([("Objects", [], [])]), "obj": f.obj(), "usda": f.USDA,
                 "usdz": f.usdz([("root.usda", f.USDA)])}
        for fmt, data in cases.items():
            with self.subTest(fmt=fmt):
                facts = inspect(self.write(data, "misleading.blend"))
                self.assertEqual(facts["format"], fmt)
                self.assertEqual(facts["kind"], "image" if fmt in files.IMAGE_FORMATS else "model")
        self.assertEqual(inspect(self.write(b"PXR-USDC" + bytes(80)))["format"], "usdc")

    def test_everything_else_is_refused_with_a_reason(self):
        self.refused(b"BLENDER-v502" + bytes(64), ".blend files are never imported")
        self.refused(b"\x1f\x8b\x08" + bytes(64), ".blend files are never imported")  # Compressed .blend.
        self.refused(b"GIF89a" + bytes(32), "GIF images")
        self.refused(b'{"asset": {"version": "2.0"}, "buffers": [{"uri": "a.bin"}]}', "Multi-file .gltf")
        self.refused(b"; FBX 7.4.0 project file\n", "ASCII FBX")
        self.refused(b"%PDF-1.7\n" + bytes(64), "Only PNG, JPEG or WebP images and GLB")
        self.refused(f.usdz([("readme.txt", b"hi")]), "isn't a USDZ model")

    def test_content_must_match_the_library_kind(self):
        self.refused(f.png(), "doesn't match its library kind (model)", kind="model")
        self.refused(f.glb(), "doesn't match its library kind (image)", kind="image")

    def test_size_caps_per_kind(self):
        with unittest.mock.patch.dict(files.MAX_BYTES, {"model": 100, "image": 10_000}):
            self.refused(f.glb(images=[f.png()]), "GLB files up to 0 MB")
            inspect(self.write(f.png()))


class ImageBoundsTests(FilesCase):
    def test_dimensions_are_read_from_headers(self):
        self.assertEqual(files.image_size(f.png(4, 3)), (4, 3))
        self.assertEqual(files.image_size(f.jpeg_header(1920, 1080, exif=60000)), (1920, 1080))
        self.assertEqual(files.image_size(f.webp_header(640, 480)), (640, 480))
        self.assertIsNone(files.image_size(b"\xff\xd8\xff" + bytes(16)))

    def test_pixel_bombs_are_refused_before_decoding(self):
        self.refused(f.png_header(20000, 10), "20000 x 10 pixels")
        self.refused(f.png_header(9000, 9000), "9000 x 9000 pixels")  # 81 MP > 67 MP.
        self.refused(f.jpeg_header(16384, 16384), "16384 x 16384")
        self.refused(f.webp_header(16383, 16383), "16383 x 16383")
        self.refused(f.png_header(0, 10), "0 x 10")
        inspect(self.write(f.png_header(8192, 8192)))

    def test_unreadable_image_headers_are_refused(self):
        self.refused(b"\xff\xd8\xff\xe0" + bytes(4), "unreadable header")


class GlbTests(FilesCase):
    def test_external_references_are_refused(self):
        for document in ({"asset": {"version": "2.0"}, "images": [{"uri": "../../Pictures/secret.png"}]},
                         {"asset": {"version": "2.0"}, "images": [{"uri": "/etc/hosts"}]},
                         {"asset": {"version": "2.0"}, "buffers": [{"uri": "mesh.bin", "byteLength": 4}]},
                         {"asset": {"version": "2.0"}, "images": [{"uri": "https://example.test/a.png"}]}):
            with self.subTest(document=document):
                self.refused(f.glb(document), "refers to other files")

    def test_embedded_textures_are_bounded(self):
        self.assertEqual(inspect(self.write(f.glb(images=[f.png()])))["textures"], 1)
        self.refused(f.glb(images=[f.png_header(9000, 9000)]), "Texture 1 of this GLB is 9000 x 9000")
        self.refused(f.glb(images=[b"GIF89a" + bytes(40)]), "isn't a PNG, JPEG or WebP")
        self.refused(f.glb(images=[f.png_header(8000, 8000)] * 5), "embedded textures total more than 268 megapixels")
        data_uri = "data:image/png;base64," + __import__("base64").b64encode(f.png_header(20000, 4)).decode()
        self.refused(f.glb({"asset": {"version": "2.0"}, "images": [{"uri": data_uri}]}), "20000 x 4")

    def test_decoded_geometry_is_bounded(self):
        self.refused(f.glb({"asset": {"version": "2.0"}, "accessors": [
            {"count": 100_000_000, "type": "VEC3", "componentType": 5126}]}), "decodes to 1144 MB")
        self.refused(f.glb({"asset": {"version": "2.0"}, "accessors": [{"count": -1, "type": "VEC3",
                                                                         "componentType": 5126}]}), "malformed accessor")

    def test_malformed_containers_are_refused(self):
        data = f.glb()
        self.refused(data[:-4], "malformed or not glTF 2.0")  # Header length no longer matches.
        self.refused(b"glTF" + (1).to_bytes(4, "little") + data[8:], "malformed or not glTF 2.0")
        self.refused(data[:12] + b"\xff\xff\xff\x00" + data[16:], "malformed or not glTF 2.0")


class FbxTests(FilesCase):
    MESH = [("Objects", [], [("Geometry", [("S", b"Geometry::Box"), ("S", b"Mesh")],
                              [("Vertices", [("d", [0.0, 1.0, 2.0])], []), ("PolygonVertexIndex", [("i", [0, 1, -3])], [])])])]

    def test_versions_7400_and_7500_walk_every_node(self):
        for version in (7100, 7400, 7500, 7700):
            with self.subTest(version=version):
                self.assertEqual(inspect(self.write(f.fbx(self.MESH, version)))["format"], "fbx")
        self.refused(f.fbx(self.MESH, 6100), "FBX version 6100")

    def test_zlib_bombs_are_refused_with_a_bounded_inflate(self):
        payload = zlib.compress(bytes(8 * 20_000_000))  # 160 MB of zeros declared as 10 doubles.
        bomb = [("Objects", [], [("Geometry", [], [("Vertices", [("d", ("zlib", [0.0] * 10, 10, payload))], [])])])]
        inflated = []
        real = zlib.decompressobj

        def spy():
            inflater = real()

            class Spy:
                def decompress(self, data, limit):
                    output = inflater.decompress(data, limit)
                    inflated.append(len(output))
                    return output

                def __getattr__(self, name):
                    return getattr(inflater, name)
            return Spy()
        with unittest.mock.patch.object(files.zlib, "decompressobj", spy):
            self.refused(f.fbx(bomb), "doesn't match its declared size")
        self.assertEqual(inflated, [81], "at most the declared length plus one byte is ever inflated")

    def test_declared_totals_are_bounded(self):
        huge = [("Objects", [], [("Vertices", [("d", ("zlib", [0.0], 200_000_000, None))], [])])]
        self.refused(f.fbx(huge), "decodes to more than 1024 MB")

    def test_texture_paths_may_not_climb_and_embedded_textures_are_bounded(self):
        for path in (b"..\\..\\Users\\me\\secret.png", b"textures/../../secret.png"):
            with self.subTest(path=path):
                self.refused(f.fbx([("Objects", [], [("Video", [], [("RelativeFilename", [("S", path)], [])])])]),
                             "points at files outside its folder")
        inspect(self.write(f.fbx([("Objects", [], [("Video", [], [("RelativeFilename", [("S", b"tex\\wood.png")], [])])])])))
        self.refused(f.fbx([("Objects", [], [("Video", [], [("Content", [("R", f.png_header(20000, 20000))], [])])])]),
                     "A texture embedded in this FBX file is 20000 x 20000")
        self.assertEqual(inspect(self.write(f.fbx([("Objects", [], [("Video", [], [("Content", [("R", f.png())], [])])])])))
                         ["textures"], 1)
        many = [("Video", [], [("Content", [("R", f.png_header(8000, 8000))], [])]) for _ in range(5)]
        self.refused(f.fbx([("Objects", [], many)]), "embedded textures total more than 268 megapixels")

    def test_truncated_or_inconsistent_records_are_refused(self):
        data = f.fbx(self.MESH)
        self.refused(data[:60], "FBX")
        broken = bytearray(data)
        broken[27:31] = (len(data) * 2).to_bytes(4, "little")  # First node ends past the file.
        self.refused(bytes(broken), "malformed")


class ObjAndUsdzTests(FilesCase):
    def test_obj_material_libraries_must_be_bare_names(self):
        inspect(self.write(f.obj()))
        for line in ("mtllib ../../materials.mtl\n", "mtllib /etc/passwd\n", "mtllib C:\\x.mtl\n", "mtllib a b/c.mtl\n"):
            with self.subTest(line=line):
                self.refused(f.obj(line), "points at material files elsewhere")

    def test_usdz_entries_are_stored_relative_and_bounded(self):
        facts = inspect(self.write(f.usdz([("root.usda", f.USDA), ("textures/a.png", f.png())])))
        self.assertEqual(facts["layers"], ["root.usda"])
        self.refused(f.usdz([("root.usda", f.USDA)], compress=True), "isn't a valid package")
        self.refused(f.usdz([("root.usda", f.USDA), ("../escape.png", f.png())]), "isn't a valid package")
        self.refused(f.usdz([("root.usda", f.USDA), ("/abs.png", f.png())]), "isn't a valid package")
        self.refused(f.usdz([("root.usda", f.USDA), ("textures/big.png", f.png_header(20000, 2))]),
                     "A texture in this USDZ file is 20000 x 2")
        self.refused(f.usdz([("root.usda", f.USDA)] + [("t%d.png" % n, f.png_header(8000, 8000)) for n in range(5)]),
                     "embedded textures total more than 268 megapixels")


if __name__ == "__main__":
    unittest.main()
