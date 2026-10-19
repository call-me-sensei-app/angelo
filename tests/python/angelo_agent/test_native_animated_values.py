# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How commit measures what an animation replaces (``animated_values``) without changing anything else.

Regressions: a channel list cut inside an ID, evaluated whole, lost the unlisted
channels' unkeyed edits; and tagging every animated ID as edited outdated unbaked physics caches. Each ID is now
listed whole or not at all, and the frame measurement is the update a frame change runs.
``test_blender_camera_guards`` measures both through Blender.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_camera_guards import Evaluated  # noqa: E402


class MeasurementTests(unittest.TestCase):
    def channels(self, *owners):
        from unittest.mock import patch
        from angelo_agent import animated_values
        with patch("angelo_agent.bone_state.animated_paths", lambda owner: owner.paths):
            return animated_values.Channels(owners)

    def test_an_owner_is_listed_whole_or_not_at_all_and_only_listed_owners_are_evaluated(self):
        """Regression: a list cut inside an ID, evaluated whole, lost the unlisted channels' unkeyed edits."""
        from unittest.mock import patch
        from angelo_agent import animated_values
        with patch.object(animated_values, "MAX_CHANNELS", 3):
            first = Evaluated("First", {"a": [0.0], "b": [0.0]}, {}, {"a", "b"})
            second = Evaluated("Second", {"c": [0.0], "d": [0.0]}, {}, {"c", "d"})  # Would pass the cap: left out.
            deep = Evaluated("Deep", {"e": [0.0]}, {}, {"e", "*"})  # NLA past its bound: left out.
            channels = self.channels(first, second, deep)
        self.assertEqual([path for _owner, path in channels.entries], ["a", "b"])
        self.assertTrue(channels.partial)
        self.assertEqual(len(channels.skipped), 2)
        self.assertEqual(animated_values.unmeasured()["partial"], True)

    def test_the_frame_measurement_is_a_time_update_with_the_stored_values_written_back(self):
        """Regression: no edit tag (it outdated physics caches); the update a frame change runs instead."""
        from angelo_agent.animated_values import at_frame
        keyed = Evaluated("Keyed", {"location": [2.0, 0.0, 0.0]}, {"location": [0.0, 0.0, 0.0]}, {"location"})
        channels = self.channels(keyed)

        def time_update():
            keyed.values.update({path: list(value) for path, value in keyed.animated.items()})
        now = at_frame(channels, time_update, NS(update=lambda: None))
        self.assertEqual(channels.changes(channels.values, now), [(0, 0)])  # The artist's unkeyed edit.
        self.assertFalse(keyed.tagged)  # Never tagged as edited.
        self.assertEqual(keyed.values["location"], (2.0, 0.0, 0.0))  # Written back.

    def test_strips_own_animated_settings_are_listed_meta_strips_too(self):
        """Regression: an NLA strip's animated influence was evaluated but not listed, so not written back."""
        from angelo_agent.animated_values import strip_controls
        inner = NS(name="Inner", fcurves=[NS(data_path="strip_time")], strips=[])
        meta = NS(name="Meta", fcurves=[], strips=[inner])
        plain = NS(name="Walk", fcurves=[NS(data_path="influence")], strips=[])
        owner = NS(animation_data=NS(nla_tracks=[NS(name="Track", strips=[plain, meta])]))
        self.assertEqual(strip_controls(owner), {'animation_data.nla_tracks["Track"].strips["Walk"].influence',
                                                 'animation_data.nla_tracks["Track"].strips["Meta"].strips["Inner"]'
                                                 '.strip_time'})
        deep = NS(name="D", fcurves=[], strips=[])
        for _ in range(10):
            deep = NS(name="D", fcurves=[], strips=[deep])
        self.assertIsNone(strip_controls(NS(animation_data=NS(nla_tracks=[NS(name="T", strips=[deep])]))))


if __name__ == "__main__":
    unittest.main()
