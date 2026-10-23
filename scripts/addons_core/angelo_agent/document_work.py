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
