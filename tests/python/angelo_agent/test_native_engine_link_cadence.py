# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When the engine link sends live frames (``engine_link_cadence``; doc/angelo/engine_link.rst, "Cadence"), on a
stand-in ``bpy`` and a simulated clock: the link's real ``_tick``, depsgraph handler and ``publish``, with an export
that takes as long as each case says and an editor that answers each frame after as long as each case says.

Covered: a change after a pause goes out at the next tick (the leading edge); continuous changes reach the editor at
a steady cadence and the last one always arrives (before 1.0.11 a 3 s fade arrived as one frame after it ended);
a large scene's exports keep to their share of the main thread; a slow editor never has a frame waiting behind
another (each frame waits for its answer, up to a bound), an editor that never answers still gets frames, and one
that left holds nothing; a forced frame (a joining editor, a lagging page) goes at once and a later change doesn't
delay it; ``publish`` notes when the export ended and which acknowledging editors the frame reached.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_native_engine_link_state as base   # Its stand-in bpy, server and second engine (not its tests).

engine_link, assets, engine_link_facts = base.engine_link, base.assets, base.engine_link_facts

TICK = engine_link.POLL_SECONDS


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Simulation:
    """The link with one live, acknowledging editor (client 2) of a stand-in engine, ticked on a simulated clock."""

    def __init__(self, test, export_ms=5.0, answer_after=0.0, answers=True):
        self.test, self.export_ms, self.answer_after, self.answers = test, export_ms, answer_after, answers
        self.clock = Clock()
        self.frames = []          # (time sent, version)
        self.owed = []            # (time due, version) answers the editor will send.
        self.exporting_s = 0.0
        test.server.info[2]["capabilities"] = ["durable", "live", "acks"]
        engine_link._state.update(session={"asset": test.standin(), "params": {}, "number": 1},
                                  folder=str(test.project), dirty_at=None, exported_at=None, awaiting=frozenset(),
                                  last_ms=0.0, version=0, facts=engine_link_facts.new())
        self.scene = SimpleNamespace(session_uid=11)
        sent = test.server.broadcast

        def broadcast(header, payload=b"", only=None):
            reached = sent(header, payload, only)
            if header.get("type") == "scene" and reached:
                self.frames.append((self.clock.now, header["version"]))
                self.owed.append((self.clock.now + self.answer_after, header["version"]))
            return reached
        test.server.broadcast = broadcast

    def export(self, window, path, options, export_id=assets.EXPORT_ID):
        self.clock.now += self.export_ms / 1000.0
        self.exporting_s += self.export_ms / 1000.0
        Path(path).write_bytes(b"glb")

    def change(self):   # What the depsgraph handler sees after an edit of the followed scene.
        engine_link._on_depsgraph(self.scene, SimpleNamespace(updates=()))

    def run(self, seconds, change_every=None, changes_until=None):
        """Tick for ``seconds``; a change every ``change_every`` seconds until ``changes_until`` (from the start)."""
        start, next_change, changes = self.clock.now, self.clock.now, []
        with mock.patch("time.monotonic", self.clock), mock.patch("time.perf_counter", self.clock), \
                mock.patch.object(assets, "export", side_effect=self.export), \
                mock.patch.object(engine_link, "_window", return_value=None), \
                mock.patch.object(engine_link, "_followed", return_value=(self.scene, None)), \
                mock.patch.object(engine_link, "scene_key", return_value=None), \
                mock.patch.object(engine_link.scene_changes, "matters", return_value=(True, None)):
            while self.clock.now - start < seconds:
                if change_every is not None and self.clock.now >= next_change and \
                        self.clock.now - start <= changes_until + 1e-9:
                    self.change()
                    changes.append(self.clock.now)
                    next_change += change_every
                self.deliver()
                engine_link._tick()
                self.clock.now += TICK / 2.0   # Edits and ticks interleave.
                self.deliver()
                self.clock.now += TICK / 2.0
        return changes

    def deliver(self):
        if not self.answers:
            return
        ready = [item for item in self.owed if item[0] <= self.clock.now]
        self.owed = [item for item in self.owed if item[0] > self.clock.now]
        for _at, version in ready:
            engine_link._handle(2, {"type": "status", "asset": "/Game/Crate", "code": "previewed",
                                    "version": version, "instances": 1})


class TickCadenceTests(unittest.TestCase):
    setUp = base.EngineRoutingTests.setUp
    standin = base.EngineRoutingTests.standin

    def test_a_change_after_a_pause_goes_out_at_the_next_tick(self):
        sim = Simulation(self)
        sim.run(2.0)
        self.assertEqual(sim.frames, [])
        [changed] = sim.run(0.5, change_every=10.0, changes_until=0.0)
        self.assertEqual(len(sim.frames), 1)
        self.assertLessEqual(sim.frames[0][0] - changed, TICK + 0.01,
                             "the leading edge: no wait for stillness (1.0.10 waited 0.35 s)")

    def test_continuous_changes_arrive_at_a_steady_cadence_and_the_last_one_always(self):
        """The 1.0.10 demo's case: a 3 s colour fade, a change every 0.1 s. 1.0.10 sent one frame, after the end."""
        sim = Simulation(self)
        changes = sim.run(4.0, change_every=0.1, changes_until=3.0)
        during = [at for at, _version in sim.frames if at <= changes[-1]]
        self.assertGreaterEqual(len(during), 10, "frames while the changes go on: %r" % sim.frames)
        times = [changes[0]] + [at for at, _version in sim.frames]
        gaps = [later - earlier for earlier, later in zip(times, times[1:]) if earlier < changes[-1]]
        self.assertLessEqual(max(gaps), 2 * TICK + 0.02, "a steady cadence, never a gap that waits for stillness")
        self.assertGreater(sim.frames[-1][0], changes[-1], "the final state is sent")
        self.assertLessEqual(sim.frames[-1][0] - changes[-1], 2 * TICK + 0.02)
        self.assertIsNone(engine_link._state["dirty_at"], "nothing left unsent")

    def test_a_large_scenes_exports_keep_to_their_share_of_the_main_thread(self):
        sim = Simulation(self, export_ms=500.0)
        sim.run(12.0, change_every=0.1, changes_until=11.0)
        self.assertGreaterEqual(len(sim.frames), 4, "even a slow export shows the changes as they go: %r" % sim.frames)
        starts = [at - 0.5 for at, _version in sim.frames]
        spacing = [later - earlier for earlier, later in zip(starts, starts[1:])]
        self.assertGreaterEqual(min(spacing), 0.5 + 1.5 - 1e-6, "3x the export's time between exports")
        self.assertLessEqual(sim.exporting_s / 12.0, 0.26, "about a quarter of the main thread at most")

    def test_a_slow_editor_never_has_a_frame_waiting_behind_another(self):
        sim = Simulation(self, answer_after=0.45)
        changes = sim.run(4.0, change_every=0.1, changes_until=3.0)
        self.assertGreaterEqual(len(sim.frames), 5, "%r" % sim.frames)
        spacing = [later - earlier for (earlier, _a), (later, _b) in zip(sim.frames, sim.frames[1:])]
        self.assertGreaterEqual(min(spacing), 0.45 - 1e-6, "each frame waits for the editor's answer to the last")
        self.assertGreater(sim.frames[-1][0], changes[-1], "the final state is sent")

    def test_an_editor_that_never_answers_still_gets_frames(self):
        sim = Simulation(self, answers=False)
        sim.run(5.0, change_every=0.1, changes_until=4.5)
        from angelo_agent import engine_link_cadence as cadence
        spacing = [later - earlier for (earlier, _a), (later, _b) in zip(sim.frames, sim.frames[1:])]
        self.assertGreaterEqual(len(sim.frames), 4, "%r" % sim.frames)
        self.assertGreaterEqual(min(spacing), cadence.ACK_WAIT_SECONDS - 1e-6)
        self.assertLessEqual(max(spacing), cadence.ACK_WAIT_SECONDS + 2 * TICK + 0.02)

    def test_an_editor_that_left_holds_nothing(self):
        sim = Simulation(self, answers=False)
        sim.run(0.5, change_every=10.0, changes_until=0.0)
        self.assertEqual(len(sim.frames), 1)
        engine_link._handle(2, {"type": "disconnected"})
        del self.server.info[2]
        self.server.info[3] = dict(self.server.info[1], engine="standin", capabilities=["durable", "live", "acks"])
        sim.run(0.5, change_every=10.0, changes_until=0.0)
        self.assertEqual(len(sim.frames), 2, "a change goes out at once: the editor that owed an answer left")

    def test_a_forced_frame_goes_at_once_and_a_later_change_keeps_it_forced(self):
        from angelo_agent import engine_link_cadence as cadence
        sim = Simulation(self, answers=False)
        sim.run(0.3, change_every=10.0, changes_until=0.0)
        self.assertEqual(len(sim.frames), 1)
        engine_link._state["dirty_at"] = cadence.FORCED   # An editor joined (or a page asked for a whole frame).
        with mock.patch.object(engine_link.scene_changes, "matters", return_value=(True, None)), \
                mock.patch.object(engine_link, "_window", return_value=None), \
                mock.patch.object(engine_link, "_followed", return_value=(sim.scene, None)), \
                mock.patch.object(engine_link, "scene_key", return_value=None):
            sim.change()
        self.assertEqual(engine_link._state["dirty_at"], cadence.FORCED, "the oldest unsent change is kept")
        sim.run(TICK)
        self.assertEqual(len(sim.frames), 2, "no wait for the unanswered frame nor the gap")

    def test_a_lagging_editor_gets_a_whole_frame_at_once(self):
        from angelo_agent import engine_link_cadence as cadence
        sim = Simulation(self)
        self.server.lagging_any = lambda: True
        sim.run(TICK, change_every=10.0, changes_until=0.0)
        self.assertEqual(engine_link._state["dirty_at"], cadence.FORCED)


class PublishBookkeepingTests(unittest.TestCase):
    setUp = base.EngineRoutingTests.setUp
    standin = base.EngineRoutingTests.standin

    def test_publish_notes_the_export_and_the_acknowledging_editors_it_reached(self):
        self.server.info[2]["capabilities"] = ["durable", "live", "acks"]
        self.server.info[3] = dict(self.server.info[2], capabilities=["durable", "live"])   # Shows, never answers.
        sim = Simulation(self)
        sim.run(TICK, change_every=10.0, changes_until=0.0)
        self.assertEqual(engine_link._state["awaiting"], frozenset({2}))
        self.assertAlmostEqual(engine_link._state["exported_at"], sim.frames[0][0], places=6)

    def test_a_failed_export_still_spends_the_gap(self):
        sim = Simulation(self)
        with mock.patch("time.monotonic", sim.clock), mock.patch("time.perf_counter", sim.clock), \
                mock.patch.object(assets, "export", side_effect=RuntimeError("disk full")), \
                mock.patch.object(engine_link, "_followed", return_value=(sim.scene, None)), \
                mock.patch.object(engine_link, "scene_key", return_value=None):
            self.assertEqual(engine_link.publish(None), 0)
        self.assertEqual(engine_link._state["exported_at"], sim.clock.now)
        self.assertEqual(sim.frames, [])


class CadenceTests(unittest.TestCase):
    def setUp(self):
        from angelo_agent import engine_link_cadence
        self.cadence = engine_link_cadence

    def test_the_gap_is_the_tick_or_the_exports_share_within_bounds(self):
        gap = self.cadence.gap_seconds
        self.assertEqual(gap(0.0), self.cadence.MIN_GAP_SECONDS)
        self.assertEqual(gap(5.0), self.cadence.MIN_GAP_SECONDS)
        self.assertAlmostEqual(gap(200.0), 0.6)
        self.assertEqual(gap(60000.0), self.cadence.MAX_GAP_SECONDS)
        self.assertEqual(gap(-3.0), self.cadence.MIN_GAP_SECONDS)
        self.assertLess(self.cadence.MIN_GAP_SECONDS, engine_link.POLL_SECONDS, "a cheap frame can go every tick")

    def test_due(self):
        due, wait = self.cadence.due, self.cadence.ACK_WAIT_SECONDS
        now = 100.0
        self.assertFalse(due(now, None, None, 0.0))
        self.assertTrue(due(now, now, None, 0.0), "the first frame of a link")
        self.assertTrue(due(now, now, now - 5.0, 0.0), "the leading edge")
        self.assertFalse(due(now, now - 1.0, now - 0.05, 0.0), "inside the gap")
        self.assertTrue(due(now, now - 1.0, now - 0.1, 0.0), "the gap passed: changes still coming go out")
        self.assertFalse(due(now, now, now - 1.0, 500.0), "a slow export's gap")
        self.assertFalse(due(now, now, now - 5.0, 0.0, waiting_since=now - 0.2), "the last frame is unanswered")
        self.assertTrue(due(now, now, now - 5.0, 0.0, waiting_since=now - wait), "an answer that never comes")
        self.assertTrue(due(now, self.cadence.FORCED, now, 9000.0, waiting_since=now), "forced: at once")

    def test_waiting_since_reads_only_the_editors_that_got_the_frame_and_are_still_here(self):
        facts = engine_link_facts.new()
        engine_link_facts.pushed(facts, 4, 10, 1.0, 2)
        at = facts["push"]["at"]
        state = {"facts": facts, "awaiting": frozenset({2, 3})}
        self.assertEqual(self.cadence.waiting_since(state, {2: {}, 3: {}}), at)
        left = dict(state)
        self.assertIsNone(self.cadence.waiting_since(left, {}), "both left")
        self.assertEqual(left["awaiting"], frozenset(), "and owe nothing any more")
        info = {"engine": "godot"}
        engine_link_facts.acknowledge(facts, 2, info, {"code": "previewed", "version": 4, "instances": 1})
        self.assertEqual(self.cadence.waiting_since(state, {2: {}, 3: {}}), at, "3 still owes its answer")
        engine_link_facts.acknowledge(facts, 3, info, {"code": "preview_failed", "version": 5, "message": "x"})
        self.assertIsNone(self.cadence.waiting_since(state, {2: {}, 3: {}}), "a failure and a newer answer count")
        self.assertEqual(state["awaiting"], frozenset(), "nothing to read again until the next frame")
        self.assertIsNone(self.cadence.waiting_since({"facts": engine_link_facts.new(), "awaiting": frozenset({2})},
                                                     {2: {}}), "no frame yet")


if __name__ == "__main__":
    unittest.main()
