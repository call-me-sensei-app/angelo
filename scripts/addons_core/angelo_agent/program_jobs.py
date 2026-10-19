# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cooperative, main-thread staged programs, independent of Blender's UI.

``poll`` performs bounded units of staging, action and validation work. It never
commits. The host must call ``commit`` inside one normal UNDO-enabled Blender
operator. Opt-in native action quanta contain at most eight operations or 4 ms
of action time, always allowing one operation. Opt-in fused backends then chain
units (preparation, quanta, checks, read-back) within one poll while its work
stays under 16 ms, or under the poll's own hydration and suspension cost when
that is larger (at most 100 ms, ``fused_work_budget``), so one hydration serves
them all; a poll always completes one unit. Expensive kernels can exceed either
budget; cancellation is between polls, never inside a kernel. Hydration and
serialization never spend either budget. An operation the backend says must wait (``operation_waits``: a
transform during an artist's modal tool) ends the poll before hydration or
the operation, and a later poll retries it. An immutable pre-request
checkpoint remains the recovery baseline.
"""

import copy
import time
import uuid

from .exact_fence import HandOff
from .refusal_codes import error_result, refusal_code
from .program_job_state import (ProgramError, WriterBusyError, WriterLease, PreviewStore,
                                WRITER, PREVIEWS, native_preparation_error_result,
                                _native_error_attributes, resumed_before_writes, failure_facts)


MAX_QUANTUM_OPERATIONS = 8
MAX_QUANTUM_ACTION_SECONDS = .004
# File-backed yields cost a bundle write/reload and artist depsgraph rebuild;
# closed stages can instead transfer native ownership. Both still benefit from
# bounded fused work. Hydration and suspension are not counted.
MAX_FUSED_POLL_SECONDS = .016
# Every poll of a staged program pays a fixed hydration and suspension (the
# stage's exact validation, provenance and ownership transfer), which grows
# with the stage, not with the poll's work. With 16 ms of work per poll, a
# 32-object material stage spent most of its time there and, under load, the
# split into more polls multiplied that cost (doc/angelo/performance.rst,
# "Amortized poll budget"). A poll whose fixed cost exceeds 16 ms may therefore
# work as long as that cost, up to this bound on the extra main-thread time.
MAX_AMORTIZED_POLL_SECONDS = .1


def fused_work_budget(residency_seconds):
    """A fused poll's work budget: its hydration plus the last suspension, within [16 ms, 100 ms].

    While residency costs at most 100 ms, a program's total time stays within
    about twice its work plus one poll's fixed cost, instead of paying that
    cost once per 16 ms of work; a slower residency is spread over 100 ms of
    work per poll. A cheap residency keeps the 16 ms budget. Measured on the
    backend's budget clock, like the work itself.
    """
    return min(MAX_AMORTIZED_POLL_SECONDS, max(MAX_FUSED_POLL_SECONDS, residency_seconds))
# Phases whose units read or write the stage. REVALIDATE and READY_TO_COMMIT never hydrate it.
_STAGE_PHASES = frozenset({"COPY", "REMAP", "OPERATIONS", "VALIDATE", "READBACK"})


class ProgramJob:
    """A staged transaction and its explicit host-side commit continuation.

    The backend owns RNA work; this state machine owns safe yields, writer
    admission, retained-preview ownership, and final result semantics.
    ``closed`` only becomes true on a final result, not on progress or intent.
    """

    is_program_job = True

    def __init__(self, backend, program, *, owner=None, previews=PREVIEWS, writer=WRITER, preview=None,
                 approved_obligations=None):
        from .program_obligation_lifecycle import initial_obligations
        self.id = str(uuid.uuid4())
        self.backend, self.program, self.owner = backend, copy.deepcopy(program), owner
        self.obligations = initial_obligations(self.program, preview, approved_obligations)
        self.checks = self.obligations.checks() if self.obligations else []
        self.checks_digest = self.obligations.checks_digest if self.obligations else ""
        self.previews, self.writer = previews, writer
        self.binding = preview["binding"] if preview else None
        self.stage = preview["stage"] if preview else None
        self.delta = copy.deepcopy(preview["delta"]) if preview else None
        self.preview_id = preview["id"] if preview else ""
        self.accepting_preview = bool(preview)
        self.phase = "REVALIDATE" if preview else "PREPARE"
        self.index = 0
        self.closed = False
        self.ready_to_commit = False
        self.waiting = False  # This poll stopped at an operation that waits (operation_waits).
        self.result = None
        self.operation_results, self.check_results = [], []
        self.started = time.perf_counter()
        self.native_ms = 0.0
        self.verification_ms = 0.0  # Checks, stage validation and read-back only.
        self._suspension_seconds = 0.  # The last suspension's cost on the budget clock (fused_work_budget).
        self._native_preparation_owner = None
        self._writer_release_pending = False
        self.writer.acquire(self)

    def retain_native_preparation(self, owner):
        """Keep this existing writer lease until the native owner closes."""
        if owner is None or self._native_preparation_owner is not None or self.writer.owner is not self:
            raise ProgramError("The staged writer has no exclusive preparation ownership", code="OWNERSHIP")
        self._native_preparation_owner = owner

    def release_native_preparation(self, owner):
        """Consume only the exact same-dispatch owner, without stage cleanup."""
        if self._native_preparation_owner is not owner or owner is None:
            raise ProgramError("The native preparation owner no longer matches", code="OWNERSHIP")
        self._native_preparation_owner = None
        if self._writer_release_pending:
            self._writer_release_pending = False
            self.writer.release(self)

    def _release_writer(self):
        if self._native_preparation_owner is not None:
            self._writer_release_pending = True
        else:
            self.writer.release(self)

    @classmethod
    def accept(cls, identity, owner, *, previews=PREVIEWS, writer=WRITER):
        # Do not consume a retained preview if another chat owns the writer.
        record = previews.get(identity, owner)
        job = cls(record["backend"], record["program"], owner=owner,
                  previews=previews, writer=writer, preview=record)
        try:
            previews.take(identity, owner)
        except Exception:
            job.writer.release(job)
            raise
        return job

    @property
    def progress(self):
        operations = len(self.program["operations"])
        checks = len(self.checks)
        total = max(1, len(self.program.get("read_set", [])) + len(self.program.get("write_set", []))
                    + operations + checks + 5)
        completed = {"PREPARE": 0, "COPY": 1 + self.index, "REMAP": 1 + self._copy_count(),
                     "OPERATIONS": 2 + self._copy_count() + self.index,
                     "VALIDATE": 2 + self._copy_count() + operations + self.index,
                     "READBACK": total - 2, "REVALIDATE": total - 1,
                     "READY_TO_COMMIT": total, "PREVIEW": total, "COMMITTED": total}.get(self.phase, 0)
        done = operations if self.accepting_preview else len(self.operation_results)
        return {"phase": self.phase, "operation_index": min(done, operations),
                "operation_count": operations, "completed_units": min(completed, total), "total_units": total}

    def _copy_count(self):
        return len(self.binding.get("handles", ())) if isinstance(self.binding, dict) else 0

    def _progress(self):
        result = {"success": True, "status": "progress", "final": False, "program_id": self.id,
                  "progress": self.progress, "scene_changed": False, "native_exec_ms": round(self.native_ms, 3),
                  "cancellation": "Safe boundaries between bounded polls; no interruption inside a kernel"}
        if self.waiting:
            result["waiting"] = "The next operation waits for the active Blender tool to finish"
        return result

    def _next_waits(self):
        """Whether the next operation must wait for the host (``backend.operation_waits``); nothing runs then."""
        waits, operations = getattr(self.backend, "operation_waits", None), self.program["operations"]
        self.waiting = bool(waits is not None and self.phase == "OPERATIONS" and self.index < len(operations)
                            and waits(operations[self.index]))
        return self.waiting

    def _operation_quantum(self):
        """Action-only budget starts after hydration; default backends do one."""
        operations = self.program["operations"]
        coalesce = getattr(self.backend, "coalesce_operations", False)
        clock = getattr(self.backend, "operation_quantum_clock", time.perf_counter)
        started, executed = clock(), 0
        while self.index < len(operations):
            if executed and (not coalesce or executed >= MAX_QUANTUM_OPERATIONS or
                             clock() - started >= MAX_QUANTUM_ACTION_SECONDS) or self._next_waits():
                break
            self.applying = self.index  # The operation in flight, which a failure names (failure_facts).
            self.operation_results.append(self.backend.apply(self.stage, operations[self.index]))
            self.applying, self.index = None, self.index + 1
            executed += 1
            if self.closed:
                return  # A synchronous Stop callback already discarded this stage.
        if self.index == len(operations):
            self.phase, self.index = "VALIDATE", 0

    def _error(self, error):
        prepared = self._native_preparation_owner is not None
        attributes = _native_error_attributes(error)
        cleanup_error = dict.get(attributes, "cleanup_error", "") if prepared else getattr(error, "cleanup_error", "")
        if prepared and type(cleanup_error) is not str:
            cleanup_error = ""
        if self.stage is not None:
            try:
                self.backend.discard(self.stage)
            except Exception as cleanup:
                message = native_preparation_error_result(cleanup)["error"] if prepared else str(cleanup)
                cleanup_error += ("; " if cleanup_error else "") + message[:1000]
        # Journal restoration is a candidate, never proof. Reverse ID remaps
        # also point staged copies at live dependencies, so read the original
        # binding only after cleanup has removed their temporary users.
        rollback_verified = False
        rollback_error = dict.get(attributes, "rollback_error", "")
        rollback_candidate = (self.stage is not None and type(error) is ProgramError  # Commit rollback, or a resume.
                              and dict.get(attributes, "rollback_restored") is True)
        if (rollback_candidate and not dict.get(attributes, "partial_changes_possible")
                and not cleanup_error and not getattr(self.backend, "mutation_started", False)
                and not getattr(self.backend, "cleanup_pending", False)):
            try:
                self.backend.verify_rollback(self.stage)
                rollback_verified = True
            except Exception as rollback:
                rollback_error = native_preparation_error_result(rollback)["error"] if prepared else str(rollback)[:1000]
        self.stage = None
        self._release_writer()
        self.closed, self.ready_to_commit, self.phase = True, False, "FAILED"
        failure = native_preparation_error_result(error) if prepared else error_result(error)
        partial = dict.get(attributes, "partial_changes_possible") is True if prepared else bool(getattr(error, "partial_changes_possible", False))
        self.result = {**failure, "success": False, "status": "failed", "final": True,
                       "program_id": self.id, "scene_changed": False,
                       "partial_changes_possible": partial,
                       "native_exec_ms": round(self.native_ms, 3), "operation_results": self.operation_results,
                       "checks": self.check_results}
        failure_facts(self.result, getattr(self, "applying", None), self.program.get("operations"))
        # An unclassified failure, or a classified one wrapping an unclassified
        # cause (program_storage._hydration_cleanup_error), read no live fingerprint.
        unclassified = ((failure["error_code"] if prepared else refusal_code(error)) in {"UNKNOWN", "INTERNAL_ERROR"}
                        or (dict.get(attributes, "unclassified_cause") if prepared
                            else getattr(error, "unclassified_cause", False)) is True)
        if not rollback_verified and (rollback_candidate or unclassified):
            self.result.pop("scene_changed", None)
            self.result["partial_changes_possible"] = True
        if not prepared and isinstance(getattr(error, "context_change", None), str):
            # A recoverable binding change (program_context), e.g. the frame moved.
            self.result["context_changed"] = error.context_change
        if getattr(self.backend, "mutation_started", False):
            self.result.update(mutation_started=True, scene_changed=True, partial_changes_possible=True)
        if cleanup_error:
            self.result.update(cleanup_error=cleanup_error[:2000], cleanup_warning=True, partial_changes_possible=True)
            if rollback_candidate:
                self.result.pop("scene_changed", None)
        if rollback_error:
            self.result["rollback_error"] = rollback_error
        if rollback_verified:
            self.result["rollback_verified"] = True
        if getattr(self.backend, "cleanup_pending", False):
            self.result.update(cleanup_pending=True, partial_changes_possible=True)
            self.result["recovery"] = "Temporary native data cleanup is pending. New staging and checkpoints are blocked; retry cleanup or restore the durable pre-request checkpoint."
        if self.result["partial_changes_possible"]:
            self.result.setdefault("recovery", "Inspect the scene and restore the durable pre-request snapshot if necessary. "
                                   "Do not retry or automatically undo the completed user history.")
        return self.result

    def poll(self):
        if self.closed:
            return self.result
        # A wrong-thread caller must not discard/drop Main ownership while a
        # legitimate UI-thread poll is in flight. Reject before error cleanup.
        self.backend.ensure_main_thread()
        start = time.perf_counter()
        try:
            self.backend.ensure_main_thread()
            self.backend.check_context(self.binding)
            if self._next_waits():  # Before hydration: a waiting poll does no stage work.
                self.native_ms += (time.perf_counter() - start) * 1000
                return self._progress()
            fused = getattr(self.backend, "fuse_phases", False)
            clock = getattr(self.backend, "operation_quantum_clock", time.perf_counter)
            resident, work, budget = False, 0., MAX_FUSED_POLL_SECONDS
            while True:
                if not resident and self.phase in _STAGE_PHASES:
                    # Hydrate lazily, once per poll, before the budget clock.
                    if self.stage is not None and hasattr(self.backend, "resume_stage"):
                        hydrating = clock()
                        self.stage = resumed_before_writes(self.backend, self.stage)  # Staging writes nothing live.
                        budget = fused_work_budget(clock() - hydrating + self._suspension_seconds)
                    resident = True
                phase, started = self.phase, clock()
                self._step()
                work += clock() - started
                resident = resident or phase == "PREPARE"
                if not fused or self.closed or self.ready_to_commit or self.waiting or work >= budget:
                    break
            if not self.closed and self.stage is not None and hasattr(self.backend, "suspend_stage"):
                # No staged Blender Main ID may survive a yield: a manual edit's
                # normal Undo snapshot must not encode a hidden agent scene.
                suspending = clock()
                self.stage = self.backend.suspend_stage(self.stage)
                self._suspension_seconds = clock() - suspending
        except Exception as error:
            self.native_ms += (time.perf_counter() - start) * 1000
            return self._error(error)
        self.native_ms += (time.perf_counter() - start) * 1000
        if self.closed:
            self.result["native_exec_ms"] = round(self.native_ms, 3)
            return self.result
        if self.ready_to_commit:
            return {"success": True, "status": "ready_to_commit", "final": False,
                    "ready_to_commit": True, "program_id": self.id, "preview_id": self.preview_id,
                    "delta": copy.deepcopy(self.delta), "progress": self.progress, "scene_changed": False,
                    "native_exec_ms": round(self.native_ms, 3)}
        return self._progress()

    def _verified(self, callback, *args):
        """Run one check or read-back call, timing it as verification."""
        started = time.perf_counter()
        try:
            return callback(*args)
        finally:
            self.verification_ms += (time.perf_counter() - started) * 1000

    def _check_obligations(self):
        """A prepared program may not lose or change any approved assertion."""
        from .program_obligation_lifecycle import assert_checks
        assert_checks(self.obligations, self.program, self.checks, self.checks_digest)

    def completion_obligations(self):
        from .program_obligation_lifecycle import completion_obligations
        return completion_obligations(self)

    def _summarize_checks(self):
        from .program_obligation_lifecycle import summarize_checks
        summarize_checks(self)

    def _step(self):
        """Run one bounded unit of the current phase; ``poll`` owns residency."""
        if self.phase == "PREPARE":
            from .program_obligation_lifecycle import bound_obligations
            from .exact_fence import fence
            with fence():  # Staging's own reads (which materials it borrows) reuse bind's walks.
                self.binding = self.backend.bind(self.program)
                self.obligations = bound_obligations(self.program, self.binding, self.obligations)
                self.checks, self.checks_digest = self.obligations.checks(), self.obligations.checks_digest
                self.stage = self.backend.create_stage(self.binding)
            # Native preparation already copies/remaps the complete graph
            # before its first serialized yield. Do not reload/rewrite it
            # once per no-op copy. Incremental test/other backends retain
            # their explicit COPY/REMAP protocol.
            self.phase, self.index = ("OPERATIONS" if getattr(self.backend, "prepared_stage_ready", False) else "COPY"), 0
        elif self.phase == "COPY":
            handles = self.binding["handles"]
            if self.index < len(handles):
                self.backend.copy_object(self.stage, handles[self.index])
                self.index += 1
            if self.index == len(handles):
                self.phase, self.index = "REMAP", 0
        elif self.phase == "REMAP":
            self.backend.finish_stage(self.stage)
            self.phase, self.index = "OPERATIONS", 0
        elif self.phase == "OPERATIONS":
            self._operation_quantum()
        elif self.phase == "VALIDATE":
            if self.index == 0:
                self._check_obligations()
            checks = self.checks
            if self.index < len(checks):
                outcome = self._verified(self.backend.validate, self.stage, checks[self.index])
                self.check_results.append(outcome)
                if outcome.get("status") == "unobservable":  # Certifies nothing: the run is partial.
                    self.unobservable = getattr(self, "unobservable", set()) | {self.index}
                elif not outcome.get("passed", False):
                    raise ProgramError("Expected check failed: " + str(outcome.get("message", outcome.get("kind", "check"))), code="CHECK_FAILED")
                self.index += 1
            if self.index == len(checks):
                self._check_obligations()
                self._verified(self.backend.validate_stage, self.stage)
                self.phase, self.index = ("READY_TO_COMMIT" if self.accepting_preview else "READBACK"), 0
                if self.accepting_preview:
                    self.ready_to_commit = True
        elif self.phase == "READBACK":
            self.delta = self._verified(self.backend.delta, self.stage)
            self._verified(self.backend.verify, self.binding)
            if self.program["mode"] == "PREVIEW":
                record = self.previews.retain(self.backend, self.stage, self.binding, self.program, self.delta, self.owner,
                                             self.checks, self.obligations)
                self.stage = None  # The PreviewStore owns cleanup from now on.
                self._release_writer()
                self.phase, self.closed = "PREVIEW", True
                self.result = {"success": True, "status": "preview", "final": True, "program_id": self.id,
                               "preview_id": record["id"], **self._preview_live_revisions(),
                               "delta": copy.deepcopy(self.delta),
                               "scene_changed": False, "partial_changes_possible": False,
                               "readable_summary": self.delta.get("summary", "Native staged preview; no live scene edits"),
                               "checks": self.check_results, "operation_results": self.operation_results}
                self._summarize_checks()
                for key in ("views", "preview_note"):
                    if key in self.delta:
                        self.result[key] = copy.deepcopy(self.delta[key])
            else:
                self.phase, self.ready_to_commit = "READY_TO_COMMIT", True
        elif self.phase == "REVALIDATE":
            self._verified(self.backend.verify, self.binding)
            # Global predicates such as object_count can become false even
            # when all explicitly bound object revisions remain unchanged.
            self.phase, self.index = "VALIDATE", 0
        elif self.phase != "READY_TO_COMMIT":
            raise ProgramError("Invalid native program phase", code="INTERNAL_ERROR")

    def _preview_live_revisions(self):
        """A PREVIEW changes nothing live: its bound revisions, just re-verified
        by READBACK, still bind other programs. Accept commits through fresh
        checks and returns post-commit revisions like any APPLY."""
        revisions = self.binding.get("revisions") if isinstance(self.binding, dict) else None
        if not isinstance(revisions, dict):
            return {}
        return {"live_revisions": {
            "status": "UNCHANGED_UNTIL_ACCEPT",
            "objects": [{"handle": handle, "object_revision": revision, "role": "DECLARED"}
                        for handle, revision in revisions.items()],
            "note": ("The live scene is unchanged: these declared revisions still bind other programs while "
                     "nothing else edits them. Objects this preview would create have no handle until Accept; "
                     "the accept result carries post-commit handles and revisions.")}}

    def commit(self, *, before_apply=None):
        """The sole live mutation entry point; invoke from the host UNDO operator."""
        if self.closed:
            return self.result
        if not self.ready_to_commit or self.phase != "READY_TO_COMMIT":
            raise ProgramError("A staged program must finish validation before commit", code="INTERNAL_ERROR")
        self.backend.ensure_main_thread()
        start = time.perf_counter()
        try:
            self.backend.ensure_main_thread()
            self.backend.check_context(self.binding)
            self.backend.verify(self.binding)  # Exact, fresh revisions, immediately before writes.
            if hasattr(self.backend, "resume_stage"):
                self.stage = resumed_before_writes(self.backend, self.stage)
            self._check_obligations()
            for index, check in enumerate(self.checks):
                outcome = self.backend.validate(self.stage, check)
                if outcome.get("status") == "unobservable":
                    self.unobservable = getattr(self, "unobservable", set()) | {index}
                elif not outcome.get("passed", False):
                    raise ProgramError("Expected check changed before commit: " + str(outcome.get("message", check["kind"])), code="CHECK_FAILED")
            if hasattr(self.backend, "prepare_commit"):
                self.backend.prepare_commit(self.stage)
            # The controller's exact preparation fence runs after the last callback-capable
            # preflight, immediately before typed apply: a fencing backend runs it in commit,
            # after its final stage evaluation and with its own checks (final_checks).
            handed = None  # Run by the backend's commit (a HandOff records that it ran).
            if getattr(self.backend, "fences_before_apply", False):
                handed = self.backend.pending_before_apply = HandOff(before_apply) if before_apply is not None else None
            elif before_apply is not None:
                before_apply()
            self.backend.pending_checks = self.checks  # Re-run on the live objects inside commit (live_checks).
            self.backend.pending_unobservable = set(getattr(self, "unobservable", ()))
            outcome = self.backend.commit(self.stage)
            self.stage = None
            if handed is not None and not handed.ran:  # Handed off and never run, even if cleared: loud.
                raise ProgramError("The backend committed without the controller's preparation fence", code="INTERNAL_ERROR")
            self.native_ms += (time.perf_counter() - start) * 1000
            self.result = dict(outcome, success=True, final=True, status="committed", program_id=self.id,
                               preview_id=self.preview_id, delta=copy.deepcopy(self.delta), scene_changed=True,
                               undoable=True, native_exec_ms=round(self.native_ms, 3),
                               checks=self.check_results, operation_results=self.operation_results)
            from .stage_names import correct
            self.result = correct(self.result)  # Names commit had to change since staging (stage_names).
            self._summarize_checks()
            self.phase, self.closed, self.ready_to_commit = "COMMITTED", True, False
            self._release_writer()
            return self.result
        except Exception as error:
            self.native_ms += (time.perf_counter() - start) * 1000
            return self._error(error)

    def cancel(self):
        if self.closed:
            return self.result  # A completed commit is never automatically undone.
        self.backend.ensure_main_thread()
        cleanup_error = ""
        try:
            self.backend.ensure_main_thread()
            if self.stage is not None:
                self.backend.discard(self.stage)
        except Exception as error:
            cleanup_error = (native_preparation_error_result(error)["error"]
                             if self._native_preparation_owner is not None else str(error))[:1000]
        finally:
            self.stage = None
            self._release_writer()
        self.phase, self.closed, self.ready_to_commit = "CANCELLED", True, False
        self.result = {"success": False, "status": "cancelled", "final": True, "program_id": self.id,
                       "cancelled": True, "scene_changed": False, "partial_changes_possible": False,
                       "message": "Stopped before commit; the staged edits were discarded. No live changes were applied."}
        if cleanup_error:
            self.result.update(cleanup_error=cleanup_error, cleanup_warning=True,
                               message="Stopped before commit; no live changes were applied. Temporary data teardown reported a cleanup warning.")
        if getattr(self.backend, "cleanup_pending", False):
            self.result.update(cleanup_pending=True, partial_changes_possible=True,
                               recovery="Temporary native data cleanup is pending. New staging and checkpoints are blocked; retry cleanup or restore the durable pre-request checkpoint.")
        return self.result

    def suspend(self):
        """Remove staged Blender Main IDs before a project/checkpoint save.

        The private bundle is hydrated lazily on a subsequent safe poll. This
        does not authorize the live edit, and suspended previews own no Main IDs.
        """
        if not self.closed and self.stage is not None and hasattr(self.backend, "suspend_stage"):
            self.backend.ensure_main_thread()
            self.stage = self.backend.suspend_stage(self.stage)
