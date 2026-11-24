# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict script protection (doc/angelo/script_confinement.rst, "Strict script protection"; the maintainer's Ruling
17): one opt-in preference, off by default, for the confinement's checks that cost agent scripts time. It is the
person's, kept with the add-on's preferences (``ui.ANGELO_Preferences``) and saved with them; an update leaves an
existing person's preferences without it, which reads as off.

C reads it as a plain atomic flag (``angelo_strict_script_protection()``, bpy_angelo_guard_strict.cc), never RNA.
The flag follows the saved preference, which C reads itself (``strict_flag.sync``): at registration, after every
preference read (``prefs_reload.READ_LISTENERS``) and on each change the preference takes.

* **Who may change it.** Never an agent script: C refuses a confined thread's every write to a preference (attribute,
  ``property_unset``, ID properties), every change of its definition (redefine, remove, register, unregister) and its
  sync, and a change a script's run left is put back at the run's end (the sweep, ``script_confinement_blender``),
  which also re-syncs C. On narrows, so it is open to anything else. Off widens: C allows it only for the person on
  this setting's own control (its checkbox's ticket, which C takes, or the grant scope of their press on its switch on
  Angelo's page), a preference read the person started, or a test harness; never while a script runs. A refused Off
  gives the preference On back. An On press spends its ticket, so it leaves none for an Off.
* **Kept across a crash.** Blender saves preferences only at a clean exit, so each change is also saved at once in
  Angelo's state folder (``FILE``, as ``always_allow``). At registration On from either wins: a crash or a lost save
  never turns protection off.
* **A refused change says why** in the status bar (a script runs, or it wasn't the person's own press).
"""

import json
import os

from bpy.props import BoolProperty

from . import grant_guard, strict_flag
from .ui_text import n_, rpt_

NAME = "strict_script_protection"
DEFAULT = False
FILE = "strict-script-protection.json"
enabled = strict_flag.enabled   # Whether it is on, as the confinement reads it.
_path = [None]   # The state file (``attach``); None: in memory only (tests).
REFUSED = n_("Strict script protection was not changed: {reason}")


def _preferences():
    try:
        import bpy
        addon = bpy.context.preferences.addons.get(__package__)
    except Exception:   # noqa: BLE001 - any failing read (a closing window, a freed context) is "unreadable".
        return None
    return getattr(addon, "preferences", None)


def _stored():
    """The saved preference: True or False; None when it can't be read (the flag is then kept, never turned off)."""
    prefs = _preferences()
    return None if prefs is None or not hasattr(prefs, NAME) else getattr(prefs, NAME) is True


def sync(persist=False):
    """C takes the saved preference. ``persist``: the state file says it too. The flag now.

    C refuses Off that is neither the person's (their control, their own preference read) nor a harness's, and any
    sync on a confined thread: the flag is kept, and a preference left Off past its update (an unconfined reset, a
    read Python started) gets On back, so the switch shows what is in effect and the next sync agrees (the re-read
    of 9b3f450, M3 and its NIT)."""
    try:
        on = strict_flag.sync(_stored)
    except PermissionError as error:
        on = enabled()
        if on and _stored() is False:
            try:
                setattr(_preferences(), NAME, True)   # Narrowing; its update finds it equal to the flag.
            except Exception as refused:   # noqa: BLE001 - a confined thread: C refuses the write too.
                print("Angelo: Strict script protection was not changed:", refused)
                return on
            print("Angelo: Strict script protection stays on:", error)
    if persist and _stored() is not None:
        _save(on)
    return on


def attach(directory):
    """Registration: On saved in the state folder or in the preferences wins (a crash lost the other save), then the
    flag follows the preference."""
    from . import shared_replace
    _path[0] = os.path.join(os.fspath(directory), FILE)
    try:   # Sharing delete: another window's save lands (as always_allow).
        saved = json.loads(shared_replace.read_text(_path[0])).get("on") is True
    except (OSError, ValueError, AttributeError):
        saved = False
    prefs = _preferences()
    if saved and prefs is not None and hasattr(prefs, NAME) and getattr(prefs, NAME) is not True:
        setattr(prefs, NAME, True)   # Narrowing: its update syncs and saves.
    return sync(persist=True)


def _save(on):
    path = _path[0]
    if path is None:
        return
    from . import owned_paths, shared_replace
    temporary = path + ".tmp"
    try:
        owned_paths.require_writable(path)   # C first: nothing is made beside a target it refuses.
        # Registration saves first: an explicit state folder (ANGELO_STATE_DIR) may not be made yet.
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "on": bool(on)}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, path)
        if os.name != "nt":   # The rename itself survives a crash once its folder is synced.
            folder = os.open(os.path.dirname(path), os.O_RDONLY)
            try:
                os.fsync(folder)
            finally:
                os.close(folder)
    except OSError as error:   # The preferences still hold it; saved at the next change.
        shared_replace.discard(temporary)
        print("Angelo: Strict script protection could not be saved in Angelo's state folder:", error)


def _person():
    """The host's stand-in for C's person check (``strict_flag.sync``): the person's press on its switch (its ticket,
    taken) or the grant scope of their click on Angelo's page. In the app C decides, taking the ticket itself."""
    taken = grant_guard.person_set(NAME)
    authority = grant_guard._authority()
    return bool(taken or (authority.available() and authority.GUARD.admitted("prop." + NAME)))


def _update(prefs, _context):
    value, current = getattr(prefs, NAME) is True, enabled()
    if value == current or value:
        grant_guard.person_set(NAME)   # The press's ticket, spent: an On (or no change) leaves none for an Off.
        if value != current:
            sync(persist=True)   # On narrows: open to anything (C refuses a confined thread itself).
        return
    if grant_guard.script_running():
        reason = grant_guard.SCRIPT_RUNS
    else:
        try:   # Off: C's to allow (the person's scope or press, or a preference read).
            on = strict_flag.sync(_stored, off_allowed=_person)
        except PermissionError:
            on = True
        if not on:
            _save(False)
            return
        reason = grant_guard.NOT_YOURS
    setattr(prefs, NAME, True)   # Updates again with On: taken as unchanged.
    _tell(reason)


def _tell(reason):
    """The refusal where the person sees it: the hint on the switch for a press made before it armed, else the status
    bar with the real reason (from a timer: this runs inside the preference's update)."""
    from . import grant_hints
    if reason == grant_guard.NOT_YOURS and grant_hints.refused("prop." + NAME):
        return
    message = rpt_(REFUSED, reason=rpt_(reason))
    print("Angelo:", message)
    try:
        import bpy
        from . import studio_library_ops
        bpy.app.timers.register(lambda: studio_library_ops.report('WARNING', message), first_interval=0.0)
    except Exception as error:   # noqa: BLE001 - no window (background): the console line above says it.
        print("Angelo: the refusal could not be shown:", error)


class StrictProtectionPreferences:
    strict_script_protection: BoolProperty(
        name="Strict script protection", default=DEFAULT, update=_update,
        description="Stronger protection for agent scripts: every path and link is checked in full, pipes and "
                    "handles a script makes are tracked, and more of what a script changes is put back when its "
                    "run ends. Agent scripts can run slower")


def _read(*_args):
    sync(persist=True)   # A preference read: the person's (Load Factory Preferences, Revert) may turn it off.


def register(directory=None):
    from . import prefs_reload
    if directory is not None:
        attach(directory)
    else:
        sync()
    if _read not in prefs_reload.READ_LISTENERS:
        prefs_reload.READ_LISTENERS.append(_read)


def unregister():
    from . import prefs_reload
    if _read in prefs_reload.READ_LISTENERS:
        prefs_reload.READ_LISTENERS.remove(_read)
