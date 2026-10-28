# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender's own chats (design A′): the lead's ``chat.*`` requests, and the loop that runs their turns.

With ``ANGELO_WORKER_CHAT_HOST`` on (the default, ``worker_chat_seams.enabled``) a worker chat's real ``Controller``
and its provider run here, in the worker that edits its part, on this Blender's main thread with a local
``BlenderExecutor``: no shadow, no lane, no precondition round trips. The lead keeps a mirror and sends every action
as a request (C2); everything the chat emits goes back as events (C3, ``worker_chat_events``).

``Angelo --background`` fires no timers, so the loop is ``WorkerHost.run``: it takes its next request through
``next_request``, which waits on the inbox at most ``PUMP_SECONDS`` (``BUSY_SECONDS`` while a hosted chat has work)
and between requests gives every hosted chat the window timer's own two calls, ``pump_events`` and the shared
``pump_controller_tools`` with a fresh ``ToolDispatchBudget``. After a committed mutation the part is saved, as an
approved edit's was before, and its layout is sent when it changed.

Requests (each answered ``{"ok": True, ...}`` or the worker's encoded error; nothing runs on a refusal, and the
chat's cards are sent again after any refusal, since the lead may have spent a card's one Approve on it):

* ``chat.open`` loads the chat from the lead's state folder (``store_directory``, the folder holding ``sessions/``) as
  its single writer, with settings that may carry no credential, the lead's model list, options and consent lease;
* ``chat.send`` / ``chat.stop`` / ``chat.options`` / ``chat.decline`` are the window's own ``Controller`` calls;
  a Stop also takes effect as its frame arrives (``worker_chat_arrival``: the turn marked stopping, its provider
  interrupted, no call started until the loop's Stop has run);
* ``chat.approve`` approves only the call whose digest the card showed (``worker_consent.approve``), through the
  window's approve operator; never while a merge holds the chat or a Stop is pending;
* ``chat.notice`` writes the lead's system row (a Stop's or an ending's notice) into the chat, the file's one writer;
* ``chat.consent`` applies a consent lease and answers once it holds (an older epoch is refused), on the reader
  thread as its frame arrives (``worker_chat_arrival``);
* ``chat.hold`` / ``chat.release``: the merge hold (``worker_chat_holds``);
* ``chat.calls``: its pending calls and one call's recorded outcome, its cards sent again (read only);
* ``chat.close`` stops a running turn, forwards its end, closes the chat (its file compacted: sealed) and releases
  the file (``worker_journal_owner.end_hosting``), answering ``closed: True`` and ``was_busy``; a chat not hosted
  here (already closed: a retry) answers ``closed: False``, so the lead takes it back;
* ``chat.prewarm`` starts (``start``, which also keeps one) or releases (``release``) the chat's runtime prewarm
  while the person writes in the lead's composer. An open starts one too (a message follows it) and asks Angelo for
  the chat's catalog when the open carried none; one no message adopted ends after ``runtime_prewarm.IDLE_SECONDS``.

Its usage and telemetry go to the lead (``worker_usage``, covered before the chat is made), its progress reports as
``news``, its live row's signs as ``signs``, its request's usage so far as ``running_usage`` (the live meter) and its
turn's rounds and clock as ``turn_facts`` (``worker_facts``). When the lead's connection ends, every hosted chat is
stopped and closed the same way before the worker exits (``worker_journal_owner.lead_gone``).
"""

import queue
import time
import uuid
from pathlib import Path

from . import worker_chat_events as events
from . import worker_chat_holds as holds, worker_chat_part
from . import worker_chat_seams as seams
from .worker_chat_arrival import Arrivals
from .worker_chat_holds import Deferred, refused as _refused

PUMP_SECONDS = 0.1     # The inbox wait while no hosted chat has work.
BUSY_SECONDS = 0.005   # ... and while one does (a turn, pending calls, queued events).
SIGNS_SECONDS = 1.0   # A busy chat's live-row signs go to the lead at most this often (and only when they changed).
RUNNING_SECONDS = 0.25   # ... and its request's usage so far (the lead's live meter), when it changed.
CREDENTIAL_WORDS = ("api_key", "apikey", "secret", "token", "password", "grant")
PART_LOST = "This worker could not load its part again after a script loaded another file, so it ended"
HELD = "The lead is merging this worker's part, so no change is approved until it ends; nothing ran."


class WorkerChatHost:
    def __init__(self, host, *, consent=None, ownership=None, transport=None, executor_factory=None, pump_tools=None,
                 outbox=None):
        from .worker_chat_hosted import hosted_class
        self.host, self.ui = host, host.ui
        self.consent = consent or seams.consent()
        self.ownership = ownership or seams.journal_owner()
        self.transport = transport   # None: the relay's launcher, looked up at each open (``seams.runtime_transport``).
        self.executor_factory = executor_factory
        self.pump_tools = pump_tools
        self.outbox = worker_chat_part.TurnEnds(outbox or events.Outbox(host.endpoint))   # Ends after saves.
        self.chats, self.paths, self._pending, self._waiting, self._versions = {}, {}, {}, {}, {}
        self._cursor = 0
        self._layout, self._save_after, self._signs, self._running = None, 0.0, {}, {}
        self.save_due = False   # A script saved the open file as another file (``worker_chat_part``).
        self._type = hosted_class(self.ui.Controller)
        self.on_reverse = None   # The relay's handler of ``{"rseq", "response"}`` frames (runtime_relay, C3/C5).
        self.usage_required = False   # ``worker_main``: usage forwarding installed, so every open must be covered.
        self.arrivals = Arrivals(self)   # Stop and consent at their frame's arrival (``worker_chat_arrival``).
        self._facts = {}

    # The loop ------------------------------------------------------------------------------------------------------

    def next_request(self):
        """The inbox's next item for ``WorkerHost.run``, pumping the hosted chats while none comes; None at the end,
        after every hosted chat was closed."""
        while True:
            if worker_chat_part.ended(self):   # Its part was lost after a script (``worker_script.contain``).
                self.shutdown(PART_LOST)
                return None
            try:
                self.pump()
            except Exception as error:  # noqa: BLE001 - as the window's guarded timer: reported, and it runs again.
                from .pump_guard import report
                report("WorkerChatHost.pump", error)
            try:
                item = self.host.inbox.get(timeout=BUSY_SECONDS if self.has_work() else PUMP_SECONDS)
            except queue.Empty:
                continue
            if item is None:
                self.shutdown("The lead's connection to this worker ended")
            return item

    def has_work(self):
        return any(chat.busy or chat.pending or not chat.events.empty() or chat.has_dispatchable_tools
                   for chat in list(self.chats.values())) or bool(self._waiting)

    def pump(self):
        """The window timer's calls for every hosted chat, then what changed goes to the lead."""
        chats = list(self.chats.values())
        for chat in chats:
            chat.pump_events()
            self._observe(chat)   # Calls a provider thread admitted meanwhile, before any of them runs.
        runnable = [chat for chat in chats if not chat.held]
        if runnable:
            self._cursor = self._pump_tools(runnable)
        for chat in chats:
            self._observe(chat)
            self._send_signs(chat)
            self._send_running(chat)
            self._send_facts(chat)
        saved = self.outbox.cut()   # The turn ends that came before this save step go after it (TurnEnds),
        holds.answer_holds(self)
        self._save_part()
        for chat in chats:   # and after this chat applied them: a turn end the lead sees here is over here too.
            chat.pump_events()
        self.outbox.release(saved)
        from . import runtime_prewarm   # A prewarm no message adopted is released after its idle bound.
        runtime_prewarm.observe(None, None, chats, time.monotonic())

    def _pump_tools(self, chats):
        if self.pump_tools is None:
            from .tool_pump import pump_controller_tools
            self.pump_tools = pump_controller_tools
        return self.pump_tools(chats, self._cursor, self.executor(), self.ui.document_key(), self.apply_mutation,
                               commit_mutation=self.commit_mutation)

    def apply_mutation(self, target, call):
        """The window's approval route for ``target``'s call (its approve operator, on the background window)."""
        with self.host.bpy.context.temp_override(window=self.ui.main_window()):
            self.host.bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])

    def commit_mutation(self, target, call):
        with self.host.bpy.context.temp_override(window=self.ui.main_window()):
            return self.host.bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id,
                                                               session_id=target.state["id"])

    def executor(self):
        if self.executor_factory is not None:
            return self.executor_factory()
        from .blender_tools import BlenderExecutor
        return BlenderExecutor(self.ui.main_window())

    def _observe(self, chat):
        session = chat.state["id"]
        mark = events.fingerprint(chat)
        if self._pending.get(session) != mark:
            self._pending[session] = mark
            self.outbox.put(events.event(session, "pending", {"calls": events.pending_calls(chat, self.consent),
                                                              "seq": events.pending_version(self._versions, chat)}))

    def _send_signs(self, chat):
        """The live row's signs (``turn_activity.signs`` of its provider) as ages, when they changed (``SIGNS_SECONDS``)."""
        self._send_changed(chat, "signs", events.signs(chat, time.monotonic()), self._signs, SIGNS_SECONDS)

    def _send_running(self, chat):
        """The usage so far of the chat's request in progress (``events.running``), when it changed
        (``RUNNING_SECONDS``): the lead's meter shows a worker's turn live, as its own turns."""
        self._send_changed(chat, "running_usage", events.running(chat), self._running, RUNNING_SECONDS)

    def _send_changed(self, chat, kind, data, last_sent, seconds):
        session, now = chat.state["id"], time.monotonic()
        last = last_sent.get(session)
        if data is not None and (last is None or last[1] != data and now - last[0] >= seconds):
            last_sent[session] = (now, data)
            self.outbox.put(events.event(session, kind, data))

    def news(self, session, done, steps=None):
        """``parallel_sessions.note_progress`` in this worker: a hosted chat's progress report goes to the lead."""
        if self.hosted(session) is None:
            return False
        self.outbox.put(events.event(session, "news", {"progress": {"done": int(done), "steps": steps}}))
        return True

    def _save_part(self):
        worker_chat_part.save_when_idle(self)

    def save_part_now(self):
        return worker_chat_part.save_now(self)

    def _send_facts(self, chat):
        """The chat's turn facts for the lead's ``worker_facts`` (``events.turn_facts``), when they changed."""
        data = events.turn_facts(chat)
        if data is not None and self._facts.get(chat.state["id"]) != data:
            self._facts[chat.state["id"]] = data
            self.outbox.put(events.event(chat.state["id"], events.TURN_FACTS, data))

    # Events --------------------------------------------------------------------------------------------------------

    def forward(self, chat, kind, data):
        """Any thread: a hosted chat's emit, as the lead's event (``worker_chat_events``)."""
        try:
            self.outbox.put(events.event(chat.state["id"], kind, data, chat.redact))
        except RuntimeError:   # Its data changed while read (another thread): the kind still goes, without it.
            self.outbox.put(events.event(chat.state["id"], kind, None))

    def journal_written(self, chat):
        self.outbox.put(events.event(chat.state["id"], "journal", {
            "seq": chat.journal_writes, "path": str(chat.store.path), "records": chat.store._writer.records}))

    def reverse(self, frame):
        """A ``{"rseq", "response"}`` frame (the relay's reverse request answered); dropped with no relay."""
        if self.on_reverse is not None:
            self.on_reverse(frame)

    # Requests ------------------------------------------------------------------------------------------------------

    def hosted(self, session):
        return self.chats.get(session) if isinstance(session, str) else None

    def handle(self, request):
        op = request.get("op")
        if not isinstance(op, str) or not op.startswith("chat.") or op[5:] not in OPS:
            raise ValueError("Unknown worker chat request")
        handler = getattr(self, "_" + op[5:])
        session = request.get("session")
        if op == "chat.open":
            return handler(_session_id(session), request)
        chat = self.hosted(session)
        if chat is None:
            if op == "chat.close":   # Already closed here (an answer the lead never got): the lead takes it back.
                return {"ok": True, "closed": False}
            raise _refused("This worker hosts no such chat", details=holds.NOT_HOSTED)
        try:
            answer = handler(chat, request)
        except Exception:
            self.resend_cards(chat)
            raise
        if isinstance(answer, dict) and answer.get("ok") is False:
            self.resend_cards(chat)
        return answer

    def resend_cards(self, chat):
        """After any refusal the chat's cards go to the lead again at the next pump: the lead may have spent a card's
        one Approve on the refused request (break-it F7)."""
        self._pending.pop(chat.state["id"], None)

    def _open(self, session, request):
        if session in self.chats:   # Open already (an answer the lead never got): its frames carry the new id.
            events.OPENS[session] = events.open_id(request) or events.OPENS.get(session)
            self.resend_cards(self.chats[session])
            return {"ok": True, "opened": False, "open_id": events.OPENS.get(session)}
        settings = request.get("settings") or {}
        if not isinstance(settings, dict) or _carries_credential(settings):
            raise _refused("A worker's chat settings never carry a credential")
        directory = _store_directory(request.get("store_directory"))
        path = directory / "sessions" / (session + ".json")
        transport = self.transport or seams.runtime_transport()
        if transport is seams.unavailable_transport:
            raise _refused(seams.NO_RUNTIME)   # No model child could start: never hosted half-way.
        if self.ownership.claim(session, str(path), request.get("resume") is True) is not True:
            raise _refused("This chat's file has another writer")
        from . import worker_journal_owner, worker_usage
        events.OPENS[session] = opened = events.open_id(request) or uuid.uuid4().hex   # C9: on its every frame.
        chat = None
        try:
            if worker_usage.installed():
                worker_usage.cover(str(directory))   # Before the chat is made: its stores forward, never write (C8).
            try:
                chat = self._type(str(directory), settings=dict(settings), session_id=session)
            except worker_journal_owner.JournalTaken as error:
                raise _refused("This chat's file has another writer (%s)" % error) from None
            if self.usage_required and not worker_usage.covered(chat):
                raise _refused("This chat's usage could not be forwarded to Angelo, so it is not hosted here")
            chat.chat_host, chat.runtime_transport = self, transport
            from .worker_refusals import seal
            seal(chat)   # Its file tools refused, as a lane chat's (its executor: ``worker_chat_executor``).
            chat.set_document(self.ui.document_key())
            chat.gpu_verdict = request.get("gpu_verdict")   # Angelo's offscreen verdict, checked by ``WorkerHost.gpu``.
            chat.checkpoints = _snapshot_store(chat, directory)
            chat.checkpoint_factory = lambda target=chat: self._checkpoint(target)
            models = request.get("models")
            chat.models = [model for model in models if isinstance(model, dict)] if isinstance(models, list) else []
            options = request.get("options")
            if isinstance(options, dict) and options:
                chat.set_options(**{str(key): value for key, value in options.items()})
            consent = request.get("consent")
            if isinstance(consent, dict):   # The open's answer is the lease's acknowledgement (worker_consent_lead).
                applied = self.consent.apply_consent(chat, consent.get("epoch"), consent.get("grants"))
                if applied.get("error_code"):
                    raise _refused(str(applied.get("error")), code=applied["error_code"])
        except BaseException:
            if chat is not None:
                worker_journal_owner.end_hosting(chat)
            self.ownership.release(session, str(path))
            raise
        self.chats[session], self.paths[session] = chat, str(path)
        self.ui._controllers[session] = chat   # The approve and commit operators find their chat here.
        if not chat.models:   # Its catalog through Angelo (relay, probes), forwarded as ``models`` to the mirror.
            chat.refresh_models()
        self._warm(chat)   # A message follows an open (the brief, a relaunch's): its runtime starts now.
        return {"ok": True, "opened": True, "path": str(path), "open_id": opened}

    def _warm(self, chat):
        """``runtime_prewarm.start`` for ``chat``'s next request, as the window's composer starts one: the launch
        goes through Angelo (C5) and the request adopts it (``runtime_prewarm.adopt``). Never raises."""
        from . import runtime_prewarm, runtime_relay
        if not runtime_relay.relayed():
            return False   # Only Angelo's launch service starts a worker's runtime (C5): never one of its own.
        try:
            launch_key = runtime_prewarm.key(chat)
            if chat.prewarmed is not None:
                chat.prewarmed.touched = time.monotonic()
            elif launch_key is not None:
                return runtime_prewarm.start(chat, launch_key, time.monotonic()) is not None
        except Exception as error:  # noqa: BLE001 - only a head start: the request launches its own.
            from .pump_guard import report_defect
            report_defect("a worker chat's runtime prewarm", error, chat.redact)
        return chat.prewarmed is not None

    def _prewarm(self, chat, request):
        """The lead's composer is being written in this chat (``worker_mirror_prewarm``): start (or keep) its
        runtime, or release it when the draft went."""
        if request.get("action") == "release":
            from . import runtime_prewarm
            runtime_prewarm.release(chat)
            return {"ok": True, "warm": False}
        return {"ok": True, "warm": bool(self._warm(chat))}

    def _send(self, chat, request):
        text, attachments = request.get("text"), request.get("attachments") or []
        if not isinstance(text, str) or not isinstance(attachments, list):
            raise _refused("A message is text and a list of attachments")
        def start():
            chat.attachments = [dict(reference) for reference in attachments if isinstance(reference, dict)]
            chat.send(text, host_request=request.get("host_request"))
        self.arrivals.send(chat, request, start)   # In the person's order with their Stops (C5).
        return {"ok": True}

    def _approve(self, chat, request):
        if chat.holding or chat.held:
            raise _refused(HELD)
        answer = self.consent.approve(chat, request.get("call_id"), request.get("digest"),
                                      lambda call: self.apply_mutation(chat, call))
        if answer.get("approved") is not True:   # Its cards are drawn again from the calls as they are (``handle``).
            raise _refused(str(answer.get("error")), code=answer.get("error_code") or "STALE_REVISION")
        return {"ok": True, "approved": True}

    def _decline(self, chat, request):
        chat.decline_tool(request.get("call_id"))
        return {"ok": True}

    def _stop(self, chat, request):
        reason = request.get("reason")
        if not self.arrivals.stops(chat, request):
            return {"ok": True, "stopped": False}   # Ordered before the Send whose turn runs now: not its Stop.
        chat.stop(reason if isinstance(reason, str) and reason else None)
        return {"ok": True}

    def _options(self, chat, request):
        options = request.get("options")
        if not isinstance(options, dict):
            raise _refused("Options are a dictionary")
        chat.set_options(**{str(key): value for key, value in options.items()})
        return {"ok": True, "options": dict(chat.state["options"])}

    def _consent(self, chat, request):
        """A consent queued before its chat was hosted (``worker_chat_arrival``); one for a hosted chat is applied and
        answered on the reader thread as it arrives (``consent_answer``)."""
        return self.consent_answer(chat, request)

    def consent_answer(self, chat, request):
        """Answered once applied: ``{ok, epoch}``, or ``ok: False`` with the refusal and the epoch held there. Any
        thread: the lease changes under the chat's lock."""
        answer = self.consent.apply_consent(chat, request.get("epoch"), request.get("grants"))
        answer = dict(answer, ok=not answer.get("error_code"))
        if not answer["ok"]:
            self.resend_cards(chat)
        return answer

    def _notice(self, chat, request):
        message = request.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            raise _refused("A notice is a message with its text")
        chat.emit("message", dict({key: value for key, value in message.items() if isinstance(key, str)},
                                  role="system"))
        return {"ok": True}

    def _calls(self, chat, request):
        return events.calls_answer(self, chat, request.get("call_id"))   # The lead's check of an unanswered Approve.

    def _hold(self, chat, request):
        del request
        return holds.hold(chat)

    def _release(self, chat, request):
        del request
        return holds.release(self, chat)

    def _close(self, chat, request):
        """``{ok, closed: True, seq, was_busy}``: ``was_busy`` when a turn was running, whose end (``finished``,
        interrupted) was forwarded before the close; a chat not hosted here answers ``closed: False`` (``handle``)."""
        reason = request.get("reason")
        self._save_part()   # As a pump would: the turn's end it releases follows the part's save (TurnEnds).
        was_busy = self._end(chat, reason if isinstance(reason, str) and reason else None)
        self.outbox.release()
        self.outbox.flush()   # The lead reads the file's last write before it takes the file back.
        return {"ok": True, "closed": True, "seq": chat.journal_writes, "was_busy": was_busy}

    # Hold answers --------------------------------------------------------------------------------------------------

    def defer(self, seq, response):
        """Whether ``response`` is answered later (a ``Deferred``); ``WorkerHost.run`` then sends nothing now."""
        if not isinstance(response, Deferred):
            return False
        self._waiting[seq] = response
        holds.answer_holds(self)
        return True

    # Ending --------------------------------------------------------------------------------------------------------

    def _end(self, chat, reason):
        from .worker_journal_owner import end_hosting
        session, was_busy = chat.state["id"], bool(chat.busy)
        try:
            if was_busy:
                chat.stop(reason)
                chat.forward_turn_end()   # Before the close silences its emits: the lead's mirror ends its turn too.
            end_hosting(chat)   # Compacts the file (its last write, the C7 seal), then releases it.
            return was_busy
        finally:
            self.chats.pop(session, None)
            self._pending.pop(session, None)
            self._signs.pop(session, None)
            self._facts.pop(session, None)
            if self.ui._controllers.get(session) is chat:
                del self.ui._controllers[session]
            self.ownership.release(session, self.paths.pop(session, ""))

    def shutdown(self, reason):
        for chat in list(self.chats.values()):
            try:
                self._end(chat, reason)
            except Exception as error:  # noqa: BLE001 - every chat is closed; one failing never keeps another open.
                print("Angelo worker: a hosted chat did not close:", type(error).__name__, error)
        holds.answer_holds(self)
        self.outbox.close()

    def _checkpoint(self, chat):
        """A request snapshot of the part, into the chat's worker snapshot store (as ``WorkerHost.checkpoint``)."""
        from .checkpoint_policy import clean_policy
        policy = clean_policy(getattr(chat, "_turn_checkpoint_policy", None))
        record = self.host.checkpoint({"root": str(chat.checkpoints.root), "session": chat.checkpoints.session_id,
                                       "capture_mode": policy["capture_mode"], "purpose": "request"})
        from . import snapshot_service
        snapshot_service.after_capture(chat, record)
        return record


OPS = frozenset({"open", "send", "approve", "decline", "stop", "options", "consent", "notice", "hold", "release",
                 "close", "prewarm", "calls"})


def _session_id(value):
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        raise _refused("A worker's chat is named by its chat id") from None


def _store_directory(value):
    """The lead's state folder: absolute and existing; a path naming its ``sessions`` folder means its parent."""
    path = Path(value) if isinstance(value, str) and value else None
    if path is None or not path.is_absolute() or not path.is_dir():
        raise _refused("A worker's chat opens only on the lead's own state folder")
    return path.parent if path.name == "sessions" else path


def _snapshot_store(chat, directory):
    from .checkpoints import CheckpointStore
    from .worker_routing import worker_snapshot_session
    return CheckpointStore(str(directory), worker_snapshot_session(chat.state["id"]))


def _carries_credential(value):
    if isinstance(value, dict):
        return any(any(word in str(key).lower().replace("-", "_") for word in CREDENTIAL_WORDS)
                   or _carries_credential(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_carries_credential(item) for item in value)
    return False
