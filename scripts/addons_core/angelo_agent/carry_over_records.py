# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a carry-over's renames (``version_carry_over``): the records that name a place in the old state folder.

* **Snapshot records** follow the move as they do after the pre-rename move (``state_migration``): each ordered
  record's file-journal roots and every record's ``original_path`` (legacy journals keep their recorded roots, which
  are their admission evidence).
* **Every other record at the state folder's top** (``*.json``: background jobs' staging and recovery folders,
  worker chats' part files, the start panel's scene titles and any record added later) has every string that names
  a place in the old folder, as a value or a key, moved to the same place in the new one. A record with no such
  string is not rewritten.
* **Finished or named** (``update``): each step runs whatever another meets. A record that cannot be read or written
  now is returned as pending (``carry_over_finish`` tries it again at later launches, a bounded number of times); a
  damaged one (not UTF-8, not JSON, nested past Python's limit; a damaged snapshot record) is left to its owner.
* **Re-derived on load, never rewritten:** a chat's task-memory scope (``sessions`` accepts the folders the state
  lived in, ``state_paths.earlier_state_folders``), attachments (found by file name, ``references``), and a chat's
  native contexts, which never resume after the move because a native CLI keys its sessions to the working folder
  the chat started them in (``sessions.SessionStore._read``, ``state_paths.carried_chats``).
* **The chat list** (``conversations.json``) is the one record both sides add to when the new folder already had
  chats (``combine_chat_lists``): the old list's chats whose transcripts are now in the new folder join its list.

Standard library only.
"""

import json
import os
import time
from pathlib import Path

from . import shared_replace, state_paths

CHAT_LIST = "conversations.json"


def spellings(source):
    """The old folder as records may name it: as recorded and resolved through any link."""
    found = [str(Path(source).absolute())]
    try:
        resolved = str(Path(source).resolve())
    except (OSError, RuntimeError):
        resolved = found[0]
    return found + ([resolved] if resolved not in found else [])


def _moved(value, olds, current, retained=()):
    if isinstance(value, str):
        if any(state_paths.relocate(value, path, "") != value for path in retained):
            return value   # Inside a folder that stayed where it was.
        for old in olds:
            moved = state_paths.relocate(value, old, current)
            if moved != value:
                return moved
        return value
    if isinstance(value, list):
        return [_moved(item, olds, current, retained) for item in value]
    if isinstance(value, dict):
        return {_moved(key, olds, current, retained): _moved(item, olds, current, retained)
                for key, item in value.items()}
    return value


def write_json(path, value):
    temporary = path.with_name(path.name + ".carry-%d.tmp" % os.getpid())
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle)   # ASCII escapes: a lone surrogate from a Windows name writes as itself.
            handle.flush()
            os.fsync(handle.fileno())
        shared_replace.replace(temporary, path)   # Retried while a reader holds the record (Windows).
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


class RecordsUnfinished(OSError):
    """Records the carry-over could not read or rewrite this time (a sharing violation, an antivirus scan, a full
    disk): the carry-over is not finished, so its record stays ``moving`` and the next launch rewrites them (every
    rewrite is idempotent). The records it could rewrite are rewritten."""

    def __init__(self, failures):
        self.failures = failures
        names = "; ".join("%s (%s: %s)" % (name, type(error).__name__, error) for name, error in failures[:3])
        super().__init__("%d record(s) could not be updated yet: %s" % (len(failures), names))


READ_RETRIES = (0.05, 0.1, 0.2, 0.4)   # A reader's brief hold (an antivirus scan, Windows) is waited out.


def _read_held(path):
    """The file's bytes; a refusal (Windows: another process holds it without sharing) is tried again a moment later,
    then raised."""
    for delay in READ_RETRIES + (None,):
        try:
            return shared_replace.read_bytes(path)
        except PermissionError:
            if delay is None or os.name != "nt":
                raise
            time.sleep(delay)


def _read(path):
    """The record's JSON value, or None when it is damaged: not UTF-8 or not JSON (bytes that never change on a
    retry, so not ours to repair: its owner reads, or keeps aside, a damaged record as it always does). A record that
    cannot be read now raises ``OSError``: it may be readable next time, so it is never taken for a damaged one."""
    data = _read_held(path)
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):   # UnicodeDecodeError included; nesting past Python's limit too.
        return None


def relocate_top_records(current, olds, retained=(), only=None):
    """Rewrite the top-level ``*.json`` records of ``current`` that name a place in an old spelling; returns how many.
    A place inside a ``retained`` folder (a native profile left where it was) is not moved. Raises
    ``RecordsUnfinished`` after the others when a record could not be read or rewritten."""
    count, failures = 0, []
    retained_spellings = [str(state_paths.relocate(path, olds[0], old)) for path in retained for old in olds]
    with os.scandir(current) as listing:   # A folder that cannot be listed raises: never read as "no records".
        names = sorted(entry.name for entry in listing if entry.name.endswith(".json"))
    for path in (current / name for name in names if only is None or name in only):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = _read_held(path).decode("utf-8")
        except UnicodeError:
            continue   # Damaged (not UTF-8): see ``_read``.
        except OSError as error:
            failures.append((path.name, error))
            continue
        if not any(old in text or json.dumps(old)[1:-1] in text or json.dumps(old, ensure_ascii=False)[1:-1] in text
                   for old in olds):
            continue   # Cheap test first: most records name no path at all.
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            continue   # Damaged (or nested past Python's limit): see ``_read``.
        try:
            moved = _moved(value, olds, str(current), retained_spellings)
        except RecursionError:
            continue   # Nested deeper than Python's limit: damaged for our purpose, left to its owner.
        if moved != value:
            try:
                write_json(path, moved)
            except OSError as error:
                failures.append((path.name, error))
                continue
            count += 1
    if failures:
        raise RecordsUnfinished(failures)
    return count


CHAT_LISTS, SNAPSHOTS = "+chat-lists", "+snapshots"   # Pending steps that are not one top-level record.


def update(current, source, retained=(), also=(), kept_root=None, only=None):
    """Every record step of one carry-over, each whatever another meets: the chat lists combined (``kept_root``),
    snapshot records and top-level records rewritten. Returns ``(records changed, pending)``: what could not be done
    now (record names, ``CHAT_LISTS``, ``SNAPSHOTS``), for a later launch to try again (``only``). Idempotent.
    ``also``: more spellings of the old folder (as the running app spells it, when a link in the user folder's path
    was resolved). A damaged snapshot record is skipped, as a damaged top-level one is."""
    from .state_migration import relocate_snapshot_records
    olds = spellings(source) + [str(old) for old in also if str(old) not in spellings(source)]
    count, pending = 0, []
    if kept_root is not None and (only is None or CHAT_LISTS in only):
        try:
            combine_chat_lists(current, kept_root)
        except OSError:
            pending.append(CHAT_LISTS)
    # Snapshot storage never follows a link to its folder (checkpoint_filesystem), so a linked state folder holds
    # no snapshot this build could restore, and none to rewrite.
    if not current.is_symlink() and (only is None or SNAPSHOTS in only):
        try:
            count += sum(relocate_snapshot_records(current, Path(old), skip_damaged=True) for old in olds)
        except (OSError, ValueError):   # A record the store cannot rewrite (one never written by this store).
            pending.append(SNAPSHOTS)
    # "." is the state folder that could not be listed: every top-level record, not a file named ".".
    names = None if only is None or "." in only else {name for name in only if not name.startswith("+")}
    if names is None or names:
        try:
            count += relocate_top_records(current, olds, retained, only=names)
        except RecordsUnfinished as unfinished:
            pending += [name for name, _error in unfinished.failures]
        except OSError:
            pending.append(".")   # The folder itself could not be listed: every record again.
    return count, pending


def _chat_list(path):
    """A chat list, or None when there is none or it is damaged (``_read``). One that cannot be read now raises
    ``OSError``, so the carry-over stays unfinished instead of leaving its chats out of the list for good."""
    try:
        value = _read(path)
    except FileNotFoundError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("sessions"), list):
        return None
    return value


def combine_chat_lists(current, kept_root):
    """When old chat lists were kept aside (the new folder had its own), list their chats in the new one."""
    new = _chat_list(current / CHAT_LIST)
    if new is None:
        return 0
    listed, added = {record.get("id") for record in new["sessions"] if isinstance(record, dict)}, []
    for path in sorted(Path(kept_root).glob(CHAT_LIST + "*")):   # ``conversations.json (2)``: a later return.
        old = _chat_list(path)
        for record in old["sessions"] if old is not None else ():
            identity = record.get("id") if isinstance(record, dict) else None
            if (isinstance(identity, str) and identity not in listed
                    and (current / "sessions" / (identity + ".json")).is_file()):
                listed.add(identity)
                added.append(record)
    if added:
        write_json(current / CHAT_LIST, dict(new, sessions=new["sessions"] + added))
    return len(added)
