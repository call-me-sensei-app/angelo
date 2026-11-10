# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The records Angelo's window keeps in ``<state>/mcp/`` for the MCP launcher, and the markers that keep the launcher
closed while a write of one has not landed (``external_clients``: ``access.json``, ``organization.json``).

* A record carries its ``intent``: when the window's value last changed (0: as it started, from its saved settings).
* A window writes a record with its own marker beside it (``<record>.<pid>-<token>.owed``, holding the record it means
  to write; the token keeps a reused process ID apart): the launcher fails closed while any marker is there, so a
  switch turned off or a policy tightened never reads as the record's older, looser value, also when Windows refuses
  the write (another program holds the file). A write that lands removes its marker; one refused leaves it, and its
  writer tries again (``shared_replace.RETRY_SECONDS`` later), rewriting the marker at every attempt.
* A marker untouched for ``STALE`` seconds (either way: a clock stepped back too) was left by a writer that stopped
  trying (it quit, or stalls). Another window claims it (renames it to its own name and touches it, so the first
  writer's next attempt makes a fresh one it never removes, and no third window claims it meanwhile) and writes the
  record with the newest intent among its own, the claimed ones and the record as it stands (``newest``; the stricter
  on a tie; the strictest when any can't be read), never looser than its own value: a takeover never loosens the
  record against the window still open, nor lets an older change outweigh a newer one. A plain write is the window's
  own value, as before (between live windows, the last change written wins).
* A launcher read that finds markers younger than a second waits up to ``SETTLE`` for the write in progress.

Standard library only; no bpy (the launcher imports it too).
"""

import glob
import json
import math
import os
import time
import uuid
from pathlib import Path

from . import shared_replace

OWED = ".owed"
STALE = 3 * shared_replace.RETRY_SECONDS
SETTLE = 0.2       # Seconds a launcher read waits for a write in progress (every marker younger than ``FRESH``).
FRESH = 1.0
_TOKEN = uuid.uuid4().hex[:8]   # This process's own: a marker of a dead process with the same ID is not its.


def markers(root, name):
    """Every marker of the record ``name`` in ``root`` (this window's, another's, claimed ones)."""
    return sorted(Path(root).glob(glob.escape(name) + ".*" + OWED))


def write(root, name, record, claimed=()):
    """Write ``record`` as ``root/name`` (owner-only, replaced atomically) with this process's marker beside it while
    it is written and when it fails. ``claimed``: markers (``claim_stale``) this write answers, removed with this
    process's own once it lands. A marker that can't go raises ``shared_replace.Refused``: the record stays owed
    (failing closed) and is tried again later."""
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    own = root / ("%s.%d-%s%s" % (name, os.getpid(), _TOKEN, OWED))
    marker = os.open(own, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(marker, "w", encoding="utf-8") as handle:
        json.dump(record, handle)   # What this writer means: a takeover weighs it (``newest``). Touched each try.
    temporary = root / (".%s.%s.tmp" % (name, uuid.uuid4().hex))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        shared_replace.replace(temporary, root / name)   # The launcher reads it sharing delete (read_regular).
    except BaseException:
        shared_replace.discard(temporary)
        raise
    for marker in (own, *claimed):
        try:
            os.remove(marker)
        except FileNotFoundError:
            pass
        except OSError as error:   # Held: the record stays owed (failing closed), tried again after the pause.
            raise shared_replace.Refused(error.errno, error.strerror, os.fspath(marker)) from error


def claim_stale(root, name, now=None):
    """``[(path, record or None)]``: the markers of ``name`` untouched for ``STALE`` seconds, each renamed to this
    process's own name first and touched (never claimed twice), so only what was claimed is removed later."""
    now = time.time() if now is None else now
    claimed = []
    for marker in markers(root, name):
        mine = marker.with_name("%s.%d-%sc%s%s" % (name, os.getpid(), _TOKEN, uuid.uuid4().hex[:8], OWED))
        try:
            if abs(now - os.lstat(marker).st_mtime) < STALE:
                continue
            shared_replace.replace(marker, mine, told=False)   # A fresh name: never over another file.
            os.utime(mine)
        except OSError:
            continue   # Gone, claimed by another window meanwhile, or not ours to move: it stays (closed).
        try:
            record = json.loads(shared_replace.read_text(mine))
        except FileNotFoundError:
            continue   # Claimed away by another window meanwhile: its write answers it.
        except (OSError, ValueError, RecursionError):
            record = None   # Unknown: the strictest wins (``newest``).
        claimed.append((mine, record if isinstance(record, dict) else None))
    return claimed


def current(root, name):
    """``[record or None]``: the record as it stands (none when there is no file; None when it can't be read)."""
    try:
        record = json.loads(shared_replace.read_text(Path(root) / name))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError):
        return [None]
    return [record if isinstance(record, dict) else None]


def intent(record):
    """When the change a record carries was made (``time.time``); 0 for none (as a window started)."""
    value = record.get("intent") if isinstance(record, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        value = float(value)
    except OverflowError:
        return 0.0
    return value if math.isfinite(value) else 0.0


def newest(own, others, stricter, strictest):
    """The record a takeover writes: the newest ``intent`` among ``own`` and ``others`` (claimed markers, the
    record as it stands), ``stricter(a, b)`` of a tie; ``strictest`` when any of them can't be read (None)."""
    if any(other is None for other in others):
        return strictest
    best = own
    for other in others:
        if intent(other) > intent(best):
            best = other
        elif intent(other) == intent(best):
            best = stricter(best, other)
    return best


def settled(root, name):
    """Whether every marker of ``name`` went within ``SETTLE`` seconds: only while each is a write in progress (at
    most ``FRESH`` old) is it waited for; a failed one (or one from a clock out of step) is not."""
    deadline = time.monotonic() + SETTLE
    while True:
        found = markers(root, name)
        if not found:
            return True
        try:
            failed = any(not 0.0 <= time.time() - os.lstat(marker).st_mtime <= FRESH for marker in found)
        except OSError:
            failed = False   # One went meanwhile: look again.
        if failed or time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def weigh(root, name, own, claimed, stricter, strictest):
    """The record this window writes: its ``own``; with ``claimed`` (a takeover) the newest change among its own,
    theirs and the record as it stands, never looser than its own. A plain write is the window's own value, its
    start-up value included (between live windows the last write wins, as before)."""
    if not claimed:
        return own
    standing = current(root, name)
    return stricter(own, newest(own, [other for _path, other in claimed] + standing, stricter, strictest))


def stricter_access(first, second):
    """Of two access records: allowed only when both allow it; the later intent (the merged record's "as of" time:
    at least as strict as the newer change, so nothing older outweighs it)."""
    return {"version": 1, "allow_local_cli": first.get("allow_local_cli") is True and second.get("allow_local_cli")
            is True, "intent": max(intent(first), intent(second))}


def stricter_policy(first, second, org_policy, strictest):
    """Of two organization records: a switch on only where both allow it, edits ask where either asks, "no
    organization" (None) only when both say so; a policy that can't be read counts as ``strictest``."""
    values = [record.get("organization", strictest) for record in (first, second)]
    known = [org_policy(value) or org_policy(strictest) for value in values if value is not None]
    merged = None
    if known:
        merged = {}
        for policy in known:
            for key, value in policy.items():
                if key.startswith("allow"):
                    merged[key] = merged.get(key, True) and value
                elif key == "requireAskForEdits":
                    merged[key] = merged.get(key, False) or value
                elif key == "adminContact":
                    merged[key] = merged.get(key) or value
    return {"version": 1, "organization": merged, "intent": max(intent(first), intent(second))}
