# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat language for Blender's native text editor surface, not a Python evaluator."""

import json
import bpy

from .display_text import error_notice
from .ui_text import display_notice, iface_, join, n_

language_id = "angelo"
_versions = {}
_states = {}


ROLE_NAMES = {"user": n_("YOU"), "assistant": n_("AGENT"), "tool": n_("BLENDER"), "system": n_("ANGELO"), "error": n_("ERROR")}


def _prompt():
    return iface_("Ask >") + " "


def display_text(message):
    """The row as drawn, in the interface language. System and error rows translate their notice template here; the stored ``text`` is never changed."""
    if message.get("notice") and message.get("job"):  # A background job's end, from its live record (job_chat).
        from .job_chat import notice_display
        return notice_display(message)
    text = display_notice(message) if message["role"] in {"system", "error"} else message["text"]
    if message.get("references"):
        text += "\n" + join(iface_("Reference: {name}", name=r["name"]) for r in message["references"])
    if message["role"] == "activity":
        from .chat_view import tool_title
        text = tool_title(message)
        if message.get("result", {}).get("error"):
            text += ": " + message["result"]["error"]
        return text
    if message["role"] == "tool" and ": " in text and not text.startswith("Approval required"):
        name, payload = text.split(": ", 1)
        try:
            data = json.loads(payload)
        except ValueError:
            return text[:2000]
        if data.get("error"):
            return iface_("Not applied: {detail}", detail=data["error"])
        ms = data.get("native_exec_ms", 0)
        if name == "angelo_inspect_scene":
            return iface_("Inspected {scene}: {count} objects. ({ms:g} ms native)", scene=data.get("scene", "scene"), count=data.get("object_count", 0), ms=ms)
        if name == "angelo_apply_batch":
            return iface_("Applied {count} native edits. Undo is available. ({ms:g} ms native)", count=len(data.get("operations", [])), ms=ms)
        if name == "angelo_execute_python":
            return iface_("Blender Python completed. ({ms:g} ms native)", ms=ms) + ("\n" + data["output"] if data.get("output") else "") + ("\n" + json.dumps(data["result"], ensure_ascii=False) if data.get("result") is not None else "")
        if data.get("object"):
            return iface_("Updated {object} at {location}. Undo is available. ({ms:g} ms native)", object=data["object"], location=data.get("location"), ms=ms)
    if message["role"] == "assistant":
        return text.replace("**", "")
    return text


def is_chat(area):
    return area.type == "CONSOLE" and area.spaces.active.language == language_id


def banner(context):
    context.space_data.prompt = _prompt()
    render(context, force=True)
    return {'FINISHED'}


def execute(context, is_interactive=False):
    from . import ui
    space = context.space_data
    if not space.history:
        return {'CANCELLED'}
    text = space.history[-1].body
    try:
        ui.get_controller().require_model_chat()  # Before preparing a connection or project brief.
        if ui.get_controller().voice:
            ui.get_controller().send_voice_text(text)
            bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
            return {'FINISHED'}
        ui.prepare_controller()
        from . import turn_context
        controller = ui.get_controller()
        # Small bounded observation (see turn_context.py); richer queries stay on demand.
        turn = turn_context.prepare(ui.main_window(), controller.state["id"], message=text)
        controller.send(text, observation=turn.observation, recovery_evidence=turn.recovery_evidence)
        # Only a delivered observation is a planning boundary for the change feed.
        turn.commit()
    except Exception as error:
        ui.get_controller().emit("message", {"role": "error", **error_notice(error)})
        return {'CANCELLED'}
    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
    return {'FINISHED'}


def autocomplete(context):
    return {'CANCELLED'}


def copy_as_script(context):
    from .ui import get_controller
    context.window_manager.clipboard = "\n\n".join(f"{m['role']}: {m['text']}" for m in get_controller().state["messages"])
    return {'FINISHED'}


def redraw_chats(controllers):
    """Redraw every chat on the next pump: ``render`` follows each controller's version."""
    for controller in controllers:
        controller.version += 1


def render(context, force=False):
    from .ui import get_controller
    controller = get_controller()
    pointer = context.area.as_pointer()
    version = (controller.state["id"], controller.version)
    if not force and _versions.get(pointer) == version:
        return
    _versions[pointer] = version
    messages = controller.state["messages"]
    state = _states.get(pointer)
    full = force or state is None or state["session"] != controller.state["id"]
    if state and not full:
        shown = state["shown"]
        full = shown > len(messages) or (state["welcome"] and bool(messages))
        full = full or any(display_text(messages[i]) != state["raw"][i] for i in range(max(0, shown - 1)))
        if shown and not full:
            full = not display_text(messages[shown - 1]).startswith(state["last"])
    if full:
        bpy.ops.console.clear(scrollback=True, history=False)
        state = {"session": controller.state["id"], "shown": 0, "raw": [], "last": "", "welcome": not bool(messages)}
    if not messages and full:
        lines = [n_("ANGELO / AGENT CHAT"), "", n_("Ask about the scene, then approve any changes."),
                 n_("This editor resizes the viewport; it never covers it."),
                 "", n_("Connections: Preferences > AI Providers."),
                 n_("Blender Python enables modeling, materials, rigging and animation; review its exact code before applying."),
                 "", n_("Enter sends. Shift+Enter adds a line. View controls docking and Pop Out.")]
        for line in lines:
            bpy.ops.console.scrollback_append(text=iface_(line) if line else line, type="OUTPUT")
    elif messages:
        if not full and state["shown"]:
            previous = state["last"]
            new = display_text(messages[state["shown"] - 1])
            if new != previous:
                # Update the native text line in place instead of rebuilding the
                # entire transcript per streamed token. The input and selection remain intact.
                old_count = len(previous.split("\n"))
                lines = new.split("\n")
                context.space_data.scrollback[-1].body = lines[old_count - 1]
                for line in lines[old_count:]:
                    bpy.ops.console.scrollback_append(text=line, type="OUTPUT")
        # Native textview handles wrapping, selection, scrolling and DPI scaling.
        for message in messages[max(state["shown"], len(messages) - 120):]:
            role = message["role"]
            kind = "ERROR" if role == "error" else "INPUT" if role == "user" else "INFO" if role in {"system", "tool"} else "OUTPUT"
            bpy.ops.console.scrollback_append(text="", type=kind)
            bpy.ops.console.scrollback_append(text=iface_(ROLE_NAMES[role]) if role in ROLE_NAMES else role.upper(), type=kind)
            for line in display_text(message).split("\n"):
                bpy.ops.console.scrollback_append(text=line, type=kind)
    state["shown"] = len(messages)
    state["raw"] = [display_text(m) for m in messages]
    state["last"] = display_text(messages[-1]) if messages else ""
    _states[pointer] = state
    context.space_data.prompt = _prompt()
    context.area.tag_redraw()
