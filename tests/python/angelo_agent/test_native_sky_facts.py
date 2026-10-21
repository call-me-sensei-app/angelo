# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sky Texture facts both runtimes read (agent_guidance.BLENDER_API_FACTS) agree with the host's own readings.

The text states sun_rotation's frame and how the two scattering skies differ in colour, so a model can set a sky's
sun and choose its type before it looks. Those statements must be what world_light computes (the bearing a result
reports) and what world_light_table measured in Blender 5.2, never an estimate; and they stay facts, not advice.
"""

import math
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import world_light, world_light_table  # noqa: E402
from angelo_agent.agent_guidance import BLENDER_API_FACTS  # noqa: E402


class Node:
    def __init__(self, rotation, elevation=0.0, sky_type="MULTIPLE_SCATTERING"):
        self.sun_rotation, self.sun_elevation, self.sky_type = rotation, elevation, sky_type


def flat(text):
    return " ".join(text.split())


class SkyFactsTests(unittest.TestCase):
    def test_the_stated_frame_is_the_bearing_a_result_reports(self):
        text = flat(BLENDER_API_FACTS)
        self.assertIn("sun_rotation is the sun's bearing seen from above, clockwise from +Y (0 toward +Y, pi/2 toward "
                      "+X)", text)
        # The same frame, in the same words, as every result's ``directions``.
        self.assertTrue(world_light.DIRECTIONS.startswith("toward the sun: bearing in degrees clockwise from +Y seen "
                                                          "from above (+X is 90)"))
        for rotation, bearing in ((0.0, 0.0), (math.pi / 2, 90.0), (math.pi, 180.0), (-math.pi / 2, 270.0)):
            with self.subTest(rotation=rotation):
                found = world_light.angles(world_light.sky_direction(Node(rotation, elevation=0.2)))
                self.assertAlmostEqual(found[0], bearing, places=1)

    def test_the_stated_colour_ratio_holds_at_every_measured_elevation(self):
        low, high = (float(value) for value in re.search(r"blue/red ([0-9.]+) to ([0-9.]+) times",
                                                          flat(BLENDER_API_FACTS)).groups())
        single, multiple = world_light_table.SKY_TABLE["SINGLE_SCATTERING"], world_light_table.SKY_TABLE["MULTIPLE_SCATTERING"]
        ratios = []
        for one, many in zip(single, multiple):
            self.assertEqual(one[0], many[0])
            if one[0] < 0:  # "with the sun at or above the horizon"
                continue
            for direction in (1, 2, 3):
                ratios.append((many[direction][2] / many[direction][0]) / (one[direction][2] / one[direction][0]))
        self.assertGreaterEqual(min(ratios), low)
        self.assertLessEqual(max(ratios), high)
        # The range is the table's, not a loose bound: within 0.05 of what was measured.
        self.assertLess(min(ratios) - low, 0.06)
        self.assertLess(high - max(ratios), 0.06)

    def test_blender_s_own_words_name_the_more_accurate_type(self):
        # rna_nodetree.cc def_sh_tex_sky: "Multiple scattering sky model (more accurate)".
        self.assertIn("Blender calls MULTIPLE_SCATTERING the more accurate one", flat(BLENDER_API_FACTS))

    def test_the_sky_facts_are_facts_not_advice(self):
        sky = flat(BLENDER_API_FACTS).split("render.engine")[0]
        for word in ("use", "prefer", "should", "must", "always", "never", "recommend", "choose", "instead"):
            with self.subTest(word=word):
                self.assertIsNone(re.search(r"\b%s\b" % word, sky, re.I))


if __name__ == "__main__":
    unittest.main()
