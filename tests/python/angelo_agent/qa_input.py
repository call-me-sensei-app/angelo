# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A simulated click as a person makes it: the pointer arrives, then the button is pressed (GUI suites, 1.0.5).

Blender's interface activates the button under the pointer on a mouse move and handles a press against that active
button. A suite that queued move, press and release in one event-loop pass lost the click whenever the button had not
been activated yet, as in a click sent in the pass a window opened or a layout changed (35f5b544698 and dbe022dddb9
lines: Studio and Flows clicks that ran no operator; a probe showed the same click a pass later ran). ``press`` moves
the pointer now and presses on a later pass; a suite waits for the click's effect as before.

``drag_path`` is a drag as a person makes it, for the same reason: Blender starts dragging a button (an Asset Shelf
tile, an Asset Browser item) only from moves past its drag threshold while the pointer is still on that button. A
suite's drag whose first move after the press jumped a twelfth of the way to the target left the tile in one event,
so the 3D view's box select took the press and nothing was dropped (test_blender_media_shelf on a1c3a0dc6cf: the
shipped shelf and a plain Python shelf alike; the same drag paced this way landed on both).
"""

DELAY = 0.15   # Seconds between the pointer's arrival and the press: at least one pass with a redraw.
sent = []      # (x, y) of each press actually sent (a suite's timeout can say whether its click went out).
NUDGE = 3      # Pixels per move past the drag threshold, all inside the pressed button.
NUDGES = 5
STEP = 20      # Most pixels a dragging pointer moves per event on its way (a person's moves are this small).


def press(window, x, y=None, delay=DELAY):
    """Move ``window``'s pointer to (x, y) now; press and release there ``delay`` seconds later. ``x`` may be a
    function returning (x, y), read again at the press: a point taken from the drawn bar is current then."""
    import bpy   # Here, so drag_path needs no Blender (host tests).
    where = x if callable(x) else (lambda: (x, y))
    x, y = (int(v) for v in where())
    pointer = window.as_pointer()
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)

    def click():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            return None
        x, y = (int(v) for v in where())
        target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        sent.append((x, y))
        return None
    bpy.app.timers.register(click, first_interval=delay)


def drag_path(start, end, scale=1.0, button='LEFTMOUSE', release=True, linger=False):
    """The events of a drag from ``start`` to ``end`` as (type, value, (x, y)): the pointer arrives on what it
    presses, presses ``button``, moves past the drag threshold in small moves still on it, then travels in moves of
    at most ``STEP`` x ``scale`` pixels and releases at ``end``. A suite sends one per event-loop pass (a drag and
    drop), or all at once with ``send`` (a modal drag: an area edge, a scrollbar, a view navigation). Without
    ``release`` the button stays held (a capture mid-drag; the suite releases with ``release_at``); ``linger`` adds a
    1 px move off ``end`` and back before the release, so a drop target under the pointer draws its highlight."""
    (x0, y0), (x1, y1) = start, end
    events = [('MOUSEMOVE', 'NOTHING', (x0 - 2 * scale, y0)), ('MOUSEMOVE', 'NOTHING', (x0, y0)),
              (button, 'PRESS', (x0, y0))]
    distance = max(abs(x1 - x0), abs(y1 - y0))
    if distance:
        ux, uy = (x1 - x0) / distance, (y1 - y0) / distance
        for n in range(1, NUDGES + 1):
            reach = min(NUDGE * scale * n, distance)
            events.append(('MOUSEMOVE', 'NOTHING', (x0 + ux * reach, y0 + uy * reach)))
    events += _travel(events[-1][2], end, scale)
    if linger:
        events += [('MOUSEMOVE', 'NOTHING', (x1 + scale, y1)), ('MOUSEMOVE', 'NOTHING', (x1, y1))]
    if release:
        events.append(release_at(end, button))
    return events


def drag_across_path(start, end, scale=1.0, approach=200):
    """A drag that leaves one window and ends in another, as (source events, target events): the OS sends a
    dragging pointer's events to the window under it, so the arrival, press and threshold moves go to the source
    window and the rest, in the target's coordinates, travel ``approach`` x ``scale`` pixels to ``end`` there."""
    source = drag_path(start, (start[0] + 2 * NUDGE * NUDGES * scale, start[1]), scale, release=False)
    source = source[:3 + NUDGES]   # Arrival, press, and the threshold moves only.
    origin = (end[0] - approach * scale, end[1])
    target = [('MOUSEMOVE', 'NOTHING', origin)] + _travel(origin, end, scale) + [release_at(end)]
    return source, target


def release_at(end, button='LEFTMOUSE'):
    return (button, 'RELEASE', tuple(end))


def send(window, events, **modifiers):
    """Send ``events`` to ``window`` now, in one pass (a modal drag follows every move it is given)."""
    for kind, value, (x, y) in events:
        window.event_simulate(type=kind, value=value, x=int(x), y=int(y), **modifiers)


def _travel(start, end, scale):
    (xs, ys), (x1, y1) = start, end
    steps = max(1, int(-(-max(abs(x1 - xs), abs(y1 - ys)) // (STEP * scale))))
    return [('MOUSEMOVE', 'NOTHING', (xs + (x1 - xs) * n / steps, ys + (y1 - ys) * n / steps))
            for n in range(1, steps + 1)]
