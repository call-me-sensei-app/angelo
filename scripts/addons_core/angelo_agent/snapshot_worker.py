# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One background thread per snapshot profile: ingestion and maintenance.

The thread never imports or touches ``bpy``; it only works on private profile
files. Blender's main thread submits tasks and drains ``events`` from its UI
pump (snapshot_service.py). Every task failure becomes an event; nothing is
allowed to escape the thread, and a failed ingestion leaves the full copy as
the authoritative snapshot.

Tasks:

* ``recover``: at start-up, remove leftover full copies of adopted snapshots
  and queue every READY full copy (interrupted ingestion, or snapshots written
  before deduplication) for ingestion, oldest first.
* ``ingest``: snapshot_ingest.ingest_snapshot for one capture.
* ``maintain``: automatic retention with the main thread's fresh protection
  references (only the latest pending request is kept), then store cleanup.
"""

import collections
import queue
import shutil
import threading
from pathlib import Path

from . import background_pauses
from .checkpoints import CheckpointStore
from .checkpoint_policy import clean_policy
from .snapshot_git import committed_project_times
from .snapshot_ingest import IngestCancelled, ingest_snapshot, pending_snapshots, remove_leftover_copies
from .snapshot_maintenance import clean_store, profile_checkpoints
from .snapshot_retention import enforce_retention
from .snapshot_store import SnapshotStore, StoreBusy

BUSY_RETRIES = 3
BUSY_RETRY_DELAY = 2.0


class SnapshotWorker:
    def __init__(self, root, *, compress=True, git=None):
        self.root = Path(root)
        self.compress = compress
        self.git = git
        self.events = queue.Queue()
        self._tasks = collections.deque()
        self._condition = threading.Condition()
        self._thread = None
        self._stopping = False
        self._git_cache = {}

    # -- control (any thread) ---------------------------------------------
    def start(self):
        with self._condition:
            if self._thread is None:
                self._stopping = False
                self._thread = threading.Thread(target=self._run, name="angelo-snapshot-worker", daemon=True)
                self._thread.start()

    def stop(self, timeout=5.0):
        """Ask the thread to stop at a safe boundary; uncommitted data is discarded."""
        with self._condition:
            self._stopping = True
            self._tasks.clear()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def stopping(self):
        return self._stopping

    def _submit(self, task, *, replace_kind=None):
        with self._condition:
            if replace_kind:
                self._tasks = collections.deque(item for item in self._tasks if item[0] != replace_kind)
            self._tasks.append(task)
            self._condition.notify_all()

    def submit_recovery(self):
        self._submit(("recover", 0))

    def submit_ingest(self, session_id, identity, notify=None):
        self._submit(("ingest", 0, session_id, identity, notify))

    def submit_maintenance(self, policy, protected_refs, watermark, notify=None, paused=False):
        """``policy`` is the loaded human policy; ``paused`` when it was unreadable."""
        self._submit(("maintain", 0, policy, tuple(protected_refs), watermark, notify, paused), replace_kind="maintain")

    def pending(self):
        with self._condition:
            return len(self._tasks)

    # -- execution -----------------------------------------------------------
    def run_pending(self):
        """Process queued tasks on the calling thread (host tests, shutdown)."""
        while True:
            with self._condition:
                if not self._tasks or self._stopping:
                    return
                task = self._tasks.popleft()
            self._execute(task)

    def _run(self):
        background_pauses.enable()  # This thread's whole-history passes let Blender's main thread in.
        while True:
            with self._condition:
                while not self._tasks and not self._stopping:
                    self._condition.wait(1.0)
                if self._stopping:
                    return
                task = self._tasks.popleft()
            self._execute(task)

    def _emit(self, kind, **data):
        self.events.put(dict(data, kind=kind))

    def _execute(self, task):
        kind, attempts = task[0], task[1]
        try:
            if kind == "recover":
                self._recover()
            elif kind == "ingest":
                self._ingest(*task[2:])
            elif kind == "maintain":
                self._maintain(*task[2:])
        except IngestCancelled:
            return
        except StoreBusy as error:
            if attempts < BUSY_RETRIES and not self._stopping:
                with self._condition:
                    self._condition.wait(BUSY_RETRY_DELAY)  # stop() wakes this early.
                self._submit((kind, attempts + 1, *task[2:]))
            else:
                self._emit("failed", task=kind, error=str(error), notify=self._notify(task))
        except Exception as error:  # Never let a worker failure reach Blender's log as a traceback.
            self._emit("failed", task=kind, error=str(error) or type(error).__name__, notify=self._notify(task))

    @staticmethod
    def _notify(task):
        if task[0] == "ingest":
            return task[4]
        if task[0] == "maintain":
            return task[5]
        return None

    def _recover(self):
        checkpoints = profile_checkpoints(self.root)
        leftovers = remove_leftover_copies(checkpoints) if SnapshotStore(self.root).exists() else 0
        queued = pending_snapshots(checkpoints)
        for session_id, identity in queued:
            self._submit(("ingest", 0, session_id, identity, None))
        self._emit("recovered", queued=len(queued), leftovers=leftovers)

    def _ingest(self, session_id, identity, notify):
        stats = ingest_snapshot(CheckpointStore(self.root, session_id), identity,
                                cancelled=self.stopping, compress=self.compress)
        self._emit("ingested", stats=stats, notify=notify)

    def _git_signal(self, paths):
        git = self.git or shutil.which("git")
        return committed_project_times(paths, git=git, cache=self._git_cache) if git else {}

    def _maintain(self, policy, protected_refs, watermark, notify, paused):
        summary, probe = {"paused": paused, "removed": []}, {}
        if not paused:
            summary = enforce_retention(profile_checkpoints(self.root), clean_policy(policy),
                                        protected_refs=protected_refs, watermark=watermark,
                                        git_signal=self._git_signal, probe=probe)
            summary["paused"] = False
        # One lock-free scan serves both probes when retention ended at its probe (checkpoint_policy.rst).
        summary["cleanup"] = clean_store(self.root, cancelled=self.stopping, probed=probe.get("inventory"))
        self._emit("maintained", summary=summary, notify=notify)
