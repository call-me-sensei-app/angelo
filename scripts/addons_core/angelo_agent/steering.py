# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Messages the person sends while a turn runs: steered into it at a safe step, or queued for after it.

A message sent while the chat's turn runs is never refused. It waits here as a pending message, drawn in a tray
between the transcript and the message box (``chat_pending``), in the order the person sent it:

* **steer** ("Now"): the running request takes it at its next safe step, a moment the host owns between model steps,
  when nothing the model asked for is still running (``steering_delivery``). It counts as delivered only once the
  model has read it: Claude answered the round that carried it; Codex showed it as the turn's user message and the
  model went on (an accepted ``turn/steer`` can still be dropped by an interrupt). Delivered, it is a user row where
  the model read it (``"steer": True``) and leaves this list;
* **queue** ("After this turn"): it waits for the turn to end.

When a turn ends normally, every waiting message goes out as one request, oldest first (``next_due``): a steer the
turn never read goes with them, first. After Stop, a failed turn or one that never started, nothing is sent by
itself: the messages are **paused** until the person sends, edits or removes them. Nothing the person typed is lost
or sent twice by Angelo: a message leaves this list only once a turn read it (``delivered``), once it reached the
agent's own conversation by the turn's end (``turn_ended``: the agent has it, so it is never sent again), or once a
request carrying it started (``sent``); one whose send never reached the agent comes back paused (``not_sent``).

A pending message is the person's own words for the model, nothing more: it never answers an approval (only the
Approve and Decline controls do) and carries no snapshot or Undo step of its own. Pending messages belong to their
chat: saved with it (``records``, ``chat_pending.save``) and back paused after a relaunch, never moved into another
chat. No bpy; every method is safe from any thread.
"""

import itertools
import threading
from dataclasses import dataclass, field

STEER, QUEUE = "steer", "queue"
WAITING, DELIVERING, PAUSED, SENDING = "waiting", "delivering", "paused", "sending"
EDITABLE = (WAITING, PAUSED)   # The person can switch, edit or remove it; otherwise a runtime or a send has it.
MAX_PENDING = 32   # Far above what a person types during one turn; a runaway caller cannot grow the list without end.
MAX_TEXT = 32000   # The composer's own limit (Controller.send).
MAX_REFERENCES = 16

_ids = itertools.count(1)


@dataclass(eq=False)
class Pending:
    text: str
    references: list = field(default_factory=list)   # Attachments taken from the message box with the message.
    mode: str = STEER
    phase: str = WAITING
    reason: object = ""     # Why it is paused: a key of chat_pending.PAUSED_TEXT, or a refused send's notice.
    accepted: bool = False  # Codex took the turn/steer; not read until its user message and model output follow.
    shown: bool = False     # Its row is in the transcript (a Claude round wrote it); withdrawn if never answered.
    seen: bool = False      # Codex showed it as the turn's user message; read once the model's output follows.
    carried: bool = False   # In a host-written Codex turn's own input: seen when Codex echoes that turn's message.
    label: str = ""         # Angelo's own follow-up (``host``): the n_() recipe its bubble shows instead of its text.
    send_options: dict = field(default_factory=dict)   # Angelo's own follow-up: how its request is sent.
    run_chosen_tools: bool = True   # Exact retry: no unrelated composer choices (tools, or its draft goal).
    separate: bool = False   # An exact retry owns one whole request, never a steer or a joined pending message.
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def host(self):
        """Angelo's own follow-up (a stop card's Continue, goal mode's next step), queued behind the person's words:
        always its own request, never steered, never edited, never saved."""
        return bool(self.label)

    @property
    def can_steer(self):
        """Steering carries text only: a message with pictures waits for the turn to end."""
        return not self.host and not self.separate and not self.references and bool(self.text.strip())

    @property
    def client_id(self):
        """The id Codex echoes on this message's user item (``turn/steer`` clientUserMessageId)."""
        return "angelo-steer-%d" % self.id


def _fresh(item):
    """No delivery evidence left over from an earlier try: every way out of (or into) delivering clears it, so a
    later echo can never mark an item read that this try never carried."""
    item.accepted = item.shown = item.seen = item.carried = False


class Inbox:
    """One chat's pending messages, oldest first."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []
        self.version = 0   # Bumped on every change: the chat redraws and saves its pending file.

    def _changed(self):
        self.version += 1

    def items(self):
        with self._lock:
            return list(self._items)

    def __len__(self):
        with self._lock:
            return len(self._items)

    def _find(self, item_id):
        return next((item for item in self._items if item.id == item_id), None)

    # The person ---------------------------------------------------------------------------------------------------

    def hold(self, text, references=(), *, steerable=True, label="", send_options=None,
             run_chosen_tools=True, separate=False):
        """Keep a message sent during a turn: a steer when the turn can take one, else queued. ``label``: Angelo's
        own follow-up (``Pending.host``), sent as ``send_options`` say."""
        if type(run_chosen_tools) is not bool or type(separate) is not bool:
            raise TypeError("Pending request policy must be boolean")
        references = [dict(r) for r in references][:MAX_REFERENCES]
        if not isinstance(text, str) or len(text) > MAX_TEXT or (not text.strip() and not references):
            raise ValueError("A pending message is 1-32000 characters or a picture")
        item = Pending(text, references, label=label, send_options=dict(send_options or {}),
                       run_chosen_tools=run_chosen_tools, separate=separate)
        item.mode = STEER if steerable and item.can_steer else QUEUE
        with self._lock:
            if len(self._items) >= MAX_PENDING:
                raise OverflowError("Too many messages are waiting")
            self._items.append(item)
            self._changed()
        return item

    def set_mode(self, item_id, mode):
        """Switch a waiting or paused message between Now and After this turn; False when it can't be."""
        with self._lock:
            item = self._find(item_id)
            if item is None or item.phase not in EDITABLE or mode not in (STEER, QUEUE):
                return False
            if mode == STEER and not item.can_steer:
                return False
            item.mode = mode
            self._changed()
            return True

    def take(self, item_id):
        """Remove a message for Edit or Remove; None while a runtime or a send has it."""
        with self._lock:
            item = self._find(item_id)
            if item is None or item.phase not in EDITABLE:
                return None
            self._items.remove(item)
            self._changed()
            return item

    def take_newest(self):
        """The newest message the person can still change (Up arrow in an empty message box), removed; or None."""
        with self._lock:
            for item in reversed(self._items):
                if item.phase in EDITABLE and not item.host:
                    self._items.remove(item)
                    self._changed()
                    return item
        return None

    def resume(self, item_id):
        """Send on a paused message: it waits again (steered if a turn runs and it is a steer, else sent next)."""
        with self._lock:
            item = self._find(item_id)
            if item is None or item.phase != PAUSED:
                return False
            item.phase, item.reason = WAITING, ""
            self._changed()
            return True

    # A running request's safe step (provider threads) ------------------------------------------------------------

    def take_steers(self):
        """Every waiting steer, oldest first, now being delivered (never handed out twice)."""
        with self._lock:
            taken = [item for item in self._items if item.phase == WAITING and item.mode == STEER and item.can_steer]
            for item in taken:
                item.phase = DELIVERING
                _fresh(item)
            if taken:
                self._changed()
            return taken

    def mark(self, items, **flags):
        """``accepted``/``shown`` on messages still being delivered; returns those it marked."""
        with self._lock:
            marked = [item for item in items if item in self._items and item.phase == DELIVERING]
            for item in marked:
                for name, value in flags.items():
                    setattr(item, name, value)
            if marked:
                self._changed()
            return marked

    def delivering(self, client_id=None):
        """The messages a runtime has now (or the one with Codex's ``client_id``)."""
        with self._lock:
            return [item for item in self._items if item.phase == DELIVERING
                    and (client_id is None or item.client_id == client_id)]

    def delivered(self, items):
        """The model read them: they are rows of the running request now, no longer pending."""
        with self._lock:
            gone = [item for item in items if item in self._items and item.phase == DELIVERING]
            for item in gone:
                self._items.remove(item)
            if gone:
                self._changed()
            return gone

    def returned(self, items, paused_reason=None):
        """Not read: back in their places, waiting; or paused with ``paused_reason`` (the turn was stopped)."""
        with self._lock:
            back = [item for item in items if item in self._items and item.phase == DELIVERING]
            for item in back:
                item.phase, item.reason = (PAUSED if paused_reason else WAITING), paused_reason or ""
                _fresh(item)
            if back:
                self._changed()
            return back

    # The turn's end and the sends after it (Blender's main thread) -----------------------------------------------

    def turn_ended(self, normal, reason):
        """The turn's end: what a runtime still had either reached the agent or comes back; after an abnormal end
        (``reason``) nothing waits to be sent by itself.

        A message reached the agent only on evidence that the agent has it: a Claude round that carried it was
        written (``shown``), or Codex echoed it as a user message of the turn (``seen``; Codex echoes what it
        drains or records, and drops undrained input on any interrupt). Those leave the list and are never sent
        again; the ones without a row yet are returned, for their rows. Every other one comes back in its place,
        an accepted ``turn/steer`` never echoed included. When Angelo closed (``relaunch``) a Codex message seen
        but never answered comes back too (paused): the person decides whether to send it again.
        """
        with self._lock:
            reached, back = [], []
            for item in (item for item in self._items if item.phase == DELIVERING):
                (reached if item.shown or (item.seen and reason != "relaunch") else back).append(item)
            for item in back:
                item.phase = WAITING
                _fresh(item)
            self._items = [item for item in self._items if item not in reached]
            if not normal:
                for item in self._items:
                    if item.phase == WAITING:
                        item.phase, item.reason = PAUSED, reason
            if reached or back or not normal:
                self._changed()
            return [item for item in reached if not item.shown]

    def next_due(self):
        """Every waiting message of the person, oldest first, now being sent as one request (up to Angelo's own
        follow-up, which goes alone); [] when none waits or a send runs."""
        with self._lock:
            if any(item.phase == SENDING for item in self._items):
                return []
            due = []
            for item in (item for item in self._items if item.phase == WAITING):
                if (item.host or item.separate or (due and (due[-1].host or due[-1].separate
                                                          or item.run_chosen_tools != due[-1].run_chosen_tools))):
                    if not due:
                        due.append(item)
                    break
                due.append(item)
            for item in due:
                item.phase = SENDING
            if due:
                self._changed()
            return due

    def sent(self, items):
        """Their request started (its row carries them): they leave the list."""
        with self._lock:
            self._items = [item for item in self._items if item not in items]
            self._changed()

    def not_sent(self, items, reason):
        """Their send never reached the agent (refused at once, or later by a worker's lane, or a request that never
        started): back, paused, saying why. One still listed stays in its place; one already removed as sent comes
        back first, in its order. Nothing the person typed is lost."""
        with self._lock:
            back = [item for item in items if item not in self._items]
            for item in items:
                item.phase, item.reason = PAUSED, reason
                _fresh(item)
            self._items[:0] = back   # Never truncated: these are the person's words coming back.
            self._changed()

    restore = not_sent   # The controller's not_sent event (a request that never reached the agent).

    def has_due(self):
        """Whether a message waits to be sent, or is being sent, after the turn (paused ones wait for the person)."""
        with self._lock:
            return any(item.phase in (WAITING, SENDING, DELIVERING) for item in self._items)

    def waiting_steers(self):
        with self._lock:
            return any(item.phase == WAITING and item.mode == STEER and item.can_steer for item in self._items)

    # Saved with the chat -------------------------------------------------------------------------------------------

    def records(self):
        """What a relaunch gets back: each message's words, pictures and mode (never a runtime's state)."""
        with self._lock:
            return [{"text": item.text, "references": item.references, "mode": item.mode,
                     **({"run_chosen_tools": False} if not item.run_chosen_tools else {}),
                     **({"separate": True} if item.separate else {})}
                    for item in self._items if not item.host]   # Angelo's follow-ups belong to this session only.

    def load(self, records, reason):
        """A relaunch's pending messages: back paused, never sent by themselves. Malformed entries are skipped."""
        kept = []
        for record in records if isinstance(records, list) else ():
            if not isinstance(record, dict):
                continue
            text, references, mode = record.get("text"), record.get("references", []), record.get("mode")
            if (not isinstance(text, str) or len(text) > MAX_TEXT or not isinstance(references, list)
                    or not all(isinstance(r, dict) for r in references) or (not text.strip() and not references)):
                continue
            run_tools, separate = record.get("run_chosen_tools", True), record.get("separate", False)
            if type(run_tools) is not bool or type(separate) is not bool:
                continue
            item = Pending(text, [dict(r) for r in references][:MAX_REFERENCES], phase=PAUSED, reason=reason,
                           run_chosen_tools=run_tools, separate=separate)
            item.mode = STEER if mode == STEER and item.can_steer else QUEUE
            kept.append(item)
        with self._lock:
            self._items = kept   # Never cut: the limit is for new messages (``hold``), not the person's saved words.
            self._changed()
