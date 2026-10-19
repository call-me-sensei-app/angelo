# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-turn provider usage derived from runtime-reported running totals.

Neither runtime reports usage for one turn, so a turn's usage is the difference
between the running totals around it. A turn is *unmeasured* (``None``) when
its baseline is unknown, no total arrived for it, or a total decreased; it is
never recorded as zero.

Normalized fields, so both runtimes can be compared:

* ``input_tokens`` counts all prompt tokens, including cache reads and writes;
  ``cached_input_tokens`` and ``cache_write_input_tokens`` are subsets of it.
* ``reasoning_output_tokens`` is a subset of ``output_tokens``.
* ``turn_ms`` is the runtime's own turn duration. Codex turns include host tool
  calls and approvals; Claude typed-plan turns exclude host plan execution.
* ``api_ms`` (Claude only) is provider API time, including network, queueing and
  retries; it is not pure inference time. ``cost_usd`` is Claude Code's
  estimate, not a billing statement.
"""

import math
import threading

TOKENS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens")
MAX_TOKENS = 10 ** 12
MAX_COST_USD = 10 ** 6
# A turn's usage may also carry ``models``: the model IDs that served it, when
# the runtime reports them (Claude Code per model, Codex the turn's model).
MAX_SERVED_MODELS = 8
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
              "reasoning_output_tokens", "requests", "cost_usd")


def _tokens(value):
    return value if type(value) is int and 0 <= value <= MAX_TOKENS else None


def _milliseconds(value):
    return value if type(value) in {int, float} and math.isfinite(value) and 0 <= value <= MAX_TOKENS else None


def codex_total(breakdown):
    """Normalize a Codex app-server ``TokenUsageBreakdown`` running total.

    In Codex 0.160.0 ``cachedInputTokens`` is part of ``inputTokens``, and
    ``cacheWriteInputTokens`` defaults to zero when omitted.
    """
    if not isinstance(breakdown, dict):
        return None
    total = {"input_tokens": _tokens(breakdown.get("inputTokens")),
             "cached_input_tokens": _tokens(breakdown.get("cachedInputTokens")),
             "cache_write_input_tokens": _tokens(breakdown.get("cacheWriteInputTokens", 0)),
             "output_tokens": _tokens(breakdown.get("outputTokens")),
             "reasoning_output_tokens": _tokens(breakdown.get("reasoningOutputTokens"))}
    return None if None in total.values() else total


def claude_total(result):
    """Normalize a Claude Code result's process-cumulative usage.

    Claude Code 2.1.258 documents ``modelUsage`` as the field for token/cost
    accounting: per-model running totals across the turns of one streaming
    process. ``total_cost_usd`` and ``duration_api_ms`` are running totals with
    the same lifecycle. Its ``inputTokens`` excludes cache reads and writes;
    ``thinkingTokens`` is optional and already counted in ``outputTokens``.
    """
    models = result.get("modelUsage") if isinstance(result, dict) else None
    if not isinstance(models, dict):
        return None
    total = dict.fromkeys(TOKENS, 0)
    thinking_reported = True
    for entry in models.values():
        if not isinstance(entry, dict):
            return None
        uncached, read, write, output = (_tokens(entry.get(key)) for key in (
            "inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "outputTokens"))
        if None in (uncached, read, write, output):
            return None
        total["input_tokens"] += uncached + read + write
        total["cached_input_tokens"] += read
        total["cache_write_input_tokens"] += write
        total["output_tokens"] += output
        thinking = _tokens(entry.get("thinkingTokens"))
        if thinking is None:
            thinking_reported = False
        else:
            total["reasoning_output_tokens"] += thinking
    if not thinking_reported:
        del total["reasoning_output_tokens"]
    api = _milliseconds(result.get("duration_api_ms"))
    if api is not None:
        total["api_ms"] = api
    cost = result.get("total_cost_usd")
    if type(cost) in {int, float} and math.isfinite(cost) and 0 <= cost <= MAX_COST_USD:
        total["cost_usd"] = cost
    return total


def claude_model_tokens(result):
    """Running token totals per model of a Claude Code result, or None.

    Claude Code can serve a turn with another model than the one requested (an
    alias's default, or its automatic fallback), so the receipt names the models
    whose totals grew.
    """
    models = result.get("modelUsage") if isinstance(result, dict) else None
    if not isinstance(models, dict) or len(models) > 64:
        return None
    totals = {}
    for model, entry in models.items():
        values = [_tokens(entry.get(key)) for key in ("inputTokens", "cacheReadInputTokens",
                                                       "cacheCreationInputTokens", "outputTokens")] \
            if isinstance(entry, dict) and isinstance(model, str) else [None]
        if None in values:
            return None
        totals[model] = sum(values)
    return totals


def served_models(current, baseline):
    """Models whose running token total grew between two results, in name order."""
    if current is None or baseline is None:
        return None
    return sorted(model for model, total in current.items() if total > baseline.get(model, 0))[:MAX_SERVED_MODELS]


def difference(current, baseline):
    """Usage between two running totals, or None when it cannot be known."""
    if current is None or baseline is None:
        return None
    usage = {}
    for key, value in current.items():
        if key in baseline:
            change = value - baseline[key]
            if change < 0:
                return None
            usage[key] = change
    return usage


class CodexThreadUsage:
    """The provider's current thread total and the baseline of its active turn.

    ``thread/tokenUsage/updated`` carries the thread's running ``total``. A new
    thread starts at zero; a resumed thread's total stays unknown until Codex
    reports it, so the first turn after a resume may be unmeasured.

    Codex reports neither model requests nor rounds. It sends one such update
    after each model response, so a turn's ``requests`` is the number of its
    updates whose total grew: the model responses that reported usage.
    """

    def __init__(self):
        self.thread = None
        self.total = None
        self.updates = 0
        self._turn = None
        # Connection setup and the RPC reader thread can both report a thread.
        self._lock = threading.Lock()

    def _select(self, thread_id):
        if self.thread != thread_id:
            self.thread, self.total, self._turn = thread_id, None, None

    def thread_started(self, thread_id):
        with self._lock:
            self.thread, self.total, self._turn = thread_id, dict.fromkeys(TOKENS, 0), None

    def thread_loaded(self, thread_id, known_total):
        """A thread whose running total is known without a new report (codex_runtime).

        A prediction fork's totals include its base's, so it starts from the
        base's total; a thread still loaded in a shared runtime from its last
        reported total. Unknown (``None``) when Codex never reported one.
        """
        total = codex_total(known_total)
        with self._lock:
            self.thread, self.total, self._turn = thread_id, total, None

    def thread_resumed(self, thread_id):
        # A reloaded thread's total is trusted only once Codex reports it again.
        with self._lock:
            self.thread, self.total, self._turn = thread_id, None, None

    def observe_event(self, method, params, context, *, thread_id, scope, accepted_input):
        """Handle usage/compaction receipts before startup deferral, under the provider's scope lock.

        Return whether this event belongs to receipt handling, even when its
        thread does not match. Only a matching turn can acknowledge input.
        """
        if method == "turn/completed" and isinstance(params, dict) and params.get("threadId") == thread_id:
            step = getattr(context, "record_model_step", None)  # The lead's own request only (request_recovery).
            if callable(step) and scope.matches(context, params, allow_cancelled=True, allow_completed=True):
                step()  # Every tool call of the turn has resolved, whether or not the provider reported usage.
            return False
        if method == "thread/tokenUsage/updated":
            if isinstance(params, dict) and params.get("threadId") == thread_id:
                if scope.matches(context, params):
                    step = getattr(context, "record_model_step", None)  # The lead's own request only.
                    if callable(step):
                        step()  # Sent after every tool call of the response resolved (request_recovery).
                    # A model response's usage acknowledges its inputs. A thread-only
                    # update cannot attribute delivery to this particular turn.
                    last = (params.get("tokenUsage") or {}).get("last", {})
                    if isinstance(last, dict) and type(last.get("outputTokens")) is int and last["outputTokens"] > 0:
                        accepted_input(context)
                self.observe(thread_id, params.get("tokenUsage"), getattr(context, "record_running_usage", None))
                # The last model request's prompt is the thread's context size:
                # a shrink means Codex compacted it (context_ledger.compacted).
                usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
                last, window = usage.get("last"), usage.get("modelContextWindow")
                record, fact = getattr(context, "record_context_tokens", None), getattr(context, "record_context_fact", None)
                if isinstance(last, dict) and type(last.get("inputTokens")) is int and callable(record) and not context.voice:
                    record(last["inputTokens"])
                    if type(window) is int and window > 0 and callable(fact):
                        fact("context_window", window)  # A forked thread is rebased below it (context_rebase).
            return True
        if method == "thread/compacted" or (method == "item/completed" and isinstance(params, dict)
                                            and (params.get("item") or {}).get("type") == "contextCompaction"):
            if isinstance(params, dict) and params.get("threadId") == thread_id:
                record = getattr(context, "record_context_compaction", None)
                if callable(record):
                    record()  # Earlier blocks are summarized away: the next message is complete.
            return True
        return False

    def observe(self, thread_id, token_usage, notify=None):
        """One update. ``notify(usage)`` receives the active turn's usage so far, when it can be known."""
        total = codex_total(token_usage.get("total")) if isinstance(token_usage, dict) else None
        if total is None:
            return
        with self._lock:
            self._select(thread_id)
            grew = self.total is None or any(total[key] > self.total[key] for key in TOKENS)
            if self.total is not None and difference(total, self.total) is None and self._turn is not None:
                self._turn["valid"] = False
            self.total = total
            self.updates += 1
            if self._turn is not None:
                self._turn["requests"] += grew
            running = self._usage(self._turn, total)
        if callable(notify) and running is not None:
            notify(running)

    def begin_turn(self, thread_id):
        with self._lock:
            self._select(thread_id)
            self._turn = {"baseline": None if self.total is None else dict(self.total),
                          "updates": self.updates, "valid": True, "requests": 0}

    @staticmethod
    def _usage(turn, total):
        if turn is None or not turn["valid"]:
            return None
        usage = difference(total, turn["baseline"])
        if usage is not None and turn["requests"]:
            usage["requests"] = turn["requests"]
        return usage

    def finish_turn(self, duration_ms):
        with self._lock:
            turn, self._turn, total, updates = self._turn, None, self.total, self.updates
        if turn is None or updates == turn["updates"]:
            return None
        usage = self._usage(turn, total)
        if usage is not None and type(duration_ms) is int and 0 <= duration_ms <= MAX_TOKENS:
            usage["turn_ms"] = duration_ms
        return usage


class ClaudeProcessUsage:
    """Consecutive results of one Claude Code process.

    A fresh process starts its totals at zero. On ``--resume`` Claude Code
    2.1.258 restores the session's saved ``cost-state`` (``modelUsage``, cost and
    API time) from its transcript, despite its result schema text saying resumed
    sessions start fresh. So a resumed process's first result only establishes
    the baseline, as does the next valid result after any unusable total.
    """

    def __init__(self, *, resumed=False):
        self.baseline = None if resumed else dict(dict.fromkeys(TOKENS, 0), api_ms=0, cost_usd=0)
        self.model_baseline = None if resumed else {}

    def turn(self, result, observed=None):
        """One round's usage. ``observed`` is the round's ``(usage, complete)`` as the API streamed it.

        The running totals give tokens, cost and API time. Where they cannot
        (a resumed process's first result, an unusable total), the API's own
        complete token counts for the round are used instead: tokens only,
        never a cost.
        """
        current, models = claude_total(result), claude_model_tokens(result)
        streamed = dict(observed[0]) if observed and observed[1] and observed[0] else None
        requests = observed[0].get("requests") if observed and isinstance(observed[0], dict) else None
        if self.baseline is None:
            self.baseline, self.model_baseline = current, models
            usage = streamed
        else:
            usage = difference(current, self.baseline)
            served = served_models(models, self.model_baseline) if usage is not None else None
            # A missing or decreasing total may be a zeroed crash result, so it is
            # not trusted as the next baseline either.
            self.baseline = current if usage is not None else None
            self.model_baseline = models if usage is not None else None
            if served:
                usage["models"] = served
            usage = streamed if usage is None else usage
        duration = _milliseconds(result.get("duration_ms")) if isinstance(result, dict) else None
        if usage is not None and duration is not None:
            usage["turn_ms"] = duration
        if usage is not None and requests:
            usage["requests"] = requests  # API messages the round started, as the stream showed them.
        return usage


def record_interrupted(context, usage):
    """Report what a turn the host cut short (Stop, a timeout) is known to have used; None is unmeasured."""
    record = getattr(context, "record_interrupted_usage", None)
    if callable(record):
        record(usage)


def running_usage(metrics):
    """A request's usage so far, or None before any is known: its finished provider turns plus the one in progress.

    Every number is a lower bound while ``running`` is true: a turn in
    progress reports what its runtime has said so far (a Claude round's input
    counts once each message starts and its output when the message ends; a
    Codex turn's thread total after each model response).
    """
    with metrics.lock:
        totals = {key: value for key, value in metrics.usage.items() if key in USAGE_KEYS}
        partial = dict(getattr(metrics.path, "running", None) or {})
        turns, finished = metrics.counts.get("provider_turn_requests", 0), metrics.finished
    for key in USAGE_KEYS:
        value = partial.get(key)
        if type(value) in (int, float) and value >= 0:
            totals[key] = totals.get(key, 0) + value
    if not totals:
        return None
    return dict(totals, turns=turns, running=bool(partial) and not finished)


def record_runtime_round(controller, *, claude=False):
    """Count submitted provider turns, not hidden Codex inference iterations."""
    callback = getattr(controller,'record_provider_turn',None)
    if callable(callback):
        callback()
    if claude:
        callback = getattr(controller,'record_model_round',None)
        if callable(callback):
            callback()


def record_accepted_input(context):
    callback = getattr(context, "record_provider_accepted", None)
    if callable(callback):
        callback()
