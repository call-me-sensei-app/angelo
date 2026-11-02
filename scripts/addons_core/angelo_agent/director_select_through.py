# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Select Through in Director's top bar (doc/angelo/select_through.rst, "Director").

Director hides the 3D view's header, where Manual mode shows the toggle beside X-ray, so its top bar offers the same
toggle after Blender's menus, while the Director view is in a mode where it changes selection (mesh Edit Mode, the
paint vertex mask, Particle Edit). One state: the button is the Director view's own ``use_select_through``, which
the header, the shading pie (Z), this button and the chat bar's + menu row (``ANGELO_OT_select_through``, which
toggles the same view's) all show and change. No key of its own.
"""

from types import SimpleNamespace

import bpy

from . import live_windows
from .ui_text import n_, rpt_

drawn = {}   # window pointer -> (offered, on) at its last top-bar draw; GUI suites read it.


def view_space(window):
    """The Director view: the window's largest 3D view (Director is that view and the chat)."""
    views = [area for area in live_windows.areas(window) if area.type == 'VIEW_3D']
    if not views:
        return None
    return max(views, key=lambda area: area.width * area.height).spaces.active


def draw(layout, context):
    """The toggle after the menus, greyed where X-ray already selects through (the header's own rule)."""
    from bl_ui.space_view3d import _select_through_applies, _select_through_redundant
    window = context.window
    space = view_space(window) if window is not None else None
    offered = space is not None and _select_through_applies(context)
    if window is not None:
        drawn[window.as_pointer()] = (offered, bool(offered and space.use_select_through))
    if not offered:
        return
    layout.separator(factor=2.0)
    row = layout.row()
    row.active = not _select_through_redundant(SimpleNamespace(mode=context.mode, space_data=space))
    row.prop(space, "use_select_through", icon='ANGELO_SELECT_THROUGH')


TITLE = n_("Select Through")
WHY = n_("Box, lasso and circle selection reach hidden parts too, while the view stays solid (Edit Mode)")
ON, OFF = n_("Select Through is on"), n_("Select Through is off")


class ANGELO_OT_select_through(bpy.types.Operator):
    # The chat bar's + menu row: the Director view's toggle (the header's and the top bar's state). A comment, not a
    # docstring: bl_description is the tooltip.
    bl_idname = "angelo.select_through"
    bl_label = "Select Through"
    bl_description = ("Let box, lasso and circle selection reach hidden elements in solid display, as in X-ray, in "
                      "the main 3D view")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        space = view_space(context.window) if context.window is not None else None
        if space is None:
            return {'CANCELLED'}
        space.use_select_through = not space.use_select_through
        self.report({'INFO'}, rpt_(ON if space.use_select_through else OFF))
        return {'FINISHED'}


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_select_through)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: Angelo's main navigation.
        "select_through", TITLE, WHY, glyph="select", order=46, section=n_("Inspect"), operator=ANGELO_OT_select_through.bl_idname,
        owner="director_select_through"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("select_through")
    bpy.utils.unregister_class(ANGELO_OT_select_through)
