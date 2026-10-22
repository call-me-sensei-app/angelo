# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plain-language transcript titles for tool activity rows. No ``bpy``: host-testable.

Titles are display text only; they are never sent to a model. Exact tool
names, arguments and results stay available under each row's Details. Every
title and state is a whole phrase marked with ``n_``; they are joined through
named-placeholder templates and translated here, at display time
(``ui_text.py``). Names, models and numbers fill placeholders untranslated.
"""

from .canvas_rtl import visual
from .legacy_names import current_tool_name
from .sessions import UNTITLED
from .ui_text import iface_, n_

NATIVE = {"angelo_scene_program": n_("Scene edit"), "angelo_apply_recipe": n_("Recipe edit"),
          "angelo_expand_recipe": n_("Planned a recipe"), "angelo_accept_preview": n_("Apply preview"),
          "angelo_discard_preview": n_("Discard preview"), "angelo_inspect_preview": n_("Inspect preview"),
          "angelo_memory_update": n_("Update Project Brief"), "angelo_memory_read": n_("Read Project Brief"),
          "angelo_agent_metrics": n_("Read local performance")}
STUDIO = {"catalog": n_("Read Media Studio models"), "list": n_("Searched the media library"),
          "asset": n_("Inspected media asset"), "generated": n_("Generated media"), "generate": n_("Generating media"),
          "enhance": n_("Enhanced prompt"), "create_folder": n_("Created library folder"),
          "move_asset": n_("Organized library asset"), "import_image": n_("Imported library image"),
          "import_3d": n_("Imported model into scene"), "export": n_("Exported media output"),
          "stop": n_("Stopped local monitoring")}
OTHER = {"angelo_inspect_scene": n_("Read scene"), "angelo_create_cube": n_("Added an object"),
         "angelo_read_data": n_("Read scene data"), "angelo_blend_file": n_("Checked a .blend file"),
         "angelo_inspect_sculpt": n_("Inspected sculpt surface"), "angelo_prepare_sculpt": n_("Prepared sculpt target"),
         "angelo_sculpt_batch": n_("Sculpted a brush pass"), "angelo_sculpt_view": n_("Viewed sculpt from fresh angles"),
         "angelo_sculpt_target": n_("Located brush targets on the surface"),
         "angelo_sculpt_program": n_("Sculpted a sequence of brush passes"),
         "angelo_sculpt_region": n_("Defined a sculpt region"),
         "angelo_sculpt_resolution": n_("Adjusted sculpt working resolution"),
         "angelo_sculpt_review": n_("Compared sculpt shape with reference targets"),
         "angelo_view": n_("Viewed the scene"), "angelo_set_transform": n_("Edited object transform"),
         "angelo_apply_batch": n_("Edited scene"), "angelo_execute_python": n_("Ran Blender Python"),
         "angelo_read_reference": n_("Read attached file"), "web_search": n_("Searched the web"),
         "WebSearch": n_("Searched the web"), "WebFetch": n_("Read a web page"), "angelo_list_files": n_("Listed files"),
         "angelo_read_file": n_("Read a file"), "angelo_write_file": n_("Edited a file"),
         "angelo_delete_file": n_("Deleted a file"), "angelo_job_start": n_("Started a background job"),
         "angelo_job_cancel": n_("Cancelled a background job")}
# The live row under the transcript while a turn runs, by the controller's status (an English key).
# "Preparing the next steps": the runtime is writing its plan (claude_stream.STATUS_PLAN).
# "Provider busy, retrying…": Codex retries a throttled or overloaded turn itself (codex_retry.RETRYING).
# The host's own statuses name the host's work, never "Thinking" (live_progress reads a running step's kind first).
LIVE = {"Connecting": n_("Connecting"), "Preparing the next steps": n_("Preparing the next steps"),
        "Reading file metadata": n_("Reading file metadata"), "Provider busy, retrying…": n_("Provider busy, retrying…"),
        "Staging native program": n_("Working in Blender"),
        "Waiting for animation playback to stop": n_("Waiting for animation playback to stop"),
        "Generating media": n_("Generating media"), "Enhancing prompt": n_("Enhancing the prompt"),
        "Stopping": n_("Stopping"), "Waiting for a render": n_("Waiting for a render"),
        "Rendering a look": n_("Rendering a look"), "Approval required": n_("Waiting for your approval"),
        "Approve collaborator usage": n_("Waiting for your approval"),
        "External work": n_("Working for a connected app"), "External read": n_("Reading for a connected app"),
        "Event queue full; reconnect required": n_("Reconnect required")}
# Statuses that never label a running turn's live row: the turn is over, not started, or a voice turn (its own text).
NOT_LIVE = frozenset({"New chat", "Interrupted", "Failed", "Completed", "Idle", "Disconnected", "Off", "Voice active",
                      "Voice · agent working", "Starting voice"})
STATES = {"queued": n_("queued"), "running": n_("running"), "working": n_("working"),
          "approval": n_("needs your approval"), "usage": n_("needs usage approval"), "failed": n_("failed"),
          "reverted": n_("reverted"), "preview": n_("preview, not applied yet"), "committed": n_("applied, Undo available")}


def _with_state(action, state):
    return iface_("{action} · {state}", action=action, state=iface_(STATES[state])) if state else action


def live_label(status):
    """What a running turn is doing, for the live row: every other status reads as thinking.

    A plan being written may carry what it will make (plan_progress): shown after the label as data.
    """
    from .codex_retry import WAITING
    from .plan_progress import split
    status, detail = split(status)
    if status == WAITING and detail.isdigit():  # The one resend's wait, counted down (codex_retry.waiting).
        return iface_("Provider busy — retrying in {seconds} s", seconds=int(detail))
    label = iface_(LIVE.get(status, n_("Thinking")))
    return iface_("{action} · {state}", action=label, state=detail) if detail else label


def tool_title(message):
    # Chats saved before the rename record pre-rename tool names.
    name = current_tool_name(message.get("tool", ""))
    status = message.get("status")
    if name in NATIVE:
        result = message.get("result", {})
        title = iface_(NATIVE[name])
        if result.get("status") in {"preview", "committed"}:
            return _with_state(title, result["status"])
        if result.get("progress"):
            from .agent_workspace_ui import job_progress_text
            title = iface_("{action} · {progress}", action=title, progress=job_progress_text({"progress": result["progress"]}))
        return _with_state(title, {"queued": "queued", "running": "working", "approval": "approval", "failed": "failed"}.get(status))
    if name == "angelo_delegate":
        target = message.get("result", {}).get("collaborator") or message.get("collaborator") or message.get("arguments", {}).get("model")
        if target:
            title = iface_("Consulted {model}", model=target) if status == "completed" else iface_("Consult {model}", model=target)
        else:
            title = iface_("Consulted another model") if status == "completed" else iface_("Consult another model")
        return _with_state(title, {"queued": "queued", "running": "running", "approval": "usage", "failed": "failed"}.get(status))
    from .studio_spec import STUDIO_NAMES
    if name in STUDIO_NAMES:
        result = message.get("result", {})
        key = name.removeprefix("angelo_studio_")
        key = "generated" if key == "generate" and result.get("status") == "ready" else key
        title = iface_(STUDIO.get(key, n_("Media Studio")))
        if status == "running" and result.get("progress") is not None:
            title = iface_("{action} · {percent}%", action=title, percent=result["progress"])
        return _with_state(title, {"queued": "queued", "approval": "approval", "failed": "failed"}.get(status))
    title = iface_(OTHER[name]) if name in OTHER else name or iface_("Used a tool")
    if message.get("collaborator"):
        title = iface_("{collaborator} · {action}", collaborator=message["collaborator"], action=title)
    if message.get("reverted"):
        return _with_state(title, "reverted")
    return _with_state(title, {"queued": "queued", "running": "running", "approval": "approval", "failed": "failed"}.get(status))


def session_title(record):
    """A conversation's title as shown: the person's first request, or "New chat" in the interface language.

    Blender's menus draw left to right without shaping: a request typed in Arabic is shaped and put in visual order.
    """
    return iface_(UNTITLED) if record["title"] == UNTITLED else visual(record["title"])
