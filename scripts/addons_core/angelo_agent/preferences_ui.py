# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Preferences navigation compatibility for the installed, unmodified binary.

The compiled fork has a real AI_PROVIDERS RNA section. Until that build exists,
the same native panels are surfaced through a temporary navigation enum. Nothing
is written into Blender's installed scripts or the user's preferences file.
"""

import bpy
from bpy.props import EnumProperty
from bpy.types import Panel

from .ui_text import n_

_items = []
_native = False
_selected = False
_navigation_draw = None
_addons_draw = None
AI_PROVIDERS_VALUE = 20


def native_section():
    return "AI_PROVIDERS" in bpy.context.preferences.bl_rna.properties["active_section"].enum_items.keys()


def redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()


def is_selected():
    return bpy.context.preferences.active_section == "AI_PROVIDERS" if _native else _selected


def select():
    global _selected
    if _native:
        bpy.context.preferences.active_section = "AI_PROVIDERS"
    else:
        _selected = True
        bpy.context.preferences.active_section = "ADDONS"
    redraw()


def enum_get(_):
    if _selected:
        return AI_PROVIDERS_VALUE
    return bpy.context.preferences.bl_rna.properties["active_section"].enum_items[bpy.context.preferences.active_section].value


def enum_set(_, value):
    global _selected
    _selected = value == AI_PROVIDERS_VALUE
    section = "ADDONS" if _selected else next(i[0] for i in _items if i and i[0] and i[3] == value)
    bpy.context.preferences.active_section = section
    redraw()


def draw_navigation(self, context):
    self.layout.prop(context.space_data, "search_filter", icon='VIEWZOOM', text="")
    self.layout.separator(factor=0.1)
    col = self.layout.column()
    col.scale_x = col.scale_y = 1.3
    col.prop(context.window_manager, "angelo_preferences_section", expand=True)


def draw_addons(self, context):
    if not _selected:
        _addons_draw(self, context)


class ANGELO_PT_ai_providers_compat(Panel):
    bl_label = "AI Providers"
    bl_space_type = 'PREFERENCES'
    bl_region_type = 'WINDOW'
    bl_context = "addons"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return _selected

    def draw(self, context):
        from .ui import draw_ai_providers
        draw_ai_providers(self.layout, context)


def register():
    global _native, _navigation_draw, _addons_draw, _selected
    _native, _selected = native_section(), False
    if _native:
        return
    _items.clear()
    for item in bpy.context.preferences.bl_rna.properties["active_section"].enum_items:
        if item.identifier in {"WORKSPACE_CONFIG", "WORKSPACE_ADDONS", "WORKSPACE_KEYMAPS", "EXPERIMENTAL", "DEVELOPER_TOOLS"}:
            continue
        _items.append((item.identifier, item.name, item.description, item.value) if item.identifier else None)
        if item.identifier == "SYSTEM":
            _items.append(("AI_PROVIDERS", n_("AI Providers"), n_("AI subscriptions and API keys"), AI_PROVIDERS_VALUE))
    bpy.types.WindowManager.angelo_preferences_section = EnumProperty(
        name="Preferences Section", items=_items, get=enum_get, set=enum_set,  # i18n: its name is never shown
        options={'SKIP_SAVE'})
    bpy.utils.register_class(ANGELO_PT_ai_providers_compat)
    _navigation_draw = bpy.types.USERPREF_PT_navigation_bar.draw
    _addons_draw = bpy.types.USERPREF_PT_addons.draw
    bpy.types.USERPREF_PT_navigation_bar.draw = draw_navigation
    bpy.types.USERPREF_PT_addons.draw = draw_addons
    redraw()


def unregister():
    global _selected
    if not _native and _navigation_draw:
        bpy.types.USERPREF_PT_navigation_bar.draw = _navigation_draw
        bpy.types.USERPREF_PT_addons.draw = _addons_draw
        bpy.utils.unregister_class(ANGELO_PT_ai_providers_compat)
        del bpy.types.WindowManager.angelo_preferences_section
    _selected = False
    _items.clear()
    redraw()
