# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Away regions: worker lanes run while Blender's main thread is in another chat's tools (``worker_dispatch``'s
module docstring; lane g3b, item 2b). Standard library only and no ``bpy``; ``worker_dispatch`` re-exports these.

``_away`` is written by the main thread (``woken`` by the clock); a lane reads it under its own lock, so it either takes
the baton before ``reclaim`` clears the region (and ``reclaim`` waits that section out) or sees it cleared.
"""

import threading
import time

STATS = {"steps": 0, "io": 0, "main_waits_ms_max": 0.0, "main_asks": 0, "main_asks_ms_max": 0.0,
         "io_under_lock": 0, "free_steps": 0, "free_resumes": 0, "reclaims": 0, "reclaim_ms_max": 0.0}
AWAY_AFTER = 0.008   # A region lets lanes run once it lasted this long: ``ToolDispatchBudget``'s tick.
_lanes = {}   # chat id -> worker_dispatch.Lane
_away = {"on": False, "since": 0.0, "depth": 0, "woken": False}
_clock = threading.Condition()
_clocks = {"thread": None, "armed": False}


def away(controller=None):
    """The main thread starts running ``controller``'s tools, not a worker chat's (``tool_pump``): lanes may run
    meanwhile (module docstring). None, at the cost of one check, while no worker chat has a lane, and when it has no
    work (its pump is then short)."""
    if not _lanes or threading.current_thread() is not threading.main_thread():
        return None
    try:
        if controller is not None and not controller.has_dispatchable_tools:
            return None
    except Exception:   # noqa: BLE001 - unreadable: a region, as the pump may then run anything.
        pass
    _away["depth"] += 1
    if _away["depth"] == 1:
        _away["since"], _away["woken"] = time.monotonic(), False
        _away["on"] = True
        with _clock:
            _clocks["armed"] = True
            if _clocks["thread"] is None:
                _clocks["thread"] = threading.Thread(target=_tick_clock, daemon=True, name="AngeloAwayClock")
                _clocks["thread"].start()
            _clock.notify_all()
    return _away["depth"]


def _tick_clock():
    """The one clock: wakes every lane once per region, ``AWAY_AFTER`` after it began, if it is still open; between
    regions it waits untimed. Ends once no lane is left (``close_all``)."""
    with _clock:
        while _lanes:
            due = _away["since"] + AWAY_AFTER - time.monotonic()
            if not _clocks["armed"] or (_away["on"] and due > 0):
                _clock.wait(due if _clocks["armed"] else None)
                continue
            _clocks["armed"] = False
            if _away["on"]:
                _away["woken"] = True
                for item in list(_lanes.values()):
                    item._wake()
        _clocks["thread"] = None


def back(region):
    """The region ``away`` began ends: the lanes are reclaimed, and what they left for the main thread runs here."""
    if region is None:
        return
    _away["depth"] -= 1
    if _away["depth"] > 0:
        return
    reclaim()
    for item in list(_lanes.values()):
        item._drain()


def reclaim():
    """Main thread: end the away region (if any) and wait until no lane runs a section it took itself: at most each
    lane's current one, to its next worker request or the end of its step. Off the main thread, nothing."""
    if not _away["on"] or threading.current_thread() is not threading.main_thread():
        return
    _away["on"] = _away["woken"] = False   # Before each lock: a lane checks and takes under its own.
    STATS["reclaims"] += 1
    for item in list(_lanes.values()):
        started = time.monotonic()
        with item._turn:
            while item._free:
                item._turn.wait()
        waited = (time.monotonic() - started) * 1000
        STATS["reclaim_ms_max"] = max(STATS["reclaim_ms_max"], waited)
        item._record_block(getattr(item.controller, "_request_metrics", None), started)
