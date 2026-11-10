# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through (Blender #73479): which native paths read the viewport toggle, read from the sources.

Box, lasso and circle selection take the X-ray path when the toggle is on; click selection, the
nearest-element lookups that tools share (loop cut, knife, path pick, the pre-selection gizmo) and
particle brushes keep testing depth. test_blender_select_through(_gui).py prove the behavior in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELECT = ROOT / "source/blender/editors/space_view3d/view3d_select.cc"
PARTICLE = ROOT / "source/blender/editors/physics/particle_edit.cc"


def function_bodies(text):
    """Map each top-level C++ function name to its body text (brace matched)."""
    bodies = {}
    for match in re.finditer(r"^(?:static )?[A-Za-z_][\w:<> *&]*?\b(\w+)\(([^;{]*?)\)\s*\{", text, re.M | re.S):
        depth, index = 0, match.end() - 1
        while True:
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
            if depth == 0:
                break
        bodies[match.group(1)] = text[match.start():index]
    return bodies


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


class SelectThroughSources(unittest.TestCase):
    def test_flag_is_a_free_high_bit_of_flag2(self):
        text = (ROOT / "source/blender/makesdna/DNA_view3d_types.h").read_text()
        enum = re.search(r"enum eView3D_Flag2 : int \{(.*?)\};", text, re.S).group(1)
        bits = [int(bit) for bit in re.findall(r"1 << (\d+)", enum)]
        self.assertIn("V3D_SELECT_THROUGH = 1 << 30", enum)
        self.assertEqual(len(bits), len(set(bits)), "a flag2 bit is used twice")
        self.assertLess(max(bit for bit in bits if bit != 30), 30)

    def test_region_selections_read_the_toggle_and_clicks_do_not(self):
        bodies = function_bodies(SELECT.read_text())
        region = {"do_lasso_select_mesh", "do_lasso_select_paintvert", "do_paintvert_box_select",
                  "do_mesh_box_select", "mesh_circle_select", "paint_vertsel_circle_select"}
        readers = {name for name, body in bodies.items() if "SELECT_THROUGH_ENABLED" in body}
        self.assertEqual(readers, region)
        for name in region:
            self.assertRegex(bodies[name], r"const bool use_zbuf = !XRAY(_FLAG)?_ENABLED\(vc->v3d\) && "
                                           r"!SELECT_THROUGH_ENABLED\(vc->v3d\);", name)
        # The weight-paint vertex click keeps its depth test.
        self.assertIn("const bool use_zbuf = !XRAY_ENABLED(v3d);", bodies["ed_wpaint_vertex_select_pick"])

    def test_shared_nearest_lookups_never_read_the_toggle(self):
        for path in ("source/blender/editors/mesh/editmesh_select.cc",
                     "source/blender/editors/space_view3d/view3d_view.cc",
                     "source/blender/editors/space_view3d/view3d_gizmo_preselect_type.cc",
                     "source/blender/editors/armature/armature_select.cc",
                     "source/blender/editors/transform/transform_snap_object.cc"):
            self.assertNotIn("SELECT_THROUGH", (ROOT / path).read_text(), path)

    def test_particle_edit_scopes_the_toggle_to_region_selection(self):
        bodies = function_bodies(PARTICLE.read_text())
        region = {"PE_box_select", "pe_select_cache_init_with_generic_userdata", "PE_lasso_select"}
        callers = {name for name, body in bodies.items() if "PE_set_view3d_data_ex(C, " in body
                   and name != "PE_set_view3d_data"}
        self.assertEqual(callers, region)
        for name in region:
            self.assertRegex(bodies[name], r"PE_set_view3d_data_ex\(C, &?data, true\)", name)
        self.assertIn("PE_set_view3d_data_ex(C, data, false)", bodies["PE_set_view3d_data"])
        self.assertIn("if (XRAY_ENABLED(v3d) || data->ignore_depth)", bodies["key_test_depth"])

    def test_rna_property_and_icon(self):
        rna = (ROOT / "source/blender/makesrna/intern/rna_space.cc").read_text()
        block = rna[rna.index('"use_select_through"'):][:800]
        self.assertIn('RNA_def_property_boolean_sdna(prop, nullptr, "flag2", V3D_SELECT_THROUGH)', block)
        self.assertIn("RNA_def_property_clear_flag(prop, PROP_ANIMATABLE)", block)
        self.assertIn("ICON_ANGELO_SELECT_THROUGH", block)
        icons = (ROOT / "source/blender/editors/include/UI_icons.hh").read_text()
        # After every existing SVG icon, so those keep their IDs (icons after the SVG block shift, as upstream's do).
        self.assertLess(icons.index("DEF_ICON(ANGELO_MEDIA_STUDIO)"), icons.index("DEF_ICON(ANGELO_SELECT_THROUGH)"))
        self.assertLess(icons.index("DEF_ICON(ANGELO_SELECT_THROUGH)"), icons.index("DEF_ICON_BLANK(LAST_SVG_ITEM)"))
        self.assertIn("      angelo_select_through\n",
                      (ROOT / "source/blender/editors/datafiles/CMakeLists.txt").read_text())
        self.assertTrue((ROOT / "release/datafiles/icons_svg/angelo_select_through.svg").is_file())

    def test_header_offers_the_toggle_without_a_default_key(self):
        source = (ROOT / "scripts/startup/bl_ui/space_view3d.py").read_text()
        tree = ast.parse(source)
        predicate = next(node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == "_select_through_applies")
        modes = {elt.value for node in ast.walk(predicate) if isinstance(node, ast.Set) for elt in node.elts}
        self.assertEqual(modes, {"EDIT_MESH", "PARTICLE", "PAINT_WEIGHT", "PAINT_VERTEX"})
        self.assertIn("icon='ANGELO_SELECT_THROUGH'", source)
        for keymap in (ROOT / "scripts/presets/keyconfig").rglob("*.py"):
            self.assertNotIn("use_select_through", keymap.read_text(), keymap)

    def test_toggle_greyed_only_where_xray_already_selects_through(self):
        # Each mode's own X-ray test (view3d_select.cc): Edit Mode skips depth whenever X-ray is on
        # (XRAY_FLAG_ENABLED); the paint vertex mask and Particle Edit only while it is see-through (XRAY_ENABLED).
        source = (ROOT / "scripts/startup/bl_ui/space_view3d.py").read_text()
        tree = ast.parse(source)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_select_through_redundant")
        namespace = {}
        exec(compile(ast.Module([node], []), "space_view3d.py", "exec"), namespace)
        redundant = namespace["_select_through_redundant"]
        from types import SimpleNamespace

        def context(mode, shading_type, xray, alpha):
            shading = SimpleNamespace(type=shading_type, show_xray=xray, show_xray_wireframe=xray,
                                      xray_alpha=alpha, xray_alpha_wireframe=alpha)
            return SimpleNamespace(mode=mode, space_data=SimpleNamespace(shading=shading))
        for shading_type in ("SOLID", "WIREFRAME"):
            self.assertTrue(redundant(context("EDIT_MESH", shading_type, True, 1.0)))
            self.assertFalse(redundant(context("EDIT_MESH", shading_type, False, .5)))
            for mode in ("PAINT_WEIGHT", "PAINT_VERTEX", "PARTICLE"):
                self.assertTrue(redundant(context(mode, shading_type, True, .5)))
                self.assertFalse(redundant(context(mode, shading_type, True, 1.0)), mode)
                self.assertFalse(redundant(context(mode, shading_type, False, .5)))
        # Reached where the header is hidden (Director): the shading pie offers it beside X-ray, last.
        # A pie item of its own (a direction or its numpad key picks it), never a row inside the pie.
        item = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_select_through_pie_item")
        self.assertIn("pie.prop(", ast.unparse(item))
        self.assertNotIn(".row(", ast.unparse(item))
        # Z opens the plain pie by default (the keymap's "Extra Shading Pie Menu Items" is off): both pies, last.
        for name in ("VIEW3D_MT_shading_pie", "VIEW3D_MT_shading_ex_pie"):
            pie = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
            calls = [c.func.id for c in ast.walk(pie) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
            self.assertEqual(calls[-2:], ["_select_through_applies", "_select_through_pie_item"], name)


    def test_director_top_bar_offers_the_same_toggle(self):
        # Director hides the 3D view's header: its top bar offers the toggle after Blender's menus, bound to the
        # Director view's own property (one state with the header and the pie), in the header's modes, no key.
        addon = ROOT / "scripts/addons_core/angelo_agent"
        director = (addon / "director.py").read_text()
        menus = director.index("TOPBAR_MT_editor_menus.draw_collapsible(context, self.layout)")
        self.assertLess(menus, director.index("director_select_through.draw(self.layout, context)"),
                        "the toggle follows Blender's menus")
        module = (addon / "director_select_through.py").read_text()
        for term in ("_select_through_applies(context)", "_select_through_redundant(",
                     'row.prop(space, "use_select_through", icon=\'ANGELO_SELECT_THROUGH\')'):
            self.assertIn(term, module)
        self.assertNotIn("keymap", module.lower())

    def test_hidden_selection_drawing_only_when_on_and_leaves_stock_shaders(self):
        overlay = ROOT / "source/blender/draw/engines/overlay"
        mesh = (overlay / "overlay_mesh.hh").read_text()
        condition = mesh[mesh.index("    show_hidden_selection_ = ("):][:200]
        for term in ("V3D_SELECT_THROUGH", "!state.xray_flag_enabled", "!state.is_depth_only_drawing"):
            self.assertIn(term, condition)
        # Set up only when shown, submitted only in the color-only stage (which can read the depth).
        self.assertEqual(mesh.count("manager.submit(edit_mesh_hidden_selection_ps_, view);"), 1)
        color_only = mesh[mesh.index("void draw_color_only("):]
        self.assertIn("manager.submit(edit_mesh_hidden_selection_ps_, view);", color_only[:1200])
        self.assertIn("if (show_hidden_selection_) {", mesh[mesh.index("hidden_selection_edges_ = hidden_selection_verts_"):][:200])
        infos = (overlay / "shaders/infos/overlay_edit_mode_infos.hh").read_text()
        self.assertEqual(infos.count('DEFINE("SELECTED_HIDDEN_ONLY")'), 3)
        for name in ("vert", "edge", "facedot"):
            variant = infos[infos.index(f"GPU_SHADER_CREATE_INFO(overlay_edit_mesh_{name}_hidden_selection)"):][:200]
            self.assertIn(f"ADDITIONAL_INFO(overlay_edit_mesh_{name})", variant)
        # The regular shaders' code paths stay as they were: every addition is inside SELECTED_HIDDEN_ONLY.
        for source in ("overlay_edit_mesh_vert.glsl", "overlay_edit_mesh_lib.glsl", "overlay_edit_mesh_frag.glsl"):
            text = (overlay / "shaders" / source).read_text()
            self.assertIn("SELECTED_HIDDEN_ONLY", text, source)

    def test_non_modal_particle_circle_select_frees_its_user_data(self):
        """Found by this feature's rehearsal: stock Blender leaked ~5.7 MB per scripted Particle Edit circle select."""
        execute = function_bodies(SELECT.read_text())["view3d_circle_select_exec"]
        particle = execute[execute.index("OB_MODE_PARTICLE_EDIT)) {"):]
        particle = particle[:particle.index("\n  }\n")]
        self.assertNotIn("return OPERATOR_FINISHED;", particle)
        self.assertLess(particle.index("WM_generic_user_data_free(wm_userdata);"), particle.index("return changed"))

    def test_interface_text_is_in_every_catalogue(self):
        self.assertEqual(untranslated((("", "Select Through"),
                   ("", "Box, lasso and circle selection also select elements hidden behind others, as in X-ray, while "
                        "the display stays solid. Click selection still picks what is visible"))), [])


if __name__ == "__main__":
    unittest.main()
