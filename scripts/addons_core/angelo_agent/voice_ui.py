# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's voice control, hidden at 1.0 unless the person opts in.

Until voice can be properly tested, the
microphone is hidden everywhere by default, behind the off-by-default
Experimental voice preference. Even when it is on, the control shows only where
voice can work (``first_run.voice_supported``). The relay itself is unchanged
in ``voice.py``.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import document_scene, voice
from .display_text import shown
from .ui_text import iface_, rpt_


def enabled(preferences):
    return bool(getattr(preferences, "experimental_voice", False))


def mic_visible(controller):
    """Drawn by the chat composer; reads only cached state."""
    from . import ui
    return voice.control_visible(controller, enabled(ui.preferences()))


class ANGELO_OT_voice(Operator):
    bl_idname = "angelo.voice"
    bl_label = "Voice · Experimental"
    bl_description = "Opt-in microphone and speaker relay through the selected native Codex account. Requires runtime access; no API billing fallback"
    microphone: StringProperty(name="Microphone", default="0", description="macOS AVFoundation audio-device index or name", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import ui
        controller = ui.get_controller()
        if controller.voice or controller.status == "Starting voice":
            controller.stop()
            return {'FINISHED'}
        if controller.busy:
            self.report({'INFO'}, rpt_("Stop the current turn before starting voice"))
            return {'CANCELLED'}
        if not enabled(ui.preferences()):
            self.report({'INFO'}, rpt_("Voice is off. Turn on Experimental voice in the Angelo add-on preferences"))
            return {'CANCELLED'}
        if not ui.connection_available():
            self.report({'INFO'}, rpt_("Connect a ChatGPT account in the chat to use voice"))
            return {'CANCELLED'}
        if not controller.selection_provider.startswith("codex_"):
            self.report({'INFO'}, rpt_("Claude Code doesn't expose Codex realtime voice. Choose a Codex connection for this feature."))
            return {'CANCELLED'}
        from .voice import availability
        unavailable = availability()
        if unavailable:
            self.report({'ERROR'}, shown(unavailable))
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=480, confirm_text=iface_("Start voice"))

    def draw(self, context):
        layout = self.layout
        layout.label(text="Experimental native Codex voice connection.")
        layout.label(text="Your microphone streams to your selected provider while active.")
        layout.label(text="No audio is saved locally. Stop turns the microphone off.")
        layout.label(text="Use headphones: echo cancellation is not implemented yet.")
        layout.label(text="Provider voice access, limits and charges apply; no API fallback.")
        layout.prop(self, "microphone")
        layout.label(text="0 is AVFoundation's first microphone; use another index/name if needed.")

    def execute(self, context):
        try:
            from . import ui, worker_routing
            ui.prepare_controller()
            controller = ui.get_controller()
            document_scene.refuse(controller)   # Its scene not shown: no voice (R5-M-1 M-5).
            turn = worker_routing.prepare_turn(controller, ui.main_window(), "")   # The worker's, for a worker's chat.
            controller.start_voice(turn.observation, self.microphone, recovery_evidence=turn.recovery_evidence)
            turn.commit()
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


def register():
    bpy.utils.register_class(ANGELO_OT_voice)


def unregister():
    bpy.utils.unregister_class(ANGELO_OT_voice)
