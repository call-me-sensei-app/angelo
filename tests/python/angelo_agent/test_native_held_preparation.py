# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GUI suite that makes a look's budget end during preparation holds the render there (1.0.7).

``rendered_view_fixtures.HeldPreparation`` waits in ``render_pre``, on the render job's thread, until the suite has
seen the budget end. Without it a sub-second budget raced the scene's preparation: test_blender_rendered_view_safety
expected "still preparing the scene" from a 0.3 s request total against ~0.5 s of preparation, and a budget check
seen late let the first samples in (9/10 on 744f498d0a6, 4/10 on d72ea2aa3ea). Covered: the hold blocks until
released, leaving it releases and removes the handler, and two lints over the GUI suites: a suite whose checks
expect a look to end "still preparing the scene" holds preparation, and a sub-second look budget written as
``view_render.X = constant`` is followed by a hold. The lints see this idiom, not every way to shrink a budget.
"""

import ast
import importlib.util
import sys
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
BUDGETS = {"RENDER_SECONDS", "RENDER_REQUEST_SECONDS"}
PREPARING = "still preparing the scene"   # view_render's note for a look whose budget ended before its first sample.


def load_fixtures():
    """``rendered_view_fixtures`` against a stand-in ``bpy`` and ``render_extensions``; nothing left behind."""
    hosted = []
    package = types.ModuleType("angelo_agent")
    package.render_extensions = SimpleNamespace(host_handler=lambda function: hosted.append(function) or function)
    handlers = SimpleNamespace(render_pre=[])
    modules = {"bpy": SimpleNamespace(app=SimpleNamespace(handlers=handlers)), "angelo_agent": package,
               "angelo_agent.render_extensions": package.render_extensions}
    patch = mock.patch.dict(sys.modules, modules)
    patch.start()
    spec = importlib.util.spec_from_file_location("_rendered_view_fixtures_under_test", HERE / "rendered_view_fixtures.py")
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    return fixtures, handlers, hosted, patch


class HeldPreparationTests(unittest.TestCase):
    def setUp(self):
        self.fx, self.handlers, self.hosted, patch = load_fixtures()
        self.addCleanup(patch.stop)

    def render(self):
        """The render job's thread reaching ``render_pre``."""
        thread = threading.Thread(target=lambda: [handler(None) for handler in list(self.handlers.render_pre)])
        thread.start()
        return thread

    def test_the_render_waits_until_released(self):
        with self.fx.HeldPreparation() as held:
            self.assertEqual(self.handlers.render_pre, [held._handler])
            self.assertEqual(self.hosted, [held._handler], "the fixture's own handler, never add-on Python")
            thread = self.render()
            self.assertTrue(held.held.wait(5))
            thread.join(0.3)
            self.assertTrue(thread.is_alive(), "still preparing until the suite releases it")
            held.release()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(held.threads, [False], "held on the render's thread, never the main one")
        self.assertEqual(self.handlers.render_pre, [])

    def test_leaving_releases_and_removes_the_handler(self):
        held = self.fx.HeldPreparation()
        with held:
            thread = self.render()
            self.assertTrue(held.held.wait(5))
        thread.join(5)
        self.assertFalse(thread.is_alive(), "a suite that fails mid-hold never leaves a render waiting")
        self.assertEqual(self.handlers.render_pre, [])

    def test_it_never_holds_longer_than_its_limit(self):
        with self.fx.HeldPreparation(limit=0.2) as held:
            thread = self.render()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(held.held.is_set())


def unheld_budgets(source):
    """(line, text) of each sub-second look budget (``view_render.RENDER_SECONDS`` / ``RENDER_REQUEST_SECONDS``)
    whose following statements, until that budget is set again, start no ``HeldPreparation``."""
    found = []
    for parent in ast.walk(ast.parse(source)):
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        for index, statement in enumerate(body):
            if not isinstance(statement, ast.Assign):
                continue
            pairs = []
            for target in statement.targets:
                names = target.elts if isinstance(target, ast.Tuple) else [target]
                values = statement.value.elts if isinstance(statement.value, ast.Tuple) else [statement.value]
                pairs += list(zip(names, values)) if len(names) == len(values) else []
            short = {name.attr for name, value in pairs
                     if isinstance(name, ast.Attribute) and isinstance(name.value, ast.Name)
                     and name.value.id == "view_render" and name.attr in BUDGETS
                     and isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and value.value < 1}
            if not short:
                continue
            held = False
            for later in body[index + 1:]:
                text = ast.unparse(later)
                if "HeldPreparation(" in text:
                    held = True
                    break
                if any("view_render.%s" % name in text and isinstance(later, ast.Assign) for name in short):
                    break
            if not held:
                found.append((statement.lineno, ast.unparse(statement)))
    return found


class SubSecondBudgetTests(unittest.TestCase):
    def test_every_sub_second_look_budget_is_held_in_preparation(self):
        found = {p.name: lines for p in sorted(HERE.glob("test_blender_*.py"))
                 if (lines := unheld_budgets(p.read_text(encoding="utf-8")))}
        self.assertEqual(found, {}, "a sub-second look budget races the scene's preparation: hold it with "
                                    "rendered_view_fixtures.HeldPreparation until the suite has seen it end")

    def test_a_suite_expecting_still_preparing_holds_preparation(self):
        """Ending a look before its first sample, whatever the budget or how it was shrunk, needs the hold."""
        found = [p.name for p in sorted(HERE.glob("test_blender_*.py"))
                 if PREPARING in (text := p.read_text(encoding="utf-8")) and "HeldPreparation(" not in text]
        self.assertEqual(found, [], "expects %r without rendered_view_fixtures.HeldPreparation" % PREPARING)

    def test_the_check_sees_an_unheld_budget(self):
        bare = ("def scenario():\n    view_render.RENDER_REQUEST_SECONDS, view_render.MINIMUM_SECONDS = 0.3, 0\n"
                "    pending = start()\n    view_render.RENDER_REQUEST_SECONDS = 360\n")
        held = ("def scenario():\n    view_render.RENDER_SECONDS = 0.3\n    with fx.HeldPreparation() as held:\n"
                "        pending = start()\n    view_render.RENDER_SECONDS = 120\n")
        long = "def scenario():\n    view_render.RENDER_SECONDS = 4\n    pending = start()\n"
        self.assertEqual(unheld_budgets(bare), [(2, "view_render.RENDER_REQUEST_SECONDS, view_render.MINIMUM_SECONDS"
                                                    " = (0.3, 0)")])
        self.assertEqual(unheld_budgets(held), [])
        self.assertEqual(unheld_budgets(long), [], "a budget of seconds ends during sampling, as its check expects")


if __name__ == "__main__":
    unittest.main()
