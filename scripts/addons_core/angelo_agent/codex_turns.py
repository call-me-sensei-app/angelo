# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Codex turns still open on one app-server connection, independent of request ownership.

codex app-server keeps a turn open while a dynamic tool call waits for its
reply, and a ``turn/start`` sent on a thread with an open turn is steered into
that turn instead of starting a new one (``codex_stale_call_probe.py``). A
request's delivery owner can close while its turn is still open (the request
finished, or ``turn/start`` answered after ownership moved on), so the
provider keeps this separate record of what Codex itself still has open:

* a turn opens when ``turn/start`` returns it, a ``turn/started`` notification
  names it or one of its tool calls arrives, and closes on ``turn/completed``;
* ``interrupt`` ends an orphaned turn without blocking the RPC reader thread;
* ``settle`` runs before every ``turn/start``: it interrupts the thread's open
  turns and waits, bounded, for Codex to complete them. If one never completes
  the caller closes the connection instead of sending, so a new human message
  is never absorbed into an older turn and a dead turn cannot block the chat.
  On a shared app-server (``codex_runtime``) one record per process outlives
  each chat's connection, and the stuck thread is abandoned instead
  (``provider.abandon_thread``, ``STUCK_SHARED``).
  Stop ends that wait at once (``wake``) and abandons the send: the message is
  not sent, the connection stays warm and the older turn's interrupt stands.

Voice threads are not recorded: realtime turns belong to the voice session.
Nothing here admits, answers or records a tool call.
"""

import threading
import time
from collections import OrderedDict

from .protocol import RpcError

MAX_TURNS = 64  # Open turns per connection; a chat has one at a time.
MAX_COMPLETED = 256  # Completed turn IDs remembered against a late open.
SETTLE_SECONDS = 15.0  # Interrupt round trip plus Codex's turn/completed.
STUCK = ("An earlier agent turn in this chat did not end, so this message was not sent. The agent connection "
         "was restarted; send the message again.")
# On a shared app-server (codex_runtime) the process stays: the chat's next message opens a new context instead.
STUCK_SHARED = ("An earlier agent turn in this chat did not end, so this message was not sent. The chat continues "
                "in a new agent context; send the message again.")
ABANDONED = "Stopped before the message was sent to the agent; nothing was sent."


class MessageNotSent(RpcError):
    """No ``turn/start`` was sent: the person's message never reached the agent, so the controller offers it back."""

    message_not_sent = True


class TurnStartAbandoned(MessageNotSent):
    """Stop (or a newer request) arrived before ``turn/start`` was sent; the connection stays usable."""


def _identities(params):
    if not isinstance(params, dict):
        return None, None
    turn = params.get("turn")
    turn_id = params.get("turnId", turn.get("id") if isinstance(turn, dict) else None)
    thread_id = params.get("threadId")
    valid = all(isinstance(value, str) and 1 <= len(value) <= 512 for value in (thread_id, turn_id))
    return (thread_id, turn_id) if valid else (None, None)


class OpenTurns:
    def __init__(self, clock=time.monotonic):
        self.settle_seconds = SETTLE_SECONDS
        self._lock = threading.Condition()
        self._open = OrderedDict()  # turn ID -> thread ID
        self._completed = OrderedDict()
        self._interrupting = set()
        self._workers = []
        self._clock = clock

    def clear(self):
        """A new or closed transport: the old process's turns no longer exist."""
        with self._lock:
            self._open.clear()
            self._completed.clear()
            self._interrupting.clear()
            self._lock.notify_all()

    def opened(self, thread_id, turn_id):
        with self._lock:
            if not thread_id or not turn_id or turn_id in self._completed or turn_id in self._open:
                return
            self._open[turn_id] = thread_id
            while len(self._open) > MAX_TURNS:
                self._open.popitem(last=False)  # Bounded: the oldest is the least likely still open.

    def completed(self, turn_id):
        with self._lock:
            self._open.pop(turn_id, None)
            self._interrupting.discard(turn_id)
            self._completed[turn_id] = True
            while len(self._completed) > MAX_COMPLETED:
                self._completed.popitem(last=False)
            self._lock.notify_all()

    def observe(self, method, params):
        """A notification from the current transport, whoever owns it now."""
        if method == "host/disconnected":
            self.clear()
            return
        thread_id, turn_id = _identities(params)
        if method == "turn/completed" and turn_id:
            self.completed(turn_id)
        elif method in ("turn/started", "item/tool/call"):
            self.opened(thread_id, turn_id)

    def wake(self):
        """Stop: end a ``settle`` wait now instead of at its next poll."""
        with self._lock:
            self._lock.notify_all()

    def interrupting(self, turn_id):
        """Stop is interrupting this turn itself: no second ``turn/interrupt`` for it."""
        with self._lock:
            self._interrupting.add(turn_id)

    def open_on(self, thread_id):
        with self._lock:
            return [turn for turn, thread in self._open.items() if thread == thread_id]

    def is_open(self, thread_id, turn_id):
        with self._lock:
            return self._open.get(turn_id) == thread_id

    def interrupt(self, rpc, thread_id, turn_id):
        """End an orphaned open turn; Codex then cancels its waiting tool calls itself.

        Runs on a daemon thread: the caller may be the RPC reader, which must
        stay free to read the reply. A turn is interrupted at most once.
        """
        with self._lock:
            if rpc is None or self._open.get(turn_id) != thread_id or turn_id in self._interrupting:
                return False
            self._interrupting.add(turn_id)

        def send():
            try:
                rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)
            except Exception:  # A closed transport or a turn already ended: turn/completed or settle decides.
                pass

        worker = threading.Thread(target=send, daemon=True, name="AngeloCodexInterrupt")
        with self._lock:
            self._workers = [item for item in self._workers if item.is_alive()] + [worker]
        worker.start()
        return True

    def join(self, timeout=5.0):
        """Wait for sent interrupts to return (tests and settle)."""
        deadline = self._clock() + timeout
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(max(0.0, deadline - self._clock()))

    def start(self, provider, rpc, params, context=None):
        """``turn/start`` once no older turn is open on its thread; records the turn it returns.

        A turn that does not complete after its interrupt closes the connection
        (a new app-server process has no open turns; the next request
        reconnects) and fails this request without sending its message. A
        stopped request (``context.cancelled``) sends nothing either: the wait
        ends at once and ``TurnStartAbandoned`` leaves the warm connection as it is.
        """
        context = getattr(provider, "controller", None) if context is None else context  # This message's request.

        def stopped():  # Stop, or this request no longer current: the message must not be sent.
            return bool(getattr(context, "cancelled", False))

        thread_id = params["threadId"]
        settled = self.settle(rpc, thread_id, stopped=stopped)
        if stopped():
            raise TurnStartAbandoned(ABANDONED)  # The controller finishes a cancelled request as interrupted.
        if not settled:
            abandon = getattr(provider, "abandon_thread", None)
            text = abandon(thread_id) if callable(abandon) else None
            provider.close()
            raise MessageNotSent(text or STUCK)
        response = rpc.request("turn/start", params)
        turn = response.get("turn") if isinstance(response, dict) else None
        self.opened(thread_id, turn.get("id") if isinstance(turn, dict) else None)
        return response

    def settle(self, rpc, thread_id, timeout=None, stopped=lambda: False):
        """True once no older turn is open on ``thread_id``; interrupts any that are.

        Called before ``turn/start`` on a worker thread, never the RPC reader.
        False means a turn did not complete in time (``settle_seconds``) or
        ``stopped()`` became true (``wake`` makes that immediate): do not send.
        """
        for turn_id in self.open_on(thread_id):
            self.interrupt(rpc, thread_id, turn_id)
        deadline = self._clock() + (self.settle_seconds if timeout is None else timeout)
        with self._lock:
            while any(thread == thread_id for thread in self._open.values()):
                remaining = deadline - self._clock()
                if (remaining <= 0 or stopped() or rpc is None
                        or getattr(rpc, "closed", None) is not None and rpc.closed.is_set()):
                    return False
                self._lock.wait(min(remaining, 0.25))
            return True
