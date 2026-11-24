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

_reported = {}   # What was reported, oldest first; at most ``REPORTED_LIMIT`` kept (each new one drops the oldest).
REPORTED_LIMIT = 256


def _first(key):
    """Whether ``key`` is reported for the first time (remembered now)."""
    if key in _reported:
        return False
    _reported[key] = None
    while len(_reported) > REPORTED_LIMIT:
        _reported.pop(next(iter(_reported)))
    return True


def redacting(redact):
    """``redact`` that never raises: what it can't redact is never printed."""
    def run(text):
        try:
            return redact(text)
        except Exception:  # noqa: BLE001 - a report never fails, and never shows what it couldn't redact.
            return "[text that could not be redacted]"
    return run


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
    if _first(key):
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


def failed(where, error, redact=str, owner=None):
    """Report ``error`` of ``owner`` (a chat's id) at ``where``, once per owner, place and kind, through its own
    redaction (never raising); True when this was its first report."""
    redact = redacting(redact)
    if not _first((where, owner, type(error).__name__, redact(str(error))[:200])):
        return False
    report_defect(where, error, redact)
    return True


def each(where, step, redact=str, owner=None):
    """``step()`` for one of several (a worker's hosted chats): an error is reported (``failed``), and the others go
    on; True when it ran."""
    try:
        step()
        return True
    except Exception as error:  # noqa: BLE001 - one chat's failure never stalls another's.
        failed(where, error, redact, owner)
        return False
