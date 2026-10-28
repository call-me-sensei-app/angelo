# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Paging proof keeps exact offsets and bounded tail progress when a new more-row reduces capacity."""

import ast
import contextlib
import sys
import traceback
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

SOURCE = Path(__file__).with_name('test_blender_session_rail.py')
ORIGINAL = ['lead', 'real-room', 'real-lamp']


@contextlib.contextmanager
def world(height=605, scale=1.0, fail_bind=False, contracting=False, fault=None):
    view, system, window = NS(ui_scale=scale), NS(ui_scale=scale), object()
    sessions, state, clock, frames = list(ORIGINAL), {}, [0.0], []
    checks, events, sent, removed, shots = {}, [], [], [], []
    ui = NS(_sessions=NS(active='lead'), _controllers={'lead': NS(state={'id': 'lead'})})
    ui.show_session = lambda session: setattr(ui._sessions, 'active', session)
    paging_first, down, up = [0], [0], [0]
    region = NS(x=11, y=13, height=height, tag_redraw=lambda: None)

    def plan():
        if view.ui_scale != scale:
            return NS(panel=(0, 0, 0, 0), scale=scale, tiles=[], paged=False, first=0, hidden=(0, 0),
                      more=(None, None))
        paged = len(sessions) > len(ORIGINAL)
        first = paging_first[0] if paged else 0
        capacity = 3 if contracting and first else 4
        if (fault == 'incorrect_drop' and first == 1
                or fault == 'second_no_tail' and down[0] >= 2
                or fault == 'reverse_window' and up[0]):
            capacity = 2
        shown = [sessions[0]] + sessions[1 + first:1 + first + capacity] if paged else list(sessions)
        if first and fault == 'reordered':
            shown[1], shown[2] = shown[2], shown[1]
        if first and fault == 'wrong_main':
            shown[0], shown[1] = shown[1], shown[0]
        hidden = (first, len(sessions) - len(shown) - first)
        if first and fault == 'wrong_hidden':
            hidden = (first, hidden[1] + 1)
        upper = (4, height - 60, 200, 26) if first else None
        if fault == 'absent_upper' or fault == 'reverse_more' and up[0]:
            upper = None
        elif fault == 'already_upper':
            upper = (4, height - 60, 200, 26)
        lower = (4, 20, 200, 26) if hidden[1] else None
        panel_height = 40 + 72 * len(shown) + 34 * (bool(upper) + bool(lower))
        x = 2 if fault == 'unstable_span' and first else 0
        return NS(panel=(x, height - 1 - panel_height, 210, panel_height), scale=scale,
                  tiles=[NS(session=session) for session in shown], paged=paged, first=first, hidden=hidden,
                  more=(upper, lower), arrows=((0, 0, 10, 10), (20, 0, 10, 10)))

    def bind(lead, added, count):
        assert lead is ui._controllers['lead']
        for index in range(count):
            session = 'paging-qa-%d' % index
            added.append(session)
            sessions.append(session)
            ui._sessions.active = session
            if fail_bind:
                raise RuntimeError('partial bind failed')
        ui._sessions.active = 'lead'

    def unbind(actual_window, added, keep):
        assert actual_window is window
        for session in added[keep:]:
            assert session not in ORIGINAL
            removed.append(session)
            sessions.remove(session)
        del added[keep:]

    def check(name, condition, detail=None):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def send(kind, value, x, y):
        sent.append({'type': kind, 'value': value, 'x': x, 'y': y})
        if kind == 'WHEELDOWNMOUSE':
            events.append(kind)
            down[0] += 1
            if fault == 'ignored' or fault == 'second_ignored' and down[0] == 2:
                return
            paging_first[0] += 2 if fault == 'double' or fault == 'second_double' and down[0] == 2 else 1
        elif kind == 'WHEELUPMOUSE':
            events.append(kind)
            up[0] += 1
            if fault != 'reverse_ignored':
                paging_first[0] -= 1
        elif kind == 'LEFTMOUSE' and value == 'RELEASE':
            events.append('ARROW')
            if fault != 'arrow_ignored':
                paging_first[0] -= 1

    def click(x, y):
        for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
            send(kind, value, x, y)

    def tile_point(session):
        assert session == plan().tiles[-1].session
        return 30 + paging_first[0] * 5, 70 + len(plan().tiles) * 3

    scope = dict(state=state, frames=frames, report={'lead': 'lead', 'timings': {}},
                 bpy=NS(context=NS(preferences=NS(view=view, system=system))),
                 time=NS(monotonic=lambda: clock[0]), traceback=traceback, ui=ui, window=window,
                 session_rail=NS(TEXT_BLOCK=64), parallel_sessions=NS(sessions=lambda: list(sessions)),
                 view_region=lambda: region, plan=plan, check=check, send=send, click=click,
                 tile_point=tile_point, screenshot=shots.append)
    nodes = [node for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name.startswith('paging_')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)

    def tick():
        clock[0] += 4
        current = plan()
        frames.extend({'at': clock[0] - delta, 'top': current.panel[3] if current.tiles else None}
                      for delta in (0.3, 0.2, 0.1))
        return scope['paging_case']()

    def finish():
        for _ in range(30):
            if not tick():
                return
        raise AssertionError('fixture did not finish')

    with patch.dict(sys.modules, {'rail_positions_fixture': NS(bind=bind, unbind=unbind)}):
        yield NS(tick=tick, finish=finish, state=state, view=view, sessions=sessions, ui=ui, checks=checks,
                 events=events, sent=sent, shots=shots, removed=removed, report=scope['report'])


class PagingFixtureTests(unittest.TestCase):
    def assert_clean(self, fixture):
        self.assertEqual(fixture.sessions, ORIGINAL)
        self.assertEqual(fixture.ui._sessions.active, 'lead')
        self.assertEqual(fixture.view.ui_scale, 1.0)
        self.assertFalse(set(fixture.removed).intersection(ORIGINAL))

    def test_crowd_exceeds_capacity_without_changing_interface_scale(self):
        for height, scale in ((605, 1.0), (1200, 2.0), (1700, 1.5)):
            with self.subTest(height=height, scale=scale), world(height, scale) as fixture:
                self.assertTrue(fixture.tick())
                self.assertEqual(fixture.view.ui_scale, scale)
                self.assertGreater(len(fixture.sessions), height / (64 * scale))
                self.assertEqual(fixture.sessions[:3], ORIGINAL)

    def test_constant_capacity_keeps_the_original_down_then_arrow_route(self):
        with world() as fixture:
            fixture.finish()
            self.assertTrue(all(fixture.checks.values()), fixture.checks)
            self.assertEqual(fixture.events, ['WHEELDOWNMOUSE', 'ARROW'])
            self.assertEqual(fixture.report['timings'].get('paging_tail_progress_notches'), 1)
            self.assertIn('its_up_arrow_shows_the_first_workers_again', fixture.checks)
            self.assertIn('every_tile_back_when_there_is_room', fixture.checks)
            self.assert_clean(fixture)

    def test_one_new_more_row_gets_only_one_extra_notch_then_reverses_it(self):
        with world(contracting=True) as fixture:
            fixture.finish()
            self.assertEqual(fixture.events, ['WHEELDOWNMOUSE', 'WHEELDOWNMOUSE', 'WHEELUPMOUSE', 'ARROW'])
            self.assertEqual(fixture.report['timings'].get('paging_tail_progress_notches'), 2)
            self.assertTrue(fixture.checks.get('the_wheel_over_a_paged_rail_shows_the_next_worker'))
            self.assertTrue(fixture.checks.get('a_reverse_wheel_restores_the_first_scrolled_window'))
            self.assert_clean(fixture)

    def test_ignored_doubled_reordered_or_wrong_count_first_step_fails(self):
        for fault in ('ignored', 'double', 'reordered', 'wrong_main', 'wrong_hidden'):
            with self.subTest(fault=fault), world(contracting=True, fault=fault) as fixture:
                with self.assertRaisesRegex(AssertionError, 'paging_wheel_has_exact'):
                    fixture.finish()
                self.assertEqual(fixture.events, ['WHEELDOWNMOUSE'])
                self.assert_clean(fixture)

    def test_extra_notch_is_refused_for_any_other_capacity_change(self):
        for fault in ('absent_upper', 'already_upper', 'incorrect_drop', 'unstable_span'):
            with self.subTest(fault=fault), world(contracting=True, fault=fault) as fixture:
                with self.assertRaisesRegex(AssertionError, 'paging_extra_notch_requires'):
                    fixture.finish()
                self.assertEqual(fixture.events, ['WHEELDOWNMOUSE'])
                self.assert_clean(fixture)

    def test_second_notch_cannot_be_ignored_doubled_or_still_hide_the_old_tail(self):
        for fault, failure in (('second_ignored', 'paging_wheel_tail_has_exact'),
                               ('second_double', 'paging_wheel_tail_has_exact'),
                               ('second_no_tail', 'the_wheel_over_a_paged_rail')):
            with self.subTest(fault=fault), world(contracting=True, fault=fault) as fixture:
                with self.assertRaisesRegex(AssertionError, failure):
                    fixture.finish()
                self.assertEqual(fixture.events, ['WHEELDOWNMOUSE', 'WHEELDOWNMOUSE'])
                self.assert_clean(fixture)

    def test_reverse_must_restore_the_entire_first_step_window(self):
        for fault, failure in (('reverse_ignored', 'paging_wheel_return_has_exact'),
                               ('reverse_window', 'a_reverse_wheel_restores'),
                               ('reverse_more', 'a_reverse_wheel_restores')):
            with self.subTest(fault=fault), world(contracting=True, fault=fault) as fixture:
                with self.assertRaisesRegex(AssertionError, failure):
                    fixture.finish()
                self.assertEqual(fixture.events, ['WHEELDOWNMOUSE', 'WHEELDOWNMOUSE', 'WHEELUPMOUSE'])
                self.assert_clean(fixture)

    def test_original_arrow_back_requirement_still_fails_when_arrow_is_ignored(self):
        for contracting in (False, True):
            with self.subTest(contracting=contracting), world(contracting=contracting, fault='arrow_ignored') as fixture:
                with self.assertRaisesRegex(AssertionError, 'its_up_arrow_shows_the_first_workers_again'):
                    fixture.finish()
                self.assert_clean(fixture)

    def test_receipts_record_actual_coordinates_and_each_stage_capture(self):
        for contracting in (False, True):
            with self.subTest(contracting=contracting), world(contracting=contracting) as fixture:
                fixture.finish()
                self.assertIn('paging_stages', fixture.report)
                entries = fixture.report['paging_stages']
                self.assertEqual([event for entry in entries for event in entry['events']], fixture.sent)
                for entry in entries:
                    self.assertIn('before', entry)
                    self.assertIn('after', entry)
                    self.assertTrue({'panel', 'first', 'shown', 'hidden', 'more'} <= entry['after'].keys())
                    self.assertIn('1d-rail-' + entry['stage'].replace('_', '-') + '-after.png', fixture.shots)

    def test_failed_stage_keeps_its_after_snapshot_and_screenshot(self):
        with world(contracting=True, fault='second_no_tail') as fixture:
            with self.assertRaises(AssertionError):
                fixture.finish()
            self.assertIn('paging_stages', fixture.report)
            last = fixture.report['paging_stages'][-1]
            self.assertEqual(last['stage'], 'wheel_tail')
            self.assertIn('after', last)
            self.assertEqual(last['after']['first'], 2)
            self.assertIn('1d-rail-wheel-tail-after.png', fixture.shots)

    def test_partial_setup_failure_detaches_only_the_chats_it_added(self):
        with world(fail_bind=True) as fixture:
            with self.assertRaisesRegex(RuntimeError, 'partial bind failed'):
                fixture.tick()
            self.assert_clean(fixture)
            self.assertEqual(fixture.removed, ['paging-qa-0'])


if __name__ == '__main__':
    unittest.main()
