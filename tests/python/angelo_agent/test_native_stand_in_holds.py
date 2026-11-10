# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A moment a window suite must see in the stand-in provider is held until seen, never timed (1.0.8).

The stand-in ``claude``/``codex`` (``fake_provider_cli``) runs in its own process; a ``{"sleep": s}`` round item, a
login that lasts ``ANGELO_FAKE_CLI_LOGIN_SECONDS`` or a model listing delayed by seconds is wall-clock. A suite that
had to observe the state inside that window on its main-thread tick missed it whenever the main thread stalled
longer: the event pump then applied both sides in one pass. test_blender_turn_recovery's "Preparing the next steps"
(a 2.5 s sleep), test_blender_first_run's sign-in in progress (a 1 s login), test_blender_reply_stream's and
test_blender_plan_progress's streamed moments, test_blender_chat_waiting's 22 s measurement window and
test_blender_chat_start_states' models file written before a slow app-server read it are this one class.
Covered: the stand-in's holds (a round item, a login, a model listing, a codex turn) wait until released and say when they wait,
and two lints over the window suites and the helper modules they import: no scripted round pauses by time, and a
suite that waits for a sign-in in progress holds the stand-in's login.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import fake_provider_cli

HERE = Path(__file__).resolve().parent
TIMED_PAUSES = ("claude_delay", "codex_delay")   # The listing delays the stand-in took before its holds.


def suite_sources(folder=HERE):
    """Each window suite's source, with the source of every helper module of ``folder`` it imports (the stand-in
    itself excepted: plan_events' pacing between fragments is where streaming speed lives, not a pause)."""
    import native_suite_manifest as manifest
    found = {}
    for suite in manifest.SUITES:
        if suite.kind == manifest.BACKGROUND:
            continue
        path = folder / (suite.script if suite.script.endswith(".py") else suite.script + ".py")
        found[path.name] = path.read_text(encoding="utf-8")
        for module in set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", found[path.name], re.M)):
            helper = folder / (module + ".py")
            if helper.is_file() and module != "fake_provider_cli" and not module.startswith("test_"):
                found.setdefault(helper.name, helper.read_text(encoding="utf-8"))
    return found


def timed_pauses(source):
    """(line, text) of each scripted pause by time: a ``{"sleep": ...}`` round item or a listing delay."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict) and any(isinstance(key, ast.Constant) and key.value in ("sleep",) + TIMED_PAUSES
                                              for key in node.keys):
            found.append((node.lineno, ast.unparse(node)[:80]))
        if isinstance(node, ast.Call) and any(keyword.arg in TIMED_PAUSES for keyword in node.keywords):
            found.append((node.lineno, ast.unparse(node)[:80]))
    return found


def waits_for_a_sign_in_without_holding_it(source):
    """Whether the suite reads a provider sign-in's flow (``quick_connect.flow``, which runs the stand-in's login) and
    compares a phase with "signing_in" (it waits to see the sign-in in progress), and never sets the login gate."""
    compares = "quick_connect.flow(" in source and any(isinstance(node, ast.Compare) and any(isinstance(part, ast.Constant) and part.value == "signing_in"
                                                          for part in [node.left] + node.comparators)
                   for node in ast.walk(ast.parse(source)))
    return compares and "LOGIN_GATE" not in source


class NoTimedPauseTests(unittest.TestCase):
    def test_no_window_suite_scripts_a_pause_by_time(self):
        found = {name: pauses for name, source in sorted(suite_sources().items())
                 if (pauses := timed_pauses(source))}
        self.assertEqual(found, {}, "a pause a suite must see is fake_provider_cli.hold(folder), released once seen")

    def test_a_suite_waiting_for_a_sign_in_holds_the_login(self):
        found = sorted(name for name, source in suite_sources().items()
                       if name.startswith("test_blender_") and waits_for_a_sign_in_without_holding_it(source))
        self.assertEqual(found, [], "set fake_provider_cli.LOGIN_GATE and release it once the sign-in was seen")

    def test_the_lints_see_the_bare_idioms(self):
        bare = "def rounds():\n    return [stream(event), {'sleep': 2.5}, result(plan)]\n"
        held = "def rounds(folder):\n    return [stream(event), fake_provider_cli.hold(folder), result(plan)]\n"
        delayed = "set_models(codex=TWO, codex_delay=4)\n"
        self.assertEqual([line for line, _ in timed_pauses(bare)], [2])
        self.assertEqual(timed_pauses(held), [])
        self.assertEqual(len(timed_pauses(delayed)), 1)
        racing = ("flow = quick_connect.flow(CLAUDE)\n"
                  "if not wait(phase, lambda: flow and flow['phase'] == 'signing_in'):\n    pass\n")
        own_phase = "if state['phase'] == 'signing_in':\n    pass\n"   # A suite's own phase name: no stand-in login.
        self.assertTrue(waits_for_a_sign_in_without_holding_it(racing))
        self.assertFalse(waits_for_a_sign_in_without_holding_it(
            "os.environ[fake_provider_cli.LOGIN_GATE] = folder\n" + racing))
        self.assertFalse(waits_for_a_sign_in_without_holding_it(own_phase))


class HoldTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def test_a_round_hold_waits_until_released(self):
        folder = fake_provider_cli.fresh(self.root / "hold")
        item = fake_provider_cli.hold(folder)
        self.assertFalse(fake_provider_cli.held(folder))
        # Only a release ends it, so no clock decides "still held" (a main thread stalled past a hold's limit found it
        # over: test_native_held_preparation, CI 41896025168). The test's cleanup releases it whatever happens.
        hold = mock.patch.object(fake_provider_cli, "HOLD_SECONDS", float("inf"))
        hold.start()
        self.addCleanup(hold.stop)
        self.addCleanup(fake_provider_cli.release, folder)
        thread = threading.Thread(target=fake_provider_cli.wait_for_gate, kwargs=item["gate"], daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not fake_provider_cli.held(folder) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(fake_provider_cli.held(folder))
        thread.join(0.2)
        self.assertTrue(thread.is_alive(), "still held: the suite has not released it")
        fake_provider_cli.release(folder)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(fake_provider_cli.held(folder))
        fake_provider_cli.release(folder)   # Nothing waits: nothing happens.

    def test_each_login_is_held_until_released(self):
        gate = fake_provider_cli.fresh(self.root / "login")
        home = self.root / "home"
        environ = dict(os.environ, HOME=str(home), ANGELO_FAKE_CLI_STATE=str(self.root / "state"),
                       ANGELO_FAKE_CLI_LOGIN_SECONDS="600", ANGELO_FAKE_CLI_HOLD_SECONDS="inf",   # Released only.
                       **{fake_provider_cli.LOGIN_GATE: str(gate)})
        environ.pop("CLAUDE_CONFIG_DIR", None)
        for number in range(2):
            process = subprocess.Popen([sys.executable, str(HERE / "fake_provider_cli.py"), "claude", "auth", "login",
                                        "--claudeai"], env=environ, stdout=subprocess.PIPE, text=True)
            self.addCleanup(process.kill)   # A hold only a release ends never outlives a failed test.
            deadline = time.monotonic() + 10
            while not fake_provider_cli.held(gate) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(fake_provider_cli.held(gate), "login %d waits at the gate" % number)
            self.assertIsNone(process.poll(), "and has not signed in: the 600 s login time is not used")
            fake_provider_cli.release(gate)
            self.assertEqual(process.wait(10), 0)
            process.stdout.close()
        self.assertTrue((home / ".claude" / fake_provider_cli.MARKER).is_file())

    def test_a_model_listing_hold_reads_the_list_first(self):
        state = self.root / "state"
        state.mkdir()
        gate = fake_provider_cli.fresh(self.root / "models")
        (state / "models.json").write_text(json.dumps({"codex_hold": str(gate)}))
        with mock.patch.dict(os.environ, {"ANGELO_FAKE_CLI_STATE": str(state)}):
            thread = threading.Thread(target=fake_provider_cli._hold_listing, args=("codex_hold",))
            thread.start()
            deadline = time.monotonic() + 5
            while not fake_provider_cli.held(gate) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(fake_provider_cli.held(gate))
            fake_provider_cli.release(gate)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertIsNone(fake_provider_cli._hold_listing("claude_hold"), "no hold named: no wait")

    def test_a_held_codex_turn_runs_until_released(self):
        """test_blender_goal_gui types a draft while a turn runs: an "ok" turn ends ~40 ms after it starts."""
        state = self.root / "state"
        fake_provider_cli.script_codex_turns(["held", "ok"], state)
        gate = fake_provider_cli.fresh(fake_provider_cli.codex_turn_hold(state))
        environ = dict(os.environ, HOME=str(self.root / "home"), ANGELO_FAKE_CLI_STATE=str(state))
        process = subprocess.Popen([sys.executable, str(HERE / "fake_provider_cli.py"), "codex", "app-server",
                                    "--listen", "stdio://"], env=environ, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        self.addCleanup(process.kill)
        seen = []
        reader = threading.Thread(target=lambda: seen.extend(json.loads(line).get("method") for line in process.stdout))
        reader.start()

        def start(number):
            process.stdin.write(json.dumps({"id": number, "method": "turn/start", "params": {"threadId": "t"}}) + "\n")
            process.stdin.flush()

        def until(condition):
            deadline = time.monotonic() + 10
            while not condition() and time.monotonic() < deadline:
                time.sleep(0.01)
            return condition()

        start(1)
        self.assertTrue(until(lambda: fake_provider_cli.held(gate) and "turn/started" in seen))
        self.assertNotIn("turn/completed", seen, "held: the turn runs until the suite releases it")
        fake_provider_cli.release(gate)
        self.assertTrue(until(lambda: seen.count("turn/completed") == 1))
        start(2)   # The next scripted outcome, "ok": no hold.
        self.assertTrue(until(lambda: seen.count("turn/completed") == 2))
        self.assertFalse(fake_provider_cli.held(gate))
        process.stdin.close()
        self.assertEqual(process.wait(10), 0)
        reader.join(5)
        process.stdout.close()


if __name__ == "__main__":
    unittest.main()
