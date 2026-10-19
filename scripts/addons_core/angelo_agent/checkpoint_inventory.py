# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measured private inventory and full live-output/journal admission.

One pass over every snapshot record of the profile. It is the O(n) path, so it
runs only where whole-history evidence is required: restore plans, retention
plans and reviews, worker recovery, and an allocation that cannot trust its
clock (checkpoint_timeline.py), never for an ordinary request capture. Folders
are walked with held descriptors (``_private_child``) and each record is read
directly, so the pass costs a few system calls per snapshot.

Callers normally hold the profile transaction. The snapshot worker also calls
it without the transaction to probe whether maintenance has anything to do
(``probe=True``); such a read can see a write in progress (a reservation
without metadata yet, a temporary file), which shows as an issue, so retention
then plans again under the transaction and orphan cleanup re-checks its
suspects there. A probe re-measures only records whose folder or files changed
since it last measured them (``checkpoint_inventory_cache``) and pauses for
the main thread as it goes (``background_pauses``). Issues found while the
transaction is held withdraw the timeline's trust, so the next allocation
validates the whole history (a false alarm costs only that scan).
"""

from . import checkpoint_inventory_cache
from .background_pauses import pause
from .checkpoint_policy import _digest, _identity, checkpoint_ref, journal_file_limit
from .checkpoint_filesystem import _file_info, _private_child, _private_directory, _read_json, list_names
from .checkpoint_capture_state import capture_activity, decode_record, is_bundle, scene_usable
from .checkpoint_capture_records import output_names
from .checkpoint_timeline import NAME, current_kinds, forget_trust, planned_entry, read_timeline, validate_order
from .checkpoint_transactions import holds_transaction


def _measure(owner, session, identity, folder):
    """One validated record and its files, read through the held record folder."""
    record = decode_record(_read_json(folder, "checkpoint.json"), identity)
    storage = record.get("storage", "full")
    # A deduplicated record may briefly keep its verified full copy (crash
    # between adoption and deletion).
    allowed = {"checkpoint.json"} | ({"scene.blend"} if record["state"] != "PRUNED" else set())
    if is_bundle(record) and record["state"] != "PRUNED":
        allowed.add("sculpt.runtime")  # Measurable declaration, never deletion/scene authority.
        allowed.update(output_names(role)[0] for role in record.get("capture_outputs", {}).get("roles", {}))
    allowed |= {item["backup"] for item in record["files"] if item["existed"]}
    names = sorted(list_names(folder))
    if set(names) - allowed:
        raise ValueError("Unrecognized files in snapshot storage; no pruning is permitted")
    infos = [(name, _file_info(folder, name)) for name in names]
    if not is_bundle(record) and record["state"] == "READY" and storage == "full" and "scene.blend" not in names:
        raise ValueError("The Blender snapshot is missing")
    if not is_bundle(record) and record["state"] == "READY" and storage == "full" and not _file_info(folder, "scene.blend").st_size:
        raise ValueError("The Blender snapshot is empty")
    for item in record["files"]:
        if item["existed"] and item.get("phase", "ready") != "discarded" and (_file_info(folder, item["backup"]).st_size > journal_file_limit(item, backup=True)):
            raise ValueError("Oversized journal backup")
    fingerprint = _digest({"record": record, "files": [(name, info.st_size, info.st_mtime_ns,
                           info.st_dev, info.st_ino) for name, info in infos]})
    active = capture_activity(owner.root, session, identity) if holds_transaction(owner.root) else None
    usable = scene_usable(record)
    if is_bundle(record):
        sizes = {name: info.st_size for name, info in infos}
        usable = (usable and storage == "full"
                  and sizes.get("scene.blend", 0) == record.get("scene_bytes", -1)
                  and sizes.get("sculpt.runtime", 0) == record.get("sculpt", {}).get("bytes", -1)
                  and not {"scene.blend@", "sculpt.runtime@"}.intersection(names))
    return {"ref": checkpoint_ref(session, identity), "session_id": session, "id": identity,
        "version": record.get("version", 1), "scene_usable": usable,
        "capture_validation": record.get("capture_validation"), "capture_active": active,
        "created": record["created"], "boundary": record.get("boundary"), "state": record["state"], "bytes": sum(info.st_size for _, info in infos),
        "legacy_captured": record["state"] == "READY" or (record["state"] == "PRUNED" and record.get("prune_reason") == "retention"),
        "files": record["files"], "purpose": record["purpose"], "protected": record["protected"],
        "scene_bytes": (record["scene_bytes"] if storage == "store" else
                        next((info.st_size for name, info in infos if name == "scene.blend"), 0)),
        "storage": storage, "scene_file": "scene.blend" in names,
        "original_path": record["original_path"],
        "capture_ms": record.get("capture_ms"), "fingerprint": fingerprint}


def _scan(owner, records, issues):
    try:
        with _private_directory(owner.root / "checkpoints") as base:
            for session in sorted(list_names(base)):
                try:
                    _identity(session)
                    with _private_child(base, session) as session_folder:
                        for identity in sorted(list_names(session_folder)):
                            if identity == "recoveries":
                                continue
                            pause()
                            try:
                                _identity(identity)
                                with _private_child(session_folder, identity) as folder:
                                    records.append(_measure(owner, session, identity, folder))
                            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                                issues.append({"session_id": session, "id": identity, "error": str(error)})
                except (OSError, ValueError, TypeError, RecursionError) as error:
                    issues.append({"session_id": session, "error": str(error)})
    except FileNotFoundError:
        pass


def inventory(owner, *, validate_targets=True, probe=False):
    """Every record of the profile, measured, with any issues and a token of the whole result.

    ``probe``: a lock-free probe (the snapshot worker's), which may reuse the measurements of
    unchanged records and has no token (None). Ignored under the transaction, where every record
    is measured afresh.
    """
    records, issues = [], []
    locked = holds_transaction(owner.root)
    probed = probe and not locked and checkpoint_inventory_cache.scan(owner, records, issues, _measure)
    if not probed:
        _scan(owner, records, issues)
    if issues and locked:
        forget_trust(owner.root)
    if validate_targets:
        try:
            if any(record.get("boundary") for record in records) or (owner.root / NAME).exists():
                validate_order(records, read_timeline(owner))
        except (OSError, ValueError, TypeError) as error:
            issues.append({"error": str(error)})
            if locked:
                forget_trust(owner.root)
        kinds = current_kinds(records)
        for record in records:
            try:
                for entry in record["files"]:
                    if entry.get("phase", "ready") == "pending":
                        raise ValueError("A pending file mutation needs reconciliation before restore or retention")
                    if entry.get("phase", "ready") != "discarded":
                        owner._target(planned_entry(entry, kinds))
            except (OSError, ValueError, TypeError) as error:
                issues.append({"session_id": record["session_id"], "id": record["id"], "error": str(error)})
    result = {"checkpoints": records, "issues": issues, "total_bytes": sum(item["bytes"] for item in records)}
    # A probe never backs a removal (that plans again under the transaction): it needs no token of the whole result.
    result["token"] = None if probed else _digest(result)
    return result
