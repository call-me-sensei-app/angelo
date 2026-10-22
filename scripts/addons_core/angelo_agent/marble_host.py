# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's marble wait in Blender: its redraw timer, the running waits and the reduced-motion choice.

``marble_wait`` decides what the wait shows and when the next frame is needed; this module only asks it on a
timer and redraws **the chat's region and nothing else**, at most ``marble_wait.FPS`` times a second, and only
when a frame is needed. The timer exists only while the drawn chat runs a turn:

* the chat's existing poll (``chat_view.animate_popovers``, every 0.1 s) calls ``poll`` and starts it when the
  chat is busy; no new idle timer;
* each tick reads the chat's controller (``marble_wait.progress``: its status and rows, nothing written),
  applies the events, and returns ``FRAME`` while something moves, else the time to the next scheduled change
  capped at ``POLL`` (so a new event is seen within 0.1 s); a frame redraws only the marks over the chat's kept
  picture (``marble_frames``), and frames are paced so they never take more than ``marble_wait.MAX_SHARE`` (2%)
  of the main thread by their measured cost (``frame_interval``);
* it **pauses** (asks for no frame, keeps polling) while the person interacts or cannot see it (``paused``): a
  modal operator other than Angelo's own canvases (``input_policy``), view navigation in the last
  ``NAVIGATION_PAUSE`` seconds (trackpad, wheel, NDOF), playback, the window out of focus, or no mark in view;
* the tick that finds the turn over forgets the wait, frees the kept picture and returns None: the timer stops at
  once, and the chat draws no hero and no marble from then on.

It never runs in the agent's request path: the provider threads, the controller's pump and native dispatch are
untouched, and a failing tick only stops the animation (``last_error``). The cached geometry is built on the
first tick of the first wait (``marble_wait.prepare``, about 13 ms once per process), never in a draw callback;
until then the live row keeps its spinner.
"""

import time

import bpy

from . import marble_wait, motion_preference

NAVIGATION_PAUSE = 0.4  # Seconds after a navigation event the wait still pauses.
NAVIGATION = frozenset({"TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM", "MOUSEPAN", "WHEELUPMOUSE",
                        "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE"})
tracker = marble_wait.Tracker()
enabled = True  # Off: no wait is shown and the live row keeps its spinner (the GUI suite's comparison runs).
ready = False  # The cached geometry exists: drawing may use it.
_focused = True  # False from the window losing focus until its next event.
_navigated = -1e9  # When the last navigation event arrived (time.monotonic).
pauses = 0  # Ticks that asked for a frame but paused, and the last reason (GUI fixtures read them).
last_pause = None
frames = 0  # Redraws this timer asked for (GUI fixtures read it).
_last_frame = -1e9  # When it last asked for one (time.monotonic).
tick_seconds = 0.0  # Main-thread time spent in ticks (GUI fixtures read it).
last_error = None
# The live row's status line (live_row.status_line) rides on this timer: a frame each new second of the turn, and
# the dot's breathing at PULSE_FPS while nothing pauses the wait (Reduce Motion keeps the seconds only). The one pause
# a second still passes is the window being out of focus: someone watching from another app sees the time move. A
# modal tool, view navigation, playback or no mark in view pause it like the raven, so an interaction or playback
# never pays for the clock.
PULSE_FPS = 8
TICK, PULSE = "tick", "pulse"
TICK_THROUGH = frozenset({"unfocused"})  # The only pause a once-a-second tick still passes.
marble_frames_asked = 0


def clock_frame(started, reduced, now, last_frame):
    """``(kind, delay)`` for the clock chip: TICK when a new second began since ``last_frame``, PULSE when the dot
    is due a frame, else None; ``delay`` is the seconds until the next of them (None without a clock)."""
    from .live_progress import next_tick
    if started is None:
        return None, None
    if now >= started and int(now - started) != int(max(last_frame, started) - started):
        return TICK, 0.0
    pulse = None if reduced else max(0.0, 1.0 / PULSE_FPS - (now - last_frame))
    if pulse == 0.0:
        return PULSE, 0.0
    tick = next_tick(started, now)
    return None, tick if pulse is None else min(tick, pulse)


def reduced():
    """Blender's Reduce Motion preference, or the system's (macOS Reduce motion, Windows animations off)."""
    if getattr(bpy.context.preferences.view, "use_reduce_motion", False):
        return True
    return motion_preference.os_reduces_motion()


def current(controller):
    """The running wait to draw for ``controller``, or None."""
    return tracker.current(controller) if ready and enabled else None


def note_event(event_type, value):
    """Every event the chat's window handler sees (``chat_view``): focus and view navigation, for ``paused``."""
    global _focused, _navigated
    if event_type == 'WINDOW_DEACTIVATE':
        _focused = False
        return
    if event_type.startswith("TIMER") or event_type == "NONE":
        return
    _focused = True
    if event_type in NAVIGATION or event_type.startswith("NDOF_"):
        _navigated = time.monotonic()


def paused(now):
    """Why the wait asks for no frame now (a short reason), or None: the person interacts or cannot see it."""
    from . import input_policy, marble_frames
    if not _focused:
        return "unfocused"
    if now - _navigated < NAVIGATION_PAUSE:
        return "navigating"
    for window in bpy.context.window_manager.windows:
        if input_policy.tool_running(window):
            return "modal"
        if window.screen.is_animation_playing:
            return "playback"
    return None if marble_frames.visible() else "hidden"


def poll():
    """Start the timer when the drawn chat runs a turn; cheap, so the chat's poll can call it.

    After a failure (``last_error``) the animation stays off for the session: the live row keeps its spinner.
    """
    from . import ui
    controller = ui._controller  # The chat the canvas draws (ui.get_controller), never a new one.
    if (enabled and last_error is None and controller is not None and controller.busy and not controller.voice
            and not bpy.app.timers.is_registered(_tick)):
        bpy.app.timers.register(_tick, first_interval=0.0)


def _redraw_chats(frame=True):
    """Redraw the chats' regions; ``frame``: a marble frame (``marble_frames.reuse``), not the turn's end."""
    global frames, _last_frame
    from . import console, marble_frames
    frames += frame
    _last_frame = time.monotonic()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if console.is_chat(area):
                if frame:
                    marble_frames.request(area)
                for region in area.regions:
                    if region.type == 'WINDOW':
                        region.tag_redraw()


def _tick():
    global tick_seconds
    started = time.perf_counter()
    try:
        return _step()
    finally:
        tick_seconds += time.perf_counter() - started


def _step():
    global ready, last_error, pauses, last_pause, marble_frames_asked
    try:
        from . import chat_view, marble_frames, ui
        controller = ui._controller  # The chat the canvas draws (ui.get_controller), never a new one.
        if not chat_view._enabled or not enabled or controller is None:
            tracker.waits.clear()
            marble_frames.release()
            return None
        if not ready:
            marble_wait.prepare()
            ready = True
        now = time.monotonic()
        tracker.end([controller.state.get("id")])  # Only the drawn chat has a wait.
        wait = tracker.update(controller, now, reduced)
        if wait is None:
            marble_frames.release()
            _redraw_chats(frame=False)  # The hero leaves with the turn.
            return None
        delay = wait.next_frame(now)
        from .live_progress import started as turn_started
        clock, clock_delay = clock_frame(turn_started(controller), wait.reduced, now, _last_frame)
        interval = marble_wait.frame_interval(marble_frames.frame_cost)
        if (delay is not None and delay <= 0.0) or clock:
            reason = paused(now)
            if reason and not (clock == TICK and reason in TICK_THROUGH):
                pauses, last_pause = pauses + 1, reason
                return marble_wait.POLL
            if interval > marble_wait.FRAME and now - _last_frame < interval:
                # A costly frame: wait out its share before the next one, still looking for events.
                return min(marble_wait.POLL, max(0.01, interval - (now - _last_frame)))
            if delay is not None and delay <= 0.0:
                marble_frames_asked += 1  # The raven's own frames, apart from the status line's (fixtures).
            _redraw_chats()
            return min(interval, marble_wait.POLL)
        due = [value for value in (delay, clock_delay) if value is not None]
        return marble_wait.POLL if not due else max(marble_wait.FRAME, min(marble_wait.POLL, *due))
    except Exception as error:  # The animation is decoration: it stops, the chat keeps working.
        last_error = "tick: %s" % error
        tracker.waits.clear()
        return None


def stop():
    """Unregister: no timer, no waits, no cached GPU batches."""
    from . import marble_draw
    from . import marble_frames
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    tracker.waits.clear()
    marble_frames.release(now=True)
    marble_draw.clear()
