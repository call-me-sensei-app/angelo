# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""UI-only branding for the unbuilt-fork preview. No scene/application patching.

The compiled fork gets these icons from its SVG datafiles. This compatibility
layer uses the same exported artwork in a stock Blender scratch process, so
the top-left icon can be verified without modifying the installed application.
"""

from pathlib import Path
import bpy
import bpy.utils.previews

from . import live_windows
from .ui_text import iface_, rpt_

_icons = None
_original_menus = None
_original_app_menu = None
_original_source_menus = None
_original_source_app_menu = None
_last_icon = None
_exports = Path(__file__).resolve().parents[3] / "release/branding/angelo/exports"


class AngeloDialog:
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=500, confirm_text=iface_("Close", "Angelo"))

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.alignment = 'CENTER'
        row.template_icon(icon_value=_icons["app"].icon_id, scale=6)
        row = layout.row()
        row.alignment = 'CENTER'
        row.label(text="Angelo")
        layout.label(text="Native AI-assisted 3D creation")
        layout.separator()
        layout.label(text=iface_("Based on Blender {version}", version=bpy.app.version_string), translate=False)
        layout.label(text="Blender Foundation and contributors · GPL")
        layout.label(text="Preview: installed Blender and native icon enums are unchanged.")

    def execute(self, context):
        return {'FINISHED'}


# Operator idnames keep their pre-rename spelling: saved keymaps and menus refer to them.
class ANGELO_OT_about(AngeloDialog, bpy.types.Operator):
    bl_idname = "angelo.about"
    bl_label = "About Angelo"
    bl_description = "Angelo preview branding; Blender engine credits and license remain available"


class ANGELO_OT_welcome(bpy.types.Operator):
    """Opens the introduction (intro.py), so there is one welcome, not a second About box."""
    bl_idname = "angelo.welcome"
    bl_label = "Welcome to Angelo"
    bl_description = "What Director and Manual mode are, how to start, and short demos"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        if not hasattr(bpy.types, "ANGELO_OT_intro"):
            self.report({'INFO'}, rpt_("Enable the Angelo Agent add-on to see the introduction"))
            return {'CANCELLED'}
        return bpy.ops.angelo.intro('INVOKE_DEFAULT')


def header_draw(self, context):
    global _last_icon
    layout = self.layout
    if getattr(context.area, "show_menus", False):
        text = context.preferences.themes[0].user_interface.wcol_menu.text
        level = sum(text[:3]) / 3
        level /= 255 if max(text[:3]) > 1 else 1
        name = "white" if level > .5 else "dark"
        _last_icon = name
        layout.menu("TOPBAR_MT_blender", text="", icon_value=_icons[name].icon_id)
    else:
        layout.menu("TOPBAR_MT_blender", text="Angelo")
    for menu in ("file", "edit", "render", "window", "help"):
        layout.menu("TOPBAR_MT_" + menu)


def app_menu_draw(self, context):
    layout = self.layout
    layout.operator("angelo.welcome", text="Welcome to Angelo")
    layout.operator("angelo.about", text="About Angelo")
    layout.separator()
    layout.operator("preferences.app_template_install", text="Install Application Template...")
    layout.separator()
    layout.menu("TOPBAR_MT_blender_system")


def install():
    global _icons, _original_menus, _original_app_menu, _original_source_menus, _original_source_app_menu
    if _icons is not None:
        return
    _icons = bpy.utils.previews.new()
    for name, file in (("white", "angelo-white-32.png"), ("dark", "angelo-dark-32.png"), ("app", "angelo-256.png")):
        _icons.load(name, str(_exports / file), 'IMAGE')
    for cls in (ANGELO_OT_about, ANGELO_OT_welcome):
        bpy.utils.register_class(cls)
    from bl_ui import space_topbar
    _original_source_menus = space_topbar.TOPBAR_MT_editor_menus.draw
    _original_source_app_menu = space_topbar.TOPBAR_MT_blender.draw
    _original_menus, _original_app_menu = _original_source_menus, _original_source_app_menu
    # Menu draw callbacks are cached in RNA at registration. Replacing a Python
    # attribute alone doesn't update layout.menu_contents() in the native header.
    for cls in (space_topbar.TOPBAR_MT_editor_menus, space_topbar.TOPBAR_MT_blender):
        bpy.utils.unregister_class(cls)
    space_topbar.TOPBAR_MT_editor_menus.draw = header_draw
    space_topbar.TOPBAR_MT_blender.draw = app_menu_draw
    for cls in (space_topbar.TOPBAR_MT_editor_menus, space_topbar.TOPBAR_MT_blender):
        bpy.utils.register_class(cls)
    for _window, area in live_windows.window_areas(bpy.context.window_manager):
        area.tag_redraw()


def uninstall():
    global _icons, _original_menus, _original_app_menu, _original_source_menus, _original_source_app_menu
    if _icons is None:
        return
    from bl_ui import space_topbar
    for cls in (space_topbar.TOPBAR_MT_editor_menus, space_topbar.TOPBAR_MT_blender):
        bpy.utils.unregister_class(cls)
    space_topbar.TOPBAR_MT_editor_menus.draw = _original_source_menus
    space_topbar.TOPBAR_MT_blender.draw = _original_source_app_menu
    for cls in (space_topbar.TOPBAR_MT_editor_menus, space_topbar.TOPBAR_MT_blender):
        bpy.utils.register_class(cls)
    for cls in (ANGELO_OT_welcome, ANGELO_OT_about):
        bpy.utils.unregister_class(cls)
    bpy.utils.previews.remove(_icons)
    _icons = _original_menus = _original_app_menu = None
    _original_source_menus = _original_source_app_menu = None
