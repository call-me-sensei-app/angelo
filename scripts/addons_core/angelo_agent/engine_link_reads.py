# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reading a project's folders for the engine link off Angelo's main thread, bounded in entries and time. No bpy.

A project can be huge or on a slow or network drive, so resolving an asset (``engine_link_assets.inside``'s check for
Angelo's working file, the agent's lookup by a plain name, ``engine_link_assets.named``) never runs on the main thread:
* **``Lookup``** runs one resolve on a daemon thread under a deadline (``READ_S``) that the folder walks read through
  ``expired``, so a walk stops at its time bound as at its entry bound, and counts as unfinished (refused, never
  guessed). The caller waits at most ``QUICK_S`` on the main thread, which most resolves need, then polls.
* **``Inbox``** gives each editor's requests to ``engine_link._handle`` in the order that editor sent them: a request
  whose asset is still being resolved holds that editor's later ones (a "close" never overtakes the "open" sent before
  it), never another editor's (break-it 9-M2: their acknowledgements, which PUSH and SAVE wait for, kept flowing).
* A walk past its time raises ``Late`` (``expired`` alone only says so): the resolve is unfinished, refused as
  unreadable, never as an asset outside the project or a project too large (break-it 9-L1).

doc/angelo/engine_link.rst, "Reading the project", is the contract.
"""

import threading
import time
from collections import deque

READ_S = 10.0    # One resolve's folder reading at most; past it the asset counts as unreadable (refused).
QUICK_S = 0.05   # The main thread's wait for a resolve before it polls instead.
RESOLVED = frozenset({"connected", "disconnected", "status", "close"})   # Requests that never resolve an asset.
UNREADABLE = ("Angelo couldn't read the project in time to check {asset} (a very large project, or a slow or network "
              "drive); try again")

_local = threading.local()


class Late(Exception):
    """A folder walk ran past its ``Lookup``'s time: the resolve is unfinished (``Lookup.outcome`` "late")."""


def expired():
    """Whether this thread's reading is past its deadline (never on a thread with none, as the main thread)."""
    deadline = getattr(_local, "deadline", None)
    return deadline is not None and time.monotonic() > deadline


class Lookup:
    """``function()`` on a daemon thread under a deadline; ``outcome`` is ("done", value), ("error", exception) or
    ("late", None) once ``ready``."""

    def __init__(self, function, seconds=None):
        self.deadline, self._outcome = time.monotonic() + (READ_S if seconds is None else seconds), None
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(function,), daemon=True, name="engine-link-read")
        self._thread.start()

    def _run(self, function):
        _local.deadline = self.deadline
        try:
            self._outcome = ("done", function())
        except Late:
            self._outcome = ("late", None)
        except Exception as error:   # The caller decides; an unexpected one is its own refusal there.
            self._outcome = ("error", error)
        finally:
            _local.deadline = None
            self._done.set()

    def wait(self, seconds=QUICK_S):
        self._done.wait(seconds)
        return self.ready()

    def ready(self):
        return self._done.is_set() or time.monotonic() > self.deadline + QUICK_S

    @property
    def outcome(self):
        return self._outcome if self._done.is_set() else ("late", None)   # Past its time and still reading.

    def value(self, asset=""):
        """The function's value; raises its exception, or an ``AssetError`` "unreadable" when it ran out of time."""
        kind, found = self.outcome
        if kind == "done":
            return found
        if kind == "error":
            raise found
        from .engine_link_assets import AssetError
        raise AssetError("unreadable", str(asset)[:200], UNREADABLE)


class Unread:
    """A resolve that couldn't start: finished at once, refused as unreadable (``Lookup``'s face)."""

    def __init__(self, asset, error):
        from .engine_link_assets import AssetError
        self.error = AssetError("unreadable", str(asset)[:200], UNREADABLE)
        self.cause = error

    def wait(self, seconds=0.0):
        return True

    def ready(self):
        return True

    @property
    def outcome(self):
        return ("error", self.error)

    def value(self, asset=""):
        raise self.error


def resolver(client, header, info):
    """``Inbox``'s resolve for ``engine_link``: the editor's engine adapter resolving the request, or None when this
    Angelo has no adapter for it (``engine_link._handle`` refuses that one itself)."""
    from . import engine_link_assets as assets
    adapter = assets.adapter(info.get("engine", "godot"))
    return (lambda: adapter.resolve(header)) if adapter is not None else None


class Inbox:
    """The engine's requests, each with what its editor said when it arrived (``info``: an editor that left is
    forgotten at the next drain), its arrival number (``seq``: a card answers the request sent last, whichever was
    read first, break-it 10-N2) and its asset resolved off the main thread (``resolve(client, header, info)`` returns
    the function to run, or None for a request that resolves nothing)."""

    def __init__(self):
        self._queue, self._server, self._seq = deque(), None, 0

    def ready(self, server, resolve):
        """Each request whose turn has come, as (client, header, info, lookup or None, seq). An editor's requests come
        in the order it sent them: one whose asset is being read holds that editor's later ones (a "close" never
        overtakes its "open"; at most one resolve per editor), never another editor's, and never its own status
        acknowledgements, which PUSH and SAVE wait for. A new server (the link started again) starts a new queue."""
        if server is not self._server:
            self._queue, self._server = deque(), server
        for client, header in server.drain():
            self._seq += 1
            self._queue.append([client, header, server.info_of(client), None, False, self._seq])
        self._queue = deque(entry for entry in self._queue if not entry[4])   # Linear: compacted once a pass.
        waiting = set()   # Editors whose earliest request here is still being read.
        for entry in list(self._queue):
            if entry[0] in waiting and entry[1].get("type") != "status":
                continue
            if entry[1].get("type") not in RESOLVED and entry[3] is None:
                try:
                    function = resolve(entry[0], entry[1], entry[2])
                    entry[3] = Lookup(function) if function is not None else False
                except Exception as error:   # No thread to read on (break-it 10-N4): this request is refused,
                    entry[3] = Unread(entry[1].get("asset", ""), error)   # and the link's timer goes on.
                done = not entry[3] or entry[3].wait()   # Most resolves end within this one short wait.
            else:
                done = not entry[3] or entry[3].ready()
            if not done:
                waiting.add(entry[0])
                continue
            entry[4] = True
            yield entry[0], entry[1], entry[2], entry[3] or None, entry[5]


inbox = Inbox()   # The engine link's, one per process (``engine_link._tick``).
