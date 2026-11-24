# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rule-based scattering's rules, layers and biome files (``scatter_spec``), without Blender.

Contract: doc/angelo/scatter.rst. Each rule is a factor from 0 to 1 on a layer's density, built as geometry nodes
(``scatter_nodes``) with the same Hermite smoothstep as the Map Range node's Smooth Step; the functions here are that
math in NumPy, which the rule audit and these tests use. A biome file (``angelo-biome/1``) holds layers whose assets are
Library items (an id or a search) or objects by name, never a scene handle.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import read_only  # noqa: E402
from angelo_agent import scatter_spec as spec  # noqa: E402

H = "scn:" + "a" * 32 + ":1"


def layer(**overrides):
    base = {"name": "Pines", "assets": [{"object": H}], "density": 0.05}
    base.update(overrides)
    return base


class RuleMathTests(unittest.TestCase):
    def test_band_is_hard_without_softness_and_hermite_with_it(self):
        x = np.array([9.9, 10.0, 20.0, 30.0, 30.1])
        self.assertEqual(list(spec.band(x, 10.0, 30.0, 0.0)), [0.0, 1.0, 1.0, 1.0, 0.0])
        soft = spec.band(np.array([5.0, 7.5, 10.0, 32.5]), 10.0, 30.0, 5.0)
        self.assertAlmostEqual(soft[0], 0.0)
        self.assertAlmostEqual(soft[1], 0.5)   # Halfway up the soft edge.
        self.assertAlmostEqual(soft[2], 1.0)
        self.assertAlmostEqual(soft[3], 0.5)

    def test_slope_in_degrees_from_normals(self):
        normals = np.array([[0, 0, 1], [1, 0, 0], [0, -1, 1] / np.sqrt(2), [0, 0, -1]], dtype=np.float64)
        self.assertTrue(np.allclose(spec.slope_degrees(normals), [0.0, 90.0, 45.0, 180.0]))

    def test_slope_rule(self):
        normals = np.array([[0, 0, 1], [math.sin(math.radians(30)), 0, math.cos(math.radians(30))], [1, 0, 0]])
        factor = spec.factor({"slope": {"min": 0, "max": 35}}, normals=normals, positions=np.zeros((3, 3)))
        self.assertEqual(list(factor), [1.0, 1.0, 0.0])

    def test_height_rule_absolute_and_relative(self):
        positions = np.array([[0, 0, 0.0], [0, 0, 50.0], [0, 0, 100.0]])
        up = np.tile([0.0, 0.0, 1.0], (3, 1))
        self.assertEqual(list(spec.factor({"height": {"min": 40, "max": 200}}, normals=up, positions=positions)),
                         [0.0, 1.0, 1.0])
        relative = {"height": {"min": 0.0, "max": 0.6, "relative": True}}
        self.assertEqual(list(spec.factor(relative, normals=up, positions=positions)), [1.0, 1.0, 0.0])

    def test_distance_keep_away_and_near(self):
        distances = np.array([0.5, 2.0, 3.0, 10.0])
        keep = spec.distance_factor(distances, {"keep_away": 2.0, "falloff": 2.0})
        self.assertEqual(keep[0], 0.0)
        self.assertAlmostEqual(keep[2], 0.5)
        self.assertEqual(keep[3], 1.0)
        near = spec.distance_factor(distances, {"near": 2.0, "falloff": 0.0})
        self.assertEqual(list(near), [1.0, 1.0, 0.0, 0.0])

    def test_mask_with_invert(self):
        values = np.array([0.0, 0.25, 1.0])
        self.assertEqual(list(spec.mask_factor(values, {"name": "Paths"})), [0.0, 0.25, 1.0])
        self.assertEqual(list(spec.mask_factor(values, {"name": "Paths", "invert": True})), [1.0, 0.75, 0.0])

    def test_rules_multiply(self):
        normals = np.array([[0, 0, 1], [1, 0, 0]], dtype=np.float64)
        positions = np.array([[0, 0, 100.0], [0, 0, 100.0]])
        rules = {"slope": {"min": 0, "max": 35}, "height": {"min": 0, "max": 50}}
        self.assertEqual(list(spec.factor(rules, normals=normals, positions=positions)), [0.0, 0.0])


class LayerTests(unittest.TestCase):
    def test_defaults(self):
        checked = spec.check_layer(layer())
        self.assertEqual(checked["spacing"], spec.DEFAULT_SPACING)
        self.assertEqual(checked["transform"], read_only.thawed(spec.DEFAULT_TRANSFORM))
        self.assertEqual(checked["rules"], {})
        self.assertEqual(checked["assets"], [{"object": H, "weight": 1.0}])

    def test_refusals_name_the_field_and_the_fix(self):
        cases = [
            (layer(density=0), "density"),
            (layer(density=spec.MAX_DENSITY * 2), "density"),
            (layer(assets=[]), "assets"),
            (layer(assets=[{"object": H, "library_id": "x"}]), "exactly one of"),
            (layer(rules={"slope": {"min": 50, "max": 10}}), "slope"),
            (layer(rules={"wind": {}}), "rules take"),
            (layer(transform={"scale": [2.0, 1.0]}), "scale"),
            (layer(transform={"align": 1.5}), "align"),
            (layer(name=""), "name"),
        ]
        for value, words in cases:
            with self.subTest(words=words), self.assertRaisesRegex(spec.ScatterError, words):
                spec.check_layer(value)

    def test_collision_radius_from_footprint_scale_and_spacing(self):
        # A 4 x 2 footprint: half its diagonal in the ground plane, times the largest scale and the spacing.
        radius = spec.collision_radius((4.0, 2.0, 9.0), scale_max=1.5, spacing=1.0)
        self.assertAlmostEqual(radius, math.hypot(2.0, 1.0) * 1.5)

    def test_layers_are_capped(self):
        with self.assertRaisesRegex(spec.ScatterError, "layers"):
            spec.check_layers([layer(name="L%d" % i) for i in range(spec.MAX_LAYERS + 1)])
        with self.assertRaisesRegex(spec.ScatterError, "twice"):
            spec.check_layers([layer(), layer()])


class BiomeTests(unittest.TestCase):
    def biome(self):
        return {"schema": spec.BIOME_SCHEMA, "name": "Conifer slopes", "description": "Pines on steep ground",
                "layers": [layer(assets=[{"query": "pine tree", "kind": "model"}],
                                 rules={"slope": {"min": 10, "max": 40, "soft": 5}}),
                           layer(name="Rocks", assets=[{"library_id": "toonlab:rock-07"}], density=0.2)]}

    def test_round_trip(self):
        text = spec.dump_biome(self.biome())
        self.assertEqual(spec.load_biome(text), spec.load_biome(spec.dump_biome(spec.load_biome(text))))
        self.assertEqual(json.loads(text)["schema"], "angelo-biome/1")

    def test_a_file_never_holds_a_scene_handle(self):
        biome = self.biome()
        biome["layers"][0]["assets"] = [{"object": H}]
        with self.assertRaisesRegex(spec.ScatterError, "object_name"):
            spec.load_biome(json.dumps(biome))
        biome["layers"][0]["assets"] = [{"object_name": "My Pine"}]
        self.assertEqual(spec.load_biome(json.dumps(biome))["layers"][0]["assets"][0]["object_name"], "My Pine")

    def test_unknown_schema_and_size_are_refused(self):
        biome = self.biome()
        biome["schema"] = "angelo-biome/9"
        with self.assertRaisesRegex(spec.ScatterError, "angelo-biome/1"):
            spec.load_biome(json.dumps(biome))
        with self.assertRaisesRegex(spec.ScatterError, "KiB"):
            spec.load_biome(" " * (spec.MAX_BIOME_BYTES + 1))

    def test_shipped_presets_load_and_use_no_scene_objects(self):
        names = set()
        for path in sorted(spec.PRESET_DIR.glob("*.angelo-biome.json")):
            biome = spec.load_biome(path.read_text(encoding="utf-8"))
            names.add(biome["name"])
            for item in biome["layers"]:
                for asset in item["assets"]:
                    self.assertTrue({"query", "library_id"} & set(asset), path.name)
            self.assertLess(path.stat().st_size, spec.MAX_BIOME_BYTES)
        self.assertGreaterEqual(len(names), 6)


if __name__ == "__main__":
    unittest.main()
