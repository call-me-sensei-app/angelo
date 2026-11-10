# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's process state (library_host.py) without Blender: a failing or malformed index never leaves
the library stuck loading, workers never hold the interpreter at exit, and shutdown stops running downloads."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import tempfile
import threading
import time
import unittest
import unittest.mock

import team_library_fixtures  # noqa: F401  (puts the add-on folder on sys.path)
from angelo_agent import library_fetch, library_host


def settle(library, seconds=5.0):
    deadline = time.monotonic() + seconds
    while library.busy() and time.monotonic() < deadline:
        library.pump()
        time.sleep(0.01)
    library.pump()


class LoadTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.library = library_host.Library(folder.name)
        self.addCleanup(self.library.shutdown)

    def test_any_failure_while_loading_is_reported_and_the_next_load_can_retry(self):
        with unittest.mock.patch.object(library_fetch, "read_local", return_value=None), \
                unittest.mock.patch.object(library_fetch, "refresh", side_effect=AttributeError("boom")):
            self.library.ensure_catalog()
            settle(self.library)
        self.assertEqual(self.library.status, "offline")
        self.assertFalse(self.library._loading)

    def test_a_malformed_index_is_refused_not_raised(self):
        bad = {"schema": "toonlab-catalog/1", "version": "v1", "items": [{"id": "x", "kind": "model", "files": [
            {"role": "model", "name": "m.glb", "url": "https://assets.toonlab.io:99999/m.glb", "size": 1,
             "sha256": "a" * 64}]}]}
        with unittest.mock.patch.object(library_fetch, "read_local", return_value=bad), \
                unittest.mock.patch.object(library_fetch, "refresh", return_value=None):
            self.library.ensure_catalog()
            settle(self.library)
        self.assertEqual(self.library.status, "ready")
        self.assertEqual(self.library.catalog.items, [])   # The bad item is dropped; the library works.


class TextureBudgetTests(unittest.TestCase):
    """GPU thumbnails are bounded by bytes, least recently drawn out first (a count bound let 600 thumbnails of
    512 x 512 RGBA16F reach 1.17 GiB)."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.library = library_host.Library(folder.name)
        self.addCleanup(self.library.shutdown)

    def test_kept_textures_stay_within_the_byte_budget(self):
        side = library_host.THUMB_SIDE
        each = library_host.gpu_bytes(unittest.mock.Mock(width=side, height=side))
        self.assertEqual(each, side * side * library_host.TEXEL_BYTES)
        fits = library_host.THUMB_GPU_BYTES // each
        for n in range(fits + 25):
            self.library._keep_texture("u%d" % n, unittest.mock.Mock(width=side, height=side))
            if n == fits - 1:
                self.library.texture({"thumb": "u0"})   # Drawn again just before the budget fills: kept.
        self.assertLessEqual(self.library._texture_bytes, library_host.THUMB_GPU_BYTES)
        self.assertEqual(self.library._texture_bytes,
                         sum(library_host.gpu_bytes(t) for t in self.library._textures.values()))
        self.assertIn("u0", self.library._textures)
        self.assertNotIn("u1", self.library._textures)
        self.assertEqual(library_host.gpu_bytes(False), 0)

    def test_a_thumbnail_is_at_most_a_tile_at_2x(self):
        from angelo_agent import library_overlay_draw as look
        self.assertGreaterEqual(library_host.THUMB_SIDE, look.TILE * 2)


class PoolTests(unittest.TestCase):
    def test_workers_are_daemons_and_shutdown_stops_running_fetches(self):
        pool = library_host.DaemonPool(2, "test-pool")
        self.assertTrue(all(t.daemon for t in threading.enumerate() if t.name.startswith("test-pool")))
        started, stop = threading.Event(), threading.Event()

        def slow():
            started.set()
            stop.wait(5)
            return stop.is_set()
        future = pool.submit(slow)
        started.wait(2)
        queued = [pool.submit(time.sleep, 5) for _ in range(4)]
        pool.shutdown()
        stop.set()
        self.assertTrue(future.result(2))
        self.assertTrue(any(f.cancelled() for f in queued))

    def test_a_transfer_stops_for_its_last_caller_or_the_shutdown(self):
        import concurrent.futures
        shutdown, mine, theirs = threading.Event(), threading.Event(), threading.Event()
        transfer = library_host.Transfer(shutdown)
        first, second = concurrent.futures.Future(), concurrent.futures.Future()
        self.assertTrue(transfer.join(first, mine) and transfer.join(second, theirs))
        mine.set()
        self.assertFalse(transfer.is_set(), "another caller still wants the files")
        self.assertEqual(first.exception(0).refusal_code, "CANCELLED")
        theirs.set()
        self.assertTrue(transfer.is_set())
        self.assertTrue(library_host.Transfer(shutdown).join(concurrent.futures.Future()))
        shutdown.set()
        late = library_host.Transfer(shutdown)
        late.join(concurrent.futures.Future())
        self.assertTrue(late.is_set())
        done = concurrent.futures.Future()
        done.set_result({"m.glb": "/x"})
        transfer.finish(done)
        self.assertFalse(transfer.join(concurrent.futures.Future()), "an ended transfer takes no new caller")


if __name__ == "__main__":
    unittest.main()
