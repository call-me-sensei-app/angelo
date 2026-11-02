# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dragging the region to render over the last render (``angelo.render_region_draw``; doc/angelo/render_region.rst).

A tool that holds the pointer in one area until the person drags a region, presses Escape or right-clicks. It runs in
the render window's Image Editor (the region is in the render's own pixels: ``View2D`` there is in frame fractions)
or in a 3D view looking through the scene camera (the region is relative to the camera's frame, as the 3D view's own
Render Region tool reads it). Wheel and middle-button events pass through, so the person can zoom and pan first.

Drawn with the card kit's primitives: a teal rectangle while dragging and one hint chip at the top of the area. The
scene's own render region is never changed: the region goes to the render operator for that render only.
"""

import bpy
from bpy.types import Operator

from . import card_kit as kit, input_policy, render_region
from .ui_text import iface_, n_

HINT = n_("Drag over the render to choose the region to render again")
HINT_CAMERA = n_("Drag inside the camera's frame to choose the region to render again")
CANCEL_HINT = n_("Esc cancels")
TOO_SMALL = n_("That region is too small; drag a larger one")
NAVIGATION = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'}
_tool = {"op": None, "view": None}   # The running tool (one at a time) and the drawing view, for ``_draw``.


def camera_frame(region, view, scene):
    """The scene camera's frame in ``region`` pixels as (min, max) corners, or None when it is not in front."""
    from bpy_extras import view3d_utils
    camera = scene.camera
    corners = [camera.matrix_world @ corner for corner in camera.data.view_frame(scene=scene)]
    points = [view3d_utils.location_3d_to_region_2d(region, view, corner) for corner in corners]
    if any(point is None for point in points):
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys)), (max(xs), max(ys))


def region_fractions(context, start, end):
    """The dragged region as frame fractions (left, bottom, right, top), or None when it cannot be read."""
    region = context.region
    if context.area.type == 'IMAGE_EDITOR':   # The render's View2D is in fractions of its frame.
        a = region.view2d.region_to_view(*start)
        b = region.view2d.region_to_view(*end)
        return render_region.fractions(a, b)
    frame = camera_frame(region, context.space_data.region_3d, context.scene)
    if frame is None:
        return None
    a, b = render_region.frame_point(start, *frame), render_region.frame_point(end, *frame)
    return None if a is None or b is None else render_region.fractions(a, b)


class ANGELO_OT_render_region_draw(Operator):
    bl_idname = "angelo.render_region_draw"
    bl_label = "Draw Render Region"
    bl_description = "Drag the region to render over the last render"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        area, region = context.area, context.region
        if area is None or region is None or region.type != 'WINDOW' or _tool["op"] is not None:
            return {'CANCELLED'}
        self.area_pointer, self.start, self.end = area.as_pointer(), None, None
        self.message = HINT if area.type == 'IMAGE_EDITOR' else HINT_CAMERA
        space = bpy.types.SpaceImageEditor if area.type == 'IMAGE_EDITOR' else bpy.types.SpaceView3D
        self._handler = (space, space.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL'))
        context.window.cursor_modal_set('CROSSHAIR')
        context.window_manager.modal_handler_add(self)
        _tool["op"] = self
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if input_policy.overlay_passes(event, context.window_manager):
            return {'PASS_THROUGH'}   # A card over the view (the region card) takes its own clicks.
        if context.area is None or context.area.as_pointer() != self.area_pointer:
            return self._end(context, {'CANCELLED'})
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            return self._end(context, {'CANCELLED'})
        if event.type in NAVIGATION and self.start is None:
            return {'PASS_THROUGH'}
        point = (event.mouse_region_x, event.mouse_region_y)
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'} and self.start is not None:
            self.end = point
            context.area.tag_redraw()
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.start = self.end = point
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE' and self.start is not None:
            self.end = point
            return self._finish(context)
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        scene = context.scene
        region = region_fractions(context, self.start, self.end)
        if region is None or not render_region.large_enough(region, *render_region.frame_size(scene)):
            self.start = self.end = None
            self.message = TOO_SMALL
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        window, area = context.window, context.area
        self._end(context, {'FINISHED'})
        try:
            render_region.start_render(window, area, scene, region)
        except RuntimeError as error:   # Blender's own reason: no last render, another size, a render running.
            self.report({'ERROR'}, render_region.blender_reason(error))
            return {'CANCELLED'}
        return {'FINISHED'}

    def _end(self, context, result):
        space, handler = self._handler
        space.draw_handler_remove(handler, 'WINDOW')
        _tool["op"] = None
        if context.window is not None:
            context.window.cursor_modal_restore()
        if context.area is not None:
            context.area.tag_redraw()
        return result

    def cancel(self, context):
        self._end(context, {'CANCELLED'})


def _draw():
    tool, area, region = _tool["op"], bpy.context.area, bpy.context.region
    if tool is None or area is None or area.as_pointer() != tool.area_pointer:
        return
    if _tool["view"] is None:
        from .card_view import View
        _tool["view"] = View()
    view, scale = _tool["view"], bpy.context.preferences.system.ui_scale
    if tool.start is not None and tool.end is not None:
        (ax, ay), (bx, by) = tool.start, tool.end
        x, y, w, h = min(ax, bx), min(ay, by), abs(bx - ax), abs(by - ay)
        view.shape(x, y, w, h, kit.TEAL_SOFT, 0)
        for x1, y1, x2, y2 in ((x, y, x + w, y), (x + w, y, x + w, y + h), (x + w, y + h, x, y + h), (x, y + h, x, y)):
            view.stroke(x1, y1, x2, y2, kit.TEAL, 1.6 * scale)
    _draw_hint(view, region, scale, iface_(tool.message), iface_(CANCEL_HINT))


def _draw_hint(view, region, scale, message, cancel):
    """One chip at the top centre of the area: the instruction, then how to leave."""
    size, pad = 13 * scale, 12 * scale
    main_w, cancel_w = view.measure(message, size, view._font), view.measure(cancel, size, view._font)
    width = main_w + cancel_w + 3 * pad
    height = 32 * scale
    x, y = (region.width - width) / 2, region.height - height - 16 * scale
    view.shape(x, y, width, height, kit.CARD, 10 * scale)
    view.text(message, x + pad, y + (height - size) / 2 + 2 * scale, size, kit.TEXT)
    view.text(cancel, x + 2 * pad + main_w, y + (height - size) / 2 + 2 * scale, size, kit.MUTED)


def register():
    bpy.utils.register_class(ANGELO_OT_render_region_draw)


def unregister():
    tool = _tool["op"]
    if tool is not None:   # Reload Scripts while dragging: drop its drawing, the window manager drops the handler.
        tool._handler[0].draw_handler_remove(tool._handler[1], 'WINDOW')
        _tool["op"] = None
    bpy.utils.unregister_class(ANGELO_OT_render_region_draw)
