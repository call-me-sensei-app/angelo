# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a live session page waits on is waited on for a bounded time (``session_page_pending``).

Independent break-it review of ffe232b8253, NIT-1: a picture that never read as a whole PNG, JPEG or WebP file stayed
pending while a page was open, and the main thread read it again and republished the page's state every 0.5 s for as
long as the page stayed open. Now an item the page waits on is read again after a growing wait, given up after a
bounded time and shown as unavailable, and the page is published again only when what it shows changed.

Break-it review of 4afc19981c3: a given-up picture whose file changes is read again at the next build (NIT-1); an id
that can never be served is dropped at once, never a tile (NIT-2); a listed picture never read yet is due, so a viewer
leaving between two looks can't skip its first read (NIT-3). Break-it review of 09db060: a failed look at the disk
waits instead of ending the page (NIT-2), and a picture whose file appears later is shown (NIT-3).
The real ``Pages``, the real picture serving (``session_page_pending.shown``) and a clock the test moves; no
Blender, no server.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_page, session_page_pending as pending  # noqa: E402
from angelo_agent.reference_errors import ImageMissing  # noqa: E402
from angelo_agent.session_page_site import _whole  # noqa: E402

WHOLE = b"\x89PNG\r\n\x1a\n" + b"pixels" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
HALF = WHOLE[:14]   # A capture reserved and never finished: never a whole picture.


class PictureSite:
    """``SessionSite``'s picture half: it takes a picture only when it is whole, as the real one does."""

    def __init__(self, marks=None):
        self.url, self.viewers, self.published, self.closed = "http://127.0.0.1:1/s/t/", 0, [], False
        self.scene_feed = None
        self.pictures = {}

    def publish(self, state):
        self.published.append({key: list(value) for key, value in state.items()})

    def publish_picture(self, identity, data):
        if not _whole(data):
            return False
        self.pictures[identity] = data
        return True

    def has_picture(self, identity):
        return identity in self.pictures

    def close(self):
        self.closed = True


class Harness(unittest.TestCase):
    """The real ``Pages`` with the real picture serving over a folder of this conversation's pictures."""

    site_kind = PictureSite

    def setUp(self):
        from angelo_agent import change_clock, reference_images
        self.clock = [100.0]
        # One clock (integrator, batch 5): the change clock judges a file's stamps against the system time, so it runs
        # on as the pages' clock does; never behind the real one, which stamps the files written here.
        started, real_time, base = self.clock[0], change_clock.system_time, change_clock.system_time()
        patcher = mock.patch.object(change_clock, "system_time", lambda: max(
            real_time(), base + int((self.clock[0] - started) * 10_000_000)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.folder, True)
        self.locates, self.reads, self.listed = [], [], ["view-1"]
        self.refused = set()   # Ids ``attached_image`` refuses for good, as it does a non-UUID or a too-large file.
        self.broken = {}       # Id -> the OSError a look at the disk raises for it.
        self.unread = 0        # Reads still to fail with an OSError (a sharing violation, a share's reconnect).
        self.short = 0         # Reads still to return fewer bytes than the file holds (a truncation, a flaky share).
        real_read = reference_images.read

        def read(store, handle):   # The one read of a picture's bytes (``reference_images``).
            self.reads.append(handle)
            if self.unread:
                self.unread -= 1
                raise PermissionError(13, "Permission denied")
            if self.short:
                self.short -= 1
                return real_read(store, handle)[:-3]
            return real_read(store, handle)
        patcher = mock.patch.object(reference_images, "read", read)
        patcher.start()
        self.addCleanup(patcher.stop)

        def attached_image(identity):   # ``ReferenceStore.attached_image``'s answers.
            self.locates.append(identity)
            path = self.folder / (identity + ".png")
            if identity in self.broken:
                raise self.broken[identity]
            if identity in self.refused:
                raise ValueError("That image is larger than the 20 MiB an attachment may be")
            if not path.exists():
                raise ImageMissing("No image with that reference_id is attached to this conversation")
            return path
        self.controller = SimpleNamespace(state={"id": "c1", "messages": []}, version=1, references=SimpleNamespace(
            attached_image=attached_image, image_limit=5 * 1024 * 1024))
        self.pages = session_page.Pages(
            make_site=self.site_kind, clock=lambda: self.clock[0], retry=pending.retry,
            build=lambda controller, entry: pending.shown(controller, entry, {"pictures": list(self.listed)}))
        self.write("view-1", HALF)
        self.pages.link(self.controller, "codex")
        self.site = self.pages.site("c1")
        self.site.viewers = 1   # A page is open.

    def write(self, identity, data):
        (self.folder / (identity + ".png")).write_bytes(data)

    def run_for(self, seconds, step=0.1, change=None):
        for tick in range(int(round(seconds / step))):
            self.clock[0] += step
            if change and tick % change == 0:
                self.controller.version += 1
            self.pages.pump(lambda identity: self.controller)

    def last(self):
        return self.site.published[-1]["pictures"], self.site.published[-1]["unavailable"]



class BoundedWaitTests(Harness):
    def test_a_picture_that_never_reads_whole_stops_being_read_and_the_page_stops_being_republished(self):
        self.run_for(10 * 60)   # Ten minutes of 0.1 s ticks with a page open and nothing changing in the chat.
        self.assertLessEqual(len(self.reads), 16, "read again with a growing wait, then never again")
        self.assertGreaterEqual(len(self.reads), 5, "a capture still being written gets several chances")
        self.assertEqual(self.last(), (["view-1"], ["view-1"]), "shown as unavailable")
        self.assertLessEqual(len(self.site.published), 3, "the first publish, the first viewer's, the give-up's")
        work, locates = (len(self.reads), len(self.site.published)), len(self.locates)
        self.run_for(10 * 60)
        self.assertEqual((len(self.reads), len(self.site.published)), work, "settled: never read or published again")
        self.assertLessEqual(len(self.locates) - locates, 10 * 60 / pending.LONGEST + 1, "a stat every LONGEST")

    def test_a_wait_alone_never_republishes_and_an_arrival_publishes_once(self):
        self.run_for(1)
        before = len(self.site.published)
        self.assertEqual(self.last(), ([], []), "never listed while the page can't serve it")
        self.run_for(5)
        self.assertEqual(len(self.site.published), before, "failed reads change nothing on the page")
        self.write("view-1", WHOLE)   # The capture is finished.
        self.run_for(10)
        self.assertEqual(len(self.site.published), before + 1, "one publish for its arrival")
        self.assertEqual(self.last(), (["view-1"], []))
        reads = len(self.reads)
        self.run_for(60, change=5)
        self.assertEqual(len(self.reads), reads, "a served picture is never read again")

    def test_a_changing_chat_reads_a_waiting_picture_no_faster_than_its_wait(self):
        self.run_for(30, change=5)   # The conversation changes every 0.5 s (a streaming turn): published each time.
        self.assertGreater(len(self.site.published), 30)
        self.assertLessEqual(len(self.reads), 8, "each read waits its turn, whatever else changes")
        self.run_for(10 * 60, change=5)
        self.assertLessEqual(len(self.reads), 16, "and is given up all the same: an unchanged file is not read")
        self.assertEqual(self.last(), (["view-1"], ["view-1"]))

    def test_a_clock_that_never_settles_still_bounds_every_read(self):
        """VM CI on 0ce7c3a7bf74 (Linux): there the mark of an unchanged file never read as settled, so a given-up
        picture was read again at every build (217 reads in ten minutes). An unsettled mark earns one more read,
        paced like any other, and then counts as settled by the time passed, whatever the clock says (a coarse
        drive, a share, a clock that never settles)."""
        from angelo_agent import change_clock
        with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: False):
            self.run_for(10 * 60, change=5)   # The chat changes every 0.5 s: a build each time.
            self.assertLessEqual(len(self.reads), 17, "the wait's reads and at most one more")
            self.assertEqual(self.last(), (["view-1"], ["view-1"]))
            reads = len(self.reads)
            self.run_for(10 * 60, change=5)
            self.assertEqual(len(self.reads), reads, "an unchanged file is never read again")
            self.write("view-1", WHOLE)   # Its file changes: read at the next build, as on any clock.
            self.run_for(1, change=5)
        self.assertEqual(self.last(), (["view-1"], []))

    def test_a_failed_look_forgets_nothing_about_a_given_up_file(self):
        """Break-it review of 6daffb057ee, NIT-1: a failed look (a flaky share's stat) cleared a given-up picture's
        mark, so its unchanged file was read again at the next build that looked, without end."""
        from angelo_agent import change_clock
        with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: True):
            self.run_for(10 * 60)
            reads = len(self.reads)
            for _ in range(20):   # Every other build's look fails.
                self.broken["view-1"] = OSError(5, "Input/output error")
                self.controller.version += 1
                self.run_for(1)
                del self.broken["view-1"]
                self.controller.version += 1
                self.run_for(1)
        self.assertEqual(len(self.reads), reads, "an unchanged file is never read again")
        self.assertEqual(self.last(), (["view-1"], ["view-1"]))

    def test_a_given_up_picture_whose_file_changes_is_shown_within_one_look_even_in_an_idle_chat(self):
        """Review of 4afc19981c3, NIT-1: a picture finished after the wait was given up stayed a crossed tile; and
        (coordinator, 2026-11-19: "never wrongly unavailable" outranks "no timer") one finished while the chat is
        idle was shown only at the next change in the chat."""
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)   # Finished at last (a slow disk, a tool that lists a view before its job ends).
        reads = len(self.reads)
        self.run_for(pending.LONGEST + 0.5)   # Nothing changes in the chat.
        self.assertEqual(self.last(), (["view-1"], []), "its changed file is read and shown")
        self.assertEqual(len(self.reads), reads + 1)

    def test_a_failed_read_vouches_for_no_mark_after_give_up(self):
        """Break-it review of e0fc461e471, MUST-1: a read that raised (a Windows sharing violation from an antivirus or
        backup, a share's reconnect, a file made mode 000 and then opened) recorded its mark as read, so the whole,
        unchanged picture stayed unavailable for good."""
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)   # Finished at last; its first read fails.
        self.unread = 1
        self.run_for(pending.LONGEST + 0.5)   # Its next look sees the new mark: the read fails.
        self.assertEqual(self.last(), (["view-1"], ["view-1"]))
        self.run_for(pending.LONGEST + 0.5)   # The next paced look reads it again, in an idle chat.
        self.assertEqual(self.last(), (["view-1"], []), "shown")

    def test_reads_that_fail_through_the_wait_never_vouch_for_a_mark(self):
        self.write("view-1", WHOLE)   # Whole from the start, but every read fails for the first 70 s.
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70)
        self.assertEqual(self.last(), (["view-1"], ["view-1"]), "given up, a tile")
        self.unread = 0
        self.run_for(pending.GIVE_UP + pending.LONGEST + 0.5)   # The first look at or after its retry time.
        self.assertEqual(self.last(), (["view-1"], []), "read at its next retry once reads work again")

    def test_failing_reads_are_tried_at_most_once_per_longest_never_at_each_build(self):
        self.write("view-1", WHOLE)
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70)
        reads = len(self.reads)
        self.run_for(10 * 60, change=5)   # A build every 0.5 s.
        self.assertLessEqual(len(self.reads) - reads, 10 * 60 / pending.LONGEST + 1, "one try per LONGEST")
        self.site.viewers = 0
        reads = len(self.reads)
        self.run_for(10 * 60, change=5)
        self.assertEqual(len(self.reads), reads, "none while nobody views")

    def test_a_failing_read_is_tried_again_at_a_build_only_for_a_new_mark(self):
        """VM CI of 708978dcfd5: a read that raised never recorded the mark it saw, so every build after give-up
        took the file's mark for a new one and read it again (1172 reads in ten minutes against a bound of 76). Stated
        as the bound (break-it review of b4dbd6b3c78, SHOULD-1): over N seconds of builds an unchanged failing file
        gets at most N / LONGEST + 1 tries, and a new mark one read at the next build."""
        self.write("view-1", WHOLE)
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70)
        self.assertEqual(self.last(), (["view-1"], ["view-1"]), "given up, a tile")
        reads = len(self.reads)
        self.run_for(60, change=5)   # Builds every 0.5 s, the file unchanged.
        self.assertLessEqual(len(self.reads) - reads, 60 / pending.LONGEST + 1)
        self.write("view-1", HALF)   # Rewritten: a new mark.
        self.controller.version += 1
        reads = len(self.reads)
        self.run_for(0.1)
        self.assertEqual(len(self.reads), reads + 1, "a new mark is read at the next build")

    def shown_after_one_failed_read(self, change):
        """Give up on a capture never finished, then it is finished and its first read fails (a scanner holding the
        new file, a share's reconnect): it is shown within two LONGEST whatever the build rate."""
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)
        self.unread = 1
        self.run_for(2 * pending.LONGEST + session_page.INTERVAL + 0.5, change=change)
        self.assertEqual(self.last(), (["view-1"], []), "shown")

    def test_a_read_that_failed_once_is_shown_in_an_idle_chat(self):
        self.shown_after_one_failed_read(None)

    def test_a_read_that_failed_once_is_shown_while_the_chat_changes_faster_than_longest(self):
        """Break-it review of b4dbd6b3c78, MUST-1: each build pushed the paced look 8 s ahead, so in a chat changing
        faster than that a failed read was never tried again until the chat went quiet."""
        self.shown_after_one_failed_read(5)

    def test_a_read_that_failed_once_is_shown_while_the_chat_changes_slower_than_longest(self):
        self.shown_after_one_failed_read(40)

    def test_reads_that_failed_through_the_wait_are_shown_once_they_work_in_a_busy_chat(self):
        """The bound's liveness twin (SHOULD-1): failing reads back off (NIT-3) but never stop."""
        self.write("view-1", WHOLE)
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70, change=5)
        self.assertEqual(self.last(), (["view-1"], ["view-1"]))
        self.run_for(10 * 60, change=5)   # Still failing: tried less and less often, never at each build.
        self.unread = 0
        self.run_for(pending.GIVE_UP + session_page.INTERVAL + 0.5, change=5)
        self.assertEqual(self.last(), (["view-1"], []), "shown within the longest backoff once reads work")

    def test_reads_that_failed_for_long_are_shown_in_an_idle_chat_once_they_work(self):
        """Delta re-read of c8f7c307dd9, NIT-2: at full backoff in an idle chat the retry waits for the first paced
        look at or after its time, so the bound is GIVE_UP plus one LONGEST, and it holds."""
        self.write("view-1", WHOLE)
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70)
        self.run_for(10 * 60)   # Failing all along, idle: the backoff reaches GIVE_UP.
        self.unread = 0
        self.run_for(pending.GIVE_UP + pending.LONGEST + 0.5)
        self.assertEqual(self.last(), (["view-1"], []), "shown")

    def test_a_whole_picture_is_shown_whatever_the_size_its_descriptor_reports(self):
        """Delta re-read of c8f7c307dd9, NIT-1: a read whose length differed from fstat was never published, so on a
        file system whose fstat never matches the bytes (some FUSE or overlay ones) the picture was never shown. The
        mismatch only means its mark vouches for nothing; whether the bytes are a picture is publish_picture's call."""
        import contextlib
        from angelo_agent import reference_images
        real_opened = reference_images.opened

        class Skewed:
            def __init__(self, status):
                self._status = status

            def __getattr__(self, name):
                value = getattr(self._status, name)
                return value + 1 if name == "st_size" else value

        @contextlib.contextmanager
        def opened(store, identity):
            with real_opened(store, identity) as (path, status, handle):
                yield path, Skewed(status), handle
        real_restat = reference_images.restat
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)
        with mock.patch.object(reference_images, "opened", opened), \
                mock.patch.object(reference_images, "restat", lambda handle: Skewed(real_restat(handle))):
            self.run_for(pending.LONGEST + 0.5)
        self.assertEqual(self.last(), (["view-1"], []), "shown")

    def test_a_mismatched_read_is_shown_only_once_its_stamps_are_past_the_clock_step(self):
        """Integrator, batch 3 (test_native_change_clock): the re-stat after a mismatched read compared times that
        were not settled, and within one tick of the file system's clock a same-size rewrite keeps size and time, so a
        torn read matched. Its bytes are shown only once the stamps taken at open are past the step
        (``change_clock.settled``); before that it waits, as an unread file does."""
        import contextlib
        from angelo_agent import change_clock, reference_images
        real_opened, real_restat = reference_images.opened, reference_images.restat

        class Skewed:
            def __init__(self, status):
                self._status = status

            def __getattr__(self, name):
                value = getattr(self._status, name)
                return value + 1 if name == "st_size" else value

        @contextlib.contextmanager
        def opened(store, identity):
            with real_opened(store, identity) as (path, status, handle):
                yield path, Skewed(status), handle
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)
        with mock.patch.object(reference_images, "opened", opened), \
                mock.patch.object(reference_images, "restat", lambda handle: Skewed(real_restat(handle))), \
                mock.patch.object(change_clock, "settled", lambda *args, **kwargs: False):
            self.run_for(pending.LONGEST + 0.5)
            self.assertEqual(self.last(), (["view-1"], ["view-1"]), "not shown while the stamps could hide a rewrite")
        with mock.patch.object(reference_images, "opened", opened), \
                mock.patch.object(reference_images, "restat", lambda handle: Skewed(real_restat(handle))), \
                mock.patch.object(change_clock, "settled", lambda *args, **kwargs: True):
            self.run_for(pending.GIVE_UP + pending.LONGEST + 0.5)
        self.assertEqual(self.last(), (["view-1"], []), "shown once settled")

    def test_a_file_rewritten_during_a_mismatched_read_is_not_shown_until_it_holds_still(self):
        """Re-read of 894dd997c19, N-1: publishing whole bytes on a size mismatch let a rewrite during the read tear
        the picture (the old file's head, the new one's tail). It is published only when the descriptor's size and
        time read again after the read are those taken at open; otherwise it is tried again later."""
        import contextlib
        from angelo_agent import reference_images
        real_opened, real_restat, moved = reference_images.opened, reference_images.restat, [2]

        class Shifted:
            def __init__(self, status, size=0, time=0):
                self._status, self._size, self._time = status, size, time

            def __getattr__(self, name):
                value = getattr(self._status, name)
                return value + self._size if name == "st_size" else value + self._time if name == "st_mtime_ns" else value

        @contextlib.contextmanager
        def opened(store, identity):
            with real_opened(store, identity) as (path, status, handle):
                yield path, Shifted(status, size=1), handle

        def restat(handle):   # The first two reads see the file change under them.
            if moved[0]:
                moved[0] -= 1
                return Shifted(real_restat(handle), size=1, time=1)
            return Shifted(real_restat(handle), size=1)
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)
        with mock.patch.object(reference_images, "opened", opened), mock.patch.object(reference_images, "restat", restat):
            self.run_for(pending.LONGEST + 0.5)
            self.assertEqual(self.last(), (["view-1"], ["view-1"]), "not shown while it moved")
            self.run_for(pending.GIVE_UP + pending.LONGEST + 0.5)
        self.assertEqual(self.last(), (["view-1"], []), "shown once it held still")

    def test_repeated_failing_reads_back_off(self):
        """Break-it review of b4dbd6b3c78, NIT-3: a read that keeps failing on an unchanged file was retried every
        LONGEST at full cost for as long as the page stayed open; it backs off to GIVE_UP."""
        self.write("view-1", WHOLE)
        self.unread = 10 ** 6
        self.controller.version += 1
        self.run_for(70)
        reads = len(self.reads)
        self.run_for(30 * 60, change=5)
        self.assertLessEqual(len(self.reads) - reads, 30 * 60 / pending.GIVE_UP + 5)

    def test_a_short_read_vouches_for_no_mark(self):
        """Break-it review of b4dbd6b3c78, NIT-1: a read that returned fewer bytes than the file's fstat recorded its
        mark, so a whole picture whose reads came back short stayed unavailable for good."""
        self.run_for(10 * 60)
        self.write("view-1", WHOLE)
        self.short = 2
        self.run_for(30 * 60)
        self.assertEqual(self.last(), (["view-1"], []), "shown")

    def test_only_a_refusal_drops_a_picture_any_other_value_error_is_a_defect(self):
        """Break-it review of b4dbd6b3c78, NIT-4: ``except ValueError`` around the whole look also caught a defect's
        ValueError (in ``_mark``, ``settled``, ``worth_reading``) and dropped the picture silently."""
        from angelo_agent import reference_images
        self.assertTrue(issubclass(reference_images.Refused, ValueError))

        def defect(status):
            raise ValueError("defect")
        with mock.patch.object(pending, "_mark", defect):
            self.run_for(5)
        self.assertTrue(self.site.closed, "a defect ends that page")

    def test_a_given_up_look_is_a_stat_every_longest_and_none_while_nobody_views(self):
        """The bound on the look that keeps a given-up picture from staying wrongly unavailable: per listed item at
        most one look (``attached_image`` and one ``os.stat``, never its bytes) every ``LONGEST``, and nothing at all
        while no page is open."""
        identities = ["view-%d" % n for n in range(6)]   # ``session_page_state.PICTURES``: the most a page lists.
        for identity in identities:
            self.write(identity, HALF)
        self.listed = identities
        self.controller.version += 1
        from angelo_agent import change_clock
        marks = []
        real = pending._mark
        with mock.patch.object(change_clock, "settled", lambda witness, *a, **k: True), \
                mock.patch.object(pending, "_mark", lambda status: marks.append(status) or real(status)):
            self.run_for(2 * 60)   # All given up.
            self.assertEqual(self.last()[1], identities)
            work = len(self.locates), len(marks), len(self.reads), len(self.site.published)
            seconds = 10 * 60
            self.run_for(seconds)   # Idle, viewed.
            bound = len(identities) * (seconds / pending.LONGEST + 1)
            self.assertLessEqual(len(self.locates) - work[0], bound, "items x looks per interval")
            self.assertLessEqual(len(marks) - work[1], bound, "one stat a look")
            self.assertGreater(len(marks) - work[1], len(identities) * (seconds / pending.LONGEST - 2), "it looks")
            self.assertEqual((len(self.reads), len(self.site.published)), work[2:], "an unchanged file: no read")
            self.site.viewers = 0
            work = len(self.locates), len(marks)
            self.run_for(seconds)
            self.assertEqual((len(self.locates), len(marks)), work, "nobody viewing: no look at all")

    def test_an_id_that_can_never_be_served_is_dropped_at_once_never_a_tile(self):
        """Review of 4afc19981c3, NIT-2: a non-UUID or too-large id, or one the page can't serve, waited the full
        minute and became an unavailable tile; a script's returned JSON can list any string as a view."""
        self.write("view-big", WHOLE)
        self.refused.update({"view-big", "view-nonuuid"})   # ``attached_image``'s ValueErrors for good.
        self.listed = ["not a view, id", "view-nonuuid", "view-big", "view-1"]
        self.controller.version += 1
        self.run_for(10 * 60, change=5)
        self.assertNotIn("not a view, id", self.locates, "an id the page can't serve is never even looked up")
        self.assertEqual((self.locates.count("view-nonuuid"), self.locates.count("view-big")), (1, 1), "tried once")
        self.assertEqual(self.last(), (["view-1"], ["view-1"]), "never shown, as a picture or as a tile")

    def test_a_failed_look_at_the_disk_waits_and_never_ends_the_page(self):
        """Review of 09db060, NIT-2: an OSError from ``attached_image`` (a PermissionError from ``is_file``, a file
        gone between ``is_file`` and ``stat``) escaped the wait and ended the whole page "after an error"."""
        self.broken["view-locked"] = PermissionError(13, "Permission denied")
        self.listed = ["view-locked", "view-raced", "view-1"]
        from angelo_agent import shared_replace
        real_open = shared_replace.open_file

        def gone(path, flags):   # Its file goes between ``attached_image`` and the open.
            if Path(path).stem == "view-raced":
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return real_open(path, flags)
        self.write("view-raced", WHOLE)
        self.controller.version += 1
        with mock.patch.object(shared_replace, "open_file", gone):
            self.run_for(10 * 60)
        self.assertFalse(self.site.closed, "the page goes on")
        everything = ["view-locked", "view-raced", "view-1"]
        self.assertEqual(self.last(), (everything, everything), "each waited on, then given up: a tile")
        bound = 16 + 10 * 60 / pending.LONGEST + 1   # The wait's tries, then a given-up look every LONGEST.
        self.assertLessEqual(self.locates.count("view-locked"), bound, "waited on, with the same bounds")
        self.assertGreater(self.locates.count("view-locked"), 4)
        self.run_for(1)
        self.controller.version += 1   # Looked at again at a build: the race is over, its file whole.
        self.run_for(1)
        self.assertIn("view-raced", self.last()[0])
        self.assertNotIn("view-raced", self.last()[1])

    def test_a_picture_whose_file_appears_later_is_shown(self):
        """Review of 09db060, NIT-3: a missing file was dropped as if its id could never be valid, so a picture
        listed before its file was written was never shown while listed."""
        self.listed = ["view-late", "view-later"]
        self.controller.version += 1
        self.run_for(5)
        self.assertEqual(self.last(), ([], []), "not listed while there is nothing to serve")
        self.write("view-late", WHOLE)   # Written within the wait: shown at its next read, with no change in the chat.
        self.run_for(10)
        self.assertEqual(self.last(), (["view-late"], []))
        self.run_for(10 * 60)   # Its file never came: given up, a tile, looked for every LONGEST.
        self.assertEqual(self.last(), (["view-late", "view-later"], ["view-later"]))
        locates = self.locates.count("view-later")
        self.run_for(60)
        self.assertLessEqual(self.locates.count("view-later") - locates, 60 / pending.LONGEST + 1)
        self.write("view-later", WHOLE)
        self.run_for(pending.LONGEST + 0.5)   # An idle chat: its next look finds it.
        self.assertEqual(self.last(), (["view-late", "view-later"], []))

    def test_a_failed_read_of_a_file_that_is_there_still_waits(self):
        self.listed = ["view-dir"]   # A file there whose read fails (OSError): not ready yet, never dropped.
        (self.folder / "view-dir.png").mkdir()
        self.controller.version += 1
        self.run_for(10 * 60)
        self.assertGreater(self.locates.count("view-dir"), 4)
        self.assertEqual(self.last(), (["view-dir"], ["view-dir"]))

    def test_a_defect_in_a_wait_ends_that_page_alone_never_the_pump(self):
        self.run_for(1)

        def broken(identity):
            raise RuntimeError("defect")
        self.controller.references.attached_image = broken
        self.run_for(5)   # Raises nothing here: the page's defect ends that page.
        self.assertTrue(self.site.closed)
        self.assertIsNone(self.pages.site("c1"))

    def test_nobody_looking_reads_nothing(self):
        self.site.viewers = 0
        work = len(self.locates), len(self.reads)
        self.run_for(120, change=5)
        self.assertEqual((len(self.locates), len(self.reads)), work)

    def test_a_later_picture_is_waited_on_apart_from_one_given_up(self):
        self.run_for(10 * 60)
        self.listed = ["view-2", "view-1"]   # The agent got another view: a change in the chat.
        self.write("view-2", WHOLE)
        self.controller.version += 1
        self.run_for(1)
        self.assertEqual(self.last(), (["view-2", "view-1"], ["view-1"]))


class RacySite(PictureSite):
    """A site whose open pages change between two looks, as a server thread's stream ending mid-tick does."""

    looks = None

    @property
    def viewers(self):
        return self.looks.pop(0) if self.looks else self._viewers

    @viewers.setter
    def viewers(self, value):
        self._viewers = value


class ViewerRaceTests(Harness):
    """Review of 4afc19981c3, NIT-3: the last viewer left between the pump's look and the read's, so the publish
    recorded the chat as seen with nothing read, and a later viewer of an idle chat got no pictures."""

    site_kind = RacySite

    def test_a_viewer_leaving_between_two_looks_never_skips_a_first_read(self):
        self.write("view-1", WHOLE)
        self.site.viewers = 0
        self.site.looks = [1, 0]   # The pump sees a page; by the build it has gone.
        self.controller.version += 1
        self.run_for(1)
        self.assertEqual(self.last(), ([], []), "nothing read while nobody looked")
        self.site.viewers = 1   # A page opens on the idle chat.
        self.run_for(2)
        self.assertEqual(self.last(), (["view-1"], []), "its first read is due all the same")


class CoarseClockBoundedWaitTests(BoundedWaitTests):
    """Integrator, batch 5 (Linux, 6d692a3b65b): every bounded wait again on a file system whose clock steps (2 s, as
    the change clock judges a volume it can't name). macOS's APFS needs no clock, so these held there and failed on
    Linux's ext4, whose stamps come from the kernel's tick clock."""

    def setUp(self):
        from angelo_agent import change_clock
        coarse = change_clock.Volume(20_000_000, 20_000_000, None, False, True)
        patcher = mock.patch.object(change_clock, "volume", lambda witness: coarse)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()


if __name__ == "__main__":
    unittest.main()
