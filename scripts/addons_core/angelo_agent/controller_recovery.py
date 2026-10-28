# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-memory recovery finalization must not depend on transcript durability."""

import queue
import threading

from .memory_projects import restore_memory

_missing = {}   # chat id -> its scene's name_full while a Restore left that scene out of the file: a later Restore
                # (Undo restore) that brings it back rebinds the chat to it.


def scene_names(controllers, shown, scenes):
    """Before a load (Restore, its safety reload), each chat's own scene: a file read renews every session_uid, so
    ``restored_document`` finds the scene again (R5-M-1: Restore keeps each chat on its own scene; it never moves a
    lead onto the scene the window shows, where its merges would land). Only a chat bound to a scene of the shown file
    and holding work about it; any other follows the shown scene, as in 1.0.5.
    {chat id: (its scene's name_full now, or None when its scene is already gone; its document key)}. No bpy:
    ``scenes`` is passed."""
    from . import document_scene, document_work
    names = {scene.session_uid: scene.name_full for scene in scenes}
    return {target.state["id"]: (names.get(target.document[1]) or _missing.get(target.state["id"]), target.document)
            for target in controllers
            if getattr(target, "remote_executor", None) is None and not target.document_invalid
            and document_scene.same_file(target.document, shown) and document_work.holds_document_work(target)}


def restored_document(recorded, shown, scenes, chat_id=None, captured=None):
    """The key a chat is rebound to after the load: ``shown`` (the window's) for no ``scene_names`` entry, else the
    new serial with its own scene's uid: the scene still holding its old uid (a process never reuses one, so only a
    read that kept uids leaves one), else the scene with the name its scene had when the checkpoint the load read was
    captured (``captured``, that checkpoint's scene map: names changed since never move it), or, when the map cannot
    say (none: an earlier build's checkpoint, or a malformed one; a scene of another load), the name read before the
    load. Neither: it keeps its old uid, a key no scene has, so it reads away (``document_scene.away``) with its scene
    missing, and never lands on the shown one; its name is kept for a later Restore."""
    from .checkpoint_scene_map import captured_name, continues
    _missing.pop(chat_id, None)
    if recorded is None:
        return shown
    name, document = recorded
    uid = document[1]
    if any(scene.session_uid == uid for scene in scenes):
        continues((shown[0], uid), document)
        return (shown[0], uid)
    known, then = captured_name(captured, document)
    wanted = then if known else name
    found = next((scene.session_uid for scene in scenes if wanted is not None and scene.name_full == wanted), None)
    if found is None and name is not None and chat_id is not None:
        _missing[chat_id] = name
    key = (shown[0], uid if found is None else found)
    continues(key, document)   # A later Restore of this load's checkpoints finds the chat's scene through it.
    return key


def rebind_after_restore(controller, document, checkpoint_id, *, project_scope=None):
    """Revoke first, then cleanup. No save: UI persists each chat independently.

    Call only after the native recovery copy actually opened. A failed storage
    write cannot leave another chat owning the document that was just replaced.
    This does not roll back file/scene recovery or authorize any future action.
    """
    warnings = []
    if getattr(controller, "remote_executor", None) is not None:
        return {"warnings": warnings}   # A worker Blender's chat: Angelo restored its own file, not the worker's part.
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
