# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport UV checker (doc/angelo/uv_checker.rst), read from the sources and laid out on a fake view.

No Blender here. Covered: the DNA struct and its versioning defaults, the RNA properties (display only, never
animated), the batch cache's ``cu`` alias (requested only for a map the mesh has, re-extracted when it changes), the
overlay's zero cost while off (nothing set up, no batch requested) and that nothing in it makes or changes a
data-block, the shaders' registration, the controls (+ menu, Director's top bar, the header, F3), the card's layout
and actions, the agent's UV_CHECKER look (on only in that look, off in every other), and the catalogues.
``test_blender_uv_checker_draw.py`` proves the drawing with pixels in the app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import card_kit as kit, chat_destinations, uv_checker_card, view_looks, view_spec  # noqa: E402
from test_native_notice_cards import VIEW  # noqa: E402
from test_native_select_through import function_bodies, untranslated  # noqa: E402

SOURCE = ROOT / "source/blender"
OVERLAY = SOURCE / "draw/engines/overlay"
ADDON = ROOT / "scripts/addons_core/angelo_agent"
DNA_SIZES = {"char": 1, "float": 4, "int": 4}


class NativeSources(unittest.TestCase):
    def test_dna_struct_is_8_byte_aligned_and_in_view3d(self):
        text = (SOURCE / "makesdna/DNA_view3d_types.h").read_text()
        body = re.search(r"struct View3DUVChecker \{(.*?)\n\};", text, re.S).group(1)
        enums = set(re.findall(r"enum (eView3DUVChecker\w+) : char", text))
        size = 0
        for kind, name, count in re.findall(r"^\s+(\w+) (\w+)(?:\[(?:/\*\w+\*/ )?(\d+)\])? =", body, re.M):
            unit = 1 if kind in enums else DNA_SIZES[kind]
            self.assertEqual(size % unit, 0, f"{name} is not aligned")
            size += unit * int(count or 1)
        self.assertEqual(size % 8, 0, "DNA needs the struct's size to be a multiple of 8")
        self.assertIn("View3DUVChecker uv_checker;", text[text.index("struct View3D {"):])

    def test_versioning_gives_older_files_the_defaults(self):
        text = (SOURCE / "blenloader/intern/versioning_520.cc").read_text()
        helper = function_bodies(text)["version_angelo_uv_checker_defaults"]
        self.assertIn('DNA_struct_member_exists_with_alias(fd.filesdna, "View3D", "View3DUVChecker", "uv_checker")',
                      helper)
        self.assertIn("->uv_checker = defaults;", helper)
        main = text[text.index("void blo_do_versions_520("):]
        call = main.index("version_angelo_uv_checker_defaults(*fd, *bmain);")
        self.assertGreater(call, main.rindex("if (!MAIN_VERSION_FILE_ATLEAST("), "not gated by a file version")

    def test_rna_is_a_display_setting(self):
        rna = (SOURCE / "makesrna/intern/rna_space.cc").read_text()
        body = function_bodies(rna)["rna_def_space_view3d_uv_checker"]
        props = set(re.findall(r'RNA_def_property\(srna, "(\w+)"', body))
        self.assertEqual(props, {"show", "use_selected_only", "show_flipped", "pattern", "uv_map", "uv_map_name",
                                 "scale", "opacity", "texture_size", "target_density"})
        self.assertLess(body.index("RNA_define_animate_sdna(false);"), body.index('"show"'))
        self.assertEqual(body.count("RNA_def_property_update(prop, NC_SPACE | ND_SPACE_VIEW3D, nullptr);"), len(props))
        for item in ("COLOR_GRID", "CHECKER", "TEXEL_DENSITY", "STRETCH", "ACTIVE", "RENDER", "NAMED"):
            self.assertIn(f'"{item}"', body)

    def test_batch_cache_requests_only_existing_maps_and_reextracts_on_a_change(self):
        cache = (SOURCE / "draw/intern/draw_cache_impl_mesh.cc").read_text()
        request = function_bodies(cache)["DRW_mesh_batch_cache_get_surface_uv_checker"]
        guard = request.index("return nullptr;")
        self.assertLess(guard, request.index("mesh_batch_cache_get(mesh)"), "nothing requested for a missing map")
        self.assertIn("lookup_meta_data(mesh, uv_map) != uv_meta_data", request)
        # Each map a checker reads keeps its slot, so viewports on different maps of one mesh share one extraction;
        # only a new map (or a fifth at once) extracts again.
        self.assertIn("drw_attributes_overlap(&cache.cd_used.uv_checker,", cache)
        self.assertIn("r_slot = int(cache.cd_used.uv_checker.index_of_try_as(uv_map));", request)
        uv = (SOURCE / "draw/intern/mesh_extractors/extract_mesh_vbo_uv.cc").read_text()
        self.assertIn('{"cu0", "cu1", "cu2", "cu3"}', uv)
        self.assertIn("GPU_vertformat_alias_add(format, checker_names[checker_slot]);", uv)

    def test_overlay_costs_nothing_while_off_and_makes_no_data(self):
        text = (OVERLAY / "overlay_uv_checker.hh").read_text()
        enabled = text[text.index("static bool is_enabled("):text.index("void begin_sync(")]
        for term in ("V3D_UV_CHECKER_SHOW", "ELEM(state.v3d->shading.type, OB_SOLID, OB_MATERIAL)",
                     "!state.xray_enabled", "!res.is_selection()", "!state.is_depth_only_drawing"):
            self.assertIn(term, enabled)
        begin = text[text.index("void begin_sync("):text.index("const View3DUVChecker &settings")]
        self.assertIn("if (!enabled_) {\n      /* Not used. But release the data. */\n      ps_.init();\n      return;",
                      begin)
        for method in ("void object_sync(", "void pre_draw(", "void draw("):
            self.assertIn("if (!enabled_) {\n      return;\n    }", text[text.index(method):][:400], method)
        patterns = (OVERLAY / "overlay_uv_checker_patterns.cc").read_text()
        for creator in ("BKE_image_add", "BKE_material", "BKE_id_new", "BKE_libblock", "id_us_plus", "bNode",
                        "ntreeAdd", "add_generated"):
            self.assertNotIn(creator, text)
            self.assertNotIn(creator, patterns)
        # The pattern images: made once per session on a worker thread, shared by every viewport, freed with the
        # engine; an interactive view draws without the checker until shaders and image are ready.
        self.assertIn("std::thread", patterns)
        self.assertIn("uv_checker::free_patterns();", (OVERLAY / "overlay_engine.cc").read_text())
        self.assertIn("DRW_viewport_request_redraw();", text)
        self.assertIn("const bool wait = DRW_context_get()->mode != DRWContext::VIEWPORT;", text)
        instance = (OVERLAY / "overlay_instance.cc").read_text()
        sync = instance[instance.index("layer.uv_checker.object_sync"):]
        self.assertLess(sync.index("layer.uv_checker"), sync.index("if (!state.hide_overlays) {"),
                        "drawn with the overlays hidden: its own toggle")
        self.assertLess(instance.index("regular.uv_checker.draw("), instance.index("regular.facing.draw("))
        self.assertLess(instance.index("infront.uv_checker.draw("), instance.index("infront.facing.draw("))

    def test_color_grid_labels_never_share_a_font_across_threads(self):
        """Audit P1 (2026-11-01): a color grid's labels drew with the render stamp's font object, whose buffer, size,
        color and pen are unlocked shared state, so a grid made off the main thread (the checker's worker, a render
        loading a generated image) raced a stamping render or another grid. The labels now use a font of their own,
        one grid at a time; the stamp keeps blf_mono_font_render (as the sequencer and compositor keep theirs)."""
        gen = (SOURCE / "blenkernel/intern/image_gen.cc").read_text()
        text = function_bodies(gen)["checker_board_text"]
        self.assertNotIn("= blf_mono_font_render", gen)   # Named in a comment only, never used.
        self.assertLess(text.index("std::lock_guard lock(checker_text_mutex);"),
                        text.index("checker_text_font_ensure()"))
        self.assertLess(text.index("checker_text_font_ensure()"), text.index("BLF_size("))
        ensure = function_bodies(gen)["checker_text_font_ensure"]
        self.assertIn("checker_text_font = BLF_load_mono_default_private();", ensure)
        self.assertNotIn("BLF_enable", ensure)   # No flag set after the font is published.
        # Third audit (P2): the font is private from the moment it exists. Its flags are set before publication
        # under the load lock, the reloads take that lock and keep private fonts, and no lookup shares one.
        blf = (SOURCE / "blenfont/intern/blf.cc").read_text()
        bodies = function_bodies(blf)
        load = bodies["BLF_load_unique_flags"]
        self.assertLess(load.index("std::lock_guard lock(g_blf_load_mutex);"), load.index("font->flags |= flags;"))
        self.assertLess(load.index("font->flags |= flags;"), load.index("global_font[i] = font;"))
        for name in ("BLF_unload_all", "BLF_reset_fonts"):
            self.assertIn("std::lock_guard lock(g_blf_load_mutex);", bodies[name], name)
            self.assertIn("BLF_PRIVATE", bodies[name], name)
        for name in ("blf_search_by_filepath", "blf_search_by_mem_name", "BLF_unload"):
            self.assertIn("BLF_PRIVATE", bodies[name], name)
        default = (SOURCE / "blenfont/intern/blf_font_default.cc").read_text()
        self.assertIn("BLF_MONOSPACED | BLF_PRIVATE | BLF_NO_FALLBACK", function_bodies(default)["BLF_load_mono_default_private"])
        stamp = (SOURCE / "blenkernel/intern/image.cc").read_text()
        self.assertIn("const int mono = blf_mono_font_render;", stamp)   # The stamp's font stays the stamp's.

    def test_shaders_are_registered(self):
        private = (OVERLAY / "overlay_private.hh").read_text()
        for slot in range(4):
            self.assertIn(f'shader_clippable("overlay_uv_checker_{slot}")', private)
        self.assertIn('StaticShader uv_checker_missing = shader_clippable("overlay_uv_checker_missing");', private)
        cmake = (SOURCE / "draw/CMakeLists.txt").read_text()
        for name in ("overlay_uv_checker.hh", "overlay_uv_checker_patterns.cc", "infos/overlay_uv_checker_infos.hh",
                     "overlay_uv_checker_vert.glsl",
                     "overlay_uv_checker_frag.glsl", "overlay_uv_checker_missing_vert.glsl",
                     "overlay_uv_checker_missing_frag.glsl"):
            self.assertIn(name, cmake)
        infos = (OVERLAY / "shaders/infos/overlay_uv_checker_infos.hh").read_text()
        for slot in range(4):
            self.assertIn(f"VERTEX_IN(2, float2, cu{slot})", infos)
        for variant in ("overlay_uv_checker_0_clipped", "overlay_uv_checker_3_clipped",
                        "overlay_uv_checker_missing_clipped"):
            self.assertIn(f"CREATE_INFO_VARIANT({variant},", infos)

    def test_heat_ramp_matches_the_shader(self):
        frag = (OVERLAY / "shaders/overlay_uv_checker_frag.glsl").read_text()
        self.assertIn("float hue = (2.0f / 3.0f) * (1.0f - clamp(t, 0.0f, 1.0f));", frag)
        for t, expected in ((0.0, (0, 0, 1)), (0.5, (0, 1, 0)), (1.0, (1, 0, 0))):
            for got, want in zip(uv_checker_card.heat_color(t)[:3], expected):
                self.assertAlmostEqual(got, want, places=6)


class Controls(unittest.TestCase):
    def test_reached_from_the_menu_director_header_and_f3(self):
        self.assertIn("uv_checker", chat_destinations.TOP_LEVEL)
        module = (ADDON / "uv_checker.py").read_text()
        for term in ("chat_destinations.register(", "chat_destinations.unregister(\"uv_checker\")",
                     "bpy.types.VIEW3D_HT_header.append(_draw_header)", "bpy.types.VIEW3D_HT_header.remove(",
                     "bpy.types.VIEW3D_MT_view.append(_draw_view_menu)", "bpy.types.VIEW3D_MT_view.remove(",
                     'bl_idname = "angelo.uv_checker_toggle"', 'bl_idname = "angelo.uv_checker"'):
            self.assertIn(term, module)
        director = (ADDON / "director.py").read_text()
        self.assertLess(director.index("TOPBAR_MT_editor_menus.draw_collapsible(context, self.layout)"),
                        director.index("uv_checker.draw_director(self.layout, context)"))
        init = (ADDON / "__init__.py").read_text()
        self.assertIn("uv_checker.register()", init)
        self.assertIn("uv_checker.unregister()", init)
        for keymap in (ROOT / "scripts/presets/keyconfig").rglob("*.py"):
            self.assertNotIn("uv_checker", keymap.read_text(), keymap)


def settings(**changes):
    values = dict(show=True, pattern="COLOR_GRID", uv_map="ACTIVE", uv_map_name="", scale=1.0, opacity=1.0,
                  texture_size=1024, target_density=512.0, use_selected_only=False, show_flipped=False)
    values.update(changes)
    return SimpleNamespace(**values)


class Card(unittest.TestCase):
    def setUp(self):
        self.state = settings()
        patcher = mock.patch.object(uv_checker_card.UVCheckerCard, "settings", property(lambda card: self.state))
        patcher.start()
        self.addCleanup(patcher.stop)
        redraw = mock.patch.object(uv_checker_card, "_redraw_views")
        redraw.start()
        self.addCleanup(redraw.stop)
        self.card = uv_checker_card.UVCheckerCard(1, ["UVMap", "Lightmap"])
        self.host = SimpleNamespace(redraw=lambda: None, close=mock.Mock())

    def plan(self, width=1280, height=900):
        return self.card.layout(VIEW, width, height, 1.0, kit.UiState())

    def actions(self, plan):
        return {action for _rect, action, _value in plan.hits}

    def test_grid_and_checker_offer_repeats_density_offers_its_size_and_target(self):
        for pattern in ("COLOR_GRID", "CHECKER", "TEXEL_DENSITY", "STRETCH"):
            self.state.pattern = pattern
            plan = self.plan()
            actions = self.actions(plan)
            self.assertTrue({"show", "pattern", "objects", "uv_open", "flipped", "close"} <= actions, pattern)
            density = pattern == "TEXEL_DENSITY"
            self.assertEqual({"texture", "target"} <= actions, density, pattern)
            self.assertEqual("scale" in actions, not density, pattern)
            legend = [op for op in plan.ops if op[0] == "kit" and op[1] == "uv_heat_legend"]
            self.assertEqual(bool(legend), pattern in {"TEXEL_DENSITY", "STRETCH"}, pattern)
            x, y, w, h = plan.card
            for (rx, ry, rw, rh), action, _value in plan.hits:
                self.assertTrue(x <= rx and rx + rw <= x + w + 0.5 and y <= ry and ry + rh <= y + h + 0.5, action)

    def test_a_small_region_still_fits(self):
        self.state.pattern = "TEXEL_DENSITY"
        plan = self.plan(520, 420)
        x, y, w, h = plan.card
        self.assertTrue(0 <= x and x + w <= 520 and 0 <= y and y + h <= 420, plan.card)

    def test_actions_write_only_the_views_setting(self):
        for action, value, field, expected in (("show", None, "show", False), ("pattern", "STRETCH", "pattern", "STRETCH"),
                                               ("scale", "8", "scale", 8.0), ("texture", "4096", "texture_size", 4096),
                                               ("target", "1024", "target_density", 1024.0),
                                               ("objects", "SELECTED", "use_selected_only", True),
                                               ("flipped", None, "show_flipped", True),
                                               ("uv_map", "RENDER", "uv_map", "RENDER")):
            self.card.act(action, value, self.host)
            self.assertEqual(getattr(self.state, field), expected, action)
        self.card.act("uv_map", "named:Lightmap", self.host)
        self.assertEqual((self.state.uv_map, self.state.uv_map_name), ("NAMED", "Lightmap"))
        self.assertEqual(self.card.uv_choice(), "named:Lightmap")
        # A name is data: offered as it is, never translated.
        self.assertIn(("named:Lightmap", "Lightmap"), self.card.uv_options())

    def test_meshes_without_the_map_are_named_on_the_card(self):
        counts = []

        def missing(settings):
            counts.append(settings.uv_map)
            return (5, ["Cube", "Rock", "Tree"]) if settings.uv_map == "NAMED" else (0, [])
        card = uv_checker_card.UVCheckerCard(1, ["Atlas"], missing)
        self.assertEqual(card.missing, (0, []))
        notices = lambda: [op for op in card.layout(VIEW, 1280, 900, 1.0, kit.UiState()).ops
                           if op[0] == "kit" and op[1] == "notice"]
        self.assertEqual(notices(), [])
        card.act("uv_map", "named:Atlas", self.host)
        self.assertEqual(card.missing[0], 5)
        [notice] = notices()
        self.assertIn("5", notice[3]["title"])
        self.assertIn("Cube, Rock, Tree, …", notice[3]["title"])
        self.state.show = False   # Off: nothing to explain.
        card._version += 1
        self.assertEqual(notices(), [])

    def test_the_count_follows_the_scene_only_while_open_and_never_per_redraw(self):
        counted = []
        card = uv_checker_card.UVCheckerCard(1, [], lambda settings: counted.append(1) or (len(counted), ["Cube"]))
        timers = []
        host = SimpleNamespace(redraw=lambda: None, close=lambda how="": None,
                               after=lambda seconds, function: timers.append(function))
        handlers = SimpleNamespace(depsgraph_update_post=[])
        fake_bpy = SimpleNamespace(app=SimpleNamespace(handlers=handlers))
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy}):
            card.opened(host)
            self.assertEqual(len(handlers.depsgraph_update_post), 1)
            opened_count = len(counted)
            for _ in range(5):   # Redraws and idle timer passes count nothing.
                card.version()
                card.layout(VIEW, 1280, 900, 1.0, kit.UiState())
                timers[0]()
            self.assertEqual(len(counted), opened_count)
            for _ in range(3):   # A burst of depsgraph updates: one count on the next timer pass.
                handlers.depsgraph_update_post[0](None, None)
            timers[0]()
            self.assertEqual(len(counted), opened_count + 1)
            card.closed("closed")
            self.assertEqual(handlers.depsgraph_update_post, [])

    def test_a_view_that_is_gone_closes_the_card(self):
        self.state = None
        self.card.act("pattern", "CHECKER", self.host)
        self.host.close.assert_called_once_with("lost")
        self.plan()   # Still lays out (the defaults) until it closes.


class AgentLook(unittest.TestCase):
    def test_uv_checker_is_a_closed_look_of_angelo_view(self):
        self.assertIn("UV_CHECKER", view_spec.SHADINGS)
        self.assertIn("UV_CHECKER", view_spec.VIEW_TOOLS[0]["inputSchema"]["properties"]["shading"]["enum"])
        self.assertIn("UV_CHECKER a color grid on each mesh's active UVs", view_spec.VIEW_TOOLS[0]["description"])

    def test_only_its_own_look_shows_the_checker(self):
        """A person's own checker never colours an agent's other views."""
        for shading in view_looks.SHADING:
            settings_ = dict(view_looks.UV_CHECKER.get(shading, view_looks.UV_CHECKER_OFF))
            self.assertEqual(settings_["show"], shading == "UV_CHECKER", shading)
            self.assertIn(("View3DUVChecker", "show"), view_looks.display_keys(shading), shading)
        look = dict(view_looks.UV_CHECKER["UV_CHECKER"])
        self.assertEqual((look["pattern"], look["uv_map"], look["use_selected_only"]), ("COLOR_GRID", "ACTIVE", False))
        for module in ("view_capture.py", "view_motion.py"):
            self.assertIn("UV_CHECKER.get(shading, ", (ADDON / module).read_text(), module)


class Catalogues(unittest.TestCase):
    def test_interface_text_is_in_every_catalogue(self):
        keys = []
        for module in ("uv_checker.py", "uv_checker_card.py"):
            for node in ast.walk(ast.parse((ADDON / module).read_text())):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"n_", "rpt_", "iface_"}
                        and node.args and isinstance(node.args[0], ast.Constant)):
                    context = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else ""
                    keys.append((context, node.args[0].value))
        keys += [("Operator", "Toggle UV Checker"), ("Operator", "UV Checker")]
        self.assertGreater(len(keys), 30)
        self.assertEqual(untranslated(tuple(sorted(set(keys)))), [])


if __name__ == "__main__":
    unittest.main()
