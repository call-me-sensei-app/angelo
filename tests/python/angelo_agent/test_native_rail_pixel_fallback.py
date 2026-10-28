# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""A subpixel rail disc is drawn only when an uncovered integer mouse pixel can reach it."""

import math
import random
import unittest
import ast
import contextlib
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import test_native_rail_minimized as fixture
import rail_positions_checks as geometry
import angelo_agent
from angelo_agent import rail_minimized as rail, round_button


def integer_hits(circle, box):
    cx, cy, radius = circle
    left, bottom, right, top = box
    return [(x, y) for x in range(math.ceil(left), math.ceil(right))
            for y in range(math.ceil(bottom), math.ceil(top))
            if round_button.inside(cx, cy, radius, x, y)] if radius > 0 else []


def feasible(radius, box):
    """Independent oracle: project each native-uncovered integer point onto the allowed centre rectangle."""
    if radius <= 0:
        return False
    left, bottom, right, top = box
    if right - left < 2 * radius or top - bottom < 2 * radius:
        return False
    for x in range(math.ceil(left), math.ceil(right)):
        for y in range(math.ceil(bottom), math.ceil(top)):
            cx = min(max(x, left + radius), right - radius)
            cy = min(max(y, bottom + radius), top - radius)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                return True
    return False


class PixelFallbackTests(unittest.TestCase):
    def test_exact_windows_short_view_has_a_real_integer_hotspot(self):
        circle = rail.place((708, 272), dict(left=169, right=538, top=156, bottom=72), 3,
                            (-100.0, -534.4, 245.0, 620.4))
        self.assertTrue(integer_hits(circle, (169, 72, 170, 116)), circle)
        self.assertEqual(circle[2], 0.5)
        for ox, oy in ((0, 0), (6, 385), (317, 81)):
            shifted = (circle[0] + ox, circle[1] + oy, circle[2])
            self.assertTrue(integer_hits(shifted, (169 + ox, 72 + oy, 170 + ox, 116 + oy)))

    def test_zero_width_and_one_pixel_square_have_no_false_control(self):
        for width, height, right in ((708, 272, 539), (708, 229, 538)):
            circle = rail.place((width, height), dict(left=169, right=right, top=156, bottom=72), 3, None)
            self.assertEqual(circle[2], 0, circle)

    def test_pixel_alignment_keeps_a_clear_corner_clear(self):
        block = (169, 100, 1, 16)
        circle = rail.place((708, 272), dict(left=169, right=538, top=156, bottom=72), 3, None, [block])
        self.assertTrue(integer_hits(circle, (169, 72, 170, 116)), circle)
        self.assertLessEqual(circle[1] + circle[2], 100)

    def test_narrow_boxes_draw_a_contained_reachable_disc_exactly_when_possible(self):
        randomizer = random.Random(106)
        for _ in range(500):
            left, bottom = randomizer.randrange(5, 80), randomizer.randrange(5, 80)
            gap_w, gap_h = randomizer.randrange(0, 9), randomizer.randrange(0, 13)
            right, top = left + gap_w, bottom + gap_h
            size = right + 50, top + 50
            circle = rail.place(size, dict(left=left, right=50, bottom=bottom, top=50), 3, None)
            expected_radius = min(gap_w, gap_h) / 2
            with self.subTest(box=(left, bottom, right, top), circle=circle):
                self.assertEqual(circle[2] > 0, feasible(expected_radius, (left, bottom, right, top)))
                if circle[2] <= 0:
                    continue
                cx, cy, radius = circle
                self.assertTrue(left <= cx - radius and cx + radius <= right)
                self.assertTrue(bottom <= cy - radius and cy + radius <= top)
                self.assertTrue(integer_hits(circle, (left, bottom, right, top)))


class GuiContractTests(unittest.TestCase):
    def load(self, name, scope, mutate_restore=False):
        path = Path(__file__).with_name('test_blender_rail_positions.py')
        tree = ast.parse(path.read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        if mutate_restore:
            calls = sorted((n for n in ast.walk(node) if isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute) and n.func.attr == 'screen_full_area'),
                           key=lambda n: n.lineno)
            calls[-1].keywords[0].value = ast.Constant(value=False)
            ast.fix_missing_locations(node)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        return scope[name]

    def test_zero_space_and_one_pixel_corner_are_not_reported_as_reachable(self):
        possible = self.load('integer_disc_room', {'math': math})
        for box in ((0, 0, 0, 44), (169, 72, 170, 73), (0.2, 0.2, 0.8, 0.8)):
            self.assertFalse(possible(box), box)
        for box in ((169, 72, 170, 116), (0, 0, 2, 1), (0, 0, 1, 2), (0.8, 0.8, 1.2, 1.2)):
            self.assertTrue(possible(box), box)

    def test_gui_integer_assertion_rejects_float_only_disc(self):
        window = NS(as_pointer=lambda: 7)
        region = NS(x=6, y=385, width=708, height=272)
        area = NS(regions=[])
        for cy, expected in ((500.5, False), (500.0, True)):
            checks = []
            scope = dict(math=math, geometry=geometry, S=NS(window=window),
                         fx=NS(window_region=lambda window: (area, region)),
                         state={'subpixel_recovery_checked': True}, stages=NS(cramped=lambda tag: iter(())),
                         check=lambda name, passed, detail=None: checks.append(bool(passed)))
            scope['integer_disc_room'] = self.load('integer_disc_room', scope)
            rail.release()
            with patch.object(geometry, 'view_obstacles', return_value=[]), \
                    patch.object(geometry, 'clear_box', return_value=(169, 72, 170, 116)):
                rail._places[7] = (11, 175.5, cy, 0.5)
                rail.cramp(7, True)
                list(self.load('cramped', scope)('fixture'))
            self.assertEqual(checks, [expected], cy)
        rail.release()

    def probe(self, *, failure=None, initial='NORMAL', mutate_restore=False):
        events, calls, checks = [], [], {}
        area = NS(as_pointer=lambda: 11, type='VIEW_3D', x=0, y=0, width=708, height=272)
        normal = NS(show_fullscreen=False, as_pointer=lambda: 20, areas=[area])
        full = NS(show_fullscreen=True, as_pointer=lambda: 21, areas=[area])
        mode = [initial]
        window = NS(as_pointer=lambda: 7, screen=normal if initial == 'NORMAL' else full)
        def event_simulate(**event):
            events.append(event)
            if event['type'] == 'INBETWEEN_MOUSEMOVE':
                rail._hover.add(7)
            elif event['value'] == 'PRESS':
                rail._pressed.add(7)
            else:
                rail._pressed.discard(7)
        window.event_simulate = event_simulate
        def full_area(*, use_hide_panels=False):
            # E screen_ops.cc: True only NORMAL/FULL; False only NORMAL/MAXIMIZED.
            destination = 'FULL' if use_hide_panels else 'MAXIMIZED'
            if mode[0] not in ('NORMAL', destination):
                calls.append((use_hide_panels, 'CANCELLED', mode[0]))
                return {'CANCELLED'}
            entering = mode[0] == 'NORMAL'
            if failure == ('cancel-entry' if entering else 'cancel-exit'):
                calls.append((use_hide_panels, 'CANCELLED', mode[0]))
                return {'CANCELLED'}
            mode[0] = destination if entering else 'NORMAL'
            window.screen = full if entering else normal
            calls.append((use_hide_panels, 'FINISHED', mode[0]))
            rail._hover.clear(); rail._pressed.clear(); rail.cramp(7, not entering)
            if entering and failure == 'expand':
                raise RuntimeError('expand failed after changing layout')
            if not entering and failure == 'wrong-layout':
                area.width += 1
            return {'FINISHED'}
        def shot(*args):
            if failure == 'capture':
                raise RuntimeError('capture failed')
        scope = dict(sys=sys, traceback=traceback, S=NS(window=window),
                     bpy=NS(context=NS(temp_override=lambda **kwargs: contextlib.nullcontext()),
                            ops=NS(screen=NS(screen_full_area=full_area))),
                     fx=NS(window_region=lambda window: (area, None), redraw=lambda window: None),
                     plan=lambda: NS(tiles=['lead']), run=NS(shot=shot),
                     check=lambda name, passed, *details: checks.update({name: bool(passed)}),
                     until=lambda predicate, *args: predicate, pause=lambda seconds: lambda: True)
        rail.release(); rail.cramp(7, True)
        sessions = NS(_active=lambda: 'lead')
        error = None
        with patch.dict(sys.modules, {'angelo_agent.parallel_sessions': sessions}), \
                patch.object(angelo_agent, 'parallel_sessions', sessions, create=True):
            scenario = self.load('pixel_recovery', scope, mutate_restore)('fixture', (175, 500))
            value = None
            try:
                for _ in range(20):
                    value = scenario.send(value)()
                self.fail('probe generator did not finish')
            except StopIteration:
                pass
            except RuntimeError as caught:
                error = str(caught)
        rail.release()
        return NS(error=error, calls=calls, events=events, checks=checks, mode=mode[0])

    def test_real_native_mode_rules_restore_normal_and_exact_layout(self):
        result = self.probe()
        self.assertIsNone(result.error)
        self.assertEqual(result.calls, [(True, 'FINISHED', 'FULL'), (True, 'FINISHED', 'NORMAL')])
        self.assertTrue(all(result.checks.values()), result.checks)
        self.assertEqual(result.mode, 'NORMAL')

    def test_original_false_restore_mutation_is_rejected(self):
        result = self.probe(mutate_restore=True)
        self.assertIn('cleanup failed', result.error)
        self.assertEqual(result.calls[-1], (False, 'CANCELLED', 'FULL'))
        self.assertEqual(result.mode, 'FULL')

    def test_native_probe_failure_releases_press_and_restores_fullscreen(self):
        for failure in ('expand', 'capture'):
            result = self.probe(failure=failure)
            self.assertIn(failure + ' failed', result.error)
            self.assertEqual(result.mode, 'NORMAL')
            self.assertEqual(sum(e['value'] == 'RELEASE' for e in result.events), 1)

    def test_cancelled_operator_and_wrong_layout_cannot_continue(self):
        for failure, reason in (('cancel-entry', 'enter Focus Mode'), ('cancel-exit', 'cleanup failed'),
                                ('wrong-layout', 'original area layout')):
            with self.subTest(failure=failure):
                result = self.probe(failure=failure)
                self.assertIn(reason, result.error)
                self.assertNotIn('fixture__the_original_cramped_layout_returns', result.checks)
                self.assertEqual(sum(e['value'] == 'RELEASE' for e in result.events), 1)

    def test_initial_full_or_maximized_is_rejected_before_input(self):
        for mode in ('FULL', 'MAXIMIZED'):
            result = self.probe(initial=mode)
            self.assertIn('requires a normal screen', result.error)
            self.assertEqual(result.calls, [])
            self.assertEqual(result.events, [])


if __name__ == '__main__':
    unittest.main()
