# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute actual source argv statements only; no browser, module/native import or process creation."""
import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent'
FLAGS = ('--restore-last-session', '--no-startup-window')



def _background_flags():
    """fab_browser_background.MANAGED_FLAGS, read from its file (the module needs no package or bpy)."""
    import importlib.util
    path = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent/fab_browser_background.py'
    spec = importlib.util.spec_from_file_location('fab_browser_background_flags', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MANAGED_FLAGS


MANAGED_FLAGS = _background_flags()


def argv_statements(filename, *, windows):
    tree = ast.parse((ROOT / filename).read_text())
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == ('WindowsBrowserProcess' if windows else 'BrowserProcess'))
    init = next(n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    for block in ast.walk(init):
        for field in ('body', 'orelse', 'finalbody'):
            rows = getattr(block, field, None)
            if not isinstance(rows, list):
                continue
            for start, row in enumerate(rows):
                if not isinstance(row, ast.Assign) or len(row.targets) != 1:
                    continue
                target = row.targets[0]
                if not isinstance(target, ast.Name) or target.id != ('managed_arguments' if windows else 'args'):
                    continue
                chosen = rows[start:start + (3 if windows else 3)]
                if windows:
                    if (len(chosen) != 3 or not isinstance(chosen[1], ast.Assign)
                            or not isinstance(chosen[1].targets[0], ast.Name)
                            or chosen[1].targets[0].id != 'arguments'
                            or not isinstance(chosen[2], ast.Assign)
                            or not isinstance(chosen[2].targets[0], ast.Attribute)
                            or chosen[2].targets[0].attr != '_launch_arguments'):
                        raise AssertionError('Windows actual argv block changed')
                elif len(chosen) != 3 or not isinstance(chosen[1], ast.If) or not isinstance(chosen[2], ast.AugAssign):
                    raise AssertionError('Mac actual argv block changed')
                # Restrict extraction to the no-call assignment/branch block, never execute a constructor.
                if any(isinstance(n, (ast.Call, ast.Import, ast.ImportFrom)) for row in chosen for n in ast.walk(row)):
                    # str(self.profile) is the only production conversion in either argv block.
                    calls = [n for row in chosen for n in ast.walk(row) if isinstance(n, ast.Call)]
                    if any(not isinstance(n.func, ast.Name) or n.func.id != 'str'
                           or len(n.args) != 1 or n.keywords for n in calls):
                        raise AssertionError('unexpected callable in argv source block')
                return ast.Module(body=chosen, type_ignores=[])
    raise AssertionError('actual argv block not found')


def build(*, windows, manual):
    program = 'C:\\Fixture\\chrome.exe' if windows else '/fixture/Google Chrome'
    browser = SimpleNamespace(profile='fixture-owned-profile', _manual_login=manual)
    values = {'self': browser, 'program': program, 'manual_login': manual,
              'pipe_arguments': [] if manual else ['--remote-debugging-pipe'], 'str': str}
    name = 'fab_browser_process_windows.py' if windows else 'fab_browser_process.py'
    exec(compile(argv_statements(name, windows=windows), '<actual-argv-source>', 'exec'), values)
    return values.get('arguments') if windows else values['args'], browser, program


class StartupArgumentsTests(unittest.TestCase):
    def check(self, *, windows, manual):
        argv, browser, program = build(windows=windows, manual=manual)
        original = [program, '--user-data-dir=fixture-owned-profile',
                    '--no-first-run', '--no-default-browser-check', '--new-window', 'https://www.fab.com/']
        if manual:
            self.assertEqual(list(argv), original)
            for flag in (*FLAGS, '--remote-debugging-pipe'):
                self.assertNotIn(flag, argv)
        else:
            background = [] if windows else list(MANAGED_FLAGS)   # macOS: the managed browser works behind Angelo.
            self.assertEqual(list(argv), original[:2] + [*FLAGS, '--remote-debugging-pipe'] + background
                             + original[2:-1] + ['about:blank'])
            for flag in FLAGS:
                self.assertEqual(argv.count(flag), 1)
        if windows:
            self.assertIs(type(argv), tuple)
            self.assertIs(browser._launch_arguments, argv)
            self.assertLessEqual(len(argv), 32)
            self.assertLessEqual(sum(len(value) + 1 for value in argv), 32767)
    def test_mac_managed_uses_both_flags(self):
        self.check(windows=False, manual=False)
    def test_mac_manual_argv_unchanged(self):
        self.check(windows=False, manual=True)
    def test_windows_managed_uses_both_flags(self):
        self.check(windows=True, manual=False)
    def test_windows_manual_argv_unchanged(self):
        self.check(windows=True, manual=True)


if __name__ == '__main__':unittest.main()
