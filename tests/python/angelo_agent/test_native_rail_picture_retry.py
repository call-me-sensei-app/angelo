# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A rail picture that could not be drawn is drawn once it can be, lead or worker, never every tick.

The maintainer's audit of e2e 34f111dfc68: ``rail_pictures.refresh`` removed a picture's pending update before
drawing it and put it back only for the lead's, so a worker's draw that failed (no 3D view for a moment, a draw that
raised) left its tile stale until another save marked it again. Without Blender: the real ``refresh`` and ``draw``
against stub Blender, GPU and live parts; the window's own proof is the session rail's GUI suite. While the window
shows another scene than the lead's own, the lead's tile keeps its last picture, drawn again once its scene is shown
(break-it E2E-20 R5-M-1).
"""

import contextlib
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))


class OffScreen:
    def __init__(self, width, height):
        self.texture_color = (width, height)

    def free(self):
        pass


def stub(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


BPY = stub("bpy", app=SimpleNamespace(background=True, timers=SimpleNamespace(is_registered=lambda f: False)),
           context=SimpleNamespace(temp_override=lambda **kwargs: contextlib.nullcontext()))
GPU = stub("gpu", types=SimpleNamespace(GPUOffScreen=OffScreen))
ADDED = "bpy" not in sys.modules   # Imported once, kept: a module first imported inside a patched sys.modules is
sys.modules.setdefault("bpy", BPY)   # dropped on exit, and refresh's own imports would then load fresh copies.
from angelo_agent import live_view, parallel_sessions, rail_pictures, rail_redraw  # noqa: E402,F401
if ADDED:
    del sys.modules["bpy"]

WORKER, LEAD = "w1", "lead"
HOST = (SimpleNamespace(scene="scene", view_layer="layer"), SimpleNamespace(spaces=SimpleNamespace(active="space")),
        "region")


class World:
    """One lead and one worker; ``host`` is whether a 3D view exists now, ``failures`` how many draws raise next."""

    def __init__(self):
        self.host, self.failures, self.attempts, self.raises = True, 0, 0, None
        self.lead_scene = "scene"   # The lead's own scene (HOST's window shows "scene").
        self.scene = ((0, 0, 0), (1, 1, 1))   # What the lead's scene shows (``_scene_bounds``).

    def native_draw(self, *args, **kwargs):
        self.attempts += 1
        if self.raises is not None:
            raise self.raises
        if self.failures:
            self.failures -= 1
            raise RuntimeError("not evaluated yet")


@contextlib.contextmanager
def world():
    w = World()
    view = (((1, 0, 0, 0),), ((1, 0, 0, 0),))
    with patch.dict(sys.modules, {"bpy": BPY, "gpu": GPU}), \
            patch.object(rail_pictures, "bpy", BPY), \
            patch.object(rail_pictures, "_host", lambda: HOST if w.host else None), \
            patch.object(rail_pictures, "_scene_bounds", lambda window: w.scene), \
            patch.object(rail_pictures, "framing", lambda bounds, aspect: view + (None, None, None)), \
            patch.object(live_view, "part", lambda session: "part", create=True), \
            patch.object(live_view, "bounds", lambda session: ((0, 0, 0), (1, 1, 1)), create=True), \
            patch.object(live_view, "native_draw", w.native_draw, create=True), \
            patch.object(parallel_sessions, "_order", [WORKER]), \
            patch.object(parallel_sessions, "lead", LEAD), \
            patch.object(parallel_sessions, "stale", {}), \
            patch.object(parallel_sessions, "costs", {}), \
            patch.object(parallel_sessions, "_bump", lambda session=None: None), \
            patch.object(parallel_sessions, "lead_of", lambda session: "lead chat"), \
            patch.object(parallel_sessions, "_document_scene",
                         lambda lead: w.lead_scene if lead == "lead chat" else None):
        rail_pictures._offscreens.clear(), rail_pictures._drawn.clear(), rail_pictures._lead_costs.clear()
        getattr(rail_pictures, "_retry", {}).clear()
        rail_pictures._staged.clear()
        try:
            yield w
        finally:
            rail_pictures._offscreens.clear(), rail_pictures._drawn.clear()
            getattr(rail_pictures, "_retry", {}).clear()


def settle(now):
    """``refresh`` at ``now``, then again while the lead's redraw has stages left (``_tick`` calls it again after
    ``STAGE_TICK``): what was drawn."""
    drawn = rail_pictures.refresh(now=now)
    for _ in range(10):
        if not rail_pictures._staged:
            break
        drawn += rail_pictures.refresh(now=now)
    return drawn


class RetryTests(unittest.TestCase):
    def test_a_worker_drawn_while_no_3d_view_exists_is_drawn_once_one_does(self):
        with world() as w:
            parallel_sessions.stale[WORKER] = 10.0
            w.host = False
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertIn(WORKER, parallel_sessions.stale)   # Still out of date, never dropped.
            w.host = True
            self.assertEqual(rail_pictures.refresh(now=10.1), [])   # Not every tick: its time has not come.
            self.assertEqual(w.attempts, 0)
            self.assertEqual(rail_pictures.refresh(now=10.6), [WORKER])   # The recovered draw.
            self.assertNotIn(WORKER, parallel_sessions.stale)

    def test_a_worker_draw_that_raised_is_tried_again(self):
        with world() as w:
            parallel_sessions.stale[WORKER] = 10.0
            w.failures = 1
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertIn(WORKER, parallel_sessions.stale)
            self.assertEqual(rail_pictures.refresh(now=11.0), [WORKER])
            self.assertEqual(w.attempts, 2)
            self.assertNotIn(WORKER, parallel_sessions.stale)

    def test_retries_back_off_and_never_spin_yet_never_give_up(self):
        with world() as w:
            parallel_sessions.stale[WORKER] = 0.0
            w.failures = 10 ** 6
            ticks = [0.25 * n for n in range(int(120 / 0.25))]   # Two minutes of the pictures timer.
            for now in ticks:
                rail_pictures.refresh(now=now)
            self.assertLessEqual(w.attempts, 25)   # RETRY doubling to RETRY_MAX: ~20, against 480 ticks.
            self.assertGreaterEqual(w.attempts, 15)   # Still trying every RETRY_MAX.
            w.failures = 0
            drawn = [now for now in (120 + 0.25 * n for n in range(40)) if rail_pictures.refresh(now=now)]
            self.assertEqual(len(drawn), 1)
            self.assertLessEqual(drawn[0] - 120, rail_pictures.RETRY_MAX)   # Within one spacing once it can.
            self.assertNotIn(WORKER, parallel_sessions.stale)
            parallel_sessions.stale[WORKER] = 200.0   # A later failure starts from RETRY again.
            w.failures = 1
            rail_pictures.refresh(now=200.0)
            self.assertEqual(rail_pictures.refresh(now=200.0 + rail_pictures.RETRY), [WORKER])

    def test_the_lead_is_retried_alike(self):
        with world() as w:
            parallel_sessions.stale[LEAD] = 10.0
            w.host = False
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertIn(LEAD, parallel_sessions.stale)
            w.host = True
            self.assertEqual(settle(11.0), [LEAD])

    def test_the_lead_keeps_its_last_picture_while_its_scene_is_not_shown(self):
        with world() as w:
            parallel_sessions.stale[LEAD] = 10.0
            self.assertEqual(settle(10.0), [LEAD])
            rail_pictures._drawn[LEAD] = 10.0   # Drawn at 10 on this test's clock (``draw`` notes the real one).
            parallel_sessions.stale[LEAD] = 20.0
            w.lead_scene = "kitchen"   # The window shows another scene of the file: never drawn as the lead's.
            for now in (20.0, 25.0, 30.0):
                self.assertEqual(rail_pictures.refresh(now=now), [])
            self.assertEqual(w.attempts, 1)
            self.assertIsNotNone(rail_pictures.texture(LEAD))   # Its last picture stays on its tile.
            self.assertIn(LEAD, parallel_sessions.stale)
            w.lead_scene = "scene"   # Its tile brought its scene back: drawn at once, no retry wait.
            self.assertEqual(settle(30.25), [LEAD])

    def test_a_draw_that_raises_past_draw_stays_out_of_date(self):
        with world() as w:
            parallel_sessions.stale[WORKER] = 10.0
            w.raises = AttributeError("a live part went away mid-draw")
            with self.assertRaises(AttributeError):
                rail_pictures.refresh(now=10.0)
            self.assertIn(WORKER, parallel_sessions.stale)   # The timer reports it; the picture still comes.
            w.raises = None
            self.assertEqual(rail_pictures.refresh(now=11.0), [WORKER])

    def test_the_lead_is_framed_on_what_its_scene_shows_and_an_empty_one_has_no_picture(self):
        """Director capture, 2026-10-25: the lead's picture was the artist's own view (a dot, an empty frame)."""
        framed = []
        with world() as w, patch.object(rail_pictures, "framing", lambda bounds, aspect: (
                framed.append(bounds) or (((1, 0, 0, 0),), ((1, 0, 0, 0),), None, None, None))):
            parallel_sessions.stale[LEAD] = 10.0
            self.assertEqual(settle(10.0), [LEAD])
            self.assertEqual(framed, [((0, 0, 0), (1, 1, 1))])
            w.scene = None   # Nothing it shows: no picture (the tile says so), never retried.
            rail_pictures._drawn.pop(LEAD, None)   # Its once-a-second pacing reads the real clock.
            parallel_sessions.stale[LEAD] = 20.0
            self.assertEqual(rail_pictures.refresh(now=20.0), [])
            self.assertNotIn(LEAD, parallel_sessions.stale)
            self.assertIsNone(rail_pictures.texture(LEAD))
            w.scene = rail_pictures._UNREAD   # Not evaluated since its change: tried again later.
            rail_pictures._drawn.pop(LEAD, None)
            parallel_sessions.stale[LEAD] = 30.0
            self.assertEqual(rail_pictures.refresh(now=30.0), [])
            self.assertIn(LEAD, parallel_sessions.stale)

    def test_any_size_fills_its_picture(self):
        """No 5 cm floor on the framed radius (a 2 mm part was a dot): the distance follows the size it frames."""
        source = Path(rail_pictures.__file__).read_text(encoding="utf-8")
        self.assertIn("radius = max(1e-5, (high - low).length / 2)", source)

    def test_nothing_to_draw_is_not_retried(self):
        with world() as w, patch.object(live_view, "part", lambda session: None, create=True):
            parallel_sessions.stale[WORKER] = 10.0
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertNotIn(WORKER, parallel_sessions.stale)   # Its picture is removed: that is its state.
            self.assertEqual(w.attempts, 0)


class StageTests(unittest.TestCase):
    """The lead's redraw a stage a main-loop turn (the coordinator, 2026-10-25: one redraw was one 73-98 ms block at
    ~200k instances): its box, each decision picture, the picture, on turns of their own; a worker's at once."""

    def choice(self, events, pictures):
        def lead_choice(window, area, region, box, shows=None, last=None):
            events.append("choose")
            for index in range(pictures):
                yield "candidate"
                events.append("decision picture %d" % index)
            return (((1, 0, 0, 0),), ((1, 0, 0, 0),), "bounds")
        return lead_choice

    def test_each_stage_on_a_turn_of_its_own(self):
        events = []
        with world() as w, patch.object(rail_pictures, "lead_choice", self.choice(events, 2)), \
                patch.object(rail_pictures, "_scene_bounds", lambda window: events.append("box") or w.scene):
            w.native_draw = lambda *args, **kwargs: events.append("picture")
            live_view.native_draw = w.native_draw
            parallel_sessions.stale[LEAD] = 10.0
            turns = []
            for _ in range(6):
                before = len(events)
                drawn = rail_pictures.refresh(now=10.0)
                turns.append((events[before:], drawn))
                if drawn:
                    break
            self.assertEqual(turns, [(["box", "choose"], []), (["decision picture 0"], []),
                                     (["decision picture 1"], []), (["picture"], [LEAD])])
            self.assertEqual(rail_pictures._staged, {})
            self.assertEqual(rail_pictures.sources[LEAD], "bounds")

    def test_a_window_that_changed_meanwhile_is_not_drawn_and_tried_again(self):
        events = []
        with world() as w, patch.object(rail_pictures, "lead_choice", self.choice(events, 1)):
            parallel_sessions.stale[LEAD] = 10.0
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertIn(LEAD, rail_pictures._staged)
            w.host = False   # Its 3D view closed between turns.
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            self.assertEqual(rail_pictures._staged, {})
            self.assertIn(LEAD, parallel_sessions.stale, "out of date still: tried again")
            self.assertEqual(w.attempts, 0)

    def test_what_it_was_drawn_from_is_read_when_it_begins(self):
        """REVIEW-TILE-STAGES M1, H1: the view moved during the stages (K0, then K1) is a move after the picture, and a
        running redraw is not marked again by ``_view_moved``."""
        events, keys = [], ["K0"]
        with world(), patch.object(rail_pictures, "lead_choice", self.choice(events, 1)), \
                patch.object(rail_pictures, "_view_key", lambda *args: keys[-1]):
            parallel_sessions.stale[LEAD] = 10.0
            rail_pictures._lead_keys[LEAD] = "older"
            self.assertEqual(rail_pictures.refresh(now=10.0), [])
            keys.append("K1")   # Moved while the redraw runs.
            rail_pictures._view_moved()
            self.assertNotIn(LEAD, parallel_sessions.stale, "a running redraw is not marked again")
            for _ in range(5):
                if rail_pictures.refresh(now=10.0):
                    break
            self.assertEqual(rail_pictures._lead_keys[LEAD], "K0", "the key it began with")
            rail_pictures._view_moved()
            self.assertIn(LEAD, parallel_sessions.stale, "the move after it: drawn again")

    def test_decision_pictures_never_reach_the_tile(self):
        """VW11-M-1: a decision picture drawn into the tile's own offscreen showed a rejected view between stages, and
        one stayed shown after a failure. The tile's picture is drawn only at the last stage."""
        events, used = [], []
        with world() as w, patch.object(rail_pictures, "shown_share", lambda target, offscreen, *args: used.append(
                offscreen) or 0.5), patch.object(rail_pictures, "lead_choice", self.choice(events, 1)):
            original = rail_pictures.lead_choice

            def choice(window, area, region, box, shows=None, last=None):
                shows(None, None)
                return (yield from original(window, area, region, box, shows, last))
            with patch.object(rail_pictures, "lead_choice", choice):
                parallel_sessions.stale[LEAD] = 10.0
                rail_pictures.refresh(now=10.0)
                self.assertNotIn(LEAD, rail_pictures._offscreens, "no tile picture until the last stage")
                for _ in range(5):
                    if rail_pictures.refresh(now=10.0):
                        break
            self.assertTrue(used and all(target is not rail_pictures._offscreens[LEAD] for target in used))
            self.assertIs(used[0], rail_pictures._scratch[0])

    def test_a_tick_that_raised_keeps_a_running_redraw_out_of_date(self):
        source = Path(rail_pictures.__file__).read_text(encoding="utf-8")
        tick = source[source.index("def _tick():"):source.index("def _view_moved():")]
        handler = tick[tick.index("except Exception"):]
        self.assertIn("_unstage()", handler, "REVIEW-TILE-STAGES M2")
        self.assertIn("_unstage()", tick[:tick.index("try:")], "-2 L-a: an idle rail keeps it out of date too")
        unstage = source[source.index("def _unstage():"):source.index("def _view_moved():")]
        self.assertLess(unstage.index("stale.setdefault(session_id, since)"), unstage.index("_staged.clear()"))

    def test_what_shows_is_recorded_when_the_redraw_begins(self):
        """REVIEW-TILE-STAGES-2 M: hidden then shown again during the stages, the second change was taken as drawn."""
        from angelo_agent import rail_changes
        events = []
        shown = [1]
        with world(), patch.object(rail_pictures, "lead_choice", self.choice(events, 1)), \
                patch.object(rail_changes, "shown_state", lambda depsgraph: shown[0]), \
                patch.dict(rail_changes._drawn, clear=True):
            parallel_sessions.stale[LEAD] = 10.0
            rail_pictures.refresh(now=10.0)
            self.assertEqual(rail_changes._drawn[LEAD], 1, "recorded at the first stage")
            shown[0] = 2   # Changed during the stages.
            bare = SimpleNamespace(updates=[SimpleNamespace(is_updated_geometry=False, is_updated_transform=False,
                                                            is_updated_shading=False)])
            self.assertTrue(rail_changes.visual_update(bare, LEAD), "a change after the picture began")

    def test_a_worker_is_drawn_at_once_and_the_timer_comes_back_soon_meanwhile(self):
        with world():
            parallel_sessions.stale[WORKER] = 10.0
            self.assertEqual(rail_pictures.refresh(now=10.0), [WORKER])
        source = Path(rail_pictures.__file__).read_text(encoding="utf-8")
        self.assertIn("return STAGE_TICK if _staged and not held else POLL", source)


class VisualUpdateTests(unittest.TestCase):
    """Only a change the Solid picture can show puts the lead's picture out of date: a selection, the active object
    and a rename reach Python as a bare Scene update, as an eye toggle does (Mac 2 probe, 2026-10-25), so those are
    told apart by which objects show (``_angelo_live.shown_state``)."""

    def update(self, geometry=False, transform=False, shading=False):
        return SimpleNamespace(is_updated_geometry=geometry, is_updated_transform=transform,
                               is_updated_shading=shading)

    def test_flags_or_what_shows(self):
        from angelo_agent import rail_changes
        shown = [7]
        live = SimpleNamespace(shown_state=lambda depsgraph: shown[0])
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_live=live)}), \
                patch.dict(rail_changes._drawn, clear=True):
            bare = SimpleNamespace(updates=[self.update()])
            self.assertTrue(rail_changes.visual_update(bare, LEAD), "never drawn: out of date")
            rail_changes.drawn(LEAD, bare)
            self.assertFalse(rail_changes.visual_update(bare, LEAD), "a selection: nothing shown changed")
            for flags in ({"geometry": True}, {"transform": True}, {"shading": True}):
                self.assertTrue(rail_changes.visual_update(SimpleNamespace(updates=[self.update(**flags)]), LEAD))
            shown[0] = 8   # An eye toggle: which objects show changed.
            self.assertTrue(rail_changes.visual_update(bare, LEAD))
            live.shown_state = lambda depsgraph: None
            self.assertTrue(rail_changes.visual_update(bare, LEAD), "cannot tell: out of date")
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace()}):
            self.assertTrue(rail_changes.visual_update(SimpleNamespace(updates=[self.update()]), LEAD),
                            "an older build: every update, as before")

    def test_the_handler_asks_it(self):
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        handler = source[source.index("def _depsgraph_changed("):]
        handler = handler[:handler.index("\n\n\n")]
        self.assertIn("rail_changes.visual_update(depsgraph, parallel_sessions.lead)", handler)


if __name__ == "__main__":
    unittest.main()
