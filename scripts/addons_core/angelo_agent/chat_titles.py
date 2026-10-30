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

# Every tool title has three forms: (done, in progress, not done). A row whose step happened reads in the past
# ("Ran Blender Python"); one in progress (running, a plan being written, a generation in flight) in the
# progressive ("Running Blender Python"); one queued, waiting for approval, declined, stopped or failed names the
# action without claiming it happened ("Run Blender Python"), with its state beside it (the chip, or a suffix here).
# A noun phrase reads true in every state and is one entry. ``title_form`` picks (``happened``, ``in_progress``);
# test_native_activity_text pins every table. "Read" is past and present alike in English but not in translation,
# so a read that did not happen says "Inspect", "Check" or "Open" instead.
NATIVE = {"angelo_scene_program": (n_("Scene edit"),) * 3, "angelo_apply_recipe": (n_("Recipe edit"),) * 3,
          "angelo_expand_recipe": (n_("Planned a recipe"), n_("Planning a recipe"), n_("Plan a recipe")),
          "angelo_accept_preview": (n_("Applied the preview"), n_("Applying the preview"), n_("Apply preview")),
          "angelo_discard_preview": (n_("Discarded the preview"), n_("Discarding the preview"),
                                     n_("Discard preview")),
          "angelo_inspect_preview": (n_("Inspected the preview"), n_("Inspecting the preview"),
                                     n_("Inspect preview")),
          "angelo_memory_update": (n_("Updated the Project Brief"), n_("Updating the Project Brief"),
                                   n_("Update Project Brief")),
          "angelo_memory_read": (n_("Read Project Brief"), n_("Reading the Project Brief"),
                                 n_("Check the Project Brief")),
          "angelo_agent_metrics": (n_("Read local performance"), n_("Reading local performance"),
                                   n_("Check local performance"))}
STUDIO = {"catalog": (n_("Read Media Studio models"), n_("Reading Media Studio models"),
                      n_("Check Media Studio models")),
          "list": (n_("Searched the media library"), n_("Searching the media library"),
                   n_("Search the media library")),
          "asset": (n_("Inspected media asset"), n_("Inspecting a media asset"), n_("Inspect a media asset")),
          # A submitted generation keeps working after its tool row completes: "Generating media" until it is ready.
          "generate": (n_("Generated media"), n_("Generating media"), n_("Generate media")),
          "enhance": (n_("Enhanced prompt"), n_("Enhancing the prompt"), n_("Enhance the prompt")),
          "create_folder": (n_("Created library folder"), n_("Creating a library folder"),
                            n_("Create a library folder")),
          "move_asset": (n_("Organized library asset"), n_("Organizing a library asset"),
                         n_("Organize a library asset")),
          "import_image": (n_("Imported library image"), n_("Importing a library image"),
                           n_("Import a library image")),
          "import_3d": (n_("Imported model into scene"), n_("Importing a model into the scene"),
                        n_("Import a model into the scene")),
          "export": (n_("Exported media output"), n_("Exporting media output"), n_("Export media output")),
          "stop": (n_("Stopped local monitoring"), n_("Stopping local monitoring"), n_("Stop local monitoring"))}
STUDIO_FALLBACK = (n_("Media Studio"),) * 3
OTHER = {"angelo_inspect_scene": (n_("Read scene"), n_("Reading the scene"), n_("Inspect the scene")),
         "angelo_create_cube": (n_("Added an object"), n_("Adding an object"), n_("Add an object")),
         "angelo_read_data": (n_("Read scene data"), n_("Reading scene data"), n_("Inspect scene data")),
         "angelo_blend_file": (n_("Checked a .blend file"), n_("Checking a .blend file"), n_("Check a .blend file")),
         "angelo_inspect_sculpt": (n_("Inspected sculpt surface"), n_("Inspecting the sculpt surface"),
                                   n_("Inspect the sculpt surface")),
         "angelo_prepare_sculpt": (n_("Prepared sculpt target"), n_("Preparing the sculpt target"),
                                   n_("Prepare the sculpt target")),
         "angelo_sculpt_batch": (n_("Sculpted a brush pass"), n_("Sculpting a brush pass"),
                                 n_("Sculpt a brush pass")),
         "angelo_sculpt_view": (n_("Viewed sculpt from fresh angles"), n_("Viewing the sculpt from fresh angles"),
                                n_("View the sculpt from fresh angles")),
         "angelo_sculpt_target": (n_("Located brush targets on the surface"),
                                  n_("Locating brush targets on the surface"),
                                  n_("Locate brush targets on the surface")),
         "angelo_sculpt_program": (n_("Sculpted a sequence of brush passes"),
                                   n_("Sculpting a sequence of brush passes"),
                                   n_("Sculpt a sequence of brush passes")),
         "angelo_sculpt_region": (n_("Defined a sculpt region"), n_("Defining a sculpt region"),
                                  n_("Define a sculpt region")),
         "angelo_sculpt_resolution": (n_("Adjusted sculpt working resolution"),
                                      n_("Adjusting the sculpt working resolution"),
                                      n_("Adjust the sculpt working resolution")),
         "angelo_sculpt_review": (n_("Compared sculpt shape with reference targets"),
                                  n_("Comparing the sculpt shape with reference targets"),
                                  n_("Compare the sculpt shape with reference targets")),
         "angelo_view": (n_("Viewed the scene"), n_("Viewing the scene"), n_("View the scene")),
         "angelo_set_transform": (n_("Edited object transform"), n_("Editing an object transform"),
                                  n_("Edit an object transform")),
         "angelo_apply_batch": (n_("Edited scene"), n_("Editing the scene"), n_("Edit the scene")),
         "angelo_execute_python": (n_("Ran Blender Python"), n_("Running Blender Python"), n_("Run Blender Python")),
         "angelo_read_reference": (n_("Read attached file"), n_("Reading the attached file"),
                                   n_("Open the attached file")),
         "web_search": (n_("Searched the web"), n_("Searching the web"), n_("Search the web")),
         "WebSearch": (n_("Searched the web"), n_("Searching the web"), n_("Search the web")),
         "WebFetch": (n_("Read a web page"), n_("Reading a web page"), n_("Open a web page")),
         "angelo_list_files": (n_("Listed files"), n_("Listing files"), n_("List files")),
         "angelo_read_file": (n_("Read a file"), n_("Reading a file"), n_("Open a file")),
         "angelo_write_file": (n_("Edited a file"), n_("Editing a file"), n_("Edit a file")),
         "angelo_delete_file": (n_("Deleted a file"), n_("Deleting a file"), n_("Delete a file")),
         "angelo_job_start": (n_("Started a background job"), n_("Starting a background job"),
                              n_("Start a background job")),
         "angelo_web_check": (n_("Checked the scene for the web"), n_("Checking the scene for the web"),
                              n_("Check the scene for the web")),
         "angelo_web_export": (n_("Exported for the web"), n_("Exporting for the web"), n_("Export for the web")),
         "angelo_web_preview": (n_("Live web preview"),) * 3,
         "angelo_job_cancel": (n_("Cancelled a background job"), n_("Cancelling a background job"),
                               n_("Cancel a background job"))}
UNKNOWN = (n_("Used a tool"), n_("Using a tool"), n_("Use a tool"))
# The live row under the transcript while a turn runs, by the controller's status (an English key).
# "Preparing the next steps": the runtime is writing its plan (claude_stream.STATUS_PLAN).
# "Provider busy, retrying…": Codex retries a throttled or overloaded turn itself (codex_retry.RETRYING).
# The host's own statuses name the host's work, never "Thinking" (live_progress reads a running step's kind first).
LIVE = {"Connecting": n_("Connecting"), "Sending": n_("Sending"),
        "Preparing the next steps": n_("Preparing the next steps"),
        "Reading file metadata": n_("Reading file metadata"), "Provider busy, retrying…": n_("Provider busy, retrying…"),
        "Staging native program": n_("Working in Angelo"),
        "Waiting for animation playback to stop": n_("Waiting for animation playback to stop"),
        "Generating media": n_("Generating media"), "Enhancing prompt": n_("Enhancing the prompt"),
        "Stopping": n_("Stopping"), "Waiting for a render": n_("Waiting for a render"),
        "Rendering a look": n_("Rendering a look"), "Waiting for workers": n_("Waiting for the worker agents"),
        "Compiling shaders for a view": n_("Compiling shaders for a view"),
        "Approval required": n_("Waiting for your approval"),
        "Approve collaborator usage": n_("Waiting for your approval"),
        "External work": n_("Working for a connected app"), "External read": n_("Reading for a connected app"),
        "Event queue full; reconnect required": n_("Reconnect required")}
# Statuses that never label a running turn's live row: the turn is over, not started, or a voice turn (its own text).
NOT_LIVE = frozenset({"New chat", "Interrupted", "Failed", "Completed", "Idle", "Disconnected", "Off", "Voice active",
                      "Voice · agent working", "Starting voice"})
STATES = {"queued": n_("queued"), "running": n_("running"), "working": n_("working"),
          "approval": n_("needs your approval"), "usage": n_("needs usage approval"), "failed": n_("failed"),
          "declined": n_("declined"), "stopped": n_("stopped"),
          "reverted": n_("reverted"), "preview": n_("preview, not applied yet"), "committed": n_("applied, Undo available")}


# A step that never ran by the person's choice or a stop (its card declined, its turn stopped): not a failure.
NOT_RUN = {"DECLINED": "declined", "CANCELLED": "stopped"}


def happened(message):
    """Did the row's step happen? Completed without an error (a staged preview or a reverted edit happened too).

    Queued, running, waiting for approval, declined, stopped and failed steps did not; nor did a call that has
    no status and no result yet (a planned step, a call under review)."""
    status = message.get("status")
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    if status not in {"completed", None} or (status is None and not result):
        return False
    return not (result.get("error") or result.get("error_code") or result.get("script_error"))


def in_progress(message):
    """Is the row's step under way right now (running, a plan being written)? Not done, and not a step that waits,
    was declined, stopped or failed."""
    return message.get("status") == "running"


def title_form(forms, message):
    """The translated title of a (done, in progress, not done) entry for this row."""
    return iface_(forms[0 if happened(message) else 1 if in_progress(message) else 2])


def _running_state(forms, message, state):
    """The suffix "running"/"working" is said already by a progressive title: keep it only for a noun phrase."""
    return None if in_progress(message) and forms[1] != forms[2] else state


def _state(message, states):
    """The suffix state of a row that did not happen, by its status (``states``) or how it ended."""
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    if result.get("error_code") in NOT_RUN:
        return NOT_RUN[result["error_code"]]
    return states.get(message.get("status"))


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
    """A tool row's title with its state, for the console, the changes popup and plain fallbacks: past only for a
    step that happened (``happened``), progressive while it is under way (``in_progress``)."""
    # Chats saved before the rename record pre-rename tool names.
    name = current_tool_name(message.get("tool", ""))
    status = message.get("status")
    if name in NATIVE:
        result = message.get("result", {})
        title = title_form(NATIVE[name], message)
        if result.get("status") in {"preview", "committed"}:
            return _with_state(title, result["status"])
        if result.get("progress"):
            from .agent_workspace_ui import job_progress_text
            title = iface_("{action} · {progress}", action=title, progress=job_progress_text({"progress": result["progress"]}))
        state = _state(message, {"queued": "queued", "running": "working", "approval": "approval", "failed": "failed"})
        return _with_state(title, _running_state(NATIVE[name], message, state))
    if name == "angelo_delegate":
        state = _state(message, {"queued": "queued", "approval": "usage", "failed": "failed"})
        return _with_state(delegate_title(message), state)   # Running: "Consulting {model}" says it.
    from .studio_spec import STUDIO_NAMES
    if name in STUDIO_NAMES:
        result = message.get("result", {})
        title = studio_title(name, message)
        if status == "running" and result.get("progress") is not None:
            title = iface_("{action} · {percent}%", action=title, percent=result["progress"])
        return _with_state(title, _state(message, {"queued": "queued", "approval": "approval", "failed": "failed"}))
    if name:
        from .activity_text import title_forms   # Its tables and the generic phrases by kind: never a raw tool id.
        forms = title_forms(message)
    else:
        forms = UNKNOWN
    title = title_form(forms, message)
    if message.get("collaborator"):
        title = iface_("{collaborator} · {action}", collaborator=message["collaborator"], action=title)
    if message.get("reverted"):
        return _with_state(title, "reverted")
    state = _state(message, {"queued": "queued", "running": "running", "approval": "approval", "failed": "failed"})
    return _with_state(title, _running_state(forms, message, state))


def delegate_title(message):
    """A consultation: "Consulted {model}" once it happened, "Consulting {model}" while it runs, "Consult {model}"
    before it and when it did not happen."""
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    target = result.get("collaborator") or message.get("collaborator") or (message.get("arguments") or {}).get("model")
    if target:
        if happened(message):
            return iface_("Consulted {model}", model=target)
        if in_progress(message):
            return iface_("Consulting {model}", model=target)
        return iface_("Consult {model}", model=target)
    if happened(message):
        return iface_("Consulted another model")
    return iface_("Consulting another model") if in_progress(message) else iface_("Consult another model")


def studio_title(name, message):
    """A Media Studio tool's title. A generation runs on after its row completes: until its media is ready the row
    says it is generating (the progressive form), never that it generated."""
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    key = name.removeprefix("angelo_studio_")
    forms = STUDIO.get(key, STUDIO_FALLBACK)
    if key == "generate" and happened(message) and result.get("status") != "ready":
        return iface_(forms[1])
    return title_form(forms, message)


def session_title(record):
    """A conversation's title as shown: the person's first request, or "New chat" in the interface language.

    Blender's menus draw left to right without shaping: a request typed in Arabic is shaped and put in visual order.
    """
    return iface_(UNTITLED) if record["title"] == UNTITLED else visual(record["title"])
