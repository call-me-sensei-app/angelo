# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local, bounded performance receipts. No transcript, tool arguments, or uploads.

Durations are monotonic wall-clock measurements, not CPU time. Phase sums may
be smaller than total (provider/user wait) or overlap (integrated read-back).
Missing phases are *unmeasured*, never an invented zero-time verification.
Peak phases keep the longest single sample instead of a sum. Provider usage
keeps only fields that every provider turn of the request reported.
"""

import json
import math
import os
import queue
import stat
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .chat_settings import EFFORTS
from .fallback_categories import CATEGORIES as FALLBACK_CATEGORIES
from .failure_codes import CODES as FAILURE_CODES
from .outcome_evidence import scene_outcome_evidence  # noqa: F401  (its importers' name)
from .provider_registry import CODING_MODES
from .provider_usage import MAX_COST_USD, MAX_SERVED_MODELS, MAX_TOKENS, TOKENS as USAGE_TOKENS
from .request_path import PathFacts, clean_path, running_usage
from .runtime_environment import PROMPT_CACHE_TTLS

PHASES = frozenset({"checkpoint_ms", "runtime_start_ms", "first_text_ms", "first_native_action_ms",
                   "first_scene_change_ms", "first_verified_result_ms",
                   "queued_wait_ms", "precondition_ms", "execution_ms", "staging_ms", "commit_ms",
                   "verification_ms", "approval_wait_ms", "main_thread_block_ms", "total_ms"})
PEAK_PHASES = frozenset({"main_thread_block_ms"})
FALLBACK_COUNTS = frozenset({"python_fallbacks"} | {"python_fallback_" + name for name in FALLBACK_CATEGORIES})
COUNTS = frozenset({"tool_invocations", "tool_executions", "tool_completions", "tool_failures", "replays",
                   "duplicate_pending", "invalid_calls", "rejections", "native_steps", "native_commits",
                   "precondition_checks", "verification_samples"})
COUNTS = COUNTS | {"model_rounds", "provider_turn_requests"} | FALLBACK_COUNTS
# Host recoveries of a program whose bound frame moved (program_context), content-free classes of failed tool
# results (failure_codes) and turns resent after a transient provider failure (codex_retry).
COUNTS = COUNTS | {"frame_rebinds", "staging_restarts", "playback_waits", "provider_retries_rate", "provider_retries_busy"}
COUNTS = COUNTS | {"failure_" + code for code in FAILURE_CODES} | {"runtime_prewarmed"}  # Adopted a prewarm (runtime_prewarm).
USAGE_TIMES = ("turn_ms", "api_ms")
USAGE_FIELDS = USAGE_TOKENS + USAGE_TIMES + ("cost_usd",)
# Receipt-only: model requests a provider turn reported (provider_usage), scripts that raised, resends, prewarms.
# None is in report(), which a model reads with angelo_agent_metrics: its bytes stay as they were.
USAGE_SUMS = USAGE_FIELDS + ("requests",)
RECEIPT_ONLY_COUNTS = frozenset({"failure_script", "provider_retries_rate", "provider_retries_busy", "runtime_prewarmed"})
# "escalated": Auto routing handed the request to a stronger model before anything ran.
STATUSES = frozenset({"completed", "failed", "interrupted", "cancelled", "escalated"})
RUNTIMES = frozenset({"codex", "claude", "unknown"})
MAX_REQUESTS = 256
# Time since the previous model request of the same native session ended:
# the share after a gap above 5 minutes decides whether a 1-hour prompt-cache
# TTL pays (doc/angelo/model_routing.rst). Buckets only, never content.
CACHE_GAPS = ("new_session", "unknown", "under_5m", "5m_to_1h", "over_1h")
MAX_CONSULTATIONS = 4
MAX_FILE_BYTES = 512 * 1024
MAX_MS = 7 * 24 * 60 * 60 * 1000
_LOCK = threading.RLock()
_STORES = {}


def _number(value):
    return (type(value) in {int, float} and 0 <= value <= MAX_MS and math.isfinite(value))


def cache_gap(previous, native_id, now):
    """The CACHE_GAPS bucket for a request. ``previous`` is ``(native_id, monotonic end)``."""
    if not native_id:
        return "new_session"
    if not previous or previous[0] != native_id or not _number((now - previous[1]) * 1000):
        return "unknown"  # For example the first request after Angelo restarted.
    seconds = now - previous[1]
    return "under_5m" if seconds < 300 else "5m_to_1h" if seconds < 3600 else "over_1h"


def model_id(value):
    """A bounded printable model identifier, or None. Never free text or content."""
    return value if type(value) is str and 1 <= len(value) <= 128 and all(32 < ord(c) < 127 for c in value) else None


def clean_route(value):
    """Which connection mode, model, effort and cache TTL a request used (allowlisted)."""
    if not isinstance(value, dict):
        return None
    route = {}
    if value.get("mode") in CODING_MODES:
        route["mode"] = value["mode"]
    requested = value.get("requested_model")
    if requested == "" or model_id(requested):
        route["requested_model"] = requested  # "" is the runtime's own default model.
    if value.get("effort") in EFFORTS:
        route["effort"] = value["effort"]
    if value.get("cache_ttl") in PROMPT_CACHE_TTLS:
        route["cache_ttl"] = value["cache_ttl"]
    if value.get("cache_gap") in CACHE_GAPS:
        route["cache_gap"] = value["cache_gap"]
    # fast; auto: Auto routing chose the model; single_model: a measurement run (refusal fallback off);
    # cache_affinity: a Codex thread keyed by the agent runtime (codex_connect.stamp_route).
    route.update({key: value[key] for key in ("fast", "auto", "single_model", "cache_affinity")
                  if type(value.get(key)) is bool})
    if value.get("agent_runtime") in ("bundled", "override", "own"):
        route["agent_runtime"] = value["agent_runtime"]  # Which Codex ran it (agent_runtime.kind).
    if type(value.get("escalation")) is int and 0 <= value["escalation"] <= 3:
        route["escalation"] = value["escalation"]  # 1: Auto's re-run of the request on a stronger model.
    served = value.get("served_models")
    if isinstance(served, list):
        served = [model for model in served[:MAX_SERVED_MODELS] if model_id(model)]
        if served:
            route["served_models"] = served
    return route or None


def _clean_usage(value):
    cleaned = {key: _usage_value(key, number) for key, number in (value.items() if isinstance(value, dict) else ())}
    usage = {key: number for key, number in cleaned.items() if number is not None}
    return usage if "turns" in usage and len(usage) > 1 else None


def _usage_value(key, value):
    """A bounded usage number for an allowlisted field, else None."""
    if key in USAGE_TOKENS:
        return value if type(value) is int and 0 <= value <= MAX_TOKENS else None
    if key in USAGE_TIMES:
        return round(value, 3) if _number(value) else None
    if key == "cost_usd" and type(value) in {int, float} and math.isfinite(value) and 0 <= value <= MAX_COST_USD:
        return round(value, 6)
    if key in ("turns", "interrupted_turns", "requests") and type(value) is int and 1 <= value <= 1000000:
        return value
    return None


def clean_receipt(value):
    """Positive numeric allowlist; unknown strings cannot become metrics data."""
    if not isinstance(value, dict):
        return None
    output = {"status": value.get("status") if type(value.get("status")) is str and value.get("status") in STATUSES else "failed",
              "runtime": value.get("runtime") if type(value.get("runtime")) is str and value.get("runtime") in RUNTIMES else "unknown",
              "runtime_start": "warm" if value.get("runtime_start") == "warm" else "cold",
              "phases": {}, "counts": {}, **({"kind": "warm_up"} if value.get("kind") == "warm_up" else {})}
    session = value.get("session_id")
    if isinstance(session, str):
        try:
            output["session_id"] = str(uuid.UUID(session))
        except ValueError:
            pass
    for key, number in (value.get("phases") or {}).items() if isinstance(value.get("phases"), dict) else ():
        if key in PHASES and _number(number):
            output["phases"][key] = round(number, 3)
    for key, number in (value.get("counts") or {}).items() if isinstance(value.get("counts"), dict) else ():
        if key in COUNTS and type(number) is int and 0 <= number <= 1000000:
            output["counts"][key] = number
    usage = _clean_usage(value.get("usage"))
    if usage:
        output["usage"] = usage
    route = clean_route(value.get("route"))
    if route:
        output["route"] = route
    path = clean_path(value.get("path"))
    if path:
        output["path"] = path
    consultations = []
    for entry in value.get("consultations", [])[:MAX_CONSULTATIONS] if isinstance(value.get("consultations"), list) else ():
        cleaned = clean_route(entry) or {}
        usage = _clean_usage(entry.get("usage")) if isinstance(entry, dict) else None
        if usage:
            cleaned["usage"] = usage
        if cleaned:
            consultations.append(cleaned)
    if consultations:
        output["consultations"] = consultations
    return output if "total_ms" in output["phases"] else None


class RequestMetrics:
    """One request, with immutable numeric receipts once finish() is called."""

    def __init__(self, runtime="unknown", warm=False, clock=None, session_id=None):
        self.clock = clock or time.monotonic
        self.started = self.clock()
        self.runtime = runtime if runtime in RUNTIMES else "unknown"
        self.warm = bool(warm)
        self.session_id = session_id
        self.phases = {}
        # Present-but-zero marks fallback counting as measured for this request.
        self.counts = {"python_fallbacks": 0}
        self.usage, self.usage_reports = {}, {}
        self.interrupted_turns = 0  # Provider turns the host cut short (Stop, a timeout).
        self.path = PathFacts()  # How the request got to its result (request_path), recorded by its context.
        self.route = {}
        self.served_models = []
        # Read-only collaborator consultations of this request, by call ID.
        self.consultations = {}
        self.finished = False
        self.lock = threading.RLock()

    def count(self, name, amount=1):
        if name not in COUNTS or type(amount) is not int or amount < 0:
            return
        with self.lock:
            if not self.finished:
                self.counts[name] = min(1000000, self.counts.get(name, 0) + amount)

    def count_fallback(self, categories):
        """One admitted reviewed script, counted once in each fixed category."""
        with self.lock:
            if self.finished:
                return
            self.count("python_fallbacks")
            for category in set(categories) & set(FALLBACK_CATEGORIES):
                self.count("python_fallback_" + category)

    def add(self, name, milliseconds):
        if name not in PHASES or name in PEAK_PHASES or not _number(milliseconds):
            return
        with self.lock:
            if not self.finished:
                self.phases[name] = min(MAX_MS, self.phases.get(name, 0) + milliseconds)

    def peak(self, name, milliseconds):
        """Keep the longest single sample, e.g. one contiguous main-thread block."""
        if name not in PEAK_PHASES or not _number(milliseconds):
            return
        with self.lock:
            if not self.finished:
                self.phases[name] = max(self.phases.get(name, 0), milliseconds)

    @staticmethod
    def _add_usage(totals, reports, served, usage):
        for key, value in usage.items() if isinstance(usage, dict) else ():
            if key in USAGE_SUMS and _usage_value(key, value) is not None:
                totals[key] = totals.get(key, 0) + value
                reports[key] = reports.get(key, 0) + 1
        models = usage.get("models") if isinstance(usage, dict) else None
        for model in models if isinstance(models, list) else ():
            if model_id(model) and model not in served and len(served) < MAX_SERVED_MODELS:
                served.append(model)

    def set_route(self, **route):
        """The request's connection mode, requested model, effort, Fast, cache TTL and gap."""
        with self.lock:
            if not self.finished:
                self.route.update({key: value for key, value in route.items() if value is not None})

    def provider_usage(self, usage, interrupted=False):
        """One provider turn's usage; None or a missing field is unmeasured. ``interrupted``: the host cut it short."""
        with self.lock:
            if not self.finished:
                self.interrupted_turns += bool(interrupted)
                self._add_usage(self.usage, self.usage_reports, self.served_models, usage)

    def consultation_usage(self, key, route, usage):
        """One provider turn of a read-only consultation, attributed to this request.

        Each call counts one turn; a field is reported only if every turn of that
        consultation reported it. At most MAX_CONSULTATIONS are kept.
        """
        with self.lock:
            if self.finished:
                return
            entry = self.consultations.get(key)
            if entry is None:
                if len(self.consultations) >= MAX_CONSULTATIONS:
                    return
                entry = self.consultations[key] = {"route": dict(route), "usage": {}, "reports": {},
                                                   "served": [], "turns": 0}
            entry["turns"] += 1
            self._add_usage(entry["usage"], entry["reports"], entry["served"], usage)

    @staticmethod
    def _complete(totals, reports, turns, interrupted=0):
        # A field is a total only if every submitted provider turn reported it;
        # partial sums would understate cost and cache misses. A turn the host
        # cut short reports what it can: ``interrupted_turns`` then marks every
        # field as a lower bound instead of hiding what the other turns used.
        usage = {key: value for key, value in totals.items() if turns and reports.get(key, 0) >= turns - interrupted}
        usage = dict(usage, turns=turns) if usage or (turns and interrupted) else None
        return dict(usage, interrupted_turns=interrupted) if usage and interrupted else usage

    def elapsed(self, name, started):
        self.add(name, max(0, self.clock() - started) * 1000)

    def mark(self, name):
        if name not in PHASES:
            return
        with self.lock:
            if not self.finished and name not in self.phases:
                value = max(0, self.clock() - self.started) * 1000
                if _number(value):
                    self.phases[name] = value

    def snapshot(self):
        with self.lock:
            snapshot = {"runtime": self.runtime, "runtime_start": "warm" if self.warm else "cold",
                        "session_id": self.session_id,
                        "phases": {key: round(value, 3) for key, value in self.phases.items()},
                        "counts": dict(self.counts)}
            usage = self._complete(self.usage, self.usage_reports, self.counts.get("provider_turn_requests", 0),
                                   self.interrupted_turns)
            if usage:
                snapshot["usage"] = usage
            if self.route or self.served_models:
                snapshot["route"] = dict(self.route, **({"served_models": list(self.served_models)}
                                                        if self.served_models else {}))
            path = self.path.snapshot()
            if path:
                snapshot["path"] = path
            consultations = []
            for entry in self.consultations.values():
                item = dict(entry["route"], **({"served_models": list(entry["served"])} if entry["served"] else {}))
                usage = self._complete(entry["usage"], entry["reports"], entry["turns"])
                if usage:
                    item["usage"] = usage
                consultations.append(item)
            if consultations:
                snapshot["consultations"] = consultations
            return snapshot

    def finish(self, status):
        with self.lock:
            if self.finished:
                return None
            self.mark("total_ms")
            self.finished = True
            return clean_receipt(dict(self.snapshot(), status=status))


def _percentile(values, percentage):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * percentage
    lower, upper = math.floor(position), math.ceil(position)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 3)


def _usage_report(completed):
    """Per-field request totals, over completed requests that measured each field."""
    fields = {}
    for name in USAGE_FIELDS:
        values = [record["usage"][name] for record in completed if name in record.get("usage", {})]
        if values:
            fields[name] = {"samples": len(values), "p50": _percentile(values, .5),
                            "p95": _percentile(values, .95), "total": round(sum(values), 6)}
    prompts = [record["usage"] for record in completed
               if {"input_tokens", "cached_input_tokens"} <= record.get("usage", {}).keys()]
    prompt_tokens = sum(usage["input_tokens"] for usage in prompts)
    return {"fields": fields, "unmeasured_fields": [name for name in USAGE_FIELDS if name not in fields],
            "requests_with_provider_turns": sum(record["counts"].get("provider_turn_requests", 0) > 0
                                                for record in completed),
            "cached_input_share": (round(sum(usage["cached_input_tokens"] for usage in prompts) / prompt_tokens, 4)
                                   if prompt_tokens else None)}


def _route_report(records):
    """Requests per (mode, requested model, effort, cache TTL), and their cache gaps."""
    routes, gaps = {}, {}
    for record in records:
        route = record.get("route") or {}
        key = (route.get("mode", "unknown"), route.get("requested_model", "unknown"),
               route.get("effort", "unknown"), route.get("cache_ttl", "runtime default"))
        entry = routes.setdefault(key, {"requests": 0, "completed": 0, "served_models": []})
        entry["requests"] += 1
        entry["completed"] += record["status"] == "completed"
        for model in route.get("served_models", ()):
            if model not in entry["served_models"]:
                entry["served_models"].append(model)
        if "cache_gap" in route:
            by_ttl = gaps.setdefault(route.get("cache_ttl", "runtime default"), dict.fromkeys(CACHE_GAPS, 0))
            by_ttl[route["cache_gap"]] += 1
    rows = [dict(zip(("mode", "requested_model", "effort", "cache_ttl"), key), **value)
            for key, value in sorted(routes.items())]
    return rows, gaps


def _consultation_report(records):
    """Read-only consultations attributed to requests, with their measured usage totals."""
    entries = [entry for record in records for entry in record.get("consultations", ())]
    totals = {}
    for name in USAGE_FIELDS:
        values = [entry["usage"][name] for entry in entries if name in entry.get("usage", {})]
        if values:
            totals[name] = {"samples": len(values), "total": round(sum(values), 6)}
    models = {}
    for entry in entries:
        name = entry.get("requested_model") or "runtime default"
        models[name] = models.get(name, 0) + 1
    return {"requests_with_consultations": sum(bool(record.get("consultations")) for record in records),
            "consultations": len(entries), "by_requested_model": models, "usage": totals,
            "unmeasured": sum("usage" not in entry for entry in entries)}


def _fallback_report(records):
    """Fixed-category script counts over receipts that measured fallbacks."""
    measured = [record["counts"] for record in records if "python_fallbacks" in record["counts"]]
    categories = {name: sum(counts.get("python_fallback_" + name, 0) for counts in measured)
                  for name in FALLBACK_CATEGORIES}
    return {"samples": len(measured), "scripts": sum(counts["python_fallbacks"] for counts in measured),
            "categories": {name: total for name, total in categories.items() if total}}


class TelemetryStore:
    """One private bounded file/writer per application directory, shared by chats.

    A caller can supply the application's serialized memory worker as ``submit``;
    it must schedule a callable without running it on the Blender UI thread.
    The fallback is one daemon writer with a single coalesced dirty signal.
    report() returns an independent read-only snapshot and never schedules I/O.
    """

    def __init__(self, directory, submit=None):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "performance.json"
        self.receipts = deque(maxlen=MAX_REQUESTS)
        self.lock = threading.RLock()
        self.submit = submit
        self._signals = queue.Queue(maxsize=1)
        self._write_queued = False
        self._revision = 0
        self._persisted = 0
        self._worker = None
        self._live = {}  # Session id -> the RequestMetrics of its request in progress.
        self._load()

    @classmethod
    def shared(cls, directory, submit=None):
        key = str(Path(directory).resolve())
        with _LOCK:
            if key not in _STORES:
                _STORES[key] = cls(directory, submit)
            store = _STORES[key]
            if submit is not None:
                store.submit = submit
            return store

    def _load(self):
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                return
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                raw = json.loads(handle.read(MAX_FILE_BYTES + 1))
            if not isinstance(raw, dict) or raw.get("version") != 1:
                return
            records = raw.get("requests", [])
            if not isinstance(records, list):
                return
            for value in records[-MAX_REQUESTS:]:
                cleaned = clean_receipt(value)
                if cleaned:
                    self.receipts.append(cleaned)
        except (OSError, ValueError, TypeError, RecursionError):
            pass

    def begin(self, metrics):
        """Register a request in progress (returned), so its running usage can be read."""
        with self.lock:
            self._live[metrics.session_id] = metrics
        return metrics

    def running_usage(self, session_id):
        """Read-only: the usage so far of that chat's request in progress, or None (request_path.running_usage)."""
        with self.lock:
            metrics = self._live.get(session_id)
            if metrics is not None and metrics.finished:
                del self._live[session_id]  # Its receipt is the record now.
                metrics = None
        return None if metrics is None else running_usage(metrics)

    def record(self, receipt):
        value = clean_receipt(receipt)
        if value is None:
            return False
        with self.lock:
            self.receipts.append(value)
            self._revision += 1
            if self._write_queued:
                return True
            self._write_queued = True
        if self.submit is not None:
            try:
                if self.submit(self._drain) is not False:
                    return True
            except Exception:
                # Storage failure must never interrupt the user's scene work.
                pass
        with self.lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._writer, daemon=True, name="AngeloPerformanceWriter")
                try:
                    self._worker.start()
                except RuntimeError:
                    self._worker = None
                    self._write_queued = False
                    return True  # Keep bounded in-memory receipts; never stop scene work for metrics.
        try:
            self._signals.put_nowait(True)
        except queue.Full:
            pass
        return True

    def _writer(self):
        while True:
            try:
                self._signals.get(timeout=1)
            except queue.Empty:
                with self.lock:
                    if not self._write_queued:
                        self._worker = None
                        return
                continue
            self._drain()

    def _drain(self):
        # Coalesce writes while keeping receipt updates race-free. Serialized
        # workers may already contain another request's memory persistence.
        while True:
            with self.lock:
                revision = self._revision
                records = list(self.receipts)
            temporary = self.path.with_name(".performance-" + str(uuid.uuid4()) + ".tmp")
            try:
                encoded = json.dumps({"version": 1, "requests": records}, ensure_ascii=False, allow_nan=False)
                while len(encoded.encode("utf-8")) > MAX_FILE_BYTES and records:
                    # The budget is fixed; receipts are not all one size. The file keeps the newest that fit.
                    records = records[max(1, len(records) // 8):]
                    encoded = json.dumps({"version": 1, "requests": records}, ensure_ascii=False, allow_nan=False)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(temporary, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            except (OSError, ValueError, TypeError):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                with self.lock:
                    self._write_queued = False
                return
            with self.lock:
                self._persisted = revision
                if revision == self._revision:
                    self._write_queued = False
                    return

    def flush(self, timeout=2):
        """Testing/shutdown synchronization only; never used by the UI pump."""
        with self.lock:
            needs_writer = (self._write_queued and self.submit is None
                            and (self._worker is None or not self._worker.is_alive()))
        if needs_writer:
            self._drain()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if not self._write_queued:
                    return self._persisted == self._revision
            time.sleep(.005)
        return False

    def report(self, session_id=None, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Performance report limit must be 1-100")
        with self.lock:  # A receipt with a "kind" ("warm_up": codex_bases.report_warm_up) is no chat's request.
            available = [value for value in self.receipts if "kind" not in value and session_id in (None, value.get("session_id"))]
            records = available[-limit:]
        completed = [record for record in records if record["status"] == "completed"]
        phases = {}
        for name in sorted(PHASES):
            values = [record["phases"][name] for record in completed if name in record["phases"]]
            if values:
                phases[name] = {"samples": len(values), "p50": _percentile(values, .5), "p95": _percentile(values, .95)}
        runtime_start, total_by_start = {}, {}
        for temperature in ("cold", "warm"):
            for phase, target in (("runtime_start_ms", runtime_start), ("total_ms", total_by_start)):
                values = [record["phases"][phase] for record in completed
                          if record["runtime_start"] == temperature and phase in record["phases"]]
                if values:
                    target[temperature] = {"samples": len(values), "p50": _percentile(values, .5),
                                           "p95": _percentile(values, .95)}
        routes, cache_gaps = _route_report(records)
        externally_instrumented = {"model_rounds", "provider_turn_requests"}
        counts = {name: sum(record["counts"].get(name, 0) for record in records)
                  for name in sorted(COUNTS - FALLBACK_COUNTS - RECEIPT_ONLY_COUNTS)
                  if name not in externally_instrumented or any(name in record["counts"] for record in records)}
        return {"version": 1, "local_only": True, "retained_requests": len(records),
                "available_requests": len(available), "limit": limit,
                "completed_requests": len(completed), "max_retained_requests": MAX_REQUESTS,
                "phases": phases, "runtime_start": runtime_start, "total_by_runtime_start": total_by_start,
                "counts": counts, "unmeasured_counts": sorted(externally_instrumented - counts.keys()),
                "count_samples": {name: sum(name in record["counts"] for record in records) for name in sorted(externally_instrumented)},
                "provider_usage": _usage_report(completed), "python_fallbacks": _fallback_report(records),
                "routes": routes, "cache_gaps": cache_gaps, "consultations": _consultation_report(records),
                "count_scopes": {"model_rounds": "Claude host-visible typed-plan rounds only; Codex internal model rounds are unavailable",
                                 "provider_turn_requests": "Host-visible provider requests, not underlying model calls",
                                 "python_fallbacks": "Admitted reviewed-Python invocations; categories are host-derived from referenced Blender API names, not the model's intent, and a script counts once in each category it touches"},
                "phase_scopes": {"first_scene_change_ms": "Latency to the first fresh positive live-scene outcome receipt, not an inferred kernel write instant",
                                 "first_verified_result_ms": "Latency to the first meaningful current scene-mutation read-back receipt; read-only, preview and historical replay are excluded",
                                 "main_thread_block_ms": "Longest single main-thread section this chat spent on its events, tools, approvals, commits or end-of-request saves during the request; drawing is excluded"},
                "usage_scopes": {"input_tokens": "All prompt tokens including cache reads and writes",
                                 "cached_input_tokens": "Prompt tokens served from the provider cache; third-party models routed through Codex may report zero when unknown",
                                 "turn_ms": "Runtime-reported turn time; Codex turns include host tools and approvals, Claude typed-plan turns do not",
                                 "api_ms": "Claude API time including network, queueing and retries; not pure inference",
                                 "cost_usd": "Claude Code's estimate, not a billing statement",
                                 "routes": "Requests per connection mode, requested model ('' is the runtime default), effort sent and prompt-cache TTL; served_models are the models the runtime reported",
                                 "cache_gaps": "Requests per time since the same native session's previous model request ended, by TTL; counts only",
                                 "consultations": "Read-only collaborator consultations, attributed to the request that asked for them; not included in provider_usage"},
                "notes": "p50/p95 use completed requests only. Missing phases/counts/usage are unmeasured; execution/commit may include integrated verification. Approval wait is separate from machine phases, but included in total. Counts cover all selected requests; retries with new IDs are not inferable. Provider usage covers a request only when every provider turn reported it; collaborator consultations are reported separately under consultations."}
