# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared scene fixtures, measurement and ratchet output for native benchmarks.

Only the benchmark scripts in this directory import this module, inside an
isolated ``--background --factory-startup`` Angelo process. Fixtures replace the
scratch scene's contents; never call them in an artist's document.

Every measured operation reports wall time (``samples_ms``/``median_ms``/
``p95_ms``, unchanged from earlier reports) plus process CPU time, retired
instructions and cycles from ``perf_counters``. Wall time is the user-visible
cost but varies with machine load; instruction counts are the stable signal for
comparing code changes and feed the ratchet (``perf_ratchet.py``).
"""

import os
from pathlib import Path
import platform
import sys
import time

import bmesh
import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import native_suite_paths  # noqa: E402  (path set above)
import perf_counters  # noqa: E402  (path set above)

COHORTS = ("many_objects", "dense_geometry", "deep_collections")
# Nominal retired instructions per "second" for ``instruction_work_clock``, a
# fixed convention rather than a machine property. At 2e10 the benchmark
# programs split into the poll counts that the fused-poll measurements in
# doc/angelo/performance.rst recorded (one poll at 100 and 2,000 objects, two
# on the 99,856-vertex grid); 1e10 split each of them into one more poll.
WORK_CLOCK_INSTRUCTIONS_PER_SECOND = 2e10


def default_output(name):
    """``$ANGELO_QA_DIR``, else ``<short temp root>/<name>``: the folder depth the ratchet ceilings were
    recorded at on macOS, since snapshot storage costs more per folder level (native_suite_paths)."""
    return os.environ.get("ANGELO_QA_DIR") or str(native_suite_paths.short_temp_root() / name)


def isolate(out):
    """Private accounts/state for this process; returns the output directory."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(out / "accounts"),
                      ANGELO_STATE_DIR=str(out / "state"))
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    bpy.context.preferences.filepaths.temporary_directory = str(out)
    return out


def script_arguments():
    """Arguments after Blender's ``--`` separator."""
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def platform_key():
    return sys.platform + "-" + platform.machine().lower()


def environment():
    """Binary and machine identity recorded in every report."""
    return {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "platform": platform.platform(),
            "machine": platform.machine(), "platform_key": platform_key(),
            "opensubdiv": bool(bpy.app.build_options.opensubdiv), "counters": perf_counters.availability(),
            "load_average_at_start": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
            "cpu_count": os.cpu_count(), "paid_requests": 0}


def measure(function, samples=5, warmup=1):
    """Warmup + samples; wall keys stay compatible with earlier reports."""
    summary, raw, result = perf_counters.repeat(function, samples=samples, warmup=warmup)
    walls = [sample["wall_ms"] for sample in raw]
    stats = {"samples_ms": [round(value, 3) for value in walls], "median_ms": summary["wall_ms"]["median"],
             "p95_ms": summary["wall_ms"]["p95"], "cpu_ms": summary["cpu_ms"],
             "instructions": summary["instructions"], "cycles": summary["cycles"], "validity": summary["validity"]}
    return stats, result


class Ratchet:
    """Median instruction counts per operation, for ``perf_ratchet.py``.

    Each operation also records its spread (relative range of its samples,
    ``None`` when the benchmark kept only a median) and, when its samples came
    from ``perf_counters.measure``, their validity. An operation whose every
    sample was rejected is listed under ``rejected`` instead of measured, so
    the gate fails it with that reason. ``bytes`` operations are exact sizes,
    and ``gross`` values are reported, never compared.
    """

    def __init__(self):
        self.operations, self.spread, self.validity, self.rejected = {}, {}, {}, {}
        self.units, self.gross = {}, {}

    def add(self, name, stats):
        instructions, validity = stats.get("instructions"), stats.get("validity")
        if validity is not None:
            self.validity[name] = validity
        if instructions and instructions.get("median") is not None:
            self.operations[name] = int(round(instructions["median"]))
            spread = instructions.get("relative_range", instructions.get("spread"))
            self.spread[name] = spread if isinstance(spread, (int, float)) else None
        elif validity is not None and validity.get("rejected"):
            self.rejected[name] = validity

    def add_bytes(self, name, size):
        """An exact byte count (no spread): ratcheted with its own unit."""
        if size is not None:
            self.operations[name] = int(size)
            self.units[name] = "bytes"

    def note_gross(self, name, value):
        if value is not None:
            self.gross[name] = int(round(value))

    def section(self):
        return {"metric": "instructions", "statistic": "median", "platform": platform_key(),
                "operations": dict(sorted(self.operations.items())), "spread": dict(sorted(self.spread.items())),
                "validity": dict(sorted(self.validity.items())), "rejected": dict(sorted(self.rejected.items())),
                "units": dict(sorted(self.units.items())), "gross": dict(sorted(self.gross.items()))}


def instruction_work_clock():
    """Seconds of nominal work, derived from this process's retired instructions.

    A staged program's fused polls stop after 16 ms of work and its action
    quanta after 4 ms, both read from ``time.perf_counter`` by default. On a
    loaded machine the same program then splits into more polls, and each
    extra poll pays a stage write and reload, so its instruction count varies
    by up to about 18% between runs. Passing this clock through the backend's
    existing ``operation_quantum_clock`` hook makes the split depend on work
    done instead of machine load. Use it only for ratchet measurements; the
    product and the wall-clock timings keep ``time.perf_counter``.

    The clock counts the calling (main) thread only. A process-wide count also
    included Blender's worker threads, so a program whose poll work sat near
    the 16 ms budget split into one or two polls at random: the eight-operation
    program measured 1.09 or 1.31 billion instructions on the same code (one
    extra stage write and reload). The process count remains the fallback.
    """
    counters = perf_counters.read_thread_counts() or perf_counters.read_rusage()
    if counters is None:
        raise RuntimeError("The instruction work clock needs proc_pid_rusage instruction counters (macOS)")
    return counters["instructions"] / WORK_CLOCK_INSTRUCTIONS_PER_SECOND


def new_mesh(name, build):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        build(bm)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    return mesh


def grid(segments):
    return lambda bm: bmesh.ops.create_grid(bm, x_segments=segments, y_segments=segments, size=1)


def cube(bm):
    bmesh.ops.create_cube(bm, size=1)


def clear_scene_data():
    """Remove every object, mesh, material and collection in one batch (not measured)."""
    bpy.data.batch_remove(ids=[block for data in (bpy.data.objects, bpy.data.meshes, bpy.data.materials,
                                                  bpy.data.collections) for block in data])


def finish_fixture(window, target):
    from angelo_agent import scene_queries
    window.view_layer.objects.active = target
    target.select_set(True)
    window.view_layer.update()
    scene_queries.invalidate()
    return target


def default_fixture(window):
    """100 objects: one 128x128 grid target and 99 shared eight-vertex meshes."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    obj = bpy.data.objects.new("Execution benchmark target", new_mesh("Benchmark grid", grid(128)))
    window.scene.collection.objects.link(obj)
    shared = new_mesh("Benchmark neighbor shared mesh", cube)
    for index in range(99):
        neighbor = bpy.data.objects.new("Benchmark neighbor %03d" % index, shared)
        neighbor.location = (index % 10 * 3 + 4, index // 10 * 3, 0)
        window.scene.collection.objects.link(neighbor)
    return finish_fixture(window, obj)


def shared_node_material():
    """One noise/ramp material shared by every prop, as in a reused asset kit."""
    material = bpy.data.materials.new("Benchmark shared material")
    nodes, links = material.node_tree.nodes, material.node_tree.links
    coordinates, mapping = nodes.new("ShaderNodeTexCoord"), nodes.new("ShaderNodeMapping")
    noise, ramp = nodes.new("ShaderNodeTexNoise"), nodes.new("ShaderNodeValToRGB")
    links.new(coordinates.outputs["Object"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Factor"], ramp.inputs["Factor"])
    links.new(ramp.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    return material


def collection_leaves(parent, depth, fanout):
    if depth == 0:
        return [parent]
    leaves = []
    for index in range(fanout):
        child = bpy.data.collections.new("%s.%d" % (parent.name, index))
        parent.children.link(child)
        leaves += collection_leaves(child, depth - 1, fanout)
    return leaves


def cohort_fixture(window, kind, target_segments=128):
    """Rebuild the scene for one scale cohort and return its staged-program target.

    ``many_objects``: 2,000 objects sharing one node material (every tenth with
    a Bevel modifier). ``dense_geometry``: 20 grids, about one million vertices.
    ``deep_collections``: 2,000 objects in a 511-collection tree, some linked
    twice. ``large_target``: only the target, sized by ``target_segments``.
    """
    clear_scene_data()
    root = bpy.data.collections.new("Benchmark cohort")
    window.scene.collection.children.link(root)
    target = bpy.data.objects.new("Execution benchmark target", new_mesh("Benchmark grid", grid(target_segments)))
    root.objects.link(target)
    if kind == "dense_geometry":
        for index in range(19):
            dense = bpy.data.objects.new("Benchmark dense %02d" % index, new_mesh("Dense %02d" % index, grid(224)))
            dense.location = (index * 3 + 4, 0, 0)
            root.objects.link(dense)
    elif kind in {"many_objects", "deep_collections"}:
        material = shared_node_material()
        owners = collection_leaves(root, 8, 2) if kind == "deep_collections" else [root]
        for index in range(1999):
            prop = bpy.data.objects.new("Benchmark prop %04d" % index, new_mesh("Prop %04d" % index, cube))
            prop.location = (index % 50 * 3 + 4, index // 50 * 3, 0)
            prop.data.materials.append(material)
            if index % 10 == 0:
                prop.modifiers.new("Bevel", "BEVEL")
            owners[index % len(owners)].objects.link(prop)
            if index % 5 == 0 and len(owners) > 1:
                owners[(index + 1) % len(owners)].objects.link(prop)
    elif kind != "large_target":
        raise ValueError("Unknown benchmark cohort " + kind)
    return finish_fixture(window, target)


def fixture_counts(window):
    return {"objects": len(window.scene.objects), "collections": len(bpy.data.collections),
            "vertices": sum(len(o.data.vertices) for o in window.scene.objects if o.type == "MESH")}


def staged_program(window, target, handle, steps=1, offset=0, on_job=None, work_clock=None):
    """Stage, poll to ready and commit one transform program on ``target``.

    ``handle`` is the target's document-scoped handle, resolved once by the
    caller so it is not part of the measured program cost.
    ``on_job(job)`` runs right after ``begin`` (before any poll), so a benchmark
    can wrap that job's backend. ``work_clock`` (for example
    ``instruction_work_clock``) replaces the poll budget clock for this job
    only. The final location is asserted, so every sample is a real edit
    rather than a no-op.
    """
    from angelo_agent import native_programs, scene_queries
    binding = {"handle": handle, "revision": scene_queries.object_revision(window, target)}
    operations = [{"kind": "transform", "target": handle,
                   "parameters": {"location": [float(offset + index + 1), 0., 0.]}}
                  for index in range(steps)]
    args = {"mode": "APPLY", "read_set": [], "write_set": [binding], "operations": operations,
            "expected_checks": [{"kind": "transform", "target": handle, "property": "location",
                                 "expected": [float(offset + steps), 0., 0.], "tolerance": 1e-6}]}
    started = time.perf_counter()
    job = native_programs.begin(window, "angelo_scene_program", args)
    if work_clock is not None:
        job.backend.operation_quantum_clock = work_clock  # Instance attribute: this job only.
    if on_job is not None:
        on_job(job)
    ticks = []
    try:
        for _ in range(128):
            tick_start = time.perf_counter()
            result = job.poll()
            ticks.append((time.perf_counter() - tick_start) * 1000)
            if job.closed or job.ready_to_commit:
                break
        assert job.ready_to_commit, result
        commit_start = time.perf_counter()
        result = job.commit()
        commit_ms = (time.perf_counter() - commit_start) * 1000
        assert result.get("success") and result["status"] == "committed", result
        assert tuple(target.location) == (float(offset + steps), 0., 0.)
        return {"poll_count": len(ticks), "polls_ms": [round(value, 3) for value in ticks],
                "max_poll_ms": round(max(ticks), 3), "commit_ms": round(commit_ms, 3),
                "native_ms": result["native_exec_ms"], "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    finally:
        if not job.closed:
            job.cancel()
