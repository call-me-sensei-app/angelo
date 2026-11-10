# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_app_feature`` on the host (``app_feature_spec``): the agent's route to what a feature's + menu row does,
after the person approved the card. Results are English facts for the model, never a path."""

import bpy

from .refusal_codes import RefusalError


def _engine_link(window, request):
    from . import engine_link, engine_link_ui
    action = request["action"]
    if action == "OFF":
        was = engine_link.running()
        engine_link.stop()
        engine_link_ui.remember(False)
        return {"feature": "engine_link", "on": False, "was_on": was}
    if action == "ON":
        if not engine_link.running():
            failed = engine_link_ui.turn_on()
            if failed:
                engine_link_ui.show_notice("alert", failed)   # The person reads the reason in their language.
                raise RefusalError("The engine live link could not start (another Angelo may have it, or the port "
                                   "is taken); the person sees why", code="WRITER_BUSY")
        return {"feature": "engine_link", "on": True,
                "next": "In Godot, Unity or Unreal Engine (with Angelo Link in the project), Edit in Angelo on a "
                        "model opens it here; saving here updates it there. A Three.js page with "
                        "@call-me-sensei/angelo in its Vite dev server asks the same way (Alt-click a tracked model)."}
    if window is None:
        raise RefusalError("This chat has no window to open the folder picker in", code="UNSUPPORTED")
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.godot_link_install('INVOKE_DEFAULT', engine=request["engine"])
    return {"feature": "engine_link", "folder_picker_open": True, "engine": request["engine"],
            "next": "The person chooses the project folder; Angelo copies Angelo Link there, turns the link on and "
                    "tells them the engine's last step (Godot: enable it in Project Settings > Plugins; Unreal: "
                    "enable it in Edit > Plugins and restart; Unity loads it by itself; WEB: Angelo copies nothing, "
                    "puts the npm install command on the clipboard and names the Vite plugin to add)."}


def execute(window, name, request):
    if request["feature"] == "ENGINE_LINK":
        return _engine_link(window, request)
    raise RefusalError("Unknown feature", code="VALIDATION")
