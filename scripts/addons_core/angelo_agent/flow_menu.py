# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own dropdown for Generation Flows: the Flows popup (Director) and a flow's Add step menu.

1.0.5 review: Flows opened Blender's stock dropdown (small grey text, a node-tree icon per item) and a flow's Add
step Blender's node Add menu. ``doc/angelo/studio_entry_points.rst`` (section 3) makes this popup Director's Flows:
the top bar's button, an Asset Browser or Shelf pill and the welcome's slide open it under themselves. It lists the
Generation Flows rows (``flow_open.menu_rows``, the registry the + menu and the start page draw), New Flow first,
then *Start from a template*, then *Your flows*, with the + menu's row (``chat_add_menu.draw_row``). The flow bar
(``flow_window``) opens the same dropdown for Add step with the step types (``flow_window.step_rows``); a step row
adds that node at the pointer for the person to place, as Shift+A does.

* **Look** (section 4, popups): ``card_kit.CARD`` fill, ``RADIUS_PANEL``, the + menu's rows at ``ROW_HEIGHT`` and its
  section labels, at least ``MIN_WIDTH`` wide, why lines wrapped and never cut. A row with no glyph (a template, one
  of your flows: section 2) has no tile. Mirrored in a right-to-left language, like the chat.
* **Where**: on the editor below the button (the top bar's, which ``screen.areas`` does not list) or under the
  pointer's pill, hanging ``top`` pixels below that editor's top edge, kept inside it; it scrolls when the editor is
  shorter than the list.
* **Input**: the opener's operator stays modal while the menu is open and holds the window's input, as a menu does
  (``input_policy.HOLDING_TOOLS``): a click on a row runs it with an Undo step; a click anywhere else, Escape or the
  button again closes it; arrow keys move a ``FOCUS`` ring, Return opens; the wheel scrolls. The quit key and what is
  not input pass (``input_policy.overlay_passes``).
* ``layout`` is pure and ``paint`` reads no Blender context, so geometry is host-tested and pixels checked offscreen.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import FloatProperty, StringProperty
from bpy.types import Operator

from . import chat_add_menu, flow_open, input_policy, ui_tokens as tk

IDENTITY = "angelo.flow_menu"
FLOWS, STEPS = "FLOWS", "STEPS"   # i18n: menu kinds, never shown
WIDTH, MIN_WIDTH = 380, 320       # Unscaled pixels, like the chat canvas; narrower only when the editor is.
PAD, GAP, EDGE, BELOW_POINTER, HEADER_GAP = 8, 6, 8, 14, 9
_open = None         # {"menu", "window", "area", "anchor", "top", "rows", "layout", "hover", "pressed", "cursor", ...}
_handlers = {}       # Space type -> POST_PIXEL draw handler (added when the menu first opens over that kind of editor).
drawn = 0            # Draws while open, for the GUI checks.
history = []         # The last opens and closes with their reason (GUI checks read why a menu did not stay open).


def _note(what, **details):
    import time
    history.append(dict(details, what=what, at=round(time.monotonic(), 3)))
    del history[:-24]


def rows(menu=FLOWS):
    """The rows of ``menu``: Add step's are ``flow_window.step_rows``; the Flows popup's are ``flow_rows``."""
    if menu == STEPS:
        from . import flow_window
        return flow_window.step_rows()
    return flow_rows()


def flow_rows():
    """The Flows popup's rows: a header (the Generation Flows glyph, name and why line, section 0's words), then the
    Generation Flows rows, with *Start from a template* above the templates and *Your flows* above the person's flows
    (``flow_open``'s labels, as the stock submenu says them)."""
    result, section = [{"kind": "header", "label": flow_open.MENU_LABEL, "subtitle": flow_open.MENU_WHY,
                        "glyph": "flow", "value": "flows:header"}], None   # i18n: row id, never shown
    for row in flow_open.menu_rows():
        value = row["value"]
        here = (flow_open.TEMPLATES if value.startswith(flow_open.STARTER_ROW) else
                flow_open.YOURS if value.startswith(flow_open.TREE_ROW) else None)
        if here and here != section:
            result.append({"kind": "section", "label": here})
        section = here or section
        result.append(dict(row))
    return result


def row_height(row):
    if row["kind"] == "section":
        return chat_add_menu.HEIGHTS["section"]
    return (row.get("height") or tk.ROW_HEIGHT) + (HEADER_GAP if row["kind"] == "header" else 0)


def menu_width(width):
    """``WIDTH``, never under ``MIN_WIDTH`` unless the editor itself is narrower."""
    return min(WIDTH, max(160, width - 2 * EDGE)) if width - 2 * EDGE < MIN_WIDTH else WIDTH


def wrap_rows(view, items, width):
    """Each row's why lines, all of them (never cut), and its height to hold them."""
    for row in items:
        if row["kind"] not in {"row", "header"}:
            continue
        avail = width - 2 * PAD - chat_add_menu.TEXT_X - 12
        text = chat_add_menu._shown(row, "subtitle")
        lines = [(l[0] if isinstance(l, tuple) else l).rstrip() for l in view._flow_wrap(text, avail, 11.5)] if text else []
        row["subtitle_lines"] = lines
        row["height"] = tk.ROW_HEIGHT + chat_add_menu.LINE * max(0, len(lines) - 1)


def layout(items, anchor_x, width, height, scroll=0, top=0):
    """The menu's rectangle, its list and each row's rectangle (unscaled region pixels, y up) for an editor ``width``
    x ``height``, hanging ``top`` below its top edge with its left edge at ``anchor_x`` (moved left to stay inside),
    and the clamped scroll."""
    w = menu_width(width)
    content = sum(row_height(row) for row in items)
    below = height - top - GAP
    h = min(content + 2 * PAD, max(80, below - EDGE))
    x = max(EDGE, min(anchor_x, width - w - EDGE))
    y = below - h
    list_rect = (x + PAD, y + PAD, w - 2 * PAD, h - 2 * PAD)
    max_scroll = max(0, content - list_rect[3])
    scroll = max(0, min(max_scroll, scroll))
    rects, row_top = [], y + h - PAD + scroll
    for row in items:
        rh = row_height(row)
        row_top -= rh
        rects.append((list_rect[0], row_top, list_rect[2], rh))
    return {"rect": (x, y, w, h), "list_rect": list_rect, "rows": rects, "scroll": scroll, "max_scroll": max_scroll,
            "content_height": content}


def mirrored(geometry, width):
    """``geometry`` (``layout``) for a mirrored drawing: each rectangle where the mirror shows it at its laid-out place."""
    flip = lambda r: (width - r[0] - r[2], r[1], r[2], r[3])
    return dict(geometry, rect=flip(geometry["rect"]), list_rect=flip(geometry["list_rect"]),
                rows=[flip(r) for r in geometry["rows"]])


def row_at(geometry, items, x, y):
    """The index of the actionable row under (``x``, ``y``) (unscaled region pixels, layout orientation), or None."""
    lx, ly, lw, lh = geometry["list_rect"]
    if not (lx <= x <= lx + lw and ly <= y <= ly + lh):
        return None
    for index, (rx, ry, rw, rh) in enumerate(geometry["rows"]):
        if items[index]["kind"] in chat_add_menu.ACTIONABLE and rx <= x <= rx + rw and ry <= y < ry + rh:
            return index
    return None


def is_open(window=None):
    return _open is not None and (window is None or _open["window"] == window.as_pointer())


def target_area(window, x, y):
    """The editor at window pixel (``x``, ``y``); None over the top bar, a global area ``screen.areas`` omits."""
    for area in window.screen.areas if window and window.screen else ():
        if area.x <= x < area.x + area.width and area.y <= y <= area.y + area.height:
            return area
    return None


def under_top_bar(window, x):
    """The editor directly under the top bar at window x ``x`` (the highest one spanning it), else the largest: where
    a popup from a top-bar button hangs. The top bar is a global area ``screen.areas`` leaves out, so the editor is
    found from the editors' own edges, never from the clicked region's (1.0.5: a Director click found none)."""
    areas = list(window.screen.areas) if window and window.screen else []
    spanning = [a for a in areas if a.x <= x < a.x + a.width]
    if spanning:
        return max(spanning, key=lambda a: a.y + a.height)
    return max(areas, key=lambda a: a.width * a.height, default=None)


def _find(pointer, windows):
    return next((w for w in windows if w.as_pointer() == pointer), None)


def _area(window):
    return next((a for a in window.screen.areas if a.as_pointer() == _open["area"]), None) if window else None


def _region(area):
    return next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None


def _redraw():
    if _open is None:
        return
    area = _area(_find(_open["window"], bpy.context.window_manager.windows))
    if area is not None:
        area.tag_redraw()
    from . import chat_toggle
    chat_toggle.redraw_top_bars()   # The Flows button is pressed while its popup is open.


def close(reason="closed"):
    global _open
    if _open is None:
        return
    _note("close", reason=reason, drawn=_open.get("layout") is not None)
    _redraw()
    window = _find(_open["window"], bpy.context.window_manager.windows)
    _open = None
    if window is not None:
        try:
            window.cursor_modal_restore()
        except (RuntimeError, TypeError):
            pass
    from . import chat_toggle
    chat_toggle.redraw_top_bars()


def open_from_top_bar(window):
    """Open the Flows popup under the top bar's Flows button (the welcome's slide), as a click on it does."""
    from . import chat_toggle, top_bar_fit
    anchor, fitted = chat_toggle.anchors.get(window.as_pointer()), top_bar_fit.widths.get(window.as_pointer(), {})
    x = anchor[0] if anchor else window.width / 2
    for name in ("chat", "studio"):   # Chat, Studio, Flows follow each other from Chat's left edge.
        if name in fitted:
            x += fitted[name][1] * fitted[name][2]
    area = target_area(window, x, (anchor[2] if anchor else window.height) - 2) or under_top_bar(window, x)
    region = _region(area)
    if region is None:
        _note("open_from_top_bar", x=x, anchor=list(anchor) if anchor else None,
              result="no editor under the button")   # i18n: a history reason for suites, never shown
        return False
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        bpy.ops.angelo.flow_menu('INVOKE_DEFAULT', anchor=x, top=0.0)
    return is_open(window)


def _draw():
    global drawn
    from . import chat_view
    state, area, region = _open, bpy.context.area, bpy.context.region
    if state is None or area is None or region is None or area.as_pointer() != state["area"]:
        return
    paint(state, region.width, region.height, chat_view.ui_scale())
    drawn += 1


def paint(state, region_width, region_height, scale):
    """Draw the open menu ``state`` into the current framebuffer, a region ``region_width`` x ``region_height``
    pixels; records its geometry in ``state``. Reads no Blender context, so a check can draw it offscreen."""
    import gpu
    from . import canvas_rtl, card_kit as kit, chat_view, ui_kit
    saved = chat_view._scale
    chat_view._scale = scale
    gpu.matrix.push()
    gpu.matrix.scale((scale, scale))   # Laid out in unscaled pixels, as the chat canvas; scissors scale back.
    width, height = region_width / scale, region_height / scale
    canvas_rtl.begin(width)
    old_scissor = gpu.state.scissor_get()
    try:
        items = state["rows"]
        wrap_rows(chat_view, items, menu_width(width))
        # The box stays under its button in every language (the top bar is not mirrored); only its content mirrors:
        # laid out where it shows, then given in the mirrored drawing's coordinates (review P1).
        geometry = layout(items, state["anchor"] / scale - 2 * PAD, width, height, state["scroll"], state["top"])
        if canvas_rtl.mirrored():
            geometry = mirrored(geometry, width)
        state["layout"], state["scroll"], state["scale"] = geometry, geometry["scroll"], scale
        x, y, w, h = geometry["rect"]
        radius = tk.RADIUS_PANEL
        for grow, alpha in ((8, 0.06), (4, 0.10), (1, 0.16)):   # A soft shadow: it floats over the editor.
            chat_view.shape(x - grow, y - grow - 2, w + 2 * grow, h + 2 * grow, (0, 0, 0, alpha), radius + grow)
        chat_view.shape(x, y, w, h, kit.LINE, radius + 1)
        chat_view.shape(x + 1, y + 1, w - 2, h - 2, kit.CARD, radius)
        gpu.state.scissor_test_set(True)
        lx, ly, lw, lh = geometry["list_rect"]
        gpu.state.scissor_set(*chat_view._scissor(lx, ly, lw, lh))
        cursor = state["cursor"] if state["keyboard"] else None
        for index, (row, rect) in enumerate(zip(items, geometry["rows"])):
            rx, ry, rw, rh = rect
            if ry + rh <= ly or ry >= ly + lh:
                continue
            if row["kind"] == "section":   # The + menu's section label.
                label = chat_add_menu._label(row["label"]).upper()
                chat_view.text(chat_view.elide(label, rw - 16, 11), rx + 8, ry + 8, 11, chat_view.MUTED)
                continue
            if row["kind"] == "header":   # Says what the rows are; a rule under it.
                chat_add_menu.draw_row(chat_view, row, (rx, ry + HEADER_GAP, rw, rh - HEADER_GAP), glyphs=draw_glyph)
                chat_view.shape(rx + 8, ry + 3, rw - 16, 1, kit.LINE)
                continue
            if state["hover"] == index or cursor == index:
                fill = kit.PANEL_HOVER if state["pressed"] != index else kit.LINE_PRESSED
                chat_view.shape(rx, ry + 1, rw, rh - 2, fill, tk.RADIUS_CONTROL)
            if cursor == index:
                ui_kit.focus_ring(chat_view, (rx, ry + 1, rw, rh - 2), tk.RADIUS_CONTROL, 1.0)
            chat_add_menu.draw_row(chat_view, row, rect, glyphs=draw_glyph)
        if geometry["max_scroll"]:
            thumb_h = max(22, lh * lh / geometry["content_height"])
            thumb_y = ly + lh - thumb_h - (lh - thumb_h) * geometry["scroll"] / geometry["max_scroll"]
            gpu.state.scissor_set(*chat_view._scissor(x + 1, y + 1, w - 2, h - 2))
            chat_view.shape(x + w - 5, thumb_y, 2, thumb_h, chat_view.MUTED, 1)
    finally:
        gpu.state.scissor_set(*old_scissor)
        gpu.state.scissor_test_set(False)
        canvas_rtl.end()
        gpu.matrix.pop()
        chat_view._scale = saved
        gpu.state.blend_set('NONE')


def _pointer(event, region):
    """The event's position in the menu's layout coordinates (unscaled, mirrored like the drawing)."""
    from . import canvas_rtl
    scale = _open.get("scale") or 1.0
    x = canvas_rtl.pointer_x(event.mouse_x - region.x, region.width) / scale
    return x, (event.mouse_y - region.y) / scale


def draw_glyph(view, glyph, tx, ty):
    """A row's tile: a step type's own (``flow_window.draw_step_glyph``), else the + menu's."""
    from . import flow_window
    if not flow_window.draw_step_glyph(view, glyph, tx, ty):
        chat_add_menu.draw_glyph(view, glyph, tx, ty)


def _run(window, area, row):
    """Close the menu, then run the row's operator in the editor it was over, with an Undo step
    (``chat_add_menu.dispatch``): Add step's ``node.add_node`` needs the flow editor; a flow row needs only the window."""
    close("ran a row")   # i18n: a history reason for suites, never shown
    group, name = row["operator"].split(".", 1)
    region = _region(area)
    try:
        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
            getattr(getattr(bpy.ops, group), name)('INVOKE_DEFAULT', True, **dict(row.get("properties") or ()))
    except RuntimeError as error:   # Its poll refused (the editor changed meanwhile): the menu just closes.
        print("Angelo: flow menu:", error)


class ANGELO_OT_flow_menu(Operator):
    bl_idname = "angelo.flow_menu"   # IDENTITY: a literal, as every Angelo operator (test_native_chat_start_states).
    bl_label = "Generation Flows"
    bl_description = "Start a new Generation Flow, start one from a template, or open one of yours"
    bl_options = {'INTERNAL'}
    menu: StringProperty(default=FLOWS, options={'SKIP_SAVE'})
    # A caller that knows its button (Add step, the welcome's slide): the window x of the button's left edge and how
    # far below the editor's top the menu hangs (unscaled pixels), over the editor in the context. Unset: beside the
    # pointer, under the top bar or under the pill the pointer is on.
    anchor: FloatProperty(default=-1.0, options={'SKIP_SAVE'})
    top: FloatProperty(default=0.0, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        global _open
        window = context.window
        if window is None:
            _note("invoke", result="no window")   # i18n: a history reason for suites, never shown
            return {'CANCELLED'}
        if is_open(window):   # The button again: it closes its menu.
            close("button again")   # i18n: a history reason for suites, never shown
            return {'FINISHED'}
        close("reopened")
        from . import chat_view
        scale = chat_view.ui_scale()
        anchor, top = self.anchor, self.top
        if self.menu == STEPS or anchor >= 0:
            area = context.area
        else:
            anchor = event.mouse_x
            area = target_area(window, event.mouse_x, event.mouse_y)
            if area is None:   # The top bar's button: the editor under it, flush with the bar.
                area = under_top_bar(window, event.mouse_x)
            else:   # A pill in an editor's header: just under the pointer, never above the editor's top.
                editor = _region(area)
                if editor is not None:
                    top = max(0.0, (editor.y + editor.height - event.mouse_y) / scale + BELOW_POINTER)
        target = _region(area)
        if target is None:
            _note("invoke", x=event.mouse_x, y=event.mouse_y, anchor=self.anchor,
                  result="no editor under the button")   # i18n: a history reason for suites, never shown
            return {'CANCELLED'}
        space = type(area.spaces.active)
        if space not in _handlers:   # Added after the editor's own drawing (the chat canvas), so drawn above it.
            _handlers[space] = space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        _open = {"menu": self.menu, "window": window.as_pointer(), "area": area.as_pointer(),
                 "anchor": anchor - target.x, "top": top, "rows": rows(self.menu), "layout": None, "hover": None,
                 "pressed": None, "cursor": None, "keyboard": False, "scroll": 0, "token": object()}
        self.token = _open["token"]
        context.window_manager.modal_handler_add(self)
        _note("invoke", result="opened", area=area.type, menu=self.menu)
        _redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        state = _open
        if state is None or state["token"] is not self.token:
            return {'FINISHED', 'PASS_THROUGH'}
        if event.type == 'WINDOW_DEACTIVATE':   # Another app or window took focus: a menu does not stay open.
            close("window deactivated")   # i18n: a history reason for suites, never shown
            return {'FINISHED', 'PASS_THROUGH'}
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}
        window = context.window
        if window is None or window.as_pointer() != state["window"]:
            return {'PASS_THROUGH'}
        area = _area(window)
        region = _region(area)
        if region is None:
            close("editor gone")   # i18n: a history reason for suites, never shown
            return {'FINISHED', 'PASS_THROUGH'}
        if state["layout"] is None:
            return {'PASS_THROUGH'}   # Not drawn yet.
        items, geometry = state["rows"], state["layout"]
        x, y = _pointer(event, region)
        over_region = 0 <= event.mouse_x - region.x < region.width and 0 <= event.mouse_y - region.y < region.height
        over = over_region and _inside(geometry["rect"], x, y)
        index = row_at(geometry, items, x, y) if over else None
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            refresh = input_policy.refresh_move(event)
            if index != state["hover"] or (state["keyboard"] and not refresh):
                state["hover"] = index
                state["keyboard"] = state["keyboard"] and refresh
                _set_cursor(window, index is not None)
                area.tag_redraw()
            return {'PASS_THROUGH'} if not over or refresh else {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'TRACKPADPAN'} and over:
            from . import scroll_input
            step = scroll_input.view_up(event, 46, state.get("scale") or 1.0) or 0.0
            state["scroll"] = max(0, min(geometry["max_scroll"], state["scroll"] - step))
            area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE':
            if event.value in {'PRESS', 'DOUBLE_CLICK'}:
                if not over:
                    # A click anywhere else closes the menu and does nothing else, as a menu does.
                    close("click outside")   # i18n: a history reason for suites, never shown
                    return {'FINISHED'}
                state["pressed"] = index
                area.tag_redraw()
                return {'RUNNING_MODAL'}
            if event.value == 'RELEASE':
                pressed, state["pressed"] = state["pressed"], None
                if pressed is not None and pressed == index:
                    _run(window, area, items[index])
                    return {'FINISHED'}
                area.tag_redraw()
                return {'RUNNING_MODAL'}
        if event.type in {'RIGHTMOUSE', 'MIDDLEMOUSE'} and event.value == 'PRESS':
            if not over:
                close("click outside")   # i18n: a history reason for suites, never shown
                return {'FINISHED'}
            return {'RUNNING_MODAL'}
        if event.value == 'PRESS':
            if event.type == 'ESC':
                close("escape")
                return {'FINISHED'}
            popup = {"rows": items, "cursor": state["cursor"], "layout": geometry, "scroll": state["scroll"]}
            chosen = chat_add_menu.key(popup, event)
            if chosen is not None:
                _run(window, area, chosen)
                return {'FINISHED'}
            if event.type in {'UP_ARROW', 'DOWN_ARROW', 'HOME', 'END'}:
                state.update(cursor=popup["cursor"], keyboard=True, hover=None, scroll=popup["scroll"])
                area.tag_redraw()
        # Keys while the menu is open are the menu's, as in any menu: never a shortcut of the editor behind it.
        return {'RUNNING_MODAL'} if event.value in {'PRESS', 'RELEASE'} else {'PASS_THROUGH'}


def _inside(rect, x, y):
    return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]


def _set_cursor(window, hand):
    try:
        window.cursor_modal_set('HAND') if hand else window.cursor_modal_restore()
    except (RuntimeError, TypeError):
        pass


@persistent
def _load_pre(*_args):
    """Opening a file drops every modal operator and replaces the windows: the menu goes with them."""
    global _open
    _open = None


def register():
    bpy.utils.register_class(ANGELO_OT_flow_menu)
    bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _open
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
    _open = None
    for space, handler in _handlers.items():
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    bpy.utils.unregister_class(ANGELO_OT_flow_menu)
