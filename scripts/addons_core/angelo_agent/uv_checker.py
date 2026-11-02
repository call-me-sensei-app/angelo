# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport UV checker's controls (doc/angelo/uv_checker.rst): one toggle, its card, and where they are reached.

The checker itself is native: ``SpaceView3D.uv_checker`` (``View3DUVChecker``), drawn by the overlay engine from each
mesh's UVs (``overlay_uv_checker.hh``), so it never makes or changes a material, image or node. This module only
reads and writes that view setting:

* ``angelo.uv_checker_toggle`` turns it on or off for the 3D view it runs in (Director's view from the top bar), and
  ``angelo.uv_checker`` opens the UV checker card (``uv_checker_card``) with every option.
* Reached from the chat bar's + menu (``chat_destinations``), Director's top bar after Blender's menus, the 3D
  view's header in Manual mode and its View menu (appended, so ``bl_ui`` stays stock), and F3 (which searches the
  View menu).

Agents see it through ``angelo_view``'s UV_CHECKER look (``view_looks``), which never changes the person's view; every
other look turns it off for its draw, so a person's checker never colours an agent's view.
"""

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator

from .ui_text import n_, rpt_

TITLE = n_("UV checker")
MENU_WHY = n_("See how textures land on every mesh: grid, texel density or stretch. Materials stay as they are")
GLYPH = "uv_checker"
drawn = {}   # window pointer -> on, at its last Director top-bar draw; GUI suites read it.


def largest_view(window):
    """The window's largest 3D view area (Director is that view and the chat), or None."""
    from . import live_windows
    views = [area for area in live_windows.areas(window) if area.type == 'VIEW_3D']
    return max(views, key=lambda area: area.width * area.height, default=None)


def target_space(context):
    """The 3D view a control acts on: the one it runs in, else the window's largest (Director's view)."""
    space = getattr(context, "space_data", None)
    if space is not None and space.type == 'VIEW_3D':
        return space
    area = largest_view(context.window) if context.window is not None else None
    return area.spaces.active if area is not None else None


def uv_map_names(scene, view_layer, limit=16, scan=2000):
    """UV map names of the visible meshes, in first-seen order, at most ``limit`` (the card's Named choices). Reads
    names only, over at most ``scan`` objects, so a heavy scene costs a bounded walk."""
    names, seen = [], set()
    for count, obj in enumerate(scene.objects):
        if count >= scan or len(names) >= limit:
            break
        if obj.type != 'MESH' or not obj.visible_get(view_layer=view_layer):
            continue
        for layer in obj.data.uv_layers:
            if layer.name not in seen:
                seen.add(layer.name)
                names.append(layer.name)
    return names[:limit]


def meshes_without_map(scene, view_layer, settings, limit=3, scan=2000):
    """``(count, first names)`` of the visible meshes the checker hatches as missing: no UV map at all (Active,
    Render), or none named ``settings.uv_map_name`` (Named); only selected ones with Selected Only. A bounded walk
    over at most ``scan`` objects, reading names only."""
    count, names = 0, []
    for index, obj in enumerate(scene.objects):
        if index >= scan:
            break
        if obj.type != 'MESH' or not obj.visible_get(view_layer=view_layer):
            continue
        if settings.use_selected_only and not obj.select_get(view_layer=view_layer):
            continue
        layers = obj.data.uv_layers
        has = settings.uv_map_name in layers if settings.uv_map == 'NAMED' else len(layers) > 0
        if not has:
            count += 1
            if len(names) < limit:
                names.append(obj.name)
    return count, names


def _redraw(space):
    """Redraw every area showing ``space`` (a property set from Python redraws only through RNA updates)."""
    from . import live_windows
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        if area.type == 'VIEW_3D' and area.spaces.active == space:
            area.tag_redraw()


class ANGELO_OT_uv_checker_toggle(Operator):
    bl_idname = "angelo.uv_checker_toggle"
    bl_label = "Toggle UV Checker"
    bl_description = ("Show or hide a UV checker on the meshes in this 3D view. Their materials, images and nodes "
                      "stay as they are")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return target_space(context) is not None

    def execute(self, context):
        space = target_space(context)
        space.uv_checker.show = not space.uv_checker.show
        _redraw(space)
        from . import chat_toggle
        chat_toggle.redraw_top_bars()   # Director's top-bar button shows the new state.
        return {'FINISHED'}


class ANGELO_OT_uv_checker(Operator):
    bl_idname = "angelo.uv_checker"
    bl_label = "UV Checker"
    bl_description = ("Show a UV checker on the meshes in the 3D view, and choose its pattern, UV map, scale and "
                      "texel density. Materials stay as they are")

    show: BoolProperty(name="Show", description="Turn the UV checker on as the card opens", default=True,
                       options={'SKIP_SAVE'})

    def execute(self, context):
        from . import card_host, uv_checker_card
        space = target_space(context)
        if space is None:
            self.report({'WARNING'}, rpt_("No 3D view to show a UV checker in"))
            return {'CANCELLED'}
        if self.show and not space.uv_checker.show:
            space.uv_checker.show = True
            _redraw(space)
        if card_host.is_active(uv_checker_card.KIND):
            return {'FINISHED'}
        from . import live_target
        held_scene, held_layer = live_target.scene_ref(context.scene), live_target.view_layer_ref(context.view_layer)

        def count_missing(settings):   # The card outlives this call: a load frees the scene it was opened on.
            scene, layer = held_scene(), held_layer()
            return meshes_without_map(scene, layer, settings) if scene and layer else (0, [])
        card_host.request(uv_checker_card.UVCheckerCard(
            space.as_pointer(), uv_map_names(context.scene, context.view_layer), count_missing))
        card_host.pump(lambda: True)
        return {'FINISHED'}


def draw_buttons(layout, space, compact=True):
    """The toggle (pressed while on, the checker glyph) and its settings button, side by side."""
    from . import glyph_tint, render_glyphs
    on = bool(space.uv_checker.show)
    row = layout.row(align=True)
    row.operator(ANGELO_OT_uv_checker_toggle.bl_idname, text="", depress=on,
                 icon_value=render_glyphs.icon_id(GLYPH, glyph_tint.colour("tool", on)))
    row.operator(ANGELO_OT_uv_checker.bl_idname, text="", icon='DOWNARROW_HLT').show = False
    return on


def draw_director(layout, context):
    """Director's top bar, after Blender's menus (``director.draw_topbar``): the Director view's checker."""
    window = context.window
    area = largest_view(window) if window is not None else None
    if area is None:
        return
    layout.separator(factor=2.0)
    on = draw_buttons(layout, area.spaces.active)
    drawn[window.as_pointer()] = on


def _draw_header(self, context):
    """Manual mode's 3D view header (appended to ``VIEW3D_HT_header``): this view's checker."""
    if context.space_data is None or context.space_data.type != 'VIEW_3D':
        return
    if context.space_data.shading.type not in {'SOLID', 'MATERIAL'} and not context.space_data.uv_checker.show:
        return   # Drawn only in Solid and Material Preview: offered there, or while on to turn it off.
    draw_buttons(self.layout, context.space_data)


def _draw_view_menu(self, _context):
    """The 3D view's View menu: both commands, so F3 (menu search, which reads the 3D view's menus even while
    Director hides its header) finds them by name."""
    layout = self.layout
    layout.separator()
    layout.operator(ANGELO_OT_uv_checker_toggle.bl_idname)
    layout.operator(ANGELO_OT_uv_checker.bl_idname, text="UV Checker…")


CLASSES = (ANGELO_OT_uv_checker_toggle, ANGELO_OT_uv_checker)


def register():
    from . import chat_destinations
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(_draw_header)
    bpy.types.VIEW3D_MT_view.append(_draw_view_menu)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "uv_checker", TITLE, MENU_WHY, glyph=GLYPH, order=48, section=n_("Inspect"),
        operator=ANGELO_OT_uv_checker.bl_idname, owner="uv_checker"))


def unregister():
    from . import chat_destinations, uv_checker_card
    chat_destinations.unregister("uv_checker")
    uv_checker_card.close()
    bpy.types.VIEW3D_HT_header.remove(_draw_header)
    bpy.types.VIEW3D_MT_view.remove(_draw_view_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
