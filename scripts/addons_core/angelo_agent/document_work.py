# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a chat holds anything about the Blender document it is bound to (``Controller.set_document``).

A chat with nothing about its document (a new chat: no message besides host notices, turn, tool call, native
preview, native thread or remembered brief or result) follows a newly opened document quietly; any other chat
says the document changed and keeps its turns bound to theirs (doc/angelo/agent_chat.rst, "Composer").
Moved out of ``controller`` to keep it within its size exception. No bpy.
"""


def save_quietly(controller):
    """Save ``controller`` where a failure must never stop a bind or a load handler: its state stays in RAM, the next
    save writes it, and the chat says so (``save_failed``)."""
    from .tool_finalization import PERSISTENCE_ERRORS
    try:
        controller.save()
    except PERSISTENCE_ERRORS as error:
        save_failed(controller, error)
        return False
    return True


def save_failed(controller, error):
    """A bind's save failed: the chat's ``persistence_warning``, and once per chat a row in it saying so in the person's
    language (nothing else reads that warning)."""
    controller.persistence_warning = "The chat's saved state could not be written (%s)" % type(error).__name__
    controller.version += 1
    if not getattr(controller, "_save_failure_shown", False):
        from .document_identity import SAVE_FAILED
        from .ui_text import notice
        controller._save_failure_shown = True
        controller.emit("message", {"role": "system", **notice(SAVE_FAILED)})


def stale_memory(controller, reason, *, rescope=True):
    """``Controller.mark_memory_stale`` (which saves) for a bind or a load: its memory changes in RAM whatever the
    save does (``save_quietly``). ``rescope`` False keeps the notes' document scope: a chat rebound to a document
    (``document_identity._bind``: a reopen, a relaunch, the person's Continue) gets its notes back only through a
    verified saved receipt (the same saved bytes, ``memory_projects.bind_saved_memory``) or the person's brief
    confirmation, never because it was rebound (the 1.0.7 candidate: a Continue onto changed bytes carried the brief)."""
    from .tool_finalization import PERSISTENCE_ERRORS
    try:
        if rescope:
            controller.mark_memory_stale(reason)
        else:
            controller.mark_memory_stale(reason, rescope=False)
    except PERSISTENCE_ERRORS as error:
        save_failed(controller, error)


def set_document(controller, document):
    """``Controller.set_document``: bind a chat with no document yet, else ``document_changed`` for another key. A
    restored chat waiting for its own file (``document_identity``) is bound only by that file's load."""
    if getattr(controller, "awaited_file", None) is not None:
        return
    if controller.document is None:
        controller.document = document
        memory = controller.state.get("task_memory", {})
        if not (memory.get("scope") or {}).get("document"):
            stale_memory(controller, "restart")
    elif document != controller.document:
        document_changed(controller, document)   # Another file invalidates it; another scene of its file never does.


def holds_document_work(controller):
    """Whether anything in ``controller``'s chat was made in or about its bound document."""
    state = controller.state
    memory = state.get("task_memory") or {}
    return bool(controller.busy or controller.pending or controller._native_preview_cards or state.get("native_id")
                or any(message.get("role") != "system" for message in state.get("messages", ()))
                or memory.get("verified_results") or memory.get("planned_steps")
                or (memory.get("brief") or {}).get("objective"))


def document_changed(controller, document):
    """``Controller.set_document`` for a key other than the chat's own. A chat with nothing about its document follows
    the shown one quietly (1.0.5). Otherwise only another file (a load: the serial changes) invalidates it; the main
    window showing another scene of the same file never does: the chat keeps its own scene, and whatever needs the
    window waits until its scene is shown again (``document_scene``; break-it E2E-20 R5-M-1, the maintainer's spec of
    2026-10-25: selecting a session only changes what is displayed). A load of the same document (its saved id at the
    path the chat knew) never reaches this: ``document_identity`` rebinds the chat to it."""
    from . import document_scene, ui_text
    if not controller.document_invalid and not holds_document_work(controller):   # A new chat follows quietly.
        controller.document = document
        stale_memory(controller, "restart")
        return
    if controller.document_invalid or (document_scene.same_file(document, controller.document)
                                        and not document_scene.unattended()):   # Background: no person shows it back.
        return
    controller.document_invalid = True
    controller._script_consent = None
    stale_memory(controller, "document_change")
    controller.invalidate_native_stages("The Blender document changed; previews and staged jobs were discarded")
    controller.cancel_tools("The bound Blender document changed. Start a new chat.")
    controller.stop()
    controller.emit("message", {"role": "system", **ui_text.notice(
        "Document changed. Start New Chat before issuing scene commands.")})
