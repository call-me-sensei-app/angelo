# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A label whose words mean something else elsewhere carries its own context, so its translation matches its meaning.

Re-read of 70142a371dd, S-3: the Loops menu's "Space" (a verb: space the loops evenly) took the catalogue's noun
("Raum", "Espace", "空間": coordinate space), and the live page's "Now" (the current step's heading) took the chat's
"Now" (send immediately: "今すぐ", "立即"). Each has its own context, translated as what it means. No Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from i18n import catalogs, languages, po  # noqa: E402


class LabelMeaningTests(unittest.TestCase):
    def test_the_loops_menu_and_the_live_page_label_by_their_own_context(self):
        loops = (ROOT / "scripts/addons_core/angelo_agent/loops_menu.py").read_text(encoding="utf-8")
        self.assertIn('n_("Space", "Loops"), "Loops")', loops)
        self.assertIn("iface_(text, context)", loops)
        page = (ROOT / "scripts/addons_core/angelo_agent/session_page.py").read_text(encoding="utf-8")
        self.assertIn('"step": (n_("Now", "Live page"), "Live page")', page)

    def test_every_language_translates_them_and_never_as_the_other_meaning(self):
        for language in languages.LANGUAGES:
            with self.subTest(language=language.code):
                _, entries = po.read(str(catalogs.catalog_path(language)))
                live = {(e.ctxt, e.id): e.str for e in entries if not e.obsolete}
                for ctxt, other in (("Loops", ("", "Space")), ("Live page", ("Angelo", "Now"))):
                    mine = live.get((ctxt, other[1]))
                    self.assertTrue(mine, (ctxt, other[1]))
                # Spacing loops is an action, a space a thing: no language says both with one word. ("Now" may be one
                # word for both of its meanings, so only its own entry is required.)
                self.assertNotEqual(live.get(("Loops", "Space")), live.get(("", "Space")), "the noun's words")


if __name__ == "__main__":
    unittest.main()
