# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Store maintenance after removals: orphan manifests, garbage and compaction.

Runs on the snapshot worker thread. Order matters for safety:

1. ``drop_orphan_manifests`` (profile transaction, then store lock): a manifest
   whose snapshot record is PRUNED or no longer exists is removed. Manifests of
   READY records are never touched here; one that belongs to an unfinished
   ingestion is replaced or adopted by that ingestion. Unreadable records are
   kept (fail closed). Finding orphans reads every record, so a lock-free probe
   first selects the suspects (manifests without a readable, unpruned record);
   only those are re-checked and dropped under the transaction, and a run with
   no suspects never takes it. An orphan the probe misses is dropped next run.
2. ``collect_garbage`` (store lock): reference counts are rebuilt from the
   remaining manifests and only unreferenced chunks/packs are deleted. Only a
   dropped manifest leaves chunks unreferenced, so it runs only when this
   process dropped one since it last collected the store, and at least once
   per process (``snapshot_store_writer.garbage_possible``); a skipped run
   deletes nothing, so it can only leave garbage for later, never lose data.
3. ``compact`` (profile transaction, then store lock, one pack per hold):
   live chunks of mostly-dead packs are copied, verified and re-pointed.

While holding the profile transaction the store lock is only tried briefly,
so another app's long ingestion can never stall Blender's main thread.
"""

import uuid

from .checkpoint_capture_state import require_capture_idle
from .checkpoint_inventory import inventory
from .checkpoint_transactions import _transaction
from .checkpoints import CheckpointStore
from .snapshot_store import SnapshotStore, StoreBusy
from .snapshot_store_writer import garbage_possible

COMPACT_PACKS_PER_RUN = 8
STORE_LOCK_WAIT_UNDER_TRANSACTION = 0.5


def profile_checkpoints(root):
    """Profile-wide handle; the nil UUID never owns snapshots of its own."""
    return CheckpointStore(root, str(uuid.UUID(int=0)))


def _orphan_suspects(checkpoints, store, probed=None):
    """Advisory suspects; every unpruned record owns its manifest, including v4.

    ``probed``: the retention probe's lock-free inventory of this same run, shared instead of a second
    scan. Both are advisory: suspects are checked again under the transaction.
    """
    refs = store.manifest_refs()
    if not refs:
        return []
    records = (probed or inventory(checkpoints, validate_targets=False, probe=True))["checkpoints"]
    live = {item["ref"] for item in records if item["state"] != "PRUNED"}
    return sorted(refs - live)


def drop_orphan_manifests(checkpoints, store, probed=None):
    suspects = _orphan_suspects(checkpoints, store, probed)
    if not suspects:
        return 0
    with _transaction(checkpoints.root):
        orphans = []
        for ref in sorted(set(suspects) & store.manifest_refs()):
            session_id, identity = ref.split("/")
            owner = checkpoints._session_store(session_id)
            try:
                record = owner.load(identity)
            except FileNotFoundError:
                record = None
            except (OSError, ValueError, TypeError, RecursionError):
                continue
            if record is not None and record["state"] != "PRUNED":
                continue
            try:
                # A missing record or tombstone is not proof that its exact
                # capture owner is idle. Probe independently before dropping.
                require_capture_idle(owner, identity)
            except (OSError, ValueError, TypeError, RecursionError):
                continue
            orphans.append(ref)
        if orphans:
            with store.writer(timeout=STORE_LOCK_WAIT_UNDER_TRANSACTION) as writer:
                writer.drop_manifests(orphans)
    return len(orphans)


def compact(checkpoints, store, max_packs=COMPACT_PACKS_PER_RUN, cancelled=None):
    reclaimed = 0
    for _ in range(max_packs):
        if cancelled and cancelled():
            break
        with _transaction(checkpoints.root):
            with store.writer(timeout=STORE_LOCK_WAIT_UNDER_TRANSACTION) as writer:
                candidates = writer.compaction_candidates()
                if not candidates:
                    break
                reclaimed += writer.compact_pack(candidates[0])
    return reclaimed


def clean_store(root, *, cancelled=None, probed=None):
    """Run all three steps; returns counts. Skips quietly if the store is busy.

    ``probed``: this run's lock-free retention probe inventory, for the orphan probe (``_orphan_suspects``).
    """
    store = SnapshotStore(root)
    result = {"orphans": 0, "chunks_removed": 0, "bytes_released": 0, "packs_removed": 0, "compacted_bytes": 0}
    if not store.exists():
        return result
    checkpoints = profile_checkpoints(root)
    try:
        result["orphans"] = drop_orphan_manifests(checkpoints, store, probed)
        if garbage_possible(store):
            with store.writer() as writer:
                result.update(writer.collect_garbage())
        result["compacted_bytes"] = compact(checkpoints, store, cancelled=cancelled)
    except StoreBusy:
        result["busy"] = True
    return result
