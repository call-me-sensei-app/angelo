# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""PDF output ownership, finite buffers and admission-before-parse contracts."""
import suite_isolation  # noqa: F401

import io
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import pdf_process_output as output, pdf_text, pdf_text_worker as worker


class Process:
    def __init__(self):
        self.stdout, self.code, self.killed = io.BytesIO(), None, False
    def poll(self):
        return self.code
    def wait(self, timeout=None):
        self.code = 0 if not self.killed else -9
        return self.code
    def kill(self):
        self.killed, self.code = True, -9


class OutputTests(unittest.TestCase):
    def invoke(self, process, chunks, limit=8):
        with mock.patch.object(output.sys, "platform", "linux"), \
                mock.patch.object(output.os, "name", "posix"), \
                mock.patch.object(output.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(output, "_chunk", side_effect=chunks):
            result = output.run(["worker"], memory_bytes=1024, output_bytes=limit, timeout=30, env={})
        kwargs = spawn.call_args.kwargs
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", kwargs)
        return result

    def test_complete_answer_is_bounded_and_pipe_closed(self):
        process = Process()
        result = self.invoke(process, [b"abcd", b"efgh", b""])
        self.assertEqual(result.stdout, b"abcdefgh")
        self.assertTrue(process.stdout.closed)
        self.assertFalse(process.killed)

    def test_first_byte_over_limit_kills_and_reaps_owned_worker(self):
        process = Process()
        with self.assertRaises(output.OutputLimitExceeded):
            self.invoke(process, [b"abcdefghi"])
        self.assertTrue(process.killed)
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout.closed)

    def test_pipe_failure_kills_and_reaps_owned_worker(self):
        process = Process()
        with self.assertRaises(OSError):
            self.invoke(process, OSError("broken read"))
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)

    def test_deadline_kills_and_reaps_without_unbounded_communicate(self):
        process = Process()
        with mock.patch.object(output.time, "monotonic", side_effect=[0, 31]), \
                self.assertRaises(subprocess.TimeoutExpired):
            self.invoke(process, [])
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)

    def test_live_kill_failure_still_closes_job_and_attempts_reap(self):
        process = Process()
        closed = []
        process.close_owned_job = lambda: closed.append(True)
        process.kill = mock.Mock(side_effect=OSError("live termination failure"))
        process.wait = mock.Mock(return_value=0)
        with self.assertRaisesRegex(OSError, "live termination failure"):
            output._cleanup(process)
        self.assertEqual(closed, [True])
        process.wait.assert_called_once_with(timeout=5)
        self.assertTrue(process.stdout.closed)

    def test_reap_timeout_does_not_hide_original_termination_error(self):
        process = Process()
        closed = []
        process.close_owned_job = lambda: closed.append(True)
        process.kill = mock.Mock(side_effect=OSError("live termination failure"))
        process.wait = mock.Mock(side_effect=subprocess.TimeoutExpired("worker", 5))
        with self.assertRaisesRegex(OSError, "live termination failure") as caught:
            output._cleanup(process)
        self.assertEqual(closed, [True])
        self.assertIn("TimeoutExpired", " ".join(caught.exception.__notes__))
        self.assertTrue(process.stdout.closed)

    def test_transient_job_close_failure_is_retried_by_parent_without_losing_original_error(self):
        import threading
        limits = output.pdf_process_limits
        job = limits._LimitedJob.__new__(limits._LimitedJob)
        job.handle, job.lock = 71, threading.Lock()
        job.api = types.SimpleNamespace(close=mock.Mock(side_effect=[OSError("transient close failure"), None]))
        process = limits._PDFWindowsProcess.__new__(limits._PDFWindowsProcess)
        process._limited_job, process.returncode, process.stdout = job, 0, io.BytesIO()
        with mock.patch.object(subprocess.Popen, "poll", return_value=0), \
                mock.patch.object(subprocess.Popen, "wait", return_value=0) as wait, \
                self.assertRaisesRegex(OSError, "transient close failure"):
            output._cleanup(process)
        self.assertIsNone(job.handle, "the caller retry must release the owned raw Job handle")
        self.assertEqual(job.api.close.call_count, 2)
        wait.assert_called_once_with(timeout=5)
        self.assertTrue(process.stdout.closed)

    def test_permanent_job_close_failure_keeps_ownership_and_reports_uncertainty(self):
        import threading
        limits = output.pdf_process_limits
        job = limits._LimitedJob.__new__(limits._LimitedJob)
        job.handle, job.lock = 71, threading.Lock()
        job.api = types.SimpleNamespace(close=mock.Mock(side_effect=OSError("persistent close failure")))
        process = limits._PDFWindowsProcess.__new__(limits._PDFWindowsProcess)
        process._limited_job, process.returncode, process.stdout = job, 0, io.BytesIO()
        with mock.patch.object(subprocess.Popen, "poll", return_value=0), \
                mock.patch.object(subprocess.Popen, "wait", return_value=0) as wait, \
                self.assertRaisesRegex(OSError, "persistent close failure") as caught:
            output._cleanup(process)
        self.assertEqual(job.handle, 71, "an unclosed handle must not be misreported as released")
        wait.assert_called_once_with(timeout=5)
        self.assertTrue(caught.exception.__notes__)
        self.assertTrue(process.stdout.closed)


class AdmissionTests(unittest.TestCase):
    def test_resource_refusal_precedes_any_pdf_read(self):
        read = mock.Mock(return_value={"ok": True})
        with mock.patch.object(worker, "admit", side_effect=OSError("no ceiling"), create=True), \
                mock.patch.object(worker, "read", read), mock.patch.object(worker, "answer") as answer:
            worker.main(["worker", "reader", "pdf", "20", "64", "100", "20", "1024"])
        self.assertEqual(read.call_count, 0, "PDF parsing must never precede OS memory admission")
        answer.assert_called_once_with(ok=False, code="resource")

    def test_admission_finishes_before_read_starts(self):
        calls = []
        with mock.patch.object(worker, "admit", side_effect=lambda n: calls.append(("admit", n))), \
                mock.patch.object(worker, "read", side_effect=lambda *a: calls.append(("read", a)) or {}), \
                mock.patch.object(worker, "answer"):
            worker.main(["worker", "reader", "pdf", "20", "64", "100", "20", "1024"])
        self.assertEqual([row[0] for row in calls], ["admit", "read"])
        self.assertEqual(calls[0][1], 1024)

    def test_parent_reports_resource_refusal_without_reinstall_advice(self):
        with mock.patch.object(pdf_text, "available", return_value=True), \
                mock.patch.object(output, "run", return_value=types.SimpleNamespace(
                    returncode=0, stdout=b'{"ok":false,"code":"resource"}')) as run:
            with self.assertRaises(pdf_text.Unreadable) as caught:
                pdf_text.extract("brief.pdf")
        self.assertEqual(caught.exception.code, "resource")
        self.assertIn("text or page images", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["output_bytes"], 6 * pdf_text.MAX_CHARS + 4096)


if __name__ == "__main__":
    unittest.main()
