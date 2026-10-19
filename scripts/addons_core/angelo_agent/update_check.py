# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The notify-only update check: when to look, what the feed says, what the person is told.

Contract: doc/angelo/updates_and_reports.rst. The app **never downloads or
installs anything**; the most it does is open the download link in the
person's browser when they click it.

Schedule: once shortly after launch (not when one succeeded within the last
hour, nor when the last try was under 5 minutes ago) and about every 24 hours
while Angelo stays open (plus or minus 10 percent, so installs don't all call
at once). A failed try is retried after 1, 2, 4, 8 and then 24 hours. A
manual check ("Check for Updates…") always runs. The request is one GET of a
small JSON file; it carries a User-Agent with the product version, OS family
and CPU (``app_http.user_agent``), the ETag from the last answer, and nothing
else: no identifier, no account, no scene data.

Where: ``https://angelo.studio/updates/v1/stable.json``, or the machine policy's
``UpdateFeedURL`` (an organization's https mirror of the same signed file), or
``ANGELO_UPDATE_FEED_URL`` for development and tests. The signature is required
whichever address served the file. An unusable ``UpdateFeedURL`` stops the
check instead of falling back to the public address.

Switches, strongest first: the machine policy ``DisableUpdateCheck`` (no
request at all, not even a manual one), an explicit ``--offline-mode``, and the
person's own "Check for updates automatically" (manual checks still work).

Pure logic over injected functions (clock, fetch, keys) so every branch is
host-tested; ``update_ui`` supplies the worker thread and the drawing. No bpy.
"""

import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import app_http, app_version, managed_policy, product_env, update_feed, update_state
from .app_http import NetworkError

FEED_URL = "https://angelo.studio/updates/v1/stable.json"
FEED_VARIABLE = "UPDATE_FEED_URL"   # ANGELO_UPDATE_FEED_URL: development and tests; the signature is still required.
DAY = 24 * 3600
LAUNCH_AFTER_SUCCESS = 3600
LAUNCH_AFTER_ATTEMPT = 300
RETRY_AFTER_FAILURE = (3600, 7200, 14400, 28800, DAY)
SNOOZE = 3 * DAY
SNOOZE_CRITICAL = DAY
_LOCK = threading.Lock()   # Load-change-save of the state file: the worker and the main thread both write it.

# Outcomes (``Result.kind``).
UPDATE, CURRENT, DISABLED, OFFLINE, UNAVAILABLE, FAILED = "update", "current", "disabled", "offline", "unavailable", "failed"


@dataclass(frozen=True)
class Result:
    kind: str
    offer: object = None    # update_feed.Offer for UPDATE
    detail: str = ""        # Stable code for logs and tests, never shown as is.
    skipped: bool = False   # UPDATE the person chose to skip: no notice, no status-bar item.
    snoozed: bool = False   # UPDATE the person chose "Later" for: no notice yet, the status-bar item stays.


def feed_url(policy, environ=None):
    """The feed to read: the machine policy's mirror, else the development override, else Angelo's own."""
    if policy.update_feed_url:
        return policy.update_feed_url
    return product_env.get(FEED_VARIABLE, environ) or FEED_URL


def disabled_reason(policy, state, manual, explicit_offline=False):
    """Why a check must not run now, or "": ``policy``, ``offline_mode`` or ``automatic_off``."""
    if policy.disable_update_check or policy.update_feed_url == managed_policy.INVALID:
        return "policy"
    if explicit_offline:
        return "offline_mode"
    if not manual and not state.automatic:
        return "automatic_off"
    return ""


def due(state, now, launch=False, rng=random.random):
    """True when an automatic check should run now."""
    if not state.last_attempt:
        return True
    if launch:
        recent_success = state.last_success and now - state.last_success < LAUNCH_AFTER_SUCCESS
        return not recent_success and now - state.last_attempt >= LAUNCH_AFTER_ATTEMPT
    return now >= next_due(state, rng)


def next_due(state, rng=random.random):
    """Epoch time of the next automatic check (a day after a success, else the failure backoff)."""
    if state.failures:
        return state.last_attempt + RETRY_AFTER_FAILURE[min(state.failures, len(RETRY_AFTER_FAILURE)) - 1]
    return state.last_success + DAY * (0.9 + 0.2 * rng())


def cached_feed(state, keys, now_utc=None):
    """The last good feed, verified again now; None when there is none or it no longer verifies."""
    if not state.envelope:
        return None
    try:
        return update_feed.open_feed(state.envelope.encode("utf-8"), keys, now_utc, last_sequence=0)
    except update_feed.FeedError:
        return None


class Checker:
    """One check against the feed. Create per use; all state lives in ``update_state``."""

    def __init__(self, directory, *, fetch=app_http.request, clock=time.time, keys=None, current=None, platform=None,
                 policy=None, explicit_offline=False, environ=None):
        self.directory, self.fetch, self.clock = directory, fetch, clock
        self.keys = update_feed.load_keys() if keys is None else keys
        self.current = current or app_version.current()
        self.platform = update_feed.platform_key() if platform is None else platform
        self.policy = policy or managed_policy.current()
        self.explicit_offline, self.environ = explicit_offline, environ

    def state(self):
        return update_state.load(self.directory)

    def _change(self, **fields):
        """Apply field changes to the freshly loaded state under the lock (never overwrites a newer choice)."""
        with _LOCK:
            state = self.state()
            for name, value in fields.items():
                setattr(state, name, value)
            return update_state.save(self.directory, state)

    def _result(self, feed, state, manual, now):
        offer = update_feed.offer_for(feed, self.current, self.platform)
        if offer is None:
            return Result(CURRENT)
        version = offer.release.version
        if manual:
            return Result(UPDATE, offer)
        return Result(UPDATE, offer, skipped=state.skipped(version, offer.critical), snoozed=state.snoozed(version, now))

    def pending(self):
        """The stored offer without any network (the status bar after a restart): a ``Result``."""
        if disabled_reason(self.policy, self.state(), True):
            return Result(DISABLED, detail="policy")
        state = self.state()
        feed = cached_feed(state, self.keys)
        return self._result(feed, state, False, self.clock()) if feed else Result(CURRENT)

    def check(self, manual=False):
        """Fetch, verify and decide. Blocking; call it from a worker thread. Never raises."""
        state = self.state()
        reason = disabled_reason(self.policy, state, manual, self.explicit_offline)
        if reason:
            return Result(OFFLINE if reason == "offline_mode" else DISABLED, detail=reason)
        if not self.keys:
            return Result(UNAVAILABLE, detail="no_keys")
        now = self.clock()
        result, changes = self._fetch_and_decide(state, now, manual)
        failed = result.kind not in {UPDATE, CURRENT}
        changes.update(last_attempt=now, failures=state.failures + 1 if failed else 0)
        if manual and result.kind == UPDATE:
            changes.update(skipped_version="", snoozed_version="", snoozed_until=0.0)  # A manual look starts over.
        self._change(**changes)
        return result

    def _fetch_and_decide(self, state, now, manual):
        """``(Result, state fields to store)``."""
        headers = {"Accept": "application/json"}
        if state.etag and state.envelope:
            headers["If-None-Match"] = state.etag
        try:
            response = self.fetch("GET", feed_url(self.policy, self.environ), headers=headers)
        except NetworkError as error:
            kind = OFFLINE if error.kind in {app_http.OFFLINE, app_http.TIMEOUT} else FAILED
            return Result(kind, detail=error.kind), {}
        if response.status == 304 and state.envelope:
            raw, etag = state.envelope.encode("utf-8"), state.etag
        elif response.status == 200:
            raw, etag = response.body, response.headers.get("etag", "")[:200]
        else:
            return Result(FAILED, detail="http_%d" % response.status), {}
        try:
            feed = update_feed.open_feed(raw, self.keys, datetime.fromtimestamp(now, timezone.utc), state.last_sequence)
        except update_feed.FeedError as error:
            return Result(FAILED, detail=error.code), {}
        stored = dict(last_success=now, last_sequence=max(state.last_sequence, feed.sequence), etag=etag,
                      envelope=raw.decode("utf-8", "replace"))
        return self._result(feed, state, manual, now), stored

    def snooze(self, offer):
        """"Later": the notice returns in 3 days (a critical release, in 1)."""
        seconds = SNOOZE_CRITICAL if offer.critical else SNOOZE
        return self._change(snoozed_version=offer.release.version, snoozed_until=self.clock() + seconds)

    def skip(self, offer):
        """"Skip this version". A critical release can't be skipped: it is snoozed for a day instead."""
        if offer.critical:
            return self.snooze(offer)
        return self._change(skipped_version=offer.release.version)

    def set_automatic(self, enabled):
        return self._change(automatic=bool(enabled))

    def last_seen(self):
        """The version whose What's New was last handled ("" before the first launch)."""
        return self.state().last_seen_version

    def mark_seen(self, version):
        return self._change(last_seen_version=version)

    def crash_report_url(self):
        """The crash-report endpoint the signed feed announced; "" when none or when the check is disabled by policy."""
        if disabled_reason(self.policy, self.state(), True):
            return ""
        feed = cached_feed(self.state(), self.keys)
        return feed.crash_reports_url if feed else ""
