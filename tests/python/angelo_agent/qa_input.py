# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A simulated click as a person makes it: the pointer arrives, then the button is pressed (GUI suites, 1.0.5).

Blender's interface activates the button under the pointer on a mouse move and handles a press against that active
button. A suite that queued move, press and release in one event-loop pass lost the click whenever the button had not
been activated yet, as in a click sent in the pass a window opened or a layout changed (35f5b544698 and dbe022dddb9
lines: Studio and Flows clicks that ran no operator; a probe showed the same click a pass later ran). ``press`` moves
the pointer now and presses on a later pass; a suite waits for the click's effect as before.

The press is a timer registered after the suite's own: when a stalled main thread makes both due in one pass, Blender
runs the suite's first, which then read the click's effect before the click was sent (flow_menu's "ran" phase, the
library card's untick; 1.0.8). ``after_clicks`` holds a suite's timer while a press is still on its way.

``aim`` is a click on a control that grants (Approve, Always allow, a review's Accept) as a person makes it in
2.0.0 and later: such a control takes only a click aimed at it (``approval_arming``).

``popup_button`` is where a menu item or a dialog's button is drawn, so a suite clicks it as a person does (a grant
operator's menu item takes only a real press: test_blender_trusted_scripts_consent, 2.0.0).

``drag_path`` is a drag as a person makes it, for the same reason: Blender starts dragging a button (an Asset Shelf
tile, an Asset Browser item) only from moves past its drag threshold while the pointer is still on that button. A
suite's drag whose first move after the press jumped a twelfth of the way to the target left the tile in one event,
so the 3D view's box select took the press and nothing was dropped (test_blender_media_shelf on a1c3a0dc6cf: the
shipped shelf and a plain Python shelf alike; the same drag paced this way landed on both).
"""

import time

AIM_CAP = 30.0   # Seconds an aimed click waits for its control at most (a hang, never a slow machine).
DELAY = 0.5    # Seconds between the pointer's arrival and the press: past C's ARM_SECONDS (0.3) since the control
               # was drawn, so the person's real move onto it at the press arms it (grant_operators.rst).
sent = []      # (x, y) of each press actually sent (a suite's timeout can say whether its click went out).
sent_at = []   # time.monotonic() of each press actually sent (a suite times what the click started from it).
_on_the_way = [0]   # Presses registered and not yet sent.
_waiting_since = [None]   # When ``after_clicks`` began holding a tick for a press (a cap, never forever).
lost = []      # Why a held tick stopped waiting: a press never sent within AIM_CAP (a suite reads it for its miss).
NUDGE = 3      # Pixels per move past the drag threshold, all inside the pressed button.
NUDGES = 5
STEP = 20      # Most pixels a dragging pointer moves per event on its way (a person's moves are this small).


def press(window, x, y=None, delay=DELAY):
    """Move ``window``'s pointer next to (x, y) now; ``delay`` seconds later move it onto (x, y), a real move, and press
    and release there. A grant control mints its C ticket only for a press after a real move inside it once it has
    been drawn there ARM_SECONDS (the coordinator's ruling on the review of d3024be): a person's hand reaches a
    control they read, never one that appeared under a resting pointer. ``x`` may be a function returning (x, y),
    read again at the press: a point taken from the drawn bar is current then."""
    import bpy   # Here, so drag_path needs no Blender (host tests).
    where = x if callable(x) else (lambda: (x, y))
    x, y = (int(v) for v in where())
    pointer = window.as_pointer()
    window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 1, y=y)   # Arriving; the press moves the last pixel.

    def click():
        _on_the_way[0] -= 1
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            return None
        x, y = (int(v) for v in where())
        target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        sent.append((x, y))
        sent_at.append(time.monotonic())
        return None
    _on_the_way[0] += 1
    bpy.app.timers.register(click, first_interval=delay, persistent=True)   # A file load keeps it.


def aim(window, rect, delay=None, ready=None):
    """A person's click on a control that grants (``approval_arming``, 2.0.0): the pointer waits just below it while
    its shield passes, then moves into it and clicks; if the window lost focus since, the first click goes below it
    (the click that brings the window forward, which never grants) and the shield is waited out again. ``rect`` is
    ``(x, y, w, h)`` in window coordinates, or a function returning it (read again at the click; a card not shown yet
    is waited for). ``ready()``, when given, is the arming's own signal that the control's shield has passed where it
    is now (``Arming.key_armed``): the click waits for it, so a layout that moved the control meanwhile is waited out
    too. Every wait is capped (``AIM_CAP``) and the click goes out then, so a hang fails in the suite's own check."""
    import bpy
    from angelo_agent import approval_arming
    from angelo_agent.approval_wait import SHIELD_SECONDS
    where = rect if callable(rect) else (lambda: rect)
    wait = SHIELD_SECONDS + DELAY if delay is None else delay
    pointer, started = window.as_pointer(), time.monotonic()

    def points():
        x, y, w, h = where()
        return (int(x + w / 2), int(y - 6)), (int(x + w / 2), int(y + h / 2))   # Just below it, then its middle.

    def click():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None:
            _on_the_way[0] -= 1
            return None
        patient = time.monotonic() - started < AIM_CAP
        try:
            (ox, oy), (x, y) = points()
        except Exception:   # noqa: BLE001 - not shown yet (a card opening): look again shortly.
            if patient:
                return 0.1
            _on_the_way[0] -= 1
            raise
        if approval_arming.window_state(pointer)[1]:   # Lost focus: bring the window forward first, beside it.
            for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
                target.event_simulate(type=kind, value=value, x=ox, y=oy)
            return SHIELD_SECONDS + DELAY
        if ready is not None and patient and not ready():
            target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=ox, y=oy)   # Waits below it, as a person.
            return 0.1
        _on_the_way[0] -= 1
        target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=ox, y=oy)
        target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
        target.event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
        sent.append((x, y))
        sent_at.append(time.monotonic())
        return None
    try:
        window.event_simulate(type='MOUSEMOVE', value='NOTHING', x=points()[0][0], y=points()[0][1])
    except Exception:   # noqa: BLE001 - not shown yet: the click looks again.
        pass
    _on_the_way[0] += 1
    bpy.app.timers.register(click, first_interval=wait, persistent=True)


def c_idname(operator):
    """``angelo.trusted_scripts`` as Blender's buttons name their operator: ``ANGELO_OT_trusted_scripts``."""
    group, _, name = operator.partition(".")
    return "%s_OT_%s" % (group.upper(), name) if name else operator


def popup_button(window, operator):
    """``(x, y, w, h)`` in window pixels of the drawn button that runs ``operator`` (its idname) in an open popup of
    ``window`` (a menu, a dialog), or None while none is drawn (``_angelo_jobs.popup_buttons``, a test seam). A suite
    clicks it with ``press``, as a person clicks a menu item: C mints a grant operator's ticket only for such a real
    press on its own button (grant_operators.rst), never for a scripted ``bpy.ops`` call."""
    from _bpy import _angelo_jobs
    return next(((x, y, w, h) for pointer, _block, _text, idname, x, y, w, h in _angelo_jobs.popup_buttons()
                 if pointer == window.as_pointer() and idname in (operator, c_idname(operator)) and w > 0 and h > 0),
                None)


def region_button(window, operator, text=None):
    """As ``popup_button``, for a button drawn in an editor's region (a sidebar panel's, a header's): ``(x, y, w, h)``
    of the first drawn one that runs ``operator`` (and reads ``text``, if given), or None
    (``_angelo_jobs.region_buttons``)."""
    from _bpy import _angelo_jobs
    return next(((x, y, w, h) for pointer, _block, label, idname, x, y, w, h in _angelo_jobs.region_buttons()
                 if pointer == window.as_pointer() and idname in (operator, c_idname(operator))
                 and (text is None or label == text)
                 and w > 0 and h > 0), None)


def pending():
    """Presses ``press`` registered that have not been sent yet."""
    return _on_the_way[0]


def after_clicks(callback):
    """``callback`` as a timer callback that waits while a press is on its way: register the result in place of the
    suite's tick, so a phase never reads a click's effect before the click was sent, however the timers line up."""
    def tick():
        if _on_the_way[0] > 0:
            started = _waiting_since[0] = _waiting_since[0] or time.monotonic()
            if time.monotonic() - started < AIM_CAP:
                return 0.02
            reason = "a press registered %.0f s ago was never sent (its timer is gone)" % AIM_CAP
            lost.append(reason)
            print("qa_input:", reason, flush=True)
            _on_the_way[0] = 0   # Stop holding: the suite's next check fails on the missing click, now.
        _waiting_since[0] = None
        return callback()
    return tick


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


# Chat canvas actions whose dispatch reaches a guarded operator: since the grant authority moved into C
# (doc/angelo/grant_operators.rst, "Authority in C"), only a press the window manager receives mints the ticket, so a
# suite clicks them through the window (``window_click``), never by calling the canvas's handler itself.
GRANT_ACTIONS = frozenset({"approve", "review", "script_session_allow", "script_always_allow", "popup_select_model",
                           "popup_custom_model", "popup_effort", "popup_reset", "popup_fast", "popup_enable_fast",
                           "popup_approval", "accept_native_preview",
                           # Read from the add-on's dispatch (test_native_qa_input; 02bfa33's Mac round): the
                           # welcome's Connect and Reconnect, the + menu's opener, Voice, and Media Studio's and the
                           # flows' Generate, Enhance, Run, Check and Fit.
                           "welcome_connect", "welcome_reconnect", "popup_add_open", "voice", "generate", "enhance",
                           "run", "wf_check", "wf_fit"})
AIMED = frozenset({"approve", "review", "script_session_allow", "script_always_allow"})   # SHIELDED chat controls


def window_click(window, region, rect, action, ready=None, scale=1.0):
    """A click on the canvas control at ``rect`` through the window: aimed for an approval's own control (``aim``:
    below it while its shield passes, then into it), else a plain ``press``. ``rect`` is in region pixels, or in the
    chat canvas's points with ``scale`` its ``chat_view.ui_scale()`` (the interface scale with the pixel size: a
    click that skips it misses when that is not 1, as under Xvfb's 1.04)."""
    x, y, width, height = (value * scale for value in rect)
    if action in AIMED:
        aim(window, (region.x + x, region.y + y, width, height), ready=ready)
    else:
        press(window, region.x + x + width / 2, region.y + y + height / 2)


def chat_rect(target, rect, scale=None, rtl=None):
    """A chat canvas rect (``chat_view``'s hits and geometry: points, laid out left to right) as window pixels
    ``(x, y, width, height)``: the inverse of the chat's pointer mapping, ``x = pointer_x(px - region.x, width) /
    ui_scale()``. ``target`` is the chat's area or its WINDOW region. A suite that added a chat rect to
    ``region.x``/``region.y`` by hand clicked beside the control whenever ``ui_scale()`` is not 1 (Xvfb at 75 dpi:
    0.78) or the language reads right to left (test_native_chat_click_scale)."""
    region = target if getattr(target, "type", None) == "WINDOW" else next(
        r for r in target.regions if r.type == "WINDOW")
    if scale is None:
        from angelo_agent import chat_view
        scale = chat_view.ui_scale()
    if rtl is None:
        from angelo_agent import ui_text
        rtl = ui_text.is_rtl()
    x, y, width, height = (value * scale for value in rect)
    if rtl:   # canvas_rtl.pointer_x mirrors about the region's width: the rect's right edge is its left on screen.
        x = region.width - (x + width)
    return region.x + x, region.y + y, width, height


def chat_point(target, rect, scale=None, rtl=None):
    """The window pixel at the centre of a chat canvas rect (``chat_rect``), as ints for ``press``/``event_simulate``."""
    x, y, width, height = chat_rect(target, rect, scale, rtl)
    return int(x + width / 2), int(y + height / 2)
