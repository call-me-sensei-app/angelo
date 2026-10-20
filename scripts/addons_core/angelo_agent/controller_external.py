# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-originated requests in an External conversation, mixed into ``Controller``.

A CLI client's reads (``open_external_read``, doc/angelo/external_cli.rst), a
connected MCP app's tool calls (``open_external_work``) and its tasks
(``send_external_task``), doc/angelo/external_mcp.rst. None starts a provider
except a task, which is an ordinary turn.
"""

from . import display_text, ui_text
from .checkpoint_policy import CheckpointPolicyStore
from .request_fences import RequestFence


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
        self.version += 1
        return True

    def open_external_work(self, text, client):
        """Open a host-originated request for a connected MCP app's tool calls (external_work).

        Like ``open_external_read`` it starts no provider, but its calls may edit: a user row says who is working,
        so the snapshot taken before the first approved edit (``ensure_request_checkpoint``; the pump calls it)
        binds to it, as a chat request's does (``text``: the row, request data like a typed message, as the phase-1
        "External: <label>" row is). An app's request never captures before a read-only call, whatever the snapshot
        policy, so an app that only looks costs nothing.
        Approval, Undo, journaling and receipts are ``request_tool``'s. Returns the request's fence;
        ``close_external_read`` ends it.
        """
        fence = self.open_external_read()
        self.request_checkpoint = None
        # The person's retention settings; only the capture moment is the app's.
        self._turn_checkpoint_policy = dict(CheckpointPolicyStore(self.store.directory).load(),
                                            capture_mode="BEFORE_FIRST_EDIT")
        self.status = "External work"
        self.emit("message", {"role": "user", "text": text, "request_kind": "external_request", "client": client,
                              "checkpoint_scope": "before_first_approved_edit"})
        return fence

    def send_external_task(self, text):
        """A connected app's task (``external_tasks``): one model turn in its External conversation, as a message.

        The person still approves, stops and undoes it there; the conversation's own connection and model run it.
        """
        self._external_task = True
        try:
            self.send(text)
        finally:
            self._external_task = False

    def require_model_chat(self):
        """Raise for an External conversation: only its app's task (``send_external_task``) starts a model turn."""
        if self.external_client and not getattr(self, "_external_task", False):
            raise RuntimeError(display_text.Text(ui_text.n_("External client conversation: approvals and Stop only. "
                                                            "Switch to another chat to talk to a model.")))
