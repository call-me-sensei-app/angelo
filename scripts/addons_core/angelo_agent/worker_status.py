# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's ``angelo_workers_status`` call: queued parts taken back, queued parts listed, and the wait.

``call`` takes back the queued parts named in ``take_back`` first (``worker_queue.take_back``: never one that started),
then answers with ``worker_sessions.status`` at once, or defers it (``wait``). Status lists each queued part with its
place in line and what it waits for, and, while any of the lead's parts waits, how many places there are
(``places``: ``parallel_access.limit_text``).

The wait (a deferred status: the controller polls it with Studio's work, never blocking) answers as soon as any
worker finishes that the lead's model was not yet shown finished (its turn ended, or it failed or ended:
``worker_sessions.finished``; shown: ``worker_news``, by a delivered ``workers_finished_since_status``), or once every part
of the lead is done, or at its timeout; listing every worker either way, so the lead can merge and review a finished
part while the others build (latency proposal 1c, B3-SPEED-ANALYSIS: the dev check's lead waited 55.8 s for its last
worker with nothing to do). A worker that finished while the lead wrote the request that waits answers at once
(LANE-overheads: in 4 of 8 trials such a part waited 11-23 s unannounced while the lead waited); one already shown
never answers it again (break-it E2E-3 M-2: answering at once only cost the lead a billed round per poll).

A worker's approval card is the person's to answer, on its tile in the workers' rail; the lead cannot act on it (the
maintainer's decision, 2026-10-25: C2's capped runs woke the lead 3-4 times per trial for cards the person answered
within seconds). So a card never answers the wait while a window shows the rail (``cards_shown``). A card no window
shows and no delivered ``approval_no_window_shows`` named (``worker_news.unshown_cards``), on any worker of the lead (a named target
may wait on another's card: OVH3-M-2), answers it, judged on the state at every poll, whenever the card came (before
the wait included: break-it OVH-M-4, OVH2-M-4), so the lead can tell the person in its chat
(``approval_no_window_shows``); once an answer listing it reached the lead's model it never answers again.
Every answer lists each worker's state, ``approval`` included. A worker whose start or brief failed is never done:
its retained chat is read after the one failure record status and the merge read (OVH2-M-3). Each poll reads the
state first, so a satisfied wait answers before any background step; a background Angelo then steps its queue for at
most one poll interval and before the wait's deadline (``worker_queue.drive(until=...)``, OVH2-M-5, OVH3-M-1), and
Stop and expiry are served between polls. Finishes are read as the news reads them, at every poll, so one delivered
by any route never answers a later poll of any wait (OVH3-M-3).

``workers`` narrows the wait to those workers: each named number is resolved, a number with no worker started,
starting or queued is refused before anything waits, and the wait answers once none of them runs or waits to start,
naming in ``not_built`` any whose start failed or that was dropped meanwhile (OVH-M-2), so the request that waits can
merge the finished ones next. Stop ends the wait, never the workers. No bpy.
"""

import functools
import time

DONE_SINCE, NOT_BUILT, UNSEEN = "done_since_wait", "not_built", "approval_no_window_shows"   # Model-visible keys.
DONE, RUNNING, WAITING, FAILED, GONE = "done", "running", "waiting", "failed", "gone"


def call(lead, arguments):
    """``angelo_workers_status``: take back, then status now or once the wait answers."""
    from . import worker_queue, worker_sessions
    worker_queue.reap()   # A worker that died between requests reads failed, and frees its place, before anything.
    extra = {}
    if arguments.get("take_back"):
        taken, refused = worker_queue.take_back(lead, arguments["take_back"])
        extra.update({"taken_back": taken} if taken else {}, **({"not_taken_back": refused} if refused else {}))
    if arguments.get("wait"):
        unknown = [n for n in arguments.get("workers") or () if _where(lead, n) == GONE]
        if unknown:   # Each named worker is resolved: none waits on a number with no worker (break-it OVH-M-2).
            return dict({"error_code": "VALIDATION", "scene_changed": False, "error": (
                "This conversation has no worker %s started, starting or queued (never started, or taken back); "
                "nothing was waited for" % ", ".join(map(str, unknown)))}, **extra)
        return wait(lead, arguments["timeout_seconds"], extra, arguments.get("workers"))
    worker_queue.drive()
    return dict(worker_sessions.status(lead), **extra)


def queued(lead):
    """``(rows, places)``: ``lead``'s queued parts as status lists them, and the places line while any waits."""
    from . import parallel_access, worker_places, worker_queue
    rows = []
    for index, item in enumerate(worker_queue.queue, start=1):
        if item.get("kind") != worker_queue.RESUME and item["lead"] is lead:
            rows.append({"worker": item["number"], "name": item["part"]["name"], "state": "queued", "position": index,
                         "waits_for": waits_for(index)})
    places = parallel_access.limit_text(parallel_access.worker_limit(), worker_places.occupied()) if rows else None
    return rows, places


def waits_for(place):
    """What a part at ``place`` in line waits for (model-visible, never translated)."""
    return "a running worker to be done" if place == 1 else "%d running workers to be done" % place


def wait(lead, seconds, extra=None, numbers=None):
    """A deferred status that answers as the module docstring says; ``numbers``: only these workers. Every poll reads
    the one current state (``state``): nothing is snapshotted when the wait begins (break-it OVH3-M-3)."""
    from . import worker_queue, worker_sessions
    started = time.monotonic()
    deadline = started + seconds
    lead_id = lead.state["id"]

    def state():
        """``(done, everything, unseen, lost)`` now: finishes the lead's model was not shown (every route's delivery
        counts), whether nothing more will finish, cards on any of its workers no window shows and no delivered answer
        showed (a named target may wait on another worker's card: OVH3-M-2), and named targets that cannot be built."""
        from . import worker_news
        sessions = list(worker_sessions._started.get(lead_id, ()))
        unseen = worker_news.unshown_cards(lead_id)
        unseen = unseen if unseen and not cards_shown() else []
        if numbers is None:
            fresh = set(worker_news.fresh(lead_id))   # This turn of each worker, unacknowledged, never a failed start.
            finished = [s for s in sessions if worker_sessions.finished(s)]
            return ([s for s in sessions if s in fresh], not worker_queue.waiting(lead) and len(finished) == len(sessions),
                    unseen, [])
        places = {n: _where(lead, n) for n in numbers}
        everything = not any(where in (RUNNING, WAITING) for where in places.values())
        done = [s for s in sessions if places.get(_number(s)) == DONE] if everything else []
        return done, everything, unseen, sorted(n for n, where in places.items() if where in (FAILED, GONE))

    def answer(done, everything, unseen, lost):
        answered = bool(done or everything or unseen)
        result = dict(worker_sessions.status(lead), waited_seconds=round(time.monotonic() - started, 1),
                      timed_out=not answered)
        if done:
            result[DONE_SINCE] = sorted(_number(s) for s in done)
        if lost and answered:
            result[NOT_BUILT] = lost
        if unseen:
            result[UNSEEN] = sorted(_number(s) for s in unseen)
        return dict(result, **(extra or {}))

    def poll():
        worker_queue.reap()
        now = state()   # Before any background step: a satisfied wait answers at once (OVH3-M-1).
        if any(now[:3]) or time.monotonic() >= deadline:
            return answer(*now)
        # A background Angelo runs no timer: one bounded step of its queue, then the state again; the controller
        # polls again (Stop and expiry are served between polls).
        worker_queue.drive(until=min(deadline, time.monotonic() + worker_sessions.POLL))
        now = state()
        return answer(*now) if any(now[:3]) or time.monotonic() >= deadline else None
    pending = _Wait(poll, lambda: None)
    pending.lead_id, pending.extra = lead_id, extra
    pending.timeout = seconds + 30   # The poll answers first (timed_out), never the call's own expiry.
    pending.status_text = "Waiting for workers"
    pending.activity_note = "Waiting for the workers to finish their parts; Stop ends the wait, never the workers."
    return pending


def cards_shown():
    """Whether an Angelo window shows the workers' rail now, where each worker's approval card is (its largest 3D
    view: ``live_view_draw.target``). No window (a background Angelo), no 3D view (another workspace, a maximized
    editor) or no Blender: no (break-it OVH-M-4)."""
    try:
        import bpy
        from . import live_view_draw, live_windows
        return any(live_view_draw.target(window) is not None for window in live_windows.windows(bpy.context.window_manager))
    except Exception:   # noqa: BLE001 - no Blender here, or no window manager: nothing shows a card.
        return False


def _where(lead, number):
    """Worker ``number`` of ``lead``: DONE or RUNNING (its chat's turn ended or not), WAITING (queued or launching),
    FAILED (its start failed: no chat ever came) or GONE (no such worker: never started, taken back, or dropped)."""
    from . import worker_news, worker_queue, worker_sessions
    if worker_news.start_failed(lead.state["id"], number):   # Before its chat: a failed brief leaves one behind.
        return FAILED
    for session in list(worker_sessions._started.get(lead.state["id"], ())):
        if _number(session) == number:
            return DONE if worker_sessions.finished(session) else RUNNING
    if any(i.get("kind") != worker_queue.RESUME and i["lead"] is lead and i["number"] == number
           for i in list(worker_queue.queue) + list(worker_sessions._launching)):
        return WAITING
    return GONE


@functools.lru_cache(maxsize=1)
def _wait_class():
    from .studio_agent import StudioPending

    class Wait(StudioPending):
        @property
        def stopped_result(self):
            """A stopped wait's answer, built when it is delivered (the controller reads it then): the queue's live
            facts while anything of the lead waits (break-it QF-L-1), never a snapshot from when the wait began."""
            from . import worker_facts
            queue = worker_facts.queue_facts(self.lead_id)
            return dict({"error_code": "CANCELLED", "error": "Stopped waiting; the workers carry on",
                         "scene_changed": False}, **({worker_facts.QUEUE: queue} if queue else {}), **(self.extra or {}))
    return Wait


def _Wait(poll, cancel):
    return _wait_class()(poll, cancel)


def _number(session):
    from . import worker_sessions
    found = worker_sessions._parts.get(session)
    return found[0] if found else 0


SIZE_NOTE = "Bounding box; a Subdivision Surface counts at its cage, so the smooth surface may be a little smaller."


def _size(session_id, summary):
    """The part's size as the worker measured it on its own evaluated scene (Multires and every other modifier at
    its own levels); else the live part's, simplified (break-it P1-3 M5). Blender evaluates a Subdivision Surface
    only when it is drawn, so both count it at its cage: an upper bound, said so. Forcing it on each save would add
    the subdivision itself to every edit."""
    from . import live_view, object_bounds, parallel_sessions
    measured = parallel_sessions.sizes.get(session_id)
    if measured is not None:
        box = parallel_sessions.boxes.get(session_id)
        return {"size_m": [round(v, 3) for v in measured], "size_note": SIZE_NOTE,
                **({"bounds_m": object_bounds.rounded(box)} if box is not None else {})}
    box = live_view.bounds(session_id)   # Where it stands in the world, from the live part (same rule).
    return {"size_m": [round(v, 3) for v in summary.get("size") or (0.0, 0.0, 0.0)],
            "size_note": SIZE_NOTE + " Of the live view's simplified copy: Multires counts at its base mesh.",
            **({"bounds_m": object_bounds.rounded(box)} if box is not None else {})}
