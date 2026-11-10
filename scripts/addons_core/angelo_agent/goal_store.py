# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where held goals are kept: ``goals.json`` in the state folder, one record per chat, never in the chat file.

Contract: doc/angelo/goal_mode.rst, "The goal" (Storage). A chat file's fields are a whitelist
(``SessionStore._fields``) and an older build reads a field it does not know as a chat it cannot fully read
(``sessions.KNOWN_FIELDS``), so a goal lives beside the chats as ``usage.json`` does (``usage_meter``), with its rules:

* written off the UI thread by one writer at a time (``write_turn``), coalesced, and drained at quit; the writer
  serialises copies taken on the main thread at ``put`` (``_saved_copies``), never the live records the chat changes;
* merged with the file on each write: only the records this process saved or removed since its last write are its
  own; every other chat's record is the file's as it is now, so two processes on one folder never write each other's
  old copies back (a goal dismissed in one is never resurrected by the other);
* ``MAX_CHATS`` most recently updated goals and ``MAX_FILE_BYTES`` at most, so a file this writes always reads back;
* a damaged file reads as empty and is kept beside it (``kept_state``) before it is replaced; a file that can't be
  read now, or that a later version wrote, is never written over;
* a link at the name is never followed;
* every record is checked when read: a malformed one is left out and the others load;
* read back after a restart, or read late from a file that could not be read at first, an active goal is paused
  (``goal_state.after_restart``): it never resumes by itself.

Only the process hosting the chats writes it (a worker Blender holds no goals). No bpy.
"""

import copy
import json
import math
import os
import stat
import threading
import time
import uuid
import weakref
from pathlib import Path

from . import goal_state as G, kept_state, shared_replace, write_turn

VERSION = 1
MAX_CHATS = 200
MAX_FILE_BYTES = 1024 * 1024
MAX_CHAT_ID = 64
_STATUSES = frozenset({G.PENDING, G.PROPOSED, G.ACTIVE, G.PAUSED}) | G.ENDS
_LOCK = threading.Lock()
_STORES = {}


def _number(value, low=0.0, high=1e12):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


_ROUND_NUMBERS = ("n", "working_ms", "measure_ms")


def _round_ok(entry):
    return (isinstance(entry, dict) and all(_number(entry.get(key, 0)) for key in _ROUND_NUMBERS)
            and entry.get("kind") in (G.PERSON, G.AUTOMATIC, G.WORKER_EVENT)
            and (entry.get("score") is None or _number(entry["score"], -1e12))
            and all(entry.get(key) is None or _number(entry[key]) for key in ("tokens", "usd")))


def clean(goal):
    """A goal read from the file, checked field by field; None when it is not one this build wrote."""
    if not isinstance(goal, dict):
        return None
    try:
        if not isinstance(goal["id"], str) or not 0 < len(goal["id"]) <= 64:
            return None
        if not all(type(goal[key]) is int and 0 <= goal[key] <= 10_000 for key in ("stall", "unmeasured")):
            return None
        if goal["best"] is not None and not _number(goal["best"], -1e12):
            return None
        if not all(isinstance(goal.get(key, ""), str) for key in ("reason", "paused_by")):
            return None
        if goal.get("measure_request") is not None and not isinstance(goal["measure_request"], dict):
            return None
        if (goal["status"] not in _STATUSES or goal["check"] not in G.CHECKS
                or goal["origin"] not in (G.CHAT_BAR, G.WORDS)):
            return None
        if not isinstance(goal["objective"], str) or len(goal["objective"]) > G.OBJECTIVE_MAX:
            return None
        if not _number(goal["pass_at"], *G.PASS_AT_RANGE) or not isinstance(goal["rounds"], list):
            return None
        budget, spent = goal["budget"], goal["spent"]
        if not all(_number(budget[key], 1, 10_000) for key in ("minutes", "stall_rounds", "unmeasured_rounds")):
            return None
        if not _number(budget["max_rounds"], 1, G.MAX_ROUNDS_KEPT):   # Rounds past the records kept never end it.
            return None
        if any(budget.get(key) is not None and not _number(budget[key]) for key in ("usd", "tokens")):
            return None
        if not all(_number(spent[key]) for key in ("working_ms", "tokens", "usd")):
            return None
        if goal.get("measure") is not None and not isinstance(goal["measure"], dict):
            return None
        if len(goal["rounds"]) > G.MAX_ROUNDS_KEPT or not all(_round_ok(entry) for entry in goal["rounds"]):
            return None
        return copy.deepcopy(goal)
    except (KeyError, TypeError):
        return None


class GoalStore:
    """The state folder's goals, by chat id (``shared``); ``get`` hands out the live record the chat changes and
    ``put`` saves (the caller passes the same dict back after each change)."""

    def __init__(self, directory, clock=time.time):
        self.path = Path(directory) / "goals.json"
        self.clock = clock
        self.lock = threading.Lock()
        self.goals = {}
        self._prepared = weakref.WeakSet()   # Live controllers already notified of their restored goal.
        self._saved_copies = {}   # What the writer writes: copies taken on the main thread at ``put``.
        self._owed = {}           # chat id -> change number: records this process saved or removed, not written yet.
        self._removed = set()     # Chats whose goal this session removed: a merge never brings them back.
        self._dirty = threading.Event()
        self._worker = None
        self._changes = self._written = 0
        self._unread = False    # A file is there that can't be read now: never written over.
        self._later = False     # A later version's file: never written over.
        self._damaged = False   # No build can read it: kept beside it before the next write.
        self._write_turn = write_turn.WriteTurn()
        write_turn.track(self.drain)
        saved = self._saved()
        if saved is None:
            self._unread = True
        else:
            for chat_id, goal in saved.items():
                G.after_restart(goal)
                self.goals[chat_id] = goal
                self._saved_copies[chat_id] = copy.deepcopy(goal)

    @classmethod
    def shared(cls, directory):
        with _LOCK:
            key = str(directory)
            store = _STORES.get(key)
            if store is None:
                store = _STORES[key] = cls(directory)
            return store

    @classmethod
    def cached(cls, directory):
        """The already-loaded shared owner, or None. Safe from drawing: never constructs or reads a store."""
        with _LOCK:
            return _STORES.get(str(directory))

    def prepare(self, controller):
        """UI pump only: notify each live controller once if this loaded store restores its chip."""
        with self.lock:
            if controller in self._prepared:
                return
            self._prepared.add(controller)
            shown = controller.state["id"] in self.goals
        if shown:
            controller.version = getattr(controller, "version", 0) + 1

    def _saved(self):
        """The file's goals; {} for none or a damaged file; None while it can't be read or a later version wrote it."""
        self._damaged = False
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return {}   # A link or a folder at the name is never followed; a write replaces the link itself.
        if info.st_size > MAX_FILE_BYTES:
            self._damaged = True
            return {}
        try:
            fd = shared_replace.open_shared(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))   # Shares delete.
            with os.fdopen(fd, "rb") as handle:
                raw = json.loads(handle.read(MAX_FILE_BYTES + 1))
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        except (ValueError, RecursionError):
            raw = None
        if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("goals"), dict):
            if isinstance(raw, dict) and type(raw.get("version")) is int and raw["version"] > VERSION:
                self._later = True
                return None
            self._damaged = True
            return {}
        goals = {}
        for chat_id, value in raw["goals"].items():
            goal = clean(value)
            if isinstance(chat_id, str) and 0 < len(chat_id) <= MAX_CHAT_ID and goal is not None:
                goals[chat_id] = goal
        return goals

    def get(self, chat_id):
        with self.lock:
            return self.goals.get(chat_id)

    def put(self, chat_id, goal):
        """Save ``chat_id``'s goal (written in the background)."""
        goal["updated"] = self.clock()
        snapshot = copy.deepcopy(goal)   # On the caller's (main) thread: the writer never reads a live record.
        with self.lock:
            self.goals[chat_id] = goal
            self._saved_copies[chat_id] = snapshot
            self._removed.discard(chat_id)
            self._owed[chat_id] = self._changes + 1
            if len(self.goals) > MAX_CHATS:
                oldest = sorted(self.goals, key=lambda key: self.goals[key].get("updated", 0))
                for old in oldest[:len(self.goals) - MAX_CHATS]:
                    del self.goals[old]
                    self._saved_copies.pop(old, None)
                    self._removed.add(old)
                    self._owed[old] = self._changes + 1
            self._changes += 1
        self._schedule()

    def remove(self, chat_id):
        with self.lock:
            if self.goals.pop(chat_id, None) is None:
                return
            self._saved_copies.pop(chat_id, None)
            self._removed.add(chat_id)
            self._owed[chat_id] = self._changes + 1
            self._changes += 1
        self._schedule()

    # Writing -------------------------------------------------------------------------------------------------------

    def _schedule(self):
        self._dirty.set()
        with self.lock:
            if self._worker is not None and self._worker.is_alive():
                return
            worker = threading.Thread(target=self._writer, daemon=True, name="AngeloGoalWriter")
            try:
                worker.start()
            except RuntimeError:
                self._worker = None   # The goals stay in memory; a goal never stops a chat.
                return
            self._worker = worker

    def _writer(self):
        while self._dirty.wait(timeout=2):
            self._dirty.clear()
            with self._write_turn:
                self._write_owed()
        with self.lock:
            self._worker = None
        if self._dirty.is_set():
            self._schedule()

    def drain(self, timeout=2.0):
        """Write what is owed now (a chat closing, quit); False when a write under way outlasts ``timeout``."""
        if not self._write_turn.acquire(timeout):
            return False
        try:
            return self._write_owed()
        finally:
            self._write_turn.release()

    def flush(self):
        """Write now (tests). Whether the file holds every goal."""
        with self._write_turn:
            return self._write()

    def _write_owed(self):
        with self.lock:
            owed = self._changes != self._written
        return self._write() if owed else True

    def _write(self):
        if self._later:
            return False
        saved = self._saved()   # Merged on each write: another process's goals in this folder are kept.
        if saved is None:
            return False
        with self.lock:
            if self._unread:   # Read late: what the file held and this session did not change joins it, paused.
                for chat_id, goal in saved.items():
                    if chat_id not in self.goals and chat_id not in self._removed:
                        G.after_restart(goal)
                        self.goals[chat_id] = goal
                        self._saved_copies[chat_id] = copy.deepcopy(goal)
                self._unread = False
            goals, owed = dict(saved), dict(self._owed)   # The file as it is now, with this process's own changes.
            for chat_id in owed:
                if chat_id in self._saved_copies:
                    goals[chat_id] = self._saved_copies[chat_id]
                else:
                    goals.pop(chat_id, None)
            changes = self._changes
        for old in sorted(goals, key=lambda key: goals[key].get("updated", 0))[:max(0, len(goals) - MAX_CHATS)]:
            del goals[old]
        if self._damaged:
            if not kept_state.keep_before_write(self.path):
                return False
            self._damaged = False
        encoded = json.dumps({"version": VERSION, "goals": goals}, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        while len(encoded) > MAX_FILE_BYTES and goals:   # The oldest left out first: the reader always reads it back.
            del goals[min(goals, key=lambda key: goals[key].get("updated", 0))]
            encoded = json.dumps({"version": VERSION, "goals": goals}, ensure_ascii=False, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")
        temporary = self.path.with_name(".goals-" + uuid.uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
            shared_replace.replace(temporary, self.path)   # Refused while held: retried, else said; owed.
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        with self.lock:
            self._written = max(self._written, changes)
            for chat_id, number in owed.items():   # Written; a change made while writing stays owed.
                if self._owed.get(chat_id) == number:
                    del self._owed[chat_id]
        return True


def cached_goal(controller):
    """A draw client's goal from the one shared store, never a cold filesystem lookup."""
    store = GoalStore.cached(controller.telemetry.directory)
    return store.get(controller.state["id"]) if store is not None else None
