# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shows one Angelo notice card at a time over the largest 3D view, and owns its input.

A *card* (``update_card``, ``crash_card``, ``whatsnew_card``) lays itself out with
``card_kit`` and reacts to actions; this module draws it (``card_draw``) from a
POST_PIXEL handler on the 3D view and runs one modal operator for events, the same
arrangement as the welcome screen (``intro.py``).

* A **modal** card (the crash dialog, What's New) takes every input event while the pointer
  is over the 3D view, so nothing behind it reacts; the chat and the rest of the
  window stay usable. Tab, Shift+Tab, Return, Space and Escape work with the pointer
  over the view; a focus ring shows only after the keyboard has been used.
* A **corner** card (the update notice) is non-modal: it takes only mouse presses and wheel
  turns on its own rectangle (so a click on it never selects what is behind it) and lets
  every other event through, so orbiting, selecting and typing in the viewport are untouched.
* What is not input (timers, window events), Blender's motionless hover refresh and the quit key
  always pass through (``input_policy.overlay_passes``, ``refresh_move``), so a card never starves
  another operator's timers or a quit requested before it opened. A closed card's operator is
  woken by a one-shot timer and ends at once, passing its event on.

One card is visible at a time; others wait in a priority queue (a crash report before What's
New before an update notice). Drawing touches no files and no Blender data. A card that
loses its area (a layout change) is shown again by the next pump if it asks for it.

The not-connected showcase (``connect_hero``) covers the same 3D view. It yields to a card as it does to
the welcome screen, whichever opened first: the card paints it beneath itself (``_draw``) and the showcase's
input handler passes on what the card takes (``takes_input``).
"""

import time
from dataclasses import dataclass, field

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import canvas_rtl, card_draw, card_kit as kit, font_watch, input_policy

_handlers = {}            # Space type (bpy.types.Space subclass) -> its POST_PIXEL draw handler.
_active = None            # HostState of the visible card.
_queue = []               # [(-priority, sequence, card)]
_sequence = 0
_pending = None           # The card the operator about to be invoked shows.
_nudge = None             # (window, timer): wakes the closed card's operator so it ends at once.
opened = 0                # Cards shown this session; window fixtures read it.
history = []              # The kinds shown, newest last (bounded), for fixtures.
ANIMATION_STEP = 0.07
POINTER_EVENTS = frozenset({'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE', 'WHEELUPMOUSE',
                            'WHEELDOWNMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE', 'MOUSESMARTZOOM'})


@dataclass
class HostState:
    card: object
    window: int
    area: int
    token: object
    ui: kit.UiState = field(default_factory=kit.UiState)
    plan: object = None
    plan_key: object = None
    keyboard: bool = False    # Focus ring visible.
    cursor: str = "DEFAULT"


class Host:
    """What a card's ``act`` may do."""

    def __init__(self, state):
        self.state = state

    def redraw(self):
        _redraw()

    def close(self, how="done"):
        close(how)

    def after(self, seconds, function):
        """Call ``function`` after ``seconds`` on the main thread, again after each number it returns, never once
        this card has closed."""
        token = self.state.token

        def run():
            if _active is None or _active.token is not token:
                return None
            return function()

        bpy.app.timers.register(run, first_interval=seconds)

    def window(self):
        return next((w for w in bpy.context.window_manager.windows if w.as_pointer() == self.state.window), None)


class _View:
    """The chat canvas's drawing primitives, with measuring, for ``card_kit``.

    The font IDs are read from ``chat_view`` at every use, never copied: it binds new ones after a file is
    opened or Blender reloads its interface fonts (``font_watch``), and a copied ID would draw nothing.
    """

    def __init__(self):
        from . import chat_view
        self._chat = chat_view
        self.wrap, self.shape, self.text, self.stroke = chat_view.wrap, chat_view.shape, chat_view.text, chat_view.stroke

    @property
    def _font(self):
        return self._chat._font

    @property
    def _mono(self):
        return self._chat._mono or self._chat._font

    @staticmethod
    def measure(text, size, font):
        blf.size(font, size)
        return blf.dimensions(font, text)[0]


def is_active(kind=None):
    return _active is not None and (kind is None or _active.card.kind == kind)


def active_card():
    return _active.card if _active else None


def refresh(kind):
    """Redraw the visible card of ``kind`` after its live content changed (a download's progress)."""
    if _active is not None and _active.card.kind == kind:
        _redraw()


def shown_on(area_pointer):
    """Whether a card shows over this 3D view."""
    return _active is not None and _active.area == area_pointer


def takes_input(area_pointer, x, y):
    """Whether the visible card takes the input at ``(x, y)`` (region pixels, canvas orientation) of this 3D
    view: anywhere for a modal card, on its own rectangle for a corner card. A canvas whose input handler sits
    above the card's (the showcase, opened later) passes these events on."""
    state = _active
    if state is None or state.area != area_pointer:
        return False
    return state.card.modal or (state.plan is not None and kit.inside(state.plan.card, x, y))


def queued(kind=None):
    return [card for _, _, card in _queue if kind is None or card.kind == kind]


def request(card):
    """Show ``card`` when nothing else is showing; otherwise queue it (a corner card yields to a more important one)."""
    global _sequence
    _queue[:] = [item for item in _queue if item[2].kind != card.kind]   # One card of each kind waits at most.
    _sequence += 1
    _queue.append((-card.priority, _sequence, card))
    _queue.sort(key=lambda item: item[:2])
    if _active is not None and not _active.card.modal and card.priority > _active.card.priority:
        close("replaced", requeue=True)


def check_area():
    """Close the visible card as lost when its area went away without an event reaching it (a layout change: the
    chat hint's own Hide chat closes the chat it stands over). Its modal operator would notice only at the next
    input; a card that asks (``retry_when_lost``) waits again and the next pump shows it where it belongs now."""
    if _active is None:
        return False
    for window in bpy.context.window_manager.windows:
        if window.screen and any(area.as_pointer() == _active.area for area in window.screen.areas):
            return False
    close("lost")
    return True


def withdraw(kind):
    """Drop a waiting card of ``kind`` that no longer applies (a visible one is closed with ``close``)."""
    _queue[:] = [item for item in _queue if item[2].kind != kind]


def pump(ready):
    """Show the best waiting card when the window is ready (``ready()`` is the caller's condition)."""
    if _active is not None or not _queue or not ready():
        return False
    card = _queue[0][2]
    window = next((w for w in bpy.context.window_manager.windows if w.screen and not w.screen.is_temporary
                   and _target_area(w, card) is not None), None)
    if window is None:
        return False
    _queue.pop(0)
    return _show(card, window)


def _target_area(window, card=None):
    """The area a card shows over: the one it asks for (``target_area``, the chat hint's area under the top bar's
    Chat button), else the largest 3D view."""
    chosen = getattr(card, "target_area", None)
    if chosen is not None:
        area = chosen(window)
        if area is not None:
            return area
    areas = [a for a in window.screen.areas if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height, default=None)


def _show(card, window):
    global _pending, opened
    _pending = card
    try:
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.card_host('INVOKE_DEFAULT')
    except RuntimeError:
        _pending = None
        _queue.append((-card.priority, _sequence, card))
        _queue.sort(key=lambda item: item[:2])
        return False
    if _active is None or _active.card is not card:
        return False
    opened += 1
    history.append(card.kind)
    del history[:-32]
    return True


def close(how="done", requeue=False, wake=True):
    """Hide the visible card. ``wake=False`` when its operator is already gone (a file is being opened)."""
    global _active
    state, _active = _active, None
    if state is None:
        return
    for window in bpy.context.window_manager.windows:
        if window.as_pointer() == state.window:
            try:
                window.cursor_set('DEFAULT')
            except (RuntimeError, TypeError):
                pass
            if wake:
                _wake(window)
        for area in window.screen.areas:
            if area.as_pointer() == state.area:
                area.tag_redraw()
    state.card.closed(how)
    if requeue or (how == "lost" and getattr(state.card, "retry_when_lost", False)):
        _queue.append((-state.card.priority, _sequence, state.card))
        _queue.sort(key=lambda item: item[:2])


def _wake(window):
    """A one-shot timer event, so the closed card's modal operator ends now rather than at the next input.

    Until it ends, Blender lists it among the window's modal operators, and everything that waits for the
    window to be free of interactions (the welcome screen, the next card) would wait for the person to move.
    """
    global _nudge
    _end_nudge()
    try:
        _nudge = (window, bpy.context.window_manager.event_timer_add(0.05, window=window))
    except (RuntimeError, TypeError, ValueError):
        _nudge = None
        return
    # Window timers repeat: removed by the operator it wakes, and in any case half a second later.
    if bpy.app.timers.is_registered(_end_nudge):
        bpy.app.timers.unregister(_end_nudge)
    bpy.app.timers.register(_end_nudge, first_interval=0.5)


def _end_nudge():
    """Remove the wake-up timer (idempotent). Returns None, so it also ends as an app timer."""
    global _nudge
    if _nudge is not None:
        try:
            bpy.context.window_manager.event_timer_remove(_nudge[1])
        except (RuntimeError, TypeError, ValueError, ReferenceError):
            pass
        _nudge = None


def _redraw():
    if _active is None:
        return
    if _active.card.animated and not bpy.app.timers.is_registered(_animate):
        bpy.app.timers.register(_animate, first_interval=ANIMATION_STEP)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.as_pointer() == _active.area:
                area.tag_redraw()


_view = None


def _draw():
    global _view
    state = _active
    area, region = bpy.context.area, bpy.context.region
    if state is None or area is None or region is None or area.as_pointer() != state.area:
        return
    if _view is None:
        _view = _View()
    ui, card = state.ui, state.card
    ui.focus = ui.focus if state.keyboard else None
    scale = bpy.context.preferences.system.ui_scale
    # A font reload can hand back the same ID: its count lays the card out again (text measured meanwhile was 0 wide).
    key = (region.width, region.height, scale, ui.hover, ui.pressed, ui.focus, tuple(sorted(ui.scroll.items())),
           card.version(), int(ui.time * 12) if card.animated else 0, _view._font, font_watch.checks["reloads"])
    if state.plan is None or key != state.plan_key:
        state.plan, state.plan_key = card.layout(_view, region.width, region.height, scale, ui), key
    from . import connect_hero
    connect_hero.paint_beneath(area, region)   # The showcase, if it covers this view, sits under the card.
    canvas_rtl.begin(region.width)  # Mirrored in a right-to-left language, like the chat; ``end`` in ``finally``.
    try:
        card_draw.draw(_view, state.plan)
    finally:
        canvas_rtl.end()
    gpu.state.blend_set('NONE')


def _animate():
    """Keeps a spinning card redrawing; the timer ends as soon as no card animates."""
    if _active is None or not _active.card.animated:
        return None
    _active.ui.time = time.monotonic()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.as_pointer() == _active.area:
                area.tag_redraw()
    return ANIMATION_STEP


class ANGELO_OT_card_host(Operator):
    bl_idname = "angelo.card_host"
    bl_label = "Angelo Notice"
    bl_description = "Shows an Angelo notice card (update, What's New or crash report) over the 3D view"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        global _active, _pending
        card, _pending = _pending, None
        window = context.window
        area = _target_area(window, card) if window else None
        if card is None or area is None:
            return {'CANCELLED'}
        space = type(area.spaces.active)
        if space not in _handlers:   # Added after the editor's own drawing (the chat's canvas), so drawn above it.
            _handlers[space] = space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        self.token = object()
        _active = HostState(card, window.as_pointer(), area.as_pointer(), self.token)
        _active.ui.time = time.monotonic()
        card.opened(Host(_active))
        context.window_manager.modal_handler_add(self)
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        state = _active
        if state is None or state.token is not self.token:
            _end_nudge()
            return {'FINISHED', 'PASS_THROUGH'}   # Closed: whatever this event is, it belongs to what is below.
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}   # Timers are not input: they belong to whoever set them; the quit key quits.
        if context.window.as_pointer() != state.window:
            return {'PASS_THROUGH'}
        area = next((a for a in context.window.screen.areas if a.as_pointer() == state.area), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
        if region is None:
            close("lost")
            return {'FINISHED', 'PASS_THROUGH'}
        card, plan, ui = state.card, state.plan, state.ui
        over = (region.x <= event.mouse_x < region.x + region.width and region.y <= event.mouse_y < region.y + region.height)
        if not over or plan is None:
            if ui.hover is not None or ui.pressed is not None:
                ui.hover = ui.pressed = None
                area.tag_redraw()
            return {'PASS_THROUGH'}
        x, y = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width), event.mouse_y - region.y
        target = kit.hit_test(plan, x, y)
        modal = card.modal
        # A modal card owns the whole 3D view; a corner card only the pointer's presses and wheel turns on itself,
        # so a click on its background never reaches the objects behind it.
        captures = modal or (event.type in POINTER_EVENTS and kit.inside(plan.card, x, y))
        if event.type == 'MOUSEMOVE':
            if target != ui.hover:
                ui.hover = target
                _cursor(context.window, state, "HAND" if target else "DEFAULT")
                area.tag_redraw()
            return {'RUNNING_MODAL'} if modal and not input_policy.refresh_move(event) else {'PASS_THROUGH'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'} and (box := kit.scroll_box_at(plan, x, y)):
            step = 54 * plan.scale
            if event.type == 'TRACKPADPAN':
                delta = (event.mouse_y - event.mouse_prev_y) * -1.0
            else:
                delta = -step if event.type == 'WHEELUPMOUSE' else step
            ui.scroll[box] = kit.clamp_scroll(plan, box, ui.scroll.get(box, 0.0) + delta)
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            ui.pressed, state.keyboard, ui.focus = target, False, None
            area.tag_redraw()
            if target is not None:
                return {'RUNNING_MODAL'}
            if modal and card.outside_dismisses and not kit.inside(plan.card, x, y):
                self._act(card.cancel_action, None, state)
            return {'RUNNING_MODAL'} if captures else {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            pressed, ui.pressed = ui.pressed, None
            area.tag_redraw()
            if pressed is not None and pressed == target:
                self._act(pressed[0], pressed[1], state)
                return {'RUNNING_MODAL'}
            return {'RUNNING_MODAL'} if pressed is not None or captures else {'PASS_THROUGH'}
        if event.value == 'PRESS' and getattr(card, "key_event", None) and card.key_event(event, Host(state)):
            _redraw()   # A card that edits text (ui_kit_field) takes the key first; Tab and the rest fall through.
            return {'RUNNING_MODAL'}
        if modal and event.value == 'PRESS' and self._key(event, state, area):
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'} if captures else {'PASS_THROUGH'}

    def _key(self, event, state, area):
        """Keyboard control of a modal card. True when the event was used."""
        card, plan, ui = state.card, state.plan, state.ui
        order = plan.focus_order
        if event.type == 'TAB' and order:
            index = order.index(ui.focus) if ui.focus in order else -1
            ui.focus = order[(index + (-1 if event.shift else 1)) % len(order)]
            state.keyboard = True
        elif event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'}:
            target = ui.focus if state.keyboard and ui.focus in order else (card.default_action, None) if event.type != 'SPACE' else None
            if target is None or target[0] is None:
                return event.type == 'SPACE'
            self._act(target[0], target[1], state)
        elif event.type == 'ESC':
            self._act(card.cancel_action, None, state)
        else:
            return False
        area.tag_redraw()
        return True

    def _act(self, action, value, state):
        state.card.act(action, value, Host(state))
        _redraw()


def _cursor(window, state, name):
    if state.cursor != name:
        state.cursor = name
        try:
            window.cursor_set(name)
        except (RuntimeError, TypeError):
            pass


@persistent
def _load_pre(*_args):
    """Opening a file drops every modal operator: the visible card loses its input, so it is closed as lost
    (the crash dialog and What's New come back after the file is open; an update notice stays in the bars)."""
    close("lost", wake=False)


def register():
    bpy.utils.register_class(ANGELO_OT_card_host)
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _active, _view
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    for timer in (_animate, _end_nudge):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    _end_nudge()
    _active, _view = None, None
    _queue.clear()
    for space, handler in _handlers.items():
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    bpy.utils.unregister_class(ANGELO_OT_card_host)
