# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat language for Blender's native text editor surface, not a Python evaluator."""

import json
import sys
import bpy

from . import document_scene
from .display_text import error_notice
from .request_rows import person_request
from .ui_text import display_notice, iface_, join, n_, plural_

language_id = "angelo"
_versions = {}
_states = {}


ROLE_NAMES = {"user": n_("You"), "assistant": n_("Agent"), "tool": n_("Tool"), "system": n_("Angelo"), "error": n_("Error")}


def _prompt():
    return iface_("Ask >") + " "


def display_text(message):
    """The row as drawn, in the interface language. System and error rows translate their notice template here; the stored ``text`` is never changed."""
    if message.get("notice") and message.get("job"):  # A background job's end, from its live record (job_chat).
        from .job_chat import notice_display
        return notice_display(message)
    if message.get("request_kind") == "angelo_goal":   # A held goal's round (goal_loop): its numbers, never its words.
        from .goal_text import row_display
        return row_display(message.get("goal") or {})
    if message.get("request_kind") == "angelo_event":   # Angelo's own request to the lead (worker_resume).
        return iface_("Angelo continued this chat: its workers have all ended. Not merged into this scene: {parts}",
                      parts=", ".join(message.get("parts") or ()))
    text = display_notice(message) if message["role"] in {"system", "error"} else message["text"]
    if message.get("references"):
        text += "\n" + join(iface_("Reference: {name}", name=r["name"]) for r in message["references"])
    if message["role"] == "activity":
        from .activity_text import friendly_error
        from .chat_view import tool_title
        text = tool_title(message)
        if message.get("result", {}).get("error"):   # A plain headline; the model's raw error is not the person's row.
            text = iface_("{label}: {value}", label=text, value=friendly_error(message["result"]["error"]))
        return text
    if message["role"] == "tool" and ": " in text and not text.startswith("Approval required"):
        return _tool_row(text) or text[:2000]
    if message["role"] == "assistant":
        return text.replace("**", "")
    return text


def _tool_row(text):
    """A tool result row in plain words: what happened and whether Undo covers it, never raw errors or JSON."""
    from .activity_text import friendly_error
    from .agent_workspace_ui import control_lines
    name, payload = text.split(": ", 1)
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("error"):
        return iface_("Not applied: {detail}", detail=friendly_error(data["error"]))
    if name == "angelo_inspect_scene":
        count = data.get("object_count", 0)
        return plural_(count, "Inspected {scene}: {count} object.", "Inspected {scene}: {count} objects.",
                       scene=data.get("scene") or iface_("the scene"))
    if name == "angelo_apply_batch":
        return plural_(len(data.get("operations", [])), "Applied {count} edit. You can undo it.",
                       "Applied {count} edits. You can undo them.")
    if name == "angelo_execute_python":
        lines = [iface_("The Blender Python script finished.")]
        lines += [data["output"]] if data.get("output") else []
        lines += control_lines(data["result"]) if data.get("result") is not None else []
        return "\n".join(lines)
    if data.get("object"):
        return iface_("Updated {object}. You can undo it.", object=data["object"])
    return None


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
        controller = ui.get_controller()
        controller.require_model_chat()  # Before preparing a connection or project brief.
        if controller.voice:
            document_scene.refuse(controller)   # Its scene not shown: refused, never sent (R5-M-1 M-5).
            controller.send_voice_text(text)
            bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
            return {'FINISHED'}
        send_message(controller, text)
    except Exception as error:
        ui.get_controller().emit("message", {"role": "error", **error_notice(error)})
        return {'CANCELLED'}
    bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
    return {'FINISHED'}


def send_message(controller, text, references=None, *, run_chosen_tools=True, submission=None):
    """The person sends ``text`` (Enter, the send arrow, a card's Resend): while the chat's turn runs it is steered
    into the turn at its next safe step or queued after it (``chat_pending.hold``), never refused for the turn and
    never preparing a connection under it; otherwise it starts a request (``submit``). Raises what refused it.
    ``references``: the pictures it carries (default: the message box's)."""
    from . import chat_pending
    policy = {} if run_chosen_tools else {"run_chosen_tools": False}
    if controller.turn_running():
        item = chat_pending.hold(controller, text, references, **policy,
                                 **({"separate": True} if submission is not None else {}))
        if submission is not None:
            submission.queued(item)   # Explicit inbox admission, not an API-return delivery guess.
        return item
    return submit(controller, text, references, **policy,
                  **({"submission": submission} if submission is not None else {}))


def submit(controller, text, references=None, *, pending=(), unsent=None, run_chosen_tools=True,
           submission=None, **send_options):
    """Send ``text`` as a new request of ``controller``'s chat, the one way every message starts a turn.

    The person's Enter, and a pending message sent when a turn ended (``chat_pending.dispatch_due``, with the
    pictures it was sent with as ``references``): the connection prepared, the chat's scene checked, the person's
    chosen tools run, a fresh observation built (on the worker's lane for a worker's chat). Raises what refused it.
    ``pending``: the pending messages it carries; ``unsent(error)`` gets a worker lane's later refusal of them
    instead of the message box (they go back to their own bubbles, never both). ``send_options``: passed on to
    ``Controller.send`` for Angelo's own follow-up requests (``chat_pending.send_or_queue``).
    """
    from . import ui
    ui.prepare_controller(controller)
    document_scene.refuse(controller)   # Its scene not shown: refused before anything is observed or sent.
    performed = None
    from . import worker_routing
    worker_chat = worker_routing.worker_snapshots(controller)   # Bound to a worker, or parked or ended.
    if run_chosen_tools and getattr(controller, "chosen_tools", None) and not worker_chat:
        # The person's chosen tools run first, so the observation shows what they added (chosen_tools.py).
        # They act on this window's scene, so a chat on a worker's document does not run them.
        from . import chosen_tools_host
        performed = chosen_tools_host.perform_for_request(controller, ui.main_window())
    failures = []   # A worker chat's send fails later (on its lane, or once its worker starts again): in its chat.
    def failed(error):
        if submission is not None:
            submission.reject(error)   # This notice owns a refused retry, never the unrelated composer.
            if not worker_chat:
                failures.append(error)
        elif not worker_chat:
            failures.append(error)
        elif unsent is not None:
            unsent(error)
        else:
            worker_routing.unsent(controller, text, error) or controller.emit(
                "message", {"role": "error", **error_notice(error)})
    try:
        # Small bounded observation (see turn_context.py); richer queries stay on demand. A worker's chat
        # observes the worker's document, on its lane (worker_routing.deliver).
        def send(turn):
            if submission is not None:
                submission.before_start(controller)
            if performed:   # A chosen tool's facts for the model (Work on hands), with this request only.
                turn.observation.update(chosen_tools_host.observation_facts(performed))
            # Engines running here, only when that changed for this chat (engine_suggest); its watcher is loaded
            # with the add-on's interface, never in a headless child.
            watcher = sys.modules.get(__package__ + ".engine_suggest_ui")
            engines = watcher.for_request(controller) if watcher is not None and not worker_chat else None
            if engines:
                turn.observation["engines"] = engines
            # Web exports read back in the background since the last request (web_readback), once.
            readbacks = sys.modules.get(__package__ + ".web_readback")
            finished = readbacks.for_request(controller) if readbacks is not None else None
            if finished:
                turn.observation["preflight"] = finished
            controller.send(text, observation=turn.observation, recovery_evidence=turn.recovery_evidence,
                            references=references, pending=pending,
                            **({"submission": submission} if submission is not None else {}),
                            **({"use_composer_choices": False} if not run_chosen_tools else {}), **send_options)
        worker_routing.deliver(controller, ui.main_window(), text, send, failed)
    finally:
        if performed:   # After the person's message row, whether or not the send went through.
            chosen_tools_host.report(controller, performed)
    if failures:
        raise failures[0]


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
        lines = [n_("Angelo agent chat"), "", n_("Ask about the scene, then approve any changes."),
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
            kind = ("ERROR" if role == "error" else "INPUT" if person_request(message) else "INFO"
                    if role in {"system", "tool", "user"} else "OUTPUT")   # Angelo's own request: never the person's.
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
