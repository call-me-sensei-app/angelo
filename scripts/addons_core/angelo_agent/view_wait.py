# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image`` naming a render job that has not ended: wait for it inside the turn.

A job's result used to reach a model only in the jobs block of a later
message, so a request that starts a render and has to judge its picture could
not finish in one turn. A view of a job that is still queued or rendering now
stays pending, as a RENDERED look does (``Controller.media_jobs``), until the
job ends, and then answers with the picture.

**Bounded, and the job is never the wait's to stop.**

* One request may wait ``view_spec.WAIT_SECONDS`` for jobs in all. Overlapping
  waits count elapsed time once; intervals with no active wait cost nothing. The call
  is host-held, so the turn's silence and stretch clocks rest while it waits
  (``turn_activity``) and Blender's interface is not blocked: the job is
  Blender's own, and this module only reads its record on the UI pump.
* A job that will not end in time is not waited for to the end of the
  allowance. Its pace is read from how its progress moves while the call
  watches (``Pace``: preparation, at 0%, is not counted, so a scene that is slow
  to prepare and quick to sample is not mistaken for a long render). Once that
  pace has ``SETTLE_SECONDS`` behind it and the time it projects exceeds what
  the request has left by ``ETA_FACTOR``, the call answers: a 48-minute frame
  returns some tens of seconds after its first progress, not after ten minutes.
  A queued job is judged by the pace of the job ahead of it.
* Every ending is a result the model can act on, never an error that ends the
  turn: the picture; "still rendering, N%, about M left, end your turn and
  tell the person"; the job failed, was cancelled or was stopped at its own
  time budget, with its reason.
* Stop ends the wait and the turn, not the job. Cancel on the job's card ends
  the job, and the waiting call then says that the person cancelled it. The
  job's card, the status bar and the notice row when it ends are what tell the
  person about a job that outlives its turn (``job_ui``); nothing is added.
* The controller owns one request account. Each wait keeps its original
  account until it ends, then releases that reference and retains only its
  final budget values. A late poll reuses the terminal result, never captures
  again. The caller's optional ownership check fences job lookup and picture
  decoding; losing that ownership returns no facts about a replacement job.

Model-facing text is English and never translated. Main thread (the UI pump).
"""

import time

from .job_store import TERMINAL, context_entry
from .studio_agent import StudioPending
from .view_spec import RENDERED, WAIT_SECONDS

SETTLE_SECONDS = 15   # No "will not end in time" before a job's progress has moved for this long.
ETA_FACTOR = 1.5      # How far the projected time must exceed the time left before the call stops waiting.
GRACE_SECONDS = 30    # The pending call outlives its allowance by the pump's latency.


def _now():
    return time.monotonic()


def _spent(controller):
    """The current request's account: seconds ``waited`` and jobs ``declined`` as too long (id -> when, time to go)."""
    request = (controller.state["id"], getattr(controller, "_epoch", 0),
               getattr(controller, "_request_generation", 0))
    account = getattr(controller, "_render_wait_account", None)
    if account is None or account["request"] != request:
        account = controller._render_wait_account = {
            "request": request, "waited": 0.0, "declined": {}, "active": set(), "updated": _now()}
    return account


def _remaining(account):
    """Settle the union of active wait intervals before reading or changing its owners."""
    now = _now()
    if account["active"]:
        account["waited"] += max(0.0, now - account["updated"])
    account["updated"] = now
    return max(0.0, WAIT_SECONDS - account["waited"])


def allowance(controller):
    """Seconds the request may still wait for jobs, including its currently pending calls."""
    return _remaining(_spent(controller))


def _clock(seconds):
    """A duration as a model and a person read it: 45 s, 4 min, 48 min, 15 h."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "%d s" % round(seconds)
    return "%d min" % round(seconds / 60) if seconds < 5400 else "%d h" % round(seconds / 3600)


class Pace:
    """Seconds a job has left, from how its progress moved while it was watched.

    Measured between two moments at which the progress changed, so neither the
    time a job spends preparing (at 0%) nor a wait that began between two
    coarse steps of it bends the rate.
    """

    def __init__(self):
        self.job = self.seen = self.first = self.last = None

    def remaining(self, record, now):
        progress = float(record.get("progress") or 0.0)
        if record["id"] != self.job or (self.seen is not None and progress < self.seen):
            self.job, self.seen, self.first, self.last = record["id"], progress, None, None
        elif progress != self.seen:
            self.seen, self.last = progress, (now, progress)
            self.first = self.first or self.last
        if self.first is None or self.last[0] - self.first[0] < SETTLE_SECONDS or self.last[1] <= self.first[1]:
            return None
        rate = (self.last[1] - self.first[1]) / (self.last[0] - self.first[0])
        return max(0.0, (1.0 - self.last[1]) / rate - (now - self.last[0]))


def job_facts(record, now=None, remaining=None):
    """What a result says about a job: its state, how far it is and how long it has left (no paths)."""
    facts = {"id": record["id"], "kind": record["kind"], "status": record["status"]}
    if record["status"] not in TERMINAL:
        facts["progress"] = round(float(record.get("progress") or 0.0), 3)
        if record.get("started"):
            facts["elapsed_seconds"] = round(max(0.0, (now or time.time()) - record["started"]))
        estimate = remaining if remaining is not None else record.get("eta_s")
        if estimate is not None:
            facts["eta_seconds"] = round(estimate)
    elif record.get("reason"):
        # Reuse the bounded public job reason; never expose an unbounded runner diagnostic in a note.
        facts["reason"] = context_entry(record).get("reason", "")
    if record["detail"].get("frames_total", 1) > 1:
        facts.update(frames_done=record["detail"].get("frames_done", 0), frames_total=record["detail"]["frames_total"])
    return facts


def _progress(facts):
    if facts["status"] == "queued":
        text = "is queued behind another job"
    else:
        text = "is %d%% done" % round(100 * facts.get("progress", 0.0))
        if "frames_total" in facts:
            text += " (%d of %d frames)" % (facts["frames_done"], facts["frames_total"])
        if "elapsed_seconds" in facts:
            text += " after %s" % _clock(facts["elapsed_seconds"])
    if "eta_seconds" in facts:
        text += ", with about %s to go" % _clock(facts["eta_seconds"])
    return text


def _answer(facts, waited, left, ended, note):
    return {"read_only": True, "scene_changed": False, "views": [], "shading": RENDERED, "job": facts,
            "waiting": {"waited_seconds": round(waited), "request_seconds_left": round(left), "ended": ended},
            "note": note}


def still_running(record, waited, left, ended, remaining=None):
    """The answer for a job that has not ended: what it is doing and what the model does now. Not an error."""
    facts = job_facts(record, remaining=remaining)
    identity = record["id"]
    if ended == "will_not_end_in_time":
        why = ("job %s %s; this request can wait %s more for jobs (%s per request), so the call stopped waiting" % (
            identity, _progress(facts), _clock(left), _clock(WAIT_SECONDS)))
    else:
        why = "job %s %s, and this request has used its %s of waiting for jobs" % (
            identity, _progress(facts), _clock(WAIT_SECONDS))
    return _answer(facts, waited, left, ended, (
        "No picture yet: %s. Do not ask again in this turn. End your turn and tell the person that the job is still "
        "running, how far it is, and that its picture can be shown once it ends (image %s in a later message). The "
        "job goes on by itself; scene changes are refused until it ends." % (why, identity)))


def _terminal_note(record, problem=None):
    """Public job outcome, independent of whether any retained picture can be shown."""
    identity, status, reason = record["id"], record["status"], job_facts(record).get("reason", "")
    if status == "cancelled" and reason == "user":
        note = "the person cancelled job %s. Do not start it again unless they ask." % identity
    elif status == "cancelled" and reason == "time_budget":
        note = ("job %s was stopped at its own time budget before it finished. Tell the person; a longer "
                "budget (minutes) or fewer samples is theirs to approve." % identity)
    elif status == "cancelled":
        note = "job %s was cancelled (%s)." % (identity, reason or "no reason given")
    elif status == "interrupted":
        note = "job %s was interrupted (the document was closed or reloaded)." % identity
    elif status == "failed":
        note = "job %s failed: %s Fix what it names before starting another." % (
            identity, (reason or problem or "Blender gave no reason").rstrip(".") + ".")
    else:
        note = problem or "job %s wrote nothing that can be shown." % identity
    return note


def ended_without_picture(record, waited, left, problem=None):
    """The answer for a job that ended and has no picture to show. Not an error: the model reads why."""
    return _answer(job_facts(record), waited, left, "job_" + record["status"],
                   "No picture: " + _terminal_note(record, problem))


def picture_result(record, result):
    """A complete retained frame does not imply that its animation job completed."""
    if result.get("error"):
        return result  # A post-decode ownership refusal must not describe a replacement record.
    result["job"] = job_facts(record)
    result.setdefault("rendered", {})["job_complete"] = record["status"] == "done"
    if record["status"] != "done":
        result["note"] += " Job outcome: " + _terminal_note(record)
    return result


def finished(record, show, waited, left):
    """The same terminal result whether the job ended before or during a wait."""
    try:
        return picture_result(record, show())
    except RuntimeError as error:
        return ended_without_picture(record, waited, left, str(error))


def ownership_lost(identity, waited=0.0, left=0.0, outcome=None):
    """Do not describe a replacement job or decode pixels after the original ownership ended."""
    result = {"error": "The original ownership of job %s changed; its wait ended without returning a picture. "
                     "Inspect the job in the current conversation before asking again." % identity,
            "read_only": True, "scene_changed": False, "views": [],
            "waiting": {"ended": "ownership_changed", "waited_seconds": round(waited),
                        "request_seconds_left": round(left)}}
    if outcome is not None:
        if outcome.get("side_effects"):
            result["side_effects"] = outcome["side_effects"]
        result["scene_changed"] = outcome.get("scene_changed", False)
    return result


class JobWait(StudioPending):
    """A view's tool call while the job it names runs; ``Controller._pump_events`` polls it on the main thread."""

    status_text = "Waiting for a render"

    def __init__(self, manager, record, show, controller, *, current=None):
        super().__init__(self._poll_job, self._stop)
        self.manager, self.identity, self.show, self.controller = manager, record["id"], show, controller
        self._current = current
        self._result = self._stopped = None
        self._terminal_waited = self._terminal_left = None
        self.started, self.left, self.pace = _now(), allowance(controller), Pace()
        # Capture ownership: cleanup of an old request must never charge a newer request's account.
        self._wait_account, self._wait_key = _spent(controller), object()
        self._wait_account["active"].add(self._wait_key)
        self.timeout = self.left + GRACE_SECONDS
        self.activity_note = ("Waiting for job %s to finish, to show its picture. Stop ends the wait, not the job; "
                              "Cancel on the job's card ends the job." % self.identity)

    @property
    def stopped_result(self):
        """Controller cancellation also covers reload and disconnect, not just the person's Stop."""
        if self._stopped is not None:
            return self._stopped
        result = self._stopped_answer()
        if self.closed:
            self._stopped = result
        return result

    def _stopped_answer(self):
        record = self.manager.record(self.identity)
        waited, left = self._budget()
        if record is not None and not self._owned():
            return ownership_lost(self.identity, waited, left)
        if record is not None and record["status"] in TERMINAL:
            if record["status"] == "done":
                return {"error": "The wait for job %s was stopped after the job finished; its output can be "
                                 "viewed in a later message." % self.identity,
                        "job": job_facts(record), "read_only": True, "scene_changed": False}
            result = ended_without_picture(record, waited, left)
            result["error"] = "The wait for job %s was stopped. %s" % (self.identity, result["note"])
            return result
        if getattr(self.controller, "document_invalid", False) or getattr(self.controller, "document_leaving", False):
            # load_pre handlers can cancel the wait before the manager marks the job interrupted; a load of the chat's
            # own file (``document_identity``) interrupts the job as another file's does.
            return {"error": "The bound Blender document changed; the wait for job %s was interrupted. "
                             "Check the job card after reload for its final status." % self.identity,
                    "read_only": True, "scene_changed": False,
                    "waiting": {"ended": "document_changed", "waited_seconds": round(waited),
                                "request_seconds_left": round(left)}}
        if record is None:
            message = "The wait for job %s was stopped; the job is no longer on record." % self.identity
        elif record["status"] == "stopping":
            message = "The wait for job %s was stopped; the job is stopping. Check its card for the outcome." % self.identity
        else:
            message = ("The wait for job %s was stopped with its turn; the job itself goes on and "
                       "reports in a later message's jobs block." % self.identity)
        result = {"error": message, "read_only": True, "scene_changed": False}
        if record is not None:
            result["job"] = job_facts(record)
        return result

    def _owned(self):
        return self._current is None or bool(self._current())

    def _budget(self):
        if self._wait_account is None:
            return self._terminal_waited, self._terminal_left
        return _now() - self.started, _remaining(self._wait_account)

    def _stop(self):
        account = self._wait_account
        if account is not None:
            self._terminal_waited, self._terminal_left = _now() - self.started, _remaining(account)
            account["active"].discard(self._wait_key)
            self._wait_account = self._wait_key = None

    def cancel(self):
        if not self.closed:
            super().cancel()
            self._result = self.stopped_result

    def poll(self):
        """Cancellation and completion are terminal; never decode another copy on a late poll."""
        if self._result is not None:
            return self._result
        if self.closed:
            self._result = self.stopped_result
            return self._result
        result = super().poll()
        if result is not None and self._result is None:
            self._result = result
        return self._result

    def _poll_job(self):
        record = self.manager.record(self.identity)
        waited, left = self._budget()
        if record is None:
            self._stop()
            return {"error": "Job %s is no longer on record; start it again if it is still needed" % self.identity,
                    "read_only": True, "scene_changed": False}
        if not self._owned():
            self._stop()
            return ownership_lost(self.identity, waited, left)
        if record["status"] in TERMINAL:
            self._stop()
            if self.closed or not self._owned():
                return self.stopped_result if self.closed else ownership_lost(self.identity, waited, left)
            result = finished(record, self.show, waited, left)
            if not self._owned():
                return ownership_lost(self.identity, waited, left, result)
            if result.get("views"):
                result["rendered"]["waited_seconds"] = round(waited)
            return result
        if left <= 0.0:
            self._stop()
            return still_running(record, waited, 0.0, "wait_used")
        ahead = record if record["status"] != "queued" else self.manager.active()
        remaining = self.pace.remaining(ahead, _now()) if ahead is not None else None
        if remaining is not None and remaining > ETA_FACTOR * left:
            self._wait_account["declined"][self.identity] = (_now(), remaining)
            self._stop()
            return still_running(record, waited, left, "will_not_end_in_time", remaining)
        return None


def begin(manager, record, show, controller, *, current=None):
    """Wait for ``record``'s job, then ``show()`` its picture: a pending result, or the answer when no wait is left."""
    if current is not None and not current():
        return ownership_lost(record["id"])
    left = allowance(controller)
    if left < 1.0:
        return still_running(record, 0.0, 0.0, "wait_used")
    when, remaining = _spent(controller)["declined"].get(record["id"], (0.0, 0.0))
    remaining -= _now() - when
    if remaining > ETA_FACTOR * left:  # Asked again in the request that was told it is too long: no second wait.
        return still_running(record, 0.0, left, "will_not_end_in_time", remaining)
    return JobWait(manager, record, show, controller, current=current)
