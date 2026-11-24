# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""**Rules…**: the + menu footer's way to the chat's Rules page (``rules_sheet``; doc/angelo/agent_chat.rst, "Rules").

The operator only opens the page in a chat area (the one it runs in, else the first chat shown): saving or using rules
takes a click on the page itself, which no script can make, so a script that calls this operator decides nothing.
"""

import bpy
from bpy.types import Operator

from .ui_text import n_, rpt_

TITLE = n_("Rules…")
NO_CHAT = n_("Open the chat to write rules")
WHY = n_("Standing instructions Angelo follows in every request: for this project, or for all of them")


def _chat_area(context):
    from . import console, live_windows
    if context.area is not None and console.is_chat(context.area):
        return context.area
    return next((area for _window, area in live_windows.window_areas(context.window_manager) if console.is_chat(area)),
                None)


class ANGELO_OT_chat_rules(Operator):
    bl_idname = "angelo.chat_rules"
    bl_label = "Rules"
    bl_description = "Write standing instructions the agent follows in every request"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from . import chat_view, rules_sheet, ui
        area = _chat_area(context)
        if area is None:
            self.report({'WARNING'}, rpt_(NO_CHAT))
            return {'CANCELLED'}
        rules_sheet.open_sheet(chat_view._views.setdefault(area.as_pointer(), {}), ui.get_controller())
        area.tag_redraw()
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_chat_rules)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "rules", TITLE, WHY, glyph="feedback", order=88,
        operator=ANGELO_OT_chat_rules.bl_idname, placement="footer", owner="rules_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("rules")
    bpy.utils.unregister_class(ANGELO_OT_chat_rules)
