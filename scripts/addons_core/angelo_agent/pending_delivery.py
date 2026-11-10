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
    script_file: object = None  # script_files.ScriptFile an app's angelo_run_script_file named; host-only.
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
    chat_id: str = ""  # The chat that asked for it: what its card reads is that chat's own (worker_approval).
    merge_waits: bool = False  # An approved merge stopped at its run behind its chat's own bound edit (merge_hold).
    binds_after_merge: bool = False  # A′: an edit not bound yet, waiting for its chat's earlier merge (merge_hold).
    # The model step that wrote it, when its runtime names one (model_steps.record_call_step: a yielded
    # Codex exec cell's later call keeps its cell's step); None: the step it is checked in. Host-only.
    model_step: object = None
    shown_status: str = ""  # The live row's status this call set for its own work; it ends with the call (call_status).


def request_tool(self, call_id, name, arguments, callback, *, enqueue=True, request_fence=None,
                 delivery_owner=None, parent_call=None, result_observer=None, completion_sink=None, script_file=None,
                 model_step=None):
    """Admit synchronously; deliver immediate answers after our admission locks have exited.

    ``model_step``: the step that wrote the call (``ToolCall.model_step``), None for the step it is checked in."""
    from .program_completion import CompletionWaiter

    callback = CompletionWaiter(callback, completion_sink)
    answers = []
    try:
        return self._admit_tool_request(call_id, name, arguments, callback, answer=answers.append,
            enqueue=enqueue, request_fence=request_fence, delivery_owner=delivery_owner,
            parent_call=parent_call, result_observer=result_observer, script_file=script_file,
            model_step=model_step)
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


def retire_native_cleanup(self, budget):
    """One deferred native cleanup (a stage stopped off the main thread, e.g. by a voice session's end:
    ``Controller._discard_native_job``) on the UI thread, before any other native work of this chat and before
    another writer can enter. Retiring it is not work on the chat's scene: it runs whatever scene is shown, so the
    writer lease the stopped stage keeps never waits for that scene (break-it R5-M-1). ``ProgramJob.cancel`` keeps
    the lease for a native preparation owner until it closes. True when it spent the tick's quantum."""
    if not self._native_cleanup or budget.exhausted:
        return False
    job = self._native_cleanup.pop(0)
    budget.admissions += 1
    budget.executions += 1
    try:
        job.cancel()
    except Exception:
        self._native_cleanup.append(job)
    return True


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
        if not (call.committing or call.preparation_effects_possible):   # Unstarted or only staged: nothing of it ran.
            result.update(error_code="CANCELLED", scene_changed=False)
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


def request_context_tool(self, call_id, name, arguments, callback, *, delivery_owner=None, script_file=None,
                         model_step=None):
    """Route one request's owned tool call without moving billing or request state; ``model_step``: the step that
    wrote it (``record_call_step``), None for the step it is checked in."""
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
                                     delivery_owner=owner, result_observer=observer,
                                     script_file=script_file if name == "angelo_execute_python" else None,
                                     model_step=model_step)


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


def expire_media_job(controller, call):
    """A deferred result (``studio_agent.StudioPending``) whose call expired: True when it was finished here with its
    own result (ready at the last moment) or, when the job states one, the result that stands (``stopped_result``,
    an engine link's save: the save happened, only the wait ended), never a bare expiry the agent would read as
    "nothing happened" (break-it F6). A revoked call, or a job with neither, is cancelled and left to the generic
    expiry (False). (A turn's end never revokes such a call: it stays ``executing`` while its result is awaited.)"""
    job = controller.media_jobs.pop(call.id)
    result = None
    if not call.revoked:
        try:
            result = job.poll()
        except Exception:   # A failing last poll is no result; the job's own words, if any, still stand.
            result = None
    job.cancel()
    stood = None if call.revoked else getattr(job, "stopped_result", None)
    if result is None and not stood:
        return False
    controller.finish_tool(call, result if result is not None else dict(stood, expired=True))
    return True
