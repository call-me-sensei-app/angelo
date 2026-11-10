# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One thread that can't start never leaves its runner "started" (doc/angelo/agent_integration.rst, "Background
threads"; ``thread_start``).

``Thread.start`` raises here as it does in a process with no thread to give (``RuntimeError: can't start new
thread``), for the next start only. Each runner must then put back what it recorded before the start (a running flag,
a held slot, a "checking" entry, a registered worker), report the failure where its work's own failures go, and run
the next job on a real thread. The web read-back exposed the class (the maintainer's audit of #131/#148); the other
runners here were fixed by the same sweep and are checked the same way."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (api_connections, codex_turns, engine_link_writeback, engine_preflight_link,  # noqa: E402
                          snapshot_service, snapshot_worker, team_library_host, thread_start, web_readback,
                          worker_consent_marks, worker_mirror_ops)

REAL_START = threading.Thread.start
NO_THREAD = "can't start new thread"


def failing_starts(times=1):
    """``Thread.start`` (``Timer`` too) raising as an exhausted process does, for the next ``times`` starts."""
    left = [times]

    def start(thread):
        if left[0] > 0:
            left[0] -= 1
            raise RuntimeError(NO_THREAD)
        return REAL_START(thread)
    return mock.patch.object(threading.Thread, "start", start)


def wait_for(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the later job")
        time.sleep(0.01)


def completed(command, **_options):
    return subprocess.CompletedProcess(command, 0, b"", b"")


class WebReadBack(unittest.TestCase):
    def read_back_job(self, root, revision, controller=None):
        folder = Path(tempfile.mkdtemp(prefix="read-back-", dir=root))
        return {"spec": str(folder / "spec.json"), "result": str(folder / "result.json"), "folder": str(folder),
                "written": "w%d.glb" % revision, "revision": revision, "controller": controller}

    @staticmethod
    def child(command, **_options):   # The background Angelo: writes its result beside its spec.
        Path(command[-1]).with_name("result.json").write_text(json.dumps({"findings": [], "ms": 1.0}))
        return completed(command)

    def test_the_runner_is_idle_after_a_start_that_failed_and_runs_the_next_export(self):
        runner = web_readback._Runner("angelo")
        with tempfile.TemporaryDirectory() as root, mock.patch.object(web_readback.subprocess, "run", self.child):
            first, second = self.read_back_job(root, 1), self.read_back_job(root, 2)
            with failing_starts():
                with self.assertRaisesRegex(RuntimeError, NO_THREAD):
                    runner.request(first)
                self.assertFalse(runner.running, "a read-back that never started must not hold the runner")
                runner.request(second)
            job, result = runner.done.get(timeout=10)
            self.assertEqual((job["revision"], result), (2, {"findings": [], "ms": 1.0}))
            wait_for(lambda: not runner.running)
            self.assertTrue(runner.done.empty())

    def test_an_export_whose_read_back_cant_start_says_unavailable_and_the_next_one_runs(self):
        bpy = mock.MagicMock()
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root, "angelo")
            binary.write_bytes(b"")
            bpy.app.binary_path = str(binary)
            folders = [Path(root, "a"), Path(root, "b")]
            for folder in folders:
                folder.mkdir()
            with mock.patch.dict(sys.modules, {"bpy": bpy}), \
                    mock.patch.dict(web_readback._state, runner=None, timer=False, revision=0), \
                    mock.patch("angelo_agent.private_roots.allocate", side_effect=folders), \
                    mock.patch("angelo_agent.export_snapshot.take", return_value="identity"), \
                    mock.patch.object(web_readback.subprocess, "run", self.child), \
                    mock.patch("builtins.print") as printed:
                with failing_starts():
                    self.assertEqual(web_readback.start(None, "/a.glb", "a.glb", {}, False), ("unavailable", None))
                self.assertFalse(folders[0].exists(), "its copy goes with it")
                self.assertIn(NO_THREAD, " ".join(str(arg) for call in printed.call_args_list for arg in call.args))
                state, revision = web_readback.start(None, "/b.glb", "b.glb", {}, False)
                self.assertEqual(state, "running")
                job, result = web_readback._state["runner"].done.get(timeout=10)
            self.assertEqual((job["revision"], job["written"], result["findings"]), (revision, "b.glb", []))


class EngineLinkRunners(unittest.TestCase):
    def test_a_preflight_that_cant_start_leaves_the_runner_idle_and_its_folder_gone(self):
        runner = engine_preflight_link.Runner("angelo")
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(engine_preflight_link.subprocess, "run", side_effect=completed) as run:
            first, second = Path(root, "first"), Path(root, "second")
            first.mkdir()
            second.mkdir()
            with failing_starts():
                with self.assertRaisesRegex(RuntimeError, NO_THREAD):
                    runner.request("a.blend", {"folder": str(first), "report": str(Path(root, "a.json"))})
            self.assertFalse(runner.busy())
            self.assertFalse(first.exists())
            runner.request("b.blend", {"folder": str(second), "report": str(Path(root, "b.json"))})
            wait_for(lambda: not runner.busy())
            self.assertEqual(run.call_count, 1)
            self.assertIn("b.blend", run.call_args.args[0])
            self.assertIsNone(runner.last_error)

    def test_a_write_back_that_cant_start_ends_unwritten_said_so_and_the_next_save_runs(self):
        writeback = engine_link_writeback.WriteBack("angelo")
        with mock.patch.object(engine_link_writeback.subprocess, "run", side_effect=completed):
            with failing_starts():
                writeback.request("save-1", "a.blend", "a.glb", "gltf", {})   # Report-only: never raises.
            self.assertFalse(writeback.busy())
            self.assertEqual(writeback.finished(), [("save-1", "RuntimeError")])
            writeback.request("save-2", "a.blend", "a.glb", "gltf", {})
            ended = []
            wait_for(lambda: ended.extend(writeback.finished()) or ended)
        self.assertEqual(ended, [("save-2", None)])
        self.assertFalse(writeback.busy())


class ChatRunners(unittest.TestCase):
    def setUp(self):
        worker_mirror_ops.reset()
        self.addCleanup(worker_mirror_ops.reset)

    def test_a_chat_lane_that_cant_start_answers_its_op_as_not_sent_and_the_next_op_goes(self):
        sent = []
        mirror = SimpleNamespace(state={"id": "chat"}, send_op=lambda session, op, **fields: sent.append(op) or {
            "ok": True})
        with failing_starts():
            first = worker_mirror_ops.submit(mirror, "chat.options", "options")
        lane = mirror._op_lane
        self.assertFalse(lane.running)
        self.assertEqual(list(worker_mirror_ops._answers), [(first, {"error": NO_THREAD})])   # Said, then shown.
        worker_mirror_ops._answers.clear()
        second = worker_mirror_ops.submit(mirror, "chat.send", "send")
        wait_for(lambda: worker_mirror_ops._answers)
        self.assertEqual(list(worker_mirror_ops._answers), [(second, {"ok": True})])
        self.assertEqual(sent, ["chat.send"])
        wait_for(lambda: not lane.running)

    def test_a_connection_check_that_cant_start_fails_and_a_retry_runs(self):
        checks = api_connections.ApiConnections()
        record = {"id": "c1", "mode": "openai_api"}
        verified = api_connections.CheckResult("verified", "ok", True)
        with mock.patch.object(api_connections, "check_connection", return_value=verified):
            with failing_starts():
                checks.start(record, "sk-test-key")
            self.assertEqual(checks.results["c1"].state, "failed")
            self.assertNotIn("c1", checks.running)
            checks.start(record, "sk-test-key")   # Not refused as "already running".
            wait_for(lambda: checks.pump())
        self.assertEqual(checks.results["c1"], verified)

    def test_an_interrupt_that_cant_start_is_not_counted_as_sent(self):
        turns = codex_turns.OpenTurns()
        turns.opened("thread", "turn")
        rpc = mock.Mock()
        with failing_starts():
            self.assertFalse(turns.interrupt(rpc, "thread", "turn"))
        turns.join(1.0)   # Never waits on (or trips over) a worker that never started.
        self.assertTrue(turns.interrupt(rpc, "thread", "turn"))
        turns.join(5.0)
        rpc.request.assert_called_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"}, timeout=10)

    def test_a_consent_applier_that_cant_start_is_said_and_the_chats_next_frame_is_applied(self):
        applied = []
        applier = worker_consent_marks.Applier(lambda *item: applied.append(item))
        with failing_starts(), mock.patch("builtins.print") as printed:
            applier.put("chat", (1, "frame-1"))
        self.assertIn(NO_THREAD, " ".join(str(arg) for call in printed.call_args_list for arg in call.args))
        self.assertEqual(applier._queues, {})
        applier.put("chat", (2, "frame-2"))
        wait_for(lambda: applied)
        self.assertEqual(applied, [(2, "frame-2")])


class HostRunners(unittest.TestCase):
    def test_a_team_library_job_that_cant_start_frees_its_place_and_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, "job")
            folder.mkdir()
            with failing_starts():
                with self.assertRaisesRegex(RuntimeError, NO_THREAD):
                    team_library_host.Job(lambda cancel: "never", folder=str(folder))
            self.assertEqual(team_library_host._active, set())
            self.assertFalse(folder.exists())
            jobs = [team_library_host.Job(lambda cancel, index=index: index) for index in range(4)]   # All four.
            for job in jobs:
                self.assertTrue(job.done.wait(10))
        self.assertEqual([job.value for job in jobs], [0, 1, 2, 3])

    def test_a_snapshot_worker_that_cant_start_is_started_again_by_the_next_start(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(snapshot_worker.SnapshotWorker, "submit_recovery"), \
                mock.patch("builtins.print") as printed:
            self.addCleanup(snapshot_service.stop)
            with failing_starts():
                self.assertIsNone(snapshot_service.start(root))   # Not running: captures stay full copies.
            self.assertFalse(snapshot_service.running(root))
            self.assertIn(NO_THREAD, " ".join(str(arg) for call in printed.call_args_list for arg in call.args))
            worker = snapshot_service.start(root)
            self.assertTrue(snapshot_service.running(root))
            self.assertTrue(worker._thread.is_alive())
            self.assertTrue(worker.stop())


class Helpers(unittest.TestCase):
    def test_start_rolls_back_and_raises_again(self):
        undone = []
        with failing_starts():
            with self.assertRaisesRegex(RuntimeError, NO_THREAD):
                thread_start.start(threading.Thread(target=lambda: None), lambda: undone.append(True))
        self.assertEqual(undone, [True])
        ran = threading.Event()
        thread_start.daemon("test-daemon", ran.set, undo=lambda: undone.append(False)).join(5)
        self.assertTrue(ran.is_set())
        self.assertEqual(undone, [True])

    def test_start_or_fail_ends_the_record_as_a_failure(self):
        record = {"done": threading.Event(), "error": None}
        with failing_starts():
            thread_start.start_or_fail(threading.Thread(target=lambda: None), record)
        self.assertTrue(record["done"].is_set())
        self.assertEqual(str(record["error"]), NO_THREAD)

    def test_started_and_daemon_or_later(self):
        with failing_starts():
            self.assertFalse(thread_start.started(threading.Thread(target=lambda: None)))
        ran, timers = [], []
        with failing_starts(times=2):
            self.assertIsNone(thread_start.daemon_or_later("test-later", lambda: ran.append(threading.current_thread()),
                                                           timers.append))
            self.assertEqual((ran, len(timers)), ([], 1))   # Never run here: retried from a timer.
            self.assertEqual(timers[0](), 1.0)              # Still no thread: the timer runs again in a second.
        self.assertIsNone(timers[0]())                      # A thread started: the timer ends.
        wait_for(lambda: ran)
        self.assertIsNot(ran[0], threading.current_thread())
        thread = thread_start.daemon_or_later("test-there", lambda: ran.append(threading.current_thread()))
        thread.join(5)
        self.assertIs(ran[-1], thread)


if __name__ == "__main__":
    unittest.main()
