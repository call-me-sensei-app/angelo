# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plain-language text for agent progress in the chat: step titles, kinds, states, groups and notices.

No ``bpy``: host-testable. Everything here is display text, read at draw time and never sent to a model.
Internal names (tool ids, error codes, exception classes, ids) never reach a headline: an unknown tool or
code falls back to a generic friendly title, and the exact technical text stays under the row's Details.
Every phrase is marked with ``n_`` and translated here (``ui_text``).

API:
    step_kind(message) -> str          one of KINDS: picks the row's stroke icon (chat_rows)
    step_title(message) -> str         the row's title, without a state suffix
    step_status(message) -> str        one of STATUSES: picks the row's chip
    status_label(status) -> str        the chip's text
    status_tone(status) -> str         "teal" | "coral" | "amber" | "neutral"
    friendly_error(text) -> str        a headline for an error text, from its code or exception class
    group_title(messages, live) -> str a turn's work group: live progress, or a summary when done
    group_status(messages, live) -> str
    notice_view(message) -> dict|None  {"tone", "headline", "body"} for host-verified, recovery, script and
                                       plan notices; None leaves the notice as it is drawn today
"""

import re

from .chat_titles import NATIVE, NOT_RUN, OTHER, delegate_title, studio_title, title_form
from .legacy_names import current_tool_name
from .ui_text import iface_, join, n_, plural_

KINDS = ("read", "look", "search", "create", "edit", "delete", "material", "render", "script", "generate",
         "file", "job", "consult", "memory", "sculpt", "check", "plan", "work")
STATUSES = ("running", "queued", "needs_you", "failed", "declined", "stopped", "reverted", "preview", "done")

_KIND = {
    "angelo_inspect_scene": "read", "angelo_read_data": "read", "angelo_blend_file": "read",
    "angelo_scene_object": "read", "angelo_scene_settings": "read", "angelo_scene_relationships": "read",
    "angelo_scene_delta": "read", "angelo_vertex_group_weights": "read", "angelo_agent_capabilities": "read",
    "angelo_agent_metrics": "read", "angelo_blender_api": "search", "angelo_scene_search": "search",
    "web_search": "search", "WebSearch": "search", "WebFetch": "read", "angelo_view": "look",
    "angelo_sculpt_view": "look", "angelo_inspect_preview": "look", "angelo_create_cube": "create",
    "angelo_scene_program": "edit", "angelo_apply_recipe": "edit", "angelo_expand_recipe": "edit",
    "angelo_apply_batch": "edit", "angelo_set_transform": "edit", "angelo_accept_preview": "edit",
    "angelo_set_workspace_mode": "edit", "angelo_discard_preview": "delete", "angelo_delete_file": "delete",
    "angelo_execute_python": "script", "angelo_script": "script", "angelo_read_reference": "file", "angelo_list_files": "file",
    "angelo_read_file": "file", "angelo_write_file": "file", "angelo_job_start": "job", "angelo_web_check": "check",
    "angelo_web_export": "file", "angelo_web_preview": "look",
    "angelo_job_cancel": "job", "angelo_delegate": "consult", "angelo_memory_update": "memory",
    "angelo_memory_read": "memory", "angelo_scene_verify": "check", "angelo_genai": "generate",
    "angelo_flow_run": "generate", "angelo_team_library_import": "file", "angelo_team_library_save": "file",
    "StructuredOutput": "plan",
}
_PREFIX_KIND = (("angelo_sculpt", "sculpt"), ("angelo_prepare_sculpt", "sculpt"), ("angelo_inspect_sculpt", "sculpt"),
                ("angelo_studio_generate", "generate"), ("angelo_studio_enhance", "generate"),
                ("angelo_studio_import", "create"), ("angelo_studio", "file"), ("angelo_flow", "generate"),
                ("angelo_team", "file"), ("angelo_job", "job"), ("angelo_material", "material"),
                ("angelo_render", "render"), ("angelo_bake", "render"))
# Titles for tools chat_titles does not name, as (done, in progress, not done) like its tables (``title_form``);
# anything else falls back to a generic phrase by its kind, never its id.
# app-name: names the Blender API and manual the lookup tool reads
EXTRA = {"angelo_blender_api": (n_("Looked up how Blender works"), n_("Looking up how Blender works"),
                                n_("Look up how Blender works")),
         "StructuredOutput": (n_("Planned the changes"), n_("Planning the changes"),
                              n_("Plan the changes")),  # The Claude runtime's plan call (claude_stream).
         "angelo_agent_capabilities": (n_("Checked what Angelo can do here"), n_("Checking what Angelo can do here"),
                                       n_("Check what Angelo can do here")),
         "angelo_scene_verify": (n_("Checked the result"), n_("Checking the result"), n_("Check the result")),
         "angelo_scene_search": (n_("Searched the scene"), n_("Searching the scene"), n_("Search the scene")),
         "angelo_scene_object": (n_("Looked at an object"), n_("Looking at an object"), n_("Look at an object")),
         "angelo_scene_settings": (n_("Read scene settings"), n_("Reading scene settings"),
                                   n_("Inspect scene settings")),
         "angelo_scene_relationships": (n_("Read how objects connect"), n_("Reading how objects connect"),
                                        n_("Inspect how objects connect")),
         "angelo_scene_delta": (n_("Read recent scene changes"), n_("Reading recent scene changes"),
                                n_("Inspect recent scene changes")),
         "angelo_vertex_group_weights": (n_("Read vertex weights"), n_("Reading vertex weights"),
                                         n_("Inspect vertex weights")),
         "angelo_set_workspace_mode": (n_("Switched the workspace"), n_("Switching the workspace"),
                                       n_("Switch the workspace")),
         "angelo_genai": (n_("Generated with AI"), n_("Generating with AI"), n_("Generate with AI")),
         "angelo_flow_run": (n_("Ran a generation flow"), n_("Running a generation flow"),
                             n_("Run a generation flow")),
         "angelo_flow_build": (n_("Built a generation flow"), n_("Building a generation flow"),
                               n_("Build a generation flow")),
         "angelo_flow_choose": (n_("Chose a generation flow"), n_("Choosing a generation flow"),
                                n_("Choose a generation flow")),
         "angelo_flow_inspect": (n_("Looked at a generation flow"), n_("Looking at a generation flow"),
                                 n_("Look at a generation flow")),
         "angelo_team_library_import": (n_("Brought in from the team library"),
                                        n_("Bringing in from the team library"),
                                        n_("Bring in from the team library")),
         "angelo_team_library_save": (n_("Saved to the team library"), n_("Saving to the team library"),
                                      n_("Save to the team library"))}
_GENERIC = {"read": (n_("Looked at the scene"), n_("Looking at the scene"), n_("Look at the scene")),
            "look": (n_("Looked at the scene"), n_("Looking at the scene"), n_("Look at the scene")),
            "search": (n_("Searched"), n_("Searching"), n_("Run a search")),
            "create": (n_("Added to the scene"), n_("Adding to the scene"), n_("Add to the scene")),
            "edit": (n_("Edited the scene"), n_("Editing the scene"), n_("Edit the scene")),
            "delete": (n_("Removed something"), n_("Removing something"), n_("Remove something")),
            "material": (n_("Worked on materials"), n_("Working on materials"), n_("Work on materials")),
            "render": (n_("Rendered"), n_("Rendering"), n_("Run a render")),
            "script": (n_("Ran a script"), n_("Running a script"), n_("Run a script")),
            "generate": (n_("Generated with AI"), n_("Generating with AI"), n_("Generate with AI")),
            "file": (n_("Worked with files"), n_("Working with files"), n_("Work with files")),
            "job": (n_("Ran a background job"), n_("Running a background job"), n_("Run a background job")),
            "consult": (n_("Asked another model"), n_("Asking another model"), n_("Ask another model")),
            "memory": (n_("Used the Project Brief"), n_("Using the Project Brief"), n_("Use the Project Brief")),
            "sculpt": (n_("Sculpted"), n_("Sculpting the mesh"), n_("Sculpt the mesh")),
            "check": (n_("Checked the result"), n_("Checking the result"), n_("Check the result")),
            "plan": (n_("Planned the changes"), n_("Planning the changes"), n_("Plan the changes")),
            "work": (n_("Worked on the scene"), n_("Working on the scene"), n_("Work on the scene"))}
_STATUS_LABELS = {"running": n_("Running"), "queued": n_("Queued"), "needs_you": n_("Needs you"),
                  "failed": n_("Failed"), "declined": n_("Declined"), "stopped": n_("Stopped"),
                  "reverted": n_("Reverted"), "preview": n_("Preview"), "done": n_("Done")}
_TONES = {"running": "teal", "queued": "neutral", "needs_you": "amber", "failed": "coral", "declined": "neutral",
          "stopped": "neutral", "reverted": "amber", "preview": "amber", "done": "teal"}
_NOT_RUN = NOT_RUN   # A step that never ran by the person's choice or a stop: not a failure (chat_titles).
_EDITS = {"create", "edit", "delete", "material", "script", "sculpt", "generate"}
_JOBS = {"job", "render"}
_LOOKS = {"read", "look", "search", "check", "file", "memory", "consult"}

# Error codes and exception classes as a 3D artist reads them. Unknown ones read as SOMETHING_WRONG.
_CODES = {"OBJECT_IN_USE": n_("Another object still depends on it"),
          "NOT_FOUND": n_("Angelo couldn't find what it needed"),
          "OBJECT_NOT_FOUND": n_("Angelo couldn't find that object"),
          "STALE": n_("The scene changed while Angelo was working"),
          "STALE_HANDLE": n_("The scene changed while Angelo was working"),
          "VALIDATION": n_("Some of the settings weren't valid"), "INVALID": n_("Some of the settings weren't valid"),
          "DENIED": n_("That wasn't allowed"), "PERMISSION": n_("That wasn't allowed"),
          "TIMEOUT": n_("It took too long"), "CANCELLED": n_("It was stopped"),
          "IndexError": n_("The script reached past the end of a list"),
          "KeyError": n_("The script asked for something that doesn't exist"),
          "AttributeError": n_("The script used a setting Angelo doesn't have"),
          "NameError": n_("The script used a name it never defined"),
          "SyntaxError": n_("The script had a typing mistake"),
          "TypeError": n_("The script passed the wrong kind of value"),
          "ValueError": n_("The script used a value Angelo can't accept"),
          "RuntimeError": n_("Angelo couldn't do what the script asked"),
          "ZeroDivisionError": n_("The script divided by zero")}
SOMETHING_WRONG = n_("Something went wrong")
_CODE = re.compile(r"\b([A-Z][A-Za-z]*Error|[A-Z]{2,}(?:_[A-Z]+)+|[A-Z]{4,})\b")


def tool_name(message):
    return current_tool_name(message.get("tool", "") or "")


def step_kind(message):
    """The kind of work a step did, for its icon: from the tool, never from free text."""
    name = tool_name(message)
    if name in _KIND:
        return _KIND[name]
    for prefix, kind in _PREFIX_KIND:
        if name.startswith(prefix):
            return kind
    return "work"


def title_forms(message):
    """The (done, in progress, not done) title of a step that is neither a consultation nor Media Studio's: its
    tool's own, else the generic phrase of its kind (never its id)."""
    name = tool_name(message)
    for table in (NATIVE, OTHER, EXTRA):
        if name in table:
            return table[name]
    return _GENERIC[step_kind(message)]


def step_title(message):
    """The step's title for a 3D artist, without its state (the chip shows that). Past tense only for a step that
    happened (``chat_titles.happened``), progressive while it runs (``in_progress``); a queued, waiting, declined,
    stopped or failed step names its action."""
    name, status = tool_name(message), message.get("status")
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    if name == "angelo_delegate":
        return delegate_title(message)
    if name.startswith("angelo_studio_"):
        title = studio_title(name, message)
        if status == "running" and isinstance(result.get("progress"), (int, float)):
            title = iface_("{action} · {percent}%", action=title, percent=result["progress"])
    else:
        title = title_form(title_forms(message), message)
    if message.get("collaborator"):
        title = iface_("{collaborator} · {action}", collaborator=message["collaborator"], action=title)
    return title


def step_status(message):
    result = message.get("result") if isinstance(message.get("result"), dict) else {}
    status = message.get("status")
    if status == "approval":
        return "needs_you"
    if status in {"queued", "running"}:
        return status
    if result.get("error_code") in _NOT_RUN:
        return _NOT_RUN[result["error_code"]]
    if status == "failed" or result.get("error") or result.get("script_error"):
        return "failed"
    if message.get("reverted"):
        return "reverted"
    if result.get("status") == "preview":
        return "preview"
    return "done"


def status_label(status):
    return iface_(_STATUS_LABELS.get(status, n_("Done")))


def status_tone(status):
    return _TONES.get(status, "neutral")


def friendly_error(text):
    """A one-line headline for an error: its code or exception class, translated; never the code itself."""
    for match in _CODE.finditer(str(text or "")):
        if match.group(1) in _CODES:
            return iface_(_CODES[match.group(1)])
    return iface_(SOMETHING_WRONG)


def group_status(messages, live):
    statuses = [step_status(message) for message in messages]
    for status in ("needs_you", "running", "queued"):
        if status in statuses:
            return status
    if live:
        return "running"
    return "failed" if statuses and statuses[-1] == "failed" else "done"


def group_title(messages, live):
    """``Working on the scene · 6 steps`` while it runs; ``Made 2 changes · checked 3 things`` when done."""
    count = len(messages)
    if live or group_status(messages, False) in {"running", "queued", "needs_you"}:
        return plural_(count, "Working on the scene · {count} step", "Working on the scene · {count} steps")
    statuses = [step_status(message) for message in messages]
    ran = [(step_kind(message), status) for message, status in zip(messages, statuses)
           if status not in _NOT_RUN.values()]   # A declined or stopped step did nothing to count.
    failed, declined, stopped = (statuses.count(status) for status in ("failed", "declined", "stopped"))
    edits = sum(kind in _EDITS and status != "failed" for kind, status in ran)
    looks = sum(kind in _LOOKS for kind, _status in ran)
    jobs = sum(kind in _JOBS for kind, _status in ran)
    parts = []
    if edits:
        parts.append(plural_(edits, "Made {count} change", "Made {count} changes"))
    if jobs:
        parts.append(plural_(jobs, "Ran {count} background job", "Ran {count} background jobs"))
    if looks:
        parts.append(plural_(looks, "Checked {count} thing", "Checked {count} things") if parts
                     else plural_(looks, "Looked at {count} thing", "Looked at {count} things"))
    if failed:
        parts.append(plural_(failed, "{count} step failed", "{count} steps failed"))
    if declined:
        parts.append(plural_(declined, "{count} step declined", "{count} steps declined"))
    if stopped:
        parts.append(plural_(stopped, "{count} step stopped", "{count} steps stopped"))
    return join(parts) if parts else plural_(count, "Finished {count} step", "Finished {count} steps")


def _problem(message):
    fields = message.get("i18n", [None, {}])[1] if isinstance(message.get("i18n"), list) else {}
    fields = fields if isinstance(fields, dict) else {}
    return fields.get("problem") or fields.get("error") or message.get("text", "")


_JOB_ENDED = {"done": n_("Background job finished"), "failed": n_("Background job failed"),
              "cancelled": n_("Background job stopped"), "interrupted": n_("Background job stopped")}
_STORED_JOB = re.compile(r"^Job \S+ \((\w+)\) (\w+)")


def job_view(message, title=None, state=None, status=None):
    """A finished background job's row: its title and outcome, never its id (``Job j3``).

    ``title``, ``state`` and ``status`` come from the job's live record (``job_chat``) when Angelo still has it;
    without it, the stored English row gives only the outcome.
    """
    if status is None:
        match = _STORED_JOB.match(message.get("text") or "")
        status = match.group(2) if match else ""
    tone = {"done": "teal", "failed": "coral"}.get(status, "amber")
    headline = (iface_("{title}: {state}", title=title, state=state) if title and state
                else iface_(_JOB_ENDED.get(status, n_("Background job ended"))))
    return {"tone": tone, "headline": headline, "body": "", "quiet": True}  # Its card above already shows it.


def notice_view(message):
    """How a host notice reads: a tone, a friendly headline and one suggested fix. None: not one of ours.

    Matches the stored English text (stored notices are never translated); the full text, with every id and
    exception, stays available under the notice's Details.
    """
    text = message.get("text") or ""
    if message.get("notice") and message.get("job"):
        return job_view(message)
    if message.get("recovery_check") is True:   # After a relaunch: a restore that did not finish (session_validation).
        return {"tone": "amber", "headline": iface_("A restore in this chat didn't fully finish"),
                "body": iface_("Check the scene and your files before you continue. Undo restore brings back the "
                               "scene from before.")}
    if message.get("restore_state") or message.get("restore_checkpoint"):
        return _restore_view(message)
    if text.startswith("Host-verified result"):
        checks = sum(line.lstrip().startswith("- ") for line in text.splitlines())
        body = (plural_(checks, "Angelo checked {count} thing directly in the scene, so no extra model round was needed.",
                        "Angelo checked {count} things directly in the scene, so no extra model round was needed.")
                if checks else iface_("Angelo checked the result directly in the scene, so no extra model round was needed."))
        return {"tone": "teal", "headline": iface_("Done and checked in your scene"), "body": body}
    if text.startswith("Standalone verification passed"):
        return {"tone": "teal", "headline": iface_("Checked: everything matches"), "body": "", "quiet": True}
    if text.startswith("Native plan completed"):
        return {"tone": "teal", "headline": iface_("Changes applied"), "quiet": True,
                "body": iface_("Undo in Angelo reverses them; anything outside Angelo can't be undone.")}
    if text.startswith("The plan stopped before changing the scene"):
        return {"tone": "amber", "headline": iface_("That step couldn't be done, so nothing changed"),
                "body": iface_("{reason}. Angelo is trying another way.", reason=friendly_error(_problem(message)))}
    if text.startswith("The plan stopped at operations"):
        return {"tone": "amber", "headline": iface_("Part of the plan couldn't be done"),
                "body": iface_("{reason}. Angelo is finishing the remaining edits another way.",
                               reason=friendly_error(_problem(message)))}
    if text.startswith(("The script stopped with an error. Scene data was restored",
                        "The script stopped with an error. Blender data was restored")):  # Rows stored before 1.0.6.
        return {"tone": "coral", "headline": iface_("The script hit a problem, so your scene was put back"),
                "body": iface_("{reason}. Images and anything outside Angelo weren't checked.",
                               reason=friendly_error(_problem(message)))}
    if text.startswith("The script stopped with an error"):
        return {"tone": "coral", "headline": iface_("The script hit a problem partway through"),
                "body": iface_("{reason}. Some changes may remain: check the scene before you continue.",
                               reason=friendly_error(_problem(message)))}
    return None


def _restore_view(message):
    state = message.get("restore_state")
    if message.get("recovery_copy_open"):   # Its path could not become the document's (checkpoint_restore_ui).
        return {"tone": "amber", "headline": iface_("Your scene is open as a recovery copy"),
                "body": iface_("It isn't your file: File > Save asks where to save it. Undo restore brings back the "
                               "scene from before.")}
    if state == "pending":
        return {"tone": "neutral", "headline": iface_("Restoring your scene…"),
                "body": iface_("If it's interrupted, Undo restore brings back the scene from before.")}
    if state == "not_applied":
        return {"tone": "coral", "headline": iface_("Restore wasn't applied, and nothing was lost"),
                "body": iface_("Your scene is as it was. Undo restore keeps the earlier state available.")}
    if state == "partial" or "recovery is incomplete" in (message.get("text") or ""):
        return {"tone": "amber", "headline": iface_("Your scene was restored, but not everything came back"),
                "body": iface_("Check the scene and your files before you continue. Undo restore brings back the "
                               "scene from before.")}
    return {"tone": "teal", "headline": iface_("Your scene was restored"),
            "body": iface_("Chat history was kept. Undo restore brings back the scene from before.")}
