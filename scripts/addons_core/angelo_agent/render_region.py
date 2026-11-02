# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render a region over the last render (1.0.8). Contract: doc/angelo/render_region.rst.

The native side (``render.render(region_over_last=True)``, RE_angelo_region.hh) renders a region into the render the
Image Editor shows and keeps every pass outside it (compositing runs on the whole frame); ``render.region_revert``
swaps the region's previous pixels back and forth; ``RenderSettings.angelo_region_render`` says which the region
shows. This module is the person's way in:

* ``angelo.render_region``: the chat bar's + menu, the Render menu, the render window's pill and F3. It finds where the
  last render shows (the render window, else a 3D view looking through the camera; it opens the render window when
  neither shows), then lets the person drag the region there (``render_region_draw``) and renders it.
* ``angelo.render_region_again``: the last drawn region again (the scene keeps it as ``angelo_render_region``).
* ``angelo.render_region_revert``: Revert / Show region render, wherever the person is.
* After a region render the person started, a card in the render window says what happened and offers Revert,
  Render again and a new region (``render_region_card``).

Fractions are Blender's render-region convention throughout: (left, bottom, right, top) of the frame, 0 to 1.
"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator

from . import live_windows
from .ui_text import iface_, n_

TITLE = n_("Render region")
WHY = n_("Re-render one part of the last render; its passes stay exactly as they were outside it")
MENU_LABEL = n_("Render Region Over Last Render")
REGION_KEY = "angelo_render_region"   # The scene's last drawn region (saved: a reopened file renders it again).
MIN_PIXELS = 4                        # The smallest region side Angelo renders, in pixels of the frame.
STATES = ("NONE", "APPLIED", "REVERTED")
BLENDER_ERROR = "Error: "             # The prefix of an operator's RuntimeError (Blender's own reason follows).
_started = {"by_person": False}       # A region render the person started here: its end shows the card.
_waiting = {"tries": 0}               # Ticks spent waiting for the render window to show (``_draw_when_shown``).


# -- fractions --------------------------------------------------------------------------------------------------------
def fractions(a, b):
    """The region between two points given as frame fractions (x, y), clamped to the frame:
    (left, bottom, right, top)."""
    def clamp(value):
        return min(1.0, max(0.0, float(value)))
    (ax, ay), (bx, by) = a, b
    return (clamp(min(ax, bx)), clamp(min(ay, by)), clamp(max(ax, bx)), clamp(max(ay, by)))


def frame_point(point, frame_min, frame_max):
    """A region pixel as a fraction of the frame drawn between ``frame_min`` and ``frame_max`` (region pixels)."""
    width, height = frame_max[0] - frame_min[0], frame_max[1] - frame_min[1]
    if width <= 0 or height <= 0:
        return None
    return (point[0] - frame_min[0]) / width, (point[1] - frame_min[1]) / height


def large_enough(region, width, height):
    """Whether ``region`` (fractions) covers at least MIN_PIXELS on each side of a ``width`` x ``height`` frame."""
    left, bottom, right, top = region
    return (right - left) * width >= MIN_PIXELS and (top - bottom) * height >= MIN_PIXELS


def frame_size(scene):
    """The frame's size in pixels as the next render makes it (percentage included, never cropped)."""
    render = scene.render
    return (max(1, int(render.resolution_x * render.resolution_percentage / 100)),
            max(1, int(render.resolution_y * render.resolution_percentage / 100)))


def stored(scene):
    """The scene's last drawn region, or None."""
    value = scene.get(REGION_KEY) if scene is not None else None
    try:
        region = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return region if len(region) == 4 and region[2] > region[0] and region[3] > region[1] else None


# -- where the last render shows --------------------------------------------------------------------------------------
def shows_render(area):
    space = area.spaces.active if area.type == 'IMAGE_EDITOR' else None
    image = getattr(space, "image", None)
    return image is not None and image.type == 'RENDER_RESULT'


def through_camera(area, scene):
    space = area.spaces.active if area.type == 'VIEW_3D' else None
    view = getattr(space, "region_3d", None)
    return view is not None and view.view_perspective == 'CAMERA' and scene.camera is not None


def target(context):
    """``(window, area)`` to draw the region in: the context's own when it shows the render or the camera's view,
    else the render window, else a 3D view through the camera; None when nothing shows either."""
    scene = context.scene
    if context.area is not None and (shows_render(context.area) or through_camera(context.area, scene)):
        return context.window, context.area
    pairs = [(window, area) for window, area in live_windows.window_areas(context.window_manager)
             if window.scene == scene]
    found = next(((window, area) for window, area in pairs if shows_render(area)), None)
    return found or next(((window, area) for window, area in pairs if through_camera(area, scene)), None)


def last_render_size(scene):
    """The size of the scene's last render, or None (no render yet). ``Image.size`` reads 0 x 0 for the Render
    Result (it has no image user), so the render itself is asked (``RenderSettings.angelo_last_render_size``)."""
    width, height = tuple(getattr(scene.render, "angelo_last_render_size", (0, 0)))
    return (width, height) if width and height else None


def state(scene):
    """NONE, APPLIED or REVERTED: what the region of the scene's last region render shows."""
    return getattr(scene.render, "angelo_region_render", "NONE") if scene is not None else "NONE"


def blender_reason(error):
    """Blender's own reason from an operator's RuntimeError, as its report said it."""
    return " ".join(str(error).split()).removeprefix(BLENDER_ERROR)


# -- starting -----------------------------------------------------------------------------------------------------
def start_render(window, area, scene, region, by_person=True):
    """Render ``region`` (fractions) over the last render, from ``window``. Returns Blender's result set; raises
    RuntimeError with Blender's own reason when it refuses (no last render, a size change, a running render)."""
    scene[REGION_KEY] = list(region)
    _started["by_person"] = by_person
    with bpy.context.temp_override(window=window, area=area):
        return bpy.ops.render.render('INVOKE_DEFAULT', region_over_last=True, region=region)


def _draw_in(window, area):
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    with bpy.context.temp_override(window=window, area=area, region=region):
        return bpy.ops.angelo.render_region_draw('INVOKE_DEFAULT')


class ANGELO_OT_render_region(Operator):
    bl_idname = "angelo.render_region"
    bl_label = "Render Region Over Last Render"
    bl_description = ("Drag over the last render (or the camera's view) to re-render only that part; its passes "
                      "stay exactly as they were outside it, and compositing runs again on the whole frame")

    def execute(self, context):
        from . import render_region_card
        scene = context.scene
        if last_render_size(scene) is None:
            render_region_card.show(render_region_card.NO_RENDER_PHASE)
            return {'CANCELLED'}
        found = target(context)
        if found is None:   # Open the render window, then draw there once it shows.
            bpy.ops.render.view_show('INVOKE_DEFAULT')
            _waiting["tries"] = 0
            name = scene.name   # By name: the timer runs later, after the scene may have gone.
            bpy.app.timers.register(lambda: _draw_when_shown(name), first_interval=0.1)
            return {'FINISHED'}
        _draw_in(*found)
        return {'FINISHED'}

    def invoke(self, context, event):
        return self.execute(context)


def _draw_when_shown(scene_name):
    context = bpy.context
    scene = bpy.data.scenes.get(scene_name)
    found = target(context) if scene is not None and context.scene == scene else None
    if found is not None and shows_render(found[1]):
        _waiting["tries"] = 0
        _draw_in(*found)
        return None
    _waiting["tries"] += 1
    if _waiting["tries"] > 20:   # Two seconds: the render window never showed (it shows nowhere by preference).
        _waiting["tries"] = 0
        return None
    return 0.1


class ANGELO_OT_render_region_again(Operator):
    bl_idname = "angelo.render_region_again"
    bl_label = "Render Region Again"
    bl_description = "Render the last drawn region over the last render again"

    @classmethod
    def poll(cls, context):
        return stored(context.scene) is not None

    def execute(self, context):
        found = target(context) or (context.window, context.area)
        try:
            start_render(found[0], found[1], context.scene, stored(context.scene))
        except RuntimeError as error:
            self.report({'ERROR'}, blender_reason(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_render_region_revert(Operator):
    bl_idname = "angelo.render_region_revert"
    bl_label = "Revert Region"
    bl_description = ("Show the last render's pixels in the region rendered over it again, or the region render "
                      "again")

    @classmethod
    def poll(cls, context):
        return state(context.scene) in {"APPLIED", "REVERTED"}

    def execute(self, context):
        try:
            bpy.ops.render.region_revert()
        except RuntimeError as error:
            self.report({'ERROR'}, blender_reason(error))
            return {'CANCELLED'}
        for _window, area in live_windows.window_areas(context.window_manager):
            if shows_render(area):
                area.tag_redraw()
        return {'FINISHED'}


def render_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator("angelo.render_region", text=iface_(MENU_LABEL), translate=False, icon='SELECT_SET')


# -- after a render ---------------------------------------------------------------------------------------------------
@persistent
def _render_ended(scene, _depsgraph=None):
    """The person's region render ended: show its card (after this handler; a card never opens inside one)."""
    if not _started["by_person"]:
        return
    _started["by_person"] = False
    name = scene.name if scene is not None else ""

    def show():
        from . import render_region_card
        live = bpy.data.scenes.get(name)
        if live is not None:
            render_region_card.show(render_region_card.phase_for(state(live)))
        return None
    bpy.app.timers.register(show, first_interval=0.05)


CLASSES = (ANGELO_OT_render_region, ANGELO_OT_render_region_again, ANGELO_OT_render_region_revert)


def register():
    from . import chat_destinations, render_region_card, render_region_draw, render_region_pills
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    render_region_draw.register()
    render_region_card.register()
    render_region_pills.register()
    bpy.types.TOPBAR_MT_render.append(render_menu)
    for handlers in (bpy.app.handlers.render_complete, bpy.app.handlers.render_cancel):
        if _render_ended not in handlers:
            handlers.append(_render_ended)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "render_region", TITLE, WHY, glyph="region", order=23, section=chat_destinations.CREATE,
        operator="angelo.render_region", owner="render_region"))


def unregister():
    from . import chat_destinations, render_region_card, render_region_draw, render_region_pills
    chat_destinations.unregister("render_region")
    for handlers in (bpy.app.handlers.render_complete, bpy.app.handlers.render_cancel):
        if _render_ended in handlers:
            handlers.remove(_render_ended)
    bpy.types.TOPBAR_MT_render.remove(render_menu)
    render_region_pills.unregister()
    render_region_card.unregister()
    render_region_draw.unregister()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

