# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict script protection's flag as this process has it (``strict_protection`` is the preference): C's
``angelo_strict_script_protection()`` (bpy_angelo_guard_strict.cc), read and set through the native
``_bpy._angelo_guard`` itself (never a Python global a script could rebind); a plain value where there is no guard
module (host tests, a build without it). Needs no ``bpy``.

* ``sync``: C takes the saved preference, which it reads itself; an unreadable preference keeps the flag.
* A worker Blender (``worker_process``) starts with ``--factory-startup`` and loads no preferences, so it takes the
  flag of the Angelo that started it from its environment (``carried``, ``from_environment``) before any agent code
  runs; a warm worker launched with another value is never taken or reused (``matches``, worker_pool).
Turning protection on narrows (open to anything but a confined thread); off is C's to allow (the person's scope).
"""

ENVIRONMENT = "ANGELO_STRICT_SCRIPT_PROTECTION"   # A worker's: "1" when the Angelo that started it had it on.
_fallback = [False]


def _guard():
    try:
        import _bpy
    except ImportError:
        return None
    guard = getattr(_bpy, "_angelo_guard", None)
    return guard if guard is not None and hasattr(guard, "sync_strict_script_protection") else None


def enabled():
    """Whether Strict script protection is on, as the confinement reads it."""
    guard = _guard()
    return bool(guard.strict_script_protection()) if guard is not None else _fallback[0]


def sync(stored, off_allowed=None):
    """C takes the saved preference, which it reads itself; it turns protection off only for the person on this
    setting's own control (their press on its page switch, or its checkbox's ticket, which C takes), a preference read
    the person started, or a test harness. ``PermissionError`` otherwise, and on a confined thread. The host's stand-in (no guard module): ``stored()`` (True, False, or None when
    unreadable) and ``off_allowed()`` model C's read and its person check. The flag now."""
    guard = _guard()
    if guard is not None:
        return bool(guard.sync_strict_script_protection())
    value = stored()
    if value is False and _fallback[0] and off_allowed is not None and not off_allowed():
        raise PermissionError("Only your own click in Angelo can turn Strict script protection off.")
    if value is not None:
        _fallback[0] = bool(value)
    return _fallback[0]


def turn_on():
    """Narrowing: open to anything but a confined thread (a worker's start)."""
    guard = _guard()
    if guard is not None:
        guard.set_strict_script_protection(True)
    else:
        _fallback[0] = True
    return enabled()


def carried():
    """For a worker's environment (``worker_process.worker_environment``): this process's flag."""
    return {ENVIRONMENT: "1" if enabled() else "0"}


def from_environment(environ):
    """A worker's start (``headless_main``): on when the Angelo that started it had it on. Whether it is on."""
    return turn_on() if environ.get(ENVIRONMENT) == "1" else enabled()


def matches(worker):
    """Whether ``worker`` (a ``worker_process.WorkerProcess``) was launched with the flag as it is now; True before
    its launch (it takes the flag then)."""
    launched = getattr(worker, "strict", None)
    return launched is None or launched == enabled()
