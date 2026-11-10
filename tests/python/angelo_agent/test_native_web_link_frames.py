# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web link's frame decisions (``web_link_live.live_frames``; doc/angelo/web_link.rst, "Live frames"), with a
stand-in export that writes what the scene would hold.

Covered: the first frame is full, an unchanged scene sends nothing, a change sends a delta of that object alone with
its base, removals are listed, and a resync, a lagging page, an undo or a removal list too long for a header gets a
full frame; a delta that can't be made right becomes a full frame; the still-scene check, its back-off and the
pause it needs; a new link starts over; the server never drops a delta and owes a page that fell too far behind a
full frame.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
from angelo_agent import engine_link_server, web_link_live as live  # noqa: E402
from test_native_engine_link_protocol import TOKEN  # noqa: E402
from test_native_web_link_live import document_with  # noqa: E402


class FakeServer:
    def __init__(self):
        self.info, self.late = {1: {}, 2: {}}, set()

    def clients(self):
        return dict(self.info)

    def lagging(self, client):
        found = client in self.late
        self.late.discard(client)
        return found


class FrameTests(unittest.TestCase):
    """``live_frames`` with a stand-in export that writes what the scene would hold."""

    def setUp(self):
        self.scene = {"1": ("Chair", 1), "2": ("Table", 2)}   # key -> (name, object uid)
        self.colors = {}
        self.server = FakeServer()
        self.link = types.SimpleNamespace(_state={"server": self.server},
                                          _followed=lambda window: (SimpleNamespace(objects=[]), None))
        patches = [mock.patch.object(live, "_clients", lambda link, found: sorted(self.server.info)),
                   mock.patch.object(live, "_export_glb", self.export),
                   mock.patch.object(live, "image_changes", lambda scene=None: set()),
                   mock.patch.object(live, "changed_objects",
                                     lambda objects, depsgraph=None: set(live._session["dirty"]))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        live.reset()
        self.found = {"asset": "a", "number": 1, "params": {}}
        self.contents = []

    def export(self, window, path, found, content, known_images):
        keys = [key for key, (_name, uid) in self.scene.items() if content is None or uid in content]
        self.contents.append(content)
        document, binary = document_with([(key, self.scene[key][0]) for key in keys], colors=self.colors)
        live._export["owners"] = {key: self.scene[key][1] for key in keys}
        return document, binary, {uid for _name, uid in self.scene.values()}

    def frames(self, base):
        return live.live_frames(self.link, None, "x.glb", self.found, base)

    def nodes(self, frame):
        document, _binary = live.read_glb(frame[2])
        return [node["extras"][live.KEY] for node in document["nodes"]]

    def test_first_full_then_nothing_then_a_delta_of_the_one_change(self):
        [frame] = self.frames(0)
        self.assertEqual(frame[1], {"kind": live.FULL})
        self.assertEqual(self.nodes(frame), ["1", "2"])
        self.assertEqual(self.frames(1), [], "nothing changed: no frame")
        self.colors["2"] = [1, 0, 0, 1]
        live._session["dirty"].add(2)
        [frame] = self.frames(1)
        self.assertEqual(frame[1], {"kind": live.DELTA, "base": 1, "removed": []})
        self.assertEqual(self.nodes(frame), ["2"], "only the changed object")
        self.assertEqual(self.contents[-1], {2}, "only it was exported")

    def test_a_change_back_or_a_selection_sends_nothing(self):
        self.frames(0)
        live._session["dirty"].add(1)
        self.assertEqual(self.frames(1), [])

    def test_removals_are_listed_by_key(self):
        self.frames(0)
        del self.scene["2"]
        [frame] = self.frames(1)
        self.assertEqual((frame[1]["kind"], frame[1]["removed"]), (live.DELTA, ["2"]))
        self.assertEqual(self.nodes(frame), [])

    def test_a_resync_a_lagging_page_or_an_undo_gets_a_full_frame(self):
        self.frames(0)
        for cause in ("resync", "lagging", "undo", "behind"):
            with self.subTest(cause=cause):
                if cause == "resync":
                    live.resync(2)
                elif cause == "lagging":
                    self.server.late.add(1)
                elif cause == "undo":
                    live.unsure()
                base = live._session["sent"][1]
                if cause == "behind":
                    live._session["sent"][2] = base - 1
                [frame] = self.frames(base)
                self.assertEqual(frame[1]["kind"], live.FULL)
                self.assertIsNone(self.contents[-1], "a full frame exports everything")

    def test_a_removal_list_too_long_for_a_header_is_a_full_frame(self):
        self.scene = {str(index): ("O%d" % index, index) for index in range(1, 6000)}
        self.frames(0)
        self.scene = {"1": ("O1", 1)}
        [frame] = self.frames(1)
        self.assertEqual(frame[1]["kind"], live.FULL)

    def test_a_delta_that_cant_be_made_right_becomes_a_full_frame(self):
        self.frames(0)
        live._session["dirty"].add(2)
        real = self.export

        def failing(window, path, found, content, known_images):
            if content is not None:
                raise RuntimeError("A live frame's nodes don't each have their own key")
            return real(window, path, found, content, known_images)
        with mock.patch.object(live, "_export_glb", failing):
            [frame] = self.frames(1)
        self.assertEqual(frame[1]["kind"], live.FULL)

    def test_a_still_scene_is_checked_whole_once_after_a_delta(self):
        """Whatever deciding which objects changed misses, a whole export corrects once the scene is still: it sends
        a full frame only when a digest moved, and nothing when the pages have it all."""
        self.frames(0)
        live._session["dirty"].add(2)
        self.colors["2"] = [1, 0, 0, 1]
        self.frames(1)
        self.assertIsNotNone(live._session["verify_due"], "a delta schedules the check")
        self.assertNotIn("dirty_at", self.link._state, "the check keeps its own time (live.due), never the link's")
        live._session["verify_due"] = 0.0
        self.assertEqual(self.frames(2), [], "nothing was missed: nothing is sent")
        self.assertIsNone(self.contents[-1], "the check exports everything")
        self.assertIsNone(live._session["verify_due"])
        self.assertFalse(live.live_time_counts(), "a check's time never lengthens the next edit's wait")
        self.assertEqual(live._session["verify_wait"], 2 * live.VERIFY_SECONDS, "found nothing: it backs off")
        live._session["dirty"].add(1)   # A delta, then a change nobody reported.
        self.colors["1"] = [0, 0, 1, 1]
        self.frames(2)
        self.colors["2"] = [0, 1, 0, 1]
        live._session["verify_due"] = 0.0
        [frame] = self.frames(3)
        self.assertEqual(frame[1]["kind"], live.FULL)
        self.assertEqual(live._session["verify_wait"], live.VERIFY_SECONDS, "a correction brings it back")
        live._session["dirty"].add(1)
        self.colors["1"] = [1, 1, 0, 1]
        self.frames(4)
        self.assertTrue(live.live_time_counts(), "an edit's time counts")

    def test_a_frame_that_sends_nothing_leaves_a_due_check_as_it_was(self):
        """A publish that finds nothing the page shows (a selection, a value set back, a poll that saw a redraw) adds
        no doubt, so it never pushes a check back (break-it round 9, L1)."""
        self.frames(0)
        self.edit("2", 2)
        due = live._session["verify_due"]
        live._session["dirty"].add(2)   # Re-evaluated, nothing the page shows changed.
        self.assertEqual(self.frames(live._session["sent"][1]), [])
        self.assertEqual(live._session["verify_due"], due)

    def clocked(self, took=0.0):
        """web_link_live's clock, simulated: ``monotonic`` at ``self.now``, each export ``took`` seconds."""
        self.now, ticks = 1000.0, [0.0]

        def perf():
            ticks[0] += took / 2.0   # Read twice around each attempt.
            return ticks[0]
        return mock.patch.object(live, "time", SimpleNamespace(monotonic=lambda: self.now, perf_counter=perf))

    def test_a_frame_that_raises_is_tried_again_after_a_growing_back_off(self):
        """Round 13: a check that raised was retried every 2 s (back to back once its export took longer), and a
        delta or a first frame that raised was never tried again. Any frame that raises is now tried again after
        a back-off of at least ``VERIFY_SHARE`` times the attempt, doubling with each raise in a row."""
        with self.clocked(took=10.0):
            self.frames(0)
            live._session["dirty"].add(2)
            self.colors["2"] = [0, 0.5, 0, 1]
            failing = mock.patch.object(live, "_export_glb", side_effect=OSError("disk full"))
            with failing, self.assertRaises(OSError):
                self.frames(1)
            self.assertEqual(live._session["verify_due"], self.now + live.VERIFY_SHARE * 10.0, "the attempt's cost")
            with mock.patch.object(live, "pixels_changed", return_value=False):
                self.assertIsNone(live.due())
                self.now = live._session["verify_due"]
                self.assertEqual(live.due(), 0.0, "then it is asked for")
            with failing, self.assertRaises(OSError):
                self.frames(1)
            self.assertEqual(live._session["fail_wait"], 60.0, "doubled, up to the most")
            [frame] = self.frames(1)
        self.assertEqual(frame[1]["kind"], live.DELTA, "what failed is sent when it works")
        self.assertEqual(self.nodes(frame), ["2"])
        self.assertEqual(live._session["fail_wait"], 0.0)

    def test_a_first_frame_that_raises_is_tried_again(self):
        with self.clocked():
            with mock.patch.object(live, "_export_glb", side_effect=OSError("disk full")), \
                    self.assertRaises(OSError):
                self.frames(0)
            self.now = live._session["verify_due"]
            with mock.patch.object(live, "pixels_changed", return_value=False):
                self.assertEqual(live.due(), 0.0)
            [frame] = self.frames(0)
        self.assertEqual(frame[1]["kind"], live.FULL)

    def test_not_knowing_what_changed_sends_a_whole_frame(self):
        """Round 10, H1: an error while deciding what changed stopped every later frame; now it sends a whole one."""
        self.frames(0)
        live._session["dirty"].add(2)
        self.colors["2"] = [0, 1, 1, 1]
        with mock.patch.object(live, "changed_objects", side_effect=TypeError("unreadable")):
            [frame] = self.frames(1)
        self.assertEqual(frame[1]["kind"], live.FULL)
        self.assertEqual(live._session["dirty_data"], set(), "and what was noted is cleared")

    def emitting(self, moving_exports):
        """The scene holds a particle emitter; its copies (node "2") move in the first ``moving_exports`` exports."""
        system = SimpleNamespace(settings=SimpleNamespace(render_type="OBJECT"))
        scene = SimpleNamespace(objects=[SimpleNamespace(session_uid=9, particle_systems=[system])])
        self.link._followed = lambda window: (scene, None)
        real, made = self.export, [0]

        def export(window, path, found, content, known_images):
            made[0] += 1
            if made[0] <= moving_exports:
                self.colors["2"] = [made[0] / 10.0, 0, 0, 1]
            return real(window, path, found, content, known_images)
        return export, made

    def test_a_whole_frame_is_exported_until_its_particles_settle(self):
        """Round 12, L1 and M2: a whole frame with particle copies is exported until two exports agree, and a
        settled whole frame is never followed by a still-scene check (it has nothing to miss); only one that never
        settled within SETTLE_EXPORTS is."""
        export, made = self.emitting(moving_exports=2)
        with mock.patch.object(live, "_export_glb", export):
            [frame] = self.frames(0)
        self.assertEqual(made[0], 3, "exports 2 and 3 agreed")
        self.assertIsNone(live._session["verify_due"], "settled: no check")
        live.reset()
        export, made = self.emitting(moving_exports=99)
        with mock.patch.object(live, "_export_glb", export):
            self.frames(0)
        self.assertEqual(made[0], live.SETTLE_EXPORTS)
        self.assertIsNotNone(live._session["verify_due"], "never settled: a check follows")
        live._session["verify_due"] = 0.0
        self.colors["2"] = [0, 0, 0, 1]
        with mock.patch.object(live, "_export_glb", export):
            self.frames(live._session["sent"][1])
        self.assertIsNone(live._session["verify_due"], "and no check follows a check")

    def edit(self, key, uid):
        live._session["dirty"].add(uid)
        self.colors[key] = [len(self.contents) / 100.0, 0, 0, 1]
        return self.frames(live._session["sent"][1])

    def test_editing_never_pushes_the_check_past_its_back_off(self):
        """The back-off spaces checks; it is never the pause a check needs after an edit, so edits that come more
        often than the back-off still get checked once the person pauses (break-it round 8, M3)."""
        clock = SimpleNamespace(now=1000.0)   # A simulated clock: the schedule, never this machine's speed.
        with mock.patch.object(live, "time", SimpleNamespace(monotonic=lambda: clock.now,
                                                             perf_counter=time.perf_counter)):
            self.frames(0)
            live._session.update(verify_wait=32.0, checked_at=clock.now - 100.0)   # Long since the last check.
            self.edit("2", 2)
            self.assertEqual(live._session["verify_due"], clock.now + live.VERIFY_SECONDS)
            live._session["checked_at"] = clock.now   # Just checked: the next one keeps its distance.
            clock.now += 1.0
            self.edit("2", 2)
            self.assertEqual(live._session["verify_due"], clock.now - 1.0 + 32.0)

    def test_a_check_waits_for_a_pause_in_proportion_to_its_cost(self):
        """A check is a whole export: it waits for stillness of ``VERIFY_SHARE`` times the last whole export's time,
        so on a large scene it never takes most of a short pause (break-it round 8, M4)."""
        real = self.export

        def slow(window, path, found, content, known_images):
            if content is None:
                time.sleep(0.2)
            return real(window, path, found, content, known_images)
        with mock.patch.object(live, "_export_glb", slow), mock.patch.object(live, "VERIFY_SECONDS", 0.01):
            live.reset()
            self.frames(0)
            self.assertGreaterEqual(live._session["whole_ms"], 200.0)
            self.edit("2", 2)
        self.assertGreater(live._session["verify_due"] - time.monotonic(), live.VERIFY_SHARE * 0.2 - 0.05)

    def test_a_new_link_starts_over(self):
        self.frames(0)
        self.found = dict(self.found, number=2)
        [frame] = self.frames(1)
        self.assertEqual(frame[1]["kind"], live.FULL)


class DeltaQueueTests(unittest.TestCase):
    """``LinkServer.send`` keeps every delta, and owes a page that fell too far behind a full frame. The client has
    no writer thread here, so what is sent stays queued."""

    def setUp(self):
        self.server = engine_link_server.LinkServer(TOKEN, {"name": "Angelo"})
        self.addCleanup(self.server.close)
        self.client = engine_link_server._Client(1, None, ("127.0.0.1", 0))
        self.client.authenticated, self.client.closed = True, False
        self.server._clients[1] = self.client
        self.addCleanup(self.server._clients.clear)

    def scenes(self):
        return [(item[0].get("kind"), item[0]["version"]) for item in self.client.queue if item[0]["type"] == "scene"]

    def send(self, version, kind="delta"):
        return self.server.send(1, {"type": "scene", "kind": kind, "version": version})

    def test_deltas_wait_in_order_and_a_full_scene_replaces_them(self):
        self.send(1, "full")
        self.send(2)
        self.send(3)
        self.server.send(1, {"type": "status", "code": "edit_mode"})
        self.assertEqual(self.scenes(), [("full", 1), ("delta", 2), ("delta", 3)], "a delta never replaces one")
        self.send(4, "full")
        self.assertEqual(self.scenes(), [("full", 4)])
        self.assertEqual(sum(item[0]["type"] == "status" for item in self.client.queue), 1, "other frames stay")
        self.assertFalse(self.server.lagging(1))

    def test_more_deltas_than_the_bound_make_the_page_lagging(self):
        for version in range(1, engine_link_server.MAX_DELTAS + 1):
            self.assertTrue(self.send(version))
        self.assertFalse(self.send(99), "past the bound the delta isn't queued")
        self.assertEqual(self.scenes(), [], "and the waiting ones go: they need a full scene")
        self.assertTrue(self.server.lagging_any())
        self.assertTrue(self.server.lagging(1))
        self.assertFalse(self.server.lagging(1), "cleared as read")
        self.assertFalse(self.server.lagging_any())


if __name__ == "__main__":
    unittest.main()
