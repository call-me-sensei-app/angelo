# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Work one thread hands another runs only as ``WorkItem``s (doc/angelo/script_confinement.rst, "Work handed to
another thread"; coordinator, 2026-11-15: one class rule for every Angelo pool and cross-thread consumer).

A thread that already runs (a pool's worker, a chat lane) cannot tell who put a callable on its queue: a builtin a
confined script put there would run unconfined. So:

* ``work(function, *args, **kwargs)`` makes a ``WorkItem``: in the app ``_bpy._angelo_guard.WorkItem``, a C object
  that records, when it is made, whether its maker was confined, with nothing a script can set;
* ``run(item, *extra)`` runs one, confined when its maker was, and refuses anything else;
* ``Pool``: the thread pool every long-lived Angelo pool is (``ThreadPoolExecutor``'s ``submit`` and ``shutdown``).
  Its workers hold ``run`` and the item and future types as locals of the running loop, so rebinding a module name
  changes nothing for a loop already running, and they take only a ``WorkItem`` with a real ``Future``.

``test_native_guard_work`` inventories every pool and every cross-thread consumer of callables in Angelo's code and
fails on one that does not go through here. Without ``_bpy._angelo_guard`` (host tests) a Python stand-in records
the same.
"""

import concurrent.futures
import queue
import threading

from . import script_confinement

_guard = script_confinement._guard


class _PythonWorkItem:
    """The stand-in for ``_bpy._angelo_guard.WorkItem`` outside the app."""
    __slots__ = ("function", "args", "kwargs", "made_confined")

    def __init__(self, function, args=(), kwargs=None):
        if not callable(function):
            raise TypeError("WorkItem: not callable")
        self.function, self.args, self.kwargs = function, tuple(args or ()), dict(kwargs or {})
        self.made_confined = script_confinement.confined()


def _python_run(item, *extra):
    if type(item) is not _PythonWorkItem:
        raise script_confinement._PythonConfined(script_confinement.GUARD, "run_work: not a WorkItem")
    function = script_confinement.defer(item.function) if item.made_confined else item.function
    return function(*item.args, *extra, **item.kwargs)


WorkItem = _guard.WorkItem if _guard is not None else _PythonWorkItem
run = _guard.run_work if _guard is not None else _python_run


def work(function, *args, **kwargs):
    """``function(*args, **kwargs)`` as work for another thread."""
    return WorkItem(function, args, kwargs)


def _worker(jobs):
    run_work, item_type, future_type = run, WorkItem, concurrent.futures.Future   # Fixed for this loop's life.
    while True:
        job = jobs.get()
        if job is None:
            return
        try:
            future, item = job
        except (TypeError, ValueError):
            continue   # Not a job this pool made: dropped, never called.
        if type(future) is not future_type or type(item) is not item_type:
            continue
        if not future.set_running_or_notify_cancel():
            continue
        try:
            future.set_result(run_work(item))
        except BaseException as error:   # noqa: BLE001 - the caller reads it from the future.
            future.set_exception(error)


class Pool:
    """A few worker threads that run only ``WorkItem``s (``submit`` makes them). ``daemon``: the interpreter never
    waits for them at exit."""

    def __init__(self, workers, name, daemon=True):
        self._jobs, self._threads, self._closed = queue.SimpleQueue(), [], False
        for index in range(max(1, int(workers))):
            thread = threading.Thread(target=_worker, args=(self._jobs,), name="%s-%d" % (name, index), daemon=daemon)
            thread.start()
            self._threads.append(thread)

    def submit(self, function, *args, **kwargs):
        if self._closed:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = concurrent.futures.Future()
        self._jobs.put((future, work(function, *args, **kwargs)))
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        self._closed = True
        if cancel_futures:
            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if isinstance(job, tuple) and job and type(job[0]) is concurrent.futures.Future:
                    job[0].cancel()
        for _ in self._threads:
            self._jobs.put(None)
        if wait:
            for thread in self._threads:
                if thread is not threading.current_thread():
                    thread.join()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.shutdown(wait=True)
        return False
