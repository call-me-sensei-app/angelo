# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search the Conversations list by title and by what the person asked. Standard library only, main thread.

Matching: the query and the text are compared after NFKC normalization and ``casefold`` (so "STRASSE" finds
"Straße", full-width "ＡＢＣ" finds "abc", and Greek, Cyrillic, Arabic, Devanagari or CJK text matches itself); every
word of the query must appear, in any order, as part of the chat's shown title or of one of its requests (a word of
a language written without spaces matches anywhere in a line). Marks are never stripped: in Hindi or Thai they are
letters.

The index (``SearchIndex``) never reads a chat for a key press. Per chat it keeps the person's requests (the first
and the last always, then the others in order, each at most ``LINE`` characters, ``LIMIT`` in all) and the chat
file's settled identity/modification/change-time/size witness they were read from. It is built lazily, on the
first search, and checked again at most every ``RECHECK_S`` while the person types: one ``stat`` per chat, and
only a changed chat is read again. A coarse clock still in its current tick never certifies cache reuse; changed
or raced reads match by title until a later refresh can index them. Signature scans and changed reads resume under
one step deadline
(``refresh(budget=...)``); an already-started filesystem operation remains atomic, so this limits admission of
another operation, not its wall-clock duration. What is not read yet is matched by title until it is. The index is saved in
``conversation-search.json`` (owner-only, beside the chat list), so a launch reads only the chats changed since.
A deleted chat's entry is dropped (``forget``) with the chat.
"""

import json
import os
import time
import unicodedata
from collections import deque
from pathlib import Path

from . import change_clock, document_file_identity, shared_replace

NAME = "conversation-search.json"
LINE = 240        # Characters kept of one request.
LIMIT = 2000      # Characters kept per chat.
RECHECK_S = 2.0   # A search re-checks the chat files at most this often.
STEP_S = 0.012    # The popover's read budget per draw while the index is incomplete.
VERSION = 2


def fold(text):
    return unicodedata.normalize("NFKC", text).casefold()


def requests(document):
    """The person's requests in a chat document (``session_journal.read``): the lines the index keeps."""
    from .request_rows import IMAGE_ONLY_TEXT, person_request   # The person's own words, never Angelo's rows.
    rows = document.get("messages") if isinstance(document, dict) else None
    texts = [" ".join(row["text"].split())[:LINE] for row in rows if person_request(row)
             and isinstance(row.get("text"), str) and row["text"].strip() and row["text"] != IMAGE_ONLY_TEXT] \
        if isinstance(rows, list) else []
    if len(texts) <= 2:
        return texts
    chosen, total = {0, len(texts) - 1}, len(texts[0]) + len(texts[-1])
    for index in range(1, len(texts) - 1):
        if total + len(texts[index]) > LIMIT:
            break
        chosen.add(index)
        total += len(texts[index])
    return [texts[index] for index in sorted(chosen)]


class SearchIndex:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / NAME
        self._entries = None     # id -> {"signature": settled witness, "lines": [...]}, loaded lazily
        self._folded = {}        # id -> (signature, folded lines): the haystack, folded once per read
        self._checked = None     # time.monotonic() of the last complete check
        self._queue = []         # ids whose file changed, still to read
        self._stats = deque()    # ids not yet checked in the current resumable pass
        self._listed = set()     # prior listing, including ids whose files are missing
        self._checking = self._full_check = False
        self._dirty = False
        self._refused_at = None  # Windows retry pauses are paid at most once per existing retry window.
        self.last = {"stats": 0, "reads": 0, "seconds": 0.0}   # The last refresh step (measurements, tests)

    def _load(self):
        if self._entries is not None:
            return
        self._entries = {}
        try:
            value = json.loads(shared_replace.read_text(self.path, encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return
        if not isinstance(value, dict) or value.get("version") != VERSION or not isinstance(value.get("chats"), dict):
            return
        for identity, entry in value["chats"].items():
            if (isinstance(entry, dict) and isinstance(entry.get("signature"), list)
                    and isinstance(entry.get("lines"), list)
                    and all(isinstance(line, str) for line in entry["lines"])):
                self._entries[identity] = {"signature": entry["signature"], "lines": entry["lines"][:64]}

    def _signature(self, identity):
        try:
            status = os.stat(self.directory / "sessions" / (identity + ".json"))
        except OSError:
            return None
        identity = document_file_identity.identity_of(status)
        if identity is None:
            return list(change_clock.unique())
        return list(change_clock.cache_key(status,
            (*identity[:2], status.st_size, status.st_mtime_ns, status.st_ctime_ns, identity[2]), change=True))

    @property
    def complete(self):
        return self._checked is not None and not self._checking and not self._queue

    def forget(self, identity=None):
        """Drop one chat's entry (deleted), or check every chat again at the next search (None)."""
        self._load()
        if identity is None:
            self._checked = None
            self._stats.clear()
            self._checking = self._full_check = False
            return
        if self._entries.pop(identity, None) is not None:
            self._dirty = True
        self._folded.pop(identity, None)
        self._queue = [item for item in self._queue if item != identity]
        self._stats = deque(item for item in self._stats if item != identity)
        self.save()

    def refresh(self, identities, budget=STEP_S, now=None):
        """Bring the index up to the chats ``identities`` lists, reading changed chats for at most ``budget``
        seconds of admitted operations. A filesystem operation already started
        is atomic; the deadline prevents starting another, not a walltime bound.
        True when both the signature pass and changed reads finish (``complete``)."""
        started = time.perf_counter()
        deadline = started + budget
        now = time.monotonic() if now is None else now
        self._load()
        stats = reads = 0
        wanted = list(identities)
        known = set(wanted)
        newly_listed = known - self._listed
        self._listed = known
        waiting = set(self._queue)
        due = not self._checking and (self._checked is None
                                     or (not self._queue and now - self._checked >= RECHECK_S))
        if due:
            for identity in [i for i in self._entries if i not in known]:
                del self._entries[identity]
                self._folded.pop(identity, None)
                self._dirty = True
            self._stats = deque(wanted)
            self._checking = self._full_check = True
        else:   # A newly listed chat joins a pending pass without restarting its cursor.
            pending = waiting.union(self._stats)
            added = [identity for identity in wanted if identity in newly_listed and identity not in pending]
            self._stats.extend(added)
            self._checking |= bool(added)
        while self._stats and time.perf_counter() < deadline:
            identity = self._stats.popleft()
            if identity not in known:
                continue
            stats += 1
            signature = self._signature(identity)
            entry = self._entries.get(identity)
            changed = entry is None or entry["signature"] != signature
            if entry is not None and changed:
                del self._entries[identity]
                self._folded.pop(identity, None)
                self._dirty = True
            if signature is not None and changed and identity not in waiting:
                self._queue.append(identity)
                waiting.add(identity)
        if self._checking and not self._stats:
            if self._full_check:
                self._checked = now
            self._checking = self._full_check = False
        while self._queue and time.perf_counter() < deadline:
            identity = self._queue.pop(0)
            if identity not in known:
                continue
            reads += 1
            self._read(identity)
        if self.complete:
            self.save()
        self.last = {"stats": stats, "reads": reads, "seconds": time.perf_counter() - started}
        return self.complete

    def _read(self, identity):
        from . import session_journal
        signature = self._signature(identity)
        if signature is None or signature[0] == "unsettled":
            return
        try:
            # Search keeps no journal write authority and must not wait for a
            # coarse clock on the UI thread; its own witnesses gate cache reuse.
            document, opened = session_journal.read(self.directory / "sessions" / (identity + ".json"),
                                                    settle=lambda witness: witness)
            # The path can change A -> B -> A around open. Bind the actual
            # descriptor read, not only the two matching path samples.
            if opened.identity is None or opened.identity[:5] != tuple(signature[:5]):
                return
            lines = requests(document)
        except (OSError, ValueError, TypeError, AttributeError, RecursionError, OverflowError, UnicodeError):
            return   # Retry at the next poll; a transient failure is not an empty transcript.
        if signature != self._signature(identity):
            return   # Changed during the read, or still in a coarse tick: title-only until the next poll.
        self._entries[identity] = {"signature": signature, "lines": lines}
        self._folded.pop(identity, None)
        self._dirty = True

    def save(self):
        if (not self._dirty or self._entries is None or self._refused_at is not None
                and time.monotonic() - self._refused_at < shared_replace.RETRY_SECONDS):
            return
        temporary = self.path.with_name(NAME + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"version": VERSION, "chats": self._entries}, handle, ensure_ascii=False)
            shared_replace.replace(temporary, self.path, told=False)
            self._dirty = False
            self._refused_at = None
        except shared_replace.Refused:
            self._refused_at = time.monotonic()  # Keep the dirty cache, without retrying on every UI tick.
        except (OSError, ValueError):
            pass   # A cache: the next launch reads the chats again.

    def lines(self, identity):
        entry = (self._entries or {}).get(identity)
        if entry is None:
            return ()
        folded = self._folded.get(identity)
        if folded is None or folded[0] is not entry["signature"]:
            folded = (entry["signature"], tuple(fold(line) for line in entry["lines"]))
            self._folded[identity] = folded
        return folded[1]

    def search(self, records, query, title_of):
        """``[(record, snippet)]`` of ``records`` (in order) matching ``query``; ``snippet`` is the first request
        holding a query word when the title alone does not match, else None. ``title_of(record)`` is the shown title."""
        words = fold(query).split()
        if not words:
            return [(record, None) for record in records]
        found = []
        for record in records:
            title, lines = fold(title_of(record)), self.lines(record["id"])
            missing = [word for word in words if word not in title]
            if missing and not all(any(word in line for line in lines) for word in missing):
                continue
            snippet = None
            if missing:
                index = next(i for i, line in enumerate(lines) if missing[0] in line)
                snippet = self._entries[record["id"]]["lines"][index]
            found.append((record, snippet))
        return found
