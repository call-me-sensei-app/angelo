# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's uv_layout operation as declared for the typed-operation pattern (uv_layout_ops), without Blender.

Covered: the declaration against the pattern and against angelo.uv_layout's own properties (wrapped_op_contract);
the checks on laid-out UVs (every face inside one whole UDIM tile, geometry unchanged, a UV map present) with the
failures included (a face across a tile border, below zero, past the tenth column, changed counts); and the receipt's
tiles and efficiency. Layouts vary (one tile, several rows, rotated quads and triangles) so no check fits one layout.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import uv_layout_ops as ops  # noqa: E402
import wrapped_op_contract as contract  # noqa: E402


def facts(uv_maps=("UVMap",), base=(24, 36, 14, 28)):
    counts = dict(zip(("vertices", "edges", "faces", "triangles"), base))
    return {"base": counts, "evaluated": dict(counts), "open_edges": 0, "volume": 1.0, "children": [],
            "modifiers": [], "uv_maps": list(uv_maps)}


def square(u, v, side, lift=0.0):
    return [(u, v + lift), (u + side, v), (u + side, v + side), (u, v + side)]


SPREAD = [square(0.1, 0.1, 0.5), [(1.2, 0.1), (1.9, 0.3), (1.4, 0.8)], square(0.3, 1.3, 0.4, 0.05),
          square(2.05, 0.05, 0.9)]   # Tiles 1001, 1002, 1011, 1003.


def outcome(uvs, before_maps=(), after_base=(24, 36, 14, 28)):
    return ({}, {"target": facts(before_maps), "created": []},
            {"target": facts(base=after_base), "created": [], "uvs": uvs})


def failed(checks):
    return sorted(c["name"] for c in checks if not c["passed"])


class Declaration(unittest.TestCase):
    def test_the_packer_is_quality_unless_the_person_asks_for_speed(self):
        packer = ops.PARAMETERS["properties"]["packer"]
        self.assertEqual(packer["enum"], ["QUALITY", "FAST"])
        self.assertEqual(packer["default"], "QUALITY")
        self.assertIn("asks for speed", packer["description"])

    def test_the_menu_tile_is_a_member_of_the_model_group(self):
        # 2.1.0's + menu (coordinator ruling, 2026-11-15): modelling tools share the Model tile, never a tile of
        # their own, so the top level stays within its budget at 1280 x 800.
        from angelo_agent import chat_destinations as cd
        self.assertEqual(cd.PARENTS.get("uv_layout"), cd.MODEL_GROUP)
        self.assertNotIn("uv_layout", cd.TOP_LEVEL)

    def test_the_guide_example_calls_the_operator_as_declared(self):
        (declaration,) = ops.WRAPPED_OPS
        contract.check_guide_examples(self, declaration)

    def test_against_the_pattern_and_the_operator(self):
        (declaration,) = ops.WRAPPED_OPS
        contract.check_declaration(self, "uv_layout_ops.py", declaration, "uv_layout_ui.py")
        self.assertEqual((declaration["kind"], declaration["mutates"], declaration["creates"]),
                         ("uv_layout", {"mesh"}, None))
        self.assertEqual(declaration["levers"], ("search",))   # Fewer packing trials per tile: less work.

    def test_the_shared_registry_registers_it(self):
        # The shared code registers what wrapped_op_modules lists (wrapped_ops, Part E); a declaration it refuses, or
        # one left unlisted, never reaches the agent. QUALITY goes to the UV_LAYOUT job at any size (job_if); FAST
        # runs in the turn until its measured bound is set.
        from angelo_agent import wrapped_ops
        from angelo_agent.refusal_codes import RefusalError
        contract.check_registered(self, ops.WRAPPED_OPS[0])   # As registered: frozen, never the module's dict.
        with self.assertRaises(RefusalError) as refused:
            wrapped_ops.admit("uv_layout", {"faces": 12}, {"packer": "QUALITY"})
        self.assertEqual((refused.exception.code, refused.exception.details["limit"],
                          refused.exception.details["job_kind"]), ("LIMIT_EXCEEDED", "turn_budget", "UV_LAYOUT"))
        self.assertIsNone(wrapped_ops.admit("uv_layout", {"faces": 12}, {"packer": "FAST"}))

    def test_search_is_here(self):
        self.assertIn("search", ops.PARAMETERS["properties"])

    def test_texture_sizes_are_the_operators(self):
        self.assertEqual(ops.PARAMETERS["properties"]["texture_size"]["enum"],
                         [str(2 ** p) for p in range(8, 14)])


class Checks(unittest.TestCase):
    def test_laid_out_faces_pass(self):
        for uvs in (SPREAD, [square(0.0, 0.0, 1.0)], [[(0.25, 0.25), (0.75, 0.25), (0.5, 0.75)]]):
            with self.subTest(faces=len(uvs)):
                self.assertEqual(failed(ops.uv_layout_checks(*outcome(uvs))), [])

    def test_faces_outside_whole_tiles_fail(self):
        for bad in (square(0.8, 0.1, 0.4), square(-0.2, 0.1, 0.1), square(10.2, 0.1, 0.5), square(0.1, 0.9, 0.2)):
            with self.subTest(face=bad):
                self.assertEqual(failed(ops.uv_layout_checks(*outcome(SPREAD + [bad]))), ["faces_in_whole_tiles"])

    def test_geometry_must_not_change(self):
        self.assertEqual(failed(ops.uv_layout_checks(*outcome(SPREAD, after_base=(24, 36, 15, 30)))),
                         ["geometry_unchanged"])

    def test_no_uvs_fails(self):
        parameters, before, after = outcome([])
        after["target"]["uv_maps"] = []
        self.assertEqual(failed(ops.uv_layout_checks(parameters, before, after)), ["has_uv_map"])

    def test_checks_never_write(self):
        args = outcome(SPREAD)
        frozen = copy.deepcopy(args)
        ops.uv_layout_checks(*args)
        ops.uv_layout_receipt(args[2])
        self.assertEqual(args, frozen)


class Receipt(unittest.TestCase):
    def test_tiles_and_efficiency(self):
        receipt = ops.uv_layout_receipt(outcome(SPREAD)[2])
        self.assertEqual(receipt["tiles"], [1001, 1002, 1003, 1011])
        area = 0.25 + 0.5 * abs((1.9 - 1.2) * (0.8 - 0.1) - (1.4 - 1.2) * (0.3 - 0.1)) + (0.16 - 0.4 * 0.05 / 2) + 0.81
        self.assertAlmostEqual(receipt["efficiency"], round(area / 4, 4), places=4)
        self.assertEqual(receipt["faces"], 4)

    def test_empty(self):
        self.assertEqual(ops.uv_layout_receipt(outcome([])[2]), {"faces": 0, "tiles": [], "efficiency": 0.0})


if __name__ == "__main__":
    unittest.main()
