# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's side of a worker chat hosted in its worker (design A′, contracts C5 Stop and C6): the consent lease it
sends, the Approve it relays and the Stop it enforces. The worker side is ``worker_consent``. No bpy.

**Consent.** The lead computes each hosted chat's grants with the window's own rules, read from that chat's mirror
(``grants``): Auto only when its approval is Auto (``clean_options`` applies RequireAskForEdits), its Trust scripts
for this chat or its lead's (``TrustedScripts.chat_consents``, which folds in ``AllowAutoApproval``), Always allow
scripts (``always_allow.token``) — and nothing at all while its connection is gone (key removal, Team sign-out) or the
chat needs approval. Each change gets a new, higher epoch and goes out as ``chat.consent``. Every place standing
consent can change calls ``changed`` (the approval menu, the trust dialog, the Always allow toggle, Team sign-out and
close, connection removal); anything else that moves it (a policy that arrives, a document change, a New Chat in the
lead) is found by ``tick`` on the UI timer. Nothing here waits on a worker: ``changed`` sends and returns, and a
**revocation** (a send that narrows any lease the worker may hold: the acknowledged one, or one sent since and still
in flight or uncertain, ``worker_consent_held``) not acknowledged within ``ACK_SECONDS``
(``tick``) kills the chat's model child (``runtime_service.kill``), sends ``chat.stop`` and marks the chat as needing
approval: it gets no grants until the person's next message in it succeeds in re-sending them (``resume_later``: on
that message's lane, never the window's frame). A widening that is not acknowledged is sent again later; until a newer
lease is acknowledged it counts as maybe held, so withdrawing it is a revocation.

**Approve.** Cards are drawn from the worker's ``pending`` summaries; ``note_pending`` keeps each one's digest and the
chat's Stop generation, and ``drew`` the digest each card showed when it was drawn. Approve sends that drawn digest,
once (a repeated click sends nothing), never for a card whose call changed since it was drawn, never after a Stop
began or for a card drawn before one. A card no chat area here ever drew (a tester's or add-on's ``angelo.approve``
from the lead) sends the mirror's digest, as a window chat's. The worker decides (``worker_consent.check_approval``).

**Stop.** ``stop`` sends ``chat.stop`` without waiting for it, so a wedged worker never blocks the window; if the turn
has not ended ``STOP_SECONDS`` later, ``tick`` kills the model child, which the lead owns. Approvals stay refused
until the turn has ended.

Sends run on ``spawn`` (a daemon thread by default; they touch no Blender data) because a worker may not answer.
The seams (``bind``) are the chat-op transport, the runtime kill, the clock and the connection check, so tests use
fakes and integration wires the lanes. With ``ANGELO_WORKER_CHAT_HOST=0`` no chat is registered and nothing here runs.
"""

import itertools
import sys
import threading
import time

from .worker_consent_held import ACK_SECONDS, Send as _Send, maybe_taken, narrows, possible, superseded
from .worker_mirror_pins import auto as _auto, route as _route   # The lead's own record, never the file's.
from .ui_text import n_, notice

STOP_SECONDS = 2.0   # Contract C5: a turn not ended this long after Stop has its model child killed.
NEEDS_APPROVAL = n_("Angelo stopped this chat's turn: its worker did not confirm a change of approval settings in "
                    "time, so until your next message it asks before running anything.")
STOPPING = n_("This chat is stopping; nothing was approved.")
NOT_SHOWN = n_("This card is no longer current; nothing was approved.")
REVIEW_FIRST = n_("Read the script shown in the chat, then click Approve to run it")

_hooks = {"send": None, "kill": None, "clock": time.monotonic, "connection": None, "spawn": None, "wait": None}
_epochs = itertools.count(time.time_ns() // 1000)   # Only forward; a later run of Angelo starts above this one.
_hosted = {}          # session -> its mirror Controller (registered on chat.open)
_acked = {}           # session -> (epoch, grants) the worker acknowledged last
_latest = {}          # session -> the grants sent last
_inflight = {}        # session -> [_Send]
_uncertain = {}       # session -> [(epoch, grants)] sent, unacknowledged, maybe held (``worker_consent_held``)
_needs = set()        # sessions whose consent was lost (``NEEDS_APPROVAL``)
_stops = {}           # session -> {"deadline", "killed"}
_generations = {}     # session -> Stops so far: a card drawn before one is stale
_drawn = {}           # session -> {call id: (name, digest, generation)}: the cards its ``pending`` events carry
_shown = {}           # session -> {call id: digest}: what each card last drew (``drew``); "" once spent, never undrawn
_nonces = {}          # id(token) -> (token, nonce): one nonce per consent token, kept while it is granted
UNKNOWN = {"auto": True, "scripts": "unknown", "always_allow": "unknown"}   # A lost lease: every send narrows it.


def enabled(environ=None):
    """Design A′ is on unless ``ANGELO_WORKER_CHAT_HOST=0`` (``worker_chat_seams.enabled``)."""
    from .worker_chat_seams import enabled as switch
    return switch(environ)


def bind(**hooks):
    """Set seams: ``send(session, request, timeout) -> answer``, ``kill(session)``, ``clock()``, ``connection(
    controller) -> bool``, ``spawn(fn)``, ``wait(record, seconds) -> bool``. Unknown names are refused."""
    unknown = set(hooks) - set(_hooks)
    if unknown:
        raise ValueError("Unknown consent seams: %s" % ", ".join(sorted(unknown)))
    _hooks.update(hooks)


def reset():
    """Tests and add-on reload: forget every chat and seam."""
    for table in (_hosted, _acked, _latest, _inflight, _uncertain, _stops, _generations, _drawn, _shown, _nonces):
        table.clear()
    _needs.clear()
    _hooks.update(send=None, kill=None, clock=time.monotonic, connection=None, spawn=None, wait=None)


def _now():
    return _hooks["clock"]()


def _call(session, request, timeout):
    send = _hooks["send"]
    if send is None:
        return {"error": "No worker channel"}  # i18n: internal answer, never shown
    try:
        answer = send(session, request, timeout)
    except Exception as error:   # A lost, closed or timed-out channel is an unanswered request.
        from .worker_reasons import answer_of   # Its kind and refusal code, for ``worker_mirror_ops.why``.
        return dict(answer_of(error), unknown=maybe_taken(error))
    return answer if isinstance(answer, dict) else {"error": "Not an answer"}  # i18n: internal answer, never shown


def _spawn(function):
    spawn = _hooks["spawn"]
    if spawn is not None:
        return spawn(function)
    threading.Thread(target=function, daemon=True, name="angelo-worker-consent").start()


def _kill(session):
    kill = _hooks["kill"]
    try:
        if kill is None:
            from . import runtime_service
            kill = runtime_service.kill
        kill(session)
    except Exception as error:   # Nothing left to kill, or no runtime service: the chat is still marked.
        print("Angelo: the worker chat's model could not be stopped:", error)


# Registration -------------------------------------------------------------------------------------------------------

def register(controller):
    """``chat.open`` of a hosted chat: remember its mirror and return the ``consent`` field the open carries (C2).
    The open's answer is its acknowledgement, so a chat that opened holds this lease."""
    session = controller.state["id"]
    _hosted[session] = controller
    grants_now = grants(controller)
    epoch = next(_epochs)
    _acked[session], _latest[session] = (epoch, grants_now), grants_now
    _uncertain.pop(session, None)   # A new open holds exactly this lease.
    _prune_nonces()
    return {"epoch": epoch, "grants": grants_now}


def forget(session):
    """``chat.close`` (park, switch, end): nothing more is sent or approved for it. A lost consent stays marked."""
    for table in (_hosted, _acked, _latest, _inflight, _uncertain, _stops, _drawn):
        table.pop(session, None)
    _spend(session)   # Across a reopen too.
    _generations[session] = _generations.get(session, 0) + 1
    _prune_nonces()


def hosted(controller):
    """Whether ``controller`` is a registered hosted chat's mirror."""
    return controller is not None and _hosted.get(controller.state.get("id")) is controller


def needs_approval(session):
    return session in _needs


# Consent ------------------------------------------------------------------------------------------------------------

def _nonce(token):
    entry = _nonces.get(id(token))
    if entry is None or entry[0] is not token:
        entry = _nonces[id(token)] = (token, "%x-%x" % (next(_epochs), id(token)))
    return entry[1]


def _prune_nonces():
    granted = {nonce for grants_now in _latest.values() for nonce in grants_now.values() if isinstance(nonce, str)}
    for key in [key for key, (_token, nonce) in _nonces.items() if nonce not in granted]:
        del _nonces[key]


def _connected(controller):   # Whether the connection Angelo pinned for ``controller`` (``_route``) is usable.
    pinned, check = _route(controller), _hooks["connection"]
    if pinned is None or check is not None:   # A mirror never pinned, or its worker reported another route: never.
        return pinned is not None and bool(check(controller))
    try:
        mode, identity = pinned
        if identity:
            from .connection_access import find_connection   # Personal (if policy allows) or a live Team connection.
            return find_connection(identity) is not None
        # No connection: the chat runs only on its provider's saved key, as the window's ``connection_available``
        # and the runtime's key lookup (``worker_chat_wiring.key_of``) decide (D9: a lane chat inherits trust then).
        from .connection_access import mode_key_usable   # A personal key: the policy refuses it as a connection.
        ui = sys.modules.get(__package__ + ".ui")
        return ui is not None and mode_key_usable(mode, getattr(ui, "_memory_keys", {}))
    except Exception:   # No window state to check: nothing is granted.
        return False


def grants(controller):
    """What ``controller``'s worker may do without asking now (module docstring)."""
    session = controller.state.get("id")
    if (session in _needs or getattr(controller, "connection_error", False) or not _connected(controller)
            or not _auto(controller)):   # The lead's own options: the confirmed ones and every change still waiting.
        return {"auto": False}
    granted = {"auto": True}
    consents = controller.chat_consents()
    if consents:
        granted["scripts"] = _nonce(consents[0])
    if controller.always_allow_scripts:
        from . import always_allow
        token = always_allow.token()
        if token is not None:
            granted["always_allow"] = _nonce(token)
    return granted


def _send_consent(session, grants_now, now):
    held = possible(_acked.get(session, (None, {"auto": False})), _inflight.get(session, ()),
                    _uncertain.get(session, ()))   # Acknowledged, in flight or uncertain: it may hold any of them.
    record = _Send(session, next(_epochs), grants_now, any(narrows(one, grants_now) for one in held), now)
    _latest[session] = grants_now
    _inflight.setdefault(session, []).append(record)

    def run():
        record.answer = _call(session, {"op": "chat.consent", "session": session, "epoch": record.epoch,
                                        "grants": grants_now}, ACK_SECONDS)
        record.answered = _now()
        record.done.set()
    _spawn(run)
    return record


def sync(now=None):
    """Send every hosted chat whose grants changed its new lease; the records sent."""
    if not _hosted or not enabled():
        return []
    now = _now() if now is None else now
    sent = []
    for session, controller in list(_hosted.items()):
        current = grants(controller)
        if current != _latest.get(session) and not _opening(session):
            sent.append(_send_consent(session, current, now))
    _prune_nonces()
    return sent


def _opening(session):
    """Whether a ``chat.open`` of ``session``, which carries its lease (``register``), is unanswered on any path
    (``worker_chat_lifecycle.answering``); the next ``sync`` sends the change."""
    from . import worker_chat_lifecycle
    return worker_chat_lifecycle.answering(session)


def changed():
    """A consent toggle in the window: send the new leases, without waiting (no window frame waits on a worker).
    A revocation still binds within ``ACK_SECONDS``: ``tick`` (the UI timer) stops and marks a chat whose worker has
    not acknowledged it by then (``settle``), and its mirror already shows the narrower options."""
    sync()
    settle()


def resume_later(session):
    """The person's next message in a chat marked ``needs_approval``, without waiting: its real grants are sent
    again now, and the callable returned (run on the message's lane, ``worker_mirror_ops``) is True only once the
    worker acknowledged them in time, so the message goes only then. Unacknowledged, ``settle`` marks it again."""
    if _hosted.get(session) is None:
        return lambda: False
    _needs.discard(session)
    _latest.pop(session, None)
    records = [record for record in sync() if record.session == session]
    if not records:
        return lambda: False

    def acknowledged(record=records[-1]):
        return record.done.wait(ACK_SECONDS) and record.in_time() and _acknowledged(record)
    return acknowledged


def _wait(record):
    wait = _hooks["wait"]
    if not (wait(record, ACK_SECONDS) if wait is not None else record.done.wait(ACK_SECONDS)):
        record.expired = True   # The whole deadline passed unanswered.


def resume(session):
    """The person's next message in a chat marked ``needs_approval``: its real grants are sent again; True once the
    worker acknowledged them (only then may the message go to the worker), else it stays marked."""
    controller = _hosted.get(session)
    if controller is None:
        return False
    _needs.discard(session)
    _latest.pop(session, None)
    records = sync()
    for record in records:
        if not record.done.is_set():
            _wait(record)
    settle()
    if session in _needs or _inflight.get(session) or _acked.get(session, (None, None))[1] != grants(controller):
        _needs.add(session)
        return False
    return True


def _acknowledged(record):
    answer = record.answer or {}
    return answer.get("epoch") == record.epoch and not answer.get("error") and not answer.get("error_code")


def settle(now=None):
    """Main thread: take in the workers' answers; a revocation unanswered after ``ACK_SECONDS``, or refused, stops its
    chat (``_lost``). A newer acknowledged lease supersedes an older one's answer."""
    now = _now() if now is None else now
    for session, records in list(_inflight.items()):
        keep = []
        for record in sorted(records, key=lambda item: item.epoch):
            held = _acked.get(session, (-1, None))[0]
            if record.in_time() and _acknowledged(record):
                if record.epoch > held:
                    _acked[session] = (record.epoch, record.grants)
                    _uncertain[session] = superseded(_uncertain.get(session, ()), record.epoch)
            elif record.done.is_set() or record.expired or now - record.started >= ACK_SECONDS:
                answered = (record.answer or {}).get("epoch")
                newer = max(held, answered if type(answered) is int else -1) > record.epoch
                if not newer and record.maybe_held():   # Accounted for until a newer acknowledged lease.
                    _uncertain.setdefault(session, []).append((record.epoch, record.grants))
                if record.revoking and not newer:
                    _lost(session)
                elif not newer and _latest.get(session) == record.grants:
                    _latest.pop(session, None)   # A widening not acknowledged: sent again by a later sync.
            else:
                keep.append(record)
        if keep:
            _inflight[session] = keep
        else:
            _inflight.pop(session, None)
        if not _uncertain.get(session):
            _uncertain.pop(session, None)


def _lost(session):
    """A revocation the worker did not acknowledge: fail closed (module docstring)."""
    _latest.pop(session, None)   # The empty lease goes out again at the next sync.
    _acked[session] = (_acked.get(session, (-1, None))[0], UNKNOWN)   # Anything may still be held there.
    if session in _needs:
        return   # Already stopped and marked: a worker still not answering is not stopped twice.
    _needs.add(session)
    _kill(session)
    controller = _hosted.get(session)
    if controller is not None:
        stop(controller, NEEDS_APPROVAL, killed=True)
        controller.emit("message", {"role": "system", **notice(NEEDS_APPROVAL)})


# Approve and Stop ---------------------------------------------------------------------------------------------------

def note_pending(session, calls):
    """A ``pending`` event (C3): the cards the chat draws now. Ignored while a Stop is under way."""
    if session not in _hosted or session in _stops:
        return
    generation = _generations.get(session, 0)
    _drawn[session] = {call["id"]: (call.get("name", ""), call["digest"], generation)
                       for call in calls if isinstance(call, dict) and isinstance(call.get("id"), str)
                       and isinstance(call.get("digest"), str)}


def note_call(session, call):
    """One card's digest from an answer about that call only (``worker_mirror_settle``); the other cards stand."""
    if (session in _hosted and session not in _stops and isinstance(call, dict) and isinstance(call.get("id"), str)
            and isinstance(call.get("digest"), str)):
        _drawn.setdefault(session, {})[call["id"]] = (call.get("name", ""), call["digest"], _generations.get(session, 0))


def drew(controller, call_id):
    """The card drawing (``chat_card_draw``): remember the digest of the call this card shows now, which is what its
    Approve sends (S3); a hosted chat's only."""
    if hosted(controller):
        call = controller.pending.get(call_id)
        digest = getattr(call, "approval_digest", "") if call is not None else ""
        _shown.setdefault(controller.state["id"], {})[call_id] = digest


def _spend(session, call_id=None):
    """A card drawn here, once answered, stopped or closed (all its chat's cards when ``call_id`` is None), never takes
    the mirror's digest: a late click on its old drawing sends nothing. One never drawn may be sent again (its answer
    lost, or refused and the card sent again)."""
    cards = _shown.setdefault(session, {})
    cards.update(dict.fromkeys([key for key in cards if call_id in (None, key)], ""))


def drawn(session, call_id):
    """``(name, digest)`` of the card shown for ``call_id``, or None."""
    entry = _drawn.get(session, {}).get(call_id)
    return entry[:2] if entry is not None else None


def approve(controller, call_id, digest=None):
    """The person's Approve on a hosted chat's card: ``chat.approve`` with the digest that card showed when it was
    drawn (``drew``; ``digest`` when the caller holds it), sent once. A card whose call changed since it was drawn
    (a new digest: new arguments, precondition or recovery review) is not approved: its redraw shows the new one.
    None when sent, else why nothing was sent."""
    session = controller.state.get("id")
    if not hosted(controller):
        return NOT_SHOWN
    if session in _stops:
        return STOPPING
    entry = _drawn.get(session, {}).get(call_id)   # Never drawn here: the mirror's own digest (module docstring).
    shown = digest if digest is not None else _shown.get(session, {}).get(call_id, entry and entry[1])
    call = controller.pending.get(call_id)
    if (entry is None or entry[2] != _generations.get(session, 0) or not isinstance(shown, str) or not shown
            or entry[1] != shown or getattr(call, "approval_digest", None) != shown):
        return NOT_SHOWN
    _drawn[session].pop(call_id, None)   # A repeated click finds nothing to send.
    _spend(session, call_id)
    request = {"op": "chat.approve", "session": session, "call_id": call_id, "digest": shown}
    _show(controller, "approval_sent", call_id)
    _spawn(lambda: _answered(controller, request, _call(session, request, _answer_seconds())))
    return None


def approve_operator(operator, controller, call_id):
    """``angelo.approve`` on a hosted chat's card. A script asks for its exact-code review first, as in any chat."""
    from .ui_text import rpt_
    shown, call = drawn(controller.state.get("id"), call_id), controller.pending.get(call_id)
    reviewed = (call is not None and call_id in controller.reviewed_calls   # Reviews are of one (id, digest): a
                and getattr(call, "reviewed_script", None))                 # changed digest is a new call object.
    if shown is not None and shown[0] == "angelo_execute_python" and not reviewed:
        operator.report({'INFO'}, rpt_(REVIEW_FIRST))
        import bpy
        bpy.ops.angelo.review_tool(call_id=call_id, session_id=controller.state["id"])
        return {'CANCELLED'}
    reason = approve(controller, call_id)
    if reason is not None:
        operator.report({'INFO'}, rpt_(reason))
    return {'CANCELLED'} if reason is not None else {'FINISHED'}


def decline(controller, call_id):
    """The person's Decline: ``chat.decline``, even while stopping (it runs nothing)."""
    session = controller.state.get("id")
    if hosted(controller):
        _drawn.get(session, {}).pop(call_id, None)
        _spend(session, call_id)
        request = {"op": "chat.decline", "session": session, "call_id": call_id}
        _show(controller, "declined", call_id)
        _spawn(lambda: _answered(controller, request, _call(session, request, _answer_seconds())))


def _answer_seconds():
    """An Approve's or Decline's answer bound: the worker answers once the edit ran, so as any asynchronous op's."""
    from .worker_mirror_ops import ANSWER_SECONDS
    return ANSWER_SECONDS


def _show(controller, name, call_id):
    """The card shows the person's answer at once (``worker_mirror_opens``)."""
    show = getattr(controller, name, None)
    if callable(show):
        show(call_id)


def _answered(controller, request, answer):
    """Any thread: the worker's answer goes to the mirror, which shows a refusal (never dropped)."""
    report = getattr(controller, "op_answered", None)
    if callable(report):
        report(request["op"], request.get("call_id"), answer)


def stop(controller, reason=None, killed=False):
    """The person's (or Angelo's) Stop of a hosted chat: ``chat.stop`` now, the kill ``STOP_SECONDS`` later unless
    its turn ended (``tick``). Every card drawn before it is stale."""
    session = controller.state.get("id")
    if session not in _hosted:
        return
    _generations[session] = _generations.get(session, 0) + 1
    _drawn.pop(session, None)
    _spend(session)   # Every card drawn before the Stop.
    record = _stops.get(session)
    if record is None:
        _stops[session] = {"deadline": _now() + STOP_SECONDS, "killed": killed}
    else:
        record["killed"] = record["killed"] or killed
    from .worker_mirror_ops import order   # The person's order: the worker never runs a Send this Stop follows.
    request = {"op": "chat.stop", "session": session, "reason": reason or "", "lead_seq": order()}
    _spawn(lambda: _call(session, request, STOP_SECONDS))


def stopping(session):
    return session in _stops


def tick(now=None):
    """UI timer: the mirrors' asynchronous answers (``worker_mirror_ops.reconcile``), then settle answers, send
    changed leases, and kill the model child of a turn not ended after Stop."""
    from . import worker_mirror_ops
    worker_mirror_ops.reconcile()
    if not _hosted and not _stops:
        return
    now = _now() if now is None else now
    settle(now)
    sync(now)
    for session, record in list(_stops.items()):
        controller = _hosted.get(session)
        if controller is None or not controller.busy:
            _stops.pop(session, None)   # Its turn ended (or the chat closed): approvals are drawn afresh.
        elif not record["killed"] and now >= record["deadline"]:
            record["killed"] = True
            _kill(session)
