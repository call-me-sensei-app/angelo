# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Same-dispatch controller ownership through native preparation and Undo publication.

The Controller remains the owner of admission, pending delivery and durable
results. This mixin retains its existing method interfaces and calls those
owners; it creates no request, writer, approval, or completion authority.
"""

import copy
import time
from dataclasses import dataclass

from . import exact_fence
from .chat_settings import clean_options
from .refusal_codes import RefusalError, error_result
from .program_context import (MAX_CONTEXT_REBINDS, MAX_STAGING_RESTARTS,
                              changed_message, refusal_message)
from .program_jobs import native_preparation_error_result as _preparation_error_result

def _preparation_outcome(outcome):
    """Keep actual publication and bounded native diagnostics as separate facts."""
    outcome = outcome if type(outcome) is dict else {}
    published = outcome.get("undo_published")
    published = published if type(published) is bool else None
    diagnostics = {}
    for key in ("error", "error_code", "editor_finalization_error", "finalization_transport_error", "native_scope_close_error"):
        value = outcome.get(key)
        if value is None or value is False or type(value) is str and not value:
            continue
        diagnostics[key] = value[:1000] if type(value) is str else "Native finalization reported an error"
    return published, diagnostics

@dataclass(eq=False)
class NativePreparation:
    """One same-dispatch owner, retained until native finalization and close.

    The snapshot is approval evidence, never a replacement approval. Native
    preparation and precondition reads can call Python before typed apply.
    """
    job: object
    document: object
    epoch: int
    generation: int
    invocation_id: str
    invocation_digest: str
    name: str
    arguments: object
    program_arguments: object
    precondition: object
    binding: object
    expires: float
    approved: bool
    approved_program: object
    approved_deletions: tuple
    delivery_owner: object = None
    parent_call: object = None
    result: object = None
    persist: bool = True
    callbacks_started: bool = False
    writer_retained: bool = False
    restaging: bool = False
    leaving: bool = False
    cleanup_started: bool = False
    delivering: bool = False

CONTEXT_BOUND_PROGRAMS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})


class ProgramCommitController:
    """Existing native commit continuation; Controller supplies its dependencies."""

    def _preparation_result(self, call, result):
        """Preparation callbacks are not evidence that no scene effects occurred."""
        if not isinstance(result, dict) or not call.preparation_effects_possible or call.mutation_started:
            return result
        result = dict(result, partial_changes_possible=True, mutation_started=False,
                      request_checkpoint=self.request_checkpoint)
        if result.get("scene_changed") is False:
            result.pop("scene_changed")
        result["note"] = "The typed edit was not applied. Preparation callbacks may have changed the scene; inspect it before continuing."
        return result

    def _defer_preparation_finish(self, call, result, persist):
        """One revocation rule for Stop, expiry, invalidation and direct finish."""
        preparation = call.native_preparation
        if preparation is None:
            return False
        if (preparation.result is None or preparation.restaging) and not call.mutation_started:
            preparation.result = result
            preparation.persist = persist
            if isinstance(result, dict) and result.get("error"):
                call.revoked = call.revoked or str(result["error"])
        return True

    def _check_native_preparation(self, call, preparation, document, *, restaging=False, staged_job=True):
        """Reject replaced ownership or approval; never adopt callback changes."""
        if (call.native_preparation is not preparation or self.pending.get(preparation.invocation_id) is not call
                or call.id != preparation.invocation_id
                or staged_job and self.native_jobs.get(call.id) is not preparation.job
                or getattr(preparation.job, "invocation_id", None) != preparation.invocation_id
                or call.invocation_digest != preparation.invocation_digest
                or call.delivery_owner is not preparation.delivery_owner
                or call.parent_call is not preparation.parent_call
                or call.name != preparation.name or call.arguments != preparation.arguments
                or call.program_arguments != preparation.program_arguments
                or getattr(preparation.job, "binding", None) is not preparation.binding
                or call.document != preparation.document or document != preparation.document
                or self.document != preparation.document or call.epoch != preparation.epoch
                or call.generation != preparation.generation or self._epoch != preparation.epoch
                or self._request_generation != preparation.generation
                or not call.authorized or not call.approved or call.approved != preparation.approved
                or call.expires != preparation.expires or call.precondition != preparation.precondition):
            raise RefusalError("The original staged invocation or approval changed during native preparation", code="OWNERSHIP")
        if not self._tool_available(call, document):
            raise RefusalError("The staged invocation was revoked during native preparation", code="CANCELLED")
        if clean_options(self.state.get("options"))["approval"] == "READ_ONLY":
            raise RefusalError("This chat is read-only; the typed edit was not applied", code="READ_ONLY")
        if preparation.result is not None and not (restaging and preparation.restaging):
            raise RefusalError("Native preparation was already refused", code="CANCELLED")

    def native_preparation_callbacks_started(self, call, preparation):
        """Mark callback-capable work before entering it, including native begin."""
        if call.native_preparation is not preparation:
            raise RefusalError("The native preparation owner no longer matches", code="OWNERSHIP")
        preparation.callbacks_started = True
        call.preparation_effects_possible = True

    def enter_native_preparation(self, call, executor, document):
        """Pin the exact call and writer before any callback-bearing preparation.

        The caller must leave this returned owner in finally, after native close,
        then call finish_native_commit once. A rejected nested entry has no owner.
        The existing proved frame-only restage is admitted only here, before
        native begin; its cleanup waits for leave.
        """
        if call.native_preparation is not None or call.committing:
            raise RefusalError("This invocation already owns native preparation or commit", code="OWNERSHIP")
        job = self.native_jobs.get(call.id)
        approved_program = copy.deepcopy(getattr(job, "program", None))
        originals = getattr(job, "binding", {}).get("originals", {}) if isinstance(getattr(job, "binding", None), dict) else {}
        from .program_commit_scope import approved_deletions as deletions
        approved_deletions = deletions(approved_program, originals)  # Exactly the program's own deletions.
        preparation = NativePreparation(job, document, call.epoch, call.generation,
                                        call.id, call.invocation_digest, call.name, copy.deepcopy(call.arguments),
                                        copy.deepcopy(call.program_arguments), copy.deepcopy(call.precondition),
                                        getattr(job, "binding", None), call.expires, call.approved,
                                        approved_program, approved_deletions,
                                        delivery_owner=call.delivery_owner, parent_call=call.parent_call)
        call.native_preparation = preparation
        call.finished_preparation = None
        call.preparation_effects_possible = False
        call.commit_result = None
        executor.studio_context = self
        try:
            self._check_native_preparation(call, preparation, document)
            job = preparation.job
            if not job.ready_to_commit or job.closed:
                raise RefusalError("The exact authorized stage is not ready to commit", code="OWNERSHIP")
            job.retain_native_preparation(preparation)
            preparation.writer_retained = True
            self.native_preparation_callbacks_started(call, preparation)
            if call.name == "angelo_accept_preview" and hasattr(job, "backend"):
                self._measure(call.metrics, "precondition_ms", lambda: job.backend.verify(job.binding))
                current, failure = preparation.precondition, None
            else:
                current, failure = self._fresh_precondition(call, executor)
            self._check_native_preparation(call, preparation, document)
            if failure is not None or current != preparation.precondition:
                outcome = self._frame_rebind(call, executor)
                self._check_native_preparation(call, preparation, document)
                if (isinstance(outcome, dict) and "arguments" in outcome
                        and call.name in CONTEXT_BOUND_PROGRAMS and not call.mutation_started
                        and call.staging_restarts < MAX_STAGING_RESTARTS and call.context_rebinds < MAX_CONTEXT_REBINDS):
                    preparation.restaging = True
                    preparation.result = {"restaging": True, "final": False, "scene_changed": False}
                elif failure is not None:
                    raise failure
                else:
                    from .program_context import change_code
                    change = self._binding_change(call, executor, current)
                    raise RefusalError(changed_message(change, "before native preparation")
                                       or "The target changed before native preparation; the typed edit was not applied",
                                       code=change_code(change))
        except Exception as error:
            if preparation.result is None:
                preparation.result = _preparation_error_result(error, scene_changed=False, partial_changes_possible=False)
        preparation.result = self._preparation_result(call, preparation.result)
        call.commit_result = preparation.result
        return preparation

    def prepare_native_finish(self, call, preparation):
        """Clean the owned stage while native Preparing rights are still bound.

        The native wrapper calls this before final editor delivery, under its
        backend scope. Keep the job, writer, refusal and provider callback until
        native terminal close and leave_native_preparation.
        """
        if call.native_preparation is not preparation:
            raise RefusalError("The native preparation owner no longer matches", code="OWNERSHIP")
        if preparation.cleanup_started:
            return
        preparation.cleanup_started = True
        job = preparation.job
        cleanup = {}
        try:
            if job is not None:
                result = job.result if job.closed else job.cancel()
                if isinstance(result, dict):
                    cleanup = {name: result[name] for name in ("cleanup_error", "cleanup_warning", "cleanup_pending",
                                                               "temporary_ids_remaining") if name in result}
        except Exception as error:
            cleanup = {"cleanup_error": _preparation_error_result(error)["error"][:1000],
                       "cleanup_warning": True, "cleanup_pending": True}
            if job is not None and job not in self._native_cleanup:
                self._native_cleanup.append(job)
        self.record_native_residue(call, preparation, cleanup)

    def record_native_residue(self, call, preparation, cleanup):
        """Merge private cleanup/registration evidence while the exact owner is pinned."""
        if call.native_preparation is not preparation:
            raise RefusalError("The native preparation owner no longer matches", code="OWNERSHIP")
        if cleanup:
            raw = preparation.result if preparation.result is not None and not call.mutation_started else call.commit_result
            raw = dict(raw) if isinstance(raw, dict) else {
                "error": "Native preparation did not complete; inspect retained temporary data", "error_code": "CLEANUP_FAILED"}
            cleanup = dict(cleanup)
            previous, incoming = raw.get("cleanup_error"), cleanup.get("cleanup_error")
            if type(previous) is str and type(incoming) is str and previous != incoming:
                cleanup["cleanup_error"] = previous[:500] + "; " + incoming[:1000]
            previous, incoming = raw.get("temporary_ids_remaining"), cleanup.get("temporary_ids_remaining")
            if type(previous) is int and type(incoming) is int:
                cleanup["temporary_ids_remaining"] = max(previous, incoming)
            if preparation.restaging:
                preparation.restaging = False
                raw.pop("restaging", None)
                raw.update(final=True, success=False)
                if not raw.get("error"):
                    raw.update(error="Native stage cleanup failed before restaging", error_code="CLEANUP_FAILED")
            raw.update(cleanup, partial_changes_possible=True, request_checkpoint=self.request_checkpoint)
            if raw.get("scene_changed") is False:
                raw.pop("scene_changed")
            call.commit_result = raw
            if not call.mutation_started:
                preparation.result = raw

    def leave_native_preparation(self, call, preparation, error=None):
        """Release this owner after native finalization/close, without delivery."""
        if call.native_preparation is not preparation:
            raise RefusalError("The native preparation owner no longer matches", code="OWNERSHIP")
        preparation.leaving = True
        try:
            if error is not None and (preparation.result is None or preparation.restaging):
                preparation.restaging = False
                preparation.result = _preparation_error_result(error, error_code="RECOVERY_REQUIRED",
                    scene_changed=False, partial_changes_possible=bool(call.mutation_started))
            if preparation.restaging:
                try:
                    self._check_native_preparation(call, preparation, preparation.document, restaging=True)
                except Exception as refusal:
                    preparation.restaging = False
                    if preparation.result is None or not preparation.result.get("error"):
                        preparation.result = _preparation_error_result(refusal)
            if preparation.result is not None and not call.mutation_started:
                call.commit_result = preparation.result
            call.commit_result = self._preparation_result(call, call.commit_result)
            # Refusal must retire the invocation that acquired this owner, even
            # if a reentrant host callback changed its mutable display fields.
            call.id, call.name = preparation.invocation_id, preparation.name
            call.invocation_digest = preparation.invocation_digest
            call.arguments, call.program_arguments = preparation.arguments, preparation.program_arguments
            call.delivery_owner, call.parent_call = preparation.delivery_owner, preparation.parent_call
            if self.pending.get(call.id) is not call:
                self._discard_native_job(call)
            if preparation.restaging:
                if not self._restart_staging(call, {"context_changed": "frame", "scene_changed": False}):
                    preparation.restaging = False
                    call.commit_result = self._preparation_result(call, {
                        "error": "The staged program could not be discarded safely before restaging", "error_code": "CLEANUP_FAILED"})
                # Cleanup itself can call Python. Preserve any Stop/expiry that
                # arrived while the old stage and writer were still pinned.
                if preparation.restaging:
                    try:
                        self._check_native_preparation(call, preparation, preparation.document,
                                                       restaging=True, staged_job=False)
                    except Exception as refusal:
                        preparation.restaging = False
                        if not isinstance(preparation.result, dict) or not preparation.result.get("error"):
                            preparation.result = _preparation_error_result(refusal)
                if call.revoked or isinstance(preparation.result, dict) and preparation.result.get("error"):
                    preparation.restaging = False
                    call.commit_result = self._preparation_result(call, preparation.result)
        finally:
            preparation.leaving = False
            call.id, call.name = preparation.invocation_id, preparation.name
            call.invocation_digest = preparation.invocation_digest
            call.arguments, call.program_arguments = preparation.arguments, preparation.program_arguments
            call.delivery_owner, call.parent_call = preparation.delivery_owner, preparation.parent_call
            call.native_preparation = None
            call.finished_preparation = preparation
            if preparation.writer_retained:
                preparation.job.release_native_preparation(preparation)
                preparation.writer_retained = False
        return call.commit_result

    def _begin_prepared_apply(self, call, preparation, executor, document):
        """The last controller fence after job hydration/verification callbacks."""
        self._check_native_preparation(call, preparation, document)
        if call.name == "angelo_accept_preview":
            preparation.job.backend.verify(preparation.binding)
            current, failure = preparation.precondition, None
        else:
            current, failure = self._fresh_precondition(call, executor)
        self._check_native_preparation(call, preparation, document)
        if failure is not None:
            raise failure
        if current != preparation.precondition:
            from .program_context import change_code
            change = self._binding_change(call, executor, current)
            raise RefusalError(changed_message(change, "immediately before typed apply")
                               or "The target changed during preparation; the typed edit was not applied",
                               code=change_code(change))
        call.mutation_started = True
        self._turn_mutated = True

    def commit_native_job(self, call, executor, document, *, defer_finish=False):
        started = time.monotonic()
        try:
            try:
                result = self._commit_native_job(call, executor, document, defer_finish=defer_finish)
            except Exception as error:
                make_error = _preparation_error_result if call.native_preparation is not None else error_result
                result = make_error(error, partial_changes_possible=bool(call.mutation_started),
                                    **({"scene_changed": False} if not call.mutation_started else {}))
                if result.get("partial_changes_possible"):
                    result["request_checkpoint"] = self.request_checkpoint
                if not defer_finish:
                    try:
                        return self.finish_tool(call, result) or result
                    finally:
                        call.committing = False
            if defer_finish:
                preparation = call.native_preparation
                if preparation is not None and preparation.result is not None and not call.mutation_started:
                    result = preparation.result
                result = self._preparation_result(call, result)
                call.commit_result = result
                call.committing = call.committing or call.mutation_started or bool(result.get("partial_changes_possible"))
            return result
        finally:
            self._main_thread_block(getattr(call, "metrics", None), started)

    def finish_native_commit(self, call, outcome):
        """Publish once, after the native Undo capsule returns its actual outcome."""
        from .program_finalization import after_native_undo
        if call.native_preparation is not None:
            raise RefusalError("Native preparation must close before final delivery", code="OWNERSHIP")
        if call.finished_preparation is not None and call.finished_preparation.delivering:
            return call.commit_result
        try:
            if self.pending.get(call.id) is not call:
                # A refused preflight can already be final; a second completion is inert.
                return self.state.get("results", {}).get(call.id, {}).get("result", call.commit_result)
            raw = call.commit_result
            preparation = call.finished_preparation
            published, diagnostics = _preparation_outcome(outcome)
            restaging = preparation.restaging if preparation is not None else (
                isinstance(raw, dict) and raw.get("restaging") is True and raw.get("final") is False
                and raw.get("scene_changed") is False and not raw.get("partial_changes_possible"))
            if restaging:
                if published is False and not diagnostics:
                    return raw  # The same approved call restages; this is not its terminal result.
                raw = {"error": "Native Undo cleanup failed before restaging; inspect the scene",
                       "error_code": "INTERNAL_ERROR", "partial_changes_possible": True}
            if preparation is not None and not call.mutation_started and isinstance(raw, dict):
                # Possible preparation callback effects are distinct from typed
                # apply. Keep the actual refusal and publication as separate facts.
                result = dict(raw, undo_published=published, undoable=False)
                if "error" in diagnostics or "error_code" in diagnostics:
                    result["native_undo_error"] = diagnostics.get("error", diagnostics.get("error_code"))
                finalization = [key + ": " + value for key, value in diagnostics.items()
                                if key not in {"error", "error_code"}]
                if finalization:
                    result["native_finalization_error"] = "; ".join(finalization)
                if diagnostics or published is None:
                    result.update(partial_changes_possible=True, request_checkpoint=self.request_checkpoint)
                    if result.get("scene_changed") is False:
                        result.pop("scene_changed")
            else:
                result = after_native_undo(raw, outcome, self.request_checkpoint)
            return self.finish_tool(call, result, persist=preparation.persist if preparation else True) or result
        finally:
            call.commit_result = None
            call.committing = False
            call.finished_preparation = None

    def _commit_native_job(self, call, executor, document, *, defer_finish=False):
        """Only an Undo-enabled UI operator may call this live-edit continuation.

        Exact preconditions are refreshed after staging and immediately before
        the commit. A late/stopped/expired callback cannot use a retained job.
        """
        executor.studio_context = self
        job = self.native_jobs.get(call.id)
        preparation = call.native_preparation
        if preparation is not None:
            self._check_native_preparation(call, preparation, document)
        if not self._tool_available(call, document):
            return {"error_code": 'OWNERSHIP', "error": "This staged invocation no longer owns the document", "scene_changed": False}
        if (not call.authorized or job is None or not job.ready_to_commit or job.closed or call.committing
                or getattr(job, "invocation_id", None) != call.id):
            return {"error_code": 'OWNERSHIP', "error": "The exact authorized stage is not ready to commit", "scene_changed": False}
        if clean_options(self.state.get("options"))["approval"] == "READ_ONLY":
            result = {"error_code": 'READ_ONLY', "error": "This chat is read-only; the staged change was discarded", "scene_changed": False}
            self.finish_tool(call, result)
            return result
        # From the fresh precondition through the journal write to the job's own first
        # verification nothing can change the document: one exact fence (exact_fence),
        # which the job's hydration ends.
        with exact_fence.fence():
            try:
                if call.name == "angelo_accept_preview" and hasattr(job, "backend"):
                    # begin() has consumed the PreviewStore record. Its immutable
                    # native binding, not a lookup of that now-absent record, is the
                    # exact before-commit authority (commit checks it again).
                    if call.metrics:
                        call.metrics.count("precondition_checks")
                    self._measure(call.metrics, "precondition_ms", lambda: job.backend.verify(job.binding))
                    current, failure = call.precondition, None
                else:
                    current, failure = self._fresh_precondition(call, executor)
                if preparation is not None:
                    self._check_native_preparation(call, preparation, document)
                if not self._tool_available(call, document):
                    return {"error_code": 'CANCELLED', "error": "The staged invocation stopped during revalidation", "scene_changed": False}
                if failure is not None or current != call.precondition:
                    outcome = self._frame_rebind(call, executor) if preparation is None else None
                    if (isinstance(outcome, dict) and "arguments" in outcome
                            and self._restart_staging(call, {"context_changed": "frame", "scene_changed": False})):
                        # Nothing was written: the stage is discarded and the same
                        # approved program stages again at the current frame.
                        return {"restaging": True, "final": False, "scene_changed": False}
                    moment = ("during native preparation; the typed edit was not applied" if preparation is not None
                              else "before commit; the staged result was discarded, nothing changed")
                    if isinstance(outcome, dict):
                        raise RefusalError(refusal_message(outcome.get("refused") or outcome["reason"] + " again", moment),
                                           code=outcome.get("error_code", "FRAME_CHANGED"), details=outcome.get("refusal_details"))
                    if failure is not None:
                        raise failure
                    change = self._binding_change(call, executor, current)
                    from .program_context import change_code
                    raise RefusalError(changed_message(change, moment)
                                       or ("The target changed during native preparation; the typed edit was not applied"
                                           if preparation is not None else
                                           "The target changed before commit; staged work was discarded without live changes"),
                                       code=change_code(change))
                self._journal_tool(call, "commit_started")
                if preparation is not None:
                    self._check_native_preparation(call, preparation, document)
                if not self._tool_available(call, document):
                    return {"error_code": 'CANCELLED', "error": "The staged invocation stopped before commit", "scene_changed": False}
                call.committing = True
                if preparation is None:
                    call.mutation_started = True
                    self._turn_mutated = True
                if call.metrics:
                    call.metrics.count("native_commits")
                apply = job.commit
                if preparation is not None:
                    apply = lambda: job.commit(before_apply=lambda: self._begin_prepared_apply(call, preparation, executor, document))
                result = self._measure(call.metrics, "commit_ms", apply)
            except Exception as error:
                make_error = _preparation_error_result if preparation is not None else error_result
                result = make_error(error, partial_changes_possible=bool(call.mutation_started),
                                    **({"scene_changed": False} if not call.mutation_started else {}))
        if isinstance(result, dict) and result.get("partial_changes_possible"):
            result["request_checkpoint"] = self.request_checkpoint
            if result.get("scene_changed") is False:
                result.pop("scene_changed")  # Unknown is not a measured no-op.
        try:
            if defer_finish:
                return result  # The UI owns same-dispatch native publication, then finish_native_commit.
            return self.finish_tool(call, result) or result
        finally:
            # Deferred callers keep ownership until native publication has finished.
            if not defer_finish:
                call.committing = False
