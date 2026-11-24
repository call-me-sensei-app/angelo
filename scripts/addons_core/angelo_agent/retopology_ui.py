# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Retopology from the chat bar's + menu (doc/angelo/retopology.rst).

The tool itself is native: Retopology (``object.retopology``, in Properties > Mesh > Remesh beside QuadriFlow, and F3)
makes clean quad topology for the active mesh and writes it to a new object. This module is its row in the + menu's
Model group (``chat_destinations.MODEL_GROUP``; the top level has no room for another tile): ``angelo.retopology``
opens that operator's options on the active mesh, or says what to select first, since a menu click has no mesh of its
own. The agent reaches the same operator through the ``RETOPOLOGY`` job (``job_retopology``).
"""

import bpy
from bpy.types import Operator

from .ui_text import iface_, n_

MENU_LABEL = n_("Retopology")
MENU_WHY = n_("Clean quads for a scanned, sculpted or generated mesh, as a new object")
NO_MESH = n_("Select a mesh in Object Mode to retopologize it")


def target(context):
    """The active mesh a click retopologizes, or None (no active object, not a mesh, or not in Object Mode)."""
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is None or obj.type != 'MESH' or obj.mode != 'OBJECT':
        return None
    return obj


class ANGELO_OT_retopology(Operator):
    bl_idname = "angelo.retopology"
    bl_label = "Retopology"
    bl_description = "Make clean quad topology for the active mesh, as a new object"
    bl_options = {'REGISTER', 'INTERNAL'}   # A launcher: F3 lists the native Retopology itself.

    def invoke(self, context, event):
        if target(context) is None:
            self.report({'INFO'}, iface_(NO_MESH))
            return {'CANCELLED'}
        # The native operator's options (faces, symmetry, sharp edges) with Retopologize to confirm; it pushes its own
        # Undo step when the result is in place. Its dialog is its own: this launcher is done once it is open (returning
        # the dialog's RUNNING_MODAL here, with no modal handler of ours, left this operator unfreed at exit).
        started = bpy.ops.object.retopology('INVOKE_DEFAULT')
        return {'FINISHED'} if started & {'RUNNING_MODAL', 'FINISHED'} else {'CANCELLED'}

    def execute(self, context):
        return self.invoke(context, None)


def register():
    from . import chat_destinations
    bpy.utils.register_class(ANGELO_OT_retopology)
    chat_destinations.register(chat_destinations.Feature(   # The chat bar's + menu: a row of its Model group.
        "retopology", MENU_LABEL, MENU_WHY, glyph="base_mesh", order=46, section=chat_destinations.TOOLS,
        operator="angelo.retopology", owner="retopology_ui"))


def unregister():
    from . import chat_destinations
    chat_destinations.unregister("retopology")
    bpy.utils.unregister_class(ANGELO_OT_retopology)
