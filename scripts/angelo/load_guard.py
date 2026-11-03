# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Refuse heavy work on a shared machine while another run holds its benchmark lock (standard library only).

A machine that runs timed benchmark trials marks each run with a lock directory, ``/tmp/angelo-billed.lock``
(``ANGELO_BILLED_LOCK`` names another), whose ``owner`` file holds the holder's token, by convention
``NAME-PID-...``. A host suite, a build or a loop over test files started beside a timed trial skews its timings,
so the suite runners and build scripts ask this guard first:

* No lock: every check passes and nothing is written (machines that run no benchmarks, CI, Windows).
* The holder's own work passes: ``ANGELO_BILLED_TOKEN`` equals the owner token (slot scripts export it), or the
  caller descends from a process whose PID the token names (a slot script's children).
* Anyone else's heavy work (a whole suite, a build) is refused. Light work (test files named one by one) passes
  while this machine's light runs stay within LIGHT_FILES files per LIGHT_WINDOW seconds, counted for every caller
  in a ledger beside the lock: one file after an edit passes, a loop over many files is a suite and is refused.

From a shell script, before the heavy part::

    python3 scripts/angelo/load_guard.py "macOS release build" || exit $?
    python3 scripts/angelo/load_guard.py --files 2 "host test files" || exit $?

The exit status is 0 to go ahead, or REFUSED (75, EX_TEMPFAIL: try again later) with the reason on stderr.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

LOCK_ENV = "ANGELO_BILLED_LOCK"
DEFAULT_LOCK = "/tmp/angelo-billed.lock"
TOKEN_ENV = "ANGELO_BILLED_TOKEN"
LIGHT_FILES = 10      # test files per window on the machine, every caller together
LIGHT_WINDOW = 600    # seconds
REFUSED = 75


def lock_path(environ):
    return Path(environ.get(LOCK_ENV) or DEFAULT_LOCK)


def lock_owner(lock):
    """The holder's token; '' while the holder has not written it yet; None when nobody holds the lock."""
    if not lock.is_dir():
        return None
    try:
        return (lock / "owner").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def ancestor_pids(pid=None, run=subprocess.run):
    """This process and its ancestors below PID 1, from one ``ps`` listing; empty when ``ps`` fails."""
    try:
        listing = run(["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    parents = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            parents[int(fields[0])] = int(fields[1])
    pid, chain = os.getpid() if pid is None else pid, set()
    while pid > 1 and pid not in chain:
        chain.add(pid)
        pid = parents.get(pid, 0)
    return chain


def ledger_path(lock):
    return lock.with_name(lock.name + ".light-runs")


def recent_files(ledger, now, window=LIGHT_WINDOW):
    """Test files the ledger records in the last ``window`` seconds."""
    try:
        lines = ledger.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    total = 0
    for line in lines:
        fields = line.split("\t")
        try:
            stamp, files = float(fields[0]), int(fields[1])
        except (IndexError, ValueError):
            continue
        if now - window <= stamp <= now + 60:
            total += files
    return total


def record(ledger, now, files, owner, what):
    try:
        with open(ledger, "a", encoding="utf-8") as stream:
            stream.write("%d\t%d\t%d\t%s\t%s\n" % (now, files, os.getpid(), owner, what.replace("\t", " ")))
    except OSError:
        pass   # An unwritable ledger only loses the count; the lock itself stays authoritative.


def check(files=None, what="this work", environ=None, now=None, ancestors=ancestor_pids):
    """None to go ahead, or the refusal to print. ``files`` None is heavy work; a number is that many test files."""
    environ = os.environ if environ is None else environ
    lock = lock_path(environ)
    owner = lock_owner(lock)
    if owner is None:
        return None
    if owner and environ.get(TOKEN_ENV) == owner:
        return None
    token_pids = {int(part) for part in owner.split("-") if part.isascii() and part.isdigit() and int(part) > 1}
    if token_pids and token_pids & ancestors():
        return None
    now = time.time() if now is None else now
    if files is None:
        reason = "A whole suite or a build is heavy work."
    else:
        ledger = ledger_path(lock)
        used = recent_files(ledger, now)
        if used + files <= LIGHT_FILES:
            record(ledger, now, files, owner, what)
            return None
        reason = ("%d test file(s) already ran on this machine in the last %d minutes; %d more would go over the limit "
                  "of %d, and a loop over test files is a suite." % (used, LIGHT_WINDOW // 60, files, LIGHT_FILES))
    try:
        held = "held %d min" % ((now - lock.stat().st_mtime) // 60)
    except OSError:
        held = "just released"
    return ("Refused: %s, because another run holds %s (owner %s, %s). Work beside a timed benchmark trial "
            "skews it.\n%s\nGo ahead only by waiting until the lock is free or for your own slot in its queue, by "
            "running on a machine without this lock, or by running a few test files by name (at most %d per %d "
            "minutes on this machine).\nNever copy the owner token into %s: it belongs to the holder's slot.\n"
            % (what, lock, owner or "not written yet", held, reason, LIGHT_FILES, LIGHT_WINDOW // 60, TOKEN_ENV))


def refused(what, files=None, stream=None, **options):
    """REFUSED after printing the reason to ``stream`` (stderr), or 0 to go ahead."""
    reason = check(files, what, **options)
    if reason is None:
        return 0
    print(reason, end="", file=stream or sys.stderr, flush=True)
    return REFUSED


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exit %d while another run holds this machine's benchmark lock."
                                     % REFUSED)
    parser.add_argument("what", help="the work, for the message (for example \"macOS release build\")")
    parser.add_argument("--files", type=int, default=None, metavar="N",
                        help="light work: N test files named one by one (default: heavy work)")
    args = parser.parse_args(argv)
    if args.files is not None and args.files < 1:
        parser.error("--files must be at least 1")
    return refused(args.what, args.files)


if __name__ == "__main__":
    sys.exit(main())
