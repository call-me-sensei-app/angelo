# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The wait a live session page keeps for each item (``session_page_pending.Pending`` and ``serve``) and how the page
draws a given-up picture, case by case: the growing wait, give-up, drops, the file's mark and when an unchanged file
is read once more (break-it reviews of 4afc19981c3, 09db060, 404eface611 and 6daffb057ee). The pages around it are
``test_native_session_page_pending``. No Blender, no server.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent import session_page_pending as pending  # noqa: E402
from angelo_agent.reference_errors import ImageMissing  # noqa: E402
from angelo_agent.session_page_site import MAX_PICTURES  # noqa: E402
import test_native_session_page_pending as pages  # noqa: E402  (its site and pictures; its tests stay its own)

PictureSite, WHOLE, HALF = pages.PictureSite, pages.WHOLE, pages.HALF


def store(locate):
    """A ``ReferenceStore`` as ``reference_images`` reads it: ``locate`` its ``attached_image``."""
    from types import SimpleNamespace
    return SimpleNamespace(attached_image=locate, image_limit=5 * 1024 * 1024)


class PendingTests(unittest.TestCase):
    """The wait itself, for any kind of item a page waits on."""

    def setUp(self):
        self.clock = [0.0]
        self.waiting = pending.Pending(lambda: self.clock[0])
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)

    def file(self, identity, data):
        path = self.folder / (identity + ".png")
        path.write_bytes(data)
        return path

    def test_the_wait_grows_to_its_longest_and_ends_at_the_limit(self):
        tried = []
        while not self.waiting.given_up("x") and self.clock[0] < 10 * pending.GIVE_UP:
            if self.waiting.due("x"):
                tried.append(self.clock[0])
                self.waiting.failed("x")
            self.clock[0] += 0.05
        gaps = [later - earlier for earlier, later in zip(tried, tried[1:])]
        self.assertAlmostEqual(gaps[0], pending.FIRST, delta=0.06)
        self.assertTrue(all(later >= earlier - 0.06 for earlier, later in zip(gaps, gaps[1:])), "never shorter")
        self.assertLessEqual(max(gaps), pending.LONGEST + 0.06)
        self.assertTrue(self.waiting.given_up("x"))
        self.assertGreaterEqual(tried[-1] - tried[0], pending.GIVE_UP)
        self.assertLess(tried[-1] - tried[0], pending.GIVE_UP + pending.LONGEST + 0.1)
        self.assertFalse(self.waiting.due("x"))
        self.assertFalse(self.waiting.any_due(["x"], lambda key: False))

    def test_a_listed_item_never_tried_is_due_and_a_served_one_is_not(self):
        self.assertTrue(self.waiting.any_due(["x"], lambda key: False))
        self.assertFalse(self.waiting.any_due(["x"], lambda key: True))
        self.waiting.drop("y")
        self.assertFalse(self.waiting.any_due(["y"], lambda key: False), "a dropped item is never due")

    def test_a_given_up_item_says_so_once(self):
        self.assertFalse(self.waiting.failed("x"))
        self.clock[0] += pending.GIVE_UP
        self.assertTrue(self.waiting.failed("x"), "this failure gave it up")
        self.assertFalse(self.waiting.failed("x"))

    def test_an_item_no_longer_listed_is_forgotten_and_starts_anew(self):
        self.waiting.failed("x")
        self.clock[0] += pending.GIVE_UP
        self.waiting.failed("x")
        self.waiting.drop("z")
        self.waiting.keep(["y"])
        self.assertFalse(self.waiting.given_up("x") or self.waiting.dropped("z"))
        self.assertTrue(self.waiting.due("x") and self.waiting.due("z"))

    def test_serve_lists_what_the_site_has_and_what_was_given_up_in_order(self):
        site = PictureSite()
        site.viewers = 1
        paths = {"a": self.file("a", WHOLE), "b": self.file("b", HALF)}
        self.assertEqual(pending.serve(site, self.waiting, ["b", "a"], store(paths.__getitem__)), (["a"], [], True))
        self.clock[0] += pending.GIVE_UP
        self.assertEqual(pending.serve(site, self.waiting, ["b", "a"], store(paths.__getitem__)),
                         (["b", "a"], ["b"], True))
        self.assertEqual(pending.serve(site, self.waiting, ["b", "a"], store(paths.__getitem__), look_again=True),
                         (["b", "a"], ["b"], False), "the same file is not read again, and nothing changed")
        self.file("b", WHOLE)
        self.assertEqual(pending.serve(site, self.waiting, ["b", "a"], store(paths.__getitem__), look_again=True),
                         (["b", "a"], [], True))

    def test_a_read_that_raises_is_a_failed_try_and_a_refused_id_is_dropped(self):
        from angelo_agent import reference_images
        site = PictureSite()
        site.viewers = 1
        path = self.file("a", WHOLE)

        def gone(store, handle):
            raise OSError("gone")
        with mock.patch.object(reference_images, "read", gone):   # The one reader of a picture's bytes.
            self.assertEqual(pending.serve(site, self.waiting, ["a"], store(lambda identity: path)), ([], [], False))
        self.assertFalse(self.waiting.due("a") or self.waiting.dropped("a"))

        def defect(identity):
            raise AttributeError("a defect, never an answer about the picture")
        with self.assertRaises(AttributeError, msg="a defect ends the page (review of e0fc461e471, NIT-4)"):
            pending.serve(site, self.waiting, ["f"], store(defect))

        def refused(identity):
            raise ValueError("Not an image reference_id")
        self.assertEqual(pending.serve(site, self.waiting, ["b", "c/../d"], store(refused)), ([], [], False))
        self.assertTrue(self.waiting.dropped("b") and self.waiting.dropped("c/../d"))

        def missing(identity):
            raise ImageMissing("No image with that reference_id is attached to this conversation")
        self.assertEqual(pending.serve(site, self.waiting, ["e"], store(missing)), ([], [], False))
        self.assertFalse(self.waiting.dropped("e"), "a file not there yet is waited on, never dropped")

    def test_a_same_size_rewrite_in_its_stamps_step_is_read_again(self):
        """VM CI on c1679adcb59 (the change_clock rule): a size and modification time can't tell a same-size rewrite
        inside one tick of the file system's clock from no write. A mark seen while the clock is still in that step
        earns one more read ``SETTLE_S`` later whatever ``change_clock.settled`` says (a share's skewed clock can
        make it say yes wrongly: break-it review of e0fc461e471, NIT-1), so a capture filled in place at the same
        length is shown, never left unavailable."""
        import os
        from angelo_agent import change_clock
        site = PictureSite()
        site.viewers = 1
        broken = WHOLE[:-1] + b"\x00"   # The same length as the whole picture, never whole.
        path = self.file("a", broken)
        stamp = os.stat(path).st_mtime_ns
        for settled in (True, False):
            with self.subTest(settled=settled):
                waiting = pending.Pending(lambda: self.clock[0])
                site.pictures.clear()
                self.file("a", broken)
                os.utime(path, ns=(stamp, stamp))
                with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: settled):
                    waiting.failed("a")
                    self.clock[0] += pending.GIVE_UP
                    pending.serve(site, waiting, ["a"], store(lambda identity: path))   # Given up, marked.
                    self.file("a", WHOLE)   # Filled in place: the same size, and the same stamp in its tick.
                    os.utime(path, ns=(stamp, stamp))
                    at_once = pending.serve(site, waiting, ["a"], store(lambda identity: path), look_again=True)
                    self.clock[0] += pending.SETTLE_S
                    after = pending.serve(site, waiting, ["a"], store(lambda identity: path), look_again=True)
                self.assertEqual(at_once[1], ["a"], "its one more read waits, as any read does")
                self.assertEqual(after[1], [], "read again SETTLE_S after its mark was first seen, and shown")

    def test_an_unsettled_read_is_followed_by_one_more_once_the_stamp_settles(self):
        """Break-it review of 6daffb057ee, MUST-1: a picture read while its stamp was unsettled, then finished in
        place at the same size within that step, was never read again once the stamp settled (any normal clock:
        a step plus a tick, far under ``SETTLE_S``), so its tile stayed crossed."""
        import os
        from angelo_agent import change_clock
        site = PictureSite()
        site.viewers = 1
        broken = WHOLE[:-1] + b"\x00"   # The same length as the whole picture, never whole.
        path = self.file("a", broken)
        stamp = os.stat(path).st_mtime_ns
        verdict = [False]
        with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: verdict[0]):
            self.waiting.failed("a")
            self.clock[0] += pending.GIVE_UP
            pending.serve(site, self.waiting, ["a"], store(lambda identity: path))   # Read unsettled, given up.
            self.file("a", WHOLE)   # Finished in place within the step: the same size and stamp.
            os.utime(path, ns=(stamp, stamp))
            verdict[0] = True   # A step later the stamp has settled; no SETTLE_S has passed.
            self.clock[0] += 0.1
            first = pending.serve(site, self.waiting, ["a"], store(lambda identity: path), look_again=True)
            again = pending.serve(site, self.waiting, ["a"], store(lambda identity: path), look_again=True)
        self.assertEqual(first, (["a"], [], True), "read once more as soon as it settled, and shown")
        self.assertEqual(again, (["a"], [], False))

    def test_a_mark_is_read_once_more_and_then_never_for_the_same_file(self):
        from angelo_agent import change_clock, reference_images
        site = PictureSite()
        site.viewers = 1
        path = self.file("a", HALF)
        reads = []
        real = reference_images.read
        with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: True), \
                mock.patch.object(reference_images, "read", lambda s, h: reads.append(h) or real(s, h)):
            self.waiting.failed("a")
            self.clock[0] += pending.GIVE_UP
            pending.serve(site, self.waiting, ["a"], store(lambda identity: path))
            for _ in range(20):
                self.clock[0] += pending.SETTLE_S
                pending.serve(site, self.waiting, ["a"], store(lambda identity: path), look_again=True)
        self.assertEqual(len(reads), 2, "its read, and one more SETTLE_S after its mark was first seen")

    def test_the_settle_wait_passes_every_step_a_file_system_stamps_in(self):
        """Every step either table knows (Windows' file systems, and the POSIX volumes and trailing-zero steps of
        ``angelo_change_clock_volumes``), on either side of the clock, plus a tick."""
        import sys
        from angelo_agent import change_clock
        volumes = sys.modules["angelo_change_clock_volumes"]
        steps = list(change_clock.FILE_SYSTEM_STEPS.values()) + list(volumes.VALUE_STEPS)
        for volume in [volumes.UNKNOWN, volumes.LINUX_DISK, *volumes.MAC_TYPES.values()]:
            steps += [volume.step, volume.below]
        coarsest = max(steps) / 1e7   # FILETIME steps of 100 ns.
        self.assertGreater(pending.SETTLE_S, 2 * coarsest + change_clock.TICK / 1e7, "either side of the clock")

    def test_the_site_keeps_more_pictures_than_the_page_lists(self):
        from angelo_agent import session_page_state
        self.assertGreaterEqual(MAX_PICTURES, session_page_state.PICTURES)


class PageTests(unittest.TestCase):
    def script(self):
        script = (ADDON / "session_page/app.js").read_text(encoding="utf-8")
        return script, re.search(r"function renderViews\(ids, unavailable\) \{(.*?)\n\}", script, re.S).group(1)

    def test_the_page_draws_a_given_up_picture_as_unavailable_never_asks_for_it(self):
        script, body = self.script()
        style = (ADDON / "session_page/app.css").read_text(encoding="utf-8")
        self.assertIn("renderViews(state.pictures || [], state.unavailable || [])", script)
        self.assertIn('classList.add("unavailable")', body)
        self.assertLess(body.index("unavailable.includes(id)"), body.index('picture.src = "picture/"'))
        self.assertRegex(style, r"\.views li\.unavailable\{")
        self.assertIn('item.title = text("view_unavailable")', body, "named for sight (review of e0fc461e471, NIT-5)")
        self.assertIn('item.setAttribute("aria-label", item.title)', body, "and for a screen reader")

    def test_no_two_lists_of_views_compare_equal(self):
        """Review of 4afc19981c3, NIT-2: keys joined by a comma made an id with a comma blur two lists."""
        _, body = self.script()
        self.assertNotIn('.join(",")', body)
        self.assertEqual(body.count("JSON.stringify("), 2, "both lists compared as JSON")
        self.assertIn('(unavailable.includes(id) ? "u:" : "v:") + id', body, "a fixed-width mark before the id")


if __name__ == "__main__":
    unittest.main()
