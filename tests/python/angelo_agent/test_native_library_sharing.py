# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Callers sharing one Angelo Library download each keep their own lifetime (second audit, 1.0.8).

* **Stop is per caller.** One caller's Stop ends that caller's result only; the transfer stops when no caller still
  wants it. Every caller hears the transfer's progress. (The shared transfer used the first caller's Stop: one
  caller stopping failed every caller with "The download was stopped".)
* **Sharing follows the files.** Calls share a transfer only when they want the same files (the item's cache folder,
  a digest of its id and its files' names, checksums and sizes), and each caller's lease is on the folder its files
  come from. (Sharing was keyed by id and resolution: after a catalog refresh changed an item, a second caller
  joined the old version's transfer, got its files, and leased the new version's folder.)

Against a loopback server that holds each response half sent until the test lets it finish."""

import hashlib
import os
import tempfile
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import team_library_fixtures  # noqa: F401  (puts the add-on folder on sys.path)
from angelo_agent import library_fetch as fetch, library_files, library_host

SIZE = 64 * 1024


class HeldServer:
    """Serves ``routes``; each response sends half its body, then waits for ``release`` (capped) before the rest."""

    def __init__(self):
        self.routes, self.asked, self.started, self.release = {}, [], threading.Event(), threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.asked.append(self.path)
                body = owner.routes[self.path]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body[:len(body) // 2])
                self.wfile.flush()
                owner.started.set()
                owner.release.wait(10)
                try:
                    self.wfile.write(body[len(body) // 2:])
                except OSError:   # The client stopped reading.
                    pass

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


class SharingCase(unittest.TestCase):
    def setUp(self):
        self.server = HeldServer()
        self.addCleanup(self.server.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for patcher in (unittest.mock.patch.dict(os.environ, {fetch.URL_VARIABLE: self.server.base + "/latest.json"}),
                        unittest.mock.patch("urllib.request.getproxies", return_value={}),
                        unittest.mock.patch.object(fetch, "CHUNK", 4096),   # Stop is checked between chunks.
                        unittest.mock.patch.object(library_files, "check_item", lambda item, paths: dict(paths))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.library = library_host.Library(folder.name)
        self.addCleanup(self.library.shutdown)

    def item(self, version=b"a"):
        data = version * SIZE
        path = "/files/%s.glb" % hashlib.sha256(data).hexdigest()[:8]
        self.server.routes[path] = data
        return {"id": "src:thing", "kind": "model", "files": [{"role": "model", "name": "thing.glb",
                "url": self.server.base + path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}

    def started(self):
        self.assertTrue(self.server.started.wait(5), "the download never started")


class StopTests(SharingCase):
    def test_one_callers_stop_ends_only_its_own_result(self):
        item, stop = self.item(), threading.Event()
        first = self.library.fetch(item, cancel=stop)
        second = self.library.fetch(item)
        self.started()
        stop.set()
        time.sleep(0.05)   # The transfer reads a chunk with the stop set (a stop shared with every caller ends here).
        self.server.release.set()
        self.assertEqual(Path(second.result(10)["thing.glb"]).read_bytes(), b"a" * SIZE)
        with self.assertRaises(fetch.FetchError) as caught:
            first.result(10)
        self.assertEqual(caught.exception.refusal_code, "CANCELLED")
        self.assertEqual(self.server.asked.count(self.server.asked[0]), 1, "one transfer for both callers")

    def test_the_transfer_stops_when_every_caller_has_stopped(self):
        item, stops = self.item(), (threading.Event(), threading.Event())
        futures = [self.library.fetch(item, cancel=stop) for stop in stops]
        self.started()
        for stop in stops:
            stop.set()
        # The rest is sent only now, so the transfer's next check between chunks sees both stops (a stop never
        # interrupts a read in progress; that is the network timeout's job).
        self.server.release.set()
        for future in futures:
            with self.assertRaises(fetch.FetchError) as caught:
                future.result(10)
            self.assertEqual(caught.exception.refusal_code, "CANCELLED")
        self.assertFalse(any((Path(future.lease.folder) / "thing.glb").exists() for future in futures))

    def test_every_caller_hears_the_progress(self):
        item, heard = self.item(), {"first": [], "second": []}
        futures = [self.library.fetch(item, progress=lambda done, total, who=who: heard[who].append((done, total)))
                   for who in heard]
        self.server.release.set()
        for future in futures:
            future.result(10)
        self.assertEqual(heard["first"], [(SIZE, SIZE)])
        self.assertEqual(heard["second"], [(SIZE, SIZE)])


class RetryTests(SharingCase):
    """A request made after the download decided to stop starts a new download (third audit, 1.0.8): the decision to
    stop closes the transfer under the same lock, so no caller joins a download that is ending."""

    def test_a_retry_right_after_the_last_caller_stopped_gets_a_fresh_download(self):
        decided, go, original = threading.Event(), threading.Event(), library_host.Transfer.is_set

        def is_set(transfer):
            stopping = original(transfer)
            if stopping:
                decided.set()
                go.wait(5)   # Held between the decision and the download's end: a retry arrives here.
            return stopping
        with unittest.mock.patch.object(library_host.Transfer, "is_set", is_set):
            item, stop = self.item(), threading.Event()
            first = self.library.fetch(item, cancel=stop)
            self.started()
            stop.set()
            self.server.release.set()
            self.assertTrue(decided.wait(5), "the download never decided to stop")
            retry = self.library.fetch(item)
            go.set()
            self.assertEqual(Path(retry.result(10)["thing.glb"]).read_bytes(), b"a" * SIZE)
        with self.assertRaises(fetch.FetchError):
            first.result(10)

    def test_a_retry_right_after_a_failed_download_gets_a_fresh_one(self):
        failures, finishing, go, original = [ValueError("transient")], threading.Event(), threading.Event(), \
            library_host.Transfer.finish

        def check(item, paths):
            if failures:
                raise failures.pop()
            return dict(paths)

        def finish(transfer, shared):
            finishing.set()
            go.wait(5)   # Held between the download's end and its outcome reaching the callers: a retry arrives.
            original(transfer, shared)
        with unittest.mock.patch.object(library_files, "check_item", check), \
                unittest.mock.patch.object(library_host.Transfer, "finish", finish):
            item = self.item()
            self.server.release.set()
            first = self.library.fetch(item)
            self.assertTrue(finishing.wait(5))
            retry = self.library.fetch(item)
            go.set()
            with self.assertRaises(ValueError):
                first.result(10)
            self.assertEqual(Path(retry.result(10)["thing.glb"]).read_bytes(), b"a" * SIZE)

    def test_a_stopped_transfer_takes_no_new_caller(self):
        import concurrent.futures
        stop = threading.Event()
        transfer = library_host.Transfer(threading.Event())
        transfer.join(concurrent.futures.Future(), stop)
        stop.set()
        self.assertTrue(transfer.is_set())
        self.assertFalse(transfer.join(concurrent.futures.Future()), "a stopped transfer is closed at once")
        shutdown = threading.Event()
        ending = library_host.Transfer(shutdown)
        ending.join(concurrent.futures.Future())
        shutdown.set()
        self.assertTrue(ending.is_set())
        self.assertFalse(ending.join(concurrent.futures.Future()), "nor one stopped by the shutdown")


class VersionTests(SharingCase):
    def test_a_changed_item_never_joins_the_old_versions_transfer(self):
        old, new = self.item(b"a"), self.item(b"b")   # The catalog refreshed between the two calls.
        first = self.library.fetch(old)
        self.started()
        second = self.library.fetch(new)
        self.server.release.set()
        first_paths, second_paths = first.result(10), second.result(10)
        self.assertEqual(Path(first_paths["thing.glb"]).read_bytes(), b"a" * SIZE)
        self.assertEqual(Path(second_paths["thing.glb"]).read_bytes(), b"b" * SIZE, "the new version's files")
        for future, paths in ((first, first_paths), (second, second_paths)):
            self.assertEqual(Path(paths["thing.glb"]).parent.resolve(), Path(future.lease.folder).resolve(),
                             "the lease is on the folder the caller's files come from")


if __name__ == "__main__":
    unittest.main()
