# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU subdivision status (Blender #58191): how the status is decided and carried, read from the sources.

One function decides it and the GPU path uses that same function, the modifier records it only for
viewport and render evaluations (never while applying), the active depsgraph copies it to the
original, a read file resets it, its RNA is read-only runtime data, and its text is translated.
test_blender_gpu_subdivision_status(_gui).py prove it in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')


def read(path):
    return (ROOT / path).read_text()


def body(text, signature):
    return re.search(re.escape(signature) + r".*?\n\}", text, re.S).group(0)


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


class GpuSubdivisionStatusSources(unittest.TestCase):
    def test_status_values_are_unique_and_fit_the_padding(self):
        dna = read("source/blender/makesdna/DNA_modifier_types.h")
        enum = re.search(r"enum eSubsurfGPUStatus : char \{(.*?)\};", dna, re.S).group(1)
        values = [int(value) for value in re.findall(r"SUBSURF_GPU_STATUS_\w+ = (\d+)", enum)]
        self.assertEqual(sorted(values), list(range(7)))
        # The struct ends at its own closing line; a member's `= {}` initializer is not the end.
        struct = re.search(r"struct SubsurfModifierData \{(.*?)^\};", dna, re.S | re.M).group(1)
        self.assertRegex(struct, r"eSubsurfGPUStatus gpu_subdivision_status = SUBSURF_GPU_STATUS_NOT_EVALUATED;\n"
                                 r"  char _pad\[7\] = \{\};\n$")

    def test_one_function_decides_and_the_gpu_path_uses_it(self):
        subdiv = read("source/blender/blenkernel/intern/subdiv_modifier.cc")
        can_do = body(subdiv, "bool BKE_subsurf_modifier_can_do_gpu_subdiv(")
        self.assertIn("BKE_subsurf_modifier_gpu_status(smd, mesh, true) == SUBSURF_GPU_STATUS_GPU", can_do)
        decide = body(subdiv, "eSubsurfGPUStatus BKE_subsurf_modifier_gpu_status(")
        order = [decide.index(name) for name in ("PREFERENCE_OFF", "subdivision_evaluation_status_on_gpu",
                                                 "NOT_LAST", "SPLIT_NORMALS")]
        self.assertEqual(order, sorted(order), "the reasons are checked in the documented order")

    def test_modifier_records_it_where_it_decides(self):
        modify = body(read("source/blender/modifiers/intern/MOD_subsurf.cc"), "static Mesh *modify_mesh(")
        self.assertIn("smd->gpu_subdivision_status = BKE_subsurf_modifier_gpu_status(smd, mesh, is_last_enabled);",
                      modify)
        self.assertIn("const bool has_gpu_subdiv = smd->gpu_subdivision_status == SUBSURF_GPU_STATUS_GPU;", modify)
        self.assertNotIn("BKE_subsurf_modifier_can_do_gpu_subdiv", modify)
        # Applying (MOD_APPLY_TO_ORIGINAL) never writes it.
        self.assertIn("if (!is_applying) {", modify)
        decided = modify.index("smd->gpu_subdivision_status = BKE_subsurf_modifier_gpu_status")
        self.assertLess(modify.index("if ((ctx->flag & MOD_APPLY_TO_ORIGINAL) == 0) {"), decided)

    def test_copied_to_the_original_and_reset_on_read(self):
        update = read("source/blender/blenkernel/intern/object_update.cc")
        sync = body(update, "void BKE_object_sync_to_original(")
        self.assertIn("if (!DEG_is_active(depsgraph)) {", sync)
        self.assertIn("sync_subsurf_gpu_status_to_original(", sync)
        helper = body(update, "static void sync_subsurf_gpu_status_to_original(")
        self.assertIn("BKE_modifier_is_enabled(", helper)
        subsurf = read("source/blender/modifiers/intern/MOD_subsurf.cc")
        self.assertIn("/*blend_read*/ blend_read,", subsurf)
        self.assertIn("SUBSURF_GPU_STATUS_NOT_EVALUATED", body(subsurf, "static void blend_read("))

    def test_preference_update_reaches_every_subdivision_modifier(self):
        userdef = body(read("source/blender/makesrna/intern/rna_userdef.cc"),
                       "static void rna_UserDef_subdivision_update(")
        self.assertIn("BKE_modifiers_findby_type(ob, eModifierType_Subsurf)", userdef)
        self.assertNotIn("BKE_object_get_last_subsurf_modifier", userdef)

    def test_edit_mode_is_read_from_the_mesh_everywhere(self):
        update = body(read("source/blender/blenkernel/intern/object_update.cc"),
                      "static void sync_subsurf_gpu_status_to_original(")
        self.assertIn("->runtime->edit_mesh", update)
        self.assertNotIn("OB_MODE_EDIT", update)
        panel = body(read("source/blender/modifiers/intern/MOD_subsurf.cc"), "static void panel_draw(")
        self.assertIn("->runtime->edit_mesh", panel)

    def test_rna_is_read_only_runtime_data(self):
        rna = read("source/blender/makesrna/intern/rna_modifier.cc")
        subsurf = body(rna, "static void rna_def_modifier_subsurf(")
        definition = subsurf[subsurf.index('"gpu_subdivision_status"'):]
        self.assertLess(subsurf.index("RNA_define_lib_overridable(false);"), subsurf.index('"gpu_subdivision_status"'))
        self.assertIn("RNA_def_property_clear_flag(prop, PROP_EDITABLE);", definition)
        self.assertIn("RNA_def_property_clear_flag(prop, PROP_ANIMATABLE);", definition)

    def test_every_status_wraps_instead_of_clipping(self):
        source = (ROOT / "source/blender/modifiers/intern/MOD_subsurf.cc").read_text(encoding="utf-8")
        body = source[source.index("static void draw_gpu_subdivision_status("):]
        body = body[:body.index("\n}\n")]
        # Blender 5.3 wraps a label itself (ui::Layout::label_multiline), with the status icon.
        self.assertNotIn(".label(", body)
        self.assertEqual(body.count("label_multiline("), 4)
        self.assertEqual(body.count("ICON_STATUS_INFO"), 4)

    def test_text_is_translated(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import sources
        panel = body(read("source/blender/modifiers/intern/MOD_subsurf.cc"), "static void draw_gpu_subdivision_status(")
        messages = ["".join(LITERAL.findall(group)).encode().decode("unicode_escape")
                    for group in re.findall(r"RPT_\(((?:\s*\"(?:[^\"\\]|\\.)*\")+)\)", panel)]
        # Blender's catalogues translate the two messages Blender already had.
        blender_own = {"Sharp edges or custom normals detected, disabling GPU subdivision",
                       "Using both CPU and GPU subdivision"}
        msgids = [("", message) for message in messages if message not in blender_own]
        self.assertEqual(len(msgids), 2)
        rna = [(ctxt, msgid) for ctxt, msgid, origin in sources.EXTRA_MSGIDS
               if origin.endswith("rna_modifier.cc")]
        self.assertEqual(len(rna), 14)
        self.assertIn("source/blender/modifiers/intern/MOD_subsurf.cc",
                      {path.relative_to(ROOT).as_posix() for path in sources.CPP_FILES})
        self.assertEqual(untranslated(msgids + rna), [])


if __name__ == "__main__":
    unittest.main()
