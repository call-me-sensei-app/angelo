# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pointer rules of ``card_host``'s cards: when a press on a grant counts, and the cursor. Split from ``card_host``
(500-line rule), which imports these names.

A card names the controls that grant in ``grants`` (the preview review card's confirmation and Accept). They take a
press only once armed (``approval_arming``: their shield has passed since they were drawn where they are, the
pointer moved into them since, and the window's focus has not changed since), and the keyboard only once the
person focused them and their shield has passed; a card's keyboard default is never a grant.
"""

from . import approval_arming


def grants(card):
    return getattr(card, "grants", frozenset())


def sync(state, region=None):
    """The card's plan as drawn now: where each of its grants is (``approval_arming.Arming.drawn``), and, from its
    draw in ``region``, as C's consent regions (grant_guard.canvas_regions: card pixels, mirrored right to left)."""
    if state.arming is None:
        state.arming = approval_arming.Arming(state.window)
    granting = grants(state.card)
    hits = getattr(state.plan, "hits", None) or []
    state.arming.drawn([((action, value), rect) for rect, action, value in hits if action in granting])
    if region is not None:
        import bpy
        from . import grant_guard
        from .ui_text import is_rtl
        grant_guard.canvas_regions(bpy.context.window, region, "card", hits, 1.0, is_rtl(), prefix=PREFIX,
                                   shielded=granting, focus=state.ui.focus if state.keyboard else None)


PREFIX = "card."   # A card's grants are ticketed as "card.<action>" (grant_guard.ticketed).


def act(state, action, value, host):
    """The card's own act, in the grant scope of the person's press on it when it minted a ticket (grant_guard)."""
    from . import grant_guard
    try:
        grant_guard.ticketed(PREFIX, action, value, state.card.act, action, value, host)
    except PermissionError as error:
        print("Angelo: a card's click was not taken as a grant:", error)


def moved(state, point, real=True):
    """The pointer over the card's view at ``point``, or None when it left the view; ``real`` False: a move without
    motion (Blender's refresh, a window's activation), never a move into anything (approval_arming)."""
    sync(state)
    state.arming.moved(point, real=real)


def lands(state, target):
    """Whether a press on ``target`` (``(action, value)``) counts."""
    sync(state)
    if target[0] not in grants(state.card):
        return True
    activating = approval_arming.window_state(state.arming.owner)[2]
    if not activating and state.arming.armed(target):
        return True
    state.arming.pressed(target, activating=activating)   # Lands on nothing, and aims at it (approval_arming).
    return False


def key_lands(state, target):
    """Whether a keyboard activation of the focused ``target`` counts."""
    sync(state)
    return target[0] not in grants(state.card) or state.arming.key_armed(target)


def default(card):
    """The card's keyboard default (Return with nothing focused), never a grant."""
    action = card.default_action
    return None if action in grants(card) else action


def cursor(window, state, name):
    if state.cursor != name:
        state.cursor = name
        try:
            window.cursor_set(name)
        except (RuntimeError, TypeError):
            pass
