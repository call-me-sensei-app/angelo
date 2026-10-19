# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reads of a datablock many objects share, computed once per read pass.

A whole-scene search or a discovery page captures every object with one
``ReadBudget``. Objects that share a material (an asset kit, instanced props)
re-read the same node graph once per object: on a 2,000-prop scene that was
most of the pass. ``shared_read`` computes such a read once per pass and, for
every later object, replays the first read's side effects in their original
order instead of reading again:

* every budget charge, so the pass exhausts its work budget at exactly the
  same point (a charge that fails still leaves the earlier ones taken);
* every limitation appended to the caller's list, before or after a charge
  that fails, exactly as the read would have appended them;
* every resource registered with the observation identity map.

The result itself is shared between those objects' captures, which only hash
and walk it; it must never be mutated. A read that raises is not remembered,
so the next object reads again and raises again. The memo belongs to the
pass's budget and is dropped with it: nothing is reused across passes, edits
or documents. Budgets without one (host-test stand-ins) read every time.
"""

_TAKE, _APPEND, _RESOURCE = range(3)


class Recorder:
    """Stands in for the budget, the limitations list and the resource formatter during a first read."""

    def __init__(self, work, unsupported=None, resource=None):
        self.work, self.unsupported, self._resource, self.log = work, unsupported, resource, []

    def take(self, elements=0, items=0, packed=0):
        self.work.take(elements=elements, items=items, packed=packed)
        self.log.append((_TAKE, (elements, items, packed)))

    def append(self, value):
        self.unsupported.append(value)
        self.log.append((_APPEND, value))

    def resource(self, *args):
        result = self._resource(*args)
        self.log.append((_RESOURCE, args))
        return result

    def __getattr__(self, name):  # Anything else a reader consults (the deadline) is the budget's.
        return getattr(self.work, name)


def shared_read(work, key, read, unsupported=None, resource=None):
    """``read(work, unsupported, resource)`` once per pass for ``key``.

    Later calls with the same key replay the first call's effects on ``work``,
    ``unsupported`` and ``resource`` and return its (shared, read-only) result.
    """
    memo = getattr(work, "shared", None)
    if memo is None or key is None:
        return read(work, unsupported, resource)
    entry = memo.get(key)
    if entry is None:
        recorder = Recorder(work, unsupported, resource)
        value = read(recorder, recorder if unsupported is not None else None,
                     recorder.resource if resource is not None else None)
        memo[key] = (value, recorder.log)
        return value
    value, log = entry
    for kind, item in log:
        if kind == _TAKE:
            work.take(elements=item[0], items=item[1], packed=item[2])
        elif kind == _APPEND:
            unsupported.append(item)
        else:
            resource(*item)
    return value
