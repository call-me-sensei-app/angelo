# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a hand-started suite runs again isolated (provider_isolation.ensure), and the status it then exits with.

A relaunched suite's end is reported as itself: its own exit status, or 128+N with the signal and ``waitpid``'s raw
status said on stderr when it ended by signal N. Found on 1.1.0's cut 08e2b0f7842 (macOS, 2026-11-13):
test_blender_prefs_and_quit's quit-open phase ended with exit status 1, no receipt and nothing printed after start-up,
because a child ended by a signal (a negative status from subprocess) was reported as plain 1.
"""

import os
import signal
import subprocess
import sys


def run_child(command, env):
    """Run ``command`` to its end: its exit as subprocess reports it (``-N``: ended by signal N) and the raw status
    ``waitpid`` gave (on Windows, the exit code again)."""
    process = subprocess.Popen(command, env=env)
    if os.name == "nt":
        code = process.wait()
        return code, code
    while True:
        try:
            _pid, raw = os.waitpid(process.pid, 0)
            break
        except InterruptedError:
            continue
    process.returncode = os.waitstatus_to_exitcode(raw)
    return process.returncode, raw


def exit_status(code, raw=None):
    """The status this process exits with for its relaunched suite's ``code``: its own (0-255), or 128+N for a suite
    ended by signal N, said on stderr with ``waitpid``'s raw status, so a red receipt tells a crash or a kill from an
    exit (a signal death read as plain 1 hid it: test_blender_prefs_and_quit, 1.1.0's cut 08e2b0f7842)."""
    if code < 0:
        number = -code
        try:
            name = signal.Signals(number).name
        except ValueError:
            name = "unknown"
        status = min(128 + number, 255)
        print("suite_isolation: the relaunched suite ended by signal %d (%s; waitpid status %s); exiting %d"
              % (number, name, raw, status), file=sys.stderr, flush=True)
        return status
    return code if code < 256 else 1


def relaunch(env, argv=None, binary=None, call=run_child):
    """Run this Blender's command line again with ``env``; returns the status to exit with (``exit_status``)."""
    if binary is None:
        import bpy
        binary = bpy.app.binary_path
    command = [str(binary)] + list(sys.argv[1:] if argv is None else argv[1:])
    sys.stdout.flush()
    sys.stderr.flush()
    outcome = call(command, env=env)
    code, raw = outcome if isinstance(outcome, tuple) else (outcome, None)
    return exit_status(code, raw)
