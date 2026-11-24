# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An operator type's registration properties are written only while it registers (doc/angelo/script_confinement.rst).

Upstream's ``bl_options`` and ``bl_cursor_pending`` were raw ``type->`` setters, writable through any instance of a
registered operator: ``self`` in a modal, or an entry of ``WindowManager.operators`` (the person's own history). Once
Angelo exposed ``LOCK_BYPASS`` (an operator that runs while a job locks the interface), one assignment gave any
operator, Blender's own included, the bypass that registration refuses a confined script (the review of
86b9af0f149, M1). The strings were written "while empty", and registration allocates each at its own length, so
writing an empty ``bl_undo_group`` after registration ran past its single byte. Once a type of ``Operator`` or
``Macro`` (one shared definition) is registered, every setter refuses; the flags (``bl_options``,
``bl_cursor_pending``) also read as read-only, so a write raises, while a string stays upstream's silent no-op for
add-ons that set one, such as a dialog title (review 2, SHOULD-2). Only the validation dummy is editable, so a type
the check cannot place counts as registered (fails closed, NIT-1). ``test_blender_script_confinement`` proves it in
the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RNA = (ROOT / "source/blender/makesrna/intern/rna_wm.cc").read_text(encoding="utf-8")


def _body(start):
    text = RNA[RNA.index(start):]
    return text[:text.index("\n}\n")]


COMMON = _body("static void rna_def_operator_common(StructRNA *srna)")


def _properties():
    """Each property the shared definition declares: name -> its definition's lines."""
    parts = re.split(r'\n  prop = RNA_def_property\(srna, "', COMMON)[1:]
    return {part[:part.index('"')]: part for part in parts}


class RegistrationProperties(unittest.TestCase):
    def test_operator_and_macro_share_one_definition(self):
        for definer in ("static void rna_def_operator(BlenderRNA *brna)",
                        "static void rna_def_macro_operator(BlenderRNA *brna)"):
            self.assertIn("rna_def_operator_common(srna);", _body(definer), definer)

    FLAGS = {"bl_options", "bl_cursor_pending"}
    STRINGS = {"bl_idname", "bl_label", "bl_translation_context", "bl_description", "bl_undo_group"}

    def test_the_flags_raise_once_registered(self):
        registered = {name: lines for name, lines in _properties().items() if "PROP_REGISTER" in lines}
        self.assertEqual(set(registered), self.FLAGS | self.STRINGS)
        for name in self.FLAGS:
            self.assertIn('RNA_def_property_editable_func(prop, "rna_Operator_registration_editable");',
                          registered[name], name)

    def test_the_strings_stay_a_silent_no_op(self):
        """Upstream's behaviour for a string set after registration: nothing written, no error (add-ons set a
        dialog title through ``self.bl_label``). The setters refuse; Python is not told the property is read-only."""
        properties = _properties()
        for name in self.STRINGS:
            self.assertNotIn("rna_Operator_registration_editable", properties[name], name)

    def test_only_the_validation_dummy_is_editable(self):
        """Registration validates into a dummy type that has no properties struct yet (``wmOperatorType::srna``,
        which every type gets as it is appended, C or Python). Editable only there: a type the check cannot place,
        or no type at all, counts as registered (fails closed)."""
        check = _body("static bool rna_Operator_type_is_registered(const PointerRNA *ptr)")
        self.assertIn("return op == nullptr || op->type == nullptr || op->type->srna != nullptr;", check)
        self.assertNotIn("WM_operatortype_find", check)
        editable = _body("static int rna_Operator_registration_editable(")
        self.assertIn("rna_Operator_type_is_registered(ptr) ? 0 : PROP_EDITABLE", editable)

    def test_every_setter_refuses_once_registered(self):
        """A second barrier for any caller that sets the property without asking whether it is editable."""
        properties = _properties()
        for name, setter in (("bl_options", "rna_Operator_bl_options_set"),
                             ("bl_cursor_pending", "rna_Operator_bl_cursor_pending_set")):
            self.assertIn('RNA_def_property_enum_funcs(prop, nullptr, "%s", nullptr);' % setter, properties[name])
            self.assertIn("if (!rna_Operator_type_is_registered(ptr)) {", _body("static void %s(" % setter), setter)
        # The strings return before upstream's "while empty" write (and its debug assert): silent in every build.
        for setter in ("static void rna_Operator_bl_idname_set(", "static void rna_Operator_bl_label_set("):
            body = _body(setter)
            self.assertLess(body.index("if (rna_Operator_type_is_registered(ptr)) {"), body.index("BLI_assert_msg"),
                            setter)
        macro = RNA[RNA.index("#  define OPERATOR_STR_MAYBE_NULL_GETSET"):]
        macro = macro[:macro.index("_get(PointerRNA")]
        self.assertLess(macro.index("if (rna_Operator_type_is_registered(ptr)) {"), macro.index("BLI_strncpy_utf8"))

    def test_no_registration_property_is_a_raw_type_setter(self):
        """The class: a ``type->`` property with neither a refusing setter of its own nor the editable check is the
        hole M1 was (a generated setter writes the registered type)."""
        stored = {name: lines for name, lines in _properties().items() if '_sdna(prop, nullptr, "type->' in lines}
        self.assertIn("bl_options", stored)
        for name, lines in stored.items():
            setters = re.findall(r'"(rna_Operator_bl_[a-z_]+_set)"', lines)
            self.assertTrue(setters or "RNA_def_property_clear_flag(prop, PROP_EDITABLE);" in lines, name)
            for setter in setters:
                if setter.endswith(("translation_context_set", "description_set", "undo_group_set")):
                    continue   # The string macro, checked above.
                self.assertIn("rna_Operator_type_is_registered(ptr)", _body("static void %s(" % setter), setter)


if __name__ == "__main__":
    unittest.main()
