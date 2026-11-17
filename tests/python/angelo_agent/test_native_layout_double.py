# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The suites' ``UILayout`` stand-in (layout_double.py) matches Blender's: the properties a draw may set are exactly
UILayout's settable ones in rna_ui.cc, and anything else set on it (a misspelled flag) raises as Blender does
(the break-it review of the cut's GUI round on 3fc1f94df29, SHOULD-3). Runs outside Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

import layout_double

RNA = Path(__file__).resolve().parents[3] / "source/blender/makesrna/intern/rna_ui.cc"


def settable_properties():
    text = RNA.read_text(encoding="utf-8")
    body = text.split("static void rna_def_ui_layout(BlenderRNA *brna)", 1)[1].split("\nstatic void ", 1)[0]
    found = set()
    for block in body.split("prop = RNA_def_")[1:]:   # Each: one property's definition, of any kind.
        if "RNA_def_property_clear_flag(prop, PROP_EDITABLE)" not in block:
            found.add(re.search(r'"([a-z_]+)"', block).group(1))
    return found


class LayoutDoubleTest(unittest.TestCase):
    def test_its_properties_are_uilayouts_settable_ones(self):
        self.assertEqual(set(layout_double.PROPERTIES), settable_properties())

    def test_a_misspelled_flag_raises(self):
        layout = layout_double.UILayoutDouble()
        layout.use_property_split = True
        layout.column(align=True).scale_y = 2.0
        with self.assertRaises(AttributeError):
            layout.use_property_splt = True

    def test_a_suite_sets_only_what_it_records(self):
        class Recording(layout_double.UILayoutDouble):
            RECORDS = ("labels",)

            def __init__(self):
                self.labels = []
        recording = Recording()
        recording.labels.append("x")
        with self.assertRaises(AttributeError):
            recording.lables = []


if __name__ == "__main__":
    unittest.main()
