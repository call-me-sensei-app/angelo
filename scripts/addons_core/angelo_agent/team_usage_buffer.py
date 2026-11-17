# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent-task counts waiting for the Angelo console, kept in one small file every Angelo process shares.

Contract: doc/angelo/ai_providers.rst, "Agent usage reports"; the console's
``POST /api/orgs/{org}/usage/app-reports`` (``appUsageReportBody``).

``team-usage.json`` in the connections folder holds, per signed-in person
(console, organization, user):

* ``counts``: agent tasks and the longest task (ms) per UTC day, gateway
  provider and model, not yet in a report;
* ``endpoint_counts``: per UTC day, custom endpoint, format, model (the server's
  own name) and the Angelo model it maps to ("" when untested, ``null`` on the
  wire), agent tasks and the requests' tokens (input, cached input, output,
  reasoning; Codex's TokenUsage meaning), for connections that bypass the
  gateway (``customEndpoints``);
* ``batch``: at most one report body, formed once with a random ``batchId``
  and sent verbatim until the console answers 2xx. A batch holds either gateway
  ``entries`` or ``customEndpoints`` (with ``entries`` empty), never both kinds,
  so a console that refuses the newer kind drops only those, never gateway counts. Counts that arrive later
  go to the next batch, so a retry never changes what the id stands for and
  the console adds a batch at most once;
* ``parked``: a ``customEndpoints`` batch a console refused (HTTP 400, a console
  older than the field), with when it was first refused. Gateway batches go
  ahead of it; it is sent again with the scheduled reports for ``PARK`` (6 h, so
  a console deployed after the first apps report still counts it), then dropped;
* ``hold_until`` (a 429's ``Retry-After``, or a short lease while one process
  sends) and ``next_at`` (the next scheduled report, shared so that several
  processes report about every 15 minutes in all): wall-clock seconds.

It holds no token, key, prompt or path. Every change is a read-modify-write
under ``team_lock.held(…, name="team-usage.lock")`` (an OS file lock, released
when a process dies) and lands with an atomic replace, so a crash leaves the
previous complete file; counts move into a batch under the same lock, so two
processes never report the same counts under different ids. The file is
bounded: days outside the console's window (older than 30 days, more than
one day ahead) are pruned, as are a person's keys past ``MAX_KEYS`` and people
past ``MAX_OWNERS``; an unreadable or malformed file reads as empty.

Standard library only; no bpy. Called on the team account's worker threads.
"""

import datetime
import errno
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from . import kept_state, owned_paths, shared_replace, team_lock

FILE = "team-usage.json"
LOCK = "team-usage.lock"
FORMAT = 1
DAYS_BACK = 30                # the console skips older days (APP_REPORT_MAX_DAYS_BACK)
MAX_ENTRIES = 200             # entries per report (APP_REPORT_MAX_ENTRIES)
MAX_TASKS = 10_000            # agentTasks per entry
MAX_TASK_MS = 86_400_000      # longestTaskMs
MAX_OWNERS = 4
MAX_KEYS = 1000               # pending day x provider x model keys per person
MAX_FILE = 1024 * 1024
_UNREAD, _DAMAGED = object(), object()   # ``_read``: a file it can't read now / no build can use (never "no file")
LEASE = 30                    # seconds other processes leave a batch being sent alone (the send's 20 s timeout)
SPACING = 14 * 60             # seconds after a delivered report before the next scheduled one
MAX_HOLD = 3600               # a hold further out is a clock change, and ignored
PARK = 6 * 3600               # seconds a refused customEndpoints batch is sent again before it is dropped
LOCK_TIMEOUT = 2.0
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_TOKENS = 10 ** 10         # each token field of a custom endpoint item (the console's bound)
TOKEN_FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens", "reasoningTokens")
ENDPOINT_FORMATS = ("openai-responses", "gemini", "anthropic-messages")
_ENDPOINT_ID = re.compile(r"[0-9a-f]{32}")
_MODEL_NAME = re.compile(r"[\x21-\x7e]{1,200}")   # A server's own model name (custom_endpoints' rule).
_PROFILE = re.compile(r"[\x20-\x7e]{1,64}")
ENDPOINT_KEYS = ("day", "endpointId", "format", "model", "profile", "agentTasks") + TOKEN_FIELDS


def day_of(seconds):
    """The UTC day ``YYYY-MM-DD`` of a wall-clock time."""
    return time.strftime("%Y-%m-%d", time.gmtime(seconds))


def _date(day):
    try:
        return datetime.date.fromisoformat(day) if isinstance(day, str) and _DAY.fullmatch(day) else None
    except ValueError:
        return None


def in_window(day, now):
    """Whether the console still counts ``day``: at most 30 days old and at most one day ahead."""
    date, today = _date(day), _date(day_of(now))
    return date is not None and today - datetime.timedelta(days=DAYS_BACK) <= date <= today + datetime.timedelta(days=1)


def _name(value, limit):
    return value if isinstance(value, str) and value == value.strip() and 1 <= len(value) <= limit else None


def _whole(value, limit):
    return value if type(value) is int and 0 <= value <= limit else None


def entry(day, provider, model, tasks, longest):
    """One report entry in the contract's shape, or None when a field is out of its range."""
    if (_date(day) is None or _name(provider, 60) is None or _name(model, 200) is None
            or _whole(tasks, MAX_TASKS) is None or longest is not None and _whole(longest, MAX_TASK_MS) is None):
        return None
    return {"day": day, "provider": provider, "model": model, "agentTasks": tasks, "longestTaskMs": longest}


def endpoint_entry(day, endpoint_id, format_id, model, profile, tasks, *tokens):
    """One ``customEndpoints`` item in the contract's shape, or None when a field is out of its range. ``profile`` is
    "" for an untested model (sent as null); cached input never exceeds input, nor reasoning output."""
    if (_date(day) is None or not isinstance(endpoint_id, str) or not _ENDPOINT_ID.fullmatch(endpoint_id)
            or format_id not in ENDPOINT_FORMATS or not isinstance(model, str) or not _MODEL_NAME.fullmatch(model)
            or not isinstance(profile, str) or profile and not _PROFILE.fullmatch(profile)
            or _whole(tasks, MAX_TASKS) is None or len(tokens) != len(TOKEN_FIELDS)
            or any(_whole(number, MAX_TOKENS) is None for number in tokens)
            or tokens[1] > tokens[0] or tokens[3] > tokens[2]):
        return None
    return dict(zip(ENDPOINT_KEYS, (day, endpoint_id, format_id, model, profile or None, tasks) + tuple(tokens)))


def merge_endpoint(counts, more):
    """Add ``more`` to ``counts`` in place: ``{(day, endpoint, format, model, profile): [tasks, *tokens]}``. The
    profile is part of the key: a name an admin maps again during the day keeps one item per profile that ran."""
    for key, (tasks, *tokens) in more.items():
        current = counts.setdefault(key, [0] + [0] * len(TOKEN_FIELDS))
        current[0] = min(MAX_TASKS, current[0] + tasks)
        for index, number in enumerate(tokens):
            current[1 + index] = min(MAX_TOKENS, current[1 + index] + number)
    return counts


def merge(counts, more):
    """Add ``more`` to ``counts`` in place: ``{(day, provider, model): [tasks, longest ms or None]}``."""
    for key, (tasks, longest) in more.items():
        current = counts.setdefault(key, [0, None])
        current[0] = min(MAX_TASKS, current[0] + tasks)
        if longest is not None:
            current[1] = min(MAX_TASK_MS, max(current[1] or 0, longest))
    return counts


def _endpoint_batch(value):
    """A stored ``customEndpoints`` report body (``entries`` empty), checked, or None."""
    try:
        identity = str(uuid.UUID(value["batchId"]))
    except (ValueError, TypeError, AttributeError):
        return None
    items = value["customEndpoints"] if isinstance(value["customEndpoints"], list) else []
    cleaned = [endpoint_entry(*((item.get(k) or "") if k == "profile" else item.get(k) for k in ENDPOINT_KEYS))
               for item in items if isinstance(item, dict) and set(item) == set(ENDPOINT_KEYS)]
    keys = {(e["day"], e["endpointId"], e["format"], e["model"], e["profile"]) for e in cleaned if e}
    if (identity != value["batchId"] or value["entries"] != [] or not 1 <= len(items) <= MAX_ENTRIES
            or None in cleaned or len(keys) != len(items)):
        return None
    return {"batchId": identity, "entries": [], "customEndpoints": cleaned}


def _batch_days(batch):
    return [item["day"] for item in batch.get("entries") or batch.get("customEndpoints") or ()]


def _batch(value):
    """A stored report body, checked against the contract, or None: gateway ``entries``, or ``customEndpoints``."""
    if isinstance(value, dict) and set(value) == {"batchId", "entries", "customEndpoints"}:
        return _endpoint_batch(value)
    if not isinstance(value, dict) or set(value) != {"batchId", "entries"}:
        return None
    try:
        identity = str(uuid.UUID(value["batchId"]))
    except (ValueError, TypeError, AttributeError):
        return None
    entries = value["entries"] if isinstance(value["entries"], list) else []
    cleaned = [entry(*(item.get(k) for k in ("day", "provider", "model", "agentTasks", "longestTaskMs")))
               for item in entries if isinstance(item, dict) and set(item) == {"day", "provider", "model",
                                                                               "agentTasks", "longestTaskMs"}]
    keys = {(e["day"], e["provider"], e["model"]) for e in cleaned if e}
    if (identity != value["batchId"] or not 1 <= len(entries) <= MAX_ENTRIES or None in cleaned
            or len(keys) != len(entries)):
        return None
    return {"batchId": identity, "entries": cleaned}


def _seconds(value):
    return value if type(value) in {int, float} and 0 <= value < 1e11 else 0


def _owner(value):
    """``(owner, record)`` from the file, or None. ``owner`` is ``(console, org, user)``."""
    if not isinstance(value, dict):
        return None
    owner = tuple(value.get(k) for k in ("console", "org", "user"))
    if not all(isinstance(part, str) and 1 <= len(part) <= 512 for part in owner):
        return None
    counts = {}
    for item in value.get("counts") if isinstance(value.get("counts"), list) else ():
        cleaned = entry(*item) if isinstance(item, list) and len(item) == 5 else None
        if cleaned and len(counts) < MAX_KEYS:
            merge(counts, {(cleaned["day"], cleaned["provider"], cleaned["model"]):
                           (cleaned["agentTasks"], cleaned["longestTaskMs"])})
    endpoint_counts = {}
    for item in value.get("endpoint_counts") if isinstance(value.get("endpoint_counts"), list) else ():
        cleaned = endpoint_entry(*item) if isinstance(item, list) and len(item) == len(ENDPOINT_KEYS) else None
        if cleaned and len(endpoint_counts) < MAX_KEYS:
            merge_endpoint(endpoint_counts, {tuple(item[:5]): tuple(item[5:])})
    parked = value.get("parked") if isinstance(value.get("parked"), dict) else {}
    parked_batch = _batch(parked.get("batch"))
    return owner, {"counts": counts, "endpoint_counts": endpoint_counts, "batch": _batch(value.get("batch")),
                   "hold_until": _seconds(value.get("hold_until")), "next_at": _seconds(value.get("next_at")),
                   "updated": _seconds(value.get("updated")),
                   "parked": {"batch": parked_batch, "since": _seconds(parked.get("since"))}
                   if parked_batch and "customEndpoints" in parked_batch else None}


def _encode(owners):
    rows = []
    for (console, org, user), record in owners.items():
        rows.append({"console": console, "org": org, "user": user, "updated": record["updated"],
                     "hold_until": record["hold_until"], "next_at": record["next_at"], "batch": record["batch"],
                     "parked": record["parked"],
                     "counts": [[*key, *value] for key, value in sorted(record["counts"].items())],
                     "endpoint_counts": [[*key, *value] for key, value in sorted(record["endpoint_counts"].items())]})
    return json.dumps({"version": FORMAT, "owners": rows}, separators=(",", ":"), allow_nan=False)


class UsageBuffer:
    """``team-usage.json`` in ``directory``. Nothing is read or written until the first call."""

    def __init__(self, directory, *, lock_timeout=LOCK_TIMEOUT):
        self.directory = str(directory)
        self.path = os.path.join(self.directory, FILE)
        self.lock_timeout = lock_timeout
        self._thread_lock = threading.Lock()  # The file lock excludes other processes; this, other threads here.

    # Each call: lock, read, change, write. ``team_lock.LockTimeout`` and ``OSError`` reach the caller, which keeps
    # its counts in memory and tries again later.

    def add(self, owner, counts, now, endpoint_counts=None):
        """Keep ``counts`` (``merge``'s shape) and ``endpoint_counts`` (``merge_endpoint``'s) for ``owner``; days the
        console wouldn't count are dropped."""
        def change(owners):
            record = owners.setdefault(owner, _empty())
            for kept, more, add in ((record["counts"], counts, merge),
                                    (record["endpoint_counts"], endpoint_counts or {}, merge_endpoint)):
                for key, value in more.items():
                    if in_window(key[0], now) and (key in kept or len(kept) < MAX_KEYS):
                        add(kept, {key: value})
            record["updated"] = now
        self._change(change, now)

    def take(self, owner, now, *, scheduled=True, lease=LEASE):
        """The report to send for ``owner`` now, or None: its pending batch, else a new one from its counts.

        ``scheduled``: a periodic report, which waits for ``next_at``; a report at quit doesn't. ``lease``: seconds
        other processes leave this batch alone while it is sent (0 at quit: the process may end mid-send).
        """
        taken = []

        def change(owners):
            record = owners.get(owner)
            if record is None or _held(record["hold_until"], now) or scheduled and _held(record["next_at"], now):
                return
            if record["batch"] is None and record["counts"]:
                keys = sorted(record["counts"])[:MAX_ENTRIES]
                entries = [entry(*key, *record["counts"].pop(key)) for key in keys]
                record["batch"] = {"batchId": str(uuid.uuid4()), "entries": entries}
            elif record["batch"] is None and record["parked"] is not None:   # A refused batch, again (``PARK``).
                if _parked(record["parked"]["since"], now):
                    record["batch"] = record["parked"]["batch"]
                else:
                    record["parked"] = None
            if record["batch"] is None and record["endpoint_counts"] and record["parked"] is None:
                keys = sorted(record["endpoint_counts"])[:MAX_ENTRIES]
                items = [endpoint_entry(*key, *record["endpoint_counts"].pop(key)) for key in keys]
                record["batch"] = {"batchId": str(uuid.uuid4()), "entries": [], "customEndpoints": items}
            if record["batch"] is not None:
                record["hold_until"] = now + lease if lease else record["hold_until"]
                taken.append(record["batch"])
        self._change(change, now)
        return taken[0] if taken else None

    def settle(self, owner, batch_id, outcome, now, hold=0):
        """After sending ``batch_id``: ``sent`` or ``drop`` removes it; ``retry`` keeps it; ``park`` (a refused
        ``customEndpoints`` batch) sets it aside for the next scheduled reports until ``PARK`` has passed since it
        was first refused; ``hold`` (seconds, a 429's Retry-After) keeps every process from sending before then.
        True when more is waiting."""
        more = []

        def change(owners):
            record = owners.get(owner)
            if record is None:
                return
            batch, parked = record["batch"], record["parked"]
            same = parked is not None and parked["batch"]["batchId"] == batch_id
            if batch is not None and batch["batchId"] == batch_id and outcome in {"sent", "drop", "park"}:
                record["batch"] = None
                if outcome == "park" and "customEndpoints" in batch:
                    record["parked"] = {"batch": batch, "since": parked["since"] if same else now}
                    record["next_at"] = now + SPACING
                elif same:
                    record["parked"] = None
            if outcome == "sent":
                record["next_at"] = now + SPACING
            record["hold_until"] = now + min(hold, MAX_HOLD) if hold else 0
            more.append(bool(record["batch"] or record["counts"] or record["endpoint_counts"] and not record["parked"]))
        self._change(change, now)
        return bool(more and more[0])

    def pending(self, owner, now):
        """``(counts, batch)`` kept for ``owner`` (tests and diagnostics)."""
        with self._thread_lock, team_lock.held(self.directory, self.lock_timeout, name=LOCK):
            record = _prune(self._read()[0], now).get(owner) or _empty()
        return record["counts"], record["batch"]

    def pending_endpoints(self, owner, now):
        """``endpoint_counts`` kept for ``owner`` (tests and diagnostics)."""
        with self._thread_lock, team_lock.held(self.directory, self.lock_timeout, name=LOCK):
            return (_prune(self._read()[0], now).get(owner) or _empty())["endpoint_counts"]

    def _change(self, change, now):
        with self._thread_lock, team_lock.held(self.directory, self.lock_timeout, name=LOCK):
            owners, data = self._read()
            owners = _prune(owners, now)
            change(owners)
            owners = _prune(owners, now)
            if data is _UNREAD or data is _DAMAGED:   # Left as it is with nothing to write.
                if not owners:
                    return
                # Counts it can't read now are never written over, and a damaged file is kept beside it first;
                # otherwise the caller keeps this change and tries again.
                if data is _UNREAD or not kept_state.keep_before_write(Path(self.path)):
                    raise OSError(errno.EIO, "The team usage file can't be read now")
                self._write(owners)
            elif (_encode(owners) if owners else None) != data:  # An unchanged file is not rewritten.
                self._write(owners)

    def _read(self):
        """``(owners, the file's text)``: None for no file; ``_UNREAD`` for one that can't be read now (never written
        over); ``_DAMAGED`` for one that is damaged, too large or another version's (kept beside it, then replaced)."""
        try:
            with shared_replace.open_file(self.path) as handle:   # Shares delete (shared_replace).
                raw = handle.read(MAX_FILE + 1)
        except FileNotFoundError:
            return {}, None
        except OSError:
            return {}, _UNREAD
        try:
            data = raw.decode("utf-8") if len(raw) <= MAX_FILE else None
            value = json.loads(data) if data is not None else None
        except ValueError:
            return {}, _DAMAGED
        if not isinstance(value, dict) or value.get("version") != FORMAT:
            return {}, _DAMAGED
        rows = value.get("owners")
        return dict(owner for owner in map(_owner, rows if isinstance(rows, list) else ()) if owner), data

    def _write(self, owners):
        """Replace the file atomically (the caller holds the lock, so the one temporary name is safe)."""
        owned_paths.require_writable(self.path)
        if not owners:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            return
        temporary = self.path + ".tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(temporary, flags, 0o600), "w", encoding="utf-8") as handle:
            handle.write(_encode(owners))
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, self.path)   # Refused: raised; the caller keeps the counts and retries.


def _empty():
    return {"counts": {}, "endpoint_counts": {}, "batch": None, "hold_until": 0, "next_at": 0, "updated": 0,
            "parked": None}


def _parked(since, now):
    """Whether a batch first refused at ``since`` is still sent again; a ``since`` ahead of now means the clock
    moved back."""
    return now - PARK <= since <= now + SPACING


def _held(until, now):
    """Whether a hold is still running; one further out than ``MAX_HOLD`` means the clock moved back."""
    return now < until <= now + max(MAX_HOLD, SPACING)


def _prune(owners, now):
    """Drop days the console no longer counts, batches that would add nothing, idle and surplus people."""
    for record in owners.values():
        for name in ("counts", "endpoint_counts"):
            record[name] = {key: value for key, value in record[name].items() if in_window(key[0], now)}
        batch = record["batch"]
        if batch is not None and not any(in_window(day, now) for day in _batch_days(batch)):
            record["batch"] = None
        if record["parked"] is not None and not _parked(record["parked"]["since"], now):
            record["parked"] = None
    active = {owner: record for owner, record in owners.items() if record["counts"] or record["endpoint_counts"]
              or record["batch"] or record["parked"]
              or _held(record["hold_until"], now) or _held(record["next_at"], now)}
    newest = sorted(active, key=lambda owner: active[owner]["updated"], reverse=True)[:MAX_OWNERS]
    return {owner: active[owner] for owner in newest}
