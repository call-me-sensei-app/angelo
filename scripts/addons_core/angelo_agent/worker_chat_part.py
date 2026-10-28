# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosting worker's part file (design A′, ``worker_chat_host``): saved as the hosted chats left it.

Once nothing is under way, the part is saved when it has unsaved changes, or when a script saved the open file as
another file (``save_due``, set by ``worker_chat_executor``): the open file is then the part again, as on the lane
path (``worker_script.on_part``). A worker whose part could not be loaded again after a script loaded another file
(``WorkerHost.ended``) never saves: another file's data is never saved as the part. Each save tells the lead
(``part_saved`` with the layout when it changed and the saved version's object count, ``worker_part_counts.
saved_facts``, or ``part_save_failed``, tried again ``SAVE_RETRY_SECONDS`` later).

A turn's end reaches the lead after its part's save (``TurnEnds``): a hosted chat's ``finished`` is emitted on its
provider's thread, before the worker's main thread has saved what the turn left, so the lead's live view would show
the part's last saved version for a moment after the turn ended (Mac 2, live_view@hosted: a deleted object). From a
``finished`` on, every frame waits, in order, until the main thread's next save step has run (``cut`` before it,
``release`` after, once each hosted chat applied its events: a turn end the lead sees from a save step is over in
the worker too, so a Send right after it never meets the D3 gap); the save's own frames go at once, so
``part_saved`` precedes the turn's end. A main thread that
runs no save step within ``END_WAIT`` (an edit, a save or a script holds it: the Stop of U8) never keeps the lead's
mirror busy: the held frames then go anyway, in order, from a timer's thread. Nothing waits on a lock held for long:
a frame is only appended or handed to the outbox, whose own thread sends it.
"""

import threading
import time

END_WAIT = 1.0   # Seconds a turn's end waits for the save step: ten idle pumps (``worker_chat_host.PUMP_SECONDS``).
SAVE_KINDS = frozenset({"part_saved", "part_save_failed"})   # The save step's own frames: never held behind it.

SAVE_RETRY_SECONDS = 1.0   # After a failed save of the part, the next attempt (the lead is told each time).


def ended(chats):
    """Whether the worker ends after its part was lost (``worker_script.contain``): it saves nothing."""
    return getattr(chats.host, "ended", False) is True


def unsaved(chats):
    """Whether the part has changes its file lacks (``chats`` a ``WorkerChatHost``)."""
    return not ended(chats) and (getattr(chats.host.bpy.data, "is_dirty", False) or chats.save_due)


def save_when_idle(chats):
    """The part as the hosted chats left it, once nothing is under way (as an approved edit was saved)."""
    if not chats.chats or not unsaved(chats):
        return
    if any(chat.busy_with_calls() for chat in chats.chats.values()) or time.monotonic() < chats._save_after:
        return
    save_now(chats)


def save_now(chats):
    """Save the part now if it has unsaved changes (a merge hold's first step, ``worker_chat_holds``): None once the
    file holds every edit, else the failure, which the lead also hears (the next try a second later)."""
    if ended(chats):
        return "The worker could not load its part again, so it saves nothing"
    if not unsaved(chats):
        return None
    try:
        path = chats.host.save()
    except Exception as error:  # noqa: BLE001 - the part stays unsaved; the lead hears it, and it is tried again.
        chats._save_after = time.monotonic() + SAVE_RETRY_SECONDS
        failure = "%s: %s" % (type(error).__name__, error)
        chats.outbox.put({"session": None, "kind": "part_save_failed", "t": time.time(), "data": {"error": failure}})
        return failure
    chats.save_due = False
    layout = chats.host.layout()
    changed = layout != chats._layout
    chats._layout = layout if changed else chats._layout
    from .worker_part_counts import saved_facts   # Its count for this version: the lead's status knows it at once.
    chats.outbox.put({"session": None, "kind": "part_saved", "t": time.time(),
                      "data": {"path": path, "revision": chats.host.revision, "layout": layout if changed else None,
                               "part": saved_facts(chats.host.bpy, path)}})
    return None


class TurnEnds:
    """The worker's outbox, holding the frames from a turn's ``finished`` on until the save step after it ran."""

    def __init__(self, outbox):
        self.outbox, self._lock, self._held, self._holds = outbox, threading.Lock(), None, 0

    def __getattr__(self, name):   # ``flush``, ``broken`` and the rest are the outbox's own.
        return getattr(self.outbox, name)

    def put(self, event):
        """Any thread: ``event`` goes to the outbox now, or waits behind a turn's end that waits."""
        with self._lock:
            kind = event.get("kind") if isinstance(event, dict) else None
            if self._held is not None and kind not in SAVE_KINDS:
                self._held.append(event)
            elif kind == "finished" and event.get("session") is not None:
                self._hold([event])
            else:
                self.outbox.put(event)

    def cut(self):
        """Main thread, before the save step: which held frames it covers (those that came before it)."""
        with self._lock:
            return self._holds, len(self._held or ())

    def release(self, cut=None):
        """Main thread, after the save step (``cut``, taken before it; None: all, as a close or the end): the frames
        it covers go in order, and so do those after them up to a turn's end that came during it, which waits for the
        next save step with every frame after it."""
        with self._lock:
            held = self._held or []
            count = len(held) if cut is None else cut[1] if cut[0] == self._holds else 0   # Else expired since.
            stop = next((index for index in range(count, len(held)) if held[index].get("kind") == "finished"),
                        len(held))
            for event in held[:stop]:
                self.outbox.put(event)
            if stop == len(held):
                self._held = None
            elif stop:
                self._hold(held[stop:])

    def _hold(self, frames):
        """Under the lock: hold ``frames`` (a turn's end first) until the next save step, or ``END_WAIT``."""
        self._held, self._holds = frames, self._holds + 1
        timer = threading.Timer(END_WAIT, self._expired, args=(self._holds,))
        timer.daemon = True
        timer.start()

    def _expired(self, hold):
        with self._lock:
            if hold != self._holds or self._held is None:
                return   # Released by its save step already, or held again since (its own timer runs).
            held, self._held = self._held, None
            for event in held:
                self.outbox.put(event)

    def close(self):
        self.release()
        self.outbox.close()
