# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cramped and roomy windows share pictures, but own their button input and redraw signatures."""

import contextlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_native_rail_minimized  # Establishes the add-on path without Blender.
from test_native_rail_redraw import WINDOW, world
from test_native_rail_picture_retry import rail_pictures

import angelo_agent
from angelo_agent import rail_minimized as circle, rail_redraw, session_rail_controls as controls


@contextlib.contextmanager
def visibility():
    setting = SimpleNamespace(rail_minimized=lambda: False)
    with patch.dict(sys.modules, {'angelo_agent.parallel_setting': setting}), \
            patch.object(angelo_agent, 'parallel_setting', setting, create=True):
        circle.release()
        try:
            yield
        finally:
            circle.release()


class CursorTests(unittest.TestCase):
    def test_roomy_draw_clears_the_button_without_waiting_for_an_event(self):
        with patch.dict(sys.modules, {'bpy': SimpleNamespace()}):
            from angelo_agent import session_rail_draw as draw, session_rail_place, session_rail
        cursor = []
        window = SimpleNamespace(as_pointer=lambda: WINDOW, cursor_modal_restore=lambda: cursor.append('restore'))
        area = SimpleNamespace(as_pointer=lambda: 11)
        view = SimpleNamespace(chip_rect=lambda *args: None)
        chat = SimpleNamespace(ui_scale=lambda: 1)
        setting = SimpleNamespace(rail_minimized=lambda: False, rail_position=lambda: 'LEFT')
        with visibility(), patch.dict(sys.modules, {'angelo_agent.chat_view': chat,
                'angelo_agent.live_view_draw': view, 'angelo_agent.parallel_setting': setting}), \
                patch.object(angelo_agent, 'chat_view', chat, create=True), \
                patch.object(angelo_agent, 'live_view_draw', view, create=True), \
                patch.object(angelo_agent, 'parallel_setting', setting, create=True), \
                patch.object(draw, '_steady_text', return_value=0), patch.object(circle, '_redraw'), \
                patch.object(draw, 'last', {}), patch.object(draw, 'scroll', {}), \
                patch.object(draw, 'hover', {}), patch.object(draw, 'pressed', {}), \
                patch.object(session_rail_place, 'column', return_value=session_rail.Layout()):
            circle._places[WINDOW] = (11, 100, 100, 38)
            circle._hover.add(WINDOW)
            circle._pressed.add(WINDOW)
            circle.cramp(WINDOW, True)
            draw._draw(window, area, None, [])
            self.assertFalse(circle.shown(WINDOW))
            self.assertFalse(circle._hover or circle._pressed)
            self.assertEqual(cursor, ['restore'])

    def test_cramped_to_roomy_releases_hover_press_and_cursor_before_dispatch(self):
        cursors = []
        window = SimpleNamespace(as_pointer=lambda: WINDOW, cursor_modal_set=cursors.append,
                                 cursor_modal_restore=lambda: cursors.append('restore'))
        event = lambda kind, value='NOTHING': SimpleNamespace(type=kind, value=value, mouse_x=100, mouse_y=100)
        with visibility(), patch.object(circle, '_redraw'):
            circle._places[WINDOW] = (11, 100, 100, 38)
            circle.cramp(WINDOW, True)
            controls.restore_event(window, event('MOUSEMOVE'))
            controls.restore_event(window, event('LEFTMOUSE', 'PRESS'))
            self.assertEqual(cursors, ['HAND'])
            circle.cramp(WINDOW, False)
            self.assertIsNone(controls.restore_event(window, event('LEFTMOUSE', 'RELEASE')))
            self.assertEqual(cursors, ['HAND', 'restore'])
            self.assertFalse(circle._hover or circle._pressed)
            self.assertFalse(circle.hit(WINDOW, 100, 100))
            controls.restore_event(window, event('MOUSEMOVE'))
            self.assertEqual(cursors, ['HAND', 'restore'], 'restore once, not on every event')


class RedrawTests(unittest.TestCase):
    def test_cramped_window_ignores_hidden_text_but_updates_badge_and_recovers(self):
        with visibility(), world() as w:
            w.sessions.lead = 'lead'
            circle.cramp(WINDOW, True)
            rail_redraw.note_drawn(WINDOW, w.sessions.version, w.tiles)
            w.tiles[1].status, w.tiles[1].step = 'Working 42s', 'Roof'
            w.bump()
            self.assertEqual(rail_redraw.tick(10), 0)
            w.tiles[1].kind = 'approval'
            self.assertEqual(rail_redraw.tick(11), 1, 'approval badge still changes')
            rail_redraw.note_drawn(WINDOW, w.sessions.version, w.tiles)
            circle.cramp(WINDOW, False)
            self.assertEqual(rail_redraw.tick(12), 1, 'roomy panel receives its current tile content')

    def test_each_window_compares_its_own_visible_content(self):
        with visibility(), world() as w:
            w.sessions.lead = 'lead'
            manager = sys.modules['bpy'].context.window_manager
            other = SimpleNamespace(as_pointer=lambda: 8, screen=SimpleNamespace(is_temporary=False))
            manager.windows.append(other)
            circle.cramp(WINDOW, True)
            for pointer in (WINDOW, 8):
                rail_redraw.note_drawn(pointer, w.sessions.version, w.tiles)
            w.tiles[1].status = 'Working 42s'
            w.bump()
            self.assertEqual(rail_redraw.tick(10), 1)
            self.assertEqual([entry[1] for entry in rail_redraw.tags], [8])
            self.assertTrue(rail_redraw.panels_visible(), 'roomy second window needs shared pictures')
            circle.cramp(8, True)
            self.assertFalse(rail_redraw.panels_visible())
            manager.windows.remove(other)
            self.assertFalse(rail_redraw.panels_visible(), 'closed window cannot keep pictures running')


class PictureTests(unittest.TestCase):
    def test_shared_picture_work_waits_while_all_panels_are_hidden(self):
        with visibility(), world() as w, patch.object(rail_pictures, '_view_moved') as moved, \
                patch.object(rail_pictures, 'refresh') as refresh, patch.object(rail_redraw, 'tick'):
            w.sessions.stale = {}
            circle.cramp(WINDOW, True)
            with patch.object(rail_pictures, '_staged', {'lead': (3, iter(()))}):
                self.assertEqual(rail_pictures._tick(), rail_pictures.POLL)
                refresh.assert_not_called()
                moved.assert_called_once()
                self.assertEqual(w.sessions.stale, {'lead': 3}, 'interrupted work stays owed')
                self.assertEqual(rail_pictures._staged, {})
                other = SimpleNamespace(as_pointer=lambda: 8, screen=SimpleNamespace(is_temporary=False))
                sys.modules['bpy'].context.window_manager.windows.append(other)
                rail_pictures._tick()
                refresh.assert_called_once()
                w.held = True
                rail_pictures._tick()
                refresh.assert_called_once()


if __name__ == '__main__':
    unittest.main()
