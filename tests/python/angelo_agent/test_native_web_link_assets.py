# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web's engine-link adapter (``web_link_assets``; doc/angelo/web_link.rst), without Blender.

Covered: a web project is a folder with package.json; an asset is a .glb named by its path in the project, inside it,
never in node_modules or build output, never Angelo's working folder; a by-name lookup reads the project but skips
installed packages and build output, so a large node_modules never makes it unsure; a save keeps the file's mesh
compression and image format; live scenes are uncompressed, never animate and carry node identity; the plugin is the
npm package, never a copy.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_assets as assets, web_link_assets as web  # noqa: E402


def glb(path, used=()):
    """A minimal .glb whose JSON chunk lists ``used`` extensions."""
    document = json.dumps({"asset": {"version": "2.0"}, "extensionsUsed": list(used)}).encode()
    document += b" " * (-len(document) % 4)
    body = struct.pack("<II", len(document), 0x4E4F534A) + document
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.project = os.path.realpath(self.folder.name)
        Path(self.project, "package.json").write_text('{"name": "shop"}', encoding="utf-8")

    def resolve(self, path):
        return web.resolve({"type": "open", "project": self.project, "asset": path})

    def test_registered_as_an_engine(self):
        self.assertIs(assets.adapter("web"), web)
        self.assertEqual(assets.display_name("web"), "Three.js")

    def test_a_project_is_a_folder_with_package_json(self):
        self.assertTrue(web.is_project(self.project))
        self.assertFalse(web.is_project(tempfile.mkdtemp()))
        self.assertFalse(web.is_project(None))

    def test_an_asset_is_a_glb_in_the_project_named_by_its_path(self):
        glb(Path(self.project, "public/models/chair.glb"))
        asset = self.resolve("public/models/chair.glb")
        self.assertEqual((asset.engine, asset.ident, asset.kind), ("web", "public/models/chair.glb", ".glb"))
        self.assertEqual(asset.editable, os.path.join(self.project, ".angelo", "public", "models", "chair.glb.blend"))

    def test_refusals(self):
        glb(Path(self.project, "node_modules/kit/a.glb"))
        glb(Path(self.project, "dist/a.glb"))
        glb(Path(self.project, ".angelo/a.glb"))
        Path(self.project, "public").mkdir()
        Path(self.project, "public/a.gltf").write_text("{}", encoding="utf-8")
        for path, code in (("node_modules/kit/a.glb", "outside"), ("Dist/a.glb", "outside"),
                           (".angelo/a.glb", "outside"), ("../a.glb", "outside"), ("/etc/a.glb", "outside"),
                           ("public/a.gltf", "kind"), ("public/missing.glb", "missing"), ("", "outside")):
            with self.subTest(path=path):
                with self.assertRaises(assets.AssetError) as caught:
                    self.resolve(path)
                self.assertEqual(caught.exception.code, code)
                self.assertIn(code, web.REFUSED)
        with self.assertRaises(assets.AssetError) as caught:
            web.resolve({"project": tempfile.mkdtemp(), "asset": "a.glb"})
        self.assertEqual(caught.exception.code, "not_project")

    def test_a_link_or_a_folded_name_into_packages_or_build_output_is_refused(self):
        """Break-it F3: the folder rule read only the spelled path, so a link from public/ into node_modules (pnpm,
        workspaces) let a save write into a package; Windows also folds "dist." into dist."""
        glb(Path(self.project, "node_modules/three/models/robot.glb"))
        glb(Path(self.project, "dist/assets/chair.glb"))
        Path(self.project, "public").mkdir()
        os.symlink(Path(self.project, "node_modules/three/models"), Path(self.project, "public/models"))
        os.symlink(Path(self.project, "dist/assets/chair.glb"), Path(self.project, "public/chair.glb"))
        for path in ("public/models/robot.glb", "public/chair.glb", "dist./assets/chair.glb",
                     "Node_Modules /three/models/robot.glb"):
            with self.subTest(path=path):
                with self.assertRaises(assets.AssetError) as caught:
                    self.resolve(path)
                self.assertEqual(caught.exception.code, "outside")

    def test_a_lookup_by_name_skips_packages_and_build_output(self):
        glb(Path(self.project, "public/models/Chair.glb"))
        glb(Path(self.project, "node_modules/kit/chair.glb"))
        glb(Path(self.project, "build/chair.glb"))
        found, complete = assets.named(web, self.project, "chair")
        self.assertEqual(found, ["public/models/Chair.glb"])
        self.assertTrue(complete)

    def test_a_large_node_modules_never_makes_a_lookup_unsure(self):
        glb(Path(self.project, "src/assets/chair.glb"))
        packages = Path(self.project, "node_modules")
        packages.mkdir()
        with mock.patch.object(assets, "NAME_SCAN", 50):
            for index in range(200):
                (packages / ("p%d" % index)).mkdir()
            self.assertEqual(assets.named(web, self.project, "chair.glb"), (["src/assets/chair.glb"], True))


class ExportTests(unittest.TestCase):
    def asset(self, path):
        return assets.Asset("web", os.path.dirname(path), "a.glb", path, ".glb", "a.glb")

    def test_a_save_keeps_the_files_compression_and_image_format(self):
        folder = tempfile.mkdtemp()
        for used, meshopt, draco, image in (((), False, False, "AUTO"),
                                            ((web.MESHOPT, web.WEBP), True, False, "WEBP"),
                                            ((web.DRACO,), False, True, "AUTO"),
                                            ((web.MESHOPT, web.DRACO), True, False, "AUTO")):
            with self.subTest(used=used):
                path = os.path.join(folder, "a.glb")
                glb(path, used)
                exporter, options = web.durable(self.asset(path), web.import_params(self.asset(path)))
                self.assertEqual(exporter, "gltf")
                self.assertEqual((options["export_meshopt_compression_enable"],
                                  options["export_draco_mesh_compression_enable"],
                                  options["export_image_format"]), (meshopt, draco, image))
                self.assertEqual(options["export_import_convert_lighting_mode"], "COMPAT")

    def test_an_unreadable_file_saves_in_the_plain_form(self):
        folder = tempfile.mkdtemp()
        deep = os.path.join(folder, "deep.glb")   # Break-it F4: nested 200,000 deep raised RecursionError.
        Path(deep).write_bytes(b"")
        document = b'{"asset":{"version":"2.0"},"extras":' + b"[" * 200000 + b"]" * 200000 + b"}"
        document += b" " * (-len(document) % 4)
        body = struct.pack("<II", len(document), 0x4E4F534A) + document
        Path(deep).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
        self.assertEqual(web.glb_extensions(deep), frozenset())
        nested = os.path.join(folder, "nested.glb")   # Break-it round 2: only the document's own list counts.
        for document, expected in (
                ({"asset": {"extras": {"extensionsUsed": [web.DRACO]}}}, frozenset()),
                ({"asset": {"extras": {"extensionsUsed": []}}, "extensionsUsed": [web.MESHOPT]},
                 frozenset({web.MESHOPT})),
                (b'{"\\u0065xtensionsUsed": ["%s"]}' % web.DRACO.encode(), frozenset({web.DRACO}))):
            text = document if isinstance(document, bytes) else json.dumps(document).encode()
            text += b" " * (-len(text) % 4)
            body = struct.pack("<II", len(text), 0x4E4F534A) + text
            Path(nested).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
            self.assertEqual(web.glb_extensions(nested), expected)
        # Break-it round 4: a document too long to scan whole still keeps the list three.js writes last.
        text = json.dumps({"nodes": [{"name": "n%d" % index} for index in range(50)],
                           "extensionsUsed": [web.INSTANCING]}).encode()
        text += b" " * (-len(text) % 4)
        body = struct.pack("<II", len(text), 0x4E4F534A) + text
        Path(nested).write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
        with mock.patch.object(web, "MAX_TOKENS", 10):
            self.assertEqual(web.glb_extensions(nested), frozenset({web.INSTANCING}))
        if hasattr(os, "mkfifo"):   # A FIFO swapped in after the asset resolved: answered at once, never read.
            fifo = os.path.join(folder, "fifo.glb")
            os.mkfifo(fifo)
            self.assertEqual(web.glb_extensions(fifo), frozenset())
        for content in (b"", b"not a glb at all", b"glTF" + b"\x00" * 16):
            path = os.path.join(folder, "a.glb")
            Path(path).write_bytes(content)
            self.assertEqual(web.glb_extensions(path), frozenset())
        self.assertEqual(web.glb_extensions(os.path.join(folder, "gone.glb")), frozenset())

    def test_live_scenes_are_uncompressed_never_animate_and_carry_identity(self):
        options = web.live_options({"meshopt": True, "draco": True, "webp": True, "instancing": True})
        self.assertFalse(options["export_gpu_instances"], "linked duplicates stay nodes the page can key")
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "a.glb")
        for used, instancing in (((), False), ((web.INSTANCING,), True)):
            glb(path, used)
            _exporter, saved = web.durable(self.asset(path), web.import_params(self.asset(path)))
            self.assertEqual(saved["export_gpu_instances"], instancing, "a save keeps what the file had")
        self.assertFalse(options["export_meshopt_compression_enable"])
        self.assertFalse(options["export_draco_mesh_compression_enable"])
        self.assertEqual((options["export_animations"], options["export_current_frame"]), (False, True))
        self.assertEqual(web.LIVE_EXPORT_ID, "angelo_link_web")
        source = (ROOT / "scripts/addons_core/angelo_agent/export_baked.py").read_text(encoding="utf-8")
        self.assertIn('"%s"' % web.LIVE_EXPORT_ID, source.split("EXPORT_IDS = ", 1)[1].split(")", 1)[0],
                      "baked materials reach the live scene too")


    def test_the_plugin_is_the_npm_package(self):
        self.assertIsNone(web.PLUGIN_SOURCE)
        command, config = web.install_hint()
        self.assertEqual(command, "npm i -D @call-me-sensei/angelo")
        self.assertIn("@call-me-sensei/angelo/vite", config)
        package = ROOT / "web_link/angelo-live/package.json"
        if package.is_file():
            self.assertEqual(json.loads(package.read_text(encoding="utf-8"))["name"], web.PACKAGE)


class NodeIdentityTests(unittest.TestCase):
    """The glTF hooks of ``web_export.WebExportExtension`` (imported with a stand-in ``bpy``)."""

    def setUp(self):
        stand_in = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"bpy": stand_in, "bpy.props": stand_in.props, "bpy.types": stand_in.types}):
            sys.modules.pop("angelo_agent.web_export", None)
            from angelo_agent import web_export
        self.addCleanup(sys.modules.pop, "angelo_agent.web_export", None)
        self.extension = web_export.WebExportExtension()
        self.link = {"gltf_export_id": web.LIVE_EXPORT_ID}

    def test_the_live_hooks_act_on_the_web_links_export_only(self):
        from angelo_agent import web_link_live
        vtree, image = SimpleNamespace(nodes={}), SimpleNamespace(buffer_view=None, extras=None)
        for settings, expected in ((self.link, 1), ({"gltf_export_id": "angelo_web"}, 0),
                                   ({"gltf_export_id": "angelo_link"}, 0), ({}, 0)):
            with self.subTest(settings=settings), \
                    mock.patch.object(web_link_live, "filter_tree") as filter_tree, \
                    mock.patch.object(web_link_live, "mark_nodes") as mark_nodes, \
                    mock.patch.object(web_link_live, "image") as name_image:
                self.extension.gather_tree_filter_tag_hook(vtree, settings)
                self.extension.gather_image_hook(image, None, None, settings)
                if settings.get("gltf_export_id") == "angelo_web":
                    continue   # Angelo's web export has its own scene hook (the look, merging).
                self.extension.gather_scene_hook(SimpleNamespace(extras=None), None, dict(settings, vtree=vtree))
                self.assertEqual((filter_tree.call_count, name_image.call_count, mark_nodes.call_count),
                                 (expected,) * 3)


if __name__ == "__main__":
    unittest.main()
