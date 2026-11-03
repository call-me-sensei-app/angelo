# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a script's time went in ``bpy.ops``: each operator it called, how often and for how long. No bpy at import.

Evidence: in the 1.0.6 Town benchmark of record (13d18cae0f9, 2026-10-28) four scripts held Blender's main thread
for 66 s to 427 s, all of it in ``bpy.ops.mesh.primitive_*_add`` calls, while the same model built 1,891 objects
through ``bpy.data`` in 9.4 s. Blender updates the scene's dependency graph before and after every ``bpy.ops`` call
from Python (``bpy_operator_function.cc``); an added object makes that update rebuild the whole graph, so each call
that adds one costs in proportion to the scene's object count (measured in background: 30 ms at 2,000 objects, 62 ms
at 4,000, 125 ms at 8,000; 0.03 ms through ``bpy.data`` at any count). 41 of the run's 90 scripts used those
operators; nothing told their model what they cost, and the script result said only that it completed.

``Clock`` wraps the operators a script creates while it runs, as ``script_booleans.Applied`` does (stacked on whatever
``_op_create_function`` is then, and put back on the same ``bpy.ops`` however the script ends). Only the outermost call
counts: an operator that calls ``bpy.ops`` itself (a Python operator, a handler) is timed once, as the script called
it. ``outcome`` is the ``bpy_ops`` field of the script's result: the calls, their seconds and the ``TOP`` costliest
operators, with ``COST`` once calls that added objects took ``NOTE_SECONDS`` or more. Measurements and a fact of
Blender's, never an instruction.

``len(bpy.data.objects)`` walks every object (no length callback, ``rna_main.cc``), so a call is counted only while its
operator's last call took ``COUNTED_FROM`` or more, and ``COUNT_SHARE`` times what a count itself took: a cheap
operator in a long loop pays for the timing alone in a scene of any size, and an operator that adds objects, which
costs more as the scene grows, is counted from the point its cost could matter.
"""

import time

TOP = 3
NOTE_SECONDS = 1.0   # A pause a person notices; below it the measurements alone stand.
COUNTED_FROM = 0.001   # Seconds.
COUNT_SHARE = 20   # A call's two counts stay within a tenth of its operator's last call.
# Model-visible (never translated): how Blender runs a bpy.ops call, a fact of its source.
COST = ("Blender updates the scene's dependency graph before and after each bpy.ops call; an added object makes that "
        "update rebuild the whole graph, so each call that adds one takes longer the more objects the scene has. "
        "Objects and meshes made through bpy.data add no update of their own.")


class _Timed:
    """An operator whose outermost calls are counted and timed while its script runs; everything else, its
    documentation and printed form included, is the operator's own."""

    __slots__ = ("_operator", "_clock", "_name")

    def __init__(self, operator, clock, name):
        self._operator, self._clock, self._name = operator, clock, name

    def __call__(self, *args, **kwargs):
        return self._clock.call(self._name, self._operator, args, kwargs)

    def __getattr__(self, name):
        if name in _Timed.__slots__:   # Unset while ``copy`` or ``pickle`` builds one: never a recursion.
            raise AttributeError(name)
        return getattr(self._operator, name)

    @property
    def __doc__(self):
        return self._operator.__doc__

    def __repr__(self):
        return repr(self._operator)

    def __str__(self):
        return str(self._operator)


class Clock:
    """The ``bpy.ops`` calls of one script while it runs (module docstring); a context manager."""

    def __init__(self, bpy):
        self._bpy, self._ops, self._real, self._active = bpy, None, None, False   # A kept callable counts nothing.
        self._depth, self._count_seconds = 0, 0.0
        # "module.function" -> [calls, seconds, seconds of the calls that added objects, the last call's seconds]
        self.calls = {}

    def __enter__(self):
        ops = getattr(self._bpy, "ops", None)
        real = getattr(ops, "_op_create_function", None)
        if real is None:   # A stand-in bpy: nothing to time.
            return self

        def create(module, function):
            return _Timed(real(module, function), self, "%s.%s" % (module, function))
        try:
            ops._op_create_function = create
        except Exception:  # noqa: BLE001  Optional evidence: the script runs as written and nothing is stated.
            return self
        self._ops, self._real, self._active = ops, real, True
        return self

    def __exit__(self, *_exception):
        self._active = False
        if self._real is not None:   # The module it replaced, even if the script rebound ``bpy.ops``.
            self._ops._op_create_function, self._ops, self._real = self._real, None, None
        return False

    def _objects(self):
        begun = time.perf_counter()
        try:
            return len(self._bpy.data.objects)
        except Exception:  # noqa: BLE001  A stand-in or a replaced file: no count, so no added objects.
            return None
        finally:
            self._count_seconds = time.perf_counter() - begun

    def call(self, name, operator, args, kwargs):
        if not self._active or self._depth:   # Kept past the script, or inside an operator already timed.
            return operator(*args, **kwargs)
        self._depth += 1
        row = self.calls.setdefault(name, [0, 0.0, 0.0, COUNTED_FROM])
        before = self._objects() if row[3] >= max(COUNTED_FROM, COUNT_SHARE * self._count_seconds) else None
        begun = time.perf_counter()
        try:
            return operator(*args, **kwargs)
        finally:
            spent = time.perf_counter() - begun
            after = self._objects() if before is not None else None
            self._depth -= 1
            row[0] += 1
            row[1] += spent
            row[3] = spent
            if before is not None and after is not None and after > before:
                row[2] += spent

    def outcome(self):
        """``{"bpy_ops": ...}`` for a script that called any operator; ``{}`` otherwise."""
        if not self.calls:
            return {}
        seconds = sum(row[1] for row in self.calls.values())
        costliest = sorted(self.calls.items(), key=lambda item: (-item[1][1], item[0]))[:TOP]
        facts = {"calls": sum(row[0] for row in self.calls.values()), "seconds": round(seconds, 2),
                 "costliest": [[name, row[0], round(row[1], 2)] for name, row in costliest]}
        if sum(row[2] for row in self.calls.values()) >= NOTE_SECONDS:
            facts["cost"] = COST
        return {"bpy_ops": facts}
