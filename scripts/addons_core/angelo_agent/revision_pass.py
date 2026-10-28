# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact revisions of several objects of the bound scene, read as one pass with nothing edited between them.

A read-back after an edit (a reviewed script's ``after``, ``script_after``) gives up to 24 objects an exact
revision each. ``scene_queries.object_revision`` read each one alone: it listed the scene's objects to check
membership (about 40 µs at 1,536 objects) and re-read every datablock the objects share, such as one material's
node graph, once per object. ``RevisionPass`` lists the scene once and lets the objects' captures share their reads
of shared datablocks (``scene_shared_reads``), as a whole-scene pass does. Each object still gets a work budget of
its own, on which a shared read replays every charge and limitation, so every revision is exactly the one
``object_revision`` returns for it (24 cubes sharing one material: 10.4 ms read alone, 6.8 ms as one pass). The
pass lives for one read-back on the main thread: an edit between its reads would need a new pass.
"""

from . import live_target, scene_queries
from .scene_read_values import SceneQueryError


class RevisionPass:
    """``object_revision`` for objects of ``window``'s scene, read with nothing edited between the calls."""
    _window = live_target.WindowAttribute()   # Held by identity: a window gone since reads as None, never freed data.

    def __init__(self, window):
        self._window, self._shared, self._members = window, {}, None

    def __call__(self, obj):
        """Raises what ``object_revision`` raises, for this object alone (its caller states why it has none)."""
        scene_queries._main_thread()
        window = self._window
        if window is None:   # Closed or replaced since the pass began: no revision, as for an unreadable one.
            raise SceneQueryError("The bound window is gone")
        if self._members is None:   # Listed at the first read: a pass that reads nothing lists nothing.
            self._members = {member.as_pointer() for member in window.scene.objects}
        if obj.as_pointer() not in self._members:   # As object_revision refuses it (scene_queries._bound_target).
            raise SceneQueryError("Native program target must belong to the bound scene")
        work = scene_queries._read_budget()
        work.shared = self._shared   # The pass's shared reads; the budget, charges and limitations are this object's.
        return scene_queries._fingerprints.revision(window, obj, work)
