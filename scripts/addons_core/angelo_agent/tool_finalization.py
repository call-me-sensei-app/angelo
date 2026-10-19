# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tool-call journal: strict start records, and outcomes delivered even when final storage fails.

Admission/execution-start saves (``journal_tool``) remain strict and precede
native side effects. Completion and presentation saves are best effort: they
never retry an operation, weaken the permanent ID ledger, or auto-Undo.

``state["results"]`` changes only under ``controller.lock``, and a record is
replaced, never changed in place (the journal compares records by identity,
``session_writer``). A provider thread journals the calls it admits, and that
save encodes the request's rows and what changed since the last save (see
``Controller._pump_events``).
"""

PERSISTENCE_ERRORS = (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError)
JOURNAL_WARNING = ("The actual result is available, but its final conversation journal could not be saved. "
                   "Do not repeat the operation; inspect the current scene/files and the safety snapshot before continuing.")
# Events that only change what is drawn while a round streams (the status line, a thinking summary).
# They never start a conversation save of their own: a save encodes the request's rows and appends and syncs a
# record on the main thread (doc/angelo/agent_execution.rst), and a thinking summary can stream for minutes.
# The next durable event, or the end of the turn, saves the row they built.
DISPLAY_EVENTS = frozenset({"status", "thinking", "thinking_started", "thinking_completed"})


def journal_tool(controller, call, phase):
    """Durable before-start receipt. A restart may replay it, never reapply.

    Staging owns only isolated copies. Only execution/commit-start markers
    represent a potentially incomplete live mutation after a process crash.
    """
    live = phase in {"execution_started", "commit_started"} and controller.requires_mutation_checkpoint(call.name)
    result = {"error_code": 'OWNERSHIP', "error": "This invocation ended without a durable final read-back; inspect before issuing a new invocation",
              "interrupted": True, "journal_phase": phase, "scene_changed": False}
    if live:
        result.update(partial_changes_possible=True, request_checkpoint=controller.request_checkpoint,
            recovery="Inspect the document and restore the pre-request snapshot if necessary. Do not automatically retry this invocation.")
        result.pop("scene_changed", None)
    with controller.lock:  # Admission runs on provider threads, the later phases on the main thread.
        controller.state["results"][call.id] = {"document": repr(call.document), "invocation_digest": call.invocation_digest,
                                                "phase": phase, "result": result, "approved": call.approved,
                                                "automatic": call.automatic}
        while len(controller.state["results"]) > 256:
            controller.state["results"].pop(next(iter(controller.state["results"])))
        controller.save()


def save_observed_state(controller, *, compact=False, compact_if_due=False, leave=False):
    """Best-effort outcome/UI persistence, distinct from strict admission saves.

    ``compact_if_due`` (a turn's end) also rewrites the journal whole once its records outgrow the base.
    ``compact`` rewrites it now, for a chat closed (``Controller.close``); ``leave`` leaves it one base line
    for a chat left (``ui.show_session``, ``SessionStore.leave``). A failure there (a full disk, or a file
    this build will not write over: ``SessionStore.load``) must not stop the close or the switch; the file
    keeps its last save.
    """
    try:
        if compact:
            controller.save(compact=True)
        elif leave:
            controller.save(leave=True)
        elif compact_if_due:
            controller.save(compact_if_due=True)
        else:
            controller.save()
    except PERSISTENCE_ERRORS:
        controller.persistence_warning = JOURNAL_WARNING
        return False
    controller.persistence_warning = ""
    return True


def complete_result(controller, call, result, *, persist=True):
    """Install once in RAM, then attempt durability; caller delivers all waiters.

    Caller holds controller.lock. A failed save keeps the durable earlier
    execution marker plus permanent invocation reservation unchanged on disk.
    Detailed results are replayable in RAM with an explicit durability warning.
    """
    record = {"document": repr(call.document), "invocation_digest": call.invocation_digest,
              "phase": "completed", "result": result, "approved": call.approved, "automatic": call.automatic}
    controller.state["results"][call.id] = record
    controller._record_memory_result(call, result)
    controller.reviewed_calls.discard(call.id)
    while len(controller.state["results"]) > 256:
        controller.state["results"].pop(next(iter(controller.state["results"])))
    if not persist or not save_observed_state(controller):
        result = dict(result, journal_durable=False,
                      journal_warning=JOURNAL_WARNING if persist else "This completion has not yet been saved to the conversation journal.")
        if controller.request_checkpoint:
            result.setdefault("request_checkpoint", controller.request_checkpoint)
        # A new record, never a change in place: the journal finds changed results by identity (session_writer).
        if controller.state["results"].get(call.id) is record:
            controller.state["results"][call.id] = dict(record, result=result)
    return result
