# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add-ons that fail as Blender enables them, while a start (or a preference read's enabling) runs: named, with the
add-on being loaded kept on file for a native crash (``angelo_safe_start``; doc/angelo/app_identity.rst, "A start that
did not finish").

Blender's ``addon_utils.enable`` catches an add-on that raises as it imports or registers (a newer Blender's API
removed something it uses: ``PropertyGroup.name`` read-only in 5.3), prints the traceback to the console only and
returns None; the start completes, so a start record never says so, and the add-on stays enabled, so every launch
fails it again, silently. ``install`` wraps ``enable`` (once; transparent outside a start and for the add-ons that ship
with Angelo) so that, while ``active()``:

* ``note(names)`` keeps the add-ons being enabled now on file, outermost first (an add-on's ``register`` may enable
  another), so a native crash in one is named by the next launch (``angelo_start_records.Held.note_loading``);
* a failure is collected as ``Failure(module, cause)``: a one-line cause, the first error's type and first line, with
  folders shortened to the file's name (no home folder in a notice). An add-on that is not installed at all (its
  module, or its extension repository, missing) is no failure: there is nothing of it to load, as Blender itself says.

Nothing is undone and nothing is turned off by itself, exactly as stock Blender: what a ``register`` that raised
part-way set up before it raised stays (removing a registered ``PropertyGroup`` that a runtime pointer property still
uses frees it under that property, a native crash stock Blender never has), and the add-on stays enabled. The notice
names each failed add-on with its cause and offers **Turn off** for each (``turn_off``), which the person clicks.

The wrapper never raises out of ``enable`` and never stops a start: anything it meets is printed, and Blender's own
reporting is kept (the traceback is still printed, or the caller's ``handle_error`` called). The pure parts need no
bpy: tests/python/angelo_agent/test_native_addon_failures.py.
"""

import re
import sys
import traceback
from collections import namedtuple

Failure = namedtuple("Failure", "module cause")
CAUSE_LENGTH = 160
_loading = []   # The watched add-ons being enabled now, outermost first.
# A path's start: ``~``, a drive (``C:\``), a share (``\\nas\`` or ``//nas/``) or a root (``/``).
_START = r"""(?:~[\\/]|[A-Za-z]:[\\/]|[\\/]{1,2})"""
# A quoted path, spaces allowed (``'D:\My Tools\addons\x.py'``): kept quoted, only its last part.
_QUOTED = re.compile(r"""(['"])%s(?:[^'"\\/]*[\\/])+([^'"\\/]+)\1""" % _START)
# An unquoted path (``dlopen(/a/b/core.so, 2)``): only its last part. Never a URL's (``https://x/y``).
_PATH = re.compile(r"""(?<![\w:/\\.])%s(?:[^\\/\s'"]+[\\/])+([^\\/\s'"]+)""" % _START)


def redact(text, home=None):
    """``text`` with every folder path shortened to its last part, so the person's home folder never shows: the home
    folder itself first (a user name may have spaces), then quoted paths whole, then unquoted ones."""
    import os
    home = os.path.expanduser("~") if home is None else home
    if home and len(home) > 1:
        for spelling in {home, home.replace("\\", "/")}:
            text = text.replace(spelling, "~")
    text = _QUOTED.sub(lambda match: match.group(1) + match.group(2) + match.group(1), text)
    return _PATH.sub(lambda match: match.group(1), text)


def cause(error):
    """The exception as one short line: its type and the first line of its message, paths shortened."""
    try:
        lines = str(error).strip().splitlines()
    except Exception:   # An add-on's exception whose ``__str__`` raises: its type is the cause.
        lines = []
    text = "%s: %s" % (type(error).__name__, redact(lines[0].strip())) if lines else type(error).__name__
    return text if len(text) <= CAUSE_LENGTH else text[:CAUSE_LENGTH - 1] + "…"


def absent(error, module):
    """Whether ``error`` says the add-on is not installed at all (nothing of it to load), not that it broke."""
    if isinstance(error, ModuleNotFoundError) and error.name == module:
        return True
    try:
        return isinstance(error, ImportError) and "extension repository" in str(error)
    except Exception:
        return False


def _print_error(error):
    """Blender's own default (``addon_utils.enable``): a missing add-on's one line, else the whole traceback."""
    message = getattr(error, "msg", None)
    if isinstance(error, ImportError) and isinstance(message, str) and message.startswith("Add-on "):
        print(message)
        return
    traceback.print_exception(type(error), error, error.__traceback__)


def _noted(note):
    try:
        note(list(_loading))
    except Exception as error:   # A name is a courtesy: the enabling goes on.
        print("Angelo: could not note the add-on loading:", error, file=sys.stderr)


def install(addon_utils, active, note, failures, watched):
    """Wrap ``addon_utils.enable`` once. ``active()``: whether a start or enabling runs; ``note(names)``: the record's
    loading names (outermost first; empty: none); ``failures``: the list that collects ``Failure``s; ``watched(module)``:
    whether the add-on is the person's own (one that ships with Angelo is never named)."""
    original = addon_utils.enable
    if getattr(original, "_angelo_failures", False):
        return

    def enable(module_name, *args, handle_error=None, **kwargs):
        try:
            watch = bool(active() and watched(module_name))
        except Exception as error:
            print("Angelo: add-on failure check:", error, file=sys.stderr)
            watch = False
        if not watch:
            return original(module_name, *args, handle_error=handle_error, **kwargs)
        caught = []

        def collect(error):
            caught.append(error)
            (handle_error or _print_error)(error)
        _loading.append(module_name)
        _noted(note)
        try:
            module = original(module_name, *args, handle_error=collect, **kwargs)
        finally:
            _loading.pop()
            _noted(note)
        try:   # The first error is the import's or register's: a later one is a follow-on (extensions_refresh).
            if module is None and caught and not absent(caught[0], module_name):
                failures.append(Failure(module_name, cause(caught[0])))
        except Exception as error:
            print("Angelo: add-on failure check:", error, file=sys.stderr)
        return module
    enable._angelo_failures = True
    addon_utils.enable = enable


def pending(failures, enabled, loaded):
    """The failures to name, each module once: still enabled in the preferences (``enabled(module)``; one the person
    turned off, or Blender's own operator removed, is not), and not loaded since (``loaded(module)``: a later enabling
    of the same add-on worked)."""
    found, seen = [], set()
    for failure in failures:
        if failure.module in seen:
            continue
        seen.add(failure.module)
        try:
            if enabled(failure.module) and not loaded(failure.module):
                found.append(failure)
        except Exception as error:
            print("Angelo: add-on failure check:", error, file=sys.stderr)
    return found


def turn_off(module):
    """The notice's **Turn off**: as the Add-ons list's checkbox does (``addon_utils.disable`` with the preference
    changed), then the preferences saved, since the person asked for it whatever Auto-Save Preferences says (as with
    any explicit save, other changes still in memory are saved with it)."""
    import addon_utils
    import bpy
    addon_utils.disable(module, default_set=True)
    bpy.context.preferences.is_dirty = True
    try:
        bpy.ops.wm.save_userpref()
    except Exception as error:   # Off for this session; Auto-Save or a later save keeps it.
        print("Angelo: could not save the preferences after turning off %s: %s" % (module, error), file=sys.stderr)
