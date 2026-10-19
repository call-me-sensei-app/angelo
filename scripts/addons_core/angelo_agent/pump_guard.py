# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep a main-thread pump alive through an error.

Blender removes a ``bpy.app.timers`` function the first time it raises, so one
unexpected value (a console answer, a damaged file) used to stop the UI pump
for the rest of the session: the chat stopped following sign-ins, connections
and turns without a word. A guarded pump reports the error once per kind,
with no internals beyond the traceback in the console, and runs again.

A worker that ends a request on an unexpected error reports it with
``report_defect``: the chat shows only the error's message, so the console
keeps its traceback.
"""

import functools
import sys
import traceback

_reported = set()


def keep_alive(retry):
    """Decorate a timer function: an exception is reported and the timer runs again after ``retry`` seconds."""
    def decorate(function):
        @functools.wraps(function)
        def run(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception as error:  # noqa: BLE001 - the pump must outlive any one subsystem's failure.
                report(function.__qualname__, error)
                return retry
        return run
    return decorate


def report(where, error):
    """Print a failure once per place and kind of error."""
    key = (where, type(error).__name__, str(error)[:200])
    if key not in _reported:
        _reported.add(key)
        print("Angelo: %s failed and will retry: %s" % (where, error))
        traceback.print_exc()


def report_defect(where, error, redact=str):
    """Print ``error``'s traceback every time, passed through ``redact`` (a chat's own redaction)."""
    trace = redact("".join(traceback.format_exception(error)))
    try:
        sys.stderr.write("Angelo: %s failed on an unexpected error\n%s" % (where, trace))
        sys.stderr.flush()
    except (AttributeError, OSError, ValueError):
        pass  # No usable console: the caller's own error report still stands.
