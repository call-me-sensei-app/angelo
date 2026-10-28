# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel's three places without Blender (``parallel_setting.rail_position``; the maintainer, 2026-10-25:
"it can potentially be at the top and bottom in the middle as well. We can let users decide"): the strips centred
across the top and the bottom (``session_rail_strip``), the "+N more" rows and the minimize button in every
position, and a panel that never leaves the view at any interface scale (Windows T4, 2026-10-25: the paged column ran
8 px off a 502 px view at 3x). Each rule is checked over every position, scale, view size and worker count here, never
one layout; the in-app proof is the rail positions GUI suite."""

import sys
import unittest
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import live_view_label as label, session_rail as rail, session_rail_controls as controls  # noqa: E402
from angelo_agent import session_rail_strip as strip  # noqa: E402

EPS = 1e-6
SIZES = ((1280, 720), (1920, 1080))
SCALES = (1.0, 2.0)
WORKERS = (1, 3, 16)
POSITIONS = ("LEFT", "TOP", "BOTTOM")


def sessions(workers, waiting=()):
    """The main tile, then ``workers`` workers; numbers in ``waiting`` wait for approval, the last of several ended."""
    tiles = [rail.Tile("lead", "Scene", "Main", "Idle", rail.IDLE, active=True)]
    for n in range(1, workers + 1):
        kind = rail.APPROVAL if n in waiting else rail.ENDED if n == workers > 1 else rail.WORKING
        tiles.append(rail.Tile("w%d" % n, "Part %d" % n, "Worker %d" % n, "Working", kind, progress=(1, 3)))
    return tiles


def overlaps(a, b):
    return (a[0] < b[0] + b[2] - EPS and a[0] + a[2] > b[0] + EPS and a[1] < b[1] + b[3] - EPS
            and a[1] + a[3] > b[1] + EPS)


def inside(inner, outer):
    return (inner[0] >= outer[0] - EPS and inner[1] >= outer[1] - EPS
            and inner[0] + inner[2] <= outer[0] + outer[2] + EPS and inner[1] + inner[3] <= outer[1] + outer[3] + EPS)


def centre(rect):
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def views(size, s):
    """``(name, strip.insets keywords, rects never covered, rects not covered where the view has room)`` of each kind
    of 3D view a panel stands in, at scale ``s``: the numbers are Blender's and Angelo's own at that scale."""
    width, height = size
    headers = 52 * s                                                    # Header + tool header, 26 px each.
    top_bar = (0, height - headers, width, headers)
    live = label.rect(width, height - headers, 460 * s, s)             # A live view's label under them.
    text = headers + 150 * s                                            # Statistics on: a deep text block.
    words = (0, height - text, 300 * s, text - headers)
    shelf = 146 * s                                                     # An asset shelf and its tab strip.
    pills = (56 * s, shelf + 6 * s, 290 * s, 28 * s)                    # Media Studio + Generation Flows on it.
    gizmo = (width - 90 * s, height - headers - 220 * s, 90 * s, 220 * s)   # Axes + four buttons, top right.
    disc = (width - 92 * s, 16 * s, 76 * s, 76 * s)                     # Angelo in the corner (chat hidden).
    bars = ((0, 0, 48 * s, height), (width - 320 * s, 0, 320 * s, height))
    yield "bare", {}, [], []
    yield "header", dict(headers=headers), [top_bar], []
    yield "live label", dict(headers=headers, label=label.room(s)), [top_bar, live], []
    yield "text info", dict(headers=headers, label=label.room(s), text=text), [top_bar, live], [words]
    yield "asset shelf", dict(shelf=shelf, edge=(pills[1] + pills[3],)), [(0, 0, width, shelf), pills], []
    yield "Director", dict(corners=[disc]), [disc], []   # No header, toolbar, sidebar, shelf or gizmo there.
    yield "toolbar and sidebar", dict(sides=(48 * s, 320 * s)), list(bars), []
    yield "gizmo", dict(headers=headers, corners=[gizmo]), [top_bar, gizmo], []
    yield "everything", dict(sides=(48 * s, 0), headers=headers, label=label.room(s), text=text, shelf=shelf,
                             edge=(pills[1] + pills[3],), corners=[gizmo, disc]), \
        [top_bar, live, (0, 0, width, shelf), pills, gizmo, disc, bars[0]], [words]


def place(position, tiles, size, s, view, first=0, keep=None):
    """``(plan, room)``: the panel in ``position`` of a view with ``view`` (the obstacle keywords
    ``session_rail_place.obstacles`` reads), each position's room from the same set, as ``session_rail_place``."""
    if position == "LEFT":
        room = strip.column_insets(size, s, len(tiles), **view)
        plan = rail.layout(tiles, size, s, room["left"], room["top"], room["bottom"], first, keep, room["right"])
        return plan, room
    room = strip.insets(position, size, s, **view)
    return strip.layout(tiles, size, s, room, position, first, keep), room


class Checks(unittest.TestCase):
    def check(self, plan, tiles, size, room, hard=(), where=""):
        """Inside the view and its insets, clear of ``hard``, nothing on the panel overlapping, every hit where it is
        drawn (buttons and dismiss before tiles), and what paging hides counted and flagged."""
        width, height = size
        view = (room["left"], room["bottom"], width - room["left"] - room["right"],
                height - room["top"] - room["bottom"])
        self.assertTrue(plan.tiles, where)
        self.assertTrue(inside(plan.panel, view), (where, plan.panel, view))
        for rect in hard:
            self.assertFalse(overlaps(plan.panel, rect), (where, plan.panel, rect))
        items = [t.rect for t in plan.tiles] + [r for r in plan.more if r is not None]
        for a, b in combinations(items, 2):
            self.assertFalse(overlaps(a, b), (where, a, b))
        for rect in items + [plan.minimize] + list(plan.arrows or ()):
            self.assertTrue(inside(rect, plan.panel), (where, rect))
        for rect in [plan.minimize] + list(plan.arrows or ()):
            self.assertFalse(any(overlaps(rect, item) for item in items), where)   # In the header row.
        self.assertEqual(rail.hit(plan, *centre(plan.minimize)), ("minimize", None), where)
        for rect, step in zip(plan.arrows or (), (-1, 1)):
            self.assertEqual(rail.hit(plan, *centre(rect)), ("scroll", step), where)
        for side, rect in enumerate(plan.more):
            if rect is not None:
                self.assertEqual(rail.hit(plan, *centre(rect)), ("page", (-1, 1)[side] * plan.page), where)
        for t in plan.tiles:
            self.assertEqual(rail.hit(plan, t.rect[0] + 3, t.rect[1] + 3), ("switch", t.session), where)
            if t.close is not None:
                self.assertEqual(rail.hit(plan, *centre(t.close)), ("dismiss", t.session), where)
        shown = [t.session for t in plan.tiles]
        if not plan.paged:
            self.assertEqual(shown, [t.session for t in tiles], where)
            self.assertEqual((plan.more, plan.arrows, plan.hidden), ((None, None), None, (0, 0)), where)
            return
        pinned = len(shown) >= 2   # The main tile stays whenever two tiles fit.
        if pinned:
            self.assertEqual(shown[0], "lead", where)
        pool = tiles[1:] if pinned else tiles
        window = shown[1:] if pinned else shown
        at = [t.session for t in pool].index(window[0])
        self.assertEqual([t.session for t in pool[at:at + len(window)]], window, where)
        before, after = pool[:at], pool[at + len(window):]
        self.assertEqual(plan.hidden, (len(before), len(after)), where)
        self.assertEqual(plan.attention, (any(t.kind == rail.APPROVAL for t in before),
                                          any(t.kind == rail.APPROVAL for t in after)), where)
        lines = tuple(rect is not None for rect in plan.more)   # Where two tiles fit only without the lines, they go
        if pinned and lines == (False, False) and (before or after):   # and the header's arrows page (session_rail.page).
            self.assertIsNotNone(plan.arrows, where)
        else:
            self.assertEqual(lines, (bool(before), bool(after)), where)
        self.assertTrue(1 <= plan.page <= len(window), where)   # A page on never skips a worker.


class StripTests(Checks):
    def test_every_position_scale_size_count_and_view_keeps_clear(self):
        """1, 3 and 16 workers, 1x and 2x, 1280x720 and 1920x1080, every position and every kind of view."""
        for size in SIZES:
            for s in SCALES:
                for name, view, hard, soft in views(size, s):
                    for workers in WORKERS:
                        tiles = sessions(workers, waiting={2, 13})
                        for position in POSITIONS:
                            where = (position, size, s, name, workers)
                            plan, room = place(position, tiles, size, s, view)
                            self.check(plan, tiles, size, room, hard, where)   # One obstacle set everywhere.
                            if position == "LEFT":
                                continue
                            self.assertEqual(plan.orientation, "HORIZONTAL")
                            left, right = plan.panel[0] - room["left"], size[0] - room["right"] - sum(plan.panel[::2])
                            self.assertAlmostEqual(left, right, places=6, msg=where)   # Centred in its room.
                            xs = [t.rect[0] for t in plan.tiles]
                            self.assertEqual(xs, sorted(xs), where)   # Side by side, in rail order.
                            if size[1] - room["bottom"] - strip.least(1)[0] * s >= view.get("text", 0):
                                for rect in soft:   # Below the text info wherever the view has room.
                                    self.assertFalse(overlaps(plan.panel, rect), where)

    def test_the_strips_pages_keep_the_main_tile_and_reach_every_worker(self):
        for size in SIZES:
            for s in SCALES:
                tiles = sessions(16, waiting={2, 13})
                for position in POSITIONS:
                    view = dict(headers=52 * s, label=label.room(s), sides=(48 * s, 0))
                    plan, room = place(position, tiles, size, s, view)
                    where = (position, size, s)
                    self.assertTrue(plan.paged, where)
                    seen, steps = {t.session for t in plan.tiles}, 0
                    while plan.hidden[1]:   # The trailing "+N more": a page each click (scroll_by keeps it >= 0).
                        step = rail.hit(plan, *centre(plan.more[1]))[1]
                        self.assertEqual(step, plan.page, where)
                        previous = plan.first
                        plan, _room = place(position, tiles, size, s, view, max(0, plan.first + step))
                        self.check(plan, tiles, size, room, (), where)
                        self.assertGreater(plan.first, previous, where)
                        seen |= {t.session for t in plan.tiles}
                        steps += 1
                    self.assertEqual(seen, {t.session for t in tiles}, where)
                    self.assertEqual(plan.more[1], None, where)
                    back = rail.hit(plan, *centre(plan.more[0]))
                    self.assertEqual(back, ("page", -plan.page), where)
                    plan, _room = place(position, tiles, size, s, view, max(0, plan.first + back[1]))
                    self.assertIsNotNone(plan.more[1], where)   # Back a page: later ones are hidden again.

    def test_a_switch_brings_the_active_worker_into_view_in_every_position(self):
        tiles = sessions(16)
        for position in POSITIONS:
            for s in SCALES:
                view = dict(headers=52 * s)
                last, _room = place(position, tiles, (1280, 720), s, view, keep=15)
                self.assertIn("w16", [t.session for t in last.tiles], (position, s))
                again, _room = place(position, tiles, (1280, 720), s, view, first=last.first, keep=0)
                self.assertIn("w1", [t.session for t in again.tiles], (position, s))
                self.assertEqual(again.tiles[0].session, "lead", (position, s))

    def test_the_header_holds_the_minimize_button_last_and_the_arrows_before_it(self):
        for position in POSITIONS:
            for workers in WORKERS:
                plan, _room = place(position, sessions(workers), (1280, 720), 1.0, dict(headers=52))
                mx, my, mw, mh = plan.minimize
                self.assertAlmostEqual(mx + mw, plan.panel[0] + plan.panel[2] - rail.PAD, places=6)
                self.assertLess(plan.header_end, mx)
                for arrow in plan.arrows or ():
                    self.assertLess(plan.header_end, arrow[0])
                    self.assertLess(arrow[0] + arrow[2], mx + 1e-6)
                self.assertEqual(rail.hover_key(rail.hit(plan, *centre(plan.minimize))), rail.MINIMIZE_KEY)
                self.assertIn(rail.MINIMIZE_KEY, rail.control_keys(plan))

    def test_a_strip_shrinks_its_pictures_in_a_short_view_and_drops_them_last(self):
        tiles = sessions(3)
        tall, _r = place("TOP", tiles, (1920, 1400), 1.0, {})
        short, _r = place("TOP", tiles, (1920, 640), 1.0, {})
        tiny, _r = place("TOP", tiles, (1920, 300), 1.0, {})
        self.assertGreater(tall.tiles[0].picture[3], short.tiles[0].picture[3])
        self.assertAlmostEqual(tall.tiles[0].picture[3] / tall.tiles[0].picture[2], rail.PICTURE_RATIO, places=3)
        self.assertTrue(tiny.compact and all(t.picture is None for t in tiny.tiles))
        for plan, height in ((tall, 1400), (short, 640)):
            self.assertLessEqual(plan.panel[3], (height - 2 * rail.MARGIN) * strip.SHARE + 1e-6)

    def test_a_corner_is_cleared_only_where_the_strips_band_crosses_it(self):
        gizmo = (1190, 448, 90, 220)
        top = strip.insets("TOP", (1280, 720), 1.0, headers=52, corners=[gizmo])
        bottom = strip.insets("BOTTOM", (1280, 720), 1.0, headers=52, corners=[gizmo])
        self.assertEqual((top["right"], bottom["right"]), (90, 0))
        disc = (16, 16, 76, 76)   # Angelo in the left corner (a chat docked on the left).
        self.assertEqual(strip.insets("BOTTOM", (1280, 720), 1.0, corners=[disc])["left"], 92)
        self.assertEqual(strip.insets("TOP", (1280, 720), 1.0, headers=52, corners=[disc])["left"], 0)


class NeverLeavesTheViewTests(Checks):
    def test_no_panel_leaves_the_view_at_any_scale(self):
        """Scales 1-3, views 300-1080 px tall and 480-1920 wide, 1, 3 and 16 workers and one session alone, every
        position, the paged case included: the panel and its "+N more" rows and arrows stay inside."""
        checked = 0
        for s in (1.0, 1.25, 1.5, 2.0, 3.0):
            for height in list(range(300, 1081, 60)) + [502]:
                for width in (480, 800, 1280, 1920):
                    for workers in (0,) + WORKERS:
                        tiles = sessions(workers, waiting={2})
                        view = dict(sides=(48 * s, 0), headers=52 * s, label=label.room(s), text=52 * s + 60 * s)
                        for position in POSITIONS:
                            where = (position, s, width, height, workers)
                            plan, room = place(position, tiles, (width, height), s, view)
                            if not plan.tiles:   # Only where the headers leave no room at all.
                                self.assertLessEqual(height - room["top"] - room["bottom"], 0, where)
                                continue
                            self.check(plan, tiles, (width, height), room, (), where)
                            self.assertLessEqual(plan.scale, s + EPS, where)
                            checked += 1
        self.assertGreater(checked, 3000)   # Every combination laid out, not skipped.

    def test_the_windows_case_a_502_px_view_at_3x(self):
        """Windows T4 (test_blender_session_rail): headers 156 px and a 145 px toolbar at 3x put the paged column's
        panel at (181, -8, 612, 318); it now lays out smaller, wholly inside, with its rows and arrows reachable."""
        tiles = sessions(3)
        plan = rail.layout(tiles, (1280, 502), 3.0, 145, 156, 0)
        self.assertGreaterEqual(plan.panel[1], -EPS)
        self.assertLessEqual(plan.panel[1] + plan.panel[3], 502 - 156 + EPS)
        self.assertLess(plan.scale, 3.0)
        self.check(plan, tiles, (1280, 502), {"left": 145, "right": 0, "top": 156, "bottom": 0})

    def test_a_view_with_room_keeps_the_persons_scale(self):
        for position in POSITIONS:
            for workers in WORKERS:
                for s in SCALES:
                    plan, _room = place(position, sessions(workers), (1920, 1080), s, dict(headers=52 * s))
                    self.assertEqual(plan.scale, s, (position, workers, s))


class MoreRowTests(Checks):
    def test_the_rows_show_only_where_tiles_are_hidden_with_their_count_and_mark(self):
        for position in POSITIONS:
            for s in SCALES:
                tiles = sessions(16, waiting={2, 15})
                for first in range(0, 17, 3):
                    plan, room = place(position, tiles, (1280, 720), s, dict(headers=52 * s), first)
                    self.check(plan, tiles, (1280, 720), room, (), (position, s, first))
        few, _room = place("TOP", sessions(1), (1280, 720), 1.0, {})
        self.assertEqual((few.paged, few.more), (False, (None, None)))

    def test_the_rows_text_is_translatable_and_drawn_with_its_chevron(self):
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        self.assertIn('iface_("+{count} more", "Angelo", count=count)', draw)
        self.assertIn("rail_glyph.chevron(", draw[draw.index("def _more("):])
        self.assertIn('iface_("Minimize the Sessions panel")', draw)
        self.assertIn("rail_glyph.minimize(", draw)


class InputTests(unittest.TestCase):
    def test_wheel_and_both_trackpad_axes_step_a_strip_towards_later_sessions(self):
        plan = SimpleNamespace(step=196.0, scale=2.0)
        self.assertEqual(controls.along(SimpleNamespace(type='WHEELDOWNMOUSE'), plan), (196.0, 196.0))
        self.assertEqual(controls.along(SimpleNamespace(type='WHEELUPMOUSE'), plan), (-196.0, 196.0))
        pan = dict(type='TRACKPADPAN', mouse_prev_x=100, mouse_prev_y=100)
        self.assertEqual(controls.along(SimpleNamespace(mouse_x=60, mouse_y=102, **pan), plan)[0], 20.0)  # Left.
        self.assertEqual(controls.along(SimpleNamespace(mouse_x=99, mouse_y=140, **pan), plan)[0], 20.0)  # Up.
        self.assertEqual(controls.along(SimpleNamespace(mouse_x=140, mouse_y=100, **pan), plan)[0], -20.0)

    def stubs(self, minimized=True):
        """``parallel_setting`` and ``rail_minimized`` as the handler reaches them; the circle's ``handle`` records."""
        state = SimpleNamespace(minimized=minimized, handled=[], left=[], released=[])
        setting = SimpleNamespace(rail_minimized=lambda: state.minimized)
        circle = SimpleNamespace(shown=lambda pointer: state.minimized,
                                 handle=lambda window, event: state.handled.append(event.type) or (
            {'RUNNING_MODAL'} if event.type == 'LEFTMOUSE' else None), leave=state.left.append,
            release=lambda: state.released.append(True))
        modules = {"parallel_setting": setting, "rail_minimized": circle}
        patches = [mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in modules.items()})]
        patches += [mock.patch.object(angelo_agent, name, value, create=True) for name, value in modules.items()]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return state

    def test_a_minimized_panel_hands_its_circle_the_events_and_takes_none_of_its_own(self):
        state, win = self.stubs(), SimpleNamespace(as_pointer=lambda: 7)
        event = lambda kind: SimpleNamespace(type=kind, value='PRESS', mouse_x=5, mouse_y=5)
        self.assertEqual(controls.restore_event(win, event('LEFTMOUSE')), {'RUNNING_MODAL'})
        self.assertEqual(controls.restore_event(win, event('MOUSEMOVE')), {'PASS_THROUGH'})   # No panel hover.
        self.assertIsNone(controls.restore_event(win, event('A')))   # Not the circle's: the handler goes on.
        self.assertEqual(state.handled, ['LEFTMOUSE', 'MOUSEMOVE', 'A'])
        controls.leave(win)
        controls.release()
        self.assertEqual((state.left, state.released), ([win], [True]))

    def test_nothing_reaches_the_circle_while_the_panel_shows(self):
        state, win = self.stubs(minimized=False), SimpleNamespace(as_pointer=lambda: 7)
        self.assertIsNone(controls.restore_event(win, SimpleNamespace(type='LEFTMOUSE', value='PRESS')))
        self.assertEqual(state.handled, [])

    def test_the_minimize_button_minimizes_and_ends_the_panels_hover(self):
        chosen = []
        setting = SimpleNamespace(set_rail_minimized=chosen.append)
        handler = SimpleNamespace(left=[])
        handler._leave = handler.left.append
        with mock.patch.dict(sys.modules, {"angelo_agent.parallel_setting": setting}), \
                mock.patch.object(angelo_agent, "parallel_setting", setting, create=True):
            controls.minimize(handler, "win")
        self.assertEqual((chosen, handler.left), ([True], ["win"]))

    def test_the_handler_and_the_draw_are_wired(self):
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        modal = source[source.index("    def modal(self, context, event):"):source.index("    def _drag_to(")]
        restore = modal.index("rail_controls.restore_event(window, event)")
        self.assertLess(restore, modal.index("main = rail_region(area)"))
        self.assertIn("rail_controls.leave(window)", source[source.index("    def _leave("):])
        self.assertEqual(source.count("rail_controls.release()"), 2)   # A file load and unregister.
        self.assertIn('elif found[0] == "minimize":\n' + " " * 24 + "rail_controls.minimize(self, window)", modal)
        self.assertIn('if plan.orientation == "VERTICAL" else rail_controls.along(event, plan)', modal)
        self.assertIn("under = session_rail.hover_key(found)", modal)
        draw = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        body = draw[draw.index("def _draw("):draw.index("def _paint(")]
        self.assertLess(body.index("session_rail_controls.minimized()"), body.index("session_rail_place.column("))
        self.assertIn("rail_minimized.draw(window, area, region, workers, waiting, scale, avoid=avoid, cramped=cramped)",
                      body)
        self.assertIn("last[window.as_pointer()] = (area.as_pointer(), rail.Layout(scale=scale))", body)
        self.assertIn("_circle(window, area, region, tiles, scale, label, True)", body)   # No readable room.
        self.assertIn("session_rail_place.strip(", body)
        self.assertIn("session_rail_place.column(", body)
        self.assertIn("rail_minimized.counts(tiles, parallel_sessions.lead)", body)
        self.assertIn("avoid = tuple(rect for rect in (label, session_rail_place.angelo(window, area, region),", body)
        self.assertIn("*session_rail_place.pill_rects(window, area, region)) if rect)", body)
        self.assertIn("circle.region_of(area) if circle else rail_region(area)", draw[:draw.index("def _draw(")])
        live = (ADDON / "live_view_draw.py").read_text(encoding="utf-8")
        self.assertIn("live_view_label.rect(region.width, top, width + 28 * scale, scale)", live)
        self.assertIn("line, (x, y, width, height) = _chip(area, region, session_id)", live)
        self.assertIn("label = live_view_draw.chip_rect(window, area, region)", draw)

    def test_the_shelf_pills_and_angelo_in_the_corner_are_read_where_they_were_drawn(self):
        from angelo_agent import session_rail_place as place_module
        shelf = SimpleNamespace(places={11: (7, (("studio", (60, 152, 120, 28)), ("flows", (186, 152, 140, 28))),
                                                 True, "3d")})
        launcher = SimpleNamespace(_places={7: (11, 1200, 80, 38)}, _shown={7: 11})
        gizmo = SimpleNamespace(navigation_rect=lambda area, region: (1180, 400, 90, 260))
        modules = {"asset_shelf_add": shelf, "chat_launcher": launcher}
        with mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in modules.items()}), \
                mock.patch.object(angelo_agent, "asset_shelf_add", shelf, create=True), \
                mock.patch.object(angelo_agent, "chat_launcher", launcher, create=True), \
                mock.patch.dict(sys.modules, {"angelo_agent.rail_minimized": gizmo}), \
                mock.patch.object(angelo_agent, "rail_minimized", gizmo, create=True):
            window, area = SimpleNamespace(as_pointer=lambda: 7), SimpleNamespace(as_pointer=lambda: 11)
            region = SimpleNamespace(x=10, y=20, width=1280, height=720)
            self.assertEqual(place_module.pills(window, area, region), (160, 160))
            self.assertEqual(place_module.corners(window, area, region), [(1152, 22, 76, 76), (1180, 400, 90, 260)])
            other = SimpleNamespace(as_pointer=lambda: 8)
            self.assertEqual(place_module.pills(other, area, region), ())


if __name__ == "__main__":
    unittest.main()
