# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Only a surface's controls are C's consent regions (grant_guard.canvas_regions; doc/angelo/grant_operators.rst,
"Authority in C").

A hit that takes no press (an approval reveal's shield, a disabled control, an info row) drawn over a control made a
press on that control land in two regions, which C refuses as ambiguous. C keeps the regions of the last draw, so a
reveal's shield still covered the card's buttons after it lapsed, until the next redraw, and a person's armed click on
"Allow for this session" in a chat opened from the status bar's notice was refused.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import angelo_guard_twin as twin
from test_native_grant_operators import GuardCase

WINDOW = SimpleNamespace(as_pointer=lambda: 0x2000)
REGION = SimpleNamespace(x=10, y=20, width=400, height=600)


class OnlyControlsAreRegions(GuardCase):
    def setUp(self):
        self.assertIsNotNone(self.guard, "no grant guard")
        twin.reset()
        self.addCleanup(twin.reset)

    def regions(self, hits, surface="chat:1"):
        with patch.object(self.guard, "script_running", return_value=False):
            self.guard.canvas_regions(WINDOW, REGION, surface, hits, 1.0, False)
        return twin._regions[(0x2000, surface)]

    def test_a_reveal_shield_over_the_card_is_no_region(self):
        card = ((100, 100, 120, 24), "script_session_allow", "call-1")
        regions = self.regions([((0, 0, 400, 600), "inert", 99.0), card])
        self.assertEqual([region[4] for region in regions], ["canvas.script_session_allow"])
        self.assertTrue(regions[0][6], "the approval's control stays shielded")
        self.assertEqual(regions[0][:4], (110, 120, 120, 24))

    def test_disabled_and_info_hits_are_no_regions_either(self):
        regions = self.regions([((0, 300, 400, 20), "info", None), ((5, 5, 50, 20), "disabled", None),
                                ((100, 300, 80, 20), "approve", "call-2")])
        self.assertEqual([region[4] for region in regions], ["canvas.approve"])

    def test_the_cap_counts_controls_only(self):
        shields = [((0, 0, 400, 600), "inert", 99.0)] * self.guard.MAX_REGIONS
        regions = self.regions(shields + [((100, 100, 120, 24), "approve", "call-3")])
        self.assertEqual([region[4] for region in regions], ["canvas.approve"])


if __name__ == "__main__":
    unittest.main()
