# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared staged-job errors, one writer lease and retained preview ownership.

program_jobs re-exports these existing identities. No duplicate writer or
preview registry is created when the scheduler or a backend imports them.
"""

import copy
import time
import uuid
from collections import OrderedDict

from .refusal_codes import CODES, RefusalError

class ProgramError(RefusalError, RuntimeError):
    partial_changes_possible = False


def resumed_before_writes(backend, stage):
    """``backend.resume_stage(stage)``; a failure there came before any live write, so the job verifies the live
    binding (``rollback_restored``) and reports what that proves, not an unproven partial change.

    Only an unknown or internal failure is marked: a classified one (a stale
    borrowed material) already reports what it proved, and a cleanup that
    failed keeps its own ``partial_changes_possible``. Without it, a
    hydration failure, at commit or while
    polling, reported partial changes although nothing was written.
    """
    from .refusal_codes import refusal_code, refusal_details
    try:
        return backend.resume_stage(stage)
    except Exception as error:
        if refusal_code(error) not in {"UNKNOWN", "INTERNAL_ERROR"}:
            raise  # Classified (a stale borrowed material...): it already reports what it proved.
        if type(error) is ProgramError:
            if not error.partial_changes_possible:
                error.rollback_restored = True
            raise
        failure = ProgramError(str(error)[:2000], code=refusal_code(error), details=refusal_details(error))
        failure.rollback_restored = True
        raise failure from error


def _native_error_attributes(error):
    # Runtime lookup only: program_context imports ProgramError during its initialization.
    from .program_context import ProgramFrameChanged
    from .scene_read_values import SceneQueryError
    known = type(error) is RefusalError or type(error) is ProgramError or type(error) is ProgramFrameChanged
    attributes = BaseException.__getattribute__(error, "__dict__") if known else {}
    if type(error) is SceneQueryError:   # scene_read_values.unreadable_revision: classified by its refusal_code.
        attributes = {"code": dict.get(BaseException.__getattribute__(error, "__dict__"), "refusal_code")}
    return attributes


def bound_revision(read, handle):
    """``read()``, the revision of an object that was exact when bound; unreadable now (Multires added, data
    over a budget) means it changed since: STALE_REVISION naming it, so the model reads it again."""
    from .scene_read_values import SceneQueryError
    try:
        return read()
    except SceneQueryError as error:
        if dict.get(BaseException.__getattribute__(error, "__dict__"), "refusal_code") is None:
            raise
        raise ProgramError("The exact target changed and its revision can no longer be read (%s); the staged program "
                           "was discarded, not applied" % str(error).partition(": ")[2][:200],
                           code="STALE_REVISION", details={"handles": [handle]}) from error


# A program refused at one of its bounds, as a fact beside the refusal (``failure_facts``). A recorded bridge trial
# met "Rule-based face selection exceeds 4096 elements" with nothing about what would work.
LIMIT_INSTEAD = ("Typed programs keep these bounds so a staged edit is checked and then committed or refused whole; "
                 "angelo_execute_python runs such an edit without them (its result reports what changed). A bound on "
                 "one operation or on one program's inputs also yields to splitting the work across operations or "
                 "programs.")


def failure_facts(result, index, operations):
    """A failed program's result with ``failed_operation`` (index and kind of the operation that was being applied,
    never one a failure between operations would name) and, at a bound (LIMIT_EXCEEDED), ``instead``."""
    within = isinstance(index, int) and isinstance(operations, list) and 0 <= index < len(operations)
    operation = operations[index] if within else None
    if isinstance(operation, dict):
        result["failed_operation"] = {"index": index, "kind": operation.get("kind")}
    if result.get("error_code") == "LIMIT_EXCEEDED":
        result["instead"] = LIMIT_INSTEAD
    return result


def native_preparation_error_result(error, **fields):
    """Format retained-native-owner failures without invoking exception callbacks."""
    args = BaseException.args.__get__(error)
    message = (args[0][:2000] if type(args) is tuple and args and type(args[0]) is str else
               "Native preparation failed; inspect the scene and request checkpoint")
    attributes = _native_error_attributes(error)
    code = dict.get(attributes, "code", "UNKNOWN")
    code = code if type(code) is str and code in CODES else "UNKNOWN"
    result = {"error": message, "error_code": code, **fields}
    if result["error_code"] in {"UNKNOWN", "INTERNAL_ERROR"} and result.get("scene_changed") is False:
        result.pop("scene_changed")
        result["partial_changes_possible"] = True
    details = dict.get(attributes, "details")
    if type(details) is dict and details:
        result["refusal_details"] = details.copy()
    return result


class WriterBusyError(ProgramError):
    def __init__(self, message):
        super().__init__(message, code="WRITER_BUSY")


class WriterLease:
    """One cooperative writer across every chat, not a background-thread lock."""

    def __init__(self):
        self.owner = None

    def acquire(self, owner):
        if self.owner is not None and self.owner is not owner:
            raise WriterBusyError("Another native program is staging or awaiting commit; finish or stop it first")
        self.owner = owner

    def release(self, owner):
        if self.owner is owner:
            self.owner = None


WRITER = WriterLease()


class PreviewStore:
    """Small runtime-only retained previews; never a durable substitute for recovery."""

    def __init__(self, limit=3, lifetime=900, clock=time.monotonic):
        self.limit, self.lifetime, self.clock = limit, lifetime, clock
        self.records = OrderedDict()

    def _dispose(self, record):
        record["backend"].discard(record["stage"])

    def expire(self):
        expired = [identity for identity, record in self.records.items()
                   if self.clock() - record["created"] > self.lifetime]
        for identity in expired:
            record = self.records.pop(identity)
            self._dispose(record)

    def retain(self, backend, stage, binding, program, delta, owner, checks=None, obligations=None):
        from .program_obligation_lifecycle import bound_obligations, assert_checks
        checked = bound_obligations(program, binding, obligations)
        obligations = obligations if obligations is not None else checked
        checks = obligations.checks() if checks is None else copy.deepcopy(checks)
        digest = obligations.checks_digest
        assert_checks(obligations, program, checks, digest)
        self.expire()
        while len(self.records) >= self.limit:
            _, record = self.records.popitem(last=False)
            self._dispose(record)
        # Blender Main contains even unlinked IDs. Retained previews must not
        # become ghost collections in the artist's next .blend/checkpoint save.
        if hasattr(backend, "retain_stage"):
            stage = backend.retain_stage(stage)
        elif hasattr(backend, "suspend_stage"):
            stage = backend.suspend_stage(stage)
        identity = str(uuid.uuid4())
        record = {"id": identity, "backend": backend, "stage": stage, "binding": binding,
                  "program": copy.deepcopy(program), "delta": copy.deepcopy(delta), "owner": owner,
                  "checks": checks, "checks_digest": digest, "obligations": obligations, "created": self.clock()}
        self.records[identity] = record
        return record

    def get(self, identity, owner):
        self.expire()
        record = self.records.get(identity)
        if record is None:
            raise ProgramError("This preview expired, was discarded, or belongs to a previous document session", code="EXPIRED")
        if record["owner"] != owner:
            raise ProgramError("Only the conversation that created a preview may inspect, accept or discard it", code="OWNERSHIP")
        return record

    def take(self, identity, owner):
        record = self.get(identity, owner)
        del self.records[identity]
        return record

    def discard(self, identity, owner):
        record = self.take(identity, owner)
        self._dispose(record)
        return {"success": True, "final": True, "status": "discarded", "preview_id": identity,
                "scene_changed": False, "partial_changes_possible": False}

    def clear(self):
        records = list(self.records.values())
        self.records.clear()
        errors = []
        for record in records:
            try:
                self._dispose(record)
            except Exception as error:
                errors.append(str(error)[:1000])
        return errors


PREVIEWS = PreviewStore()
