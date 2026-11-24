# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Always run this exact script as 2.1.0 ships it (off: ``reviewed_scripts.OFFERED``) and what a pending script costs,
split from test_native_reviewed_scripts.py (re-read of 1c2c4edaf48, N-5). No Blender here."""

import suite_isolation  # noqa: F401

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import reviewed_scripts as rs  # noqa: E402
import angelo_guard_twin  # noqa: E402
from test_native_reviewed_scripts import SHIPPED, Chat, script_call  # noqa: E402  (as the add-on ships: off)

angelo_guard_twin.stand_in_for_the_person(globals())
_twin_set_up = setUpModule   # noqa: F821  Chained (M-2): the mechanism is remembered with the switch on.


def setUpModule():
    _twin_set_up()
    patcher = patch.object(rs, "OFFERED", True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class FailClosedTests(unittest.TestCase):
    """Break-it re-read of 23413c6bae1, M-1 (ruling 2026-11-20): until a run-time check in C keeps a remembered script
    to its own bytes, Always run this exact script is never offered, nothing remembered runs unasked, and the Rules page
    has no Scripts tab. The mechanism stays tested with the switch on (setUpModule)."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        rs._stores.clear()
        self.addCleanup(rs._stores.clear)
        self.chat = Chat(self.directory)

    def test_it_ships_off(self):
        self.assertIs(SHIPPED, False)

    def test_turning_it_on_drops_what_earlier_builds_remembered(self):
        """Re-read of 1c2c4edaf48, S-3: approvals remembered by the 2.1.0 dev builds that offered the button were made
        under the text check the re-read showed unsound; the day it turns on, the file version moves past them."""
        self.assertTrue(SHIPPED is False or rs.VERSION >= 2, "bump reviewed_scripts.VERSION when turning OFFERED on")
        source = (Path(rs.__file__)).read_text(encoding="utf-8")
        self.assertNotIn('"version": 1', source)
        self.assertNotIn('get("version") == 1', source)

    def test_off_nothing_is_offered_and_nothing_remembered_runs_unasked(self):
        call = script_call()
        self.chat.pending[call.id] = call
        self.chat.review_script(call)
        self.assertTrue(self.chat.remember_script(call))   # Remembered while it was on (the mechanism as built).
        later = script_call(identity="call-2")
        self.chat.pending[later.id] = later
        with patch.object(rs, "OFFERED", False):
            self.assertFalse(self.chat.offers_remember_script(later))
            self.assertFalse(self.chat.reviewed_script_allowed(later))
            self.assertIsNone(self.chat.script_consent_kind(later))
            self.assertFalse(self.chat._automatic_script(later), "asks, as any script does")

    def test_off_the_rules_page_has_no_scripts_tab(self):
        from angelo_agent import rules_sheet
        with patch.object(rs, "OFFERED", False):
            self.assertEqual(rules_sheet.scopes(), ("project", "global"))
        self.assertEqual(rules_sheet.scopes(), ("project", "global", "scripts"))


class DigestCostTests(unittest.TestCase):
    """Fable review 1, A-5: a pending script's SHA-256 was taken at every pump tick and twice per card draw (0.96 ms
    for a 120 KB script on Mac 1, 2026-11-20), and again by the reach check's memo key. A script's text is hashed once
    while it stays pending; A-7: text that can't be UTF-8 (a lone surrogate) is never remembered, and never raises."""

    def counted(self, module):
        real, calls = module.hashlib.sha256, []

        def sha256(data=b""):
            calls.append(len(data))
            return real(data)
        patcher = patch.object(module.hashlib, "sha256", sha256)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_a_pending_scripts_text_is_hashed_once_for_every_check(self):
        from angelo_agent import script_reach
        code = "x = %r\n" % uuid.uuid4().hex * 2000
        expected = hashlib.sha256(code.encode("utf-8")).hexdigest()
        calls = self.counted(rs)
        for _ in range(4):
            self.assertEqual(rs.digest(code), expected)
            rs.runs_other_code(code)
        self.assertLessEqual(len([n for n in calls if n >= len(code)]), 1, "one hash of the text, then kept")
        self.assertIs(script_reach.hashlib, rs.hashlib)   # The same module: the count above covers the reach check.

    def test_text_that_cant_be_utf8_is_never_remembered_and_never_raises(self):
        broken = "x = '\ud800'\n"
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        store = rs.ReviewedScripts(Path(scratch.name))
        self.assertIsNone(rs.digest(broken))
        self.assertFalse(store.allowed("p" * 64, broken))
        with self.assertRaises(ValueError):
            store.add("p" * 64, broken, "label")


if __name__ == "__main__":
    unittest.main()
