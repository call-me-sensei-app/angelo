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


def _markup(window, controller, request):
    """The picture viewer for the person, on the chat's picture or the 3D view now (``markup_ui``)."""
    if window is None or controller is None or getattr(controller, "references", None) is None:
        raise RefusalError("This chat has no window to show a picture in", code="UNSUPPORTED")
    from . import markup_message, markup_overlay, markup_ui
    try:
        if "reference_id" in request:
            picture = markup_ui.reference_record(controller, request["reference_id"])
        else:
            from . import document_scene   # Never the shown scene when it isn't the chat's (N8).
            if document_scene.refusal(controller) is not None:
                raise RefusalError("This chat's scene isn't the one shown, so its 3D view can't be marked up; "
                                   "ask the person to switch back to it", code="VALIDATION")
            markup_message.room(controller)
            picture = markup_ui.capture_view(controller, window)
            controller.attachments.append(picture)
            controller.version += 1
        shown = markup_overlay.open_reference(controller, picture, keyboard=False)   # The person may be typing.
    except (OSError, ValueError) as error:
        raise RefusalError("The picture viewer could not open: %s" % error, code="VALIDATION") from None
    return {"feature": "markup", "open": bool(shown), "reference_id": picture["id"],
            "next": "The person draws and comments on the picture and sends it with their instructions; it arrives as "
                    "the marked picture, the original and the numbered comments. Nothing is sent until they send."}


def execute(window, name, request, controller=None):
    if request["feature"] == "ENGINE_LINK":
        return _engine_link(window, request)
    if request["feature"] == "MARKUP":
        return _markup(window, controller, request)
    raise RefusalError("Unknown feature", code="VALIDATION")
