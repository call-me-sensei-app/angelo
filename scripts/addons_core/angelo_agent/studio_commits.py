# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A generation worker's events, written to the library on the main thread (``StudioJobs.pump``). No bpy.

Each event is taken from the queue and its asset's record saved (``StudioLibrary.update``). On Windows another
program can hold that record for longer than a tick's retries (``shared_replace``: 100 ms), most likely a scan of the
file the event before wrote. The save is then refused, and the event is already gone: a finished, paid picture stayed
"generating" with no Stop until the library was read again, a Generation Flow step waited on it for good, and the
agent's call failed although the picture was on disk.

The main thread never waits longer for a file: the longer wait in ``shared_replace`` is for a worker thread's save
that has no later try, and ``test_native_shared_replace_once`` fails if any other module takes it. So a refused record
is shown at once and owed to its file: written again no sooner than ``shared_replace.RETRY_SECONDS`` after each
refusal, and once more when Studio closes. If it never lands, the next launch still reads the result from
the job's own saved state (``job-state.json``, ``StudioLibrary.reload``).

What is owed is the fields the refused save changed, each with the value it replaced:

* a field that still holds the owed value is written as it is;
* a field back at the value it replaced was read again from the old file (a reload): the owed value returns;
* a field holding anything else was changed by another writer since (the person's Stop, a move to a folder): theirs
  stays.

Any other failure of a save (a full disk, a folder that cannot be written) is raised as before.
"""

import time

from . import shared_replace

_ABSENT = object()   # A field the record did not have before the refused save.


class Commits:
    def __init__(self, library, clock=time.monotonic):
        self.library, self.clock = library, clock
        self.owed = {}   # Asset id -> [{field: (value replaced, value owed)}, when its save was last refused]
        self.results = 0   # Events that brought a generation's files (a result), never progress or a status.

    def pump(self, events, active, kinds):
        """Write every waiting event to the library; True when something the window shows changed."""
        changed = self.retry()
        while not events.empty():
            identity, updates = events.get_nowait()
            if updates.pop("_finished", False):
                active.pop(identity, None)
                kinds.pop(identity, None)
                changed = True   # The job left the active set (its card's Stop goes) even when its state didn't change.
            asset = self.library.assets.get(identity)
            record = dict(self._restored(identity, asset), **updates) if asset else None
            if asset and record != asset:   # An event that changes nothing rewrites no library state.
                self.save(record)
                changed = True
                self.results += "files" in updates
        return changed

    def _restored(self, identity, current):
        """``current`` with each owed field that is back at the value it replaced (a reload read the old file) set
        to its owed value again; a field another writer changed since keeps theirs."""
        record = dict(current)
        for name, (replaced, value) in self.owed.get(identity, ({},))[0].items():
            if current.get(name, _ABSENT) == replaced:
                record[name] = value
        return record

    def save(self, record):
        """Save ``record`` over the asset the library holds; refused by a holder, it is shown now and owed."""
        identity = record["id"]
        before = self.library.assets.get(identity, {})
        try:
            self.library.update(record)
        except shared_replace.Refused:
            fields = self.owed.get(identity, ({}, 0.0))[0]
            for name, value in record.items():
                if before.get(name, _ABSENT) != value:
                    # The first value replaced is the file's: a reload returns the field to it.
                    fields[name] = (fields[name][0] if name in fields else before.get(name, _ABSENT), value)
            self.owed[identity] = [fields, self.clock()]
            self.library.assets[identity] = dict(record)
        else:
            self.owed.pop(identity, None)

    def retry(self, final=False):
        """Write what is owed again, each record no sooner than ``RETRY_SECONDS`` after its last refusal (``final``:
        now, as Studio closes). True when a record the window shows changed (a reload had read the old file)."""
        changed = False
        for identity, (_fields, refused) in list(self.owed.items()):
            if not final and self.clock() - refused < shared_replace.RETRY_SECONDS:
                continue
            current = self.library.assets.get(identity)
            if current is None:   # Gone from the library (removed in another window): nothing is owed to it.
                del self.owed[identity]
                continue
            record = self._restored(identity, current)
            changed = record != current or changed
            self.save(record)
        return changed
