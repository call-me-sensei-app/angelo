# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Director's Flows and Add step open Angelo's own popup, never Blender's (1.0.5, ``flow_menu``).

On a stand-in ``bpy``, against ``doc/angelo/studio_entry_points.rst``. Covered: in Director the Flows button and the
Generation Flows pill open Angelo's popup (Manual keeps its tab), whose rows are exactly the registry's Generation
Flows rows, New Flow then *Start from a template* then *Your flows*, a tile only on New Flow; it hangs under the
button inside its editor, at least 320 wide, scrolling when short, with why lines wrapped and never cut, in the
popup look (CARD, RADIUS_PANEL, ROW_HEIGHT, FOCUS ring); the flow bar's Add step opens it with every flow step type,
each adding its node at the pointer; the bar says what the hidden sidebar said (step count, Running with Stop, the
flow's error) and links back to Media Studio with the shared pill; the popup's modal handler is classified
(``input_policy``). The running app's proof is ``test_blender_flow_menu``; the bar itself also
``test_native_flow_window``'s.
"""

import ast
import contextlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})   # Any Blender base class: a plain class.
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text, locale="en_US"),
                              handlers=handlers, background=True)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.data = SimpleNamespace(node_groups=[])
    return bpy, {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
                 "bpy.app.handlers": handlers}


BPY, MODULES = fake_bpy()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import (chat_add_menu, flow_menu, flow_nodes, flow_open, flow_start, flow_starters, flow_window,
                              input_policy)


def measure(text, size):
    return len(text) * size * 0.55


def source(name):
    return (ADDON / name).read_text(encoding="utf-8")


def function(name, module):
    return source(module).split("\ndef %s(" % name, 1)[1].split("\ndef ", 1)[0]



@contextlib.contextmanager
def stubbed(**stubs):
    """``angelo_agent.<name>`` replaced for the block. ``from . import name`` reads the package's attribute before
    ``sys.modules``, so a module another test already imported must be swapped on the package too."""
    import angelo_agent
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(sys.modules, dict(MODULES, **{
            "angelo_agent." + name: module for name, module in stubs.items()})))
        for name, module in stubs.items():
            stack.enter_context(mock.patch.object(angelo_agent, name, module, create=True))
        yield

class FlowWindowTests(unittest.TestCase):
    def test_add_step_opens_angelos_step_menu_not_blenders_add_menu(self):
        act = function("act", "flow_window.py")
        self.assertIn("bpy.ops.angelo.flow_menu('INVOKE_DEFAULT', menu=flow_menu.STEPS", act)
        self.assertNotIn("NODE_MT_add", source("flow_window.py"))

    def test_the_bar_links_back_to_media_studio_with_the_shared_pill(self):
        plan = flow_window.layout(1400, 900, 1.0, measure, "Image to 3D")
        (x, y, w, h), action, label = plan["link"]
        self.assertEqual(action, "studio")
        self.assertEqual(h, flow_window.PILL_H)
        self.assertEqual(x, flow_window.PAD, "on the left: the way back (rev 3)")
        self.assertLess(x + w, plan["title"][0], "the flow's name after it")
        self.assertLessEqual(plan["title"][0] + plan["title_width"], plan["buttons"][0][0][0])
        act = function("act", "flow_window.py")
        self.assertIn("ANGELO_OT_open_media_studio", act)
        self.assertIn("toggle_studio(mode='OPEN')", act, "never closes an open Media Studio")

    def test_a_narrow_bar_drops_the_pills_label_before_anything_overlaps(self):
        wide = lambda text, size: len(text) * size * 0.75   # A long language.
        plan = flow_window.layout(900, 900, 2.0, wide, "Image to reference to 3D")
        (x, y, w, h), _, label = plan["link"]
        self.assertEqual(label, "", "icon only (studio_entry_points.rst, section 4)")
        self.assertLess(x + w, plan["title"][0])
        self.assertLessEqual(plan["title"][0], plan["buttons"][0][0][0])
        roomy = flow_window.layout(2400, 900, 1.0, wide, "Image to 3D")["link"][2]
        self.assertTrue(roomy, "labelled when there is room")

    def test_the_bar_says_what_the_hidden_sidebar_said_and_offers_stop_while_running(self):
        for running in (False, True):
            plan = flow_window.layout(1400, 900, 1.0, measure, "Image to 3D", running, link=False)
            actions = [action for _, action, _, _ in plan["buttons"]]
            self.assertEqual(actions, (["stop"] if running else []) + ["add", "run"])
            rects = [rect for rect, *_ in plan["buttons"]]
            for (x, y, w, h), (nx, *_rest) in zip(rects, rects[1:]):
                self.assertLess(x + w, nx)
            self.assertAlmostEqual(rects[-1][0] + rects[-1][2], 1400 - flow_window.PAD)
            self.assertLessEqual(plan["title"][0] + plan["title_width"], rects[0][0])
            self.assertLess(plan["status"][1], plan["title"][1], "the status line under the name")
        status = function("status", "flow_window.py")
        for said in ('entry.get("error")', "flow_host.active_runs(tree.name)", "flow_open.size_text(tree)"):
            self.assertIn(said, status)

    def test_the_bar_mirrors_in_a_right_to_left_language_as_every_angelo_canvas(self):
        self.assertIn("canvas_rtl.begin(region.width)", function("paint", "flow_window.py"))
        self.assertIn("canvas_rtl.end()", function("paint", "flow_window.py"))
        self.assertIn("canvas_rtl.pointer_x(event.mouse_x - region.x, region.width)", function("_target", "flow_window.py"))

    def test_the_flow_editor_is_angelo_owned_so_no_region_tabs_show(self):
        prepare = function("prepare_area", "flow_window.py")
        self.assertLess(prepare.index("area.angelo_owned = True"), prepare.index("chrome_guard.turn_off(window, area"),
                        "flagged first: hiding the regions rebuilds the zones without the tabs")
        self.assertIn('hasattr(area, "angelo_owned")', prepare, "a build without the flag still opens flows")
        cpp = (ROOT / "source/blender/editors/screen/area.cc").read_text()
        self.assertIn("if (is_hidden && area->runtime.angelo_owned)", cpp)
        dna = (ROOT / "source/blender/makesdna/DNA_screen_types.h").read_text()
        runtime = dna.split("struct ScrArea_Runtime {", 1)[1].split("};", 1)[0]
        self.assertIn("char angelo_owned = 0;", runtime, "runtime only: reset on read, never a saved flag bit")
        self.assertNotIn("ANGELO", dna.split("enum eScrArea_Flag", 1)[1].split("};", 1)[0])
        rna = (ROOT / "source/blender/makesrna/intern/rna_screen.cc").read_text()
        self.assertIn('prop = RNA_def_property(srna, "angelo_owned", PROP_BOOLEAN, PROP_NONE);', rna)
        self.assertIn('RNA_def_property_boolean_funcs(prop, "rna_Area_angelo_owned_get", "rna_Area_angelo_owned_set");', rna)
        self.assertIn("->runtime.angelo_owned = value", rna)
        owned = rna.split('RNA_def_property(srna, "angelo_owned"', 1)[1].split("RNA_def_property(", 1)[0]
        self.assertIn("RNA_def_property_flag(prop, PROP_CONTEXT_UPDATE);", owned,
                      "its update takes a bContext: without the flag makesrna emits it as a Main/Scene update and the "
                      "generated rna_screen_gen.cc does not compile (1.0.5 release build)")
        self.assertIn("area->runtime = ScrArea_Runtime{};", (ROOT / "source/blender/blenkernel/intern/screen.cc")
                      .read_text(), "reset on read: a file never brings the mark back, Python sets it again")
        self.assertIn("area_dst->runtime.angelo_owned = area_src->runtime.angelo_owned;",
                      (ROOT / "source/blender/blenkernel/intern/screen.cc").read_text(), "copies stay Angelo-owned")
        self.assertIn("area.angelo_owned = True", function("_load_post", "flow_window.py"), "set again on load")
        self.assertIn('STREQ(ntree.idname, "ANGELO_GenerationFlowTree")',
                      (ROOT / "source/blender/editors/space_node/node_draw.cc").read_text(), "flow headers only")

    def test_flow_node_bodies_take_the_card_tokens_and_only_flow_trees_do(self):
        """A3: node_draw.cc swaps the theme for a flow tree's draw only, with card_kit's own values, and restores it."""
        import re
        from angelo_agent import card_kit as kit
        cpp = (ROOT / "source/blender/editors/space_node/node_draw.cc").read_text()
        theme = cpp.split("class AngeloFlowTheme {", 1)[1].split("\n};\n", 1)[0]
        self.assertIn('STREQ(ntree.idname, "ANGELO_GenerationFlowTree")', theme, "flow trees only")
        self.assertIn("theme_->tui = saved_ui_;", theme.split("~AngeloFlowTheme()", 1)[1], "restored after the draw")
        draw = cpp.split("static void draw_nodetree(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("const AngeloFlowTheme flow_theme(ntree);", draw.split("\n", 3)[2], "first, for the whole draw")
        soft = tuple(round(t * kit.TEAL_SOFT[3] + p * (1 - kit.TEAL_SOFT[3]), 3)
                     for t, p in zip(kit.TEAL_SOFT[:3], kit.PANEL[:3]))
        tokens = {tuple(round(c, 3) for c in kit.CARD[:3]), tuple(round(c, 3) for c in kit.PANEL[:3]),
                  tuple(round(c, 3) for c in kit.PANEL_HOVER[:3]), tuple(round(c, 3) for c in kit.LINE[:3]),
                  tuple(round(c, 3) for c in kit.TEXT[:3]), soft}
        used = {tuple(round(float(v), 3) for v in m) for m in re.findall(
            r"set\([^,]+, ([0-9.]+)f, ([0-9.]+)f, ([0-9.]+)f\)", theme)}
        self.assertTrue(used, "colours found")
        self.assertLessEqual(used, tokens, "every colour is a card_kit token (TEAL_SOFT composited over PANEL)")
        for wcol in ("wcol_regular", "wcol_tool", "wcol_menu", "wcol_toggle", "wcol_num", "wcol_text", "space_node.syntaxl"):
            self.assertIn(wcol, theme)

    def test_a_reopened_files_flow_window_is_still_a_flow_window(self):
        saved = SimpleNamespace(as_pointer=lambda: 77, screen={flow_open.MARK: True})
        other = SimpleNamespace(as_pointer=lambda: 78, screen={})
        self.assertTrue(flow_open.marked(saved), "its screen carries the mark: it gets its bar back")
        self.assertFalse(flow_open.marked(other))
        show = function("show", "flow_open.py")
        self.assertIn("target.screen[MARK] = True", show, "saved with the file")
        self.assertIn("flow_open.marked(w)", function("is_flow_window", "flow_window.py"))

    def test_the_start_page_fits_its_editor_and_never_cuts_a_why_line(self):
        def row(value, subtitle=""):
            return {"value": value, "subtitle": subtitle}
        rows = ([row(flow_open.NEW_ROW), row(flow_open.STARTER_ROW + "a", "one two three"),
                 row(flow_open.STARTER_ROW + "b", "x")] + [row(flow_open.TREE_ROW + str(i), "Your flow") for i in range(12)])
        wrap = lambda r: r["subtitle"].split()   # One word a line: a long why line wraps.
        with mock.patch.dict(sys.modules, dict(MODULES, **{"angelo_agent.flow_open": flow_open})):
            laid, left = flow_start.start_rows(rows, wrap, 1000, 2.0)
            small = flow_start.start_rows(rows[:5], wrap, 5000, 1.0)[1]
        self.assertLessEqual(sum(h for _, _, h in laid) + flow_start.ROW_H * 2.0, 1000 + 1e-6)
        self.assertEqual(len(laid) + left, len(rows), "every flow is either shown or counted")
        self.assertGreater(left, 0)
        first = next(lines for r, lines, _ in laid if r["value"].endswith("a"))
        self.assertEqual(first, ["one", "two", "three"], "every line of the why")
        tall = next(h for r, _, h in laid if r["value"].endswith("a"))
        self.assertEqual(tall, (flow_start.ROW_H + 2 * flow_start.LINE_H) * 2.0)
        self.assertEqual(small, 0)

    def test_run_all_waits_while_no_step_can_run_and_the_bar_names_it(self):
        tree = SimpleNamespace(name="F", nodes=[SimpleNamespace(name="3D model", bl_idname="ANGELO_FlowGenerate3DNode"),
                                                SimpleNamespace(name="Image", bl_idname="ANGELO_FlowImageNode")])
        states = {"3D model": {"state": "BLOCKED", "setup": "model"}}
        host = SimpleNamespace(node_state=lambda t, name: states.get(name))
        nodes = SimpleNamespace(TYPE_BY_IDNAME=flow_nodes.TYPE_BY_IDNAME, setup_need=lambda t, n: "choose",
                                SETUP_LABELS={"choose": "Choose a model to run"}, SETUP_TEXT={})
        with stubbed(flow_host=host, flow_nodes=nodes):
            self.assertEqual(flow_window.blocking(tree), ("3D model", "Choose a model to run"))
            states["3D model"] = {"state": "READY"}
            self.assertIsNone(flow_window.blocking(tree), "a step that can run: Run all is teal")
        paint = function("paint", "flow_window.py")
        self.assertIn('"run_blocked"', paint, "drawn disabled, its hit does nothing")
        self.assertIn("canvas_controls.tip(", paint, "the tooltip names the step")
        self.assertEqual(flow_window.BLOCKED_STEP, "{step}: {gate}")

    def test_glyphs_never_mirror_and_the_popup_stays_under_its_button(self):
        self.assertIn("canvas_rtl.upright(", function("draw_row", "chat_add_menu.py"))
        self.assertIn("canvas_rtl.upright(", function("_pill", "flow_window.py"))
        geometry = flow_menu.layout([{"kind": "row", "height": 44}], 300, 1000, 900)
        flipped = flow_menu.mirrored(geometry, 1000)
        x, y, w, h = flipped["rect"]
        self.assertEqual(1000 - x - w, geometry["rect"][0], "the mirror shows it where it was laid out")
        self.assertNotIn("pointer_x(state[\"anchor\"]", function("paint", "flow_menu.py"))

    def test_a_new_flow_is_stored_canonical_and_shown_in_the_persons_language(self):
        self.assertIn('new(flow_starters.starter(starter_id)["title"], ', function("from_starter", "flow_open.py"))
        self.assertIn("new(NEW_NAME, ", function("new_flow", "flow_open.py"))
        self.assertIn("new(FLOW_NAME, ", source("studio_to_flow.py"))
        self.assertIn("flow_label(tree.name)", function("menu_rows", "flow_open.py"), "your flows' rows")
        self.assertIn("flow_label(tree.name)", source("flow_window.py"), "the bar's title")

    def test_add_step_lists_every_step_type_and_adds_it_at_the_pointer(self):
        rows = [row for row in flow_window.step_rows() if row["kind"] == "row"]
        self.assertEqual({dict(row["properties"])["type"] for row in rows}, {cls.bl_idname for cls in flow_nodes.NODES},
                         "a new step type needs its Add step row")
        for row in rows:
            self.assertEqual(row["operator"], "node.add_node")
            self.assertTrue(dict(row["properties"])["use_transform"], "placed by the person, as Shift+A does")
            self.assertTrue(row["subtitle"], "each says what it adds")
        glyphs = {dict(row["properties"])["type"]: row["glyph"] for row in rows}
        self.assertEqual(glyphs["ANGELO_FlowGenerateImageNode"], glyphs["ANGELO_FlowImageNode"],
                         "a picture, as Generate 3D has the cube: the spark-frame is Media Studio's (section 2)")
        self.assertFalse(any("recipe" in row["subtitle"].lower() for row in rows), "a reusable chain is a flow")
        sections = [row["label"] for row in flow_window.step_rows() if row["kind"] == "section"]
        self.assertEqual(sections, [flow_window.INPUTS, flow_window.GENERATE, flow_window.MORE])

    def test_every_step_glyph_is_drawn(self):
        calls = []
        view = SimpleNamespace(shape=lambda *a: calls.append("shape"), stroke=lambda *a: calls.append("stroke"))
        for row in flow_window.step_rows():
            if row["kind"] == "row" and row["glyph"].startswith("step_"):
                calls.clear()
                self.assertTrue(flow_window.draw_step_glyph(view, row["glyph"], 0, 0))
                self.assertGreater(len(calls), 2, row["glyph"])
        self.assertFalse(flow_window.draw_step_glyph(view, "flow", 0, 0), "the + menu's own glyphs stay its own")


class FlowsMenuTests(unittest.TestCase):
    def setUp(self):
        BPY.data.node_groups = [SimpleNamespace(name=name, bl_idname=flow_nodes.TREE, nodes=[1, 2, 3])
                                for name in ("Hero", "chair")]
        self.addCleanup(setattr, BPY.data, "node_groups", [])

    def test_directors_flows_opens_angelos_popup_never_blenders_menu(self):
        show = source("flow_open.py").split("class ANGELO_OT_show_flows", 1)[1].split("\nCLASSES", 1)[0]
        manual = show.index('studio_workspaces.show(context.window, "flows", toggle=True)')
        self.assertGreater(show.index("bpy.ops.angelo.flow_menu('INVOKE_DEFAULT')"), manual,
                           "Manual's home is its tab; Director's is the popup (studio_entry_points.rst, section 3)")
        self.assertNotIn("call_menu", show)
        self.assertNotIn("show_in_plus_menu", source("flow_open.py"), "one home per mode")
        top_bar = function("draw_top_bar", "flow_open.py")
        self.assertIn("flow_menu.is_open(context.window)", top_bar, "pressed while its popup is open")
        self.assertIn("bpy.ops.angelo.show_flows()", source("asset_browser_entry.py"), "the pill opens it too")

    def test_its_rows_are_the_plus_menus_flow_rows_with_section_labels(self):
        with mock.patch.dict(sys.modules, MODULES):
            menu, plus = flow_menu.flow_rows(), flow_open.menu_rows()
        self.assertEqual([row for row in menu if row["kind"] not in {"section", "header"}], plus,
                         "one registry, never two lists")
        header = menu[0]
        self.assertEqual((header["kind"], header["label"], header["subtitle"], header["glyph"]),
                         ("header", flow_open.MENU_LABEL, flow_open.MENU_WHY, "flow"), "section 0's why line, verbatim")
        self.assertEqual(flow_open.MENU_WHY, "Go further: chain generation steps into a flow you can rerun")
        kinds = [(row["kind"], row.get("label")) for row in menu[1:]]
        self.assertEqual(kinds[0], ("row", flow_open.NEW))
        self.assertEqual(kinds[1], ("section", flow_open.TEMPLATES), "New Flow, then Start from a template")
        self.assertEqual(kinds[2 + len(flow_starters.STARTERS)], ("section", flow_open.YOURS), "then Your flows")
        self.assertEqual([label for kind, label in kinds[-2:]], ["chair", "Hero"])
        rows = [row for row in menu if row["kind"] == "row"]
        self.assertEqual(rows[0]["glyph"], "add", "New Flow: a 16 px ADD glyph, no tile (section 2)")
        self.assertTrue(all(row["glyph"] is None and row["operator"] for row in rows[1:]),
                        "a template or one of your flows has no tile (studio_entry_points.rst, section 2)")
        self.assertEqual([row["subtitle"] for row in rows[-2:]], ["3 steps", "3 steps"],
                         "its size, never 'Your flow' again under Your flows (review P2)")
        with mock.patch.dict(sys.modules, MODULES):
            self.assertEqual(flow_open.size_text(SimpleNamespace(name="e", nodes=[])), "Empty", "never '0 steps'")

    def test_no_section_for_an_empty_list_of_flows(self):
        BPY.data.node_groups = []
        with mock.patch.dict(sys.modules, MODULES):
            labels = [row["label"] for row in flow_menu.flow_rows() if row["kind"] == "section"]
        self.assertEqual(labels, [flow_open.TEMPLATES])

    def test_it_hangs_under_the_button_inside_its_editor(self):
        rows = [{"kind": "row", "height": 46}] * 4
        geometry = flow_menu.layout(rows, 300, 700, 900)
        x, y, w, h = geometry["rect"]
        self.assertEqual((x, y + h), (300, 900 - flow_menu.GAP), "its left edge at the button, under the top bar")
        self.assertEqual(w, flow_menu.WIDTH)
        self.assertEqual(geometry["max_scroll"], 0)
        x, *_ = flow_menu.layout(rows, 650, 700, 900)["rect"]
        self.assertEqual(x + flow_menu.WIDTH, 700 - flow_menu.EDGE, "moved left to stay inside the editor")
        narrow = flow_menu.layout(rows, 10, 200, 900)["rect"]
        self.assertLessEqual(narrow[2], 200 - 2 * flow_menu.EDGE, "narrower only when the editor is")
        self.assertGreaterEqual(flow_menu.layout(rows, 10, 2 * flow_menu.EDGE + flow_menu.MIN_WIDTH, 900)["rect"][2],
                                flow_menu.MIN_WIDTH)
        below_bar = flow_menu.layout(rows, 300, 700, 900, top=flow_window.HEIGHT)["rect"]
        self.assertEqual(below_bar[1] + below_bar[3], 900 - flow_window.HEIGHT - flow_menu.GAP, "Add step's: under the bar")

    def test_a_short_editor_scrolls_the_list_and_rows_stay_hittable(self):
        rows = [{"kind": "section", "label": "x"}] + [{"kind": "row", "height": 46}] * 12
        geometry = flow_menu.layout(rows, 20, 700, 300, scroll=10_000)
        self.assertGreater(geometry["max_scroll"], 0)
        self.assertEqual(geometry["scroll"], geometry["max_scroll"], "clamped")
        lx, ly, lw, lh = geometry["list_rect"]
        last = geometry["rows"][-1]
        self.assertEqual(flow_menu.row_at(geometry, rows, lx + 10, last[1] + 5), len(rows) - 1)
        self.assertIsNone(flow_menu.row_at(geometry, rows, lx + 10, ly + lh + 5), "above the list: nothing")
        top = flow_menu.layout(rows, 20, 700, 300)
        sx, sy, sw, sh = top["rows"][0]
        self.assertIsNone(flow_menu.row_at(top, rows, sx + 5, sy + 5), "a section label is not a row")

    def test_both_menus_draw_rows_with_the_plus_menus_row(self):
        draw = function("paint", "flow_menu.py")
        self.assertIn("chat_add_menu.draw_row(chat_view, row, rect, glyphs=draw_glyph)", draw)
        self.assertIn("wrap_rows(chat_view, items,", draw)
        for token in ("kit.CARD", "tk.RADIUS_PANEL", "ui_kit.focus_ring("):   # studio_entry_points.rst, section 4.
            self.assertIn(token, draw)
        self.assertEqual(flow_menu.row_height({"kind": "row"}), flow_menu.tk.ROW_HEIGHT)
        plus = function("draw", "chat_add_menu.py")
        self.assertIn("draw_row(view, row, (rx, ry, rw, rh))", plus)
        self.assertIn("wrap_subtitles(view, rows,", plus)

    def test_why_lines_wrap_and_are_never_cut(self):
        view = SimpleNamespace(_flow_wrap=lambda text, width, size: [w + " " for w in text.split()])
        rows = [{"kind": "row", "glyph": None, "subtitle": "one two three four five"}]
        with mock.patch.object(chat_add_menu, "_shown", lambda row, key: row.get(key, "")):
            flow_menu.wrap_rows(view, rows, 320)
        self.assertEqual(rows[0]["subtitle_lines"], ["one", "two", "three", "four", "five"])
        self.assertEqual(rows[0]["height"], flow_menu.tk.ROW_HEIGHT + 4 * chat_add_menu.LINE)

    def test_a_pill_opens_the_popup_under_itself(self):
        invoke = source("flow_menu.py").split("    def invoke(", 1)[1].split("    def modal(", 1)[0]
        self.assertIn("BELOW_POINTER", invoke)
        self.assertIn("open_from_top_bar", source("flow_menu.py"), "the welcome's slide opens it under Flows")

    def test_a_row_runs_with_an_undo_step_in_the_menus_editor(self):
        run = function("_run", "flow_menu.py")
        self.assertIn("temp_override(window=window, screen=window.screen, area=area, region=region)", run)
        self.assertIn("('INVOKE_DEFAULT', True, **dict(row.get(\"properties\") or ()))", run)


class VocabularyTests(unittest.TestCase):
    # The agent's own modelling recipes (angelo_apply_recipe / angelo_expand_recipe: chat titles, approval cards,
    # detail keys) are a different thing from flows and keep their noun; flow_setup's gate is B's (renamed there).
    AGENT_RECIPES = {"Recipe edit", "Planned a recipe", "Expanding {recipe} recipe", "{recipe} recipe · {summary}",
                     "Plan the {recipe} recipe (read-only)", "Recipe inputs, defaults included", "Recipe",
                     "Choose a recipe to run"}

    def test_a_reusable_chain_is_a_flow_never_a_recipe(self):
        sys.path.insert(0, str(ROOT / "scripts/angelo"))
        from i18n import po, sources
        _, entries = po.read(str(sources.POT))
        flows = [e for e in entries if "recipe" in e.id.lower() and e.id not in self.AGENT_RECIPES
                 and any("flow" in s.lower() or "recipe" in s.lower() for s in e.extracted)]
        self.assertEqual([e.id for e in flows], [], "studio_entry_points.rst, section 1: a reusable chain is a flow")


class InputTests(unittest.TestCase):
    def test_the_menu_holds_input_while_open(self):
        self.assertIn(flow_menu.IDENTITY, input_policy.HOLDING_TOOLS)
        for module, identity in (("flow_menu.py", flow_menu.IDENTITY),):
            names = [node.value.value for node in ast.walk(ast.parse(source(module)))
                     if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "bl_idname" for t in node.targets)]
            self.assertEqual(names, [identity])


class TopBarButtonTests(unittest.TestCase):
    """1.0.5 blocker: in Director a real click on the top bar's Flows found no editor under it (the top bar is a
    global area ``screen.areas`` leaves out), so the popup was refused. Geometry from that run's report."""

    def window(self):
        def area(kind, x, y, w, h):
            return SimpleNamespace(type=kind, x=x, y=y, width=w, height=h)
        self.view, self.chat = area("VIEW_3D", 2, 2, 1488, 1380), area("CONSOLE", 1493, 2, 799, 1380)
        return SimpleNamespace(screen=SimpleNamespace(areas=[self.view, self.chat]), height=1408, width=2294)

    def test_the_editor_under_the_top_bar_button_is_found_from_the_editors_edges(self):
        window = self.window()
        self.assertIsNone(flow_menu.target_area(window, 2024, 1395), "the top bar is no editor")
        self.assertIs(flow_menu.under_top_bar(window, 2024), self.chat)
        self.assertIs(flow_menu.under_top_bar(window, 2031), self.chat)
        self.assertIs(flow_menu.under_top_bar(window, 400), self.view)
        self.assertIs(flow_menu.under_top_bar(window, 5000), self.view, "off every editor: the largest")

    def test_both_routes_use_it(self):
        invoke = source("flow_menu.py").split("    def invoke(self, context, event):", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("area = under_top_bar(window, event.mouse_x)", invoke)
        self.assertNotIn("context.region", invoke, "never the clicked region's edges")
        self.assertIn("or under_top_bar(window, x)", function("open_from_top_bar", "flow_menu.py"))


if __name__ == "__main__":
    unittest.main()
