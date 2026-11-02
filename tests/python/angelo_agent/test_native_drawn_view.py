# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Main tile's picture is decided from the artist's view as its region drew it from its settings now.

Blender computes a 3D view's matrices only when its region draws, so for a moment after a script or a tool sets the
view, ``RegionView3D.view_matrix`` is the previous frame's. The 1.0.8 T4 round f3038da40a4 failed
test_blender_rail_crowd's view-at-the-sky case: the rail's timer ran between the view being set and its region
drawing, and decided through the previous case's view of a ground plane. ``drawn_view`` records what each frame was
drawn from (PRE_VIEW) and ``rail_pictures.refresh`` waits for the frame that brings the matrices up to date (tagging
it), at most ``drawn_view.WAIT``. Without Blender: stub views; the window's own proof is
test_blender_rail_view_drawn.
"""

import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_native_rail_picture_retry as retry
from angelo_agent import drawn_view, parallel_sessions, rail_pictures

IDENTITY = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))


class Pointer:
    def as_pointer(self):
        return id(self)


class View(Pointer):
    """A RegionView3D: its settings, and the perspective matrix its last frame computed."""

    def __init__(self):
        self.view_rotation, self.view_location, self.view_distance = (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 10.0
        self.view_perspective, self.view_camera_zoom, self.view_camera_offset = 'PERSP', 0.0, (0.0, 0.0)
        self.perspective_matrix = IDENTITY

    def draw(self):   # Its region's frame: the matrices from the settings now.
        self.perspective_matrix = ((self.view_distance,) + self.view_rotation[1:],) + IDENTITY[1:]


class Region(Pointer):
    def __init__(self, data, kind='WINDOW'):
        self.data, self.type, self.width, self.height, self.tags = data, kind, 800, 500, 0

    def tag_redraw(self):
        self.tags += 1


def setup(lock=None, camera=None):
    view = View()
    space = SimpleNamespace(region_3d=view, lens=50.0, clip_start=0.01, clip_end=1000.0, camera=camera,
                            lock_object=lock, lock_bone="", lock_cursor=False)
    window_region, header = Region(view), Region(None, 'HEADER')
    area = SimpleNamespace(spaces=SimpleNamespace(active=space), regions=[header, window_region])
    scene = SimpleNamespace(cursor=SimpleNamespace(location=(0.0, 0.0, 0.0)))
    return view, space, window_region, header, area, scene


def frame(view, space, region, scene):
    """The region draws: its matrices from its settings, then PRE_VIEW notes them."""
    view.draw()
    drawn_view.note(space, view, region, scene)


def current(view, space, region, scene):
    return drawn_view.current(space, view, region.width, region.height, scene)


class Thing(Pointer):
    def __init__(self, **data):
        self.matrix_world = IDENTITY
        self.data = SimpleNamespace(**data) if data else None


class DrawnViewTests(unittest.TestCase):
    def setUp(self):
        drawn_view.forget()
        drawn_view.gave_up[0] = 0

    def test_a_view_is_current_only_once_its_region_drew_its_settings(self):
        view, space, region, _header, _area, scene = setup()
        self.assertFalse(current(view, space, region, scene))   # Never drawn: its first frame notes it.
        frame(view, space, region, scene)
        self.assertTrue(current(view, space, region, scene))
        view.view_rotation = (0.0, 1.0, 0.0, 0.0)   # A script sets the view: the matrices are the old frame's.
        self.assertFalse(current(view, space, region, scene))
        frame(view, space, region, scene)
        self.assertTrue(current(view, space, region, scene))

    def test_every_setting_the_matrices_come_from_counts(self):
        changes = [lambda v, s, r, c: setattr(v, "view_location", (1.0, 0.0, 0.0)),
                   lambda v, s, r, c: setattr(v, "view_distance", 2.0),
                   lambda v, s, r, c: setattr(v, "view_perspective", 'ORTHO'),
                   lambda v, s, r, c: setattr(s, "lens", 24.0),
                   lambda v, s, r, c: setattr(s, "clip_start", 0.1),
                   lambda v, s, r, c: setattr(s, "clip_end", 50.0),
                   lambda v, s, r, c: setattr(r, "width", 640),
                   lambda v, s, r, c: setattr(s, "lock_cursor", True)]
        for change in changes:
            drawn_view.forget()
            view, space, region, _header, _area, scene = setup()
            frame(view, space, region, scene)
            change(view, space, region, scene)
            self.assertFalse(current(view, space, region, scene))

    def test_through_a_camera_or_locked_to_an_object_its_place_counts(self):
        camera = Thing(type='PERSP', lens=50.0, ortho_scale=6.0, sensor_width=36.0, sensor_height=24.0,
                       sensor_fit='AUTO', shift_x=0.0, shift_y=0.0, clip_start=0.1, clip_end=100.0)
        view, space, region, _header, _area, scene = setup(camera=camera)
        view.view_perspective = 'CAMERA'
        frame(view, space, region, scene)
        camera.matrix_world = ((1.0, 0.0, 0.0, 5.0),) + IDENTITY[1:]   # The camera moved: drawn next frame.
        self.assertFalse(current(view, space, region, scene))
        frame(view, space, region, scene)
        camera.data.lens = 85.0
        self.assertFalse(current(view, space, region, scene))
        drawn_view.forget()
        target = Thing()
        view, space, region, _header, _area, scene = setup(lock=target)
        frame(view, space, region, scene)
        target.matrix_world = ((1.0, 0.0, 0.0, 3.0),) + IDENTITY[1:]
        self.assertFalse(current(view, space, region, scene))

    def test_a_frame_drawn_through_other_matrices_is_not_the_views(self):
        """A picture drawn with the view's context (another add-on's offscreen ``draw_view3d``) runs PRE_VIEW with
        its own matrices; the view's own, restored after it, are not those."""
        view, space, region, _header, _area, scene = setup()
        frame(view, space, region, scene)
        view.view_rotation = (0.0, 1.0, 0.0, 0.0)
        stale = view.perspective_matrix
        view.perspective_matrix = IDENTITY   # The offscreen's own matrices while its PRE_VIEW runs.
        drawn_view.note(space, view, region, scene)
        view.perspective_matrix = stale   # Restored: still the old frame's.
        self.assertFalse(current(view, space, region, scene))

    def test_ready_waits_for_the_frame_tagging_its_view_never_longer_than_wait(self):
        view, space, region, header, area, scene = setup()
        found = (view, region.width, region.height)
        self.assertTrue(drawn_view.ready("lead", area, None, scene, 0.0))   # No view: nothing to wait for.
        self.assertFalse(drawn_view.ready("lead", area, found, scene, 10.0))
        self.assertEqual((region.tags, header.tags), (1, 0))   # Its frame is asked for; its header is not.
        self.assertFalse(drawn_view.ready("lead", area, found, scene, 10.0 + drawn_view.WAIT / 2))
        frame(view, space, region, scene)
        self.assertTrue(drawn_view.ready("lead", area, found, scene, 10.5 + drawn_view.WAIT / 2))
        view.view_location = (0.0, 0.0, 9.0)   # A later change waits afresh.
        self.assertFalse(drawn_view.ready("lead", area, found, scene, 20.0))
        given_up = drawn_view.gave_up[0]
        for tick in range(1, 8):   # Asked about every poll: a window not drawn gets its picture after WAIT.
            self.assertFalse(drawn_view.ready("lead", area, found, scene, 20.0 + tick * drawn_view.WAIT / 8))
        self.assertTrue(drawn_view.ready("lead", area, found, scene, 20.0 + drawn_view.WAIT))
        self.assertEqual(drawn_view.gave_up[0], given_up + 1)

    def test_a_wait_left_off_never_lets_a_later_change_through_at_once(self):
        """Break-it review: a wait left off (a hold, hidden panels) kept its old clock, so the next change, long
        after, was decided at once through the previous frame's matrices."""
        view, space, region, _header, area, scene = setup()
        found = (view, region.width, region.height)
        self.assertFalse(drawn_view.ready("lead", area, found, scene, 10.0))   # Waits; then nobody asks for a while.
        frame(view, space, region, scene)
        view.view_rotation = (0.0, 0.0, 1.0, 0.0)   # Much later a script sets the view: still before its frame.
        self.assertFalse(drawn_view.ready("lead", area, found, scene, 30.0))
        frame(view, space, region, scene)
        self.assertTrue(drawn_view.ready("lead", area, found, scene, 30.25))

    def test_the_view_drawn_now_is_never_the_one_dropped(self):
        view, space, region, _header, _area, scene = setup()
        frame(view, space, region, scene)
        for _ in range(80):   # Views of layouts rebuilt over a long session, each drawn once.
            other, other_space, other_region, _h, _a, _s = setup()
            frame(other, other_space, other_region, scene)
            frame(view, space, region, scene)   # The artist's own view draws on.
        self.assertTrue(current(view, space, region, scene))


def chosen(*args, **kwargs):   # The view chosen (``lead_choice``'s own tests are test_native_rail_main_view).
    yield from ()
    return IDENTITY, IDENTITY, "view"


@contextlib.contextmanager
def lead_world():
    view, space, region, header, area, scene = setup()
    window = SimpleNamespace(scene="scene", view_layer="layer")
    with retry.world() as w, patch.object(rail_pictures, "_host", lambda: (window, area, region)), \
            patch.object(rail_pictures, "lead_choice", chosen):
        drawn_view.forget()
        try:
            yield w, view, space, region, scene
        finally:
            drawn_view.forget()


class RefreshTests(unittest.TestCase):
    def test_the_lead_is_decided_on_the_frame_of_the_view_set_now(self):
        """FAILS before the fix: the lead was drawn at once, through the matrices of the view's previous frame."""
        with lead_world() as (w, view, space, region, scene):
            frame(view, space, region, scene)
            view.view_rotation = (0.0, 0.0, 0.0, 1.0)   # A script looks at the sky; its region has not drawn yet.
            parallel_sessions.stale[retry.LEAD] = 10.0
            self.assertEqual(retry.settle(10.0), [])
            self.assertEqual(w.attempts, 0)
            self.assertIn(retry.LEAD, parallel_sessions.stale)   # Still owed; never counted as a failed draw.
            self.assertNotIn(retry.LEAD, rail_pictures._retry)
            self.assertGreaterEqual(region.tags, 1)   # The frame it waits for is asked for.
            frame(view, space, region, scene)
            self.assertEqual(retry.settle(10.25), [retry.LEAD])
            self.assertNotIn(retry.LEAD, parallel_sessions.stale)

    def test_a_view_its_region_never_draws_still_gets_its_picture(self):
        with lead_world() as (w, view, space, region, scene):
            parallel_sessions.stale[retry.LEAD] = 10.0
            ticks = [10.0 + rail_pictures.POLL * n for n in range(int(drawn_view.WAIT / rail_pictures.POLL))]
            for now in ticks:   # Never drawn (a minimized window): waited for, the pictures timer asking each poll...
                self.assertEqual(retry.settle(now), [])
            self.assertEqual(retry.settle(10.0 + drawn_view.WAIT), [retry.LEAD])   # ...at most WAIT.

    def test_a_current_view_is_drawn_at_once(self):
        with lead_world() as (w, view, space, region, scene):
            frame(view, space, region, scene)
            parallel_sessions.stale[retry.LEAD] = 10.0
            self.assertEqual(retry.settle(10.0), [retry.LEAD])
            self.assertEqual(region.tags, 0)


if __name__ == "__main__":
    unittest.main()
