# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Sessions panel's own glyphs (rail_glyph): the minimized panel's robot, the minimize control and the paged
chevrons are drawn only with chat_view's primitives in the colours given, stay inside their squares at any size and
centre, and mirror exactly; the count badge and the approval dot sit inside the minimized circle's square, apart."""

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import card_kit as kit  # noqa: E402
from angelo_agent import rail_glyph as glyph  # noqa: E402

INK, HOLE = (0.95, 0.96, 0.97, 1), (0.09, 0.098, 0.118, 1)


class FakeView(types.SimpleNamespace):
    """chat_view's drawing surface, recording rounded shapes and strokes."""

    def __init__(self):
        super().__init__(shapes=[], strokes=[])

    def shape(self, x, y, w, h, color, radius=0):
        self.shapes.append((x, y, w, h, color, radius))

    def stroke(self, x1, y1, x2, y2, color=None, thickness=1.2):
        self.strokes.append((x1, y1, x2, y2, color, thickness))


def drawn(call, *args, **kwargs):
    view = FakeView()
    with patch.object(glyph, "_view", lambda: view):
        call(*args, **kwargs)
    return view


class RobotTests(unittest.TestCase):
    def test_only_primitives_in_the_given_colours_inside_its_square(self):
        for size in (20, 24, 28, 32, 40, 48, 64):
            for cx, cy in ((50.0, 50.0), (50.3, 49.6), (120.5, 80.5)):
                with self.subTest(size=size, centre=(cx, cy)):
                    view = drawn(glyph.robot, cx, cy, size, INK, eyes=HOLE)
                    self.assertFalse(view.strokes)
                    self.assertGreaterEqual(len(view.shapes), 6, "head, ears, antenna, eyes, mouth")
                    self.assertLessEqual({shape[4] for shape in view.shapes}, {INK, HOLE})
                    half = size / 2 + 1   # One pixel for the snapping to whole pixels.
                    for x, y, w, h, _color, radius in view.shapes:
                        self.assertGreaterEqual(x, cx - half)
                        self.assertLessEqual(x + w, cx + half)
                        self.assertGreaterEqual(y, cy - half)
                        self.assertLessEqual(y + h, cy + half)
                        self.assertLessEqual(radius, max(w, h) / 2 + 1e-6)

    def test_the_whole_shape_mirrors_exactly_and_stands_28_of_32_units_tall(self):
        """Every part has its mirror image about the snapped centre (head, ears, antenna, eyes, mouth), and the robot
        keeps a unit of its 32-unit square clear above and below for the pixel snapping."""
        for size in (21, 32, 47, 64, 96):
            for cx in (50.0, 50.4, 50.6):
                view = drawn(glyph.robot, cx, 60.0, size, INK, eyes=HOLE)
                centre = int(cx + 0.5)
                shapes = {(round(x, 6), y, w, h, color, radius) for x, y, w, h, color, radius in view.shapes}
                for x, y, w, h, color, radius in shapes:
                    self.assertIn((round(2 * centre - x - w, 6), y, w, h, color, radius), shapes, (size, cx))
                top = max(y + h for _x, y, _w, h, _c, _r in view.shapes)
                bottom = min(y for _x, y, _w, _h, _c, _r in view.shapes)
                self.assertLessEqual(abs((top - bottom) - 28 * size / 32), 2, (size, top - bottom))

    def test_its_eyes_and_ears_mirror_exactly_about_the_snapped_centre(self):
        for size in (21, 32, 47, 64):
            for cx in (50.0, 50.4, 50.6):
                view = drawn(glyph.robot, cx, 60.0, size, INK, eyes=HOLE)
                eyes = [shape for shape in view.shapes if shape[4] == HOLE and shape[2] == shape[3]]
                self.assertEqual(len(eyes), 2)
                centre = int(cx + 0.5)
                left, right = sorted(eye[0] + eye[2] / 2 for eye in eyes)
                self.assertAlmostEqual(centre - left, right - centre, places=6)

    def test_eyes_default_to_the_card_fill_and_it_grows_with_size(self):
        self.assertIn(kit.CARD, {shape[4] for shape in drawn(glyph.robot, 50, 50, 32, INK).shapes})
        small = drawn(glyph.robot, 50, 50, 24, INK).shapes
        large = drawn(glyph.robot, 50, 50, 48, INK).shapes
        self.assertGreater(max(s[2] for s in large), max(s[2] for s in small))

    def test_it_fills_the_same_share_of_the_circle_at_every_scale(self):
        self.assertEqual(glyph.robot_size(38), 32)    # The 76 px circle at UI scale 1.
        self.assertEqual(glyph.robot_size(76), 64)    # ...and at 2.


class ControlGlyphTests(unittest.TestCase):
    def test_minimize_is_strokes_inside_its_square(self):
        for size in (10, 15, 20):
            view = drawn(glyph.minimize, 40, 40, size, INK)
            self.assertFalse(view.shapes)
            self.assertEqual(len(view.strokes), 3, "the corner's two arms and the shaft")
            for x1, y1, x2, y2, color, thickness in view.strokes:
                self.assertEqual(color, INK)
                for value in (x1, x2, y1, y2):
                    self.assertLessEqual(abs(value - 40), size / 2 + thickness)

    def test_the_chevron_refuses_a_direction_it_does_not_know(self):
        for direction in ("up", "", None, "NORTH"):
            with self.assertRaises(ValueError):
                drawn(glyph.chevron, 30, 30, 8, INK, direction)

    def test_the_chevron_points_where_it_is_asked(self):
        for direction, axis, sign in (("RIGHT", 0, 1), ("LEFT", 0, -1), ("UP", 1, 1), ("DOWN", 1, -1)):
            view = drawn(glyph.chevron, 30, 30, 8, INK, direction)
            self.assertEqual(len(view.strokes), 2)
            tip = view.strokes[0][2 + axis]   # Both arms meet at the tip: the first arm's end.
            self.assertGreater((tip - 30) * sign, 0, direction)


class CircleMarkTests(unittest.TestCase):
    def test_badge_and_dot_stay_inside_the_circles_square_apart_with_their_rings(self):
        for scale in (1, 1.5, 2, 3):
            radius = 38 * scale
            ring = glyph.BADGE_RING * scale
            for digits in (1, 2, 3):
                for digit_width in (6, 7, 9):   # Narrow to wide UI fonts.
                    with self.subTest(scale=scale, digits=digits, digit_width=digit_width):
                        badge = glyph.badge_rect(100, 100, radius, digit_width * digits * scale, scale)
                        self._apart(radius, ring, badge, glyph.approval_dot(100, 100, radius, scale))

    def _apart(self, radius, ring, badge, dot):
        bx, by, bw, bh = badge
        ax, ay, d = dot
        for x, y, w, h in ((bx - ring, by - ring, bw + 2 * ring, bh + 2 * ring),
                           (ax - ring, ay - ring, d + 2 * ring, d + 2 * ring)):
            self.assertGreaterEqual(x, 100 - radius - 1e-6)
            self.assertLessEqual(x + w, 100 + radius + 1e-6)
            self.assertGreaterEqual(y, 100 - radius - 1e-6)
            self.assertLessEqual(y + h, 100 + radius + 1e-6)
        self.assertTrue(ay >= by + bh or ay + d <= by, "the dot never sits on the count")
        self.assertGreaterEqual(bw, bh, "a pill at least as wide as it is tall")


if __name__ == "__main__":
    unittest.main()
