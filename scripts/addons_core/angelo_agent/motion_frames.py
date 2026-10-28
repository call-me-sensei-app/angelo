# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's evaluated state at other frames, read on the main thread and put back: nothing about the scene changes.

``Sampler`` moves the scene to each frame with ``scene.frame_set`` and reads Blender's evaluated state there: the
update every view layer's depsgraph runs for a new frame (animation, drivers, NLA, constraints, parents, time-reading
modifiers), with no ID tagged as edited, so no physics cache is outdated. ``frame_set`` is the path Blender's own
Python API keeps for this: unlike setting ``frame_current`` it queues no frame-change notifier, whose interface
update would run once more after the read and replace every unkeyed edit of an animated channel in the scene
(``rna_scene_api.cc``, #24690; otherwise a read in the interface lost an untouched object's edit),
publishes no message-bus change of the frame, and does not clamp a negative frame. It evaluates every view layer, so
the read covers every layer (``motion_guards``).

``open`` first evaluates the edit's own pending updates in every view layer's depsgraph (a depsgraph not evaluated
since objects were removed still lists them, and reading them crashed the app, in the bound layer and in any
other), then keeps the stored value of every channel an animation can
write in any view layer's depsgraph (``animated_values``: every animated ID listed whole, else not measured).
``close`` returns to the frame and subframe with the same call, writes back every stored value the evaluations
replaced (an unkeyed edit stands until its animation is evaluated again, and the model's or the artist's own must
outlive a read), sets the scene camera back if a marker switched it, updates every layer's depsgraph, and states when
the frame did not come back (``restored``).
The change feed, view cache and scene revision counter ignore the read's own updates (``change_feed.paused``): a
frames read never stales an approved script's precondition (otherwise four updates did); a frame that did not
come back counts as a change. What is not measured is ``motion_guards.unmeasured``.

Bounded by ``MAX_SECONDS`` of wall time, the stall a person sees (depsgraph evaluation runs on many threads, so the
main thread's own CPU time understated it several times over), counted from ``open`` once the edit's own pending
updates ran: the scene-wide checks and the plan count, and a frame's cost is its evaluation and its read together
(measured: a 100,000-object scene spent 0.2 s before the clock started, and measuring 14,000
instances 65 ms a frame outside it). Before each frame, the time spent plus the slowest frame so far for that frame
and the return must fit. One frame is never interrupted, so a read costs at most about ``MAX_SECONDS`` plus one frame;
the first read of a heavy scene pays that once, and while its projected cost (the read's work beside its frames,
plus two frames) is over the budget, later reads are not measured at once (``motion_guards.Estimates``).
"""

import time
from contextlib import nullcontext

from . import live_target
from .motion_guards import Estimates, graphs, subscribed, unmeasured

MAX_SECONDS = 0.3
ESTIMATES = Estimates()


class Sampler:
    """Evaluate the scene at frames and put everything back (module docstring). Main thread only.

    ``open()`` returns a reason when it cannot start (nothing was evaluated at another frame); ``at(frame)``
    evaluates one frame, or returns False when that frame and the return would pass the budget; ``close()`` always
    runs (use ``with``)."""
    window = live_target.WindowAttribute()   # Read live at each frame (live_target).

    def __init__(self, window, clock=time.perf_counter, seconds=MAX_SECONDS, mode=None):
        self.window, self.seconds, self.clock, self.mode = window, seconds, clock, mode
        self.started = self.channels = self.value = None
        self.opened, self.restored, self.read, self.slowest, self.framed = False, True, 0, 0.0, 0.0

    def open(self):
        from . import animated_values
        scene, layer = self.window.scene, self.window.view_layer
        # The edit's own pending updates first (recorded as any edit's are): the depsgraph must be current before its
        # IDs are read, since a stale one still lists objects an edit removed, and every part must be evaluated.
        layer.update()
        for graph in graphs(scene):  # Every other layer's too: one an edit has not updated lists objects it freed.
            if graph is not None and graph != layer.depsgraph:
                graph.update()
        _first_use()
        self.started = self.clock()  # The read's own work from here; the updates above are the edit's.
        reason = unmeasured(self.window, self.mode, self.seconds, ESTIMATES)
        if reason:
            return reason
        owners = [owner for graph in graphs(scene) for owner in animated_values.animated_ids(graph)]
        self.channels = animated_values.Channels(owners)
        if self.channels.partial:
            return "the scene animates more channels than a read keeps (%d)" % animated_values.MAX_CHANNELS
        reason = subscribed(self.channels, self.mode)
        if reason:
            return reason
        self.frame, self.subframe, self.camera = scene.frame_current, scene.frame_subframe, scene.camera
        self.opened = True
        return None

    def at(self, frame, read=None):
        """Evaluate ``frame`` and call ``read`` there (its result in ``value``); False when the time spent, this
        frame and the return would pass the budget (the first frame is read unless the scene is held as slow)."""
        spent = self.clock() - self.started
        if self.read and spent + 2 * self.slowest > self.seconds:
            return False
        started = self.clock()
        with _paused():
            self.window.scene.frame_set(int(frame), subframe=0.0)
        self.value = read() if read is not None else None
        took = self.clock() - started
        self.slowest, self.read, self.framed = max(self.slowest, took), self.read + 1, self.framed + took
        return True

    def uncounted(self, work):
        """Run ``work`` (a first-use import) outside the read's time."""
        started = self.clock()
        work()
        if self.started is not None:
            self.started += self.clock() - started

    def close(self):
        if not self.opened:
            return
        from . import animated_values
        self.opened = False
        scene, layer = self.window.scene, self.window.view_layer
        with _paused():
            scene.frame_set(self.frame, subframe=self.subframe)
            after = self.channels.read()
            animated_values._restore(self.channels, self.channels.values, after)
            if scene.camera != self.camera:
                scene.camera = self.camera
            layer.update()
            for graph in graphs(scene):  # The other layers' evaluated state, of the values put back.
                if graph is not None and graph != layer.depsgraph:
                    graph.update()
        if self.read:  # The work beside the frames (checks, plan, return), and two frames: the least that tells motion.
            ESTIMATES.note(scene, self.clock() - self.started - self.framed + 2 * self.slowest, self.seconds)
        self.restored = scene.frame_current == self.frame and abs(scene.frame_subframe - self.subframe) < 1e-6
        if not self.restored:
            _changed(scene)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _first_use():
    """Import what a read uses before its clock starts: a first read in a session would otherwise count the imports
    as the scene's cost and hold a fast scene as slow (``motion_guards.Estimates``); numpy only when instances are
    read (``Sampler.uncounted``)."""
    from . import animated_values, dependency_paths, input_policy, job_ui, render_extensions, scene_queries  # noqa: F401


def _changed(scene):
    """The frame did not come back: the scene changed as an edit's would (its revision and cached views)."""
    try:
        from . import blender_tools, view_cache
    except ImportError:
        return
    uid = scene.session_uid
    blender_tools._scene_revisions[uid] = blender_tools._scene_revisions.get(uid, 0) + 1
    view_cache.note_scene_change(uid)


def _paused():
    try:
        from .change_feed import paused
    except ImportError:
        return nullcontext()
    return paused()
