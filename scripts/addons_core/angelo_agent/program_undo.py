# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native commit Undo capability, including older binaries with a newer add-on."""

def native_bridge():
    """Do not register unknown operator flags when an old binary loads a newer add-on."""
    try:
        from _bpy import _angelo_undo
    except ImportError:
        return None
    return _angelo_undo if all(callable(getattr(_angelo_undo, name, None)) for name in ("begin_synchronized", "finish_synchronized", "publication", "close",
                                                                                "seal_approved_objects", "stage_owned", "stage_residue", "delete_stage",
                                                                                "delete_approved_objects", "new_collection",
                                                                                "delete_created_collections", "engine_prepare", "engine_apply")) else None


def operator_flags(bridge):
    """The native scope publishes its own Undo; never add an automatic duplicate step."""
    return {"INTERNAL"} if bridge is not None else {"UNDO", "INTERNAL"}


def unavailable(mode, bridge):
    """A missing native capability is a refusal before mutation, never a false Undo promise."""
    if mode == "SCULPT" and bridge is None:
        return {"error": "This binary lacks Sculpt-compatible native commit Undo. Use a rebuilt Angelo binary "
                         "or leave Sculpt Mode before applying this staged change.",
                "error_code": "RECOVERY_REQUIRED", "scene_changed": False, "partial_changes_possible": False}
    return None


def undo_disabled(bridge, undo_steps):
    """Native commit publishes one Undo step: with no Undo steps it is refused before any callback runs."""
    if bridge is None or undo_steps is None or undo_steps > 0:
        return None
    return {"error": "Enable Global Undo with at least one Undo step before applying a native commit",
            "error_code": "UNSUPPORTED", "scene_changed": False, "partial_changes_possible": False}


def commit(controller, call, executor, document, mode, bridge, undo_steps=None):
    """Release every native owner in this dispatch before delivering its actual outcome.

    Native publication and final obligation certification are distinct. The
    terminal verifier integration consumes the retained job/transfer evidence;
    this mechanical owner never manufactures that proof from earlier revisions.
    """
    failure = unavailable(mode, bridge) or undo_disabled(bridge, undo_steps)
    if failure:
        controller.finish_tool(call, failure)
        return failure
    if bridge is None:
        return controller.commit_native_job(call, executor, document)
    # Reentrant entry raises before creating a new owner. It must not finish or
    # release the already-running outer invocation.
    preparation = controller.enter_native_preparation(call, executor, document)
    result, scope, error = preparation.result, None, None
    outcome = {"undo_published": False}
    try:
        if result is None:
            controller.native_preparation_callbacks_started(call, preparation)
            scope = bridge.begin_synchronized()
            from .program_commit_scope import bind
            with bind(controller, call, preparation, bridge, scope):
                result = controller.commit_native_job(call, executor, document, defer_finish=True)
    except Exception as failure:
        error = failure
    finally:
        try:
            if scope is not None:
                if isinstance(call.commit_result, dict):
                    result = call.commit_result  # Scoped cleanup may have added actual retained effects.
                keep = bool(call.mutation_started or isinstance(result, dict) and (
                    result.get("scene_changed") or result.get("partial_changes_possible") or
                    result.get("mutation_started") or result.get("status") == "committed"))
                try:
                    outcome = bridge.finish_synchronized(scope, keep)
                except Exception as failure:
                    if error is None:
                        error = failure
                    # Query this exact scope, never infer a result from Undo names or availability.
                    try:
                        outcome = bridge.publication(scope)
                    except Exception:
                        outcome = {"undo_published": None}
                    if not isinstance(outcome, dict):
                        outcome = {"undo_published": None}
                    # Exception formatting can itself execute Python. Keep this cleanup path
                    # independent of custom __str__/__bool__ methods while the owner is held.
                    outcome["finalization_transport_error"] = "Native finalization did not return its result"
                finally:
                    # A later integration reads immutable obligations here, after synchronous editor
                    # delivery and before close. No old revision is promoted to terminal evidence.
                    try:
                        bridge.close(scope)
                    except Exception as failure:
                        if error is None:
                            error = failure
                        if not isinstance(outcome, dict):
                            outcome = {"undo_published": None}
                        outcome["native_scope_close_error"] = "Native finalization scope did not close cleanly"
        finally:
            controller.leave_native_preparation(call, preparation, error=error)
    return controller.finish_native_commit(call, outcome)
