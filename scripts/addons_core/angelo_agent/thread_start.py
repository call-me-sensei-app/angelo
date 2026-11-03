# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Starting a thread without leaving "started" behind when it cannot start.

A runner records that its worker runs (a flag, a held slot, a registered handle, a "running" entry) before it starts
the thread, so that a second caller never starts a second one. ``Thread.start`` can raise (``RuntimeError: can't start
new thread`` when the process has no thread to give, or while the interpreter shuts down); a record left in place then
says a worker runs that never existed, and every later job waits on it for good. Two shapes keep the start and its
rollback together:

* ``start(thread, undo)``: ``undo`` puts back exactly what the caller recorded, and the error is raised again for the
  caller to report.
* ``started(thread)``: False instead of the error, for a caller that rolls back and reports in its own branch.
* ``daemon_or_later(name, target)``: for work asked for on Blender's main thread that must still happen (an
  answer, a stop) but may wait long (a worker's launch, a five-minute answer): when no thread can start, the start is
  tried again from a timer each second, never run here, where it would hold the interface for as long as it waits.
* ``start_or_fail(thread, record)``: for work whose end is ``record["done"]`` set, with ``record["error"]`` on a
  failure, read by its owner on the main thread. A start that raises ends the record as that failure at once, so its
  owner reports it and moves on as after any other.

A record made only after a successful start needs neither. Standard library only; ``bpy`` only for the timer.
"""

import threading


def start(thread, undo):
    """Start ``thread``; when it cannot start, call ``undo()`` and raise the start's error again."""
    try:
        thread.start()
    except BaseException:
        undo()
        raise


def daemon(name, target, *args, undo=lambda: None):
    """``target(*args)`` on a new daemon thread named ``name``, started as ``start`` does; returns the thread."""
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    start(thread, undo)
    return thread


def started(thread):
    """Start ``thread``; False when it cannot start (nothing raised), True when it runs."""
    try:
        thread.start()
    except RuntimeError:
        return False
    return True


def daemon_or_later(name, target, register=None):
    """``target()`` on a new daemon thread named ``name``; the thread, or None when none can start yet, and then the
    start is tried again each second from ``register`` (Blender's timers by default) until one starts."""
    thread = threading.Thread(target=target, daemon=True, name=name)
    if started(thread):
        return thread

    def again():
        return None if started(threading.Thread(target=target, daemon=True, name=name)) else 1.0
    if register is None:
        import bpy
        register = lambda function: bpy.app.timers.register(function, first_interval=1.0, persistent=True)  # noqa
    register(again)
    return None


def start_or_fail(thread, record):
    """Start ``thread``; when it cannot start, end ``record`` as a failure: its ``error`` the start's, ``done`` set."""
    try:
        thread.start()
    except RuntimeError as error:
        record["error"] = error
        record["done"].set()
