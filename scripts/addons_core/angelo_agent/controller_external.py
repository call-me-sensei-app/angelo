# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-originated requests in an External conversation, mixed into ``Controller``.

A CLI client's reads (``open_external_read``, doc/angelo/external_cli.rst) start no provider. A connected MCP app's
messages to Angelo's agent (``external_send``, doc/angelo/external_mcp.rst) are ordinary turns.
"""

import contextlib

from . import display_text, ui_text
from .request_fences import RequestFence


def bound_target(controller, executor, document):
    """(executor, document) a chat works on: its worker Blender's when it is bound to one (``worker_routing``),
    whoever called (an operator, the UI pump, a document load); else the caller's, Angelo's own window, kept to the
    worker's part for a chat its worker hosts (``worker_chat_executor``, the lane path's refusals)."""
    if getattr(controller, "chat_host", None) is not None:
        from .worker_chat_executor import confined
        return confined(executor, controller), document
    remote = getattr(controller, "remote_executor", None)
    if remote is None:
        from .worker_routing import ended_worker
        remote = ended_worker(controller)   # A worker chat whose worker is gone (a restart): never this window's.
        if remote is None:
            return executor, document
    remote.studio_context = controller
    return remote, remote.document_key


@contextlib.contextmanager
def external_send(controller):
    """A connected app's message being sent in its External conversation (``external_agent``; a queued one when the
    turn ends, ``chat_pending.dispatch_due``): the one way a model turn starts there
    (``require_model_chat``). Its request row says whose it is and takes none of the person's files
    (``request_rows``)."""
    previous = getattr(controller, "_external_task", False)
    controller._external_task = True
    try:
        yield
    finally:
        controller._external_task = previous


class ExternalRequests:
    def open_external_read(self):
        """Open a host-originated read-only request for the local CLI.

        No provider, snapshot or checkpoint: read-only calls have no effects.
        Concurrent reads share the returned fence; ``close_external_read``
        ends the request once no admitted call remains pending.
        """
        if self.busy or (self._worker and self._worker.is_alive()) or self._closing or self.document_invalid:
            raise RuntimeError("This conversation is not idle")
        self._request_generation += 1
        self.recovery_barrier.clear()
        self._initial_recovery_evidence = {}
        self._request_message_index = None
        self._request_metrics = None
        self.cancelled, self.busy = False, True
        self.status = "External read"
        self.version += 1
        return RequestFence.capture(self)

    def close_external_read(self, fence):
        """End only the request this fence opened; a newer request is untouched."""
        if not isinstance(fence, RequestFence) or fence.generation != self._request_generation or not self.busy:
            return False
        self.busy = False
        self.status = "Interrupted" if self.cancelled else "Idle"
        # How it ended, as a turn's "finished" records it (session_rail.turn_end): every request's end, this one too.
        self.turn_outcome = ("interrupted" if self.cancelled else "completed", bool(self.cancelled), False,
                             len(self.state["messages"]))
        self.version += 1
        return True

    def require_model_turn(self, host_request):
        """``Controller.send``'s check: ``require_model_chat``, except for the next round of a goal this External
        conversation holds from its app's words (``goal_controls.admits_round``), Angelo's own request there."""
        if host_request is not None and self.external_client:
            from .external_access import revoked
            from .goal_controls import admits_round
            if revoked(self):   # The app's access ended (external_access): no round of its goal starts.
                raise RuntimeError("This app's access to Angelo ended; its goal does not continue")
            if admits_round(self, host_request):
                return
        self.require_model_chat()

    def require_model_chat(self):
        """Raise for an External conversation: only its app's message (``external_send``) starts a model turn there
        (and a goal round it holds, ``external_goal_round``)."""
        if self.external_client and not getattr(self, "_external_task", False):
            raise RuntimeError(display_text.Text(ui_text.n_("External client chat: approvals and Stop only. "
                                                            "Switch to another chat to talk to a model.")))
        from .external_access import revoked
        if self.external_client and revoked(self):   # Its app's access ended: no message of it starts a turn.
            raise RuntimeError("This app's access to Angelo ended; its message was not sent")
