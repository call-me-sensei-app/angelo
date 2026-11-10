# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the web link's live frames count as changed (``web_link_live``; doc/angelo/web_link.rst, "Live frames"), on
a stand-in ``bpy``.

Covered: pixel writes that reach no depsgraph update (break-it round 8, M1) are found by polling Angelo's native pixel
revision, at most every ``POLL_SECONDS``, and only in a build that has it; an image losing its unsaved pixels is a
change (round 8, M2); render results and Viewer pictures are left out; materials, images and node groups reach the
objects that use them; ``due`` asks for a publish when pixels changed, even while a still-scene check waits (round 9,
M1), and when the check is due; an image file the clock passes after a save is no change (round 13's follow-up), and
the bytes hashed for files the clock hasn't passed are bounded per call. What the depsgraph evaluated:
``test_native_web_link_users``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import web_link_live as live  # noqa: E402


def picture(uid, kind="IMAGE", dirty=False, revision=0):
    return SimpleNamespace(session_uid=uid, type=kind, is_dirty=dirty, angelo_pixel_revision=revision,
                           source="GENERATED", packed_file=None, filepath="", size=(4, 4), channels=4)


def fake_bpy(images, native=True):
    properties = {"angelo_pixel_revision": object()} if native else {}
    return SimpleNamespace(data=SimpleNamespace(images=images, materials=[], node_groups=[]),
                           types=SimpleNamespace(Image=SimpleNamespace(bl_rna=SimpleNamespace(properties=properties))),
                           path=SimpleNamespace(abspath=lambda path, library=None: path))


class PixelPollTests(unittest.TestCase):
    def setUp(self):
        live.reset()
        self.addCleanup(live.reset)
        self.images = [picture(1), picture(2, dirty=True, revision=5)]

    def poll(self, native=True):
        live._session["poll_at"] = 0.0   # Its time has come.
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(self.images, native)}):
            return live.pixels_changed()

    def test_a_pixel_write_with_no_depsgraph_update_starts_a_publish(self):
        self.assertFalse(self.poll(), "the first poll only records")
        self.assertFalse(self.poll(), "nothing changed")
        self.images[0].angelo_pixel_revision += 1   # pixels[...] =, foreach_set, update(), scale()
        self.assertTrue(self.poll())
        self.assertEqual(live._session["dirty_data"], {1}, "its users join the next frame")
        self.assertFalse(self.poll(), "counted once")

    def test_dropping_unsaved_pixels_is_a_change(self):
        """``Image.buffers_free`` puts an edited image back as saved without moving its revision."""
        self.poll()
        self.images[1].is_dirty = False
        self.assertTrue(self.poll())
        self.assertEqual(live._session["dirty_data"], {2})

    def test_the_poll_reads_at_most_every_poll_seconds(self):
        self.poll()
        self.images[0].angelo_pixel_revision += 1
        live._session["poll_at"] = float("inf")
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(self.images)}), \
                mock.patch.object(live, "image_changes", side_effect=AssertionError("read early")):
            self.assertFalse(live.pixels_changed())

    def test_without_the_native_revision_nothing_is_read(self):
        with mock.patch.object(live, "image_changes", side_effect=AssertionError("pixels read")):
            self.assertFalse(self.poll(native=False))

    def test_rendered_pictures_are_left_out(self):
        """Drawing a render slot or the compositor's Viewer marks them changed; a glTF never holds them."""
        self.images += [picture(3, "RENDER_RESULT"), picture(4, "COMPOSITING")]
        self.poll()
        self.images[2].angelo_pixel_revision += 1
        self.images[3].angelo_pixel_revision += 1
        self.assertFalse(self.poll())
        self.assertNotIn(3, live._session["image_stamps"])


class FileBudgetTests(unittest.TestCase):
    def test_files_are_stamped_in_turn_within_a_budget(self):
        """Round 10, L1: every file of every image was statted each poll; now at most ``FILE_BUDGET`` files a call,
        the rest keeping their last stamp, and every image's turn comes."""
        live.reset()
        self.addCleanup(live.reset)
        images = [picture(index) for index in range(1, 6)]
        for image in images:
            image.source = "FILE"
        stamped = []
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(images)}), mock.patch.object(live, "FILE_BUDGET", 2), \
                mock.patch.object(live.users, "file_stamp", lambda image, *_: stamped.append(image.session_uid)):
            live.image_changes()
            self.assertEqual(sorted(stamped), [1, 2, 3, 4, 5], "a first sight stamps each")
            turns = []
            for _ in range(3):
                del stamped[:]
                live.image_changes()
                turns.append(list(stamped))
        self.assertEqual([len(turn) for turn in turns], [2, 2, 2])
        self.assertEqual(sorted({uid for turn in turns for uid in turn}), [1, 2, 3, 4, 5])

    def test_the_budget_counts_files_not_images(self):
        """Round 11, L3: a sequence read at several frames or a multi-view image stats several files."""
        live.reset()
        self.addCleanup(live.reset)
        images = [picture(index) for index in range(1, 6)]
        for image in images:
            image.source = "FILE"
        stamped = []

        def three_files(image, *_):
            stamped.append(image.session_uid)
            return ("a", "b", "c")
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(images)}), mock.patch.object(live, "FILE_BUDGET", 4), \
                mock.patch.object(live.users, "file_stamp", three_files):
            live.image_changes()
            del stamped[:]
            live.image_changes()
        self.assertEqual(len(stamped), 2, "4 files: the first image's 3, then one more image crosses the budget")


class SettlingFileTests(unittest.TestCase):
    """Image files the file system's clock hasn't passed yet (a FAT, exFAT or network volume just after a save)."""

    def setUp(self):
        live.reset()
        live.users.KEYS.clear()
        self.addCleanup(live.reset)
        self.addCleanup(live.users.KEYS.clear)
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-web-settle-"))
        self.addCleanup(lambda: [path.unlink() for path in self.folder.iterdir()] and self.folder.rmdir())

    def filed(self, uid, data):
        (self.folder / ("%d.png" % uid)).write_bytes(data)
        image = picture(uid)
        image.__dict__.update(source="FILE", packed_files=[], filepath_raw=str(self.folder / ("%d.png" % uid)),
                              library=None, tiles=[])
        return image

    def changes(self, images, settled):
        module = live.users.change_keys.change_clock
        clock = contextlib.nullcontext() if settled else mock.patch.multiple(
            module, settle=mock.Mock(side_effect=module.Unsettled("tick")), settled=mock.Mock(return_value=False))
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(images)}), clock:
            return live.image_changes()

    def test_the_clock_passing_a_saved_file_is_no_change(self):
        """Round 13's follow-up: after every save an untouched file read as changed exactly once more, when the
        clock passed it (one extra live update)."""
        images = [self.filed(1, b"saved a moment ago")]
        self.changes(images, settled=False)
        self.assertEqual(self.changes(images, settled=False), set())
        self.assertEqual(self.changes(images, settled=True), set(), "the clock passed it; nothing was written")
        self.assertEqual(self.changes(images, settled=True), set())
        (self.folder / "1.png").write_bytes(b"saved again, later")
        self.assertEqual(self.changes(images, settled=True), {1})

    def test_bytes_hashed_per_call_are_bounded_and_a_deferred_image_is_no_change(self):
        images = [self.filed(uid, b"z" * 100) for uid in (1, 2, 3)]
        reads = []
        digest = live.users.change_keys._digest
        with mock.patch.object(live, "HASH_BYTES", 150), mock.patch.object(
                live.users.change_keys, "_digest", lambda *args: (digest(*args), reads.append(args[0]))[0]):
            self.assertEqual(self.changes(images, settled=False), set())
            self.assertEqual(len(reads), 2, "the second file crosses the bound and is read whole; the third waits")
            self.assertNotIn(3, live._session["image_stamps"], "not stamped yet")
            for _ in range(3):
                self.assertEqual(self.changes(images, settled=False), set(), "a first stamp is never a change")
            self.assertEqual(sorted(live._session["image_stamps"]), [1, 2, 3])

    def test_an_image_begun_within_the_bound_is_read_whole_so_every_write_is_seen(self):
        """Break-it review, L4: an image whose unsettled files passed the bound part-way was deferred at every poll,
        so a tool rewriting it faster than its clock's step was never seen."""
        for tile in (1001, 1002, 1003):
            (self.folder / ("wood.%d.png" % tile)).write_bytes(b"t" * 100)
        image = self.filed(1, b"unused")
        image.__dict__.update(source="TILED", filepath_raw=str(self.folder / "wood.<UDIM>.png"),
                              tiles=[SimpleNamespace(number=n, is_generated_tile=False) for n in (1001, 1002, 1003)])
        with mock.patch.object(live, "HASH_BYTES", 150):
            self.changes([image], settled=False)
            for index in range(3):
                (self.folder / "wood.1003.png").write_bytes(bytes([65 + index]) * 100)   # Same size, in its step.
                self.assertEqual(self.changes([image], settled=False), {1}, "write %d" % index)


class DueTests(unittest.TestCase):
    """``due``: what ``engine_link._tick`` publishes on while no edit waits."""

    def setUp(self):
        live.reset()
        self.addCleanup(live.reset)
        self.images = [picture(1)]
        self.clock = SimpleNamespace(now=1000.0)
        clock = mock.patch.object(live, "time", SimpleNamespace(monotonic=lambda: self.clock.now))
        clock.start()
        self.addCleanup(clock.stop)

    def due(self):
        with mock.patch.dict(sys.modules, {"bpy": fake_bpy(self.images)}):
            return live.due()

    def test_pixels_are_polled_while_a_still_scene_check_waits(self):
        """Round 9, M1: after a delta the check waits up to a minute (its back-off); a pixel write in that time
        publishes at the next poll, and the check keeps its own time."""
        self.assertIsNone(self.due(), "the first poll only records")
        live._session.update(verify_due=self.clock.now + 50.0, verify_wait=32.0)   # A warmed back-off.
        self.clock.now += live.POLL_SECONDS
        self.images[0].angelo_pixel_revision += 1
        self.assertEqual(self.due(), self.clock.now)
        self.assertEqual(live._session["verify_due"], 1050.0, "the check keeps its time")

    def test_a_due_check_publishes_at_once_and_a_future_one_waits(self):
        self.due()
        live._session["verify_due"] = self.clock.now + 5.0
        self.clock.now += live.POLL_SECONDS
        self.assertIsNone(self.due())
        self.clock.now += 5.0
        self.assertEqual(self.due(), 0.0)


class ChangedObjectsTests(unittest.TestCase):
    def test_data_materials_images_and_groups_reach_their_users(self):
        image, group_image = SimpleNamespace(session_uid=900), SimpleNamespace(session_uid=901)
        group = SimpleNamespace(session_uid=800, nodes=[SimpleNamespace(image=group_image)])
        textured = SimpleNamespace(session_uid=700, node_tree=SimpleNamespace(
            session_uid=701, nodes=[SimpleNamespace(image=image), SimpleNamespace(node_tree=group)]))
        plain = SimpleNamespace(session_uid=710, node_tree=None)

        def obj(uid, data, *materials):
            return SimpleNamespace(session_uid=uid, data=SimpleNamespace(session_uid=data),
                                   material_slots=[SimpleNamespace(material=m) for m in materials])
        objects = [obj(1, 101, textured), obj(2, 102, plain), obj(3, 103), obj(4, 104, None)]
        live.reset()
        for data, expected in (({101}, {1}), ({700}, {1}), ({900}, {1}), ({901}, {1}), ({800}, {1}),
                               ({710}, {2}), ({103}, {3}), ({555}, set())):
            with self.subTest(data=data):
                live._session["dirty_data"] = set(data)
                self.assertEqual(live.changed_objects(objects), expected)


if __name__ == "__main__":
    unittest.main()
