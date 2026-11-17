# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo.narrow``: every narrowing a chat or the person may want, open to anything (doc/angelo/grant_operators.rst,
"Scopes"; the C guard review, 2026-11-15, SHOULD-6). From an agent script, a choice the ledger holds (Ask or Read only,
script trust, Always allow scripts) is refused by C, which takes no write from a script in either direction (the Fusion
break-it review, 2026-11-15, G2): the operator reports that and cancels, having changed nothing.

The operators that grant are refused to an agent script as a whole (C's denied list), and none of them decides from
Python state whether a call only narrows (SHOULD-3: a script could make a widening call look narrowing). So narrowing
has its own operator, which is not a grant and can only narrow: Ask before changes or Read only, Fast off, voice
stopped, the engine link off, this chat's script trust off, Always allow scripts off.
"""

import bpy
from bpy.props import EnumProperty

WHAT = (("APPROVAL_ASK", "Ask before changes", ""), ("APPROVAL_READ_ONLY", "Read only", ""),
        ("FAST_OFF", "Fast off", ""), ("VOICE_STOP", "Stop voice", ""), ("ENGINE_LINK_OFF", "Engine link off", ""),
        ("TRUST_OFF", "Stop trusting this chat's scripts", ""), ("ALWAYS_ALLOW_OFF", "Always allow scripts off", ""))


class ANGELO_OT_narrow(bpy.types.Operator):
    bl_idname = "angelo.narrow"
    bl_label = "Take Back a Permission"
    bl_options = {'INTERNAL'}
    what: EnumProperty(items=WHAT, options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            self._narrow()
        except PermissionError as refusal:   # C's (G2): every writer asks C first, so nothing else changed.
            self.report({'ERROR'}, str(refusal))
            return {'CANCELLED'}
        return {'FINISHED'}

    def _narrow(self):
        from . import ui
        controller = ui.get_controller()
        what = self.what
        if what in {"APPROVAL_ASK", "APPROVAL_READ_ONLY"}:
            controller.set_options(approval="ASK" if what == "APPROVAL_ASK" else "READ_ONLY")
        elif what == "FAST_OFF":
            controller.set_options(fast=False)
        elif what == "VOICE_STOP":
            if controller.voice or controller.status == "Starting voice":
                controller.stop()
        elif what == "ENGINE_LINK_OFF":
            from . import engine_link
            from .engine_link_ui import remember
            engine_link.stop()
            remember(False)
        elif what == "TRUST_OFF":
            controller.set_trusted_scripts(False)
        elif what == "ALWAYS_ALLOW_OFF":
            from . import always_allow
            always_allow.set_enabled(False)


def register():
    bpy.utils.register_class(ANGELO_OT_narrow)


def unregister():
    bpy.utils.unregister_class(ANGELO_OT_narrow)
