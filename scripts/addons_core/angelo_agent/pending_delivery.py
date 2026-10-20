# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Transient tool calls and their pending delivery lifetimes.

Controller owns admission, execution and durable results. These helpers retain
its existing method interfaces while separating lifetime cleanup and result
observation from provider delivery callbacks. RuntimeContext retains billing and
request state. Dependencies back to those owners are resolved only when called.
"""

import time
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    document: object
    callbacks: list = field(default_factory=list)
    expires: float = field(default_factory=lambda: time.monotonic() + 180)
    precondition: object = None
    approval: bool = False
    automatic: bool = False
    recovery_review: bool = False
    recovery_evidence: object = None  # Host-only facts for the exact renewed approval.
    executing: bool = False
    queued_at: float = field(default_factory=time.monotonic)
    dispatch_wait_ms: float = 0
    usage_label: str = ""
    epoch: int = 0
    generation: int = 0
    invocation_digest: str = ""
    submitted_arguments_digests: tuple = ()
    metrics: object = None
    authorized: bool = False
    staging: bool = False
    commit_requested: bool = False
    mutation_started: bool = False
    native_preparation: object = None
    finished_preparation: object = None
    preparation_effects_possible: bool = False
    commit_result: object = None  # Private until the same-dispatch native Undo outcome.
    committing: bool = False
    local: bool = False
    approved: bool = False
    approval_started: object = None
    script_consent: object = None  # Host-only admission token; never serialized.
    consent_kind: str = ""  # The standing consent that approved an automatic script (its result's ``consent``).
    reviewed_script: str = ""  # Exact reviewed invocation digest.
    revoked: str = ""  # Why the turn that admitted this call ended before it ran (tool_deadlines).
    delivery_owner: object = None  # Exact request/native-turn lifetime; never serialized or reused for approval.
    parent_call: object = None  # A delegated child owns this exact admission, not an invocation-key prefix.
    result_observer: object = None  # Host outcome accounting once per admission, independently of delivery waiters.
    # angelo_apply_recipe: the host-expanded canonical program, fixed at its
    # first precondition. Approval shows it and only it is ever staged.
    program_arguments: object = None
    # program_camera.staged_program: an orbit was measured from a camera that moves with time, so the
    # resolved program holds only at the approved frame (controller._frame_rebind refuses to move it).
    orbit_moves_with_time: bool = False
    # program_context recoveries: a frame-only rebind, staging restarts and a
    # wait while animation playback runs. Bounded per call.
    context_rebinds: int = 0
    staging_restarts: int = 0
    waiting_for_playback: bool = False


def request_tool(self, call_id, name, arguments, callback, *, enqueue=True, request_fence=None,
                 delivery_owner=None, parent_call=None, result_observer=None, completion_sink=None):
    """Admit synchronously; deliver immediate answers after our admission locks have exited."""
    from .program_completion import CompletionWaiter

    callback = CompletionWaiter(callback, completion_sink)
    answers = []
    try:
        return self._admit_tool_request(call_id, name, arguments, callback, answer=answers.append,
            enqueue=enqueue, request_fence=request_fence, delivery_owner=delivery_owner,
            parent_call=parent_call, result_observer=result_observer)
    finally:
        for result in answers:
            self._observe_tool_result(result_observer, result)
            callback(result)


def sweep_delegates(self):
    """Settle exact closed helper admissions before their pending child readers poll."""
    for identity, job in list(self.delegate_jobs.items()):
        if self.delegate_jobs.get(identity) is not job:
            continue
        call = self.pending.get(identity)
        if call is not job.call:
            self.delegate_jobs.pop(identity, None)
            job.cancel("The original consultation no longer owns this request; local monitoring stopped")
            continue
        self._end_closed_delivery(call)


def end_closed_delivery(self, call):
    """Release a closed reader/monitor on the main thread before its next phase.

    Already-live atomic work retains its actual outcome. A pending monitor
    uses its own stopped-result contract; ending delivery does not claim
    that its submitted work never ran or cancel another request's jobs.
    """
    owner = call.delivery_owner
    parent_current = call.parent_call is None or self.pending.get(call.parent_call.id) is call.parent_call
    if parent_current and (owner is None or owner.current(self)):
        return False
    if (call.committing and (call.native_preparation is None or call.mutation_started)
            or call.executing and call.mutation_started):
        return False
    delegate = self.delegate_jobs.get(call.id)
    if delegate is not None and delegate.call is call:
        self.delegate_jobs.pop(call.id, None)
        result = {"error": "The requesting delivery ended; the read-only consultation stopped locally. "
                           "Submitted provider work may still finish or incur usage.", "read_only": True}
        try:
            delegate.cancel(result["error"])
        except Exception as error:
            result["cleanup_error"] = str(error)[:300]
        self.finish_tool(call, result)
        return True
    job = self.media_jobs.pop(call.id, None)
    if job is not None:
        try:
            job.cancel()
            result = getattr(job, "stopped_result", None) or {
                "error": "The tool delivery ended; monitoring stopped. Submitted work may still finish; inspect its status before retrying.",
                "asset_id": job.asset_id}
        except Exception as error:
            result = {"error": "The tool delivery ended, but monitoring cleanup failed; inspect the task before retrying.",
                      "cleanup_error": str(error)[:300]}
    else:
        result = {"error": "The tool delivery ended before execution; no operation was applied"}
    self.finish_tool(call, result)
    return True


def observe_tool_result(observer, result):
    if observer is not None:
        try:
            observer(result)
        except Exception:
            # Optional receipt accounting cannot strand result delivery or
            # change the durable outcome of an already-completed operation.
            pass


def request_context_tool(self, call_id, name, arguments, callback, *, delivery_owner=None):
    """Route one request's owned tool call without moving billing or request state."""
    from . import routing

    owner = delivery_owner if delivery_owner is not None else self.delivery_owner
    if self.cancelled or not self.delivery_owner.current(self.host) or not owner.current(self.host):
        callback({"error": "This native runtime no longer owns the active request"})
        return
    allowed = getattr(self, "allowed_tools", None)
    if allowed is not None and name not in allowed:   # Every route offers only these; a call outside them never runs.
        callback({"error": f"{name} is not available in this request; this call did not run", "error_code": "REFUSED"})
        return
    observer = routing.request_path.result_observer(self, name, arguments, delivery_owner=owner)
    if name == "angelo_execute_python":
        deliver, preparing = callback, True

        def script_result(result):
            if preparing:
                observer(result)  # The script budget can refuse synchronously, before host admission.
            deliver(result)

        callback = self.scripts.admit(self.emit, script_result)
        preparing = False
        if callback is None:
            return  # Answered: the request's script corrections are used.
    scope = routing.route_key(self.connection_id, self.state["provider"]) + "/" + str(self.state.get("native_id") or "connecting")
    self.program_completions.request(scope + "/" + str(call_id), name, arguments, callback,
                                     delivery_owner=owner, result_observer=observer)


def result_observer(context, name, arguments, *, delivery_owner=None):
    """Accounting callback owned by one admitted ToolCall, not by its delivery waiters."""
    from . import request_path

    def observe(result):
        path = request_path.facts(context)
        current = delivery_owner is None or delivery_owner.current(context.host)
        if path is not None and (current or request_path.scene_edit(name)):
            # Live mutation outcomes remain true even when their runtime ended.
            # Closed readers and attached images confer no new delivery facts.
            path.tool(name, arguments, result, include_images=current)
    return observe
