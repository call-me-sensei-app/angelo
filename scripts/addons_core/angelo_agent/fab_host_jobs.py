# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""One running Fab operation and one latest queued operation; browser I/O never runs in a draw/pump."""
from dataclasses import dataclass, field
import threading
import time
import weakref

from .fab_catalog import FabError


class Stop:
    def __init__(self, *events, seconds=900):
        self.events, self.deadline = events, time.monotonic() + seconds

    def is_set(self):
        return any(event.is_set() for event in self.events) or time.monotonic() >= self.deadline

    def check(self):
        if any(event.is_set() for event in self.events):
            raise FabError('CANCELLED')
        if time.monotonic() >= self.deadline:
            raise FabError('EXPIRED')


@dataclass(eq=False)
class Job:
    request: int
    kind: str
    work: object
    cancel: object = field(default_factory=threading.Event)
    done: object = field(default_factory=threading.Event)
    value: object = None
    error: object = None
    cleanup: object = None
    lock: object = field(default_factory=threading.Lock, repr=False)

    def stop(self):
        self.cancel.set()
        with self.lock:
            if self.done.is_set() and self.value is not None and self.cleanup is not None:
                self.cleanup(self.value)
                self.value = None


    def take(self):
        if not self.done.is_set():
            raise RuntimeError('Fab job is not complete')
        if self.error is not None:
            raise self.error
        if self.cancel.is_set():
            raise FabError('CANCELLED')
        value, self.value = self.value, None
        return value


class Worker:
    """Only this owner runs commands; a superseded pending command never starts.

    The caller retains its exact Job until it takes the result. At most one
    completed superseded value remains with its caller until taken or canceled.
    Cleanup callbacks are strictly in-memory Session/cache-lease revocation, never
    browser/file work. No provider/account objects go to model text.
    """
    def __init__(self, *, idle=None):
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.idle = idle
        self._threads = weakref.WeakSet()
        self._pending = self._running = self._thread = None

    def submit(self, job, *, replace=True, previous=None):
        if previous is not None:
            previous.cancel.set()
            if previous.done.is_set() and previous.value is not None and previous.cleanup is not None:
                previous.cleanup(previous.value)  # Bounded in-memory revocation only; caller retains on failure.
                previous.value = None
                previous.error = FabError('CANCELLED')
        with self._lock:
            if not replace and (self._pending is not None or self._running is not None):
                raise FabError('UNAVAILABLE')
            if self._pending is not None:
                pending = self._pending
                pending.cancel.set()
                pending.error = FabError('CANCELLED')
                pending.done.set()
            if self._running is not None and replace:
                self._running.cancel.set()
            self._pending = job
            self._wake.set()
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='angelo-fab-owner', daemon=True)
                self._threads.add(self._thread)
                try:
                    self._thread.start()
                except RuntimeError as error:
                    self._threads.discard(self._thread)  # An unstarted thread has no live ownership to join.
                    self._thread = self._pending = None
                    job.error = error
                    job.done.set()
                    raise
        return job

    def _run(self):
        while True:
            with self._lock:
                job, self._pending = self._pending, None
                if job is None:
                    if self.idle is None:
                        self._thread = None
                        return
                    self._wake.clear()
                else:
                    self._running = job
            if job is None:
                # Same serialized owner, no public request or second browser executor.
                # The callback handles its exact connection cleanup/error publication.
                keep = self.idle()
                if not keep:
                    with self._lock:
                        if self._pending is None:
                            self._thread = None
                            return
                else:
                    self._wake.wait(0.25)  # No spin when auth/transfer guards skipped I/O; enqueue wakes immediately.
                continue
            try:
                if job.cancel.is_set():
                    raise FabError('CANCELLED')
                job.value = job.work(job.cancel)
                if job.cancel.is_set():
                    raise FabError('CANCELLED')  # One cleanup attempt, serialized with done publication below.
            except (Exception, SystemExit, KeyboardInterrupt) as error:
                job.error = error if isinstance(error, Exception) else FabError('UNAVAILABLE')
            finally:
                with job.lock:
                    if job.cancel.is_set() and job.value is not None and job.cleanup is not None:
                        try:
                            job.cleanup(job.value)
                            job.value = None
                        except Exception as error:
                            job.error = error  # Retain the value for explicit caller cleanup/retry.
                    with self._lock:
                        self._running = None
                        job.done.set()

    def busy(self):
        with self._lock:
            return self._pending is not None or self._running is not None

    def join(self, seconds):
        # A cleared scheduling slot is not proof that its thread has returned.
        # Threading's live registry retains each thread; the weak set never
        # accumulates completed threads or privately owns their old job values.
        deadline = time.monotonic() + seconds
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        with self._lock:
            return all(not thread.is_alive() for thread in self._threads)
