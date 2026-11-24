# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which folders a chat has as context, and which one is its working folder (``context_folder``; 2.1.0).

A chat has up to ``MAX_FOLDERS`` folders, in the order the person attached them. One of them is the **working
folder**: the first attached, or the one the person made it from its card. The agent may create and edit text files
there (``context_folder_write``); every other folder is a **reference** folder it only reads. Removing the working
folder makes the next folder the working one; with no folder left there is none.

**Every grant-bearing value is C's** (2.0.0's rule for every grant, ``grant_authority.CONTEXT_FOLDERS``), written only
in a grant scope (the person's pick or click, or Angelo's own automatic decision) and read from C:

* ``<chat>/<folder id>`` -> the SHA-256 of the folder's real path, device and inode: what may be read.
* ``<chat>/working`` -> that digest of the working folder: what may be written.
* ``<chat>/<folder id>/own`` -> the folder's state (``State``): its attach order, how many files the agent created in it
  (``MAX_CREATED`` counts these), whether it was attached read-only before several folders (``legacy``), and a token
  per file the agent created there, as it left it (``token``), at most ``OWNED_CAP``.

**The file** ``context-folders.json`` in the state folder (owner-only, written whole), version 3: ``{"chats": {chat id:
{"folders": [{"id", "path", "real", "device", "inode", "at", "state"}], "working": folder id or null}}}``. It is
written FROM C (``_snapshot``): a folder C doesn't hold is left out, the working folder is the one C names, and each
folder's state is C's own text. Python's copy holds only where each folder is on disk, each checked against C's digest
wherever it is used, so nothing an agent script changes in place in Python's state is ever saved and mirrored back
into C at the next start (break-it re-read of 121b533, M2). The file is mirrored into C as it is read, never while an
agent script runs; a start whose mirror was refused saves nothing (it would drop the person's grants).

A file is the agent's own while nobody changes it; an UPDATE of an own file carries it forward, and an UPDATE of any
other file, the person's approved one included, never makes it the agent's (M1). A version 1 file (one folder per
chat, 2.1.0 before this) is read as that folder attached as a reference folder marked legacy: it was attached as
read-only, so only the person's click makes it writable, and the working folder never moves on to it (M3).
Standard library only; no bpy.
"""

import hashlib
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

from . import context_folder_root, grant_authority, owned_paths, shared_replace
from .context_folder_roots import vet_root

NAME = "context-folders.json"
# Each folder's path-free manifest is bounded (``context_folder.manifest``: 24 kinds, 50 top-level names of at most
# ``context_folder.NAME_CHARS`` characters), about 1.5 KB for a typical project and at most ``MANIFEST_BYTES``; five
# keep the turn's folder context under about 30 KB even at that bound (pinned by test_native_context_folders).
MAX_FOLDERS = 5
OWNED_CAP = 128        # Files remembered as the agent's own per folder (17 characters each of C's 4,096); an older
#                        one asks again (fails safe).
MAX_FILE_BYTES = 32 * 1024 * 1024   # Past this it is not Angelo's file: read as no grants (fails closed). A chat's
#                                     record is bounded (5 folders, 128 tokens each), and a deleted chat's is removed.
OWN = "own"            # "<chat>/<folder id>/own": the folder's State.
WORKING = "working"    # The C key suffix of a chat's working folder (folder ids are hex, never this word).
VERSION = 3
_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_NUMBER = re.compile(r"^[0-9]{1,9}$")   # ASCII digits only (str.isdigit admits other scripts' digits).
NOT_READY = "The folder grants could not be read into the grant authority yet"


class TooMany(ValueError):
    """The chat has ``MAX_FOLDERS`` folders already."""


class AlreadyAttached(ValueError):
    """That folder is attached to this chat already."""


class Overlaps(ValueError):
    """That folder is inside one attached to this chat, or holds one: a write to the working folder could otherwise
    reach a reference folder, or a reference read reach the working one by another name."""


def identity(folder):
    """The folder as it is now: its real path, device and inode (OSError when it is gone)."""
    real = os.path.realpath(str(folder))
    info = os.stat(real)
    return real, info.st_dev, info.st_ino


digest = context_folder_root.digest   # What C holds for a folder: one definition, the root step's.


def folder_digest(folder):
    """What C holds for a folder: the SHA-256 of its real path with its device and inode, so a grant follows that
    folder, never another one later at its path (F-2). None when the folder is gone."""
    try:
        return digest(*identity(folder))
    except OSError:
        return None


def key(chat_id, folder_id):
    return "%s/%s" % (chat_id, folder_id)


def own_key(chat_id, folder_id):
    return "%s/%s/%s" % (chat_id, folder_id, OWN)


def _saved_digest(record):
    return digest(record["real"], record["device"], record["inode"])


def _within(path, root):
    path, root = os.path.normcase(path), os.path.normcase(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _valid_record(value):
    return (isinstance(value, dict) and isinstance(value.get("id"), str) and bool(_HEX16.match(value["id"]))
            and isinstance(value.get("path"), str) and isinstance(value.get("real"), str)
            and type(value.get("device")) is int and type(value.get("inode")) is int
            and type(value.get("at")) in (int, float))


def token(signature):
    """A file as the agent left it (``context_folder_working.file_signature``: size, mtime, inode, device, ctime), as
    16 hex characters: any change to the file, or another file at its name, is another token."""
    return hashlib.sha256(("%d:%d:%d:%d:%d" % tuple(signature)).encode()).hexdigest()[:16]


class State:
    """A folder's state as C holds it: ``"s<order> c<created>[ L] <token> ..."`` (at most 4,096 characters)."""

    def __init__(self, order=0, created=0, legacy=False, owned=()):
        self.order, self.created, self.legacy, self.owned = order, created, legacy, list(owned)[-OWNED_CAP:]

    @classmethod
    def parse(cls, text):
        """The state of C's (or the file's) text; None when it is not one (left out: fails closed)."""
        if not isinstance(text, str) or len(text) > 4096:
            return None
        order = created = None
        legacy, owned = False, []
        for part in text.split():
            if part[:1] == "s" and _NUMBER.match(part[1:]) and order is None:
                order = int(part[1:])
            elif part[:1] == "c" and _NUMBER.match(part[1:]) and created is None:
                created = int(part[1:])
            elif part == "L":
                legacy = True
            elif _HEX16.match(part):
                owned.append(part)
            else:
                return None
        if order is None or created is None:
            return None
        return cls(order, created, legacy, owned)

    def text(self):
        return " ".join(["s%d" % self.order, "c%d" % self.created] + (["L"] if self.legacy else []) + self.owned)


def _new_id():
    return uuid.uuid4().hex[:16]


class Grants:
    """The chats' folders (the module's docstring). Main thread."""

    def __init__(self, directory):
        self.path = Path(directory) / NAME
        self._grants, self._pairs = self._load()
        self._mirrored = False
        self._mirror()

    def _mirror(self):
        """The file's grants into C (as saved: a folder replaced since never matches), once. Refused while an agent
        script runs: then tried again at the next call that isn't one (``_ready``), and nothing is saved before."""
        if not self._mirrored and not _grant_guard.inside_agent_code():
            self._mirrored = grant_authority.mirror(grant_authority.CONTEXT_FOLDERS, self._pairs)
        return self._mirrored

    def _ready(self):
        if not self._mirror():
            raise PermissionError(NOT_READY)

    # Reading the file -----------------------------------------------------------------------------------------------

    def _load(self):
        """``(chats, pairs to mirror into C)``: each chat's folders on disk (``id``, ``path``, ``real``, ``device``,
        ``inode``, ``at``) and the C values the file holds for them."""
        try:
            info = os.lstat(self.path)
            if info.st_size > MAX_FILE_BYTES or not stat.S_ISREG(info.st_mode):
                return {}, []
            raw = json.loads(shared_replace.read_bytes(self.path).decode("utf-8"))
        except (OSError, ValueError, UnicodeError):
            return {}, []
        if (not isinstance(raw, dict) or not isinstance(raw.get("chats"), dict)
                or raw.get("version") not in (1, VERSION)):
            return {}, []
        chats, pairs = {}, []
        for chat_id, value in raw["chats"].items():
            if not isinstance(chat_id, str) or "/" in chat_id or not isinstance(value, dict):
                continue
            if raw["version"] == 1:   # One read-only folder per chat: kept as a reference folder, marked legacy.
                record = dict(value, id=_new_id())
                if _valid_record(record):
                    chats[chat_id] = {"folders": [self._record(record)]}
                    pairs += [(key(chat_id, record["id"]), _saved_digest(record)),
                              (own_key(chat_id, record["id"]), State(1, 0, True).text())]
                continue
            if not isinstance(value.get("folders"), list):
                continue
            folders = []
            for item in value["folders"][:MAX_FOLDERS]:
                state = State.parse(item.get("state")) if isinstance(item, dict) else None
                if not _valid_record(item) or state is None or any(f["id"] == item["id"] for f in folders):
                    continue
                folders.append(self._record(item))
                pairs += [(key(chat_id, item["id"]), _saved_digest(item)), (own_key(chat_id, item["id"]), state.text())]
                if value.get("working") == item["id"]:
                    pairs.append((key(chat_id, WORKING), _saved_digest(item)))
            if folders:
                chats[chat_id] = {"folders": folders}
        return chats, pairs

    @staticmethod
    def _record(value):
        return {name: value[name] for name in ("id", "path", "real", "device", "inode", "at")}

    # Reading C ---------------------------------------------------------------------------------------------------

    def _chat(self, chat_id):
        self._mirror()   # A start whose mirror was refused: tried again before any grant is read.
        return self._grants.get(chat_id) or {"folders": []}

    def _find(self, chat_id, folder_id):
        return next((record for record in self._chat(chat_id)["folders"] if record["id"] == folder_id), None)

    @staticmethod
    def _held(chat_id, folder_id):
        return grant_authority.value(grant_authority.CONTEXT_FOLDERS, key(chat_id, folder_id))

    @staticmethod
    def state(chat_id, folder_id):
        """The folder's ``State`` as C holds it; none held reads as a legacy folder (never moved on to: fails safe)."""
        found = State.parse(grant_authority.value(grant_authority.CONTEXT_FOLDERS, own_key(chat_id, folder_id)))
        return found if found is not None else State(legacy=True)

    def _held_records(self, chat_id):
        """This chat's folders C holds (its digest is this record's), in the order C says they were attached."""
        held = [record for record in self._chat(chat_id)["folders"]
                if self._held(chat_id, record["id"]) == _saved_digest(record)]
        return sorted(held, key=lambda record: self.state(chat_id, record["id"]).order)

    def _working_id(self, chat_id):
        """The id of the folder C holds as this chat's working folder, or None."""
        held = grant_authority.value(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING))
        return next((record["id"] for record in self._held_records(chat_id) if held == _saved_digest(record)), None)

    def records(self, chat_id):
        """``[{"id", "path", "working"}]`` of each folder C holds for this chat, in attach order, without looking at
        the disk: what its cards show and what Remove removes, whether or not the folder is still there."""
        working = self._working_id(chat_id)
        return [{"id": record["id"], "path": Path(record["path"]), "working": record["id"] == working}
                for record in self._held_records(chat_id)]

    def readable(self, chat_id, folder_id):
        """The folder's path while C holds it and it is still that folder; None otherwise."""
        record = self._find(chat_id, folder_id)
        if record is None:
            return None
        held = self._held(chat_id, folder_id)
        if held is None or held != _saved_digest(record) or held != folder_digest(record["path"]):
            return None
        return Path(record["path"])

    def saved_identity(self, chat_id, folder_id):
        """(device, inode) the grant was made for: a workspace opened later must be this very folder."""
        record = self._find(chat_id, folder_id)
        return None if record is None else (record["device"], record["inode"])

    def saved(self, chat_id, folder_id):
        """(real path, device, inode) as attached, whether or not the folder is still there or moved since."""
        record = self._find(chat_id, folder_id)
        return None if record is None else (record["real"], record["device"], record["inode"])

    def writable(self, chat_id, folder_id):
        """The working folder's path while C holds it as this chat's working folder and it is still that folder."""
        folder = self.readable(chat_id, folder_id)
        return folder if folder is not None and self._working_id(chat_id) == folder_id else None

    def get(self, chat_id):
        """The working folder, else the first readable one (single-folder callers and checks)."""
        for record in sorted(self.records(chat_id), key=lambda item: not item["working"]):
            folder = self.readable(chat_id, record["id"])
            if folder is not None:
                return folder
        return None

    def recorded(self, chat_id):
        records = self.records(chat_id)
        return records[0]["path"] if records else None

    def owns(self, chat_id, folder_id, signature):
        """Whether the file with ``signature`` is one the agent created in this folder in this chat (or carried
        forward from one by its own updates) and nobody changed since: read from C."""
        if self.readable(chat_id, folder_id) is None or not isinstance(signature, tuple) or len(signature) != 5:
            return False
        return token(signature) in self.state(chat_id, folder_id).owned

    def created_count(self, chat_id, folder_id):
        return self.state(chat_id, folder_id).created

    # Changing ---------------------------------------------------------------------------------------------------

    @staticmethod
    def _set_state(chat_id, folder_id, state):
        """C's state of the folder, in a grant scope (the caller's, or Angelo's own automatic one)."""
        grant_authority.automatic(grant_authority.record, grant_authority.CONTEXT_FOLDERS,
                                  own_key(chat_id, folder_id), state.text())

    def attach(self, chat_id, folder):
        """Adds ``folder`` to the chat (the person's pick, in their grant scope) and returns its id. The first folder
        is the working folder, as is one added while the chat has none. Refused: not a real folder, ``vet_root``'s
        refusals, the cap, a folder attached already, and one inside or holding an attached folder."""
        self._ready()   # Never over a file whose grants C doesn't hold yet.
        folder = Path(folder)
        if not folder.is_absolute() or folder.is_symlink() or not folder.is_dir():
            raise ValueError("Choose a folder")
        vet_root(folder)
        real, device, inode = identity(folder)
        held = self._held_records(chat_id)
        for record in held:
            if (record["device"], record["inode"]) == (device, inode) or os.path.normcase(record["real"]) \
                    == os.path.normcase(real):
                raise AlreadyAttached("That folder is attached to this chat already")
            if _within(real, record["real"]) or _within(record["real"], real):
                raise Overlaps("That folder is inside a folder attached to this chat, or holds one")
        if len(held) >= MAX_FOLDERS:
            raise TooMany("A chat can have %d folders" % MAX_FOLDERS)
        record = {"id": _new_id(), "path": str(folder), "real": real, "device": device, "inode": inode,
                  "at": time.time()}
        first = self._working_id(chat_id) is None   # The chat has no working folder: this one is.
        order = max((self.state(chat_id, item["id"]).order for item in held), default=0) + 1
        made = [key(chat_id, record["id"]), own_key(chat_id, record["id"])] + ([key(chat_id, WORKING)] if first else [])
        # Held before it is saved, so a refused or failed write grants nothing, in memory either (r2 S-c).
        grant_authority.record(grant_authority.CONTEXT_FOLDERS, key(chat_id, record["id"]), _saved_digest(record))
        try:
            self._set_state(chat_id, record["id"], State(order))
            if first:
                grant_authority.record(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING), _saved_digest(record))
            chat = self._chat(chat_id)
            self._grants = dict(self._grants, **{chat_id: {"folders": chat["folders"] + [record]}})
            self._save()
        except BaseException:
            for name in made:
                grant_authority.forget(grant_authority.CONTEXT_FOLDERS, name)
            self._grants = dict(self._grants, **{chat_id: {"folders": [
                item for item in self._chat(chat_id)["folders"] if item["id"] != record["id"]]}})
            raise
        return record["id"]

    def make_working(self, chat_id, folder_id):
        """The person's click on a reference folder's card: it becomes the working folder, the other a reference one
        (narrowing first: the old one stops being writable at once). It is no legacy folder from then on."""
        self._ready()
        record = self._find(chat_id, folder_id)
        if record is None or self.readable(chat_id, folder_id) is None:
            raise ValueError("That folder isn't attached to this chat")
        if self._working_id(chat_id) == folder_id:
            return
        grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING))
        state = self.state(chat_id, folder_id)
        if state.legacy:
            state.legacy = False
            self._set_state(chat_id, folder_id, state)
        grant_authority.record(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING), _saved_digest(record))
        try:
            self._save()
        except BaseException:
            grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING))
            raise

    def remove(self, chat_id, folder_id):
        """One folder off the chat, narrowing at once in C (r3 S-4: a failed write is raised, never undone). The
        working folder's removal makes the next folder still there the working one, as Angelo's own decision: never a
        legacy one, never while agent code runs (then there is none). Returns the id of the folder made the working
        one (the chat says so: ``context_folder_host.detach``), True when none was, False when nothing was removed."""
        self._ready()   # The file's grants in C first, so its save never leaves a removed one in the file.
        if self._find(chat_id, folder_id) is None:
            return False
        was_working = self._working_id(chat_id) == folder_id
        grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, folder_id))
        grant_authority.forget(grant_authority.CONTEXT_FOLDERS, own_key(chat_id, folder_id))
        promoted = None
        if was_working:
            grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING))
            successor = next((record for record in self._held_records(chat_id)
                              if not self.state(chat_id, record["id"]).legacy
                              and self.readable(chat_id, record["id"])), None)
            if successor is not None and not _grant_guard.inside_agent_code():   # Agent code moves nothing on.
                try:
                    grant_authority.automatic(grant_authority.record, grant_authority.CONTEXT_FOLDERS,
                                              key(chat_id, WORKING), _saved_digest(successor))
                    promoted = successor["id"]
                except PermissionError:   # Agent code removed it: it moves nothing on.
                    promoted = None
        rest = [record for record in self._chat(chat_id)["folders"] if record["id"] != folder_id]
        grants = dict(self._grants)
        if rest:
            grants[chat_id] = {"folders": rest}
        else:
            grants.pop(chat_id, None)
        self._grants = grants
        self._save()
        return promoted or True

    def revoke(self, chat_id, folder_id=None):
        """Every folder of the chat (``folder_id`` None: a deleted chat), or one."""
        if folder_id is not None:
            return self.remove(chat_id, folder_id)
        self._ready()
        chat = self._grants.get(chat_id)
        if chat is None:
            return False
        grants = dict(self._grants)
        grants.pop(chat_id)
        self._grants = grants
        grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, WORKING))
        for record in chat["folders"]:
            grant_authority.forget(grant_authority.CONTEXT_FOLDERS, key(chat_id, record["id"]))
            grant_authority.forget(grant_authority.CONTEXT_FOLDERS, own_key(chat_id, record["id"]))
        self._save()
        return True

    def note_written(self, chat_id, folder_id, signature, created, replaced=None):
        """The agent wrote a file. A file it ``created`` becomes its own and counts toward ``MAX_CREATED``; an update of
        its own file (``replaced`` is one) stays its own as written now; an update of any other file never becomes its
        own (M1). In C first, in Angelo's own grant scope (refused while agent code runs)."""
        if self.readable(chat_id, folder_id) is None:
            return
        state = self.state(chat_id, folder_id)
        before = token(replaced) if replaced is not None else None
        if before in state.owned:
            state.owned.remove(before)
        elif not created:
            return   # Someone else's file: it stays theirs, and its next change asks again.
        state.owned = (state.owned + [token(signature)])[-OWNED_CAP:]
        state.created += 1 if created else 0
        self._set_state(chat_id, folder_id, state)
        self._save()

    # Writing the file ------------------------------------------------------------------------------------------

    def _snapshot(self):
        """What the file holds: C's grants, never Python's (the module's docstring)."""
        chats = {}
        for chat_id in self._grants:
            working = self._working_id(chat_id)
            folders = [dict(record, state=self.state(chat_id, record["id"]).text())
                       for record in self._held_records(chat_id)]
            if folders:
                chats[chat_id] = {"folders": folders, "working": working}
        return {"version": VERSION, "chats": chats}

    def _save(self):
        self._ready()   # C holds none of the file's grants yet (a refused mirror): saving would drop them all.
        owned_paths.require_writable(self.path)   # C first: nothing is made beside a target it refuses.
        snapshot = self._snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name("." + NAME + "." + uuid.uuid4().hex[:12] + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            shared_replace.replace(temporary, self.path)
        finally:
            shared_replace.discard(temporary)


# The lowest functions that grant the agent a folder's read or write refuse agent code (grant_guard.entry; r2 S-c).
from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)
_grant_guard.entry_methods(Grants, "attach", "make_working", "note_written")
