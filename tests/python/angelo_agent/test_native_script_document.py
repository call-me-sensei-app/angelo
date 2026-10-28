# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script that replaced the open document reads nothing back (``script_document``), without Blender.

Evidence: in a background Blender a script that loads a file freed the window the executor read the scene back
through, and the process crashed (the parallel-agents spike, exit 139). ``test_blender_script_document_replaced``
and ``test_blender_headless_script_document`` prove the fix in a background Angelo and the real headless child;
these tests pin the guard's rules and its wiring.
"""

import ast
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import plan_recovery, script_document, script_errors  # noqa: E402


class FakeManager:
    def __init__(self, pointer):
        self.pointer = pointer

    def as_pointer(self):
        return self.pointer


class FakeBpy:
    """The parts of ``bpy`` the watch reads: ``app.handlers.load_post`` and the window manager."""

    def __init__(self):
        self.manager = FakeManager(1)
        self.context = types.SimpleNamespace(window_manager=self.manager)
        self.app = types.SimpleNamespace(handlers=types.SimpleNamespace(load_post=[], persistent=lambda f: f))

    def load(self, succeeded=True, new_manager=False):
        """What Blender does on a file load: load_post only when it succeeded."""
        if new_manager:
            self.context.window_manager = FakeManager(self.context.window_manager.pointer + 1)
        if succeeded:
            for handler in list(self.app.handlers.load_post):
                handler(None)


class WatchTests(unittest.TestCase):

    def watch(self):
        self.bpy = FakeBpy()
        return script_document.Watch(self.bpy).start()

    def test_no_load_is_no_replacement(self):
        watch = self.watch()
        self.assertFalse(watch.stop())
        self.assertEqual(self.bpy.app.handlers.load_post, [], "the handler is gone after the script")

    def test_a_load_that_succeeded_is_a_replacement(self):
        watch = self.watch()
        self.bpy.load()
        self.assertTrue(watch.stop())

    def test_a_failed_load_is_no_replacement(self):
        """Blender fires load_pre before it reads anything; a wrong path fires load_post_fail, never load_post."""
        watch = self.watch()
        self.bpy.load(succeeded=False)
        self.assertFalse(watch.stop())

    def test_a_removed_handler_fails_closed(self):
        """``bpy.app.handlers.load_post.clear()`` is a common clean-slate idiom; what it hid is unknown."""
        watch = self.watch()
        self.bpy.app.handlers.load_post.clear()
        self.assertTrue(watch.stop())

    def test_a_new_window_manager_is_a_replacement(self):
        watch = self.watch()
        self.bpy.app.handlers.load_post.clear()
        self.bpy.load(new_manager=True)
        self.assertTrue(watch.replaced)
        watch2 = script_document.Watch(FakeBpy()).start()
        watch2.bpy.context.window_manager = FakeManager(99)
        self.assertTrue(watch2.stop(), "a background load replaces the window manager")
        watch.stop()

    def test_stop_is_idempotent(self):
        watch = self.watch()
        self.bpy.load()
        self.assertTrue(watch.stop())
        self.assertTrue(watch.stop())

    def test_host_hooks_inside_the_script_stop_once_the_document_is_gone(self):
        watch = self.watch()
        script_document.guard()   # Nothing loaded yet: the hook runs.
        self.bpy.load()
        with self.assertRaises(script_document.Replaced):
            script_document.guard()
        watch.stop()
        script_document.guard()   # No script running: nothing to guard.


class ReachTests(unittest.TestCase):
    """What the script itself can reach of the host stops too, by construction."""

    def test_the_kit_stops_once_the_document_is_gone(self):
        bpy = FakeBpy()
        kit = script_document.Guarded(types.SimpleNamespace(box=lambda *a: "made", count=1))
        watch = script_document.Watch(bpy).start()
        self.assertEqual(kit.box(), "made")
        kit.count = 2
        bpy.load()
        with self.assertRaises(script_document.Replaced):
            kit.box()
        with self.assertRaises(script_document.Replaced):
            kit.count = 3
        watch.stop()

    def test_a_script_run_from_a_script_keeps_the_outer_guard(self):
        outer_bpy, inner_bpy = FakeBpy(), FakeBpy()
        outer = script_document.Watch(outer_bpy).start()
        inner = script_document.Watch(inner_bpy).start()
        self.assertFalse(inner.stop())
        outer_bpy.load()
        with self.assertRaises(script_document.Replaced):
            script_document.guard()
        outer.stop()

    def test_the_namespace_hands_the_script_a_guarded_kit(self):
        text = (ADDON / "script_prepare.py").read_text(encoding="utf-8")
        self.assertIn('"kit": script_document.Guarded(kit)', text)


class ResultTests(unittest.TestCase):

    def test_the_result_promises_nothing_it_did_not_check(self):
        result = script_document.Replaced("printed", "ValueError").result
        self.assertEqual(result["error_code"], "DOCUMENT_REPLACED")
        self.assertIs(result["document_replaced"], True)
        self.assertIs(result["main_data_restored"], False)
        self.assertIs(result["partial_changes_possible"], True)
        self.assertEqual((result["output"], result["raised"]), ("printed", "ValueError"))

    def test_it_is_a_decision_that_ends_the_request(self):
        """Not a script error to correct: the document is gone, so the person looks before anything else runs."""
        result = script_document.Replaced("").result
        self.assertTrue(plan_recovery.decided(result))
        self.assertFalse(script_errors.raised(result), "no script correction, no 'stopped with an error' row")

    def test_the_script_s_failure_handling_never_catches_it(self):
        self.assertFalse(issubclass(script_document.Replaced, script_errors.SCRIPT_FAILURES),
                         "a rollback would read the freed window")

        def replaced():
            raise script_document.Replaced("x")
        self.assertEqual(script_document.run(replaced)["output"], "x")
        self.assertEqual(script_document.run(lambda a: {"ok": a}, 1), {"ok": 1})


class WiringTests(unittest.TestCase):

    def _source(self, name):
        return (ADDON / name).read_text(encoding="utf-8")

    def _method(self, module, cls, name):
        tree = ast.parse(self._source(module))
        owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
        return ast.unparse(next(f for f in owner.body if isinstance(f, ast.FunctionDef) and f.name == name))

    def test_the_executor_returns_the_refusal_before_any_read_back(self):
        execute = self._method("blender_tools.py", "BlenderExecutor", "execute")
        self.assertIn("result = script_document.run(self._execute, name, arguments)", execute)
        early = execute.index("result.get('document_replaced')")
        self.assertLess(early, execute.index("result['changes']"), "no scene diff through the freed window")
        self.assertLess(early, execute.index("prior.attach("), "no after-read either")
        self.assertIn("script_after.Observing(bpy, self._prior, output)", self._source("blender_tools.py"))

    def test_observing_watches_the_whole_script_and_stops_in_finally(self):
        enter = self._method("script_after.py", "Observing", "__enter__")
        self.assertLess(enter.index("self._document.start()"), enter.index("ExitStack"))
        exit_ = self._method("script_after.py", "Observing", "__exit__")
        self.assertIn("finally:", exit_)
        self.assertLess(exit_.index("finally:"), exit_.index("self._document.stop()"))
        self.assertIn("raise script_document.Replaced", exit_)
        self.assertIn("self._document.stop()", enter, "a script that never started leaves no handler")

    def test_hooks_that_hold_a_view_layer_guard_first(self):
        hook = self._method("script_prepare.py", "Prepared", "_evaluated_parent")
        self.assertLess(hook.index("script_document.guard()"), hook.index("self._layer"))

    def test_the_apply_operator_pushes_no_undo_into_a_replaced_file(self):
        self.assertIn('result.get("main_data_restored") or result.get("document_replaced")) else {\'FINISHED\'}',
                      self._source("ui.py"))


if __name__ == "__main__":
    unittest.main()
