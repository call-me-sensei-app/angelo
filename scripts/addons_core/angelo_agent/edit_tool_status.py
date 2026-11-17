# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One line in Blender's status bar while Angelo's work waits for the person's operation (``edit_tool_hold``).

A hold is never silent: wherever the waiting work is (a chat turn or a message in the tray, a Library or Fab import,
Media Studio, a texture set, a File > Export > Web, an external app's call or task), the status bar, which every
workspace shows, names what Angelo waits for: "Angelo is waiting for you to finish {tool}". It appears once the hold
has lasted ``edit_tool_hold.SHOW_AFTER`` seconds with work waiting (a quick orbit never flashes it) and goes when the
operation ends; ``edit_tool_hold`` redraws the status bar when it changes. The chat's live row says the same for its
own turn. Break-it review 4, M4.
"""

from .ui_text import iface_, n_

STATUS = n_("Angelo is waiting for you to finish {tool}")   # bpy only in register: the gate's tests import no bpy.


def draw_status(self, context):
    from . import edit_tool_hold
    tool = edit_tool_hold.status_tool()
    if tool is not None:
        row = self.layout.row(align=True)
        row.alert = True
        row.label(text=iface_(STATUS, tool=edit_tool_hold.tool_name(tool)), icon='TIME', translate=False)


def register():
    import bpy
    bpy.types.STATUSBAR_HT_header.append(draw_status)


def unregister():
    import bpy
    try:
        bpy.types.STATUSBAR_HT_header.remove(draw_status)
    except (AttributeError, ValueError):
        pass
