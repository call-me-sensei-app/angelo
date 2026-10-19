# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex request's turn attempts: one ``turn/start`` and its wait, and the one resend ``codex_retry`` allows.

``providers.CodexProvider._run_turn`` builds a turn's input and calls ``begin`` (a fresh turn record,
before the input is built, as before) and ``run``. ``send`` starts the turn on the request's thread
(``codex_turns.OpenTurns.start``: never into an older open turn), replays what arrived before Codex's
reply, waits for ``turn/completed`` (``turn_activity``: host tool calls are not model time) and returns
its status, or raises the turn's error. A turn a transient provider failure ended before anything
happened is the exception: ``send`` reports it, and ``run`` waits (counting down on the live row, Stop
ends the wait and sends nothing more) and sends it once more (``codex_retry`` has the policy and why it
is safe). An app-server lost during the wait sends nothing more: the request fails with the provider's
own error (the "Too many requests" card, the harness's limit evidence), never "Agent process has
disconnected". A Stop between the wait's last poll and the resend's ``turn/start`` ends the request as
stopped: Codex already holds the message (never "Your message wasn't sent"). Every function takes the
provider: its scope lock, fences and transport are the provider's.
"""

from . import codex_retry, turn_activity
from .codex_turns import TurnStartAbandoned
from .protocol import RpcError
from .provider_usage import record_interrupted, record_runtime_round


def begin(provider, context, native_id, turns):
    """A fresh turn record for one ``turn/start``, or None when ``context`` no longer owns the provider."""
    with provider._scope.lock:
        if provider.controller is not context:
            return None
        rpc = provider.rpc
        provider.done.clear()
        provider.last_error = None
        provider.turn_status = None
        provider.turn_id = None
        provider.thread_id = native_id
        provider._scope.begin(context, native_id, closed_with=getattr(rpc, "closed", None))
        start_owner, start_transport = provider._scope.delivery_owner, provider._transport_token
        provider._usage.begin_turn(native_id)
        provider._turn_calls, provider._turn_reply, provider._plan = [], None, turns
        provider._input_accepted = False
        codex_retry.reset(provider)
        provider._activity = turn_activity.TurnActivity()
        if turns is not None:
            turns.begin_turn()
        return rpc, start_owner, start_transport, provider._scope.fence


def run(provider, context, native_id, turns, params, attempt):
    """``(status, fence)`` of the request's turn, after at most one resend (``codex_retry``)."""
    status, fence, retry = send(provider, context, params, attempt)
    if retry is None:
        return status, fence
    seconds, recorded, kind, failure = retry
    rpc = attempt[0]
    receipt_count(context, codex_retry.COUNTS[kind])
    emit = lambda status: context.emit("status", {"text": status})  # noqa: E731
    if not codex_retry.wait(seconds, lambda: context.cancelled or provider.controller is not context, emit):
        return "interrupted", fence  # Stopped or replaced while waiting: nothing more is sent.
    if _lost(provider, rpc):
        raise RpcError(failure)  # The app-server ended during the wait: the request met the provider's error.
    attempt = begin(provider, context, native_id, turns)
    if attempt is None:
        return "interrupted", fence
    emit("Thinking")
    if recorded:  # Codex holds the message in the thread already: the model must not see it twice.
        params = dict(params, input=[{"type": "text", "text": codex_retry.NOTE}])
    try:
        status, fence, _ = send(provider, context, params, attempt)
    except TurnStartAbandoned:
        return "interrupted", fence  # Stop just before the resend: a stopped request, its message delivered once.
    except RpcError as error:
        if not getattr(error, "message_not_sent", False) and _lost(provider, rpc):
            raise RpcError(failure) from None  # Lost while sending the resend: the provider's error stands.
        raise
    return status, fence


def send(provider, context, params, attempt):
    """``(status, fence, retry)``: one ``turn/start`` and its wait. ``retry`` is ``(seconds, recorded, kind,
    failure)`` when the turn failed transiently before anything happened, else None; any other failure raises."""
    rpc, start_owner, start_transport, fence = attempt
    response = provider._scope.turns.start(provider, rpc, params, context)
    record_runtime_round(context)
    with provider._scope.lock:
        if (provider.controller is not context or provider._scope.fence != fence or provider.rpc is not rpc
                or provider._scope.delivery_owner is not start_owner or provider._transport_token is not start_transport):
            return "interrupted", fence, None
        provider.turn_id = response["turn"]["id"]
        if not isinstance(provider.turn_id, str) or not 1 <= len(provider.turn_id) <= 512:
            raise RpcError("Codex did not return a valid native turn identity; no host work was admitted")
        pending = provider._scope.bind_turn(provider.turn_id)
        replay_owner, replay_transport = provider._scope.delivery_owner, provider._transport_token
    for kind, identity, method, values in pending:
        if kind == "request":
            provider._request(identity, method, values, transport=replay_transport, expected_owner=replay_owner)
        else:
            provider._event(method, values, transport=replay_transport, expected_owner=replay_owner)
    # Stop may race with turn/start before its ID has reached the host.
    if context.cancelled:
        provider.stop_request(fence)
    # Host tool calls arrive inside a Codex turn: approving and running them is not model time.
    overdue = turn_activity.wait(provider.done, provider._activity, busy=getattr(context, "tools_pending", None))
    if overdue:
        provider.stop_request(fence)
        record_interrupted(context, provider._usage.finish_turn(None))
        raise RpcError(turn_activity.problem("Agent", overdue, "Inspect the scene before retrying"))
    if provider.turn_status == "failed":
        retry = _resend(provider, context, rpc)
        if retry is not None:
            return "failed", fence, retry
        raise RpcError(provider.last_error or "Codex turn failed")
    return provider.turn_status or "interrupted", fence, None


def _resend(provider, context, rpc):
    """``(seconds, recorded, kind, failure)`` for the one resend of a failed turn that did nothing, or None."""
    with provider._scope.lock:
        if (provider._retries_left <= 0 or getattr(context, "voice", None) or context.cancelled
                or provider.controller is not context or provider.rpc is not rpc or rpc is None
                or getattr(rpc, "closed", None) is not None and rpc.closed.is_set()
                or provider._input_accepted or provider._turn_output or provider._turn_calls
                or provider._codex_retried):
            return None
        seconds = codex_retry.delay(provider._turn_error)
        if seconds is None:
            return None
        provider._retries_left -= 1
        failure = provider.last_error or "Codex turn failed"  # What the request fails with if the resend cannot go.
        return seconds, provider._turn_user_recorded, codex_retry.kind(provider._turn_error), failure


def _lost(provider, rpc):
    """Whether the transport the failed turn ran on is gone (closed, or replaced on the provider)."""
    closed = getattr(rpc, "closed", None)
    return rpc is None or provider.rpc is not rpc or closed is not None and closed.is_set()


def receipt_count(context, name):
    """A receipt-only count of this request (``telemetry.RECEIPT_ONLY_COUNTS``), under its fence."""
    host, fence = getattr(context, "host", None), getattr(context, "request_fence", None)
    metrics = getattr(host, "_request_metrics", None)
    if metrics is not None and fence is not None and fence.matches(host) and not context.cancelled:
        metrics.count(name)
