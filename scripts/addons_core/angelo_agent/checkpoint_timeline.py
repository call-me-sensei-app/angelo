# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Durable profile-wide order for captures and their first pre-write versions.

The profile transaction must be held throughout allocation and the file effect.
Gaps after failed operations are harmless; a missing clock for ordered history
is not silently recreated. Legacy journals stay immutable and unclaimed.

Allocation checks the clock against the whole recorded history (every snapshot
record) only when it cannot trust the clock: the first allocation of a process,
and whenever the clock file is not exactly the one this process last validated
or wrote (identity and content). Any other writer (another process, a restored
backup, a stale copy, a hand edit) replaces the file and so forces the full
check. A trusted clock is newer than every order any record can hold, so
skipping the rescan cannot recycle an order, and each request's capture stays
O(1) in the number of stored snapshots. Restore and retention still validate
the whole history every time, and a locked inventory that finds a damaged
history withdraws the trust (``forget_trust``) so the next allocation refuses.
"""

import hashlib
import json
from .checkpoint_filesystem import _file_info, _private_directory, _read_json, _write_json
from .checkpoint_transactions import holds_transaction

NAME = "checkpoint-timeline.json"
MAX_SEQUENCE = 2**53 - 1
# Profile root -> (clock file identity, clock content) this process validated
# against the whole history or wrote itself. Used only under the transaction.
_TRUSTED = {}


def legacy_fingerprint(records):
    values = [[record["ref"], record["created"], record.get("legacy_captured", record["state"] == "READY"), record["files"]]
              for record in records if not record.get("boundary")]
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, allow_nan=False).iterencode(sorted(values)):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def forget_trust(root):
    """Validate the whole history again at the next allocation for ``root``."""
    _TRUSTED.pop(str(root), None)


def read_timeline(store):
    return _read_clock(store)[0]


def _read_clock(store):
    """The validated clock and its file identity (None for a clock not yet written)."""
    with _private_directory(store.root, create=True) as folder:
        try:
            before = _file_info(folder, NAME)
            clock = _read_json(folder, NAME)
            identity = _file_identity(before)
            if identity != _file_identity(_file_info(folder, NAME)):
                identity = None  # Replaced while reading: never trusted.
        except FileNotFoundError:
            identity = None
            inventory = store._inventory(validate_targets=False)
            if inventory["issues"] or any(item.get("boundary") for item in inventory["checkpoints"]):
                raise ValueError("Ordered snapshot timeline is missing or history is incomplete; restore its original profile metadata")
            clock = {"version": 1, "sequence": 0, "boundary": 0,
                     "legacy_fingerprint": legacy_fingerprint(inventory["checkpoints"])}
        if (not isinstance(clock, dict) or not {"version", "sequence", "boundary"} <= set(clock) or
                set(clock) - {"version", "sequence", "boundary", "legacy_fingerprint"} or
                type(clock["version"]) is not int or clock["version"] != 1 or
                type(clock["sequence"]) is not int or type(clock["boundary"]) is not int or
                not 0 <= clock["boundary"] <= clock["sequence"] <= MAX_SEQUENCE):
            raise ValueError("Invalid ordered snapshot timeline; no file mutation was applied")
        # Earlier development-only clocks admitted no legacy metadata. Missing
        # evidence must not manufacture admission for subsequently added legacy.
        clock.setdefault("legacy_fingerprint", legacy_fingerprint([]))
        fingerprint = clock["legacy_fingerprint"]
        if type(fingerprint) is not str or len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("Invalid legacy timeline admission evidence")
        return clock, identity


def allocate_event(store, *, capture=False):
    """Allocate the next profile-wide sequence; the caller holds the transaction.

    The whole-history check runs only for a clock this process does not trust
    (see the module documentation); a trusted clock costs one read and one write.
    """
    clock, identity = _read_clock(store)
    key = str(store.root)
    locked = holds_transaction(store.root)
    if not locked or identity is None or _TRUSTED.get(key) != (identity, clock):
        _TRUSTED.pop(key, None)
        inventory = store._inventory(validate_targets=False)
        if inventory["issues"]:
            raise ValueError("Snapshot history cannot admit another event: " + inventory["issues"][0]["error"])
        validate_order(inventory["checkpoints"], clock)
    if clock["sequence"] == MAX_SEQUENCE:
        raise ValueError("The snapshot timeline has reached its sequence limit")
    clock["sequence"] += 1
    if capture:
        clock["boundary"] = clock["sequence"]
    _TRUSTED.pop(key, None)  # Not trusted again until the new clock is durable.
    with _private_directory(store.root) as folder:
        _write_json(folder, NAME, clock)
        if locked:
            _TRUSTED[key] = (_file_identity(_file_info(folder, NAME)), dict(clock))
    return clock["sequence"], clock["boundary"]


def version_key(item):
    return item["root"], item["path"], item.get("sequence")


def validate_order(records, clock):
    if legacy_fingerprint(records) != clock["legacy_fingerprint"]:
        raise ValueError("Legacy capture/journal history changed after ordered admission; its write timing cannot be proved")
    seen = set()
    for record in records:
        boundary = record.get("boundary")
        if boundary is not None:
            if boundary in seen or boundary > clock["sequence"] or boundary > clock["boundary"]:
                raise ValueError("Snapshot capture order conflicts with its durable timeline")
            seen.add(boundary)
        for entry in record["files"]:
            sequence = entry.get("sequence")
            if sequence is not None:
                if sequence in seen or sequence > clock["sequence"] or entry["window"] > clock["boundary"]:
                    raise ValueError("File-write order conflicts with its durable timeline")
                seen.add(sequence)
                effect = entry["effect_sequence"]
                if effect > clock["sequence"] or (effect != sequence and effect in seen):
                    raise ValueError("Latest file-effect order conflicts with its durable timeline")
                seen.add(effect)


def current_kinds(records):
    """Latest durable effect per path authorizes only its live-output bound."""
    latest = {}
    for record in records:
        for entry in record["files"]:
            if entry.get("phase", "ready") != "ready":
                continue
            order = (1, entry["effect_sequence"]) if "sequence" in entry else (0, record["created"])
            key = entry["root"], entry["path"]
            if key not in latest or order > latest[key][0]:
                kind = entry.get("effect_kind", "studio_export" if entry.get("kind") in {"studio_export", "studio_export_safety"} else "file_tool")
                latest[key] = order, kind
    return {key: value[1] for key, value in latest.items()}


def planned_entry(entry, kinds):
    return dict(entry, current_kind=kinds.get((entry["root"], entry["path"]), entry.get("kind", "file_tool")))
