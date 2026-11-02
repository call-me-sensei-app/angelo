# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render window's region pills (doc/angelo/render_region.rst, "In the render window").

* **Where**: every Image Editor showing the Render Result, at the top-left of its image area, while no render runs.
* **What**: *Render region* (a framed corner glyph), then, after a region render, *Revert* or *Show region render*.
  Each is a card-kit ``PANEL`` pill with a 1 px ``LINE`` border, ``PANEL_HOVER`` under the pointer and ``TEAL_SOFT``
  while pressed, as the Asset Shelf's pills are (``asset_shelf_add``).
* **Input**: a passive modal operator per window (``input_policy.CANVAS_INPUTS``) that takes only presses on the pills
  and passes every other event on. It starts from a timer the first time a pill draws in a window and ends when no
  pill is shown there.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import card_kit as kit, input_policy, live_windows, render_region, ui_kit
from .ui_text import iface_, n_

IDENTITY = "angelo.render_region_input"
RENDER_LABEL, REVERT_LABEL, SHOW_LABEL = n_("Render region"), n_("Revert region"), n_("Show region render")
places = {}        # area pointer -> (window pointer, [(action, (x, y, w, h) in window pixels)])
_hover, _pressed, _starting = set(), set(), set()
_handlers = []
_view = {"view": None}


def pills(scene):
    """The pills to show for ``scene``'s render: (action, label) in order."""
    shown = [("render", RENDER_LABEL)]
    state = render_region.state(scene)
    if state in {"APPLIED", "REVERTED"}:
        shown.append(("revert", REVERT_LABEL if state == "APPLIED" else SHOW_LABEL))
    return shown


def _draw():
    context = bpy.context
    area, region, window = context.area, context.region, context.window
    if area is None or region is None or window is None or region.type != 'WINDOW':
        return
    pointer = area.as_pointer()
    from . import render_region_draw
    busy = bpy.app.is_job_running('RENDER') or render_region_draw._tool["op"] is not None   # The tool's hint is there.
    if not render_region.shows_render(area) or busy:
        places.pop(pointer, None)
        return
    if _view["view"] is None:
        from .card_view import View
        _view["view"] = View()
    view, scale = _view["view"], context.preferences.system.ui_scale
    size, pad, height, gap = 12.5 * scale, 12 * scale, 28 * scale, 6 * scale
    x, y = 12 * scale, region.height - height - 12 * scale
    rects = []
    for action, label in pills(context.scene):
        text = iface_(label)
        glyph = 14 * scale if action == "render" else 0
        width = view.measure(text, size, view._font) + 2 * pad + (glyph + 6 * scale if glyph else 0)
        key = (pointer, action)
        fill = kit.TEAL_SOFT if key in _pressed else kit.PANEL_HOVER if key in _hover else kit.PANEL
        view.shape(x, y, width, height, fill, height / 2)
        ui_kit.outline((x, y, width, height), height / 2, max(1.0, scale), kit.LINE)
        tx = x + pad
        if glyph:   # A frame with one teal corner: a region of the frame.
            gx, gy = tx, y + (height - glyph) / 2
            for x1, y1, x2, y2 in ((gx, gy, gx + glyph, gy), (gx + glyph, gy, gx + glyph, gy + glyph),
                                   (gx + glyph, gy + glyph, gx, gy + glyph), (gx, gy + glyph, gx, gy)):
                view.stroke(x1, y1, x2, y2, kit.MUTED, 1.2 * scale)
            view.shape(gx + glyph / 2, gy, glyph / 2, glyph / 2, kit.TEAL, 1.5 * scale)
            tx += glyph + 6 * scale
        view.text(text, tx, y + (height - size) / 2 + 2 * scale, size, kit.TEXT)
        rects.append((action, (region.x + x, region.y + y, width, height)))
        x += width + gap
    places[pointer] = (window.as_pointer(), rects)
    _ensure_input(window)


def pill_at(window_pointer, x, y):
    """``(area pointer, action)`` of the pill holding window pixel (``x``, ``y``), else None."""
    for pointer, (window, rects) in places.items():
        if window != window_pointer:
            continue
        for action, rect in rects:
            if rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]:
                return pointer, action
    return None


def _area(pointer):
    for window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.as_pointer() == pointer:
            return window, area
    return None, None


def _redraw(pointer):
    area = _area(pointer)[1]
    if area is not None:
        area.tag_redraw()


def _ensure_input(window):
    """Start this window's input handler after the draw (an operator never starts inside one)."""
    pointer = window.as_pointer()
    if pointer in _starting or input_policy.handler_running(window, IDENTITY):
        return
    _starting.add(pointer)

    def start(target):
        _starting.discard(pointer)
        if target is None or input_policy.handler_running(target, IDENTITY):
            return None
        if any(p[0] == pointer for p in places.values()):
            try:
                with bpy.context.temp_override(window=target):
                    bpy.ops.angelo.render_region_input('INVOKE_DEFAULT')
            except RuntimeError as error:
                print("Angelo: render region pills input:", error)
        return None
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, start),
                            first_interval=0.0)


def activate(key):
    """Run what a pill names, after this event, from the window it shows in."""
    pointer, action = key

    def run():
        window, area = _area(pointer)
        if window is None:
            return None
        operator = bpy.ops.angelo.render_region if action == "render" else bpy.ops.angelo.render_region_revert
        try:
            with bpy.context.temp_override(window=window, area=area):
                operator('INVOKE_DEFAULT')
        except RuntimeError as error:   # Its own reason, in the status bar.
            print("Angelo: render region:", " ".join(str(error).split()))
        return None
    bpy.app.timers.register(run, first_interval=0.0)


class ANGELO_OT_render_region_input(Operator):
    bl_idname = "angelo.render_region_input"   # IDENTITY
    bl_label = "Render Region"
    bl_description = "The render window's region pills"
    bl_options = {'INTERNAL'} | input_policy.modal_options(bl_idname)   # Auto-save never waits for it.

    def invoke(self, context, event):
        self.window = context.window.as_pointer()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}
        window = context.window
        if window is None or window.as_pointer() != self.window:
            return {'PASS_THROUGH'}
        if not any(p[0] == self.window for p in places.values()):
            _hover.clear()
            return {'FINISHED', 'PASS_THROUGH'}   # No pill in this window: this event is not ours.
        if input_policy.canvas_blocking(window):
            return {'PASS_THROUGH'}   # A real tool holds the window.
        over = pill_at(self.window, event.mouse_x, event.mouse_y)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            if {over} - {None} != _hover:
                for key in _hover | ({over} - {None}):
                    _redraw(key[0])
                _hover.clear()
                if over is not None:
                    _hover.add(over)
            return {'PASS_THROUGH'}
        if event.type == 'LEFTMOUSE':
            if event.value in {'PRESS', 'DOUBLE_CLICK'} and over is not None:
                _pressed.add(over)
                _redraw(over[0])
                return {'RUNNING_MODAL'}   # Never a click on the image behind a pill.
            if event.value == 'RELEASE' and _pressed:
                was = set(_pressed)
                _pressed.clear()
                for key in was:
                    _redraw(key[0])
                if over in was:
                    activate(over)
                return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


@persistent
def _load_post(_):
    """A file load drops modal handlers and replaces windows and areas."""
    places.clear()
    _hover.clear()
    _pressed.clear()
    _starting.clear()


def register():
    bpy.utils.register_class(ANGELO_OT_render_region_input)
    if not bpy.app.background:
        space = bpy.types.SpaceImageEditor
        _handlers.append((space, space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')))
    bpy.app.handlers.load_post.append(_load_post)


def unregister():
    for space, handler in _handlers:
        space.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    _load_post(None)
    bpy.utils.unregister_class(ANGELO_OT_render_region_input)
