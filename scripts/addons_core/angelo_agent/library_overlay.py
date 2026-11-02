# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library in every 3D view: the search pill, its results and the docked browser (doc/angelo/library.rst).

``library_overlay_draw`` lays out and draws; this module keeps each view's state and takes input through one passive
modal handler per window (``angelo.library_input``, a canvas input in ``input_policy``): presses, wheel and keys over
the pill or panel are taken, everything else passes on, and typing is taken only while the search field has focus
(Escape, Return or a click elsewhere ends it). The pill shows in Object mode only.

* **Search**: every keystroke searches the local catalog (``library_catalog.Catalog.search``, milliseconds).
* **Add**: double-click or Return adds the selected item where the view looks (the surface at the view's centre,
  else the 3D cursor); a texture set goes on the selected objects; an HDRI becomes the world and the view shows it.
* **Drag**: a press that moves past Blender's drag threshold drags the item (its download starts at once); a release
  over any 3D view of the window drops it: a model stands on the surface under the pointer, a texture set goes on
  the object under it, an HDRI becomes the world. Elsewhere the drag is cancelled.

The import itself is ``library_host.fetch`` (worker) then ``library_import.import_item`` (main thread), one Undo step.
"""

import time

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import input_policy, library_actions as actions, library_overlay_draw as look, live_windows

IDENTITY = "angelo.library_input"
NOTICE_SECONDS = 4.0
_views = {}         # area pointer -> state
_handlers = []
_starting = set()
_drag = {}          # The window's drag: {"item", "window", "x", "y", "resolution", "started"}
draws = 0           # Fixtures.
last_error = None
events = []         # Fixtures: ("added" | "failed", item id, detail), newest last, bounded.


def enabled():
    return not bpy.app.background


def state_for(area):
    state = _views.get(area.as_pointer())
    if state is None:
        state = _views[area.as_pointer()] = {"mode": None, "query": "", "caret": 0, "kind": None, "scroll": 0.0,
                                             "selected": None, "resolution": {}, "hits": [], "focused": False}
    return state


def main_region(area):
    regions = [r for r in area.regions if r.type == 'WINDOW' and r.width > 1 and r.height > 1]
    return max(regions, key=lambda r: (r.width * r.height, r.x, r.y)) if regions else None


def _free_span(area, region, scale):
    """``(top, left, right)`` in region pixels: below headers and between toolbar and sidebar drawn over the view."""
    top, left, right = region.height, 0.0, float(region.width)
    for other in area.regions:
        if other.width <= 1 or other.height <= 1 or other == region:
            continue
        overlaps_y = other.y < region.y + region.height and other.y + other.height > region.y
        if other.type in {'HEADER', 'TOOL_HEADER'} and overlaps_y and other.y >= region.y + region.height / 2:
            top = min(top, other.y - region.y)
        elif other.type == 'TOOLS' and overlaps_y and other.x <= region.x + 4:
            left = max(left, other.x + other.width - region.x)
        elif other.type == 'UI' and overlaps_y and other.x + other.width >= region.x + region.width - 4:
            right = min(right, other.x - region.x)
    return top, left, right


def results(state):
    from . import library_host
    library = library_host.library()
    found, total = library.search(state["query"], look.active_kind(state), limit=400, category=state.get("category"))
    return library, found, total


# -- drawing --------------------------------------------------------------------------------------------------
def _draw():
    global draws, last_error
    context = bpy.context
    area, region, window = context.area, context.region, context.window
    if area is None or region is None or window is None or region.type != 'WINDOW' or not enabled():
        return
    if region != main_region(area) or context.mode != 'OBJECT' or _covered(area):
        _views.pop(area.as_pointer(), None) if region == main_region(area) else None
        return
    import gpu
    try:
        from . import chat_view
        scale = chat_view.ui_scale()
        state = state_for(area)
        state["hits"], state["window"] = [], window.as_pointer()
        state["region_x"], state["region_y"] = region.x, region.y
        top, left, right = _free_span(area, region, scale)
        dragging = bool(_drag) and _drag.get("window") == window.as_pointer()
        mode = None if dragging and state["mode"] == "search" else state["mode"]
        rects = look.layout(region.width, region.height, top, left, right, mode, scale)
        state["rects"] = rects
        if rects is None:
            return
        paint = look.Painter(scale)
        gpu.state.blend_set('ALPHA')
        count = None
        pending = state.get("progress")
        notice = state.get("notice")
        status = pending if pending else (1.0, notice[0]) \
            if notice and time.monotonic() - notice[1] < NOTICE_SECONDS else None
        if mode is not None:
            library, found, total = results(state)
            state["shown"] = [item["id"] for item in found]
            count = total
            look.draw_panel(paint, state, rects, library, found, total, status)
        look.draw_pill(paint, state, rects["pill"], state["focused"], count)
        if status and "footer" not in rects:
            look.draw_progress(paint, look.progress_rect(rects["pill"], scale), *status)
        if dragging and region.x <= _drag["x"] < region.x + region.width and region.y <= _drag["y"] < region.y + region.height:
            from . import library_host
            item = _drag["item"]
            look.draw_ghost(paint, _drag["x"] - region.x, _drag["y"] - region.y, library_host.library().texture(item),
                            item["title"])
        draws += 1
        _ensure_input(window)
    except Exception as error:   # A failed overlay never breaks the view's own drawing.
        last_error = str(error)
        print("Angelo Library overlay draw:", error)
    finally:
        gpu.state.scissor_test_set(False)
        gpu.state.blend_set('NONE')


def _covered(area):
    try:
        from . import chat_launcher
        return chat_launcher._covered(area)
    except (ImportError, AttributeError):
        return False


def _ensure_input(window):
    pointer = window.as_pointer()
    if pointer in _starting or input_policy.handler_running(window, IDENTITY):
        return
    _starting.add(pointer)

    def start(target):
        _starting.discard(pointer)
        if target is None or input_policy.handler_running(target, IDENTITY):
            return None
        try:
            with bpy.context.temp_override(window=target):
                bpy.ops.angelo.library_input('INVOKE_DEFAULT')
        except RuntimeError as error:
            print("Angelo Library input:", error)
        return None
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, start), first_interval=0.0)


def redraw_all():
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _area_at(window, x, y):
    for area in live_windows.areas(window):
        if area.type == 'VIEW_3D' and area.x <= x < area.x + area.width and area.y <= y < area.y + area.height:
            region = main_region(area)
            if region is not None and region.x <= x < region.x + region.width and region.y <= y < region.y + region.height:
                return area, region
    return None, None


def _hit(state, x, y):
    for rect, action, value in reversed(state.get("hits") or []):
        if rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
            return action, value
    return None


def _inside_panel(state, x, y):
    rects = state.get("rects") or {}
    for key in ("panel", "pill"):
        rect = rects.get(key) if state["mode"] or key == "pill" else None
        if rect and rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
            return True
    return False


# -- actions --------------------------------------------------------------------------------------------------
def open_overlay(area, mode="search", lock=None):
    """Open the search (or the docked browser) in ``area`` with the field focused; loads the catalog. ``lock``: the
    one kind a surface shows (Material properties: materials; World properties: HDRIs), None in the 3D view."""
    from . import library_host
    library_host.library().ensure_catalog()
    state = state_for(area)
    if lock != state.get("lock"):
        state.update(category=None, scroll=0.0)
    state.update(mode=mode, focused=True, caret=len(state["query"]), lock=lock)
    area.tag_redraw()


def close(state):
    state.update(mode=None, focused=False, scroll=0.0, lock=None)


def click(window, area, region, state, action, value, double=False):
    from . import library_host
    library = library_host.library()
    if action == "pill":
        if state["mode"] is None:
            open_overlay(area)
        state["focused"] = True
    elif action == "kind":
        state.update(kind=value, category=None, scroll=0.0, focused=True)
    elif action == "category":
        state.update(category=value, scroll=0.0, focused=True)
    elif action == "browse":
        state.update(mode=None if state["mode"] == "browse" else "browse", scroll=0.0)
        state["focused"] = state["mode"] is not None
    elif action == "retry":
        library.ensure_catalog(refresh=True)
    elif action == "resolution":
        if state.get("selected"):
            state["resolution"][state["selected"]] = value
    elif action == "credits":
        bpy.ops.angelo.library_credits('INVOKE_DEFAULT')
    elif action == "tile":
        state["selected"] = value
        item = library.get(value)
        if double and item is not None:
            actions.add(window, area, region, item)
            if state["mode"] == "search":
                close(state)
    area.tag_redraw()


# -- input ----------------------------------------------------------------------------------------------------
class ANGELO_OT_library_input(Operator):
    bl_idname = "angelo.library_input"   # IDENTITY
    bl_label = "Angelo Library"
    bl_description = "Search the Angelo Library and drop its assets into the scene"
    bl_options = {'INTERNAL'} | input_policy.modal_options(bl_idname)

    def invoke(self, context, event):
        self.window = context.window.as_pointer()
        self.press = None
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if input_policy.overlay_passes(event, context.window_manager):   # Timers, window events, the quit key.
            return {'PASS_THROUGH'}
        if not any(s.get("window") == self.window for s in _views.values()) and not _drag:
            return {'FINISHED', 'PASS_THROUGH'}
        if _drag and _drag.get("window") == self.window:
            return self.dragging(context, window, event)
        if input_policy.canvas_blocking(window):
            return {'PASS_THROUGH'}
        area, region = _area_at(window, event.mouse_x, event.mouse_y)
        state = _views.get(area.as_pointer()) if area is not None else None
        x, y = (event.mouse_x - region.x, event.mouse_y - region.y) if region is not None else (0, 0)
        over = state is not None and _inside_panel(state, x, y)
        focused = next((s for s in _views.values() if s.get("focused") and s.get("window") == self.window), None)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            return self.moved(context, window, area, region, state, x, y, event)
        if event.type == 'LEFTMOUSE' and event.value in {'PRESS', 'DOUBLE_CLICK'}:
            if not over:
                for other in _views.values():
                    other["focused"] = False
                    if other.get("mode") == "search":
                        close(other)
                redraw_all()
                return {'PASS_THROUGH'}
            target = _hit(state, x, y)
            if target and target[0] == "tile":
                self.press = (area.as_pointer(), target[1], event.mouse_x, event.mouse_y)
            if target:
                click(window, area, region, state, target[0], target[1], double=event.value == 'DOUBLE_CLICK')
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.press = None
            return {'RUNNING_MODAL'} if over else {'PASS_THROUGH'}
        if over and event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'} and state.get("mode"):
            from . import scroll_input
            self.scroll(state, -scroll_input.view_up(event, 48))
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if over and event.type in {'RIGHTMOUSE', 'MIDDLEMOUSE'}:
            return {'RUNNING_MODAL'}
        if focused is not None and event.value == 'PRESS':
            return self.key(context, window, focused, event)
        return {'PASS_THROUGH'}

    def moved(self, context, window, area, region, state, x, y, event):
        if self.press is not None:
            pointer, identity, px, py = self.press
            threshold = context.preferences.inputs.drag_threshold_mouse * context.preferences.system.ui_scale
            if max(abs(event.mouse_x - px), abs(event.mouse_y - py)) >= threshold:
                from . import library_host
                self.press = None
                item = library_host.library().get(identity)
                if item is not None:
                    begin_drag(window, pointer, item)
                    _drag["x"], _drag["y"] = event.mouse_x, event.mouse_y
                    redraw_all()
                    return {'RUNNING_MODAL'}
        if state is not None:
            target = _hit(state, x, y) if _inside_panel(state, x, y) else None
            hover = target if target is None else (target[0], target[1])
            if state.get("hover") != hover:
                state["hover"] = hover
                if area is not None:
                    area.tag_redraw()
        return {'PASS_THROUGH'}

    def scroll(self, state, step):
        rects = state.get("rects") or {}
        grid = rects.get("grid")
        if not grid:
            return
        from . import chat_view
        _library, found, _total = results(state)
        limit = max(0.0, look.content_height(grid[2], len(found), chat_view.ui_scale()) - grid[3])
        state["scroll"] = max(0.0, min(limit, state.get("scroll", 0.0) + step))

    def key(self, context, window, state, event):
        query, caret = state["query"], state["caret"]
        modifier = event.ctrl or event.oskey
        kind = event.type
        if kind == 'ESC':
            if state["mode"] == "search" or not query:
                close(state)
            state["focused"] = False
        elif kind in {'RET', 'NUMPAD_ENTER'}:
            self.add_selected(window, state)
        elif kind in {'UP_ARROW', 'DOWN_ARROW'}:
            shown = state.get("shown") or []
            if shown:
                index = shown.index(state["selected"]) if state.get("selected") in shown else -1
                index = max(0, min(len(shown) - 1, index + (1 if kind == 'DOWN_ARROW' else -1)))
                state["selected"] = shown[index]
        elif kind == 'BACK_SPACE':
            query, caret = (("", 0) if modifier else (query[:caret - 1] + query[caret:], caret - 1)) if caret else (query, 0)
        elif kind == 'DEL':
            query = query[:caret] + query[caret + 1:]
        elif kind in {'LEFT_ARROW', 'RIGHT_ARROW', 'HOME', 'END'}:
            caret = {'LEFT_ARROW': max(0, caret - 1), 'RIGHT_ARROW': min(len(query), caret + 1), 'HOME': 0,
                     'END': len(query)}[kind]
        elif modifier and kind == 'V':
            text = " ".join(context.window_manager.clipboard.split())[:200]
            query, caret = query[:caret] + text + query[caret:], caret + len(text)
        elif modifier and kind == 'A':
            caret = len(query)
        elif event.unicode and not modifier and not event.alt and event.unicode.isprintable():
            query, caret = query[:caret] + event.unicode + query[caret:], caret + len(event.unicode)
        elif kind in {'LEFT_SHIFT', 'RIGHT_SHIFT', 'LEFT_CTRL', 'RIGHT_CTRL', 'OSKEY', 'LEFT_ALT', 'RIGHT_ALT'}:
            return {'PASS_THROUGH'}
        if query != state["query"]:
            state.update(query=query[:200], scroll=0.0, selected=None)
        state["caret"] = max(0, min(len(state["query"]), caret))
        redraw_all()
        return {'RUNNING_MODAL'}   # Never a shortcut behind a focused search field.

    def add_selected(self, window, state):
        from . import library_host
        shown = state.get("shown") or []
        identity = state.get("selected") or (shown[0] if shown else None)
        item = library_host.library().get(identity) if identity else None
        live = actions._live(window.as_pointer(), next((p for p, s in _views.items() if s is state), 0))
        if item is None or live is None:
            return
        actions.add(window, live[1], live[2], item)
        if state["mode"] == "search":
            close(state)

    def dragging(self, context, window, event):
        _drag["x"], _drag["y"] = event.mouse_x, event.mouse_y
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            redraw_all()
            return {'RUNNING_MODAL'}
        if event.type == 'ESC' or event.type == 'RIGHTMOUSE':
            _end_drag(window)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            item = _drag["item"]
            source = _views.get(_drag.get("area"))
            _end_drag(window)
            area, region = _area_at(window, event.mouse_x, event.mouse_y)
            state = _views.get(area.as_pointer()) if area is not None else None
            if area is None or (state is not None and _inside_panel(state, event.mouse_x - region.x,
                                                                    event.mouse_y - region.y)):
                return {'RUNNING_MODAL'}   # Dropped back on the panel, or outside every 3D view: nothing happens.
            if source is not None and area.as_pointer() != _drag.get("area"):
                state_for(area)["resolution"].update(source["resolution"])
            actions.add(window, area, region, item, at=(event.mouse_x - region.x, event.mouse_y - region.y))
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}


def begin_drag(window, area_pointer, item):
    from . import library_host
    state = _views.get(area_pointer) or {}
    library_host.library().prefetch(item, state.get("resolution", {}).get(item["id"]))
    _drag.update(item=item, window=window.as_pointer(), area=area_pointer, x=0, y=0, started=time.monotonic())
    try:
        window.cursor_modal_set('HAND')
    except (RuntimeError, TypeError):
        pass


def _end_drag(window):
    _drag.clear()
    try:
        window.cursor_modal_restore()
    except (RuntimeError, TypeError):
        pass
    redraw_all()


@persistent
def _load_post(_):
    _views.clear()
    _drag.clear()
    _starting.clear()


def register():
    bpy.utils.register_class(ANGELO_OT_library_input)
    if not bpy.app.background:
        _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL'))
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    for handler in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    _load_post(None)
    bpy.utils.unregister_class(ANGELO_OT_library_input)
