# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""glTF URI checks shared by every place Angelo imports a GLB or glTF it didn't write (gltf_checks.py).

The checks must read a URI exactly as Blender's importer does: data only in the form it decodes as data, any other
URI a path it opens. Every import path uses them: Media Studio's provider results and imports
(``studio_generation.checked_glb``), Media Studio's model helper (drops, its background writer, photo drop:
``studio_assets_mesh.import_glb``), the team library (``team_library_files``) and engine-link working files
(``engine_link_import``). Host tests only.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import base64
import json
import ntpath
import os
import struct
import tempfile
import unittest
from pathlib import Path

import team_library_fixtures as f
from angelo_agent import gltf_checks as checks, studio_generation
from angelo_agent.gltf_checks import Refused

ADDON = f.ROOT / "scripts/addons_core/angelo_agent"
DATA = "data:application/octet-stream;base64," + base64.b64encode(bytes(8)).decode()


def document(*uris):
    return {"asset": {"version": "2.0"}, "buffers": [{"uri": uri, "byteLength": 8} for uri in uris]}


class UriTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.model = self.root / "asset" / "model.gltf"
        self.model.parent.mkdir()
        (self.model.parent / "model.bin").write_bytes(bytes(8))
        (self.root / "outside.bin").write_bytes(bytes(8))

    def test_data_is_admitted_only_in_the_form_the_importer_decodes(self):
        self.assertEqual(checks.data_payload(DATA, "x"), bytes(8))
        self.assertEqual(checks.data_payload("data:;base64x,QUFB;base64,QkJC", "x"), b"BBB")   # As the importer.
        for uri in ("data:x/../outside.bin", "data:application/octet-stream,AAAA", "data:"):
            with self.subTest(uri=uri), self.assertRaises(Refused):
                checks.data_payload(uri, "x")

    def test_a_downloaded_model_refers_to_nothing_but_data(self):
        checks.check_references(document(DATA), self_contained=True)
        for uri in ("model.bin", "data:x/../outside.bin"):
            with self.subTest(uri=uri), self.assertRaises(Refused):
                checks.check_references(document(uri), self.model, self_contained=True)

    def test_a_persons_own_model_may_refer_only_inside_its_folder(self):
        checks.check_references(document("model.bin", "./model.bin", "model%2Ebin", DATA), self.model,
                                self_contained=False)
        for uri in ("../outside.bin", "..\\outside.bin", "%2E%2E/outside.bin", str(self.root / "outside.bin"),
                    "data:x/../../outside.bin", "sub/../../outside.bin"):
            with self.subTest(uri=uri), self.assertRaises(Refused):
                checks.check_references(document(uri), self.model, self_contained=False)

    def test_a_link_inside_the_folder_that_leads_outside_is_refused(self):
        os.symlink(self.root / "outside.bin", self.model.parent / "link.bin")
        with self.assertRaises(Refused):
            checks.check_references(document("link.bin"), self.model, self_contained=False)

    def test_images_are_checked_as_buffers_are(self):
        doc = {"asset": {"version": "2.0"}, "images": [{"uri": "data:x/../outside.png"}]}
        with self.assertRaises(Refused):
            checks.check_references(doc, self.model, self_contained=False)

    def test_check_file_reads_a_glb_or_a_gltf(self):
        glb = self.model.parent / "model.glb"
        glb.write_bytes(f.glb({"asset": {"version": "2.0"}, "buffers": [{"uri": "../outside.bin", "byteLength": 8}]}))
        with self.assertRaises(Refused):
            checks.check_file(glb, self_contained=False)
        self.model.write_text(json.dumps(document("model.bin")))
        checks.check_file(self.model, self_contained=False)
        with self.assertRaises(Refused):
            checks.check_file(self.model, self_contained=True)


class StudioTests(unittest.TestCase):
    def glb(self, uri):
        return f.glb({"asset": {"version": "2.0"}, "buffers": [{"uri": uri, "byteLength": 8}]})

    def test_media_studio_admits_only_data_in_the_importers_form(self):
        self.assertTrue(studio_generation.checked_glb(self.glb(DATA)))
        for uri in ("data:x/../outside.bin", "model.bin"):
            with self.subTest(uri=uri), self.assertRaises(studio_generation.GenerationError):
                studio_generation.checked_glb(self.glb(uri))


class CallSiteTests(unittest.TestCase):
    """Each import path checks before Blender's importer reads the file (source level: the importer needs bpy)."""

    def calls(self, name, function, owner=None):
        """``[(line, called name)]`` in ``function`` (of class ``owner``), in source order."""
        tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
        scope = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == owner) if owner else tree
        node = next(n for n in ast.walk(scope) if isinstance(n, ast.FunctionDef) and n.name == function)
        return sorted((c.lineno, ast.unparse(c.func)) for c in ast.walk(node) if isinstance(c, ast.Call))

    def first(self, calls, *names):
        return min((line for line, call in calls if call.endswith(names)), default=None)

    def test_every_import_path_checks_first(self):
        for name, owner, function, check, mode in (
                ("studio_assets_mesh.py", None, "import_glb", "check_file", "self_contained=True"),
                ("asset_stock_import.py", None, "apply_glb", "check_file", "self_contained=True"),
                ("engine_link_import.py", None, "working_file", "check_file", "self_contained=False"),
                ("studio_host.py", None, "import_glb", "checked_glb", None),
                ("studio_asset_ops.py", "ANGELO_OT_studio_import_3d", "execute", "checked_glb", None)):
            with self.subTest(module=name):
                calls = self.calls(name, function, owner)
                checked, imported = self.first(calls, check), self.first(calls, "gltf", "read_homefile", "import_glb")
                self.assertIsNotNone(checked, "%s.%s never checks" % (name, function))
                if imported is not None:
                    self.assertLess(checked, imported)
                if mode:
                    self.assertIn(mode, (ADDON / name).read_text(encoding="utf-8"))

    def test_the_angelo_library_imports_only_what_its_worker_checked(self):
        """library_import reads ``facts`` that only library_files.check_item makes, on the fetch worker."""
        self.assertIn("check_item", [call.rsplit(".", 1)[-1] for _line, call in
                                     self.calls("library_host.py", "_fetch_job", "Library")])
        source = (ADDON / "library_files.py").read_text(encoding="utf-8")
        self.assertIn("gltf.data_payload", source)
        self.assertIn("checks.inspect", source)

    def test_no_other_module_imports_gltf_unchecked(self):
        """A new import_scene.gltf call joins one of the checked paths (or this list, with its check)."""
        known = {"studio_assets_mesh.py", "engine_link_import.py", "studio_host.py", "studio_asset_ops.py",
                 "team_library_import.py", "library_import.py", "asset_stock_import.py",
                 "engine_reimport.py"}   # The preflight's read-back: gltf_checks.check_file before the importer.
        found = {p.name for p in ADDON.glob("*.py") if "import_scene.gltf(" in p.read_text(encoding="utf-8")}
        self.assertEqual(found - known, set())


class PlatformTests(unittest.TestCase):
    """The containment test resolves a URI with the platform's own rules, as the importer does (Windows: ntpath)."""

    def refused_on_windows(self, uri):
        doc = document(uri)
        with self.assertRaises(Refused):
            checks._check_references(doc, "C:\\Projects\\Game\\pack\\model.glb", False, "x", path=ntpath)

    def test_windows_separators_and_drives_stay_inside_or_are_refused(self):
        checks._check_references(document("tex/model.bin"), "C:\\Projects\\Game\\pack\\model.glb", False, "x",
                                 path=ntpath)
        for uri in ("%5C..%5C..%5Coutside.bin", "..\\outside.bin", "a%5C..%5C..%5Coutside.bin", "D:/outside.bin",
                    "//host/share/x.bin", "\\\\host\\share\\x.bin", "/outside.bin"):
            with self.subTest(uri=uri):
                self.refused_on_windows(uri)

    def test_what_the_importer_opens_is_what_the_check_tested(self):
        """Every admitted URI resolves inside the folder by the importer's own expression, on both platforms."""
        parts = ["a", "..", ".", "%2E%2E", "%5C", "\\", "/", "b.bin", "%2F"]
        import itertools
        # The host's own rules on a full path of the host (a drive on Windows, where os.path is ntpath).
        host_model = os.path.abspath(os.path.join(os.sep, "p", "q", "model.glb"))
        for pathmod, model in ((ntpath, "C:\\p\\q\\model.glb"), (os.path, host_model)):
            folder = pathmod.dirname(model)
            for combo in itertools.product(parts, repeat=4):
                uri = "".join(combo)
                try:
                    checks._check_references(document(uri), model, False, "x", path=pathmod)
                except (Refused, ValueError):
                    continue
                opened = pathmod.join(folder, pathmod.normpath(checks.unquote(uri.replace("\\", "/"))))
                self.assertTrue(pathmod.abspath(opened).startswith(folder), (pathmod.__name__, uri))

    def test_unusable_paths_are_refusals_not_errors(self):
        with self.assertRaises(Refused):
            checks.check_references(document("x%00.bin"), "/p/q/model.glb", self_contained=False)


class MeshoptTests(unittest.TestCase):
    def test_malformed_accessor_types_are_refusals(self):
        for accessor in ({"count": 3, "type": ["VEC3"], "componentType": 5126},
                         {"count": 3, "type": "VEC3", "componentType": [5126]}):
            with self.subTest(accessor=accessor), self.assertRaises(Refused):
                checks.decoded_size({"accessors": [accessor]})

    def test_malformed_mode_or_filter_types_are_refusals(self):
        for extension in ({"filter": ["OCTAHEDRAL"]}, {"mode": {"x": 1}}):
            with self.subTest(extension=extension), self.assertRaises(Refused):
                checks.check_references(self.view(**extension), self_contained=True)

    def view(self, **extension):
        ext = dict({"buffer": 0, "byteLength": 8, "count": 3, "byteStride": 4, "mode": "TRIANGLES"}, **extension)
        return {"asset": {"version": "2.0"}, "buffers": [{"uri": DATA, "byteLength": 8}],
                "bufferViews": [{"buffer": 0, "byteLength": 8, "extensions": {"EXT_meshopt_compression": ext}}]}

    def test_only_views_within_the_specification_pass_on_every_path(self):
        for ok in ({}, {"byteStride": 2}, {"mode": "INDICES", "count": 7, "byteStride": 2},
                   {"mode": "ATTRIBUTES", "byteStride": 8, "filter": "QUATERNION"},
                   {"mode": "ATTRIBUTES", "byteStride": 12, "filter": "EXPONENTIAL"}):
            checks.check_references(self.view(**ok), self_contained=True)
        for bad in ({"byteStride": 1}, {"byteStride": 3}, {"count": 4}, {"mode": "INDICES", "byteStride": 1},
                    {"mode": "ATTRIBUTES", "byteStride": 6}, {"mode": "ATTRIBUTES", "byteStride": 4, "filter": "QUATERNION"},
                    {"mode": "ATTRIBUTES", "byteStride": 2, "filter": "OCTAHEDRAL"}, {"filter": "OCTAHEDRAL"},
                    {"mode": "OTHER"}, {"count": 2 ** 36, "mode": "ATTRIBUTES", "byteStride": 16}):
            with self.subTest(bad=bad), self.assertRaises(Refused):
                checks.check_references(self.view(**bad), self_contained=True)

    def test_the_importer_refuses_them_before_native_code(self):
        path = f.ROOT / "scripts/addons_core/io_scene_gltf2/io/imp/gltf2_io_binary_meshopt.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        keep = [n for n in tree.body if getattr(n, "name", None) == "check_parameters" or (
            isinstance(n, ast.Assign) and any(getattr(t, "id", "") in {"MAX_DECODED_BYTES", "_FILTER_STRIDES"}
                                              for t in n.targets))]
        namespace = {"ImportError": ImportError}
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), namespace)
        check = namespace["check_parameters"]
        check(3, 4, "TRIANGLES", None)
        for args in ((300, 1, "TRIANGLES", None), (4, 4, "TRIANGLES", None), (10, 1, "INDICES", None),
                     (10, 2, "ATTRIBUTES", "OCTAHEDRAL"), (10, 4, "ATTRIBUTES", "QUATERNION")):
            with self.subTest(args=args), self.assertRaises(ImportError):
                check(*args)
        source = path.read_text(encoding="utf-8")
        self.assertLess(source.index("check_parameters(count, byte_stride, mode, filter)"),
                        source.index("bytearray(count * byte_stride)"))


    def test_the_importer_never_raises_a_draco_count_above_its_accessor(self):
        path = f.ROOT / "scripts/addons_core/io_scene_gltf2/blender/imp/draco_compression_extension.py"
        source = path.read_text(encoding="utf-8")
        for decoded, declared in (("decoderGetIndexCount(decoder) > index_accessor.count", "index_accessor.count = dll"),
                                  ("decoderGetVertexCount(decoder) > point_limit", " accessor.count = dll")):
            self.assertLess(source.index(decoded), source.index(declared))


class DracoHeaderTests(unittest.TestCase):
    """Angelo's glTF importer bounds a Draco stream by its header before the native decoder builds the mesh."""

    def setUp(self):
        path = f.ROOT / "scripts/addons_core/io_scene_gltf2/blender/imp/draco_compression_extension.py"
        self.source = path.read_text(encoding="utf-8")
        tree = ast.parse(self.source)
        names = lambda node: {getattr(x, "id", "") for t in node.targets for x in ast.walk(t)}   # noqa: E731
        keep = [n for n in tree.body if getattr(n, "name", None) in {"_varint", "header_counts", "check_header"} or (
            isinstance(n, ast.Assign) and names(n) & {"MAX_FACES", "SEQUENTIAL", "EDGEBREAKER"})]
        self.ns = {"ImportError": ImportError}
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), self.ns)

    @staticmethod
    def varint(value):
        out = bytearray()
        while True:
            byte, value = value & 0x7F, value >> 7
            out.append(byte | (0x80 if value else 0))
            if not value:
                return bytes(out)

    def stream(self, faces, vertices, method=1, version=(2, 2), kind=1, flags=0):
        head = b"DRACO" + bytes(version) + bytes([kind, method]) + flags.to_bytes(2, "little")
        if method == 0:
            return head + self.varint(faces) + self.varint(vertices) + bytes(8)
        return head + b"\x00" + self.varint(vertices) + self.varint(faces) + bytes(8)

    def test_counts_are_read_from_both_encodings(self):
        self.assertEqual(self.ns["header_counts"](self.stream(3042, 1600, method=0)), (3042, 1600))
        self.assertEqual(self.ns["header_counts"](self.stream(150000, 91918)), (150000, 91918))

    def test_more_geometry_than_the_accessors_declare_is_refused(self):
        check = self.ns["check_header"]
        self.assertEqual(check(self.stream(100, 60), 300, 60), 300)
        self.assertEqual(check(self.stream(4, 6), 12, 5), 12)   # Non-manifold: split vertices above POSITION.
        for data, indices, vertices in ((self.stream(101, 60), 300, 60), (self.stream(100, 301), 300, 60),
                                        (self.stream(100, 10 ** 8, method=0), 300, 10 ** 7),
                                        (self.stream(10 ** 8, 60), 10 ** 9, 60)):
            with self.subTest(header=self.ns["header_counts"](data)), self.assertRaises(ImportError):
                check(data, indices, vertices)

    def test_unreadable_headers_are_refused(self):
        for data in (self.stream(1, 3, version=(2, 1)), self.stream(1, 3, flags=0x8000), self.stream(1, 3, kind=0),
                     self.stream(1, 3, method=5), b"DRACO\x02\x02\x01\x01\x00\x00\x00\xff\xff", b"NOTDRACO"):
            with self.subTest(data=data[:12]), self.assertRaises(ImportError):
                self.ns["check_header"](data, 10 ** 6, 10 ** 6)

    def test_the_bound_runs_before_the_native_decode(self):
        self.assertLess(self.source.index("check_header(draco_buffer"), self.source.index("dll.decoderDecode(decoder"))


if __name__ == "__main__":
    unittest.main()
