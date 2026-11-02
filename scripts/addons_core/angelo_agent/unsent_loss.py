# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A request whose agent runtime ended before it sent anything: started once more on a fresh runtime.

A runtime can end between Angelo choosing it and Angelo's first write to it: a prewarmed or shared process that
exited, a worker's relay that closed, a child that died as it started. Under heavy load that window is long enough to
hit (a worker chat's first message failed with "Agent process has disconnected", 1.0.8). The request had sent
nothing then, so a person would only have been told to send it again.

``Controller._run`` starts such a request once more, on a fresh runtime, when all hold:

* the failure is ``protocol.TransportClosed``: a write that found the runtime's connection already ended, so nothing
  of it was written, or a connect whose runtime ended under it (``codex_runtime.transport``; a connect never sends
  the person's message);
* no model round of the request was written yet (``written``: Codex's ``turn/start`` once it may have gone, Claude's
  user message once written): nothing the model could have acted on, so nothing can run twice;
* it is the request's first such loss, and the request was not stopped or replaced.

A second loss ends the request with ``RuntimeLost``: the reason said in the chat, and the message offered back as
unsent (``message_not_sent``), since nothing was sent either time. Every retry is logged. Standard library only.
"""

import sys

from .protocol import RpcError, TransportClosed
from .ui_text import n_, notice

RETRIES = 1
STOPPED = object()   # ``attempts``: the request was stopped before it ran (its ``finished`` already said so).
LOST_TWICE = n_("Angelo's agent runtime stopped twice before your message reached it, so nothing was sent. Send your "
                "message again. If this keeps happening, restart Angelo.")


class RuntimeLost(RpcError):
    """The request's runtime ended before anything was sent, on its retry too (module docstring)."""

    message_not_sent = True   # ``Controller._run``: the message comes back unsent.
    show_reason = True        # And the chat says why, unlike a plain unsent message.

    def __init__(self, cause):
        message = notice(LOST_TWICE)
        super().__init__(message["text"])
        self.i18n, self.cause = message.get("i18n"), str(cause)


def written(context):
    """A model round of ``context``'s request may have reached the runtime: it is never started again."""
    try:
        context.model_round_written = True
    except AttributeError:   # A context that takes no attributes keeps no request to start again.
        pass


def retry(context, error, losses):
    """Whether ``Controller._run`` starts ``context``'s request once more after ``error`` (``losses`` before it);
    raises ``RuntimeLost`` for a loss past the one retry."""
    if not isinstance(error, TransportClosed) or getattr(context, "model_round_written", False):
        return False
    if getattr(context, "cancelled", False):
        return False
    if losses >= RETRIES:
        print("Angelo: a request's agent runtime ended again before anything was sent (%s); it ends unsent"
              % error, file=sys.stderr)
        raise RuntimeLost(error) from error
    print("Angelo: a request's agent runtime ended before anything was sent (%s); starting it once more on a fresh "
          "runtime" % error, file=sys.stderr)
    return True


def attempts(controller, context, text, composing):
    """``Controller._run``'s connect and run of ``context``'s request, started once more on a fresh runtime after a
    loss ``retry`` allows; its result, or ``STOPPED``. ``composing``: ``(raw, composed, observation, workspace_root,
    seed)``, from which a lost native context's message is composed again (``codex_connect.ContextLost``)."""
    message = [text, text[len(composing[1]):]]   # What a retry sends, and what follows the blocks in a new context's.
    losses = 0
    while True:
        try:
            return _attempt(controller, context, message, composing)
        except TransportClosed as error:
            if not retry(context, error, losses):
                raise
            losses += 1
            drop(controller)
            from .codex_attempts import receipt_count
            receipt_count(context, "runtime_lost_retries")   # Counted, never hidden (a benchmark's trial says so).


def _attempt(controller, context, message, composing):
    from .codex_connect import ContextLost
    raw, _composed, observation, workspace_root, seed = composing
    try:
        native_id = controller._connect_runtime(context)
    except ContextLost:  # A new native context, with every block and the chat's handoff.
        context.state["native_id"] = None
        message[0] = controller._context_blocks(context, raw, observation, workspace_root, seed) + message[1]
        native_id = controller._connect_runtime(context)
    text = message[0]
    provider = controller.provider
    if context.cancelled:
        context.emit("finished", {"status": "interrupted"})
        return STOPPED
    context.emit("status", {"text": "Thinking"})
    run_request = getattr(provider, "run_request", None)
    return (run_request(context, native_id, text, observation=observation) if callable(run_request)
            else provider.run(native_id, text, observation=observation))


def drop(controller):
    """Before the retry: the provider whose runtime ended closes and is let go, so the next connect starts a new one
    (a shared app-server that ended leaves its pool: ``codex_runtime.runtime_for``)."""
    provider = controller.provider
    if provider is not None:
        provider.close()
        provider.dropped_for_retry = True   # ``Controller._run``: a failure after this closes the retry's provider.
        if controller.provider is provider:
            controller.provider = None
