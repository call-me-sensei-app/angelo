# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's waiting state while a turn plans: "carved from marble". No ``bpy``: host-testable.

While a turn has not run its first step, the Angelo raven sits in a block of marble that chips away
(``marble_carving``). Everything here is driven by the chat's **real** progress, read from the controller the
chat already draws (``progress``), never by a timer pretending to know:

* **Carving** starts with the turn. Chips fall on the steady schedule, slowing as the block thins.
* **A tap** (a bigger burst, dust, a small shake) on a real progress event: the plan call starting
  (``claude_stream.STATUS_PLAN``), more provisional plan rows (``plan_steps``), or a new line of the
  runtime's thinking summary in the live row's detail (``plan_progress.thinking_status``). Taps are at most
  one per ``TAP_GAP``.
* **The reveal** on the turn's first step: the first activity row after the turn's request, or the plan call's
  row once the runtime accepted it (a refused plan is no step). The rest of the stone falls, the raven looks up
  and blinks, and the hero shrinks from ``HERO`` to ``HERO_COMPACT`` pixels (``WAKE_SECONDS``).
* **Building** keeps the revealed raven: a blink every ``BLINK_PERIOD``, and on the live row a slight tilt.
* The wait **ends** with the turn (``Tracker``): nothing is kept, and no frame is asked for again.

``Wait.next_frame`` says when the next frame is needed: 0 while something moves, else the time to the next
scheduled chip or blink, or None when nothing will change. ``marble_host``'s timer redraws the chat region only
then, at most ``FPS`` times a second. Display only: nothing here is stored, saved or sent to a model, and it
reads the controller without changing it.
"""

import math
from collections import namedtuple

from .claude_stream import PLAN_TOOL, STATUS_PLAN, STATUS_THINKING
from .marble_carving import Block

HERO, HERO_COMPACT, ICON = 112.0, 56.0, 22.0  # Pixels at a UI scale of 1.
HERO_GRID, ICON_GRID, HERO_INSET = 8, 4, 5.0  # The inset is each chip's darker edge, in mark units.
FPS = 24  # Enough for chips falling a few pixels a frame; each frame is a window composite and swap for Blender.
FRAME = 1.0 / FPS
POLL = 0.1  # Seconds between looks for a new event while nothing moves.
MAX_SHARE = 0.02  # The most of the main thread the wait's own frames may take, by what one costs.
TAP_GAP = 0.5
LOOK_SECONDS, SHRINK_START, SHRINK_END, WAKE_SECONDS = 0.6, 0.9, 1.4, 1.4
FIRST_BLINK, BLINK_SECONDS, BLINK_PERIOD = 0.6, 0.14, 3.8
LOOK_UNITS = 27.0  # How far the eyes rise when the raven looks up (mark units).
ICON_TILT = 7.0  # Degrees, counter-clockwise: the live row's raven cocks its head while steps run.
CARVING, REVEALING, BUILDING = "carving", "revealing", "building"

# What the wait reads from a chat: busy, whether a plan call is being written, how many provisional plan rows the
# live row carries, the thinking line it shows, whether the turn has run a step, and the turn's request row itself
# (by identity: a row inserted before it moves its index, never the row).
Progress = namedtuple("Progress", "busy plan rows line steps request")
Pose = namedtuple("Pose", "eye_dx eye_dy eye_open tilt")
# One draw of one block: the raven's pose, the standing stone's shake and mesh (None once all fell), the chips in
# flight and the dust, all in mark units (marble_draw hands them to the GPU; the benchmark measures this).
Frame = namedtuple("Frame", "pose offset solid flying dust")
IDLE = Progress(False, False, 0, "", False, None)


def is_step(message):
    """Whether a transcript row is a step of the turn: any tool row, a plan call only once it was accepted."""
    return message.get("role") == "activity" and (message.get("tool") != PLAN_TOOL
                                                   or message.get("status") == "completed")


def progress(controller, steps=True):
    """The facts of ``controller``'s running turn the wait reacts to (``Progress``).

    The request row is the controller's own (``Controller.send`` stores its index once the row is in the
    transcript; reply_overlay reads the same private state). Only rows after it count, so a step of an earlier
    turn is never this turn's. ``steps=False`` skips that scan once the raven is revealed.
    """
    from .plan_progress import split
    from .plan_steps import rows
    status = str(controller.status)
    base, detail = split(status)
    planned, more = rows(status)
    messages = controller.state["messages"]
    anchor = controller.__dict__.get("_request_message_index")
    if not (type(anchor) is int and 0 <= anchor < len(messages) and messages[anchor].get("role") == "user"):
        anchor = None
    stepped = steps and anchor is not None and any(is_step(message) for message in messages[anchor + 1:])
    return Progress(bool(controller.busy), base == STATUS_PLAN, len(planned) + more,
                    detail if base == STATUS_THINKING else "", stepped, None if anchor is None else messages[anchor])


def _smooth(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3 - 2 * value)


def hero_block():
    """A new hero block: an 8 x 8 grid with edges and veins (the chips are built once, ``marble_carving.chips``)."""
    return Block(HERO_GRID, HERO_GRID, inset=HERO_INSET, veins=True, dust=True)


def icon_block():
    return Block(ICON_GRID, ICON_GRID)


def prepare():
    """Build the cached chip geometry ahead of the first wait (``marble_host`` calls it outside drawing)."""
    hero_block()
    icon_block()


class Wait:
    """One turn's wait, from its start (``now``, ``time.monotonic`` seconds) until the turn ends."""

    def __init__(self, now, seen=IDLE, reduced=False):
        self.started, self.reduced, self.seen = now, bool(reduced), seen
        self.request = seen.request
        self.hero, self.icon = hero_block(), icon_block()
        self.phase, self.revealed_at, self.last_tap, self.taps = CARVING, None, -1e9, 0
        if seen.steps:  # Opened mid-turn, past its first step: already revealed, with no animation to replay.
            self.phase, self.revealed_at = BUILDING, now - WAKE_SECONDS
            self.hero.clear()
            self.icon.clear()

    def observe(self, now, seen):
        """Apply what changed since the last observation; returns the events it caused ("tap", "reveal")."""
        events, previous = [], self.seen
        if self.request is None:
            self.request = seen.request
        if self.phase == CARVING:
            if seen.steps:
                self.reveal(now)
                events.append("reveal")
            elif ((seen.plan and not previous.plan) or seen.rows > previous.rows
                  or (seen.line and seen.line != previous.line)) and self.tap(now):
                events.append("tap")
        self.seen = seen
        return events

    def tap(self, now):
        """A chisel tap on both blocks; False when it came within ``TAP_GAP`` of the last one."""
        if now - self.last_tap < TAP_GAP:
            return False
        self.last_tap, self.taps = now, self.taps + 1
        self.hero.tap(now)
        self.icon.tap(now)
        return True

    def reveal(self, now):
        self.phase, self.revealed_at = REVEALING, now
        self.hero.fall_all(now)
        self.icon.fall_all(now)

    def advance(self, now):
        """The steady carving owed by ``now`` (each chip at its scheduled time), and the end of the wake."""
        if self.phase == CARVING:
            self.hero.carve(now - self.started, self.started)
            self.icon.carve(now - self.started, self.started)
        elif self.phase == REVEALING and now - self.revealed_at >= WAKE_SECONDS:
            self.phase = BUILDING
        self.hero.prune(now)
        self.icon.prune(now)

    def wake(self, now):
        return None if self.revealed_at is None else now - self.revealed_at

    def hero_size(self, now):
        """The hero's side in pixels: ``HERO`` until the reveal, then shrinking to ``HERO_COMPACT``."""
        wake = self.wake(now)
        if wake is None:
            return HERO
        share = (wake - SHRINK_START) / (SHRINK_END - SHRINK_START)
        share = (1.0 if share >= 0 else 0.0) if self.reduced else _smooth(share)
        return HERO + (HERO_COMPACT - HERO) * share

    def blink(self, wake):
        """How open the eyes are (1 open, 0.1 shut): a blink as the raven wakes, then every ``BLINK_PERIOD``."""
        if self.reduced or wake < FIRST_BLINK:
            return 1.0
        phase = (wake - FIRST_BLINK) % BLINK_PERIOD
        half = BLINK_SECONDS / 2
        return 0.1 + 0.9 * abs(phase - half) / half if phase < BLINK_SECONDS else 1.0

    def pose(self, now, small):
        """Where the eyes look, how open they are and the head's tilt, at ``now``."""
        wake = self.wake(now)
        if wake is None:
            return Pose(0.0, 0.0, 1.0, 0.0)
        if self.reduced:  # Still poses only: no look-up, wobble or blink in motion.
            return Pose(0.0, LOOK_UNITS, 1.0, ICON_TILT if small else 0.0)
        tilt = -4.0 * math.sin(min(wake, 1.2) * 2.6)
        if small:
            tilt += ICON_TILT * _smooth(wake / 0.8)
        return Pose(0.0, LOOK_UNITS * min(1.0, wake / LOOK_SECONDS), self.blink(wake), tilt)

    def next_frame(self, now):
        """Seconds until the next frame is needed: 0 now, a delay, or None when nothing will change."""
        if self.hero.moving(now, self.reduced) or self.icon.moving(now, self.reduced):
            return 0.0
        if self.phase == CARVING:
            due = [t for t in (self.hero.next_drop(self.started), self.icon.next_drop(self.started)) if t is not None]
            return max(0.0, min(due) - now) if due else None
        wake = self.wake(now)
        if self.reduced:  # The one still change left: the hero's size steps down once.
            return None if wake >= SHRINK_START + FRAME else max(0.0, SHRINK_START - wake)
        if self.phase == REVEALING:
            return 0.0
        phase = (wake - FIRST_BLINK) % BLINK_PERIOD
        return 0.0 if phase < BLINK_SECONDS + FRAME else BLINK_PERIOD - phase


def frame_interval(frame_seconds):
    """Seconds between the wait's frames: ``FRAME`` (24 a second), longer when one frame costs enough that 24
    would take more than ``MAX_SHARE`` of the main thread (a slow machine), at least one a second."""
    return max(FRAME, min(1.0, frame_seconds / MAX_SHARE))


def frame(wait, block, now, small):
    """Everything one draw of ``block`` (``wait.hero`` or ``wait.icon``) needs at ``now``."""
    return Frame(wait.pose(now, small), block.offset(now, wait.reduced),
                 block.solid() if block.removed < block.total else None,
                 block.flying(now, wait.reduced), block.dust_mesh(now, wait.reduced))


class Tracker:
    """The running wait of each chat, by conversation id; a wait ends when its chat's turn does."""

    def __init__(self):
        self.waits = {}

    def current(self, controller):
        """The wait drawn for ``controller``, or None (no turn running, or not seen by the timer yet)."""
        wait = self.waits.get(controller.state.get("id"))
        return wait if wait is not None and controller.busy and not controller.voice else None

    def update(self, controller, now, reduced=lambda: False):
        """Observe ``controller`` at ``now``: start, feed, advance or end its wait; returns the wait or None.

        ``reduced`` is asked once, when a wait starts. A new request row under a busy chat starts a new wait.
        """
        key = controller.state.get("id")
        wait = self.waits.get(key)
        if not controller.busy or controller.voice:
            self.waits.pop(key, None)
            return None
        seen = progress(controller, steps=wait is None or wait.phase == CARVING)
        if wait is None or (wait.request is not None and seen.request is not None
                            and seen.request is not wait.request):
            wait = self.waits[key] = Wait(now, seen if wait is None else progress(controller), reduced())
        else:
            wait.observe(now, seen)
        wait.advance(now)
        return wait

    def end(self, keys):
        """Forget the waits of chats no longer open (``keys``: the ids still open)."""
        for key in set(self.waits) - set(keys):
            del self.waits[key]
