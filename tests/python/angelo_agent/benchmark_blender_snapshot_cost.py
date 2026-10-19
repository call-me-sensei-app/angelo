# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-snapshot cost on large synthetic documents, per checkpoint policy.

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_snapshot_cost.py \\
        -- [--sizes-mb 50,200,500] [--many-ids 10000] [--samples 7] [--history 10,1000]

Set ``ANGELO_QA_DIR`` to a new temporary directory; snapshots, state and
accounts stay inside it, and each measured snapshot is deleted after it is
measured, so disk use peaks at about one snapshot. Two compositions bound the
cost per byte:

* ``dense``: copies of one 251,001-vertex grid mesh (about 16 MB each when
  saved; no UVs, materials or images) are added in place until the saved size
  reaches each target. Few, large arrays: the cheapest bytes to write.
* ``many_ids``: a separate document of ``--many-ids`` one-cube objects with
  their own meshes, one shared node material and a Bevel modifier on every
  tenth (about 50 MB at 10,000). Per-ID overhead dominates.

The first capture after the document grows is reported separately from the
repeat captures, because it measured several times slower (Blender-side work
on newly created data); real requests capture after edits, so both matter.

Every sample calls the product's own ``ui.capture_checkpoint`` (reserve,
preview-free ``save_as_mainfile(copy=True, compress=False)`` with a window
override, permissions and metadata) on Blender's main thread, exactly as a
request does. Wall time there is the time the UI is blocked. The policy table
converts the measured capture into per-message costs: Every Request captures
before every message, read-only included; Before First Approved Edit captures
only before a request's first approved edit, so a read-only message pays
nothing and an editing message pays the same capture, later in the request.

Real documents differ: images, packed files, many small IDs and linked
libraries all change bytes per second. Page-cache state and disk contention
also move wall time, so treat wall/CPU here as measured-under-load figures and
compare instruction counts across code changes.

``--history`` (default ``10,1000``; empty skips) captures a one-cube document
into profiles already holding that many stored snapshots (written by
``snapshot_fixtures.build_history`` as a long session leaves them). Its repeat
captures must cost the same at every history size; the ratchet keeps both
``snapshot/history_<n>_repeat_capture_net`` operations. The first capture of
each profile validates the whole history once per process and is reported only.

``--dedup-mb`` (default 500; 0 skips) adds the deduplicated-storage phase: a
document of distinct dense meshes (each its own resolution, with per-copy vertex
noise so no two arrays match) plus ``--dedup-objects`` small objects is captured
after a sequence of typical edits. Each capture is ingested on a separate
thread with the product's ``ingest_snapshot``, exactly as the snapshot worker
does, and the phase reports main-thread capture time, off-thread ingestion
time, new store bytes per edit, and restore reassembly time from the store
against the previous full-copy restore copy.
"""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402
import snapshot_fixtures  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-snapshot-cost-benchmark"))
from angelo_agent import ui  # noqa: E402  (after isolate: state/accounts env)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.snapshot_ingest import ingest_snapshot  # noqa: E402
from angelo_agent.snapshot_store import SnapshotStore  # noqa: E402

MB = 1000 * 1000
BASE_SEGMENTS = 500  # 501 x 501 = 251,001 vertices, 250,000 quads.


def parse(argv):
    parser = argparse.ArgumentParser(description="Request snapshot cost on large synthetic documents")
    parser.add_argument("--sizes-mb", default="50,200,500", help="dense document sizes; empty to skip")
    parser.add_argument("--many-ids", type=int, default=10000, help="objects in the many_ids document; 0 to skip")
    parser.add_argument("--samples", type=int, default=7, help="repeat captures after the first one")
    parser.add_argument("--dedup-mb", type=int, default=500, help="deduplicated-storage document size; 0 to skip")
    parser.add_argument("--dedup-objects", type=int, default=200, help="small objects in the dedup document")
    parser.add_argument("--history", default="10,1000", help="stored snapshots before the history captures; empty to skip")
    args = parser.parse_args(argv)
    args.histories = sorted(int(value) for value in args.history.split(",") if value.strip())
    if args.histories and not (min(args.histories) >= 1 and max(args.histories) <= 20000):
        parser.error("Use 1-20,000 stored snapshots for --history")
    if not 0 <= args.dedup_mb <= 4000 or not 60 <= args.dedup_objects <= 5000:
        parser.error("Use a 0-4000 MB dedup document with 60-5,000 small objects")
    args.sizes = sorted(int(value) for value in args.sizes_mb.split(",") if value.strip())
    if (args.sizes and (min(args.sizes) < 1 or max(args.sizes) > 4000)) or not 1 <= args.samples <= 20 \
            or not 0 <= args.many_ids <= 50000:
        parser.error("Use 1-4000 MB sizes, 0-50,000 objects and 1-20 samples")
    return args


class Document:
    """The scratch scene: many small IDs, or copies of one dense base mesh."""

    def __init__(self, window):
        self.window = window
        fixtures.clear_scene_data()
        self.base = None
        self.copies = 0

    def add_many_ids(self, count):
        material = fixtures.shared_node_material()
        for index in range(count):
            obj = bpy.data.objects.new("Snapshot prop %05d" % index, fixtures.new_mesh("Prop %05d" % index, fixtures.cube))
            obj.location = (index % 100 * 3, index // 100 * 3, 0)
            obj.data.materials.append(material)
            if index % 10 == 0:
                obj.modifiers.new("Bevel", "BEVEL")
            self.window.scene.collection.objects.link(obj)
        self.window.view_layer.update()

    def add_copies(self, count):
        if self.base is None:
            self.base = fixtures.new_mesh("Snapshot base grid", fixtures.grid(BASE_SEGMENTS))
        for _ in range(count):
            mesh = self.base.copy()  # A full data copy: saved separately, like distinct assets.
            obj = bpy.data.objects.new("Snapshot dense %03d" % self.copies, mesh)
            obj.location = (self.copies % 10 * 3, self.copies // 10 * 3, 0)
            self.window.scene.collection.objects.link(obj)
            self.copies += 1
        self.window.view_layer.update()


def saved_bytes(path):
    """Size of a plain uncompressed copy-save, for sizing only (not measured)."""
    outcome = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, check_existing=False, compress=False)
    assert "FINISHED" in outcome
    size = path.stat().st_size
    path.unlink()
    return size


def write_reference(directory, size):
    """Instructions to write ``size`` new bytes beside the snapshots: the page-cache copy every capture also pays.

    Measured right after each capture, in the same process and folder, so the
    kernel's cost per byte (which moves with the machine's free memory and
    swap) cancels out of ``net_instructions``.
    """
    block = (os.urandom(4096) * 256)  # 1 MiB, written in Blender-sized chunks.
    path = Path(directory) / ("write-reference-%s.bin" % uuid.uuid4().hex)

    def write():
        with open(path, "wb", buffering=0) as handle:
            remaining = size
            while remaining > 0:
                remaining -= handle.write(block[:min(remaining, len(block))])
    try:
        return perf_counters.measure(write)[1]
    finally:
        path.unlink(missing_ok=True)


def capture_samples(store, samples):
    """First capture after a change, then repeats; each snapshot is deleted once measured.

    Every repeat is bracketed by writes of its own byte count (``write_reference``):
    a dense capture retires 85-90% of its instructions copying bytes into the
    page cache, and that kernel cost per byte moved from 0.55 to 0.79
    instructions between runs (and between two modes within a run) without
    any code change. ``net_instructions``, the fewest instructions of any repeat
    minus the fewest of any reference write, is the work Angelo and Blender do
    themselves; it feeds the ratchet. Gross counts stay in the report.
    """
    controller = SimpleNamespace(checkpoints=store, _turn_checkpoint_policy={"capture_mode": "EVERY_REQUEST"})
    raw, sizes, references = [], [], []
    for index in range(1 + samples):
        if index:
            references.append(write_reference(store.directory, sizes[-1]))
        record, metrics = perf_counters.measure(lambda: ui.capture_checkpoint(controller))
        metrics["metadata_capture_ms"] = record.get("capture_ms")
        raw.append(metrics)
        sizes.append(record["scene_bytes"])
        shutil.rmtree(store.folder(record["id"]))
        if index:
            references.append(write_reference(store.directory, record["scene_bytes"]))
    repeats = perf_counters.summarize(raw[1:])
    repeats["write_reference"] = perf_counters.summarize(references)
    captured = [sample["instructions"] for sample in raw[1:]]
    written = [sample["instructions"] for sample in references]
    repeats["net_instructions"] = ({"median": min(captured) - min(written), "statistic": "min capture - min reference"}
                                   if None not in captured + written else None)
    capture = {"scene_bytes": sorted(sizes)[len(sizes) // 2], "first_capture": raw[0], "repeat": repeats,
               "repeat_samples": raw[1:], "write_reference_samples": references}
    capture["repeat_mb_per_s"] = round(capture["scene_bytes"] / MB / (repeats["wall_ms"]["median"] / 1000), 1)
    return capture


def policy_costs(capture):
    """Per-message capture cost and retained bytes under each checkpoint policy."""
    first, repeat = capture["first_capture"], capture["repeat"]
    wall = [round(first["wall_ms"], 3), repeat["wall_ms"]["median"]]
    cpu = [round(first["cpu_ms"], 3), repeat["cpu_ms"]["median"]]
    size = capture["scene_bytes"]
    # [first capture after a change, repeat capture]: the realistic per-message cost lies between.
    every = {"capture_ms_first_repeat": wall, "cpu_ms_first_repeat": cpu, "retained_bytes": size}
    none = {"capture_ms_first_repeat": [0, 0], "cpu_ms_first_repeat": [0, 0], "retained_bytes": 0}
    return {"EVERY_REQUEST": {"read_only_message": every, "editing_message": every,
                              "when": "before the request is submitted"},
            "BEFORE_FIRST_EDIT": {"read_only_message": none, "editing_message": every,
                                  "when": "after approval, before the first approved edit"},
            "notes": "retained_bytes is the full capture; the snapshot worker then deduplicates it (see the "
                     "deduplicated_storage phase) and the 2 GiB default budget is enforced automatically. "
                     "Messages that never edit pay nothing under BEFORE_FIRST_EDIT."}


def noisy_dense_mesh(index):
    """A distinct dense mesh: its own resolution plus deterministic vertex noise."""
    import numpy
    mesh = fixtures.new_mesh("Dedup dense %03d" % index, fixtures.grid(BASE_SEGMENTS - index))
    coords = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)
    mesh.vertices.foreach_get("co", coords)
    coords += numpy.random.default_rng(index).normal(0, 0.002, coords.size).astype(numpy.float32)
    mesh.vertices.foreach_set("co", coords)
    mesh.update()
    return mesh


def build_dedup_document(window, target_mb, small_objects, sizing):
    fixtures.clear_scene_data()
    material = fixtures.shared_node_material()
    for index in range(small_objects):
        obj = bpy.data.objects.new("Dedup prop %04d" % index, fixtures.new_mesh("Dedup prop %04d" % index, fixtures.cube))
        obj.location = (index % 20 * 2, -10 - index // 20 * 2, 0)
        obj.data.materials.append(material)
        window.scene.collection.objects.link(obj)
    dense = 0
    while saved_bytes(sizing) < target_mb * MB:
        remaining = target_mb * MB - saved_bytes(sizing)
        for _ in range(max(1, math.ceil(remaining / (16 * MB)))):
            obj = bpy.data.objects.new("Dedup dense %03d" % dense, noisy_dense_mesh(dense))
            obj.location = (dense % 10 * 3, dense // 10 * 3, 0)
            window.scene.collection.objects.link(obj)
            dense += 1
        window.view_layer.update()
    return dense


def rotate_props(count):
    for index in range(10, 10 + count):
        bpy.data.objects["Dedup prop %04d" % index].rotation_euler.z += 0.3


def edit_dense_vertices(name):
    import numpy
    mesh = bpy.data.objects[name].data
    coords = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)
    mesh.vertices.foreach_get("co", coords)
    coords[2::3] += 0.01
    mesh.vertices.foreach_set("co", coords)
    mesh.update()


DEDUP_EDITS = (
    ("base", lambda: None),
    ("no_change", lambda: None),
    ("move_one_object", lambda: setattr(bpy.data.objects["Dedup prop 0000"].location, "z", 1.5)),
    ("add_material", lambda: bpy.data.objects["Dedup dense 000"].data.materials.append(bpy.data.materials.new("Dedup red"))),
    ("add_modifier", lambda: bpy.data.objects["Dedup prop 0001"].modifiers.new("Bevel", "BEVEL")),
    ("rotate_50_objects", lambda: rotate_props(50)),
    ("add_object", lambda: bpy.context.scene.collection.objects.link(bpy.data.objects.new(
        "Dedup added", fixtures.new_mesh("Dedup added", fixtures.grid(64))))),
    ("edit_dense_mesh", lambda: edit_dense_vertices("Dedup dense 001")),
)


def main_thread_probe(running, interval=0.005):
    """Latency of a ~20 us Python task every 5 ms while ``running()``.

    The worker's own Python (block parsing) shares the GIL with Blender's
    main-thread Python (UI timers, draw callbacks), so this is the delay the
    main thread can see while ingestion runs.
    """
    delays = []
    while running():
        started = time.perf_counter()
        sum(range(2000))
        delays.append((time.perf_counter() - started) * 1000)
        time.sleep(interval)
    delays.sort()
    if not delays:
        return {"samples": 0}
    return {"samples": len(delays), "median_ms": round(delays[len(delays) // 2], 3),
            "p95_ms": round(delays[int(len(delays) * 0.95)], 3), "max_ms": round(delays[-1], 3)}


def ingest_off_thread(store, identity):
    """Run the product ingestion on another thread while probing the main thread."""
    result = {}
    worker = threading.Thread(target=lambda: result.update(stats=ingest_snapshot(store, identity)))
    started = time.perf_counter()
    worker.start()
    result["main_thread_probe"] = main_thread_probe(worker.is_alive)
    worker.join()
    result["wall_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def measure_dedup(report, args, window):
    """Capture after typical edits, ingest off-thread, then time reassembly."""
    sizing = OUT / "dedup-sizing.blend"
    dense = build_dedup_document(window, args.dedup_mb, args.dedup_objects, sizing)
    store = CheckpointStore(OUT / "dedup-profile", str(uuid.uuid4()))
    chunks = SnapshotStore(store.root)
    controller = SimpleNamespace(checkpoints=store, _turn_checkpoint_policy={"capture_mode": "EVERY_REQUEST"})
    edits, previous_live = [], 0
    idle_until = time.perf_counter() + 1.0
    phase = {"dense_meshes": dense, "small_objects": args.dedup_objects, "edits": edits,
             "main_thread_probe_idle": main_thread_probe(lambda: time.perf_counter() < idle_until)}
    for name, edit in DEDUP_EDITS:
        edit()
        window.view_layer.update()
        record, capture = perf_counters.measure(lambda: ui.capture_checkpoint(controller))
        if name == "base":  # The pre-deduplication restore path copied the full file.
            copied = perf_counters.measure(lambda: store.recovery_copy(record["id"]))
            copied[0][1].unlink()
            phase["full_copy_restore_copy"] = {"wall_ms": round(copied[1]["wall_ms"], 1),
                                               "cpu_ms": round(copied[1]["cpu_ms"], 1)}
        ingested = ingest_off_thread(store, record["id"])
        stats = ingested["stats"]
        live = chunks.usage()["live_bytes"]
        edits.append({"edit": name, "id": record["id"], "scene_bytes": record["scene_bytes"],
                      "capture_wall_ms": round(capture["wall_ms"], 1), "capture_cpu_ms": round(capture["cpu_ms"], 1),
                      "ingest_off_thread_ms": ingested["wall_ms"], "ingest_store_ms": stats["store_ms"],
                      "ingest_verify_ms": stats["verify_ms"], "segments": stats["segments"],
                      "new_raw_bytes": stats["new_bytes"], "new_stored_bytes": stats["stored_bytes"],
                      "store_growth_bytes": live - previous_live, "adopted": stats["adopted"],
                      "main_thread_probe_during_ingest": ingested["main_thread_probe"]})
        previous_live = live
        print("DEDUP", json.dumps(edits[-1]), flush=True)
    for entry in edits:
        restored, timing = perf_counters.measure(lambda: store.recovery_copy(entry["id"]))
        restored[1].unlink()
        entry["store_restore_copy_wall_ms"] = round(timing["wall_ms"], 1)
        entry["store_restore_copy_cpu_ms"] = round(timing["cpu_ms"], 1)
    usage = chunks.usage()
    phase.update(full_copy_bytes=sum(entry["scene_bytes"] for entry in edits), store=usage)
    report["deduplicated_storage"] = phase
    print("DEDUP_TOTAL", json.dumps({"full_copy_bytes": phase["full_copy_bytes"], "store": usage}), flush=True)


def measure_history(report, ratchet, window, histories, samples):
    """Request capture of one small document with many stored snapshots already in the profile."""
    fixtures.clear_scene_data()
    window.scene.collection.objects.link(bpy.data.objects.new("History cube", fixtures.new_mesh("History cube", fixtures.cube)))
    window.view_layer.update()
    phase = report["history"] = {}
    for count in histories:
        folder = OUT / ("history-%d" % count)
        folder.mkdir()
        store = CheckpointStore(snapshot_fixtures.build_history(folder / "state", count), str(uuid.uuid4()))
        capture = capture_samples(store, samples)
        phase["stored_%d" % count] = {"first_capture_validates_history": capture["first_capture"],
                                     "repeat": capture["repeat"], "scene_bytes": capture["scene_bytes"]}
        ratchet.add("snapshot/history_%d_repeat_capture_net" % count, {"instructions": capture["repeat"]["net_instructions"]})
        print("SNAPSHOT_HISTORY", count, json.dumps({"first_wall_ms": round(capture["first_capture"]["wall_ms"], 3),
              "repeat_wall_ms": capture["repeat"]["wall_ms"]["median"],
              "repeat_instructions": (capture["repeat"]["instructions"] or {}).get("median"),
              "repeat_net_instructions": (capture["repeat"]["net_instructions"] or {}).get("median")}), flush=True)


def measure_document(report, ratchet, store, key, document, samples):
    capture = capture_samples(store, samples)
    entry = {"objects": len(document.window.scene.objects), "capture": capture, "policies": policy_costs(capture)}
    report["documents"][key] = entry
    ratchet.add("snapshot/%s_repeat_capture_net" % key, {"instructions": capture["repeat"]["net_instructions"]})
    print("SNAPSHOT", key, json.dumps({"bytes": capture["scene_bytes"],
          "first_wall_ms": round(capture["first_capture"]["wall_ms"], 3),
          "repeat_wall_ms": capture["repeat"]["wall_ms"]["median"],
          "repeat_cpu_ms": capture["repeat"]["cpu_ms"]["median"],
          "repeat_instructions": (capture["repeat"]["instructions"] or {}).get("median"),
          "repeat_net_instructions": (capture["repeat"]["net_instructions"] or {}).get("median")}), flush=True)
    return entry


def main():
    args = parse(fixtures.script_arguments())
    window = bpy.context.window
    report = dict(fixtures.environment(),
                  method="product ui.capture_checkpoint on the main thread; per document the first capture after "
                         "it changed, then %d repeat captures; dense documents grown in place with copies of one "
                         "%d-vertex grid mesh" % (args.samples, (BASE_SEGMENTS + 1) ** 2),
                  documents={}, success=False)
    ratchet = fixtures.Ratchet()
    store = CheckpointStore(OUT / "snapshot-profile", str(uuid.uuid4()))
    sizing = OUT / "sizing.blend"
    if args.many_ids:
        document = Document(window)
        document.add_many_ids(args.many_ids)
        measure_document(report, ratchet, store, "many_ids_%d" % args.many_ids, document, args.samples)
    if args.sizes:
        document = Document(window)
        empty = saved_bytes(sizing)
        document.add_copies(1)
        per_copy = max(1, saved_bytes(sizing) - empty)
        report["sizing"] = {"empty_document_bytes": empty, "bytes_per_mesh_copy": per_copy,
                            "vertices_per_copy": len(document.base.vertices)}
        for target_mb in args.sizes:
            wanted = max(1, round((target_mb * MB - empty) / per_copy))
            document.add_copies(max(0, wanted - document.copies))
            entry = measure_document(report, ratchet, store, "dense_%dmb" % target_mb, document, args.samples)
            entry.update(target_mb=target_mb, mesh_copies=document.copies,
                         vertices=document.copies * len(document.base.vertices))
    if args.histories:
        measure_history(report, ratchet, window, args.histories, args.samples)
    if args.dedup_mb:
        measure_dedup(report, args, window)
    report["ratchet"] = ratchet.section()
    report["success"] = True
    (OUT / "snapshot-cost.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("SNAPSHOT_BENCHMARK_DONE", json.dumps({"success": True, "results": str(OUT / "snapshot-cost.json")}), flush=True)


main()
