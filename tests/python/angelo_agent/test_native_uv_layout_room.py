# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UV Layout never finishes silently at near-zero coverage (doc/angelo/uv_layout.rst, "Too little room").

Before packing, ``uv_layout_math.room`` predicts the most of the tile the islands can cover when each gets its padding
(Pack Islands' Fraction margin on every side): from their boxes and true areas, the scale at which the padded boxes
fill the tile, then the islands' area at that scale. It is a ceiling a real pack stays under (the held-out benchmark,
2026-11-18: 0.33 against 0.30 measured on a 449-island kit, 0.017 against 0.013 on a 3,000-triangle soup, 0 against 0
on a 10,731-island scan). Below ``ROOM_FLOOR`` (1/16: less than a texture half the size on each side would give the
islands whole) the layout says so, in the person's language and in the agent's receipt, with the concrete fixes: the
most mip levels kept, or the smallest texture, that would leave them the floor. ``test_blender_uv_layout`` runs it on
the held-out soup and scan and on a kit that must not warn.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import uv_layout_math as uvm  # noqa: E402


def square(x, y, side):
    return [(x, y), (x + side, y), (x + side, y + side), (x, y + side)]


def islands(count, side, group=0):
    """``count`` square islands of ``side`` (any scale: the pack scales them to fill), with their true areas."""
    return [(square(k * side * 2, 0, side), side * side, group) for k in range(count)]


class Room(unittest.TestCase):
    def test_the_floor_is_a_sixteenth(self):
        self.assertEqual(uvm.ROOM_FLOOR, 1 / 16)

    def test_a_few_islands_have_room(self):
        room = uvm.room(islands(20, 0.1), 2048, 4)
        self.assertFalse(room["too_little"])
        self.assertGreater(room["share"], 0.5)
        self.assertEqual(room["floor"], uvm.ROOM_FLOOR)

    def test_thousands_of_islands_at_four_mips_have_too_little(self):
        room = uvm.room(islands(3000, 0.01), 2048, 4)
        self.assertTrue(room["too_little"])
        self.assertLess(room["share"], uvm.ROOM_FLOOR)

    def test_padding_that_alone_fills_the_tile_leaves_nothing(self):
        # 10,731 islands with 16 px around each on 2048 px: 4 n m^2 > 1, so no scale fits them all.
        room = uvm.room(islands(10731, 0.01), 2048, 4)
        self.assertEqual(room["share"], 0.0)
        self.assertTrue(room["too_little"])

    def test_the_share_is_a_ceiling_scale_free(self):
        a, b = uvm.room(islands(500, 0.01), 2048, 4), uvm.room(islands(500, 3.0), 2048, 4)
        self.assertAlmostEqual(a["share"], b["share"], places=6)   # The pack scales the islands: only shape counts.

    def test_the_fixes_are_concrete_and_each_clears_the_floor(self):
        room = uvm.room(islands(3000, 0.01), 2048, 4)
        mips = room["fits_with_mips"]
        self.assertIsNotNone(mips)
        self.assertLess(mips, 4)
        self.assertGreaterEqual(uvm.room(islands(3000, 0.01), 2048, mips)["share"], uvm.ROOM_FLOOR)
        if mips + 1 < 4:
            self.assertLess(uvm.room(islands(3000, 0.01), 2048, mips + 1)["share"], uvm.ROOM_FLOOR)   # The most that fits.
        size = room["fits_with_texture_size"]
        self.assertIn(size, (4096, 8192, 16384, 32768))
        self.assertGreaterEqual(uvm.room(islands(3000, 0.01), size, 4)["share"], uvm.ROOM_FLOOR)

    def test_no_fix_is_named_when_none_fits(self):
        # 8 mip levels keep 256 px of padding on every side: 10,731 islands fill even a 32768 px texture with padding
        # alone (4 n (256 / 32768)^2 > 1), so no larger texture is named.
        room = uvm.room(islands(10731, 0.01), 256, 8)
        self.assertTrue(room["too_little"])
        self.assertIsNone(room["fits_with_texture_size"])

    def test_each_group_gets_its_own_tile_and_the_worst_decides(self):
        roomy = islands(20, 0.1, group=0)
        crowded = [(points, area, 1) for points, area, _ in islands(3000, 0.01)]
        self.assertTrue(uvm.room(roomy + crowded, 2048, 4)["too_little"])
        self.assertFalse(uvm.room(roomy, 2048, 4)["too_little"])

    def test_no_islands_is_no_warning(self):
        self.assertFalse(uvm.room([], 2048, 4)["too_little"])

    def test_random_shapes_stay_within_the_unit_share(self):
        rng = random.Random(7)
        shapes = [([(rng.random(), rng.random()) for _ in range(5)], rng.random() * 0.01, 0) for _ in range(300)]
        share = uvm.room(shapes, 2048, 2)["share"]
        self.assertGreaterEqual(share, 0.0)
        self.assertLessEqual(share, 1.0)


class AdaptiveMargin(unittest.TestCase):
    """The coordinator's decision (2026-11-18): a margin nobody set keeps 4 mip levels wherever the islands have room
    (the predicted ceiling clears ROOM_FLOOR), and otherwise drops to the most mip levels that leave them room, saying
    so; a margin the person or the agent set is never lowered (the too-little-room warning applies instead)."""

    def test_an_automatic_margin_drops_to_the_most_mips_that_fit_and_says_so(self):
        crowded = islands(3000, 0.01)
        room, lowered = uvm.fit_margin(crowded, 2048, 4, automatic=True)
        expected = uvm.room(crowded, 2048, 4)
        self.assertEqual(lowered["from"], 4)
        self.assertEqual(lowered["to"], expected["fits_with_mips"])
        self.assertEqual(lowered["keep_with_texture_size"], expected["fits_with_texture_size"])
        self.assertFalse(room["too_little"])
        self.assertEqual(room, uvm.room(crowded, 2048, lowered["to"]))

    def test_a_margin_someone_set_is_never_lowered(self):
        crowded = islands(3000, 0.01)
        room, lowered = uvm.fit_margin(crowded, 2048, 4, automatic=False)
        self.assertIsNone(lowered)
        self.assertTrue(room["too_little"])

    def test_room_enough_keeps_the_margin(self):
        room, lowered = uvm.fit_margin(islands(20, 0.1), 2048, 4, automatic=True)
        self.assertIsNone(lowered)
        self.assertFalse(room["too_little"])

    def test_when_no_margin_fits_it_is_kept_and_the_warning_stands(self):
        # n equal islands keep (1 - 2 m sqrt n)^2 of the tile: on 256 px even 0 mip levels (m = 1/256) leave 10,731
        # islands 3.6%, under the floor.
        crowded = islands(10731, 0.01)
        self.assertIsNone(uvm.room(crowded, 256, 8)["fits_with_mips"])
        room, lowered = uvm.fit_margin(crowded, 256, 8, automatic=True)
        self.assertIsNone(lowered)
        self.assertTrue(room["too_little"])


class MarginIsSetOnlyWhenGiven(unittest.TestCase):
    def test_the_mapping_passes_mips_only_when_given(self):
        from angelo_agent import uv_layout_ops
        self.assertNotIn("mips", uv_layout_ops.to_layout({}))
        self.assertEqual(uv_layout_ops.to_layout({"mips": 2})["mips"], 2)

    def test_the_job_keeps_a_missing_margin_missing(self):
        from angelo_agent.job_spec import validate_job
        handle = "scn:" + "a" * 32 + ":1"
        request = validate_job("angelo_job_start", {"kind": "UV_LAYOUT", "target": handle})
        self.assertNotIn("mips", request["layout"])
        given = validate_job("angelo_job_start", {"kind": "UV_LAYOUT", "target": handle, "layout": {"mips": 3}})
        self.assertEqual(given["layout"]["mips"], 3)

    def test_the_dialog_sends_mips_only_when_the_person_set_it(self):
        ui = _source("uv_layout_ui.py")
        mips = ui[ui.index("    mips: bpy.props.IntProperty("):]
        self.assertIn('"SKIP_SAVE"', mips[:mips.index(")\n") + 1])   # A remembered value never counts as set.
        self.assertIn('is_property_set("mips")', ui)


def _source(name):
    return (ADDON / name).read_text(encoding="utf-8")


class Wiring(unittest.TestCase):
    def test_the_layout_predicts_room_before_packing_in_fill_mode(self):
        steps = ast.unparse(next(n for n in ast.parse(_source("uv_layout.py")).body
                                 if isinstance(n, ast.FunctionDef) and n.name == "steps"))
        # Through uvm.fit_margin, which predicts with uvm.room (the adaptive margin, 2026-11-18).
        self.assertIn("uvm.fit_margin(", steps)
        self.assertLess(steps.index("uvm.fit_margin("), steps.index("yield {"))   # The first pack's yield.
        receipt = _source("uv_layout.py")
        self.assertIn('"room": room', receipt[receipt.index("def _receipt("):])

    def test_the_agent_gets_the_room(self):
        self.assertIn('"room"', _source("job_uv_layout_spec.py")[_source("job_uv_layout_spec.py").index("def receipt("):])

    def test_the_layout_fits_the_margin_before_packing(self):
        steps = ast.unparse(next(n for n in ast.parse(_source("uv_layout.py")).body
                                 if isinstance(n, ast.FunctionDef) and n.name == "steps"))
        self.assertIn("uvm.fit_margin(", steps)
        self.assertLess(steps.index("uvm.fit_margin("), steps.index("yield {"))
        self.assertIn("spec['mips'] = lowered['to']", steps)   # The packs use the margin it chose.
        self.assertIn('"margin_lowered"', _source("uv_layout.py")[_source("uv_layout.py").index("def _receipt("):])
        spec = _source("job_uv_layout_spec.py")
        self.assertIn('"margin_lowered"', spec[spec.index("def receipt("):])

    def test_a_lowered_margin_is_said_in_their_language(self):
        ui = _source("uv_layout_ui.py")
        self.assertIn("MARGIN_LOWERED = n_(", ui)
        self.assertIn("iface_(MARGIN_LOWERED", ui)

    def test_the_person_is_told_in_their_language(self):
        ui = _source("uv_layout_ui.py")
        self.assertIn("TOO_LITTLE_ROOM = n_(", ui)
        self.assertIn("iface_(TOO_LITTLE_ROOM", ui)
        self.assertIn('self.report({"WARNING"}', ui)


if __name__ == "__main__":
    unittest.main()
