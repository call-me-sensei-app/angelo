# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One heavy background job at a time: queue, progress, time budget and cancellation.

No Blender dependency; ``job_runners`` supplies a runner per job with this interface:

* ``start()`` applies temporary settings and starts Blender's own job. Only
  explicitly classified preconditions prove no change; unclassified native or
  cleanup failures retain uncertainty about scene effects and restoration;
* ``poll()`` returns None once Blender's job has ended, else ``{"progress": 0..1}``
  plus optional ``frames_done``/``frames_total``;
* ``stop()`` asks Blender's job to stop at its next break check;
* ``finish(stopped)`` restores the settings, publishes or discards the output and
  returns ``(status, reason, receipt)``; ``published`` is then the output's local
  path when one was published (the card's Open);
* ``abandon()`` drops the output without touching scene data (the document closed).

Everything runs on Blender's main thread from a timer (``job_ui``), never inside an
agent turn, a staged program or a commit. A request while a job runs is refused
unless it asks to queue; at most ``MAX_QUEUED`` wait.
"""

import time

from .job_spec import MAX_QUEUED, JobRequestError
from .job_store import ACTIVE, TERMINAL, new_record
from .refusal_codes import RefusalError, error_result
from .ui_text import n_

PROGRESS_SAVE_SECONDS = 2.0
# Receipt notes: English for the model, translated by the chat card where shown.
NOTHING_RAN = n_("Nothing ran, and the job's settings were restored.")
NOT_STARTED = n_("Cancelled before it started; nothing ran.")
NOT_PUBLISHED = n_("Nothing was published.")
FILE_CLOSED = n_("The document was closed or reloaded, which stops Blender's jobs. Nothing was published.")
# ETA only once it means something: some progress after a few seconds.
ETA_MIN_PROGRESS, ETA_MIN_SECONDS = 0.02, 3.0


_shared = None


def shared():
    """The application's manager once ``job_ui`` created it, else None (readable without Blender)."""
    return _shared


def install(manager):
    global _shared
    _shared = manager


class JobStartError(RefusalError, RuntimeError):
    """A startup failure; only explicitly classified preconditions prove no change."""


class JobManager:
    def __init__(self, store, *, clock=time.time, on_change=None, on_finish=None):
        self.store = store
        self.clock = clock
        self.on_change = on_change or (lambda record: None)
        self.on_finish = on_finish or (lambda record: None)
        self.queue = []
        self.running = None
        self.runners = {}

    # Queries -------------------------------------------------------------

    def record(self, identity):
        return self.store.get(identity)

    def active(self):
        """The running (or starting/stopping) record, or None."""
        return self.store.get(self.running) if self.running else None

    def locks_interface(self):
        runner = self.runners.get(self.running) if self.running else None
        return bool(runner is not None and getattr(runner, "locks_interface", False))

    def queued(self):
        return [self.store.get(identity) for identity in self.queue if self.store.get(identity)]

    def has_work(self):
        return bool(self.running or self.queue)

    # Requests ------------------------------------------------------------

    def admit(self, request):
        """Refuse (JobRequestError) a request that could neither start nor queue now; else the running record."""
        busy = self.active()
        if busy is not None and not request.get("queue"):
            raise JobRequestError(
                "Job %s (%s) is still running. Wait for it, cancel it, or start this one with queue=true"
                % (busy["id"], busy["kind"]), code="VALIDATION")
        if busy is not None and len(self.queue) >= MAX_QUEUED:
            raise JobRequestError("%d jobs are already queued; wait for one to finish" % MAX_QUEUED, code="LIMIT_EXCEEDED")
        return busy

    def submit(self, session, call_id, request, runner, identity=None):
        """Admit one validated request: start it now, queue it, or refuse (JobRequestError). ``identity``: the ID its
        approval reserved (job_host), else the next one."""
        busy = self.admit(request)
        record = new_record(identity or self.store.allocate(), session, call_id, request, now=self.clock())
        runner.identity = record["id"]
        record["detail"].update(getattr(runner, "describe", dict)())
        self.runners[record["id"]] = runner
        self.store.add(record)
        if busy is None:
            self._start(record)
        else:
            self.queue.append(record["id"])
            self.on_change(record)
        return record

    def cancel(self, identity, *, by, session=None):
        """Cancel a queued or running job; ``session`` limits an agent to its own chat's jobs."""
        record = self.store.get(identity)
        if record is None or (session is not None and record["session"] != session):
            raise JobRequestError("No job %s in this conversation" % identity, code="OWNERSHIP")
        if record["status"] in TERMINAL:
            raise JobRequestError("Job %s already ended (%s)" % (identity, record["status"]), code="VALIDATION")
        if record["status"] == "queued":
            self.queue = [item for item in self.queue if item != identity]
            self.runners.pop(identity, None)
            self._end(record, "cancelled", by, {"note": NOT_STARTED})
            return record
        if record["status"] != "stopping":
            record.update(status="stopping", reason=by, eta_s=None)
            self.store.save()
            self.on_change(record)
            try:
                self.runners[identity].stop()
            except Exception as error:  # Blender's job may have just ended; the next poll finishes it.
                record["detail"]["stop_error"] = str(error)[:200]
        return record

    # Main-thread lifecycle -----------------------------------------------

    def poll(self):
        """Advance the running job (progress, budget, end) and start the next queued one."""
        record = self.active()
        if record is not None:
            self._poll_running(record)
        if self.running is None:
            while self.queue and self.running is None:
                identity = self.queue.pop(0)
                queued = self.store.get(identity)
                if queued is not None and queued["status"] == "queued":
                    self._start(queued)
        return self.has_work()

    def document_closed(self):
        """A file load or snapshot restore ends Blender's jobs: every active job is interrupted."""
        for identity in ([self.running] if self.running else []) + list(self.queue):
            record, runner = self.store.get(identity), self.runners.get(identity)
            if record is None or record["status"] not in ACTIVE:
                continue
            try:
                if runner is not None and identity == self.running:
                    runner.abandon()
            except Exception:
                pass
            self._end(record, "interrupted", "file_closed", {"note": FILE_CLOSED})
        self.queue, self.running = [], None

    def _start(self, record):
        runner = self.runners[record["id"]]
        record.update(status="starting", started=self.clock())
        self.running = record["id"]
        # A runner must establish its own pre-effect admission phase. Overrides
        # of start() cannot inherit no-change authority from construction.
        runner.startup_effects_possible, runner.startup_unrestored = True, None
        try:
            runner.start()
        except Exception as error:
            if isinstance(error, (JobStartError, JobRequestError)):
                failure = error_result(error, scene_changed=False)
            else:
                failure = {"error": "Blender couldn't start the job (%s)" % type(error).__name__,
                           "error_code": "UNKNOWN", "partial_changes_possible": True}
            if (runner.startup_effects_possible is not False
                    or failure["error_code"] in {"CLEANUP_FAILED", "RECOVERY_REQUIRED"}):
                failure.pop("scene_changed", None)
                failure["partial_changes_possible"] = True
            unrestored = runner.startup_unrestored
            if type(unrestored) is list and unrestored:
                failure["unrestored"] = [path[:200] for path in unrestored[:8] if type(path) is str]
            message = failure.pop("error")
            failure["note"] = (n_("Job startup failed; scene effects and restoration could not be verified.")
                               if failure.get("partial_changes_possible") else NOTHING_RAN)
            self.running = None
            self._end(record, "failed", message[:500], failure)
            return
        record["status"] = "running"
        record["detail"].update(getattr(runner, "describe", dict)())  # Measured again at start (a queued job).
        if getattr(runner, "staging", None) is not None:
            # Local only: a crash leaves it for the next start to remove (job_ui._reconcile).
            record["detail"]["staging"] = str(getattr(runner.staging, "path", runner.staging))
        self.store.save()
        self.on_change(record)

    def _poll_running(self, record):
        runner = self.runners.get(record["id"])
        now = self.clock()
        try:
            state = runner.poll() if runner else None
        except Exception as error:
            state, record["detail"]["poll_error"] = None, str(error)[:200]
        if state is None:
            stopped = record["status"] == "stopping"
            try:
                status, reason, receipt = runner.finish(stopped)
            except Exception as error:
                status, reason, receipt = "failed", "Finishing the job failed (%s: %s)" % (
                    type(error).__name__, str(error)[:200]), {"note": NOT_PUBLISHED}
                try:   # What it published is taken back, so the receipt says true: nothing was published.
                    staging = getattr(runner, "staging", None)
                    if staging is not None and getattr(staging, "published", None) is not None:
                        staging.unpublish()
                    runner.abandon()  # Never leave a hidden staging folder behind.
                except Exception:
                    pass
            self.running = None
            record["detail"].pop("publishing", None)   # It ended: whatever was published is said by its receipt.
            published = getattr(runner, "published", None)
            if published is not None:
                record["detail"]["output_path"] = str(published)  # Local only, for Open; never sent to a model.
            self._end(record, status, reason or (record["reason"] if stopped else ""), receipt)
            return
        progress = min(1.0, max(0.0, float(state.get("progress") or 0.0)))
        elapsed = now - (record["started"] or now)
        record["progress"] = progress
        for key in ("frames_done", "frames_total"):
            if key in state:
                record["detail"][key] = state[key]
        if record["status"] == "running":
            record["eta_s"] = (elapsed * (1 - progress) / progress
                               if progress >= ETA_MIN_PROGRESS and elapsed >= ETA_MIN_SECONDS else None)
            if elapsed > record["request"]["minutes"] * 60:
                self.cancel(record["id"], by="time_budget")
                return
        self.store.save(throttle=PROGRESS_SAVE_SECONDS)
        self.on_change(record)

    def _end(self, record, status, reason, receipt):
        record.update(status=status, reason=reason or "", ended=self.clock(), eta_s=None, receipt=dict(receipt or {}))
        record["detail"].pop("staging", None)  # Published or removed by now.
        if status == "done":
            record["progress"] = 1.0
        self.runners.pop(record["id"], None)
        self.store.save()
        self.on_change(record)
        self.on_finish(record)
