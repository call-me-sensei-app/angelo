# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Serial local execution costs, not a provider or MCP speed comparison.

Run the Angelo binary --background --factory-startup --python-exit-code 1
--python this_file after other test/build processes have stopped. The default
fixture contains 100 objects, one 128-by-128 grid and 99 shared eight-vertex
meshes. Three scale cohorts follow it, because costs that grow with the scene
stay hidden at 100 objects: 2,000 objects sharing one node material (every
tenth with a Bevel modifier), 20 dense grids of about one million vertices, and
2,000 objects spread through a 511-collection tree with some double links.
One warmup and five measurements cover fresh readbacks, private staging I/O,
local expected checks, direct commit, and (default fixture only) complete
request snapshot storage. No model, network, UI timer wait, GPU preview, or
Undo serialization is timed.

Each operation reports wall time, process CPU time and retired instructions
(``benchmark_fixtures.measure``). ``results.json`` carries a ``ratchet``
section of median instruction counts; check it with ``perf_ratchet.py``.
Staged programs split into polls by a wall-clock budget, so their counts move
with machine load; the ratchet instead uses ``*_work_clock`` repeats of those
programs, whose polls are split by retired instructions (macOS only).
"""

import hashlib
import itertools
import json
import os
from pathlib import Path
import sys
import uuid

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-agent-execution-benchmark"))
from angelo_agent import native_programs, scene_queries  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor, scene_signature  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402
from angelo_agent.schema_compaction import compact_schema  # noqa: E402
from angelo_agent.tool_specs import TOOLS, dynamic_tools, native_plan_schema  # noqa: E402


def json_bytes(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())


window = bpy.context.window
target = fixtures.default_fixture(window)
executor = BlenderExecutor(window)
handle = scene_queries.object_handle(target)
ratchet = fixtures.Ratchet()
report = dict(fixtures.environment(),
              method="serial; 1 warmup + 5 samples; wall, process CPU and retired instructions; "
                     "no UI waits, GPU, Undo or provider latency",
              fixture={"objects": len(window.scene.objects), "target_vertices": len(target.data.vertices),
                       "target_faces": len(target.data.polygons), "neighbor_vertices_each": 8},
              timings={}, payload_bytes={})
report["addon_source_sha256"] = hashlib.sha256(b"".join(
    path.name.encode() + b"\0" + path.read_bytes()
    for path in sorted((fixtures.ROOT / "scripts/addons_core/angelo_agent").glob("*.py")))).hexdigest()


WORK_CLOCK = fixtures.instruction_work_clock if fixtures.perf_counters.read_rusage() else None


def record(timings, prefix, label, function, ratchet_op=True):
    stats, result = fixtures.measure(function)
    timings[label] = stats
    if label.startswith("staged_"):
        timings[label]["last_job"] = result
    if ratchet_op:
        ratchet.add(prefix + label, stats)
    print("BENCHMARK", prefix + label, json.dumps({key: stats[key] for key in ("median_ms", "p95_ms")}),
          "instructions", (stats["instructions"] or {}).get("median"), flush=True)
    return result


def staged(steps, offset=0, work_clock=None):
    return fixtures.staged_program(window, target, handle, steps, offset, work_clock=work_clock)


def staged_operations(prefix, timings, cases):
    """Wall-clock programs for reporting, then work-clock repeats for the ratchet."""
    for label, function in cases:
        record(timings, prefix, label, lambda: function(None), ratchet_op=False)
    if WORK_CLOCK is not None:
        for label, function in cases:
            record(timings, prefix, label + "_work_clock", lambda: function(WORK_CLOCK))


for label, function in (
    ("target_only_signature", lambda: scene_signature(window.scene, [target])),
    ("whole_scene_signature", lambda: scene_signature(window.scene)),
    ("rich_object_query_warm_exact_revalidation", lambda: scene_queries.execute(window, "angelo_scene_object", {"object": handle})),
    ("transform_object_query", lambda: scene_queries.execute(window, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})),
    ("search_100_objects", lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 100})),
    ("search_page_coverage_100", lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 100, "coverage": "PAGE"})),
    ("legacy_typed_location", lambda: executor.execute("angelo_set_transform", {"object": target.name, "location": [0, 0, 0]})),
):
    record(report["timings"], "default/", label, function)
staged_operations("default/", report["timings"], (
    ("staged_1_operation_1_check", lambda clock: staged(1, work_clock=clock)),
    ("staged_8_operations_1_check", lambda clock: staged(8, work_clock=clock)),
    ("eight_separate_staged_programs", lambda clock: [staged(1, offset=index, work_clock=clock) for index in range(8)]),
))

# Compare wire payloads for the same scene, not delta latency with no checks.
# A SCENE baseline compares every object; an OBJECTS baseline only the target.
initial = scene_queries.execute(window, "angelo_scene_search", {"limit": 100})
covered = scene_queries.execute(window, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})
target.location.x += .1
window.view_layer.update()
delta = record(report["timings"], "default/", "one_object_delta", lambda: scene_queries.execute(
    window, "angelo_scene_delta", {"observation_token": initial["observation_token"], "limit": 100}))
assert len(delta["changes"]) == 1, delta
scoped = record(report["timings"], "default/", "object_scoped_delta", lambda: scene_queries.execute(
    window, "angelo_scene_delta", {"observation_token": covered["observation_token"], "limit": 100}))
assert [c["change"] for c in scoped["changes"]] == ["MODIFIED"] and scoped["observation_coverage"] == "OBJECTS", scoped
report["payload_bytes"]["full_search"] = json_bytes(initial)
report["payload_bytes"]["one_object_delta"] = json_bytes(delta)
report["payload_bytes"]["tool_schemas_original"] = sum(json_bytes(tool["inputSchema"]) for tool in TOOLS)
report["payload_bytes"]["tool_schemas_compact"] = sum(json_bytes(compact_schema(tool["inputSchema"])) for tool in TOOLS)
report["payload_bytes"]["native_plan_compact"] = json_bytes(native_plan_schema())
report["payload_bytes"]["dynamic_tools_compact"] = json_bytes(dynamic_tools())

# Complete uncompressed request snapshot: reserve + native copy-save + durable
# metadata. No hand-waved estimate; do not compare with a deferred-mode zero.
# benchmark_blender_snapshot_cost.py measures the real capture path at scale.
checkpoints = CheckpointStore(OUT / "snapshot-profile", str(uuid.uuid4()))


def snapshot():
    record_, path = checkpoints.reserve(bpy.data.filepath)
    outcome = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, check_existing=False,
                                       relative_remap=True, compress=False)
    assert "FINISHED" in outcome and path.is_file()
    os.chmod(path, 0o600)
    record_["scene_bytes"] = path.stat().st_size
    checkpoints.save(record_)
    return {"scene_bytes": path.stat().st_size, "metadata_capture_ms": record_.get("capture_ms")}


report["snapshot"] = record(report["timings"], "default/", "whole_request_snapshot", snapshot)

# Scale cohorts run after the default fixture so its workload and keys stay comparable.
report["cohorts"] = {}
for cohort in fixtures.COHORTS:
    # staged() reads the module-level target and handle, so rebind them per cohort.
    target = fixtures.cohort_fixture(window, cohort)
    handle = scene_queries.object_handle(target)
    entry = {"fixture": fixtures.fixture_counts(window), "timings": {}}
    offsets = itertools.count()  # Every sample moves the target somewhere new: a real edit, not a no-op.
    covered = scene_queries.execute(window, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})
    for label, function in (
        ("target_revision", lambda: scene_queries.object_revision(window, target)),
        ("search_100_objects", lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 100})),
        ("search_page_coverage_100", lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 100, "coverage": "PAGE"})),
        ("search_page_coverage_10", lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 10, "coverage": "PAGE"})),
        ("rich_object_query", lambda: scene_queries.execute(window, "angelo_scene_object", {"object": handle})),
        ("transform_object_query", lambda: scene_queries.execute(window, "angelo_scene_object", {"object": handle, "sections": ["TRANSFORM"]})),
    ):
        record(entry["timings"], cohort + "/", label, function)
    staged_operations(cohort + "/", entry["timings"], (
        ("staged_1_operation_1_check", lambda clock: staged(1, offset=next(offsets), work_clock=clock)),))
    # After the staged edits, as before: the covered target has really changed.
    record(entry["timings"], cohort + "/", "object_scoped_delta", lambda: scene_queries.execute(
        window, "angelo_scene_delta", {"observation_token": covered["observation_token"]}))
    report["cohorts"][cohort] = entry

report["ratchet"] = ratchet.section()
report["success"] = True
native_programs.cleanup_all()
(OUT / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False))
print("EXECUTION_BENCHMARK_DONE", json.dumps({"success": True, "results": str(OUT / "results.json"),
                                             "operations": len(report["ratchet"]["operations"])}), flush=True)
