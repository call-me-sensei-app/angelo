# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo in the corner: while the chat is hidden, the raven waits in a bottom corner of the 3D view, and a click
opens the chat (doc/angelo/agent_chat.rst, "Show and hide").

Evidence: the maintainer (2026-10-21): "we should always have Angelo in the bottom corner of the screen when chat
window is closed and it should be about the same size as the marble loading we have right now. When user click on
Angelo, it opens up the chat window and Angelo in the corner disappears." The top bar's Chat button (``chat_toggle``)
stays; this puts the way back where the person is looking.

* **Where**: the largest 3D view of each window whose chat is hidden (``chat_toggle.chat_state``: a popped-out chat
  is shown), in the bottom corner on the chat's own side (``ui._dock_side``; Director's chat is on the right), clear
  of the toolbar, the sidebar and a bottom asset shelf or header when they overlap the view (``insets``). Blender's
  navigation gizmo is at the top and the status bar outside the view, so neither is covered.
* **What**: the raven of the chat's marble wait at its compact hero size (``marble_wait.HERO_COMPACT``), drawn from
  the same process-wide batches (``marble_draw.draw_still``), on the Angelo button's round card (``round_button``,
  which the minimized Sessions panel shares). The pointer over it lifts its
  eyes, rings the disc in teal and shows the toggle's own label as a tooltip (*Chat ⌃ ⌥ N*,
  ``chat_toggle.button_text``: translated, the shortcut as bound now).
* **Motion**: a blink every ``BLINK_EVERY`` seconds, and only in a 3D view whose own draw is cheap: a blink is three
  redraws of the whole view, so a view that took longer than ``MAX_VIEW_COST`` (measured between its PRE_VIEW and
  POST_PIXEL callbacks) keeps Angelo still. Never with Reduce Motion (``marble_host.reduced``: Blender's or the
  system's). The launcher's own draw is a disc, a ring and the raven's cached batches (``draw_seconds``).
* **Input**: a passive modal operator (``input_policy.CANVAS_INPUTS``, so it never makes a tool, a dock or a mode
  change wait) that takes only left and right presses on its own circle; a click, or Return with the pointer on it,
  runs the toggle after the event and hides Angelo at once. Every other event passes, and while a real tool holds
  the window (``input_policy.canvas_blocking``) it takes nothing. Keyboard: the shortcut on the tooltip.
* **When it moves**: at once on every signalled change: the toggle, a workspace switch, a mode switch or dock ending
  (``settled``), a file load, an area's editor type or a console's language (the message bus, ``_subscribe``). Close
  Area, a window closing or opening, Maximize Area and resizes notify no one: the watch (twice a second) places it
  after those, and its draw never stands beside a shown chat meanwhile.
* Never in a background or automation run, unless ``ANGELO_ENABLE_CHAT_LAUNCHER=1`` (its GUI suite).
"""

import time
from collections import deque

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import input_policy, live_windows, round_button

IDENTITY = "angelo.chat_launcher_input"
MARK = 56.0             # marble_wait.HERO_COMPACT: the marble's compact hero size (pixels at a UI scale of 1).
DISC = round_button.DISC   # The round card under the raven (the Angelo button's one size).
MARGIN = 16.0           # From the view's edges and from any region drawn over it.
LOOK = 20.0             # Mark units the eyes rise while the pointer is over Angelo.
BLINK_EVERY = 6.0       # Seconds between blinks.
BLINK_SECONDS = 0.16
FRAME = 0.05
MAX_VIEW_COST = 0.006   # Seconds: a 3D view that draws slower keeps Angelo still (a blink redraws it three times).
OPENING_SECONDS = 3.0   # A click hides Angelo until the chat shows, or this long if it could not.
BOTTOM_REGIONS = frozenset({'ASSET_SHELF', 'ASSET_SHELF_HEADER', 'HEADER', 'TOOL_HEADER', 'FOOTER'})

_shown = {}       # window pointer -> area pointer of the 3D view Angelo stands in.
_places = {}      # window pointer -> (area pointer, centre x, centre y, radius) in window pixels, as last drawn.
_hover = set()    # Window pointers whose Angelo the pointer is over.
_pressed = set()
_opening = {}     # window pointer -> when a click asked for the chat.
_started = {}     # area pointer -> perf_counter at its PRE_VIEW callback.
_cost = {}        # area pointer -> its last full draw, seconds (PRE_VIEW to POST_PIXEL).
_blink = None     # When the current blink started (time.monotonic), or None.
drawn_at = {}     # area pointer -> when any 3D view last drew (time.monotonic): chat_toggle docks a follow after it.
_handlers = []
_owner = object()   # The message bus subscriptions' owner (``_subscribe``).
_view = None
last_error = None      # A raven draw that raised: the disc draws on without it (never the chat's marble_host).
draws = 0              # Fixtures: launcher draws and their total time, and the latest draws' own times.
draw_seconds = 0.0
samples = deque(maxlen=256)
blinks = 0


def enabled():
    from . import app_notices, product_env
    if bpy.app.background:
        return False
    return not app_notices.automation_off() or product_env.flag("ENABLE_CHAT_LAUNCHER")


def moving():
    """A layout change is under way (a mode switch, a dock): the chat is about to show or hide, so Angelo waits.
    Evidence: at startup, between the add-on's registration and Director's split, Angelo appeared for a moment and
    its blink timer started (release GUI run, 2026-10-22)."""
    from . import director, ui
    return director._timer is not None or ui._layout_timer is not None


def target(window, windows):
    """The 3D view Angelo stands in for ``window``: its largest, while the chat is hidden; else None."""
    from . import chat_toggle
    if window.screen is None or window.screen.is_temporary or moving():
        return None
    if chat_toggle.chat_state(window, windows)[0] != "hidden" or window.as_pointer() in chat_toggle._pending:
        return None   # Shown, or about to be: a follow docks it once the tab has drawn.
    views = [a for a in live_windows.areas(window) if a.type == 'VIEW_3D']
    return max(views, key=lambda a: a.width * a.height, default=None)


def corner(size, insets, scale, side):
    """``(cx, cy, radius)`` of the disc in the view's region pixels. ``size`` is the region's ``(width, height)``,
    ``insets`` the ``(left, right, bottom)`` pixels other regions cover, ``side`` 'RIGHT' or 'LEFT'. Pure."""
    width, height = size
    left, right, bottom = insets
    radius, margin = DISC * scale / 2, MARGIN * scale
    cx = width - right - margin - radius if side == "RIGHT" else left + margin + radius
    cy = bottom + margin + radius
    return (min(max(cx, radius), max(radius, width - radius)),
            min(max(cy, radius), max(radius, height - radius)), radius)


def insets(regions, region):
    """``(left, right, bottom)`` pixels of ``region`` covered by the other ``regions`` drawn over it (region
    overlap): the toolbar, the sidebar, a bottom asset shelf or header. Regions are anything with type/x/y/width/
    height (window pixels); the 3D view's top header is not at the bottom and counts for nothing."""
    left = right = bottom = 0
    for other in regions:
        if other is region or other.type == 'WINDOW' or other.width <= 1 or other.height <= 1:
            continue
        if not (other.x < region.x + region.width and other.x + other.width > region.x
                and other.y < region.y + region.height and other.y + other.height > region.y):
            continue
        if other.type == 'TOOLS':
            left = max(left, other.x + other.width - region.x)
        elif other.type == 'UI':
            right = max(right, region.x + region.width - other.x)
        elif other.type in BOTTOM_REGIONS and other.y <= region.y + 2:
            bottom = max(bottom, other.y + other.height - region.y)
    return left, right, bottom


def side(window):
    """The chat's side: where it opens. Director's chat is always on the right."""
    from . import director, ui
    return "RIGHT" if director.is_director(window) else getattr(ui, "_dock_side", "RIGHT")


def eye_open(now, start):
    """How open the eyes are (1 open, 0.1 shut) during a blink that started at ``start`` (None: no blink)."""
    if start is None or not 0 <= now - start < BLINK_SECONDS:
        return 1.0
    half = BLINK_SECONDS / 2
    return 0.1 + 0.9 * abs(now - start - half) / half


def inside(place, x, y):
    """Whether window pixel (``x``, ``y``) is on the disc ``place`` (``_places``)."""
    _area, cx, cy, radius = place
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius


# -- state ----------------------------------------------------------------------------------------------------
def _areas(pointers):
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.as_pointer() in pointers:
            yield area


def _redraw(pointers):
    for area in _areas(set(pointers)):
        area.tag_redraw()


def update():
    """Where Angelo stands now: called on every signalled chat change (module docstring, "When it moves") and twice a
    second by ``chat_toggle``'s watch for the rest."""
    global _shown
    if not enabled():
        if _shown:
            _redraw(_shown.values())
            _shown = {}
        return
    windows, every = live_windows.windows(bpy.context.window_manager), live_windows.every_window(bpy.context.window_manager)
    now = time.monotonic()
    for pointer, at in list(_opening.items()):
        if now - at > OPENING_SECONDS:
            del _opening[pointer]
    shown = {}
    for window in windows:
        area = target(window, every)
        if area is not None:
            shown[window.as_pointer()] = area.as_pointer()
        else:
            _opening.pop(window.as_pointer(), None)   # The chat is shown: the click did what it asked.
    if shown != _shown:
        _redraw(set(_shown.values()) | set(shown.values()))
        for pointer in set(_shown) - set(shown):
            _leave(pointer)
        _shown = shown
    for window in windows:
        if window.as_pointer() in _shown and not input_policy.handler_running(window, IDENTITY):
            try:
                with bpy.context.temp_override(window=window):
                    bpy.ops.angelo.chat_launcher_input('INVOKE_DEFAULT')
            except RuntimeError as error:
                print("Angelo: chat launcher:", error)
    if _shown and not bpy.app.timers.is_registered(_idle):
        bpy.app.timers.register(_idle, first_interval=BLINK_EVERY)


def _update_safely():
    """``update`` where it must never raise (a move's end, Blender's notifier loop): a failure is printed."""
    try:
        update()
    except Exception as error:   # noqa: BLE001
        print("Angelo: chat launcher:", error)


def settled(window_pointer):
    """A chat move in this window ended (a dock or a mode switch, done or failed): a click's wait for it ends, and
    Angelo shows or goes now. Failed: Angelo comes back now; shown: ``update`` ends the wait too."""
    _opening.pop(window_pointer, None)
    _update_safely()


def _subscribe():
    """An area's editor type (the Editor Type menu's ``ui_type``, Shift+F keys, scripts) or a console's language
    changing may show or hide the chat (``console.is_chat``). Blender publishes both; a type-wide key hears all."""
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is None or bpy.app.background:
        return
    msgbus.clear_by_owner(_owner)
    for key in ((bpy.types.Area, "type"), (bpy.types.Area, "ui_type"), (bpy.types.SpaceConsole, "language")):
        msgbus.subscribe_rna(key=key, owner=_owner, args=(), notify=_update_safely)


def _leave(pointer):
    if pointer in _hover:
        _hover.discard(pointer)
        window = live_windows.find(bpy.context.window_manager, pointer, live=False)   # The cursor needs no screen.
        if window is not None:
            try:
                window.cursor_modal_restore()
            except (RuntimeError, TypeError):
                pass
    _pressed.discard(pointer)


def _idle():
    """Angelo's blink: a few frames every ``BLINK_EVERY`` seconds, in the views that draw cheaply, never with Reduce
    Motion. Ends when Angelo is nowhere (``update`` starts it again)."""
    global _blink, blinks
    if not _shown:
        _blink = None
        return None
    now = time.monotonic()
    if _blink is not None:
        if now - _blink <= BLINK_SECONDS + FRAME:
            _redraw(a for a in _shown.values() if _cost.get(a, 1.0) <= MAX_VIEW_COST)
            return FRAME
        _blink = None
        return BLINK_EVERY
    from . import marble_host
    cheap = [a for a in _shown.values() if _cost.get(a, 1.0) <= MAX_VIEW_COST]
    if not cheap or marble_host.reduced():
        return BLINK_EVERY
    _blink = now
    blinks += 1
    _redraw(cheap)
    return FRAME


def _open(window):
    """Show the chat after this event (showing may move areas), and hide Angelo at once."""
    pointer = window.as_pointer()
    _opening[pointer] = time.monotonic()
    _leave(pointer)
    _redraw([_shown.get(pointer)])

    def run(target_window):
        if target_window is not None:
            from . import chat_toggle
            with bpy.context.temp_override(window=target_window):
                if chat_toggle.chat_state(target_window, live_windows.every_window(bpy.context.window_manager))[0] == "hidden":
                    try:
                        bpy.ops.angelo.toggle_chat()
                    except RuntimeError as error:   # Refused (the toggle reported why): Angelo comes back below.
                        print("Angelo: chat launcher:", error)
        if not moving():   # Nothing under way: shown at once, or refused. A move ends the wait itself (``settled``).
            _opening.pop(pointer, None)
            update()
        return None
    # settle: a chat in a window between screens would read as hidden and a second one would open.
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, run, settle=True),
                            first_interval=0.01)


# -- drawing --------------------------------------------------------------------------------------------------
def _pre_view():
    area = bpy.context.area
    if area is not None:
        _started[area.as_pointer()] = time.perf_counter()


def _covered(area):
    """The welcome screen or a modal card is over this view: Angelo waits under neither."""
    from . import card_host, intro
    if intro.is_open() and intro._open.get("area") == area.as_pointer():
        return True
    card = card_host.active_card()
    return card is not None and getattr(card, "modal", False) and card_host.shown_on(area.as_pointer())


def _draw():
    global draws, draw_seconds, _view
    area, region, window = bpy.context.area, bpy.context.region, bpy.context.window
    if area is None or region is None or window is None:
        return
    drawn_at[area.as_pointer()] = time.monotonic()
    began = _started.pop(area.as_pointer(), None)
    if began is not None:
        _cost[area.as_pointer()] = time.perf_counter() - began
    pointer = window.as_pointer()
    if _shown.get(pointer) != area.as_pointer() or pointer in _opening or _covered(area) or moving():
        return
    from . import chat_toggle
    if chat_toggle.chat_state(window, live_windows.every_window(bpy.context.window_manager))[0] != "hidden":
        return   # Shown since the last watch (twice a second): never Angelo beside the chat.
    started = time.perf_counter()
    import gpu
    from . import canvas_controls, chat_toggle, chat_view, marble_draw
    from .marble_wait import Pose
    scale = chat_view.ui_scale()
    cx, cy, radius = corner((region.width, region.height), insets(area.regions, region), scale, side(window))
    _places[pointer] = (area.as_pointer(), region.x + cx, region.y + cy, radius)
    hover, pressed = pointer in _hover, pointer in _pressed
    diameter = 2 * radius
    gpu.state.blend_set('ALPHA')
    round_button.draw(cx, cy, radius, scale, hover, pressed)
    pose = Pose(0.0, LOOK if hover else 0.0, eye_open(time.monotonic(), _blink), 0.0)
    global last_error
    if last_error is None:
        try:
            marble_draw.draw_still(cx, cy, MARK * scale, pose)
        except Exception as error:   # Local: marble_draw._failed would stop the chat's own wait animation.
            last_error = str(error)
            print("Angelo: chat launcher draw:", error)
    if hover:
        if _view is None:
            from . import card_host
            _view = card_host._View()
        canvas_controls.tip(_view, (cx - radius, cy - radius, diameter, diameter), chat_toggle.button_text(), scale,
                            region.width, region.height)
    gpu.state.blend_set('NONE')
    spent = time.perf_counter() - started
    draws += 1
    draw_seconds += spent
    samples.append(spent)


# -- input ----------------------------------------------------------------------------------------------------
class ANGELO_OT_chat_launcher_input(Operator):
    bl_idname = "angelo.chat_launcher_input"   # IDENTITY: a literal, as every Angelo operator (test_native_chat_start_states).
    bl_label = "Show or Hide Chat"
    bl_description = "Show or hide the Angelo chat"
    bl_options = {'INTERNAL'} | input_policy.modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        self.window = context.window.as_pointer()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if self.window not in _shown:
            _leave(self.window)
            return {'FINISHED', 'PASS_THROUGH'}   # Angelo left this window: whatever this event is, it is not ours.
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}
        place = _places.get(self.window)
        if place is None or self.window in _opening or input_policy.canvas_blocking(window):
            if self.window in _hover:
                _leave(self.window)
                _redraw([place[0]] if place else [])
            return {'PASS_THROUGH'}   # Not drawn yet, already opening, or a real tool holds the window.
        over = inside(place, event.mouse_x, event.mouse_y)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if over != (self.window in _hover):
                if over:
                    _hover.add(self.window)
                    try:
                        window.cursor_modal_set('HAND')
                    except (RuntimeError, TypeError):
                        pass
                else:
                    _leave(self.window)
                _redraw([place[0]])
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE':
            if event.value in {'PRESS', 'DOUBLE_CLICK'} and over:
                _pressed.add(self.window)
                _redraw([place[0]])
                return {'RUNNING_MODAL'}   # Never a selection behind Angelo.
            if event.value == 'RELEASE' and self.window in _pressed:
                _pressed.discard(self.window)
                _redraw([place[0]])
                if over:
                    _open(window)
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        if event.type == 'RIGHTMOUSE' and over:
            return {'RUNNING_MODAL'}   # No context menu for what is behind Angelo.
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS' and over:
            _open(window)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


@persistent
def _load_post(_):
    """A file load drops modal handlers and replaces windows and areas: ``update`` starts again from nothing. The
    message bus starts empty too."""
    _shown.clear()
    _places.clear()
    _hover.clear()
    _pressed.clear()
    _opening.clear()
    _started.clear()
    _cost.clear()
    drawn_at.clear()
    if _ is not None:   # A load, not ``unregister``.
        _subscribe()


def register():
    bpy.utils.register_class(ANGELO_OT_chat_launcher_input)
    if not bpy.app.background:
        space = bpy.types.SpaceView3D
        _handlers.append((space, space.draw_handler_add(_pre_view, (), 'WINDOW', 'PRE_VIEW')))
        _handlers.append((space, space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')))
        _subscribe()
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    global _view
    if bpy.app.timers.is_registered(_idle):
        bpy.app.timers.unregister(_idle)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    msgbus = getattr(bpy, "msgbus", None)
    if msgbus is not None:
        msgbus.clear_by_owner(_owner)
    for pointer in list(_hover):
        _leave(pointer)
    _redraw(_shown.values())
    for space, handler in _handlers:
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    _load_post(None)
    _view = None
    round_button.release()
    bpy.utils.unregister_class(ANGELO_OT_chat_launcher_input)
