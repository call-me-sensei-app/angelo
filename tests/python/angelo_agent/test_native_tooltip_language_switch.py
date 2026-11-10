# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A tooltip open across an interface language or scale change is closed (wm_event_do_notifiers). No Blender.

A tooltip's text and size are built once, when it opens. Stock Blender sends only ``NC_UI`` ``ND_UI_LANG`` /
``ND_UI_FONT`` on such a change and nothing closes an open tooltip, so it kept the old language and layout (1.0.8 slot:
an Arabic engine-button tooltip in the top-bar suite's English capture). The notifier loop now clears it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "source/blender/windowmanager/intern/wm_event_system.cc"


def notifier_loop():
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("void wm_event_do_notifiers(bContext *C)")
    return text[start:text.index("\n}\n", start)]


class TooltipLanguageSwitchTests(unittest.TestCase):
    def test_a_language_or_scale_change_clears_the_open_tooltip(self):
        body = notifier_loop()
        branch = re.search(r"if \(note->category == NC_UI && ELEM\(note->data, ND_UI_LANG, ND_UI_FONT\)[^{]*\{\s*"
                           r"WM_tooltip_clear\(C, &win\);", body)
        self.assertIsNotNone(branch, "wm_event_do_notifiers must clear a tooltip on ND_UI_LANG and ND_UI_FONT")
        # For a change sent to every window (no window) as well as to this one.
        self.assertIn("ELEM(note->window, nullptr, &win)", body[branch.start():branch.end()])


if __name__ == "__main__":
    unittest.main()
