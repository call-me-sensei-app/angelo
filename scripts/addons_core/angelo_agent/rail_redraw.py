# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When the rail and the live view are drawn again: as soon as a committed fact changes, the clock at its own pace.

The rail (``session_rail_draw``) and the live view (``live_view_draw``) draw in the 3D view's own POST_PIXEL, so
Blender draws them only when that region redraws. Nothing about a worker redraws it by itself: a part read again, a
tile picture, a progress report, a tile's state, the turn's clock. Break-it P1-8 P1: while the lead waited for its
workers the rail froze until a hover or an orbit. So the rail's draw records what it showed (``shown``, ``signature``)
and the rail's timers call ``tick``, which tags the window's rail view when what it would show now differs. Nothing is
tagged while it is already up to date.

Each tag redraws the artist's document too: the rail has no region of its own. So a tag redraws only the view's main
region (never its headers, toolbar or sidebar), and the signature has two parts, paced apart (break-it P1-11 S1):

* **Committed facts** (the version: a part read again, a picture; each tile's state, the step its worker is on, its
  counts, name and role, which tile is active): at most ``RATE`` redraws a second, spaced by ``SHARE`` times the view's
  recent redraw cost, but never more than ``FACT_WAIT`` apart. A step is seen after its part's read
  (``live_schedule``: at most 0.7 s), so commit to screen is about half a second on a cheap view and at most about
  1.5 s on a heavy one under the artist's continuous input. Facts change when a worker commits, reports, asks or
  ends, when the artist switches, and with the artist's own work: the version also moves on each depsgraph update of
  any scene and each new picture of the main tile (at most once a second); in the rail's own window that redraw is
  mostly the one the edit makes anyway. So on a view slower than ``FACT_WAIT / SHARE`` (37.5 ms) the rail may take
  more than 5% of the main thread then: one redraw per fact, at most every ``FACT_WAIT``.
* **Everything streamed** (a working tile's clock, and the live label a model's output feeds: its phase, a thinking
  title, a plan's count as it is written, a retry countdown; break-it P1-12 T1): no sooner than ``CLOCK_EVERY`` and
  ``SHARE`` times the view's recent redraw cost, with no cap, so it never takes more than 5% of the main thread,
  however heavy the view and however many sessions work (on a heavy view the clock moves every few seconds).

A change that must wait is looked at again the moment it may be drawn (``_recheck``: re-armed while anything waits, and
moved earlier when a fact comes due before a streamed change, P1-12 T2; whether it is armed is asked of Blender, which
drops it on a file load without running it, P1-13 U3). The redraw cost is the lower median of the view's last
``SAMPLES`` (one slow frame does not hold the rail back, P1-10 R3); each sample is the larger of the view's
PRE_VIEW-through-rail time (its drawing, overlays, any live view and the rail paint) and, for a frame the rail asked for, its tag-to-rail
time, which also counts the draw manager's sync before PRE_VIEW and the window's regions drawn before the rail's in that
frame, in a quad view the first of the four (P1-11 S2; capped at ``MAX_SAMPLE``: a window that is not drawn for a while,
minimized or covered, is not a slow view). GPU time is not counted. Like reads and pictures, tags wait while a tool, a
navigation or a job holds the window (the caller checks ``parallel_sessions._held_now``). ``tags`` keeps when each was
made.
"""

import math
import time

RATE = 4.0         # Committed facts: at most this many redraws a second per window (a document redraw each),
FACT_WAIT = 0.75   # and a fact never waits longer than this for its redraw.
SHARE = 20.0       # Otherwise no sooner than this many times the view's recent redraw cost (about 5% of the thread).
CLOCK_EVERY = 1.0  # Streamed text alone: no more often than this, and no sooner than SHARE x the cost, uncapped.
SAMPLES = 5        # Redraws in the recent cost (their median).
MAX_SAMPLE = 0.5   # Seconds: a longer tag-to-rail wait is a window not drawn meanwhile, not a slow view.

shown = {}     # window pointer -> ``signature`` (facts, everything) of what its rail last drew.
drawn_at = {}  # window pointer -> time.monotonic() of that draw (fixtures time the save-to-screen path).
view_cost = {}   # window pointer -> its rail view's last SAMPLES redraw costs, seconds.
_view_started = {}   # area pointer -> time.perf_counter() at its PRE_VIEW.
_tag_started = {}    # window pointer -> time.perf_counter() of the tag its next rail draw answers.
_tagged = {}   # window pointer -> time.monotonic() of the last tag this module made there.
_soonest = [None]   # Seconds until the next waiting change may be drawn, from the last tick (None: nothing waits).
_armed_at = [None]  # time.monotonic() at which ``_recheck`` will run (None: not armed).
tags = []      # Fixtures: (time.monotonic(), window pointer) of the newest 512 tags.
requested = [0]   # Every redraw this module requested (a tag of one window's main region), never capped.
_error = [None]


def signature(version, tiles, minimized=False, lead=None):
    """``(facts, everything)`` a rail shows, comparable: the facts are committed state only, the version (reads,
    pictures) and each tile's state, current step, counts, name, role and whether it is active; everything adds each
    tile's whole status line: its clock and any live label a model's output streams. ``minimized``: only the round
    button shows (``rail_minimized``), so its facts are the workers' count and whether one waits for approval (``lead``
    the main session, ``rail_minimized.counts``) and nothing streams: a hidden tile's clock, picture or step never
    redraws the view (the maintainer's audit, 2026-10-25 17:00, finding 9) (pure)."""
    if minimized:
        from .rail_minimized import counts
        return ("minimized",) + counts(tiles, lead), ()
    facts = (version, tuple((tile.session, tile.title, tile.role, tile.kind, tile.active, tile.progress, tile.step)
                            for tile in tiles))
    return facts, tuple(tile.status for tile in tiles)


def current(version, tiles, window_pointer=None):
    """``signature`` of what this window shows: the panel, or its button when minimized or cramped."""
    from . import parallel_sessions
    return signature(version, tiles, minimized(window_pointer), getattr(parallel_sessions, "lead", None))


def minimized(window_pointer=None):
    """Whether tiles are hidden by preference, or in the named window by its cramped layout."""
    try:
        from . import rail_minimized
        return rail_minimized.shown(window_pointer) if window_pointer is not None else rail_minimized.minimized()
    except ImportError:
        return False


def panels_visible():
    """Shared tile pictures are needed while at least one live target window has a panel, even if others do not."""
    import bpy
    from . import live_view_draw, live_windows
    return any(live_view_draw.target(window) is not None and not minimized(window.as_pointer())
               for window in live_windows.windows(bpy.context.window_manager))


def spacing(cost, clock_only=False, rate=RATE):
    """Seconds between two of the rail's redraws of a view whose recent redraws cost ``cost`` seconds (pure)."""
    if clock_only:
        return max(CLOCK_EVERY, SHARE * cost)
    return min(FACT_WAIT, max(1.0 / rate, SHARE * cost))


def wait(drawn, current, last_tag, now, cost=0.0, rate=RATE):
    """Seconds until a window whose rail last drew ``drawn`` may be drawn again for ``current``: 0 now, None when it
    is up to date (pure: host-tested)."""
    if drawn == current:
        return None
    clock_only = drawn is not None and drawn[0] == current[0]
    return max(0.0, spacing(cost, clock_only, rate) - (now - last_tag))


def due(drawn, current, last_tag, now, rate=RATE, cost=0.0):
    """Whether that window must be drawn again now (pure)."""
    return wait(drawn, current, last_tag, now, cost, rate) == 0.0


def recent_cost(samples):
    """The lower median of recent redraw costs (seconds), 0 before any: with two samples the smaller, so one slow
    frame (a shader compile, a hitch) never paces the rail on its own."""
    ordered = sorted(samples or ())
    return ordered[(len(ordered) - 1) // 2] if ordered else 0.0


def rearm(armed_at, due_at):
    """Whether the re-check must be (re)scheduled for ``due_at``: none is armed, or the armed one runs later (pure)."""
    return armed_at is None or due_at < armed_at - 0.01


def pre_view():
    """PRE_VIEW of every 3D view: when its draw began, so the rail knows what a redraw of it costs."""
    import bpy
    area = bpy.context.area
    if area is not None:
        _view_started[area.as_pointer()] = time.perf_counter()


def note_drawn(window_pointer, version, tiles, area_pointer=None):
    """The rail's draw: what it showed in this window, and what that view's redraw cost up to here."""
    shown[window_pointer] = current(version, tiles, window_pointer)
    drawn_at[window_pointer] = time.monotonic()
    now = time.perf_counter()
    began, tagged = _view_started.pop(area_pointer, None), _tag_started.pop(window_pointer, None)
    if began is None and tagged is None:
        return
    samples = view_cost.setdefault(window_pointer, [])
    samples.append(max(now - began if began is not None else 0.0,
                       min(MAX_SAMPLE, now - tagged) if tagged is not None else 0.0))
    del samples[:-SAMPLES]


def tick(now=None):
    """Tag each window's rail view whose picture is out of date; returns how many were tagged. Main thread."""
    import bpy
    from . import live_view_draw, live_windows, parallel_sessions, session_rail_draw
    _soonest[0] = None
    if not parallel_sessions.sessions():
        return 0
    now = time.monotonic() if now is None else now
    try:
        tiles = session_rail_draw.states(now)
    except Exception as error:   # noqa: BLE001 - the draw says the same once; a timer that raises is dropped.
        if _error[0] != repr(error):
            _error[0] = repr(error)
            print("Angelo: session rail redraw:", error)
        return 0
    count = 0
    for window in live_windows.windows(bpy.context.window_manager):
        area = live_view_draw.target(window)
        pointer = window.as_pointer()
        now_shows = current(parallel_sessions.version, tiles, pointer)
        left = wait(shown.get(pointer), now_shows, _tagged.get(pointer, -math.inf), now,
                    recent_cost(view_cost.get(pointer))) if area is not None else None
        if left is None:
            continue
        if left > 0:   # Changed, but drawn too recently: again the moment it may be.
            _soonest[0] = left if _soonest[0] is None else min(_soonest[0], left)
            continue
        for region in area.regions:   # Its main regions only (a quad view's four, live views included).
            if region.type == 'WINDOW':
                region.tag_redraw()
        _tagged[pointer], _tag_started[pointer] = now, time.perf_counter()
        tags.append((now, pointer))
        del tags[:-512]
        requested[0] += 1
        count += 1
    # Armed only while Blender still holds it: a file load drops it without running it (break-it P1-13 U3).
    armed = _armed_at[0] if bpy.app.timers.is_registered(_recheck) else None
    if _soonest[0] is not None and not _in_recheck[0] and rearm(armed, now + _soonest[0]):
        if armed is not None:
            bpy.app.timers.unregister(_recheck)   # A fact due before the streamed change it waited for (P1-12 T2).
        bpy.app.timers.register(_recheck, first_interval=_soonest[0])
        _armed_at[0] = now + _soonest[0]
    return count


_in_recheck = [False]   # ``_recheck`` is running: it re-arms itself by its return value.


def _recheck():
    """A waiting change, the moment its spacing allows (never while a tool, a navigation or a job holds the window);
    again while anything still waits (a timer cannot register itself while it runs, so it returns its next wait)."""
    from . import parallel_sessions
    started = time.monotonic()   # Blender times the next run from its timer pass, not from this call's end.
    _soonest[0], _armed_at[0], _in_recheck[0] = None, None, True
    try:
        if parallel_sessions.sessions() and not parallel_sessions._held_now():
            tick()
    except Exception as error:   # noqa: BLE001 - a timer that raises is dropped; the rail's ticks go on.
        print("Angelo: session rail redraw:", error)
        return None
    finally:
        _in_recheck[0] = False
    if _soonest[0] is not None:
        _armed_at[0] = started + _soonest[0]
    return _soonest[0]


def release():
    import bpy
    if bpy.app.timers.is_registered(_recheck):
        bpy.app.timers.unregister(_recheck)
    shown.clear()
    drawn_at.clear()
    view_cost.clear()
    _view_started.clear()
    _tag_started.clear()
    _tagged.clear()
    tags.clear()
    requested[0] = 0
    _soonest[0] = _armed_at[0] = None
    _error[0] = None
