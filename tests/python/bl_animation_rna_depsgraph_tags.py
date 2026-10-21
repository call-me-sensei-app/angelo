# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Animation data edited through the Python API must reach the evaluated copy.

Each test evaluates the scene once, edits animation data through one RNA function, and then
checks the evaluated object, which is what the viewport, renders and exporters see. Without a
depsgraph tag in the RNA function, the evaluated copy keeps the old animation data until
something unrelated tags the ID (the saved file is correct, the live session is not).

blender -b --factory-startup --python tests/python/bl_animation_rna_depsgraph_tags.py
"""

import unittest

import bpy
from bpy_extras import anim_utils


def _evaluated(obj: bpy.types.Object) -> bpy.types.Object:
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get())


def _evaluated_z(obj: bpy.types.Object) -> float:
    return _evaluated(obj).matrix_world.translation.z


class AbstractEvaluatedCopyTest:
    def setUp(self):
        bpy.ops.wm.read_homefile(use_factory_startup=True, use_empty=True)
        self.scene = bpy.context.scene
        self.obj = bpy.data.objects.new("Animated", None)
        self.source = bpy.data.objects.new("DriverSource", None)
        self.scene.collection.objects.link(self.obj)
        self.scene.collection.objects.link(self.source)

    def key_z_bounce(self) -> bpy.types.FCurve:
        """Key Z to 1, 3, 1 at frames 1, 13, 25 with linear interpolation."""
        for frame, z in ((1, 1.0), (13, 3.0), (25, 1.0)):
            self.obj.location.z = z
            self.obj.keyframe_insert("location", index=2, frame=frame)
        adt = self.obj.animation_data
        fcurve = anim_utils.action_get_channelbag_for_slot(adt.action, adt.action_slot).fcurves[0]
        for key in fcurve.keyframe_points:
            key.interpolation = 'LINEAR'
        fcurve.update()
        return fcurve

    def drive_z_by_sum(self, values) -> bpy.types.FCurve:
        """Drive Z by the sum of the driver source's X, Y, ... location components."""
        self.source.location = tuple(values) + (0.0,) * (3 - len(values))
        fcurve = self.obj.driver_add("location", 2)
        driver = fcurve.driver
        driver.type = 'SUM'
        for index in range(len(values)):
            var = driver.variables.new()
            var.name = "v{:d}".format(index)
            var.targets[0].id = self.source
            var.targets[0].data_path = "location[{:d}]".format(index)
        return fcurve


class FCurveModifierTagTest(AbstractEvaluatedCopyTest, unittest.TestCase):
    def test_new_on_action_fcurve_other_frame(self):
        fcurve = self.key_z_bounce()
        self.scene.frame_set(5)
        fcurve.modifiers.new('CYCLES')
        self.scene.frame_set(37)
        # Frame 37 repeats frame 13 once the Cycles modifier is evaluated.
        self.assertAlmostEqual(_evaluated_z(self.obj), 3.0, places=4)

    def test_new_on_action_fcurve_same_frame(self):
        fcurve = self.key_z_bounce()
        self.scene.frame_set(13)
        # The default Generator is y = 0 + 1 * frame, replacing the keyed value.
        fcurve.modifiers.new('GENERATOR')
        self.assertAlmostEqual(_evaluated_z(self.obj), 13.0, places=4)

    def test_new_on_driver_fcurve(self):
        # A new driver F-Curve maps its input 0..1 to 0..1 and extrapolates linearly.
        fcurve = self.drive_z_by_sum((2.5,))
        self.assertAlmostEqual(_evaluated_z(self.obj), 2.5, places=4)
        fcurve.modifiers.new('CYCLES')
        self.assertAlmostEqual(_evaluated_z(self.obj), 0.5, places=4)


class DriverVariableTagTest(AbstractEvaluatedCopyTest, unittest.TestCase):
    def test_remove(self):
        fcurve = self.drive_z_by_sum((2.0, 3.0))
        self.assertAlmostEqual(_evaluated_z(self.obj), 5.0, places=4)
        fcurve.driver.variables.remove(fcurve.driver.variables[1])
        self.assertAlmostEqual(_evaluated_z(self.obj), 2.0, places=4)

    def test_new(self):
        fcurve = self.drive_z_by_sum((2.0,))
        _evaluated_z(self.obj)
        fcurve.driver.variables.new()
        evaluated_driver = _evaluated(self.obj).animation_data.drivers[0].driver
        self.assertEqual(len(evaluated_driver.variables), 2)


class AnimDataDriversTagTest(AbstractEvaluatedCopyTest, unittest.TestCase):
    def test_new(self):
        self.obj.location.z = 4.0
        self.obj.animation_data_create()
        _evaluated_z(self.obj)
        # A new driver has no variables; its F-Curve maps the resulting 0 to 0.
        self.obj.animation_data.drivers.new("location", index=2)
        self.assertAlmostEqual(_evaluated_z(self.obj), 0.0, places=4)

    def test_remove(self):
        fcurve = self.drive_z_by_sum((2.0,))
        _evaluated_z(self.obj)
        self.obj.animation_data.drivers.remove(fcurve)
        self.assertEqual(len(_evaluated(self.obj).animation_data.drivers), 0)

    def test_from_existing(self):
        other = bpy.data.objects.new("Other", None)
        self.scene.collection.objects.link(other)
        self.source.location.x = 2.0
        template = other.driver_add("location", 2)
        template.driver.type = 'SUM'
        var = template.driver.variables.new()
        var.targets[0].id = self.source
        var.targets[0].data_path = "location[0]"

        self.obj.animation_data_create()
        _evaluated_z(self.obj)
        self.obj.animation_data.drivers.from_existing(src_driver=template)
        self.assertAlmostEqual(_evaluated_z(self.obj), 2.0, places=4)


class FCurveAPITagTest(AbstractEvaluatedCopyTest, unittest.TestCase):
    """Bake and sampling keep the curve's values: the evaluated Action's own curve is checked."""

    def evaluated_fcurve(self) -> bpy.types.FCurve:
        adt = self.obj.animation_data
        action = adt.action.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return anim_utils.action_get_channelbag_for_slot(action, adt.action_slot).fcurves[0]

    def test_convert_to_samples(self):
        fcurve = self.key_z_bounce()
        self.scene.frame_set(5)
        fcurve.convert_to_samples(1, 26)
        self.assertEqual(len(self.evaluated_fcurve().sampled_points), 25)
        self.assertEqual(len(self.evaluated_fcurve().keyframe_points), 0)

    def test_convert_to_keyframes(self):
        fcurve = self.key_z_bounce()
        fcurve.convert_to_samples(1, 26)
        self.scene.frame_set(5)
        fcurve.convert_to_keyframes(1, 26)
        self.assertEqual(len(self.evaluated_fcurve().keyframe_points), 25)

    def test_bake(self):
        fcurve = self.key_z_bounce()
        self.scene.frame_set(5)
        fcurve.bake(1, 25, step=1.0, remove='ALL')
        self.assertEqual(len(self.evaluated_fcurve().keyframe_points), 25)


class ChannelbagFCurveTagTest(AbstractEvaluatedCopyTest, unittest.TestCase):
    def test_new_from_fcurve_same_frame(self):
        fcurve = self.key_z_bounce()
        other = bpy.data.objects.new("Other", None)
        self.scene.collection.objects.link(other)
        for frame, z in ((1, 1.0), (25, 8.0)):
            other.location.z = z
            other.keyframe_insert("location", index=2, frame=frame)
        other_adt = other.animation_data
        source = anim_utils.action_get_channelbag_for_slot(
            other_adt.action, other_adt.action_slot).fcurves[0]

        adt = self.obj.animation_data
        channelbag = anim_utils.action_get_channelbag_for_slot(adt.action, adt.action_slot)
        self.scene.frame_set(13)
        channelbag.fcurves.remove(fcurve)
        channelbag.fcurves.new_from_fcurve(source)
        # Halfway between 1 and 8 with the default Bezier interpolation's symmetric handles.
        self.assertAlmostEqual(_evaluated_z(self.obj), source.evaluate(13), places=4)
        self.assertAlmostEqual(_evaluated_z(self.obj), 4.5, places=4)


if __name__ == "__main__":
    import sys
    sys.argv = [__file__] + (sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    unittest.main()
