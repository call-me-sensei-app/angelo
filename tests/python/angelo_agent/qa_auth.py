# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GUI suite owns the sign-in status its connect rows depend on (standard library only; no ``bpy``).

The chat's connect and "Sign in again" rows read the status of each provider *mode*
(``ui._authentication.status["codex_subscription"]``, through ``first_run.connect_options``). Angelo's start-up
(``ui.start_native_checks``, from the add-on's register) checks every mode in ``STARTUP_MODES`` on a worker thread, which runs the CLI it finds: a suite that
drew those rows from whatever that check said depended on the machine's Codex/Claude discovery and on what the
suite's own launch stand-in did inside the product's worker, where a failed assertion is swallowed into "Native
runtime unavailable" and the row silently disappears (test_blender_signin_reconnect from 46804fbd49c on: the status
check launched with ``keep_gui_programs=False``, its stand-in asserted True; seen first on a development build and
read as "no bundled runtime", which ``ANGELO_AGENT_RUNTIME=off`` already rules out in every suite).

So a suite either produces the status with a fixture-owned CLI (``fake_provider_cli`` on PATH, the product's real
check), stubs the checks (``ui._authentication.start``) and sets the status itself, or, when it intercepts the
launch, waits out the start-up checks (``startup_settled``) and then sets the modes it depends on (``own_status``).
test_native_suite_owned_status holds every GUI suite to one of the three.
"""

# The modes ``ui.start_native_checks`` checks at start-up, in its order (test_native_suite_owned_status reads them from ui.py).
STARTUP_MODES = ("codex_subscription", "claude_subscription", "codex_api")
MISSING_PREFIX = "Native runtime unavailable"   # first_run.MISSING_RUNTIME: the check could not run a CLI.


def startup_settled(authentication, modes=STARTUP_MODES):
    """Whether every start-up check of ``modes`` has its result applied: the mode has a status and is not running.

    ``NativeAuthentication.pump`` takes a mode out of ``running`` only when it applies the worker's result, so this is
    the checks' completion signal, however late the worker is; a check that could not start (its command could not be
    built) has a status and was never running. Never a sleep or a tick count."""
    with authentication.lock:
        return all(mode in authentication.status and mode not in authentication.running for mode in modes)


def status_class(status):
    """What a start-up check said, as a class for a report (the text itself is never recorded)."""
    if status is None:
        return "absent"
    return "missing" if str(status).startswith(MISSING_PREFIX) else "found"


def own_status(authentication, statuses, evidence=None):
    """Set each mode's status to the suite's own (``{mode: status}``) once ``startup_settled``; ``evidence`` (a dict)
    gets what the start-up check had said for each, as ``status_class``. Raises when a check is still running: its
    result would replace the suite's on the next pump."""
    with authentication.lock:
        late = sorted(mode for mode in statuses if mode in authentication.running)
        if late:
            raise RuntimeError("A start-up sign-in check is still running for %s" % ", ".join(late))
        for mode, status in statuses.items():
            if evidence is not None:
                evidence[mode] = status_class(authentication.status.get(mode))
            authentication.status[mode] = status
