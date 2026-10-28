# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Place Character: the Character step's models put together in the scene as one undoable step (``character_compose``).

The templates themselves (Add ▸ Templates and every other entry point) are created by ``flow_open``'s
``angelo.flow_create`` from ``flow_templates``.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import flow_host, flow_nodes
from .ui_text import iface_, plural_, rpt_


class ANGELO_OT_flow_place_character(Operator):
    """Put the generated parts together on the body as separate objects at the 3D cursor (undoable)"""
    bl_idname = "angelo.flow_place_character"
    bl_label = "Place Character"
    bl_options = {'UNDO', 'INTERNAL'}
    tree: StringProperty(options={'SKIP_SAVE'})
    node: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from . import character_compose
        from .studio_host import import_glb
        tree = bpy.data.node_groups.get(self.tree)
        if tree is None or tree.bl_idname != flow_nodes.TREE:
            self.report({'ERROR'}, rpt_("That flow no longer exists"))
            return {'CANCELLED'}
        state = flow_host.refresh(tree)["states"].get(self.node) or {}
        if state.get("state") != "READY":
            self.report({'ERROR'}, rpt_("Run the parts first; the body's model isn't ready"))
            return {'CANCELLED'}
        store = flow_host.library()

        def model_path(identity):
            asset = store.assets.get(identity)
            if not asset or asset.get("status") != "ready" or not asset.get("files"):
                raise character_compose.ComposeError(rpt_("A chosen part model is missing from the library"))
            return store.checked_file(asset, asset["files"][0])

        window = context.window
        if context.mode != 'OBJECT':
            self.report({'ERROR'}, rpt_("Leave the current editing mode before placing a character"))
            return {'CANCELLED'}
        try:
            receipt = character_compose.compose(window, state["value"], lambda path: import_glb(window, path),
                                                model_path, name=tree.name)
        except (character_compose.ComposeError, ValueError, RuntimeError, OSError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        flow_host.last_placement[tree.name] = receipt
        message = plural_(receipt["parts"], "Placed a character of {count} part", "Placed a character of {count} parts")
        if receipt["left_out"]:
            message += " · " + iface_("left out: {parts}", parts=", ".join(receipt["left_out"]))
        self.report({'INFO'}, message)
        return {'FINISHED'}


CLASSES = (ANGELO_OT_flow_place_character,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
