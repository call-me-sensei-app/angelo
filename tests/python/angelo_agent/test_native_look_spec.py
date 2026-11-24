# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lens and film look's models (``look_spec``), without Blender.

Contract: doc/angelo/camera_look.rst. The look is the camera's own settings plus one compositor node group; these are
the closed forms it is built from: natural vignetting (cos^4 of the angle from the optical axis, from the real focal
length and sensor), focus breathing (a no-Python driver expression), exposure in stops between two camera settings,
grain sized as a share of the frame, and the stage table and presets every builder and the typed operation read.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import look_spec as spec  # noqa: E402

# Blender's simple expressions (blenlib expr_pylike_eval.cc builtin_ops): the functions a no-Python driver may call.
SIMPLE_FUNCTIONS = {"radians", "degrees", "abs", "fabs", "floor", "ceil", "trunc", "round", "int", "sin", "cos", "tan",
                    "asin", "acos", "atan", "atan2", "exp", "log", "sqrt", "pow", "fmod", "lerp", "clamp",
                    "smoothstep"}


def clamp(x, low, high):
    return min(max(x, low), high)


class VignetteTests(unittest.TestCase):
    def test_centre_is_untouched(self):
        self.assertEqual(spec.natural_vignette(0.0, 0.0, 50.0, 36.0, 1.5), 1.0)

    def test_corner_of_a_full_frame_50mm(self):
        # 36 x 24 mm: the corner is 21.63 mm off axis; tan = 21.63 / 50, cos^4 = 1 / (1 + tan^2)^2.
        tangent = math.hypot(18.0, 12.0) / 50.0
        self.assertAlmostEqual(spec.natural_vignette(1.0, 1.0, 50.0, 36.0, 1.5), 1.0 / (1.0 + tangent ** 2) ** 2)

    def test_wider_lenses_darken_more(self):
        wide, long = (spec.natural_vignette(1.0, 1.0, focal, 36.0, 1.5) for focal in (18.0, 135.0))
        self.assertLess(wide, long)

    def test_amount_blends_toward_none(self):
        full = spec.natural_vignette(1.0, 0.0, 24.0, 36.0, 1.5)
        self.assertAlmostEqual(spec.natural_vignette(1.0, 0.0, 24.0, 36.0, 1.5, amount=0.5), 1.0 - 0.5 * (1.0 - full))
        self.assertEqual(spec.natural_vignette(1.0, 0.0, 24.0, 36.0, 1.5, amount=0.0), 1.0)


class BreathingTests(unittest.TestCase):
    def test_at_infinity_the_lens_is_its_own_focal_length(self):
        self.assertAlmostEqual(spec.breathing_lens(50.0, 0.3, 1e12), 50.0, places=6)

    def test_closer_focus_grows_or_shrinks_with_the_sign(self):
        self.assertGreater(spec.breathing_lens(50.0, 0.3, 500.0), 50.0)
        self.assertLess(spec.breathing_lens(50.0, -0.3, 500.0), 50.0)
        # Thin lens: magnification f / (d - f) = 50 / 450 at 0.5 m.
        self.assertAlmostEqual(spec.breathing_lens(50.0, 0.3, 500.0), 50.0 * (1.0 + 0.3 * 50.0 / 450.0))

    def test_the_driver_is_a_simple_expression_that_matches(self):
        expression = spec.breathing_expression(50.0, 0.3, mm_per_unit=1000.0)
        names = set(re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expression))
        self.assertEqual(names - SIMPLE_FUNCTIONS, {spec.FOCUS_VARIABLE})
        for metres in (0.3, 0.5, 2.0, 100.0):
            value = eval(expression, {"__builtins__": {}}, {"clamp": clamp, spec.FOCUS_VARIABLE: metres})
            self.assertAlmostEqual(value, spec.breathing_lens(50.0, 0.3, metres * 1000.0), places=6)

    def test_focus_inside_the_focal_length_stays_finite(self):
        expression = spec.breathing_expression(50.0, 0.3, mm_per_unit=1000.0)
        value = eval(expression, {"__builtins__": {}}, {"clamp": clamp, spec.FOCUS_VARIABLE: 0.01})
        self.assertTrue(math.isfinite(value))

    def test_scene_units_scale(self):
        # A scene in centimetres: focus distances are read in its units.
        expression = spec.breathing_expression(50.0, 0.3, mm_per_unit=10.0)
        value = eval(expression, {"__builtins__": {}}, {"clamp": clamp, spec.FOCUS_VARIABLE: 50.0})
        self.assertAlmostEqual(value, spec.breathing_lens(50.0, 0.3, 500.0), places=6)


class ExposureTests(unittest.TestCase):
    def test_one_stop_each_way(self):
        reference = {"f_stop": 4.0, "shutter": 1 / 50, "iso": 400}
        self.assertAlmostEqual(spec.stops(reference, {"f_stop": 4.0, "shutter": 1 / 100, "iso": 400}), -1.0)
        self.assertAlmostEqual(spec.stops(reference, {"f_stop": 4.0, "shutter": 1 / 50, "iso": 800}), 1.0)
        self.assertAlmostEqual(spec.stops(reference, {"f_stop": 4.0 * math.sqrt(2), "shutter": 1 / 50, "iso": 400}),
                               -1.0)
        self.assertEqual(spec.stops(reference, dict(reference)), 0.0)

    def test_the_setting_takes_the_cameras_f_stop_unless_it_names_one(self):
        reference = {"f_stop": 8.0, "shutter": 1 / 100, "iso": 100}
        # The camera at f/4 with the reference's shutter and ISO: two stops brighter.
        self.assertAlmostEqual(spec.exposure_stops({"reference": reference, "setting": {"shutter": 1 / 100,
                                                                                        "iso": 100}}, 4.0), 2.0)
        # A named f-stop wins over the camera's; ISO 400 at f/8 is two stops brighter too.
        self.assertAlmostEqual(spec.exposure_stops({"reference": reference, "setting": {"f_stop": 8.0,
                                                    "shutter": 1 / 100, "iso": 400}}, 1.4), 2.0)
        # Every unit agrees with stops(): half the shutter time and double the ISO cancel.
        self.assertAlmostEqual(spec.exposure_stops({"reference": reference, "setting": {"shutter": 1 / 200,
                                                                                        "iso": 200}}, 8.0), 0.0)

    def test_exposure_refusals_name_the_fix(self):
        good = {"f_stop": 4.0, "shutter": 1 / 50, "iso": 400}
        bad = {"not_an_object": [], "no_reference": {"setting": good}, "no_setting": {"reference": good},
               "unknown_key": {"reference": good, "setting": good, "aperture": 2},
               "reference_without_iso": {"reference": {"f_stop": 4.0, "shutter": 0.02}, "setting": good},
               "setting_without_shutter": {"reference": good, "setting": {"iso": 100}},
               "unknown_setting": {"reference": good, "setting": dict(good, gain=3)},
               "zero_shutter": {"reference": good, "setting": dict(good, shutter=0)},
               "iso_as_text": {"reference": good, "setting": dict(good, iso="400")},
               "boolean": {"reference": good, "setting": dict(good, f_stop=True)},
               "f_stop_out_of_range": {"reference": dict(good, f_stop=500.0), "setting": good}}
        for name, value in bad.items():
            with self.subTest(name), self.assertRaises(spec.LookError) as caught:
                spec.exposure_stops(value, 4.0)
            self.assertTrue(str(caught.exception))
        # The camera's own f-stop is checked like a named one.
        with self.assertRaises(spec.LookError):
            spec.exposure_stops({"reference": good, "setting": {"shutter": 0.02, "iso": 400}}, 0.0)


class GrainTests(unittest.TestCase):
    def test_grain_is_a_share_of_the_frame_not_of_pixels(self):
        self.assertAlmostEqual(spec.grain_pixels(0.001, 1080), 1.08)
        self.assertAlmostEqual(spec.grain_pixels(0.001, 2160) / spec.grain_pixels(0.001, 1080), 2.0)


class StageTests(unittest.TestCase):
    def test_every_preset_sets_only_known_values_within_range(self):
        for name, preset in spec.PRESETS.items():
            for stage, values in preset["stages"].items():
                self.assertIn(stage, spec.STAGES, name)
                for key, value in values.items():
                    low, high, _default = spec.STAGES[stage][key]
                    self.assertTrue(low <= value <= high, "%s %s.%s=%r" % (name, stage, key, value))
            for key, value in preset.get("camera", {}).items():
                low, high = spec.CAMERA[key]
                self.assertTrue(low <= value <= high, "%s camera.%s" % (name, key))

    def test_stage_defaults_are_within_range_and_the_order_is_physical(self):
        for stage, params in spec.STAGES.items():
            for key, (low, high, default) in params.items():
                self.assertTrue(low <= default <= high, "%s.%s" % (stage, key))
        self.assertEqual(spec.ORDER, ("lens", "vignette", "glare", "halation", "film", "grain"))
        self.assertEqual(set(spec.ORDER), set(spec.STAGES))

    def test_presets(self):
        self.assertGreaterEqual(len(spec.PRESETS), 6)
        self.assertIn("CLEAN", spec.PRESETS)
        self.assertEqual(spec.PRESETS["CLEAN"]["stages"], {})
        anamorphic = spec.PRESETS["ANAMORPHIC"]
        self.assertGreater(anamorphic["camera"]["aperture_ratio"], 1.0)   # Oval bokeh.

    def test_check_overrides(self):
        merged = spec.resolve("VINTAGE", {"grain": {"amount": 0.1}})
        self.assertEqual(merged["grain"]["amount"], 0.1)
        for bad, words in (({"grain": {"amount": 9}}, "grain.amount"), ({"wobble": {}}, "stages are"),
                           ({"film": {"curve": 1}}, "film takes")):
            with self.subTest(words=words), self.assertRaisesRegex(spec.LookError, words):
                spec.resolve("CLEAN", bad)
        with self.assertRaisesRegex(spec.LookError, "preset must be one of"):
            spec.resolve("SPACEY", {})


if __name__ == "__main__":
    unittest.main()
