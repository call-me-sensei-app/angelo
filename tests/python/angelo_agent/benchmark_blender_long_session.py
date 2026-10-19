# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-edit host cost over a long session, and by the edited object's dependencies. Unbilled; no model.

Run the Angelo binary --background --factory-startup --python-exit-code 1
--python this_file. Every edit is a real staged program (bind with the exact
revision, private stage, checks, commit; ``native_programs`` directly, no UI
timer, Undo or conversation journal), timed by the main thread's retired
instructions with the instruction work clock (``benchmark_fixtures``).

* ``session``: 500 one-operation programs on one cube, in the long benchmark's
  cycle (move, resize, bevel, material). The material edit gives the cube a new
  Principled material in its one slot, so the cube's dependencies stay the same
  size while the file gains an orphan mesh and material per cycle, as an
  artist's material edits do. The median cycle of the first ten and of the last
  ten are ratchet operations, and the run fails when the last exceeds the first
  by more than ``FLAT_LIMIT``: per-edit cost must not grow with the session.
* ``dependencies``: the same move on a cube with 1, 8 and 32 material slots
  (``material_slots_*``). Exact preconditions read every dependency, so this
  cost grows with the edited object's materials by design
  (doc/angelo/performance.rst, "Per-edit cost over a long session"); the
  operations pin its slope, they do not require it to be flat.
"""

import json
from pathlib import Path
import statistics
import sys
import time

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-long-session-benchmark"))
from angelo_agent import native_programs, scene_queries  # noqa: E402

CYCLES = 125  # Four edits each: 500 edits.
WINDOW = 10  # Cycles in each measured end of the session.
FLAT_LIMIT = 1.10  # Last ten cycles / first ten, median instructions per cycle.
SLOTS = (1, 8, 32)
WORK_CLOCK = fixtures.instruction_work_clock if fixtures.perf_counters.read_rusage() else None


def instructions():
    counts = fixtures.perf_counters.read_thread_counts() or fixtures.perf_counters.read_rusage()
    return counts["instructions"] if counts else 0


def principled(name, colour):
    material = bpy.data.materials.new(name)
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = colour
    return material


def cube_target(window, slots):
    """The long benchmark's cube: 2 m, a Bevel modifier and ``slots`` Principled materials."""
    fixtures.clear_scene_data()
    target = bpy.data.objects.new("Eval Cube", fixtures.new_mesh("Eval Cube", fixtures.cube))
    target.scale = (2., 2., 2.)
    target.modifiers.new("Bevel", "BEVEL")
    for index in range(slots):
        target.data.materials.append(principled("Slot material %02d" % index, (index / 40, .5, .3, 1.)))
    window.scene.collection.objects.link(target)
    return fixtures.finish_fixture(window, target)


def program(window, target, operation, checks=()):
    """Bind, stage, poll to ready and commit one operation; main-thread instructions and wall time."""
    handle = scene_queries.object_handle(target)
    started, clock = instructions(), time.perf_counter()
    binding = {"handle": handle, "revision": scene_queries.object_revision(window, target)}
    args = {"mode": "APPLY", "read_set": [], "write_set": [binding],
            "operations": [dict(operation, target=handle)], "expected_checks": [dict(c, target=handle) for c in checks]}
    job = native_programs.begin(window, "angelo_scene_program", args)
    if WORK_CLOCK is not None:
        job.backend.operation_quantum_clock = WORK_CLOCK
    try:
        for _ in range(128):
            job.poll()
            if job.closed or job.ready_to_commit:
                break
        assert job.ready_to_commit, operation["kind"]
        result = job.commit()
        assert result.get("success") and result["status"] == "committed", result
    finally:
        if not job.closed:
            job.cancel()
    return instructions() - started, (time.perf_counter() - clock) * 1000


def cycle(window, target, number):
    """One cycle of the long benchmark's four edits, each value new (as eval_live_tasks)."""
    location = [float(number % 5 - 2), float(number % 3 - 1), float(number % 2)]
    size = [1. + number % 4 * .5, 2. - number % 3 * .5, 1. + number % 2]
    edits = [({"kind": "transform", "parameters": {"location": location}},
              [{"kind": "transform", "property": "location", "expected": location, "tolerance": 1e-4}]),
             ({"kind": "transform", "parameters": {"scale": [value / 2 for value in size]}}, []),
             ({"kind": "modifier_configure", "parameters": {
                 "modifier": "Bevel", "type": "BEVEL",
                 "settings": {"width": round(.05 + number % 4 * .025, 3), "segments": 2 + number % 3}}}, []),
             ({"kind": "material_principled", "parameters": {
                 "name": "Long Material %03d" % number, "slot": 0,
                 "inputs": {"Base Color": [.2 + number % 4 * .2, .8 - number % 3 * .25, .3, 1.],
                            "Roughness": .2 + number % 5 * .15, "Metallic": number % 3 * .3}}},
              [{"kind": "material_assignment", "material": "Long Material %03d" % number, "all_faces": True}])]
    costs = [program(window, target, operation, checks) for operation, checks in edits]
    return sum(count for count, _ in costs), sum(wall for _, wall in costs)


def ratchet_stats(values):
    return {"instructions": {"median": statistics.median(values)}}


window = bpy.context.window
ratchet = fixtures.Ratchet()
report = dict(fixtures.environment(), method="main-thread retired instructions per staged program; work clock",
              session={}, dependencies={})
problems = []

target = cube_target(window, 1)
per_cycle = [cycle(window, target, number) for number in range(CYCLES)]
first, last = per_cycle[:WINDOW], per_cycle[-WINDOW:]
first_median = statistics.median(count for count, _ in first)
last_median = statistics.median(count for count, _ in last)
ratio = last_median / first_median
report["session"] = {
    "cycles": CYCLES, "edits": CYCLES * 4, "orphan_meshes": sum(m.users == 0 for m in bpy.data.meshes),
    "orphan_materials": sum(m.users == 0 for m in bpy.data.materials),
    "instructions_per_cycle": [count for count, _ in per_cycle], "wall_ms_per_cycle": [round(w, 2) for _, w in per_cycle],
    "first_median": first_median, "last_median": last_median, "last_over_first": round(ratio, 4), "limit": FLAT_LIMIT}
ratchet.add("long_session/edit_cycle_first_10", ratchet_stats([count for count, _ in first]))
ratchet.add("long_session/edit_cycle_last_10", ratchet_stats([count for count, _ in last]))
if ratio > FLAT_LIMIT:
    problems.append("Per-edit cost grew over the session: the last %d cycles took %.3fx the first %d"
                    % (WINDOW, ratio, WINDOW))
print("LONG_SESSION", json.dumps({key: report["session"][key] for key in
                                  ("edits", "first_median", "last_median", "last_over_first", "orphan_meshes")}), flush=True)

for slots in SLOTS:
    target = cube_target(window, slots)
    samples = [program(window, target, {"kind": "transform", "parameters": {"location": [float(i % 3), 0., 0.]}})
               for i in range(6)][1:]  # One warmup.
    counts = [count for count, _ in samples]
    report["dependencies"]["material_slots_%d" % slots] = {
        "instructions_median": statistics.median(counts), "wall_ms_median": round(statistics.median(w for _, w in samples), 2)}
    ratchet.add("long_session/move_material_slots_%02d" % slots, ratchet_stats(counts))
    print("LONG_SESSION_SLOTS", slots, json.dumps(report["dependencies"]["material_slots_%d" % slots]), flush=True)

report["ratchet"] = ratchet.section()
report["problems"] = problems
report["success"] = not problems
native_programs.cleanup_all()
(OUT / "long-session.json").write_text(json.dumps(report, indent=2, allow_nan=False))
print("LONG_SESSION_BENCHMARK_DONE", json.dumps({"success": report["success"], "problems": problems,
                                                "results": str(OUT / "long-session.json")}), flush=True)
if problems:
    sys.exit(1)
