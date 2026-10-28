# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Facts a lead reads about its workers' parts: whether each was merged into its scene, and how long each took. Facts only:
nothing here says what to do with them.

Evidence: a lead (Opus, 3 of 3 live trials, 2026-10-25) ended its turn with its workers' parts finished but never
merged. It saw ``"state": "done"`` against ``"merged"`` and the part's ``objects``, which are the worker's file and can
read as if they were in its own scene; nothing said a part was not.

- "This scene" is the lead's own scene (``parallel_sessions._document_scene``), whichever scene the window shows
  (break-it E2E-20 R5-M-1).
- Each ``angelo_workers_status`` row's ``merged_into_this_scene``: true once a merge brought its part file into this
  scene, until an Undo of that merge (``parallel_sessions.part_in_file``, the record the merged-once refusal reads). A
  history fact, never a claim about what the scene holds now: the person may have deleted or joined the copies, and no
  rule can tell (break-it E2E-20 R4-M-2: it read ``in_this_scene`` true with nothing of it left).
- Each row's ``mergeable``: whether a merge takes its part now, decided by the merge's own admission of one part
  (``worker_merge.part_refusal``: its chat's turn ended, its saved file exists and holds objects, it was not merged
  into this scene; a merge's ``again`` takes one that was). One rule decides every place that says so, so they never disagree: a worker that died after a save reads
  ``failed`` with ``mergeable`` true (the merge takes its last save, with a warning); one with no saved file, or whose
  file holds no objects (it died before its first edit), is mergeable nowhere and the merge refuses it by name.
- ``mergeable_not_in_this_scene``: each mergeable part, as "<number> <name>" (a mergeable part is never in this
  scene). Status, and so the status wait's answer, carry it only while it is not empty. The lead's scene observation
  carries the same list as the state field ``OBSERVED`` (``turn_context``): an observation delta sends it when it
  changes (``null`` once, when the last one is merged) and nothing while there is none (``context_ledger``).
- Split facts, measured in this job, never a constant: a running worker's ``elapsed_s`` since its current turn's
  message (its brief, or a later one) reached it and ``rounds``, the model requests its chat made in that turn (``ledger_events.record_round``: one per Claude
  plan round, one per Codex model request, an Auto re-run on a stronger model counted on); a done worker's
  ``built_in_s``, from its last turn's message to that turn's end (its chat's own request clock, ``Controller.timings``, monotonic as
  the brief's time).

Main thread (status, the wait's poll and ``turn_context`` run there). No bpy at import.
"""

import time

OBSERVED = "worker_parts_mergeable_not_in_this_scene"   # Model-visible (never translated).
QUEUE = "queue"   # Model-visible (never translated): ``queue_facts``.
MEASURED = "the first build turns of this conversation's workers that completed; a failed or stopped one is not counted"
ESTIMATE = ("estimate_s is median_s times the rounds of at_once parts the queued new parts take, from now; the running "
            "parts' remaining time and queued restarts are not included")
_briefed = {}   # worker chat session id -> time.monotonic() its brief was sent to its chat (``briefed``)
_built = {}     # worker chat session id -> its last ``built_in_s``: still known once its chat has left Angelo
_turns = {}     # worker chat session id -> how many turns it was given (``turn_started``)
_samples = {}   # (worker chat session id, turn) -> (brief-to-end seconds, status) as that turn ended (``turn_ended``)


def briefed(controller):
    """The brief is being sent to this worker chat (``worker_sessions._send``): its clock starts now."""
    _briefed[controller.state["id"]] = time.monotonic()


def turn_started(controller):
    """A message is being sent to ``controller`` (``worker_routing.deliver``): a worker chat's turn clock and request
    count start now, so ``elapsed_s``, ``built_in_s`` and ``rounds`` are this turn's, never a parked worker's idle
    time or its previous turn's count (break-it E2E-20 L-6: one clock per chat, from its brief)."""
    from . import worker_sessions
    if controller.state.get("id") in worker_sessions._parts:
        briefed(controller)
        controller._turn_requests = 0
        _turns[controller.state["id"]] = _turns.get(controller.state["id"], 0) + 1
        from . import worker_news
        worker_news.began(controller.state["id"])   # Its next finish is news again (break-it OVH-M-1).


def _sessions(lead_id):
    """{worker number: (session, part name, part file)} of ``lead_id``'s started workers."""
    from . import worker_sessions
    return {worker_sessions._parts[s][0]: (s,) + tuple(worker_sessions._parts[s][1:])
            for s in worker_sessions._started.get(lead_id, ()) if s in worker_sessions._parts}


def _mergeable(number, session, path, lead):
    """Whether a merge takes this part into ``lead``'s own scene now: the merge's own admission
    (``worker_merge.part_refusal``)."""
    from . import ui, worker_merge, worker_sessions
    return worker_merge.part_refusal(number, ui._controllers.get(session), path, lead,
                                     worker_sessions.merge_blocker) is None


def _lead(lead_id):
    """The lead's chat: "this scene" is its own scene (``document_scene.scene_of``), never the one the window shows."""
    from . import ui
    return ui._controllers.get(lead_id)


def unmerged(lead_id):
    """"<number> <name>" of each of ``lead_id``'s mergeable parts, in worker order (a mergeable part is never in this
    scene; a part that never started, or has no saved file, is none: the merge refuses it)."""
    lead = _lead(lead_id)
    return ["%d %s" % (number, name) for number, (session, name, path) in sorted(_sessions(lead_id).items())
            if _mergeable(number, session, path, lead)]


def observed(chat_id):
    """The lead's scene observation field while its finished parts are not in this scene; {} otherwise (no bytes)."""
    from . import worker_sessions
    if chat_id is None or chat_id not in worker_sessions._started:
        return {}
    found = unmerged(chat_id)
    return {OBSERVED: found} if found else {}


def _timing(session):
    """A running worker's ``elapsed_s`` and ``rounds``, a done one's ``built_in_s``: what its chat recorded, else {}."""
    from . import ui, worker_queue, worker_sessions
    chat, brief = ui._controllers.get(session), _briefed.get(session)
    if session in worker_queue._resuming:   # A message waits for its restart: no turn of it runs yet (R2-L-4).
        return {}
    if not worker_sessions.finished(session):
        if chat is None or brief is None:
            return {}
        return {"elapsed_s": round(time.monotonic() - brief, 1),
                "rounds": getattr(chat, "_turn_requests", 0)}
    timings = getattr(chat, "timings", None) or {}
    if brief is not None and isinstance(timings.get("started"), (int, float)) and isinstance(
            timings.get("total_ms"), (int, float)):
        ended = timings["started"] + timings["total_ms"] / 1000.0   # Its last request's end: its turn's end.
        if ended >= brief:
            _built[session] = round(ended - brief, 1)
    return {"built_in_s": _built[session]} if session in _built else {}


def annotate(lead_id, result):
    """``worker_sessions.status``'s answer with these facts: each row's ``merged_into_this_scene``, ``mergeable`` and
    timing right after its state, and ``mergeable_not_in_this_scene`` while any part is."""
    from . import parallel_sessions, worker_save_recovery
    sessions, rows, lead = _sessions(lead_id), [], _lead(lead_id)
    for row in result.get("workers") or ():
        session, _name, path = sessions.get(row.get("worker"), (None, None, None))
        facts = {"merged_into_this_scene": session is not None and parallel_sessions.part_in_file(path, lead),
                 "mergeable": session is not None and _mergeable(row["worker"], session, path, lead)}
        if session is not None and row.get("state") != "failed_to_start":
            facts.update(_timing(session))
            facts.update(worker_save_recovery.facts(session))
        out = {}
        for key, value in row.items():
            out[key] = value
            if key == "state":
                out.update(facts)
        rows.append(out)
    found = ["%d %s" % (row["worker"], sessions[row["worker"]][1]) for row in rows if row.get("mergeable")]
    queue = queue_facts(lead_id)
    return dict(result, workers=rows, **({"mergeable_not_in_this_scene": found} if found else {}),
                **({QUEUE: queue} if queue else {}))


def turn_ended(controller, status):
    """A chat's message ended with ``status`` (``Controller._after_turn``, once Auto continues nothing: an escalation's
    stronger model runs the same message, so its attempts are one turn, break-it QF2-M-1): a worker chat's turn is
    one immutable sample, keyed by (chat, turn: ``turn_started`` counts messages), its brief-to-final-end seconds and
    its final status; kept whatever reads status later (QF-M-1: one last value per worker was overwritten by a short
    follow-up, or missed when a follow-up started before any status read)."""
    session = (getattr(controller, "state", None) or {}).get("id")
    brief = _briefed.get(session)
    if brief is not None:
        _samples.setdefault((session, _turns.get(session, 1)), (round(time.monotonic() - brief, 1), status))


def queue_facts(lead_id):
    """While anything of ``lead_id`` waits for a place: how many new parts (``queued``) and restarts
    (``restarts_queued``: a message to a parked worker) wait, how many run at once, and the measured seconds of the
    first build turns of this conversation's workers that completed (``MEASURED``; a failed or stopped first turn is
    not counted): count, min, median, max, and, once any completed, an estimate of when the last queued new part would
    be done (``ESTIMATE``). None while nothing waits (no bytes). Measured, never a constant: with nothing completed it
    says so and gives no number (C3 early town-centre diagnostic: a lead queued 23 parts at one worker at a time,
    about 62 s each, told nothing of what that line costs)."""
    import math
    import statistics
    from . import parallel_access, worker_queue
    from .worker_ending import owner
    waiting = [item for item in list(worker_queue.queue) if owner(item) == lead_id]
    restarts = sum(1 for item in waiting if item.get("kind") == worker_queue.RESUME)
    queued = len(waiting) - restarts
    if not waiting:
        return None
    at_once = max(1, parallel_access.worker_limit())
    sessions = {entry[0] for entry in _sessions(lead_id).values()}
    times = sorted(seconds for (session, turn), (seconds, status) in list(_samples.items())
                   if session in sessions and turn == 1 and status == "completed")
    facts = {"queued": queued, "restarts_queued": restarts, "at_once": at_once, "measured": MEASURED}
    if not times:
        return dict(facts, finished="none finished yet")
    median = statistics.median(times)
    return dict(facts, finished={"count": len(times), "min_s": times[0], "median_s": round(median, 1),
                                 "max_s": times[-1]},
                estimate_s=round(math.ceil(queued / at_once) * median), estimate=ESTIMATE)
