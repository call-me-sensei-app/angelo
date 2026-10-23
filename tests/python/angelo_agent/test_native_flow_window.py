# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A flow's own window and its nodes as a person first sees them (1.0.5 review): Angelo's bar in place of the stock
node editor chrome (``flow_window``), text fields that never cut their text (``flow_text_field``) and a step that
only waits for an account or a model said neutrally, never as a red failure (``flow_nodes``). The drawn result is
the GUI suites' (``test_blender_studio_discoverability``, ``test_blender_chat_start_states``)."""

import importlib
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def import_with_bpy_stub(test, name):
    """Import an add-on module that only names ``bpy`` at import, with an empty stand-in removed afterwards."""
    added = []
    if "bpy" not in sys.modules:
        bpy = types.ModuleType("bpy")
        props, bpy_types = types.ModuleType("bpy.props"), types.ModuleType("bpy.types")
        props.StringProperty = lambda **kwargs: None
        bpy_types.Operator = object
        bpy.props, bpy.types = props, bpy_types
        for key, value in (("bpy", bpy), ("bpy.props", props), ("bpy.types", bpy_types)):
            sys.modules[key] = value
            added.append(key)
    module = importlib.import_module("angelo_agent." + name)
    for key in added + ["angelo_agent." + name]:
        test.addCleanup(sys.modules.pop, key, None)
    return module


class TextFieldTests(unittest.TestCase):
    def setUp(self):
        self.field = import_with_bpy_stub(self, "flow_text_field")
        self.measure = lambda text: 7 * len(text)   # 7 units a character.

    def test_a_long_text_wraps_by_words_and_keeps_every_word(self):
        text = "Turn the image into a clean reference with even light, true colours and materials."
        lines = self.field.wrap(text, 140, self.measure)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(self.measure(line) <= 140 for line in lines))
        self.assertEqual(" ".join(lines), text)

    def test_text_without_spaces_breaks_by_characters(self):
        text = "参考画像を作ってから三次元モデルにする" * 3
        lines = self.field.wrap(text, 70, self.measure)
        self.assertTrue(all(self.measure(line) <= 70 for line in lines))
        self.assertEqual("".join(lines), text)

    def test_empty_text_is_one_empty_line(self):
        self.assertEqual(self.field.wrap("", 100, self.measure), [""])

    def test_every_flow_text_field_goes_through_it(self):
        source = (ADDON / "flow_nodes.py").read_text()
        for prop in ("prompt", "text", "options_json"):
            self.assertIn('flow_text_field.draw(layout, self, "%s"' % prop, source)
        self.assertNotIn('layout.prop(self, "prompt"', source)


class BarTests(unittest.TestCase):
    def setUp(self):
        self.window = import_with_bpy_stub(self, "flow_window")

    def test_the_bar_has_the_title_then_add_step_and_run_all_at_the_right(self):
        plan = self.window.layout(1488, 1354, 1.0, lambda text, size: 7 * len(text), "Image to 3D")
        (add, add_action, _, add_style), (run, run_action, _, run_style) = plan["buttons"]
        self.assertEqual((add_action, run_action, run_style), ("add", "run", "primary"))
        self.assertLess(add[0] + add[2], run[0])
        self.assertAlmostEqual(run[0] + run[2], 1488 - self.window.PAD)
        self.assertEqual(plan["bar"][1] + plan["bar"][3], 1354)
        self.assertGreater(plan["title_width"], 0)

    def test_the_flow_window_hides_the_stock_chrome_and_flow_open_prepares_it(self):
        source = (ADDON / "flow_window.py").read_text()
        self.assertIn("chrome_guard.turn_off(window, area, chrome_guard.shown(area))", source)
        guard = (ADDON / "chrome_guard.py").read_text()
        for name in ("show_region_header", "show_region_ui", "show_region_toolbar"):
            self.assertIn('"%s"' % name, guard.split("CHROME = ", 1)[1].split("\n", 1)[0])
        self.assertIn("branded.prepare(target)", (ADDON / "flow_open.py").read_text())


class AssetBrowserEntryTests(unittest.TestCase):
    def test_every_asset_browser_gets_both_pills_and_its_list_states(self):
        source = (ADDON / "asset_browser_entry.py").read_text()
        self.assertIn("bpy.types.SpaceFileBrowser.draw_handler_add(_draw", source, "every library's header")
        self.assertIn("for region_type in ('HEADER', 'WINDOW'):", source, "header pills and the list's states")
        self.assertIn("asset_browser_states.draw(area, region", source, "the list draws a state, never an offer")
        self.assertNotIn("OFFER", source, "1.0.5: the always-on offer is replaced by the real empty state")
        self.assertIn("browse_mode", source)
        labels = source.split("def header_labels(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('("" if narrow else iface_(FLOWS), "flows", "flow")', labels, "a narrow header: icon only")
        self.assertIn('n_("Generation Flows")', source, "full names (studio_entry_points.rst rev 3)")
        self.assertIn("ANGELO_OT_open_media_studio", source, "the shared opener when it is registered")
        self.assertIn("asset_browser_entry.register()", (ADDON / "studio_toggle.py").read_text())

    def test_every_asset_browser_library_menu_offers_both(self):
        """1.0.5 review SD2: a header too narrow for the pills hides them, so Library ▸ Media Studio / Generation Flows
        is the route at every width, in every Asset Browser."""
        source = (ADDON / "asset_browser_entry.py").read_text()
        self.assertIn("bpy.types.ASSETBROWSER_MT_library.append(draw_library_menu)", source)
        menu = source.split("\ndef draw_library_menu(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"angelo.open_media_studio"', menu)
        self.assertIn('"angelo.show_flows"', menu)
        self.assertIn('icon_value=studio_toggle.icon_id(False, glyph_tint.colour("menu_item"))', menu)
        self.assertIn('icon_value=flow_open.icon_id(glyph_tint.colour("menu_item"))', menu)
        self.assertNotIn("header_labels", menu, "never conditional on the header's width")

    def test_the_header_pills_never_cover_blenders_buttons_and_are_never_teal(self):
        """Coherence AB1/AB4 (1.0.5): the pills go in the free span Blender's own header items leave, measured in the
        interface language (a fixed width let a German label push a pill over the search field), and end left of
        Blender's search group. A header row reserving their width took no width, so the pills covered the search
        field (interim dbe022dddb9): nothing may reserve, the pills only use what is left. Labels when they fit, icons
        when those fit, else none; the fill is PANEL, PANEL_HOVER, TEAL_SOFT pressed."""
        source = (ADDON / "asset_browser_entry.py").read_text()
        labels = source.split("def header_labels(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("left, imports, right = blender_spans(space)", labels)
        self.assertIn("room = region_width - left - right", labels)
        self.assertIn("room = (room - imports) / 2", labels, "Import Settings sits centred in the free span")
        self.assertIn("narrow = room < reserved_width(full, scale)", labels)
        self.assertIn("room < reserved_width(icons, scale)", labels, "no room even for icons: no pills")
        spans = source.split("def blender_spans(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("pgettext_iface(name)", spans, "the menus are measured in the interface language")
        self.assertIn("'LOCAL', 'ESSENTIALS'", spans, "Import Settings only shows for other libraries")
        draw = source.split("def _draw(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("x = region.width - right - PAD * scale", draw, "the pills end left of Blender's search group")
        self.assertIn("h = min(H * scale, region.height - 2 * scale)", draw, "a pill never overhangs the header")
        self.assertNotIn("FILEBROWSER_HT_header.append", source, "a reserving header row takes no width")
        pill = source.split("def pill(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("kit.PANEL_HOVER if hover or pressed else kit.PANEL", pill)
        self.assertIn("kit.TEAL_SOFT", pill.split("if pressed:", 1)[1])
        self.assertNotRegex(source, r"kit\.TEAL\b(?!_)", "a header pill is never teal (spec §4)")


class StudioTabsTests(unittest.TestCase):
    def test_media_studio_and_both_tabs_are_angelo_owned_areas(self):
        """Part A's A2: an area Angelo draws whole is marked ``Area.angelo_owned`` (no region-toggle arrows) before
        its regions are hidden, since setting it re-inits the area; a build without the property is left alone."""
        helper = (ADDON / "editor_types.py").read_text().split("\ndef mark_angelo_owned(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('hasattr(area, "angelo_owned")', helper)
        opened = (ADDON / "studio_toggle.py").read_text().split("\ndef open_studio(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(opened.index("mark_angelo_owned(created, area)"), opened.index("show_region_header = False"))
        fill = (ADDON / "studio_workspaces.py").read_text().split("\ndef fill(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(fill.index("mark_angelo_owned(window, area)"), fill.index("chrome_guard.guard(area, window)"))

    def test_angelo_editors_keep_no_chrome_by_events_never_by_polling(self):
        """Coherence W2 (1.0.5): hidden once when made and on load, then kept by msgbus subscriptions on the
        editor's own flags (N, T, scripts); a timer that polled every window let chrome flash and undid presses."""
        guard = (ADDON / "chrome_guard.py").read_text()
        self.assertIn("bpy.msgbus.subscribe_rna(key=space.path_resolve(name, False)", guard)
        self.assertIn("bpy.msgbus.clear_by_owner(_owner)", guard.split("\ndef guard_all(", 1)[1].split("\ndef ", 1)[0])
        self.assertIn("load_post.append(_loaded)", guard)
        self.assertIn("return 0.05 if time.monotonic() < until else None", guard, "the settle timer is bounded")
        self.assertNotIn("persistent=True", guard)
        self.assertNotIn("keep_chrome_hidden", (ADDON / "studio_workspaces.py").read_text())
        self.assertIn("chrome_guard.guard(area, window)", (ADDON / "flow_window.py").read_text())

    def test_building_the_tabs_shows_each_step_for_one_pass_only(self):
        """A first launch saw the Media Studio tab flash for 1-2 s while the tabs were built (part D's preview): each
        step waited a fixed delay after its workspace switch. Now each waits exactly until the switch applies."""
        source = (ADDON / "studio_workspaces.py").read_text()
        build = source.split("\ndef build(", 1)[1].split("\ndef ", 1)[0]
        self.assertNotRegex(build, r"yield \.\d|yield [1-9]", "no fixed delay between steps")
        self.assertEqual(build.count("yield from _switch("), 2, "source and reorder go through _switch")
        self.assertEqual(build.count("yield from _switched(window, created)"), 1, "the duplicate's own switch")
        self.assertNotIn("window.workspace = ", build.split("finally:", 1)[0],
                         "no switch queued outside _switch: a stale one takes the duplicate's stored layout")
        self.assertIn("window.workspace = home", build.split("finally:", 1)[1], "ends where it was, whatever stops it")
        import ast
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "_switched")
        scope = {}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "studio_workspaces._switched", "exec"), scope)
        switched = scope["_switched"]
        window, target = types.SimpleNamespace(workspace="Layout"), "Media Studio"
        steps = switched(window, target)
        self.assertEqual(next(steps), 0.0)   # Not applied yet: one more pass.
        window.workspace = target
        self.assertEqual(list(steps), [], "applied: no further wait")
        with self.assertRaises(RuntimeError):
            list(switched(types.SimpleNamespace(workspace="Layout"), types.SimpleNamespace(name="Never")))

    def test_a_switch_is_never_queued_to_the_workspace_already_shown(self):
        """1.0.5 crash: a switch queued to the shown workspace stayed in Blender's notifier queue past
        workspace.duplicate, then applied the copy's stored layout (temp_layout_store) to the wrong workspace."""
        import ast
        source = (ADDON / "studio_workspaces.py").read_text()
        nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in {"_switch",
                                                                                                    "_switched"}]
        scope = {"_layout_idle": lambda: True}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "studio_workspaces", "exec"), scope)

        class Window:
            def __init__(self, shown):
                self.__dict__["workspace"], self.__dict__["sets"] = shown, []

            def __setattr__(self, name, value):
                self.sets.append(value)   # A queued switch; applied on the next pass below.

        window = Window("Layout")
        self.assertEqual(list(scope["_switch"](window, "Layout")), [], "already shown: nothing queued, no wait")
        self.assertEqual(window.sets, [])
        steps = scope["_switch"](window, "Media Studio")
        self.assertEqual(next(steps), 0.0)
        self.assertEqual(window.sets, ["Media Studio"])
        window.__dict__["workspace"] = "Media Studio"   # Blender applied it.
        self.assertEqual(list(steps), [])

    def test_one_routing_place_and_a_file_never_gets_a_deleted_tab_back(self):
        source = (ADDON / "studio_workspaces.py").read_text()
        self.assertIn('TABS = {"studio": "Media Studio", "flows": "Generation Flows"}', source)
        missing = source.split("def missing():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("kind not in had", missing, "a tab this file had and lost was deleted by the person")
        self.assertIn("Never a \".001\"", source)
        place = source.split("\ndef place(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("director.angelo_move_after(anchor=None)", place, "Director first, without switching to it")
        self.assertIn("workspace.angelo_move_after(anchor=anchor)", place, "then Media Studio, then Generation Flows")
        self.assertIn("for workspace in (studio, flows):", place, "the pair together, Media Studio first")
        self.assertIn("if not kinds and not _building and not in_order():", source, "a file out of order is fixed")
        self.assertIn('studio_workspaces.show(here, "studio", toggle=self.mode == \'TOGGLE\')',
                      (ADDON / "studio_toggle.py").read_text())
        flows = (ADDON / "flow_open.py").read_text()
        self.assertIn('studio_workspaces.show(context.window, "flows", toggle=True)', flows)
        self.assertIn('if studio_workspaces.show(window, "flows"):', flows, "a flow opens on the tab in Manual")
        self.assertIn("tab_kind(w) is None", (ADDON / "director_layout.py").read_text(),
                      "Manual's fallback workspace is never one of Angelo's tabs")


    def test_studio_and_flows_entries_wait_for_a_layout_change_never_act_mid_switch(self):
        # 35f5b544698 diagnosis: a Studio click in Director during the tab build took the window for Manual and left it
        # on the half-made Media Studio tab. Every Studio/Flows entry operator runs once the layout has settled.
        studio = (ADDON / "studio_toggle.py").read_text()
        for name in ("class ANGELO_OT_toggle_studio", "class ANGELO_OT_open_media_studio"):
            body = studio.split(name, 1)[1].split("\nclass ", 1)[0].split("    def execute(", 1)[1]
            self.assertIn("layout_after.busy()", body.split("open_media_studio(kind")[0].split("studio_workspaces.show(")[0], name)
            self.assertIn("layout_after.again(self.bl_idname", body, name)
        flows = (ADDON / "flow_open.py").read_text().split("class ANGELO_OT_show_flows", 1)[1].split("\n\n\n", 1)[0]
        self.assertLess(flows.index("layout_after.busy()"), flows.index("studio_workspaces.show("))
        self.assertIn("layout_after.run_after(context.window, open_flows", flows)

    def test_a_deferred_entry_runs_once_the_layout_settles_and_never_waits_twice(self):
        import ast
        source = (ADDON / "layout_after.py").read_text()
        tree = ast.parse(source)
        keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("busy", "run_after")]
        held, timers, ran = [object()], [], []
        modules = types.SimpleNamespace(director=types.SimpleNamespace(_timer=None),
                                        ui=types.SimpleNamespace(_layout_timer=held[0]))
        window = types.SimpleNamespace(as_pointer=lambda: 7)
        bpy = types.SimpleNamespace(app=types.SimpleNamespace(timers=types.SimpleNamespace(
            register=lambda f, first_interval: timers.append(f))),
            context=types.SimpleNamespace(window_manager=types.SimpleNamespace(windows=[window])))
        scope = {"time": types.SimpleNamespace(monotonic=lambda: 0.0), "LIMIT": 10.0, "deferred": [],
                 "_running": [False]}
        exec(compile(ast.Module(keep, []), "layout_after.py", "exec"), scope)
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            with mock.patch("builtins.__import__", side_effect=lambda name, g=None, l=None, f=(), lv=0: modules
                            if lv == 1 and name == "" else importlib.__import__(name, g, l, f, lv)):
                self.assertTrue(scope["run_after"](window, lambda w: ran.append(scope["busy"]()), "studio"))
                self.assertEqual(ran, [], "deferred while a layout change holds the lock")
                self.assertEqual(timers[0](), 0.05, "still waiting")
                modules.ui._layout_timer = None
                self.assertIsNone(timers[0]())
                self.assertEqual(ran, [False], "ran once settled, and busy() is False while it runs (never re-waits)")

    def test_a_deferred_entry_runs_its_operator_from_either_idname_form(self):
        # A running operator's self.bl_idname is Blender's C form (ANGELO_OT_toggle_studio): again() raised on it, so
        # a Studio click during a layout change did nothing (interim dbe022dddb9 line, studio_discoverability).
        import ast
        tree = ast.parse((ADDON / "layout_after.py").read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "again")
        calls = []
        ops = types.SimpleNamespace(angelo=types.SimpleNamespace(toggle_studio=lambda **kw: calls.append(kw)))
        bpy = types.SimpleNamespace(ops=ops, context=types.SimpleNamespace(temp_override=lambda **kw: mock.MagicMock()))
        scope = {}
        exec(compile(ast.Module([node], []), "layout_after.py", "exec"), scope)
        window = types.SimpleNamespace(screen=None)
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            for idname in ("ANGELO_OT_toggle_studio", "angelo.toggle_studio"):
                scope["again"](idname, mode="TOGGLE")(window)
        self.assertEqual(calls, [{"mode": "TOGGLE"}, {"mode": "TOGGLE"}])

    def test_the_tab_build_is_a_layout_change_others_wait_for(self):
        # 35f5b544698's final line: chosen_tools, Studio and Flows clicks ran while the build had the window on a new
        # tab's screen. Everything that waits for a settled layout waits on ui._layout_timer, so the build holds it.
        source = (ADDON / "studio_workspaces.py").read_text()
        running = (ADDON / "studio_tab_build.py").read_text()   # The build's timer job (studio_tab_build.run).
        self.assertIn("run(target, retry)", source.split("\ndef ensure(", 1)[1].split("\ndef ", 1)[0])
        self.assertIn("tabs._holding = ui._layout_timer = step", running, "held from the build's first step")
        self.assertIn("if ui._layout_timer is step:", running, "released only if it is still the build's")
        self.assertIn("target.workspace.name != home", running, "held until the switch back home shows")
        idle = source.split("\ndef _layout_idle(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("ui._layout_timer in (None, _holding)", idle, "the build never waits for its own hold")

    def test_n_and_t_do_nothing_in_an_editor_angelo_draws_whole(self):
        guard = (ADDON / "chrome_guard.py").read_text()
        op = guard.split("class ANGELO_OT_owned_area_key", 1)[1].split("\n\n\n", 1)[0]
        self.assertIn('getattr(context.area, "angelo_owned", False)', op, "elsewhere the key goes on to Blender's")
        self.assertIn("return {'FINISHED'}", op)
        self.assertIn('KEYMAPS = (("Node Generic", \'NODE_EDITOR\'),)', guard, "never the console: N and T type there")
        default = (ROOT / "scripts/presets/keyconfig/keymap_data/blender_default.py").read_text()
        generic = default.split("def km_node_generic(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sidebar_key={\"type\": 'N'", generic, "Blender binds the node editor's N and T there")
        self.assertIn("for key in ('N', 'T'):", guard)
        self.assertIn("keyconfigs.addon", guard, "the add-on keyconfig survives a keyconfig reload")

    def test_an_angelo_tab_is_guarded_again_each_time_it_is_shown(self):
        # Interim dbe022dddb9 line: the Generation Flows tab came back with the node editor's sidebar and toolbar.
        guard = (ADDON / "chrome_guard.py").read_text()
        self.assertEqual(guard.count('subscribe_rna(key=(bpy.types.Window, "workspace"), owner=_owner, args=(), '
                                     'notify=_shown)'), 2, "at registration and on every load")
        settle = guard.split("\ndef guard(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("return 0.05 if time.monotonic() < until else None", settle, "bounded in time, then events only")
        self.assertIn("SETTLE = 1.0", guard)
        changed = guard.split("\ndef _changed(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("first_interval=0.25", changed, "one follow-up hide per change, never a poll")
        shown = guard.split("\ndef _shown(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("flow_window.owned_areas(windows, bpy.data.workspaces)", shown)
        self.assertIn("hide(area, window)", shown)

    def test_chrome_is_hidden_at_once_never_slid_out(self):
        """Blender slides an overlapping region out only for the context's area and clears its hidden flag for the
        slide, so a hide made with the area in the context read as shown, was hidden again and restarted the slide:
        73 re-hides and the sidebar and toolbar on screen (interim q_9s6asi flow window, 81ff7c4a9ed Flows tab).
        Every chrome hide goes through ``turn_off``, whose context has the window and never the area."""
        guard = (ADDON / "chrome_guard.py").read_text()
        off = guard.split("\ndef turn_off(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("temp_override(window=window, screen=window.screen, area=None, region=None)", off)
        self.assertIn("temp_override(area=None, region=None)", off)
        hide = guard.split("\ndef hide(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("turn_off(window or _window_of(area), area, names)", hide)
        self.assertNotIn("setattr", hide)
        prepare = (ADDON / "flow_window.py").read_text().split("\ndef prepare_area(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("chrome_guard.turn_off(window, area, chrome_guard.shown(area))", prepare)
        self.assertNotIn('"show_region_ui"', prepare, "no hide of its own with the area in the context")
        cpp = (ROOT / "source/blender/makesrna/intern/rna_space.cc").read_text()
        self.assertIn("region->overlap && (area == CTX_wm_area(C))", cpp, "Blender animates only the context's area")

    def test_the_editor_type_entry_shows_media_studios_spark_frame(self):
        # One icon per concept (studio_entry_points.rst section 2), coherence ruling for 1.0.5: a built-in icon,
        # because a C enum item cannot use the add-on's preview icon.
        svg = ROOT / "release/datafiles/icons_svg/angelo_media_studio.svg"
        self.assertTrue(svg.read_text().startswith('<svg height="1600" viewBox="0 0 1600 1600"'))
        self.assertIn("\n      angelo_media_studio\n",
                      (ROOT / "source/blender/editors/datafiles/CMakeLists.txt").read_text())
        self.assertIn("DEF_ICON(ANGELO_MEDIA_STUDIO)", (ROOT / "source/blender/editors/include/UI_icons.hh").read_text())
        enum = (ROOT / "source/blender/makesrna/intern/rna_screen.cc").read_text().split('"GENERATIVE_AI",', 1)[1]
        self.assertTrue(enum.lstrip().startswith("ICON_ANGELO_MEDIA_STUDIO,"))
        self.assertIn('"angelo_genai") ? ICON_ANGELO_MEDIA_STUDIO',
                      (ROOT / "source/blender/editors/space_console/space_console.cc").read_text())
        self.assertIn('return {"icon_value": icon_id(False)}', (ADDON / "editor_types.py").read_text())

    def test_the_order_is_read_as_the_tabs_show_it_never_alphabetically(self):
        # 35f5b544698's final line: the strip read Director · Media Studio · Generation Flows on screen, but
        # bpy.data.workspaces is always alphabetical, so the order check failed on a correct strip.
        import ast
        source = (ADDON / "studio_workspaces.py").read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "strip")

        def strip_of(workspaces):   # studio_workspaces.strip itself, over stand-in workspaces.
            scope = {"bpy": types.SimpleNamespace(data=types.SimpleNamespace(workspaces=workspaces))}
            exec(compile(ast.Module([node], []), "studio_workspaces.py", "exec"), scope)
            return scope["strip"]()
        made = [types.SimpleNamespace(name=name, angelo_tab_order=order) for name, order in
                (("Animation", 4), ("Director", 0), ("Generation Flows", 2), ("Layout", 3), ("Media Studio", 1),
                 ("Modeling", 3))]
        self.assertEqual([w.name for w in strip_of(made)],
                         ["Director", "Media Studio", "Generation Flows", "Layout", "Modeling", "Animation"])
        self.assertIsNone(strip_of([types.SimpleNamespace(name="Layout")]),
                          "a build without the order: unknown, so place() runs")
        rna = (ROOT / "source/blender/makesrna/intern/rna_workspace.cc").read_text()
        prop = rna.split('"angelo_tab_order"', 1)[1].split("RNA_def_property(srna", 1)[0]
        self.assertIn('RNA_def_property_int_sdna(prop, nullptr, "order")', prop)
        self.assertIn("RNA_def_property_clear_flag(prop, PROP_EDITABLE)", prop, "read-only")
        self.assertNotIn("for w in bpy.data.workspaces]", (ADDON / "studio_workspaces.py").read_text().split(
            "def in_order():", 1)[1].split("\ndef ", 1)[0].split("wanted", 1)[0])


class SetupStateTests(unittest.TestCase):
    def test_a_step_waiting_for_setup_is_neutral(self):
        source = (ADDON / "flow_nodes.py").read_text()
        status = source.split("    def _status(self, layout):", 1)[1].split("\n\n", 1)[0]
        self.assertLess(status.index("SETUP_LABELS[setup]"), status.index("column.alert ="),
                        "the neutral setup line is decided before any red")
        self.assertIn('state["state"] == "BLOCKED" and not setup else None', status,
                      "a block with no setup mark falls through to the red state")
        self.assertLess(status.index("waiting_text("), status.index("column.alert ="), "waiting is one neutral line")
        self.assertLess(status.index("STOPPED_TEXT"), status.index("column.alert ="), "a stopped run is neutral")
        self.assertIn('setup in {"connect", "reconnect"}', status, "an account is said once, by connect_notice")
        self.assertIn("'INFO'", status)
        self.assertLess(status.index("SETUP_TEXT"), status.index("column.alert ="),
                        "a step that only lacks an image, a prompt or a link is neutral too")
        words = (ADDON / "flow_setup.py").read_text()
        for need in ("connect", "reconnect", "choose"):
            self.assertIn('"%s":' % need, words.split("SETUP_LABELS = {", 1)[1].split("}", 1)[0])


if __name__ == "__main__":
    unittest.main()
