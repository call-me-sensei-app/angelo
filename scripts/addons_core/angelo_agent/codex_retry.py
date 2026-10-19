# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One automatic resend of a Codex turn that a transient provider failure ended before anything happened.

codex app-server retries a failed model request itself only when the server advised a delay
(``Retry-After``: it emits ``error`` with ``willRetry`` and tries again). A plain HTTP 429 becomes
"exceeded retry limit, last status: 429 Too Many Requests" (``responseTooManyFailedAttempts``) with no
retry at all: in a recorded run it failed its turn 73 ms after one request, and ChatGPT's websocket error
frames never carry a ``Retry-After``. The throttle observed allowed about five requests per rolling
minute (a request every 15 s succeeded). 5xx answers Codex gave up on end the turn the same way.
``codex_attempts.run`` resends such a turn once (``kind``, ``delay``) when all hold:

* the failure is transient: ``provider_errors.codex_error_kind`` is ``rate`` (HTTP 429, not a usage
  limit) or ``busy`` (overloaded, or 5xx answers Codex gave up on); never a usage limit, sign-in, bad
  request, context, policy or unknown error, and never ``rateLimitExceeded`` (Codex backs off and
  retries that one itself before it ends the turn). In practice ``busy`` is ``serverOverloaded``:
  Codex retries 5xx answers itself (``error`` with ``willRetry``), which rules the resend out;
* the turn did nothing (``note``): no tool call reached the host, no model output of any kind streamed
  (message, reasoning, web search, any item but the person's own message) and no model response
  acknowledged the input. Nothing ran, so nothing can run twice or be half-applied;
* Codex did not retry it itself (no ``error`` with ``willRetry`` in the turn): retries never stack;
* the request has not resent yet (one per request, plan corrections included), it is not voice, and
  Stop or a newer request does not arrive during the wait (``wait``): then nothing more is sent.

The wait is fixed: a throttle waits ``RATE_SECONDS`` (past a one-minute window) and an overloaded
provider ``BUSY_SECONDS``, plus jitter. No retry hint is read: codex 0.162 never passes the server's text
or ``Retry-After`` into a TurnError it did not retry itself (the message is its own, "exceeded retry
limit, last status: 429 Too Many Requests"). The live row counts the wait down (``waiting``: "Provider
busy — retrying in N s", Stop stays available) and says "Provider busy, retrying…" while Codex retries a
turn itself (``RETRYING``; ``chat_titles.live_label``). An app-server lost during the wait sends nothing
more: the request fails with the provider's own error (``codex_attempts.run``), and a Stop that lands
just before the resend's ``turn/start`` ends the request as stopped.

What is resent: Codex records the person's message in the thread when a turn starts
(``item/completed`` of type ``userMessage``; resending the same input put it in the model's history
twice). When that item was seen, the resend's only input is ``NOTE``, so the model sees the message
once and is told nothing ran in the failed turn; whatever that message reports as done (a plan
correction lists committed operations) still stands. Otherwise the original input is sent again. The
request's receipt counts the resend (``provider_retries_rate`` or ``provider_retries_busy``,
receipt-only): its time includes the wait, and a throttled request makes a harness trial invalid like
any provider limit. A throttled request Codex retries itself (``error`` with ``willRetry`` and HTTP 429)
counts as ``provider_retries_rate`` too, as Claude Code's own retries do (``claude_stream.retry_kind``),
so the rule is the same whichever runtime or side waited. The happy path pays nothing: this runs only
after a turn has already failed.
"""

import math
import random
import time

from .plan_progress import SEPARATOR
from .provider_errors import codex_error_info, codex_error_kind, codex_error_text

RETRYING = "Provider busy, retrying…"  # Codex retries the turn itself (no known delay).
WAITING = "Provider busy, waiting"     # Status key; its detail is the whole seconds left (chat_titles).
NOTE = ("[Host note] A provider error ended the previous turn before you received the message above, so nothing ran "
        "in that turn. Anything the message above reports as already done still stands. Respond to that message now.")
PER_REQUEST = 1
RATE_SECONDS, BUSY_SECONDS, JITTER_SECONDS = 60.0, 3.0, 2.0
POLL_SECONDS = 0.05  # How soon Stop ends the wait.
COUNTS = {"rate": "provider_retries_rate", "busy": "provider_retries_busy"}  # telemetry.RECEIPT_ONLY_COUNTS


def kind(error):
    """``"rate"`` or ``"busy"`` for a TurnError a resend may follow, else None."""
    found = codex_error_kind(error)
    return found if found in ("rate", "busy") and codex_error_info(error)[0] != "rateLimitExceeded" else None


def delay(error, rng=random.random):
    """Seconds to wait before the one resend of a turn this TurnError ended (fixed), or None: do not resend."""
    found = kind(error)
    if found is None:
        return None
    return (RATE_SECONDS if found == "rate" else BUSY_SECONDS) + JITTER_SECONDS * rng()


def waiting(seconds_left):
    """The status that counts a resend's wait down: ``WAITING`` and the whole seconds left."""
    return "%s%s%d" % (WAITING, SEPARATOR, max(0, math.ceil(seconds_left)))


def wait(seconds, stopped, emit=None, clock=time.monotonic, sleep=time.sleep):
    """True after ``seconds``; False as soon as ``stopped()`` (Stop, or a newer request owns the chat).

    ``emit(status)`` receives ``waiting`` each time the whole seconds left change.
    """
    deadline, shown = clock() + seconds, None
    while True:
        if stopped():
            return False
        remaining = deadline - clock()
        if remaining <= 0:
            return True
        status = waiting(remaining)
        if emit is not None and status != shown:
            shown = status
            emit(status)
        sleep(min(POLL_SECONDS, remaining))


def reset(provider):
    """A new turn attempt's record of what the turn did (``note``) and the TurnError it ended with."""
    provider._turn_output = provider._turn_user_recorded = provider._codex_retried = provider._retrying_shown = False
    provider._turn_error = None


def note(provider, context, method, params):
    """One notification of the provider's current turn: anything but its start, end, errors and the
    person's own message is model output (an unknown event counts too, so it never permits a resend)."""
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    if method in {"item/started", "item/completed"} and item.get("type") == "userMessage":
        provider._turn_user_recorded = True
    elif method == "error":
        if params.get("willRetry") is True:  # Codex retries this request itself; the turn goes on.
            provider._codex_retried = True
            if codex_error_kind(params.get("error")) == "rate":   # Throttled: counted as Angelo's own resend is.
                from .codex_attempts import receipt_count
                receipt_count(context, COUNTS["rate"])
            if not provider._retrying_shown and not context.voice:  # Voice keeps its own status line.
                provider._retrying_shown = True
                context.emit("status", {"text": RETRYING})
        else:
            provider.last_error = codex_error_text(params.get("error")) or "Provider error"
            fact = getattr(context, "record_context_fact", None)
            if codex_error_info(params.get("error"))[0] == "contextWindowExceeded" and callable(fact) and not context.voice:
                fact("context_full", True)  # A fork cannot compact: the next message starts afresh (context_rebase).
    elif method not in {"turn/started", "turn/completed"}:
        provider._turn_output = True
        if provider._retrying_shown:  # Codex's own retry got through: the live row says what happens now.
            provider._retrying_shown = False
            context.emit("status", {"text": "Thinking"})
