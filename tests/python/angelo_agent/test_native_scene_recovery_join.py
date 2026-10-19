# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Complete scene revisions and truthful recovery evidence coexist after integration.

A changed setting with a reported value is shown before and after; a change only an opaque digest records is
named as unexplained beside it, never hidden by it (request_recovery).
"""

import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import recovery_evidence, scene_settings
from angelo_agent.request_recovery import RecoveryBarrier

HANDLE = "scn:" + "a" * 32 + ":17"


class SceneRecoveryJoinTests(unittest.TestCase):
    def capture(self, resolution=640, proven_value=12, generic_value=0.0):
        scene = SimpleNamespace(session_uid=17, world=None)
        values = {"render.resolution_x": resolution}
        proven = {"cycles.additional_admitted_setting": proven_value}
        generic = {"render.border_min_x": generic_value}
        with patch.object(scene_settings, "raw_values", return_value=values) as raw, \
             patch.object(scene_settings, "world_state", return_value=None), \
             patch.object(scene_settings, "proven_values", return_value=proven) as extra, \
             patch.object(scene_settings, "generic_values", return_value=generic) as other, \
             patch.object(scene_settings, "scene_handle", return_value=HANDLE), \
             patch("angelo_agent.action_visibility.layer_rows", return_value=[]), \
             patch("angelo_agent.anim_markers.revision_rows", return_value=[]), \
             recovery_evidence.collect() as records:
            revision = scene_settings.settings_revision(scene)
        raw.assert_called_once_with(scene)
        extra.assert_called_once_with(scene)
        other.assert_called_once_with(scene)
        expected = json.dumps([scene_settings.REVISION_VERSION, 17, values, None, [], [], proven, generic],
                              sort_keys=True, allow_nan=False, separators=(",", ":"))
        self.assertEqual(revision, hashlib.blake2b(expected.encode(), digest_size=32).hexdigest())
        self.assertEqual(records[HANDLE]["revision"], revision)
        self.assertNotIn("proven_settings", records[HANDLE]["values"], "Opaque settings grant no correction rights")
        self.assertNotIn("generic_settings", records[HANDLE]["values"], "Opaque settings grant no correction rights")
        return records

    def recovery(self, before, after):
        barrier = RecoveryBarrier()
        barrier.observe(before)
        arguments = {"mode": "APPLY", "read_set": [],
                     "write_set": [{"handle": HANDLE, "revision": before[HANDLE]["revision"]}],
                     "operations": [{"kind": "property_set", "target": HANDLE,
                                     "parameters": {"values": {"render.resolution_x": 800}}}]}
        barrier.refused("angelo_scene_program", arguments,
                        {"error": "The scene changed", "error_code": "STALE_DEPENDENCY",
                         "refusal_details": {"handles": [HANDLE]}, "scene_changed": False,
                         "partial_changes_possible": False, "mutation_started": False})
        recovery = barrier.read("angelo_scene_settings", after, {"handle": HANDLE})["recovery"]
        self.assertIsNone(barrier.pending)
        return recovery

    def test_known_setting_is_shown_before_and_after(self):
        recovery = self.recovery(self.capture(), self.capture(resolution=720))
        self.assertEqual(recovery["changes"], [
            {"handle": HANDLE, "field": "setting:render.resolution_x", "before": 640, "after": 720}])
        self.assertNotIn("unexplained", recovery)

    def test_opaque_proven_setting_alone_is_named_unexplained(self):
        before, after = self.capture(), self.capture(proven_value=13)
        self.assertNotEqual(before[HANDLE]["revision"], after[HANDLE]["revision"])
        self.assertNotEqual(before[HANDLE]["fields"]["proven_settings"],
                            after[HANDLE]["fields"]["proven_settings"])
        recovery = self.recovery(before, after)
        self.assertEqual((recovery["changes"], recovery["unexplained"]),
                         ([], [{"handle": HANDLE, "fields": ["proven_settings"]}]))

    def test_a_generic_setting_alone_changes_the_revision_and_is_named_unexplained(self):
        # render.border_min_x and the other paths rna_rules admits beyond the reported ones.
        before, after = self.capture(), self.capture(generic_value=0.25)
        self.assertNotEqual(before[HANDLE]["revision"], after[HANDLE]["revision"])
        self.assertNotEqual(before[HANDLE]["fields"]["generic_settings"], after[HANDLE]["fields"]["generic_settings"])
        self.assertEqual(self.recovery(before, after)["unexplained"], [{"handle": HANDLE, "fields": ["generic_settings"]}])

    def test_known_change_cannot_hide_a_simultaneous_opaque_change(self):
        recovery = self.recovery(self.capture(), self.capture(resolution=720, proven_value=13))
        self.assertEqual([row["field"] for row in recovery["changes"]], ["setting:render.resolution_x"])
        self.assertEqual(recovery["unexplained"], [{"handle": HANDLE, "fields": ["proven_settings"]}])


if __name__ == "__main__":
    unittest.main()
