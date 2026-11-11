# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A start that never completed makes the next launch enable only the add-ons that ship with Angelo.

Contract: doc/angelo/app_identity.rst, "A start that did not finish". A third-party add-on that crashes natively as
it loads (Blender 5.3 #166395, #166397) ends the process before any Python error handling runs, and the preferences
that enable it are already saved (an update's settings import copies them), so every later launch would end the
same way and the app would never open again. So each launch in a window keeps a **start record** from before the
first add-on loads (``scripts/startup/angelo_start_guard.py``: Blender registers startup modules before it enables
add-ons) until its start has completed:

* The record is one file per process in ``<config>/angelo-start``, created exclusively and **locked** for as long as
  the process starts (``flock``; on Windows ``msvcrt.locking``). The operating system releases the lock when the
  process ends, however it ends. A later launch that can lock a record whose JSON is complete has found a start that
  never completed. One it cannot lock names its process, host and boot (``identity``): its process gone, it is found
  all the same (a dead process's lock can linger a moment); running, the launch waits up to ``HELD_WAIT`` (all
  records together) for it to end, since a crashed process may still be ending (the Windows crash dialog's Restart
  starts the next launch first). A lock that goes while its process runs is a start that completed; one still held
  then belongs to an instance still starting, so two instances start side by side; one from another host or boot (a
  shared settings folder) is never judged by its process id. A completed record is emptied before its lock goes, so
  one whose removal is held up never reads as a crash.
  A record cut short (a process that ended between creating and writing it, before any add-on) is no crash; it is
  removed once it is ``INCOMPLETE_AGE`` old.
* That launch leaves every **third-party** add-on off before Blender enables any: a user add-on or a user
  extension. Angelo's own add-ons, Blender's hidden core ones and system extensions load as always. The preferences
  keep them off once this start completes (they are saved then), and one notice names them
  (``angelo_agent.safe_start_notice``). The person turns one back on in Preferences > Add-ons.
* The start **completes** once Angelo's own start-up has run what it owes this launch (``intro.startup_done``: the
  layout, the settings import of an update and the start-up file it reloads), and the window has run
  ``SETTLE_SECONDS`` more; or after ``LONGEST_START`` whatever happens. A crash after that is not a crash at start.
  A quit before it ends the record too (``unregister`` runs on exit).
* Blender enables add-ons again on every **preference read** later in the session (Angelo's settings import, Revert
  to Saved Preferences, factory preferences, an application template): ``prefs_reload`` runs that step inside
  ``enabling``, which holds a record through it and ``SETTLE_SECONDS`` after. A settings import copies the
  preferences before it enables what they list, so without it a native crash there would end every later launch
  the same way.

Background runs and ``--factory-startup`` keep no record: no window, no person's add-ons. The cost of a normal start
is one small file created, locked, written and later removed, and a half-second timer for the first seconds.

The pure parts (records, the third-party test, completion) need no bpy: tests/python/angelo_agent/
test_native_safe_start.py.
"""

import contextlib
import sys
import time
from pathlib import Path

from angelo_start_records import (  # noqa: F401  The records' mechanics, named here as before.
    FOUND_MARK, HELD_POLL, HELD_WAIT, INCOMPLETE_AGE, LOCK_OFFSET, SUFFIX, Held, applies, begin, identity, interrupted,
    _alive, _complete, _examine, _here, _lock, _open_record, _pid, _remove, _unlock)

FOLDER = "angelo-start"
SETTLE_SECONDS = 2.0      # The window keeps running this long after Angelo's start-up is done: still part of the start.
LONGEST_START = 180.0     # A start that never settles still completes: a later crash is not a crash at start.
TICK = 0.5
EXTENSION_PREFIX = "bl_ext."

_state = {"held": None, "left_off": [], "completion": None, "saved": False, "settled_left_off": False,
          "folder": None}   # "folder": where this run keeps its records (None: it keeps none).


def third_party(modules, shipped):
    """The add-ons in ``modules`` (the preferences' order) that do not ship with Angelo."""
    return [module for module in modules if not shipped(module)]


def _inside(path, roots):
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(resolved == root or root in resolved.parents for root in roots)


def shipped_check(addon_paths, system_roots, repos, hidden_core=frozenset()):
    """``shipped(module)`` for this installation: ``addon_paths`` are the add-on folders (``addon_utils.paths()``),
    ``system_roots`` the app's own script and extension folders, ``repos`` ``{repository module: is a system
    repository}``. An extension ships when its repository is a system one; any other add-on when its file lies in a
    system root. One installed nowhere ships too: there is nothing of it to load, so it is never named."""
    roots = [Path(root).resolve() for root in system_roots]

    def shipped(module):
        if module in hidden_core:
            return True
        if module.startswith(EXTENSION_PREFIX):
            repo = (module.split(".", 2) + [""])[1]
            return bool(repos.get(repo, False))
        for folder in addon_paths:
            for candidate in (Path(folder) / module / "__init__.py", Path(folder) / (module + ".py")):
                if candidate.is_file():
                    return _inside(candidate, roots)
        return True
    return shipped


class Completion:
    """When this start has completed: ``SETTLE_SECONDS`` after Angelo's start-up was first done, or after
    ``LONGEST_START``."""

    def __init__(self, started):
        self.started, self.ready_at = started, None

    @classmethod
    def settling(cls, now):
        """Already ready: it completes ``SETTLE_SECONDS`` from ``now`` (a preference read that finished)."""
        completion = cls(now)
        completion.ready_at = now
        return completion

    def step(self, now, ready):
        if self.ready_at is None and (ready or now - self.started >= LONGEST_START):
            self.ready_at = now
        return self.ready_at is not None and now - self.ready_at >= SETTLE_SECONDS


def left_off():
    """The add-on modules this launch left off (empty on a normal start)."""
    return list(_state["left_off"])


# Blender's side: the startup module calls ``start`` and ``stop``.

def _shipped_here(bpy):
    import addon_utils
    roots = [p for p in (bpy.utils.system_resource('SCRIPTS'), bpy.utils.system_resource('EXTENSIONS')) if p]
    repos = {}
    for repo in getattr(bpy.context.preferences.extensions, "repos", ()):
        try:
            repos[repo.module] = getattr(repo, "source", "") == 'SYSTEM'
        except AttributeError:
            continue
    hidden = getattr(addon_utils, "_addons_hidden_core", frozenset())
    return shipped_check(addon_utils.paths(), roots, repos, hidden)


def _leave_off(bpy):
    addons = bpy.context.preferences.addons
    modules = third_party([entry.module for entry in addons], _shipped_here(bpy))
    for module in modules:
        while module in addons:
            addons.remove(addons[module])
    return modules


def start(bpy):
    """Before any add-on loads: leave third-party add-ons off when a start never completed, then keep this start's
    record until it completes."""
    folder = _state["folder"] = Path(bpy.utils.user_resource('CONFIG')) / FOLDER
    try:
        if interrupted(folder):
            _state["left_off"] = _leave_off(bpy)
            if _state["left_off"]:
                bpy.context.preferences.is_dirty = True   # Blender's save on quit keeps them off too.
            print("Angelo: the last start did not finish; started without:", ", ".join(_state["left_off"]) or "-")
    except Exception as error:   # The start goes on whatever this check meets.
        print("Angelo: start check:", error, file=sys.stderr)
    _state["held"] = begin(folder)
    _state["completion"] = Completion(time.monotonic())
    if _state["held"] is not None or _state["left_off"]:
        _watch()


@contextlib.contextmanager
def enabling():
    """Around Blender enabling add-ons during the session (``prefs_reload`` wraps every preference read's
    ``addon_utils.reset_all``): a record is held through it and ``SETTLE_SECONDS`` after, so a native crash there
    makes the next launch leave third-party add-ons off. During the start its own record covers it. Nothing in a run
    that keeps no record (background, factory startup, a folder that cannot hold one)."""
    folder = _state["folder"]
    if folder is not None and _state["held"] is None:
        _state["held"], _state["completion"] = begin(folder), None
    try:
        yield
    finally:
        if _state["held"] is not None:
            completion = _state["completion"]
            if completion is None or completion.ready_at is not None:   # Not a start still running: settle again.
                _state["completion"] = Completion.settling(time.monotonic())
            _watch()


def _watch():
    import bpy
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=TICK, persistent=True)   # Outlives a start-up file reload.


def _ready():
    import angelo_live_windows as live_windows   # Windows with a screen (in scripts/modules, not Angelo's add-on).
    import bpy
    if not live_windows.windows(bpy.context.window_manager):
        return False
    if not _angelo_enabled():   # Still about to load (Angelo's start-up timer enables it), or not used at all.
        return "angelo_agent" not in bpy.context.preferences.addons and not _layout_running()
    from angelo_agent import intro
    return intro.startup_done()


def _angelo_enabled():
    """Whether Angelo's add-on has registered. Read quietly: importing any of its modules before then would run
    them ahead of its registration, and ``addon_utils.check`` warns about a package imported but not enabled."""
    return bool(getattr(sys.modules.get("angelo_agent"), "__addon_enabled__", False))


def _layout_running():
    layout = sys.modules.get("angelo_default_layout")
    return bool(layout and getattr(layout, "running", lambda: False)())


def _tick():
    try:
        ready = _ready()
    except Exception as error:
        print("Angelo: start check:", error, file=sys.stderr)
        ready = True
    if not _state["completion"].step(time.monotonic(), ready):
        return TICK
    _completed()
    return None


def _completed():
    import bpy
    if _state["left_off"] and not _state["settled_left_off"]:   # Once: a later read's settling saves nothing.
        _state["settled_left_off"] = True
        try:
            bpy.ops.wm.save_userpref()   # They stay off: a later normal start must not load them again.
            _state["saved"] = True
        except Exception as error:
            print("Angelo: could not save the preferences without the add-ons left off:", error, file=sys.stderr)
        if _angelo_enabled():
            try:
                from angelo_agent import safe_start_notice
                safe_start_notice.show(_state["left_off"])
            except Exception as error:
                print("Angelo: start notice:", error, file=sys.stderr)
    stop()


def stop(exiting=False):
    """The start completed, or the app quits: its record goes. A start that left add-ons off and quits before it
    completed keeps its record (closed, so unlocked): the next launch leaves them off again, as their being off was
    not yet saved."""
    held, _state["held"] = _state["held"], None
    if held is None:
        return
    if exiting and _state["left_off"] and not _state["saved"]:
        held.close()
    else:
        held.release()
