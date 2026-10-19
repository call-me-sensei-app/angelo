# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reference pair or overlay with targets states OUTLINE's measurements beside its picture (no Blender).

Why: with free cameras and reference pairs a model compared the pictures many times a run and never asked for
compare OUTLINE's numbers; its side profile stayed about 5 mm off. The host now measures what it already can for
every pair that names targets, so the numbers reach the model whichever compare it asks for. A pair without targets is unchanged.
"""

import unittest

from test_native_view_outline import FRONT, disc, numpy, png_rows


@unittest.skipUnless(numpy is not None, "NumPy is not installed")
class PairFactsTests(unittest.TestCase):
    def setUp(self):
        self.size = 64
        self.reference = numpy.full((self.size, self.size, 4), 240, numpy.uint8)
        self.reference[disc(self.size, (30, 32), 18)] = (120, 90, 80, 255)
        self.drawn = numpy.full((self.size, self.size, 4), 13, numpy.uint8)
        self.drawn[..., 3] = 255

    def test_a_pair_with_targets_measures_as_an_outline_and_stays_side_by_side(self):
        from angelo_agent.view_compare import GAP, encoder
        target = disc(self.size, (34, 32), 18)
        paired, outlined = {"pixel_world": 0.01}, {"pixel_world": 0.01}
        rows = png_rows(encoder(self.reference, "PAIR", None, target, paired)(self.drawn.tobytes(), self.size, self.size))
        encoder(self.reference, "OUTLINE", None, target, outlined)(self.drawn.tobytes(), self.size, self.size)
        self.assertEqual(rows.shape[1], self.size * 2 + GAP)
        self.assertTrue(numpy.array_equal(rows[:, :self.size, :3], self.reference[..., :3]))   # Nothing drawn on it.
        self.assertEqual(paired, outlined)
        self.assertAlmostEqual(paired["centre_offset_px"][0], 4.0, delta=0.1)

    def test_an_overlay_with_targets_measures_too_and_draws_the_same(self):
        """A model may compare by OVERLAY, not PAIR: the record must reach it there too."""
        from angelo_agent.view_compare import encoder
        target = disc(self.size, (34, 32), 18)
        overlaid, outlined = {"pixel_world": 0.01}, {"pixel_world": 0.01}
        rows = png_rows(encoder(self.reference, "OVERLAY", None, target, overlaid)(
            self.drawn.tobytes(), self.size, self.size))
        plain = png_rows(encoder(self.reference, "OVERLAY")(self.drawn.tobytes(), self.size, self.size))
        encoder(self.reference, "OUTLINE", None, target, outlined)(self.drawn.tobytes(), self.size, self.size)
        self.assertTrue(numpy.array_equal(rows, plain))   # The picture is the overlay as before.
        self.assertEqual(overlaid, outlined)

    def test_a_pair_without_targets_measures_nothing(self):
        from angelo_agent.view_compare import encoder
        facts = {}
        encoder(self.reference, "PAIR")(self.drawn.tobytes(), self.size, self.size)
        self.assertEqual(facts, {})

    def test_the_note_explains_the_outline_field_whichever_picture_holds_it(self):
        from angelo_agent.view_compare import note
        pair = {"pair": {}, "item": dict(FRONT)}
        self.assertNotIn("offset_world", note([pair]))
        self.assertIn("offset_world", note([dict(pair, outline={})]))
        self.assertIn("attached image on the left", note([dict(pair, outline={})]))


if __name__ == "__main__":
    unittest.main()
