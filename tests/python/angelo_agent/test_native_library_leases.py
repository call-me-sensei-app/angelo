# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's cache never deletes an item's files between its request and its import (audit P2, 1.0.8).

Every completed download trims the cache to its byte limit. Before leases it spared only its own folder, so an item
that had finished downloading (or was found in the cache) and was waiting for its main-thread import could be
deleted by another item's download. Each request now holds a lease on its item's folder from the request until the
caller's import ends; the trim removes only folders nobody holds. Concurrent requests for one item share one
download. Against a loopback server; ``check_item`` is stubbed (the files' contents are not the subject here)."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import gc
import os
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import team_library_fixtures  # noqa: F401  (puts the add-on folder on sys.path)
from angelo_agent import library_fetch as fetch, library_files, library_host
from test_native_library_fetch import Server

SIZE = 100


def settle(library, seconds=10.0):
    deadline = time.monotonic() + seconds
    while library.busy() and time.monotonic() < deadline:
        library.pump()
        time.sleep(0.01)
    library.pump()


class LeaseCase(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for patcher in (unittest.mock.patch.dict(os.environ, {fetch.URL_VARIABLE: self.server.base + "/latest.json"}),
                        unittest.mock.patch("urllib.request.getproxies", return_value={}),
                        unittest.mock.patch.object(fetch, "CACHE_BYTES", int(SIZE * 1.5)),   # Room for one item.
                        unittest.mock.patch.object(library_files, "check_item", lambda item, paths: dict(paths))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.library = library_host.Library(str(self.root))
        self.addCleanup(self.library.shutdown)

    def item(self, name):
        data = name.encode() * (SIZE // len(name))
        self.server.routes["/files/%s.glb" % name] = data
        import hashlib
        return {"id": "src:" + name, "kind": "model", "files": [{"role": "model", "name": name + ".glb",
                "url": self.server.base + "/files/%s.glb" % name, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}]}

    def fetched(self, item):
        future = self.library.fetch(item)
        facts = future.result(10)
        return future, facts


class EvictionTests(LeaseCase):
    def test_a_fetched_item_waiting_for_its_import_survives_another_items_download(self):
        a, b = self.item("a"), self.item("b")
        future_a, facts_a = self.fetched(a)
        future_b, facts_b = self.fetched(b)   # B's download trims the cache to room for one item.
        self.assertTrue(all(Path(p).is_file() for p in facts_a.values()), "A was deleted before its import")
        self.assertTrue(all(Path(p).is_file() for p in facts_b.values()))
        future_a.lease.release()   # A imported: its folder may go at the next trim.
        future_b.lease.release()
        c = self.item("c")
        future_c, _facts = self.fetched(c)
        future_c.lease.release()
        self.assertFalse(any(Path(p).exists() for p in facts_a.values()), "released entries are trimmed as before")

    def test_a_cached_item_found_again_is_held_too(self):
        a, b = self.item("a"), self.item("b")
        first, _ = self.fetched(a)
        first.lease.release()
        again, facts_a = self.fetched(a)   # Found in the cache, nothing downloaded; waiting for its import.
        _future_b, _ = self.fetched(b)
        self.assertTrue(all(Path(p).is_file() for p in facts_a.values()))
        again.lease.release()

    def test_the_drop_callback_holds_its_item_until_it_returns(self):
        a, b = self.item("a"), self.item("b")
        seen = {}

        def done(facts, error):
            seen["error"] = error
            _future_b, _ = self.fetched(b)   # Another download finishes while this import runs.
            seen["present"] = all(Path(p).is_file() for p in facts.values())
            _future_b.lease.release()
        self.library.fetch(a, done=done)
        settle(self.library)
        self.assertIsNone(seen.get("error"))
        self.assertTrue(seen.get("present"))
        self.assertEqual(fetch.held(), {}, "the callback's lease ends when it returns")

    def test_a_lease_nobody_releases_ends_when_it_is_collected(self):
        future, _ = self.fetched(self.item("a"))
        self.assertEqual(len(fetch.held()), 1)
        del future
        settle(self.library)   # The pump retires the shared download, which holds its callers' futures.
        gc.collect()
        self.assertEqual(fetch.held(), {})


class SharedDownloadTests(LeaseCase):
    def test_concurrent_requests_for_one_item_share_one_download(self):
        a = self.item("a")
        first, second = self.library.fetch(a), self.library.fetch(a)
        self.assertEqual(first.result(10), second.result(10))
        self.assertEqual(self.server.asked.count("/files/a.glb"), 1)
        self.assertEqual(sum(fetch.held().values()), 2, "each caller holds its own lease")
        first.lease.release()
        first.lease.release()   # Idempotent.
        self.assertEqual(sum(fetch.held().values()), 1)
        second.lease.release()
        self.assertEqual(fetch.held(), {})


if __name__ == "__main__":
    unittest.main()
