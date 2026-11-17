# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``crash_dialog.wait``: a crashing windowed launch on Windows ends only when its crash dialog is closed; the host
waits for the exit or the dialog, records the dialog and closes it. Stand-ins for the process and the windows, so it
runs on every platform; the real dialog is proven on Windows by update_carry_over_gate.py --crashing-addon (and in
1.0.11 by test_blender_settings_import --real-profile)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import crash_dialog  # noqa: E402

ROOT = HERE.parents[2]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Process:
    """Exits ``after`` polls, or only once closed when ``needs_close``."""

    def __init__(self, after=None, needs_close=False, code=11):
        self.pid, self.args, self.polls, self.closed, self.killed = 4242, ["angelo"], 0, False, False
        self.after, self.needs_close, self.code = after, needs_close, code

    def poll(self):
        self.polls += 1
        if self.killed or (self.needs_close and self.closed) or (self.after is not None and self.polls > self.after):
            return -9 if self.killed else self.code
        return None

    def kill(self):
        self.killed = True

    def wait(self):
        return -9


class WaitTests(unittest.TestCase):
    def run_wait(self, process, dialog_after=None, timeout=240):
        clock, seen = Clock(), {"closes": 0}

        def find(pid):
            self.assertEqual(pid, process.pid)
            return "hwnd" if dialog_after is not None and process.polls > dialog_after else None

        def close(window):
            seen["closes"] += 1
            process.closed = True
            return True
        result = crash_dialog.wait(process, timeout, find=find, record=lambda w: {"title": "Angelo", "window": w},
                                   close=close, clock=clock, sleep=clock.sleep)
        return result, seen

    def test_a_process_that_ends_by_itself_has_no_dialog(self):
        (code, dialog), seen = self.run_wait(Process(after=3, code=-11))
        self.assertEqual((code, dialog, seen["closes"]), (-11, None, 0))

    def test_the_crash_dialog_is_recorded_and_closed_once_and_the_exit_waited_for(self):
        process = Process(needs_close=True)
        (code, dialog), seen = self.run_wait(process, dialog_after=4)
        self.assertEqual(code, crash_dialog.CLOSED_EXIT)
        self.assertEqual(dialog, {"title": "Angelo", "window": "hwnd", "closed": True})
        self.assertEqual(seen["closes"], 1)

    def test_what_close_saw_is_kept_with_the_dialog(self):
        """The real close reads the dialog's texts and says how it closed it; all of that stays in the record."""
        process, clock = Process(needs_close=True), Clock()

        def close(window):
            process.closed = True
            return {"texts": ["Angelo has stopped working"], "closed": True, "closed_by": "ui_automation_invoke"}
        code, dialog = crash_dialog.wait(process, 240, find=lambda pid: "hwnd", record=lambda w: {"title": "Angelo"},
                                         close=close, clock=clock, sleep=clock.sleep)
        self.assertEqual(code, 11)
        self.assertEqual(dialog["closed_by"], "ui_automation_invoke")
        self.assertEqual(dialog["texts"], ["Angelo has stopped working"])

    def test_neither_in_time_kills_the_process_and_says_so(self):
        process = Process()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_wait(process, timeout=10)
        self.assertTrue(process.killed)

    def test_a_dialog_that_does_not_close_the_process_is_bounded_too(self):
        process = Process()   # Never exits, even after Close.
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_wait(process, dialog_after=1)
        self.assertTrue(process.killed)


def self_seen():
    """What the gate reads from a crash dialog that was pressed as a person presses it."""
    return {"title": "Angelo", "closed_by": "ui_automation_invoke",
            "texts": ["Angelo", "Angelo has stopped working",
                      "A problem has caused Angelo to stop functioning correctly. ... The crash log can be "
                      "found at:\nC:\\Users\\a\\AppData\\Local\\Temp\\angelo.crash.txt", "Close"]}


class GateTests(unittest.TestCase):
    """update_carry_over_gate's crashing launch: what it requires of the dialog, on Windows and elsewhere."""

    def setUp(self):
        import update_carry_over_gate as gate
        self.gate = gate
        self.seen = {"title": "Angelo", "closed_by": "ui_automation_invoke",
                     "texts": ["Angelo", "Angelo has stopped working",
                               "A problem has caused Angelo to stop functioning correctly. ... The crash log can be "
                               "found at:\nC:\\Users\\a\\AppData\\Local\\Temp\\angelo.crash.txt", "Close"]}

    def test_windows_needs_the_dialog_its_words_its_button_and_the_exit(self):
        self.assertTrue(all(self.gate.dialog_checks(self.seen, 11, "win32").values()))
        broken = {"no dialog": (None, 11), "closed by message": (dict(self.seen, closed_by="TDM_CLICK_BUTTON"), 11),
                  "wrong exit": (self.seen, 0xC0000005), "no log named": (dict(self.seen, texts=["Angelo has stopped "
                                                                                                 "working"]), 11)}
        for what, (dialog, code) in broken.items():
            with self.subTest(what):
                self.assertFalse(all(self.gate.dialog_checks(dialog, code, "win32").values()))

    def test_a_launch_while_the_dialog_is_open_runs_before_close(self):
        """--launch-while-dialog-open: the safe launch runs while the crashed process's dialog is still up, then Close."""
        from unittest.mock import patch
        order = []

        def wait(process, timeout, record=None, close=None):
            close("hwnd")
            return 11, {"closed_by": "ui_automation_invoke"}
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(self.gate.subprocess, "Popen", lambda *a, **k: object()), \
                patch.object(self.gate.crash_dialog, "wait", wait), \
                patch.object(self.gate.crash_dialog, "press", lambda window, button: order.append(button) or {}):
            code, findings, dialog = self.gate.run_window("angelo", [], {}, Path(temp) / "crash.out",
                                                          while_open=lambda: order.append("safe launch"))
        self.assertEqual(order, ["safe launch", "Close"])
        self.assertEqual(code, 11)

    def test_the_early_launch_is_windows_only(self):
        source = (HERE / "update_carry_over_gate.py").read_text(encoding="utf-8")
        self.assertIn('if args.launch_while_dialog_open and (sys.platform != "win32" or not args.crashing_addon)',
                      source)
        self.assertIn('launch("crash", while_open=lambda: launch("safe"))', source)

    def test_elsewhere_no_dialog_is_expected(self):
        self.assertEqual(self.gate.dialog_checks(None, -11, "darwin"), {"no_crash_dialog_elsewhere": True})


class SourceTests(unittest.TestCase):
    def test_the_dialog_and_its_exit_are_the_crash_handler_s(self):
        """The constants match the handler: Close returns, then the process ends with SIGSEGV."""
        signals = (ROOT / "source/creator/creator_signals.cc").read_text(encoding="utf-8")
        dialog = (ROOT / "source/blender/blenlib/intern/system_win32.cc").read_text(encoding="utf-8")
        handler = signals[signals.index("windows_exception_handler(EXCEPTION_POINTERS"):]
        self.assertIn("BLI_windows_exception_show_dialog(", handler)
        self.assertIn("sig_cleanup_and_terminate(SIGSEGV)", handler[:handler.index("return EXCEPTION_EXECUTE_HANDLER")])
        self.assertIn("{IDCLOSE, L\"Close\"}", dialog)
        self.assertIn("TaskDialogIndirect(", dialog)
        self.assertEqual(crash_dialog.CLOSED_EXIT, 11)   # SIGSEGV on Windows' CRT.

    def test_every_windows_call_declares_its_types(self):
        """ctypes passes an untyped argument or result as a C int, which cuts a 64-bit window or device handle."""
        import ast
        tree = ast.parse((HERE / "crash_dialog.py").read_text(encoding="utf-8"))
        called, typed = set(), set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in ("user32", "gdi32")):
                called.add(node.func.attr)
            if isinstance(node, ast.Attribute) and node.attr == "argtypes" and isinstance(node.value, ast.Attribute):
                typed.add(node.value.attr)
        self.assertTrue(called)
        self.assertEqual(sorted(called - typed), [])

    def test_the_host_checks_the_dialog_s_own_words(self):
        """What the update gate requires the dialog to say is what the product's dialog says, and the close
        presses the button by the name the product gives it."""
        dialog = (ROOT / "source/blender/blenlib/intern/system_win32.cc").read_text(encoding="utf-8")
        host = (HERE / "update_carry_over_gate.py").read_text(encoding="utf-8")
        for words in ("has stopped working", "crash log can be found"):
            self.assertIn('"%s"' % words, host)
            self.assertIn(words, dialog)
        self.assertIn("$element.Current.Name -ne '%s'", crash_dialog.UIA_SCRIPT)   # Found by its name...
        self.assertNotIn("ControlType]::Button", crash_dialog.UIA_SCRIPT,   # ...never by a control type, which a
                         "a task dialog's buttons need not report Button")  # task dialog's buttons need not report.
        self.assertIn("TryGetCurrentPattern([System.Windows.Automation.InvokePattern]", crash_dialog.UIA_SCRIPT)
        # Without Invoke (DirectUI's panes), a click at the middle of the button's own rectangle, on the dialog only.
        script = crash_dialog.UIA_SCRIPT
        self.assertIn("$named.Current.BoundingRectangle", script)
        self.assertLess(script.index("$hit -ne $handle"), script.index("SetCursorPos($point.X, $point.Y)"))
        self.assertIn("SetProcessDpiAwarenessContext", script)
        for name, ident in crash_dialog.BUTTONS.items():   # Each button a run presses is the dialog's, by name and id.
            self.assertIn('{%s, L"%s"}' % ({8: "IDCLOSE", 4: "IDRETRY"}[ident], name), dialog)
        self.assertIn("InvokePattern", crash_dialog.UIA_SCRIPT)

    def test_hosts_count_only_a_person_s_press(self):
        """Invoke or a click on the button count; the buttons' own message (the fallback) never does."""
        self.assertEqual(crash_dialog.PRESSED_BY, {"ui_automation_invoke", "mouse_click"})
        self.assertIn("in crash_dialog.PRESSED_BY", (HERE / "update_carry_over_gate.py").read_text(encoding="utf-8"))
        seen = dict(self_seen(), closed_by="mouse_click")
        import update_carry_over_gate as gate
        self.assertTrue(all(gate.dialog_checks(seen, 11, "win32").values()))
        self.assertFalse(gate.dialog_checks(dict(seen, closed_by="TDM_CLICK_BUTTON"), 11, "win32")[
            "closed_with_its_close_button"])

    def test_png_writer_makes_a_valid_image(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "a.png"
            crash_dialog._png(path, 2, 1, [bytes([255, 0, 0, 0, 255, 0])])
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
            idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
            self.assertEqual(zlib.decompress(idat), b"\x00" + bytes([255, 0, 0, 0, 255, 0]))

    def test_windowed_crashing_launches_wait_through_it(self):
        """Every host that launches a windowed Angelo (gui_launch.window_flags) meant to crash waits with crash_dialog."""
        users = []
        for path in sorted(HERE.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ANGELO_QA_CRASH_ON_LOAD" in text and "gui_launch.window_flags(" in text:
                users.append(path.name)
                with self.subTest(path.name):
                    self.assertTrue(re.search(r"crash_dialog\.wait\(", text), "wait with crash_dialog.wait")
        self.assertIn("test_blender_settings_import.py", users)
        self.assertIn("update_carry_over_gate.py", users)


if __name__ == "__main__":
    unittest.main()
