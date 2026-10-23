# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private, request-owned proof of a native program's delivered verification.

No JSON field can create this authority. A completed job supplies the immutable
obligations and actual check rows before disposal; delivery seals them only after
cleanup, serialization and durability. Runtime contexts retain exact object
identities for this request, then revoke them when the request ends.
"""

from dataclasses import dataclass
import inspect
import json
import threading

from .program_checks import FrozenObligations, checks_digest, program_digest
from .request_fences import DeliveryOwner, RequestFence


def positive_rows(rows):
    """All explicit predicate rows, including nested guard evidence, must pass."""
    pending = [(rows, frozenset())]
    while pending:
        current, ancestors = pending.pop()
        if not isinstance(current, list) or not current:
            return False
        for row in current:
            if (not isinstance(row, dict) or row.get("passed") is not True or row.get("status") != "pass"
                    or id(row) in ancestors):
                return False
            if "checks" in row:
                pending.append((row["checks"], ancestors | {id(row)}))
    return True


def incomplete_verification(result, completion=None):
    """Private unknowns or public negative evidence can only refuse certification."""
    if completion is not None:
        try:
            if type(completion) is not ProgramCompletion or completion.obligations.unknowns():
                return True
        except (ValueError, TypeError, AttributeError):
            return True
    if not isinstance(result, dict) or "verification" not in result:
        return False
    verification = result["verification"]
    return (not isinstance(verification, dict) or verification.get("status") != "complete"
            or bool(verification.get("unknowns")))


def history_result_text(result, identifiers=None):
    """Keep partial status and first concrete unknown ahead of a bounded excerpt."""
    partial = incomplete_verification(result)
    shown = result
    if partial and isinstance(result.get("verification"), dict):
        verification = result["verification"]
        unknowns = verification.get("unknowns")
        if isinstance(unknowns, list):
            ordered = [{**{key: row[key] for key in ("operation", "root", "reason", "affected") if key in row}, **row}
                       if isinstance(row, dict) else row for row in unknowns]
            verification = dict(verification, unknowns=ordered)
        verification = {"status": verification.get("status"),
                        "unknown_count": len(unknowns) if isinstance(unknowns, list) else None,
                        **{key: value for key, value in verification.items() if key not in {"status", "unknown_count"}}}
        shown = {"verification": verification, **{key: value for key, value in result.items() if key != "verification"}}
    shown = identifiers.compact(shown) if identifiers is not None else shown
    separators = (",", ":") if identifiers is not None else None
    encoded = json.dumps(shown, ensure_ascii=False, separators=separators)
    if partial and len(encoded) > 5000 and isinstance(shown.get("verification"), dict):
        verification = shown["verification"]
        shown = dict(shown, verification={"status": verification.get("status"),
            "unknown_count": verification.get("unknown_count"), "excerpt": True,
            **{key: value for key, value in verification.items() if key not in {"status", "unknown_count", "excerpt"}}})
        encoded = json.dumps(shown, ensure_ascii=False, separators=separators)
    return encoded[:5000]


LIVE_FIELDS = ("handle", "name", "object_revision", "revision_omitted")


def live_rows(rows, observed=None):
    """Post-commit rows a next plan binds with, less those this message's observation shows at that revision.

    A row whose revision was left out (``revision_omitted``, a post-commit budget) is always kept with its
    reason: the handle is the only way a next plan can name a created object.
    """
    return [{key: row[key] for key in LIVE_FIELDS if key in row} for row in rows if isinstance(row, dict)
            and ("object_revision" not in row or observed is None or observed(row.get("handle")) != row["object_revision"])]


def commit_facts(result):
    """What a compact entry must keep beside its changes: objects that stopped sharing their data, what the World
    and its Sun lights show after a commit that changed them (``world``), and where what it edited that moves is at
    a few frames (``motion``, motion_facts)."""
    if not isinstance(result, dict):
        return {}
    rows = result.get("isolated_shared_data")
    facts = {"isolated_shared_data": rows[:32]} if isinstance(rows, list) and rows else {}
    facts.update((key, result[key]) for key in ("world", "motion") if isinstance(result.get(key), dict))
    return facts


def unchecked_receipt(tool, result, *, observed=None):
    """One handoff entry for a clean committed program that no check covered, or None.

    Such a program's operations stated nothing the host checks (an object
    created without an alias, a DELTA transform), so nothing about its result
    was verified, and the entry says so. A Claude context received the whole
    result instead: about 2.2 KB (about 900 written tokens) for one created
    cube, in 5 of 10 recorded cube creations. The
    entry keeps the changed objects, a dependents line, view IDs, shared edits
    and the post-commit ``live`` rows the same message's observation does not
    already show (``observed`` as in ``terse_receipt``); the observation shows
    the objects' current state. Anything else (a preview, any check, a failure,
    a replayed, partial, cleanup-pending, non-undoable, incompletely verified
    or otherwise uncertain outcome) returns None, and the whole result stays.
    """
    from .claude_outcomes import _changes, _receipt_rows, _shared_edits, _uncertain_evidence
    if (tool != "angelo_scene_program" or not isinstance(result, dict) or result.get("checks") != []
            or result.get("status") != "committed" or result.get("success") is not True
            or result.get("final") is not True or result.get("undoable") is not True or result.get("error")
            or result.get("replayed") or result.get("partial_changes_possible") or result.get("cleanup_warning")
            or result.get("cleanup_pending") or result.get("journal_durable") is False
            or incomplete_verification(result)
            or _uncertain_evidence({key: value for key, value in result.items() if key != "checks"},
                                   allow_revision_budget=True)):
        return None
    from .commit_fidelity import dependents_line  # Objects outside the program whose shape follows its edits.
    lines = ["Committed (one Undo step): " + _changes(result) + ". No host check covered it, so nothing was verified"]
    dependents = dependents_line(result)
    if dependents:
        lines.append(dependents[2:] if dependents.startswith("- ") else dependents)
    entry = {"unverified": ". ".join(lines)}
    views, _ = _receipt_rows(result.get("views"), ("id", "view_id", "name"))
    if views:
        entry["views"] = views
    if _shared_edits(result):
        entry["shared_edits"] = [dict(row, users=row["users"][:8]) for row in _shared_edits(result)[:4]]
    entry.update(commit_facts(result))
    live = result.get("live_revisions") if isinstance(result.get("live_revisions"), dict) else {}
    rows, truncated = _receipt_rows(live.get("objects"), LIVE_FIELDS)
    kept = live_rows(rows, observed)
    if kept:
        entry["live"] = kept
    if truncated or live.get("truncated"):
        entry["live_truncated"] = True
    return entry


def pure_program_checks(arguments):
    """Standalone compatibility only: an unresolved or partial program cannot certify."""
    from .program_spec import validate_program
    from .program_checks import resolve_obligations
    try:
        bundle = resolve_obligations(validate_program("angelo_scene_program", arguments))
        return None if bundle.unknowns() else bundle.checks()
    except (ValueError, TypeError, KeyError):
        return None


def pure_derived_flags(checks, arguments):
    from .program_checks import resolve_obligations
    from .program_spec import validate_program
    try:
        bundle = resolve_obligations(validate_program("angelo_scene_program", arguments))
        if not bundle.unknowns() and bundle.checks() == checks:
            return list(bundle.derived_flags)
    except (ValueError, TypeError, KeyError):
        pass
    return [False] * len(checks)


def completions_current(completions):
    """Recheck after potentially expensive hashing/summary work, before consumption."""
    return completions is None or all(completion is None or type(completion) is ProgramCompletion
        and type(completion.origin) is _CompletionOrigin and completion.origin.current(completion)
        for completion in completions)


def continuation_receipt(operation, result, completion=None):
    """Unverified effects must survive a later plan's correction prompt in full."""
    from .claude_outcomes import compact_receipt
    if incomplete_verification(result, completion):
        return {"tool": operation["tool"], "result": result}
    if operation["tool"] == "angelo_job_start" and isinstance(result, dict):
        # The job runs on: its ID and where its output lands are what the next reply names (job_host).
        return dict({"tool": operation["tool"]}, **{key: result[key] for key in JOB_RECEIPT if key in result})
    return compact_receipt(operation["tool"], result) or {"tool": operation["tool"], "status": result.get("status")}


JOB_RECEIPT = ("job", "status", "output", "workspace", "if_taken", "position", "error", "error_code")


def submitted_digest(arguments):
    """Reject non-JSON input through the controller's ordinary validation path."""
    try:
        return program_digest(arguments)
    except (TypeError, UnicodeError, OverflowError, RecursionError) as error:
        raise ValueError("Invocation arguments must be finite JSON with valid UTF-8; no work was admitted") from error


@dataclass(frozen=True)
class _CapturedProgram:
    obligations: FrozenObligations
    results_digest: str
    status: str


def capture_program(job):
    """Capture native evidence while its completed job still owns the bundle."""
    method = getattr(job, "completion_obligations", None)
    if not callable(method):
        return None
    try:
        obligations = method()
        if not isinstance(obligations, FrozenObligations):
            return None
        obligations.assert_integrity(job.program)
        return _CapturedProgram(obligations, checks_digest(job.check_results), job.result["status"])
    except (ValueError, TypeError, AttributeError, RuntimeError, KeyError, UnicodeError, OverflowError):
        return None  # Failed optional proof capture must not suppress the actual native outcome.


class _CompletionOrigin:
    """One completion identity, fenced to the request that actually executed it."""

    def __init__(self, host, call):
        self.host, self.fence = host, RequestFence(call.epoch, call.generation)
        self.call_id, self.completion, self.active = call.id, None, True
        self.delivery_owner = getattr(call, "delivery_owner", None)
        self.sealed_scope = None
        self.result = None
        self.submitted_arguments_digests = tuple(getattr(call, "submitted_arguments_digests", ()))

    def revoke(self):
        self.active = False
        self.result, self.completion, self.sealed_scope = None, None, None

    def current(self, completion):
        return (self.active and self.completion is completion and self.fence.matches(self.host)
                and not self.host.cancelled
                and (self.delivery_owner is None or self.delivery_owner.current(self.host)
                     or self.sealed_scope is not None and self.sealed_scope._sealed_current(self, completion)))


@dataclass(frozen=True)
class ProgramCompletion:
    """An identity reference, never a serializable or reconstructible receipt."""

    origin: object
    tool: str
    arguments_digest: str
    obligations: FrozenObligations
    results_digest: str
    status: str

    def verified(self, operation, result):
        from .claude_outcomes import _clean_program_checks
        try:
            if (type(self.origin) is not _CompletionOrigin or not self.origin.current(self)
                    or result is not self.origin.result or operation["tool"] != self.tool
                    or program_digest(operation["arguments"]) not in (self.arguments_digest, *self.origin.submitted_arguments_digests)
                    or result.get("status") != self.status
                    or incomplete_verification(result, self)
                    or _clean_program_checks(self.tool, result) is None
                    or _unclean_delivery(result)
                    or checks_digest(result["checks"]) != self.results_digest
                    or len(result["checks"]) != len(self.obligations.derived_flags)):
                return False
            # The immutable bundle was checked against the job's exact expanded
            # program at capture; the operation digest also covers recipe/Accept
            # arguments, which are not themselves that expanded program.
            return self.origin.call_id != "" and self.origin.current(self)
        except (ValueError, TypeError, KeyError, AttributeError):
            return False


def _unclean_delivery(result):
    return any(result.get(key) for key in ("cancelled", "staging_cleanup_pending", "staging_cleanup_failed", "cleanup_error"))


def mint_completion(host, call, captured, result):
    """Seal only the actual job's exact checks after ordinary delivery gates."""
    from .claude_outcomes import _clean_program_checks
    if captured is None or _clean_program_checks(call.name, result, allow_partial=bool(captured.obligations.unknowns())) is None:
        return None
    if _unclean_delivery(result):
        return None
    origin = _CompletionOrigin(host, call)
    if not origin.fence.matches(host) or host.cancelled:
        return None
    if result.get("status") != captured.status or checks_digest(result["checks"]) != captured.results_digest:
        return None
    program = call.program_arguments or call.arguments
    if call.name != "angelo_accept_preview" and program_digest(program) != captured.obligations.program_digest:
        return None
    completion = ProgramCompletion(origin, call.name, program_digest(call.arguments), captured.obligations,
                                   captured.results_digest, result["status"])
    origin.completion, origin.result = completion, result
    return completion


class CompletionScope:
    """The live request's delivered result/completion pairs; never a digest cache."""

    def __init__(self, context):
        self.context, self.records = context, {}
        self.delivered, self.sealed = {}, {}
        self.closed, self._lock = False, threading.RLock()

    def record(self, result, completion):
        with self._lock:
            if (type(completion) is not ProgramCompletion or type(completion.origin) is not _CompletionOrigin
                    or completion.origin.host is not self.context.host
                    or completion.origin.fence != self.context.request_fence):
                return
            if self.closed or self.context.cancelled:
                completion.origin.revoke()
                return  # A late actual result cannot reopen a finished request's authority.
            if completion.origin.current(completion):
                self.records[id(result)] = (result, completion)

    def get(self, result):
        with self._lock:
            record = self.records.get(id(result))
            if (not self.closed and record and record[0] is result
                    and not self.context.cancelled and record[1].origin.current(record[1])):
                return record[1]
        return None

    def _request_current(self):
        owner = getattr(self.context, "delivery_owner", None)
        return (not self.closed and not self.context.cancelled
                and self.context.request_fence.matches(self.context.host)
                and type(owner) is DeliveryOwner and owner.current(self.context.host))

    def _sealed_current(self, origin, completion):
        """A successfully delivered proof follows its request, never new native calls."""
        with self._lock:
            record = self.sealed.get(id(origin.result))
            retained = self.records.get(id(origin.result))
            return (self._request_current() and record is not None
                    and record[0] is origin.result and record[1] is completion
                    and record[2] is origin.delivery_owner
                    and retained is not None and retained[0] is origin.result and retained[1] is completion)

    def delivered_result(self, owner, operation, result):
        """Called only after an actual successful RPC write and its ownership checks."""
        with self._lock:
            record = self.records.get(id(result))
            if (not self._request_current() or type(owner) is not DeliveryOwner
                    or owner.parent is not self.context.delivery_owner or owner.fence != self.context.request_fence
                    or not owner.current(self.context.host) or record is None or record[0] is not result):
                return
            completion = record[1]
            if completion.origin.delivery_owner is owner and completion.verified(operation, result):
                self.delivered[id(result)] = (owner, operation, result, completion)

    def seal_delivered(self, owner, calls):
        """At successful native end, retain only already-written private evidence."""
        with self._lock:
            if (not self._request_current() or type(owner) is not DeliveryOwner
                    or owner.parent is not self.context.delivery_owner or owner.fence != self.context.request_fence
                    or not owner.current(self.context.host)):
                return
            for operation, result in calls:
                delivery = self.delivered.get(id(result))
                if (delivery is None or delivery[0] is not owner or delivery[1] is not operation
                        or delivery[2] is not result):
                    continue
                completion = delivery[3]
                record = self.records.get(id(result))
                if (record is not None and record[0] is result and record[1] is completion
                        and completion.origin.delivery_owner is owner and completion.verified(operation, result)):
                    self.sealed[id(result)] = (result, completion, owner)
                    completion.origin.sealed_scope = self

    def clear(self):
        """Permanently end this request's proof scope, including late deliveries."""
        with self._lock:
            self.closed = True
            for _, completion in self.records.values():
                completion.origin.revoke()
            self.records.clear()
            self.delivered.clear()
            self.sealed.clear()

    def emit(self, kind, data):
        """Linearize host-summary delivery with request end, without a host lock."""
        if kind == "message" and data.get("host_summary") is True:
            with self._lock:
                if not self.closed and not self.context.cancelled:
                    self.context.host.emit(kind, data)
            return
        self.context.host.emit(kind, data)

    def request(self, call_id, name, arguments, callback, *, delivery_owner=None, result_observer=None,
                script_file=None):
        captured = []

        def receive(result):
            if captured:
                self.record(result, captured.pop())
            callback(result)
        # Never hold the scope lock while entering the controller: an immediate
        # host rejection may call back while the controller holds its own lock.
        with self._lock:
            closed = self.closed
        if closed:
            callback({"error": "This native runtime no longer owns the active request"})
            return
        # script_file: an app's angelo_run_script_file read (trusted_scripts); passed only when there is one.
        extra = {"script_file": script_file} if script_file is not None else {}
        self.context.host.request_tool(call_id, name, arguments, receive,
            request_fence=self.context.request_fence, completion_sink=captured.append,
            delivery_owner=delivery_owner, result_observer=result_observer, **extra)


def delivered_result(context, owner, operation, result):
    scope = getattr(context, "program_completions", None)
    if type(scope) is CompletionScope:
        scope.delivered_result(owner, operation, result)


def seal_delivered(context, owner, calls):
    scope = getattr(context, "program_completions", None)
    if type(scope) is CompletionScope:
        scope.seal_delivered(owner, calls)


def completions_for(context, results):
    scope = getattr(context, "program_completions", None)
    return [scope.get(result) if isinstance(scope, CompletionScope) else None for result in results]



class CompletionWaiter:
    """Keep a duplicate caller's private sink beside its unchanged public callback."""

    def __init__(self, callback, sink):
        self.callback, self.sink = callback, sink

    def __call__(self, result):
        self.callback(result)


def deliver_completion(host, call, result, completion):
    if call.local and completion is not None:
        from .claude_outcomes import certified_completion
        summary = certified_completion([{"tool": call.name, "arguments": call.arguments}], [result],
                                       read_only=lambda _name: False, completions=[completion])
        if summary:
            host.emit("message", {"role": "system", "text": summary, "host_summary": True})
    for callback in call.callbacks:
        if isinstance(callback, CompletionWaiter) and completion is not None and callable(callback.sink):
            try:
                callback.sink(completion)
            except Exception:
                pass  # Optional private bookkeeping cannot suppress the actual public result.
        try:
            callback(result)
        except Exception:
            # A dead transport cannot block other waiters or undo a durable result.
            pass
    if call.local and completion is not None:
        completion.origin.revoke()


def start_native_job(call, executor):
    """Forward private approval only to adapters whose signature admits it."""
    name, arguments = call.name, call.arguments
    if name == "angelo_apply_recipe":
        if call.program_arguments is None:
            raise RuntimeError("The recipe program was never expanded for review; nothing was staged")
        name, arguments = "angelo_scene_program", call.program_arguments
    elif name == "angelo_scene_program" and call.program_arguments is not None:
        arguments = call.program_arguments
    starter = getattr(executor, "start_native_job", None)
    if not callable(starter):
        return executor.execute(name, arguments)
    parameters = inspect.signature(starter).parameters
    keywords = any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values())
    optional = {"invocation_id": call.id, "approved_obligations": getattr(call.precondition, "obligations", None)}
    # Never catch TypeError and retry: native preparation may already have run.
    return starter(name, arguments, **{key: value for key, value in optional.items()
                                      if keywords or key in parameters and parameters[key].kind != inspect.Parameter.POSITIONAL_ONLY})
