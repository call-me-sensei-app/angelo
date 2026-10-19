# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Byte accounting for deduplicated snapshots in retention planning.

Chunks are shared, so a snapshot has no fixed size of its own. The planner asks
"how many store bytes does removing these snapshots, in this order, release?".
A chunk is released only when the last manifest that references it is removed,
so evicting an old snapshot whose blocks later snapshots still use frees only
the blocks it alone held. Reads only; the counts are rebuilt from manifests,
never trusted from the cached reference column.

Store bytes are the stored (possibly compressed) bytes of chunks referenced by
all unpruned snapshots plus the index files, including unavailable captures.
Eviction pressure uses a separate set of scene-usable references; releasing
their last reference may reduce pressure without freeing the actual chunk.
Manifests whose snapshot was already removed or whose record no longer exists
are treated as released, because the
worker's garbage collection deletes them; this keeps planning from removing
more history than the budget requires. Pack space freed by deleted chunks is
reclaimed by compaction and is not counted as live.
"""

import zlib
from collections import Counter

from .background_pauses import pause
from .checkpoint_policy import scene_usable
from .snapshot_store import SnapshotStore, decode_ids


class StoreAccounting:
    def __init__(self, manifests, sizes, overhead_bytes, *, pressure_refs=None):
        self._manifests = manifests
        self._sizes = sizes
        self._pressure_refs = set(manifests) if pressure_refs is None else set(pressure_refs) & manifests.keys()
        # Counted per manifest in C (Counter.update), pausing between manifests: a whole-history pass on the
        # snapshot worker that shares the GIL with Blender's main thread (background_pauses).
        self._counts, self._pressure_counts = Counter(), Counter()
        for ref, ids in manifests.items():
            pause()
            unique = set(ids)
            self._counts.update(unique)
            if ref in self._pressure_refs:
                self._pressure_counts.update(unique)
        self.live_bytes = sum(sizes.get(chunk_id, 0) for chunk_id in self._counts)
        self.overhead_bytes = overhead_bytes
        self.total_bytes = self.live_bytes + overhead_bytes
        self.pressure_bytes = sum(sizes.get(chunk_id, 0) for chunk_id in self._pressure_counts) + overhead_bytes

    @classmethod
    def load(cls, store, live_refs, *, pressure_refs=None):
        """Account unpruned refs separately from usable refs; None without a store."""
        with store.reader() as connection:
            if connection is None:
                return None
            manifests = {}
            for ref, blob in connection.execute("SELECT ref, chunk_ids FROM manifests"):
                pause()
                if ref in live_refs:
                    manifests[ref] = decode_ids(blob)
            sizes = dict(connection.execute("SELECT id, stored FROM chunks"))
        return cls(manifests, sizes, store.usage()["index_bytes"], pressure_refs=pressure_refs)

    def simulation(self):
        return _Simulation(self)

    def pressure_simulation(self):
        return _Simulation(self, pressure=True)


class _Simulation:
    """One ordered what-if removal sequence; independent of other simulations."""

    def __init__(self, accounting, *, pressure=False):
        self.accounting = accounting
        self.counts = accounting._pressure_counts if pressure else accounting._counts
        self.refs = accounting._pressure_refs if pressure else accounting._manifests.keys()
        self.removed = {}
        self.released = set()

    def release(self, ref):
        """Store bytes released by removing ``ref`` after earlier releases."""
        ids = self.accounting._manifests.get(ref)
        if ids is None or ref not in self.refs or ref in self.released:
            return 0
        self.released.add(ref)
        freed = 0
        for chunk_id in set(ids):
            removed = self.removed.get(chunk_id, 0) + 1
            self.removed[chunk_id] = removed
            if removed == self.counts[chunk_id]:
                freed += self.accounting._sizes.get(chunk_id, 0)
        return freed


class StoreBound:
    """Every stored chunk counted as live and as eviction pressure: an upper bound of ``StoreAccounting``.

    Two aggregate queries instead of reading every manifest. A plan made with it removes nothing that
    an exact plan would keep, and when it finds nothing to remove and the budget met, neither would the
    exact one: only the byte limit depends on store bytes, and these bytes are never fewer.
    """

    def __init__(self, total_bytes):
        self.total_bytes = self.pressure_bytes = total_bytes

    @classmethod
    def load(cls, store):
        """None without a store."""
        with store.reader() as connection:
            if connection is None:
                return None
            stored = connection.execute("SELECT COALESCE(SUM(stored), 0) FROM chunks").fetchone()[0]
        return cls(stored + store.usage()["index_bytes"])

    def simulation(self):
        return _Unreleased()

    def pressure_simulation(self):
        return _Unreleased()


class _Unreleased:
    def release(self, ref):
        return 0  # A bound never claims that a removal frees store bytes.


def store_bound(checkpoints):
    """``StoreBound`` of the profile's store, or None when there is none or it cannot be read."""
    try:
        return StoreBound.load(SnapshotStore(checkpoints.root))
    except (OSError, ValueError):
        return None


def store_accounting(checkpoints, inventory):
    """Account every unpruned manifest, with separate scene eviction pressure.

    A store that exists but cannot be read is reported as an inventory issue,
    which blocks removal plans instead of guessing sizes.
    """
    live = {item["ref"] for item in inventory["checkpoints"] if item["state"] != "PRUNED"}
    pressure = {item["ref"] for item in inventory["checkpoints"] if scene_usable(item)}
    try:
        return StoreAccounting.load(SnapshotStore(checkpoints.root), live, pressure_refs=pressure)
    except (OSError, ValueError, zlib.error) as error:  # zlib.error: a damaged manifest's chunk list.
        inventory["issues"].append({"error": "Snapshot store accounting is unavailable: " + str(error)})
        return None
