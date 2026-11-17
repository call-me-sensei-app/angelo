# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bake Auto Smooth (Blender #153195): the operator's contract, read from the sources.

Only node groups known to change nothing but sharpness are baked, only sharpness is copied, every
user of a baked mesh loses the modifier, the operator is one Undo step and is offered next to Shade
Auto Smooth, and its text is translated. test_blender_bake_auto_smooth.py proves it in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BAKE = ROOT / "source/blender/editors/object/object_shade_bake.cc"
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def marked_messages(text):
    """Each RPT_("...") / N_("...") message in C++ `text`, adjacent literals joined, escapes undone."""
    messages = []
    for match in re.finditer(r"\b(?:RPT_|N_)\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*)+)\)", text):
        joined = "".join(LITERAL.findall(match.group(1)))
        messages.append(joined.encode().decode("unicode_escape"))
    return messages


def untranslated(msgids):
    """Each (context, msgid) missing from Angelo's template or untranslated in one of its 20 catalogues."""
    sys.path.insert(0, str(ROOT / "scripts/angelo"))
    from i18n import languages, po, sources
    template = {entry.key for entry in po.read(str(sources.POT))[1] if not entry.obsolete}
    problems = [("template", key) for key in msgids if key not in template]
    for language in languages.LANGUAGES:
        entries = {entry.key: entry for entry in po.read(str(sources.LOCALE_DIR / "po" / f"{language.po}.po"))[1]
                   if not entry.obsolete}
        problems += [(language.po, key) for key in msgids if not (key in entries and entries[key].str)]
    return problems


class BakeAutoSmoothSources(unittest.TestCase):
    def setUp(self):
        self.text = BAKE.read_text()

    def test_only_known_sharpness_groups_are_baked(self):
        recognizer = re.search(r"bool is_auto_smooth_modifier\(.*?\n\}", self.text, re.S).group(0)
        self.assertIn("is_smooth_by_angle_modifier(md)", recognizer)
        self.assertIn("bke::is_auto_smooth_node_tree(*nmd.node_group)", recognizer)
        self.assertIn("is_auto_smooth_modifier(*md)", self.text)
        legacy = (ROOT / "source/blender/blenkernel/intern/mesh_legacy_convert.cc").read_text()
        self.assertIn("\nbool is_auto_smooth_node_tree(const bNodeTree &group)", legacy)

    def test_only_sharpness_is_copied_after_an_exact_check(self):
        copy = re.search(r"static void copy_sharpness\(.*?\n\}", self.text, re.S).group(0)
        self.assertEqual(sorted(re.findall(r'"(sharp_\w+)"', copy)), ["sharp_edge", "sharp_face"])
        exact = re.search(r"static bool differs_only_in_sharpness\(.*?\n\}", self.text, re.S).group(0)
        for condition in ("foreach_attribute", "memcmp", "src_count == result_count", "totcol"):
            self.assertIn(condition, exact, condition)
        evaluated = re.search(r"static Mesh \*evaluate_on_copy\(.*?\n\}", self.text, re.S).group(0)
        self.assertIn("geometry_set.has_instances()", evaluated)
        depends = re.search(r"static bool depends_only_on_its_mesh\(.*?\n\}", self.text, re.S).group(0)
        for condition in ("gather_eval_dependencies_recursive", "deps.time_dependent", "IDP_TYPE_FILTER_ID",
                          "GeometryNodeIsViewport"):
            self.assertIn(condition, depends, condition)
        bake = re.search(r"static std::string bake_mesh\(.*?\n\}", self.text, re.S).group(0)
        self.assertLess(bake.index("differs_only_in_sharpness"), bake.index("copy_sharpness"))
        for condition in ("mesh.key", "edit_mesh", "BKE_id_is_editable", "same_settings"):
            self.assertIn(condition, bake, condition)
        modifier = re.search(r"static NodesModifierData \*bakeable_modifier\(.*?\n\}", self.text, re.S).group(0)
        for condition in ("ob.modifiers.first", "ob.mode != OB_MODE_OBJECT", "eModifierMode_Editmode",
                          "depends_only_on_its_mesh", "BKE_modifiers_get_virtual_modifierlist",
                          "modifier_is_animated"):
            self.assertIn(condition, modifier, condition)
        # Animation inside the group or a nested one changes the shading between frames: every tree is checked.
        animated = re.search(r"static bool modifier_is_animated\(.*?\n\}", self.text, re.S).group(0)
        self.assertIn("node_trees_in(*nmd.node_group)", animated)
        self.assertIn("BKE_animdata_id_is_animated(&tree->id)", animated)
        self.assertIn("modifier_inputs_are_animated(ob, nmd.modifier)", animated)
        # Named before the dependency check, which (5.3) also counts an animated tree as time-dependent.
        self.assertLess(modifier.index("modifier_is_animated"), modifier.index("depends_only_on_its_mesh"))

    def test_every_user_of_the_mesh_is_baked(self):
        exec_body = re.search(r"static wmOperatorStatus shade_auto_smooth_bake_exec\(.*?\n\}", self.text, re.S).group(0)
        self.assertIn("for (Object &ob : bmain.objects)", exec_body)

    def test_registered_with_undo_and_offered_beside_shade_auto_smooth(self):
        self.assertIn("OPTYPE_REGISTER | OPTYPE_UNDO", self.text)
        self.assertIn("WM_operatortype_append(OBJECT_OT_shade_auto_smooth_bake);",
                      (ROOT / "source/blender/editors/object/object_ops.cc").read_text())
        self.assertIn("object_shade_bake.cc", (ROOT / "source/blender/editors/object/CMakeLists.txt").read_text())
        menus = (ROOT / "scripts/startup/bl_ui/space_view3d.py").read_text()
        self.assertEqual(menus.count('"object.shade_auto_smooth_bake"'), 2)
        for match in re.finditer(r'"object\.shade_auto_smooth_bake"', menus):
            preceding = menus[:match.start()].rsplit('"object.shade_auto_smooth"', 1)
            self.assertEqual(len(preceding), 2)
            self.assertLess(preceding[1].count("\n"), 3)

    def test_apply_refusal_names_the_operator(self):
        modifier = (ROOT / "source/blender/editors/object/object_modifier.cc").read_text()
        self.assertIn("md == ob->modifiers.first() && is_auto_smooth_modifier(*md)", modifier)
        self.assertIn("AUTO_SMOOTH_MULTI_USER_APPLY_MESSAGE", modifier)
        self.assertIn("Object > Bake Auto Smooth", self.text)

    def test_text_is_translated(self):
        operator = re.search(r'ot->description =\s*((?:"(?:[^"\\]|\\.)*"\s*)+);', self.text).group(1)
        msgids = [("Operator", "Bake Auto Smooth"), ("", "".join(LITERAL.findall(operator)))]
        msgids += [("", message) for message in marked_messages(self.text)]
        msgids += [("", "No selected mesh objects"), ("", "Not available in Edit Mode or Sculpt Mode")]
        self.assertGreaterEqual(len(msgids), 14)
        self.assertEqual(untranslated(msgids), [])


if __name__ == "__main__":
    unittest.main()
