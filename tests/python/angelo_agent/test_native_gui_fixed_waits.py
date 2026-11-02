# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A window suite waits for asynchronous work by that work's own signal, never by a fixed sleep (1.0.8).

A fixed ``time.sleep`` before a check bets that the work (a child process exiting, a job's thread reaching its break
check, a browser thread allowing a sign-in, a scripted model's next round) is done by then; a loaded machine loses
the bet. test_blender_headless_native_commits slept a second after killing the headless child, and a call reaching
the launcher before it saw the child die went to the dying child; test_blender_headless_mcp slept before checking
the child had ended; test_blender_render_deadline held the main thread a fixed 4 s past the job's budget and needed
the job's thread to have stopped it by then; test_blender_team_chat's browser allowed the sign-in after 0.6 s,
racing the chat's redraw; test_blender_survey_grading_dryrun's scripted model replied a second after a tool result,
racing the driver's sample. Each now polls the work's own state with a cap. The lint covers the ``time.sleep(...)``
statement outside a polling loop in a window suite; a fixed tick interval returned to a timer is the same idiom but
cannot be told apart from pacing by syntax, so it is not linted.

A synthetic click (``qa_input.press``) is sent by a timer registered after the suite's own; when a stalled main thread
makes both due in one pass the suite's runs first and read the click's effect before the click (flow_menu's Run all,
the library card's untick). A suite that presses registers its tick through ``qa_input.after_clicks``: covered by a
lint and the wrapper's own test.
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent


def fixed_sleeps(source):
    """(line, text) of each ``time.sleep(...)`` that is not inside a ``while``/``for`` loop (a poll on a condition)."""
    tree = ast.parse(source)
    polled = {id(node) for loop in ast.walk(tree) if isinstance(loop, (ast.While, ast.For))
              for node in ast.walk(loop)}
    return [(node.lineno, ast.unparse(node)) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("time.sleep", "sleep")
            and id(node) not in polled]


class NoFixedSleepTests(unittest.TestCase):
    def test_no_window_suite_waits_with_a_fixed_sleep(self):
        import native_suite_manifest as manifest
        found = {}
        for suite in manifest.SUITES:
            if suite.kind == manifest.BACKGROUND:
                continue
            path = HERE / (suite.script if suite.script.endswith(".py") else suite.script + ".py")
            if sleeps := fixed_sleeps(path.read_text(encoding="utf-8")):
                found[path.name] = sleeps
        self.assertEqual(found, {}, "wait for the work's own signal (a process gone, a flag set, a sample taken), "
                                    "polling with a cap that fails loudly")

    def test_the_lint_sees_a_fixed_sleep_and_spares_a_poll(self):
        bare = "os.kill(pid, signal.SIGKILL)\ntime.sleep(1.0)\ncheck('restarted', call())\n"
        polled = ("os.kill(pid, signal.SIGKILL)\nwhile alive(pid) and time.monotonic() < deadline:\n"
                  "    time.sleep(0.05)\ncheck('restarted', call())\n")
        threaded = "def allow():\n    time.sleep(0.6)\n    open(url)\n"
        self.assertEqual(fixed_sleeps(bare), [(2, "time.sleep(1.0)")])
        self.assertEqual(fixed_sleeps(polled), [])
        self.assertEqual(len(fixed_sleeps(threaded)), 1, "a helper thread's fixed delay races the main thread too")


def presses_without_waiting(source):
    """Whether a suite sends ``qa_input.press`` clicks but never holds its tick while one is on its way."""
    return "qa_input.press(" in source and "qa_input.after_clicks(" not in source


class ClicksBeforeEffectsTests(unittest.TestCase):
    def test_every_suite_that_presses_waits_for_its_clicks(self):
        found = sorted(path.name for path in HERE.glob("test_blender_*.py")
                       if presses_without_waiting(path.read_text(encoding="utf-8")))
        self.assertEqual(found, [], "register the tick as qa_input.after_clicks(tick)")

    def test_the_lint_sees_a_press_without_the_wait(self):
        bare = "qa_input.press(window(), x, y)\nbpy.app.timers.register(tick)\n"
        self.assertTrue(presses_without_waiting(bare))
        self.assertFalse(presses_without_waiting(bare.replace("register(tick", "register(qa_input.after_clicks(tick)")))

    def test_a_tick_waits_until_the_click_is_sent(self):
        import qa_input
        registered = []
        bpy = SimpleNamespace(app=SimpleNamespace(timers=SimpleNamespace(
            register=lambda callback, first_interval: registered.append(callback))),
            context=SimpleNamespace(window_manager=SimpleNamespace(windows=[])))
        window = SimpleNamespace(as_pointer=lambda: 1, event_simulate=lambda **event: None)
        calls = []
        tick = qa_input.after_clicks(lambda: calls.append(1) or .5)
        with mock.patch.dict(sys.modules, {"bpy": bpy}):
            qa_input.press(window, 10, 20)
            self.assertEqual(qa_input.pending(), 1)
            self.assertEqual(tick(), 0.02, "both due in one pass: the suite's tick waits for the click")
            self.assertEqual(calls, [])
            registered[0]()   # The click's own timer (its window gone here: nothing sent, nothing pending).
        self.assertEqual(qa_input.pending(), 0)
        self.assertEqual(tick(), .5)


if __name__ == "__main__":
    unittest.main()
