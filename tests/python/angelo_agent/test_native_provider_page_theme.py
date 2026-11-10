# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""AI Providers page colours and actual draw/overlay theme ownership, on stand-ins; not GUI/auth proof."""
import suite_isolation  # noqa: F401  First: isolated provider state.

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / 'scripts/addons_core'))
from test_native_light_card_closure import View, actual_functions  # noqa: E402
from angelo_agent import card_kit as kit, providers_page_layout as page, ui_tokens as tk  # noqa: E402


class ProviderPageThemeTests(unittest.TestCase):
    def setUp(self):
        saved = dict(tk._theme)
        self.addCleanup(lambda: tk._theme.update(saved))

    def tearDown(self):
        self.assertEqual(tk._applied['depth'], 0)
        tk._apply('dark')

    def test_legacy_page_palette_exports_resolve_each_current_draw(self):
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                self.assertEqual(page.PAGE_BG, tk.DETAIL_FILL)
                self.assertEqual(page.TONES['amber'], (tk.AMBER, tk.AMBER_SOFT))
                self.assertEqual(page.TONES['muted'], (tk.MUTED, tk.NEUTRAL_SOFT))

    def test_actual_attention_layout_keeps_body_and_action_ownership(self):
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                value = page.Page(View(), 1, 600, kit.UiState())
                page._attention(value, [dict(title='Connection expired', lines=['Try again'],
                                            action=('Reconnect', 'connect', 'owned-record'))])
                texts = {op[1]: op[-1] for op in value.items if op[0] == 'text'}
                self.assertEqual(texts['Connection expired'], tk.TEXT)
                self.assertEqual(texts['Try again'], tk.MUTED)
                self.assertTrue(any(op[0] == 'rect' and op[5] == tk.PANEL for op in value.items))
                self.assertEqual(value.hits[-1][-2:], ('connect', 'owned-record'))

    def test_actual_tile_and_status_chip_read_current_colours(self):
        ns = actual_functions('providers_page_draw', {'_tile', '_chip'},
                              {'tones': page.tones, 'ui_kit': SimpleNamespace(
                                  measure=lambda view, text, size: view.measure(text, size, view._font))})
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                view = View()
                ns['_tile'](view, (0, 0, 40, 40), 'Q', '', None)
                self.assertEqual(view.drawn[1][1][4], tk.PANEL_HOVER)
                self.assertEqual(view.drawn[-1][1][4], tk.TEXT)
                view.drawn.clear()
                ns['_chip'](view, (0, 0, 90, 21), 'Offline', 'amber', 11.5, False, 0)
                self.assertEqual(view.drawn[0][1][4], tk.AMBER_SOFT)
                self.assertEqual(view.drawn[-1][1][4], tk.AMBER)

    def host(self, fail=False):
        import angelo_agent
        events = []
        region = SimpleNamespace(as_pointer=lambda: 21, width=800, height=600)
        context = SimpleNamespace(region=region, preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1)))
        def planned(*args):
            events.append(('plan', tk.CARD, tk.TEXT))
            return SimpleNamespace(card=(0, 0, 500, 300), animated=False), object()
        def overlay(name):
            events.append((name, tk.CARD, tk.TEXT))
            return SimpleNamespace(name=name, colour=tk.CARD)
        def draw_page(*args):
            events.append(('draw-page', tk.CARD, tk.TEXT))
            if fail:
                raise RuntimeError('injected page draw failure')
        sheet = SimpleNamespace(animated=False, layout=lambda *args: overlay('sheet'))
        ns = actual_functions('providers_page_host', {'_draw', '_draw_themed'}, {
            'time': time, 'bpy': SimpleNamespace(context=context),
            'preferences_ui': SimpleNamespace(is_selected=lambda: True), '_ui': {}, '_keyboard': set(),
            '_plan': planned, '_top': lambda region: 600, '_plans': {}, '_remember': lambda model: None,
            '_canvas_view': View, '_menu_plan': lambda *args: overlay('menu'), '_sheet': sheet,
            '_sheet_ui': kit.UiState(), '_sheet_keyboard': False,
            'canvas_rtl': SimpleNamespace(begin=lambda width: events.append(('rtl-begin',)),
                                         end=lambda: events.append(('rtl-end',))),
            'providers_page_draw': SimpleNamespace(draw=draw_page)})
        card_draw = SimpleNamespace(draw=lambda view, plan: events.append(('draw-' + plan.name, tk.CARD, tk.TEXT)))
        gpu = SimpleNamespace(state=SimpleNamespace(blend_set=lambda mode: None))
        return ns['_draw'], events, patch.object(angelo_agent, 'card_draw', card_draw, create=True), patch.dict(sys.modules, {'gpu': gpu})

    def test_actual_host_lays_out_and_draws_page_and_overlays_in_one_theme(self):
        draw, events, card_patch, gpu_patch = self.host()
        for variant in ('light', 'dark'):
            events.clear()
            with card_patch, gpu_patch, patch.object(tk, 'follow_theme',
                                                    lambda current=variant: tk._theme.update(variant=current)):
                draw()
            coloured = [event for event in events if len(event) == 3]
            self.assertEqual([event[0] for event in coloured], ['plan', 'menu', 'sheet', 'draw-page', 'draw-menu', 'draw-sheet'])
            expected = tk.LIGHT if variant == 'light' else tk.DARK
            self.assertTrue(all(event[1:] == (expected['CARD'], expected['TEXT']) for event in coloured))
            self.assertEqual(tk.TEXT, tk.DARK['TEXT'])
            self.assertEqual(events[-1], ('rtl-end',))

    def test_actual_host_draw_failure_restores_theme_and_rtl(self):
        draw, events, card_patch, gpu_patch = self.host(fail=True)
        with card_patch, gpu_patch, patch.object(tk, 'follow_theme', lambda: tk._theme.update(variant='light')):
            with self.assertRaisesRegex(RuntimeError, '^injected page draw failure$'):
                draw()
        self.assertEqual(events[-1], ('rtl-end',))
        self.assertEqual(tk.TEXT, tk.DARK['TEXT'])
        self.assertEqual(tk._applied['depth'], 0)


if __name__ == '__main__':
    unittest.main()
