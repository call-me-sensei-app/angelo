# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The UI pump outlives an error (``pump_guard``): Blender drops a timer that raises, which stopped the chat
following sign-ins, connections and turns for the rest of the session."""

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import pump_guard  # noqa: E402


class PumpGuardTests(unittest.TestCase):
    def test_an_error_is_reported_once_and_the_timer_runs_again(self):
        calls = []

        @pump_guard.keep_alive(0.25)
        def pump():
            calls.append(1)
            raise KeyError("console answer without 'providers'")

        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(pump(), 0.25)
            self.assertEqual(pump(), 0.25)
        self.assertEqual(len(calls), 2)
        self.assertEqual(out.getvalue().count("failed and will retry"), 1, "Reported once per kind")
        self.assertEqual(pump.__name__, "pump", "The timer keeps its name for register/unregister")

    def test_a_normal_return_passes_through(self):
        self.assertEqual(pump_guard.keep_alive(1)(lambda: 0.1)(), 0.1)
        self.assertIsNone(pump_guard.keep_alive(1)(lambda: None)())

    def test_the_ui_pump_and_the_team_pump_are_guarded(self):
        ui = (ADDON / "ui.py").read_text()
        self.assertIn("@keep_alive(0.1)", ui.split("\ndef pump():", 1)[0][-200:], "The decorator sits on ui.pump")
        team = (ADDON / "team_account_ui.py").read_text()
        self.assertIn('report("team account", error)', team)
        auth = ui.split("changes = _authentication.pump()", 1)[1].split("if _controller:", 1)[0]
        self.assertIn("console.redraw_chats(_controllers.values())", auth, "A CLI sign-in check redraws the chat")


if __name__ == "__main__":
    unittest.main()
