# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every rigging bake keys through ``rig_pose_write.Keys`` (one write per curve), never a key per frame per channel.

Keying frame by frame (``keyframe_insert``) re-sorts and re-handles a whole curve on every key, so a bake grew with the
square of its frames: on the Linux lane VM a 25-bone retarget took 0.83 s for 250 frames and 10.5 s for 1000, the game
rig 1.5 s and 17.8 s (v210-rigging timings, 2026-11-18). Read from source (no Blender): each call of
``rig_pose_write.write`` inside a loop in the bake modules passes ``keys=``. Expected to fail on fa850dcf5a3.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
BAKES = ("game_rig.py", "retarget.py", "root_motion.py")


def writes_in_loops(tree):
    out = []
    for loop in ast.walk(tree):
        if isinstance(loop, (ast.For, ast.While)):
            for node in ast.walk(loop):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write"
                        and getattr(node.func.value, "id", "") == "rig_pose_write"):
                    out.append(node)
    return out


class BakeKeys(unittest.TestCase):
    def test_every_bake_loop_keys_through_one_write_per_curve(self):
        missing = []
        for name in BAKES:
            calls = writes_in_loops(ast.parse((ADDON / name).read_text(encoding="utf-8")))
            self.assertTrue(calls, name)
            missing += ["%s:%d" % (name, c.lineno) for c in calls if not any(k.arg == "keys" for k in c.keywords)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
