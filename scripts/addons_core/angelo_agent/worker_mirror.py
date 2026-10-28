# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's mirror of a chat hosted in its worker Blender (design A′, contract C9).

With A′ the worker chat's real ``Controller`` and its provider run inside the worker; the lead keeps a
``MirrorController`` in ``ui._controllers``, so drawing, chat tabs, the session rail's tiles, worker cards and the
agent panel read it as they read any chat. It is a real ``Controller`` subclass that holds no work:

* **State** comes only from the chat's journal, read with the store's own reader (``worker_mirror_events.reload``),
  and from the worker's forwarded events (C3), applied display only. Its file is the worker's: it never saves
  (``save`` writes nothing, and its store refuses every write).
* **Actions** go to the worker as chat ops (C2) through one seam, ``send_op(session, op, **fields)``: Send, Approve
  (with the digest of the summary the card drew, or the mirror's for a card never drawn), Decline, Stop, options, consent and a system notice.
* **Local work refuses** (``LocalWork``): no tool is admitted, run or finished here, no provider or voice session
  starts, and no key is bound (the worker's runtime gets its credentials from the lead's launch service, C5).
  ``pump_tools`` and the tool predicates answer "nothing to do", because the UI pump asks every chat.

The behaviour switch is ``ANGELO_WORKER_CHAT_HOST`` (``enabled``): unset or ``1`` means A′, ``0`` keeps today's
lanes. A mirror exists only where A′ made one; nothing here changes a chat that is not one.

Standard library only and no ``bpy``.
"""

import queue
import threading
import time

from .controller import Controller
from . import event_queue, worker_mirror_events as events, worker_mirror_opens as opens, worker_mirror_ops as ops
from . import worker_mirror_pins as pins
from .display_text import Text
from .worker_mirror_journal import MirrorStore
from .ui_text import n_

NOT_OPEN = n_("This chat is not open in its worker; nothing was sent.")
NOT_ACCEPTED = n_("This chat's worker did not accept the request, so nothing is counted as done "
                  "({why}; support code {code}).")
KEEPS_CONNECTION = n_("A worker chat keeps the connection it started with. Start a new worker to use another one.")

def enabled():
    """Design A′ (worker-hosted chats) is on (``worker_chat_seams.enabled``); ``0`` keeps the lanes."""
    from .worker_chat_seams import enabled as switch
    return switch()


def hosted(controller):
    """Whether ``controller`` is a mirror of a chat its worker hosts."""
    return getattr(controller, "hosted_in_worker", False) is True


class LocalWork(RuntimeError):
    """A mirror was asked to do work that only its worker does; nothing ran."""


class MirrorRefused(RuntimeError):
    """The worker did not accept a chat op (no answer, an error, or no worker attached); nothing is assumed done."""


class HostedTurn:
    """What ``worker_routing.deliver`` hands ``send`` for a hosted chat: the worker observes its own document."""

    observation = None
    recovery_evidence = None

    def commit(self):
        pass


class _ReadOnlyStore:
    """The mirror's ``SessionStore``: reads and in-memory bookkeeping as the store's own; every write refuses."""

    WRITES = frozenset({"save", "compact", "leave", "compact_if_due", "save_if_due", "redact_kept"})
    # ``invalidate`` is allowed: a cache flag of the store's own writer (``SessionStore.invalidate``), which never writes.

    def __init__(self, store):
        self.__dict__["_store"] = store

    def __getattr__(self, name):
        if name in self.WRITES:
            def refuse(*_args, **_kwargs):
                raise LocalWork("A worker-hosted chat's file is written by its worker only (single writer)")
            return refuse
        return getattr(self._store, name)


class MirrorController(Controller):
    hosted_in_worker = True
    store_type = MirrorStore   # Reads only what its worker wrote since the last read (``worker_mirror_journal``).

    def __init__(self, directory, session_id, send_op=None, *, records=None, settings=None):
        super().__init__(directory, settings, session_id)   # The chat as a load reads it (the file's state).
        self.store = _ReadOnlyStore(self.store)
        self.send_op = send_op     # ``send_op(session, op, **fields)``: the worker's answer, or None (C2).
        self.records = records     # ``records(kind, data)``: usage/telemetry the lead writes (C8, its own seam).
        self.provider = None
        self._worker_request = None    # Newest (epoch, generation) of the worker's events seen.
        self._ended_request = None     # The (epoch, generation) whose ``finished`` was shown.
        self.open_id, self.awaiting_open, self.retired_opens = None, True, set()   # C9 (``worker_mirror_opens``).
        self.reload_retry = None       # When a failed read of the file is tried again (``events.reload``).
        self._signs = {"accepted": None, "last": None, "heard": None}
        self.journal_seq = 0
        pins.pin(self, self.state)   # Its route and options are the lead's from here (``worker_mirror_pins``).

    # Credentials never bind here: the mirror holds no key (C5) ---------------------------------------------------

    @property
    def api_key(self):
        return ""

    @api_key.setter
    def api_key(self, value):
        del value   # The worker's runtime is launched by the lead's own service, with the lead's credentials.

    # Forwarded frames (any thread) -----------------------------------------------------------------------------

    def receive(self, kind, data):
        """One frame from the worker, from the channel's reader thread: queued in order for the main thread."""
        events.put(self, kind, data)

    def emit(self, kind, data=None):
        """Lead code's own event on this chat: a system notice goes to the worker, which writes it into the chat's
        file (``chat.notice``); everything else is display only. A notice the worker did not take is still shown."""
        data = dict(data or {})
        if kind == "message" and data.get("role") == "system":   # Never waits on the worker (it may be wedged).
            threading.Thread(target=self._notice, args=(data,), daemon=True, name="AngeloWorkerNotice").start()
            return
        if kind == "message" and data.get("role") == "error" and threading.current_thread() is threading.main_thread():
            ops.add_row(self, "error", {k: v for k, v in data.items() if k != "role"})   # Kept across reloads.
            return
        events.put(self, kind, data)

    def _notice(self, data):
        try:
            self._op("chat.notice", message={k: v for k, v in data.items() if not k.startswith("_")})
        except MirrorRefused as error:
            print("Angelo: a worker chat's notice was not taken by its worker:", error)
            events.put(self, events.NOTICE, data)   # Saved here once the chat is the lead's again (U10).

    def op_answered(self, op, call_id, answer):
        """Any thread: the worker's answer to the person's Approve or Decline (``worker_consent_lead``)."""
        events.put(self, events.ANSWER, {"op": op, "call_id": call_id, "answer": answer})

    def approval_sent(self, call_id):
        opens.approval_sent(self, call_id)

    def declined(self, call_id):
        opens.declined(self, call_id)

    # Main thread -------------------------------------------------------------------------------------------------

    def pump_events(self):
        return self._pump_events()

    def _pump_events(self):
        drained = []
        event_queue.flush(self)   # What the reader held while the queue was full joins it first, in order.
        for _ in range(256):
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                break
        last_journal = max((i for i, (kind, _data) in enumerate(drained) if kind == events.JOURNAL), default=-1)
        changed = False
        if last_journal < 0 and events.retry_due(self):
            changed = ops.reload(self)   # A read that failed is tried again.
        changed = opens.settle(self) or changed   # An Approve no answer settled (``worker_mirror_settle``).
        for index, (kind, data) in enumerate(drained):
            if kind in events.RECORDS:
                if callable(self.records):
                    self.records(kind, data)   # Usage of any open is real usage: always written (C8).
            elif kind in LEAD_KINDS:
                changed = LEAD_KINDS[kind](self, data) or changed
            elif not opens.admits(self, data):
                continue   # A frame of an earlier (or unannounced) open of this chat (C9).
            elif kind == events.JOURNAL:
                seq = data.get("seq")
                if type(seq) is int:
                    self.journal_seq = max(self.journal_seq, seq)
                if index == last_journal:   # The file holds every earlier write: one read per pump.
                    records = data.get("records")   # The lead's own rows (its errors) stay where they were.
                    changed = ops.reload(self, records if type(records) is int else None) or changed
            elif kind == events.PENDING:
                if self.busy and self.cancelled:
                    continue   # Stopping: the cards drawn before the Stop are gone (C5), and none comes back.
                changed = events.apply_pending(self, data) or changed   # Never an older account (audit2 #5).
            elif kind == events.NEWS:
                events.news(self, data)
            elif kind == events.SIGNS:
                events.signs(self, data)
            elif kind == events.RUNNING:
                events.running(self, data)
            elif kind == "turn_facts":   # Rounds and the turn's clock for worker_facts (worker_chat_events).
                from .worker_chat_events import apply_turn_facts
                apply_turn_facts(self, data)
            elif events.admit(self, kind, data):
                if kind == "message" and data.get("role") == "user":
                    ops.took(self)   # The worker wrote the Send's own row: it took it, whatever its answer says.
                changed = events.apply(self, kind, data) or changed
        if changed:
            self.version += 1
        return changed

    def begin_turn(self):
        """The mirror's live row starts (the person's Send here, or the worker's own new request)."""
        self._request_generation += 1
        self.turn_started = (self._request_generation, time.monotonic())
        self._request_message_index = None
        self.busy, self.cancelled = True, False
        self.status = "Thinking"
        self._items.clear()
        self.thinking.clear()
        self._thinking_messages.clear()
        self._thinking_times.clear()
        self._signs.update(accepted=False, last=None)
        self._request_metrics = events.metrics(self)   # The live meter's numbers: the worker's ``running_usage``.

    def _recovered(self, data):
        """Its worker ended (``worker_journal_owner.worker_ended``): the file as reopened, the turn ended."""
        if opens.superseded(self, data):
            return False   # An earlier open's, queued behind the new open's REOPEN: nothing of it is shown now.
        state = data.get("state")
        if isinstance(state, dict):
            self.store._store.load()   # A store loaded before the hand-off never writes again until it loads.
            with self.lock:
                self.state.clear()
                self.state.update(state)
            ops.keep_rows(self)
        self.pending = {}
        later = data.get("generation") not in (None, self._request_generation)   # A turn begun since: not this one's.
        if getattr(self, "_sending", None) is not None and not later:   # A Send it never answered ran nothing.
            ops.return_unsent(self)
        if self.busy and not later:
            events.finish(self, dict(data.get("finished") or {"status": "interrupted"}))
        return True

    def turn_signs(self):
        """``turn_activity.signs`` for the live row (``live_progress``), from forwarded events, never a provider."""
        if not self.busy:
            return True, None
        return True, (self._signs["accepted"], self._signs["last"], self._signs["heard"])

    def writing(self):
        """The worker is applying one of this chat's edits now (its pending summary says so)."""
        return any(getattr(call, "executing", False) for call in list(self.pending.values()))

    # Actions: each one a chat op (C2) ----------------------------------------------------------------------------

    def _op(self, op, **fields):
        if not callable(self.send_op):
            raise MirrorRefused(Text(NOT_OPEN))
        answer = self.send_op(self.state["id"], op, **fields)
        if type(answer) is not dict or answer.get("ok") is not True:
            text, code = ops.why(answer if type(answer) is dict else {}, op)
            raise MirrorRefused(Text(NOT_ACCEPTED, why=text, code=code))
        return answer

    def send(self, text, observation=None, *, recovery_evidence=None, host_request=None):
        """The person's message (or Angelo's own ``host_request``) to the worker's chat, sent without waiting
        (``worker_mirror_ops``): the turn shows as sending until the worker confirms it, or comes back unsent. The
        worker observes its own document, so ``observation`` and ``recovery_evidence`` (the lead's) are never sent."""
        del observation, recovery_evidence
        if self.busy:
            raise RuntimeError(Text(n_("An agent turn is already running")))
        if not str(text).strip() and not self.attachments or len(str(text)) > 32000:
            raise ValueError(Text(n_("Enter a message of 1-32000 characters")))
        from . import worker_mirror_park as park   # A parked chat's worker starts again.
        if not callable(self.send_op) and not park.waits(self):
            raise MirrorRefused(Text(NOT_OPEN))
        before = park.consent(self) if callable(self.send_op) else None   # A parked chat's: as it goes (``_go``).
        drafts = [] if host_request is not None else [dict(reference) for reference in self.attachments]
        self.turn_attachments = drafts
        del self.attachments[:len(drafts)]
        self.begin_turn()
        self.status = ops.SENDING
        self._turn_message, self._host_request = text, host_request   # A message never sent goes back (finish).
        self._sending = (ops.submit if callable(self.send_op) else park.held)(
            self, "chat.send", "send", before=before, text=text, attachments=drafts,
            host_request=list(host_request) if host_request is not None else None)
        from . import worker_mirror_prewarm
        worker_mirror_prewarm.sent(self)   # Its prewarm, if any, is the worker's request's now.

    def approve_call(self, call_id):
        """Approve the call: only through ``worker_consent_lead.approve``, so what goes back is the digest its card
        showed when it was drawn (the mirror's own for a card never drawn), once, never during a Stop, and the worker
        runs it only if its call still has that digest (C6). A call already approved here is not sent twice."""
        from . import worker_consent_lead
        call = self.pending.get(call_id)
        if call is None or not call.approval or call.approved:
            raise MirrorRefused(Text(n_("This approval is no longer waiting; nothing was approved.")))
        reason = worker_consent_lead.approve(self, call_id)
        if reason is not None:
            raise MirrorRefused(Text(reason))
        return True

    def approve_tool(self, call, executor=None, document=None):
        """The approve operator's route: the worker runs the edit in its own document."""
        del executor, document
        return self.approve_call(call.id)

    def execute_tool(self, call, executor=None, document=None):
        del executor, document
        return self.approve_call(call.id)

    def decline_tool(self, key):
        self._op("chat.decline", call_id=key)
        opens.declined(self, key)
        self.version += 1

    def stop(self, reason=None):
        """The person's Stop (or an ending's, ``worker_routing.end_turn``): the worker stops its turn. A Stop never
        fails here: one the worker did not take is kept (``stop_refused``) for the kill escalation (C5), which belongs
        to the lead's runtime service behind ``send_op``; the live row stays until the worker's turn has ended."""
        if ops.withdraw(self):   # A Send still on its lane never reached the worker: it is back, nothing to stop.
            return
        self.cancelled = True
        self.stops = getattr(self, "stops", 0) + 1
        self.status = "Stopping" if self.busy else "Interrupted"
        self.pending = {}   # As a window's Stop cancels its calls: no card stays to be approved.
        self.version += 1
        from . import worker_consent_lead
        if worker_consent_lead.hosted(self):   # Sent without waiting; its model child is killed 2 s later (C5).
            worker_consent_lead.stop(self, reason)
            self.stop_refused = None
            return
        try:
            self._op("chat.stop", reason=reason, lead_seq=ops.order())
            self.stop_refused = None
        except MirrorRefused as error:
            self.stop_refused = str(error)
            print("Angelo: a worker chat's Stop was not taken by its worker:", error)

    def set_options(self, **changes):
        """Model, effort, approval mode: shown here at once, sent without waiting (``worker_mirror_ops``); the
        worker's chat changes them and its journal then shows them. A change it refuses says so in the chat."""
        from .chat_settings import stored_options
        with self.lock:
            self.state["options"] = stored_options(stored_options(self.state.get("options")) | changes)
        self.version += 1
        pins.requested(self, ops.submit(self, "chat.options", "options", options=dict(changes)))

    def set_consent(self, epoch, grants):
        """The standing-consent lease (C6; its semantics are ``worker_consent``'s): answered once applied."""
        return self._op("chat.consent", epoch=epoch, grants=grants)

    def close(self):
        """The lead lets the mirror go: the worker ends its chat (``chat.close``, ``worker_chat_lifecycle``, which hands
        its file back); nothing is written here. The close runs on a thread of its own (``closing``), so the main
        thread never waits on a wedged worker (``worker_mirror_close``)."""
        self._closing = True
        from . import worker_mirror_close
        worker_mirror_close.closing(self.state["id"])

    # Local work refuses; the UI pump's questions answer "nothing to do" --------------------------------------------

    def save(self, *, compact=False, compact_if_due=False, leave=False):
        del compact, compact_if_due, leave   # Single writer (C7): the worker's.

    def pump_tools(self, executor=None, document=None, apply_mutation=None, budget=None, commit_mutation=None):
        return False

    @property
    def has_dispatchable_tools(self):
        return False

    def set_document(self, document):
        """Only the worker's part (its bound executor's document key, ``worker_routing.bind``): what the person's
        trust and Always allow are scoped to (``trusted_scripts``), never the lead's own document."""
        executor = getattr(self, "remote_executor", None)
        if executor is not None and document is not None and document == getattr(executor, "document_key", None):
            self.document, self.document_invalid = document, False

    def select(self, *_args, **_kwargs):
        pass   # The connection is the one the chat was opened with (``chat.open``).

    def choose_connection(self, provider, settings, api_key, connection_id, models=None, model=""):
        """A model picked on this chat's own connection is a ``chat.options`` change (the window's rules for its
        effort and Fast); another connection is refused, translated: the worker's runtime is launched for the
        connection the chat opened with."""
        del settings, api_key
        if (provider, connection_id) != pins.route(self):   # The lead's own record, never the worker's file.
            raise RuntimeError(Text(KEEPS_CONNECTION))
        from .chat_settings import stored_options
        choices = list(models or self.models or [])
        selected = (next((m for m in choices if m.get("id") == model), None) if model
                    else next((m for m in choices if m.get("default")), None))
        if model and not selected:
            raise ValueError(Text(n_("Refresh the models available to this connection")))
        options = stored_options(self.state.get("options"))
        effort = options["effort"]
        if selected and selected.get("efforts") and effort not in selected["efforts"]:
            effort = (selected["default_effort"] if selected.get("default_effort") in selected["efforts"]
                      else selected["efforts"][0])
        self.set_options(model=model, effort=effort, fast=options["fast"] and bool(selected and selected.get("fast_tier")))

    def invalidate_runtime(self, connection_id=None):
        """A connection's key changed, it was removed or signed in again: nothing here holds a runtime, so this never
        refuses (the window's sweeps ask every chat). An idle chat on that connection has its model child, which the
        lead runs (C5), ended, so its worker's next request launches one with the lead's current credentials; a busy
        one is stopped by the sweep itself, and its Stop's kill follows."""
        if self.busy or (connection_id is not None and connection_id != (pins.route(self) or (None, None))[1]):
            return
        from . import worker_consent_lead
        if worker_consent_lead.hosted(self):
            worker_consent_lead._kill(self.state["id"])

    def always_allow_script(self, call):
        """The card's Always allow scripts, once the window saved the choice: the worker's lease grants it from now
        on (``worker_consent_lead.changed``) and this script, the one the card drew, is approved there with that
        card's digest, so it runs as in a window's chat. False when it does not apply."""
        if (self.pending.get(call.id) is not call or not self.offers_always_allow(call) or self.expire_tool(call)
                or not self.always_allow_scripts):
            return False
        from . import worker_consent_lead
        worker_consent_lead.changed()
        return worker_consent_lead.approve(self, call.id) is None

    def apply_next_connection(self):
        pass

    def refresh_models(self):
        """Nothing starts here: its models are the ones ``chat.open`` carried and the worker's ``models`` events,
        so the window's model picker and sign-in sweep (which ask every chat) leave a mirror as it is."""

    def expire_tool(self, call):
        """A card past its shown expiry is not approved here (the worker expires and answers it itself)."""
        return self.pending.get(call.id) is not call or time.monotonic() >= call.expires

    def cancel_tools(self, reason, *, persist=True):
        del reason, persist   # The worker's calls end in the worker (``chat.stop``); a lead Undo is not theirs.

    def invalidate_native_stages(self, reason="", *, persist=True):
        del reason, persist

    def mark_memory_stale(self, reason="external_edit", evidence_id=""):
        del reason, evidence_id

    def rebind_after_restore(self, document, checkpoint_id, *, project_scope=None):
        return {"warnings": []}   # Angelo restored its own file, not the worker's part.

    def _automatic_script(self, call):
        return getattr(call, "needs_person", True) is not True   # The worker's own verdict, from its summary.

    def _auto_bounded(self, call, mode=None):
        return getattr(call, "needs_person", True) is not True


def _refuse(name):
    def refuse(self, *_args, **_kwargs):
        raise LocalWork("%s runs only in the worker that hosts this chat; nothing ran" % name)
    refuse.__name__ = name
    return refuse


for _name in ("request_tool", "_admit_tool_request", "finish_tool", "_pump_tools", "_execute_tool", "_start_turn",
              "_run", "_connect_runtime", "runtime_context", "start_voice", "send_voice_text",
              "new_chat", "request_continuation_observation", "_answer_observation_request",
              "accept_native_preview", "discard_native_preview"):
    setattr(MirrorController, _name, _refuse(_name))


# The lead's own events on a mirror (``worker_mirror_opens``): never a worker's frame (``events.frame_parts``).
LEAD_KINDS = {events.RECOVERED: MirrorController._recovered, events.REOPEN: opens.reopen,
              events.CLOSED: opens.closed, events.ANSWER: opens.answered, events.NOTICE: opens.keep_notice}


# Registration and fan-in ------------------------------------------------------------------------------------------

def register(mirror):
    """The mirror is a chat like any other for drawing and tabs (``ui._controllers``)."""
    from . import ui
    ui._controllers[mirror.state["id"]] = mirror
    return mirror


class EventRouter:
    """One worker's ``on_event`` (``worker_channel.Client``): each frame goes to the mirror of the chat it names, in
    the order the reader thread got them. A chat the worker was not opened on (``chat.open``) is never written by
    its frames: they are dropped and logged."""

    def __init__(self):
        self.mirrors = {}   # session -> MirrorController opened on this worker
        self.dropped = 0

    def open(self, mirror):
        self.mirrors[mirror.state["id"]] = mirror

    def close(self, session):
        self.mirrors.pop(session, None)

    def __call__(self, event):
        parts = events.frame_parts(event)
        mirror = self.mirrors.get(parts[0]) if parts is not None else None
        if mirror is None:
            self.dropped += 1
            print("Angelo: a worker event for a chat it does not host was dropped")
            return
        mirror.receive(parts[1], parts[2])
