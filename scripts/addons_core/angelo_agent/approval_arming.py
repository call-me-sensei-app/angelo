# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A control that grants takes only a click the person aimed at it (doc/angelo/agent_execution.rst, "A click the
person aimed").

Found by the 2.1.0 external-access review (2026-11-14) and ruled a 2.0.0 defect by the coordinator: a card that
appears under a resting pointer, or the click that brings Angelo's window forward, could land on Approve and start a
paid generation that spends credits. Every control that grants, on every surface, takes a press only once it is
*armed* (``armed``):

* ``SHIELD_SECONDS`` (``approval_wait``'s) have passed since it was drawn where it is now: a control that first
  appears, reappears or moves starts again (``drawn``);
* after that the pointer moved *into* it: from outside it to inside, so a pointer resting where the control appeared
  never counts (``moved``; a motionless refresh move is no move);
* the window's focus has not changed since: losing focus disarms everything in that window, and the first press in it
  afterwards (the click that brings it forward, wherever it lands) disarms everything again (``window_input``).

The keyboard grants only through a control the person moved focus to themselves, once its shield has passed
(``key_armed``); no keyboard default grants. The grants are the chat card's Approve (bounded edits, paid generations
and a script's Review & approve), its Always allow scripts and Allow this file, and a card host card's ``grants``
(the preview review's confirmation and Accept). No ``bpy``: host-testable.
"""

import time

from .approval_wait import SHIELD_SECONDS

GRANTS = frozenset({"approve", "script_always_allow", "script_file_allow"})   # The chat's granting controls.
clock = time.monotonic   # Tests set a fake.
_epochs = {}       # Window key -> focus epoch, raised when the window loses focus or is brought forward.
_returning = set()   # Windows that lost focus and have taken no press since.
UNKNOWN = ("unknown",)   # Where the pointer was before a view saw it: a first move is never a move into a control.


def window_input(window, event_type, value):
    """Every event a handler of ``window`` sees, first thing: losing focus, and the first press after it, disarm
    every control in that window. True when this event is that first press."""
    if event_type == "WINDOW_DEACTIVATE":
        _epochs[window] = _epochs.get(window, 0) + 1
        _returning.add(window)
        return False
    if value == "PRESS" and window in _returning:
        _returning.discard(window)
        _epochs[window] = _epochs.get(window, 0) + 1
        return True
    return False


def _inside(rect, point):
    if point is None or point is UNKNOWN:
        return False
    x, y, w, h = rect
    return x <= point[0] < x + w and y <= point[1] < y + h


class Arming:
    """One view's granting controls: ``{key: [since, rect, entered]}`` and the pointer as the view last saw it."""

    def __init__(self, owner=None):   # ``owner``: the window's key (``as_pointer``), never a held window.
        self.owner, self.epoch, self.controls, self.pointer = owner, None, {}, UNKNOWN

    def _sync(self, now):
        epoch = _epochs.get(self.owner, 0)
        if epoch != self.epoch:
            self.epoch = epoch
            for control in self.controls.values():
                control[0], control[2] = now, False

    def drawn(self, shown, now=None):
        """``shown``: ``(key, rect)`` of every granting control drawn now. One not drawn now is forgotten (it starts
        again when it reappears); a new one, or one that moved, starts its shield now and needs a move into it."""
        now = clock() if now is None else now
        self._sync(now)
        current = {}
        for key, rect in shown:
            control = self.controls.get(key)
            current[key] = control if control is not None and control[1] == tuple(rect) else [now, tuple(rect), False]
        self.controls = current

    def moved(self, point, now=None):
        """The pointer at ``point`` (the view's coordinates; None: outside the view). A move from outside a control
        into it, once its shield has passed, arms it."""
        now = clock() if now is None else now
        self._sync(now)
        before, self.pointer = self.pointer, point
        if point is None or point == before or before is UNKNOWN:
            return
        for control in self.controls.values():
            if now - control[0] >= SHIELD_SECONDS and _inside(control[1], point) and not _inside(control[1], before):
                control[2] = True

    def armed(self, key, now=None):
        """Whether a press on ``key`` grants: its shield passed and the pointer moved into it since."""
        now = clock() if now is None else now
        self._sync(now)
        control = self.controls.get(key)
        return control is not None and control[2] and now - control[0] >= SHIELD_SECONDS

    def key_armed(self, key, now=None):
        """Whether a keyboard activation of ``key`` (focused by the person) grants: its shield passed."""
        now = clock() if now is None else now
        self._sync(now)
        control = self.controls.get(key)
        return control is not None and now - control[0] >= SHIELD_SECONDS


def view(state, owner=None):
    """The arming of the view whose state is ``state`` (a chat view's dict)."""
    arming = state.get("arming")
    if arming is None:
        arming = state["arming"] = Arming(owner)
    if owner is not None:
        arming.owner = owner
    return arming


def press_lands(state, hit):
    """Whether the chat's press on ``hit`` (``(rect, action, value)``) counts: always, unless it grants and is not
    armed (then it lands on nothing)."""
    return hit[1] not in GRANTS or view(state).armed((hit[1], hit[2]))


def key_lands(state, key):
    """Whether a keyboard activation of the focused ``key`` (``(action, value)``) counts."""
    return key[0] not in GRANTS or view(state).key_armed(key)


def left(states):
    """The pointer left the views whose states are ``states``: a return into a control is a move into it."""
    for state in states:
        if "arming" in state:
            state["arming"].moved(None)


def granting(hits):
    """``(key, rect)`` of the chat's granting controls among ``hits`` (``(rect, action, value)``)."""
    return [((action, value), rect) for rect, action, value in hits if action in GRANTS]


def forget():
    """Tests and file loads: no window's focus history carries over."""
    _epochs.clear()
    _returning.clear()
