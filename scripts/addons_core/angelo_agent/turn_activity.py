# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long the host waits for a provider turn: for silence, not for a flat time.

Both providers used to give up after 600 s whatever the runtime was doing. A
round that had been streaming its reasoning for ten minutes ended exactly like
one that never answered, and a Codex turn lost the minutes a person spent
reading a script it had asked to run. The host now gives up on a turn only
when

* nothing has come from the model for ``IDLE_SECONDS`` (a hung process or
  connection; the same 600 s, applied to silence), or
* one stretch of model work has lasted ``STRETCH_SECONDS`` without a result
  (a runaway response; Stop remains available the whole time).

While the host itself is working for the turn (a tool call waiting for
approval or running) neither clock runs, and the next stretch starts when it
hands back. Neither runs during a wait the runtime announced either (``advise``:
a provider's Retry-After the runtime waits out, which can be many minutes); both
restart at its deadline, so a runtime that is silent after the wait is still
given up on. A turn's announced waits rest the clocks for at most
``ADVISED_SECONDS`` in all, so notices that never stop cannot keep a hung turn
open. Effort is not an input: a stream of events says directly what effort
could only suggest.
"""

import math
import threading
import time

IDLE_SECONDS = 600
STRETCH_SECONDS = 1800
ADVISED_SECONDS = 3600   # One turn's announced provider waits, in all; a longer one ends the turn (ADVISED).
STOP_SECONDS = 30        # After Stop, the host ends a turn the runtime has not ended this long after (STOPPED).
POLL_SECONDS = 1.0
SILENT, LONG, CUT, ADVISED, NETWORK, STOPPED = "silent", "long", "cut", "advised", "network", "stopped"
PROVIDER_WAIT, NETWORK_WAIT = "provider", "network"   # What an announced wait is for (``advise``).
_MAX_MS = 10 ** 15   # Milliseconds past this are no wait at all (and would overflow a float).


def announced_seconds(delay_ms):
    """Seconds of a wait a runtime notice names in milliseconds, or None unless it is a finite number above zero.
    Never raises: a huge integer is no wait, not an overflow on the reader thread."""
    seconds = _positive(delay_ms, _MAX_MS)
    return None if seconds is None else seconds / 1000.0


def _positive(value, below):
    """``value`` as a float when it is a number (never a bool) with ``0 < value < below``, else None; never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, int):
        return float(value) if 0 < value < below else None
    return value if math.isfinite(value) and 0 < value < below else None


class TurnActivity:
    """When a turn last showed life, and when its current stretch of model work began.

    ``heard`` is when the runtime last sent anything at all, its keep-alives included (``hear``); the chat's live
    row reads it, with ``last``, to tell a quiet model from a silent connection (``live_progress``). Reading the
    two floats from the main thread needs no lock.
    """

    def __init__(self, clock=time.monotonic):
        self._clock, self._lock = clock, threading.Lock()
        self.last = self.stretch = self.heard = clock()
        self._until, self._advised = None, 0.0   # An announced wait's deadline; the provider seconds granted so far.
        self._kind = None       # What the current or last announced wait is for (PROVIDER_WAIT, NETWORK_WAIT).
        self._deadline = None   # The latest announced wait's deadline, kept after it passes (silent_since_wait).
        self.too_long = None    # A provider wait longer than the turn's allowance (seconds): the turn ends (ADVISED).
        self.too_long_in_all = False   # That wait passed what was left after earlier waits, not the allowance alone.
        self._offline_since = None     # The first network wait of the current stretch (offline_seconds).
        self._stopped = None    # When Stop reached the turn's waiter (stop).

    def hear(self):
        """The runtime sent an event of any kind (a model event, a keep-alive or its own notice)."""
        self.heard = self._clock()

    def touch(self, answered=True):
        """The runtime sent something. ``answered``: it was not the runtime's own notice (a retry or network wait),
        so an offline spell is over (``offline_seconds`` counts the next one afresh)."""
        with self._lock:
            self.last = self.heard = self._clock()
            if answered:
                self._offline_since = None

    def rest(self):
        """The host is working for the turn, or just handed back: a new stretch begins."""
        with self._lock:
            self.last = self.stretch = self.heard = self._clock()
            self._offline_since = None

    def advise(self, seconds, kind=PROVIDER_WAIT):
        """The runtime announced it waits ``seconds`` before sending the request again.

        A provider's wait (its Retry-After, or the runtime's backoff) rests both clocks until its deadline, and both
        restart there, as for a new request; a later, shorter notice never cuts it short. A wait longer than what is
        left of the turn's ``ADVISED_SECONDS`` is not waited for at all: ``too_long`` records it and the turn ends at
        once (``ADVISED``), saying how long the provider asked for, never a countdown that reaches a false zero. A
        network wait (the runtime waiting to reconnect) rests the silence clock only: it is not model work, and the
        stretch keeps counting, so a turn offline for long ends as ``NETWORK``. Anything but a finite number of
        seconds above zero is ignored.
        """
        seconds = _positive(seconds, _MAX_MS / 1000)
        if seconds is None:
            return
        with self._lock:
            if self._stopped is not None:
                return           # After Stop, no wait holds the turn open.
            now = self._clock()
            end = now + seconds
            if kind == NETWORK_WAIT:
                if self._offline_since is None:
                    self._offline_since = now
                if self._until is None or end > self._until:
                    self._until = self._deadline = end
                    self._kind = NETWORK_WAIT
                return
            start = max(now, self._until if self._kind == PROVIDER_WAIT and self._until else now)
            self._offline_since = None   # The provider answers again: whatever it says, it was reached.
            if end - start > ADVISED_SECONDS - self._advised:
                self.too_long, self.too_long_in_all = seconds, self._advised > 0 and end - start <= ADVISED_SECONDS
                return
            if end > start:
                self._advised += end - start
                self._until = self._deadline = end
                self._kind = PROVIDER_WAIT

    def offline_seconds(self):
        """Seconds since the stretch's first network wait, or None when it had none."""
        with self._lock:
            return None if self._offline_since is None else self._clock() - self._offline_since

    def stop(self):
        """Stop reached the turn: no announced wait holds it open any more, and the host gives the runtime
        ``STOP_SECONDS`` to end the turn before it ends it itself (``STOPPED``)."""
        with self._lock:
            if self._stopped is None:
                self._stopped, self._until = self._clock(), None

    def waiting(self):
        """``(seconds left, kind)`` of the wait the runtime announced, or None when there is none."""
        with self._lock:
            now = self._clock()
            return (self._until - now, self._kind) if self._until is not None and self._until > now else None

    def silent_since_wait(self):
        """Whether nothing came from the runtime since the last announced wait ended (or there was none)."""
        with self._lock:
            return self._deadline is not None and self.last <= self._deadline

    def show_wait(self, emit, shown):
        """Send the live row's status for the announced wait through ``emit`` when it changes; returns what is shown.

        Counted down during a provider's wait, the network notice during a network wait, and once a wait has passed
        "retrying" only while nothing has come since. The check and the send happen under the turn's lock, so an
        event that arrives meanwhile (``touch``) always lands after it and its own status ("Thinking") wins.
        """
        from .codex_retry import NETWORK, RETRYING, waiting as counted
        with self._lock:
            if self._stopped is not None:
                return shown     # Never over "Stopping".
            now = self._clock()
            if self._until is not None and self._until > now:
                status = NETWORK if self._kind == NETWORK_WAIT else counted(self._until - now)
            elif shown is not None and self._deadline is not None and self.last <= self._deadline:
                status = RETRYING
            else:
                return None if shown is not None else shown
            if status != shown:
                emit(status)
            return None if status == RETRYING else status

    def overdue(self, idle=None, stretch=None):
        """``SILENT``, ``LONG``, ``NETWORK``, ``ADVISED`` or ``STOPPED`` when the turn should be given up, else None."""
        idle = IDLE_SECONDS if idle is None else idle
        stretch = STRETCH_SECONDS if stretch is None else stretch
        with self._lock:
            now = self._clock()
            if self._stopped is not None and now - self._stopped >= STOP_SECONDS:
                return STOPPED
            if self.too_long is not None:
                return ADVISED
            if self._until is not None:
                if now < self._until:
                    return None          # The runtime waits as it said it would.
                # The wait is over. Silence counts from its deadline; a provider's wait starts a new stretch.
                self.last = max(self.last, self._until)
                if self._kind == PROVIDER_WAIT:
                    self.stretch = max(self.stretch, self._until)
                self._until = None
            if now - self.last >= idle:
                return SILENT
            if now - self.stretch >= stretch:
                offline = self._kind == NETWORK_WAIT and self._deadline is not None and self.last <= self._deadline
                return NETWORK if offline else LONG
            return None


def signs(provider):
    """``(watched, signs)`` of ``provider``'s running turn for the chat's live row (``live_progress``).

    ``signs`` is ``(accepted, last model event, last event of any kind)``, or None between turns; ``watched`` says
    the provider keeps a ``TurnActivity`` at all. Read on the main thread without locks: attribute reads of floats
    and flags the provider threads write. Claude's turn lives on its transport; Codex keeps one per attempt
    (``codex_attempts``) and sends no keep-alives, so its last event is its last model event.
    """
    if "_transport" in vars(provider):  # Claude: None until its runtime starts.
        turn = getattr(provider._transport, "turn", None)
        if turn is None or turn.done.is_set():
            return True, None
        return True, (turn.accepted, turn.activity.last, turn.activity.heard)
    activity = getattr(provider, "_activity", None)  # Codex
    if not isinstance(activity, TurnActivity):
        return False, None
    return True, (getattr(provider, "_input_accepted", None), activity.last, None)


def wait(done, activity, *, busy=None, cut=None, waiting=None, stopped=None):
    """Block until ``done`` is set. Returns None, or why the host stopped waiting.

    ``busy()`` says whether the host is working for the turn (Codex tool calls
    arrive inside it). ``cut()`` says the turn's stream passed a ceiling of its
    own (``plan_runaway``): ``CUT``, checked first, since a stream that keeps
    going resets both clocks. ``waiting(status)`` receives the live row's status
    during a wait the runtime announced (``TurnActivity.show_wait``). ``stopped()``
    says Stop reached the turn: from then on no wait holds it open, and the host
    ends it itself ``STOP_SECONDS`` later if the runtime has not (``STOPPED``).
    The limits are read on every poll, so a change takes effect at once.
    """
    shown = None
    while not done.wait(POLL_SECONDS):
        if callable(cut) and cut():
            return CUT
        if callable(stopped) and stopped():
            activity.stop()
        elif callable(waiting):
            shown = activity.show_wait(waiting, shown)
        if callable(busy) and busy():
            activity.rest()
            continue
        reason = activity.overdue()
        if reason:
            return reason
    return None


def _minutes(seconds):
    """``seconds`` as whole minutes, rounded up: "1 minute", "61 minutes"."""
    count = max(1, math.ceil(seconds / 60))
    return "%d minute%s" % (count, "" if count == 1 else "s")


def problem(runtime, reason, advice, activity=None):
    """The error a person reads when the host gave up on a turn; ``advice`` says what state that leaves."""
    if reason == SILENT:
        return "%s turn timed out: nothing came from the model for %d minutes. %s" % (runtime, IDLE_SECONDS // 60, advice)
    if reason == ADVISED:
        if getattr(activity, "too_long_in_all", False):
            return ("%s turn stopped: the provider asked to wait again, and this turn's waits would pass %s in all, "
                    "longer than Angelo waits for one request. Send the message again later. %s"
                    % (runtime, _minutes(ADVISED_SECONDS), advice))
        return ("%s turn stopped: the provider asked to wait about %s before it answers, longer than Angelo waits for "
                "one request (%s). Send the message again later. %s"
                % (runtime, _minutes(getattr(activity, "too_long", 0) or 0), _minutes(ADVISED_SECONDS), advice))
    if reason == NETWORK:
        offline = activity.offline_seconds() if activity is not None else None
        since = " for about %s" % _minutes(offline) if offline else ""
        return ("%s turn stopped: the provider could not be reached%s. Check the internet connection, then send the "
                "message again. %s" % (runtime, since, advice))
    return ("%s turn timed out: one stretch of model work ran for %d minutes without a result. %s"
            % (runtime, STRETCH_SECONDS // 60, advice))
