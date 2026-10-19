# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The fake console's ``POST /api/orgs/{org}/usage/app-reports``, for host tests only.

Follows the console's usage-report contract (``appUsageReportBody``): the bearer token's
grant and organization are checked first (401, 404), then the rate limit (60
an hour per person, counted before the body is read, ``Retry-After`` on 429),
then the body exactly as zod parses it (strict objects, a UUID ``batchId``,
1-200 entries, each day once per provider and model; 400
``VALIDATION_FAILED`` otherwise). A batch id is applied once: a repeat answers
``applied: false`` and adds nothing. Days older than 30 or more than one ahead
of ``clock`` are skipped and counted. Tasks add up; the longest task only
grows.

Test knobs: ``fail_next`` (an HTTP status answered before anything is
applied, or ``"drop"``: applied, then the connection closes without an
answer), ``delay`` (seconds before answering), ``limit`` and ``clock``.
"""

import datetime
import json
import math
import re
import threading
import time
from collections import deque

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# zod 4 ``z.uuid()``: RFC 9562 versions 1-8 with the RFC variant, plus the nil and max UUIDs.
UUID = re.compile(r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
                  r"|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$")
ENTRY_KEYS = {"day", "provider", "model", "agentTasks", "longestTaskMs"}
MAX_ENTRIES, MAX_DAYS_BACK = 200, 30
LIMITED = "Too many attempts. Wait a minute and try again."


def _integer(value, high):
    """``z.number().int().min(0).max(high)``: a JSON number with no fraction (a boolean is not a number)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() and 0 <= value <= high else None


def _text(value, high):
    """``z.string().trim().min(1).max(high)``: the trimmed text, or None."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed if 1 <= len(trimmed) <= high else None


def parse(body):
    """The parsed body as zod returns it, or a list of problems."""
    if not isinstance(body, dict) or set(body) - {"batchId", "entries"}:
        return ["body: unknown or missing fields"]
    problems, entries = [], []
    if not isinstance(body.get("batchId"), str) or not UUID.match(body["batchId"]):
        problems.append("batchId: Invalid UUID")
    items = body.get("entries")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ENTRIES:
        return problems + ["entries: 1 to %d entries" % MAX_ENTRIES]
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - ENTRY_KEYS or not ENTRY_KEYS - {"longestTaskMs"} <= set(item):
            problems.append("entries.%d: unknown or missing fields" % index)
            continue
        longest = item.get("longestTaskMs")  # ``.nullable().default(null)``: absent means null.
        parsed = {"day": item["day"] if isinstance(item["day"], str) and DAY.match(item["day"]) else None,
                  "provider": _text(item["provider"], 60), "model": _text(item["model"], 200),
                  "agentTasks": _integer(item["agentTasks"], 10_000),
                  "longestTaskMs": None if longest is None else _integer(longest, 86_400_000)}
        bad = [key for key, value in parsed.items()
               if value is None and (key != "longestTaskMs" or longest is not None)]
        problems += ["entries.%d.%s: invalid" % (index, key) for key in bad]
        entries.append(parsed)
    keys = ["%s|%s|%s" % (e["day"], e["provider"], e["model"]) for e in entries]
    if not problems and len(set(keys)) != len(keys):
        problems.append("entries: Send each day, provider and model once.")
    return problems or {"batchId": body["batchId"], "entries": entries}


class FakeUsageReports:
    def __init__(self, limit=60):
        self.lock = threading.Lock()
        self.limit, self.clock, self.delay, self.fail_next = limit, time.time, 0.0, None
        self.rows = {}          # (user, day, provider, model) -> {"agentTasks": n, "longestTaskMs": ms or None}
        self.batches = set()    # (org, user, batch id) received
        self.received = []      # (status, body or raw bytes) of every report request that passed authentication
        self._calls = {}        # user -> deque of monotonic times, for the hourly limit

    def handle(self, org, user, raw):
        """``(status, value, headers)`` for an authenticated member of ``org``."""
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            calls = self._calls.setdefault(user, deque())
            now = time.monotonic()
            while calls and now - calls[0] >= 3600:
                calls.popleft()
            if len(calls) >= self.limit:
                self.received.append((429, raw))
                wait = max(1, math.ceil(3600 - (now - calls[0])))  # As the console: Math.ceil(retryAfterMs / 1000).
                return 429, {"error": {"code": "RATE_LIMITED", "message": LIMITED}}, {"Retry-After": str(wait)}
            calls.append(now)
            failure, self.fail_next = self.fail_next, None
        if isinstance(failure, int):
            self.received.append((failure, raw))
            code = {429: "RATE_LIMITED", 400: "VALIDATION_FAILED", 403: "FORBIDDEN"}.get(failure, "INTERNAL")
            headers = {"Retry-After": "120"} if failure == 429 else {}
            return failure, {"error": {"code": code, "message": "Test failure."}}, headers
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = None
        parsed = parse(body)
        if isinstance(parsed, list):
            self.received.append((400, raw))
            return 400, {"error": {"code": "VALIDATION_FAILED", "message": "Some fields need attention.",
                                   "fields": parsed}}, {}
        answer = self._apply(org, user, parsed)
        self.received.append((200, body))
        if failure == "drop":
            return "drop", None, {}
        return 200, {"data": answer}, {}

    def _apply(self, org, user, body):
        today = datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc).date()
        earliest, latest = today - datetime.timedelta(days=MAX_DAYS_BACK), today + datetime.timedelta(days=1)
        kept = [e for e in body["entries"] if earliest <= datetime.date.fromisoformat(e["day"]) <= latest]
        with self.lock:
            if (org, user, body["batchId"]) in self.batches:
                return {"applied": False, "skipped": len(body["entries"]) - len(kept)}
            self.batches.add((org, user, body["batchId"]))
            for item in kept:
                row = self.rows.setdefault((user, item["day"], item["provider"], item["model"]),
                                           {"agentTasks": 0, "longestTaskMs": None})
                row["agentTasks"] += item["agentTasks"]
                if item["longestTaskMs"] is not None:
                    row["longestTaskMs"] = max(row["longestTaskMs"] or 0, item["longestTaskMs"])
        return {"applied": True, "skipped": len(body["entries"]) - len(kept)}

    def totals(self):
        """``{(day, provider, model): (tasks, longest)}`` over everyone."""
        with self.lock:
            return {key[1:]: (row["agentTasks"], row["longestTaskMs"]) for key, row in self.rows.items()}

    def reports(self):
        """Accepted report bodies, in order."""
        with self.lock:
            return [body for status, body in self.received if status == 200]
