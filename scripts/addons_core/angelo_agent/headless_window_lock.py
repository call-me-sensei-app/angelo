# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One GUI Angelo per state folder while a hidden-window session runs (doc/angelo/mcp_headless_window.rst,
"Hand-over"). No bpy at import; ``fcntl``, used where hidden sessions exist (macOS, ``headless_window.supported``).

Every GUI Angelo there takes ``LOCK`` in the state folder at register and holds it for its life: an exclusive ``flock``,
which the kernel releases when the process ends however it ends, so a lock is never stale. The file holds its
holder's record: pid, start identity, and its hidden session's id and app (none for a window).

**The record is the one authority for "still a hidden session"** (review 4, M-A): every signal to a hidden app, from
its launcher (``signal_allowed``), the person's launch or End it, goes only after re-reading the record and finding
that very session there with a verified pid (``live_session``). Anything else, unreadable or unverified included,
means never signal.

* A hidden session that can't take the lock at once ends before it does anything (``take`` fails closed).
* The person's own launch (``hand_over``) while a hidden session holds it **reveals** that session (SIGUSR2: it comes
  up as the person's window, as it is) and then ends itself. A session that hasn't come up within
  ``REVEAL_SECONDS`` is asked about, never killed on a timer: the person chooses End it (SIGTERM: it stops, saves and
  quits; after ``END_SECONDS`` still the same session, SIGKILL) or Quit this launch (``ask``).
"""

import errno
import json
import os
import signal
import sys
import time
from pathlib import Path

LOCK = "gui.lock"
REVEAL_SECONDS = 10.0
END_SECONDS = 45.0        # End it: a stop, a save beside the source and a quit, before anything harder.
VERIFY_SECONDS = 2.0      # A record is re-read this long while its pid doesn't verify (its holder may be writing it).
_held = {}   # The descriptor this process holds the lock with.


def started(pid):
    """The process's start identity: its start time as ``ps -o lstart=`` prints it in the C locale and UTC whatever
    the caller's LANG, LC_* or TZ (review 3, M-1); with its pid it names one process. Read from the kernel
    (``process_identity``): every launch reads it, and a launch never starts a process to (2.0.1 final 12484412217)."""
    from . import process_identity
    return process_identity.started(pid)


def verified(pid, binary, start, started_of=started, name_of=None):
    """Whether ``pid`` is still the process the record names: started at ``start`` and running this Angelo binary.
    Anything that can't be read or compared (a record anyone could have written) is "not verified", never a raise."""
    try:
        if not isinstance(start, str) or not start or not binary or started_of(pid) != start:
            return False
        if name_of is None:
            from . import process_identity
            name_of = process_identity.executable_name
        name = name_of(pid)
        return isinstance(name, str) and bool(name) and name.lower() == Path(binary).name.lower()
    except Exception:   # noqa: BLE001  Never verified on doubt: no signal follows.
        return False


def _try(path):
    """The lock's descriptor, or None while another process holds it; any other failure raises (never "held")."""
    import fcntl
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                         0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return None
        raise
    return descriptor


def _write(descriptor, session, app, start):
    record = json.dumps({"pid": os.getpid(), "start": start, "session": session, "app": app}).encode("utf-8")
    os.pwrite(descriptor, record, 0)
    os.ftruncate(descriptor, len(record))


def take(state_directory, session=None, wait=0.0, clock=time.monotonic, sleep=time.sleep, started_of=started,
         app=None):
    """Take the lock (waiting up to ``wait``) and write this process's record; False when another holds it. The
    identity is read before the lock is taken, so the record is written at once (review 4, S-2)."""
    start = started_of(os.getpid())
    if _held.get("fd") is not None:
        _write(_held["fd"], session, app, start)
        return True
    path = Path(state_directory) / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = clock() + wait
    while True:
        descriptor = _try(path)
        if descriptor is not None:
            break
        if clock() >= deadline:
            return False
        sleep(0.25)
    _held["fd"], _held["start"] = descriptor, start
    _write(descriptor, session, app, start)
    return True


def mark(session, app=None):
    """Rewrite this holder's record (``show``: a hidden session becomes the person's window, ``session`` None)."""
    if _held.get("fd") is not None:
        _write(_held["fd"], session, app, _held.get("start"))


def release():
    """Clear the record and release the lock (closing the descriptor releases the flock)."""
    descriptor = _held.pop("fd", None)
    _held.pop("start", None)
    if descriptor is not None:
        try:
            os.ftruncate(descriptor, 0)
        finally:
            os.close(descriptor)


def holder(state_directory):
    """The holder's record (``{pid, start, session, app}``), or None when unreadable."""
    try:
        record = json.loads((Path(state_directory) / LOCK).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def read_verified(state_directory, check, clock=time.monotonic, sleep=time.sleep):
    """The holder's record once its pid verifies (re-read for up to ``VERIFY_SECONDS``: its holder may be writing
    it), else None: an unverified record is never acted on."""
    deadline = clock() + VERIFY_SECONDS
    while True:
        record = holder(state_directory) or {}
        pid = record.get("pid")
        if type(pid) is int and pid != os.getpid() and check(pid, record.get("start")):
            return record
        if clock() >= deadline:
            return None
        sleep(0.2)


def live_session(state_directory, session, pid, binary, started_of=started, is_verified=None):
    """Whether the record names hidden session ``session`` held by ``pid``, and that pid is still that very
    process: the one test before any signal to a hidden app (review 4, M-A)."""
    record = holder(state_directory) or {}
    if not session or record.get("session") != session or record.get("pid") != pid:
        return False
    check = is_verified or (lambda candidate, start: verified(candidate, binary, start, started_of))
    return bool(check(pid, record.get("start")))


def _escape(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def ask(app, busy=False, run=None):
    """The person's choice when a hidden session holds the folder and doesn't come up: True to end it (``busy``),
    else only told; never a signal without their choice. The dialog is macOS's (``osascript``), as hidden sessions
    are: elsewhere nothing is started and the answer is "quit this launch"."""
    from .ui_text import iface_
    import subprocess
    if run is None and sys.platform != "darwin":
        return False
    run = run or subprocess.run
    name = app or iface_("an app")
    if busy:
        text = iface_("Angelo is busy working for {app} without its window and hasn't come up yet. End that work now "
                      "(it stops and saves first), or quit this launch and leave it running?", app=name)
    else:
        text = iface_("Angelo is working for {app} without its window, and this launch can't reach it. Quit it "
                      "from Activity Monitor, or wait for it to finish, then open Angelo again.", app=name)
    end, keep = iface_("End it"), iface_("Quit")
    buttons = '{"%s", "%s"}' % (_escape(keep), _escape(end)) if busy else '{"%s"}' % _escape(keep)
    # i18n: the AppleScript below carries text translated above.
    script = ('display dialog "%s" buttons %s default button "%s" with title "Angelo" with icon caution'  # i18n: code
              % (_escape(text), buttons, _escape(keep)))
    try:
        answer = run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(busy) and answer.returncode == 0 and answer.stdout.strip() == "button returned:" + end


def _signal(kill, pid, number):
    """Whether ``number`` reached ``pid``; any failure is "not sent" (an ended, foreign or impossible pid)."""
    try:
        kill(pid, number)
    except Exception:   # noqa: BLE001  ProcessLookupError, PermissionError, an out-of-range pid's OverflowError.
        return False
    return True


def hand_over(state_directory, binary, clock=time.monotonic, sleep=time.sleep, kill=os.kill, started_of=started,
              is_verified=None, asked=ask, say=print):
    """The person's own launch: take the lock, or reveal the hidden session that holds it. Returns what happened:
    ``free`` (taken), ``held`` (the person's own other window), ``revealed`` (this launch ends; the hidden session's
    window is the person's now), ``ended`` (the person chose End it; taken), ``kept`` (this launch quits)."""
    if take(state_directory, wait=0, clock=clock, sleep=sleep, started_of=started_of):
        return "free"
    check = is_verified or (lambda pid, start: verified(pid, binary, start, started_of))
    record = read_verified(state_directory, check, clock, sleep)
    if record is None:   # Unverified: never acted on; the person is told and this launch quits.
        if take(state_directory, wait=0, clock=clock, sleep=sleep, started_of=started_of):
            return "free"   # It ended meanwhile.
        asked((holder(state_directory) or {}).get("app"), busy=False)
        return "kept"
    session, pid, app = record.get("session"), record["pid"], record.get("app")
    if not session:
        return "held"   # The person's own other window: theirs to run, as two Blender windows are.

    def live():
        return live_session(state_directory, session, pid, binary, started_of, is_verified)

    say("Angelo: bringing up the window of the work Angelo is doing for %s." % (app or "an app"))  # i18n: log line
    if not (live() and _signal(kill, pid, signal.SIGUSR2)):
        return "free" if take(state_directory, wait=REVEAL_SECONDS, clock=clock, sleep=sleep,
                              started_of=started_of) else "kept"
    deadline = clock() + REVEAL_SECONDS
    while clock() < deadline:
        if take(state_directory, wait=0, clock=clock, sleep=sleep, started_of=started_of):
            return "free"   # It ended instead (it was ending, or its app closed it): the lock is this launch's.
        current = holder(state_directory) or {}
        if current.get("pid") == pid and not current.get("session") and check(pid, current.get("start")):
            return "revealed"
        sleep(0.25)
    if take(state_directory, wait=0, clock=clock, sleep=sleep, started_of=started_of):
        return "free"   # Never a "busy" dialog for a session that has just ended (review 5, 4).
    if not asked(app, busy=True):   # It hasn't come up: never ended on a timer, only by the person's choice.
        if live():
            _signal(kill, pid, signal.SIGUSR1)   # Quit: the reveal this launch asked for is withdrawn.
        return "kept"
    if live() and _signal(kill, pid, signal.SIGTERM):   # It stops, saves beside the source and quits.
        if take(state_directory, wait=END_SECONDS, clock=clock, sleep=sleep, started_of=started_of):
            return "ended"
        if live() and _signal(kill, pid, signal.SIGKILL):
            if take(state_directory, wait=REVEAL_SECONDS, clock=clock, sleep=sleep, started_of=started_of):
                return "ended"
    return "ended" if take(state_directory, wait=0, clock=clock, sleep=sleep, started_of=started_of) else "kept"
