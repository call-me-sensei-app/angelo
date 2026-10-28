# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The script precondition's objects as one fingerprint (``script_precondition``): it matches exactly when the facts the
per-object tuple held compare equal, over stand-ins for ``scene.objects`` and its bulk reads."""

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import script_precondition  # noqa: E402

IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


class Objects(list):
    def foreach_get(self, name, buffer):
        values = [v for o in self for v in (o.matrix if name == "matrix_world" else [o.session_uid])]
        buffer[:] = values


def thing(uid, matrix=None, data=7, modifiers=0):
    return SimpleNamespace(session_uid=uid, matrix=list(matrix or IDENTITY),
                           data=None if data is None else SimpleNamespace(session_uid=data), modifiers=[0] * modifiers)


def scene(*objects):
    return SimpleNamespace(objects=Objects(objects))


def of(*objects):
    return script_precondition.objects_fingerprint(scene(*objects))


class FingerprintTests(unittest.TestCase):
    def test_the_same_facts_match(self):
        self.assertEqual(of(thing(1), thing(2, data=None)), of(thing(1), thing(2, data=None)))

    def test_every_guarded_fact_changes_it(self):
        base = of(thing(1), thing(2))
        moved = list(IDENTITY)
        moved[12] = 0.5
        for changed in ((thing(1), thing(3)), (thing(1, moved), thing(2)), (thing(1, data=8), thing(2)),
                        (thing(1, modifiers=1), thing(2)), (thing(2), thing(1)), (thing(1),),
                        (thing(1), thing(2, data=None))):
            with self.subTest(changed=changed):
                self.assertNotEqual(of(*changed), base)

    def test_negative_zero_matches_zero_as_it_compared_equal(self):
        negative = list(IDENTITY)
        negative[1] = -0.0
        self.assertEqual(of(thing(1, negative)), of(thing(1)))

    def test_nan_never_matches_as_it_never_compared_equal(self):
        nan = list(IDENTITY)
        nan[0] = math.nan
        self.assertNotEqual(of(thing(1, nan)), of(thing(1, nan)))

    def test_it_is_small(self):
        self.assertLessEqual(len(of(*(thing(i) for i in range(2000)))), 64)


if __name__ == "__main__":
    unittest.main()
