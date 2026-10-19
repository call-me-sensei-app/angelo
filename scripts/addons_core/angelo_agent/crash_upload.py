# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Sends a crash report the person agreed to send.

The endpoint is announced by the signed update feed (``services.crash_reports``,
verified by ``update_feed``) so it can be switched on after release without a new
build, and can only ever be an Angelo host; ``ANGELO_CRASH_REPORT_URL`` overrides it
for development and tests (https, or http to this computer). With neither, there is
nowhere to send: the dialog then offers only Save report and Report a problem.

One POST of exactly ``crash_report.body(document)`` (at most 64 KiB) as
``application/json``: no cookie, no authorization, no account. The server
answers 202 (200 and 201 are accepted too) with ``{"id": "<report id>"}``. A 4xx
other than 408/425/429 means the server refused this report and it is never
retried; a network failure, 408, 425, 429 or 5xx is retried on a later launch
(``crash_state.MAX_ATTEMPTS``), not before the ``Retry-After`` it gave. The
service limits reports per network (5 in 10 minutes, 20 a day) and overall, so a
429 is not offered as an immediate "Try again". The machine policy
``DisableCrashReporting`` removes the endpoint altogether.

Standard library only; no bpy. Blocking: call it from a worker thread.
"""

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import app_http, managed_policy, product_env
from .app_http import NetworkError

URL_VARIABLE = "CRASH_REPORT_URL"
SENT, REFUSED, RETRY, OFFLINE = "sent", "refused", "retry", "offline"


MAX_RETRY_AFTER = 7 * 24 * 3600


@dataclass(frozen=True)
class Outcome:
    kind: str          # SENT, REFUSED (the server won't take it), RETRY (try again later), OFFLINE
    detail: str = ""   # Stable code for logs, never shown as is.
    receipt: str = ""  # The id the server confirmed.
    retry_after: float = 0.0   # Seconds the server asked to wait (429, 503), at most a week.

    @property
    def busy(self):
        """The service is limiting reports: don't offer an immediate retry."""
        return self.detail == "http_429"


def retry_after(headers):
    """``Retry-After`` in seconds (the integer form the service sends); 0 when absent or unusable."""
    value = str(headers.get("retry-after", "")).strip()
    return float(min(int(value), MAX_RETRY_AFTER)) if value.isdigit() else 0.0


def endpoint(feed_url, policy=None, environ=None):
    """Where to send, or "": the environment override, else the signed feed's URL; nothing when policy forbids reports."""
    policy = policy or managed_policy.current()
    if policy.disable_crash_reporting:
        return ""
    override = product_env.get(URL_VARIABLE, environ)
    url = override or feed_url
    try:
        app_http.check_url(url)
    except NetworkError:
        return ""
    return url if urlsplit(url).scheme == "https" or override else ""


def send(url, payload, report_id, request=app_http.request):
    """POST ``payload`` (bytes) and classify the answer. Never raises."""
    try:
        response = request("POST", url, body=payload, timeout=20, limit=16 * 1024,
                           headers={"Content-Type": "application/json", "Accept": "application/json"})
    except NetworkError as error:
        return Outcome(OFFLINE if error.kind in {app_http.OFFLINE, app_http.TIMEOUT} else RETRY, error.kind)
    if response.status in {200, 201, 202}:
        try:
            receipt = json.loads(response.body).get("id", "")
        except (ValueError, AttributeError, UnicodeError):
            receipt = ""
        return Outcome(SENT, "http_%d" % response.status, receipt if receipt == report_id else "")
    if response.status in {408, 425, 429} or response.status >= 500:
        return Outcome(RETRY, "http_%d" % response.status, retry_after=retry_after(response.headers))
    return Outcome(REFUSED, "http_%d" % response.status)
