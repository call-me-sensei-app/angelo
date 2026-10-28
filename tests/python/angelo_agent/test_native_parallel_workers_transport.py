# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker Blenders without Blender: exact values across the connection, the connection itself and the launcher.

``worker_values`` keeps every precondition's type and equality, ``worker_channel`` carries numbered frames over a
real Unix socket pair (and builds the Windows pipes only with their security), and ``worker_process`` starts a
worker with exactly its documented command and environment. The in-Blender proof is
``test_blender_parallel_workers.py``.
"""

import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import worker_channel, worker_process, worker_values as values  # noqa: E402
from angelo_agent.program_checks import resolve_obligations  # noqa: E402
from angelo_agent.program_context import ProgramBinding  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402


def a_program(name="Seat"):
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [], "operations": [
        {"kind": "create_primitive", "target": None, "parameters": {"primitive": "CUBE", "name": name, "alias": "s"}}]})


def a_binding(program=None, frame=(12, 0.25)):
    obligations = resolve_obligations(program or a_program())
    return ProgramBinding(3, 7, frame, 140223, (("scn:" + "a" * 32 + ":5", "f" * 64, "e" * 64),), None,
                          obligations.checks_digest, obligations)


class ValueTests(unittest.TestCase):
    def round_trip(self, value):
        return values.decode(values.encode(value))

    def test_preconditions_keep_their_exact_types_and_equality(self):
        binding = a_binding()
        again = self.round_trip(binding)
        self.assertIs(type(again), ProgramBinding)
        self.assertEqual(again, binding)
        self.assertIs(type(again.obligations), type(binding.obligations))
        again.obligations.assert_integrity(a_program())
        self.assertNotEqual(self.round_trip(a_binding(frame=(13, 0.25))), binding)
        for value in ((140223, (1.0, -0.0, 2.5e-17)), None, (None, (5, (0.1, 0.2, 0.3))), True, "x", b"\x00\xff"):
            with self.subTest(value=value):
                self.assertEqual(self.round_trip(value), value)
                self.assertIs(type(self.round_trip(value)), type(value))
        # A tuple never comes back as a list (the controller compares with ==).
        self.assertNotEqual(self.round_trip((1, 2)), [1, 2])
        self.assertEqual(repr(self.round_trip(0.1 + 0.2)), repr(0.1 + 0.2))

    def test_unknown_or_unfinite_values_never_cross(self):
        for value in (object(), {1, 2}, float("nan"), float("inf"), {1: "key not text"}, SimpleNamespace()):
            with self.subTest(value=value), self.assertRaises(TypeError):
                values.encode(value)
        for record in ({"t": 1}, {"x": []}, {"f": 1.5}, {"t": [], "f": "1"}, {"fo": {"encoded": {"b": "AA=="}}}):
            with self.subTest(record=record), self.assertRaises((TypeError, ValueError)):
                values.decode(record)
        nested = ()
        for _ in range(40):
            nested = (nested,)
        with self.assertRaises(TypeError):
            values.encode(nested)

    def test_errors_keep_their_code_details_and_certainty(self):
        error = values.decode_error(values.encode_error(RefusalError("gone", code="STALE_REVISION",
                                                                     details={"handle": "h"})))
        self.assertIsInstance(error, RefusalError)
        self.assertEqual((str(error), error.code, error.details), ("gone", "STALE_REVISION", {"handle": "h"}))
        from angelo_agent.program_jobs import WriterBusyError
        self.assertIsInstance(values.decode_error(values.encode_error(WriterBusyError("busy"))), WriterBusyError)
        plain = ValueError("bad")
        plain.partial_changes_possible = False
        decoded = values.decode_error(values.encode_error(plain))
        self.assertIs(type(decoded), ValueError)
        self.assertIs(decoded.partial_changes_possible, False)
        self.assertIs(type(values.decode_error({"type": "SystemExit", "message": "x"})), RuntimeError)
        self.assertEqual(values.decode_error({"type": "RuntimeError", "code": "MADE_UP"}).__class__, RuntimeError)
        self.assertIs(type(values.decode_error(None)), RuntimeError)

    def test_program_evidence_holds_only_for_the_approved_program(self):
        from angelo_agent.program_completion import _CapturedProgram
        program = a_program()
        captured = _CapturedProgram(resolve_obligations(program), "d" * 64, "committed")
        record = values.encode_capture(captured)
        again = values.decode_capture(record, program)
        self.assertEqual((again.obligations, again.results_digest, again.status),
                         (captured.obligations, "d" * 64, "committed"))
        self.assertIsNone(values.decode_capture(record, a_program("Other")))   # Evidence for another program.
        self.assertIsNone(values.decode_capture(dict(record, status=5), program))
        self.assertIsNone(values.decode_capture({"obligations": values.encode((1,))}, program))
        self.assertIsNone(values.encode_capture(None))


POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "Workers use a Unix socket pair only on macOS and Linux")


class ChannelTests(unittest.TestCase):
    def pair(self):
        listener = worker_channel.SocketPairListener()
        # The worker's end, here in this process: its own descriptor, as a started worker has its inherited copy.
        theirs = worker_channel.connect("fd:%d" % os.dup(listener.pass_fds()[0]))
        listener.started()
        ours = listener.accept(process_id=None)
        return ours, theirs

    @POSIX_ONLY
    def test_numbered_requests_get_their_own_answers(self):
        ours, theirs = self.pair()

        def worker():
            theirs.send({"seq": 0, "ready": {"ok": True}})
            for _ in range(3):
                frame = theirs.receive()
                theirs.send({"seq": frame["seq"], "response": {"echo": frame["request"]["n"]}})
            theirs.close()
        threading.Thread(target=worker, daemon=True).start()
        client = worker_channel.Client(ours)
        self.assertEqual(client.wait_ready(5), {"ok": True})
        self.assertEqual([client.request({"n": n}, 5)["echo"] for n in range(3)], [0, 1, 2])
        self.assertTrue(client.ended.wait(5))
        self.assertIsNone(client.request({"n": 9}, 5))   # Ended: unknown, never an answer.
        client.close()

    @POSIX_ONLY
    def test_an_unanswered_request_times_out_and_a_late_answer_is_dropped(self):
        ours, theirs = self.pair()
        client = worker_channel.Client(ours)
        self.assertIsNone(client.request({"n": 1}, 0.2))
        frame = theirs.receive()
        theirs.send({"seq": frame["seq"], "response": {"late": True}})
        theirs.send({"seq": 0, "ready": {"ok": True}})
        self.assertEqual(client.wait_ready(5), {"ok": True})
        self.assertFalse(client._waiting)
        client.close()
        theirs.close()

    @POSIX_ONLY
    def test_frames_past_their_limits_are_refused(self):
        ours, theirs = self.pair()
        from angelo_agent import external_cli_protocol as protocol
        with self.assertRaises(protocol.FrameError):
            ours.send({"blob": "x" * (worker_channel.REQUEST_LIMIT + 1)})
        ours.close()
        theirs.close()

    @POSIX_ONLY
    def test_the_pair_has_no_name_and_only_the_worker_gets_its_end(self):
        listener = worker_channel.SocketPairListener()
        ours, theirs = listener._ours, listener._theirs
        self.assertEqual(ours.family, socket.AF_UNIX)
        self.assertEqual((ours.getsockname(), theirs.getsockname()), ("", ""))
        # Neither is inheritable: pass_fds gives the worker its end, and no other child ever gets a copy.
        self.assertFalse(ours.get_inheritable() or theirs.get_inheritable())
        self.assertEqual(listener.pass_fds(), (theirs.fileno(),))
        listener.close()

    @POSIX_ONLY
    def test_a_worker_that_stops_reading_cannot_block_a_send(self):
        ours, theirs = self.pair()   # Nobody reads ``theirs``: the socket buffer fills.
        client = worker_channel.Client(ours)
        import time
        started = time.monotonic()
        answers = [client.request({"blob": "x" * 900000}, 0.5) for _ in range(4)]   # Each send bounded by 0.5 s.
        self.assertEqual(answers, [None] * 4)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(ours.closed)   # The stuck send ended the connection, which unblocked it.
        theirs.close()

    def test_a_short_write_never_cuts_a_frame(self):
        from angelo_agent.headless_pipe import Writer

        class Trickle:
            def __init__(self):
                self.data = bytearray()

            def write(self, chunk):
                piece = bytes(chunk[:7])
                self.data += piece
                return len(piece)

            def flush(self):
                pass
        stream = Trickle()
        Writer(stream, 1 << 20).send({"text": "a" * 1000})
        from angelo_agent import external_cli_protocol as protocol
        self.assertEqual(bytes(stream.data), protocol.encode_frame({"text": "a" * 1000}, 1 << 20))

    def test_only_known_channel_forms_connect(self):
        for argument in ("tcp:127.0.0.1:80", "fd:", "fd:3x", "pipe:\\\\.\\pipe\\other:12", "pipe:" + "x" * 8):
            with self.subTest(argument=argument), self.assertRaises((worker_channel.ChannelError, ValueError)):
                worker_channel.connect(argument)

    def test_windows_pipes_are_made_with_their_security_and_checked_by_process(self):
        made, accepted, connected = [], [], []
        win32 = SimpleNamespace(
            create_server=lambda name, inbound: made.append((name, inbound)) or SimpleNamespace(name=name),
            accept=lambda server, pid, timeout, alive=None: accepted.append((server.name, pid)),
            stream=lambda pipe, mode: SimpleNamespace(mode=mode, close=lambda: None),
            close_handle=lambda pipe: None,
            connect=lambda name, pid, write: connected.append((name, pid, write)) or SimpleNamespace(name=name))
        listener = worker_channel.PipeListener(win32=win32)
        self.assertTrue(listener.name.startswith(worker_channel.PIPE_PREFIX))
        self.assertEqual(len(listener.name) - len(worker_channel.PIPE_PREFIX), 32)   # 128 random bits.
        self.assertEqual(made, [(listener.name + "-up", True), (listener.name + "-down", False)])
        self.assertEqual(listener.child_argument(), "pipe:%s:%d" % (listener.name, os.getpid()))
        listener.accept(4242)
        self.assertEqual(accepted, [(listener.name + "-up", 4242), (listener.name + "-down", 4242)])
        worker_channel.connect_pipe(listener.child_argument(), win32=win32)
        self.assertEqual(connected, [(listener.name + "-up", os.getpid(), True),
                                     (listener.name + "-down", os.getpid(), False)])
        with self.assertRaises(worker_channel.ChannelError):
            worker_channel.connect_pipe("pipe:\\\\.\\pipe\\angelo-cli:12", win32=win32)

    def test_the_pipe_dacl_grants_only_the_user(self):
        from angelo_agent.worker_pipes_windows import ChannelError, user_only_sddl
        self.assertEqual(user_only_sddl("S-1-5-21-1-2-3-1001"), "D:P(A;;GA;;;S-1-5-21-1-2-3-1001)")
        for sid in ("S-1-5-21-1)(A;;GA;;;WD", "WD", "S-1-1-0;", ""):
            with self.subTest(sid=sid), self.assertRaises(ChannelError):
                user_only_sddl(sid)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        # Every worker a test starts joins the process-wide set of live workers, each a place in use under the
        # worker limit (worker_places), until its process is seen to exit; a fresh set per test keeps the ones
        # started here from filling the places of every later test file.
        patcher = patch.object(worker_process, "_live", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_command_is_a_window_free_factory_worker(self):
        command = worker_process.worker_command("/app/Angelo", "/s/angelo_headless.py", "/w/part.blend", "fd:7",
                                                "/tmp/angelo-worker-x")
        self.assertEqual(command, ["/app/Angelo", "--background", "--factory-startup", "--disable-autoexec",
                                   "--offline-mode", "--python-exit-code", "1", "--python", "/s/angelo_headless.py",
                                   "--", "--worker", "--channel", "fd:7", "--file", "/w/part.blend",
                                   "--private", "/tmp/angelo-worker-x"])

    def test_the_environment_keeps_only_what_blender_needs(self):
        environ = {"PATH": "/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "secret", "PYTHONPATH": "/evil",
                   "BLENDER_USER_SCRIPTS": "/evil", "LC_ALL": "C", "SystemRoot": "C:\\Windows", "TEMP": "C:\\t"}
        posix = worker_process.worker_environment(environ, "/p", platform="darwin")
        self.assertEqual(set(posix) - {"PATH", "HOME", "LC_ALL"}, {
            "ANGELO_HEADLESS", "ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "ANGELO_DISABLE_DEFAULT_CHAT",
            "ANGELO_DISABLE_LOCAL_CLI", "ANGELO_DISABLE_KEYCHAIN", "ANGELO_WORKER_CHAT_HOST", "ANGELO_PARALLEL_WORKER"})
        self.assertEqual(posix["ANGELO_WORKER_CHAT_HOST"], "1")   # The worker hosts its chat as Angelo decides.
        self.assertEqual(posix["ANGELO_PARALLEL_WORKER"], "1")    # Its chats are workers' own (parallel_access).
        lanes = worker_process.worker_environment(dict(environ, ANGELO_WORKER_CHAT_HOST="0"), "/p", platform="darwin")
        self.assertEqual(lanes["ANGELO_WORKER_CHAT_HOST"], "0")
        self.assertNotIn("ANGELO_PARALLEL_WORKER", lanes)   # The lanes' worker hosts no chat: unchanged.
        self.assertEqual(posix["ANGELO_STATE_DIR"], str(Path("/p") / worker_process.STATE))
        self.assertNotIn("ANGELO_QA_GPU_PROBE_CRASH", posix)
        qa = worker_process.worker_environment(dict(environ, ANGELO_QA_GPU_PROBE_CRASH="1"), "/p", platform="darwin")
        self.assertEqual(qa["ANGELO_QA_GPU_PROBE_CRASH"], "1")   # A suite's switch reaches its worker.
        windows = worker_process.worker_environment(environ, "/p", platform="win32")
        self.assertEqual((windows["SYSTEMROOT"], windows["TEMP"]), ("C:\\Windows", "C:\\t"))
        for kept in (posix, windows):
            self.assertFalse({"ANTHROPIC_API_KEY", "PYTHONPATH", "BLENDER_USER_SCRIPTS"} & set(kept))

    def test_only_a_bounded_qa_process_owner_reaches_worker_sessions(self):
        for owner in ("a" * 32, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 31 + "\n", None, 3):
            for platform in ("darwin", "win32"):
                with self.subTest(owner=owner, platform=platform):
                    kept = worker_process.worker_environment({"ANGELO_QA_PROCESS_OWNER": owner}, "/p", platform)
                    self.assertEqual(kept.get("ANGELO_QA_PROCESS_OWNER"), owner if owner == "a" * 32 else None)

    def fake_listener(self, answer):
        ours, theirs = ChannelTests.pair(self)

        def worker():
            if answer is not None:
                theirs.send({"seq": 0, "ready": answer})
            if not (answer or {}).get("ok"):
                theirs.close()   # A worker that failed ends; a ready one stays connected until its stop.
            else:
                self.addCleanup(theirs.close)
        thread = threading.Thread(target=worker, daemon=True)
        return SimpleNamespace(child_argument=lambda: "fd:99", pass_fds=lambda: (), started=thread.start,
                               accept=lambda pid, alive=None: ours, close=ours.close)

    def popen(self, started):
        def popen(command, **options):
            started.append((command, options))
            return SimpleNamespace(pid=4321, poll=lambda: None, wait=lambda timeout=None: 0, kill=lambda: None)
        return popen

    @POSIX_ONLY
    def test_start_waits_for_ready_and_stop_leaves_nothing(self):
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temporary, True)
        started = []
        worker = worker_process.WorkerProcess("/w/part.blend", binary="/app/Angelo", script="/s/h.py",
                                              environ={"PATH": "/bin"}, platform="darwin",
                                              popen=self.popen(started), temporary=temporary,
                                              listener=lambda: self.fake_listener({"ok": True, "pid": 4321}))
        self.assertEqual(worker.start(5)["pid"], 4321)
        command, options = started[0]
        self.assertEqual(command[command.index("--private") + 1], str(worker.private))
        self.assertEqual(oct(os.stat(worker.private).st_mode & 0o777), "0o700")
        self.assertTrue(options["start_new_session"] and options["close_fds"])
        self.assertGreaterEqual(worker.ready_seconds, 0)
        worker.stop()
        self.assertFalse(Path(worker.private).exists())

    @POSIX_ONLY
    def test_a_worker_that_fails_names_why_and_leaves_nothing(self):
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temporary, True)
        for answer, text in (({"ok": False, "error": "RuntimeError: no part"}, "no part"), (None, "never said ready")):
            with self.subTest(answer=answer):
                worker = worker_process.WorkerProcess("/w/part.blend", binary="/a", script="/s", environ={},
                                                      platform="darwin", popen=self.popen([]), temporary=temporary,
                                                      listener=lambda: self.fake_listener(answer))
                with self.assertRaisesRegex(worker_process.WorkerUnavailable, text):
                    worker.start(5)
                self.assertFalse(os.listdir(temporary))

    def test_requests_to_an_ended_worker_fail_at_once(self):
        worker = worker_process.WorkerProcess("/w/part.blend")
        with self.assertRaisesRegex(worker_process.WorkerUnavailable, "ended"):
            worker.request("hello", 5)


if __name__ == "__main__":
    unittest.main()
