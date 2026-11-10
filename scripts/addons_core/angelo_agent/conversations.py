# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rename, delete and bring back a conversation: the store side (no ``bpy``; ``conversations_ui`` drives it).

**Rename** sets the chat's own name (``conversation_title``) in its file and in the chat list. A chat open in
Angelo is renamed through its controller, under its lock, so the name and its rows save together; any other chat
is read, named and saved (``SessionStore``: never over a file another writer changed, never while a worker Blender
writes it). The rename never moves the chat in the list: its time (``updated``) stays.

**Delete** moves the chat to Recently Deleted, ``<state>/recently-deleted/<id>/``: its file and every copy kept
beside it (``sessions/<id>.json*``) and its attachments (``references/<id>/``), with ``entry.json`` (its chat-list
record, when it was deleted, whether it was the chat shown). Undo (``restore``) moves them back and lists the chat
again. Entries older than ``KEEP_DAYS`` are removed for good (``purge``). Why not the system's Trash: Angelo must
offer Undo on macOS and Windows alike, and Python here has no portable way to put a file in the Trash or take it
out again (no PyObjC, no shell API binding), and a transcript is private data that belongs in Angelo's own
owner-only state folder, not in a Trash every app can read.

What stays: the chat's request snapshots (``checkpoints/<id>``, the shared snapshot store). They are the scene's
history, kept and pruned by the snapshot policy like every snapshot (``checkpoint_policy``), so a chat brought back
can still restore what they hold until retention removes them. A worker part folder (``parallel/``) belongs to the
parallel work, not to one chat.

Every step is one rename in the state folder. A crash between steps is settled by ``settle``: an entry whose chat
file is back (or never left) puts the rest back and is removed, and names the chats to list again.
"""

import json
import os
import shutil
import time
from pathlib import Path

from . import shared_replace
from .conversation_title import set_name
from .display_text import Text
from .ui_text import n_

TRASH = "recently-deleted"
ENTRY = "entry.json"
KEEP_DAYS = 30
NOT_LISTED = n_("This chat is no longer in the list.")
NOT_DELETED = n_("This chat is no longer in Recently Deleted.")
ALREADY_LISTED = n_("This chat is already in the list.")
RECOVERY_KEPT = n_("Some chat files could not be moved. Their saved copies have been kept.")


def _folder(directory):
    return Path(directory) / TRASH


def _transcript(directory, identity):
    return Path(directory) / "sessions" / (identity + ".json")


def _identity(value):
    import uuid
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


# Rename -------------------------------------------------------------------------------------------------------------

def rename(directory, collection, session_id, text, controller=None):
    """Name the chat ``session_id`` ``text`` (cleaned; "" brings the automatic title back) and save it; returns the
    title the list shows now. Raises ``OSError`` (``session_journal.JournalHosted`` while a worker writes it) and
    ``ValueError`` for a chat the list does not hold."""
    if session_id not in collection.records:
        raise ValueError(Text(NOT_LISTED))
    if controller is not None:
        with controller.lock:
            previous = controller.state.get("title")
            set_name(controller.state, text)
            try:
                controller.store.save(controller.state)   # Not the controller's save: the chat keeps its time.
            except BaseException:
                _put_back(controller.state, previous)
                raise
            state = controller.state
    else:
        from .sessions import SessionStore
        store = SessionStore(directory, session_id)
        state = store.load()
        set_name(state, text)
        store.save(state)
    collection.update(state)
    collection.save()
    return collection.records[session_id]["title"]


def _put_back(state, previous):
    if previous is None:
        state.pop("title", None)
    else:
        state["title"] = previous


# Recently Deleted ---------------------------------------------------------------------------------------------------

def _parts(directory, identity):
    """``(live path, path in the entry)`` of everything that is the chat's own: its file, the copies and temporary
    files beside it, its attachments folder."""
    directory, entry = Path(directory), _folder(directory) / identity
    sessions, parts = directory / "sessions", []
    try:
        with os.scandir(sessions) as found:
            names = sorted(item.name for item in found
                           if item.name == identity + ".json" or item.name.startswith(identity + ".json.")
                           or item.name == identity + ".tmp")
    except FileNotFoundError:
        names = []
    parts += [(sessions / name, entry / "sessions" / name) for name in names]
    parts.append((directory / "references" / identity, entry / "references" / identity))
    return parts


def _exists(path):
    return os.path.lexists(path)


def _write_entry(path, value):
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    shared_replace.replace(temporary, path)


def delete(directory, record, *, shown=False, now=None):
    """Move the chat ``record`` (its chat-list record) to Recently Deleted; ``shown``: it was the chat on screen.
    Raises ``OSError`` on a failed move. Rollback is best effort: any files it cannot put back stay in the
    recovery entry, never removed as failed-deletion cleanup."""
    identity = _identity(record.get("id"))
    if identity is None:
        raise ValueError(Text(NOT_LISTED))
    folder = _folder(directory) / identity
    if _exists(folder):
        if _retained(folder):
            raise FileExistsError(Text(RECOVERY_KEPT))
        forget(directory, identity)   # Metadata only: no retained transcript or attachment may be discarded.
    for sub in (folder, folder / "sessions", folder / "references"):
        sub.mkdir(parents=True, exist_ok=True, mode=0o700)
    entry = {"version": 1, "record": {key: record.get(key) for key in ("id", "title", "provider", "updated")},
             "deleted": time.time() if now is None else now, "shown": bool(shown), "recovery": True}
    _write_entry(folder / ENTRY, entry)
    moved = []
    try:
        for live, kept in _parts(directory, identity):
            if _exists(live):
                shared_replace.replace(live, kept)
                moved.append((live, kept))
        entry["recovery"] = False   # Only a complete deletion is eligible for ordinary retention expiry.
        _write_entry(folder / ENTRY, entry)
    except OSError:
        for live, kept in reversed(moved):
            try:
                if not _exists(live):   # Never overwrite bytes created while the move was in progress.
                    shared_replace.replace(kept, live)
            except OSError:
                pass   # Keep this payload and its recovery entry for restore/settle to retry.
        forget(directory, identity)   # Removes metadata only when rollback left no retained payload.
        raise
    return entry


def entries(directory):
    """The chats in Recently Deleted, the last deleted first: ``{"record", "deleted", "shown"}`` each."""
    from .session_validation import index_record
    found = []
    try:
        with os.scandir(_folder(directory)) as items:
            names = [item.name for item in items if _identity(item.name) == item.name]
    except OSError:
        return []
    for name in names:
        value = _read_entry(_folder(directory) / name / ENTRY)
        record = index_record(value.get("record")) if value else None
        kept = _folder(directory) / name / "sessions" / (name + ".json")
        if record is None or record["id"] != name or not _exists(kept):
            continue
        deleted = value.get("deleted")
        found.append({"record": record, "deleted": deleted if isinstance(deleted, (int, float)) else 0,
                      "shown": value.get("shown") is True})
    return sorted(found, key=lambda item: item["deleted"], reverse=True)


def _read_entry(path):
    try:
        value = json.loads(shared_replace.read_text(path, encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def restore(directory, session_id):
    """Move the deleted chat ``session_id`` back; returns its entry (``entries``' shape). The caller lists it again.
    Raises ``FileExistsError`` when a chat with its file is already there, ``LookupError`` when it is not in Recently
    Deleted, ``OSError`` when a move fails (what moved is moved back)."""
    entry = next((item for item in entries(directory) if item["record"]["id"] == session_id), None)
    if entry is None:
        raise LookupError(Text(NOT_DELETED))
    # The attempt itself needs recovery retention, including a preflight conflict.
    # Keep the stored format/version and metadata; entries() returns only a UI projection.
    saved = _read_entry(_folder(directory) / session_id / ENTRY) or {}
    saved["recovery"] = True
    _write_entry(_folder(directory) / session_id / ENTRY, saved)
    if _exists(_transcript(directory, session_id)):
        raise FileExistsError(Text(ALREADY_LISTED))
    (Path(directory) / "sessions").mkdir(parents=True, exist_ok=True, mode=0o700)
    (Path(directory) / "references").mkdir(parents=True, exist_ok=True, mode=0o700)
    parts = _parts_in_entry(directory, session_id)
    if any(_exists(live) for live, _kept in parts):
        raise FileExistsError(Text(RECOVERY_KEPT))   # Preflight the whole restore before moving its first part.
    moved = []
    try:
        for live, kept in parts:
            if _exists(live):
                raise FileExistsError(Text(RECOVERY_KEPT))
            shared_replace.replace(kept, live)
            moved.append((live, kept))
    except OSError:
        for live, kept in reversed(moved):
            try:
                if not _exists(kept):
                    shared_replace.replace(live, kept)
            except OSError:
                pass   # The live copy remains; never overwrite a concurrently retained copy.
        raise
    return entry


def _parts_in_entry(directory, identity):
    """``(live path, path in the entry)`` of what the entry holds, the chat file last: it says the chat is back."""
    directory, folder = Path(directory), _folder(directory) / identity
    parts = []
    try:
        with os.scandir(folder / "sessions") as found:
            names = sorted(item.name for item in found)
    except FileNotFoundError:
        names = []
    parts += [(directory / "sessions" / name, folder / "sessions" / name) for name in names
              if name != identity + ".json"]
    if _exists(folder / "references" / identity):
        parts.append((directory / "references" / identity, folder / "references" / identity))
    parts += [(directory / "sessions" / name, folder / "sessions" / name) for name in names
              if name == identity + ".json"]
    return parts


def _retained(folder):
    """Payload or unknown/unreadable contents forbid destructive recovery cleanup."""
    if not _exists(folder):
        return False
    try:
        for child in folder.iterdir():
            if child.name in {ENTRY, ENTRY + ".tmp"}:
                continue
            if child.name in {"sessions", "references"} and child.is_dir() and not child.is_symlink():
                if any(child.iterdir()):
                    return True
            else:
                return True
    except OSError:
        return True   # An unreadable entry is not evidence that its payload is gone.
    return False


def forget(directory, session_id):
    """Remove metadata after restore/list save, only when no retained payload remains."""
    folder = _folder(directory) / session_id
    if _retained(folder):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return not _exists(folder)


def settle(directory, records):
    """Finish what a crash left half done. An entry whose chat file is in the live folder (a deletion that stopped
    before moving it, or an Undo that moved it back) puts the rest back and is removed; returns the records of those
    chats that ``records`` (the chat list) does not hold, to list again."""
    relist = []
    try:
        with os.scandir(_folder(directory)) as items:
            names = [item.name for item in items if _identity(item.name) == item.name]
    except OSError:
        return relist
    for name in names:
        if not _exists(_transcript(directory, name)):
            continue
        value = _read_entry(_folder(directory) / name / ENTRY) or {}
        try:
            for live, kept in _parts_in_entry(directory, name):
                if not _exists(live):
                    shared_replace.replace(kept, live)
        except OSError:
            continue   # Tried again at the next settle.
        from .session_validation import index_record
        record = index_record(value.get("record"))
        if name not in records and record is not None and record["id"] == name:
            relist.append(record)
        forget(directory, name)
    return relist


def purge(directory, now=None, keep_days=KEEP_DAYS):
    """Remove for good the chats deleted over ``keep_days`` days ago (and entries that hold no chat). Returns how
    many it removed."""
    now = time.time() if now is None else now
    kept = {item["record"]["id"]: item for item in entries(directory)}
    removed = 0
    try:
        with os.scandir(_folder(directory)) as items:
            names = [item.name for item in items if _identity(item.name) == item.name]
    except OSError:
        return 0
    for name in names:
        folder = _folder(directory) / name
        entry = _read_entry(folder / ENTRY)
        item = kept.get(name)
        if ((entry or {}).get("recovery") is True
                or (_retained(folder) and (item is None or _exists(_transcript(directory, name))))):
            continue   # Partial moves, conflicts and unclassified retained copies never expire as deletions.
        if item is not None and now - item["deleted"] < keep_days * 86400:
            continue
        if item is None and _exists(_transcript(directory, name)):
            continue   # Half a restore: ``settle`` finishes it.
        shutil.rmtree(_folder(directory) / name, ignore_errors=True)
        removed += 1
    return removed
