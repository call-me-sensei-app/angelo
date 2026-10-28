# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The session rail's redraw timers without Blender: ``rail_redraw.tick`` and ``_recheck`` against stub timers.

Break-it P1-12 T6 and P1-13 U4: the pacing helpers were pinned, the timers that use them were not, so a re-check that
Blender dropped on a file load (P1-13 U3) passed every test. The real window is the session rail's GUI suite.
"""

import contextlib
import sys
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import rail_redraw, session_rail as rail  # noqa: E402

WINDOW = 7


class Timers:
    """``bpy.app.timers`` as Blender keeps it (BLI_timer.cc): a list, so registering a function that is still
    registered adds a second entry (counted in ``doubled``: break-it P1-14 V7, P1-15 L7), and unregister removes the
    first entry only; ``registered`` maps each function to when it first runs."""

    def __init__(self):
        self.entries = []
        self.registrations = 0
        self.doubled = 0

    @property
    def registered(self):
        return _Registered(self, dict(self.entries))

    def register(self, function, first_interval=0.0, persistent=False):
        self.doubled += self.is_registered(function)
        self.entries.append((function, first_interval))
        self.registrations += 1

    def unregister(self, function):
        if not self.is_registered(function):
            raise ValueError("Error: function is not registered")
        index = next(i for i, entry in enumerate(self.entries) if entry[0] is function)
        del self.entries[index]

    def is_registered(self, function):
        return any(entry[0] is function for entry in self.entries)


class _Registered(dict):
    """The registered timers as a dict; ``clear()`` drops them all as a file load does, without running them."""

    def __init__(self, timers, entries):
        super().__init__(entries)
        self._timers = timers

    def clear(self):
        super().clear()
        self._timers.entries = []


class Region:
    def __init__(self, kind):
        self.type, self.tags = kind, 0

    def tag_redraw(self):
        self.tags += 1


def tiles(clock="9s", step="Carving", done=1):
    return [rail.Tile("lead", "Chat", "Main", "Idle", rail.IDLE),
            rail.Tile("w1", "Room", "Worker 1", step + " · " + clock, rail.WORKING, progress=(done, 3),
                      status_head=step, status_keep="%d of 3 steps · %s" % (done, clock), step=step)]


@contextlib.contextmanager
def world():
    """Fakes for everything ``tick`` and ``_recheck`` import; ``w.tiles``, ``w.held``, ``w.version`` drive them."""
    w = SimpleNamespace(timers=Timers(), regions=[Region('WINDOW'), Region('HEADER'), Region('TOOL_HEADER')],
                        tiles=tiles(), held=False, fail=None)
    window = SimpleNamespace(as_pointer=lambda: WINDOW, screen=SimpleNamespace(is_temporary=False))  # Live: it has a screen.
    bpy = ModuleType("bpy")
    bpy.app = SimpleNamespace(timers=w.timers)
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[window]))
    area = SimpleNamespace(regions=w.regions)
    view = ModuleType("angelo_agent.live_view_draw")
    view.target = lambda _window: area
    sessions = w.sessions = ModuleType("angelo_agent.parallel_sessions")
    sessions.sessions = lambda: ["lead", "w1"]
    sessions._held_now = lambda: w.held
    sessions.version = 1
    draw = ModuleType("angelo_agent.session_rail_draw")

    def states(_now):
        if w.fail:
            raise w.fail
        return w.tiles
    draw.states = states
    fakes = {"bpy": bpy, view.__name__: view, sessions.__name__: sessions, draw.__name__: draw}
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, fakes))
        for module in (view, sessions, draw):
            stack.enter_context(patch.object(angelo_agent, module.__name__.rpartition(".")[2], module, create=True))
        w.bump = lambda: setattr(sessions, "version", sessions.version + 1)
        rail_redraw.release()
        try:
            yield w
            assert not w.timers.doubled, "a timer registered while it still was"
        finally:
            rail_redraw.release()


def drawn(w, at):
    """The rail drew what it would show now, its last tag at ``at``."""
    rail_redraw.shown[WINDOW] = rail_redraw.signature(w.sessions.version, w.tiles)
    rail_redraw._tagged[WINDOW] = at


class TickTests(unittest.TestCase):
    def test_an_unchanged_rail_tags_nothing_and_arms_nothing(self):
        with world() as w:
            drawn(w, 10.0)
            self.assertEqual(rail_redraw.tick(10.5), 0)
            self.assertEqual(w.regions[0].tags, 0)
            self.assertFalse(w.timers.registered)

    def test_a_due_fact_tags_the_views_main_region_only(self):
        with world() as w:
            drawn(w, 10.0)
            w.bump()
            self.assertEqual(rail_redraw.tick(10.5), 1)
            self.assertEqual([region.tags for region in w.regions], [1, 0, 0])
            self.assertEqual(rail_redraw._tagged[WINDOW], 10.5)

    def test_a_clock_waits_its_second_and_a_sooner_fact_moves_the_recheck_earlier(self):
        """Break-it P1-12 T2: a clock change arms the re-check ~1 s ahead; a fact due sooner moves it, never later."""
        with world() as w:
            drawn(w, 10.0)
            w.tiles = tiles("10s")
            self.assertEqual(rail_redraw.tick(10.1), 0)
            self.assertAlmostEqual(w.timers.registered[rail_redraw._recheck], rail_redraw.CLOCK_EVERY - 0.1)
            w.bump()
            rail_redraw.tick(10.15)
            self.assertAlmostEqual(w.timers.registered[rail_redraw._recheck], 1.0 / rail_redraw.RATE - 0.15)
            self.assertEqual(len(w.timers.registered), 1)
            registrations = w.timers.registrations
            rail_redraw.tick(10.2)   # Nothing sooner: left as it is.
            self.assertEqual(w.timers.registrations, registrations)

    def test_a_file_load_that_drops_the_recheck_does_not_stop_it_being_armed_again(self):
        """Break-it P1-13 U3: Blender drops non-persistent timers on a file load without running them."""
        with world() as w:
            drawn(w, 10.0)
            w.tiles = tiles("10s")
            rail_redraw.tick(10.1)
            self.assertIn(rail_redraw._recheck, w.timers.registered)
            w.timers.registered.clear()   # File > Open, Revert, a checkpoint's Restore.
            drawn(w, 20.0)
            w.tiles = tiles("11s")
            rail_redraw.tick(20.1)
            self.assertAlmostEqual(w.timers.registered.get(rail_redraw._recheck), rail_redraw.CLOCK_EVERY - 0.1)


class CountTests(unittest.TestCase):
    """The maintainer's audit (2026-10-25): a live trial reported len(tags), the newest 512, as its redraw total."""

    def test_every_redraw_request_is_counted_past_the_retained_tags(self):
        with world() as w:
            for n in range(600):   # A fact due each time: each tick tags the view once.
                drawn(w, 10.0 * n)
                w.bump()
                self.assertEqual(rail_redraw.tick(10.0 * n + 5.0), 1)
            self.assertEqual(rail_redraw.requested[0], 600)
            self.assertEqual(len(rail_redraw.tags), 512)   # The retained sample stays capped.
            rail_redraw.release()
            self.assertEqual(rail_redraw.requested[0], 0)

    def test_every_trial_keeps_its_diagnostics_and_names_them_for_what_they_are(self):
        """A timed-out or failed trial ends through finish() without grading: it took no telemetry before."""
        source = (ADDON.parents[2] / "tests/python/angelo_agent/parallel_live_session.py").read_text(encoding="utf-8")
        finish = source[source.index("def finish("):]
        self.assertLess(finish.index("_telemetry()"), finish.index("chat.stop()"))   # Before anything stops.
        telemetry = source[source.index("def _telemetry("):source.index("def finish(")]
        self.assertIn('report["rail_redraw_requests"] = rail_redraw.requested[0]', telemetry)
        self.assertIn('report["rail_view_redraw_wall_ms_last5"]', telemetry)
        self.assertIn('report["worker_limit"] = parallel_access.worker_limit()', telemetry)
        self.assertIn("parallel_sessions.refresh_total[0]", telemetry)   # Every read, never the newest 256 alone.
        self.assertIn("rail_pictures.draw_total[0]", telemetry)
        self.assertNotIn("len(rail_redraw.tags)", source)
        self.assertNotIn('report["reload', source[:source.index("def _telemetry(")])   # Only in the common path.


class RecheckTests(unittest.TestCase):
    def test_the_recheck_draws_the_waiting_change_and_rearms_by_its_return(self):
        with world() as w:
            drawn(w, time.monotonic() - 0.1)
            w.tiles = tiles("10s")   # A clock: due 0.9 s from now.
            left = rail_redraw._recheck()
            self.assertAlmostEqual(left, rail_redraw.CLOCK_EVERY - 0.1, delta=0.05)
            self.assertFalse(w.timers.registered)   # Blender re-arms it from the return; it never registers itself.
            self.assertFalse(rail_redraw._in_recheck[0])
            rail_redraw._tagged[WINDOW] = time.monotonic() - 2.0   # The same change, now due.
            self.assertIsNone(rail_redraw._recheck())   # Tagged, nothing left waiting.
            self.assertEqual(w.regions[0].tags, 1)

    def test_a_held_window_or_a_failing_tick_ends_the_recheck(self):
        with world() as w:
            drawn(w, time.monotonic() - 2.0)
            w.bump()
            w.held = True
            self.assertIsNone(rail_redraw._recheck())
            self.assertEqual(w.regions[0].tags, 0)
            w.held, w.fail = False, RuntimeError("a tile failed")
            with patch("builtins.print"):
                self.assertIsNone(rail_redraw._recheck())
            self.assertFalse(rail_redraw._in_recheck[0])
            self.assertIsNone(rail_redraw._armed_at[0])

    def test_release_leaves_nothing_armed(self):
        with world() as w:
            drawn(w, 10.0)
            w.tiles = tiles("10s")
            rail_redraw.tick(10.1)
            rail_redraw.release()
            self.assertFalse(w.timers.registered)
            self.assertIsNone(rail_redraw._armed_at[0])
            self.assertFalse(rail_redraw.shown)


if __name__ == "__main__":
    unittest.main()
