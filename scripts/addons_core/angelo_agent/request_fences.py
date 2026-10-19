# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Immutable host-request identities and bounded Codex transport admission."""

import json
import threading
from dataclasses import dataclass, field

from .codex_turns import OpenTurns

MAX_PENDING_RESPONSES = 32 * 32  # Controller's pending calls times its waiters per call.


@dataclass(frozen=True)
class RequestFence:
    epoch: int
    generation: int

    @classmethod
    def capture(cls, host):
        return cls(host._epoch, host._request_generation)

    def matches(self, host):
        return self.epoch == host._epoch and self.generation == host._request_generation and not host._closing


@dataclass(frozen=True, eq=False)
class DeliveryOwner:
    """One irreversible delivery lifetime, checked without a provider/host lock.

    A request owns local plan calls. A native turn owns its dynamic calls and
    also depends on that request and its captured transport's closed marker.
    Identity is intentional: reused native IDs never revive an earlier owner.
    Checks admit a phase; they do not interrupt a native operation or arbitrate
    an RPC write already in progress.
    """
    fence: object = None
    parent: object = None
    closed_with: object = None
    _closed: object = field(default_factory=threading.Event, repr=False)

    def close(self):
        self._closed.set()

    def current(self, host=None):
        return (not self._closed.is_set()
                and (self.closed_with is None or not self.closed_with.is_set())
                and (self.parent is None or self.parent.current(host))
                and (host is None or self.fence is None or self.fence.matches(host)))


@dataclass(eq=False)
class ResponseClaim:
    """One logical call owns an RPC ID while pending, then until its write returns."""
    call_id: str
    writing: bool = False  # Monotonic, changed only under the native scope lock.


class CodexTurnScope:
    """One native turn; early messages wait for the turn/start response.

    A persistent connection is not a request identity. Both native IDs and the
    captured host fence must match before a callback can emit or admit a tool.
    Queuing instead of blocking lets the RPC reader deliver the start response.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.turns = OpenTurns()  # What Codex still has open on this connection, whoever owns it.
        self.context = None
        self.fence = None
        self.thread_id = self.turn_id = None
        self.starting = False
        self.completed = False
        self.seen_turns = set()
        self.pending = []
        self.pending_bytes = 0
        self.delivery_owner = None
        self.closed_with = None
        self.responses = {}

    def begin(self, context, thread_id, *, starting=True, closed_with=None):
        with self.lock:
            self.close_delivery()
            self.context = context
            self.fence = self.context_fence(context)
            self.thread_id, self.turn_id = thread_id, None
            self.starting = starting
            self.completed, self.seen_turns = False, set()
            self.pending, self.pending_bytes = [], 0
            self.closed_with = closed_with
            self.responses = {}
            # Also identify the pending turn/start binding. Its reply cannot
            # replace a newer binding that happens to reuse the same IDs.
            self.delivery_owner = DeliveryOwner(self.fence, getattr(context, "delivery_owner", None), closed_with)

    @staticmethod
    def context_fence(context):
        fence = getattr(context, "request_fence", None)
        host = getattr(context, "host", context)
        if fence is None and hasattr(host, "_request_generation"):
            fence = RequestFence.capture(host)
        return fence

    def current(self, context, *, allow_cancelled=False):
        host = getattr(context, "host", context)
        return (self.context is context and self.fence == self.context_fence(context)
                and (self.fence is None or self.fence.matches(host))
                and (allow_cancelled or not context.cancelled))

    def matches(self, context, params, *, turn_required=True, allow_cancelled=False, allow_completed=False):
        with self.lock:
            if (not self.current(context, allow_cancelled=allow_cancelled)
                    or not isinstance(params, dict) or params.get("threadId") != self.thread_id):
                return False
            turn = params.get("turn")
            identity = params.get("turnId", turn.get("id") if isinstance(turn, dict) else None)
            return not turn_required or bool(self.turn_id and identity == self.turn_id
                                              and (allow_completed or not self.completed))

    def defer(self, kind, identity, method, params):
        with self.lock:
            if not self.starting:
                return False
            try:
                size = len(json.dumps(params, allow_nan=False).encode("utf-8"))
            except (TypeError, ValueError, UnicodeError):
                return False
            if len(self.pending) >= 64 or size + self.pending_bytes > 1024 * 1024:
                return False
            self.pending.append((kind, identity, method, params))
            self.pending_bytes += size
            return True

    def bind_turn(self, turn_id):
        with self.lock:
            self.close_delivery()
            self.responses = {}
            self.turn_id = turn_id if isinstance(turn_id, str) and turn_id else None
            self.completed = False
            if self.turn_id:
                self.seen_turns.add(self.turn_id)
                self.delivery_owner = DeliveryOwner(self.fence, getattr(self.context, "delivery_owner", None),
                                                    self.closed_with)
            self.starting = False
            pending, self.pending = self.pending, []
            self.pending_bytes = 0
            return pending

    def bind_voice_turn(self, turn_id):
        with self.lock:
            if (not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 512
                    or turn_id in self.seen_turns or len(self.seen_turns) >= 256):
                return False
            self.bind_turn(turn_id)
            return True

    def interrupt_ids(self, fence):
        self.turns.wake()  # Stop ends a pending turn/start guard wait at once (codex_turns).
        with self.lock:
            if fence != self.fence or not self.turn_id:
                return None
            self.close_delivery()
            self.turns.interrupting(self.turn_id)
            return self.thread_id, self.turn_id

    def new_transport(self):
        """The app-server process was replaced or closed: close delivery and forget its open turns.

        A shared app-server's record (``codex_runtime.Runtime.turns``) is left, never cleared: its
        turns, this chat's included, stay open in that process.
        """
        self.close_delivery()
        if getattr(self.turns, "shared", False):
            self.turns.wake()
            self.turns = OpenTurns()
        else:
            self.turns.clear()

    def close_delivery(self):
        """Close only the owner currently captured under this scope's lock."""
        with self.lock:
            if self.delivery_owner is not None:
                self.delivery_owner.close()
            self.responses.clear()

    def complete(self):
        with self.lock:
            self.completed = True
            self.close_delivery()

    @staticmethod
    def _response_key(identity):
        if type(identity) is float and identity.is_integer():
            identity = int(identity)
        return json.dumps(identity, sort_keys=True, separators=(",", ":"))

    def owns_response(self, identity):
        """Whether an admitted call currently holds this RPC ID's response."""
        with self.lock:
            return self._response_key(identity) in self.responses

    def claim_response(self, identity, call_id):
        """One outstanding RPC envelope; an answered ID may be used again.

        During an admitted write, a distinct logical call may reuse its RPC ID;
        the same call is still a duplicate until that write returns. The pinned
        registry and exact claim keep old cleanup from removing a successor.
        """
        key = self._response_key(identity)
        with self.lock:
            if self.delivery_owner is None or not self.delivery_owner.current():
                return None
            previous = self.responses.get(key)
            if previous is not None and (not previous.writing or previous.call_id == call_id):
                return None
            if previous is None and len(self.responses) >= MAX_PENDING_RESPONSES:
                raise OverflowError("Too many outstanding native tool responses; wait for the admitted calls")
            claim = ResponseClaim(call_id)
            self.responses[key] = claim
            return self.delivery_owner, self.responses, key, claim

    def begin_response(self, ticket):
        """Admit one write without holding this lock across transport I/O."""
        owner, registry, key, claim = ticket
        with self.lock:
            if (owner is not self.delivery_owner or not owner.current()
                    or registry.get(key) is not claim or claim.writing):
                return False
            claim.writing = True
            return True

    def retire_response(self, ticket):
        owner, registry, key, claim = ticket
        if not owner.current():
            # This registry can no longer admit work and is never reused by a
            # successor. Closed UI cleanup must not wait on a provider lock.
            if registry.get(key) is claim:
                registry.pop(key, None)
            return
        with self.lock:
            if registry.get(key) is claim:
                del registry[key]
