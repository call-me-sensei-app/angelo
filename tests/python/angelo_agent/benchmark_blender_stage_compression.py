# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stage-bundle suspend/hydrate cost with and without zstd compression.

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_stage_compression.py \\
        -- [--samples 5] [--work-clock instructions|wall]
        [--cohorts default,many_objects,dense_geometry,deep_collections,large_target]

Each yield of a staged program writes its private stage to a bundle with
``bpy.data.libraries.write(..., compress=program_storage.STAGE_BUNDLE_COMPRESSION)``
(suspend) and loads it back on the next poll or at commit (hydrate). This
benchmark runs the same one-transform, one-check program on each cohort with
the module constant set to True (the product default) and to False, in
alternating order per round so drift on a loaded machine affects both alike.
The constant is restored to True before the process exits; nothing else in
the product changes.

By default polls are split by ``benchmark_fixtures.instruction_work_clock``,
so both settings run the same number of suspend/hydrate cycles; bundle
serialization never counts against the poll budget, so the clock cannot favour
either setting. (A residency costing more than 16 ms lets its own poll work up
to that long, ``program_jobs.fused_work_budget``; this one-transform, one-check
program has no such second poll of work to merge, and ``polls`` reports the
count per setting.) ``--work-clock wall`` keeps the product's wall-clock budget.

Per real suspend and hydrate call it records wall time, process CPU time,
retired instructions and the bundle's size on disk; per program, the whole
stage/poll/commit cost. The stage holds the target and its dependencies, not
the cohort, so ``large_target`` (a 99,856-vertex grid, just under the
100,000-vertex program input limit) is the cohort where bundle bytes matter.
"""

import argparse
import itertools
import json
import os
from pathlib import Path
import sys

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-stage-compression-benchmark"))
from angelo_agent import native_programs, program_storage, scene_queries  # noqa: E402
from angelo_agent.program_dependencies import SuspendedStage  # noqa: E402

COHORTS = ("default", "many_objects", "dense_geometry", "deep_collections", "large_target")
LARGE_TARGET_SEGMENTS = 315  # 316 x 316 = 99,856 vertices.
MODES = {True: "compressed", False: "uncompressed"}


def parse(argv):
    parser = argparse.ArgumentParser(description="Stage bundle compression benchmark")
    parser.add_argument("--samples", type=int, default=5, help="measured rounds per cohort after one warmup")
    parser.add_argument("--cohorts", default=",".join(COHORTS))
    parser.add_argument("--work-clock", choices=("instructions", "wall"), default="instructions")
    args = parser.parse_args(argv)
    args.cohorts = [value for value in args.cohorts.split(",") if value]
    if not 1 <= args.samples <= 30 or not args.cohorts or set(args.cohorts) - set(COHORTS):
        parser.error("Use 1-30 samples and cohorts from " + ", ".join(COHORTS))
    return args


def instrument(calls):
    """Wrap this job's backend so each real suspend/hydrate is measured."""
    def on_job(job):
        backend = job.backend
        backend.memory_staging = False
        suspend, resume = backend.suspend_stage, backend.resume_stage

        def measured_suspend(stage, **kwargs):
            if isinstance(stage, SuspendedStage):
                return suspend(stage, **kwargs)
            result, metrics = perf_counters.measure(lambda: suspend(stage, **kwargs))
            metrics["bundle_bytes"] = os.path.getsize(result.bundle)
            calls.append(("suspend", metrics))
            return result

        def measured_resume(stage):
            if not isinstance(stage, SuspendedStage):
                return resume(stage)
            result, metrics = perf_counters.measure(lambda: resume(stage))
            calls.append(("hydrate", metrics))
            return result

        # Instance attributes shadow the mixin methods for this job only.
        backend.suspend_stage, backend.resume_stage = measured_suspend, measured_resume
    return on_job


def summarize_mode(programs, calls):
    suspends = [metrics for kind, metrics in calls if kind == "suspend"]
    hydrates = [metrics for kind, metrics in calls if kind == "hydrate"]
    bundles = [metrics["bundle_bytes"] for metrics in suspends]
    per_program = [dict(program["metrics"]) for program in programs]
    return {"programs": len(programs), "suspend_calls": len(suspends), "hydrate_calls": len(hydrates),
            "suspend": perf_counters.summarize(suspends), "hydrate": perf_counters.summarize(hydrates),
            "bundle_bytes": perf_counters.describe(bundles), "program": perf_counters.summarize(per_program),
            "polls": [program["job"]["poll_count"] for program in programs]}


def build(window, cohort):
    if cohort == "default":
        return fixtures.default_fixture(window)
    if cohort == "large_target":
        return fixtures.cohort_fixture(window, cohort, target_segments=LARGE_TARGET_SEGMENTS)
    return fixtures.cohort_fixture(window, cohort)


def run_cohort(window, cohort, samples, ratchet, work_clock):
    target = build(window, cohort)
    handle = scene_queries.object_handle(target)
    offsets = itertools.count()
    programs = {True: [], False: []}
    calls = {True: [], False: []}
    for round_index in range(1 + samples):
        # Alternate which setting runs first, so slow drift cannot favour one.
        for compress in ((True, False) if round_index % 2 == 0 else (False, True)):
            program_storage.STAGE_BUNDLE_COMPRESSION = compress
            round_calls = []
            job, metrics = perf_counters.measure(lambda: fixtures.staged_program(
                window, target, handle, 1, next(offsets), on_job=instrument(round_calls), work_clock=work_clock))
            if round_index:  # Round 0 is the warmup for both settings.
                programs[compress].append({"metrics": metrics, "job": job})
                calls[compress].extend(round_calls)
    program_storage.STAGE_BUNDLE_COMPRESSION = True
    entry = {"fixture": fixtures.fixture_counts(window), "target_vertices": len(target.data.vertices)}
    for compress, name in MODES.items():
        entry[name] = summarize_mode(programs[compress], calls[compress])
        for phase in ("suspend", "hydrate"):
            ratchet.add("stage_bundle/%s/%s_%s" % (cohort, phase, name),
                        {"instructions": entry[name][phase]["instructions"],
                         "validity": entry[name][phase].get("validity")})
    compressed, plain = entry["compressed"], entry["uncompressed"]
    entry["uncompressed_vs_compressed"] = {
        "bundle_bytes_ratio": round(plain["bundle_bytes"]["median"] / compressed["bundle_bytes"]["median"], 3),
        **{"%s_%s_ratio" % (phase, metric): round(plain[phase][metric]["median"] / compressed[phase][metric]["median"], 3)
           for phase in ("suspend", "hydrate", "program") for metric in ("cpu_ms", "instructions", "wall_ms")
           if compressed[phase][metric] and plain[phase][metric] and compressed[phase][metric]["median"]}}
    print("COMPRESSION", cohort, json.dumps(entry["uncompressed_vs_compressed"]), flush=True)
    return entry


def main():
    # This comparison isolates unconditional zstd versus plain serialization.
    threshold = program_storage.STAGE_BUNDLE_COMPRESSION_MIN_SIZE
    program_storage.STAGE_BUNDLE_COMPRESSION_MIN_SIZE = 0
    args = parse(fixtures.script_arguments())
    window = bpy.context.window
    report = dict(fixtures.environment(),
                  method="one-transform one-check staged program per setting per round; 1 warmup + %d rounds, "
                         "setting order alternating per round; instrumented suspend/hydrate calls" % args.samples,
                  product_default=program_storage.STAGE_BUNDLE_COMPRESSION, cohorts={}, success=False)
    assert report["product_default"] is True, "The product default changed; this benchmark compares against True"
    work_clock = fixtures.instruction_work_clock if args.work_clock == "instructions" else None
    report["work_clock"] = args.work_clock
    ratchet = fixtures.Ratchet()
    try:
        for cohort in args.cohorts:
            report["cohorts"][cohort] = run_cohort(window, cohort, args.samples, ratchet, work_clock)
    finally:
        program_storage.STAGE_BUNDLE_COMPRESSION = True
        program_storage.STAGE_BUNDLE_COMPRESSION_MIN_SIZE = threshold
        native_programs.cleanup_all()
    if work_clock is not None:  # Wall-clock poll splitting is load-dependent; keep it out of the ratchet.
        report["ratchet"] = ratchet.section()
    report["success"] = True
    (OUT / "stage-compression.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("COMPRESSION_BENCHMARK_DONE", json.dumps({"success": True, "results": str(OUT / "stage-compression.json")}),
          flush=True)


main()
