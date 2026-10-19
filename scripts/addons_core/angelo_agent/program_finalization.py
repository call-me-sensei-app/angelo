# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Truthful typed-commit result after the native Undo publication boundary."""


def after_native_undo(result, outcome, checkpoint=None):
    """Combine the actual edit result with its same-dispatch native outcome.

    A missing or failed publication does not undo any edit and cannot turn an
    unverified state into an unchanged one. No provider work occurs here.
    """
    result = dict(result) if isinstance(result, dict) else {
        "error": "The deferred native commit returned no result; inspect the scene",
        "error_code": "INTERNAL_ERROR", "partial_changes_possible": True,
    }
    outcome = outcome if isinstance(outcome, dict) else {}
    published = outcome.get("undo_published") is True and not outcome.get("error")
    result["undo_published"] = published if type(outcome.get("undo_published")) is bool else None
    needs_undo = (result.get("status") == "committed" or result.get("scene_changed") is True
                  or result.get("partial_changes_possible") is True or result.get("mutation_started") is True)
    if not needs_undo:
        return result  # A refused preflight needs no new Undo step.
    if published:
        finalization_error = next((outcome[key] for key in (
            "editor_finalization_error", "finalization_transport_error", "native_scope_close_error")
            if outcome.get(key)), None)
        if finalization_error:
            result.update(success=False, final=True, status="failed", error_code="INTERNAL_ERROR",
                          partial_changes_possible=True, request_checkpoint=checkpoint,
                          error="Native Undo was published, but finalization is incomplete; inspect the scene.",
                          native_finalization_error=str(finalization_error)[:1000])
            result.pop("undoable", None)  # Publication alone does not certify all later callback effects.
            if result.get("scene_changed") is False:
                result.pop("scene_changed")
        elif result.get("success") is True and result.get("status") == "committed":
            result["undoable"] = True
        return result
    if result.get("error"):
        result["commit_error"] = str(result["error"])[:2000]
    result.update(success=False, final=True, status="failed", error_code="INTERNAL_ERROR", undoable=False,
                  partial_changes_possible=True,
                  error=("Native Undo publication could not be verified; applied edits may remain. "
                         if result["undo_published"] is None else
                         "Native Undo publication failed; applied edits may remain. ") +
                        "Inspect the scene and use the pre-request checkpoint if needed.")
    if result.get("scene_changed") is False:
        result.pop("scene_changed")
    result["request_checkpoint"] = checkpoint
    if outcome.get("error"):
        result["native_undo_error"] = str(outcome["error"])[:1000]
    if outcome.get("error_code"):
        result["native_undo_error_code"] = str(outcome["error_code"])[:64]
    return result
