# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Godot assets Angelo edits (Godot's engine-link adapter), where their working files live, and the exporter
options it uses, without Blender (doc/angelo/godot_link.rst).

Covered: a request resolves only to a .blend or .glb inside a Godot project (never through ``..``, a link out of the
project or Angelo's own working folder); a .glb's working .blend lives under ``.angelo`` with a ``.gdignore``; the
working file is current only while the .glb is what Angelo last read or wrote; ``.import`` parameters parse; and
the exporter options match what Godot 4.7.2's Blender importer sent for the same import settings (recorded from its
requests to Blender, the table in the contract).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import engine_link_assets as assets, godot_link_assets as godot  # noqa: E402


def resolve(project, res):
    return godot.resolve({"project": project, "asset": res})


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp()).resolve()
        (self.project / "project.godot").write_text("config_version=5\n")
        (self.project / "props").mkdir()
        for name in ("props/chair.glb", "props/table.blend", "props/lamp.fbx"):
            (self.project / name).write_bytes(b"x")

    def code(self, res, project=None):
        with self.assertRaises(assets.AssetError) as caught:
            resolve(str(project or self.project), res)
        return caught.exception.code

    def test_blend_and_glb_resolve_inside_the_project(self):
        glb = resolve(str(self.project), "res://props/chair.glb")
        self.assertEqual((glb.kind, glb.name, glb.path), (".glb", "chair.glb", str(self.project / "props/chair.glb")))
        self.assertEqual(glb.editable, str(self.project / ".angelo/props/chair.glb.blend"))
        blend = resolve(str(self.project), "res://props/table.blend")
        self.assertEqual(blend.editable, blend.path)

    def test_refusals_name_their_reason(self):
        self.assertEqual(self.code("res://props/lamp.fbx"), "kind")
        self.assertEqual(self.code("res://props/missing.glb"), "missing")
        self.assertEqual(self.code("res://../outside.glb"), "outside")
        self.assertEqual(self.code("res://props/./chair.glb"), "outside")
        self.assertEqual(self.code("/abs/chair.glb"), "outside")
        self.assertEqual(self.code("res://.angelo/props/chair.glb.blend"), "outside")
        self.assertEqual(self.code("res://props/chair.glb", project=self.project / "props"), "not_project")
        self.assertEqual(self.code("res://props/chair.glb", project="relative/path"), "not_project")
        with self.assertRaises(assets.AssetError) as caught:
            resolve(str(self.project), "res://props/lamp.fbx")
        self.assertIn("lamp.fbx", caught.exception.english())

    @unittest.skipIf(sys.platform == "win32", "symbolic links")
    def test_a_link_out_of_the_project_is_outside(self):
        outside = Path(tempfile.mkdtemp()) / "secret.glb"
        outside.write_bytes(b"x")
        os.symlink(outside, self.project / "props/link.glb")
        self.assertEqual(self.code("res://props/link.glb"), "outside")

    def test_angelos_working_files_are_never_assets(self):
        working = self.project / ".angelo/props"
        working.mkdir(parents=True)
        (working / "chair.glb.blend").write_bytes(b"x")
        self.assertEqual(self.code("res://.angelo/props/chair.glb.blend"), "outside")
        self.assertEqual(self.code("res://.Angelo/props/chair.glb.blend"), "outside", "any case")
        if sys.platform != "win32":
            os.symlink(working / "chair.glb.blend", self.project / "props/sneaky.blend")
            self.assertEqual(self.code("res://props/sneaky.blend"), "outside", "any link")

    @unittest.skipIf(sys.platform == "win32", "symbolic links")
    def test_a_cloned_projects_links_never_lead_angelo_out(self):
        elsewhere = Path(tempfile.mkdtemp())
        os.symlink(elsewhere, self.project / ".angelo")
        asset = resolve(str(self.project), "res://props/chair.glb")
        with self.assertRaises(OSError):
            assets.ensure_working_folder(asset)
        self.assertEqual(list(elsewhere.iterdir()), [], "nothing was made through the link")
        os.unlink(self.project / ".angelo")
        assets.ensure_working_folder(asset)
        victim = elsewhere / "victim.txt"
        victim.write_text("keep")
        os.symlink(victim, assets.stamp_path(asset))
        with self.assertRaises(OSError):
            assets.ensure_working_folder(asset)
        self.assertEqual(victim.read_text(), "keep")

    def test_working_folder_and_stamp(self):
        asset = resolve(str(self.project), "res://props/chair.glb")
        assets.ensure_working_folder(asset)
        self.assertTrue((self.project / ".angelo/.gdignore").is_file(), "Godot never imports the working files")
        self.assertFalse(assets.working_is_current(asset))
        Path(asset.editable).write_bytes(b"blend")
        assets.write_stamp(asset)
        self.assertTrue(assets.working_is_current(asset))
        (self.project / "props/chair.glb").write_bytes(b"changed in Godot")
        self.assertFalse(assets.working_is_current(asset), "a .glb changed elsewhere is imported again")


class OptionTests(unittest.TestCase):
    def test_import_params_parse(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "a.blend.import").write_text('[remap]\n\nimporter="scene"\n\n[params]\n\nnodes/root_type=""\n'
                                              "blender/nodes/visible=2\nblender/nodes/cameras=false\n"
                                              "meshes/lightmap_texel_size=0.2\n")
        params = godot.read_import_params(str(folder / "a.blend"))
        self.assertEqual(params["blender/nodes/visible"], 2)
        self.assertIs(params["blender/nodes/cameras"], False)
        self.assertNotIn("importer", params)
        self.assertEqual(godot.read_import_params(str(folder / "none.blend")), {})

    def test_defaults_are_godots(self):
        options = godot.godot_options({})
        self.assertEqual(options["export_import_convert_lighting_mode"], "COMPAT")
        self.assertEqual(options["export_vertex_color"], "ACTIVE")
        self.assertEqual((options["use_visible"], options["use_renderable"]), (False, False))
        self.assertTrue(options["export_apply"] and options["export_extras"] and options["export_all_influences"])

    def test_import_settings_map_as_godot_4_7_2_sent_them(self):
        """Each case is one recorded Godot 4.7.2 request: the .import settings and the options Blender received."""
        cases = (
            ({"blender/nodes/visible": 2, "blender/meshes/vertex_colors": 0, "blender/nodes/modifiers": 0,
              "blender/meshes/skins": 1, "blender/materials/export_materials": 0, "blender/nodes/cameras": False,
              "blender/animation/limit_playback": False},
             {"use_visible": False, "use_renderable": True, "export_vertex_color": "MATERIAL", "export_apply": False,
              "export_skins": True, "export_all_influences": False, "export_materials": "PLACEHOLDER",
              "export_cameras": False, "export_frame_range": False}),
            ({"blender/nodes/visible": 1, "blender/meshes/vertex_colors": 2, "blender/meshes/skins": 0,
              "blender/materials/export_materials": 2},
             {"use_visible": True, "use_renderable": False, "export_vertex_color": "NONE", "export_skins": False,
              "export_all_influences": False, "export_materials": "EXPORT"}),
            ({"blender/nodes/punctual_lights": False, "blender/nodes/custom_properties": False,
              "blender/meshes/uvs": False, "blender/meshes/normals": False, "blender/meshes/tangents": False,
              "blender/meshes/gpu_instances": True, "blender/meshes/export_geometry_nodes_instances": True,
              "blender/meshes/export_bones_deforming_mesh_only": True, "blender/animation/always_sample": False,
              "blender/animation/group_tracks": False, "blender/nodes/active_collection_only": True,
              "blender/meshes/skins": 2},
             {"export_lights": False, "export_extras": False, "export_texcoords": False, "export_normals": False,
              "export_tangents": False, "export_gpu_instances": True, "export_gn_mesh": True, "export_def_bones": True,
              "export_force_sampling": False, "export_animation_mode": "ACTIVE_ACTIONS", "use_active_collection": True,
              "export_skins": True, "export_all_influences": True}),
        )
        for params, expected in cases:
            with self.subTest(params=sorted(params)):
                options = godot.godot_options(params)
                self.assertEqual({key: options[key] for key in expected}, expected)

    def test_unknown_values_keep_godots_defaults(self):
        options = godot.godot_options({"blender/nodes/visible": 7, "blender/meshes/uvs": "yes"})
        self.assertEqual((options["use_visible"], options["export_texcoords"]), (False, True))


if __name__ == "__main__":
    unittest.main()
