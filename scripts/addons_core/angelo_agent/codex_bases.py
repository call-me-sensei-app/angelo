# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Warm base threads of a shared Codex app-server (``codex_runtime.Runtime``): warmed, resumed, forked, remembered.

A base is an ordinary persisted Codex thread whose one turn is the neutral
``WARM_UP`` exchange; each new chat of its variant is an ephemeral
prediction-mode fork of it, which sends the base's thread ID as the prompt
cache key (doc/angelo/agent_integration.rst, "Shared Codex app-server and
forked openings").

* A warm-up becomes a base only when its turn completed with exactly one agent
  message, which is exactly the empty plan it asks for (no duplicate key, no
  other key or value), and nothing else: no tool call, web search or any other
  item (``clean``). Otherwise it is discarded and never remembered. Its tools cannot
  be withheld, since they are part of the opening its forks share; a call it
  makes is refused and its turn interrupted (``Runtime._request``).
* ``BaseStore`` remembers a clean base's thread ID and token total (never
  content) per scope (connection, app-server command, profile, mode and Codex
  build) and variant, the newest ``MAX_ENTRIES``. A new app-server resumes it
  with no model request instead of warming another. A file that cannot be read
  counts as empty. Every warm-up turn sent leaves a receipt (``kind``
  ``warm_up``) with the chats' performance receipts, so its billed usage is
  counted (``report_warm_up``).
* A warm-up that was sent but made no base is remembered too, as a failure of
  its scope and variant. A verdict on the variant (an unclean reply, or any
  other item or a tool call: ``polluted``) keeps every launch from warming it
  again for ``FAILURE_BACKOFF_SECONDS``; a failed turn with no verdict (a
  stream error, a 5xx, a usage limit) for ``TRANSIENT_BACKOFF_SECONDS``. A new
  Codex build, model or variant is a new key. A process sends at most one
  warm-up of a variant that then fails.
  A remembered base another app-server holds (a second Angelo on the same
  profile: "active writer") is never warmed over at connect, and a base this
  process warms after a chat's turn then serves only this launch: the other
  instance's remembered base is never replaced.
* A new chat whose base is still being readied waits for it only when it is
  expected within ``PREFILL_SAVING_SECONDS`` (``wait_bound``); otherwise it
  starts its own thread at once and the base serves the next chat.
"""

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .protocol import RpcError

FORK = {"experimentalPredictionMode": True, "ephemeral": True, "excludeTurns": True}
WARM_UP = ("This message only opens the session; nothing has been asked yet. "
           "Answer with an empty reply and no operations.")
MAX_WARM_UPS = 2
WARM_UP_SECONDS = 180
# A chat waits for a base being readied only while that wait can pay for itself
# (doc/angelo/performance.rst, "Eager openings"). A cached opening saved 0.37 s
# (fork against fresh thread, completion, codex_cache_fork_check) and 0.45 s
# (first text after connect, codex_runtime_check) of model time in the billed
# runs; the smaller, rounded, is both the threshold and the longest wait.
PREFILL_SAVING_SECONDS = 0.4
# ``thread/resume`` of a remembered base in a new app-server: 0.56 s; resume and
# fork took a median 0.5 s more than a thread/start, with a slow tail to 4.3 s
# (codex_fork_probe on a loaded machine). A warm-up is a model request (about
# 7 s billed) with no such estimate: only its arrived reply says it is about to
# complete.
RESUME_SECONDS = 0.56
# A variant whose sent warm-up made no base is not warmed again, at connect or after a chat's turn: for a day
# after a verdict on it (an unclean or polluted reply), for 15 minutes after a failed turn with none.
FAILURE_BACKOFF_SECONDS = 24 * 60 * 60
TRANSIENT_BACKOFF_SECONDS = 15 * 60
HELD = "active writer"  # Codex's refusal to resume a thread another app-server holds.
FILE = "codex_bases.json"
MAX_ENTRIES = 16
_QUIET = frozenset({"userMessage", "agentMessage", "reasoning"})  # The only items a clean warm-up has.
_THREAD = re.compile(r"^[0-9A-Za-z-]{1,128}$")
_TOTAL_KEYS = ("inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens",
               "reasoningOutputTokens", "totalTokens")


def supports_forks(rpc):
    """Whether this app-server validates prediction-mode forks (no thread is created, nothing is billed)."""
    try:
        rpc.request("thread/fork", {"threadId": str(uuid.uuid4()), "experimentalPredictionMode": True,
                                    "excludeTurns": True}, timeout=10)
    except RpcError as error:
        return "experimentalPredictionMode" in str(error)
    return False


def _unique(pairs):
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")  # json.loads would keep only the last, hiding the first.
    return dict(pairs)


def empty_plan(text):
    """Whether a typed plan's text is exactly the empty reply with no operations ``WARM_UP`` asks for."""
    try:
        plan = json.loads(text, object_pairs_hook=_unique)
    except (TypeError, ValueError):
        return False
    return plan == {"reply": "", "operations": []}


def observe(record, method, params):
    """A warm-up turn's own events: its usage (the forks' baseline), its items and its completion."""
    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
        previous, total = record.get("total"), usage.get("total")
        if isinstance(total, dict) and not (isinstance(previous, dict) and total.get("totalTokens") == previous.get(
                "totalTokens")):
            record["requests"] = record.get("requests", 0) + 1  # Codex reports once per model response.
        record["total"], record["usage"] = total, usage.get("last")
    elif method in ("item/started", "item/completed"):
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if item.get("type") not in _QUIET:
            record["polluted"] = True
        elif method == "item/completed" and item.get("type") == "agentMessage":
            record["messages"], record["reply"] = record.get("messages", 0) + 1, item.get("text")
    elif method == "turn/completed":
        record["status"] = (params.get("turn") or {}).get("status")
        record["done"].set()


def clean(record):
    """A completed warm-up whose one agent message is the exact empty plan, and nothing else: a base to share."""
    return (record.get("status") == "completed" and not record.get("polluted") and record.get("messages") == 1
            and empty_plan(record.get("reply")))


def wait_bound(record, now):
    """Seconds a new chat may wait for this base being readied: ``PREFILL_SAVING_SECONDS`` or 0.

    It waits only when the base is expected within that saving: a resume
    within ``PREFILL_SAVING_SECONDS`` of its ``RESUME_SECONDS`` (one later than
    that is in the slow tail), or a warm-up whose reply or usage arrived (its
    turn is about to complete). A warm-up still waiting for the model is not
    waited for: that takes seconds.
    """
    phase, since = record.get("phase"), record.get("since")  # A resume's since: when thread/resume was sent.
    if record.get("state") != "warming":
        return 0
    if phase == "warm-up":
        answered = record.get("messages") or record.get("usage") is not None
        return PREFILL_SAVING_SECONDS if answered else 0
    if phase == "resume" and since is not None:
        remaining = RESUME_SECONDS - (now - since)
        return PREFILL_SAVING_SECONDS if abs(remaining) <= PREFILL_SAVING_SECONDS else 0
    return 0


def fork(runtime, variant):
    """``(thread/fork result, the base's token total)`` from this variant's base, or None.

    A base still being readied is waited for at most ``wait_bound``; one not
    ready by then leaves the chat to start its own thread.
    """
    with runtime.lock:
        base = runtime.bases.get(variant)
        if runtime.dead or runtime.forks is False or base is None:
            return None
        bound = 0 if base["state"] == "ready" else wait_bound(base, time.monotonic())
        if base["state"] != "ready" and not bound:
            return None
    if bound and not base["settled"].wait(bound):
        return None
    with runtime.lock:
        if runtime.dead or not runtime.forks or base["state"] != "ready":
            return None
    try:
        result = runtime.rpc.request("thread/fork", dict(FORK, threadId=base["thread"]))
        identity = (result.get("thread") or {}).get("id")
        if not isinstance(identity, str) or not identity:
            raise RpcError("Codex returned no forked thread")
    except RpcError:
        with runtime.lock:
            base["state"] = "failed"  # The next chat resumes it again or warms another (MAX_WARM_UPS).
        return None
    return result, base.get("total")


def warm(runtime, variant, parameters, turn_options, stored_only=False, at_connect=False):
    """Ready a base for ``variant`` in the background: resume the remembered one, else warm one.

    ``stored_only`` (a chat just started its thread) only resumes, which needs no
    model request. Never while a base is ready or warming, on a retired or dead
    process, nor after the variant used its ``MAX_WARM_UPS``. A warm-up is also
    refused while the variant's last sent warm-up failed (``FAILURE_BACKOFF_SECONDS``)
    and, ``at_connect`` (``codex_connect.prepare_opening``), while another
    app-server holds its remembered base. Never on a process with cache-affinity keys
    (``Runtime.cache_affinity``): a keyed new thread reads the cached opening with no base,
    warm-up or fork.
    """
    if getattr(runtime, "cache_affinity", False):
        return False
    resumable = _remembered(runtime, variant) is not None  # Read before the lock: one small file.
    with runtime.lock:
        base = runtime.bases.get(variant)
        attempts = base["attempts"] if base else 0
        if (runtime.dead or runtime.retired or runtime.forks is False or attempts >= MAX_WARM_UPS
                or (base and (base["state"] in ("warming", "ready") or stored_only and base["state"] == "absent"))):
            return False
        # ``phase`` and ``since`` are what a chat arriving meanwhile weighs (wait_bound); a resume's ``since``
        # is set when its thread/resume is sent, after the capability check.
        runtime.bases[variant] = record = {"state": "warming", "attempts": attempts, "thread": None,
                                           "total": None, "usage": None, "done": threading.Event(),
                                           "settled": threading.Event(), "since": None,
                                           "phase": "resume" if resumable else None if stored_only else "warm-up"}
    threading.Thread(target=_warm, args=(runtime, variant, record, parameters, turn_options, stored_only, at_connect),
                     daemon=True, name="AngeloCodexWarmUp").start()
    return True


def _remembered(runtime, variant):
    """This variant's remembered base that this process may still resume, or None."""
    if runtime.store is None or not runtime.scope or runtime.resumes.get(variant, 0) >= MAX_WARM_UPS:
        return None
    return runtime.store.get(runtime.scope, variant)


def _resume(runtime, variant, record, parameters):
    """Resume this variant's remembered base (again after a failed fork); whether it is ready.

    A base another app-server holds marks the variant held (``Runtime.held``):
    this process neither warms over it at connect nor remembers its own.
    """
    stored = _remembered(runtime, variant)
    if stored is None:
        return False
    resume = {key: value for key, value in parameters.items() if key != "dynamicTools"}
    with runtime.lock:
        runtime.resumes[variant] = runtime.resumes.get(variant, 0) + 1
        record.update(phase="resume", since=time.monotonic())
    try:
        runtime.rpc.request("thread/resume", dict(resume, threadId=stored["thread"], excludeTurns=True))
    except RpcError as error:  # Another launch holds it, or Codex no longer has it: warm a new one.
        with runtime.lock:
            runtime.resumes[variant] = MAX_WARM_UPS
            if HELD in str(error):
                runtime.held.add(variant)
        return False
    with runtime.lock:
        record.update(thread=stored["thread"], total=stored["total"], state="ready")
        runtime.loaded.add(stored["thread"])
    return True


def _refused(runtime, variant, at_connect):
    """Why this process must not warm ``variant`` now, or "" (``warm``)."""
    if at_connect and variant in runtime.held:
        return "held"
    if runtime.store is not None and runtime.scope and runtime.store.failed_recently(runtime.scope, variant):
        return "backoff"
    return ""


def _warm(runtime, variant, record, parameters, turn_options, stored_only, at_connect=False):
    sent, thread, turn, answered = None, None, None, False  # sent: turn/start written; it may be billed from then.
    try:
        if runtime.forks is None:
            runtime.forks = supports_forks(runtime.rpc)
        if not runtime.forks:
            record["state"] = "unsupported"
            return
        if _resume(runtime, variant, record, parameters):
            return
        record["reason"] = "stored only" if stored_only else _refused(runtime, variant, at_connect)
        if record["reason"]:
            record["state"] = "absent"
            return
        with runtime.lock:  # What a chat arriving meanwhile weighs (wait_bound), changed together.
            record["attempts"] += 1
            record.update(phase="warm-up", since=time.monotonic())
        thread = runtime.rpc.request("thread/start", parameters)["thread"]["id"]
        with runtime.lock:
            record["thread"] = thread
            runtime.warming[thread] = record
            runtime.loaded.add(thread)
        # The default tier: Fast never bills a warm-up (the cached opening is the same).
        request = dict(turn_options, threadId=thread, summary="auto", serviceTierForTurn="default",
                       input=[{"type": "text", "text": WARM_UP}])
        with runtime.lock:  # Checked last, just before the write: a login change sends no request.
            if runtime.retired or runtime.dead:
                raise RpcError("This Codex app-server was retired")
            sent = time.monotonic()
        turn = ((runtime.rpc.request("turn/start", request) or {}).get("turn") or {}).get("id")
        answered = True
        with runtime.lock:
            raced = runtime.retired  # Retired while the request was written: end it at once.
        completed = not raced and record["done"].wait(WARM_UP_SECONDS)
        if not completed and not record["done"].is_set():
            _interrupt(runtime, thread, turn, record)  # A timeout or a retirement: no turn is left running.
        record["state"] = "ready" if completed and clean(record) else "failed"
        record["settled"].set()  # A chat waiting for it (wait_bound) need not wait for the file.
        if record["state"] == "ready" and runtime.store is not None and runtime.scope:
            if variant not in runtime.held:  # Never replaces the base another instance remembered and holds.
                runtime.store.put(runtime.scope, variant, thread, record["total"])
    except Exception as error:  # A failed warm-up only means the next chats start their own threads.
        record["state"] = "failed"
        if sent is not None and not answered and isinstance(error, RpcError) and not _unanswered(error):
            sent = None  # Codex refused the turn itself: nothing was sent to the model.
    finally:
        with runtime.lock:
            runtime.warming.pop(record.get("thread"), None)
        record["settled"].set()
        if sent is not None:
            _remember_failure(runtime, variant, record)
            report_warm_up(runtime, record, turn_options, sent, answered=answered)


def _remember_failure(runtime, variant, record):
    """A sent warm-up that made no base: not paid for again in this process, nor by the next launches for a while.

    A verdict (an unclean reply, or a polluted turn: a tool call or any other
    item, which Angelo refuses and interrupts) backs off for a day; a failed
    turn without one for ``TRANSIENT_BACKOFF_SECONDS``. A quit, a timeout or a
    retirement is no verdict and leaves no failure for the next launches.
    """
    if record["state"] == "ready":
        return
    with runtime.lock:
        record["attempts"] = MAX_WARM_UPS  # At most one billed warm-up of a failing variant per launch.
    verdict = record.get("polluted") or (record.get("status") == "completed" and not clean(record))
    seconds = FAILURE_BACKOFF_SECONDS if verdict else TRANSIENT_BACKOFF_SECONDS if record.get(
        "status") == "failed" else None
    if seconds and runtime.store is not None and runtime.scope:
        runtime.store.put_failure(runtime.scope, variant, seconds)


def _unanswered(error):
    """A turn/start whose reply never came (the process ended): it may have reached the model."""
    return any(text in str(error) for text in ("exited", "disconnected"))


def _interrupt(runtime, thread, turn, record):
    """End a warm-up turn that outlived its wait; what it reported so far stays in its receipt."""
    record["status"] = record.get("status") or "interrupted"
    if turn and not runtime.dead:
        try:
            runtime.rpc.request("turn/interrupt", {"threadId": thread, "turnId": turn}, timeout=10)
        except Exception:  # A closed transport or a turn already ended.
            pass


def report_warm_up(runtime, record, turn_options, sent, answered=True):
    """A sent warm-up's receipt with the chats' (``kind`` ``warm_up``, no chat): its billed usage is counted.

    The warm-up thread is new, so its running total is the warm-up's usage. A
    total that never arrived (no reply, a quit, a timeout) leaves the usage
    unmeasured, never zero; ``answered`` False: its turn/start reply was lost.
    """
    if runtime.store is None:
        return
    from .provider_usage import codex_total
    status = record.get("status") if record.get("status") in ("completed", "interrupted") else "failed"
    receipt = {"kind": "warm_up", "status": status, "runtime": "codex", "runtime_start": "warm",
               "phases": {"total_ms": (time.monotonic() - sent) * 1000},
               "counts": {"provider_turn_requests": 1},
               "route": {"mode": runtime.mode, "requested_model": turn_options.get("model") or "",
                         "effort": turn_options.get("effort"), "fast": False}}
    usage = codex_total(record.get("total"))
    if usage is not None and answered:
        receipt["usage"] = dict(usage, turns=1, **({"requests": record["requests"]} if record.get("requests") else {}))
    try:
        runtime.store.record_warm_up(receipt)
    except Exception:  # Accounting never stops a chat; the receipt is lost, not invented.
        pass


def _total(value):
    if not isinstance(value, dict):
        return None
    total = {key: value[key] for key in _TOTAL_KEYS if type(value.get(key)) is int and 0 <= value[key] < 10 ** 12}
    return total or None


class BaseStore:
    def __init__(self, directory):
        self.path = Path(directory) / FILE
        self.lock = threading.Lock()

    def _read(self):
        try:
            entries = json.loads(self.path.read_text(encoding="utf-8")).get("bases")
        except (OSError, ValueError, AttributeError):
            return {}
        return {key: entry for key, entry in entries.items()
                if isinstance(entry, dict) and isinstance(entry.get("thread"), str) and _THREAD.match(entry["thread"])
                and type(entry.get("at")) in (int, float)} if isinstance(entries, dict) else {}

    def _failures(self):
        try:
            failures = json.loads(self.path.read_text(encoding="utf-8")).get("failures")
        except (OSError, ValueError, AttributeError):
            return {}
        if not isinstance(failures, dict):
            return {}
        # {key: {"until": time}}; a bare time (an earlier build's) is when a day-long backoff began.
        untils = {key: value + FAILURE_BACKOFF_SECONDS if type(value) in (int, float) else
                  value.get("until") if isinstance(value, dict) else None for key, value in failures.items()}
        return {key: until for key, until in untils.items() if type(until) in (int, float)}

    def get(self, scope, variant):
        """``{"thread", "total"}`` remembered for this scope and variant, or None."""
        with self.lock:
            entry = self._read().get(scope + ":" + variant)
        return {"thread": entry["thread"], "total": _total(entry.get("total"))} if entry else None

    def failed_recently(self, scope, variant):
        """Whether this scope and variant's last sent warm-up made no base and its backoff has not ended."""
        with self.lock:
            until = self._failures().get(scope + ":" + variant)
        return until is not None and 0 < until - time.time() <= FAILURE_BACKOFF_SECONDS

    def put_failure(self, scope, variant, seconds=FAILURE_BACKOFF_SECONDS):
        with self.lock:
            failures = dict(self._failures(), **{scope + ":" + variant: time.time() + seconds})
            kept = sorted(failures.items(), key=lambda item: item[1])[-MAX_ENTRIES:]
            self._write({"version": 1, "bases": self._read(),
                         "failures": {key: {"until": until} for key, until in kept}})

    def record_warm_up(self, receipt):
        """A warm-up's receipt among the state folder's performance receipts (``telemetry``)."""
        from .telemetry import TelemetryStore
        TelemetryStore.shared(self.path.parent).record(receipt)

    def put(self, scope, variant, thread, total):
        if not isinstance(thread, str) or not _THREAD.match(thread):
            return
        with self.lock:
            entries = self._read()
            entries[scope + ":" + variant] = {"thread": thread, "total": _total(total), "at": time.time()}
            kept = dict(sorted(entries.items(), key=lambda item: item[1]["at"])[-MAX_ENTRIES:])
            failures = {key: {"until": until} for key, until in self._failures().items()
                        if key != scope + ":" + variant}
            self._write({"version": 1, "bases": kept, "failures": failures})

    def _write(self, value):
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
            os.replace(temporary, self.path)
        except OSError:
            pass  # Not remembered: the next launch warms this variant again.
        finally:
            temporary.unlink(missing_ok=True)
