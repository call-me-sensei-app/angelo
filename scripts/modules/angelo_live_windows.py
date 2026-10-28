# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The one walk over Blender's windows and their areas for every Angelo module.

A window has no screen for a moment while an operator creates or closes it, and a draw callback that reads
``window.screen.areas`` then raises and loses its frame. Every Angelo walk goes through here, so each one skips such
a window by construction; ``tests/python/angelo_agent/test_native_live_windows.py`` fails on a direct
``.windows`` or ``.areas`` read anywhere else.

Callers pass the window manager (``bpy.context.window_manager`` or ``context.window_manager``) so this module never
imports ``bpy`` and serves the agent add-on, the console modules and ``angelo_render_device`` alike (the last must
never import the agent package). The agent reaches it as ``angelo_agent.live_windows``.

Three questions differ, and each has its own call. "Which windows can I read areas of?" is ``windows``/``find``. "Is
any window busy, or where do I restore a cursor?" never reads a screen, so it asks ``every_window`` (or
``find(..., live=False)``) and a window between screens still counts: a guard must never read it as idle. "Is a window
gone, or only between screens?" is ``find(..., live=False)`` after ``find`` returned None: deferred work waits for
the second and gives up only on the first. A deferred step (a ``bpy.app.timers`` callback for one window) is built
with ``deferred``, which asks that question for it, and every wait is bounded (``Wait``): a window that never gets a
screen counts as gone after ``WAIT_S``, so nothing waits for good.
"""

import time

WAIT_S = 2.0      # The longest anything waits for a window between screens (seconds).
RETRY_S = 0.05    # How soon a waiting step looks again.
GAP_S = 30.0      # A wait not fed for this long is left over from an earlier run and starts anew (feeders feed every
                  # tick, so a slow frame never stretches a wait past WAIT_S).


class Wait:
    """Bounded waiting: ``wait(waiting)`` is True while ``waiting`` has held for less than ``limit`` seconds in a row,
    False once it no longer holds or the limit has passed (then the caller acts as if the wait were over). A caller
    that feeds it only now and then is safe: a gap of ``GAP_S`` between feeds starts a new wait, so a wait left over
    from an earlier moment never cuts a new one short."""

    def __init__(self, limit=WAIT_S, clock=time.monotonic):
        self.limit, self.clock, self.since, self.last = limit, clock, None, None

    def __call__(self, waiting):
        now = self.clock()
        if self.last is not None and now - self.last >= GAP_S:
            self.since = None   # Not fed for a while: an old wait never cuts a new one short.
        self.last = now
        if not waiting:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        return now - self.since < self.limit


def deferred(manager, pointer, step, *, settle=False, limit=WAIT_S, clock=time.monotonic):
    """A ``bpy.app.timers`` callback running ``step(window)`` for the window ``pointer``: the live window, or None once
    it is gone. While that window is between screens (with ``settle``: while any window is, for a step that must
    see every window, such as one that may open a chat or a window) it retries every ``RETRY_S`` seconds, for at
    most ``limit`` seconds in a row. ``manager`` returns the window manager (``lambda: bpy.context.window_manager``).
    Returns what ``step`` returns, so a multi-step job keeps its own intervals."""
    wait = Wait(limit, clock)

    def run():
        wm = manager()
        window = find(wm, pointer)
        if wait((window is None and between_screens(wm, pointer)) or (settle and not settled(wm))):
            return RETRY_S
        return step(window)
    return run


def every_window(manager):
    """Every window of ``manager``, a screen-less one included: only for reads that never touch ``window.screen``
    (modal operators, the cursor, pointer sets). Empty when ``manager`` is None."""
    return list(manager.windows) if manager is not None else []


def settled(manager):
    """No window of ``manager`` is between screens (being created or closed) right now."""
    return all(window.screen is not None for window in every_window(manager))


def windows(manager, *, temporary=True):
    """The windows of ``manager`` that have a screen, in Blender's order; ``temporary=False`` also leaves out
    temporary screens (Preferences, the render view, a file browser). Empty when ``manager`` is None."""
    if manager is None:
        return []
    return [window for window in manager.windows
            if window.screen is not None and (temporary or not window.screen.is_temporary)]


def first(manager, *, temporary=True):
    """The first live window of ``manager``, or None."""
    return next(iter(windows(manager, temporary=temporary)), None)


def find(manager, pointer, *, live=True):
    """The live window whose ``as_pointer()`` is ``pointer``, or None (closed, or without a screen right now).
    ``live=False`` also finds a window without a screen: None then means it is gone."""
    if not pointer:
        return None
    candidates = windows(manager) if live else every_window(manager)
    return next((window for window in candidates if window.as_pointer() == pointer), None)


def prune(windows, pointers, still):
    """Forget the tracked windows in ``pointers`` (a set of ``as_pointer()`` values) that are gone, or that have a
    screen and no longer pass ``still(window)``. A window between screens is kept: what it shows is not known yet.
    ``windows`` is every window (``every_window(manager)``; a caller outside the add-on may pass
    ``window_manager.windows``). Returns the first window still tracked (it may be between screens), or None."""
    present = {window.as_pointer(): window for window in windows}
    for pointer in list(pointers):
        window = present.get(pointer)
        if window is None or (window.screen is not None and not still(window)):
            pointers.discard(pointer)
    return next((present[pointer] for pointer in pointers), None)


def screenless(window):
    """``window`` exists but has no screen right now (being created or closed); False for None."""
    return window is not None and window.screen is None


def between_screens(manager, pointer):
    """The window ``pointer`` exists but has no screen right now: a deferred step for it retries shortly instead of
    treating the window as closed."""
    return find(manager, pointer) is None and find(manager, pointer, live=False) is not None


def areas(window):
    """The areas of ``window``'s screen; empty when ``window`` is None or has no screen."""
    return screen_areas(window.screen if window is not None else None)


def screen_areas(screen):
    """The areas of ``screen`` (a window's, or one of a workspace's ``screens``); empty when ``screen`` is None."""
    return list(screen.areas) if screen is not None else []


def window_areas(manager, *, temporary=True):
    """``(window, area)`` for every area of every live window of ``manager``."""
    return [(window, area) for window in windows(manager, temporary=temporary) for area in window.screen.areas]
