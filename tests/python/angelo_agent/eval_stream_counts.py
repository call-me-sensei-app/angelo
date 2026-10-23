# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the provider streamed during a live-eval turn, as the trial process observes it (``abnormal_end.stream``).

A turn that ran out its time either went silent or kept streaming without making anything. The product keeps no
per-turn delta counts it could report: it counts streamed characters only for the live token estimate, and on a
Codex plan thread only the reply it decodes, since the plan JSON after the reply is never emitted
(``CodexProvider._scoped_event``, ``reply_delta``). So the harness observes the stream itself. In its own
disposable trial process only, it wraps the two places every model stream event passes:

* ``CodexProvider._scoped_event``: each app-server notification Angelo receives. ``item/agentMessage/delta`` is
  text, reasoning deltas are thinking, and ``item/started`` of anything other than a message, reasoning or the
  user's input is a tool call;
* ``claude_stream.RoundStream._stream``: each stream-json event of Claude Code. ``text_delta`` and
  ``input_json_delta`` are text (a plan is JSON the model writes), ``thinking_delta`` is thinking, and a
  ``tool_use`` block other than the plan's own ``StructuredOutput`` is a tool call.

Each wrapper counts the event, then calls the original unchanged. Nothing that ships is altered.

``Cursor.summary`` gives, since the turn's send: text bytes (UTF-8) and deltas, thinking bytes and deltas, the
current message's counts (``open`` until its completion event), the delta rate over the last ``WINDOW_S``, and the
current burst of text deltas (no gap over ``BURST_GAP_S``) with ``structural_progress``. That flag says whether a
tool call, or a host-visible change (a tool row, a provisional plan step or a script line,
``ProgressWatch.last_structure_at``), happened since the burst began. Every stream in the process counts; a trial
runs one chat. Standard library only at import.
"""

import bisect
import collections
import threading
import time

WINDOW_S, BURST_GAP_S = 10.0, 2.0
MAX_EVENTS = 200000   # 40 minutes at 80 deltas a second; longer turns mark their totals a lower bound.
CODEX_TEXT = frozenset({"item/agentMessage/delta"})
CODEX_THINKING = frozenset({"item/reasoning/summaryTextDelta", "item/reasoning/textDelta"})
CODEX_NOT_TOOLS = frozenset({"agentMessage", "reasoning", "userMessage"})
_installed = {}


def _ms(seconds):
    return None if seconds is None else round(seconds * 1000, 1)


class Counter:
    """Stream events with their times; written on provider threads, read on the main thread (one lock)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.times, self.text, self.structure = collections.deque(maxlen=MAX_EVENTS), collections.deque(
            maxlen=MAX_EVENTS), collections.deque(maxlen=4096)
        self.totals = {"text_bytes": 0, "text_deltas": 0, "thinking_bytes": 0, "thinking_deltas": 0}
        self.message = None   # [identity, bytes, deltas, started, open]

    def delta(self, kind, message, text, now=None):
        now, size = time.monotonic() if now is None else now, len(str(text or "").encode("utf-8"))
        with self.lock:
            self.totals[kind + "_bytes"] += size
            self.totals[kind + "_deltas"] += 1
            if kind != "text":
                return
            self.times.append(now)
            self.text.append((self.totals["text_bytes"], self.totals["text_deltas"]))
            if self.message is None or self.message[0] != message:
                self.message = [message, 0, 0, now, True]
            self.message[1] += size
            self.message[2] += 1

    def message_end(self, message):
        with self.lock:
            if self.message is not None and self.message[0] == message:
                self.message[4] = False

    def tool(self, now=None):
        with self.lock:
            self.structure.append(time.monotonic() if now is None else now)

    def cursor(self, sent=None):
        with self.lock:
            return Cursor(self, time.monotonic() if sent is None else sent, dict(self.totals))


class Cursor:
    """The counts at a turn's send; ``summary`` reports what came after it."""

    def __init__(self, counter, sent, totals):
        self.counter, self.sent, self.base = counter, sent, totals

    def summary(self, now=None, host_structure_at=None):
        now = time.monotonic() if now is None else now
        counter = self.counter
        with counter.lock:
            times, text = list(counter.times), list(counter.text)
            totals, message = dict(counter.totals), list(counter.message) if counter.message else None
            tools = [t for t in counter.structure if t >= self.sent]
        first = bisect.bisect_left(times, self.sent)
        times, text = times[first:], text[first:]

        def upto(index):   # Cumulative (bytes, deltas) after the ``index``th delta since the send (-1: at the send).
            return text[index] if index >= 0 else (self.base["text_bytes"], self.base["text_deltas"])

        recent = len(times) - bisect.bisect_left(times, now - WINDOW_S)
        recent_bytes = text[-1][0] - upto(len(text) - recent - 1)[0] if recent else 0
        burst = len(times) - 1   # The current burst's first delta: back to the last gap over BURST_GAP_S.
        while burst > 0 and times[burst] - times[burst - 1] <= BURST_GAP_S:
            burst -= 1
        structure = max(tools + ([host_structure_at] if host_structure_at is not None else []), default=None)
        before = upto(burst - 1)
        current = message if message and message[3] >= self.sent else None
        return {
            "observed_by": "harness", "lower_bound": len(counter.times) == MAX_EVENTS,
            "text": {"bytes": totals["text_bytes"] - self.base["text_bytes"],
                     "deltas": totals["text_deltas"] - self.base["text_deltas"]},
            "thinking": {"bytes": totals["thinking_bytes"] - self.base["thinking_bytes"],
                         "deltas": totals["thinking_deltas"] - self.base["thinking_deltas"]},
            "current_message": None if current is None else {
                "bytes": current[1], "deltas": current[2], "open": current[4],
                "started_after_send_ms": _ms(current[3] - self.sent)},
            "last_delta_age_ms": _ms(now - times[-1]) if times else None,
            "last_10s": {"deltas": recent, "bytes": recent_bytes, "deltas_per_s": round(recent / WINDOW_S, 2),
                         "bytes_per_s": round(recent_bytes / WINDOW_S, 1)},
            "burst": None if not times else {
                "started_after_send_ms": _ms(times[burst] - self.sent), "lasted_ms": _ms(times[-1] - times[burst]),
                "deltas": text[-1][1] - before[1], "bytes": text[-1][0] - before[0],
                "structural_progress": structure is not None and structure >= times[burst]},
            "tool_calls": len(tools),
            "last_structure_after_send_ms": _ms(structure - self.sent) if structure is not None else None}


COUNTER = Counter()


def codex_event(counter, method, params, now=None):
    params = params if isinstance(params, dict) else {}
    if method in CODEX_TEXT:
        counter.delta("text", ("codex", params.get("itemId")), params.get("delta"), now)
    elif method in CODEX_THINKING:
        counter.delta("thinking", None, params.get("delta"), now)
    elif method in {"item/started", "item/completed"}:
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if method == "item/started" and item.get("type") not in CODEX_NOT_TOOLS:
            counter.tool(now)
        elif method == "item/completed" and item.get("type") == "agentMessage":
            counter.message_end(("codex", item.get("id")))


def claude_event(counter, parser, event, plan_tool="StructuredOutput", now=None):
    event = event if isinstance(event, dict) else {}
    kind, index, key = event.get("type"), event.get("index"), id(parser)
    if kind == "content_block_delta":
        delta = event.get("delta") or {}
        if delta.get("type") in ("text_delta", "input_json_delta"):
            counter.delta("text", ("claude", key, index), delta.get("text") or delta.get("partial_json"), now)
        elif delta.get("type") == "thinking_delta":
            counter.delta("thinking", None, delta.get("thinking"), now)
    elif kind == "content_block_start":
        block = event.get("content_block") or {}
        if block.get("type") == "tool_use" and block.get("name") != plan_tool:
            counter.tool(now)
    elif kind == "content_block_stop":
        counter.message_end(("claude", key, index))


def _wrap(owner, name, observe):
    original = getattr(owner, name)

    def observed(self, *args, **kwargs):
        try:
            observe(self, *args)
        except Exception:
            pass   # Counting never changes what the product does with the event.
        return original(self, *args, **kwargs)

    observed.__wrapped__ = original
    setattr(owner, name, observed)
    _installed[(owner, name)] = original


def install(counter=COUNTER):
    """Wrap both stream entry points once, in this process only. Returns whether both are observed."""
    try:
        from angelo_agent import claude_stream
        from angelo_agent.providers import CodexProvider
    except Exception:
        return False
    if (CodexProvider, "_scoped_event") not in _installed:
        _wrap(CodexProvider, "_scoped_event", lambda _self, method, params, *rest: codex_event(counter, method, params))
    if (claude_stream.RoundStream, "_stream") not in _installed:
        _wrap(claude_stream.RoundStream, "_stream",
              lambda parser, event, *rest: claude_event(counter, parser, event, claude_stream.PLAN_TOOL))
    return True


def uninstall():
    for (owner, name), original in list(_installed.items()):
        setattr(owner, name, original)
        _installed.pop((owner, name))
