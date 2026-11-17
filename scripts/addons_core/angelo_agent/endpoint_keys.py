# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a custom endpoint's key comes from when it is not the member's own: a program the machine policy names.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (credentials).
``endpoint_ui`` asks here, and applies each finished key on the main thread
exactly as a typed one (``ui.replace_connection_key``):

* ``command``: a program the machine policy names (never the console) that prints
  a short-lived key, read from the policy again each time it runs (``policy_command``):
  never from the endpoint Angelo keeps, which a script could change in place. Angelo runs it itself, outside the runtime's network lock (an
  SSO helper must reach its identity provider), with the person's environment less
  Angelo's own secrets and any provider credential, and runs it again when
  ``REFRESH_SHARE`` of its lifetime has passed; the new key then restarts the
  runtime between turns, as a renewed team grant key does.

Every job runs on a thread of its own; nothing here touches ``bpy``.
"""

import os
import subprocess
import threading
import time

from . import owned_paths, product_env
from .display_text import Text
from .ui_text import n_

COMMAND_SECONDS = 60.0     # A command that prints nothing by then has failed.
REFRESH_SHARE = 0.8        # Run a command again once this share of its key's lifetime has passed.
RETRY_SECONDS = 300        # A program that gave no key is run again after this long.
MESSAGE_LIMIT = 200
COMMAND_FAILED = n_("Your organization's sign-in program for this model server failed ({message})")

_lock = threading.Lock()
_running = set()     # Endpoint ids with a job in flight.
_finished = []       # (endpoint id, key or None, problem Text or "", seconds the key lasts or 0)
_expires = {}        # Endpoint id -> monotonic time a command's key should be renewed.


def running(endpoint_id):
    with _lock:
        return endpoint_id in _running


def due(endpoint_id, now=None):
    """Whether a key program should be run again now."""
    now = time.monotonic() if now is None else now
    return endpoint_id in _expires and now >= _expires[endpoint_id]


def _start(endpoint_id, job):
    with _lock:
        if endpoint_id in _running:
            return False
        _running.add(endpoint_id)

    def run():
        try:
            key, problem, lifetime = job()
        except Exception as error:  # noqa: BLE001 - a key source's failure is reported, never raised into the pump.
            key, problem, lifetime = None, Text(COMMAND_FAILED, message=str(error)[:MESSAGE_LIMIT]), 0
        with _lock:
            _running.discard(endpoint_id)
            _finished.append((endpoint_id, key, problem, lifetime))

    threading.Thread(target=run, daemon=True, name="AngeloEndpointKey").start()
    return True


def command_environment(environ=None):
    """The person's environment for a key program, less Angelo's own variables and every provider credential."""
    from .runtime_environment import CREDENTIALS
    environ = os.environ if environ is None else environ
    prefixes = (product_env.PREFIX,) + product_env.LEGACY_PREFIXES
    return {name: value for name, value in environ.items()
            if not name.upper().startswith(prefixes) and name.upper() not in CREDENTIALS}


def policy_command(endpoint_id):
    """``(argv, key lifetime)`` of the key program this computer's policy names for ``endpoint_id``, read from its
    sources now (``managed_policy.load``: the system's managed settings, which no script changes), or None."""
    from . import managed_policy
    policy = managed_policy.load()
    for entry in policy.custom_endpoints or ():
        if entry.id == endpoint_id and entry.credential == "command" and entry.command:
            return tuple(entry.command), entry.refresh_seconds
    return None


def run_command(endpoint, run=subprocess.run):
    """Run ``endpoint``'s key program (argv, no shell); its first non-empty output line is the key. Only the
    endpoint's id is taken from it: the program, its arguments and the key's lifetime are the policy's as read when
    the job runs (``policy_command``), and a program a script wrote is never run."""
    endpoint_id = endpoint.id

    def job():
        named = policy_command(endpoint_id)
        if named is None:
            return None, Text(COMMAND_FAILED, message=LookupError.__name__), 0   # No longer in the policy.
        argv, lifetime = named
        if owned_paths.script_made(argv[0]):
            return None, Text(COMMAND_FAILED, message=PermissionError.__name__), 0   # A script wrote it.
        try:
            done = run(list(argv), capture_output=True, text=True, timeout=COMMAND_SECONDS,
                       stdin=subprocess.DEVNULL, env=command_environment())
        except (OSError, subprocess.SubprocessError) as error:
            return None, Text(COMMAND_FAILED, message=type(error).__name__), 0
        line = next((line.strip() for line in (done.stdout or "").splitlines() if line.strip()), "")
        if done.returncode != 0 or not line:
            return None, Text(COMMAND_FAILED, message="exit %d" % done.returncode), 0
        return line, "", lifetime

    return _start(endpoint_id, job)


def take_finished(now=None):
    """The jobs finished since the last call: ``[(endpoint id, key or None, problem, lifetime)]``."""
    now = time.monotonic() if now is None else now
    with _lock:
        finished, _finished[:] = list(_finished), []
    for endpoint_id, key, _problem, lifetime in finished:
        # A program's key is renewed before it lapses; a program that failed is run again later.
        _expires[endpoint_id] = now + (REFRESH_SHARE * lifetime if key and lifetime else RETRY_SECONDS)
    return finished


def forget(endpoint_id=None):
    """Drop renewal times (one endpoint's, or all on sign-out)."""
    if endpoint_id is None:
        _expires.clear()
    else:
        _expires.pop(endpoint_id, None)
