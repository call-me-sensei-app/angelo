# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A lead whose turn ended while its workers still ran hears, once, when they have all ended: Angelo continues the
lead's chat with a factual worker-done event (resume-lead design (i), approved by the maintainer for 1.0.6).

Evidence: a lead that ended its turn while its workers built left their finished parts in their own files, and
nothing told it when they finished; a next turn came only when the person sent one (ROUNDS-AND-ASSEMBLY, the dev
checks of 2026-10-25).

- A batch opens at a start in a turn the person asked for (``worker_sessions.execute``); a start in Angelo's own turn
  opens none, so events never chain without the person (break-it EVENT H-3), and its starts count on the person's
  request's Auto budget (``worker_sessions``). Its event fires at most once, at a queue step where the lead's chat
  is open, current and idle (no turn, nothing pending, no setup owed, and Blender not held by a render, bake or
  Angelo job: the send's own preconditions, EVENT M-1) and its own scene is shown (``document_scene.away``: R5-M-1,
  else the send is refused and the event lost), none of its workers is starting, queued, waiting to start
  again or working (``worker_sessions.finished``) or still has its saved part's count being read (``_checking``:
  status cannot say yet whether it is mergeable, so the batch waits, never ends on it, A′), and some part is
  mergeable (``worker_merge.part_refusal``'s rule, the status rows' ``mergeable``). With nothing mergeable the batch
  ends quietly. A part still parking (``worker_queue._parking``: its chat's close and its save, each bounded) keeps
  the batch waiting, so the event's merge never meets a chat whose close is under way (``worker_chat_closing``).
- A turn of the lead never has the event added to it: while it runs, the batch waits for its end (the queue's step
  slows to ``WAIT_POLL`` once only a batch waits, EVENT L-2), and the facts reach that turn through the scene
  observation's own field (``worker_facts.OBSERVED``).
- The person's Stop on the lead ends its batch; so does a load of another file (``worker_ending``), while another
  scene of its file in the window only makes it wait (above). A host's own stop does not (EVENT L-3).
- The event is the lead's next request (``Controller.send``'s ``host_request``: none of the person's draft, EVENT
  H-2), its row Angelo's (``request_kind`` ``angelo_event``, a notice in the person's language; ``request_rows`` keeps
  it out of what reads the person's own words, EVENT M-3). It states each part not merged into this scene as the
  status rows do: its quoted name, its state and how its turn ended (EVENT M-2, L-1). Facts only. A send that fails
  says in the lead's chat that Angelo could not continue it, and why.
- ``ASK_TURNS`` (the maintainer: on): such a turn starts in Ask too, with no click, and its own merge of the batch's
  parts runs with no card (``event_merge``, read by ``Controller._auto_bounded``); any other edit in it asks. Read
  only can merge nothing and starts no turn: its next turn reads the same facts (``worker_facts.OBSERVED``).
Main thread. No bpy at import.
"""

import json
import unicodedata

from . import document_scene

# The maintainer, 2026-10-25: "Should auto start the lead. User should not have to confirm" (Ask too; Read only can
# merge nothing, so it starts no turn), and its own merge in Ask: "Yes, even in ask mode since it's part of the same
# flow" (``event_merge``).
ASK_TURNS = True
REQUEST_KIND = "angelo_event"
WAIT_POLL = 1.0   # Seconds between the queue's steps while only a batch waits for its lead's turn to end.
# Model-visible (never translated): each part follows as <number> "<name>" (state <state>, turn <how it ended>).
EVENT = ("From Angelo, not the person: every worker this chat started has ended; the parts not merged into this scene "
         "are: %s.")

_batches = {}   # lead chat id -> its batch's worker numbers, while its event may still fire
UNFINISHED = frozenset({"stopped", "failed", "not_sent"})   # How a worker's turn ended that its merge never skips a card for


def started(controller, numbers=()):
    """The lead started workers ``numbers`` (``worker_sessions.execute``): a batch opens, or the open one goes on; never
    in a turn Angelo started itself (EVENT H-3: the person's next message is what opens the next one)."""
    rows = controller.state.get("messages") or ()
    last = next((row for row in reversed(rows) if isinstance(row, dict) and row.get("role") == "user"), None)
    if last is None or last.get("request_kind") != REQUEST_KIND:
        _batches.setdefault(controller.state["id"], set()).update(numbers)


def stop(controller):
    """The person's Stop on a chat (``ANGELO_OT_stop``): its turn stops, and a lead's batch ends with it (EVENT L-3); a
    host's own ``Controller.stop`` (a connection change, a harness) leaves the batch."""
    controller.stop()
    cancel(controller.state["id"])


def cancel(lead_id):
    """The person's Stop on the lead, or a change of its document: this batch fires no event."""
    _batches.pop(lead_id, None)


def pending(lead_id):
    """Whether the lead's batch may still fire its event."""
    return lead_id in _batches


def waiting():
    """Whether any batch may still fire: the queue keeps stepping (at ``WAIT_POLL``) meanwhile."""
    return bool(_batches)


def step(ui):
    """Main thread, each queue step: fire each batch whose lead is idle and whose workers have all ended."""
    for lead_id in list(_batches):
        chat = ui._controllers.get(lead_id)
        if chat is None or getattr(chat, "document_invalid", False) or getattr(chat, "_closing", False):
            cancel(lead_id)   # Gone, closed or invalidated (a load): its parts can never merge through it.
            continue
        if not _idle(chat) or not _ended(chat) or document_scene.away(chat, ui.document_key()):
            continue
        batch = _batches.pop(lead_id)   # Once per batch, whatever follows: a send that fails says so in the chat.
        parts = _unmerged(chat)
        if parts and _may_start(chat):
            _fire(ui, chat, parts, batch)


def _idle(chat):
    """No turn, nothing pending, and nothing that would refuse the send for a while (EVENT M-1)."""
    worker = getattr(chat, "_worker", None)
    if chat.busy or chat.pending or (worker is not None and worker.is_alive()) or getattr(chat, "setup_required", False):
        return False
    return not _blender_busy()


def _blender_busy():
    """A render, bake or Angelo job holds Blender, or the interface is locked: a request now would be refused."""
    try:
        import bpy  # noqa: F401
    except ImportError:
        return False
    from . import job_host, program_context
    manager = job_host._manager()
    return program_context._jobs_run() or bool(manager is not None and manager.active() is not None
                                               and manager.locks_interface())


def _ended(chat):
    from . import worker_queue, worker_sessions
    if worker_queue.waiting(chat):   # Starting, queued, or a message to a parked one waits for its worker.
        return False
    sessions = worker_sessions.workers_of(chat.state["id"])
    if any(session in worker_queue._parking for session in sessions):
        return False   # Parking: its chat's close and its save end first (each bounded), then its worker stops.
    return all(worker_sessions.finished(session) for session in sessions) and not _checking(sessions)


def _checking(sessions):
    """Whether a part of ``sessions`` is still being checked: its saved file's object count is ``UNKNOWN`` while its
    live worker's lease is asked (``worker_part_counts``). Its worker has not ended for the batch until the count is
    known, else the batch would end on a part status cannot call mergeable yet and the event would be lost (native r2:
    the part that had just parked). Asking keeps the read going; the window's pump reads it."""
    from . import worker_part_counts, worker_sessions
    for session in sessions:
        part = worker_sessions._parts.get(session)
        if part is not None and worker_part_counts.count(part[2]) == worker_part_counts.UNKNOWN:
            return True
    return False


def _unmerged(chat):
    """Each mergeable part's status row: number, name, state and how its turn ended, as status says them."""
    from . import worker_sessions
    return [row for row in worker_sessions.status(chat)["workers"] if row.get("mergeable")]


def _may_start(chat):
    from .chat_settings import clean_options
    if getattr(chat, "external_client", ""):
        return False   # A connected app's conversation: only its app starts a turn (controller_external).
    approval = clean_options(chat.state.get("options"))["approval"]
    return approval == "AUTO_BOUNDED" or (approval == "ASK" and ASK_TURNS)


def event_merge(controller, arguments):
    """In Ask: whether this merge runs with no card. Only the worker-done event's own merge, of parts its event listed,
    in the turn the event started (the person approved that flow when the workers started); never an ``again`` that
    places a part merged before (``worker_merge.auto_merge_allowed``). Any other edit in that turn asks, as Ask does."""
    rows = controller.state.get("messages") or ()
    last = next((row for row in reversed(rows) if isinstance(row, dict) and row.get("role") == "user"), None)
    if last is None or last.get("request_kind") != REQUEST_KIND or not controller.busy:
        return False
    # Only this batch's parts (EVENT R2-H-1): never an earlier batch's, one whose turn ended unfinished, or one whose
    # merge the person declined; those are still stated as facts, and their merge asks.
    allowed = getattr(controller, "_event_batch", frozenset()) - _declined(rows)
    parts = [part for part in (arguments or {}).get("parts") or () if isinstance(part, dict)]
    from .worker_merge import auto_merge_allowed
    return bool(parts) and all(part.get("worker") in allowed for part in parts) and auto_merge_allowed(
        controller, arguments)


def _declined(rows):
    """Worker numbers of every merge the person declined in this chat."""
    found = set()
    for row in rows:
        if (isinstance(row, dict) and row.get("role") == "activity" and row.get("tool") == "angelo_workers_merge"
                and (row.get("result") or {}).get("error_code") == "DECLINED"):
            found.update(part.get("worker") for part in (row.get("arguments") or {}).get("parts") or ()
                         if isinstance(part, dict))
    return found


def quoted(name):
    """A part's name as data: JSON-quoted, and every invisible character a name could carry (a line or paragraph
    separator, a bidi control) escaped, so a name never reads as the event's own words (EVENT R2-L-2)."""
    return "".join("\\u%04x" % ord(c) if unicodedata.category(c) in {"Cf", "Zl", "Zp"} else c
                   for c in json.dumps(str(name), ensure_ascii=False))


def text(rows):
    """The event's words: each part as data (``quoted``), its state and how its turn ended."""
    return EVENT % "; ".join("%d %s (state %s, turn %s)" % (
        row["worker"], quoted(row.get("name", "")), row.get("state") or "unknown", row.get("turn") or "unknown")
        for row in rows)


def _fire(ui, chat, rows, batch=frozenset()):
    """The lead's next request, as a person's message is prepared (``console.execute``), but Angelo's: none of the
    person's draft, its row Angelo's."""
    from . import worker_routing
    from .ui_text import n_, notice
    message = text(rows)
    parts = ["%d %s" % (row["worker"], quoted(row.get("name", ""))) for row in rows]   # The row's: quoted too.
    chat._event_batch = frozenset(row["worker"] for row in rows if row["worker"] in batch   # Ask's no-card merge
                                  and row.get("turn") not in UNFINISHED)                     # (event_merge)
    failures = []
    try:
        ui.prepare_controller(chat)
        worker_routing.deliver(chat, ui.main_window(), message, lambda turn: chat.send(
            message, observation=turn.observation, recovery_evidence=turn.recovery_evidence, host_request=parts),
            failures.append)
    except Exception as error:   # noqa: BLE001 - said in the lead's chat; the batch is over either way.
        failures.append(error)
    for error in failures[:1]:
        chat.emit("message", {"role": "system", **notice(
            n_("Angelo could not continue this chat after its workers ended: {error}"), error=str(error)[:300])})
