# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keyboard focus on every surface Angelo draws its own controls on: one focus order, one ring, one activation rule
and one pass-through rule for the chat canvas, Media Studio, a Generation Flow's bar, the Asset Browser's pills and
the Sessions panel (1.0.10, maintainer ruling: full keyboard focus).

A surface registers its clickables as hits ``(rect, action, value)`` in a state dict, as it does for the pointer
(``canvas_controls``), and hands each key press to ``key``:

* **Tab** and **Shift+Tab** move the focus through the controls in reading order (top to bottom, then left to right),
  or, with ``drawn=True``, in the order the surface registered them (Media Studio, whose columns each read top to
  bottom). Disabled and info hits take no focus. The **arrow keys** move it to the nearest control in that direction
  (mirrored on a canvas drawn right to left, ``mirrored``), and stay put when there is none.
* **Return** or **Space** activates the focused control: ``key`` returns its ``(action, value)`` and the surface runs
  the action its click runs, through the same function. With ``on_release=True`` the action runs when the key is let
  go, not pressed (``studio_enter``'s reason: a held key repeats its press, and Blender's confirmation dialog confirms
  on any Return press, so on the press a held key would confirm the dialog the action opened).
* **Escape** gives the keyboard back. Any other key does too and goes on (a letter to the chat's text field, a
  shortcut to Blender); a modifier pressed on its own changes nothing, so Shift+Tab steps back from where focus is.
* A focused control that goes away (a dismissed tile, a card's button): on a surface Tab goes round (``wrap``) it
  hands its place to the control now at its position in the order, so focus never silently leaves the surface, and
  the key that finds it gone only shows the ring there, never activating the new control unseen; on a canvas whose
  Tab returns to a text field, the focus returns there.

Where the keyboard starts. A canvas with a text field (the chat's composer, Media Studio's fields) takes Tab from its
field, and past either end the focus returns to the field (``entry=None``). A surface drawn inside a Blender editor
cannot take Tab or the arrows from it (the 3D view's Edit Mode, the Node Editor's Edit Group, the File Browser's
list): it is entered with ``shortcuts.FOCUS`` (``surface_focus``), Tab then wraps within it, and Escape or the entry
key again gives the keyboard back to the editor. ``entry`` is that key's test; until the surface holds the focus every
key passes through.

The ring is the console's 2 px mint ring 3 px outside the control (``ring_around``), on every surface. Pure: no
Blender at import, no GPU outside the ring's draw.
"""

DISABLED = "disabled"
INFO = "info"
INERT = "inert"   # An approval reveal shield: never a keyboard control.
ACTIVATE = frozenset({'RET', 'NUMPAD_ENTER', 'SPACE'})
ARROWS = {'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0), 'UP_ARROW': (0, 1), 'DOWN_ARROW': (0, -1)}
MODIFIERS = frozenset({'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY', 'APP',
                       'HYPER'})


def focus_order(hits, drawn=False):
    """The focusable ``(action, value)`` keys, each once: reading order (top to bottom, then left to right; region
    pixels, y up), or the order registered (``drawn``). Disabled and info hits are skipped."""
    kept = [hit for hit in hits if hit[1] not in (DISABLED, INFO, INERT)]
    if not drawn:
        kept = [hit for row in _rows(kept) for hit in sorted(row, key=lambda hit: hit[0][0])]
    order = []
    for hit in kept:   # A list, not a dict: a value need not be hashable.
        if (hit[1], hit[2]) not in order:
            order.append((hit[1], hit[2]))
    return order


def _rows(hits):
    """Hits in rows, top row first: a control whose vertical centre lies within a row's first (highest) control is
    in that row, so a 28 px pill beside 30 px buttons reads with them, left to right."""
    rows = []
    for hit in sorted(hits, key=lambda hit: -(hit[0][1] + hit[0][3])):
        middle = hit[0][1] + hit[0][3] / 2
        row = next((row for row in rows if row[0][0][1] <= middle <= row[0][0][1] + row[0][0][3]), None)
        if row is None:
            rows.append([hit])
        else:
            row.append(hit)
    return rows


def _hits(state, hits):
    return state.get("hits", []) if hits is None else hits


def _set(state, key, order):
    state["focus"] = key
    if key is not None:
        state["focus_index"] = order.index(key)
    # One focus indicator at a time: while the keyboard moves focus, a pointer resting on another control shows no
    # hover until it moves (``hover_shown``).
    state["keyboard_pointer"] = state.get("pointer") if key is not None else None


def move_focus(state, backwards=False, hits=None, drawn=False, wrap=False):
    """Tab to the next control (Shift+Tab: the previous). Past either end: none (back to the text field) or, with
    ``wrap``, round to the other end."""
    order = focus_order(_hits(state, hits), drawn)
    current = state.get("focus")
    if not order:
        _set(state, None, order)
        return
    if current in order:
        index = order.index(current) + (-1 if backwards else 1)
        index = index % len(order) if wrap else index
    else:
        index = len(order) - 1 if backwards else 0
    _set(state, order[index] if 0 <= index < len(order) else None, order)


def focus_on(state, key, hits=None, drawn=False):
    """Focus the control ``key`` when it is one of the surface's (a panel entered on its current item)."""
    order = focus_order(_hits(state, hits), drawn)
    if key in order:
        _set(state, key, order)


def first(state, hits=None, drawn=False):
    """Focus the first control (a surface entered with its key)."""
    state["focus"] = None
    move_focus(state, hits=hits, drawn=drawn)


def _centre(rect):
    return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2


def move_toward(state, direction, hits=None, drawn=False):
    """An arrow key: the nearest control whose centre lies in ``direction`` (dx, dy; y up) from the focused one's,
    distance across the direction counting double; none that way keeps the focus."""
    hits, order = _hits(state, hits), focus_order(_hits(state, hits), drawn)
    current = focused_hit(state, hits)
    if current is None:
        return
    cx, cy = _centre(current[0])
    best = None
    for rect, action, value in hits:
        if (action, value) not in order or (action, value) == state["focus"]:
            continue
        x, y = _centre(rect)
        along = (x - cx) * direction[0] + (y - cy) * direction[1]
        across = abs((x - cx) * direction[1]) + abs((y - cy) * direction[0])
        if along > 0 and (best is None or along + 2 * across < best[0]):
            best = (along + 2 * across, (action, value))
    if best is not None:
        _set(state, best[1], order)


def focused_hit(state, hits=None):
    key = state.get("focus")
    return next((hit for hit in _hits(state, hits) if (hit[1], hit[2]) == key), None) if key else None


def follow(state, hits=None, drawn=False):
    """A focused control that went away hands the focus to the control now at its place in the order. True when the
    focus moved so (the key that found it shows the ring there and does nothing more)."""
    key = state.get("focus")
    if key is None or focused_hit(state, hits) is not None:
        return False
    order = focus_order(_hits(state, hits), drawn)
    if not order:
        return False
    _set(state, order[min(state.get("focus_index", 0), len(order) - 1)], order)
    return True


def hover_shown(state):
    """The hover to draw: none while keyboard focus is shown and the pointer has not moved since it was set."""
    if state.get("focus") is not None and state.get("keyboard_pointer") is not None \
            and state.get("keyboard_pointer") == state.get("pointer"):
        return None
    return state.get("hover")


def leave(state, redraw=None):
    """The surface gives the keyboard back: no focus, nothing armed."""
    had = state.get("focus") is not None or state.get("armed") is not None
    state["focus"] = state["keyboard_pointer"] = None
    state.pop("armed", None)
    if had and redraw is not None:
        redraw()


def _plain(event):
    return not (getattr(event, "ctrl", False) or getattr(event, "oskey", False) or getattr(event, "alt", False))


def key(event, state, redraw, hits=None, *, entry=None, drawn=False, on_release=False, mirrored=False, enters=None,
        wrap=None):
    """One key event on a surface's controls.

    Returns None: not this surface's key (it passes on: to the text field, or to Blender); False: handled;
    ``(action, value)``: activate that control as its click does. ``entry``: for a surface entered with its own key,
    a test of whether ``event`` is that key (pressed again while focused, it gives the keyboard back). ``enters``: a
    test of a key that is a text field; Tab landing on one returns it, so the surface activates it (the field takes
    the keyboard) as a click on it does. ``wrap``: Tab goes round past either end (by default only on an entered
    surface; a canvas whose fields are among its controls, Media Studio, wraps too).
    """
    hits = _hits(state, hits)
    if event.type in MODIFIERS:
        return None
    loops = entry is not None if wrap is None else wrap
    if not loops and state.get("focus") is not None and focused_hit(state, hits) is None:
        leave(state)   # Its control went away: the keyboard is the text field's again.
    focused = state.get("focus") is not None
    if event.value == 'RELEASE' and event.type in ACTIVATE and on_release and state.get("armed") is not None:
        armed, hit = state.pop("armed"), focused_hit(state, hits)
        return (hit[1], hit[2]) if hit is not None and (hit[1], hit[2]) == armed else False
    if event.value != 'PRESS':
        return None
    if entry is not None and focused and entry(event):
        leave(state, redraw)
        return False
    if event.type == 'TAB' and _plain(event):
        if entry is not None and not focused:
            return None   # Tab stays the editor's until the surface is entered with its key.
        if not follow(state, hits, drawn):
            move_focus(state, event.shift, hits, drawn, wrap=loops)
        redraw()
        landed = state.get("focus")
        return landed if landed is not None and enters is not None and enters(landed) else False
    if not focused:
        return None
    if event.type in ARROWS and _plain(event) and not event.shift:
        dx, dy = ARROWS[event.type]
        if not follow(state, hits, drawn):
            move_toward(state, (-dx if mirrored else dx, dy), hits, drawn)
        redraw()
        return False
    if event.type in ACTIVATE and _plain(event) and not event.shift:
        if follow(state, hits, drawn):
            redraw()
            return False
        hit = focused_hit(state, hits)
        if hit is None:   # Nothing left to focus on the surface: the key is not its own.
            leave(state, redraw)
            return None
        if not on_release:
            return hit[1], hit[2]
        if not getattr(event, "is_repeat", False):
            state["armed"] = (hit[1], hit[2])
        return False
    leave(state, redraw)
    return False if event.type == 'ESC' else None


def ring_around(rect, radius, scale=1.0):
    """The focus ring: the console's 2 px mint ring, 3 px outside ``rect`` (``radius``: the control's own corners)."""
    from . import canvas_controls, ui_tokens
    gap = 3 * scale
    canvas_controls.ring(rect[0] - gap, rect[1] - gap, rect[2] + 2 * gap, rect[3] + 2 * gap, radius + gap, 2 * scale,
                         ui_tokens.FOCUS)


def draw_ring(state, hits=None, scale=1.0, drawn=False, radius=None, refocus=True):
    """Draw the ring around the focused control of a surface drawn now, after the surface itself (its control's own
    corners from ``canvas_controls.note_shape`` unless ``radius`` is given). Returns the control's rect, or None.
    ``refocus=False``: ``hits`` are only part of the surface's (one region of several), so a control missing from them
    is not gone."""
    from . import canvas_controls
    if refocus:
        follow(state, hits, drawn)
    hit = focused_hit(state, hits)
    if hit is None:
        return None
    ring_around(hit[0], canvas_controls.radius_for(hit[0]) if radius is None else radius, scale)
    return hit[0]
