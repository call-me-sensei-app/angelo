# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hidden-window session, the launcher's side (``headless_window.HiddenWindow``) and its MCP wiring
(``external_mcp.Server``): the real Angelo app on a working copy, its window off screen, refused while the person's
Angelo runs, its life and relayed answers on one pipe. Contract: doc/angelo/mcp_headless_window.rst."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_test_policy import environment_skip
from angelo_agent import headless_window
from angelo_agent import external_mcp_hidden
from angelo_agent.external_mcp_hidden import HIDDEN_NAMES, HiddenTools
from angelo_agent.external_mcp_tools import AGENT_NAMES, AGENT_TOOLS
from angelo_agent.headless_window import HeadlessUnavailable
from angelo_agent.headless_window import (DEFAULT_CAPS, ENV_CAPS, ENV_CLIENT, ENV_FLAG, ENV_PIPE, ENV_SESSION,
                                          ENV_SOURCE, HiddenWindow, caps_from, hidden_environment)

SOURCE_BYTES = b"BLENDER-v502 source scene"


class FakeChild:
    """The started app: alive until it reads the pipe's ``close`` (or is ended); records what the pipe carried."""

    def __init__(self, command, env, pass_fds, quits=True, pid=900001):
        self.command, self.env, self.pass_fds, self.pid = command, env, pass_fds, pid
        self.read = os.dup(int(env[ENV_PIPE]))   # The child's own end, as an inherited fd would be.
        self.code, self.quits, self.lines, self.ended, self.first = None, quits, [], None, None

    def poll(self):
        return self.code

    def _drain(self):
        with os.fdopen(self.read, "r", encoding="utf-8") as pipe:
            lines = [json.loads(line) for line in pipe]
        self.first, self.lines = (lines[0], lines[1:]) if lines else (None, [])

    def wait(self, timeout=None):
        if self.code is None and not self.quits:
            raise subprocess.TimeoutExpired("angelo", timeout)
        if self.code is None:
            self._drain()
            self.code = 0
        return self.code

    def send_signal(self, number):
        import signal
        self.ended = {signal.SIGTERM: "terminate", getattr(signal, "SIGKILL", None): "kill"}.get(number, number)
        self.quits = True
        self.wait()


class Launch:
    """A stand-in ``Popen`` and the endpoint's ``status``: answers as the session once the app ``answers``."""

    def __init__(self, answers=True, quits=True):
        self.children, self.answers, self.quits, self.state = [], answers, quits, None

    def popen(self, command, **options):
        self.options = options
        child = FakeChild(command, options["env"], options["pass_fds"], self.quits, pid=900001 + len(self.children))
        self.children.append(child)
        if self.state is not None:   # The hidden app's lock record: its session and its pid (headless_window_lock).
            self.state.mkdir(parents=True, exist_ok=True)
            (self.state / "gui.lock").write_text(json.dumps({"pid": child.pid, "start": "t0", "app": "claude-code",
                                                              "session": options["env"][ENV_SESSION]}))
        return child

    def status(self):
        child = self.children[-1] if self.children else None
        if child is None or not self.answers or child.poll() is not None:
            return None
        return {"file": "digest", "hidden_session": child.env[ENV_SESSION]}


# The launcher's process control (an inherited pipe, POSIX signals to the app it started) runs on macOS only: off it
# the launcher refuses before anything starts (OffMacTest, which runs everywhere).
POSIX_LAUNCH = environment_skip("the hidden window is macOS-only: its launcher passes a pipe and POSIX signals to the "
                                "app it starts, and refuses on Windows (OffMacTest)")


@unittest.skipIf(os.name == "nt", POSIX_LAUNCH)
class HiddenWindowTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="shw-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "scene.blend"
        self.source.write_bytes(SOURCE_BYTES)
        self.clock = [0.0]
        verified = unittest.mock.patch("angelo_agent.headless_window_lock.verified", return_value=True)
        verified.start()
        self.addCleanup(verified.stop)

    def record(self, **fields):
        path = self.root / "state" / "gui.lock"
        path.write_text(json.dumps(dict(json.loads(path.read_text()), **fields)))

    def window(self, launch, others=False, platform="darwin", environ=None):
        def sleep(seconds):
            self.clock[0] += seconds
        launch.state = self.root / "state"
        window = HiddenWindow("claude-code", self.root / "state", status=launch.status, binary="/Apps/Angelo",
                              environ={"HOME": "/home/person"} if environ is None else environ, platform=platform,
                              popen=launch.popen, clock=lambda: self.clock[0], sleep=sleep,
                              others_running=lambda binary: others)
        self.addCleanup(window.close)
        return window

    def test_starts_the_real_app_on_a_working_copy_with_the_sessions_environment(self):
        launch = Launch()
        window = self.window(launch)
        ready = window.open(str(self.source), {"usd": 2})
        self.assertTrue(ready["hidden"])
        self.assertEqual(ready["caps"], dict(DEFAULT_CAPS, usd=2))
        child = launch.children[0]
        self.assertEqual(child.command[:2], ["/Apps/Angelo", "--disable-autoexec"], "never the file's own Python")
        copy = Path(child.command[-1])
        self.assertNotEqual(copy, self.source, "the app opens a working copy, never the source")
        self.assertEqual(copy.parent, self.source.parent)
        self.assertEqual(copy.read_bytes(), SOURCE_BYTES)
        self.assertEqual(child.env[ENV_FLAG], "1")
        self.assertEqual(child.env[ENV_SOURCE], str(self.source))
        self.assertEqual(child.env[ENV_CLIENT], "claude-code")
        self.assertEqual(json.loads(child.env[ENV_CAPS])["usd"], 2)
        self.assertEqual(child.env["HOME"], "/home/person", "the person's own environment: it is the window's app")
        self.assertEqual(child.pass_fds, (int(child.env[ENV_PIPE]),))
        self.assertTrue(launch.options["start_new_session"])
        self.assertEqual(launch.options["stdin"], subprocess.DEVNULL)
        self.assertTrue(window.opened)

    def test_refused_while_the_persons_angelo_runs_and_nothing_starts(self):
        launch = Launch()
        window = self.window(launch, others=True)
        with self.assertRaises(HeadlessUnavailable) as caught:
            window.open(str(self.source))
        self.assertEqual(caught.exception.code, "window_running")
        self.assertEqual(launch.children, [])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["scene.blend"], "no working copy left")

    def test_macos_only_in_this_build(self):
        with self.assertRaises(HeadlessUnavailable):
            self.window(Launch(), platform="win32").open(str(self.source))

    def test_a_start_that_never_answers_as_this_session_ends_and_leaves_nothing(self):
        launch = Launch(answers=False)
        window = self.window(launch)
        with self.assertRaises(HeadlessUnavailable):
            window.open(str(self.source))
        self.assertFalse(window.opened)
        self.assertIsNotNone(launch.children[0].code)
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.name != "state"), ["scene.blend"])

    def test_the_persons_window_answering_is_not_this_session(self):
        window = self.window(Launch())
        self.assertFalse(window.ours({"file": "x"}), "the person's window has no hidden session")
        self.assertFalse(window.ours({"hidden_session": None}))
        self.assertFalse(window.ours(None))

    def test_close_travels_on_the_pipe(self):
        launch = Launch()
        window = self.window(launch)
        window.open(str(self.source))
        window.close()
        self.assertEqual(launch.children[0].first, {"session": launch.children[0].env[ENV_SESSION]},
                         "the pipe's first line names the session")
        self.assertEqual(launch.children[0].lines, [{"close": True}])
        self.assertFalse(window.opened)

    def test_the_app_can_only_decline_and_only_a_card_it_read(self):
        launch = Launch()
        window = self.window(launch)
        with self.assertRaises(HeadlessUnavailable):
            window.decline("a" * 16)   # Nothing open.
        window.open(str(self.source))
        for card in ("A" * 16, "a" * 15, None, "g" * 16):
            with self.assertRaises(ValueError):
                window.decline(card)
        window.decline("0123456789abcdef")
        window.close()
        self.assertEqual(launch.children[0].lines, [{"decline": "0123456789abcdef"}, {"close": True}])
        self.assertFalse(hasattr(window, "answer"), "no approve travels to the app")

    def test_the_time_cap_has_a_timer_of_its_own(self):
        window = self.window(Launch())
        window.open(str(self.source), {"minutes": 2})
        self.assertTrue(window.timer.is_alive())
        self.assertEqual(window.timer.interval, 2 * 60 + headless_window.QUIT_SECONDS)
        window.close()
        window.timer.join(2)  # window: a cancelled threading.Timer ends at once
        self.assertFalse(window.timer.is_alive(), "cancelled with the session")

    def test_show_hands_the_app_to_the_person_and_the_launcher_lets_it_go(self):
        launch = Launch(quits=False)
        window = self.window(launch)
        with self.assertRaises(HeadlessUnavailable):
            window.show()   # Nothing open.
        window.open(str(self.source))
        copy = Path(launch.children[0].command[-1])
        self.assertEqual(window.show(), self.source)
        self.assertFalse(window.opened)
        window.close()   # The launcher's exit: never ends the person's window nor removes its file.
        self.assertIsNone(launch.children[0].ended)
        self.assertTrue(copy.exists())
        with self.assertRaises(HeadlessUnavailable) as caught:
            window.open(str(self.source))   # The person's window now: one Angelo on their state folder at a time.
        self.assertEqual(caught.exception.code, "window_running")
        self.assertEqual(len(launch.children), 1)
        launch.children[0].quits = True
        launch.children[0].wait()
        self.assertEqual(launch.children[0].lines, [{"show": True}])

    def test_a_window_the_person_took_over_is_never_ended_by_the_launcher(self):
        launch = Launch(quits=False)
        window = self.window(launch)
        window.open(str(self.source))
        copy = Path(launch.children[0].command[-1])
        self.record(session=None)   # Its record now names no session: the person's window.
        window.status = lambda: None   # And its endpoint doesn't answer (busy): nothing about it is a reason.
        window.close()
        self.assertIsNone(launch.children[0].ended, "never signalled")
        self.assertTrue(copy.exists(), "its file is the person's")
        with self.assertRaises(HeadlessUnavailable):
            window.open(str(self.source))   # And it still counts as an Angelo running.
        launch.children[0].quits = True
        launch.children[0].wait()

    def test_a_record_that_cant_be_read_or_verified_is_never_a_reason_to_signal(self):
        from unittest.mock import patch
        for spoil in ("unreadable", "unverified", "another session", "another pid"):
            launch = Launch(quits=False)
            window = self.window(launch)
            window.open(str(self.source))
            path = self.root / "state" / "gui.lock"
            if spoil == "unreadable":
                path.write_text("{")
            elif spoil == "another session":
                self.record(session="f" * 32)
            elif spoil == "another pid":
                self.record(pid=1)
            with patch("angelo_agent.headless_window_lock.verified", return_value=spoil != "unverified"):
                window.close()
            self.assertIsNone(launch.children[0].ended, spoil)
            launch.children[0].quits = True
            launch.children[0].wait()

    def test_a_hung_session_is_ended_only_while_its_record_still_names_it(self):
        launch = Launch(quits=False)
        window = self.window(launch)
        window.open(str(self.source))
        window.close()
        self.assertEqual(launch.children[0].ended, "terminate", "its own record, verified: a hung session ends")

    def test_a_second_close_never_discards_under_a_running_app(self):
        launch = Launch(quits=False)
        window = self.window(launch)
        window.open(str(self.source))
        copy = Path(launch.children[0].command[-1])
        first = (window.child, window.pipe)
        window.child = None   # As while another close() (the cap's timer) waits for the app.
        window.close()
        self.assertTrue(copy.exists(), "the first close() owns the end and the copy")
        window.child, window.pipe = first
        launch.children[0].quits = True
        window.close()

    def test_a_read_only_folder_is_refused_before_anything_starts(self):
        from unittest.mock import patch
        from angelo_agent import hidden_copy
        launch = Launch()
        with patch.object(hidden_copy, "claim", return_value=(None, None)):
            with self.assertRaises(HeadlessUnavailable) as caught:
                self.window(launch).open(str(self.source))
        self.assertEqual(caught.exception.code, "read_only")
        self.assertEqual(launch.children, [])

    def test_past_its_time_cap_the_session_is_overdue_for_the_launcher(self):
        window = self.window(Launch())
        window.open(str(self.source), {"minutes": 1})
        self.assertFalse(window.overdue())
        self.clock[0] += 60 + headless_window.QUIT_SECONDS + 1
        self.assertTrue(window.overdue())

    def test_the_same_file_reuses_the_session_and_another_file_replaces_it(self):
        launch = Launch()
        window = self.window(launch)
        window.open(str(self.source))
        self.assertTrue(window.open(str(self.source))["reused"])
        other = self.root / "other.blend"
        other.write_bytes(SOURCE_BYTES)
        window.open(str(other))
        self.assertEqual(len(launch.children), 2)
        self.assertEqual(launch.children[0].lines[-1], {"close": True})


class SessionFactsTest(unittest.TestCase):
    def test_caps_are_only_ever_narrowed(self):
        self.assertEqual(caps_from(None), DEFAULT_CAPS)
        self.assertEqual(caps_from({"minutes": 5, "usd": 1.5, "tokens": 1000}),
                         {"minutes": 5, "usd": 1.5, "tokens": 1000})
        self.assertEqual(caps_from({"minutes": 600, "usd": 1e9}), DEFAULT_CAPS, "never widened past the defaults")
        self.assertEqual(caps_from({"minutes": -1, "usd": True, "tokens": "9"}), DEFAULT_CAPS)

    def test_the_environment_adds_session_facts_and_never_a_credential(self):
        base = {"HOME": "/h"}
        environment = hidden_environment(base, 7, "s" * 32, "/f/a.blend", DEFAULT_CAPS, "codex")
        self.assertEqual(base, {"HOME": "/h"}, "the launcher's own environment is unchanged")
        self.assertEqual(set(environment) - set(base),
                         {ENV_FLAG, ENV_PIPE, ENV_SESSION, ENV_SOURCE, ENV_CAPS, ENV_CLIENT})
        self.assertFalse(any("KEY" in name or "TOKEN" in name for name in environment))

    def test_another_angelo_that_cannot_be_listed_counts_as_running(self):
        from unittest.mock import patch
        from angelo_agent import carry_over_processes
        with patch.object(carry_over_processes, "process_table", side_effect=OSError("ps")):
            self.assertTrue(headless_window._others_running("/Apps/Angelo"))


class FakeHidden:
    def __init__(self, opened=False, overdue=False, refuse=None):
        self.opened, self._overdue, self.refuse, self.calls = opened, overdue, refuse, []
        self.source = Path("/f/scene.blend")

    def open(self, path, caps):
        self.calls.append(("open", path, caps))
        if self.refuse:
            raise HeadlessUnavailable("refused here", code=self.refuse)
        self.opened = True
        return {"ok": True, "file": path, "hidden": True, "caps": caps}

    def show(self):
        self.calls.append(("show",))
        self.opened = False
        return self.source

    def close(self):
        self.calls.append(("close",))
        self.opened = False

    def overdue(self):
        return self._overdue


class Launcher(HiddenTools):
    """The launcher's hidden-session half (``external_mcp_hidden.HiddenTools``) with stand-ins for the server."""

    def __init__(self, hidden):
        self._hidden, self.client, self.reads = hidden, "claude-code", []
        self.state = Path(tempfile.mkdtemp(prefix="shwt-"))


    def agent_request(self, args, wait=0.0):
        """The read after a decline: the card is gone once the app applied it."""
        self.reads.append(args)
        return {"code": "ok", "result": {"turn": "idle", "approval": None}}


class LauncherWiringTest(unittest.TestCase):
    def launcher(self, hidden, connected=True):
        """A launcher for this app, connected or not (a stand-in key: Windows refuses a real one until its named
        pipe, external_mcp's APP_KEYS, and these tests are about the hidden-session tools, not keys)."""
        launcher = Launcher(hidden)
        self.addCleanup(shutil.rmtree, launcher.state, True)
        keyed = unittest.mock.patch("angelo_agent.external_clients.AppRegistry.key",
                                    return_value="k" * 32 if connected else None)
        keyed.start()
        self.addCleanup(keyed.stop)
        return launcher

    def test_approve_answers_needs_person_and_decline_goes_to_the_hidden_app(self):
        hidden = FakeHidden(opened=True)
        declined = []
        hidden.decline = declined.append
        launcher = self.launcher(hidden)
        refused = launcher.hidden_tool("angelo_answer_approval", {"card": "a" * 16, "answer": "approve"})
        self.assertEqual(refused["structuredContent"]["code"], "needs_person")
        self.assertIn("angelo_show", refused["structuredContent"]["message"])
        self.assertEqual(declined, [])
        result = launcher.hidden_tool("angelo_answer_approval", {"card": "a" * 16, "answer": "decline"})
        self.assertTrue(result["structuredContent"]["declined"], "read back: the card is gone")
        self.assertEqual(declined, ["a" * 16])
        closed = self.launcher(FakeHidden())
        self.assertTrue(closed.hidden_tool("angelo_answer_approval", {"card": "a" * 16, "answer": "decline"})["isError"])

    def test_a_decline_no_read_confirmed_is_unknown_not_untrue(self):
        hidden = FakeHidden(opened=True)
        hidden.decline = lambda card: None
        launcher = self.launcher(hidden)
        launcher.agent_request = lambda args, wait=0.0: {"code": "busy", "message": "Too many calls"}
        with unittest.mock.patch("time.sleep"):
            result = launcher.hidden_tool("angelo_answer_approval", {"card": "a" * 16, "answer": "decline"})
        self.assertEqual(result["structuredContent"]["code"], "unknown")

    def test_open_says_what_to_do_next(self):
        launcher = self.launcher(FakeHidden())
        result = launcher.hidden_tool("angelo_open_file", {"file": "/f/scene.blend", "caps": {"usd": 1}})
        self.assertFalse(result["isError"])
        self.assertEqual(launcher._hidden.calls, [("open", "/f/scene.blend", {"usd": 1})])
        self.assertIn("angelo_show", result["structuredContent"]["next"])

    def test_an_app_not_connected_or_a_kill_switch_starts_nothing(self):
        launcher = self.launcher(FakeHidden(), connected=False)
        self.assertEqual(launcher.hidden_tool("angelo_open_file", {"file": "/f/s.blend"})["structuredContent"]["code"],
                         "not_connected")
        launcher = self.launcher(FakeHidden())
        with unittest.mock.patch.object(external_mcp_hidden, "access_refusal", return_value="off here"):
            result = launcher.hidden_tool("angelo_open_file", {"file": "/f/s.blend"})
        self.assertEqual(result["structuredContent"]["code"], "refused")
        self.assertEqual(launcher._hidden.calls, [])

    def test_a_refused_open_carries_its_code(self):
        result = self.launcher(FakeHidden(refuse="window_running")).hidden_tool("angelo_open_file",
                                                                              {"file": "/f/scene.blend"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "window_running")

    def test_show_only_when_hidden_and_then_the_window_is_the_persons(self):
        launcher = self.launcher(FakeHidden())
        self.assertTrue(launcher.hidden_tool("angelo_show", {})["isError"], "nothing hidden: nothing to show")
        launcher._hidden.opened = True
        result = launcher.hidden_tool("angelo_show", {})
        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["shown"])
        self.assertIn("person's window", result["structuredContent"]["next"])

    def test_the_launchers_end_ends_the_hidden_session(self):
        launcher = self.launcher(FakeHidden(opened=True))
        launcher.end_sessions()
        self.assertEqual(launcher._hidden.calls, [("close",)])

    def test_a_session_past_its_time_cap_is_ended_by_the_launcher(self):
        launcher = self.launcher(FakeHidden(opened=True, overdue=True))
        _ = launcher.hidden   # Every use of the session checks its cap.
        self.assertEqual(launcher._hidden.calls, [("close",)])

    def test_file_status_and_close_name_the_hidden_sessions_file(self):
        # The tools name the session's file as its OS spells it (str of its Path): both spellings checked on every
        # host, so Windows CI's "\\f\\scene.blend" is covered on a Mac too (2.0.1 CI win-4 red at f9f749b4af7).
        for source in (PurePosixPath("/f/scene.blend"), PureWindowsPath("/f/scene.blend")):
            with self.subTest(spelling=type(source).__name__):
                hidden = FakeHidden(opened=True)
                hidden.source = source
                launcher = self.launcher(hidden)
                expected = str(source)   # What the tool returns: the OS's own spelling of the file.
                self.assertEqual(launcher.hidden_tool("angelo_file_status", {})["structuredContent"],
                                 {"file": expected, "hidden": True})
                self.assertEqual(launcher.hidden_tool("angelo_close_file", {})["structuredContent"]["closed"],
                                 expected)
                self.assertEqual(launcher._hidden.calls, [("close",)])


class OffMacTest(unittest.TestCase):
    """Off macOS nothing of a hidden session starts: the launcher refuses before anything, and the add-on never
    registers one even when its environment says so (a forged or inherited environment)."""

    def test_only_macos_is_supported(self):
        self.assertTrue(headless_window.supported("darwin"))
        for platform in ("win32", "linux", "cygwin"):
            self.assertFalse(headless_window.supported(platform), platform)

    def test_the_launcher_refuses_off_macos_before_anything_starts(self):
        started = []
        window = HiddenWindow("claude-code", tempfile.gettempdir(), binary="Angelo", platform="win32",
                              popen=lambda *a, **k: started.append(a), others_running=lambda binary: False)
        with self.assertRaises(HeadlessUnavailable):
            window.open(str(Path(tempfile.gettempdir()) / "scene.blend"))
        self.assertEqual(started, [])
        self.assertFalse(window.opened)

    def test_the_add_on_registers_a_hidden_session_only_where_it_is_supported(self):
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/__init__.py").read_text(
            encoding="utf-8")
        self.assertIn("if headless_window_app.active() and headless_window.supported():", source)


class ToolContractTest(unittest.TestCase):
    def test_the_hidden_tools_are_agent_tools_and_the_app_can_never_approve(self):
        self.assertTrue(HIDDEN_NAMES <= AGENT_NAMES)
        answer = next(tool for tool in AGENT_TOOLS if tool["name"] == "angelo_answer_approval")
        self.assertIn("You can only decline", answer["description"])

    def test_open_file_takes_a_file_and_caps(self):
        tool = next(tool for tool in AGENT_TOOLS if tool["name"] == "angelo_open_file")
        properties = tool["inputSchema"]["properties"]
        self.assertEqual(set(properties), {"file", "caps"})
        self.assertEqual(set(properties["caps"]["properties"]), {"minutes", "usd", "tokens"})


if __name__ == "__main__":
    unittest.main()
