# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Object-mode shelf's remembered state applied under Angelo's start-up cover (1.0.10).

``media_shelf`` learns a view's shelf visibility from the view's draws and applies the remembered choice once the
view has been in Object mode for ``media_shelf.SETTLE`` seconds (Blender shows or hides the shelf regions over a few
draws while a view enters a mode). The native cover does not render the viewports it hides (``wm_angelo_cover.cc``),
so under it no 3D view draws: the remembered hidden or expanded shelf would change in front of the person after the
lift, the layout jump the cover exists to hide. A cover can also be shorter than ``SETTLE`` (a file opened later is
covered for a fraction of a second), so this does not wait for ``SETTLE``: the regions' layout still runs under the
cover, and a view whose shelf state has stayed the same for ``STABLE`` samples in a row has settled. The remembered
choice is applied to it then, or at the latest when the cover lifts (``before_lift``, called by
``angelo_default_layout._remove_cover``: a cover can lift before ``STABLE`` samples), once the view's shelf exists,
once per view and file (``media_shelf._applied``, which its draws honour). Nothing here is ever remembered as the
person's choice (nobody sees or touches a covered view), and its draws after the lift settle afresh.

It runs from the add-on's registration and after every file load (Angelo's start-up raises the cover for both), and
stops once the cover is off (or never came up within ``GRACE`` seconds: a binary without the native cover, whose
cover is drawn over views that still draw). A view whose sample raises is skipped for that tick, never the timer.
"""

import time

import bpy
from bpy.app.handlers import persistent

from . import live_windows

INTERVAL = 0.05   # Seconds between samples under the cover (a walk over the windows' areas: microseconds).
STABLE = 3        # Samples in a row with the same shelf state: the view's layout has settled (a few frames).
GRACE = 1.0       # Seconds to wait for the cover to come up after a registration or a file load.
_state = {"started": None, "seen": False, "failed": False, "registered": False}
_stable = {}      # Area pointer -> (shelf state at its last covered sample, samples in a row with that state).


def sample(window, area, mode, last=False):
    """One covered sample of a 3D view: apply the remembered shelf state once the view has settled in Object mode
    (``STABLE`` samples, or ``last``: the cover lifts now) and its shelf exists (Blender gives a new shelf its default
    state when it creates it, which would undo an earlier apply). Also offers Media Studio's tab first, as its draws do
    (a try that waited or raised under the cover is tried again as the cover lifts)."""
    from . import director_layout, media_shelf
    pointer = area.as_pointer()
    if mode not in media_shelf.MODES or director_layout.is_director(window):
        _stable.pop(pointer, None)   # Another mode's shelf, or Director's hidden one: not the remembered shelf.
        return
    shown = media_shelf.state(area)
    previous, count = _stable.get(pointer, (None, 0))
    count = count + 1 if shown == previous else 1
    _stable[pointer] = (shown, count)
    applied = pointer in media_shelf._applied
    if (count < STABLE and not last) or (applied and not last) or media_shelf._shelf(window, area) is None:
        return
    tried = media_shelf._offering.get(pointer)   # True once offered; a time when the last try waited or raised.
    if tried is None or (last and tried is not True):   # An unfinished covered try gets the lift's pass too: its
        if tried is None:                               # draws' next try (``RETRY`` after the first) is after it.
            media_shelf._offering[pointer] = time.monotonic()
        try:
            media_shelf._offer(pointer)   # A timer, not a draw: offered now, never in a timer run after the lift.
        except Exception as error:   # noqa: BLE001 - the tab is an offer: it never keeps the remembered shelf away.
            _report(error)
    if applied:
        return   # Applied once the view settled: its draws honour that.
    wanted = media_shelf.remembered()
    if wanted is not None and wanted != shown:
        media_shelf._apply(pointer, wanted)   # A timer, not a draw: the property writes are allowed here.
    if count >= STABLE:   # Only once applied to a settled view: its draws check one still switching again later.
        media_shelf._applied.add(pointer)
    media_shelf._seen.pop(pointer, None)   # Its draws after the lift settle afresh: nothing covered is ever learned.


def _views(wm, last=False):
    for window, area in live_windows.window_areas(wm):
        if area.type != 'VIEW_3D':
            continue
        try:
            with bpy.context.temp_override(window=window, area=area):
                mode = bpy.context.mode
            sample(window, area, mode, last)
        except Exception as error:   # noqa: BLE001 - Blender drops a timer that raises: one view never ends the rest.
            _report(error)


def _report(error):
    if not _state["failed"]:   # Once per cover (``start``): a failure repeats every sample.
        _state["failed"] = True
        print("Angelo: the shelf under the start-up cover:", error)


def before_lift():
    """The cover lifts now (``angelo_default_layout._remove_cover``): every covered view not applied yet gets the
    remembered state, however few samples it had (a file opened later is covered for about a tenth of a second)."""
    wm = bpy.context.window_manager
    if _state["registered"] and wm is not None and live_windows.covered(wm):   # Not after the add-on is disabled.
        _views(wm, last=True)


def _tick():
    wm = bpy.context.window_manager
    if wm is None or not live_windows.covered(wm):
        if _state["seen"] or time.monotonic() - _state["started"] > GRACE:
            _state["started"] = None   # Lifted (the views draw from now on), or no native cover.
            _stable.clear()
            return None
        return INTERVAL
    _state["seen"] = True
    _views(wm)
    return INTERVAL


def start():
    """Sample under the cover from now until it lifts (one timer: a second start restarts its wait)."""
    _state["started"], _state["seen"], _state["failed"] = time.monotonic(), False, False
    _stable.clear()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0, persistent=True)


@persistent
def _load_post(_):
    start()


def register():
    if bpy.app.background:
        return   # No window, no cover.
    bpy.app.handlers.load_post.append(_load_post)
    _state["registered"] = True
    start()


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _state["started"], _state["seen"], _state["registered"] = None, False, False
    _stable.clear()
