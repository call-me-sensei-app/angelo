# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual measurement: what the worker's pauses cost the store lock's holders.

    python pause_cost.py <angelo checkout> [manifests]

``background_pauses`` sleeps PAUSE_SECONDS (0.2 ms) after every 0.5 ms of work, also while ``collect_garbage``
holds the store's writer lock, which other app instances on the profile wait for (30 s for ingestion, 0.5 s
under their profile transaction). Measured on an M-series Mac: sleep(0.0002) took 0.31 ms
median (p99 0.43-0.68 ms); collection over 400 manifests held the lock 1.2-1.3 ms plain, 1.9-4.1 ms
with pauses (x1.6 to x3.2 across runs).
"""

import statistics
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

checkout = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 400
sys.path.insert(0, checkout + "/scripts/addons_core")
sys.path.insert(0, checkout + "/tests/python/angelo_agent")
from snapshot_fixtures import build_history  # noqa: E402
from angelo_agent import background_pauses  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402

samples = []
for _ in range(2000):
    start = time.perf_counter()
    time.sleep(background_pauses.PAUSE_SECONDS)
    samples.append(time.perf_counter() - start)
samples.sort()
print("sleep(%.4f) took: median %.3f ms, p90 %.3f ms, p99 %.3f ms" % (
    background_pauses.PAUSE_SECONDS, 1e3 * statistics.median(samples), 1e3 * samples[1800], 1e3 * samples[1980]))
root = build_history(Path(tempfile.mkdtemp()) / "state", count)
store = CheckpointStore(root, str(uuid.uuid4()))
for item in store.inspect()["checkpoints"]:
    ingest_snapshot(CheckpointStore(root, item["session_id"]), item["id"])


def held(enabled):
    times = []

    def run():
        if enabled:
            background_pauses.enable()
        with SnapshotStore(root).writer() as writer:
            start = time.perf_counter()
            writer.collect_garbage()
            times.append(time.perf_counter() - start)
    for _ in range(15):
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
    return statistics.median(times)


plain, paused = held(False), held(True)
print("collect_garbage over %d manifests holds the store lock %.2f ms plain, %.2f ms with pauses (x%.2f)"
      % (count, 1e3 * plain, 1e3 * paused, paused / plain))
