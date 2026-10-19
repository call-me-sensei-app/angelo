# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-request host time on a production-size scene: what one agent request spends in Blender.

Run alone, in an isolated process (no model, network, UI timer wait or GPU)::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_request_host_time.py [-- --props 5000]

The scene is a synthetic kit-bashed set: ``--props`` cube props with their own
meshes in 64 collections, sharing 40 node materials (texture coordinates,
mapping, noise, a ColorRamp and Principled BSDF; the ColorRamp is a generic
node type), every tenth with a Bevel, every twentieth with a Subdivision
Surface, plus eight 100,000-vertex hero meshes, a camera and three lights.
Eight props are selected; the active object is a hero mesh.

Each step is one request's host work as the product runs it: the turn context
built before the message is sent (``turn_context.prepare``, active and selected
objects revised, one object named in the message), a whole-scene search and a
discovery page (the usual first lookups), one object's detail, a one-operation
staged transform program on a prop (bind, polls, commit and its post-commit
revision), and the request snapshot (``ui.capture_checkpoint``). Reported per
step: medians of main-thread CPU ms (``time.thread_time`` where available),
wall ms and process instructions over ``--samples`` runs after one warmup.
``request_total`` adds the steps of a typical editing request: prepare, one
search, one page, one detail, one staged edit and one snapshot.
"""

import argparse
import json
from pathlib import Path
import statistics
import sys
import time
import uuid
from types import SimpleNamespace

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import perf_counters  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-request-host-time"))
from angelo_agent import native_programs, scene_queries, turn_context, ui  # noqa: E402
from angelo_agent.checkpoints import CheckpointStore  # noqa: E402

try:
    from angelo_agent.work_clock import THREAD_CLOCK  # noqa: E402
except ImportError:  # Older checkouts: process CPU time.
    THREAD_CLOCK = None
CPU = THREAD_CLOCK or time.process_time


def parse(argv):
    parser = argparse.ArgumentParser(description="Per-request host time on a production-size scene")
    parser.add_argument("--props", type=int, default=5000)
    parser.add_argument("--samples", type=int, default=5)
    return parser.parse_args(argv)


def kit_material(index):
    material = fixtures.shared_node_material()
    material.name = "Kit material %02d" % index
    material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = index / 40
    return material


def build(window, props):
    fixtures.clear_scene_data()
    root = bpy.data.collections.new("Set")
    window.scene.collection.children.link(root)
    groups = [bpy.data.collections.new("Set group %02d" % index) for index in range(64)]
    for group in groups:
        root.children.link(group)
    materials = [kit_material(index) for index in range(40)]
    objects = []
    for index in range(props):
        obj = bpy.data.objects.new("Prop %05d" % index, fixtures.new_mesh("Prop %05d" % index, fixtures.cube))
        obj.location = (index % 100 * 3, index // 100 * 3, 0)
        obj.data.materials.append(materials[index % len(materials)])
        if index % 10 == 0:
            obj.modifiers.new("Bevel", "BEVEL")
        if index % 20 == 5:
            obj.modifiers.new("Subdivision", "SUBSURF")
        groups[index % len(groups)].objects.link(obj)
        objects.append(obj)
    heroes = []
    for index in range(8):
        hero = bpy.data.objects.new("Hero %d" % index, fixtures.new_mesh("Hero %d" % index, fixtures.grid(315)))
        hero.location = (index * 4, -12, 0)
        hero.data.materials.append(materials[index])
        root.objects.link(hero)
        heroes.append(hero)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    root.objects.link(camera)
    for index in range(3):
        light = bpy.data.objects.new("Light %d" % index, bpy.data.lights.new("Light %d" % index, "AREA"))
        root.objects.link(light)
    for obj in window.scene.objects:
        obj.select_set(False)
    for obj in objects[1000:1008]:
        obj.select_set(True)
    fixtures.finish_fixture(window, heroes[0])
    return objects, heroes


def measure(function, samples):
    function()
    cpu, wall, instructions = [], [], []
    for _ in range(samples):
        counters = perf_counters.read_rusage()
        started_cpu, started = CPU(), time.perf_counter()
        function()
        wall.append((time.perf_counter() - started) * 1000)
        cpu.append((CPU() - started_cpu) * 1000)
        after = perf_counters.read_rusage()
        if counters and after:
            instructions.append(after["instructions"] - counters["instructions"])
    return {"cpu_ms": round(statistics.median(cpu), 3), "wall_ms": round(statistics.median(wall), 3),
            "instructions": int(statistics.median(instructions)) if instructions else None}


def main():
    args = parse(fixtures.script_arguments())
    window = bpy.context.window
    objects, heroes = build(window, args.props)
    prop = objects[2500 % len(objects)]
    handle = scene_queries.object_handle(prop)
    store = CheckpointStore(OUT / "snapshot-profile", str(uuid.uuid4()))
    controller = SimpleNamespace(checkpoints=store, _turn_checkpoint_policy={"capture_mode": "EVERY_REQUEST"})
    offsets = iter(range(1, 10 ** 6))

    def snapshot():
        record = ui.capture_checkpoint(controller)
        __import__("shutil").rmtree(store.folder(record["id"]))

    steps = {
        "turn_context_prepare": lambda: turn_context.prepare(window, "benchmark-chat",
                                                             message="Move '%s' up by 1 m." % prop.name),
        "search_100": lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 100}),
        "search_page_10": lambda: scene_queries.execute(window, "angelo_scene_search", {"limit": 10, "coverage": "PAGE"}),
        "object_detail": lambda: scene_queries.execute(window, "angelo_scene_object", {"object": handle}),
        "staged_edit": lambda: fixtures.staged_program(window, prop, handle, 1, offset=next(offsets)),
        "request_snapshot": snapshot,
    }
    report = dict(fixtures.environment(), props=args.props, objects=len(window.scene.objects),
                  vertices=fixtures.fixture_counts(window)["vertices"], samples=args.samples, steps={})
    for name, function in steps.items():
        report["steps"][name] = measure(function, args.samples)
        print("REQUEST_HOST_TIME", name, json.dumps(report["steps"][name]), flush=True)
    report["request_total"] = {key: round(sum(step[key] for step in report["steps"].values()), 3)
                               for key in ("cpu_ms", "wall_ms")}
    native_programs.cleanup_all()
    (OUT / "request-host-time.json").write_text(json.dumps(report, indent=2))
    print("REQUEST_HOST_TIME_DONE", json.dumps(report["request_total"]), flush=True)


if __name__ == "__main__":
    main()
