# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The layout locks (``ui._layout_timer``: docks, Hide, the tab build; ``director._timer``: Director's mode switch) end
with the timer that holds them, also when a load or the add-on's unregister ends that timer.

A lock is the timer that holds it; everything that waits for a settled layout (Director, the start-up build, the
cards, ``layout_after``) waits while it is set. Left set after its timer was gone, it never cleared: after a load
during a dock or Hide, Director refused, the start-up build never ran and every ``layout_after`` waited its 10 s
(break-it review of 5ca5f33c94f, the class of the 1.0.5 RC stuck layout). So a timer job's release (``timer_jobs``)
clears the lock its timer holds, and at ``load_post`` any lock whose timer the load discarded is cleared too: holds
that are not a job's (the dock's wait for the chat to draw) included. A failed load keeps its timers, and the locks.
"""


def layout(timer):
    """A timer job's release (``timer_jobs.track``) while ``timer`` holds ``ui._layout_timer``."""
    def release():
        from . import ui
        if ui._layout_timer is timer:
            ui._layout_timer = None
    return release


def mode(timer):
    """A timer job's release while ``timer`` holds ``director._timer``."""
    def release():
        from . import director
        if director._timer is timer:
            director._timer = None
    return release


def _load_post(*_args):
    import bpy
    from . import director, ui
    for holder, name in ((ui, "_layout_timer"), (director, "_timer")):
        timer = getattr(holder, name)
        if timer is not None and not bpy.app.timers.is_registered(timer):
            setattr(holder, name, None)


# As @persistent: a load never drops it. No bpy at import: the modules holding a lock import this, host tests included.
_load_post._bpy_persistent = True


def register():
    """First in the list, before any handler that waits for a settled layout looks."""
    import bpy
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.insert(0, _load_post)


def unregister():
    import bpy
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
