# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unity's engine-link adapter, without Blender or Unity (doc/angelo/unity_link.rst).

Covered: a request resolves only to a .blend, .glb or .fbx under ``Assets/`` of a Unity project (never through
``..``, a link out of the project or Angelo's working folder, never another kind); its working .blend sits outside
``Assets``; a .fbx is written back with the options Unity itself passes to Blender (Unity-BlenderToFBX.py) and a .glb
with the shared glTF profile; the package admits the same kinds and names assets by their project path; the package
installs as an embedded package with its .meta files.
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_assets as assets, unity_link_assets as unity  # noqa: E402

RULES = unity.PLUGIN_SOURCE / "Editor/AssetRules.cs"


def project():
    folder = Path(tempfile.mkdtemp()).resolve()
    (folder / "Assets/Props").mkdir(parents=True)
    (folder / "ProjectSettings").mkdir()
    (folder / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.5.6f1\n")
    for name in ("crate.fbx", "crate.glb", "crate.blend", "crate.obj"):
        (folder / "Assets/Props" / name).write_bytes(b"x")
    return folder


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.project = project()

    def resolve(self, path, folder=None):
        return unity.resolve({"project": str(folder or self.project), "asset": path})

    def code(self, path, folder=None):
        with self.assertRaises(assets.AssetError) as caught:
            self.resolve(path, folder)
        caught.exception.english()
        return caught.exception.code

    def test_models_under_assets_resolve(self):
        fbx = self.resolve("Assets/Props/crate.fbx")
        self.assertEqual((fbx.engine, fbx.ident, fbx.kind), ("unity", "Assets/Props/crate.fbx", ".fbx"))
        self.assertEqual(fbx.editable, str(self.project / ".angelo/Assets/Props/crate.fbx.blend"))
        self.assertEqual(self.resolve("Assets/Props/crate.blend").editable, str(self.project / "Assets/Props/crate.blend"))

    def test_refusals(self):
        self.assertEqual(self.code("Assets/Props/crate.obj"), "kind")
        self.assertEqual(self.code("Assets/Props/none.fbx"), "missing")
        self.assertEqual(self.code("Packages/x/crate.fbx"), "outside")
        self.assertEqual(self.code("res://Assets/Props/crate.fbx"), "outside")
        self.assertEqual(self.code("Assets/../ProjectSettings/ProjectVersion.txt"), "outside")
        self.assertEqual(self.code("Assets/Props/crate.fbx", self.project / "Assets"), "not_project")
        (self.project / "ProjectSettings/ProjectVersion.txt").unlink()
        self.assertEqual(self.code("Assets/Props/crate.fbx"), "not_project")

    @unittest.skipIf(sys.platform == "win32", "symbolic links")
    def test_a_link_out_of_the_project_is_outside(self):
        outside = Path(tempfile.mkdtemp()) / "secret.fbx"
        outside.write_bytes(b"x")
        os.symlink(outside, self.project / "Assets/link.fbx")
        self.assertEqual(self.code("Assets/link.fbx"), "outside")


class ExportTests(unittest.TestCase):
    def asset(self, kind):
        return assets.Asset("unity", "/p", "Assets/a" + kind, "/p/Assets/a" + kind, kind, "Assets/a" + kind)

    def test_fbx_is_written_as_unity_converts_a_blend(self):
        exporter, options = unity.durable(self.asset(".fbx"), {})
        self.assertEqual(exporter, "fbx")
        self.assertEqual(options, {   # Unity 6000.5's Contents/Resources/AssetImporting/Unity-BlenderToFBX.py.
            "use_active_collection": False,
            "object_types": ["ARMATURE", "CAMERA", "LIGHT", "MESH", "OTHER", "EMPTY"],
            "use_mesh_modifiers": True, "mesh_smooth_type": "OFF", "use_custom_props": True,
            "bake_anim_use_nla_strips": False, "bake_anim_use_all_actions": False,
            "apply_scale_options": "FBX_SCALE_ALL"})
        options["object_types"].append("X")
        self.assertNotIn("X", unity.UNITY_FBX["object_types"], "each save gets its own copy")

    def test_glb_and_blend(self):
        self.assertEqual(unity.durable(self.asset(".glb"), {}), ("gltf", assets.GLTF_DURABLE))
        self.assertIsNone(unity.durable(self.asset(".blend"), {}))


class PackageTests(unittest.TestCase):
    def test_the_package_admits_the_adapters_kinds_and_names_assets_by_their_path(self):
        source = RULES.read_text(encoding="utf-8")
        kinds = re.search(r"Kinds = \{([^}]*)\}", source).group(1)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', kinds)), unity.KINDS)
        self.assertIn("ToWire(string assetPath) => assetPath;", source)
        self.assertNotIn("res://", source)

    def test_it_installs_as_an_embedded_package_with_its_meta_files(self):
        folder = project()
        target = Path(assets.install_plugin(unity, str(folder)))
        self.assertEqual(target, folder / "Packages/com.hyperbond.angelo-link")
        self.assertTrue((target / "package.json").is_file())
        self.assertTrue((target / "Editor/AngeloLink.cs.meta").is_file(), "the GUIDs come with it")
        self.assertEqual([path.name for path in (folder / "Packages").iterdir()], ["com.hyperbond.angelo-link"])


if __name__ == "__main__":
    unittest.main()
