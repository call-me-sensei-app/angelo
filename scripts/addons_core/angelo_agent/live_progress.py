# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the live row says while a turn runs: its phase, the turn's clock and, when it goes quiet, why. No ``bpy``.

A person who is not a Blender expert sends a message and waits. An Opus plan can think for 50-100 s before
anything is on screen, and in 1.0.3 the live row then read "Thinking" with nothing moving, the same words as a
turn whose connection had dropped. This module turns facts the chat already has into three things:

* **Phase** (``phase``): what the turn is doing, in plain words. Before the API's first event: "Waiting for the
  model". While a step the host runs is in progress: "Working in Angelo", "Searching the web", "Consulting another
  model" or "Generating media". Otherwise the controller's status through ``chat_titles.live_label``.
* **Status line** (``elapsed``, ``tokens``): like a coding agent's status line, "• 1m 9s · 562 tokens · Thinking":
  a breathing dot, the turn's elapsed time, its output tokens so far (``usage_meter``) and the phase.
* **Note** (``note``): one muted line, or None. The model working without sharing its thoughts for
  ``QUIET_SECONDS`` says so. Nothing at all arriving from the runtime (its keep-alives included) for
  ``STALL_SECONDS`` is a stall: the chip turns amber and the line says how long, that it may still answer and that
  Stop ends the turn. The host's own give-up (``turn_activity``, 10 minutes of silence) is unchanged.

``Facts`` are read on the main thread from the controller and its provider (``facts``, ``turn_activity.signs``); nothing
here writes them, asks a model anything or changes what runs. Timestamps are ``time.monotonic`` seconds.

``thinking_detail`` replaces the old "The runtime did not supply a public reasoning summary." under a thinking
row whose runtime sent no summary (Claude Code without summarized thinking, or a Codex reasoning item the model
left unsummarized): how long the model worked and what that led to, from the rows after it.

``personal`` hides a home folder in any text the live row or a thinking row shows: a summary that quotes a path
never shows whose machine it is.
"""

import re
from collections import namedtuple

from .ui_text import iface_, n_, plural_

QUIET_SECONDS = 20.0   # No output from the model for this long: the quiet note.
STALL_SECONDS = 180.0  # Nothing at all from the runtime for this long: a stall (provisional until measured).
STALL, QUIET = "stall", "quiet"

# What the live row reads, gathered by ``facts`` (main thread).
# status: the controller's status text. started: when the turn began. accepted: the API's first event for this
# turn arrived (None: unknown, treated as accepted). last_model: the model's last output event. last_heard: the
# runtime's last event of any kind (keep-alives included; None: it sends none, use last_model). thinking: a
# thinking row of this turn is open. summarized: that open row has text. step: the newest running step row, or None.
# watched: the provider reports signs of life at all; without them the row never notes a quiet or stalled turn.
# thinking_age: seconds the open thinking row has been open (None: unknown).
FIELDS = "status started accepted last_model last_heard thinking summarized step watched thinking_age"  # i18n: field names
Facts = namedtuple("Facts", FIELDS)
# What one draw of the live row shows: the phase label (translated), the clock, the note (translated) and its kind.
Live = namedtuple("Live", "label clock note kind")  # i18n: field names

WAITING = n_("Waiting for the model")
_STEP_PHASES = {"web": n_("Searching the web"), "consult": n_("Consulting another model"),
                "generate": n_("Generating media"), "host": n_("Working in Angelo")}
_WEB_TOOLS = frozenset({"web_search", "WebSearch", "WebFetch"})
_QUIET_THINKING = n_("The model is working without sharing its thoughts. Large requests can take a few minutes.")
_STALLED = n_("Nothing has arrived from the model for {time}. It may still answer. Stop ends the turn.")


def elapsed(seconds):
    """Elapsed time as a status line reads it: "9s", "1m 9s", "1h 2m" (whole seconds; invalid reads "0s")."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        total = 0
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return iface_("{hours}h {minutes}m", hours=hours, minutes=minutes)
    if minutes:
        return iface_("{minutes}m {seconds}s", minutes=minutes, seconds=seconds)
    return iface_("{seconds}s", seconds=seconds)


_meter = None  # (usage_meter, usage_view) once found; False when this build has none.


def tokens(controller):
    """The running turn's output tokens so far as a translated phrase ("562 tokens"), or "" when not known.

    The count is ``usage_meter``'s (it owns the numbers, ``usage_view.turn_label`` the words); without that module,
    or before anything arrived, the status line simply shows no token part. The modules are looked up once.
    """
    global _meter
    if _meter is None:
        try:
            from . import usage_meter, usage_view
            _meter = (usage_meter, usage_view)
        except ImportError:
            _meter = False
    if not _meter:
        return ""
    try:
        count = _meter[0].turn_tokens(controller)
        return _meter[1].turn_label(count) if type(count) is int and count > 0 else ""
    except Exception:  # Decoration: a failing meter never takes the row down.
        return ""


def lead(controller, now):
    """The status line's changing part before the phase, as drawn: "1m 9s · 562 tokens · " (elapsed, then tokens
    when known), or "" without a clock. The chat's kept picture is keyed on it (``marble_frames.key``), so the text is
    laid out once a second, not on every frame."""
    begun = started(controller) if getattr(controller, "busy", False) else None
    if begun is None:
        return ""
    parts = [elapsed(now - begun)]
    counted = tokens(controller)
    if counted:
        parts.append(counted)
    return " · ".join(parts) + " · "


def step_phase(message):
    """The phase of a running step row: which kind of work the host or runtime is doing for it."""
    from .legacy_names import current_tool_name
    tool = current_tool_name(message.get("tool", "") or "")
    if tool in _WEB_TOOLS:
        return _STEP_PHASES["web"]
    if tool == "angelo_delegate":
        return _STEP_PHASES["consult"]
    if tool.startswith("angelo_studio_"):
        return _STEP_PHASES["generate"]
    return _STEP_PHASES["host"]


def phase(facts):
    """The live row's label, translated: a running step's phase, waiting for the model, or the status's label."""
    from .chat_titles import live_label
    from .claude_stream import STATUS_THINKING
    from .plan_progress import split
    base = split(facts.status)[0]
    if facts.step is not None and base in {STATUS_THINKING, "Staging native program"}:
        return iface_(step_phase(facts.step))
    if facts.accepted is False and base in {STATUS_THINKING, "Connecting"}:
        return iface_(WAITING)
    return live_label(facts.status)


def started(controller):
    """When the running request began (``time.monotonic``), or None: the clock belongs to the provider attempt that
    set it (``Controller._begin_metrics`` stores it with the request's generation). A request that never started one
    (an External CLI read or work, which bump the generation) has no clock, never an earlier turn's."""
    clock = getattr(controller, "turn_started", None)
    if not (isinstance(clock, tuple) and len(clock) == 2):
        return None
    generation, at = clock
    return at if generation == getattr(controller, "_request_generation", None) else None


def _latest(*times):
    values = [value for value in times if value is not None]
    return max(values) if values else None


def note(facts, now, stall_seconds=STALL_SECONDS, quiet_seconds=QUIET_SECONDS):
    """``(kind, text)`` for the line under the live row, or None.

    Only a model round in progress is judged (``turn_activity.signs`` gave timestamps). Between rounds, while a step
    runs or while the runtime relaunches, the host is the one working, so neither clock applies (``turn_activity``
    rests the same way). A stall needs evidence: a runtime that sends keep-alives (Claude Code) has sent nothing at
    all; one that sends none (Codex) is silent with no reasoning item open, since an unsummarized reasoning item is
    exactly a quiet model, never proof of a dead connection. The host's own give-up is unchanged.
    """
    if facts.step is not None or not facts.watched or facts.last_model is None:
        return None
    keepalives = facts.last_heard is not None
    heard = _latest(facts.last_heard if keepalives else facts.last_model, facts.started)
    if now - heard >= stall_seconds and (keepalives or not facts.thinking):
        return STALL, iface_(_STALLED, time=elapsed(now - heard))
    # Quiet means no visible thoughts for a while, measured on the open thinking row itself: Claude Code's default
    # display streams small thinking updates (model events, often without text), so the model clock never ages
    # during a think (PLAN-DISPLAY probe: the longest silence 8.3 s, no keep-alive pings at all).
    age = facts.thinking_age if facts.thinking_age is not None else now - _latest(facts.last_model, facts.started)
    if facts.thinking and not facts.summarized and age >= quiet_seconds:
        return QUIET, iface_(_QUIET_THINKING)
    return None


_STEP_STATUSES = frozenset({"running", "queued"})


def facts(controller):
    """The running turn's ``Facts``, read on the main thread; nothing is written.

    Only rows after the turn's request count (the request row's index, as ``marble_wait.progress`` reads it). A
    provider that keeps no ``TurnActivity`` (a test double) gives no timestamps: no note, never a false stall.
    """
    from .turn_activity import signs
    messages = (getattr(controller, "state", None) or {}).get("messages", ())
    anchor = vars(controller).get("_request_message_index")
    start = anchor + 1 if type(anchor) is int and 0 <= anchor < len(messages) else len(messages)
    thinking = summarized = False
    step = age = None
    for index in range(len(messages) - 1, start - 1, -1):
        message = messages[index]
        role = message.get("role")
        if role == "thinking" and message.get("status") == "running" and not thinking:
            thinking, summarized = True, bool((message.get("text") or "").strip())
            age = _thinking_age(controller, index)
        elif role == "activity" and step is None and message.get("status") in _STEP_STATUSES:
            step = message
    provider, forwarded = getattr(controller, "provider", None), getattr(controller, "turn_signs", None)
    try:   # A worker-hosted chat's mirror has no provider: its worker's forwarded events (worker_mirror).
        watched, found = (forwarded() if callable(forwarded) else signs(provider) if provider is not None
                          else (False, None))
    except Exception:  # A provider mid-teardown: the row shows its phase without a note.
        watched, found = False, None
    accepted, last_model, last_heard = found or (None, None, None)
    begun = started(controller) if getattr(controller, "busy", True) else None
    return Facts(str(controller.status), begun, accepted, last_model, last_heard, thinking, summarized, step,
                 watched, age)


def _thinking_age(controller, index):
    """Seconds the thinking row at ``index`` has been open, from the controller's own start times
    (``Controller._thinking_times``, ``time.perf_counter``), or None when they do not name it."""
    import time
    owners = vars(controller).get("_thinking_messages") or {}
    times = vars(controller).get("_thinking_times") or {}
    item = next((item for item, position in owners.items() if position == index), None)
    begun = times.get(item) if item is not None else None
    return time.perf_counter() - begun if isinstance(begun, (int, float)) else None


def live(facts, now):
    """Everything the live row's header draws at ``now`` (``Live``)."""
    found = note(facts, now)
    return Live(phase(facts), elapsed(now - facts.started) if facts.started is not None else "",
                found[1] if found else "", found[0] if found else None)


def next_tick(started, now):
    """Seconds until the clock shows its next whole second (the notes' thresholds are whole seconds too)."""
    elapsed = now - started
    return -elapsed if elapsed < 0 else 1.0 - (elapsed % 1.0)


# Thinking rows ----------------------------------------------------------------------------------------------------

def _then(following, time):
    """The whole sentence for a thinking row with no summary: how long the model worked and what that led to.

    One translatable sentence per outcome (never a fragment joined into another), so every language can order it.
    ``following``: the rows after it, nearest first; the first that is not thinking says what came next.
    """
    from . import activity_text
    from .claude_stream import PLAN_TOOL
    from .legacy_names import current_tool_name
    nxt = next((message for message in following if message.get("role") != "thinking"), None) or {}
    if nxt.get("role") == "activity" and current_tool_name(nxt.get("tool", "")) == PLAN_TOOL:
        operations = (nxt.get("arguments") or {}).get("operations")
        count = len(operations) if isinstance(operations, list) else 0
        if count:
            return plural_(  # One sentence per form: never split for line length.
                count,
                "The model worked for {time} without sharing its thoughts, then wrote a plan with {count} step.",
                "The model worked for {time} without sharing its thoughts, then wrote a plan with {count} steps.",
                time=time)
        return iface_("The model worked for {time} without sharing its thoughts, then wrote a plan.", time=time)
    if nxt.get("role") == "activity":
        return iface_("The model worked for {time} without sharing its thoughts, then started this step: {step}.",
                      time=time, step=activity_text.step_title(nxt))
    if nxt.get("role") == "assistant" and nxt.get("text"):
        return iface_("The model worked for {time} without sharing its thoughts, then answered.", time=time)
    return iface_("The model worked for {time} without sharing its thoughts.", time=time)


def thinking_detail(message, following=()):
    """The opened detail of a thinking row: its summary, made safe to show, or a plain account when there is none.

    ``following``: the transcript rows after it, nearest first.
    """
    text = personal(message.get("text") or "").strip()
    if text:
        return text
    if message.get("status") == "running":
        return iface_("The model is working without sharing its thoughts. This line updates when it moves on.")
    return _then(following, elapsed(max(1000, message.get("duration_ms") or 0) / 1000))


# Personal paths ---------------------------------------------------------------------------------------------------

# Home folders on macOS, Linux and Windows (either slash, or percent-encoded), any user, also under a mount
# ("/Volumes/<disk>/Users/<name>", "/mnt/c/Users/<name>"); a user name may hold spaces when a separator follows it
# (never a sentence's ". " and never ending in a space, so prose between two paths is not taken for a name). Also
# "~<name>/" and "/root". Web addresses are left whole: "https://<site>/Users/<name>" names a page, not a machine.
_SEP = r"(?:[/\\]|%2[Ff]|%5[Cc])"  # i18n: regular expression
_HOME = re.compile(
    r"(?P<url>\bhttps?://\S+)"
    r"|(?:\b[A-Za-z]:)?" + _SEP + r"(?:Users|home|Documents and Settings)" + _SEP
    + r"(?:(?:[^/\\\n\"'`<>|:,;!?%.]|\.(?!\s)){0,63}?[^/\\\s\"'`<>|:,;!?%](?=" + _SEP + r")|[^/\\\s\"'`<>|:%]*[^/\\\s\"'`<>|:%.,;!?)\]])"
    r"|(?<![\w~])~[A-Za-z_][\w.-]*(?=[/\\])"
    r"|(?<![\w.])/root(?=[/\\\s\"'`]|$)",
    re.IGNORECASE)


def personal(text):
    """``text`` with every home folder shown as "~" ("/Users/<name>/x.blend" reads "~/x.blend")."""
    return _HOME.sub(lambda match: match.group("url") or "~", text) if text else text
