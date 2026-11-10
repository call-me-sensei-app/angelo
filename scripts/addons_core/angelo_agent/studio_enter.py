# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Return in Media Studio's text fields (``text_keys.enter``), on the Studio's view state. No Blender.

A drawn field names its primary action with ``primary(state, key, action, value, reason)`` as the canvas draws the
action's button: ``reason`` (an ``n_`` message) says why it is unavailable, or None. The action is available when its
button registered a hit this frame (a disabled button registers none), and Return runs it through that hit: the same
``click`` a press on the button runs, so Generate opens the same confirmation, with its billing and price, before
anything is spent.

The action runs when Return is *released*, not pressed. Holding Return repeats its press, and Blender's confirmation
dialog confirms on any Return press: run on the press, a held key would open the confirmation and confirm it at once.
On the release the key is up, so the confirmation waits for a deliberate Return or click. Repeats are swallowed.
"""

from . import text_keys


def primary(state, key, action, value=None, reason=None):
    state.setdefault("primaries", {})[key] = (action, value, reason)


def _target(state, key):
    """``(entry, hit)``: the field's primary action and its button's hit this frame (None when it has none)."""
    entry = state.get("primaries", {}).get(key)
    if entry is None:
        return None, None
    hit = next(((r, a, v) for r, a, v in reversed(state.get("hits", [])) if (a, v) == tuple(entry[:2])), None)
    return entry, hit


def on_return(state, key, multiline, shift, repeat):
    """Apply Return's press to the focused field ``key``; returns what it did: ``text_keys.LINE`` (the caller inserts
    the line), ``BLUR``, ``BLOCKED`` (the reason is the Studio's notice), ``PRIMARY`` (armed: runs on the release)."""
    entry, hit = _target(state, key)
    rule = text_keys.enter(multiline, shift, None if entry is None else hit is not None)
    if rule == text_keys.BLUR:
        state["field_focus"], state["selection"] = None, None
    elif rule == text_keys.BLOCKED:
        state["notice"] = entry[2] or ""
    elif rule == text_keys.PRIMARY and not repeat:
        state["enter_armed"] = key
    return rule


def on_release(state, key):
    """Return let go: the primary action's hit ``(rect, action, value)`` to click, if its press armed it for ``key``
    and it is still available; None otherwise."""
    if state.pop("enter_armed", None) != key or key is None:
        return None
    return _target(state, key)[1]
