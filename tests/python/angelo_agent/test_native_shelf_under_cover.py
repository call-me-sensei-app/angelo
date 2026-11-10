# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Object-mode shelf's remembered state is applied under Angelo's start-up cover, not after it lifts (1.0.10).

Break-it reviews of the 1.0.10 first-launch change: the native cover no longer renders the viewports it hides
(``wm_angelo_cover.cc``), so no 3D view draws under it, and ``media_shelf``, which applies the remembered state from a
view's draws ``SETTLE`` seconds after the view entered Object mode, changed a returning Manual user's hidden or
expanded shelf in front of them after the lift (M2); and a cover shorter than ``SETTLE`` (a file opened later) still
let it change after the lift (second review, M1). ``media_shelf_cover`` samples the views from a timer while the
cover is up and applies the remembered state once a view's shelf state has held for ``STABLE`` samples, or when the
cover lifts (``before_lift``, third review N1); its draws after the lift settle afresh (N2). On a
stand-in ``bpy`` with a stand-in clock: applied under a short cover, once per view, never remembered as the person's
choice, only in Object mode outside Director, the mode read per window, one failing view never ends the timer, the
timer's own lifecycle. The running app's proof is the release kit's first-launch slot (``fl_ab_1010.zsh``, the
returning Manual user's shelf states per frame).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


class Timers:
    def __init__(self):
        self.registered = {}

    def register(self, function, first_interval=0.0, persistent=False):
        self.registered[function] = first_interval

    def unregister(self, function):
        self.registered.pop(function)

    def is_registered(self, function):
        return function in self.registered


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object, AssetShelf=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    handlers.load_post = []
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text),
                              handlers=handlers, background=False, timers=Timers())
    bpy.context = SimpleNamespace(window_manager=None, mode=None)

    @contextmanager
    def temp_override(window=None, area=None, **_):   # The mode is the overridden window's, as in Blender.
        before = bpy.context.mode
        bpy.context.mode = window.mode
        try:
            yield
        finally:
            bpy.context.mode = before
    bpy.context.temp_override = temp_override
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers}


FAKE = fake_bpy()
with mock.patch.dict(sys.modules, FAKE):
    import angelo_agent
    from angelo_agent import media_shelf, media_shelf_cover
BPY = FAKE["bpy"]


def region(kind, width, height):
    return SimpleNamespace(type=kind, width=width, height=height)


def collapsed_view(pointer):
    """A 3D view whose shelf shows only its tab strip (Blender's default for Angelo's shelf)."""
    return SimpleNamespace(type='VIEW_3D', as_pointer=lambda: pointer,
                           regions=[region('WINDOW', 1200, 800), region('ASSET_SHELF', 1, 1),
                                    region('ASSET_SHELF_HEADER', 1200, 26)])


class CoverSampleTests(unittest.TestCase):
    def setUp(self):
        self.clock = [100.0]
        self.applied, self.written, self.offered, self.failing = [], [], [], []
        self.shelves = {42: SimpleNamespace(active_catalog_path=None)}   # The view's shelf instance exists.
        self.window, self.area = SimpleNamespace(name="main", mode='OBJECT'), collapsed_view(42)
        self.views = [(self.window, self.area)]
        self.director = set()
        self.wm = SimpleNamespace(angelo_startup_cover=(0.1, 0.1, 0.1, 1.0))
        BPY.context.window_manager, BPY.context.mode = self.wm, None
        BPY.app.timers = Timers()
        director = SimpleNamespace(is_director=lambda window: window.name in self.director)
        for state in (media_shelf._seen, media_shelf._applied, media_shelf._offering, media_shelf._tabs):
            state.clear()
        media_shelf_cover._state["failed"] = False
        modules = {**FAKE, "angelo_agent": angelo_agent, "angelo_agent.media_shelf": media_shelf,
                   "angelo_agent.media_shelf_cover": media_shelf_cover, "angelo_agent.director_layout": director}
        patches = (mock.patch.dict(sys.modules, modules),   # The sampler imports media_shelf when it runs.
                   mock.patch.object(angelo_agent, "director_layout", director, create=True),
                   mock.patch("time.monotonic", lambda: self.clock[0]),
                   mock.patch.object(media_shelf, "bpy", BPY), mock.patch.object(media_shelf_cover, "bpy", BPY),
                   mock.patch.object(media_shelf, "remembered", lambda: "hidden"),
                   mock.patch.object(media_shelf, "remember", self.written.append),
                   mock.patch.object(media_shelf, "_apply", self.apply),
                   mock.patch.object(media_shelf, "_shelf", lambda window, area: self.shelves.get(area.as_pointer())),
                   mock.patch.object(media_shelf, "_defer", lambda function: function()),
                   mock.patch.object(media_shelf, "_offer", self.offer),
                   mock.patch.object(media_shelf, "_redraw_later", lambda pointer: None),
                   mock.patch.object(media_shelf_cover.live_windows, "window_areas", lambda manager: self.views))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(media_shelf_cover.unregister)

    def offer(self, pointer):
        """``media_shelf._offer``: True in ``_offering`` once offered, as the real one keeps it."""
        self.offered.append(pointer)
        media_shelf._offering[pointer] = True

    def apply(self, pointer, wanted):
        """``media_shelf._apply``'s record of what it applied (``_seen``), as the real one keeps it."""
        if self.failing:
            raise self.failing.pop()
        self.applied.append((pointer, wanted))
        media_shelf._seen[pointer] = (wanted, media_shelf._seen.get(pointer, (None, 0.0))[1])

    def run_timer(self, seconds):
        """Run the sampler's timer at its own interval for ``seconds``; its last return value."""
        result, end = media_shelf_cover.INTERVAL, self.clock[0] + seconds
        while self.clock[0] < end and result is not None:
            result = media_shelf_cover._tick()
            self.clock[0] += media_shelf_cover.INTERVAL
        return result

    def test_applied_under_a_cover_shorter_than_settle(self):
        """A file opened later is covered for a fraction of a second: the state is applied before the lift."""
        media_shelf_cover.register()
        self.assertTrue(BPY.app.timers.is_registered(media_shelf_cover._tick))
        self.assertLess(media_shelf_cover.STABLE * media_shelf_cover.INTERVAL, media_shelf.SETTLE)
        deferred = []
        with mock.patch.object(media_shelf, "_defer", deferred.append):
            self.assertIsNotNone(self.run_timer(media_shelf_cover.STABLE * media_shelf_cover.INTERVAL))
        self.assertEqual(self.applied, [(42, "hidden")], "Applied under the cover, before anyone sees the view")
        self.assertEqual((self.offered, deferred), ([42], []),
                         "Media Studio's tab offered in the sample itself: a deferred offer could run after the lift")
        self.wm.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)
        self.assertIsNone(media_shelf_cover._tick(), "Lifted: the views' draws take over")
        self.assertIn(42, media_shelf._applied, "Its draws never apply it a second time")

    def test_the_lift_applies_what_the_samples_have_not_yet(self):
        """A file opened later is covered for about a tenth of a second: the cover lifts before ``STABLE`` samples, so
        the lift itself applies it (``angelo_default_layout._remove_cover`` calls ``before_lift``)."""
        media_shelf_cover.register()
        media_shelf_cover._tick()   # One sample only.
        self.assertEqual(self.applied, [])
        deferred = []
        with mock.patch.object(media_shelf, "_defer", deferred.append):
            media_shelf_cover.before_lift()
        self.assertEqual(deferred, [], "Nothing left for a timer that runs after the lift")
        self.assertEqual(self.applied, [(42, "hidden")])
        self.assertEqual(self.offered, [42], "Media Studio's tab is offered under the cover too, before the lift")
        self.assertNotIn(42, media_shelf._applied, "Not settled yet: its draws check it again after SETTLE")
        layout = (ROOT / "scripts/startup/angelo_default_layout.py").read_text()
        lift = layout.split("def _remove_cover():", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(lift.index("_before_lift()"), lift.index("native.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)"))
        self.assertIn('sys.modules.get("angelo_agent.media_shelf_cover")', layout)

    def test_a_failing_offer_never_keeps_the_remembered_shelf_away(self):
        """Review of cfa9a82bb2d (Low 2): the tab offer runs before the apply in the same sample; an offer that raises
        on the lift's last pass must still let the remembered state apply under the cover."""
        def broken(pointer):
            raise RuntimeError("the library is being written")
        media_shelf_cover.register()
        media_shelf_cover._tick()   # One sample only: the lift's pass applies it.
        with mock.patch.object(media_shelf, "_offer", broken), mock.patch("builtins.print"):
            media_shelf_cover.before_lift()
        self.assertEqual(self.applied, [(42, "hidden")])

    def test_the_tab_is_offered_before_the_shelf_is_applied(self):
        """Review of 4db305d5199 (Low 1): the offer first, as the view's draws do (``media_shelf``)."""
        events = []
        with mock.patch.object(media_shelf, "_offer", lambda pointer: events.append("offer")), \
                mock.patch.object(media_shelf, "_apply", lambda pointer, wanted: events.append("apply")):
            media_shelf_cover.start()
            self.run_timer(media_shelf_cover.STABLE * media_shelf_cover.INTERVAL)
        self.assertEqual(events, ["offer", "apply"])

    def unfinished_offer_is_tried_again_as_the_cover_lifts(self, unfinished):
        """Review of 4db305d5199 (Low 3): an offer that raised or waited (no catalogs yet) under the cover is tried
        again on the lift's pass, not by the views' draws ``RETRY`` seconds after it, in front of the person; never
        again once it was offered."""
        media_shelf_cover.register()
        with mock.patch.object(media_shelf, "_offer", unfinished), mock.patch("builtins.print"):
            self.run_timer(0.5)
        self.assertEqual((self.applied, self.offered), ([(42, "hidden")], []))
        self.assertIn(42, media_shelf._applied)
        tried = media_shelf._offering[42]
        self.run_timer(0.5)
        self.assertEqual(self.offered, [], "Not every sample: once per lift")
        media_shelf_cover.before_lift()
        self.assertEqual(self.offered, [42], "The lift's pass tries it again")
        media_shelf_cover.before_lift()
        self.assertEqual(self.offered, [42], "Offered: never again")
        self.assertEqual(self.applied, [(42, "hidden")], "Applied once")
        return tried

    def test_a_covered_offer_that_raised_is_tried_again_as_the_cover_lifts(self):
        def broken(pointer):
            raise RuntimeError("the library is being written")
        self.unfinished_offer_is_tried_again_as_the_cover_lifts(broken)

    def test_a_covered_offer_that_waited_is_tried_again_as_the_cover_lifts(self):
        """``offer_tab`` False (no catalogs yet)."""
        self.unfinished_offer_is_tried_again_as_the_cover_lifts(lambda pointer: None)

    def test_a_retry_that_waits_again_keeps_the_first_try_time(self):
        """Its draws' next try stays ``RETRY`` after the first covered try, not after the lift."""
        tries = []
        media_shelf_cover.register()
        with mock.patch.object(media_shelf, "_offer", tries.append):   # Every try waits.
            self.run_timer(0.5)
            tried = media_shelf._offering[42]
            media_shelf_cover.before_lift()
        self.assertEqual((tries, media_shelf._offering[42]), ([42, 42], tried))

    def test_each_cover_reports_its_own_first_failure(self):
        """Review of 4db305d5199 (Low 2): a failure under one cover never silences a later cover's (a file load)."""
        printed = []
        with mock.patch("builtins.print", lambda *args: printed.append(args)):
            for _ in range(2):
                media_shelf_cover.start()
                self.failing.append(RuntimeError("a region in the middle of a change"))
                self.failing.append(RuntimeError("a region in the middle of a change"))
                self.run_timer(0.5)
                media_shelf._applied.clear()
                media_shelf_cover._stable.clear()
        self.assertEqual(len(printed), 2, "One report per cover, not one per sample or per session")

    def test_not_before_the_view_has_its_shelf(self):
        """Blender gives a shelf its default state when it creates it: an apply before that would be undone."""
        self.shelves.clear()
        media_shelf_cover.register()
        self.run_timer(0.5)
        media_shelf_cover.before_lift()
        self.assertEqual(self.applied, [])
        self.assertNotIn(42, media_shelf._applied, "Its draws apply it once the shelf exists")

    def test_the_lift_does_nothing_once_the_add_on_is_disabled(self):
        media_shelf_cover.register()
        media_shelf_cover.unregister()   # Disabled: Python keeps the module, the startup layout still finds it.
        media_shelf_cover.before_lift()
        self.assertEqual(self.applied, [])

    def test_a_failed_apply_is_tried_again(self):
        self.failing.append(RuntimeError("a region in the middle of a change"))
        media_shelf_cover.start()
        with mock.patch("builtins.print"):
            self.run_timer(0.5)
        self.assertEqual(self.applied, [(42, "hidden")])

    def test_what_happened_under_the_cover_is_never_learned_after_the_lift(self):
        """The views' draws after the lift settle afresh: a state Blender or a layout step set under the cover (here,
        a shelf expanded after the cover's apply) is never written as the person's choice."""
        media_shelf_cover.start()
        self.run_timer(0.5)
        self.assertEqual(self.applied, [(42, "hidden")])
        self.assertNotIn(42, media_shelf._seen)
        self.area.regions[1].width, self.area.regions[1].height = 1200, 80   # Expanded, unlike what was applied.
        self.wm.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)
        BPY.context.region, BPY.context.window, BPY.context.area = region('WINDOW', 1200, 800), self.window, self.area
        self.addCleanup(lambda: [delattr(BPY.context, name) for name in ("region", "window", "area")])
        for _ in range(int(2 * media_shelf.SETTLE / 0.05)):   # Its draws after the lift, for twice SETTLE.
            with BPY.context.temp_override(window=self.window):
                media_shelf._sample()
            self.clock[0] += 0.05
        self.assertEqual(self.written, [])

    def test_a_view_still_switching_is_not_settled(self):
        """Blender shows or hides the shelf regions over a few frames: a changing state is never applied over."""
        media_shelf_cover.start()
        shelf = self.area.regions[1]
        for step in range(2 * media_shelf_cover.STABLE):
            shelf.width, shelf.height = (1, 1) if step % 2 else (1200, 80)   # Expanding and collapsing in turn.
            media_shelf_cover._tick()
        self.assertEqual(self.applied, [])
        for _ in range(media_shelf_cover.STABLE):
            media_shelf_cover._tick()
        self.assertEqual(len(self.applied), 1, "Applied once it held")

    def test_never_remembered_as_the_persons_choice(self):
        """Nobody sees or touches a covered view: whatever its shelf does under the cover is never remembered."""
        media_shelf_cover.start()
        self.run_timer(0.5)
        self.area.regions[1].width, self.area.regions[1].height = 1200, 80   # Something under the cover expands it.
        self.run_timer(0.5)
        self.assertEqual(self.written, [])
        self.assertEqual(len(self.applied), 1, "Once per view and file")

    def test_only_object_mode_outside_director_and_the_mode_is_each_windows(self):
        sculpt = SimpleNamespace(name="second", mode='SCULPT')
        director = SimpleNamespace(name="director", mode='OBJECT')
        self.director.add("director")
        self.views = [(sculpt, collapsed_view(7)), (director, collapsed_view(8)), (self.window, self.area)]
        BPY.context.mode = 'SCULPT'   # The context's own window is another: each window's mode is read under it.
        media_shelf_cover.start()
        self.run_timer(0.5)
        self.assertEqual(self.applied, [(42, "hidden")])

    def test_one_failing_view_never_ends_the_timer(self):
        broken = SimpleNamespace(type='VIEW_3D', as_pointer=lambda: 9, regions=None)   # state() raises on it.
        self.views = [(self.window, broken), (self.window, self.area)]
        media_shelf_cover.start()
        with mock.patch("builtins.print"):
            self.assertIsNotNone(self.run_timer(0.5))
        self.assertEqual(self.applied, [(42, "hidden")])

    def test_it_waits_a_moment_for_a_cover_not_yet_up(self):
        self.wm.angelo_startup_cover = (0.0, 0.0, 0.0, 0.0)
        media_shelf_cover.start()   # A file load's handler can run before the start-up raises the cover.
        self.assertIsNotNone(media_shelf_cover._tick())
        self.wm.angelo_startup_cover = (0.1, 0.1, 0.1, 1.0)
        self.assertIsNotNone(self.run_timer(0.5))
        self.assertEqual(self.applied, [(42, "hidden")])

    def test_without_a_native_cover_it_gives_up_after_its_grace(self):
        self.wm = SimpleNamespace()   # A binary without WindowManager.angelo_startup_cover: the views still draw.
        BPY.context.window_manager = self.wm
        media_shelf_cover.start()
        self.assertIsNone(self.run_timer(media_shelf_cover.GRACE + 0.5))
        self.assertEqual(self.applied, [])

    def test_a_file_load_starts_it_again(self):
        media_shelf_cover.register()
        self.assertIn(media_shelf_cover._load_post, BPY.app.handlers.load_post)
        BPY.app.timers.unregister(media_shelf_cover._tick)
        media_shelf_cover._load_post(None)
        self.assertTrue(BPY.app.timers.is_registered(media_shelf_cover._tick))
        media_shelf_cover.unregister()
        self.assertNotIn(media_shelf_cover._load_post, BPY.app.handlers.load_post)
        self.assertFalse(BPY.app.timers.is_registered(media_shelf_cover._tick))

    def test_never_in_background_mode(self):
        with mock.patch.object(BPY.app, "background", True):
            media_shelf_cover.register()
        self.assertFalse(BPY.app.timers.is_registered(media_shelf_cover._tick))
        self.assertNotIn(media_shelf_cover._load_post, BPY.app.handlers.load_post)


if __name__ == "__main__":
    unittest.main()
