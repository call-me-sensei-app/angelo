# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A user startup script for ``bench_floor_launch.py`` trace launches: record every add-on enable and disable.

``bench_floor_launch`` copies this file into the sandboxed profile's
``scripts/startup`` folder for its trace launches only (never for the timed
ones). Blender runs startup scripts before it enables add-ons, so wrapping
``addon_utils.enable`` and ``disable`` here sees every one of them, in Angelo
and in stock Blender alike: how often each add-on is enabled per launch (a
second enable is a reload), and the process instructions and wall time each
call took. ``floor_launch_probe`` reads ``calls`` from this module.
"""

import time

import addon_utils

try:
    import perf_counters
except ImportError:   # Copied without its folder: count wall time only.
    perf_counters = None

calls = []


def _instructions():
    counts = perf_counters.read_rusage() if perf_counters is not None else None
    return counts["instructions"] if counts else None


def _wrap(name, function):
    def wrapped(module_name, *args, **kwargs):
        before, started = _instructions(), time.perf_counter()
        try:
            return function(module_name, *args, **kwargs)
        finally:
            after = _instructions()
            calls.append({"call": name, "module": module_name, "ms": round((time.perf_counter() - started) * 1000, 3),
                          "instructions": after - before if None not in (before, after) else None})
    wrapped.__wrapped__ = function
    return wrapped


def register():
    """Startup scripts' hook; the wrapping is done at import, before any add-on is enabled."""


if not hasattr(addon_utils.enable, "__wrapped__"):
    addon_utils.enable = _wrap("enable", addon_utils.enable)
    addon_utils.disable = _wrap("disable", addon_utils.disable)
