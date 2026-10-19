# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""curve_points and object_convert: schemas, references, write sets and derived checks.

Dependency-free. Staged points, conversion, collections, commit and Undo are
covered by test_blender_curves.py in the compiled app.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.action_catalog import (affected_references, resource_dependencies, validate_operation,  # noqa: E402
                                         written_references)
from angelo_agent.action_schemas import CREATE_KINDS  # noqa: E402
from angelo_agent.derived_checks import derive_checks  # noqa: E402
from angelo_agent.plan_schema import DEFERRED_ACTIONS  # noqa: E402

OBJ = "scn:" + "a" * 32 + ":7"
SOURCE = "scn:" + "a" * 32 + ":9"


def op(kind, target=OBJ, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


class CurvePointTests(unittest.TestCase):
    def test_canonical_and_refused(self):
        self.assertIn("curve_points", DEFERRED_ACTIONS)
        canonical = validate_operation(op("curve_points", points=[[0, 0, 0], [1, 0, 0]]))["parameters"]
        self.assertEqual(canonical, {"points": [[0, 0, 0], [1, 0, 0]], "spline_type": "BEZIER", "cyclic": False,
                                     "handle_type": "AUTO", "dimensions": None})
        self.assertEqual(resource_dependencies(op("curve_points", points=[[0, 0, 0], [1, 0, 0]]))["mutates"],
                         ["object", "curve"])
        # The stated points are the curve's geometry: one spline of exactly those points,
        # flattened to Z 0 when the operation makes the curve 2D (Blender flattens on every 2D assignment).
        self.assertEqual(derive_checks([validate_operation(op("curve_points", points=[[0, 0, 0], [1, 0, 2]]))]),
                         [{"kind": "curve_counts", "target": op("curve_points")["target"],
                           "points": {"minimum": 2, "maximum": 2}, "curves": {"minimum": 1, "maximum": 1},
                           "positions": [[0., 0., 0.], [1., 0., 2.]]}])
        flat = derive_checks([validate_operation(op("curve_points", points=[[0, 0, 0], [1, 0, 2]], dimensions="2D"))])
        self.assertEqual(flat[0]["positions"], [[0., 0., 0.], [1., 0., 0.]])
        for parameters in ({"points": [[0, 0, 0]]}, {"points": [[0, 0, 0]] * 257}, {"points": [[0, 0]] * 2},
                           {"points": [[0, 0, 0], [1, 0, 0]], "spline_type": "NURBS", "cyclic": True},
                           {"points": [[0, 0, 0], [1, 0, 0]], "handle_type": "FREE"}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_operation(op("curve_points", **parameters))


class ConvertTests(unittest.TestCase):
    def test_create_kind_with_its_source(self):
        self.assertIn("object_convert", DEFERRED_ACTIONS)
        self.assertIn("object_convert", CREATE_KINDS)
        kept = validate_operation(op("object_convert", None, source=SOURCE, alias="solid"))
        self.assertEqual(affected_references(kept), (SOURCE,))
        self.assertEqual(written_references(kept), ())  # Read only while the source is kept.
        replaced = validate_operation(op("object_convert", None, source=SOURCE, alias="solid", keep_original=False))
        self.assertEqual(written_references(replaced), (SOURCE,))
        self.assertEqual(resource_dependencies(kept)["creates"], ["object", "mesh"])
        for operation in (op("object_convert", OBJ, source=SOURCE, alias="solid"),
                          op("object_convert", None, source="$solid", alias="solid"),
                          op("object_convert", None, source=SOURCE)):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                validate_operation(operation)

    def test_derived_checks(self):
        derived = derive_checks([validate_operation(op("object_rename", SOURCE, name="Doomed")),
                                 validate_operation(op("object_convert", None, source=SOURCE, alias="solid",
                                                       keep_original=False))])
        self.assertEqual(derived, [{"kind": "object_type", "target": "$solid", "expected": "MESH"},
                                   {"kind": "object_exists", "target": SOURCE, "expected": False}])


if __name__ == "__main__":
    unittest.main()
