# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What checkpoint content binding costs where it applies (``checkpoint_policy.rst``, "Content binding").

Run in an isolated process, after other benchmarks and builds have stopped, once per build of a frozen pair (the
build before content binding and the one with it; the same script, the same arguments)::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_content_binding.py \\
        -- [--modes sculpt_pair,safety,hash] [--pair-mb 20,200] [--safety-mb 200] [--samples 7]

``ANGELO_QA_DIR`` names a new temporary directory; each measured snapshot is deleted once measured.

* ``sculpt_pair``: copies of the snapshot benchmark's 251,001-vertex grid until the document's ordinary save
  reaches each ``--pair-mb`` size, plus one Multires sphere (two levels) in Sculpt mode, so every capture is a
  version 4 pair; each sample is the product's ``ui.capture_checkpoint`` (capture, publish with its hash and bind,
  POST, finish) after a first, unmeasured capture.
* ``safety``: the same dense document without the Sculpt object, captured as a Restore's safety capture
  (``purpose="safety"``) from Object mode: before content binding identity-sealed with no read back, with it one
  BLAKE2b read in ``publish`` and the native hasher fed from it.
* ``hash``: BLAKE2b-256 (``hashlib``, what ``publish`` computes) against the native XXH3-128 hasher
  (``capture_hasher(...).update``, the code path ``publish`` feeds) over the same 8 MiB chunks of one
  ``--hash-mb`` buffer. Needs a build with content binding; skipped (and said so) on one without.

Each mode reports per-sample wall, CPU and (macOS) retired instructions with median, p75, min, max and spread. The
ordinary request capture is unchanged by design and is measured by ``benchmark_blender_snapshot_cost.py`` (its
ratchet operations); nothing here is a ratchet or a threshold.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace
import uuid

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-content-binding-benchmark"))
from angelo_agent import ui  # noqa: E402  (after isolate: state/accounts env)
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402

MB, MIB = 1000 * 1000, 1024 * 1024
BASE_SEGMENTS = 500   # The snapshot benchmark's grid: 251,001 vertices.


def parse(argv):
    parser = argparse.ArgumentParser(description="Checkpoint content binding cost")
    parser.add_argument("--modes", default="sculpt_pair,safety,hash")
    parser.add_argument("--pair-mb", default="20,200", help="sculpt_pair document sizes")
    parser.add_argument("--safety-mb", default="200", help="safety document sizes")
    parser.add_argument("--hash-mb", type=int, default=256, help="buffer hashed by the hash microbenchmark")
    parser.add_argument("--samples", type=int, default=7)
    args = parser.parse_args(argv)
    args.modes = [mode for mode in args.modes.split(",") if mode]
    sizes = lambda text: sorted(int(value) for value in text.split(",") if value.strip())  # noqa: E731
    args.pair_sizes, args.safety_sizes = sizes(args.pair_mb), sizes(args.safety_mb)
    if set(args.modes) - {"sculpt_pair", "safety", "hash"} or not 1 <= args.samples <= 50 \
            or not 8 <= args.hash_mb <= 4096:
        parser.error("Use modes sculpt_pair, safety and hash, 1-50 samples and an 8-4096 MB hash buffer")
    return args


def saved_bytes(path):
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, check_existing=False,
                                                     compress=False)
    size = path.stat().st_size
    path.unlink()
    return size


def dense_document(window, target_mb):
    """Grid copies until the plain save reaches ``target_mb`` (the snapshot benchmark's dense composition)."""
    fixtures.clear_scene_data()
    sizing = OUT / "sizing.blend"
    base = fixtures.new_mesh("Binding base grid", fixtures.grid(BASE_SEGMENTS))
    copies = 0
    while saved_bytes(sizing) < target_mb * MB:
        obj = bpy.data.objects.new("Binding dense %03d" % copies, base.copy())
        obj.location = (copies % 10 * 3, copies // 10 * 3, 0)
        window.scene.collection.objects.link(obj)
        copies += 1
        window.view_layer.update()
    return copies


def enter_sculpt(window):
    """One Multires sphere in Sculpt mode: every capture selects it as a Sculpt owner (a version 4 pair)."""
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, location=(0, 0, 5))
    target = bpy.context.object
    target.name = "Binding Multires"
    modifier = target.modifiers.new("Binding Multires", "MULTIRES")
    for _ in range(2):
        assert bpy.ops.object.multires_subdivide(modifier=modifier.name, mode="CATMULL_CLARK") == {"FINISHED"}
    assert bpy.ops.object.mode_set(mode="SCULPT") == {"FINISHED"}
    window.view_layer.update()
    return target


def captures(store, samples, purpose):
    """One unmeasured capture, then ``samples`` measured ones; each snapshot deleted once measured."""
    controller = SimpleNamespace(checkpoints=store, _turn_checkpoint_policy={"capture_mode": "EVERY_REQUEST"})
    raw, records = [], []
    for index in range(samples + 1):
        record, metrics = perf_counters.measure(lambda: ui.capture_checkpoint(controller, purpose=purpose))
        if index:
            raw.append(metrics)
        records.append({key: record.get(key) for key in ("version", "scene_bytes", "storage")}
                       | {"runtime_bytes": (record.get("sculpt") or {}).get("bytes"),
                          "seal": (record.get("component_seal") or "")[32:33]})
        shutil.rmtree(store.folder(record["id"]))
    return {"summary": perf_counters.summarize(raw), "samples": raw, "records": records}


def hash_microbenchmark(args, samples):
    import _bpy
    native = _bpy._angelo_checkpoint
    if not hasattr(native, "capture_hasher"):
        return {"skipped": "this build has no native content hasher (before content binding)"}
    from angelo_agent.checkpoint_capture import CaptureOwner
    fixtures.clear_scene_data()
    store = CheckpointStore(OUT / "hash-profile", str(uuid.uuid4()))
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "hash-project.blend"))
    owner = CaptureOwner.reserve(store, bpy.data.filepath, purpose="safety")
    capsule = native.capture(owner)
    chunk = os.urandom(8 * MIB)
    chunks = max(1, args.hash_mb * MIB // len(chunk))
    blake, xxh3 = [], []
    try:
        for index in range(samples + 1):
            digest = hashlib.blake2b(digest_size=32)
            started = time.perf_counter_ns()
            for _ in range(chunks):
                digest.update(chunk)
            digest.hexdigest()
            blake_ns = time.perf_counter_ns() - started
            hasher = native.capture_hasher(capsule, owner, "BASE")   # Never bound: only its update is timed.
            started = time.perf_counter_ns()
            for _ in range(chunks):
                hasher.update(chunk)
            xxh3_ns = time.perf_counter_ns() - started
            if index:
                blake.append(chunks * len(chunk) / MIB / (blake_ns / 1e9))
                xxh3.append(chunks * len(chunk) / MIB / (xxh3_ns / 1e9))
    finally:
        native.capture_abort(capsule, owner)
        native.capture_close(capsule)
        native.capture_release(capsule)
    return {"bytes": chunks * len(chunk), "chunk_bytes": len(chunk),
            "blake2b_256_mib_per_s": perf_counters.describe(blake), "xxh3_128_mib_per_s": perf_counters.describe(xxh3),
            "blake2b_samples": blake, "xxh3_samples": xxh3}


def main():
    args = parse(fixtures.script_arguments())
    window = bpy.context.window
    report = dict(fixtures.environment(), arguments=vars(args), success=False,
                  content_binding=hasattr(__import__("_bpy")._angelo_checkpoint, "capture_bind"), results={})
    for mode in args.modes:
        if mode == "hash":
            report["results"]["hash"] = hash_microbenchmark(args, args.samples)
            print("BINDING_BENCH hash", json.dumps({k: v for k, v in report["results"]["hash"].items()
                                                    if not k.endswith("_samples")}), flush=True)
            continue
        for target_mb in (args.pair_sizes if mode == "sculpt_pair" else args.safety_sizes):
            copies = dense_document(window, target_mb)
            if mode == "sculpt_pair":
                enter_sculpt(window)
            store = CheckpointStore(OUT / ("%s-%d" % (mode, target_mb)), str(uuid.uuid4()))
            result = captures(store, args.samples, "safety" if mode == "safety" else "request")
            result.update(target_mb=target_mb, mesh_copies=copies)
            key = "%s_%dmb" % (mode, target_mb)
            report["results"][key] = result
            if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            summary = result["summary"]
            print("BINDING_BENCH", key, json.dumps({
                "records": result["records"][-1], "wall_ms": summary["wall_ms"], "cpu_ms": summary["cpu_ms"],
                "instructions": summary.get("instructions")}), flush=True)
    report["success"] = True
    (OUT / "content-binding-cost.json").write_text(json.dumps(report, indent=1, default=str))
    print("BINDING_BENCH_DONE", json.dumps({"success": True, "results": str(OUT / "content-binding-cost.json")}),
          flush=True)


main()
