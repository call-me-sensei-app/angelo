# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An explicit web export waits for its World's picture without blocking (doc/angelo/web_export.rst, "The World"):
``web_export_wait.PendingExport`` and the agent's pending call (``web_host``), with a deliberately stalled worker.

Each step returns at once whatever the worker does; nothing is written until the picture's job is done; an edit made
meanwhile waits for its own picture; a stalled worker's export is written without the picture after the wait, with
a finding; cancelling ends the wait and the agent's staged file is discarded."""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import machine_clock  # noqa: E402
from angelo_agent import web_export_wait  # noqa: E402
from angelo_agent.web_export_wait import EnvironmentPending, PendingExport  # noqa: E402


def stalled_job():
    """A job whose worker thread never finishes (a hung bake): ``done`` is never set until ``release``."""
    job = SimpleNamespace(done=threading.Event(), release=threading.Event())
    worker = threading.Thread(target=job.release.wait, daemon=True)
    worker.start()
    job.worker = worker
    return job


def report(status="ready"):
    return {"settings": {"environment": {"kind": "BAKE", "status": status}}, "findings": [], "budget": {}}


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Writer:
    """``write(require)``: raises EnvironmentPending with the next job while one is queued, else writes."""

    def __init__(self, *jobs):
        self.jobs, self.calls = list(jobs), []

    def __call__(self, require):
        self.calls.append(require)
        if require and self.jobs and not self.jobs[0].done.is_set():
            raise EnvironmentPending(self.jobs[0])
        if self.jobs and self.jobs[0].done.is_set():
            self.jobs.pop(0)
            if require and self.jobs:   # The World changed meanwhile: its own picture is being made.
                raise EnvironmentPending(self.jobs[0])
        return report("ready" if require else "pending")


class PendingExportTests(unittest.TestCase):
    def test_a_ready_picture_exports_at_once(self):
        writer = Writer()
        found = PendingExport(writer).step()
        self.assertEqual((found["waited_ms"], writer.calls), (0.0, [True]))

    def test_nothing_is_written_until_the_picture_lands(self):
        job, clock = stalled_job(), Clock()
        writer = Writer(job)
        waiting = PendingExport(writer, "Sky", clock=clock)
        for _ in range(5):
            self.assertIsNone(waiting.step())
            clock.now += 1.0
        self.assertEqual(writer.calls, [True])   # Polls while the job runs never export again.
        job.done.set()
        found = waiting.step()
        self.assertEqual((found["settings"]["environment"]["status"], found["waited_ms"]), ("ready", 5000.0))
        self.assertEqual(found["findings"], [])
        job.release.set()

    def test_an_edit_meanwhile_waits_for_its_own_picture(self):
        first, second = stalled_job(), stalled_job()
        writer = Writer(first, second)
        waiting = PendingExport(writer)
        self.assertIsNone(waiting.step())
        first.done.set()
        self.assertIsNone(waiting.step())   # The World is read again: a new picture, still being made.
        self.assertIs(waiting.job, second)
        second.done.set()
        self.assertEqual(waiting.step()["settings"]["environment"]["status"], "ready")
        for job in (first, second):
            job.release.set()

    def test_a_stalled_worker_never_blocks_a_step(self):
        # A real thread that never ends: every step returns at once, the main thread is never held.
        job = stalled_job()
        waiting = PendingExport(Writer(job))
        window = machine_clock.start(self)
        for _ in range(200):
            self.assertIsNone(waiting.step())
        window.assert_within(0.5, "200 steps returned at once")
        self.assertTrue(job.worker.is_alive())
        job.release.set()

    def test_after_the_wait_a_stalled_picture_is_left_out_with_a_finding(self):
        job, clock = stalled_job(), Clock()
        writer = Writer(job)
        waiting = PendingExport(writer, "Sky", wait_seconds=30.0, clock=clock)
        self.assertIsNone(waiting.step())
        clock.now += 29.0
        self.assertIsNone(waiting.step())
        clock.now += 1.0
        found = waiting.step()
        self.assertEqual(writer.calls, [True, False])
        self.assertEqual(found["settings"]["environment"]["status"], "pending")
        self.assertEqual((found["findings"][0]["code"], found["findings"][0]["params"]["reason"]),
                         ("world_failed", "not made within 30 s"))
        job.release.set()

    def test_cancel_ends_the_wait(self):
        job = stalled_job()
        writer = Writer(job)
        waiting = PendingExport(writer)
        self.assertIsNone(waiting.step())
        waiting.cancel()
        job.done.set()
        self.assertIsNone(waiting.step())
        self.assertEqual(writer.calls, [True])
        self.assertFalse(waiting.waiting())
        job.release.set()


class Publication:
    published = "/workspace/web/scene.glb"

    def __init__(self):
        self.calls = []

    def publish_file(self, name):
        self.calls.append(("publish", name))

    def relative_output(self):
        return "web/scene.glb"

    def discard(self):
        self.calls.append(("discard",))

    def close(self):
        self.calls.append(("close",))


class AgentPendingTests(unittest.TestCase):
    """The agent's ``angelo_web_export`` while the picture is made: a pending call the controller polls."""

    def setUp(self):
        import angelo_agent
        before = set(vars(angelo_agent))
        bpy = mock.MagicMock()   # With its submodules: ``from bpy.props import ...`` needs them in sys.modules.
        patcher = mock.patch.dict(sys.modules, {"bpy": bpy, "bpy.props": bpy.props, "bpy.types": bpy.types,
                                                "bpy_extras": mock.MagicMock()})
        patcher.start()

        def restore():   # Modules imported over the stand-in bpy leave with it, from the package too.
            patcher.stop()
            for name in set(vars(angelo_agent)) - before:
                delattr(angelo_agent, name)

        self.addCleanup(restore)
        from angelo_agent import web_host
        self.web_host = web_host

    def pending(self, job, writer=None, controller=None):
        publication = Publication()
        waiting = PendingExport(writer or Writer(job))
        self.assertIsNone(waiting.step())
        call = self.web_host._pending_class()(controller, waiting, publication, SimpleNamespace(name="scene.glb"))
        return call, publication, waiting

    def test_polls_return_at_once_while_the_worker_is_stalled_and_stop_discards(self):
        job = stalled_job()
        call, publication, waiting = self.pending(job)
        window = machine_clock.start(self)
        self.assertTrue(all(call.poll() is None for _ in range(100)))
        window.assert_within(0.5, "100 polls returned at once")
        call.cancel()
        self.assertTrue(waiting.cancelled)
        self.assertEqual(publication.calls, [("discard",)])
        self.assertEqual(call.stopped_result["error_code"], "CANCELLED")
        self.assertGreater(call.timeout, web_export_wait.WAIT_SECONDS)   # The wait ends first, with its finding.
        job.release.set()

    def test_the_export_is_published_when_the_picture_lands(self):
        job = stalled_job()
        call, publication, _waiting = self.pending(job)
        job.done.set()
        result = call.poll()
        self.assertEqual(result["output"], "web/scene.glb")
        self.assertEqual(publication.calls, [("publish", "scene.glb"), ("close",)])
        job.release.set()

    def test_an_export_that_waited_still_starts_its_read_back_for_the_chat(self):
        """The preflight's read-back belongs to the chat that exported, also when the file is written on a later poll."""
        job = stalled_job()

        def write(require):
            if not job.done.is_set():
                raise EnvironmentPending(job)
            return dict(report("ready"), preflight={"findings": [], "check_ms": 1.0},
                        _readback={"source": {"objects": 1}, "merged": False})

        controller = object()
        call, _publication, _waiting = self.pending(job, write, controller)
        job.done.set()
        with mock.patch("angelo_agent.web_readback.start", return_value=("running", 7)) as start:
            result = call.poll()
        start.assert_called_once_with(controller, Publication.published, "web/scene.glb", {"objects": 1}, False)
        self.assertEqual((result["preflight"]["readback"], result["preflight"]["readback_revision"]), ("running", 7))
        self.assertNotIn("_readback", result)
        job.release.set()

    def published(self, controller, extra=None):
        """A pending export of a report with a preflight, written on its first poll; the call's result."""
        job = stalled_job()

        def write(require):
            if not job.done.is_set():
                raise EnvironmentPending(job)
            return dict(report("ready"), preflight={"findings": [], "check_ms": 1.0},
                        _readback={"source": {}, "merged": False}, **(extra or {}))
        call, publication, _waiting = self.pending(job, write, controller)
        job.done.set()
        result = call.poll()
        job.release.set()
        return result, publication

    def test_a_chat_that_isnt_a_controller_still_gets_its_published_export(self):
        """Any chat object, weakly referable or not: the read-back is "unavailable", the export is the result."""
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder, "angelo")
            binary.write_bytes(b"")
            sys.modules["bpy"].app.binary_path = str(binary)
            for chat in (SimpleNamespace(files=None), object()):
                result, publication = self.published(chat)
                self.assertEqual((result["output"], result["preflight"]["readback"]), ("web/scene.glb", "unavailable"))
                self.assertEqual(publication.calls, [("publish", "scene.glb"), ("close",)])

    def test_a_failed_preflight_summary_never_fails_the_published_export(self):
        with mock.patch("angelo_agent.engine_findings.summarize", side_effect=KeyError("code")), \
                mock.patch("angelo_agent.web_readback.start", return_value=("running", 3)):
            result, publication = self.published(object())
        self.assertEqual(result["output"], "web/scene.glb")
        self.assertEqual(result["preflight"], {"status": "unavailable", "error": "KeyError"})
        self.assertNotIn("_readback", result)
        self.assertEqual(publication.calls, [("publish", "scene.glb"), ("close",)])

    def test_a_read_back_that_cant_start_leaves_the_published_export_a_success(self):
        """Report-only, and past the publish: any failure to start is "unavailable", and leaves no folder behind."""
        from angelo_agent import private_roots, web_readback
        with tempfile.TemporaryDirectory() as folder:
            held = Path(folder, "read-back")
            held.mkdir()
            binary = Path(folder, "angelo")
            binary.write_bytes(b"")
            sys.modules["bpy"].app.binary_path = str(binary)
            with mock.patch.object(private_roots, "allocate", return_value=held), \
                    mock.patch("angelo_agent.export_snapshot.take", return_value="identity"):
                self.assertEqual(web_readback.start(None, "/a.glb", "a.glb", {"not json": {1}}, False),
                                 ("unavailable", None))
            self.assertFalse(held.exists())
            with mock.patch.object(private_roots, "allocate", side_effect=OSError("no space")):
                self.assertEqual(web_readback.start(None, "/a.glb", "a.glb", {}, False), ("unavailable", None))
            # A chat that can't be referred to weakly (the release's web scenario passes a stand-in): no folder made.
            held.mkdir()
            with mock.patch.object(private_roots, "allocate", return_value=held) as allocate:
                self.assertEqual(web_readback.start(SimpleNamespace(), "/a.glb", "a.glb", {}, False),
                                 ("unavailable", None))
            allocate.assert_not_called()
            # A runner that can't start a thread: the folder goes with it.
            with mock.patch.object(private_roots, "allocate", return_value=held), \
                    mock.patch("angelo_agent.export_snapshot.take", return_value="identity"), \
                    mock.patch.object(web_readback, "_Runner", side_effect=RuntimeError("can't start new thread")), \
                    mock.patch.dict(web_readback._state, runner=None):
                self.assertEqual(web_readback.start(None, "/a.glb", "a.glb", {}, False), ("unavailable", None))
            self.assertFalse(held.exists())

    def test_the_read_back_timer_survives_a_file_load(self):
        source = (ADDON / "web_readback.py").read_text(encoding="utf-8")
        self.assertIn("bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)", source)

    def test_a_failed_export_is_the_calls_result_and_discards(self):
        job = stalled_job()

        def write(require):
            if not job.done.is_set():
                raise EnvironmentPending(job)
            from angelo_agent.web_export import WebExportError
            raise WebExportError("The window was closed or shows another scene; export again")

        call, publication, _waiting = self.pending(job, write)
        job.done.set()
        result = call.poll()
        self.assertEqual((result["error_code"], publication.calls), ("VALIDATION", [("discard",)]))
        job.release.set()


if __name__ == "__main__":
    unittest.main()
