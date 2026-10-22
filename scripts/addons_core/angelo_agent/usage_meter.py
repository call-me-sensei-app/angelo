# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tokens and an estimate at list price, per chat and since Angelo started, for the chat's usage meter.

Contract: doc/angelo/agent_chat.rst, "Usage meter".

* **Source.** A finished request's receipt (``telemetry.clean_receipt``): its provider usage, its route
  (connection mode, served or requested model, prompt-cache TTL) and its read-only consultations. Codex
  warm-ups (``codex_bases.report_warm_up``) count since Angelo started, never in a chat. Nothing here
  asks a provider or the network for anything.
* **Estimate.** Tokens times ``model_profiles.rates`` for the model that served the request: new input,
  cache reads, cache writes (at the session's TTL rate) and output. A request Claude Code served with
  several models is priced at Claude Code's own list-price figure instead. A model the table does not
  know is counted in tokens and as *not priced*, never as zero dollars. A request whose usage a runtime
  did not fully report (a stopped turn, a missing field) is a lower bound: ``partial`` counts it.
* **Billing.** The connection's method (``provider_registry``) decides what the meter says: a
  subscription's usage is included in the plan and gets no dollar figure where it is shown; an API key
  or a team connection gets the estimate. This module only keeps the numbers per connection mode.
* **Storage.** Per-chat totals live in ``usage.json`` in the state folder (never in the chat file, so an
  older build still opens the chat), written on a background thread, coalesced, bounded to
  ``MAX_CHATS``; the totals since Angelo started are memory only. ``add`` does no I/O on its caller's
  thread. An unreadable file reads as empty.

Standard library only; no bpy.
"""

import copy
import json
import math
import os
import stat
import threading
import time
import uuid
import weakref
from pathlib import Path

from . import model_profiles

TOKEN_KEYS = ("input", "cache_read", "cache_write", "output")
COUNT_KEYS = ("requests", "unpriced", "partial")
MAX_CHATS = 1000
MAX_MODELS = 4
MAX_FILE_BYTES = 1024 * 1024
MAX_TOKENS = 10 ** 13
MAX_USD = 10 ** 7
_LOCK = threading.Lock()
_LEDGERS = {}


def _int(value):
    return value if type(value) is int and 0 <= value <= MAX_TOKENS else None


def _model(route):
    """The model to price a request at: the one model that served it, else the one requested, else None."""
    served = [model for model in route.get("served_models") or () if isinstance(model, str)]
    if len(served) == 1:
        return served[0]
    requested = route.get("requested_model")
    return requested if not served and isinstance(requested, str) and requested else None


def entry(route, usage):
    """One request's (or consultation's) tokens and estimate, or None when it reported no usage.

    ``usage`` uses ``provider_usage``'s normalized fields: ``input_tokens`` counts every prompt token,
    cache reads and writes included.
    """
    if not isinstance(usage, dict) or not isinstance(route, dict):
        return None
    prompt, read, write, output = (_int(usage.get(key)) for key in (
        "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens"))
    if prompt is None and output is None:
        return None
    read, write = read or 0, write or 0
    tokens = {"input": max(0, (prompt or 0) - read - write), "cache_read": read, "cache_write": write,
              "output": output or 0}
    complete = None not in (prompt, output) and not usage.get("interrupted_turns")
    model = _model(route)
    requests = usage.get("requests") if type(usage.get("requests")) is int and usage["requests"] > 0 else 1
    rates = model_profiles.rates(model, route.get("cache_ttl") or "5m",
                                 prompt_tokens=(prompt or 0) // requests) if model else None
    usd = None
    if rates is not None and prompt is not None and output is not None:
        usd = sum(tokens[key] * rates[rate] for key, rate in (
            ("input", "input"), ("cache_read", "cache_read"), ("cache_write", "cache_write"),
            ("output", "output"))) / 1e6
    elif route.get("served_models") and _cost(usage.get("cost_usd")) is not None:
        usd = _cost(usage["cost_usd"])  # Claude Code's own list-price figure for a mixed-model turn.
    canonical = model_profiles.resolve(model) if model else None
    return dict(tokens, requests=1, usd=usd or 0.0, unpriced=int(usd is None), partial=int(not complete),
                models=[canonical or model] if model else [])


def _cost(value):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= MAX_USD else None


def receipt_entries(receipt, default_mode=None):
    """``[(mode, entry)]`` for a finished receipt: the request's own usage, then each consultation's.

    ``default_mode`` is the chat's connection mode, for a receipt whose route does not name one.
    """
    if not isinstance(receipt, dict):
        return []
    route = dict(receipt.get("route") or {})
    route.setdefault("mode", default_mode)
    output = []
    own = entry(route, receipt.get("usage"))
    if own is None and (receipt.get("counts") or {}).get("provider_turn_requests"):
        # The request reached the provider but no usage was reported: count it, unmeasured.
        own = dict(dict.fromkeys(TOKEN_KEYS, 0), requests=1, usd=0.0, unpriced=0, partial=1, models=[])
    if own is not None and route.get("mode"):
        output.append((route["mode"], own))
    for consultation in receipt.get("consultations") or ():
        if isinstance(consultation, dict):
            value = entry(consultation, consultation.get("usage"))
            mode = consultation.get("mode") or route.get("mode")
            if value is not None and mode:
                output.append((mode, value))
    return output


def add_to(totals, mode, value):
    """Add one entry to a ``{mode: totals}`` mapping in place."""
    bucket = totals.setdefault(mode, dict(dict.fromkeys(TOKEN_KEYS + COUNT_KEYS, 0), usd=0.0, models=[]))
    for key in TOKEN_KEYS + COUNT_KEYS:
        bucket[key] = min(MAX_TOKENS, bucket[key] + value.get(key, 0))
    bucket["usd"] = min(MAX_USD, bucket["usd"] + value.get("usd", 0.0))
    for model in value.get("models", ()):
        if model not in bucket["models"] and len(bucket["models"]) < MAX_MODELS:
            bucket["models"].append(model)
    return totals


def combined(totals_list):
    """One ``{mode: totals}`` from several (a chat's saved totals plus its request in progress)."""
    output = {}
    for totals in totals_list:
        for mode, bucket in (totals or {}).items():
            add_to(output, mode, bucket)
    return output


def _clean_totals(value):
    output = {}
    for mode, bucket in (value.items() if isinstance(value, dict) else ()):
        if not isinstance(mode, str) or len(mode) > 64 or not isinstance(bucket, dict):
            continue
        clean = {key: _int(bucket.get(key)) or 0 for key in TOKEN_KEYS + COUNT_KEYS}
        clean["usd"] = _cost(bucket.get("usd")) or 0.0
        models = bucket.get("models")
        clean["models"] = [model for model in (models if isinstance(models, (list, tuple)) else ())
                           if isinstance(model, str) and 0 < len(model) <= 128][:MAX_MODELS]
        output[mode] = clean
    return output


def _clean_chat(value):
    if not isinstance(value, dict):
        return None
    totals = _clean_totals(value.get("modes"))
    if not totals:
        return None
    last = value.get("last") if isinstance(value.get("last"), dict) else None
    last = _clean_totals({last.get("mode"): last}) if last and isinstance(last.get("mode"), str) else {}
    updated = value.get("updated") if type(value.get("updated")) in (int, float) else 0
    record = {"modes": totals, "updated": updated}
    if last:
        mode, bucket = next(iter(last.items()))
        record["last"] = dict(bucket, mode=mode)
    return record


class UsageLedger:
    """Per-chat totals (saved) and the totals since this process started (memory only), per state folder."""

    def __init__(self, directory, clock=time.time):
        self.path = Path(directory) / "usage.json"
        self.clock = clock
        self.lock = threading.Lock()
        self.chats = {}
        self.since_start = {}
        self.chats_since_start = set()
        self.revision = 0  # Grows with every change: the meter's cached label is keyed by it.
        self._dirty = threading.Event()
        self._worker, self._stopping = None, False
        # One write at a time, each from a snapshot taken inside it: a later write never carries older totals.
        self._write_lock = threading.Lock()
        self._load()

    @classmethod
    def shared(cls, directory):
        """The state folder's one ledger. Looked up by the path as given first: a resolve costs system calls."""
        with _LOCK:
            ledger = _LEDGERS.get(str(directory))
            if ledger is None:
                key = str(Path(directory).resolve())
                ledger = _LEDGERS.get(key) or cls(key)
                _LEDGERS[key] = _LEDGERS[str(directory)] = ledger
            return ledger

    def _load(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                return
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                raw = json.loads(handle.read(MAX_FILE_BYTES + 1))
        except (OSError, ValueError, RecursionError):
            return
        if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("chats"), dict):
            return
        for chat_id, value in raw["chats"].items():
            try:
                record = _clean_chat(value)
            except (TypeError, ValueError, AttributeError, KeyError, OverflowError, RecursionError):
                record = None  # A malformed record reads as empty: the ledger itself always loads.
            if isinstance(chat_id, str) and len(chat_id) <= 64 and record:
                self.chats[chat_id] = record
        self._bound()

    def _bound(self):
        if len(self.chats) > MAX_CHATS:
            for chat_id in sorted(self.chats, key=lambda key: self.chats[key]["updated"])[:len(self.chats) - MAX_CHATS]:
                del self.chats[chat_id]

    def add(self, chat_id, receipt, default_mode=None):
        """A chat's finished request. Returns whether anything was counted. No I/O on this thread."""
        entries = receipt_entries(receipt, default_mode)
        if not entries:
            return False
        with self.lock:
            record = self.chats.setdefault(chat_id, {"modes": {}, "updated": 0}) if chat_id else None
            last = {}
            for mode, value in entries:
                add_to(self.since_start, mode, value)
                add_to(last, mode, value)
                if record is not None:
                    add_to(record["modes"], mode, value)
            if record is not None:
                self.chats_since_start.add(chat_id)
                mode = entries[0][0]
                record["last"] = dict(last[mode], mode=mode)  # The request's own connection.
                record["updated"] = self.clock()
                self._bound()
            self.revision += 1
        if record is not None:
            self._schedule()
        return True

    def add_background(self, receipt):
        """Usage that belongs to no chat (a Codex warm-up): counted since Angelo started only."""
        return self.add(None, receipt)

    def chat(self, chat_id):
        with self.lock:
            record = self.chats.get(chat_id)
            return copy.deepcopy(record) if record else None

    def since_started(self):
        with self.lock:
            return {"modes": copy.deepcopy(self.since_start), "chats": len(self.chats_since_start)}

    # Background persistence -------------------------------------------------------------

    def _schedule(self):
        self._dirty.set()
        with self.lock:  # Started under the lock: another caller never sees a created, not yet started writer.
            if self._worker is not None and self._worker.is_alive():
                return
            worker = threading.Thread(target=self._writer, daemon=True, name="AngeloUsageWriter")
            try:
                worker.start()
            except RuntimeError:
                self._worker = None  # The totals stay in memory; the meter never stops a chat.
                return
            self._worker = worker

    def _writer(self):
        while True:
            woke = self._dirty.wait(timeout=2)
            if woke:
                self._dirty.clear()
                self.flush()
            if not woke or self._stopping:
                break
        with self.lock:
            self._worker = None
        if self._dirty.is_set() and not self._stopping:
            self._schedule()

    def drain(self, timeout=2.0):
        """Finish a pending background write (a chat closing, quit, tests); never called while drawing."""
        with self.lock:
            worker = self._worker
        if worker is None:
            return
        self._stopping = True
        self._dirty.set()
        worker.join(timeout)
        self._stopping = False

    def flush(self):
        """Write the per-chat totals now (the background writer; tests). Returns whether it was written."""
        with self._write_lock:
            return self._write()

    def _write(self):
        with self.lock:
            document = {"version": 1, "chats": copy.deepcopy(self.chats)}
        temporary = self.path.with_name(".usage-" + uuid.uuid4().hex + ".tmp")
        try:
            encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
            os.replace(temporary, self.path)
            return True
        except (OSError, ValueError, TypeError):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False


def record(directory, chat_id, receipt, default_mode=None):
    """The controller's hook for a finished request (``Controller._finish_metrics``). Never raises."""
    try:
        UsageLedger.shared(directory).add(chat_id, receipt, default_mode)
    except Exception:  # noqa: BLE001 - the meter never stops a chat; this request is left uncounted.
        pass


def record_background(directory, receipt):
    """A Codex warm-up's receipt (``codex_bases.report_warm_up``): since Angelo started only. Never raises."""
    try:
        UsageLedger.shared(directory).add_background(receipt)
    except Exception:  # noqa: BLE001
        pass


def in_progress(metrics, default_mode=None):
    """``{mode: totals}`` of a request still running (``telemetry.RequestMetrics``), or None.

    Its numbers are lower bounds (``provider_usage.running_usage``): the meter shows them as live.
    """
    from .provider_usage import running_usage
    if metrics is None or metrics.finished:
        return None
    usage = running_usage(metrics) or {}
    with metrics.lock:
        route = dict(metrics.route, served_models=list(metrics.served_models))
    mode = route.get("mode") or default_mode
    value = entry(route, usage) or (dict(dict.fromkeys(TOKEN_KEYS, 0), usd=0.0, models=[]) if mode else None)
    return add_to({}, mode, dict(value, requests=0, unpriced=0, partial=0)) if value and mode else None


def drain(directory, timeout=0.2):
    """Finish the state folder's pending usage write, if any (``Controller.close``). Never raises."""
    try:
        ledger = _LEDGERS.get(str(directory))
        if ledger is not None:
            ledger.drain(timeout)
    except Exception:  # noqa: BLE001
        pass


# The live per-turn count (``turn_tokens``): characters streamed per running request, for the estimate between
# the runtime's own counts. Display only: never in a receipt, the ledger or anything a model reads.
CHARS_PER_TOKEN = 4
_streams = weakref.WeakKeyDictionary()  # RequestMetrics -> [chars streamed, chars at the last count, last count]
_streams_lock = threading.Lock()


def streamed(metrics, chars):
    """A runtime streamed ``chars`` characters for this request (provider threads). Never raises."""
    if metrics is None or type(chars) is not int or chars <= 0:
        return
    try:
        with _streams_lock:
            _streams.setdefault(metrics, [0, 0, 0])[0] += chars
    except TypeError:
        pass


def turn_tokens(controller):
    """Output tokens of the chat's request in progress (thinking included), or None: nothing running or arrived.

    What the runtime has reported so far plus ``CHARS_PER_TOKEN`` characters per token streamed since its last
    report; each report replaces the estimate, and the finished receipt is the final count. Main thread, O(1)
    (two short locks a provider thread holds only to add a number); never raises (the live row paints with it).
    """
    try:
        return _turn_tokens(controller)
    except Exception:  # noqa: BLE001 - no token part rather than a broken row.
        return None


def _turn_tokens(controller):
    from .provider_usage import running_usage
    metrics = getattr(controller, "_request_metrics", None)
    if metrics is None or metrics.finished:
        return None
    usage = running_usage(metrics) or {}
    reported = usage.get("output_tokens", 0)
    with _streams_lock:
        state = _streams.get(metrics)
        if state is None:
            return reported or None
        if state[2] != reported:
            state[1], state[2] = state[0], reported  # A new count: the estimate starts again from here.
        estimate = (state[0] - state[1]) // CHARS_PER_TOKEN
    return (reported + estimate) or None
