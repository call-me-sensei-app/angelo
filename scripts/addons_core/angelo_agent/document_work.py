# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a chat holds anything about the Blender document it is bound to (``Controller.set_document``).

A chat with nothing about its document (a new chat: no message besides host notices, turn, tool call, native
preview, native thread or remembered brief or result) follows a newly opened document quietly; any other chat
says the document changed and keeps its turns bound to theirs (doc/angelo/agent_chat.rst, "Composer").
Moved out of ``controller`` to keep it within its size exception. No bpy.
"""


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
    2026-10-25: selecting a session only changes what is displayed)."""
    from . import document_scene, ui_text
    if not controller.document_invalid and not holds_document_work(controller):   # A new chat follows quietly.
        controller.document = document
        controller.mark_memory_stale("restart")
        return
    if controller.document_invalid or (document_scene.same_file(document, controller.document)
                                        and not document_scene.unattended()):   # Background: no person shows it back.
        return
    controller.document_invalid = True
    controller._script_consent = None
    controller.mark_memory_stale("document_change")
    controller.invalidate_native_stages("The Blender document changed; previews and staged jobs were discarded")
    controller.cancel_tools("The bound Blender document changed. Start a new chat.")
    controller.stop()
    controller.emit("message", {"role": "system", **ui_text.notice(
        "Document changed. Start New Chat before issuing scene commands.")})
