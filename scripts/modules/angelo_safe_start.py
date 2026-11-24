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

* The person's add-ons that **fail** as they load (raise in their import or ``register``) during a start or such an
  enabling are collected (``angelo_addon_failures``); once it completes, the ones still enabled are named with their
  causes on one notice that offers **Turn off** for each. Nothing is turned off or undone by itself, as in stock
  Blender, so each start names them again until the person turns one off or it loads.

Background runs and ``--factory-startup`` keep no record: no window, no person's add-ons. The cost of a normal start
is one small file created, locked, written and later removed, a half-second timer for the first seconds, a cached
shipped-or-not lookup per add-on Blender enables, and two small in-place writes of ``.loading`` around each add-on of
the person's.

The pure parts (records, the third-party test, completion) need no bpy: tests/python/angelo_agent/
test_native_safe_start.py.
"""

import contextlib
import sys
import time
from pathlib import Path

from angelo_start_records import (  # noqa: F401  The records' mechanics, named here as before.
    CRASHED_MARK, FOUND_MARK, HELD_POLL, HELD_WAIT, INCOMPLETE_AGE, LOCK_OFFSET, RECORD_ENV, SUFFIX, applies, begin,
    Held, identity, interrupted, _alive, _complete, _examine, _here, _lock, _name_newest, _open_record, _pid,
    _publish, _published, _remove, _unlock, _withdraw)

FOLDER = "angelo-start"
SETTLE_SECONDS = 2.0      # The window keeps running this long after Angelo's start-up is done: still part of the start.
LONGEST_START = 180.0     # A start that never settles still completes: a later crash is not a crash at start.
TICK = 0.5
EXTENSION_PREFIX = "bl_ext."

_state = {"held": None, "left_off": [], "completion": None, "saved": False, "settled_left_off": False,
          "folder": None,    # "folder": where this run keeps its records (None: it keeps none).
          "culprit": None,   # The left-off add-on the last start died loading (its record's ``loading``), or None.
          "active": False,   # A start or an enabling runs: add-ons that fail now are collected (angelo_addon_failures).
          "failures": [],    # Failure(module, cause) collected and not yet settled.
          "failed_now": {},  # {module: cause} of the person's add-ons that failed as they loaded this session.
          "watched": None}   # (shipped(module), {module: the person's own add-on}) for this start or enabling.


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
    ``LONGEST_START``.

    Each bound is an instant, ``reading + duration``, and a later reading is compared with it; never a difference
    of two readings compared with the duration. The two disagree where the sum rounds (a float's spacing doubles
    at each power of two: ``(reading + 2.0) - reading`` is not 2.0 for a reading just under one, about two seconds
    of every doubling of uptime), and the instant itself then read as too early, or one float before it as late
    enough (a test at exactly ``ready_at + SETTLE_SECONDS`` failed on Windows, 2026-11-14)."""

    def __init__(self, started):
        self.started, self.ready_at = started, None

    @classmethod
    def settling(cls, now):
        """Already ready: it completes ``SETTLE_SECONDS`` from ``now`` (a preference read that finished)."""
        completion = cls(now)
        completion.ready_at = now
        return completion

    def step(self, now, ready):
        if self.ready_at is None and (ready or now >= self.started + LONGEST_START):
            self.ready_at = now
        return self.ready_at is not None and now >= self.ready_at + SETTLE_SECONDS


def left_off():
    """The add-on modules this launch left off (empty on a normal start)."""
    return list(_state["left_off"])


# Blender's side: the startup module calls ``start`` and ``stop``.

def shipped_here(bpy):
    """``shipped(module)`` for this running installation (``shipped_check``): the update card's add-on check
    judges the same add-ons this module leaves off."""
    return _shipped_here(bpy)


def failed_now():
    """``{module: cause}`` of the person's add-ons that failed as they loaded in this session and have not loaded
    since (the update card names them with their cause)."""
    import addon_utils
    return {module: cause for module, cause in _state["failed_now"].items() if not addon_utils.check(module)[1]}


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


def culprit(found, left_off):
    """The left-off add-on a found record's process was enabling when it ended, or None (it died elsewhere). Its
    ``loading`` lists the add-ons being enabled, outermost first (one's ``register`` may enable another): the innermost
    one this launch left off leads, so an add-on the preferences do not list never hides the one that enabled it."""
    for record in found:
        loading = record.get("loading")
        for name in reversed(loading if isinstance(loading, list) else [loading]):
            if name in left_off:
                return name
    return None


def isolate(found):
    """Whether this launch leaves third-party add-ons off: the last start never completed. (One reason today; a launch
    after a move to a newer Blender base could be another, the same way.)"""
    return bool(found)


def start(bpy):
    """Before any add-on loads: leave third-party add-ons off when a start never completed, then keep this start's
    record until it completes, collecting add-ons that fail meanwhile."""
    folder = _state["folder"] = Path(bpy.utils.user_resource('CONFIG')) / FOLDER
    try:
        found = interrupted(folder)
        if isolate(found):
            _state["left_off"] = _leave_off(bpy)
            _state["culprit"] = culprit(found, _state["left_off"])
            if _state["left_off"]:
                bpy.context.preferences.is_dirty = True   # Blender's save on quit keeps them off too.
            print("Angelo: the last start did not finish%s; started without:" % (
                " while %s loaded" % _state["culprit"] if _state["culprit"] else ""),
                ", ".join(_state["left_off"]) or "-")
    except Exception as error:   # The start goes on whatever this check meets.
        print("Angelo: start check:", error, file=sys.stderr)
    _state["held"] = begin(folder)
    _state["active"], _state["watched"] = True, None
    _watch_failures(bpy)
    _state["completion"] = Completion(time.monotonic())
    _watch()   # Completes the start: its record goes, add-ons left off are saved off, they and failed ones named.


@contextlib.contextmanager
def enabling():
    """Around Blender enabling add-ons during the session (``prefs_reload`` wraps every preference read's
    ``addon_utils.reset_all``): a record is held through it and ``SETTLE_SECONDS`` after, so a native crash there
    makes the next launch leave third-party add-ons off. During the start its own record covers it. Nothing in a run
    that keeps no record (background, factory startup, a folder that cannot hold one)."""
    folder = _state["folder"]
    if folder is not None and _state["held"] is None:
        held = begin(folder)
        if held is not None:   # None (the folder cannot hold one): a start still running keeps its own completion.
            _state["held"], _state["completion"] = held, None
    # Only with a record held: its watch is what ends the collecting (a folder that cannot hold one collects nothing).
    _state["active"], _state["watched"] = _state["held"] is not None, None
    try:
        yield
    finally:
        if _state["held"] is None:
            _state["active"] = False
        else:
            completion = _state["completion"]
            if completion is None or completion.ready_at is not None:   # Not a start still running: settle again.
                _state["completion"] = Completion.settling(time.monotonic())
            _watch()


def causes():
    """``{module: cause}`` of the add-ons that failed so far in this start or enabling (``settings_import_apply``'s
    notice says why each did not load)."""
    return {failure.module: failure.cause for failure in _state["failures"]}


def _note(modules):
    held = _state["held"]
    if held is not None:
        held.note_loading(modules)


def _watched(module):
    """Whether ``module`` is the person's own add-on (not one that ships with Angelo), cached for this start or
    enabling: the wrapper costs a shipped add-on nothing but this lookup."""
    import bpy
    if _state["watched"] is None:
        _state["watched"] = (_shipped_here(bpy), {})
    shipped, known = _state["watched"]
    if module not in known:
        known[module] = not shipped(module)
    return known[module]


def _watch_failures(bpy):
    try:
        import addon_utils   # Reads bpy.context as it imports: a stand-in bpy (the host tests) may lack it.
    except Exception:   # Not Blender's Python: there is no add-on to watch.
        return
    try:
        import angelo_addon_failures as failures
        failures.install(addon_utils, lambda: _state["active"], _note, _state["failures"], _watched)
    except Exception as error:   # Never stops a start: Blender's own reporting remains.
        print("Angelo: add-on failure check:", error, file=sys.stderr)


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
    completion = _state["completion"]
    if completion is not None and not completion.step(time.monotonic(), ready):   # None: nothing left to wait for.
        return TICK
    _completed()
    return None


def _completed():
    import bpy
    _state["active"] = False
    failed = _settle_failures(bpy)
    _state["failed_now"].update((failure.module, failure.cause) for failure in failed)
    _record_loads(bpy)
    left_off = _state["left_off"] and not _state["settled_left_off"]   # Once: a later read's settling names none.
    if left_off:
        _state["settled_left_off"] = True
        try:   # They stay off: a later normal start must not load them again (whatever Auto-Save says).
            bpy.ops.wm.save_userpref()
            _state["saved"] = True
        except Exception as error:
            print("Angelo: could not save the preferences without the add-ons left off:", error, file=sys.stderr)
    if (left_off or failed) and _angelo_enabled():   # Without Angelo's add-on: the console lines only.
        try:
            from angelo_agent import safe_start_notice
            if left_off:
                safe_start_notice.show(_state["left_off"], _state["culprit"])
            safe_start_notice.show_failures(failed)
        except Exception as error:
            print("Angelo: start notice:", error, file=sys.stderr)
    stop()


def _record_loads(bpy):
    """Angelo's own load result: the person's add-ons loaded now, with their versions, on this Blender version
    (``angelo_addon_loads``), read by a later version's update check. Never stops anything."""
    if _state["folder"] is None:
        return
    try:
        import addon_utils   # Reads bpy.context as it imports: a stand-in bpy (the host tests) may lack it.
    except Exception:   # Not Blender's Python: no add-on loaded.
        return
    try:
        import angelo_addon_loads
        shipped = _shipped_here(bpy)
        addon_utils.modules(refresh=False)
        loaded = {}
        for entry in bpy.context.preferences.addons:
            module = entry.module
            fake = addon_utils.addons_fake_modules.get(module)
            if shipped(module) or fake is None or module in _state["failed_now"] or not addon_utils.check(module)[1]:
                continue
            loaded[module] = angelo_addon_loads.version_text(addon_utils.module_bl_info(fake).get("version"))
        angelo_addon_loads.write(_state["folder"], tuple(bpy.app.version), loaded)
    except Exception as error:
        print("Angelo: could not record which add-ons loaded:", error, file=sys.stderr)


def _settle_failures(bpy):
    """The add-ons that failed during this start or enabling and are still enabled and not loaded (Failure list).
    Nothing is turned off: the notice offers it for each."""
    failures, _state["failures"][:] = list(_state["failures"]), []
    if not failures:
        return []
    try:
        import addon_utils
        import angelo_addon_failures
        addons = bpy.context.preferences.addons
        failed = angelo_addon_failures.pending(failures, lambda module: module in addons,
                                               lambda module: addon_utils.check(module)[1])
    except Exception as error:
        print("Angelo: add-on failure check:", error, file=sys.stderr)
        return []
    for failure in failed:
        print("Angelo: add-on %s failed as it loaded (%s); still enabled" % (failure.module, failure.cause))
    return failed


def stop(exiting=False):
    """The start completed, or the app quits: its record goes. A start that left add-ons off and quits before it
    completed keeps its record (closed, so unlocked): the next launch leaves them off again, as their being off was
    not yet saved."""
    held, _state["held"] = _state["held"], None
    _state["active"] = False
    if held is None:
        return
    if exiting and _state["left_off"] and not _state["saved"]:
        held.close()
    else:
        held.release()
