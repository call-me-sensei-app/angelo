# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One admitted Codex dynamic call: bounded response ownership and delivery phases.

The provider owns native sessions and event routing. This module owns only
its tool callback: no new capture/packing/response phase after owner closure,
no duplicate outstanding RPC response, and no lock across packing or writing.
A call that arrives after its owner closed is never admitted, but it is
answered with a refusal (``refuse_stale_call``): codex app-server waits for
every ``item/tool/call`` answer. Its turn is then interrupted, as is the turn
of an admitted call whose reply its closed owner withholds
(``close_orphaned_turn``), so no orphaned turn stays open (``codex_turns``).
"""

import threading

from .protocol import RpcError
from . import program_completion
from .query_observations import prepare_observation
from .tool_specs import CODEX_DEFERRED_NAMESPACE


STALE_CALL = "Stale tool call: that turn has ended. Nothing was admitted or run."
FOREIGN_CALL = "This tool request does not belong to the active native thread and turn; no work was admitted."


def close_orphaned_turn(provider, rpc, params):
    """Interrupt the call's turn when Codex still has it open and no live request owns it.

    The current turn of a current owner is never interrupted, even when a
    reused native ID makes a stale call name it. Takes no provider lock (a
    closed owner's cleanup never waits on one) and never blocks (``codex_turns``).
    """
    scope, thread_id, turn_id = provider._scope, params.get("threadId"), params.get("turnId")
    owner = scope.delivery_owner
    if scope.turn_id == turn_id and owner is not None and owner.current():
        return False
    return scope.turns.interrupt(rpc, thread_id, turn_id)


def _refuse(provider, request_id, rpc, params, text):
    if rpc is None or provider._scope.owns_response(request_id):
        return
    try:
        rpc.respond(request_id, {"success": False, "contentItems": [{"type": "inputText", "text": text}]})
    except (OSError, RpcError):
        return  # The transport is gone; nothing waits on it any more.
    close_orphaned_turn(provider, rpc, params)


def refuse_stale_call(provider, request_id, rpc, params):
    """Answer a call whose delivery owner closed before admission, then end its turn.

    Unanswered, codex app-server keeps that turn open (a code-mode cell yields
    after about 30 s) and steers the next ``turn/start`` on the thread into it
    (agent_execution.rst, pending delivery). Nothing is admitted, packed,
    recorded or counted. The refusal goes only to the transport the call came
    on, never to an RPC ID that a live call currently owns.
    """
    _refuse(provider, request_id, rpc, params, STALE_CALL)


def refuse_foreign_call(provider, request_id, rpc, params):
    """A call for another thread or turn than the request's own: refused, its turn ended if orphaned."""
    provider._scope.turns.observe("item/tool/call", params)
    _refuse(provider, request_id, rpc, params, FOREIGN_CALL)


def dispatch_dynamic_call(provider, request_id, params, context, rpc, owner, token, *, pack, record_result):
    tool, arguments = params.get("tool", ""), params.get("arguments", {})
    if params.get("namespace") not in (None, CODEX_DEFERRED_NAMESPACE):
        rpc.respond(request_id, {"success": False, "contentItems": [{"type": "inputText", "text":
            "Unknown tool namespace; no work was admitted."}]})
        return
    provider._scope.turns.observe("item/tool/call", params)  # Codex holds this turn open for the reply.
    # The model made this call, proving it received the turn's input.
    # Parallel tool calls alone do not prove it received earlier results.
    try:
        with provider._scope.lock:
            stale = owner is not provider._scope.delivery_owner or owner is None or not owner.current()
            if not stale:
                ticket = provider._scope.claim_response(request_id, str(params.get("callId", str(request_id))))
                if ticket is None:
                    return  # The first still-outstanding envelope alone owns this RPC response.
                provider._accepted_input(context)
    except OverflowError as error:
        if owner.current():
            rpc.respond(request_id, {"success": False, "contentItems": [{"type": "inputText", "text": str(error)}]})
        return
    if stale:
        refuse_stale_call(provider, request_id, rpc, params)  # Outside the scope lock: a pipe write.
        return
    resolve = getattr(context, "resolve_call", None)
    try:
        # The summary describes the call as the host ran it (identifiers.expand_call).
        resolved = resolve(tool, arguments) if callable(resolve) and isinstance(arguments, dict) else arguments
    except Exception:  # The host's own validation reports it.
        resolved = None

    def delivery_current():
        # No provider lock crosses image packing or a synchronous pipe write.
        # Each check admits one phase; an already-started write is not revoked.
        return (owner is not None and owner.current(getattr(context, "host", None))
                and not context.cancelled and provider.controller is context and provider.rpc is rpc
                and provider._transport_token is token and provider._scope.delivery_owner is owner)

    reply_lock, replied = threading.Lock(), False

    def respond(result):
        nonlocal replied
        # One arrival owns one response, even if a host callback is
        # accidentally repeated. A later RPC request may reuse its ID.
        with reply_lock:
            if replied:
                return
            replied = True
        try:
            if not delivery_current():
                # The reply is withheld, never replaced by a refusal: the durable
                # host result records whether the call ran. Codex cancels it on interrupt.
                close_orphaned_turn(provider, rpc, params)
                return
            table = getattr(getattr(context, "host", None), "_identifiers", None)
            observe_query = None
            try:
                content = pack(context, params.get('tool', ''), result)
                payload = {"success": not bool(result.get('error')), "contentItems":content}
                if payload["success"]:
                    observe_query = prepare_observation(context, tool, result, table, owner)
            except (ValueError, OSError) as error:
                payload = {"success":False, "contentItems":[{"type":"inputText", "text":str(error)}]}
            if not delivery_current():
                close_orphaned_turn(provider, rpc, params)
                return
            if not provider._scope.begin_response(ticket):
                return
            rpc.respond(request_id, payload)
            provider._scope.retire_response(ticket)  # An answered ID is reusable before unrelated accounting.
            if not delivery_current():
                return
            with provider._scope.lock:
                if not delivery_current():
                    return
                if observe_query is not None:
                    observe_query()
                if (resolved is not None and isinstance(result, dict)
                        and not set(result) <= {"error", "exact_schemas"}):
                    operation = {"tool": tool, "arguments": resolved}
                    provider._turn_calls.append((operation, result))
                    if payload["success"]:
                        program_completion.delivered_result(context, owner, operation, result)
            record_result(context, result, payload["contentItems"],
                                             scope=provider._scope, params=params, delivery_owner=owner)
        except (OSError, RpcError):
            pass
        finally:
            provider._scope.retire_response(ticket)
    try:
        context.request_tool(params.get("callId", str(request_id)), params.get("tool", ""),
                             params.get("arguments", {}), respond, delivery_owner=owner)
    except Exception:
        provider._scope.retire_response(ticket)
        raise
