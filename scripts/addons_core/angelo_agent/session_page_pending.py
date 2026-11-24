# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a live session page waits on, for a bounded time (2.0.1, doc/angelo/external_mcp.rst, "Live session page").
Pure: no Blender.

A page lists an item (a picture the agent got) only once it can serve it. Each listed item it can't serve yet is, by
what failed:

* **never possible** while it stays listed (an id the page can't serve, ``PICTURE_ID``; one the reference store
  refuses for good, ``reference_images.Refused``: not a UUID, no regular file, over the size limit): dropped at once,
  never read again and never shown (any other ValueError is a defect and ends the page);
* **not ready yet** (no such file yet, ``reference_errors.ImageMissing``; a file that is not a whole picture, a
  capture still being written; any failed look at the disk or read, ``OSError``): read again after a wait that starts
  at ``FIRST`` and doubles to ``LONGEST``, whatever else changes, and given up ``GIVE_UP`` after its first failed
  read. The page then shows it as unavailable. While it stays listed and a page is open its file is looked at (opened
  and its descriptor's ``fstat``, never its bytes) when the page is built (a change in the chat) and otherwise at most
  every ``LONGEST``, so a picture finished in an idle chat is shown too.

A given-up item is read again (``Pending.worth_reading``) when its file's mark (size and modification time) changed;
once more, for every new mark, ``SETTLE_S`` after it was first seen (local elapsed time, past any step a file system
stamps in, so a same-size rewrite inside a stamp's step is read whatever the file system's clock says, a share's
included), and sooner when a mark last read while ``change_clock.settled`` said no has settled since; and, after a read
that failed (it raised: a sharing violation, a share's reconnect; or its bytes were no whole picture and their count
differed from the file's ``fstat``: it vouches for no mark), again at the first look at or after its own retry time,
timed from that failed read and doubling from ``LONGEST`` to ``GIVE_UP`` while the file stays the same (a build never
pushes it back; idle, at most one ``LONGEST`` later: break-it reviews of b4dbd6b3c78, MUST-1, NIT-1, NIT-3, and of
c8f7c307dd9, NIT-2), or at once for a file changed since that read. Bytes that are a whole picture are shown whatever
size ``fstat`` reports. Never more for an unchanged file.
Nobody viewing: no look at all. Every look and read goes through ``reference_images``: checks and read on one
descriptor. Each look and read is blocking I/O on the main thread (``Pages.pump``'s timer): on a hung network volume
it blocks for the client's timeout, as the rest of Angelo's state there does (NIT-2, pre-existing).

A listed item never read yet is due (so a viewer leaving between two looks can't skip its first read). A tick where
nothing is due reads nothing, and the page is published again only when what it shows changed (``serve``'s
``changed``), never for a wait alone. A defect (any other exception) ends that page, as a build's does. Every kind of
item a page waits on goes through ``Pending``, so none is polled without end (break-it reviews of ffe232b8253, NIT-1;
of 4afc19981c3; of 09db060, NIT-2 and 3; of 6daffb057ee; of e0fc461e471).
"""

FIRST = 0.5      # Seconds before the second read of an item not ready yet.
LONGEST = 8.0    # The longest wait between reads, and between a given-up item's looks.
GIVE_UP = 60.0   # Seconds after its first failed read that an item is shown as unavailable.
# Seconds after a mark is first seen before its one more read: past any step a file system stamps in (2 s either side
# of the clock at the coarsest, ``angelo_change_clock``), so that read comes after every write the same stamp could
# hide, whatever the file system's clock says.
SETTLE_S = LONGEST


class Pending:
    """The items a page waits on, by key: when each is due for its next read, which were given up (with their file's
    mark then) and which were dropped."""

    def __init__(self, clock):
        self._clock = clock
        # Key -> {"first", "wait", "next": time or None once given up, "mark": (size, mtime) of its last read that
        # returned bytes, "fresh": when that mark was first seen (None once read SETTLE_S after), "early": that read
        # came while the mark was unsettled, "unread": the last read failed, "seen": the mark that read saw (another
        # is read at once), "retry": when it is tried again, "backoff": the wait after the next failure, "look": a
        # given-up item's next look, "dropped"}.
        self._items = {}

    def due(self, key):
        """Whether ``key`` may be read now: never tried, or its wait is over (never once given up or dropped)."""
        item = self._items.get(key)
        return item is None or (item["next"] is not None and self._clock() >= item["next"])

    def any_due(self, keys, served):
        """Whether any of ``keys`` (the listed items) not ``served`` may be read now, a never-tried one included."""
        return any(not served(key) and self.due(key) for key in keys)

    def look_due(self, key):
        """Whether a given-up ``key``'s file is due its look (its descriptor's stat, never its bytes): every
        ``LONGEST``."""
        return self.given_up(key) and self._clock() >= self._items[key]["look"]

    def looked(self, key):
        self._items[key]["look"] = self._clock() + LONGEST

    def given_up(self, key):
        item = self._items.get(key)
        return item is not None and item["next"] is None and not item["dropped"]

    def dropped(self, key):
        item = self._items.get(key)
        return item is not None and item["dropped"]

    def worth_reading(self, key, mark, settled):
        """Whether a given-up ``key`` whose file is now ``mark`` (``settled``: its stamps past the clock's step) is
        read again. See the module docstring."""
        item = self._items[key]
        if item["unread"]:   # A failed read vouches for nothing: tried again at its own retry time, never pushed
            return self._clock() >= item["retry"] or mark != item["seen"]   # back by builds; at once for a new mark.
        if mark != item["mark"]:
            return True
        if item["early"] and settled:
            item["early"] = False
            return True
        if item["fresh"] is not None and self._clock() - item["fresh"] >= SETTLE_S:
            item["fresh"], item["early"] = None, False
            return True
        return False

    def failed(self, key, mark=None, settled=True, unread=False):
        """A try of ``key`` failed: a read of a file not whole (``mark``), a read that failed (``unread``, with the
        mark it saw) or a failed look (neither). Its next read waits longer. True when this gave it up."""
        now = self._clock()
        item = self._items.get(key)
        if item is None:
            item = self._items[key] = {"first": now, "wait": FIRST, "next": None, "mark": None, "fresh": None,
                                       "early": False, "unread": False, "seen": None, "retry": None,
                                       "backoff": LONGEST, "look": None, "dropped": False}
        elif item["next"] is None:   # Given up already: read again, and still not whole.
            _remember(item, mark, settled, now, unread, given_up=True)
            return False
        else:
            item["wait"] = min(item["wait"] * 2, LONGEST)
        _remember(item, mark, settled, now, unread, given_up=False)
        if now - item["first"] >= GIVE_UP:
            item["next"], item["look"] = None, now + LONGEST
            return True
        item["next"] = now + item["wait"]
        return False

    def drop(self, key):
        self._items[key] = {"first": self._clock(), "wait": 0.0, "next": None, "mark": None, "fresh": None,
                            "early": False, "unread": False, "seen": None, "retry": None, "backoff": LONGEST,
                            "look": None, "dropped": True}

    def done(self, key):
        self._items.pop(key, None)

    def keep(self, keys):
        """Forget the items no longer listed (one listed again later starts anew)."""
        keys = set(keys)
        for key in [key for key in self._items if key not in keys]:
            del self._items[key]


def _remember(item, mark, settled, now, unread, given_up):
    """What a failed try tells: a failed read, only that and the mark it saw (it vouches for no mark; it is tried
    again at its own retry time, or at once for a new mark); a failed look, nothing (only a look that succeeds can say
    the file changed); a read of a file not whole, its mark: a new mark is first seen now, and the same one
    settles once a read came while it was settled or ``SETTLE_S`` after it."""
    if unread:   # Retried from this failure on: LONGEST, doubling to GIVE_UP while it fails on the same file once
        if not given_up or not item["unread"] or mark != item["seen"]:   # given up (the wait has its own backoff).
            item["backoff"] = LONGEST
        item["unread"], item["seen"], item["retry"] = True, mark, now + item["backoff"]
        item["backoff"] = min(item["backoff"] * 2, GIVE_UP)
        return
    if mark is None:
        return
    item["unread"] = False
    if mark != item["mark"]:
        item["mark"], item["fresh"], item["early"] = mark, now, not settled
        return
    if settled:
        item["early"] = False
    if item["fresh"] is not None and now - item["fresh"] >= SETTLE_S:
        item["fresh"], item["early"] = None, False


def serve(site, pending, identities, store, look_again=False):
    """The pictures ``identities`` (in the page's order) as the page can show them: (shown, unavailable, changed).
    ``site`` holds what it serves (``has_picture``, ``publish_picture``: False for a picture not whole); ``store`` the
    conversation's ``ReferenceStore`` (``reference_images`` reads it). Reads only while a page is open and only what
    is due. A given-up item's file is looked at with ``look_again`` (a build) or when its look is due (every
    ``LONGEST``), and read only when ``worth_reading``. ``changed``: what the page shows changed."""
    pending.keep(identities)
    before = _view(site, pending, identities)
    if site.viewers > 0:
        for identity in identities:
            if site.has_picture(identity) or pending.dropped(identity):
                continue
            if pending.due(identity) or (pending.given_up(identity) and (look_again or pending.look_due(identity))):
                _try(site, pending, identity, store)
    after = _view(site, pending, identities)
    return after[0], after[1], after != before


def _try(site, pending, identity, store):
    """One look at a listed picture, checked and read on one descriptor (``reference_images``)."""
    from . import reference_images
    from .reference_errors import ImageMissing
    from .session_page_site import PICTURE_ID
    if not isinstance(identity, str) or not PICTURE_ID.match(identity):
        return pending.drop(identity)
    given_up = pending.given_up(identity)
    if given_up:
        pending.looked(identity)   # Its next look, whatever this one finds.
    try:   # Every look at the disk: a missing file or a failed look waits, as a file not whole yet does.
        with reference_images.opened(store, identity) as (_path, status, handle):
            mark, settled = _mark(status)
            if given_up and not pending.worth_reading(identity, mark, settled):
                return None
            try:
                data = reference_images.read(store, handle)
            except OSError:
                return pending.failed(identity, mark, settled, unread=True)
            if len(data) != status.st_size:   # Short or grown: shown if whole, but it vouches for no mark (NIT-1).
                if _held_still(status, reference_images.restat(handle)) and site.publish_picture(identity, data):
                    return pending.done(identity)   # One file's bytes, never a torn read.
                return pending.failed(identity, mark, settled, unread=True)
    except (ImageMissing, OSError):
        return pending.failed(identity)
    except reference_images.Refused:   # Never this conversation's picture while listed; anything else ends the page.
        return pending.drop(identity)
    if site.publish_picture(identity, data):
        return pending.done(identity)
    return pending.failed(identity, mark, settled)



def _held_still(status, again):
    """Whether a mismatched read's file held still through it: its size and time read again after the read are those
    taken at open, and those stamps are past the file system's step (``change_clock.settled``), so a same-size rewrite
    within one clock tick could not keep them (integrator, batch 3: test_native_change_clock)."""
    from . import change_clock
    return (change_clock.settled(status, change=False)
            and (again.st_size, again.st_mtime_ns) == (status.st_size, status.st_mtime_ns))

def _mark(status):
    """The read descriptor's size and modification time, compared at a later look, and whether its stamps are past
    the file system's step (``change_clock.settled``), which only makes a same-size rewrite's read come sooner."""
    from . import change_clock
    return (status.st_size, status.st_mtime_ns), change_clock.settled(status, change=False)


def _view(site, pending, identities):
    shown = [identity for identity in identities if site.has_picture(identity) or pending.given_up(identity)]
    return shown, [identity for identity in shown if not site.has_picture(identity)]


# The live page's pictures (``session_page``) ------------------------------------------------------------------------

def shown(controller, entry, state):
    """``state`` with only the pictures the page can show (never one it would ask for in vain) and those given up,
    ``unavailable``: none is read while nobody looks (the send path)."""
    entry["want"] = list(state["pictures"])
    state["pictures"], state["unavailable"], _ = _pictures(controller, entry, look_again=True)
    return state


def retry(controller, entry):
    """``Pages``' tick with no change in the chat: True when what the page shows changed (a picture it waits on
    arrived or was given up). Nothing is read when nothing is due."""
    waiting, site, want = _waiting(entry), entry["site"], entry.get("want", ())
    if not (waiting.any_due(want, site.has_picture) or any(waiting.look_due(key) for key in want)):
        return False
    return _pictures(controller, entry)[2]


def _waiting(entry):
    import time
    return entry.setdefault("waiting", Pending(time.monotonic))   # ``Pages`` makes it on its own clock.


def _pictures(controller, entry, look_again=False):
    return serve(entry["site"], _waiting(entry), entry.get("want", ()), controller.references, look_again)
