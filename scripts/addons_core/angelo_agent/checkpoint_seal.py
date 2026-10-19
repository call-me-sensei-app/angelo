# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keyed seal binding each READY checkpoint record to its own components (doc/angelo/checkpoint_policy.rst).

Threat model. The seal binds a record's version, storage, capture order and components (the BASE
``scene.blend`` and, for Sculpt, the runtime companion) to its own capture, so Restore refuses a
component or record of another capture, a record copied from another profile and accidental mixes of
checkpoint folders before any byte is restored. It does not defend against a same-user attacker with
write access to the profile (who can read the key, re-seal a forged record or reset a file's time).

Two bindings, so no request reads back what it just wrote: identity (``<key id>:<HMAC>``) over an
ordinary version 3 BASE's size, modification time (microseconds) and inode, sealed at publication; on
a file system with coarse timestamps (HFS+, FAT, exFAT, SMB) publication binds content instead. Content
(``<key id>.<HMAC>``) over the BLAKE2b-256 of the BASE: a version 4 pair, and every record the snapshot
worker's deduplication stores (it verifies the identity seal over the bytes it reads anyway).

Files, in the profile's private ``checkpoint-seal`` folder:

* ``key``: 32 random bytes (0600), created only by sealing (temporary file, fsync, no-overwrite link).
  One that is not exactly 32 bytes is moved aside (``damaged-<id>.key``) with a chat notice. This
  process keeps every key it used. A request seals with its loaded key after one ``stat`` of the file,
  compared with the file's device, inode, size and modification time when this process last read or
  wrote it; the snapshot worker re-reads the file on every seal. A missing or damaged file is written
  again from that key. A file holding another valid key (a second Angelo, a synced profile) is never
  overwritten: it seals new captures, and this process's key is kept beside it (``retired-<id>.key``),
  with a chat notice. A process that quits before its next seal after a replacement keeps no copy.
* ``keys.json``: this profile's key identifiers, so another profile's seal is told from a lost key.
* ``cutoff.json``: the capture boundary at which sealing began; every READY version 4 record and every
  READY version 3 record from it on must be sealed. Missing or damaged, it is derived from the earliest
  sealed record and rewritten by the next seal outside a request.

Callers hold ``_transaction(root)``.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from pathlib import Path

from .checkpoint_filesystem import (_open_file, _private_directory, _read_json, _replace_file,
                                    _unlink_file, publish_new_file, sync_directory)
from .ui_text import n_

FOLDER = "checkpoint-seal"
KEY = "key"
CUTOFF = "cutoff.json"
KEYS = "keys.json"
KEY_BYTES = 32
FIELD = "component_seal"
CONTENT, IDENTITY = ".", ":"
_KEY_ID = b"angelo checkpoint seal key id"
_HEX = frozenset("0123456789abcdef")

# Restore refusals: stored diagnostics, shown inside a translated Restore outcome.
MISMATCH = ("This checkpoint's components are not bound to its own capture (missing or wrong seal); "  # i18n: stored diagnostic
            "it cannot be restored")
KEY_UNAVAILABLE = ("The checkpoint seal key is missing or damaged, so earlier checkpoints can't be verified; "  # i18n: stored diagnostic
                   "this one cannot be restored")
FOREIGN = ("This checkpoint was sealed by another Angelo profile's key, so it can't be verified here; "  # i18n: stored diagnostic
           "it cannot be restored")
NOTICE_DAMAGED = n_("The checkpoint seal key was damaged, so it was set aside and a new key was started. "
                    "Checkpoints captured before this can't be verified for Restore; new checkpoints are unaffected.")
NOTICE_MISSING = n_("The checkpoint seal key was missing, so a new key was started. Checkpoints captured "
                    "before this can't be verified for Restore; new checkpoints are unaffected.")
NOTICE_RESTORED = n_("The checkpoint seal key file was missing or damaged, so it was written again from this "
                     "session's copy; checkpoints stay verifiable.")
NOTICE_REPLACED = n_("The checkpoint seal key file now holds another key, for example from another Angelo using this "
                     "profile or a profile synced from another computer. New checkpoints use that key, and this "
                     "session's key was kept beside it, so earlier checkpoints stay verifiable.")
NOTICE_REPLACED_UNKEPT = n_("The checkpoint seal key file now holds another key, for example from another Angelo using "
                            "this profile or a profile synced from another computer. New checkpoints use that key. "
                            "This session's key could not be kept, so checkpoints captured before this can't be "
                            "verified for Restore once Angelo quits.")

_lock = threading.Lock()
_keys = {}      # Profile root -> {key id: secret} this process created or read.
_current = {}   # Profile root -> (key id, secret, key file identity) a request seals with; refreshed by every worker seal.
_notices = {}   # Profile root -> notice templates not yet shown.
_derived = {}   # Profile root -> cutoff derived from its sealed records while cutoff.json is missing or damaged.
_owners = {}    # (profile root, key id) -> whether that key was this profile's.
_DAMAGED = object()


def well_formed(value):
    """``<32 hex key id>`` ``.`` (content) or ``:`` (identity) ``<64 hex HMAC>``: structure only."""
    if type(value) is not str or len(value) != 97 or value[32] not in (CONTENT, IDENTITY):
        return False
    return set(value[:32]) <= _HEX and set(value[33:]) <= _HEX


def _key_id(secret):
    return hmac.new(secret, _KEY_ID, hashlib.sha256).hexdigest()[:32]


def _remember(root, secret, signature=None):
    """Keep ``secret`` for verification; with the key file's ``signature``, it is also the key requests seal with."""
    identity = _key_id(secret)
    with _lock:
        _keys.setdefault(str(root), {})[identity] = secret
        if signature is not None:
            _current[str(root)] = identity, secret, signature
    return identity


def _signature(info):
    """What tells one key file from its replacement without reading it: device, inode, size, modification time."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_key(folder, name=KEY):
    """(secret, signature) of a key file: secret None when absent, False when it is not exactly one key."""
    try:
        descriptor = _open_file(folder, name, os.O_RDONLY)
    except FileNotFoundError:
        return None, None
    with os.fdopen(descriptor, "rb", buffering=0) as handle:
        signature = _signature(os.fstat(handle.fileno()))
        secret = handle.read(KEY_BYTES + 1)
    return (secret if len(secret) == KEY_BYTES else False), signature


def _retired(identity):
    return "retired-" + identity + ".key"


def _retire(folder, identity, secret):
    """Keep a replaced key beside the profile's key (no overwrite), so its records stay verifiable."""
    try:
        _publish(folder, _retired(identity), secret)
    except FileExistsError:
        pass
    except OSError:
        return False
    kept, _ = _read_key(folder, _retired(identity))
    return bool(kept) and _key_id(kept) == identity


def _publish(folder, name, payload):
    """A new file holding ``payload``: temporary file, fsync, no-overwrite link (FileExistsError if taken)."""
    temporary = "." + str(uuid.uuid4()) + ".tmp"
    descriptor = _open_file(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        publish_new_file(folder, temporary, name)
        sync_directory(folder)
    finally:
        try:
            _unlink_file(folder, temporary)
        except FileNotFoundError:
            pass


def _cutoff(folder):
    """The boundary sealing began at, None without a cutoff file, _DAMAGED when it cannot be read."""
    try:
        value = _read_json(folder, CUTOFF)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return _DAMAGED
    sealed_from = value.get("sealed_from") if isinstance(value, dict) else None
    return sealed_from if type(sealed_from) is int and 0 <= sealed_from <= 2**53 - 1 else _DAMAGED


def _sealed_records(root):
    """(boundary, key id, record id) of every READY record of the profile that carries a seal.

    Only for rare paths (a missing or damaged cutoff or key, a refusal); a request never scans.
    """
    found = []
    try:
        sessions = [entry for entry in (Path(root) / "checkpoints").iterdir() if not entry.is_symlink()]
    except OSError:
        return found
    for session in sessions:
        try:
            folders = [entry for entry in session.iterdir() if not entry.is_symlink()]
        except OSError:
            continue
        for folder in folders:
            path = folder / "checkpoint.json"
            try:
                if path.is_symlink() or path.stat().st_size > 1 << 20:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get("state") == "READY" and well_formed(record.get(FIELD)):
                boundary = record.get("boundary")
                found.append((boundary if type(boundary) is int else None, record[FIELD][:32], record.get("id")))
    return found


def _derived_cutoff(root):
    """The earliest sealed record's boundary, standing in for a missing or damaged cutoff file."""
    with _lock:
        if str(root) in _derived:
            return _derived[str(root)]
    boundaries = [boundary for boundary, _, _ in _sealed_records(root) if boundary is not None]
    value = min(boundaries) if boundaries else None
    with _lock:
        _derived[str(root)] = value
    return value


def _write_small(folder, name, payload):
    """Replace a small seal-folder file atomically (temporary file, fsync, rename)."""
    temporary = "." + str(uuid.uuid4()) + ".tmp"
    descriptor = _open_file(folder, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _replace_file(folder, temporary, name)
        sync_directory(folder)
    finally:
        try:
            _unlink_file(folder, temporary)
        except FileNotFoundError:
            pass


def _known_keys(folder):
    try:
        value = _read_json(folder, KEYS)
    except (OSError, ValueError):
        return []
    keys = value.get("keys") if isinstance(value, dict) else None
    return [key for key in keys if type(key) is str and len(key) == 32] if type(keys) is list else []


def _record_key(folder, *identities):
    """Remember these as this profile's keys (for telling another profile's seals apart)."""
    keys = _known_keys(folder)
    added = [identity for identity in dict.fromkeys(identities) if identity not in keys]
    if added:
        _write_small(folder, KEYS, json.dumps({"version": 1, "keys": keys + added}).encode("ascii"))


def _signing_key(root, boundary):
    """The key file's key, created or replaced as needed; never fails because the key file is damaged.

    A missing or damaged file is written again from this process's key when it has one, so every
    record it sealed stays verifiable; only a process without one starts a new key.
    """
    with _private_directory(root / FOLDER, create=True) as folder:
        began = _cutoff(folder)
        if began is None or began is _DAMAGED:
            # A missing or damaged cutoff is rebuilt from the earliest sealed record, so legacy captures
            # before it stay admitted and later unsealed ones stay refused; a new profile starts here.
            with _lock:
                _derived.pop(str(root), None)
            derived = _derived_cutoff(root)
            _write_small(folder, CUTOFF, json.dumps({"version": 1, "sealed_from": boundary if derived is None
                                                     else min(derived, boundary)}).encode("ascii"))
            with _lock:
                _derived.pop(str(root), None)
        with _lock:
            held = _current.get(str(root))
        secret, signature = _read_key(folder)
        notice = None
        if not secret:
            if secret is False:
                _replace_file(folder, KEY, "damaged-" + str(uuid.uuid4()) + ".key")
                sync_directory(folder)
                notice = NOTICE_DAMAGED
            else:  # Missing: say so whenever earlier sealed checkpoints now cannot be verified.
                notice = NOTICE_MISSING if _sealed_records(root) else None
            if held is not None:
                notice = NOTICE_RESTORED
            else:  # The keys of this profile's own sealed records were its keys, even if their listing is gone.
                _record_key(folder, *(key for _, key, _ in _sealed_records(root)))
            try:
                _publish(folder, KEY, held[1] if held is not None else secrets.token_bytes(KEY_BYTES))
            except FileExistsError:
                pass  # Another process wrote a key first: use that one.
            secret, signature = _read_key(folder)
            if not secret:
                raise OSError("The checkpoint seal key could not be created")
        identity = _key_id(secret)
        if held is not None and held[0] != identity:
            # Another valid key took the file's place (a second process, a synced profile): never overwrite
            # it. It seals from now on; this process's key stays in memory and is kept beside it on disk.
            notice = NOTICE_REPLACED if _retire(folder, held[0], held[1]) else NOTICE_REPLACED_UNKEPT
        if notice is not None:
            with _lock:
                _notices.setdefault(str(root), []).append(notice)
        _remember(root, secret, signature)
        _record_key(folder, identity)
        return secret, identity


def _request_key(root, boundary):
    """The key a request seals with: loaded once per process; afterwards one ``stat`` of its file.

    The file must still be the one this process last read or wrote (same device, inode, size and
    modification time). Otherwise ``_signing_key`` reads it: a missing or damaged file is written
    again from this process's key, another valid key is adopted for new seals with this process's
    key kept beside it, so the records this session seals stay verifiable after a restart even
    when no snapshot worker runs (Sculpt-only).
    """
    with _lock:
        loaded = _current.get(str(root))
    if loaded is not None:
        try:
            if _signature(os.stat(Path(root) / FOLDER / KEY, follow_symlinks=False)) == loaded[2]:
                return loaded[1], loaded[0]
        except FileNotFoundError:
            pass
    return _signing_key(root, boundary)


def _known_key(root, identity):
    """The secret named by ``identity``: kept by this process, the key file or a key kept beside it after it
    was replaced. Never creates one."""
    with _lock:
        secret = _keys.get(str(root), {}).get(identity)
    if secret is not None:
        return secret
    if type(identity) is not str or len(identity) != 32 or not set(identity) <= _HEX:
        return None
    try:
        with _private_directory(root / FOLDER) as folder:
            secret, _ = _read_key(folder)
            if not secret or _key_id(secret) != identity:
                secret, _ = _read_key(folder, _retired(identity))
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not secret or _key_id(secret) != identity:
        return None
    _remember(root, secret)
    return secret


def _message(record, binding):
    facts = {"capture_id": record["id"], "version": record.get("version", 1), "storage": record.get("storage", "full"),
             "boundary": record.get("boundary"), "scene_bytes": record["scene_bytes"], "binding": binding}
    if facts["version"] == 4:
        sculpt = record["sculpt"]
        facts["sculpt"] = {key: sculpt[key] for key in ("filename", "format_version", "bytes", "hash", "owner_count")}
    return json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _identity_binding(size, mtime_ns, inode):
    # Microseconds: what time-preserving tools (utimes, backups) carry. The inode (the NTFS file
    # index on Windows) tells two same-size files with one shared timestamp apart.
    return {"identity": [int(size), int(mtime_ns) // 1000, int(inode)]}


def coarse_timestamps(mtime_ns):
    """Whether a modification time has no sub-millisecond part: HFS+, FAT/exFAT and SMB keep 10 ms to 2 s,
    so two captures can share one timestamp there (a sub-millisecond file system does so once in a million)."""
    return int(mtime_ns) % 1_000_000 == 0


def _mac(secret, identity, separator, record, binding):
    return identity + separator + hmac.new(secret, _message(record, binding), hashlib.sha256).hexdigest()


def seal(root, record, base_hash, *, request=False):
    """A content seal over this READY record and its BASE digest (lowercase hex BLAKE2b-256).

    ``request``: sealed on a request's capture path, with the key this process loaded.
    """
    secret, identity = (_request_key if request else _signing_key)(root, record.get("boundary") or 0)
    return _mac(secret, identity, CONTENT, record, {"content": base_hash})


def seal_identity(root, record, size, mtime_ns, inode):
    """An identity seal over this READY full copy and its BASE size, modification time and inode (no byte read)."""
    secret, identity = _request_key(root, record.get("boundary") or 0)
    return _mac(secret, identity, IDENTITY, record, _identity_binding(size, mtime_ns, inode))


def binding(record):
    """``"content"``, ``"identity"`` or None for an unsealed record."""
    declared = record.get(FIELD)
    if not well_formed(declared):
        return None
    return "identity" if declared[32] == IDENTITY else "content"


def requires_seal(root, record):
    """Whether this READY record must carry a seal (every v4; v3 from the profile's cutoff on)."""
    version = record.get("version", 1)
    if record.get("state") != "READY" or version < 3:
        return False
    if version == 4:
        return True
    try:
        with _private_directory(root / FOLDER) as folder:
            began = _cutoff(folder)
    except (FileNotFoundError, NotADirectoryError):
        began = None
    if began is None or began is _DAMAGED:
        began = _derived_cutoff(root)
    return began is not None and record.get("boundary", 0) >= began


def precheck(root, record):
    """Refuse what needs no BASE bytes to refuse: a missing required seal or an unavailable key."""
    declared = record.get(FIELD)
    if declared is None:
        if requires_seal(root, record):
            raise ValueError(MISMATCH)
        return
    if not well_formed(declared):
        raise ValueError(MISMATCH)
    if _known_key(root, declared[:32]) is None:
        raise ValueError(KEY_UNAVAILABLE if _profile_key_id(root, declared[:32], record.get("id")) else FOREIGN)


def _profile_key_id(root, identity, record_id):
    """Whether ``identity`` was one of this profile's keys: listed as such, or sealing another of its records."""
    with _lock:
        known = _owners.get((str(root), identity))
    if known is None:
        try:
            with _private_directory(root / FOLDER) as folder:
                known = identity in _known_keys(folder)
        except (FileNotFoundError, NotADirectoryError):
            known = False
        known = known or any(key == identity and other != record_id for _, key, other in _sealed_records(root))
        with _lock:
            _owners[(str(root), identity)] = known
    return known


def verify(root, record, base_hash=None, *, identity=None):
    """Refuse a record whose required seal is missing, unverifiable or wrong (MISMATCH vs KEY_UNAVAILABLE).

    A content seal is checked against ``base_hash``, an identity seal against ``identity``, the
    BASE's ``(size, mtime_ns, inode)``; a seal whose binding the caller cannot supply is a mismatch.
    """
    precheck(root, record)
    declared = record.get(FIELD)
    if declared is None:
        return  # A legacy capture: admitted as before.
    if declared[32] == IDENTITY:
        if identity is None:
            raise ValueError(MISMATCH)
        bound = _identity_binding(*identity)
    else:
        if base_hash is None:
            raise ValueError(MISMATCH)
        bound = {"content": base_hash}
    expected = _mac(_known_key(root, declared[:32]), declared[:32], declared[32], record, bound)
    if not hmac.compare_digest(declared, expected):
        raise ValueError(MISMATCH)


def take_notices(root):
    """Notice templates for the chat (a damaged or missing key replaced), each returned once."""
    with _lock:
        return _notices.pop(str(root), [])


class _HashingWriter:
    """A copy destination that also digests every byte written (BLAKE2b-256, as ``scene_hash``)."""

    def __init__(self, handle):
        self.handle, self.digest = handle, hashlib.blake2b(digest_size=32)

    def write(self, chunk):
        self.digest.update(chunk)
        return self.handle.write(chunk)


def copy_sealed(root, record, copy, handle):
    """Restore a version 3 BASE with ``copy(destination)`` into ``handle``, verified against its seal.

    A stored copy's seal binds ``scene_hash``, which reassembly then proves byte for byte. A full
    copy's identity seal is checked against the stable stat ``copy`` returns for the bytes it copied;
    a content seal binds the bytes themselves, hashed as they are copied.
    """
    if record.get("storage", "full") == "store":
        verify(root, record, record["scene_hash"])
        copy(handle)
        return
    precheck(root, record)
    if binding(record) == "content":
        hashing = _HashingWriter(handle)
        copy(hashing)
        verify(root, record, hashing.digest.hexdigest())
        return
    copied = copy(handle)
    verify(root, record, identity=(copied.st_size, copied.st_mtime_ns, copied.st_ino))
