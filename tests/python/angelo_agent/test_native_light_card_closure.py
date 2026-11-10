# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Card palette/layout and actual draw-function theme switching; no Blender/GPU/provider work."""
import suite_isolation  # noqa: F401  First: isolated provider state.

import ast
import contextlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / 'scripts/addons_core/angelo_agent'
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import card_kit as kit, ui_tokens as tk  # noqa: E402


class View:
    _font, _mono = 1, 2

    def __init__(self):
        self.drawn = []

    def measure(self, text, size, font):
        return len(text) * size / 2

    def wrap(self, text, width, size, font):
        return [(text, 0)]

    def shape(self, *args):
        self.drawn.append(('shape', args))

    def text(self, *args):
        self.drawn.append(('text', args))

    def icon(self, *args):
        self.drawn.append(('icon', args))


def actual_functions(module, wanted, namespace):
    """Execute the named production functions and their original button palette, including old defaults."""
    source = ast.parse((ADDON / (module + '.py')).read_text())
    body = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in wanted
            or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                                               {'BUTTON_FILL', 'BUTTON_TEXT', 'GHOST_HOVER', 'GHOST_PRESSED'}
                                               for target in n.targets for t in ast.walk(target))]
    namespace.update(tk=tk, ui_tokens=tk, kit=kit, __package__='angelo_agent')
    for name in tk.COLOURS:
        namespace.setdefault(name, getattr(tk, name))
    exec(compile(ast.Module(body=body, type_ignores=[]), str(ADDON / (module + '.py')), 'exec'), namespace)
    return namespace


class ThemeCardTests(unittest.TestCase):
    def tearDown(self):
        self.assertEqual(tk._applied['depth'], 0)
        tk._apply('dark')

    def test_card_builder_and_forwarded_palette_follow_each_draw(self):
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                self.assertEqual(kit.CARD, tk.CARD)
                b = kit.Builder(View(), 1, 400)
                b.paragraph('A description', 13)
                self.assertEqual(b.items[-1][-2], tk.MUTED)
                b.heading('A heading')
                self.assertEqual(b.items[-1][-2], tk.TEXT)

    def test_actual_card_button_reads_current_fill_and_disabled_label(self):
        ns = actual_functions('card_draw', {'_button', 'button_fill', 'button_text'}, {})
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                view = View()
                ns['_button'](view, (0, 0, 120, 30), 'Continue', 'secondary', (False, False, False, True), 13.5)
                self.assertEqual(view.drawn[0][1][4], tk.LINE)
                self.assertEqual(view.drawn[-1][1][4], tk.TEXT)
                view.drawn.clear()
                ns['_button'](view, (0, 0, 120, 30), 'Continue', 'secondary', (False, False, False, False), 13.5)
                self.assertEqual(view.drawn[-1][1][4], tk.MUTED)

    def test_actual_decision_card_defaults_are_resolved_during_draw(self):
        view = View()
        ns = actual_functions('chat_card_draw', {'_over', '_rows', '_card'}, {'_view': lambda: view})
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                self.assertEqual(ns['_over']((0, 0, 0, 0)), tk.PANEL)
                view.drawn.clear()
                ns['_rows']([('Current result', 0)], 0, 10, 13, 16, (-50, 50))
                self.assertEqual(view.drawn[-1][1][4], tk.TEXT)
                ns['_card'](0, 100, 400, None)
                self.assertEqual(view.drawn[-1][1][4], tk.PANEL)

    def test_actual_approval_preview_and_job_body_rows_keep_contrast(self):
        import angelo_agent
        view = View()
        controller = SimpleNamespace(state={'id': 'owned'}, reviewed_calls=set())
        ctx = dict(state={}, controller=controller, width=400, size=13, line_height=16,
                   band=(-100, 500), expanded=set())
        ns = actual_functions('chat_card_draw', {'_draw_approval', '_draw_preview', '_draw_job', '_rows'},
            {'_view': lambda: view, '_card': lambda *args: None, '_header': lambda *args: 200,
             '_count_chips': lambda counts: (), '_button': lambda *args: 120,
             'BUTTON_Y': 26, 'ALWAYS_GAP': 40, 'UNAVAILABLE': 'Unavailable', 'iface_': lambda text: text})
        samples = [('_draw_approval', dict(kind='script', script=False, preparing=False, detail_rows=[],
                    mono=2, always_rows=[]), [('A title', 0), ('Current body', 0)]),
                   ('_draw_preview', dict(counts={}, image_height=0, detail_rows=[], preview_id='p',
                    available=True), [('Current body', 0)]),
                   ('_draw_job', dict(fraction=None), [('Current body', 0)])]
        with patch.object(angelo_agent, 'chat_cards', SimpleNamespace(), create=True):
            for variant in ('light', 'dark'):
                with tk.themed(variant):
                    for function, message, rows in samples:
                        with self.subTest(variant=variant, function=function):
                            view.drawn.clear()
                            ns[function](message, rows, 'call', 250, 0, ctx)
                            body = [args for kind, args in view.drawn if kind == 'text' and args[0] == 'Current body']
                            self.assertEqual(len(body), 1)
                            self.assertEqual(body[0][4], tk.SUBTITLE)

    def test_actual_disabled_decision_button_uses_disabled_text_role(self):
        view = View()
        ns = actual_functions('chat_card_draw', {'_button'}, {'_view': lambda: view,
            '_measure': lambda *args: 70, 'BUTTON_H': 30})
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                view.drawn.clear()
                ns['_button']({'hits': []}, 0, 0, 'Unavailable', 'disabled', None, 'disabled')
                self.assertEqual(view.drawn[-1][1][4], tk.DISABLED_TEXT)

    def test_actual_activity_kind_icon_default_follows_current_variant(self):
        view = View()
        ns = actual_functions('chat_rows', {'kind_icon'}, {'_view': lambda: view})
        for variant in ('light', 'dark'):
            with self.subTest(variant=variant), tk.themed(variant):
                view.drawn.clear()
                ns['kind_icon']('edit', 4, 5)
                self.assertEqual(view.drawn[-1][1][-1], tk.MUTED)

    def test_actual_card_host_relayouts_once_when_theme_changes(self):
        ui = SimpleNamespace(focus=None, hover=None, pressed=None, scroll={}, time=0)
        layouts, draws = [], []
        def layout(*args):
            layouts.append((tk.CARD, tk.TEXT))
            return SimpleNamespace(colours=layouts[-1])
        card = SimpleNamespace(version=lambda: 0, animated=False, layout=layout)
        state = SimpleNamespace(area=1, ui=ui, card=card, keyboard=False, plan=None, plan_key=None)
        region = SimpleNamespace(type='WINDOW', width=800, height=600, as_pointer=lambda: 2)
        area = SimpleNamespace(regions=[region], as_pointer=lambda: 1)
        bpy = SimpleNamespace(context=SimpleNamespace(area=area, region=region,
                                   preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1))))
        hero = SimpleNamespace(paint_beneath=lambda *args: None)
        ns = actual_functions('card_host', {'_draw'}, {'_active': state, '_view': View(), 'bpy': bpy,
            'font_watch': SimpleNamespace(checks={'reloads': 0}), '_language': lambda: 'en_US',
            'canvas_rtl': SimpleNamespace(begin=lambda *args: None, end=lambda: None),
            'card_draw': SimpleNamespace(draw=lambda view, plan: draws.append(plan.colours)),
            'gpu': SimpleNamespace(state=SimpleNamespace(blend_set=lambda value: None))})
        import angelo_agent
        with patch.object(angelo_agent, 'connect_hero', hero, create=True):
            for variant in ('dark', 'light', 'light'):
                def follow(current=variant):
                    tk._theme['variant'] = current
                with patch.object(tk, 'follow_theme', follow):
                    ns['_draw']()
        self.assertEqual(layouts, [(tk.DARK['CARD'], tk.DARK['TEXT']), (tk.LIGHT['CARD'], tk.LIGHT['TEXT'])])
        self.assertEqual(draws, [layouts[0], layouts[1], layouts[1]])

    def test_shared_provider_card_helpers_do_not_capture_palette_imports(self):
        for module in ('providers_sheet', 'providers_menu'):
            tree = ast.parse((ADDON / (module + '.py')).read_text())
            captured = [(n.lineno, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                        and n.module in {'card_kit', 'ui_tokens'} for a in n.names if a.name in tk.COLOURS]
            self.assertEqual(captured, [], module)


if __name__ == '__main__':
    unittest.main()
