# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A plan thread's message that keeps streaming without finishing is cut (Codex plan turns).

A Codex plan turn ends with one message in the strict response format (``codex_plan``). In a recorded
gate turn (GPT 6.1 Sol, low effort) that message streamed its reply sentence and then kept streaming
inside the never-finished JSON at 64-80 deltas a second until the harness stopped it at 300 s, with no
reconnect, error or idle timeout. Every delta resets the silence watchdog (``turn_activity``), so only
the 1,800 s stretch limit would have ended it for a person. OpenAI documents the likely mode: a
constrained JSON response can emit whitespace without end. A healthy plan in the recorded runs showed
it once in milder form, a 3,015-character run of tabs between two operations, after which it finished.

``MessageWatch`` follows one streamed message and names the first ceiling it passes:

* ``WHITESPACE``: ``WHITESPACE_CHARS`` consecutive whitespace characters, or ``WHITESPACE_DELTAS``
  consecutive deltas of nothing else, outside any JSON string of a message that is JSON so far (leading
  whitespace included). Whitespace there adds no structure; the delta count bounds a stream of
  one-character tokens, which the character count alone would let run for minutes.
* ``SIZE``: ``MESSAGE_BYTES`` of the message (UTF-8).
* ``DURATION``: ``MESSAGE_SECONDS`` since the message's first delta, checked as deltas arrive (a stream
  that stops is the silence watchdog's).

Each ceiling sits far above every healthy message in the saved runs: 14,697 plan messages and 3,353
other agent messages from 5,492 Codex rollouts (gates, benchmarks, A/B, head-to-head, scene and
sculpt runs; GPT 6.1 Sol and GPT 6 Astra at low and medium effort). Measured maxima: 9,572 bytes
(ceiling 27x), 41 s from the turn's previous recorded event to the finished message, an upper bound on
streaming time (ceiling 14.6x), and the 3,015-character whitespace run (ceiling 10.9x; p99.9 is 2), which
took at most 671 deltas: its whole message was 671 output tokens (ceiling 12.2x).
A long written answer is a plan's ``reply``, so the size and time ceilings also leave room for prose
far longer than any chat reply: 256 KiB is about 64,000 tokens, ten minutes at the 70 tokens a second
recorded is about 42,000. ``doc/angelo/agent_execution.rst`` has the method and what the corpus lacks.

The watch only reads: ``codex_attempts`` stops the turn, resends it once when nothing reached the host,
and otherwise fails the request with ``FAILED``. Codex 0.163 exposes no per-turn output-token limit
(its Responses request has no ``max_output_tokens``), so the host is the only place to bound it.
"""

import time
from collections import OrderedDict

from .ui_text import n_

WHITESPACE_CHARS = 32 * 1024
WHITESPACE_DELTAS = 8192
MESSAGE_BYTES = 256 * 1024
MESSAGE_SECONDS = 600.0
WHITESPACE, SIZE, DURATION = "whitespace", "size", "duration"
# What the person reads; the stored text stays English (ui_text.notice).
RESENT = n_("The agent's reply kept streaming without finishing, far past the length of any plan, so Angelo "
            "stopped it and asked again. Nothing in that reply ran.")
FAILED = n_("Angelo stopped the agent's reply because it kept streaming without finishing, far past the length "
            "of any plan. Nothing in that reply ran. Edits made earlier in this request stay, and Undo reverts them; "
            "check the scene before you ask again.")
# What the model reads on the resend (never translated): what happened, nothing it must do differently.
_FACTS = {WHITESPACE: "%d characters of whitespace outside any string" % WHITESPACE_CHARS,
          SIZE: "%d bytes" % MESSAGE_BYTES, DURATION: "%d seconds of streaming" % MESSAGE_SECONDS}


def note(reason):
    """The resend's only input when Codex already holds the person's message (``codex_retry.NOTE``'s form)."""
    return ("[Host note] The host stopped the previous turn because its message kept streaming without finishing "
            "(%s), so nothing ran in that turn and the host kept none of that message. Anything the message above "
            "reports as already done still stands. Respond to that message now." % _FACTS.get(reason, reason))


class MessageWatch:
    """One streamed message: its size, its age and its whitespace outside JSON strings."""

    def __init__(self, now):
        self.started, self.bytes, self.run, self.blank = now, 0, 0, 0  # blank: deltas of whitespace only
        self.json, self.in_string, self.escape = None, False, False  # json: None until its first non-space

    def feed(self, delta, now):
        """The ceiling ``delta`` takes the message past (``WHITESPACE``, ``SIZE``, ``DURATION``), else None."""
        self.bytes += len(delta.encode("utf-8", "replace"))
        if self.json is not False and delta:
            self.blank = self.blank + 1 if self._scan(delta) and self.json is not False else 0
        if self.run >= WHITESPACE_CHARS or self.blank >= WHITESPACE_DELTAS:
            return WHITESPACE
        if self.bytes >= MESSAGE_BYTES:
            return SIZE
        return DURATION if now - self.started >= MESSAGE_SECONDS else None

    def _scan(self, delta):
        """Follow ``delta``; True when it held only whitespace outside strings."""
        blank = True
        for character in delta:
            if self.in_string:
                blank = False
                if self.escape:
                    self.escape = False
                elif character == "\\":
                    self.escape = True
                elif character == '"':
                    self.in_string = False
                continue
            if character.isspace():
                self.run += 1
                continue
            blank = False
            if self.json is None:
                self.json = character == "{"
                if not self.json:  # A plain answer: prose may hold whitespace as it likes.
                    self.run = 0
                    return False
            self.run = 0
            self.in_string = character == '"'
        return blank


class TurnWatch:
    """A turn's streamed messages, by item; ``reason`` is set once, by the first message past a ceiling.

    Codex streams one message at a time; the few most recent items are followed (``ITEMS``) so that
    interleaved items could not reset each other's watch either. ``feed`` runs on the RPC reader thread
    and ``reason`` is read by the turn's waiter (``turn_activity.wait``): one attribute written once.
    """

    ITEMS = 8

    def __init__(self, clock=time.monotonic):
        self._clock, self._watches, self.reason = clock, OrderedDict(), None

    def feed(self, item, delta):
        if self.reason is not None or not isinstance(delta, str):
            return
        now = self._clock()
        watch = self._watches.get(item)
        if watch is None:
            watch = self._watches[item] = MessageWatch(now)
            while len(self._watches) > self.ITEMS:
                self._watches.popitem(last=False)
        self.reason = watch.feed(delta, now)
