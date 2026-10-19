# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Downloading an update file: resume, verification, refusal of corrupt, partial and oversized files, offline.

Against a loopback server that answers ``Range``/``If-Range`` as the download host does (``LoopbackServer.serve_file``);
nothing leaves the computer. Contract: doc/angelo/updates_and_reports.rst, "Installing updates".
"""

import hashlib
import os
import socket
import sys
import tempfile
import threading
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import update_download  # noqa: E402
from angelo_agent.update_download import DownloadError, Expected  # noqa: E402
from loopback_server import LoopbackServer  # noqa: E402

DATA = os.urandom(3 * update_download.CHUNK + 1234)


def expected(url, data=DATA):
    return Expected(url, hashlib.sha256(data).hexdigest(), len(data))


class DownloadCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        self.server = LoopbackServer().start()
        self.addCleanup(self.server.stop)
        self.target = self.folder / "Angelo-1.0.1-macos-arm64.dmg"
        self.part = self.target.with_name(self.target.name + ".part")

    def url(self, path="/update.dmg"):
        return self.server.base + path

    def fetch(self, entry, **options):
        return update_download.fetch(entry, self.target, **options)


class FullDownloadTests(DownloadCase):
    def test_a_download_is_verified_then_moved_into_place(self):
        self.server.serve_file("/update.dmg", DATA)
        seen = []
        self.assertEqual(self.fetch(expected(self.url()), progress=lambda done, total: seen.append((done, total))),
                         self.target)
        self.assertEqual(self.target.read_bytes(), DATA)
        self.assertFalse(self.part.exists())
        self.assertEqual(seen[-1], (len(DATA), len(DATA)))
        request = self.server.requests[-1]
        self.assertNotIn("range", request.headers)
        self.assertEqual(request.headers["accept-encoding"], "identity")

    def test_a_verified_file_is_reused_without_a_request(self):
        self.target.write_bytes(DATA)
        self.assertEqual(self.fetch(expected(self.url("/never"))), self.target)
        self.assertEqual(self.server.count(), 0)

    def test_a_file_that_is_not_the_releases_is_deleted_and_refused(self):
        self.server.serve_file("/update.dmg", DATA[:-1] + bytes([DATA[-1] ^ 1]))
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url()))
        self.assertEqual(caught.exception.code, "digest")
        self.assertFalse(caught.exception.transient)
        self.assertFalse(self.target.exists() or self.part.exists(), "a corrupt file never stays")

    def test_more_bytes_than_the_feed_announced_are_refused(self):
        self.server.serve_file("/update.dmg", DATA + b"extra")
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url()))
        self.assertEqual(caught.exception.code, "size")
        self.assertFalse(self.part.exists())

    def test_an_http_error_and_an_offline_machine(self):
        self.server.route("/missing", status=404, body=b"no")
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url("/missing")))
        self.assertEqual((caught.exception.code, caught.exception.transient), ("http_404", False))
        self.server.route("/busy", status=503, body=b"busy")
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url("/busy")))
        self.assertTrue(caught.exception.transient)
        with socket.socket() as probe:   # A port nothing listens on: the machine is offline for this host.
            probe.bind(("127.0.0.1", 0))
            closed = probe.getsockname()[1]
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected("http://127.0.0.1:%d/update.dmg" % closed))
        self.assertEqual((caught.exception.code, caught.exception.transient), ("offline", True))

    def test_too_little_disk_space_writes_nothing(self):
        self.server.serve_file("/update.dmg", DATA)
        usage = namedtuple("usage", "total used free")(10 ** 12, 0, len(DATA) - 1)
        with mock.patch.object(update_download.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(DownloadError) as caught:
                self.fetch(expected(self.url()))
        self.assertEqual(caught.exception.code, "disk_space")
        self.assertEqual(self.server.count(), 0)

    def test_the_space_needed_afterwards_is_part_of_the_check(self):
        self.server.serve_file("/update.dmg", DATA)
        usage = namedtuple("usage", "total used free")(10 ** 12, 0, len(DATA) + 10)
        with mock.patch.object(update_download.shutil, "disk_usage", return_value=usage):
            with self.assertRaises(DownloadError):
                self.fetch(expected(self.url()), reserve=100)


class ResumeTests(DownloadCase):
    def test_a_cut_connection_keeps_the_part_and_the_next_try_resumes_it(self):
        self.server.serve_file("/update.dmg", DATA, cut=update_download.CHUNK + 100)
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url()))
        self.assertTrue(caught.exception.transient, caught.exception.code)
        kept = self.part.stat().st_size
        self.assertGreater(kept, 0)
        self.assertEqual(self.fetch(expected(self.url())), self.target)
        self.assertEqual(self.target.read_bytes(), DATA)
        resumed = self.server.requests[-1]
        self.assertEqual(resumed.headers["range"], "bytes=%d-" % kept)
        self.assertEqual(resumed.headers["if-range"], '"v1"')

    def test_a_file_that_changed_on_the_server_starts_over(self):
        self.server.serve_file("/update.dmg", DATA, etag='"old"', cut=update_download.CHUNK)
        with self.assertRaises(DownloadError):
            self.fetch(expected(self.url()))
        self.server.serve_file("/update.dmg", DATA, etag='"new"')   # If-Range no longer matches: a full 200.
        self.assertEqual(self.fetch(expected(self.url())), self.target)
        self.assertEqual(self.target.read_bytes(), DATA)

    def test_a_part_from_another_release_is_never_continued(self):
        self.part.write_bytes(b"x" * 1000)
        update_download._write_meta(self.part, expected("https://downloads.angelo.studio/9.9.9/x.dmg"), '"v1"')
        self.server.serve_file("/update.dmg", DATA)
        self.fetch(expected(self.url()))
        self.assertNotIn("range", self.server.requests[-1].headers)
        self.assertEqual(self.target.read_bytes(), DATA)

    def test_a_complete_part_is_checked_without_downloading_again(self):
        self.part.write_bytes(DATA)
        update_download._write_meta(self.part, expected(self.url()), '"v1"')
        self.server.serve_file("/update.dmg", DATA)
        self.assertEqual(self.fetch(expected(self.url())), self.target)
        self.assertEqual(self.server.requests[-1].headers["range"], "bytes=%d-" % len(DATA))

    def test_a_range_answer_for_other_bytes_is_refused_and_the_part_dropped(self):
        self.part.write_bytes(DATA[:500])
        update_download._write_meta(self.part, expected(self.url()), '"v1"')
        self.server.routes["/update.dmg"] = lambda record: (206, {"Content-Range": "bytes 0-9/%d" % len(DATA)}, DATA[:10])
        with self.assertRaises(DownloadError):
            self.fetch(expected(self.url()))
        self.assertFalse(self.part.exists())

    def test_a_stopped_download_keeps_its_part(self):
        self.server.serve_file("/update.dmg", DATA)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DownloadError) as caught:
            self.fetch(expected(self.url()), cancel=cancel)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertTrue(self.part.exists())


class ThrottleTests(unittest.TestCase):
    def test_a_limit_spaces_the_chunks_and_no_limit_never_waits(self):
        now, slept = [0.0], []

        def sleep(seconds):   # Time passes while it sleeps, as it does for the real one.
            slept.append(seconds)
            now[0] += seconds
        throttle = update_download.Throttle(lambda: 1000, clock=lambda: now[0], sleep=sleep)
        throttle.account(500)
        throttle.account(500)
        self.assertAlmostEqual(sum(slept), 1.0)
        slept.clear()
        free = update_download.Throttle(lambda: None, clock=lambda: now[0], sleep=sleep)
        free.account(10 ** 9)
        self.assertEqual(slept, [])

    def test_codes_say_whether_trying_later_can_help(self):
        for code in ("offline", "timeout", "incomplete", "cancelled", "http_503", "http_429"):
            self.assertTrue(DownloadError(code).transient, code)
        for code in ("digest", "size", "disk_space", "http_404", "bad_url"):
            self.assertFalse(DownloadError(code).transient, code)


if __name__ == "__main__":
    unittest.main()
