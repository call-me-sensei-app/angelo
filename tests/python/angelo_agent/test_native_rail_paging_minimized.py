# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel's paging and its minimized round button as the person meets them, without Blender.

Evidence: the maintainer's audit (2026-10-25 17:00) of an early cut of the rail positions: a paged rail skipped a
worker as it scrolled and the lead's pin changed what a page index meant; the strips' corner reading called the
navigation gizmo's geometry with the wrong arguments; the minimized badge counted the main session; the minimized
button's hover, press and Return went to tables its draw never read; and while minimized, hidden tiles still drew
pictures and asked for redraws as their clocks ran. Each rule is checked across positions, scales, view sizes and
worker counts, never one layout.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import test_native_rail_minimized as minimized_host
import test_native_session_rail_strip as strips
from test_native_session_rail_strip import POSITIONS, place, sessions, views

import angelo_agent  # noqa: E402  (the path is set by the imported tests)
from angelo_agent import rail_minimized as rm, rail_redraw, session_rail as rail  # noqa: E402

SIZES = ((1280, 502), (1280, 720), (1600, 900), (1920, 1080))
SCALES = (1.0, 1.5, 2.0, 3.0)
WORKERS = (3, 8, 16, 20)


def window_of(plan, tiles):
    """``(pinned, start, shown)`` of a plan: whether the main tile stays, where its window of the rest starts (an
    index into the tiles it pages over) and their sessions."""
    shown = [t.session for t in plan.tiles]
    pinned = plan.paged and len(shown) >= 2
    pool = [t.session for t in (tiles[1:] if pinned else tiles)]
    window = shown[1:] if pinned else shown
    return pinned, pool.index(window[0]), window


class PagingTests(unittest.TestCase):
    def test_one_step_at_a_time_shows_every_worker_in_order_and_back(self):
        """A wheel notch or an arrow (``session_rail_draw.scroll_by``: the plan's own first plus one) moves the window
        by exactly one session, never past one; the main tile's pin never changes while the view does not."""
        for size in SIZES:
            for s in SCALES:
                for name, view, _hard, _soft in views(size, s):
                    if name not in ("header", "everything", "asset shelf"):
                        continue
                    for workers in WORKERS:
                        tiles = sessions(workers)
                        for position in POSITIONS:
                            where = (position, size, s, name, workers)
                            plan, _room = place(position, tiles, size, s, view)
                            if not plan.paged:
                                continue
                            pinned, start, window = window_of(plan, tiles)
                            self.assertEqual(start, 0, where)
                            seen, starts = list(window), [start]
                            while plan.hidden[1]:
                                plan, _room = place(position, tiles, size, s, view, max(0, plan.first + 1))
                                again, start, window = window_of(plan, tiles)
                                self.assertEqual(again, pinned, where)
                                self.assertEqual(start, starts[-1] + 1, (where, starts))
                                starts.append(start)
                                seen += [session for session in window if session not in seen]
                                self.assertLess(len(starts), 100, where)
                            expected = [t.session for t in (tiles[1:] if pinned else tiles)]
                            self.assertEqual(seen, expected, where)
                            while plan.hidden[0]:
                                plan, _room = place(position, tiles, size, s, view, max(0, plan.first - 1))
                                again, start, _window = window_of(plan, tiles)
                                self.assertEqual((again, start), (pinned, starts.pop() - 1), where)

    def test_the_auditors_view_scrolls_from_worker_14_to_15(self):
        """16 workers in a 1600x900 view at 3x (the audit's case), every position."""
        tiles = sessions(16)
        for position in POSITIONS:
            view = dict(headers=52 * 3.0)
            plan, _room = place(position, tiles, (1600, 900), 3.0, view)
            order = []
            while True:
                order += [t.session for t in plan.tiles if t.session not in order]
                if not plan.hidden[1]:
                    break
                plan, _room = place(position, tiles, (1600, 900), 3.0, view, max(0, plan.first + 1))
            self.assertEqual([o for o in order if o != "lead"], ["w%d" % n for n in range(1, 17)], position)


class MinimizedTests(unittest.TestCase):
    def test_the_badge_counts_workers_and_their_approvals_only(self):
        """One working worker and the main session waiting for approval: "1 worker", no worker approval."""
        for workers in (1, 2, 16):
            tiles = sessions(workers)
            tiles[0].kind = rail.APPROVAL
            self.assertEqual(rm.counts(tiles, "lead"), (workers, False), workers)
            label = rm.label(*rm.counts(tiles, "lead"))
            self.assertNotIn("A worker waits for your approval", label)
            self.assertIn("%d worker" % workers, label)

    def test_the_strips_read_the_gizmo_through_the_real_geometry(self):
        """``session_rail_place.corners`` reaches ``rail_minimized.navigation_rect`` as Blender lays the view out, at
        1x and 2x with and without the sidebar, and a strip at the top clears what it returns."""
        from angelo_agent import session_rail_place
        for s in (1.0, 2.0):
            for sidebar in (True, False):
                regions = minimized_host.area_regions(0, 0, 1600, 900, s, sidebar=sidebar)
                main, host = regions[-1], minimized_host.area(regions)
                window = SimpleNamespace(as_pointer=lambda: 3)
                launcher = SimpleNamespace(_places={}, _shown={})
                fake = minimized_host.fake_bpy(s, 1.0 if s < 2 else 2.0, minimized_host.view())
                with mock.patch.dict(sys.modules, {"bpy": fake, "angelo_agent.chat_launcher": launcher}), \
                        mock.patch.object(angelo_agent, "chat_launcher", launcher, create=True):
                    found = session_rail_place.corners(window, host, main)
                    gizmo = rm.navigation_rect(host, main)
                self.assertIsNotNone(gizmo, (s, sidebar))
                self.assertEqual(found, [tuple(gizmo)], (s, sidebar))
                tiles = sessions(16)
                size = (main.width, main.height)
                room = strips.strip.insets("TOP", size, s, headers=52 * s, corners=found)
                plan = strips.strip.layout(tiles, size, s, room, "TOP")
                self.assertFalse(strips.overlaps(plan.panel, tuple(gizmo)), (s, sidebar, plan.panel, gizmo))

    def test_the_panels_handler_drives_the_state_the_circle_paints(self):
        """Hover, press and Return through ``session_rail_controls.restore_event`` (the panel's own handler path)
        change what ``rail_minimized.draw`` paints and its tooltip, and Return restores the panel."""
        from angelo_agent import session_rail_controls as controls
        rm.release()
        self.addCleanup(rm.release)
        setting = SimpleNamespace(minimized=True)
        setting.rail_minimized = lambda: setting.minimized
        setting.set_rail_minimized = lambda value: setattr(setting, "minimized", bool(value))
        painted, tips = [], []
        stubs = {"parallel_setting": setting,
                 "canvas_controls": SimpleNamespace(tip=lambda *args: tips.append(args[2])),
                 "chat_view": SimpleNamespace(), "card_view": SimpleNamespace(View=lambda: "view")}
        gpu = SimpleNamespace(state=SimpleNamespace(blend_set=lambda mode: None))
        patches = [mock.patch.dict(sys.modules, {"gpu": gpu}),
                   mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in stubs.items()}),
                   mock.patch.object(rm, "paint", lambda *args: painted.append(args[-2:])),
                   mock.patch.object(rm, "spot", lambda area, region, scale, avoid=(): (100.0, 100.0, 38.0)),
                   mock.patch.object(rm, "_redraw", lambda window, area: None)]
        patches += [mock.patch.object(angelo_agent, k, v, create=True) for k, v in stubs.items()]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        window = SimpleNamespace(as_pointer=lambda: 5, cursor_modal_set=lambda kind: None,
                                 cursor_modal_restore=lambda: None, screen=None)
        area = SimpleNamespace(as_pointer=lambda: 6)
        region = SimpleNamespace(x=0, y=0, width=800, height=600)

        def event(kind, value="NOTHING", x=100, y=100):
            return controls.restore_event(window, SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y))

        rm.draw(window, area, region, 1, False, 1.0)
        self.assertEqual(painted[-1], (False, False))
        self.assertEqual(event('MOUSEMOVE'), {'PASS_THROUGH'})
        rm.draw(window, area, region, 1, False, 1.0)
        self.assertEqual(painted[-1], (True, False), "lifted under the pointer")
        self.assertTrue(tips and "Show the Sessions panel" in tips[-1])
        self.assertEqual(event('LEFTMOUSE', 'PRESS'), {'RUNNING_MODAL'})
        rm.draw(window, area, region, 1, False, 1.0)
        self.assertEqual(painted[-1], (True, True), "pressed")
        self.assertEqual(event('LEFTMOUSE', 'RELEASE', 400, 400), {'RUNNING_MODAL'})   # Off it: still minimized.
        self.assertTrue(setting.minimized)
        rm.draw(window, area, region, 1, False, 1.0)
        event('MOUSEMOVE')
        self.assertEqual(event('RET', 'PRESS'), {'RUNNING_MODAL'})
        self.assertFalse(setting.minimized, "Return on it shows the panel")


class HiddenWorkTests(unittest.TestCase):
    def test_minimized_redraws_follow_the_badge_only(self):
        """While minimized the view is drawn again only when the workers' count or a worker's approval changes:
        never for a hidden tile's clock, picture (the version), step or state."""
        tiles = sessions(3)
        base = rail_redraw.signature(1, tiles, True, "lead")
        moved = sessions(3)
        moved[1].status, moved[1].step, moved[2].kind = "Working · 0:42", "Roof", rail.ENDED
        self.assertEqual(rail_redraw.signature(9, moved, True, "lead"), base)
        self.assertIsNone(rail_redraw.wait(base, rail_redraw.signature(9, moved, True, "lead"), 0.0, 10.0))
        waiting = sessions(3, waiting={2})
        self.assertNotEqual(rail_redraw.signature(1, waiting, True, "lead"), base)
        self.assertNotEqual(rail_redraw.signature(1, sessions(4), True, "lead"), base)
        self.assertNotEqual(rail_redraw.signature(1, tiles), base, "shown again: the panel is drawn")
        self.assertNotEqual(rail_redraw.signature(1, tiles)[1], rail_redraw.signature(1, moved)[1])

    def test_without_preferences_the_panel_is_never_minimized(self):
        self.assertFalse(rail_redraw.minimized())


class CornerTests(unittest.TestCase):
    """The navigation gizmo where Blender draws it (``rail_minimized.gizmo_rect``; it outgrows a short view at a large
    scale) and a sidebar that can cover half the view (the rail positions GUI suite, 2026-10-25: the strips shrank to
    a twentieth of the person's scale and a short column vanished, both deciding a corner's side by the region's
    middle; the review: a corner must never take the panel away)."""

    def layouts(self):
        for width in (480, 640, 800, 1000, 1282, 1600, 1920):
            for height in (300, 420, 502, 636, 722, 900, 1080):
                for s in (1.0, 1.5, 2.0, 3.0):
                    for sidebar in (0.0, 240 * s, width / 2):
                        tool = 44 * s
                        visible = (tool, 0, width - sidebar - 1, height - 52 * s - 1)
                        gizmo = rm.gizmo_rect(visible, s, minimized_host.view())
                        for live in (0, strips.label.room(s)):
                            yield (width, height), s, dict(sides=(tool, sidebar), headers=52 * s, label=live,
                                                           corners=[tuple(gizmo)] if gizmo else [])

    def test_a_corner_never_takes_the_panel_away_or_is_covered_where_it_can_be_cleared(self):
        for size, s, view in self.layouts():
            bare = dict(view, corners=[])
            for position in POSITIONS:
                for workers in (1, 16):
                    tiles = sessions(workers)
                    where = (position, size, s, view["sides"], workers)
                    plain, _room = place(position, tiles, size, s, bare)
                    plan, room = place(position, tiles, size, s, view)
                    if not plain.tiles:
                        continue   # No room even without the corner (headers and toolbar take the view).
                    self.assertTrue(plan.tiles, where)
                    self.assertTrue(strips.inside(plan.panel, (0, 0) + size), (where, plan.panel))
                    gizmo = view["corners"][0] if view["corners"] else None
                    if gizmo is not None and strips.overlaps(plan.panel, gizmo):
                        for way in ("left", "right", "top", "bottom"):   # Covered only where no way had room.
                            reach = {"left": gizmo[0] + gizmo[2], "right": size[0] - gizmo[0],
                                     "top": size[1] - gizmo[1], "bottom": gizmo[1] + gizmo[3]}[way]
                            other = {**room, way: max(room[way], reach)}
                            need = rail.least(len(tiles)) if position == "LEFT" else strips.strip.least(len(tiles))
                            self.assertLessEqual(rail.fit(s, (size[1] - other["top"] - other["bottom"],
                                                              size[0] - other["left"] - other["right"]), need), 0,
                                                 (where, way, plan.panel, gizmo))

    def test_beside_a_sidebar_covering_half_the_view_the_strip_keeps_its_size(self):
        """At least the scale of the room beside the gizmo: never a sliver because the gizmo stands left of the
        region's middle."""
        for s in (1.0, 2.0, 3.0):
            for width in (1282, 1600):
                size, tool, sidebar = (width, 1211), 44 * s, width * 0.55
                visible = (tool, 0, width - sidebar - 1, 1211 - 52 * s - 1)
                gizmo = tuple(rm.gizmo_rect(visible, s, minimized_host.view()))
                view = dict(sides=(tool, sidebar), headers=52 * s, corners=[gizmo])
                for position in ("TOP", "BOTTOM"):
                    plan, room = place(position, sessions(1), size, s, view)
                    beside = rail.fit(s, (size[1] - room["top"] - room["bottom"], gizmo[0] - tool),
                                      strips.strip.least(2))
                    self.assertGreaterEqual(plan.scale + 1e-9, min(s, beside), (position, s, width, plan.scale))
                    self.assertFalse(strips.overlaps(plan.panel, gizmo), (position, s, width))


class HoverTests(unittest.TestCase):
    def test_an_arrow_and_a_more_line_never_lift_together(self):
        """A page of one session is an arrow's step too: each control under the pointer has its own hover key."""
        checked = 0
        for size in ((700, 720), (1280, 502), (1280, 720)):
            for s in (1.0, 2.0):
                for workers in (3, 8, 16):
                    for position in POSITIONS:
                        tiles = sessions(workers)
                        for first in range(0, workers):
                            plan, _room = place(position, tiles, size, s, dict(headers=52 * s), first)
                            if not plan.paged:
                                break
                            keys = [rail.hover_key(rail.hit(plan, *strips.centre(rect)))
                                    for rect in list(plan.arrows) + [r for r in plan.more if r is not None]]
                            self.assertEqual(len(keys), len(set(keys)), (position, size, s, workers, first, keys))
                            self.assertLessEqual(set(keys), set(rail.control_keys(plan)))
                            checked += plan.page == 1
        self.assertGreater(checked, 0, "a page of one was among the layouts")


class GuiRuleTests(unittest.TestCase):
    """The GUI suite's own rules measure against the view's clear room (``rail_positions_checks.clear_box``): a
    sidebar can cover half the view, and a shelf with its pills the bottom (the rail positions GUI suite, 2026-10-25:
    a correct button and strip failed against the whole region)."""

    def test_top_right_is_of_the_clear_room(self):
        import rail_positions_checks as rules
        obstacles = [("tools", (0, 0, 88, 1211)), ("ui", (721, 0, 561, 1211)), ("header", (0, 1159, 1282, 52))]
        failing = lambda found: [name for name, ok, _detail in found if not ok]
        self.assertNotIn("circle_at_the_top_right", failing(rules.circle_rules((433, 999, 76), (1282, 1211),
                                                                              obstacles, 2.0, 76)))
        self.assertIn("circle_at_the_top_right", failing(rules.circle_rules((200, 999, 76), (1282, 1211),
                                                                           obstacles, 2.0, 76)))
        self.assertEqual(rules.clear_box(obstacles + [("shelf_pill_studio", (60, 140, 120, 28))], (1282, 1211)),
                         (88, 168, 721, 1159))

    def test_sides_are_never_coverable_and_the_round_button_only_where_nothing_fits(self):
        import rail_positions_checks as rules
        view = [("tools", (0, 0, 88, 581)), ("ui", (151, 0, 561, 581)), ("header", (0, 477, 712, 104)),
                ("navigation_gizmo", (-29.0, 43.4, 163.0, 413.6))]   # Windows T4 at 2x: 63 px of clear room.
        least = (198 * 1.0, 212 * 1.0)   # The least column at the readable floor of 2x.
        self.assertFalse(rules.room_holds(view, (712, 581), least, "LEFT"))
        self.assertEqual(rules.coverable(view, (712, 581), least, "LEFT"), set())
        wide = [("tools", (0, 0, 88, 1211)), ("header", (0, 1107, 1920, 104)),
                ("navigation_gizmo", (1700, 600, 180, 420))]
        self.assertTrue(rules.room_holds(wide, (1920, 1211), least, "LEFT"))
        self.assertEqual(rules.coverable(wide, (1920, 1211), least, "LEFT"), set())   # Room beside the gizmo.
        boxed = [("tools", (0, 0, 88, 600)), ("ui", (400, 0, 312, 600)), ("navigation_gizmo", (88, 0, 312, 600))]
        self.assertEqual(rules.coverable(boxed, (712, 600), (100, 200), "LEFT"), set(rules.CORNERS))


class CircleSpotTests(unittest.TestCase):
    """The round button is clear of everything whenever any clear spot exists at its size or at the least it shrinks
    to (break-it, 2026-10-25: a live view's label, a shelf or the gizmo covered while a free spot stood beside them)."""

    def test_clear_wherever_a_clear_spot_exists(self):
        import itertools
        from angelo_agent import round_button
        for (width, height), s in itertools.product(((640, 1140), (1024, 600), (1280, 380), (1280, 980), (1920, 1080),
                                                     (420, 900)), (1.0, 1.5, 2.0, 3.0)):
            for shelf, label, sidebar in itertools.product((0, 140 * s), (False, True), (0, min(240 * s, width / 2))):
                insets = {"left": 44 * s, "right": sidebar, "top": 52 * s, "bottom": shelf}
                visible = (insets["left"], 0, width - sidebar - 1, height - insets["top"] - 1)
                gizmo = rm.gizmo_rect(visible, s, minimized_host.view())
                avoid = [((width - 460 * s) / 2, height - insets["top"] - 38 * s, 460 * s, 26 * s)] if label else []
                cx, cy, r = rm.place((width, height), insets, s, gizmo, avoid)
                blocks = [b for b in [gizmo] + avoid if b]
                margin = rm.MARGIN * s
                hit = [b for b in blocks if strips.overlaps((cx - r, cy - r, 2 * r, 2 * r), b)]
                if not hit:
                    continue
                least = round_button.DISC * s / 2 * rm.SMALLEST
                lo_x, hi_x = insets["left"] + margin + least, width - sidebar - margin - least
                lo_y, hi_y = shelf + margin + least, height - insets["top"] - margin - least
                if hi_x < lo_x or hi_y < lo_y:
                    continue   # No room for the least circle at all: covering is all there is.
                step = 2 * s
                free = any(not any(strips.overlaps((x - least - margin, y - least - margin, 2 * (least + margin),
                                                    2 * (least + margin)), b) for b in blocks)
                           for x in [lo_x + i * step for i in range(int(max(0, hi_x - lo_x) / step) + 1)]
                           for y in [lo_y + j * step for j in range(int(max(0, hi_y - lo_y) / step) + 1)])
                self.assertFalse(free, ((width, height), s, shelf, label, sidebar, (cx, cy, r), hit))


class EdgeTests(unittest.TestCase):
    """A strip keeps to its edge beside a corner unless leaving it more than doubles the strip, and a column stands
    right under the headers unless a live view's label it would meet is drawn (the rail positions and session rail
    GUI suites, 2026-10-25); corners are cleared jointly when one's best way leaves another none (the review)."""

    def gizmo(self, size, s, tool, sidebar, headers):
        visible = (tool, 0, size[0] - sidebar - 1, size[1] - headers - 1)
        return tuple(rm.gizmo_rect(visible, s, minimized_host.view()))

    def test_a_strip_keeps_to_its_edge_unless_leaving_it_doubles_it(self):
        for width, sidebar, s in ((1282, 561, 2.0), (1600, 700, 2.0), (1920, 900, 2.0), (1282, 280, 1.0)):
            size, tool, headers = (width, 1211), 44 * s, 52 * s
            view = dict(sides=(tool, sidebar), headers=headers, label=strips.label.room(s),
                        corners=[self.gizmo(size, s, tool, sidebar, headers)])
            plan, room = place("TOP", sessions(3), size, s, view)
            top = size[1] - headers - strips.label.room(s)
            self.assertAlmostEqual(plan.panel[1] + plan.panel[3], top - rail.MARGIN * plan.scale, delta=1.0,
                                   msg=(width, sidebar, s, plan.panel))
            self.assertFalse(strips.overlaps(plan.panel, view["corners"][0]))
        size, s = (1278, 1163), 3.0   # The gizmo takes almost all the room beside it: past it, many times larger.
        tool, sidebar, headers = 169, 841, 72
        gizmo = self.gizmo(size, s, tool, sidebar, headers)
        view = dict(sides=(tool, sidebar), headers=headers, label=strips.label.room(s), corners=[gizmo])
        plan, room = place("TOP", sessions(16), size, s, view)
        beside = rail.fit(s, (size[1] - room["top"], gizmo[0] - tool), strips.strip.least(17))
        self.assertGreater(plan.scale, strips.strip.LEAVE_EDGE * beside)
        self.assertFalse(strips.overlaps(plan.panel, gizmo))

    def test_a_column_clears_a_live_label_only_while_it_shows(self):
        for s in (1.0, 2.0, 3.0):
            size, headers = (1920, 1080), 52 * s
            plan, _room = place("LEFT", sessions(3), size, s, dict(headers=headers))
            self.assertAlmostEqual(plan.panel[1] + plan.panel[3], size[1] - headers - rail.MARGIN * plan.scale,
                                   delta=1.0, msg=s)   # Right under the headers: no live label shows.
            narrow = (420, 900)
            label_rect = strips.label.rect(narrow[0], narrow[1] - headers, 380 * s, s)
            plan, _room = place("LEFT", sessions(3), narrow, s, dict(headers=headers, corners=[label_rect]))
            self.assertTrue(plan.tiles)
            self.assertFalse(strips.overlaps(plan.panel, label_rect), (s, plan.panel, label_rect))

    def test_two_corners_are_cleared_together(self):
        size, s = (1000, 420), 3.0
        tool, sidebar, headers = 44 * s, 500, 52 * s
        gizmo = self.gizmo(size, s, tool, sidebar, headers)
        angelo = (48, 48, 228, 228)
        for position in POSITIONS:
            view = dict(sides=(tool, sidebar), headers=headers, corners=[gizmo, angelo])
            plan, _room = place(position, sessions(1), size, s, view)
            if plan.tiles:
                for corner in (gizmo, angelo):
                    self.assertFalse(strips.overlaps(plan.panel, corner), (position, plan.panel, corner))


class PlaceholderTests(unittest.TestCase):
    def test_the_placeholder_words_never_cover_the_tiles_chips(self):
        """Every picture height a tile gets, at 1x-3x, with and without the approval badge."""
        from angelo_agent import rail_glyph as drawn, ui_tokens as tk
        for s in (1.0, 1.5, 2.0, 3.0):
            for ph in range(int(rail.MIN_PICTURE * s), int(140 * s)):
                picture = (10.0, 20.0, 160 * s, float(ph))
                for badge in (None, (16.0, 20.0 + ph - 26 * s, 90 * s, 20 * s)):
                    y = drawn.placeholder_y(picture, badge, s)
                    if y is None:
                        continue
                    words = (y - 1 * s, y + tk.TYPE_MICRO * s)   # The words' foot and head around the baseline.
                    self.assertGreaterEqual(words[0], 20.0 + 24 * s, (s, ph, badge))   # Above the role chip.
                    head = badge[1] if badge else 20.0 + ph
                    self.assertLessEqual(words[1], head, (s, ph, badge))
            self.assertIsNotNone(drawn.placeholder_y((0, 0, 160 * s, 100 * s), None, s), s)   # A full picture.


def readable_place(position, tiles, size, s, view):
    """``(plan, room)`` as session_rail_place lays the panel out: its room from the obstacles, the corner things
    covered below the readable floor (``session_rail_strip.readable``); ``(None, None)`` where no readable panel fits
    (the round button stands for it)."""
    strip = strips.strip
    n = len(tiles)
    if position == "LEFT":   # The column clears a live label only while one shows (session_rail_place.column).
        view = dict(view, label=0)
        room = strip.readable(lambda **kept: strip.column_insets(size, s, n, **kept), size, s, rail.least(n), view)
        if room is None:
            return None, None
        return rail.layout(tiles, size, s, room["left"], room["top"], room["bottom"], 0, None, room["right"]), room
    room = strip.readable(lambda **kept: strip.insets(position, size, s, count=n, **kept), size, s, strip.least(n),
                          view)
    return (None, None) if room is None else (strip.layout(tiles, size, s, room, position), room)


class ReadableTests(unittest.TestCase):
    """Windows T4, 2026-10-26: a 1280x737 window at 2x and 3x with the chat docked and the sidebar open left the 3D
    view's clear room a few dozen pixels wide, so the panel was drawn 12 px tall (scale 0.04) or not at all, and its
    minimize button could not be clicked; and a short column unpinned the main tile for its "+N more" lines. A panel
    is now readable and clear of the toolbar and sidebar (regions drawn over the view, which take the clicks there),
    covering at most the corner things drawn in the view; else its round button stands for it."""

    def cramped(self):
        for width in (560, 708, 900, 1100):
            for height in (420, 502, 581, 680):
                for s in (1.0, 1.5, 2.0, 3.0):
                    tool, headers = 44 * s, 52 * s
                    for sidebar in (0.0, 240 * s, width * 0.8):
                        visible = (tool, 0, width - sidebar - 1, height - headers - 1)
                        gizmo = tuple(rm.gizmo_rect(visible, s, minimized_host.view()))
                        yield (width, height), s, dict(sides=(tool, sidebar), headers=headers, label=strips.label.room(s),
                                                       corners=[gizmo])

    def test_a_panel_is_readable_and_clear_of_the_sides_or_the_round_button_stands_in(self):
        for size, s, view in self.cramped():
            floor = strips.strip.READABLE * s
            tool, sidebar = view["sides"]
            for position in POSITIONS:
                for workers in (1, 16):
                    tiles = sessions(workers)
                    need = rail.least(len(tiles)) if position == "LEFT" else strips.strip.least(len(tiles))
                    top = view["headers"] + (view["label"] if position != "LEFT" else 0)
                    holds = rail.fit(s, (size[1] - top, size[0] - tool - sidebar), need) >= floor - 1e-6
                    plan, _room = readable_place(position, tiles, size, s, view)
                    where = (position, size, s, view["sides"], workers)
                    if plan is None:
                        self.assertFalse(holds, where)   # The round button only where no readable panel fits.
                        continue
                    self.assertTrue(plan.tiles, where)
                    self.assertGreaterEqual(plan.scale, floor - 1e-6, where)
                    self.assertTrue(strips.inside(plan.panel, (tool, 0, size[0] - tool - sidebar, size[1])),
                                    (where, plan.panel))   # Never under the toolbar or the sidebar.
                    self.assertLessEqual(plan.panel[1] + plan.panel[3], size[1] - view["headers"] + 1e-6, where)
                    self.assertIsNotNone(plan.minimize, where)

    def test_it_stays_clear_wherever_the_clear_room_holds_it_readable(self):
        for size, s, view in self.cramped():
            for position in POSITIONS:
                plan, _room = readable_place(position, sessions(3), size, s, dict(view, corners=[]))
                bare = dict(view, corners=[], label=0 if position == "LEFT" else view["label"])
                strict, _ = strips.place(position, sessions(3), size, s, bare)
                if strict.tiles and strict.scale >= strips.strip.READABLE * s:
                    self.assertEqual(plan.panel, strict.panel, (position, size, s))   # Nothing relaxed.

    def test_the_windows_views_show_the_round_button(self):
        """The two Windows readings (712x581 at 2x, 708x502 at 3x, the sidebar over most of the view): no readable
        panel fits, so the round button stands for it, inside the view, and its tooltip says why."""
        for size, s, gizmo, sidebar in (((712, 581), 2.0, (-29.0, 43.4, 163.0, 413.6), 561.0),
                                        ((708, 502), 3.0, (-101.0, -304.4, 245.0, 620.4), 537.0)):
            view = dict(sides=(44 * s, sidebar), headers=52 * s, label=strips.label.room(s), corners=[gizmo])
            for position in POSITIONS:
                plan, _room = readable_place(position, sessions(16), size, s, view)
                self.assertIsNone(plan, (position, s))
            cx, cy, r = rm.place(size, {"left": 44 * s, "right": sidebar, "top": 52 * s}, s, gizmo)
            self.assertTrue(r <= cx <= size[0] - r and r <= cy <= size[1] - r, (s, cx, cy, r))
        self.assertTrue(rm.label(15, False, cramped=True).startswith(
            "No room for the Sessions panel here: close the sidebar or widen the view"))

    def test_the_main_tile_stays_before_the_more_lines(self):
        """A column with room for two compact tiles but not their "+N more" lines keeps the main tile pinned; the
        header's arrows page it."""
        s, count = 2.0, 17
        two = 2 * rail.PAD + rail.HEADER + 2 * rail.TEXT_BLOCK + rail.GAP + 2 * rail.MARGIN   # Two tiles, no lines.
        for extra in (0, 10, 30, 60):
            height = (two + extra) * s
            plan = rail.layout(sessions(count - 1), (1280, height), s)
            self.assertTrue(plan.paged)
            self.assertEqual(plan.tiles[0].session, "lead", extra)
            self.assertIsNotNone(plan.arrows)
            ids = [t.session for t in plan.tiles]
            for _step in range(count):
                plan = rail.layout(sessions(count - 1), (1280, height), s, first=plan.first + 1)
                self.assertEqual(plan.tiles[0].session, "lead")
                ids += [t.session for t in plan.tiles if t.session not in ids]
            self.assertEqual(len(ids), count, extra)   # Every worker reached by the arrows.


class CrampedTests(unittest.TestCase):
    def test_a_cramped_view_shows_the_round_button_whose_click_keeps_the_choice(self):
        """While no readable panel fits, the round button takes the panel's events (hover, press) and a click never
        flips the person's choice; once the panel fits again the button stands down."""
        from angelo_agent import session_rail_controls as controls
        rm.release()
        self.addCleanup(rm.release)
        setting = SimpleNamespace(minimized=False, set_calls=[])
        setting.rail_minimized = lambda: setting.minimized
        setting.set_rail_minimized = lambda value: setting.set_calls.append(value)
        patches = [mock.patch.dict(sys.modules, {"angelo_agent.parallel_setting": setting}),
                   mock.patch.object(angelo_agent, "parallel_setting", setting, create=True),
                   mock.patch.object(rm, "_redraw", lambda window, area: None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        window = SimpleNamespace(as_pointer=lambda: 9, cursor_modal_set=lambda kind: None,
                                 cursor_modal_restore=lambda: None, screen=None)
        rm._places[9] = (11, 100.0, 100.0, 38.0)
        event = lambda kind, value: SimpleNamespace(type=kind, value=value, mouse_x=100, mouse_y=100)
        self.assertIsNone(controls.restore_event(window, event('LEFTMOUSE', 'PRESS')), "the panel shows: not its")
        rm.cramp(9, True)
        self.assertTrue(rm.shown(9) and rm.hit(9, 100, 100))
        self.assertEqual(controls.restore_event(window, event('LEFTMOUSE', 'PRESS')), {'RUNNING_MODAL'})
        self.assertEqual(controls.restore_event(window, event('LEFTMOUSE', 'RELEASE')), {'RUNNING_MODAL'})
        self.assertEqual(setting.set_calls, [], "a cramped view's click keeps the person's choice")
        self.assertIn("No room for the Sessions panel", rm.label(3, False, cramped=True))
        rm.cramp(9, False)
        self.assertFalse(rm.shown(9))


if __name__ == "__main__":
    unittest.main()
