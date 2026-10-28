# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The hand-rigging tool family's contract (rig_spec, its declaration, deferral, guidance and picture delivery), and
that a worker's chat is neither offered, run nor told of it, on either path (design A′ or the lanes). No Blender."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import rig_spec  # noqa: E402
from angelo_agent.agent_guidance import FAMILY_GUIDANCE, HAND_GUIDANCE  # noqa: E402
from angelo_agent.plan_kinds import DEFERRED_TOOLS  # noqa: E402
from angelo_agent.plan_recovery import PREVIEW_IMAGE_TOOLS  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402

HANDLE = "scn:" + "a" * 32 + ":7"


class WorkerChatTests(unittest.TestCase):
    """The rig tools read the lead's window scene with pictures; a worker's set has none of them (worker_refusals)."""

    def setUp(self):
        from angelo_agent import parallel_access
        self.access = parallel_access

    def test_a_workers_chat_is_not_offered_the_rig_tools_on_either_path(self):
        for hosting in (True, False):   # A worker Blender hosting its chats (A′), or the lead's lanes with it on.
            with self.subTest(hosting=hosting), mock.patch.object(self.access, "in_worker", return_value=hosting), \
                    mock.patch.object(self.access, "enabled", return_value=not hosting):
                offered = self.access.restrict(None, set(TOOL_MAP), worker=True)
                self.assertFalse(offered & rig_spec.RIG_NAMES)
                self.assertIn("angelo_scene_program", offered)   # Where a fist's bone_set operations would go.

    def test_a_workers_chat_is_refused_them_with_nothing_run(self):
        from angelo_agent import worker_refusals
        for name in sorted(rig_spec.RIG_NAMES):
            with self.subTest(name=name):
                refused = worker_refusals.refusal(name, {"armature": HANDLE})
                self.assertEqual(refused.code, "UNSUPPORTED")
                self.assertFalse(refused.partial_changes_possible)

    def test_discovery_tells_a_workers_chat_only_of_its_own_set(self):
        lead, shadow = SimpleNamespace(), SimpleNamespace(worker_shadow=True)
        with mock.patch.object(self.access, "worker_chat", side_effect=lambda chat: chat is not lead):
            self.assertFalse(self.access.discovery_hidden(lead) & rig_spec.RIG_NAMES)
            self.assertFalse(self.access.discovery_hidden(None) & rig_spec.RIG_NAMES)
            for worker in (SimpleNamespace(), shadow):
                hidden = self.access.discovery_hidden(worker)
                self.assertLessEqual(rig_spec.RIG_NAMES, hidden)
                self.assertNotIn("angelo_scene_program", hidden)
        with mock.patch.object(self.access, "worker_chat", return_value=False):
            self.assertLessEqual(rig_spec.RIG_NAMES, self.access.discovery_hidden(shadow))   # The lanes worker's.


class RigToolContract(unittest.TestCase):
    def test_every_rig_tool_is_declared_read_only_deferred_and_guided(self):
        for name in rig_spec.RIG_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, TOOL_MAP)
                self.assertTrue(TOOL_MAP[name]["annotations"]["readOnlyHint"])
                self.assertIn(name, DEFERRED_TOOLS)
        names, text = FAMILY_GUIDANCE["RIG"]
        self.assertEqual(names, rig_spec.RIG_NAMES)
        self.assertIs(text, HAND_GUIDANCE)
        # Pictures go with the result once, like staged previews, never as requested views.
        self.assertLessEqual(rig_spec.RIG_IMAGE_TOOLS, PREVIEW_IMAGE_TOOLS)
        self.assertLessEqual(rig_spec.RIG_IMAGE_TOOLS, rig_spec.RIG_NAMES)

    def test_the_guidance_carries_the_rules_the_kit_learned(self):
        text = " ".join(HAND_GUIDANCE.split())
        for fact in ("perpendicular to the forearm", "Never the knuckles' plane", "folded to its limit",
                     "never through it", "Weights fade, never cut", "never move it to the hand bone",
                     "corrective shapes on the rest pose", "beside its rest pose", "camera angle",
                     "drops morph normals", "fit inputs", "cross-section", "bone_set operations"):
            self.assertIn(fact, text)

    def test_arguments_are_checked(self):
        ok = validate_call("angelo_rig_hand_fist", {"armature": HANDLE, "hand": "LeftHand", "pictures": True,
                                                   "views": ["BACK", "PALM_THUMB"],
                                                   "limits": [{"bone": "LeftHandIndex1", "flex": [-10, 80]}]})
        self.assertEqual(ok["limits"], {"LeftHandIndex1": {"flex": [-10.0, 80.0]}})
        # The host checks checked arguments again (rig_host.execute): the normalised form passes unchanged.
        self.assertEqual(rig_spec.validate_rig("angelo_rig_hand_fist", ok), ok)
        posed = validate_call("angelo_rig_hand_pose", {"armature": HANDLE, "angles": [{"bone": "B", "flex": 40}]})
        self.assertEqual(rig_spec.validate_rig("angelo_rig_hand_pose", posed), posed)
        with self.assertRaises(ValueError):
            validate_call("angelo_rig_hand_pose", {"armature": HANDLE})
        # The map form the host re-checks is refused with a ValueError when malformed, never a TypeError.
        for name, args in (("angelo_rig_hand_pose", {"armature": HANDLE, "angles": {"f1": 45}}),
                           ("angelo_rig_hand_fist", {"armature": HANDLE, "limits": {"f1": [0, 90]}})):
            with self.subTest(args=args), self.assertRaises(ValueError):
                validate_call(name, args)
        bad = [("angelo_rig_hand_inspect", {}),
               ("angelo_rig_hand_inspect", {"armature": "Rig"}),
               ("angelo_rig_hand_inspect", {"armature": HANDLE, "palm_normal": [0, 0, 0]}),
               ("angelo_rig_hand_fist", {"armature": HANDLE, "views": ["BACK", "BACK"]}),
               ("angelo_rig_hand_fist", {"armature": HANDLE, "views": ["TOP"]}),
               ("angelo_rig_hand_contact", {"armature": HANDLE, "limits": [{"bone": "X", "flex": [10, -10]}]}),
               ("angelo_rig_hand_contact", {"armature": HANDLE, "limits": [{"bone": "X", "bend": [0, 10]}]}),
               ("angelo_rig_hand_range", {"armature": HANDLE, "joints": []}),
               ("angelo_rig_hand_views", {"armature": HANDLE, "markers": "yes"}),
               ("angelo_rig_hand_views", {"armature": HANDLE, "pose": "FIST"})]
        for name, args in bad:
            with self.subTest(name=name, args=args):
                with self.assertRaises(ValueError):
                    validate_call(name, args)

    def test_enums_are_upper_case_identifiers(self):
        for view in rig_spec.VIEWS:
            self.assertEqual(view, view.upper())


if __name__ == "__main__":
    unittest.main()
