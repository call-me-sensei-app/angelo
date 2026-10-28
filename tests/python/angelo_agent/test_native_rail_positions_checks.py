# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""rail_positions_checks: the geometry rules test_blender_rail_positions holds the Sessions panel to (1.0.6), on plain
values: each rule passes a layout that keeps it and fails one that breaks it, at more than one scale and size."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rail_positions_checks as rules  # noqa: E402

VIEW = NS(show_navigate_ui=True, mini_axis_type='GIZMO', gizmo_size_navigate_v3d=80, mini_axis_size=25)
SPACE = NS(show_gizmo=True, show_gizmo_navigate=True)


def region(kind, x, y, w, h, alignment='NONE'):
    return NS(type=kind, alignment=alignment, x=x, y=y, width=w, height=h)


def tile(session, rect, active=False):
    return NS(session=session, rect=rect, active=active)


def strip(scale=1.0, width=1280, height=640, top=True, first=0, shown=5, total=17, more=True):
    """A TOP or BOTTOM strip of ``shown`` tiles (main pinned first) centred in a region ``width`` x ``height``."""
    s = scale
    tile_w, tile_h, gap = 188 * s, 64 * s, 8 * s
    row = shown * tile_w + (shown - 1) * gap
    x0 = (width - row) / 2
    y = height - 120 * s - tile_h if top else 60 * s
    order = ["main"] + ["w%d" % n for n in range(1, total)]
    sessions = ["main"] + order[1 + first:first + shown]
    tiles = [tile(sid, (x0 + i * (tile_w + gap), y, tile_w, tile_h)) for i, sid in enumerate(sessions)]
    hidden = (first, total - shown - first)
    panel = (x0 - 8 * s, y - 8 * s, row + 16 * s, tile_h + 42 * s)
    arrows = ((panel[0] + 4 * s, y + tile_h + 6 * s, 20 * s, 20 * s),
              (panel[0] + panel[2] - 24 * s, y + tile_h + 6 * s, 20 * s, 20 * s))
    lines = tuple((panel[0] + 30 * s + i * 200 * s, y + tile_h + 6 * s, 60 * s, 20 * s) if n else None
                  for i, n in enumerate(hidden)) if more else None
    minimize = (panel[0] + panel[2] - 48 * s, y + tile_h + 6 * s, 20 * s, 20 * s)
    hits = [(minimize, "minimize", None), (arrows[0], "scroll", -1), (arrows[1], "scroll", 1)]
    hits += [(r, "page", (-1, 1)[i] * 4) for i, r in enumerate(lines or ()) if r]
    hits += [(t.rect, "switch", t.session) for t in tiles]
    plan = NS(panel=panel, tiles=tiles, hits=hits, scale=s, paged=True, first=first, hidden=hidden,
              attention=(False, False), arrows=arrows, orientation="HORIZONTAL", minimize=minimize)
    if lines is not None:
        plan.more = lines
    return plan, order


def failing(found):
    return sorted(name for name, passed, _detail in found if not passed)


class RectTests(unittest.TestCase):
    def test_a_touch_is_not_an_overlap(self):
        self.assertFalse(rules.overlaps((0, 0, 10, 10), (10, 0, 10, 10)))
        self.assertTrue(rules.overlaps((0, 0, 10, 10), (9, 9, 10, 10)))
        self.assertTrue(rules.contains((0, 0, 10, 10), (2, 2, 8, 8)))
        self.assertFalse(rules.contains((0, 0, 10, 10), (2, -3, 8, 8)))

    def test_a_disc_meets_a_rectangle_only_within_its_radius(self):
        self.assertFalse(rules.circle_meets((0, 0, 10), (8, 8, 5, 5)))   # The corner is 11.3 away.
        self.assertTrue(rules.circle_meets((0, 0, 10), (5, -2, 5, 4)))

    def test_obstacles_are_the_regions_over_the_view_only(self):
        main = region('WINDOW', 0, 0, 1000, 600)
        regions = [main, region('HEADER', 0, 574, 1000, 26, 'TOP'), region('TOOLS', 0, 0, 40, 574, 'LEFT'),
                   region('UI', 1000, 0, 200, 600, 'RIGHT'), region('ASSET_SHELF', 0, 0, 0, 0, 'BOTTOM')]
        self.assertEqual(rules.view_obstacles(regions, main),
                         [('header', (0, 574, 1000, 26)), ('tools', (0, 0, 40, 574))])   # The sidebar beside it.


class GizmoTests(unittest.TestCase):
    def test_the_gizmo_is_what_blender_draws_at_the_visible_top_right(self):
        """Worked by hand from view3d_gizmo_navigate.cc and button2d_gizmo.cc. The visible rect is an rcti: its xmax
        the region's last column (width - 1), its ymax the header's bottom row. Scale 1: axes centred 50 in, radius
        40; column x = roundf(xmax - 22.5) = xmax - 22, buttons 30 apart from roundf(ymax - 110), bounds +-14, the
        backdrop padded 2.8 above and below. Scale 2: axes 100 in, radius 80; column x = xmax - 45, bounds +-28 (to
        xmax - 17), pad 5.6. The GIZMO_OFFSET margin at the right and top is empty: never part of the rect."""
        for width, height in ((1280, 640), (1920, 1000)):
            main = region('WINDOW', 0, 0, width, height)
            header = region('HEADER', 0, height - 26, width, 26, 'TOP')
            xmax, ymax = width - 1, height - 26
            one = rules.navigation_gizmo([main, header], main, VIEW, 1.0, SPACE)
            two = rules.navigation_gizmo([main, header], main, VIEW, 2.0, SPACE)
            for got, want in zip(one, (xmax - 90, ymax - 110 - 90 - 14 - 2.8, 82, 206.8)):
                self.assertAlmostEqual(got, want)
            for got, want in zip(two, (xmax - 180, ymax - 220 - 180 - 28 - 5.6, 163, 413.6)):
                self.assertAlmostEqual(got, want)

    def test_a_sidebar_over_the_view_moves_it_left_and_hidden_gizmos_draw_none(self):
        main = region('WINDOW', 0, 0, 1280, 640)
        sidebar = region('UI', 1000, 0, 280, 640, 'RIGHT')
        x, _y, w, _h = rules.navigation_gizmo([main, sidebar], main, VIEW, 1.0, SPACE)
        self.assertEqual((x, x + w), (1000 - 90, 1000 - 22 + 14))   # xmax = the sidebar's first column.
        self.assertIsNone(rules.navigation_gizmo([main], main, VIEW, 1.0, NS(show_gizmo=False)))
        hidden = NS(show_navigate_ui=False, mini_axis_type='NONE')
        self.assertIsNone(rules.navigation_gizmo([main], main, hidden, 1.0, SPACE))
        minimal = NS(show_navigate_ui=False, mini_axis_type='MINIMAL', mini_axis_size=25)
        self.assertIsNotNone(rules.navigation_gizmo([main], main, minimal, 1.0, SPACE), "view3d_draw draws it")

    def test_the_column_counts_the_buttons_the_view_shows(self):
        main = region('WINDOW', 0, 0, 1280, 640)
        full = rules.navigation_gizmo([main], main, VIEW, 1.0, SPACE)
        for data, slots in ((NS(lock_rotation=True, view_perspective='PERSP'), 2),
                            (NS(lock_rotation=False, view_perspective='CAMERA'), 4),
                            (NS(lock_rotation=True, view_perspective='CAMERA'), 3)):
            main.data = data
            got = rules.navigation_gizmo([main], main, VIEW, 1.0, SPACE)
            self.assertAlmostEqual(got[1], full[1] + 30 * (4 - slots), msg=(data, slots))

    def test_quad_view_draws_the_axes_in_the_top_right_quarter_only(self):
        quarters = [region('WINDOW', x, y, 640, 320, 'QUAD_SPLIT') for x in (0, 640) for y in (0, 320)]
        corner = rules.navigation_gizmo(quarters, quarters[3], VIEW, 1.0, SPACE)
        other = rules.navigation_gizmo(quarters, quarters[0], VIEW, 1.0, SPACE)
        self.assertAlmostEqual(corner[0], 639 - 90)
        # No axes: icon_offset = 10, the column from roundf(319 - 22) down; its left edge 639 - 22 - 14.
        self.assertAlmostEqual(other[0], 639 - 36)
        self.assertAlmostEqual(other[1] + other[3], 319 - 22 + 14 + 2.8)

    def test_the_pixel_size_follows_blenders_dpi(self):
        # wm_window.cc: dpi = 72 * scale, pixelsize = max(1, dpi / 64) + the line width preference.
        self.assertEqual([rules._pixel_size(s, VIEW) for s in (0.5, 1.0, 2.0, 3.0)], [1, 1, 2, 3])
        self.assertEqual(rules._pixel_size(2.0, NS(ui_line_width='THICK')), 3)
        self.assertEqual(rules._pixel_size(1.0, NS(ui_line_width='THIN')), 1)

    def test_the_products_gizmo_rect_must_cover_blenders_own(self):
        own = (1190, 400, 90, 214)
        self.assertEqual(failing(rules.gizmo_rules((1185, 395, 95, 220), own, 1.0)), [])
        self.assertIn("gizmo_rect_covers_blenders_own", failing(rules.gizmo_rules((1200, 450, 80, 150), own, 1.0)))
        self.assertEqual(failing(rules.gizmo_rules(None, None, 1.0)), [])   # Hidden: neither draws one.
        self.assertIn("gizmo_rect_covers_blenders_own", failing(rules.gizmo_rules(None, own, 1.0)))
        self.assertIn("gizmo_rect_is_known", failing(rules.gizmo_rules(None, own, 1.0, "ImportError")))

    def test_the_label_is_centred_under_the_headers(self):
        x, y, w, h = rules.label_rect(1000, 600, [('header', (0, 570, 1000, 30))], 300, 2.0)
        self.assertEqual((x, w, h), (350, 300, 52))
        self.assertEqual(y + h, 570 - 24)


class PlanTests(unittest.TestCase):
    def test_a_centred_strip_that_keeps_every_rule_passes_at_any_scale_and_size(self):
        for scale, width, height in ((1.0, 1280, 640), (2.0, 2560, 1280), (1.5, 1920, 1000)):
            plan, order = strip(scale, width, height, first=3, shown=5)
            obstacles = [('header', (0, height - 50 * scale, width, 50 * scale))]
            found = rules.plan_rules(plan, (width, height), obstacles, "TOP", order)
            self.assertEqual(failing(found), [], (scale, width))

    def test_a_strip_under_a_header_a_lost_count_and_overlapping_tiles_fail(self):
        plan, order = strip(first=2, shown=5)
        header = ('header', (0, plan.panel[1] + plan.panel[3] - 10, 1280, 60))
        self.assertIn("panel_clears_header", failing(rules.plan_rules(plan, (1280, 640), [header], "TOP", order)))
        plan.hidden = (2, 9)
        self.assertIn("every_session_shown_or_counted_hidden", failing(rules.plan_rules(plan, (1280, 640), [], "TOP",
                                                                                        order)))
        plan, order = strip(first=2, shown=5)
        plan.tiles[2].rect = plan.tiles[1].rect
        self.assertIn("tiles_never_overlap", failing(rules.plan_rules(plan, (1280, 640), [], "TOP", order)))

    def test_the_place_orientation_and_centring_are_the_positions(self):
        plan, order = strip(top=False)
        self.assertIn("panel_stands_at_the_top", failing(rules.plan_rules(plan, (1280, 640), [], "TOP", order)))
        self.assertEqual(failing(rules.plan_rules(plan, (1280, 640), [], "BOTTOM", order)), [])
        self.assertIn("orientation_is_vertical", failing(rules.plan_rules(plan, (1280, 640), [], "LEFT", order)))
        plan.panel = (plan.panel[0] + 100, *plan.panel[1:])
        for t in plan.tiles:
            t.rect = (t.rect[0] + 100, *t.rect[1:])
        self.assertIn("panel_is_centred", failing(rules.plan_rules(plan, (1280, 640), [], "BOTTOM", order)))

    def test_centred_in_the_room_a_toolbar_leaves_counts(self):
        plan, order = strip(shown=4)
        shift = 30
        plan.panel = (plan.panel[0] + shift, *plan.panel[1:])
        toolbar = ('tools', (0, 0, 2 * shift, 600))
        self.assertNotIn("panel_is_centred", failing(rules.plan_rules(plan, (1280, 640), [toolbar], "TOP", order)))

    def test_more_lines_only_where_tiles_are_hidden_and_the_mark_on_the_approvals_side(self):
        plan, order = strip(first=0, shown=5)
        self.assertEqual(failing(rules.plan_rules(plan, (1280, 640), [], "TOP", order)), [])
        plan.more = (plan.more[1], plan.more[1])   # A "+N more" before, though nothing is hidden there.
        self.assertIn("more_before_shown_only_when_hidden", failing(rules.plan_rules(plan, (1280, 640), [], "TOP",
                                                                                     order)))
        plan, order = strip(first=0, shown=5, more=False)
        self.assertIn("more_lines_exist", failing(rules.plan_rules(plan, (1280, 640), [], "TOP", order)))
        plan, order = strip(first=4, shown=5)
        found = rules.plan_rules(plan, (1280, 640), [], "TOP", order, waiting=("w2",))   # Hidden before.
        self.assertIn("a_hidden_approval_is_marked_on_its_side", failing(found))
        plan.attention = (True, False)
        self.assertEqual(failing(rules.plan_rules(plan, (1280, 640), [], "TOP", order, waiting=("w2",))), [])

    def test_hidden_sides_count_main_when_it_is_not_pinned(self):
        order = ["main", "a", "b", "c"]
        self.assertEqual(rules.hidden_sides(order, ["main", "b"]), (["a"], ["c"]))
        self.assertEqual(rules.hidden_sides(order, ["b"]), (["main", "a"], ["c"]))


class CircleTests(unittest.TestCase):
    def test_a_cramped_circle_never_hides_under_native_regions(self):
        obstacles = [('tools', (0, 0, 132, 346)), ('ui', (171, 0, 537, 346)),
                     ('header', (0, 346, 708, 156)), ('navigation_gizmo', (-101, -304.4, 245, 620.4))]
        # The old fallback is wholly behind the sidebar, even though the whole region contains it.
        self.assertIn('circle_clears_ui', failing(rules.circle_rules((237, 241, 57), (708, 502),
                                                                   obstacles, 3, 76, exact=False)))
        # The 39 px visible strip permits a smaller disc; native regions still cannot cover it.
        self.assertEqual(failing(rules.circle_rules((151.5, 326.5, 19.5), (708, 502),
                                                    obstacles, 3, 76, exact=False)), [])

    def test_the_button_at_the_top_right_clear_of_the_gizmo_passes_at_any_scale(self):
        for scale, width, height in ((1.0, 1280, 640), (2.0, 2560, 1280), (3.0, 1280, 500)):
            radius = 38 * scale
            gizmo = (width - 120 * scale, height - 260 * scale, 120 * scale, 260 * scale)
            circle = (width - 140 * scale - radius, height - 16 * scale - radius, radius)
            found = rules.circle_rules(circle, (width, height), [('navigation_gizmo', gizmo)], scale, 76)
            self.assertEqual(failing(found), [], scale)

    def test_over_the_gizmo_off_the_view_or_too_large_fails(self):
        gizmo = ('navigation_gizmo', (1160, 380, 120, 260))
        self.assertIn("circle_clears_navigation_gizmo", failing(rules.circle_rules((1200, 600, 38), (1280, 640),
                                                                                   [gizmo], 1.0, 76)))
        self.assertIn("circle_inside_the_view", failing(rules.circle_rules((1260, 620, 38), (1280, 640), [], 1.0, 76)))
        self.assertIn("circle_at_the_top_right", failing(rules.circle_rules((60, 60, 38), (1280, 640), [], 1.0, 76)))
        self.assertIn("circle_is_the_angelo_buttons_size", failing(rules.circle_rules((1000, 500, 20), (1280, 640),
                                                                                      [], 1.0, 76)))
        small = rules.circle_rules((1000, 500, 20), (1280, 640), [], 1.0, 76, exact=False)
        self.assertEqual(failing(small), [])
        self.assertIn("circle_is_never_larger_than_the_angelo_button",
                      failing(rules.circle_rules((1000, 450, 80), (1280, 640), [], 1.0, 76, exact=False)))

    def test_the_hit_points_give_the_disc(self):
        points = [(x, y) for x in range(-10, 11) for y in range(-10, 11) if x * x + y * y <= 100]
        self.assertEqual(rules.circle_from_hits([(px + 50, py + 40) for px, py in points]), (50, 40, 10))
        self.assertIsNone(rules.circle_from_hits([]))


if __name__ == "__main__":
    unittest.main()
