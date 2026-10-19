# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native RNA-shaped pointer boundaries and format-dependent scalar restoration."""

import importlib
import unittest
from types import SimpleNamespace

from rendered_view_case import LookCase
from rendered_view_fakes import FakeID, Struct


def prop(name, kind="STRING", *, readonly=False, runtime=False, array=False, target=""):
    return SimpleNamespace(identifier=name, type=kind, is_readonly=readonly, is_runtime=runtime, is_array=array,
                           fixed_type=SimpleNamespace(identifier=target))


class ProbeOwner:
    def __init__(self, kind, properties, **children):
        self.bl_rna = SimpleNamespace(identifier=kind, properties=properties)
        self.children, self.reads = children, []

    def __getattr__(self, name):
        self.reads.append(name)
        value = self.children[name]
        if isinstance(value, Exception):
            raise value
        return value


class FormatRestoreTests(LookCase):
    def setUp(self):
        super().setUp()
        self.restore = importlib.import_module("angelo_agent.view_render_restore")

    def test_readonly_embedded_pointer_keeps_writable_colorspace_children(self):
        for file_format, colour in (("OPEN_EXR", "ACEScg"), ("JPEG", "Linear Rec.709")):
            with self.subTest(file_format=file_format):
                scene = self.blender.scene
                settings = scene.render.image_settings
                settings.file_format = file_format
                settings.linear_colorspace_settings.name = colour
                before = scene.snapshot()
                values, _exact = self.restore.picture_values(scene)
                overrides = self.restore.LookSettings(scene, "format-test")
                overrides.apply(values)
                self.assertIn("render.image_settings.linear_colorspace_settings.name", overrides.originals)
                self.assertEqual(overrides.restore(), [])
                self.assertEqual(scene.snapshot(), before)

    def test_unknown_pointer_collections_runtime_getters_and_arrays_are_not_read(self):
        child = ProbeOwner("ColorManagedInputColorspaceSettings", [prop("name"), prop("derived", readonly=True)])
        owner = ProbeOwner("ImageFormatSettings", [
            prop("linear_colorspace_settings", "POINTER", readonly=True, target=child.bl_rna.identifier),
            prop("custom_pointer", "POINTER", target="ColorManagedViewSettings"),
            prop("custom_callback", runtime=True), prop("items", "COLLECTION"), prop("array", "FLOAT", array=True),
            prop("readonly", readonly=True)], linear_colorspace_settings=child)
        self.assertEqual(list(self.restore._scalar_paths(owner, "render.image_settings.")),
                         ["render.image_settings.linear_colorspace_settings.name"])
        self.assertEqual(owner.reads, ["linear_colorspace_settings"])
        self.assertEqual(child.reads, [], "Path enumeration never reads scalar getters")

    def test_target_type_owner_type_and_depth_bound_precede_pointer_access(self):
        for owner_type, target_type, depth, runtime in (("Scene", "ColorManagedInputColorspaceSettings", 2, False),
                                                       ("ImageFormatSettings", "Image", 2, False),
                                                       ("ImageFormatSettings", "ColorManagedInputColorspaceSettings", 0, False),
                                                       ("ImageFormatSettings", "ColorManagedInputColorspaceSettings", 2, True)):
            owner = ProbeOwner(owner_type, [prop("linear_colorspace_settings", "POINTER", target=target_type, runtime=runtime)])
            self.assertEqual(list(self.restore._scalar_paths(owner, "render.image_settings.", depth=depth)), [])
            self.assertEqual(owner.reads, [])

    def test_id_datablock_is_not_traversed_even_if_pointer_metadata_is_wrong(self):
        owner = ProbeOwner("ImageFormatSettings", [prop("linear_colorspace_settings", "POINTER",
                                                        target="ColorManagedInputColorspaceSettings")],
                           linear_colorspace_settings=FakeID())
        self.assertEqual(list(self.restore._scalar_paths(owner, "render.image_settings.")), [])

    def test_inaccessible_admitted_pointer_aborts_before_any_setting_changes(self):
        scene = self.blender.scene
        settings = scene.render.image_settings
        owner = ProbeOwner("ImageFormatSettings", [prop("linear_colorspace_settings", "POINTER",
                                                        target="ColorManagedInputColorspaceSettings")],
                           linear_colorspace_settings=RuntimeError("native setting unavailable"))
        scene.render.values["image_settings"] = owner
        try:
            overrides = self.restore.LookSettings(scene, "format-test")
            with self.assertRaisesRegex(RuntimeError, "native setting unavailable"):
                overrides.apply({"render.resolution_x": 64})
            self.assertEqual(scene.render.resolution_x, 1920)
            self.assertEqual(overrides.originals, {})
            self.assertNotIn(self.restore.TAG, scene)
        finally:
            scene.render.values["image_settings"] = settings

    def test_custom_scene_curves_do_not_enable_a_missing_output_mapping(self):
        scene = self.blender.scene
        settings = scene.render.image_settings
        settings.color_management = "OVERRIDE"
        scene.view_settings.use_curve_mapping = True
        values, exact = self.restore.picture_values(scene)
        self.assertIs(exact, False)
        self.assertIsNone(settings.view_settings.curve_mapping)
        self.assertIs(values["render.image_settings.view_settings.use_curve_mapping"], False)

    def test_an_existing_output_mapping_keeps_the_prior_approximation_behavior(self):
        scene = self.blender.scene
        settings = scene.render.image_settings
        settings.color_management = "OVERRIDE"
        scene.view_settings.use_curve_mapping = True
        settings.view_settings.values["curve_mapping"] = Struct(_rna_type="CurveMapping")
        values, exact = self.restore.picture_values(scene)
        self.assertIs(exact, False)
        self.assertIs(values["render.image_settings.view_settings.use_curve_mapping"], True)


if __name__ == "__main__":
    unittest.main()
