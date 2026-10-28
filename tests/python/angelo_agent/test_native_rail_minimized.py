# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel minimized to a round button (``rail_minimized``), without Blender.

Evidence: the maintainer (2026-10-25) asked for the minimized panel as "a circle like how Angelo button is when chat
window is hidden", at the top right of the 3D view, with a robot and the workers' count. Covered here: Blender 5.2's
navigation gizmo as ``view3d_gizmo_navigate.cc`` places it (UI scales 1 and 2, hidden, the minimal axes, quad view,
region overlap), where the circle stands (never on the gizmo, the headers, the toolbar or the sidebar, always inside
the region) across window sizes, scales, sidebar, header overlap, a bottom shelf, the chat's side and the Director
layout, the hit test and input protocol, the tooltip, and a draw in card_kit colours only on the Angelo button's own
disc (``round_button``, shared with corner Angelo, never copied).
"""

import itertools
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

import angelo_agent  # noqa: E402
from angelo_agent import card_kit as kit, rail_glyph, rail_minimized as rm, round_button, session_rail  # noqa
from angelo_agent import ui_tokens as tk  # noqa: E402


def load_launcher():
    """``chat_launcher`` with a stand-in ``bpy`` (as ``test_native_chat_launcher`` loads it): its ``insets``."""
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(handlers=handlers)
    with mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app,
                                       "bpy.app.handlers": handlers}):
        sys.modules.pop("angelo_agent.chat_launcher", None)
        import angelo_agent.chat_launcher as module
    return module


launcher = load_launcher()
VIEW = dict(show_navigate_ui=True, mini_axis_type='GIZMO', mini_axis_size=25, gizmo_size_navigate_v3d=80)
TOKENS = {value for module in (kit, tk) for name, value in vars(module).items()
          if name.isupper() and isinstance(value, tuple) and len(value) == 4}


def region(kind, x, y, width, height, alignment="NONE"):
    return SimpleNamespace(type=kind, x=x, y=y, width=width, height=height, alignment=alignment,
                           as_pointer=lambda: id((kind, x, y)))


def view(**changes):
    return SimpleNamespace(**{**VIEW, **changes})


def area_regions(x, y, width, height, s, sidebar=True, header_over=True, shelf=0, chrome=True):
    """A 3D view's regions as Blender lays them out with Region Overlap: the tool header, toolbar, sidebar and a
    bottom shelf drawn over the main region, the header over it too when the theme gives it transparency
    (``header_over``), else beside it. ``chrome`` False: Director hides all of them (hidden regions are 1 px)."""
    head = round(26 * s)
    top, regions = y + height, []
    if not chrome:
        regions = [region('HEADER', x, top - 1, width, 1, 'TOP'), region('TOOLS', x, y, 1, height, 'LEFT'),
                   region('UI', x + width - 1, y, 1, height, 'RIGHT')]
        return regions + [region('WINDOW', x, y, width, height)]
    main_top = top if header_over else top - head
    regions.append(region('HEADER', x, top - head, width, head, 'TOP'))
    regions.append(region('TOOL_HEADER', x, top - 2 * head, width, head, 'TOP'))
    low = y
    if shelf:
        regions.append(region('ASSET_SHELF', x, y, width, round(shelf * s), 'BOTTOM'))
        low = y + round(shelf * s)
    side_h = top - 2 * head - low
    regions.append(region('TOOLS', x, low, round(44 * s), side_h, 'LEFT'))
    regions.append(region('UI', x + width - (round(240 * s) if sidebar else 1), low,
                          round(240 * s) if sidebar else 1, side_h, 'RIGHT'))
    return regions + [region('WINDOW', x, y, width, main_top - y)]


def fake_bpy(s, pixel, prefs):
    module = types.ModuleType("bpy")
    module.context = SimpleNamespace(preferences=SimpleNamespace(
        system=SimpleNamespace(ui_scale=s, pixel_size=pixel), view=prefs))
    return module


def area(regions, gizmo=True):
    return SimpleNamespace(regions=regions, spaces=SimpleNamespace(active=SimpleNamespace(
        show_gizmo=gizmo, show_gizmo_navigate=True)))


def spot(regions, s, gizmo=True, prefs=None, avoid=()):
    """``(circle, gizmo rect, insets, main region)`` through the module's own host path (``spot``)."""
    main = regions[-1]
    host = area(regions, gizmo)
    with mock.patch.dict(sys.modules, {"bpy": fake_bpy(s, 1.0 if s < 2 else 2.0, prefs or view()),
                                       "angelo_agent.chat_launcher": launcher}), \
            mock.patch.object(angelo_agent, "chat_launcher", launcher, create=True):
        return rm.spot(host, main, s, avoid), rm.navigation_rect(host, main), rm.insets(regions, main), main


def overlaps(a, b, margin=0.0):
    return (a[0] < b[0] + b[2] + margin and b[0] < a[0] + a[2] + margin and a[1] < b[1] + b[3] + margin
            and b[1] < a[1] + a[3] + margin)


def square(circle):
    cx, cy, r = circle
    return (cx - r, cy - r, 2 * r, 2 * r)


class GizmoGeometryTests(unittest.TestCase):
    """Expected numbers worked from view3d_gizmo_navigate.cc by hand (comments), never from the module."""

    VISIBLE = (0, 0, 999, 699)   # A 1000 x 700 region, nothing drawn over it.

    def test_axes_and_column_at_scale_one(self):
        # Axes: icon_offset = 1 * (10 + 80 / 2) = 50, centre (949, 649), radius 40 -> x 909..989, y 609..689.
        # Buttons: icon_offset_mini = 30, x = roundf(999 - 22.5) = 977, top = roundf(699 - 50 * 2.2) = 589, four slots
        # down to 499, bounds 977 +- 14 -> x 963..991, y 485..603, backdrop pad (28 / 2) * 0.2 = 2.8 above and below
        # (its outline is inside) -> y 482.2..605.8. Union: x 909..991, y 482.2..689.
        rect = rm.gizmo_rect(self.VISIBLE, 1.0, view())
        for got, want in zip(rect, (909, 482.2, 82, 206.8)):
            self.assertAlmostEqual(got, want, places=6)

    def test_axes_and_column_at_scale_two(self):
        # Axes: offset 2 * 50 = 100, centre (899, 599), radius 80 -> x 819..979, y 519..679. Buttons: mini 60,
        # x = roundf(999 - 45) = 954, top = roundf(699 - 220) = 479, down to 299, radius 28, pad 5.6 -> x 926..982,
        # y 265.4..512.6. The pixel size changes nothing here: the backdrop's outline is drawn inside it.
        rect = rm.gizmo_rect(self.VISIBLE, 2.0, view(), pixel_size=2.0)
        for got, want in zip(rect, (819, 265.4, 163, 413.6)):
            self.assertAlmostEqual(got, want, places=6)

    def test_hidden(self):
        self.assertIsNone(rm.gizmo_rect(self.VISIBLE, 1.0, view(), shown=False), "Gizmos or Navigate off")
        self.assertIsNone(rm.gizmo_rect(self.VISIBLE, 1.0, view(show_navigate_ui=False, mini_axis_type='NONE')))

    def test_axes_without_the_buttons(self):
        self.assertEqual(rm.gizmo_rect(self.VISIBLE, 1.0, view(show_navigate_ui=False)), (909, 609, 80, 80))

    def test_the_navigate_size_preference(self):
        # gizmo_size_navigate_v3d 120: offset 70, centre (929, 629), radius 60 -> x 869..989.
        self.assertEqual(rm.gizmo_rect(self.VISIBLE, 1.0, view(gizmo_size_navigate_v3d=120))[0], 869)

    def test_buttons_only_without_axes(self):
        # mini_axis NONE: icon_offset_from_axis = 30 * 0.75 = 22.5, top = roundf(676.5) = 677 (half away from zero).
        rect = rm.gizmo_rect(self.VISIBLE, 1.0, view(mini_axis_type='NONE'))
        self.assertAlmostEqual(rect[1] + rect[3], 677 + 14 + 2.8, places=6)
        self.assertAlmostEqual(rect[0], 977 - 14, places=6)

    def test_a_fractional_scale_truncates_the_button_bounds_like_blenders_rcti(self):
        # s 1.25: mini 37.5, x = roundf(999 - 28.125) = 971, top = roundf(699 - 28.125) = 671, low = roundf(671 -
        # 112.5) = 559 (half away from zero); radius 17.5 into an int rcti: x 953..988, y 541..688; pad 35 / 2 * 0.2.
        rect = rm.gizmo_rect(self.VISIBLE, 1.25, view(mini_axis_type='NONE'))
        for got, want in zip(rect, (953, 537.5, 35, 154)):
            self.assertAlmostEqual(got, want, places=6)

    def test_minimal_axes(self):
        # draw_view_axis: unit = roundf(18) + 2 = 20, k = 25, start (999 - 55, 699 - 45) = (944, 654): x from 919.
        # Buttons below at 2 * 20 + 25 * 1.6 = 80: top = 619.
        rect = rm.gizmo_rect(self.VISIBLE, 1.0, view(mini_axis_type='MINIMAL'))
        self.assertAlmostEqual(rect[0], 919, places=6)
        self.assertGreaterEqual(rect[1] + rect[3], 654 + 25)
        alone = rm.gizmo_rect(self.VISIBLE, 1.0, view(mini_axis_type='MINIMAL', show_navigate_ui=False))
        self.assertAlmostEqual(alone[0], 919, places=6, msg="drawn by view3d_draw, whatever the gizmo group's poll")

    def test_quad_view_draws_the_axes_top_right_only(self):
        other = rm.gizmo_rect(self.VISIBLE, 1.0, view(), top_right=False)
        # No axes: icon_offset = 10, buttons start 10 * 2.2 = 22 below the top: roundf(677) = 677.
        self.assertAlmostEqual(other[1] + other[3], 677 + 14 + 2.8, places=6)
        self.assertGreater(other[0], 909)

    def test_locked_or_camera_columns(self):
        self.assertEqual([rm.column_buttons(*flags) for flags in ((False, False), (False, True), (True, False),
                                                                  (True, True))], [4, 4, 2, 3])
        short = rm.gizmo_rect(self.VISIBLE, 1.0, view(), buttons=2)
        self.assertAlmostEqual(short[1], 589 - 30 - 14 - 2.8, places=6)

    def test_visible_rect_is_blenders(self):
        main = region('WINDOW', 100, 50, 1000, 700)
        regions = [region('HEADER', 100, 724, 1000, 26, 'TOP'),        # Drawn over the view (a transparent theme).
                   region('TOOLS', 100, 50, 44, 674, 'LEFT'),
                   region('UI', 840, 50, 260, 674, 'RIGHT'),
                   region('HEADER', 100, 750, 1000, 26, 'TOP'),        # Beside the view: no region overlap.
                   region('UI', 1099, 50, 1, 700, 'RIGHT'),            # Hidden.
                   region('HUD', 300, 60, 200, 100, 'FLOAT'), main]
        # winrct edges are inclusive: xmin = 143 (the toolbar's last column), xmax = 840 (the sidebar's first), ymax =
        # 724 (the header's first row); then local to the region.
        self.assertEqual(rm.visible_rect(regions, main), (43, 0, 740, 674))
        self.assertEqual(rm.visible_rect([main], main), (0, 0, 999, 699))

    def test_the_corner_region_in_quad_view(self):
        quads = [region('WINDOW', 0, 0, 500, 400, 'QUAD_SPLIT'), region('WINDOW', 500, 0, 500, 400, 'QUAD_SPLIT'),
                 region('WINDOW', 0, 400, 500, 400, 'QUAD_SPLIT'), region('WINDOW', 500, 400, 500, 400, 'QUAD_SPLIT')]
        self.assertIs(rm.region_of(SimpleNamespace(regions=[region('HEADER', 0, 800, 1000, 26, 'TOP')] + quads)),
                      quads[3])


class PlaceTests(unittest.TestCase):
    """Every layout: the circle inside the region, inside the insets, clear of the gizmo whenever any spot is."""

    def test_cramped_button_never_goes_under_the_sidebar_or_toolbar(self):
        """Exact T4 readings and other narrow visible strips: the native regions cover and intercept the button."""
        for width, height, scale, left, right in ((712, 581, 2, 88, 561), (708, 502, 3, 132, 537),
                                                  (600, 420, 1, 44, 530), (900, 680, 2, 88, 740)):
            top, bottom = 52 * scale, 0
            gizmo = rm.gizmo_rect((left, bottom, width - right - 1, height - top - 1), scale, view())
            cx, cy, radius = rm.place((width, height), dict(left=left, right=right, top=top, bottom=bottom),
                                     scale, gizmo)
            with self.subTest(width=width, scale=scale):
                self.assertGreater(radius, 0)
                self.assertGreaterEqual(cx - radius, left)
                self.assertLessEqual(cx + radius, width - right)
                self.assertGreaterEqual(cy - radius, bottom)
                self.assertLessEqual(cy + radius, height - top)

    def test_no_visible_space_has_no_hittable_disc(self):
        cx, cy, radius = rm.place((300, 200), dict(left=100, right=200, top=50), 3, None)
        self.assertEqual(radius, 0)

    def check(self, label, regions, s, gizmo=True, prefs=None, avoid=()):
        circle, rect, insets, main = spot(regions, s, gizmo, prefs, avoid)
        cx, cy, r = circle
        w, h = main.width, main.height
        margin, full = rm.MARGIN * s, round_button.DISC * s / 2
        visible = max(0.0, min(w - insets['left'] - insets['right'], h - insets['top'] - insets['bottom']))
        least = min(full * rm.SMALLEST, visible / 2)
        self.assertTrue(least - 1e-6 <= r <= full + 1e-6, label + ": readable unless the visible strip is smaller")
        if r > 0:
            self.assertGreaterEqual(cx - r, insets['left'] - 1e-6, label)
            self.assertLessEqual(cx + r, w - insets['right'] + 1e-6, label)
            self.assertGreaterEqual(cy - r, insets['bottom'] - 1e-6, label)
            self.assertLessEqual(cy + r, h - insets['top'] + 1e-6, label)
        self.assertTrue(r - 1e-6 <= cx <= w - r + 1e-6 and r - 1e-6 <= cy <= h - r + 1e-6, label + ": in the region")
        box = (insets["left"] + margin, insets["bottom"] + margin, w - insets["right"] - margin,
               h - insets["top"] - margin)
        blocks = [b for b in (rect, *avoid) if b]
        if box[2] - box[0] >= 2 * full and box[3] - box[1] >= 2 * full and self.room(box, full, margin, blocks, s):
            self.assertEqual(r, full, label + ": the Angelo button's size wherever it fits clear")
        roomy = box[2] - box[0] >= 2 * r and box[3] - box[1] >= 2 * r and self.room(box, r, margin, blocks, s)
        if roomy:   # Its margins hold wherever a clear spot keeps them; else it touches an edge before covering.
            self.assertGreaterEqual(cx - r, box[0] - 1e-6, label + ": right of the toolbar")
            self.assertLessEqual(cx + r, box[2] + 1e-6, label + ": left of the sidebar")
            self.assertLessEqual(cy + r, box[3] + 1e-6, label + ": below the headers")
            self.assertGreaterEqual(cy - r, box[1] - 1e-6, label + ": above a bottom shelf")
        clear = not any(overlaps(square(circle), b, margin - 1e-6) for b in blocks)
        if roomy and self.room(box, r, margin, blocks, s):
            self.assertTrue(clear, label + ": clear of the gizmo %r at %r" % (rect, circle))
        return circle, rect, clear

    @staticmethod
    def room(box, r, margin, blocks, s):
        """Whether any spot in the box is clear (a brute-force oracle over a grid of every 2 px at scale 1)."""
        step = 2 * s
        xs = [box[0] + r + i * step for i in range(int((box[2] - box[0] - 2 * r) / step) + 1)]
        ys = [box[1] + r + i * step for i in range(int((box[3] - box[1] - 2 * r) / step) + 1)]
        return any(not any(overlaps((x - r, y - r, 2 * r, 2 * r), b, margin) for b in blocks) for x in xs for y in ys)

    def layouts(self):
        """(label, regions, scale, gizmo shown) for both window sizes and scales: the 3D view alone, beside a chat
        on its right or left (Manual), and Director's (chat on the right, every region hidden, Gizmos off)."""
        for (width, height), s in itertools.product(((1280, 720), (1920, 1080)), (1.0, 2.0)):
            bars = round(26 * s) * 2   # The top bar and the status bar, outside the view.
            for name, x, w in (("alone", 0, width), ("chat right", 0, round(width * 0.65)),
                               ("chat left", round(width * 0.35), width - round(width * 0.35))):
                for sidebar, header_over, shelf in itertools.product((True, False), (True, False), (0, 120)):
                    label = "%dx%d @%s %s sidebar=%s header_over=%s shelf=%s" % (
                        width, height, s, name, sidebar, header_over, shelf)
                    yield label, area_regions(x, 26, w, height - bars, s, sidebar, header_over, shelf), s, True
            yield ("%dx%d @%s Director" % (width, height, s),
                   area_regions(0, 0, round(width * 0.65), height, s, chrome=False), s, False)
            yield ("%dx%d @%s Director, gizmos on" % (width, height, s),
                   area_regions(0, 0, round(width * 0.65), height, s, chrome=False), s, True)

    def test_every_layout(self):
        count = 0
        for label, regions, s, gizmo in self.layouts():
            with self.subTest(label):
                circle, rect, clear = self.check(label, regions, s, gizmo)
                count += 1
        self.assertEqual(count, 2 * 2 * (3 * 8 + 2))

    def test_the_usual_views_put_it_left_of_the_gizmo_in_the_top_row(self):
        for (width, height), s in itertools.product(((1280, 720), (1920, 1080)), (1.0, 2.0)):
            regions = area_regions(0, 26, round(width * 0.65), height - 52 * s, s, sidebar=False)
            circle, rect, _insets, main = spot(regions, s)
            cx, cy, r = circle
            self.assertAlmostEqual(cx + r, rect[0] - rm.MARGIN * s, msg="%sx%s@%s" % (width, height, s))
            top = session_rail.top_inset(regions, main)
            self.assertAlmostEqual(cy + r, main.height - top - rm.MARGIN * s)

    def test_director_stands_in_the_top_right_corner(self):
        regions = area_regions(0, 0, 832, 720, 1.0, chrome=False)
        cx, cy, r = spot(regions, 1.0, gizmo=False)[0]
        self.assertEqual((cx + r, cy + r), (832 - rm.MARGIN, 720 - rm.MARGIN))

    def test_a_narrow_view_puts_it_below_the_gizmo(self):
        regions = area_regions(0, 26, 560, 1000, 2.0, sidebar=False)
        circle, rect, _insets, main = spot(regions, 2.0)
        cx, cy, r = circle
        self.assertAlmostEqual(cy + r, rect[1] - rm.MARGIN * 2)
        self.assertAlmostEqual(cx + r, main.width - rm.MARGIN * 2 - launcher.insets(regions, main)[1])

    def test_held_out_preferences_and_shapes(self):
        """Cases the rules were not developed on: other gizmo sizes, the minimal axes, buttons only, quad view's
        corner, a header flipped to the bottom, a tall narrow window."""
        cases = [("size 30", view(gizmo_size_navigate_v3d=30)), ("size 200", view(gizmo_size_navigate_v3d=200)),
                 ("minimal", view(mini_axis_type='MINIMAL')), ("none", view(mini_axis_type='NONE')),
                 ("axes only", view(show_navigate_ui=False))]
        for (label, prefs), s in itertools.product(cases, (1.0, 2.0)):
            with self.subTest(label, scale=s):
                self.check(label, area_regions(0, 26, 1100, 800, s), s, prefs=prefs)
        flipped = [region('HEADER', 0, 0, 900, 26, 'BOTTOM'), region('WINDOW', 0, 26, 900, 700)]
        cx, cy, r = self.check("header at the bottom", flipped, 1.0)[0]
        self.assertAlmostEqual(cy + r, 700 - rm.MARGIN, msg="nothing over its top")
        self.check("tall and narrow", area_regions(0, 26, 420, 1600, 2.0), 2.0)

    def test_a_live_views_label_is_kept_clear(self):
        regions = area_regions(0, 26, 832, 668, 1.0)
        main = regions[-1]
        top = session_rail.top_inset(regions, main)
        label = ((832 - 500) / 2, main.height - top - 12 - 26, 500, 26)   # live_view_draw's chip, centred.
        circle, _rect, clear = self.check("live label", regions, 1.0, avoid=(label,))
        self.assertFalse(overlaps(square(circle), label))
        self.assertGreater(circle[0], 832 / 2, "still at the right")

    def test_never_outside_a_tiny_view(self):
        for size in ((60, 50), (150, 150), (300, 90)):
            cx, cy, r = rm.place(size, {"left": 40, "right": 40, "top": 30}, 1.0, (0, 0, size[0], size[1]))
            self.assertTrue(r <= cx <= max(r, size[0] - r) and r <= cy <= max(r, size[1] - r), size)


class DrawTests(unittest.TestCase):
    def record(self, count, waiting, hover=False, pressed=False):
        calls = []
        fake = SimpleNamespace(shape=lambda *a: calls.append(("shape", a)),
                               text=lambda *a: calls.append(("text", a)),
                               measure=lambda value, size, scale=None: len(value) * size * 0.6)
        card = lambda *a: calls.append(("card", a))   # noqa: E731
        glyph = lambda *a, **k: calls.append(("glyph", a + (k.get("eyes"),)))   # noqa: E731
        rm.paint(fake, card, glyph, 500.0, 300.0, 38.0, 1.0, count, waiting, hover, pressed)
        return calls

    def test_only_card_kit_colours(self):
        for count, waiting, hover, pressed in itertools.product((0, 3, 16, 120), (False, True), (False, True),
                                                                (False, True)):
            for kind, arguments in self.record(count, waiting, hover, pressed):
                colors = [arguments[4]] if kind in {"shape", "text"} else \
                    [arguments[3], arguments[4]] if kind == "glyph" else []
                for color in colors:
                    self.assertIn(tuple(color), TOKENS, (kind, arguments))

    def test_the_card_the_robot_the_count_and_the_mark(self):
        calls = self.record(3, True)
        self.assertEqual(calls[0], ("card", (500.0, 300.0, 38.0, 1.0, False, False)), "the Angelo button's disc")
        self.assertEqual(calls[1], ("glyph", (500.0, 300.0, rail_glyph.robot_size(38.0), kit.TEXT, kit.CARD)),
                         "the designer's robot, its eyes cut out in the card's fill")
        self.assertEqual(self.record(0, False, hover=True)[1][1][4], kit.PANEL_HOVER, "the eyes follow the hover")
        digits = [arguments for kind, arguments in calls if kind == "text"][0]
        self.assertEqual(digits[3:], (tk.TYPE_SMALL, kit.CARD), "kit.CARD digits at tk.TYPE_SMALL")
        badge = rail_glyph.badge_rect(500.0, 300.0, 38.0, len("3") * tk.TYPE_SMALL * 0.6, 1.0)
        self.assertIn(badge + (kit.TEAL, badge[3] / 2), [arguments for kind, arguments in calls if kind == "shape"])
        x, y, d = rail_glyph.approval_dot(500.0, 300.0, 38.0, 1.0)
        self.assertIn((x, y, d, d, kit.AMBER, d / 2), [arguments for kind, arguments in calls if kind == "shape"])
        self.assertIn(("text", ("3",)), [(kind, arguments[:1]) for kind, arguments in calls])
        self.assertIn(kit.AMBER, [arguments[4] for kind, arguments in calls if kind == "shape"])
        self.assertNotIn(kit.AMBER, [arguments[4] for kind, arguments in self.record(3, False) if kind == "shape"])
        self.assertEqual([kind for kind, _ in self.record(0, False)], ["card", "glyph"], "no badge without workers")
        self.assertIn(("text", ("99+",)), [(kind, arguments[:1]) for kind, arguments in self.record(120, False)])

    def test_badges_stay_on_the_cards_square(self):
        for scale, count in itertools.product((1.0, 2.0), (1, 16, 120)):
            calls = []
            fake = SimpleNamespace(shape=lambda *a: calls.append(a), text=lambda *a: None,
                                   measure=lambda value, size, scale=None: len(value) * size * 0.6)
            r = round_button.DISC * scale / 2
            rm.paint(fake, lambda *a: None, lambda *a, **k: None, 500.0, 300.0, r, scale, count, True)
            ring = rail_glyph.BADGE_RING * scale   # A mark's ring may cross the square: the place's margin is wider.
            self.assertLess(ring, rm.MARGIN * scale)
            for x, y, w, h, *_rest in calls:
                self.assertTrue(500 - r - ring - 1e-6 <= x and x + w <= 500 + r + ring + 1e-6
                                and 300 - r - ring - 1e-6 <= y and y + h <= 300 + r + ring + 1e-6,
                                (scale, count, (x, y, w, h)))

    def test_the_shared_disc_is_the_angelo_buttons(self):
        self.assertEqual(round_button.DISC, 76.0)
        self.assertEqual(launcher.DISC, round_button.DISC)
        self.assertEqual(round_button.SHADOW, (0.0, 0.0, 0.0, 0.28))
        self.assertEqual(round_button.colors(False, False), (kit.CARD, kit.EDGE, 1.0))
        self.assertEqual(round_button.colors(True, False), (kit.PANEL_HOVER, kit.TEAL, 1.5))
        self.assertEqual(round_button.colors(True, True), (kit.PANEL_HOVER, kit.TEAL_PRESSED, 1.5))
        launcher_source = (ADDON / "chat_launcher.py").read_text(encoding="utf-8")
        self.assertIn("round_button.draw(cx, cy, radius, scale, hover, pressed)", launcher_source)
        self.assertNotIn("def _disc(", launcher_source, "one disc, never a copy")
        source = (ADDON / "rail_minimized.py").read_text(encoding="utf-8")
        self.assertIn("paint(chat_view, round_button.draw, rail_glyph.robot,", source)
        self.assertNotIn("batch_for_shader", source)

    def test_the_tooltip(self):
        self.assertEqual(rm.label(3, False), "Show the Sessions panel\n3 workers")
        self.assertEqual(rm.label(1, False), "Show the Sessions panel\n1 worker")
        self.assertEqual(rm.label(2, True).splitlines(),
                         ["Show the Sessions panel", "2 workers", "A worker waits for your approval"])

    def test_counts_are_the_workers(self):
        tiles = [session_rail.Tile("lead", "", "Main", "", session_rail.APPROVAL),
                 session_rail.Tile("w1", "", "Worker 1", "", session_rail.WORKING),
                 session_rail.Tile("q2", "", "Worker 2", "", session_rail.QUEUED)]
        self.assertEqual(rm.counts(tiles, "lead"), (2, False), "Main excluded; the lead's approval is in its chat")
        tiles[1].kind = session_rail.APPROVAL
        self.assertEqual(rm.counts(tiles, "lead"), (2, True))


class InputTests(unittest.TestCase):
    def setUp(self):
        rm.release()
        self.cursor = []
        self.window = SimpleNamespace(as_pointer=lambda: 7, screen=None,
                                      cursor_modal_set=lambda kind: self.cursor.append(kind),
                                      cursor_modal_restore=lambda: self.cursor.append("restore"))
        rm._places[7] = (11, 500.0, 300.0, 38.0)
        self.setting = SimpleNamespace(minimized=True)
        self.setting.rail_minimized = lambda: self.setting.minimized
        self.setting.set_rail_minimized = lambda value: setattr(self.setting, "minimized", bool(value))
        patches = [mock.patch.dict(sys.modules, {"angelo_agent.parallel_setting": self.setting}),
                   mock.patch.object(angelo_agent, "parallel_setting", self.setting, create=True)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(rm.release)

    def event(self, kind, value="NOTHING", x=500, y=300):
        return rm.handle(self.window, SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y))

    def test_hit_is_the_disc_while_minimized(self):
        self.assertTrue(rm.hit(7, 500, 300))
        self.assertTrue(rm.hit(7, 538, 300))
        self.assertFalse(rm.hit(7, 530, 330), "the square's corner is the scene's")
        self.assertFalse(rm.hit(8, 500, 300), "another window")
        self.setting.minimized = False
        self.assertFalse(rm.hit(7, 500, 300), "shown again: no circle")

    def test_hover_press_release_restores(self):
        self.assertIsNone(self.event('MOUSEMOVE'))
        self.assertIn(7, rm._hover)
        self.assertEqual(self.cursor, ['HAND'])
        self.assertEqual(self.event('LEFTMOUSE', 'PRESS'), {'RUNNING_MODAL'})
        self.assertIn(7, rm._pressed)
        self.assertTrue(self.setting.minimized, "a press alone changes nothing")
        self.assertEqual(self.event('LEFTMOUSE', 'RELEASE'), {'RUNNING_MODAL'})
        self.assertFalse(self.setting.minimized, "the panel shows again")
        self.assertNotIn(7, rm._places)
        self.assertEqual((rm._hover, rm._pressed), (set(), set()))
        self.assertEqual(self.cursor[-1], "restore")

    def test_a_release_off_the_circle_does_nothing(self):
        self.event('LEFTMOUSE', 'PRESS')
        self.assertEqual(self.event('LEFTMOUSE', 'RELEASE', 900, 900), {'RUNNING_MODAL'})
        self.assertTrue(self.setting.minimized)
        self.assertEqual(rm._pressed, set())

    def test_only_its_own_presses(self):
        self.assertIsNone(self.event('LEFTMOUSE', 'PRESS', 900, 900), "the view's")
        self.assertEqual(self.event('RIGHTMOUSE', 'PRESS'), {'RUNNING_MODAL'}, "no menu behind it")
        self.assertIsNone(self.event('WHEELUPMOUSE', 'PRESS'), "the view zooms under it, as under corner Angelo")
        self.assertIsNone(self.event('A', 'PRESS'))
        self.assertEqual(self.event('RET', 'PRESS'), {'RUNNING_MODAL'})
        self.assertFalse(self.setting.minimized)

    def test_moving_off_ends_the_hover(self):
        self.event('MOUSEMOVE')
        self.event('MOUSEMOVE', x=900, y=900)
        self.assertEqual(rm._hover, set())
        self.assertEqual(self.cursor, ['HAND', 'restore'])

    def test_nothing_taken_once_shown(self):
        self.setting.minimized = False
        self.assertIsNone(self.event('LEFTMOUSE', 'PRESS'))


class SourceTests(unittest.TestCase):
    def test_a_fully_covered_view_clears_its_old_hit_and_hover(self):
        window = SimpleNamespace(as_pointer=lambda: 41)
        stubs = {'gpu': SimpleNamespace(), 'angelo_agent.canvas_controls': SimpleNamespace(),
                 'angelo_agent.chat_view': SimpleNamespace()}
        rm._places[41] = (42, 50, 60, 38)
        rm._hover.add(41)
        rm._pressed.add(41)
        self.addCleanup(rm.release)
        with mock.patch.dict(sys.modules, stubs), mock.patch.object(rm, 'spot', return_value=(50, 60, 0)), \
                mock.patch.object(rm, '_cursor') as cursor, mock.patch.object(rm, '_redraw'), \
                mock.patch.object(rm, 'paint') as paint:
            rm.draw(window, None, None, 3, False, 3, cramped=True)
        self.assertNotIn(41, rm._places)
        self.assertNotIn(41, rm._hover)
        self.assertNotIn(41, rm._pressed)
        self.assertFalse(rm.hit(41, 50, 60))
        cursor.assert_called_once_with(window, False)
        paint.assert_not_called()

    def test_small_pure_and_on_its_own_text(self):
        source = (ADDON / "rail_minimized.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(source.splitlines()), 500)
        header = source.split("\ndef ", 1)[0]
        self.assertNotIn("import bpy", header, "pure at import: host-testable")
        for words in ("Show the Sessions panel", "{count} worker", "{count} workers", "A worker waits for your approval"):
            self.assertIn('"%s"' % words, source, "translated through iface_/plural_")
        self.assertNotIn(".icon(", source, "no stock Blender icons")


if __name__ == "__main__":
    unittest.main()
