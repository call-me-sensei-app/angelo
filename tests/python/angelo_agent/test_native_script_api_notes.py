# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script's result notes the Blender 4.x names it used that 5.2 renamed or removed (script_api_notes).

Uses inside try/except are noted too: there the old name fails silently and the fallback runs. A name in a
comment, an attribute that 5.2 still has on another type (a channelbag's fcurves) and unrelated strings are
not. ``test_blender_script_api_notes.py`` checks every listed name against Blender 5.2 itself.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.script_api_notes import CAUGHT, RENAMED, notes  # noqa: E402

NOTE = {row.get("string", row.get("attribute")): row["note"] for row in RENAMED}


class Notes(unittest.TestCase):
    def assertNoted(self, code, *names):
        self.assertEqual(notes(code), {"api_notes": [NOTE[name] for name in names] + [CAUGHT]}, code)

    def test_each_listed_name_is_noted_where_the_code_uses_it(self):
        self.assertNoted("try:\n    sky.sky_type = 'NISHITA'\nexcept Exception:\n    bg.inputs[0].default_value = (.5, .5, .5, 1)\n",
                         "NISHITA")
        self.assertNoted("if 'NISHITA' in [i.identifier for i in items]: pass", "NISHITA")
        self.assertNoted("scene.render.engine = 'BLENDER_EEVEE_NEXT'", "BLENDER_EEVEE_NEXT")
        self.assertNoted("sky.dust_density = 1.0", "dust_density")
        self.assertNoted("setattr(sky, 'dust_density', 1.0)", "dust_density")
        self.assertNoted("for name, value in (('air_density', 1.0), ('dust_density', 1.0)):\n"
                         "    try: setattr(sky, name, value)\n    except AttributeError: pass", "dust_density")
        self.assertNoted("sky.sky_type = 'NISHITA'\nsky.dust_density = 2", "NISHITA", "dust_density")

    def test_fcurves_is_noted_only_on_an_action(self):
        for code in ("for fc in body.animation_data.action.fcurves:\n    fc.extrapolation = 'LINEAR'",
                     "act = obj.animation_data.action\nfor fc in act.fcurves:\n    pass",
                     "for a in bpy.data.actions:\n    print(len(a.fcurves))",
                     "bpy.data.actions['Move'].fcurves.find('location')",
                     "bpy.data.actions.new('A').fcurves.new('location')",
                     "x = obj.animation_data.action\nif hasattr(x, 'fcurves'):\n    pass"):
            with self.subTest(code=code):
                self.assertNoted(code, "fcurves")
        for code in ("cb = anim_utils.action_get_channelbag_for_slot(act, slot)\nfor fc in cb.fcurves: pass",
                     "for fc in strip.fcurves: pass",  # NlaStrip.fcurves exists in 5.2.
                     "for fc in obj.animation_data.drivers: pass",
                     "bag = act.layers[0].strips[0].channelbag(slot)\nbag.fcurves.new('location')"):
            with self.subTest(code=code):
                self.assertEqual(notes(code), {})

    def test_comments_other_strings_and_unparsable_code_get_nothing(self):
        for code in ("# NISHITA is gone; use MULTIPLE_SCATTERING\nsky.sky_type = 'MULTIPLE_SCATTERING'",
                     "print('NISHITA sky')", "name = 'BLENDER_EEVEE_NEXT_GEN'", "def (:\n  'NISHITA'", "",
                     "scene.render.engine = 'BLENDER_EEVEE'", None):
            with self.subTest(code=code):
                self.assertEqual(notes(code), {})

    def test_nishita_names_both_5_2_sky_types(self):
        # Its own model renamed (old files load as it) and the newer default; Blender's error names the same two.
        note = NOTE["NISHITA"]
        self.assertIn("'SINGLE_SCATTERING' (the same model", note)
        self.assertIn("the new default is 'MULTIPLE_SCATTERING'", note)
        _, _, clause = note.partition(": use ")
        self.assertTrue(clause.startswith("'SINGLE_SCATTERING'"))

    def test_the_list_stays_small_and_every_row_names_its_5_2_owner(self):
        self.assertLessEqual(len(RENAMED), 8)
        for row in RENAMED:
            self.assertEqual(len([key for key in ("string", "attribute") if key in row]), 1, row)
            self.assertTrue(row["owner"] and row["note"].startswith("Blender 5.2"), row)


if __name__ == "__main__":
    unittest.main()
