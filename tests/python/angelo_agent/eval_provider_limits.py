# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider rate and usage limits in billed harness runs: an invalid trial, never a task failure.

A billed run may share its account with other work. When the account hits
its session, weekly or rate limit, every later call returns in about 0.4 s
with zero tokens and a limit message, and the trials that follow would be
meaningless. ``eval_live_agent.py`` (in-app driver ``eval_live_agent_session.py``)
and ``bench_mcp_vs_angelo.py`` (MCP side ``bench_mcp_trial.py``) use this module to

* classify such a task ``status: "invalid"``, ``invalid: "rate_limited"`` and
  its trial ``invalid: "rate_limited"``, never ``failed``;
* stop the run at once with ``STOPPED_MESSAGE`` (exit code ``EXIT_RATE_LIMITED``)
  instead of sending the remaining billed messages; and
* leave invalid trials out of every pass rate, reporting their count.

Evidence is either an explicit limit error (a chat ``error`` row a provider
wrote, a runtime result marked as an error, a rejected ``rate_limit_event``, a
transcript ``apiError``, a Codex turn HTTP 429 ended) or a throttle that was
waited out, the same on both sides and both runtimes: on the MCP side Claude
Code's own retry of an HTTP 429 (``api_retry``) or Codex's (``error`` with
``willRetry`` and HTTP 429); on Angelo's side its receipt's count of the same
retries and of a throttled turn it resent itself (``THROTTLE_RESENDS``). The
request then succeeded, but the account was at its limit and its time includes
the wait. A turn with zero tokens whose provider message names a limit counts
too. A retry for another reason (5xx, an overloaded provider) never counts, on
either side. Host-, plan- and tool-authored text never counts. A model's
ordinary reply never counts. Pure functions, no Blender: host-testable.
"""

import re

INVALID = "rate_limited"
EXIT_RATE_LIMITED = 3
STOPPED_MESSAGE = ("STOPPED: the provider's rate or usage limit was reached (%s). This trial is invalid "
                   "(rate-limited), not a task failure, and no further billed messages were sent. Rerun after "
                   "the limit resets.")
# Session/weekly/usage limits (Claude subscriptions), API rate limits and HTTP 429.
LIMIT_TEXT = re.compile(r"rate[ _-]?limit|usage[ _-]?limit|session limit|weekly limit|hit your (?:\w+ )?limit"
                        r"|limit (?:reached|exceeded)|quota exceeded|too many requests|\b429\b", re.IGNORECASE)
# How a row a provider wrote about its limit begins: a Codex TurnError as ``provider_errors.codex_error_text``
# prefixes it, Claude Code's API error or its subscription limit message. Host-, plan- and tool-authored text never
# begins so, even when it names a limit or "429" ("Host rejected the plan: object 'Cube 429' not found", "Native plan
# iteration limit reached", "Scene object limit reached"): only a row that matches here is limit evidence.
PROVIDER_LIMIT_ROW = re.compile(
    r"\s*(?:Too many requests \(HTTP 429\):|Provider usage limit reached \("
    r"|API Error\b[^\n]{0,400}?(?:\b429\b|rate[ _-]?limit|usage[ _-]?limit|quota exceeded)|rate_limit_error\b"
    r"|You(?:'|\u2019)ve (?:hit|reached) your (?:[\w-]+ )?limit|Claude AI usage limit reached"
    r"|(?:[\w-]+ )?limit (?:reached|exceeded)\b)", re.IGNORECASE)
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_read_input_tokens", "cache_write_input_tokens",
                "cache_creation_input_tokens", "output_tokens")


def limit_text(value):
    """Whether ``value`` (text) names a provider limit."""
    return isinstance(value, str) and LIMIT_TEXT.search(value) is not None


def _evidence(text):
    return " ".join(str(text).split())[:160]


def zero_tokens(usage):
    """Whether a usage record exists and every token count in it is zero (nothing reached a model)."""
    if not isinstance(usage, dict):
        return False
    counts = [usage[key] for key in TOKEN_FIELDS if key in usage]
    return bool(counts) and all(type(count) in (int, float) and count == 0 for count in counts)


# Receipt count: throttled requests (HTTP 429) Angelo resent (codex_retry) or its runtime retried itself (Claude
# Code's api_retry: claude_stream.retry_kind; Codex's willRetry), so the MCP side's own retries count the same.
THROTTLE_RESENDS = "provider_retries_rate"


def angelo_limit(rows, usage, counts=None):
    """The limit evidence of one Angelo request, or None.

    ``rows`` are the chat rows the request added (``role``/``text``), ``usage``
    its receipt's provider usage, ``counts`` its receipt's counts. Only what a
    provider wrote counts (``PROVIDER_LIMIT_ROW``): an ``error`` row, or with
    zero tokens (no model ran) an ``assistant`` or ``system`` row, that begins
    as a provider's limit message. Tool rows and plan or reply text never do.
    So is a turn the provider throttled that Angelo waited for and sent again
    (``THROTTLE_RESENDS``): even when the resend succeeded, the account was at
    its limit and the request's time includes the wait.
    """
    if isinstance(counts, dict) and type(counts.get(THROTTLE_RESENDS)) is int and counts[THROTTLE_RESENDS] > 0:
        return "the provider throttled a turn (HTTP 429); Angelo waited and sent it again"
    texts = [(row.get("role"), row.get("text")) for row in rows if isinstance(row, dict)]
    roles = ("error", "assistant", "system") if zero_tokens(usage) else ("error",)
    for role, text in texts:
        if role in roles and isinstance(text, str) and PROVIDER_LIMIT_ROW.match(text):
            return _evidence(text)
    return None


def _message_texts(message):
    content = (message.get("message") or {}).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content]
    return [item.get("text") for item in content or () if isinstance(item, dict) and item.get("type") == "text"]


def claude_throttle(message):
    """Whether a Claude Code stream message is its retry of a throttled API request (2.1.301 ``api_retry``)."""
    return (message.get("type") == "system" and message.get("subtype") == "api_retry"
            and (message.get("error_status") == 429 or message.get("error") == "rate_limit"))


# Since when a throttle a runtime waited out counts on both sides: Angelo's receipts count it from the same
# commit that made the rule (claude_stream.retry_kind, codex_retry.note), and every trial recorded since keeps
# its test code's version (bench_integrity). A re-summary of an older trial leaves it out on both sides.
WAITED_OUT_SINCE = "the trial records its test code's version (bench_integrity)"


def mcp_limit(events, result, usage, waited_out=True):
    """The limit evidence of one headless Claude Code turn (MCP side), or None.

    ``events`` are the turn's stream messages (``(stamp, message)`` pairs or
    messages), ``result`` its ``result`` message, ``usage`` its token usage.
    ``waited_out`` False (a trial recorded before Angelo's side counted the same retries, ``WAITED_OUT_SINCE``):
    a throttle Claude Code waited out is not evidence, so the rule stays the same on both sides.
    """
    messages = [event[1] if isinstance(event, tuple) else event for event in events or ()]
    for message in messages:
        if not isinstance(message, dict):
            continue
        if waited_out and claude_throttle(message):   # Claude Code waited out a throttle, as Angelo's receipt counts.
            return "Claude Code retried a throttled request (HTTP 429, attempt %s)" % _evidence(message.get("attempt"))
        info = message.get("rate_limit_info") if message.get("type") == "rate_limit_event" else None
        if isinstance(info, dict) and info.get("status") == "rejected":
            return "rate_limit_event rejected (%s)" % _evidence(info.get("rateLimitType", "limit"))
        error = message.get("apiError") or message.get("error")
        if message.get("type") == "assistant" and isinstance(error, str) and limit_text(error):
            return _evidence(" ".join(filter(None, [error] + _message_texts(message))))
    texts = [text for message in messages if isinstance(message, dict) and message.get("type") == "assistant"
             for text in _message_texts(message)]
    if isinstance(result, dict):
        texts.append(result.get("result"))
        texts += [str(error) for error in result.get("errors") or ()]
    limited = [text for text in texts if limit_text(text)]
    # Some stream events carry ``message`` as plain text, not as an API message.
    synthetic = any(isinstance(message, dict) and isinstance(message.get("message"), dict)
                    and message["message"].get("model") == "<synthetic>" for message in messages)
    if limited and (synthetic or zero_tokens(usage) or (isinstance(result, dict) and result.get("is_error"))):
        return _evidence(limited[0])
    return None


CODEX_LIMIT_ERRORS = frozenset({"usageLimitExceeded", "rateLimitExceeded", "sessionBudgetExceeded"})


def _exhausted(window):
    return isinstance(window, dict) and type(window.get("usedPercent")) in (int, float) and window["usedPercent"] >= 100


def _error_kind(info):
    """``codexErrorInfo`` is a name (``"usageLimitExceeded"``) or an object keyed by it
    (``{"responseStreamDisconnected": {"httpStatusCode": 503}}``)."""
    if isinstance(info, str):
        return info
    return next(iter(info), None) if isinstance(info, dict) and len(info) == 1 else None


def _http_status(info):
    detail = next(iter(info.values()), None) if isinstance(info, dict) and len(info) == 1 else None
    status = detail.get("httpStatusCode") if isinstance(detail, dict) else None
    return status if type(status) is int else None


def codex_limit(messages, completed, waited_out=True):
    """The limit evidence of one Codex app-server turn (MCP side), or None.

    ``messages`` are the turn's notifications (``{"method", "params"}``),
    ``completed`` its ``turn/completed`` params. A failed turn or ``error``
    notification whose ``codexErrorInfo`` names a limit is enough, as is a
    ``rateLimitReachedType`` or a rate-limit window at 100% with a failed turn.
    So is HTTP 429 in the error that ended the turn (a throttle: Codex sends no
    retry of a 429 without ``Retry-After``, ``responseTooManyFailedAttempts``),
    as a Claude turn's 429 is (``limit_text``), and a 429 Codex retried itself
    (``willRetry``) even when the turn then completed: Angelo's receipts count
    the same retry (``codex_retry.note``), and Claude Code's are counted alike. ``waited_out`` False: as in
    ``mcp_limit``, a retried 429 of a trial recorded before that is not evidence.
    """
    turn = (completed or {}).get("turn") or {}
    errors = [(turn.get("error"), turn.get("status") == "failed")]
    errors += [((message.get("params") or {}).get("error"), (message.get("params") or {}).get("willRetry") is not True)
               for message in messages or () if isinstance(message, dict) and message.get("method") == "error"]
    for error, final in errors:
        info = error.get("codexErrorInfo") if isinstance(error, dict) else None
        kind = _error_kind(info)
        if kind in CODEX_LIMIT_ERRORS:
            return "%s: %s" % (kind, _evidence(error.get("message", "")))
        if _http_status(info) == 429 and (final or waited_out):
            return "%s (HTTP 429%s): %s" % (kind, "" if final else ", retried by Codex",
                                            _evidence(error.get("message", "")))
    failed = ((completed or {}).get("turn") or {}).get("status") == "failed"
    for message in messages or ():
        snapshot = (message.get("params") or {}).get("rateLimits") if isinstance(message, dict) else None
        if isinstance(snapshot, dict) and snapshot.get("rateLimitReachedType"):
            return "rate limit reached: %s (plan %s)" % (_evidence(snapshot["rateLimitReachedType"]),
                                                         _evidence(snapshot.get("planType") or "unknown"))
        if failed and isinstance(snapshot, dict) and any(_exhausted(snapshot.get(key)) for key in ("primary", "secondary")):
            return "rate-limit window exhausted (plan %s)" % _evidence(snapshot.get("planType", "unknown"))
    return None


def invalid_trials(trials):
    """``{"rate_limited": count}`` over trial reports (dicts with an optional ``invalid``)."""
    count = sum(isinstance(trial, dict) and trial.get("invalid") == INVALID for trial in trials)
    return {INVALID: count} if count else {}


def valid_records(records):
    """``(records of valid trials, {side: {"trials": n, "tasks": m}} of the invalid ones)``.

    Records carry their trial's ``invalid`` (``bench_mcp_vs_angelo.gather``).
    """
    valid, invalid = [], {}
    for record in records:
        if record.get("invalid") == INVALID:
            entry = invalid.setdefault(record.get("side", "angelo"), {"trials": set(), "tasks": 0})
            entry["trials"].add(record.get("trial"))
            entry["tasks"] += 1
        else:
            valid.append(record)
    return valid, {side: {"trials": len(entry["trials"]), "tasks": entry["tasks"]} for side, entry in invalid.items()}
