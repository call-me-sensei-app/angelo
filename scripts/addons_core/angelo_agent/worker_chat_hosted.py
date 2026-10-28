# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``Controller`` a worker Blender hosts for one chat (design A′, ``worker_chat_host``).

It is the window's own ``Controller`` with five differences, each a boundary of the worker:

* every ``emit`` also goes to the lead (``worker_chat_events``), and every write of the chat's file is followed by a
  ``journal`` event, so the lead's mirror follows the file it never writes;
* it never holds a credential: binding any key raises, and its runtime context carries the worker's one model
  runtime transport (``runtime_transport``), through which every provider it builds starts its child (C5);
* a call starts without a person only while the newest consent lease the worker acknowledged grants it
  (``worker_consent.LeasedConsent``, mixed in by ``worker_chat_seams.hosted_base``); no lease, no autonomous start.
  Its file is owned here (``worker_journal_owner.HostedJournal``): another writer's change stops its writes;
* while the lead merges (``holding`` / ``held``), nothing starts without a person and no approval is taken;
* its providers are built by ``provider_factory``, which ``Controller`` calls wherever it builds one;
* a Stop or consent change takes effect as its frame arrives (``worker_chat_arrival``): starting a call checks, under
  the chat's lock, that no Stop is pending and that an autonomous call is still covered by the lease, so no call
  starts once the frame has arrived;
* closing a busy chat forwards its turn's end first (``forward_turn_end``), so the lead's mirror never stays busy;
* a message that arrives once its turn's end went to the lead is taken (``unwind_ended_turn``): idle on the lead means
  the worker takes a send.
"""

import threading

from .controller import Controller

UNWIND_SECONDS = 2.0   # D3: how long a message waits for a turn that already ended to return (a few ms in practice).
STOPPED = "This call's turn was stopped before the call started; nothing ran."   # A Stop arrived (worker_chat_arrival).


def hosted_class(controller_type=Controller):
    from .worker_chat_seams import hosted_base
    base = hosted_base(controller_type)

    class HostedController(base):
        chat_host = None
        holding = held = False
        runtime_transport = None
        journal_writes = 0
        _ended = None   # The (epoch, generation) whose ``finished`` went to the lead.
        _end_lock = threading.Lock()

        def emit(self, kind, data=None):
            super().emit(kind, data)
            if self.chat_host is None:
                return
            with self._end_lock:
                if self._closing:
                    return
                if kind == "finished":
                    pair = ((data or {}).get("_epoch"), (data or {}).get("_generation"))
                    if pair == self._ended:
                        return   # Already forwarded by a close (``forward_turn_end``).
                    self._ended = pair
                self.chat_host.forward(self, kind, data)

        def forward_turn_end(self):
            """A close of this busy chat: its turn's end (``finished``, interrupted) goes to the lead now, unless its
            turn already said so, because nothing it emits after the close is forwarded (break-it F2)."""
            pair = (self._epoch, self._request_generation)
            with self._end_lock:
                if self._closing or self.chat_host is None or self._ended == pair:
                    return False
                self._ended = pair
                self.chat_host.forward(self, "finished", {"status": "interrupted", "_epoch": pair[0],
                                                          "_generation": pair[1]})
            return True

        def send(self, text, *args, **kwargs):
            """A message: a turn whose end already went to the lead is over, so it never refuses this one (D3)."""
            self.unwind_ended_turn()
            return super().send(text, *args, **kwargs)

        def unwind_ended_turn(self):
            """Main thread. The lead's mirror goes idle when this chat's turn forwards ``finished``, which the turn's
            thread does just before it returns, and before the loop's next pump applies it here. A message that comes
            in that gap first takes the turn's events in order (the pump's own step) and waits for its thread to
            return, bounded (``UNWIND_SECONDS``); a turn still running after that is refused as before."""
            thread, pair = self._worker, (self._epoch, self._request_generation)
            if thread is None or thread is threading.current_thread() or self._ended != pair:
                return
            if self.busy:
                self.pump_events()
            thread.join(UNWIND_SECONDS)

        # Arrivals: a Stop or a consent change holds from its frame's arrival (``worker_chat_arrival``).

        def stop_arrived(self):
            arrivals = getattr(self.chat_host, "arrivals", None)
            return arrivals is not None and arrivals.stop_pending(self.state["id"], getattr(self, "lead_seq", None))

        def consent_arriving(self):
            arrivals = getattr(self.chat_host, "arrivals", None)
            return arrivals is not None and arrivals.consent_pending(self.state["id"])

        def consent_marks_cover(self, grant):
            """Whether every consent that arrived for this chat and is not applied yet still grants ``grant`` as its
            lease holds it (``worker_consent_marks``): until applied, the mark wins."""
            arrivals = getattr(self.chat_host, "arrivals", None)
            return arrivals is None or arrivals.consent_covers(self.state["id"], self.consent_lease, grant)

        def start_refusal(self, call):
            """``tool_deadlines.started``, under the lock: why ``call`` may not start now, or None. A call that has
            not started (``worker_consent.revocable``) is refused by a Stop or a consent no longer covering it that
            arrived after its last check, as by a turn's end: the reader marks them without this lock."""
            from . import worker_consent
            if not worker_consent.revocable(call):
                return None
            if self.stop_arrived():
                return STOPPED
            if call.automatic and not worker_consent.admit_autonomous(self, call):
                return worker_consent.CONSENT_REVOKED
            return None

        def _tool_available(self, call, document):
            """Every start of a call checks this last, under the lock; the reader marks a Stop or a consent without
            it, and this check reads those marks. A pending Stop refuses the call as the stopped turn's; an autonomous call the lease no longer
            covers is revoked (``worker_consent.revocable``), as the lease's own revocation would have. A revocation
            after this check still refuses the call: it starts only through ``tool_deadlines.started``, under the same
            lock and only while unrevoked (security re-review: the gap before ``executing``)."""
            from . import worker_consent
            with self.lock:
                stopped = self.stop_arrived()
                if (not stopped and call.automatic and worker_consent.revocable(call)
                        and not worker_consent.admit_autonomous(self, call)):
                    call.revoked = call.revoked or worker_consent.CONSENT_REVOKED
            if stopped:
                if self.pending.get(call.id) is call and not self.expire_tool(call):
                    self.finish_tool(call, {"error": STOPPED, "error_code": "CANCELLED"})
                return False
            return super()._tool_available(call, document)

        def save(self, **kwargs):
            super().save(**kwargs)
            if self.chat_host is not None:
                self.journal_writes += 1
                self.chat_host.journal_written(self)

        @property
        def api_key(self):
            return self._api_key

        @api_key.setter
        def api_key(self, value):
            if value:   # C5: the lead holds every credential; a worker never has one to lose.
                raise RuntimeError("A worker's chat never holds a credential")
            base.api_key.fset(self, value)

        def runtime_context(self):
            context = super().runtime_context()
            context.runtime_transport = self.runtime_transport
            return context

        def provider_factory(self, context, settings):
            """Every provider of this chat: the class its mode runs, started through the worker's transport."""
            from . import controller
            from .provider_registry import runtime_kind
            context.runtime_transport = self.runtime_transport
            kind = controller.CodexProvider if runtime_kind(context.state["provider"]) == "codex" else controller.ClaudeProvider
            return kind(context, settings)

        # Autonomous starts: only under the newest acknowledged lease (``LeasedConsent``), never during a merge hold.

        def _admits(self):
            return not (self.holding or self.held) and self.chat_host is not None

        def _automatic_script(self, call):
            return self._admits() and super()._automatic_script(call)

        def _auto_bounded(self, call, mode=None):
            return self._admits() and super()._auto_bounded(call, mode)

        def standing_consent(self, call, binding):
            return self._admits() and super().standing_consent(call, binding)

        def busy_with_calls(self):
            """A call of this chat is under way: executing, committing or staging (a merge hold waits for none)."""
            return bool(self.native_jobs) or any(call.executing or call.committing or call.staging
                                                 for call in list(self.pending.values()))

    return HostedController
