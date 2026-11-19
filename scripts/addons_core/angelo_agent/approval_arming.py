# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A control that grants takes only a click the person aimed at it (doc/angelo/agent_execution.rst, "A click the
person aimed").

Found by the 2.1.0 external-access review (2026-11-14) and ruled a 2.0.0 defect by the coordinator: a card that
appears under a resting pointer, or the click that brings Angelo's window forward, could land on Approve and start a
paid generation that spends credits. Every control that grants, on every surface, takes a press only once it is
*armed* (``armed``):

* ``SHIELD_SECONDS`` (``approval_wait``'s) have passed since it was first drawn: a control that first appears or
  reappears starts again (``drawn``). The same control laid out again somewhere else while it stays drawn (rows
  streaming in around its card, a scroll that keeps it in view) keeps its shield: it was in view all along;
* the person aimed at it: the pointer moved *into* it (from outside it to inside, at any time since it was drawn).
  A pointer resting where the control appeared never counts, nor do presses there, however far apart (``moved``; a
  motionless refresh move is no move; the break-it review of ca6f5ff137d, S1), and an aim is kept across a re-layout
  only while the pointer is still on the control where it is now;
* the window's focus has not changed since: losing focus, and coming forward again, restart every shield in that
  window and drop every aim. Only the press that arrives with the window's activation (the click that brings it
  forward, wherever it lands) is that click: it never grants, and it aims at the control under it, so the next
  deliberate press, once the shield has passed, does. An activation with no press (Cmd-Tab, the Dock, another app)
  leaves none: the person's next press is an ordinary one. C keeps this record from the window manager's own notes,
  and every surface reads it (``window_state``; the re-review of acd0d465ee9, SHOULD-1 and SHOULD-2).

2.0.1 (maintainer, 2026-11-18: "I have to double click approve", then 5 and 6-8 clicks in an External chat while the
agent worked): a card re-laid out on every streamed row restarted its shield and dropped its aim, so presses kept
landing on nothing, more of them the busier the chat. The rules above keep the 2.0.0 safety intent (no scripted or
automatic press, no press on a control the person could not have seen for ``SHIELD_SECONDS``, no press from a card
that appeared under a resting pointer) while one deliberate click on a visible control grants.

The keyboard grants only through a control the person moved focus to themselves, once its shield has passed
(``key_armed``); no keyboard default grants. The grants are the chat card's Approve (bounded edits, paid generations
and a script's Review & approve), its Always allow scripts and Allow this file, and a card host card's ``grants``
(the preview review's confirmation and Accept). No ``bpy``: host-testable.
"""

import time

from .approval_wait import SHIELD_SECONDS

GRANTS = frozenset({"approve", "script_session_allow", "script_always_allow"})   # The chat's granting controls.
clock = time.monotonic   # Tests set a fake.
_restarts = [0]    # Raised by ``restart``: every control in every window starts its shield again.
UNKNOWN = ("unknown",)   # Where the pointer was before a view saw it: a first move is never a move into a control.


def window_state(window):
    """``(focus epoch, returning, the last press brought the window forward)`` of ``window`` as C holds them
    (``GUARD.window_state``, from the window manager's own notes: focus lost, activation, moves, keys, presses).
    The one record every surface reads, so two modal handlers of one press agree (the re-review of acd0d465ee9,
    SHOULD-2); only a press that arrived with the activation brought the window forward (SHOULD-1).
    ``(0, False, False)`` with no guard, no window, or while an agent script runs."""
    from . import grant_authority
    reader = getattr(grant_authority.GUARD, "window_state", None) if grant_authority.available() else None
    state = reader(window) if reader is not None and window is not None else None
    return tuple(state) if state else (0, False, False)


def _inside(rect, point):
    if point is None or point is UNKNOWN:
        return False
    x, y, w, h = rect
    return x <= point[0] < x + w and y <= point[1] < y + h


class Arming:
    """One view's granting controls: ``{key: [since, rect, aimed, pressed_at]}`` and the pointer as the view last saw
    it. ``pressed_at``: when a press (not a move) aimed at it, else None."""

    def __init__(self, owner=None):   # ``owner``: the window's key (``as_pointer``), never a held window.
        self.owner, self.epoch, self.controls, self.pointer = owner, None, {}, UNKNOWN
        self.held = None   # ``[key, stayed]`` while a granting press is down: whether the pointer stayed on it.

    def _sync(self, now):
        epoch = (window_state(self.owner)[0], _restarts[0])
        if epoch != self.epoch:
            self.epoch = epoch
            for control in self.controls.values():   # Focus changed: its shield starts again and its aim goes.
                control[0], control[2], control[3] = now, False, None
            self.pointer = UNKNOWN   # And where the pointer was: the next move (an activation's own) moves into nothing.

    def drawn(self, shown, now=None):
        """``shown``: ``(key, rect)`` of every granting control drawn now. One not drawn now is forgotten (it starts
        again when it reappears); a new one starts its shield now and needs an aim. One laid out again elsewhere
        keeps its shield, and keeps its aim only while the pointer was on it before and is on it now; one that
        arrives under a pointer that was not on it is a new control (break-it review M1, 2.0.1)."""
        now = clock() if now is None else now
        self._sync(now)
        current = {}
        for key, rect in shown:
            rect = tuple(rect)
            control = self.controls.get(key)
            if control is None:
                current[key] = [now, rect, False, None]
                continue
            if control[1] != rect:
                before_on, after_on = _inside(control[1], self.pointer), _inside(rect, self.pointer)
                if after_on and not before_on:
                    control = [now, rect, False, None]   # Arrived under the pointer: as newly shown.
                else:
                    control = [control[0], rect, control[2] and before_on and after_on, control[3]]
                if self.held is not None and self.held[0] == key and not after_on:
                    self.held[1] = False   # Moved from under the held press: a release grants nothing.
            current[key] = control
        if self.held is not None and self.held[0] not in current:
            self.held[1] = False   # Not drawn this frame: its release grants nothing.
        self.controls = current

    def moved(self, point, now=None, real=True):
        """The pointer at ``point`` (the view's coordinates; None: outside the view). A move from outside a control
        into it aims at it, during its shield too (the press still waits for the shield); a real move inside it once
        its shield has passed aims too, a hand's jitter included (the re-review of acd0d465ee9, SHOULD-3: a refused
        click recovers without leaving the control); leaving it drops the aim. ``real`` False: a move without motion
        (Blender's refresh, a window's activation): where the pointer is, never a move into anything."""
        now = clock() if now is None else now
        self._sync(now)
        before, self.pointer = self.pointer, point
        if point is None or point == before or before is UNKNOWN or not real:
            for key, control in self.controls.items():
                if point is None:
                    control[2] = False
                    if self.held is not None and self.held[0] == key:
                        self.held[1] = False
            return
        for key, control in self.controls.items():
            inside_now, inside_before = _inside(control[1], point), _inside(control[1], before)
            if inside_now and not inside_before:
                control[2], control[3] = True, None
            elif inside_now and now - control[0] >= SHIELD_SECONDS:
                control[2], control[3] = True, None   # A pointer that never moved still never aims (S1).
            elif inside_before and not inside_now:
                control[2] = False
            if self.held is not None and self.held[0] == key and not inside_now:
                self.held[1] = False   # Dragged off: the release cancels, as a desktop button's does.

    def pressed(self, key, activating=False, now=None):
        """A press on ``key`` that did not grant (not armed): it aims at it only when it was the press that brought the
        window forward (it never grants itself), which lets a press grant ``SHIELD_SECONDS`` (the longest
        double-click) later. Any other press never aims: presses alone, however far apart, never grant a control
        that appeared under a resting pointer (the break-it review of ca6f5ff137d, S1); the person moves onto it."""
        now = clock() if now is None else now
        self._sync(now)
        control = self.controls.get(key)
        if control is not None and activating:
            control[2], control[3] = True, now

    def armed(self, key, now=None):
        """Whether a press on ``key`` grants: its shield passed and the person aimed at it (``moved``, or the press
        that brought the window forward at least ``SHIELD_SECONDS`` ago, ``pressed``)."""
        now = clock() if now is None else now
        self._sync(now)
        control = self.controls.get(key)
        return (control is not None and control[2] and now - control[0] >= SHIELD_SECONDS
                and (control[3] is None or now - control[3] >= SHIELD_SECONDS))

    def hold(self, key):
        """A granting press landed on ``key``: its release grants only if the pointer stays on it (``stayed``)."""
        self.held = [key, True]

    def stayed(self, key):
        """For the release of a press on ``key``: True when it was a granting press and the pointer stayed on the
        control (where it is drawn), False when it left; None for any other control (``release_active``'s rule)."""
        held, self.held = self.held, None
        if key[0] not in GRANTS:
            return None
        return bool(held is not None and held[0] == key and held[1])

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
    armed. Then it lands on nothing, and counts as aiming at it (``Arming.pressed``)."""
    if hit[1] not in GRANTS:
        return True
    arming = view(state)
    key = (hit[1], hit[2])
    activating = window_state(arming.owner)[2]
    if not activating and arming.armed(key):
        arming.hold(key)
        return True
    arming.pressed(key, activating=activating)
    return False


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


def restart():
    """Every control starts again: its shield from now, and a move into it (a hidden app's window brought up for
    the person: cards it drew while nobody saw them have spent their shield; doc/angelo/mcp_headless_window.rst)."""
    _restarts[0] += 1


def refused(window, region, rect, result):
    """A press on the chat's grant ``rect`` (canvas points) that landed on nothing: the hint on it, "Move onto the
    button, then click", as Blender's and the card host's buttons show it (grant_hints; the re-review of
    acd0d465ee9, SHOULD-3), unless it was the click that brought the window forward (that one aims). ``result``."""
    try:
        if window is not None and not window_state(window.as_pointer())[2]:
            from . import chat_view, grant_hints
            scale, (x, y, width, height) = chat_view.ui_scale(), rect
            left = region.width - (x + width) * scale if chat_view.is_rtl() else x * scale
            grant_hints.show((window.as_pointer(), region.x + left, region.y + y * scale, width * scale,
                              height * scale))
    except Exception as error:   # noqa: BLE001 - a hint never stops the press's own answer.
        print("Angelo: the hint could not be shown:", error)
    return result


def forget():
    """Tests and file loads: no window's focus history carries over (C keeps its own)."""
