# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``RailTurns``: hosted worker chats driven from a window suite's timer, never waiting in it (design A′).

``HostedWorkerChat`` waits in loops of its own; a window suite (``test_blender_session_rail``) runs in Blender's timer
and must return at once, while the window's own timer pumps each chat's mirror as it does for a person. Here:

* a request is the model's tool call in a turn of its own (the stand-in plays it, ``hosted_codex_stand_in``); the turn
  then holds (still working, as a model's turn goes on) until the suite ends it or makes its next request;
* ``tick`` (the suite's timer) ends a held turn and sends the next once its mirror shows it idle, retrying a send the
  worker did not take;
* what the lead sees is recorded when its mirror applies it, inside the window's own pump of that mirror: a result
  (the request's callback runs there), a card, the turn's end. A suite's "drawn within a second" checks are timed from
  those moments, never from the suite's later poll.

No provider or paid call.
"""

import time

from hosted_worker_chat import HostedWorkerChat, install_stand_in, stand_in

RESEND_SECONDS = 5.0   # A send whose turn never reached the model by then is sent again (at most RESENDS times).
RESENDS = 3


class RailTurns:
    def __init__(self, folder):
        install_stand_in(folder)
        self.entries = {}

    def add(self, name, chat):
        """Drive ``chat`` (a mirror Angelo made and started) as ``name``."""
        driver = HostedWorkerChat.of(chat)
        entry = self.entries[name] = {"chat": chat, "driver": driver, "queue": [], "hold": None, "out": [],
                                      "count": 0, "sent": None, "idle_at": None, "card_at": None, "was_busy": False}
        pump = chat.pump_events

        def pumped(*args, **kwargs):   # The window's own pump of this mirror: what it applied, when.
            answer = pump(*args, **kwargs)
            self._observe(entry)
            return answer
        chat.pump_events = pumped
        return driver

    def request(self, name, tool, arguments, callback, reply=None):
        """The model calls ``tool`` in a new turn, which then holds; ``callback(result)`` once the lead has it. ``reply``:
        the turn's closing message, said once it is ended (``end_turn``)."""
        entry = self.entries[name]
        entry["count"] += 1
        call_id, hold = "qa-%d" % entry["count"], "turn-%d" % entry["count"]
        steps = [{"call": {"id": call_id, "tool": tool, "arguments": arguments}}, {"hold": hold}]
        entry["queue"].append(("Please run %s." % tool, steps + ([{"reply": reply}] if reply else []), hold, 0))
        entry["out"].append((call_id, callback))

    def hold_turn(self, name):
        """A turn with no call that keeps working until ``end_turn``, as a model's does."""
        entry = self.entries[name]
        entry["count"] += 1
        hold = "turn-%d" % entry["count"]
        entry["queue"].append(("Keep going.", [{"hold": hold}], hold, 0))

    def end_turn(self, name):
        """End the held turn (True when one was held); its end is recorded once the mirror shows it (``idle_at``)."""
        entry = self.entries[name]
        if entry["hold"] is None:
            return False
        entry["driver"].release(entry["hold"])
        entry["hold"], entry["idle_at"] = None, None
        return True

    def idle_at(self, name):
        return self.entries[name]["idle_at"]

    def card_at(self, name):
        return self.entries[name]["card_at"]

    def tick(self):
        """The suite's timer: never waits."""
        for name, entry in self.entries.items():
            chat, driver = entry["chat"], entry["driver"]
            self._observe(entry)
            sent = entry["sent"]
            if sent is not None:
                marker, at, item = sent
                if any(marker in str(row.get("text")) for row in driver.model_log("turn_started")):
                    entry["sent"] = None
                elif not chat.busy and time.monotonic() - at > RESEND_SECONDS and item[3] < RESENDS:
                    entry["queue"].insert(0, item[:3] + (item[3] + 1,))   # Came back unsent: sent again.
                    entry["sent"], entry["hold"] = None, None
                continue
            if not entry["queue"]:
                continue
            if entry["hold"] is not None:
                self.end_turn(name)   # Its next request: the held turn ends first, as a model's turn would.
                continue
            if chat.busy:
                continue
            text, steps, hold, _tries = item = entry["queue"].pop(0)
            driver.queue_turn(steps)
            driver._sends += 1
            marker = "%s #%d" % (stand_in.tag(driver.session), driver._sends)
            try:
                chat.send("%s %s" % (text, marker))
            except Exception:   # noqa: BLE001 - refused at once (already running): sent again next tick.
                entry["queue"].insert(0, item)
                continue
            entry["sent"], entry["hold"], entry["was_busy"] = (marker, time.monotonic(), item), hold, True

    def _observe(self, entry):
        chat, driver, now = entry["chat"], entry["driver"], time.monotonic()
        for call_id, callback in list(entry["out"]):
            result = driver.result_of(call_id)
            if result is not None:
                entry["out"].remove((call_id, callback))
                callback(result)
        if entry["card_at"] is None and any(getattr(call, "approval", None) for call in chat.pending.values()):
            entry["card_at"] = now
        elif not any(getattr(call, "approval", None) for call in chat.pending.values()):
            entry["card_at"] = None
        if entry["was_busy"] and not chat.busy and entry["hold"] is None and entry["idle_at"] is None:
            entry["idle_at"], entry["was_busy"] = now, False
