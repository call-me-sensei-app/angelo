# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's keyboard (2.1.0, doc/angelo/attachment_markup.rst, "Keyboard").

Break-it of 2298d608dd1, M3 and S6: the viewer's modal is the window's newest handler, so it saw every key ahead of
the chat's field; over the 3D view it took them all. Letters typed into the chat became tool switches, and a Return
meant to send a message answered an open Remove BG or Erase card (a paid job). And no control had keyboard focus.

* **Who has the keyboard.** One owner per window (``keyboard_owner``). A press on the viewer (its picture or its
  bars) takes it, from the chat too (``keyboard_owner.take``: the chat never sees a press the viewer takes); a press
  anywhere else (the chat's field, another editor) gives it back, as ``chat_input`` and ``canvas_focus`` define focus
  everywhere else. The viewer opens with it when the person opened it (a picture chip, the + menu) and without it when
  the agent did (``angelo_app_feature``): typing in the chat goes on.
* **Without it, a chat holding the keyboard**: a key over the viewer goes to the chat only when the chat acts on it
  (``keyboard_owner.chat_takes``, the live view's rule), else nowhere; away from the viewer it goes to the chat. What
  the chat still hands on is never handed to the 3D view under the viewer (``overlay_holds.holds_covered``; re-review
  of b5808fd5a2e, M1: Ctrl+J joined meshes, Ctrl+Numpad 0 set the camera, Delete deleted with the composer hidden).
* **Without it, nobody holding it**: a key over the viewer makes the viewer the owner, as a press does, and is its own
  (Esc, F, the tool letters; S2). Away from the viewer it goes to the editor under the pointer, which is visible.
  Never to the 3D view the viewer covers (re-review of 4e712847d3b, M1).
* **Save** (Cmd/Ctrl+S) is handed on whoever holds the keyboard (``overlay_holds.app_shortcut``; N8).
* **With it**: a field being typed in (a comment's note, a text mark, the instructions) takes its keys. Otherwise
  ``canvas_focus.key`` runs Tab, the arrows and Return or Space on the focused control, on the key's release after
  its press armed it (the card's answers too), exactly as a click on that control does. Then the viewer's own keys
  (``markup_tools.Session.key``): the tool letters, Cmd/Ctrl+Z, Delete, F and Escape. Any key it does not use goes
  nowhere (M1, M2: passed on, it reached the covered 3D view, or the chat's draft and its Return). The quit key and
  what is not input pass before this (``input_policy.overlay_passes``).
* **An open card** (Remove BG, Erase, discard) answers only a click or the keyboard's armed press on one of its two
  buttons; Escape cancels. A Return with no button focused does nothing, so a key never starts a paid job by itself.
"""

from . import canvas_focus, canvas_rtl, input_policy, keyboard_owner, markup_tools as mt, overlay_holds

PRESSES = frozenset({'LEFTMOUSE', 'MIDDLEMOUSE', 'RIGHTMOUSE'})
NOT_KEYS = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN',
                      'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM', 'TIMER', 'WINDOW_DEACTIVATE'}) | PRESSES
RUNNING, PASS = {'RUNNING_MODAL'}, {'PASS_THROUGH'}


def owns(state, event, over, window):
    """A press takes the keyboard on the viewer (``over``: its region), from the chat of ``window`` too, and gives it
    back anywhere else."""
    if event.type in PRESSES and event.value == 'PRESS':
        if state.keyboard and not over:
            canvas_focus.leave(state.ui)
        if over:
            keyboard_owner.take(window)
        state.keyboard = over


def is_key(event):
    """Whether ``event`` takes the key path (``route``), not the pointer's."""
    return event.type not in NOT_KEYS and not event.type.startswith("NDOF_")


def route(state, event, act, effect, clipboard, redraw, *, over, chat_has_keyboard, chat_takes=False):
    """One event as a key: None when it is no key (the pointer's own path); else the modal's return set. ``act`` runs
    a control's action as its click does, ``effect`` a key's effect (``markup_overlay``). ``over``: the pointer is on
    the viewer's region; ``chat_has_keyboard``: a chat of the window holds the keyboard; ``chat_takes``: it acts on
    this key (``keyboard_owner``)."""
    if not is_key(event):
        return None
    if overlay_holds.app_shortcut(event):
        return PASS
    if not state.keyboard:
        if chat_has_keyboard:
            return PASS if chat_takes or not over else RUNNING
        if not over:
            return PASS
        if event.value != 'PRESS' or event.type.startswith("IME_"):
            return RUNNING   # A release or an input method's event takes no keyboard; it goes nowhere (N5).
        state.keyboard = True   # Nobody held it: the key over the viewer is the viewer's, as a press would be (S2).
    session = state.session
    if not (session.editing or session.instructions.editing):
        landed = canvas_focus.key(event, state.ui, redraw, hits=state.plan["hits"], on_release=True, wrap=True,
                                  mirrored=canvas_rtl.mirrored())
        if isinstance(landed, tuple):
            act(state, *landed)
            return RUNNING
        if landed is False:
            return RUNNING
    if event.value != 'PRESS':
        return RUNNING
    result = session.key(event.type, getattr(event, "unicode", "") or "", bool(event.ctrl or event.oskey),
                         bool(event.shift), clipboard=clipboard)
    if result == mt.UNUSED:
        return RUNNING
    effect(state, result)
    return RUNNING
