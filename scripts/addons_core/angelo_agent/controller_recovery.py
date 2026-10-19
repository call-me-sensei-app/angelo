# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-memory recovery finalization must not depend on transcript durability."""

import queue
import threading

from .memory_projects import restore_memory


def rebind_after_restore(controller, document, checkpoint_id, *, project_scope=None):
    """Revoke first, then cleanup. No save: UI persists each chat independently.

    Call only after the native recovery copy actually opened. A failed storage
    write cannot leave another chat owning the document that was just replaced.
    This does not roll back file/scene recovery or authorize any future action.
    """
    warnings = []
    with controller.lock:
        provider, voice = controller.provider, controller.voice
        controller._epoch += 1
        controller._request_generation += 1
        controller.document, controller.document_invalid = document, False
        controller.provider = controller._runtime_context = controller.voice = None
        controller.busy, controller.cancelled = False, True
        controller.state["native_id"] = None
        controller.state["runtime_sessions"] = {}
        controller.request_checkpoint = None
        controller._request_message_index = None
        controller.turn_options, controller.turn_attachments, controller._turn_history = None, [], None
        controller._turn_collaborators = []
        controller._items.clear()
        controller.thinking.clear()
        controller._thinking_messages.clear()
        controller._thinking_times.clear()
        controller._thinking_message = None
        while True:
            try:
                controller.tools.get_nowait()
            except queue.Empty:
                break
        # Memory errors must not undo the essential fencing already completed.
        rebound = False
        try:
            controller.state, rebound = restore_memory(controller, document, checkpoint_id, project_scope)
        except (OSError, ValueError, TypeError):
            from . import task_memory
            controller.state = task_memory.on_restore(controller.state, None, reason="document_change",
                profile_id=str(controller.store.directory.resolve()))
            warnings.append("Task memory was isolated because same-project continuity could not be verified.")
        for cleanup in (lambda: controller.cancel_tools("The snapshot was restored; prior queued actions were revoked", persist=False),
                        lambda: controller.invalidate_native_stages("The snapshot was restored; prior stages were revoked", persist=False)):
            try:
                cleanup()
            except Exception:
                warnings.append("Old request cleanup was incomplete; its document/turn authority was revoked.")
        # A malformed/stuck old callback must not retain admission ownership.
        for call in tuple(controller.pending.values()):
            try:
                controller.finish_tool(call, {"error": "The loaded document replaced this old request; inspect prior effects before retrying",
                    "partial_changes_possible": bool(call.mutation_started)}, persist=False)
            except Exception:
                controller.pending.pop(call.id, None)
                warnings.append("An old callback could not be finalized; its document/turn authority was revoked.")
        try:
            controller._finish_metrics("interrupted")
        except Exception:
            warnings.append("Recovery completed in memory, but its interrupted timing receipt could not be recorded.")
        controller.status, controller.voice_status = "Restored · reobserve scene", "Microphone off"
        controller.version += 1
    # Native stages are disposed by the main-thread cleanup path; transports
    # close independently after their immutable host fence has been revoked.
    for resource in (voice, provider):
        if resource:
            operation = resource.stop if resource is voice else resource.close
            threading.Thread(target=operation, daemon=True).start()
    return {"memory_rebound": rebound, "warnings": warnings}
