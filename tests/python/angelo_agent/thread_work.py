# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The work one thread does, counted instead of timed: the same on a quiet machine and a loaded CI runner.

``count(function)`` runs ``function()`` and returns its value with a ``Work``: the bytecode instructions this
thread ran in Python frames (a comprehension's iterations included) and the calls it made, Python functions
(``calls``) and C functions (``c_calls``, a ``dict.get`` or a ``json.loads``) apart. Only the calling thread is
counted (``sys.settrace`` and ``sys.setprofile`` are per thread), so a UI thread's share of work done with a
reader thread is its own.

One C call's inside is not counted: ``json.loads`` of a 200 KB record is one ``c_call`` whatever its size, so a test
that bounds per-byte work in C also says which functions may run on the thread (``test_native_worker_mirror_reader``
watches them). A bound states the work it allows in these units, so a regression fails on every machine alike, and
a stalled runner never does (a wall-clock bound failed at 3.1 s against 4 ms on a Windows runner, CI 41883033718).
"""

import sys


class Work:
    __slots__ = ("instructions", "calls", "c_calls")

    def __init__(self):
        self.instructions = self.calls = self.c_calls = 0

    @property
    def steps(self):
        """Instructions and C calls: the thread's work, its Python and the C functions it called."""
        return self.instructions + self.c_calls

    def __repr__(self):
        return "Work(instructions=%d, calls=%d, c_calls=%d)" % (self.instructions, self.calls, self.c_calls)


def count(function):
    """``(value, Work)``: ``function()``'s value and the work this thread did running it."""
    work = Work()

    def trace(frame, event, arg):
        frame.f_trace_opcodes = True
        frame.f_trace_lines = False
        if event == "opcode":
            work.instructions += 1
        return trace

    def profile(frame, event, arg):
        if event == "call":
            work.calls += 1
        elif event == "c_call":
            work.c_calls += 1

    previous = sys.gettrace(), sys.getprofile()
    sys.settrace(trace)
    sys.setprofile(profile)
    try:
        value = function()
    finally:
        sys.setprofile(previous[1])
        sys.settrace(previous[0])
    return value, work
