# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own glTF imports weld files that have morph targets (``angelo_agent/gltf_morph.py``).

Dependency-free. ``has_morph_targets`` reads only a file's glTF JSON (a GLB's JSON chunk, or a .gltf): any mesh
primitive with ``targets`` counts, and anything unreadable (a missing file, another format, a truncated or oversized
chunk, JSON of another shape) counts as none, so the importer reports its own error. Every ``import_scene.gltf`` call
Angelo's add-on makes passes ``import_options``, so a new import path cannot forget it; the importer's warning for an
unwelded morph file is in Blender's bundled importer. The real imports run in test_blender_gltf_morph_weld.py.
"""

import ast
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON))
import gltf_morph  # noqa: E402


def glb(document, kind=gltf_morph.JSON_CHUNK, length=None):
    data = json.dumps(document).encode()
    data += b" " * (-len(data) % 4)
    chunk = struct.pack("<II", len(data) if length is None else length, kind) + data
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunk)) + chunk


def morph_mesh(targets):
    return {"primitives": [{"attributes": {"POSITION": 0}, **({"targets": targets} if targets is not None else {})}]}


class MorphTargetTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-gltf-morph-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        return path

    def test_a_primitive_with_targets_in_a_glb_or_a_gltf(self):
        document = {"asset": {"version": "2.0"}, "meshes": [morph_mesh(None), morph_mesh([{"POSITION": 1}])]}
        for path in (self.write("model.glb", glb(document)), self.write("model.gltf", document)):
            self.assertTrue(gltf_morph.has_morph_targets(path), path.name)
            self.assertEqual(gltf_morph.import_options(path), {"merge_vertices": True})

    def test_no_targets_imports_as_blender_s_default(self):
        for document in ({"asset": {"version": "2.0"}}, {"meshes": [morph_mesh(None)]}, {"meshes": [morph_mesh([])]},
                         {"meshes": []}):
            path = self.write("plain.glb", glb(document))
            self.assertFalse(gltf_morph.has_morph_targets(path), document)
            self.assertEqual(gltf_morph.import_options(path), {})

    def test_anything_unreadable_counts_as_none(self):
        document = {"meshes": [morph_mesh([{"POSITION": 1}])]}
        cases = {
            "missing": None,
            "another format": b"PK\x03\x04 not a model",
            "truncated header": b"glTF\x02\x00",
            "binary chunk first": glb(document, kind=0x004E4942),
            "oversized chunk": glb(document, length=gltf_morph.MAX_JSON + 1),
            "broken json": b"glTF" + struct.pack("<IIII", 2, 30, 6, gltf_morph.JSON_CHUNK) + b'{"mes',
            "json of another shape": json.dumps([document]).encode(),
            "meshes of another shape": json.dumps({"meshes": {"0": morph_mesh([{}])}}).encode(),
        }
        for label, data in cases.items():
            path = self.folder / "case.glb" if data is None else self.write("case.glb", data)
            self.assertFalse(gltf_morph.has_morph_targets(path), label)


class CallSiteTests(unittest.TestCase):
    def test_every_gltf_import_in_the_add_on_passes_the_options(self):
        calls = []
        for path in sorted(ADDON.rglob("*.py")):
            if "tests" in path.relative_to(ADDON).parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "gltf"
                        and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "import_scene"):
                    spread = [keyword.value for keyword in node.keywords if keyword.arg is None]
                    passes = any(isinstance(value, ast.Call) and getattr(value.func, "id", "") == "import_options"
                                 for value in spread) or (spread and path.name == "studio_assets_mesh.py")
                    calls.append((path.name, node.lineno, bool(passes)))
        self.assertGreaterEqual(len(calls), 4, calls)
        self.assertEqual([call for call in calls if not call[2]], [], "every import_scene.gltf passes import_options")

    def test_the_model_path_builds_its_options_from_the_helper(self):
        text = (ADDON / "studio_assets_mesh.py").read_text(encoding="utf-8")
        self.assertIn("**import_options(path)}", text)

    def test_blender_s_importer_warns_and_keeps_its_default(self):
        importer = (ROOT / "scripts/addons_core/io_scene_gltf2/__init__.py").read_text(encoding="utf-8")
        merge = importer[importer.index("    merge_vertices: BoolProperty("):]
        self.assertIn("default=False,", merge[:merge.index(")\n") + 400])
        self.assertIn("if not import_settings.get('merge_vertices') and unwelded_morph_targets(gltf_importer.data):",
                      importer)

    def test_the_warning_is_in_angelo_s_catalogues(self):
        """The importer translates its warning through Blender's catalogue lookup, which falls through to Angelo's;
        the extractor cannot see the importer, so ``sources.EXTRA_MSGIDS`` carries the same text."""
        importer = ROOT / "scripts/addons_core/io_scene_gltf2/__init__.py"
        constant = next(ast.literal_eval(node.value) for node in ast.parse(importer.read_text(encoding="utf-8")).body
                        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "UNWELDED_MORPH_TARGETS")
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import po, sources
        self.assertIn(("", constant, "io_scene_gltf2/__init__.py"), sources.EXTRA_MSGIDS)
        self.assertIn(constant, {entry.id for entry in po.read(str(sources.POT))[1]})


if __name__ == "__main__":
    unittest.main()
