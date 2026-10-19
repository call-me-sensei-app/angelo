# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What one Claude Code stream-json event of the active round means for the chat.

Claude Code forwards the Messages API's stream events
(``--include-partial-messages``) as ``{"type": "stream_event", "event": ...}``
and each finished content block as an ``assistant`` message. A typed-plan
round shows nothing until its plan arrives unless the host reads them:

* A thinking block becomes a thinking row, as a Codex reasoning summary does:
  only the summary text the runtime itself streams, never an invented one.
  The text reaches the chat at most twice a second (``THINKING_SECONDS``), so
  minutes of thinking cost two redraws a second and no save
  (``tool_finalization.DISPLAY_EVENTS``).
* The ``StructuredOutput`` call that carries the plan sets the status, so a
  long plan being written is not mistaken for more thinking, and as its
  input streams the status says what it will make (``plan_progress``) and
  carries its provisional steps (``plan_steps``): the first at once, later
  changes at most twice a second. A call the runtime's schema check refuses
  takes them away at once; the round's result ends them in any case.
* The plan's ``reply`` streams into the chat as it is written (``PlanReply``),
  so an answer shows its first words instead of arriving whole with the
  finished plan. Those fragments are display only (``plan_reply``,
  ``reply_overlay``): the chat stores a reply when the provider's final row
  for the plan the host received says it, never before, and the row recording
  each plan call (the runtime's own record, with its schema verdict) keeps the
  call's operations but never its reply; nothing about what runs changes.
* The model's own events (``answering``) are signs of life (``turn_activity``).
* Claude Code's own API retries (``system`` ``api_retry``: ``retry_kind``) are
  counted on the request's receipt, as Codex's are (``codex_retry``): a
  throttled one (HTTP 429) as ``provider_retries_rate``, another as
  ``provider_retries_busy``. The request's time includes the wait, and a
  harness trial with a throttled request is invalid (``eval_provider_limits``).
* ``message_start`` and ``message_delta`` carry the API's own token counts for
  that message. Their sum is the round's usage when the runtime's running
  totals cannot give it: a resumed process's first result, and a round that
  was stopped or timed out before any result (``provider_usage``).

Display only: nothing here is sent to a model or decides what runs.
"""

import json
import re
import time

from . import plan_steps
from .plan_progress import PlanProgress, status as plan_status

PLAN_TOOL = "StructuredOutput"
UNPARSED = "__unparsedToolInput"  # Claude Code's input for a call it could not parse: {"raw": text, "len": bytes}.
SCHEMA_ERROR = "Output does not match required schema: "
# Events that show the model's request is alive: the API's stream, finished blocks, tool results and
# the result. The runtime's own notices (system events such as a retry, keep-alives) are not.
MODEL_EVENTS = frozenset({"stream_event", "assistant", "user", "result"})


def answering(event):
    """Whether a runtime event shows that the model is answering.

    Claude Code puts a ``ping`` stream event into a stream that has gone quiet
    (2.1.293), so a ping says that the runtime is waiting, not that the API is
    producing anything.
    """
    return event.get("type") in MODEL_EVENTS and (event.get("event") or {}).get("type") != "ping"
STATUS_THINKING = "Thinking"
STATUS_PLAN = "Preparing the next steps"
MAX_PLAN_CALLS = 16
MAX_MESSAGES = 64
THINKING_SECONDS = 0.5
MAX_MODELS = 8


def likely_cause(text):
    """The one mismatch a schema rejection is most likely about, as one line, or None.

    Claude Code answers a plan that fails its schema with the errors of every
    ``oneOf`` member (about 8,000 characters for one over-long text). The
    members that were never meant fail at the tool name or at the top of the
    arguments; the one that was meant fails deeper, so the error with the
    longest path is the one to read first. The runtime writes that answer to
    the model itself; the host can only put the cause first where it relays
    the text: the person's row of the rejected call, and the error of a round
    the runtime gave up on.
    """
    if not isinstance(text, str) or SCHEMA_ERROR not in text:
        return None
    best, count = None, 0
    for item in re.split(r", (?=/)", text.split(SCHEMA_ERROR, 1)[1]):
        path, _, message = item.partition(": ")
        if path.startswith("/") and message:
            count += 1
            if best is None or path.count("/") > best[0].count("/"):
                best = (path, message)
    if best is None or (count > 1 and best[0].count("/") < 4):  # Nothing below operations/N/arguments: every member failed alike.
        return None
    where = re.sub(r"/(\d+)", r"[\1]", best[0]).strip("/").replace("/", ".")
    return "Most likely cause: %s %s" % (where, best[1].strip()[:300])


def _count(value):
    return value if type(value) is int and 0 <= value <= 10 ** 12 else None


# A plan's reply streams into the chat up to this many characters; the finished plan's row has it all.
MAX_REPLY_CHARS = 32000
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_HEX = frozenset("0123456789abcdefABCDEF")


MAX_KEY_CHARS = 64  # Longer than any plan key; a longer one is never "reply".


def _decoded(raw):
    """A JSON string's text from its raw content, or None when it is not valid JSON."""
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return None


class PlanReply:
    """The text of a plan's top-level ``reply`` as the plan's JSON streams in.

    The plan schema lists ``operations`` before ``reply`` and models write them
    in that order, so an answer (operations ``[]``) starts after about twenty
    characters; an edit's reply, when it has one, comes last. Only the
    top-level key counts: strings, objects and arrays inside operations are
    skipped whole, so a ``"reply"`` inside a script or a setting is never shown.
    A key counts by its decoded text (``re\\"ply`` is not ``reply``), and a
    ``reply`` that is not a string (null, an array, an object) shows nothing.
    An escape is decoded once it is complete (a surrogate pair waits for its
    second half); text that is not valid JSON ends the stream.
    """

    def __init__(self):
        self.depth, self.in_string, self.escape, self.done = 0, False, False, False
        self._key, self._expect_key, self._state, self._raw, self._position, self.length = None, False, "", "", 0, 0

    def feed(self, text):
        """The reply text this fragment adds ("" when none)."""
        if self.done or not isinstance(text, str):
            return ""
        for character in text:
            if self._state == "value":  # Inside the reply string.
                if character == '"' and not self.escape:
                    self._state = "closed"
                    break
                self.escape = character == "\\" and not self.escape
                self._raw += character
                continue
            if self.in_string:
                if self.escape:
                    self.escape = False
                elif character == "\\":
                    self.escape = True
                elif character == '"':
                    self.in_string = False
                    if self._key is not None:  # A top-level key ended: is it, decoded, "reply"?
                        self._key, self._state = None, ("colon" if _decoded(self._key) == "reply" else "")
                    continue
                if self._key is not None and len(self._key) < MAX_KEY_CHARS:
                    self._key += character  # Raw, escapes included, and decoded whole at its end.
                continue
            if self._state == "start" and not character.isspace() and character != '"':
                self._state = ""  # A reply that is not a string (null, an array, an object) shows nothing.
            if character == '"':
                if self.depth == 1 and self._state == "start":
                    self._state = "value"
                    continue
                self.in_string = True
                if self.depth == 1 and self._expect_key:
                    self._key, self._expect_key = "", False
            elif character in "{[":
                self.depth += 1
                self._expect_key = self.depth == 1 and character == "{"
            elif character in "}]":
                self.depth -= 1
            elif character == "," and self.depth == 1:
                self._expect_key = True
            elif character == ":" and self.depth == 1 and self._state == "colon":
                self._state = "start"
        added = self._drain()
        self.done = self.done or self._state == "closed"
        return added

    def _drain(self):
        """Decode the reply's complete escapes from the last position on."""
        out, raw, index = [], self._raw, self._position
        while index < len(raw) and self.length + len(out) < MAX_REPLY_CHARS:
            character = raw[index]
            if character != "\\":
                out.append(character)
                index += 1
                continue
            if index + 1 >= len(raw):
                break
            if raw[index + 1] != "u":
                out.append(_ESCAPES.get(raw[index + 1], raw[index + 1]))
                index += 2
                continue
            if index + 6 > len(raw):
                break
            if not set(raw[index + 2:index + 6]) <= _HEX:
                self.done = True
                break
            code = int(raw[index + 2:index + 6], 16)
            if 0xD800 <= code < 0xDC00:
                if index + 12 > len(raw):
                    break  # Its low surrogate has not arrived yet.
                low = int(raw[index + 8:index + 12], 16) if (raw[index + 6:index + 8] == "\\u"
                                                             and set(raw[index + 8:index + 12]) <= _HEX) else -1
                if 0xDC00 <= low < 0xE000:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)))
                    index += 12
                    continue
                code = 0xFFFD
            out.append(chr(code))
            index += 6
        self._position = index
        self.length += len(out)
        return "".join(out)


def retry_kind(event):
    """``"rate"`` or ``"busy"`` for a Claude Code ``api_retry`` event (2.1.301 stream-json), else None.

    Claude Code sends one before each retry of a failed API request: ``error_status`` 429 or ``error``
    ``rate_limit`` is a throttle; any other status (5xx, overloaded, a dropped connection) is the provider busy.
    """
    if event.get("type") != "system" or event.get("subtype") != "api_retry":
        return None
    return "rate" if event.get("error_status") == 429 or event.get("error") == "rate_limit" else "busy"


class RoundStream:
    """One round as it streams: its text, its open thinking blocks and the API's token counts."""

    def __init__(self, on_usage=None, on_retry=None):
        # ``on_usage(usage)``: the round's usage so far, each time the API reports a count (request_path).
        # ``on_retry(kind)``: Claude Code retried an API request (``retry_kind``).
        self.had_delta, self._on_usage, self._on_retry = False, on_usage, on_retry
        self._message = None
        self._thinking = {}  # Content block index -> [thinking row item, text not shown yet, when text was last shown].
        self._plan = None  # The plan call being written: [content block index, PlanProgress, when shown, status shown].
        self._reply = None  # [content block index, PlanReply, whether this call's reply has shown yet].
        # API message id -> [uncached input, cache read, cache write, output, finished].
        self._messages = {}
        self._models = set()  # The models that answered, as the API names them.
        # The last plan call whose input Claude Code could not parse as JSON ({"raw", "len"}): json_position.
        self.unparsed = None

    def _usage(self, usage, finished=False):
        entry = self._messages.get(self._message)
        if entry is None or not isinstance(usage, dict):
            return
        for index, key in enumerate(("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                     "output_tokens")):
            value = _count(usage.get(key))
            if value is not None:
                entry[index] = value
        entry[4] = entry[4] or finished
        if callable(self._on_usage):
            self._on_usage(self.observed_usage()[0])

    def observed_usage(self):
        """``(usage, complete)`` of the round from the API's own counts, or ``(None, False)``.

        Input fields are exact once a message started. ``output_tokens`` is
        present only when every started message finished: the API reports a
        message's output count at its end, so an interrupted message's is
        unknown and never estimated.
        """
        entries = list(self._messages.values())
        if not entries or any(None in entry[:3] for entry in entries):
            return None, False
        usage = {"input_tokens": sum(sum(entry[:3]) for entry in entries),
                 "cached_input_tokens": sum(entry[1] for entry in entries),
                 "cache_write_input_tokens": sum(entry[2] for entry in entries)}
        complete = all(entry[4] and entry[3] is not None for entry in entries)
        usage["requests"] = len(entries)  # API messages this round started.
        if complete:
            usage["output_tokens"] = sum(entry[3] for entry in entries)
        if self._models:
            usage["models"] = sorted(self._models)[:MAX_MODELS]
        return usage, complete

    def events(self, event, plans):
        """Chat events for one runtime event; ``plans`` collects the round's accepted plan calls by id."""
        emits, kind = [], event.get("type")
        if retry_kind(event) and callable(self._on_retry):
            self._on_retry(retry_kind(event))
        if event.get("session_id"):
            emits.append(("native_session", {"id": event["session_id"], "model": event.get("model", "")}))
        content = (event.get("message") or {}).get("content") if kind in ("assistant", "user") else None
        content = [item for item in content if isinstance(item, dict)] if isinstance(content, list) else []
        if kind == "assistant":
            # Whether or not its text streamed: the runtime returns only the
            # last StructuredOutput call of a turn (plan_validation.superseded_plans).
            for item in content:
                if (item.get("type") == "tool_use" and item.get("name") == PLAN_TOOL
                        and isinstance(item.get("id"), str) and len(plans) < MAX_PLAN_CALLS):
                    plans[item["id"]] = item.get("input")
                if item.get("type") == "tool_use" and item.get("name") == PLAN_TOOL:
                    # The latest call only: one that parsed (and failed its schema, say) is no parse failure.
                    unparsed = (item.get("input") or {}).get(UNPARSED) if isinstance(item.get("input"), dict) else None
                    self.unparsed = unparsed if isinstance(unparsed, dict) else None
        if kind == "stream_event":
            emits += self._stream(event.get("event") or {})
        elif kind == "assistant" and not self.had_delta:
            for item in content:
                if item.get("type") == "tool_use":
                    arguments = item.get("input", {})
                    if item.get("name") == PLAN_TOOL and isinstance(arguments, dict):
                        # A plan call's row keeps what it asked to run, never its reply: that is the assistant
                        # row's once the plan arrives (reply_overlay), so a refused, superseded or failed call's
                        # reply, or a key it repeats, is saved and drawn nowhere.
                        arguments = {key: value for key, value in arguments.items() if key != "reply"}
                    emits.append(("activity", {"id": "claude-web/" + item["id"], "tool": item.get("name", "web_search"),
                                               "arguments": arguments, "status": "running"}))
                if item.get("type") == "text":
                    emits.append(("delta", {"item": "claude-assistant", "text": item.get("text", "")}))
        elif kind == "user":
            for item in content:
                if item.get("type") != "tool_result":
                    continue
                text = str(item.get("content", ""))
                if item.get("is_error"):
                    # A call the runtime's schema check rejected never counted, nor do its provisional steps.
                    if item.get("tool_use_id") in plans:
                        plans.pop(item.get("tool_use_id"))
                        emits.append(("status", {"text": STATUS_THINKING}))
                        self._plan = None
                    text = "\n".join(filter(None, (likely_cause(text), text)))
                emits.append(("activity", {"id": "claude-web/" + item["tool_use_id"],
                                           "status": "failed" if item.get("is_error") else "completed",
                                           "result": {"content": text[:8000]}}))
        elif kind == "result" and self._messages:
            emits.append(("status", {"text": STATUS_THINKING}))  # The plan is written; the host runs it next.
        return emits

    def _stream(self, event):
        emits, kind = [], event.get("type")
        if kind == "message_start":
            self.had_delta = False
            message = event.get("message") or {}
            self._message = message.get("id") if isinstance(message.get("id"), str) else None
            if self._message is not None and len(self._messages) < MAX_MESSAGES:
                self._messages.setdefault(self._message, [None, None, None, None, False])
                self._usage(message.get("usage"))
                if isinstance(message.get("model"), str) and 0 < len(message["model"]) <= 128:
                    self._models.add(message["model"])
            emits.append(("status", {"text": STATUS_THINKING}))
        elif kind == "message_delta":
            self._usage(event.get("usage"), finished=True)
        elif kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "thinking" and self._message is not None:
                item = "claude-thinking/%s/%s" % (self._message, event.get("index"))
                self._thinking[event.get("index")] = [item, "", time.monotonic()]
                emits.append(("thinking_started", {"item": item, "time": time.perf_counter()}))
            elif block.get("type") == "tool_use" and block.get("name") == PLAN_TOOL:
                emits.append(("status", {"text": STATUS_PLAN}))
                self._plan = [event.get("index"), PlanProgress(), None, STATUS_PLAN]
                self._reply = [event.get("index"), PlanReply(), False]
        elif kind == "content_block_stop":
            block = self._thinking.pop(event.get("index"), None)
            if block is not None:
                if block[1]:
                    emits.append(("thinking", {"item": block[0], "text": block[1]}))
                emits.append(("thinking_completed", {"item": block[0], "time": time.perf_counter()}))
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            self.had_delta = True
            emits.append(("delta", {"item": "claude-assistant", "text": delta.get("text", "")}))
        elif delta.get("type") == "input_json_delta" and self._plan is not None and event.get("index") == self._plan[0]:
            text = self._reply[1].feed(delta.get("partial_json"))
            if text:
                # Display only until the plan arrives (reply_overlay); a later plan call (a schema retry) or
                # round starts its reply over.
                emits.append(("delta", {"item": "claude-assistant", "text": text, "plan_reply": True,
                                        **({} if self._reply[2] else {"replace": True})}))
                self._reply[2] = True
            # What the plan will make, as it is written (plan_progress, plan_steps): the first at once, then at
            # most twice a second, like thinking.
            index, progress, shown, last = self._plan
            progress.feed(delta.get("partial_json"))
            current = plan_status(STATUS_PLAN, progress.detail(), plan_steps.encode(progress))
            if current != last and (shown is None or time.monotonic() - shown >= THINKING_SECONDS):
                self._plan[2:] = [time.monotonic(), current]
                emits.append(("status", {"text": current}))
        elif delta.get("type") == "thinking_delta" and event.get("index") in self._thinking:
            block = self._thinking[event.get("index")]
            block[1] += str(delta.get("thinking", ""))
            if time.monotonic() - block[2] >= THINKING_SECONDS:
                emits.append(("thinking", {"item": block[0], "text": block[1]}))
                block[1], block[2] = "", time.monotonic()
        return emits
