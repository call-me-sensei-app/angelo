# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Input for the session rail and the live view: one passive modal handler per window.

* **The rail:** the pointer over a tile lifts it (a hand cursor); a click switches to that session (``switch``):
  its chat in the chat area, its scene full screen. Nothing on the rail reaches the 3D view behind it.
* **A live view** (``live_view_draw``): it navigates itself (``live_nav``); a click names the object under the
  pointer (``live_view_draw.inspect``); window-wide commands that never edit (``live_view_nav.ALLOWED``), keys the
  focused chat edits with, Blender's area edges and Angelo's own overlays pass; anything else is held and the view
  says once that it is read-only. Whatever passes still never reaches the 3D view beneath: its input is held
  natively while the live view shows (``live_view_draw.renew``), so these exceptions are what the live view lets
  others take, never a way to the document no one sees (break-it P1-3 H1, H2, M3).
* **Everything else passes**, and while a real tool holds the window (``input_policy.canvas_blocking``) the
  handler takes nothing, except over a live view: a tool in front of this handler had each event first, so what
  reaches it there is nobody's (P1-3 H1). Every event it sees notes the artist's activity for the reload schedule
  (``parallel_sessions.note_input``).

A watch (twice a second while concurrent sessions exist) starts the handler in every window, follows a session
switched from anywhere (the chat's own sessions menu included: ``live_view_draw.follow``; each press follows too),
renews the input hold and keeps the pictures' timer going. Never in a background run.
"""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import input_policy, live_windows, scroll_input, session_rail_controls as rail_controls
from .session_rail import GAP as _GAP, TEXT_BLOCK as _TEXT_BLOCK
from .session_rail_guard import ends_presses as _ends_presses, fail_closed as _fail_closed

IDENTITY = "angelo.session_rail_input"
BUTTONS = frozenset({'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE'})
MOVES = frozenset({'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'})
MODIFIER_KEYS = frozenset({'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL', 'LEFT_ALT', 'RIGHT_ALT', 'OSKEY',
                           'APP', 'HYPER'})   # Half of a gesture or a shortcut: never "read-only" by themselves.
WATCH = 0.5
RAIL_STEP = float(_TEXT_BLOCK + _GAP)   # Swipe (interface pixels at a scale of 1) per session a paged rail moves: a
# compact tile and its gap, so tiles follow the finger (P1-19 L10); one wheel notch moves one session.
KEYMAP_REFRESH = 2.0   # Seconds between reads of the artist's keymap (a rebinding counts within that, P1-3 L6).
_handlers = []
_keymap_items = None   # live_view_nav items of the artist's keymap, read once per keymap change (``_items``).
_keymap_key = None


def _items():
    """The allowed window-wide items of the artist's keymap (read again when its size changes, and at least every
    ``KEYMAP_REFRESH`` seconds: a rebinding keeps the size)."""
    global _keymap_items, _keymap_key
    from . import live_view_nav
    keyconfig = bpy.context.window_manager.keyconfigs.user
    key = tuple(len(keyconfig.keymaps[name].keymap_items) if name in keyconfig.keymaps else 0
                for name in live_view_nav.GLOBAL_KEYMAPS) + (int(time.monotonic() // KEYMAP_REFRESH),)
    if key != _keymap_key:
        _keymap_items, _keymap_key = live_view_nav.items_from(keyconfig), key
    return _keymap_items


def switch(session_id, window=None):
    """Make ``session_id`` the active session: its chat shown and its scene full screen. Only what is displayed
    changes (the maintainer, 2026-10-25): a worker's part is drawn over the big view (``live_view_draw``, never the
    window's scene), and the lead's tile brings the lead's own scene back into the main window (``document_scene.show``:
    the person's click is the one route that sets the window's scene; break-it E2E-20 R5-M-1)."""
    from . import document_scene, live_view_draw, parallel_sessions, rail_pictures, ui
    if session_id not in parallel_sessions.sessions():
        return False
    if ui._sessions is None or session_id != ui._sessions.active:
        ui.show_session(session_id)
    parallel_sessions.note_active(session_id)
    if not parallel_sessions.is_worker(session_id) and session_id in ui._controllers:
        document_scene.show(ui._controllers[session_id], ui.main_window())
    for item in [window] if window is not None else live_windows.windows(bpy.context.window_manager):
        live_view_draw.follow(item)
        _redraw(item)
    parallel_sessions.lead_changed()   # The lead's picture, as it was left.
    rail_pictures.ensure_timer()
    return True


def dismiss(session_id, window=None):
    """Remove an ended worker's tile and free its live part (its chat and part file stay)."""
    from . import parallel_sessions
    if not (parallel_sessions.ended(session_id) or parallel_sessions.is_merged(session_id)):
        return False
    return remove(session_id, window)


def remove(session_id, window=None):
    """End a worker's tile and free its live part (its chat and part file stay): after a dismiss, or a merge
    (``parallel_sessions.merged``). The artist goes back to the main session when it was the one shown."""
    from . import parallel_sessions, rail_pictures, ui
    if session_id not in parallel_sessions.sessions():
        return False
    if ui._sessions is not None and ui._sessions.active == session_id and parallel_sessions.lead is not None:
        switch(parallel_sessions.lead, window)
    parallel_sessions.dismiss(session_id)
    rail_pictures.forget(session_id)
    from . import live_view_draw
    live_view_draw.forget(session_id)
    for item in [window] if window is not None else live_windows.windows(bpy.context.window_manager):
        _redraw(item)
    return True


def _redraw(window):
    for area in live_windows.areas(window):
        if area.type in {'VIEW_3D', 'CONSOLE'}:
            area.tag_redraw()


def rail_region(area):
    """The region the rail is drawn in: the area's main 3D-view region (the top-left one in quad view)."""
    regions = [r for r in area.regions if r.type == 'WINDOW']
    return min(regions, key=lambda r: (-(r.y + r.height), r.x), default=None)


def _inside(region, x, y):
    return region.x <= x < region.x + region.width and region.y <= y < region.y + region.height


def _at(window, x, y):
    """``(area, region)`` under window pixel (x, y) in the target 3D view: any of its regions (quad view's four,
    the header, toolbar and sidebar too, break-it P1-1 M1); ``(area, None)`` outside it."""
    from . import live_view_draw
    area = live_view_draw.target(window)
    if area is None or not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
        return area, None
    regions = [r for r in area.regions if r.width > 1 and r.height > 1 and _inside(r, x, y)]
    # Regions drawn over the view (toolbar, sidebar, header) come before the view itself.
    return area, next((r for r in regions if r.type != 'WINDOW'), regions[0] if regions else None)


def _pointer(event):
    return event.type.endswith("MOUSE") or event.type.startswith(("WHEEL", "TRACKPAD", "NDOF"))


def _overlay_under(window, area, event):
    """Angelo's own overlays in that view take what is theirs: the corner launcher and a corner card the pointer's
    events on themselves, a modal card (or one with a text field) its keys too, the welcome everything (P1-3 M3)."""
    from . import card_host, chat_launcher, intro
    x, y, pointer = event.mouse_x, event.mouse_y, _pointer(event)
    place = chat_launcher._places.get(window.as_pointer())
    if pointer and place is not None and place[0] == area.as_pointer() and chat_launcher.inside(place, x, y):
        return True
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is not None and card_host.takes_input(area.as_pointer(), x - region.x, y - region.y):
        if pointer or card_host.takes_keys(area.as_pointer()):
            return True   # A modal card anywhere, a corner card on its own rectangle (break-it P1-2 M1).
    return intro.is_open() and intro._open.get("area") == area.as_pointer()


EDGE = 6   # Pixels (at a UI scale of 1) along an area's border where Blender resizes, splits and joins areas.


def _on_edge(window, area, x, y):
    """Within the band of a side the area shares with another area: the window's own border is no edge (Blender
    finds none there, P1-3 M3)."""
    from . import chat_view
    band = EDGE * chat_view.ui_scale()
    areas = live_windows.areas(window) or [area]
    left, bottom = min(a.x for a in areas), min(a.y for a in areas)
    right, top = max(a.x + a.width for a in areas), max(a.y + a.height for a in areas)
    return ((area.x > left and x - area.x < band) or (area.x + area.width < right and area.x + area.width - x <= band)
            or (area.y > bottom and y - area.y < band) or (area.y + area.height < top
                                                           and area.y + area.height - y <= band))


def _chat_focused(window):
    from . import chat_view, console
    return any(console.is_chat(area) and chat_view._views.get(area.as_pointer(), {}).get("focused")
               for area in live_windows.areas(window))


def _blur_chat(window):
    """The chat's field gives up keyboard focus, as a click elsewhere takes it (the chat's own handler, behind this
    one, never sees pointer use a live view takes; it drops its focused area at its next event)."""
    from . import chat_view, console
    for area in live_windows.areas(window):
        state = chat_view._views.get(area.as_pointer())
        if console.is_chat(area) and state is not None and state.get("focused"):
            state["focused"], state["blur_request"] = False, True
            area.tag_redraw()


class ANGELO_OT_session_rail_input(Operator):
    bl_idname = "angelo.session_rail_input"   # IDENTITY: a literal, as every Angelo operator.
    bl_label = "Sessions"
    bl_description = "Switch between Angelo's concurrent sessions"
    bl_options = {'INTERNAL'} | input_policy.modal_options(bl_idname)   # Auto-save never waits for it (input_policy).

    def invoke(self, context, event):
        self.window = context.window.as_pointer()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """The handler ends with its window: none of its hover or press stays (break-it P1-16 L3; ``unregister``
        clears every window's)."""
        from . import session_rail_draw as drawn
        for table in (drawn.hover, drawn.pressed, drawn.pan):
            table.pop(getattr(self, "window", None), None)

    @_fail_closed
    @_ends_presses
    def modal(self, context, event):
        from . import live_nav, live_view_draw, parallel_sessions, session_rail, session_rail_draw as drawn
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if not parallel_sessions.sessions():
            self._leave(window)
            return {'FINISHED', 'PASS_THROUGH'}
        if event.type == 'WINDOW_DEACTIVATE':
            live_view_draw.navigation.drag = live_nav.Drag()   # Its release goes to another window (P1-3 M2).
            self._leave(window)   # And no hover or press stays held meanwhile (P1-15 L1).
        if input_policy.not_input(event):
            return {'PASS_THROUGH'}
        if input_policy.refresh_move(event):
            self._drag_to(context, window, event)   # A move with no motion of its own still moves a drag from its own
            return {'PASS_THROUGH'}                 # last point (event_simulate's have none), and is passed on.
        parallel_sessions.note_input()
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}
        if event.type not in MOVES:
            live_view_draw.follow(window)   # A switch from anywhere shows (and holds) before its first press.
            live_view_draw.renew(window)    # The lease follows input too, before it is handled (P1-4 L10).
        worker = live_view_draw.active_worker()
        target = live_view_draw.target(window)
        # Live only while the 3D view beneath holds its input: the picture, this handler and Blender's own filter
        # follow one fact, so a lapsed lease gives the artist back their document whole (P1-4 M2).
        live = (worker is not None and live_view_draw.shown == worker and target is not None
                and live_view_draw.holding(window, target))
        drag = live_view_draw.navigation.drag
        if drag.mode:   # First: a drag gets every event, wherever the pointer went, whatever started meanwhile.
            elsewhere = (event.type in BUTTONS and event.value == 'PRESS' and target is not None
                         and _at(window, event.mouse_x, event.mouse_y)[1] is None)
            if live and not elsewhere:
                main = rail_region(target)
                return self._live(context, window, target, main, worker, event, event.mouse_x - main.x,
                                  event.mouse_y - main.y)
            live_view_draw.navigation.drag = live_nav.Drag()   # A press elsewhere ends it and is its own (P1-4 L3).
        area, region = _at(window, event.mouse_x, event.mouse_y)
        if not (live and region is not None) and input_policy.canvas_blocking(window):
            self._leave(window, cursor=False)   # Another modal has the input: no hover or press holds the rail (P1-16
            # L3), and the cursor stays the one that modal set (P1-17 L5).
            return {'PASS_THROUGH'}
        if region is None or _overlay_under(window, area, event):
            self._leave(window)
            return {'PASS_THROUGH'}
        taken = rail_controls.restore_event(window, event)   # A minimized panel's round button (rail_minimized).
        if taken is not None:
            return taken
        main = rail_region(area)
        x, y = event.mouse_x - main.x, event.mouse_y - main.y
        placed = drawn.last.get(self.window)
        plan = placed[1] if placed and placed[0] == area.as_pointer() else None
        on_rail = region == main and plan is not None and session_rail.covers(plan, x, y)
        found = session_rail.hit(plan, x, y) if on_rail else None
        x, y = event.mouse_x - region.x, event.mouse_y - region.y   # From here: the region under the pointer.
        if on_rail and plan.paged and event.type in scroll_input.SCROLL_EVENTS:   # Its own scroll once it pages (P2):
            # a session per wheel notch or per RAIL_STEP of swipe, the way Blender's views follow it (P1-18 H1); a
            # strip's along it, the wheel and both trackpad axes (rail_controls.along).
            travel, unit = ((-scroll_input.view_up(event, RAIL_STEP, plan.scale), RAIL_STEP)
                            if plan.orientation == "VERTICAL" else rail_controls.along(event, plan))
            if drawn.scroll_pan(self.window, travel, unit):
                self._leave(window)   # Another tile under the pointer now: its hover and cursor go (P1-19 L1).
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type in MOVES:
            under = session_rail.hover_key(found)
            if under != drawn.hover.get(self.window):
                drawn.hover[self.window] = under
                self._cursor(window, under is not None)
                area.tag_redraw()
            return {'PASS_THROUGH'}
        if on_rail and event.type in BUTTONS:   # The rail's own: never a selection or a menu behind it.
            if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and found:
                drawn.pressed[self.window] = found
                area.tag_redraw()
            elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if found and drawn.pressed.get(self.window) == found:   # The same action too (P1-18 L8).
                    if found[0] == "dismiss":
                        dismiss(found[1], window)
                    elif found[0] in drawn.PAGING:
                        drawn.scroll_by(self.window, found[1])
                        self._leave(window)
                    elif found[0] == "minimize":
                        rail_controls.minimize(self, window)
                    else:
                        switch(found[1], window)
                drawn.pressed.pop(self.window, None)
                area.tag_redraw()
            return {'RUNNING_MODAL'}
        if not live:
            return {'PASS_THROUGH'}
        return self._live(context, window, area, region, worker, event, x, y)

    @staticmethod
    def _drag_to(context, window, event):
        """A live view's drag follows the pointer to ``event``'s position, measured from where the drag last was:
        never from the event's own previous position, which a motionless move does not carry."""
        from . import live_nav, live_view_draw
        drag, worker = live_view_draw.navigation.drag, live_view_draw.active_worker()
        target = live_view_draw.target(window)
        if not drag.mode or worker is None or live_view_draw.shown != worker or target is None \
                or not live_view_draw.holding(window, target):
            return
        main = rail_region(target)
        settings = live_nav.settings_from(context.preferences, target.spaces.active)
        if live_nav.handle(event, live_view_draw.view_of(worker), drag, settings, (main.width, main.height),
                           lambda: None):
            target.tag_redraw()

    def _live(self, context, window, area, region, worker, event, x, y):
        """An event over a live view: its own navigation and inspection, or held; the few it hands on reach no
        handler of the 3D view beneath (its input is held, ``live_view_draw.renew``)."""
        from . import live_nav, live_view_draw, live_view_nav
        drag = live_view_draw.navigation.drag
        if not drag.mode and event.type in BUTTONS | MOVES and _on_edge(window, area, event.mouse_x, event.mouse_y):
            return {'PASS_THROUGH'}   # Resizing, splitting or joining areas: Blender's own, never the view's.
        keyboard = not _pointer(event)
        typing = keyboard and _chat_focused(window) and not input_policy.follows_pointer(event.type)
        if typing and input_policy.edits_field(event):
            if event.alt and event.type not in input_policy.FIELD_KEYS:
                return {'RUNNING_MODAL'}   # The chat inserts no Option character off its own area: nobody's.
            return {'PASS_THROUGH'}   # Typing in the chat with the pointer resting over the view.
        if live_view_nav.allowed(live_view_nav.event_from(event), _items()):
            return {'PASS_THROUGH'}
        if event.type in MOVES and not drag.mode:
            return {'PASS_THROUGH'}   # Hover only (the rail and cursor shapes); the view beneath takes none.
        space = area.spaces.active
        settings = live_nav.settings_from(context.preferences, space)
        view = live_view_draw.view_of(worker)
        main = rail_region(area)

        def frame_part():
            from . import live_view
            found = live_view.bounds(worker)
            if found is not None:
                live_nav.frame(view, found, max(1, main.width) / max(1, main.height), settings.lens)
        if not keyboard and event.value == 'PRESS':
            _blur_chat(window)   # Pointer use over the view takes keyboard focus there (P1-3 L2).
        if region.type == 'WINDOW' and live_nav.handle(event, view, drag, settings, (region.width, region.height),
                                                       frame_part):
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and region.type == 'WINDOW':
            if event.value == 'PRESS':
                live_view_draw.inspect(worker, area, region, x, y)
        elif event.value == 'PRESS' and event.type not in MODIFIER_KEYS and not typing:
            live_view_draw.held(area)   # A key, a button or a header, toolbar or sidebar click: held, said.
        return {'RUNNING_MODAL'}

    def _cursor(self, window, hand):
        try:
            if hand:
                window.cursor_modal_set('HAND')
            else:
                window.cursor_modal_restore()
        except (RuntimeError, TypeError):
            pass

    def _leave(self, window, cursor=True):
        from . import session_rail_draw as drawn
        rail_controls.leave(window)
        if drawn.hover.pop(self.window, None) is not None:
            if cursor:
                self._cursor(window, False)
            self._redraw(window)
        self._unpress(window)

    def _unpress(self, window):
        from . import session_rail_draw as drawn
        if drawn.pressed.pop(self.window, None) is not None:
            self._redraw(window)

    @staticmethod
    def _redraw(window):
        """The rail's view drawn again: a tile's hover or press tint gone, and the rail free to follow the text."""
        from . import live_view_draw
        area = live_view_draw.target(window)
        if area is not None:
            area.tag_redraw()


def _watch():
    """Starts the handler where it is missing, follows the active session and keeps the pictures going."""
    from . import live_view_draw, parallel_sessions, rail_pictures
    if not parallel_sessions.sessions():
        return WATCH
    for window in live_windows.windows(bpy.context.window_manager):
        try:
            live_view_draw.follow(window)
            live_view_draw.renew(window)   # The input hold's lease, while the live view shows.
            if live_view_draw.target(window) is not None and not input_policy.handler_running(window, IDENTITY):
                with bpy.context.temp_override(window=window):
                    bpy.ops.angelo.session_rail_input('INVOKE_DEFAULT')
        except Exception as error:   # noqa: BLE001 - Blender drops a timer that raises; this one keeps watching.
            print("Angelo: session rail:", error)
    try:
        parallel_sessions.note_active(parallel_sessions._active())
        rail_pictures.ensure_timer()
    except Exception as error:   # noqa: BLE001
        print("Angelo: session rail:", error)
    return WATCH


@persistent
def _depsgraph_changed(_scene, depsgraph):
    """Angelo's own scene changed in a way its Solid picture shows (``rail_changes.visual_update``; a selection does
    not): the lead's picture is out of date (redrawn at most once a second)."""
    from . import parallel_sessions, rail_changes   # The file's change count is ``parallel_sessions``' own handler.
    if parallel_sessions.lead is not None and rail_changes.visual_update(depsgraph, parallel_sessions.lead):
        parallel_sessions.lead_changed()


@persistent
def _load_post(_):
    from . import live_view_draw, session_rail_draw
    session_rail_draw.last.clear()
    session_rail_draw.hover.clear()
    session_rail_draw.pressed.clear()
    session_rail_draw.text_kept.clear()
    session_rail_draw.scroll.clear()
    session_rail_draw.pan.clear()
    live_view_draw.release()
    rail_controls.release()


_registered = [False]


@persistent
def _document_loaded(_):
    """A load replaced Angelo's document: every lead it invalidated stops its workers (break-it E2E-18 M-3)."""
    from . import worker_queue
    worker_queue.document_replaced()


def register():
    """The rail, live views and their timers, only where parallel edit exists: on a machine below the bar (or launched
    without it) none of it is registered (break-it P1-5 O1)."""
    from . import live_view, live_view_draw, live_view_nav, parallel_access, parallel_sessions, session_rail_draw
    from . import parallel_capability, worker_sessions
    forced = " (forced at launch)" if parallel_access._forced() in {"0", "1"} else ""
    print("Angelo: parallel edit %s%s: %s" % ("on" if parallel_access.enabled() else "off", forced,
                                             parallel_capability.describe(parallel_capability.current())), flush=True)
    if not parallel_access.enabled():
        return
    _registered[0] = True
    worker_sessions.reset_for_register()
    parallel_sessions.register()
    bpy.utils.register_class(ANGELO_OT_session_rail_input)
    bpy.app.handlers.load_post.append(_document_loaded)   # Background suites too: workers run there as well.
    if bpy.app.background:
        return
    try:
        live_view.allow(sorted(live_view_nav.ALLOWED))   # What the window's key maps may run over a held view.
    except (ImportError, AttributeError):
        pass
    space = bpy.types.SpaceView3D
    from . import rail_redraw
    _handlers.append(space.draw_handler_add(rail_redraw.pre_view, (), 'WINDOW', 'PRE_VIEW'))   # Its draw's cost.
    # The live view first, then the rail over it: both after the 3D view's own drawing.
    _handlers.append(space.draw_handler_add(live_view_draw.draw, (), 'WINDOW', 'POST_PIXEL'))
    _handlers.append(space.draw_handler_add(session_rail_draw.draw, (), 'WINDOW', 'POST_PIXEL'))
    bpy.app.handlers.load_post.append(_load_post)
    bpy.app.handlers.depsgraph_update_post.append(_depsgraph_changed)
    bpy.app.timers.register(_watch, first_interval=WATCH, persistent=True)


def unregister():
    from . import live_view_draw, parallel_sessions, rail_pictures, rail_redraw, session_rail_draw, worker_sessions
    if not _registered[0]:
        return
    _registered[0] = False
    worker_sessions.release()
    if bpy.app.timers.is_registered(_watch):
        bpy.app.timers.unregister(_watch)
    for name, handler in (("load_post", _load_post), ("load_post", _document_loaded),
                          ("depsgraph_update_post", _depsgraph_changed)):
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    for handler in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    rail_pictures.release()
    rail_redraw.release()
    live_view_draw.release()
    rail_controls.release()
    for table in (session_rail_draw.hover, session_rail_draw.pressed, session_rail_draw.text_kept,
                  session_rail_draw.scroll, session_rail_draw.pan):
        table.clear()
    bpy.utils.unregister_class(ANGELO_OT_session_rail_input)
    parallel_sessions.unregister()
