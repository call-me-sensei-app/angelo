# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Facts a committed program's result keeps however it is handed on.

``world``: what the World shows after a commit that changed it or a Sun light, with each Sun light's angle from
the sky's sun (``from_sky_deg``). A Claude context gets certified results as compact entries
(``program_completion.commit_facts``); the comparison must survive that, as it does in a script's result.
The real commit's ``world`` is in test_blender_rc10_gaps. Host only; no Blender.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_completion import commit_facts  # noqa: E402

WORLD = {"world": "World", "strength": .5, "shows": "SKY",
         "sky": {"type": "MULTIPLE_SCATTERING", "sun_bearing_deg": 325.0, "sun_elevation_deg": 5.0},
         "sun_lights": [{"name": "Sun", "bearing_deg": 123.0, "elevation_deg": 30.0, "strength": 3.0,
                         "from_sky_deg": 158.2}]}


class CommitFactTests(unittest.TestCase):
    def test_the_world_comparison_survives_a_compact_entry(self):
        self.assertEqual(commit_facts({"world": WORLD}), {"world": WORLD})
        self.assertEqual(commit_facts({"world": WORLD, "isolated_shared_data": [{"name": "Cube"}]}),
                         {"world": WORLD, "isolated_shared_data": [{"name": "Cube"}]})

    def test_nothing_is_added_without_it(self):
        self.assertEqual(commit_facts({"status": "committed"}), {})
        self.assertEqual(commit_facts(None), {})


if __name__ == "__main__":
    unittest.main()
