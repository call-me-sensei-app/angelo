# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Switches the release manager can turn off without a release, through the signed update feed.

The feed's payload may list ``disabled_features`` (``update_feed``; signed with the release key, so nobody else can
set it, and ignored by builds older than this field). A feature that names a switch here is off while the last good
feed lists it, and on otherwise: before the first feed arrives, offline, or with update checks turned off by policy
(no feed is fetched then). ``BUILD_DISABLED`` is the fallback for a rebuild: switches off in this build whatever the
feed says.

Switches in use (doc/angelo/updates_and_reports.rst, "Remote switches"):

* ``gemini_subscription``: Gemini through the person's own Google plan (the Antigravity ACP route).

The answer is looked up at most once per ``TTL`` seconds: the providers page asks while drawing, and verifying the
feed's signature is not a per-frame cost. Standard library only; no ``bpy`` unless no directory is given.
"""

import time

from . import update_check, update_feed, update_state

BUILD_DISABLED = frozenset()   # For example frozenset({"gemini_subscription"}): off in this build, feed or not.
TTL = 60.0
_cache = {}   # directory -> (checked at, disabled switches)


def _directory():
    from .ui import state_directory
    return state_directory()


def disabled_switches(directory=None, *, clock=time.monotonic, keys=None):
    """The switches off now: this build's own and the last good feed's."""
    directory = str(directory or _directory())
    now = clock()
    found = _cache.get(directory)
    if found and now - found[0] < TTL:
        return found[1]
    try:
        feed = update_check.cached_feed(update_state.load(directory),
                                        update_feed.load_keys() if keys is None else keys)
    except (OSError, ValueError):
        feed = None
    switches = BUILD_DISABLED | (feed.disabled_features if feed else frozenset())
    _cache[directory] = (now, switches)
    return switches


def enabled(name, directory=None, **options):
    """Whether switch ``name`` is on (not listed by this build or the last good feed)."""
    return name not in disabled_switches(directory, **options)


def forget():
    """Drop the cached answer (a new feed was accepted, or a test changed the state)."""
    _cache.clear()
