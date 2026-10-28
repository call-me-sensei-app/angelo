# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What of a lead's workers its model was told: finished turns (the status wait's baseline) and approval cards no
window shows (``approval_no_window_shows``). Facts only: nothing here says what to do with them.

A **finish** is one turn of a worker chat that ended (``worker_sessions.finished``), identified by the chat and its
turn (``began``: every message delivered to a worker chat, its brief included, starts a new turn,
``worker_facts.turn_started``), so a resumed worker's next finish is news again. A merged or merging part is never
news, and neither is a part whose start or brief failed (``start_failed``: the record status' ``failed_to_start`` and
the merge read). A **card** is a worker chat's set of pending approval calls while ``worker_sessions.awaiting_person``
holds for it.

**Told** means one of two explicit fields reached the model, and nothing else does (break-it OVH6, by design):
- ``FIELD`` (``workers_finished_since_status``): each finish not yet told, added by ``noted`` to the final copy of a
  payload the lead's model reads (``ROUTES``), after any excerpt, so what it lists is exactly what is written;
- ``approval_no_window_shows`` of a status wait's answer (``worker_status``): the cards it lists.
History rows (a status answer's own rows, a handoff, a voice session's history) tell nothing: they may be excerpted or
re-sent, so a finish they show stays news until a field names it. ``noted`` consumes nothing: it leaves what its copy
names as this thread's packed set, the route keeps it with that payload (``packed``) and calls ``acknowledge`` only
after the write; a failed or withheld write leaves everything news, computed afresh from state for the next payload.

Routes (every place a lead's results or history are written to its model; ``test_native_worker_news_routes`` finds
each call site from the source, so a new one cannot bypass this):
  * Codex dynamic tools: ``providers.tool_content`` -> ``codex_delivery`` (after ``rpc.respond``; an error payload that
    replaced the copy takes nothing);
  * Claude direct tools: ``claude_direct._packed`` -> ``claude_direct.answer`` (after the MCP reply write);
  * Claude typed plans: ``providers._run_plans`` stages a continuation's copy (``staged``), told by ``round_written``
    (``provider_usage.record_runtime_round``, after the round's write, either runtime) whatever the answer;
  * every request's opening prompt (``controller._context_blocks``, handoff compact or full included): ``prompt_news``
    adds the field and stages it for the request's first round;
  * Codex typed plans and voice sessions: edits only / no worker tools; they write no field and tell nothing.

Evidence (LANE-overheads.md, the C1 and C2 capped early runs): a worker that finished while the lead wrote the
request that waits was announced by nothing (4 of 8 trials, 11-23 s idle); every trial spent a status-only request
(2.8-8.2 s) to learn a worker finished.

Any thread (a payload is packed where it is delivered): plain reads of the session tables and each chat's flags under
one lock, no bpy; a failure to read is no news, never a failed result.
"""

import json
import threading

FIELD = "workers_finished_since_status"   # Model-visible, never translated: "<number> <name>" of each.
UNSEEN = "approval_no_window_shows"        # Model-visible (worker_status.UNSEEN): worker numbers.
CARRY = "_worker_news"                     # What a packed Claude answer named, until its reply is written.
_lock = threading.Lock()
_turn = {}        # worker chat id -> its turn count (``began``)
_told = {}        # lead chat id -> {(worker chat id, turn)} finishes a field told its model
_told_cards = {}  # lead chat id -> {(worker chat id, frozenset of pending approval call ids)} cards a field told
_staged = {}      # lead chat id -> (request generation, finishes, cards) the next model round it writes names
_out = threading.local()   # .pending: (lead chat id, finishes, cards) the copy ``noted`` last packed on this thread


def began(session):
    """A message is being delivered to worker chat ``session``: its next finish is a new one."""
    with _lock:
        _turn[session] = _turn.get(session, 0) + 1


def start_failed(lead_id, number):
    """Worker ``number`` of ``lead_id``'s start or brief failed: the record status (``failed_to_start``) and the merge
    (``merge_blocker``) read too."""
    from . import worker_sessions
    return "%s/%d" % (lead_id, number) in worker_sessions.failures


def _lead_id(context):
    host = getattr(context, "host", None) or context
    return (getattr(host, "state", None) or {}).get("id")


def _finishes(lead_id):
    """``lead_id``'s finished worker turns, merged and never-started ones left out, in start order: [(session, turn)]."""
    from . import parallel_sessions, worker_sessions
    found = []
    for session in list(worker_sessions._started.get(lead_id, ())):
        if session in parallel_sessions._merged or session in parallel_sessions._merging:
            continue
        part = worker_sessions._parts.get(session)
        if part is not None and start_failed(lead_id, part[0]):
            continue
        if worker_sessions.finished(session):
            found.append((session, _turn.get(session, 0)))
    return found


def cards(lead_id):
    """``lead_id``'s workers that wait for the person now: [(session, frozenset of pending approval call ids)]."""
    try:
        from . import ui, worker_sessions
    except ImportError:   # No Blender: no chat holds a card.
        return []
    found = []
    for session in list(worker_sessions._started.get(lead_id, ())):
        chat = ui._controllers.get(session)
        if chat is None or worker_sessions.finished(session) or not worker_sessions.awaiting_person(session):
            continue
        found.append((session, frozenset(key for key, call in list(chat.pending.items())
                                         if getattr(call, "approval", False))))
    return found


def fresh(lead_id):
    """Worker chats of ``lead_id`` whose current finish no field told its model."""
    with _lock:
        told = _told.get(lead_id, set())
        return [session for session, turn in _finishes(lead_id) if (session, turn) not in told]


def unshown_cards(lead_id):
    """Worker chats of ``lead_id`` holding a card no delivered ``approval_no_window_shows`` named."""
    with _lock:
        told = _told_cards.get(lead_id, set())
        return [session for session, card in cards(lead_id) if (session, card) not in told]


def _news(lead_id, copy):
    """``(labels, finishes, cards)`` a final copy names: the untold finishes ``FIELD`` will list, and the cards its
    ``approval_no_window_shows`` lists (their current identity)."""
    from . import worker_sessions
    told = _told.get(lead_id, set())
    news = [f for f in _finishes(lead_id) if f not in told]
    labels = ["%d %s" % worker_sessions._parts[s][:2] for s, _turn_ in news if s in worker_sessions._parts]
    listed = set()   # The field where a route's copy carries it: whole, wrapped (``result``) or in a plan's operations.
    for part in [copy, copy.get("result")] + list(copy.get("operations") or ()) if isinstance(copy, dict) else ():
        listed.update(part.get(UNSEEN) or () if isinstance(part, dict) else ())
    shown = {(s, c) for s, c in cards(lead_id) if worker_sessions._parts.get(s, (None,))[0] in listed} if listed else set()
    return labels, set(news), shown


def noted(context, result, raw=None):
    """``result`` (the final copy of a lead's payload) with ``FIELD`` for each finish no field told its model;
    unchanged otherwise, and always for a chat that started no worker (no bytes). What the copy names is this
    thread's packed set (``packed``); nothing is consumed. ``raw`` is accepted for the routes' call shape, unused."""
    _out.pending = None
    if not isinstance(result, dict):
        return result
    lead_id = _lead_id(context)
    from . import worker_sessions
    if lead_id is None or lead_id not in worker_sessions._started:
        return result
    try:
        with _lock:
            labels, finishes, shown = _news(lead_id, result)
            _out.pending = (lead_id, finishes, shown) if finishes or shown else None
    except Exception:   # noqa: BLE001 - news is a convenience; the status call still says it.
        _out.pending = None
        return result
    return dict(result, **{FIELD: labels}) if labels else result


def prompt_news(context):
    """The opening prompt's news line for a lead with untold finishes ("" otherwise), staged for the request's first
    round: a handoff's rows tell nothing, so a finish they show reaches the model named here (break-it OVH6)."""
    copy = noted(context, {})
    stage(context, packed())
    return ("\n\nWorkers (untrusted data): " + json.dumps(copy, separators=(",", ":"))) if copy else ""


def packed():
    """What the copy ``noted`` last packed on this thread names (None: nothing), taken: the route keeps it with that
    payload and hands it to ``acknowledge`` once the payload is written."""
    pending, _out.pending = getattr(_out, "pending", None), None
    return pending


def acknowledge(pending):
    """The payload that carried ``pending`` (``packed``) was written to the lead's runtime: what it named is told."""
    if pending:
        with _lock:
            _told.setdefault(pending[0], set()).update(pending[1])
            _told_cards.setdefault(pending[0], set()).update(pending[2])


def stage(context, pending):
    """``pending`` goes in the next model round this request writes (its opening prompt, a continuation): told when
    that round is written (``round_written``), never by a later request's round."""
    host = getattr(context, "host", None) or context
    lead_id = _lead_id(context)
    if not pending or lead_id is None:
        return
    generation = getattr(host, "_request_generation", None)
    with _lock:
        held = _staged.get(lead_id)
        finishes, held_cards = (held[1], held[2]) if held and held[0] == generation else (set(), set())
        _staged[lead_id] = (generation, finishes | set(pending[1]), held_cards | set(pending[2]))


def staged(context, copy):
    """``copy`` (``noted``'s), with what it names staged for the next model round (``stage``)."""
    stage(context, packed())
    return copy


def round_written(context):
    """A model round of this request was written to the runtime (``provider_usage.record_runtime_round``, after the
    write): what was staged for it is told, whatever the answer turns out to be; a stale request's staging is dropped
    (its finishes stay news, computed afresh for the next payload)."""
    host = getattr(context, "host", None) or context
    lead_id = _lead_id(context)
    with _lock:
        held = _staged.pop(lead_id, None)
        if held and held[0] == getattr(host, "_request_generation", None) and not getattr(context, "cancelled", False):
            _told.setdefault(lead_id, set()).update(held[1])
            _told_cards.setdefault(lead_id, set()).update(held[2])


def withheld():
    """Nothing ``noted`` packed on this thread will be delivered: it stays news."""
    _out.pending = None


def forget(lead_id=None):
    """Drop what a lead was told (all leads when None): a reset, or a lead that left Angelo."""
    with _lock:
        for table in (_told, _told_cards, _staged):
            table.clear() if lead_id is None else table.pop(lead_id, None)
