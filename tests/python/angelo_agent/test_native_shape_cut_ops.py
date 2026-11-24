# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's shape_cut operation as declared for the typed-operation pattern (shape_cut_ops), without Blender.

Covered: the declaration against the pattern and against angelo.shape_cut's own properties (wrapped_op_contract);
its operations are exactly Shape Cut's; and the checks on snapshots of every operation's outcome, the failures
included (nothing cut, a closed target opened, a base mesh changed, a missing cutter or slice piece). The snapshots
vary in counts and volumes so no check passes on one shape's numbers.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import shape_cut_ops as ops, shape_cut_spec as spec  # noqa: E402
import wrapped_op_contract as contract  # noqa: E402


def facts(volume, open_edges=0, booleans=0, children=(), base=(8, 12, 6, 12), evaluated=None):
    counts = dict(zip(("vertices", "edges", "faces", "triangles"), base))
    return {"base": counts, "evaluated": dict(zip(counts, evaluated)) if evaluated else dict(counts),
            "open_edges": open_edges, "volume": volume, "children": list(children),
            "modifiers": [["Cut %d" % i, "BOOLEAN"] for i in range(booleans)] + [["Bevel", "BEVEL"]]}


def outcome(operation, before_volume, after_volume, cutters=1, **after_extra):
    before = {"target": facts(before_volume, children=["Lamp"]), "created": []}
    after = {"target": facts(after_volume, booleans=cutters, children=["Lamp"] + ["Cutter.%d" % i for i in range(cutters)],
                             evaluated=(40, 70, 32, 64), **after_extra), "created": []}
    return {"operation": operation}, before, after


def failed(checks):
    return sorted(c["name"] for c in checks if not c["passed"])


class Declaration(unittest.TestCase):
    def test_the_guide_example_calls_the_operator_as_declared(self):
        (declaration,) = ops.WRAPPED_OPS
        contract.check_guide_examples(self, declaration)

    def test_against_the_pattern_and_the_operator(self):
        (declaration,) = ops.WRAPPED_OPS
        contract.check_declaration(self, "shape_cut_ops.py", declaration, "shape_cut_tool.py")
        self.assertEqual(declaration["kind"], "shape_cut")
        # The target object itself keeps everything but its modifier stack; cutters and the piece are creations.
        self.assertEqual(declaration["mutates"], {"modifiers"})
        # Only a slice makes the sibling piece, so only a slice's preview names a new object.
        self.assertEqual(declaration["creates_if"], {"object": ("operation", {"SLICE"})})
        self.assertEqual(set(declaration["levers"]), {"columns", "rows", "segments"})

    def test_the_shared_registry_registers_it(self):
        # The shared code registers what wrapped_op_modules lists (wrapped_ops, Part E); a declaration it refuses, or
        # one left unlisted, never reaches the agent. No job does booleans: until its measured triangle bound is set,
        # a cut runs in the turn.
        from angelo_agent import wrapped_ops
        contract.check_registered(self, ops.WRAPPED_OPS[0])   # As registered: frozen, never the module's dict.
        self.assertIsNone(wrapped_ops.admit("shape_cut", {"triangles": 12}, {"operation": "CUT"}))

    def test_operations_are_shape_cuts(self):
        self.assertEqual(tuple(ops.PARAMETERS["properties"]["operation"]["enum"]), spec.OPERATIONS)
        self.assertLessEqual(set(ops.GROWS) | set(ops.TWO_SOLIDS), set(spec.OPERATIONS))

    def test_the_menu_tile_is_a_member_of_the_model_group(self):
        # 2.1.0's + menu (coordinator ruling, 2026-11-15): modelling tools share the Model tile, never a tile of
        # their own, so the top level stays within its budget at 1280 x 800.
        from angelo_agent import chat_destinations as cd
        self.assertEqual(cd.PARENTS.get("shape_cut"), cd.MODEL_GROUP)
        self.assertNotIn("shape_cut", cd.TOP_LEVEL)

    def test_hand_only_outlines_stay_off_the_schema(self):
        # Polygon and freehand outlines come from a drawn path the properties do not hold.
        self.assertEqual(ops.PARAMETERS["properties"]["shape"]["enum"], ["BOX", "CIRCLE"])

    def test_bounds_come_from_measurement(self):
        (declaration,) = ops.WRAPPED_OPS
        self.assertIsNone(declaration["job_kind"])
        # One shared turn budget (wrapped_op_budget.TURN_BUDGET_S): never an operation's own. The bound is the
        # target's triangles, set from timed runs; until then it is None, never guessed.
        self.assertNotIn("budget_s", declaration)
        bound = declaration["input_bound"]
        self.assertTrue(bound is None or (set(bound) == {"triangles"} and type(bound["triangles"]) is int
                                          and bound["triangles"] > 0))


class Checks(unittest.TestCase):
    def test_each_operation_passes_on_its_own_outcome(self):
        cases = (("CUT", 1.0, 0.97, 1), ("INTERSECT", 0.012, 0.0004, 1), ("PANEL", 2.5, 2.49, 1),
                 ("INSET", 0.3, 0.29, 2), ("UNION", 1.0, 1.08, 1))
        for operation, old, new, cutters in cases:
            with self.subTest(operation=operation):
                self.assertEqual(failed(ops.shape_cut_checks(*outcome(operation, old, new, cutters))), [])

    def test_nothing_cut_fails(self):
        for operation in ("CUT", "UNION"):
            with self.subTest(operation=operation):
                self.assertIn("volume_" + ("grew" if operation == "UNION" else "shrank"),
                              failed(ops.shape_cut_checks(*outcome(operation, 0.5, 0.5))))

    def test_a_cut_that_adds_volume_fails(self):
        self.assertEqual(failed(ops.shape_cut_checks(*outcome("CUT", 1.0, 1.2))), ["volume_shrank"])

    def test_an_opened_closed_target_fails(self):
        self.assertEqual(failed(ops.shape_cut_checks(*outcome("CUT", 1.0, 0.9, open_edges=6))), ["stays_closed"])

    def test_open_targets_skip_volume(self):
        parameters, before, after = outcome("CUT", None, None)
        before["target"]["open_edges"] = after["target"]["open_edges"] = 40
        self.assertEqual([c["name"] for c in ops.shape_cut_checks(parameters, before, after)],
                         ["cutters_added", "base_mesh_unchanged", "booleans_added"])

    def test_inset_needs_both_solids(self):
        self.assertEqual(failed(ops.shape_cut_checks(*outcome("INSET", 0.3, 0.29, 1))),
                         ["booleans_added", "cutters_added"])

    def test_a_changed_base_mesh_fails(self):
        parameters, before, after = outcome("CUT", 1.0, 0.9)
        after["target"]["base"]["faces"] += 4
        self.assertEqual(failed(ops.shape_cut_checks(parameters, before, after)), ["base_mesh_unchanged"])

    def test_slice_needs_one_piece(self):
        parameters, before, after = outcome("SLICE", 1.0, 0.8)
        self.assertEqual(failed(ops.shape_cut_checks(parameters, before, after)), ["slice_piece"])
        after["created"] = [facts(0.2)]
        self.assertEqual(failed(ops.shape_cut_checks(parameters, before, after)), [])
        after["created"] = [facts(0.2), facts(0.1)]
        self.assertEqual(failed(ops.shape_cut_checks(parameters, before, after)), ["slice_piece"])

    def test_checks_never_write(self):
        args = outcome("CUT", 1.0, 0.9)
        frozen = copy.deepcopy(args)
        ops.shape_cut_checks(*args)
        ops.shape_cut_receipt(args[2])
        self.assertEqual(args, frozen)


class Receipt(unittest.TestCase):
    def test_counts_only(self):
        receipt = ops.shape_cut_receipt(outcome("CUT", 1.0, 0.123456789123, 3)[2])
        self.assertEqual(receipt, {"evaluated_triangles": 64, "open_edges": 0, "volume": 0.123456789, "booleans": 3})
        self.assertIsNone(ops.shape_cut_receipt({"target": facts(None, open_edges=3)})["volume"])


if __name__ == "__main__":
    unittest.main()
