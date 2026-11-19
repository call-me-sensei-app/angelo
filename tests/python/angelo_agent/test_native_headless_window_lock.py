# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One GUI Angelo per state folder while a hidden-window session runs (``headless_window_lock``), on real ``flock``s
and real processes: the lock fails closed and is never stale; the record is the one authority before any signal
(``live_session``); the person's launch reveals a hidden session, never ends it on a timer, and asks the person when
it can't. Contract: doc/angelo/mcp_headless_window.rst, "Hand-over"."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from host_test_policy import environment_skip
from test_native_process_identity import no_process_started

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import headless_window_lock as lock

HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from angelo_agent import headless_window_lock as lock
    real = sys.argv[4:] == ["real"]   # Its true identity, as a launch records it; else a stand-in.
    stand_in = lambda pid: "start-%d" % pid
    assert lock.take(sys.argv[2], sys.argv[3] or None, started_of=lock.started if real else stand_in, app="claude-code")
    print("held", flush=True)
    time.sleep(60)
""")
def YES(pid, start):
    return True


@unittest.skipIf(sys.platform == "win32", environment_skip("no flock on Windows; the hidden session is macOS-only"))
class LockTest(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="shwl-"))
        self.addCleanup(shutil.rmtree, self.state, True)
        self.addCleanup(lock.release)
        self.clock = [0.0]

    def sleep(self, seconds):
        self.clock[0] += seconds

    def holder(self, session="s1", real=False):
        process = subprocess.Popen([sys.executable, "-c", HOLDER, str(ADDONS), str(self.state), session]
                                   + (["real"] if real else []), stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (process.kill(), process.wait()) if process.poll() is None else None)
        self.assertEqual(process.stdout.readline().strip(), "held")
        return process

    def rewrite(self, **fields):
        """What the hidden app does when it becomes the person's window: its record names no session."""
        record = dict(lock.holder(self.state), **fields)
        (self.state / lock.LOCK).write_text(json.dumps(record))

    def hand_over(self, **options):
        options.setdefault("clock", lambda: self.clock[0])
        options.setdefault("sleep", self.sleep)
        options.setdefault("say", lambda text: None)
        options.setdefault("started_of", lambda pid: "me")
        return lock.hand_over(self.state, "/A/Angelo", **options)

    # The lock ------------------------------------------------------------------------------------------------------

    def test_a_held_lock_refuses_another_taker_and_its_end_frees_it(self):
        process = self.holder()
        self.assertFalse(lock.take(self.state, "s2", clock=lambda: self.clock[0], sleep=self.sleep),
                         "fails closed: another Angelo holds the state folder")
        self.assertEqual(lock.holder(self.state)["session"], "s1")
        process.kill()   # A crash: the kernel releases its flock, so no lock is ever stale.
        process.wait()
        self.assertTrue(lock.take(self.state, "s2", started_of=lambda pid: "me"))
        self.assertEqual(lock.holder(self.state), {"pid": os.getpid(), "start": "me", "session": "s2", "app": None})

    def test_the_identity_is_read_before_the_lock_so_the_record_is_never_left_half_written(self):
        order = []

        def started_of(pid):
            order.append("identity")
            return "me"
        real = lock._try
        with patch.object(lock, "_try", side_effect=lambda path: order.append("lock") or real(path)):
            lock.take(self.state, "s1", started_of=started_of)
        self.assertEqual(order, ["identity", "lock"])

    def test_mark_rewrites_and_release_clears_the_record(self):
        self.assertTrue(lock.take(self.state, "s1", started_of=lambda pid: "me", app="codex"))
        lock.mark(None)
        self.assertEqual(lock.holder(self.state)["session"], None, "shown: the person's window")
        self.assertEqual(lock.holder(self.state)["start"], "me")
        lock.release()
        self.assertIsNone(lock.holder(self.state), "a released lock names nobody")

    # The one authority -----------------------------------------------------------------------------------------------

    def test_live_session_needs_the_same_session_the_same_pid_and_a_verified_process(self):
        self.holder()
        pid = lock.holder(self.state)["pid"]
        self.assertTrue(lock.live_session(self.state, "s1", pid, "/A/Angelo", is_verified=YES))
        self.assertFalse(lock.live_session(self.state, "s2", pid, "/A/Angelo", is_verified=YES))
        self.assertFalse(lock.live_session(self.state, "s1", pid + 1, "/A/Angelo", is_verified=YES))
        self.assertFalse(lock.live_session(self.state, "s1", pid, "/A/Angelo", is_verified=lambda p, s: False))
        self.assertFalse(lock.live_session(self.state, None, pid, "/A/Angelo", is_verified=YES))
        self.rewrite(session=None)
        self.assertFalse(lock.live_session(self.state, "s1", pid, "/A/Angelo", is_verified=YES), "shown")
        (self.state / lock.LOCK).write_text("{not json")
        self.assertFalse(lock.live_session(self.state, "s1", pid, "/A/Angelo", is_verified=YES), "unreadable")

    # The person's launch -----------------------------------------------------------------------------------------------

    def test_a_free_lock_is_taken_without_a_signal(self):
        self.assertEqual(self.hand_over(kill=lambda *a: self.fail("signalled")), "free")

    def test_the_persons_other_window_is_left_alone(self):
        self.holder(session="")
        self.assertEqual(self.hand_over(kill=lambda *a: self.fail("signalled"), is_verified=YES), "held")

    def test_the_persons_launch_reveals_the_hidden_session_and_ends_nothing(self):
        self.holder()
        sent = []

        def kill(pid, number):
            sent.append(number)
            self.rewrite(session=None)   # It came up for the person.
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES), "revealed")
        self.assertEqual(sent, [signal.SIGUSR2], "only asked to come up: nothing ended, nothing declined")

    def test_two_launches_the_second_finds_the_persons_window(self):
        self.holder()

        def kill(pid, number):
            self.rewrite(session=None)
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES), "revealed")
        self.assertEqual(self.hand_over(kill=lambda *a: self.fail("signalled"), is_verified=YES), "held",
                         "the revealed window is the person's: theirs, never signalled again")

    def test_a_session_that_doesnt_come_up_is_never_ended_on_a_timer(self):
        self.holder()
        sent, asked = [], []
        self.assertEqual(self.hand_over(kill=lambda pid, number: sent.append(number), is_verified=YES,
                                        asked=lambda app, busy: asked.append((app, busy)) or False), "kept")
        self.assertEqual(sent, [signal.SIGUSR2, signal.SIGUSR1], "Quit withdraws the reveal it asked for")
        self.assertEqual(asked, [("claude-code", True)], "the person decides, naming the app")

    def test_a_session_that_ends_instead_of_coming_up_frees_the_lock_without_a_dialog(self):
        process = self.holder()

        def kill(pid, number):
            os.kill(pid, signal.SIGKILL)   # It was ending anyway: it quits rather than coming up.
            process.wait()
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES,
                                        asked=lambda app, busy: self.fail("asked")), "free")

    def test_end_it_is_graceful_first_and_harder_only_while_it_is_still_that_session(self):
        process = self.holder()
        sent = []

        def kill(pid, number):
            self.assertEqual(pid, process.pid)
            sent.append(number)
            if number == signal.SIGKILL:
                os.kill(pid, signal.SIGKILL)
                process.wait()
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES, asked=lambda app, busy: True), "ended")
        self.assertEqual(sent, [signal.SIGUSR2, signal.SIGTERM, signal.SIGKILL])
        self.assertGreaterEqual(self.clock[0], lock.REVEAL_SECONDS + lock.END_SECONDS, "time to stop and save first")

    def test_a_record_that_changed_since_is_never_signalled_again(self):
        self.holder()
        sent = []

        def kill(pid, number):
            sent.append(number)
            self.rewrite(session="another")   # Another session's record now.
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES, asked=lambda app, busy: True), "kept")
        self.assertEqual(sent, [signal.SIGUSR2], "the record is re-read before each signal")

    def test_a_pid_that_vanished_is_a_lock_to_take_not_a_crash(self):
        process = self.holder()

        def kill(pid, number):
            os.kill(pid, signal.SIGKILL)
            process.wait()
            raise ProcessLookupError
        self.assertEqual(self.hand_over(kill=kill, is_verified=YES), "free")

    def test_an_unverified_record_is_re_read_and_never_acted_on(self):
        self.holder()
        reads, asked = [], []

        def check(pid, start):
            reads.append(pid)
            return False
        self.assertEqual(self.hand_over(kill=lambda *a: self.fail("signalled"), is_verified=check,
                                        asked=lambda app, busy: asked.append(busy) or True), "kept")
        self.assertGreater(len(reads), 1, "re-read while its holder may be writing it")
        self.assertEqual(asked, [False], "the person is only told: nothing ends without a verified pid")

    def test_a_record_that_verifies_on_a_later_read_is_used(self):
        self.holder(session="")
        answers = iter([False, False, True])
        self.assertEqual(self.hand_over(kill=lambda *a: self.fail("signalled"),
                                        is_verified=lambda pid, start: next(answers, True)), "held")

    # The dialog ----------------------------------------------------------------------------------------------------

    def test_ask_escapes_the_apps_name_and_reads_the_persons_button(self):
        seen = []

        def run(command, **options):
            seen.append(command[-1])
            return SimpleNamespace(returncode=0, stdout="button returned:End it\n")
        with patch("angelo_agent.ui_text.iface_", side_effect=lambda text, **fields: text.format(**fields)):
            self.assertTrue(lock.ask('my "app" \\ x', busy=True, run=run))
            self.assertIn('my \\"app\\" \\\\ x', seen[0])
            self.assertFalse(lock.ask("codex", busy=False, run=run), "told only: never an End it")
            self.assertNotIn("End it", seen[1])
            quit_run = lambda command, **options: SimpleNamespace(returncode=0, stdout="button returned:Quit\n")
            self.assertFalse(lock.ask("codex", busy=True, run=quit_run))
            cancelled = lambda command, **options: SimpleNamespace(returncode=1, stdout="")
            self.assertFalse(lock.ask("codex", busy=True, run=cancelled))

    # The identity --------------------------------------------------------------------------------------------------

    def test_the_start_identity_ignores_the_callers_time_zone(self):
        raw = lambda environ: " ".join(subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())],
                                                     capture_output=True, text=True, env=environ).stdout.split())
        utc = raw({"LC_ALL": "C", "TZ": "UTC", "PATH": "/usr/bin:/bin"})
        tokyo = raw({"LC_ALL": "C", "TZ": "Asia/Tokyo", "PATH": "/usr/bin:/bin"})
        if utc == tokyo:
            self.skipTest(environment_skip("this host's ps prints the same start time in every time zone"))
        with patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            self.assertEqual(lock.started(os.getpid()), utc, "a caller in Tokyo reads the UTC identity")

    def test_the_start_identity_ignores_the_callers_locale(self):
        installed = subprocess.run(["locale", "-a"], capture_output=True, text=True).stdout.split()
        german = next((name for name in installed if name.lower().startswith("de_de")), None)
        if german is None:
            self.skipTest(environment_skip("no German locale installed here to read ps in"))
        raw = " ".join(subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True,
                                      text=True, env={"LC_ALL": german, "TZ": "UTC"}).stdout.split())
        with patch.dict(os.environ, {"LC_ALL": german, "LANG": german, "TZ": "UTC"}):
            identity = lock.started(os.getpid())
        self.assertEqual(identity, lock.started(os.getpid()))
        if raw == identity:
            self.skipTest(environment_skip("ps prints this locale's dates as the C locale does"))

    def test_verification_needs_the_same_start_time_and_this_binary(self):
        names = {4242: "Angelo", 77: "python3"}
        binary = "/Apps/Angelo.app/Contents/MacOS/Angelo"
        name_of = names.get
        self.assertTrue(lock.verified(4242, binary, "t0", started_of=lambda pid: "t0", name_of=name_of))
        self.assertFalse(lock.verified(4242, binary, "t0", started_of=lambda pid: "t1", name_of=name_of), "pid reused")
        self.assertFalse(lock.verified(4242, binary, None, started_of=lambda pid: "t0", name_of=name_of), "no start")
        self.assertFalse(lock.verified(77, binary, "t0", started_of=lambda pid: "t0", name_of=name_of), "not Angelo")
        self.assertFalse(lock.verified(5, binary, "t0", started_of=lambda pid: "t0", name_of=name_of), "gone")

    def test_this_processs_start_time_is_read_without_starting_a_process(self):
        with no_process_started(self):
            self.assertTrue(lock.started(os.getpid()))
            self.assertIsNone(lock.started(2 ** 22 + 12345), "no such process")

    # Every launch takes the lock (2.0.1 final 12484412217: the offline GUI fixtures refused its ps) -----------------

    def test_the_persons_launch_starts_no_process_when_the_lock_is_free(self):
        with no_process_started(self):
            self.assertEqual(lock.hand_over(self.state, sys.executable, say=lambda text: None), "free")
        self.assertEqual(lock.holder(self.state)["start"], lock.started(os.getpid()))

    def test_the_persons_launch_starts_no_process_to_find_their_other_window(self):
        """Finding and verifying the holder is the claim; the person's dialog (``ask``) is never reached here. The
        holder runs this interpreter, so its binary is the executable it resolves to (VM CI linux 89edda38180: an
        unresolved python3 link never verified and fell through to the dialog)."""
        self.holder(session="", real=True)
        binary = Path(sys.executable).resolve()
        with no_process_started(self):
            self.assertEqual(lock.hand_over(self.state, binary, say=lambda text: None,
                                            asked=lambda *a, **k: self.fail("the holder did not verify")), "held")

    def test_the_dialog_is_macos_s_and_elsewhere_starts_nothing(self):
        with patch.object(lock.sys, "platform", "linux"), no_process_started(self):
            self.assertFalse(lock.ask("codex", busy=True), "quit this launch: never an End it without a dialog")
            self.assertFalse(lock.ask("codex", busy=False))

    # A record anyone could have written (review S-1) ------------------------------------------------------------------

    def test_a_record_of_any_shape_is_never_verified_and_never_raises(self):
        for pid, start in ((2 ** 40, "t0"), (-1, "t0"), (0, "t0"), (True, "t0"), (1.5, "t0"), ("12", "t0"),
                           (os.getpid(), 5), (os.getpid(), ["t0"]), (os.getpid(), None)):
            with self.subTest(pid=pid, start=start):
                self.assertFalse(lock.verified(pid, sys.executable, start))
        self.assertFalse(lock.verified(os.getpid(), sys.executable, "t0", started_of=lambda pid: 1 / 0),
                         "a reader that fails is never a verified process")

    def test_a_held_lock_with_a_record_of_any_shape_never_stops_a_launch_or_signals(self):
        self.holder(session="s1")
        for record in ({"pid": 2 ** 40, "start": "t0", "session": "s1"}, {"pid": "1", "session": ["s1"]}, [1, 2],
                       {"pid": True, "start": 5}, "not json at all"):
            with self.subTest(record=record):
                (self.state / lock.LOCK).write_text(record if isinstance(record, str) else json.dumps(record))
                outcome = lock.hand_over(self.state, sys.executable, clock=lambda: self.clock[0], sleep=self.sleep,
                                         kill=lambda *a: self.fail("signalled"), asked=lambda *a, **k: False,
                                         say=lambda text: None)
                self.assertEqual(outcome, "kept", "unverified: the person is told and this launch quits")

    def test_a_signal_that_cannot_be_sent_is_not_sent(self):
        for error in (ProcessLookupError(), PermissionError(), OverflowError("signed integer is greater than maximum")):
            def kill(pid, number, error=error):
                raise error
            self.assertFalse(lock._signal(kill, 2 ** 40, signal.SIGUSR2))


class RegisterTest(unittest.TestCase):
    """Where a launch consults the lock: only where a hidden session can exist (``headless_window.supported``)."""

    def test_the_persons_launch_takes_the_lock_only_where_a_hidden_session_can_exist(self):
        import ast
        tree = ast.parse((ADDONS / "angelo_agent/__init__.py").read_text(encoding="utf-8"))
        guards = [ast.unparse(node.test) for node in ast.walk(tree) if isinstance(node, ast.If)
                  and any(isinstance(inner, ast.Attribute) and inner.attr == "hand_over"
                          for statement in node.body for inner in ast.walk(statement))]
        self.assertEqual(guards[-1:], ["headless_window.supported()"], "the innermost guard (ast.walk is outside in)")
        self.assertFalse([guard for guard in guards if "sys.platform" in guard],
                         "Linux and Windows have no hidden session, so no lock to take at launch")


if __name__ == "__main__":
    unittest.main()
