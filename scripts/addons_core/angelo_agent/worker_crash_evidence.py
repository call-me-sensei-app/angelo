# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a worker Blender that Angelo had to kill was already crashing: from the evidence of its end.

A stop of Angelo's kills a worker that has not exited ``STOP_GRACE`` seconds after its connection closed. A worker
can be crashing at that moment, and the kill then hid its crash (1.0.8: the person never learned it crashed):

* Blender's crash handler (``source/creator/creator_signals.cc``, ``creator_crash_log``) writes the crash log's
  minimal record first, to ``<temporary folder>/<scene>.crash.txt`` (``BKE_blender_globals_crash_path_get``), then
  the full record for up to ``CRASH_HANDLER_TIMEOUT_S`` (10 s), and only then takes the signal's default action;
* the kernel may still be writing a core (Linux), or macOS's ReportCrash may hold the exiting process while it
  writes its report, often for seconds.

So a kill of Angelo's is Angelo's end only when nothing says the process was crashing (``killed_while_crashing``):

* its real termination status is a crash, not the kill: a POSIX fatal signal other than ``SIGKILL`` (an exit already
  under way may keep the signal it dies of); on Windows an exception code (``0xC0000005`` and every NTSTATUS error)
  or Blender's crash exit (11), never the Job Object's termination code (1);
* or, on Linux, it was already dying of a signal when the kill was sent (``dying``: the kernel sets ``PF_SIGNALED``
  and ``PF_DUMPCORE`` in ``/proc/<pid>/stat`` as a fatal signal begins its core dump; a kill cutting the dump short
  then leaves only ``SIGKILL`` in the status, as Linux CI showed);
* or a crash log is in the process's own Blender temporary folder (``blender_temp``: inside its private folder,
  set as its temporary-folder preference at start, ``worker_main``): every worker's part has the same name, so only
  a folder of its own tells its crash log from another worker's or an earlier run's.

Its private folder goes once its chats are handed back; its crash log is kept first (``keep``) in the folder Angelo's
own crash report reads (``crash_ui.temp_base``), named by its process, so a worker's crash is still offered there.

Standard library only; no ``bpy``.
"""

import shutil
import signal
from pathlib import Path

PF_SIGNALED, PF_DUMPCORE = 0x00000400, 0x00000200   # ``include/linux/sched.h``: killed by a signal; dumping core.
CRASH_EXIT = 11              # Blender's crash handler's own exit code on Windows (``TerminateProcess(.., SIGSEGV)``).
NTSTATUS_ERROR = 0xC0000000  # Every Windows exception code (access violation, stack overflow, ...) is at least this.
TEMP = "t"                   # Its Blender temporary folder inside its private folder (short: ``worker_paths``).


def blender_temp(private):
    """The worker's own Blender temporary folder (its crash log's folder) inside its private folder ``private``."""
    return Path(private) / TEMP


def crash_status(code, platform, killed):
    """Whether return code ``code`` is a crash: never Angelo's own kill (``killed``: SIGKILL on POSIX; on Windows the
    Job Object's code, which is neither of the crash codes)."""
    if code is None:
        return False
    if platform == "win32":
        return code == CRASH_EXIT or code >= NTSTATUS_ERROR
    return code < 0 and not (killed and -code == signal.SIGKILL)


def crash_log(private):
    """A crash log (or its full record's ``.partial`` side file) in the worker's own Blender temporary folder, or
    None: whatever its scene's name (``<scene>.crash.txt``, or the application's before a file was open)."""
    if private is None:
        return None
    try:
        for entry in blender_temp(private).iterdir():
            if entry.name.endswith(".crash.txt") or entry.name.endswith(".crash.txt.partial"):
                return entry
    except OSError:
        pass
    return None


def dying(pid, proc="/proc"):
    """Linux: whether process ``pid`` is already dying of a signal (its kernel flags), read just before Angelo kills
    it; False wherever it cannot be read (another system, a process gone)."""
    try:
        text = Path(proc, str(pid), "stat").read_text(encoding="ascii", errors="replace")
        flags = int(text[text.rindex(")") + 1:].split()[6])   # Fields after ``(comm)``: state, ppid, ..., flags.
    except (OSError, ValueError, IndexError):
        return False
    return bool(flags & (PF_SIGNALED | PF_DUMPCORE))


def killed_while_crashing(code, platform, private, was_dying=False):
    """Why a process Angelo killed was already crashing (the evidence, for its log line and the chat), or None."""
    if crash_status(code, platform, killed=True):
        return "it was crashing when Angelo stopped it (code %s)" % code
    if was_dying:
        return "it was crashing when Angelo stopped it (code %s; dying of a signal when the kill was sent)" % code
    log = crash_log(private)
    if log is not None:
        return "it was crashing when Angelo stopped it (code %s; its crash log %s was written)" % (code, log.name)
    return None


def keep(private, folder, pid):
    """Move the worker's crash log (``<scene>.crash.txt``; never an incomplete ``.partial``) into ``folder`` as
    ``angelo-worker-<pid>-<scene>.crash.txt``, where Angelo's crash report finds it; the new path, or None."""
    if private is None or folder is None:
        return None
    try:
        logs = [entry for entry in blender_temp(private).iterdir() if entry.name.endswith(".crash.txt")]
    except OSError:
        return None
    for log in logs:
        target = Path(folder) / ("angelo-worker-%s-%s" % (pid, log.name))
        try:
            shutil.move(str(log), str(target))   # Its private folder may be on another volume than the report's.
        except OSError:
            continue
        return target
    return None
