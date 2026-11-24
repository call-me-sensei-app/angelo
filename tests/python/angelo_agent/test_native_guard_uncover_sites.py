# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Blender's buttons are placed, hidden and uncovered for the grant guard's arming (grant_operators.rst; the
reviews of cf980b8..f6b01a9, SHOULD 2 and NIT 5), read from the C source: the behaviour of each helper is pinned by
the arming gtests (interface_angelo_arming_test.cc); these pin that every path reaches them.

* block_draw places a button only where it draws it; every button it skips (hidden, scrolled away, clipped out of its
  region, too wide for its panel, degenerate) starts again.
* Every temporary region's close (popups, menus, popovers, dialogs, pies, search boxes, tooltips) goes through
  region_temp_remove, which uncovers what it covered; so do a popup's refresh that changes its rect and a dragged
  popup.
* ui_angelo_note_uncovered walks every button the window shows (its editors' regions, its other popups, its global
  areas) and never the region that is going.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / "source/blender/editors/interface"


def code(path):
    """The file's text without its comments, so a comment never satisfies a pin."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def function(path, signature):
    """The body of the function whose definition starts with ``signature``, braces balanced."""
    text = code(path)
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for index in range(open_at, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[open_at:index + 1]
    raise AssertionError("unbalanced: " + signature)


class BlockDrawTests(unittest.TestCase):
    def test_a_button_is_placed_only_where_it_is_drawn(self):
        body = function(UI / "interface.cc", "void block_draw(")
        loop = body[body.index("for (Button &but : block->buttons())"):]
        loop = loop[:loop.index("widgetbase_draw_cache_end")]
        self.assertEqual(loop.count("ui_angelo_note_placed("), 1)
        placed = loop.index("ui_angelo_note_placed(")
        self.assertLess(placed, loop.index("draw_button("), "placed right where it is drawn")
        self.assertGreater(placed, loop.index("rect.xmin < rect.xmax"), "only a drawable rect is placed")

    def test_every_button_block_draw_skips_starts_again(self):
        body = function(UI / "interface.cc", "void block_draw(")
        loop = body[body.index("for (Button &but : block->buttons())"):]
        loop = loop[:loop.index("widgetbase_draw_cache_end")]
        skips = [match.start() for match in re.finditer(r"\bcontinue;", loop)]
        self.assertEqual(len(skips), 3, "hidden or scrolled, clipped out, too wide")
        for at in skips:
            before = loop[max(0, at - 160):at]
            self.assertIn("ui_angelo_note_hidden(but);", before, "a skipped button starts again")
        self.assertIn("else {\n      ui_angelo_note_hidden(but);", loop, "a degenerate rect is not on screen")


class UncoverTests(unittest.TestCase):
    def test_every_temporary_region_close_uncovers_what_it_covered(self):
        remove = function(UI / "regions/interface_regions.cc", "static void region_temp_remove_ex(")
        self.assertIn("ui_angelo_note_uncovered(win, UI_angelo_temporary_cover(region), region, true, own);", remove)
        self.assertNotIn("uncovers", remove, "no opt-out: a tooltip uncovers too (the review's NIT 4)")
        self.assertIn("region_temp_remove_ex(C, screen, region, nullptr);",
                      function(UI / "regions/interface_regions.cc", "void region_temp_remove("))
        for path in (UI / "regions").glob("*.cc"):
            for call in re.findall(r"\bregion_temp_remove\(((?!bContext)[^;]*)\);", code(path)):   # Calls, not its definition.
                self.assertEqual(call.count(","), 2, "%s: %s (no opt-out argument)" % (path.name, call))
        callers = {path.name for path in (UI / "regions").glob("*.cc") if "region_temp_remove(" in code(path)}
        self.assertLessEqual({"interface_region_popup.cc", "interface_region_search.cc",
                              "interface_region_tooltip.cc"}, callers)

    def test_a_moved_or_resized_popup_uncovers_its_old_rect(self):
        refresh = function(UI / "regions/interface_region_popup.cc", "Block *popup_block_refresh(")
        self.assertIn("const rcti covered_before = UI_angelo_temporary_cover(region);", refresh)
        self.assertIn("const rcti covered_after = UI_angelo_temporary_cover(region);", refresh)
        self.assertIn("!BLI_rcti_compare(&covered_before, &covered_after)", refresh,
                      "an unchanged refresh restarts nothing (the review of 791f48a..c43bfc0)")
        self.assertIn("ui_angelo_note_uncovered(window, covered_before, region);", refresh)
        handlers = code(UI / "interface_handlers.cc")
        drag = handlers[handlers.index("popup_translate(region, mdiff);") - 200:
                        handlers.index("popup_translate(region, mdiff);") + 200]
        self.assertIn("const rcti covered_before = UI_angelo_temporary_cover(region);", drag)
        self.assertIn("ui_angelo_note_uncovered(win, covered_before, region);", drag)

    def test_uncovering_walks_every_button_of_the_window_but_the_going_region(self):
        walk = function(UI / "interface.cc", "void ui_angelo_note_uncovered_in(")
        for place in ("screen->areabase", "screen->regionbase", "win->global_areas.areabase"):
            self.assertIn(place, walk)
        self.assertIn("&region == removed", walk, "the region that is going is never walked")
        self.assertIn("if (popups) {", walk, "popups only for a popup's own close")
        popups = walk[walk.index("if (popups) {"):]
        self.assertLess(popups.index("if (&region == removed) {\n          break;"), popups.index("restart(region);"),
                        "only the popups drawn beneath the one that went")
        public = function(UI / "interface.cc", "void UI_angelo_note_uncovered(")
        self.assertIn("ui_angelo_note_uncovered(win, rect, except, false);", public,
                      "an overlapping region or the startup cover never restarts a popup above it")
        self.assertIn("ui_angelo_restart_uncovered(but, win, uncovered);", walk)


class TooltipOwnTests(unittest.TestCase):
    """A tooltip's close keeps only its own control's arming, and only while that control is unchanged since the
    tooltip opened over it (Windows build2 on afb48bd, person-click run-2; the review of 3cfdc5f, MAJOR and NIT)."""

    def test_a_tooltip_records_its_own_control_as_it_opens(self):
        create = function(UI / "regions/interface_region_tooltip.cc", "ARegion *tooltip_create_from_button_or_extra_icon(")
        self.assertIn("->angelo_own = ui_angelo_tooltip_own(*but);", create)
        own = function(UI / "interface.cc", "AngeloTooltipOwn ui_angelo_tooltip_own(")
        self.assertIn("{&but, but.angelo_place, but.angelo_restarts}", own)
        self.assertIn("if (!but.angelo_placed) {", own, "a control not placed in a window has no owner record")

    def test_its_close_passes_that_record_and_every_other_close_none(self):
        free = function(UI / "regions/interface_region_tooltip.cc", "void tooltip_free(")
        self.assertIn("region_temp_remove_tooltip(C, screen, region, own);", free)
        self.assertIn("region_temp_remove(C, screen, region);", free, "a tooltip with no owner uncovers everything")
        for path in (UI / "regions").glob("*.cc"):
            if path.name not in ("interface_region_tooltip.cc", "interface_regions.cc"):
                self.assertNotIn("region_temp_remove_tooltip(", code(path), path.name)

    def test_the_skip_needs_the_same_button_place_and_restart_count(self):
        walk = function(UI / "interface.cc", "void ui_angelo_note_uncovered_in(")
        for part in ("&but == own->but", "BLI_rcti_compare(&but.angelo_place, &own->place)",
                     "but.angelo_restarts == own->restarts"):
            self.assertIn(part, walk)
        again = function(UI / "interface.cc", "void ui_angelo_start_again(")
        self.assertIn("but.angelo_restarts++;", again)
        placed = function(UI / "interface.cc", "void ui_angelo_note_placed(")
        self.assertIn("but.angelo_restarts++;", placed, "moved on screen counts as a restart")


WM = ROOT / "source/blender/windowmanager/intern"


class RoutingTests(unittest.TestCase):
    """The review of f6b01a9..da9ba66, SHOULD 1: the guard's input feed and Blender's handler loop ask one routing."""

    def test_blenders_handler_loop_takes_the_shared_steps(self):
        events = code(WM / "wm_event_system.cc")
        regions = function(WM / "wm_event_system.cc", "static eHandlerActionFlag wm_event_do_handlers_area_regions(")
        self.assertIn("wm_event_area_region_xy(area, event->xy)", regions)
        self.assertIn("wm_event_always_pass(event) || wm_event_area_takes_xy(&win, area, event->xy)", events)
        self.assertNotIn("ED_area_find_region_xy_visual", events, "no copy of the region step")
        self.assertNotIn("WM_angelo_input_held(&win, area)", events, "the area step holds the input-hold check")

    def test_the_feed_routes_every_move_and_press(self):
        feed = function(WM / "wm_event_system.cc", "static void angelo_note_input(")
        self.assertIn("event->type == MOUSEMOVE || (event->type == LEFTMOUSE && event->val == KM_PRESS)", feed)
        self.assertIn("WM_angelo_pointer_region(win, event->xy)", feed)
        self.assertIn("event->val, target);", feed)
        route = code(WM / "wm_angelo_pointer_region.cc")
        self.assertIn("ED_area_find_region_xy_visual(area, RGN_TYPE_ANY, xy)", route)
        self.assertIn("wm_event_pointer_region(win, WM_window_get_active_screen(win), xy)", route)

    def test_a_confined_thread_feeds_nothing(self):
        intern = ROOT / "source/blender/python/intern/bpy_angelo_guard_authority.cc"
        feed = function(intern, "void BPY_angelo_guard_note_input(")
        self.assertLess(feed.index("BPY_angelo_guard_thread_confined()"), feed.index("authority()"))

    def test_consent_regions_name_their_region(self):
        guard = (ROOT / "scripts/addons_core/angelo_agent/grant_guard.py").read_text(encoding="utf-8")
        self.assertIn("tuple(regions), region.as_pointer())", guard)

    def test_overlapping_regions_are_restart_only_covers(self):
        draw = function(WM / "wm_draw.cc", "static void wm_draw_window_onscreen(")
        self.assertIn("region.overlap && region.runtime->visible", draw)
        self.assertIn("cover_restart_only.append(true);", draw)
        self.assertEqual(draw.count("cover_restart_only.append(false);"), 2, "the startup cover, temporary regions")
        self.assertIn("UI_angelo_note_uncovered(win, rect, static_cast<const ARegion *>(gone.key));", draw)
        self.assertIn("shown_regions.data(),", draw, "the regions it shows: a gone one's menu and surfaces go")


class FlowsCoverTests(unittest.TestCase):
    """The last review of da9ba66..1b4e7f0, SHOULD 1: the Flows popup's cover is never stale or skipped."""

    ADDON = ROOT / "scripts/addons_core/angelo_agent"

    def body(self, name, source):
        import ast
        tree = ast.parse((self.ADDON / source).read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment((self.ADDON / source).read_text(encoding="utf-8"), node)

    def test_a_cover_is_set_during_an_agents_run_too(self):
        cover = self.body("_cover", "flow_menu_guard.py")
        self.assertNotIn("script_running", cover.split('"""')[-1], "C refuses only the agent's own thread")
        self.assertIn("region.as_pointer()", cover)
        self.assertIn("_cover(bpy.context.window, _window_rect(region,", self.body("consent_regions", "flow_menu_guard.py"))

    def test_every_close_clears_it_a_file_load_included(self):
        self.assertIn("flow_menu_guard.closed(window)", self.body("close", "flow_menu.py"))
        self.assertIn("flow_menu_guard.closed(window)", self.body("_load_pre", "flow_menu.py"))


if __name__ == "__main__":
    unittest.main()
