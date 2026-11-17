# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the first draw of newly compiled shaders costs on Blender's main thread: one object of several new materials,
and a new LIT world (doc/angelo/agent_execution.rst, "Residue (1.1)"; inventory C5). Background or GUI, unbilled.

    Angelo -b --factory-startup --python tests/python/angelo_agent/bench_first_draw.py

Environment: ``PROBE_SLOTS`` (materials on the object, default 5), ``PROBE_ROUNDS`` (default 5), ``PROBE_OUT`` (a
folder; writes first-draw.json). Each round makes one object whose every material is a shader of its own
(``fresh_shader``: code no earlier run compiled, so no driver cache serves it), lets them compile in the background
through ``view_warm``'s private compile-ahead draw, waits until ``_angelo_live.shader_state`` reports none missing or
queued, then times, all on the main thread:
  ``first_ms``   the object's first private draw (``only=[object]``, as ``view_first_draw.first_draws`` draws it);
  ``again_ms``   the same draw once more, so ``first_ms - again_ms`` is what the first draw built;
  ``view_ms``    the view itself once prepared: an EEVEE image render of the LIT look (as ``view_capture`` draws);
  ``world_ms``   a private draw of no object after a new world of its own code is assigned (LIT), and
  ``world_again_ms`` that draw once more once the world compiled, then ``world_view_ms`` the view.
When the build reports pipeline statistics (``_angelo_live.pipeline_stats``) each record also carries how many
pipeline states the main thread built during each draw and how long that took, and how many were built in the
background. Prints one JSON line per round and writes them all.
"""

import json
import os
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fresh_shader  # noqa: E402

SLOTS = int(os.environ.get("PROBE_SLOTS", "5"))
ROUNDS = int(os.environ.get("PROBE_ROUNDS", "5"))
OUT = Path(os.environ.get("PROBE_OUT") or Path(bpy.app.tempdir or "/tmp", "first-draw"))
WAIT_SECONDS = 60.0
SIZE = (64, 64)


def _live():
    import _bpy
    return _bpy._angelo_live


def _host():
    """The View3D space and region data a private draw takes its settings from (no window in background mode)."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D' and area.spaces.active.region_3d is not None:
                return area.spaces.active, area.spaces.active.region_3d
    raise RuntimeError("No 3D View in the startup file")


def _stats():
    live = _live()
    return live.pipeline_stats() if hasattr(live, "pipeline_stats") else None


def _draw(only=None, lit=True, prebuild=None):
    """One private compile-ahead draw (view_first_draw._draw's), its milliseconds and the pipeline statistics it
    changed."""
    import gpu
    space, rv3d = _host()
    layer = bpy.context.view_layer
    kwargs = {"only": only} if only is not None else {}
    if not lit:
        kwargs["lit"] = False
    if prebuild is not None:
        kwargs["prebuild"] = prebuild
    before = _stats()
    offscreen = gpu.types.GPUOffScreen(*SIZE)
    started = time.perf_counter()
    try:
        _live().draw(bpy.context.scene, offscreen, space, rv3d.view_matrix, rv3d.window_matrix, view_layer=layer,
                     compile_ahead=True, **kwargs)
    finally:
        offscreen.free()
    spent = (time.perf_counter() - started) * 1000.0
    after = _stats()
    delta = {key: round(after[key] - before[key], 1) for key in after} if before is not None else None
    return round(spent, 1), delta


def _view(size=256):
    """The view itself after its preparation: an EEVEE image render of the LIT look (``view_capture``'s draw), its
    milliseconds and the pipeline statistics it changed. What it still builds on the main thread is the residue."""
    import gpu
    space, rv3d = _host()
    area = next(a for screen in bpy.data.screens for a in screen.areas if a.spaces.active == space)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    shading = space.shading
    kept = (shading.type, shading.use_scene_lights, shading.use_scene_world)
    shading.type, shading.use_scene_lights, shading.use_scene_world = 'MATERIAL', True, True
    before = _stats()
    offscreen = gpu.types.GPUOffScreen(size, size)
    started = time.perf_counter()
    try:
        offscreen.draw_view3d(bpy.context.scene, bpy.context.view_layer, space, region, rv3d.view_matrix,
                              rv3d.window_matrix)
    finally:
        offscreen.free()
        shading.type, shading.use_scene_lights, shading.use_scene_world = kept
    spent = (time.perf_counter() - started) * 1000.0
    after = _stats()
    delta = {key: round(after[key] - before[key], 1) for key in after} if before is not None else None
    return round(spent, 1), delta


def _state():
    """``shader_state`` of the LIT look: with the world it draws where the build counts it (as ``view_warm`` reads)."""
    live = _live()
    world = {"world": True} if hasattr(live, "pipeline_stats") else {}
    return live.shader_state(bpy.context.view_layer.depsgraph, _host()[0], **world)


def _wait_compiled(only=None):
    """``(milliseconds, extra queue draws)`` until no material the scene draws is missing or queued. A missing one is
    queued by drawing again (as view_warm does: a cold EEVEE asks for nothing until its own shaders are ready), which
    a timed round never needs once the engine is warm; each such draw is counted, since it may build pipelines."""
    started, draws = time.perf_counter(), 0
    while True:
        state = _state()
        if state is not None and not state[1] and not state[2]:
            return round((time.perf_counter() - started) * 1000.0, 1), draws
        if time.perf_counter() - started > WAIT_SECONDS:
            raise RuntimeError("Shaders did not compile in %d s: %r" % (WAIT_SECONDS, state[:3] if state else None))
        if state is not None and state[1]:
            _draw(only)
            draws += 1
        time.sleep(0.005)


def _material(name, role):
    material = bpy.data.materials.new(name)
    tree = material.node_tree
    principled = tree.nodes.get("Principled BSDF")
    coords = tree.nodes.new("ShaderNodeTexCoord")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    tree.links.new(coords.outputs["Object"], noise.inputs["Vector"])
    rough = fresh_shader.chain(tree, noise.outputs["Fac"], 9, role)
    tree.links.new(rough, principled.inputs["Roughness"])
    tree.links.new(noise.outputs["Color"], principled.inputs["Base Color"])
    return material


def _object(n):
    """A grid of 16 faces with SLOTS new materials, a slot per face in turn (as an outfit or a car body carries
    them)."""
    mesh = bpy.data.meshes.new("Probe %d" % n)
    size = 5
    verts = [(x * 0.25, y * 0.25, 0.0) for y in range(size) for x in range(size)]
    faces = [(y * size + x, y * size + x + 1, (y + 1) * size + x + 1, (y + 1) * size + x)
             for y in range(size - 1) for x in range(size - 1)]
    mesh.from_pydata(verts, [], faces)
    mesh.uv_layers.new(name="UVMap")
    for index, polygon in enumerate(mesh.polygons):
        polygon.material_index = index % SLOTS
    for slot in range(SLOTS):
        mesh.materials.append(_material("Probe %d.%d" % (n, slot), "probe %d %d" % (n, slot)))
    obj = bpy.data.objects.new(mesh.name, mesh)
    obj.location = (n * 1.5 - 3.0, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def _world(n):
    world = bpy.data.worlds.new("Probe world %d" % n)
    world.use_nodes = True
    tree = world.node_tree
    background = tree.nodes.get("Background")
    coords = tree.nodes.new("ShaderNodeTexCoord")
    noise = tree.nodes.new("ShaderNodeTexNoise")
    tree.links.new(coords.outputs["Generated"], noise.inputs["Vector"])
    strength = fresh_shader.chain(tree, noise.outputs["Fac"], 9, "world %d" % n)
    tree.links.new(strength, background.inputs["Strength"])
    tree.links.new(noise.outputs["Color"], background.inputs["Color"])
    bpy.context.scene.world = world
    bpy.context.view_layer.update()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    import gpu
    if bpy.app.background:
        gpu.init()   # No window made a GPU context.
    records = []
    _draw()   # The engine's own shaders and the default world, untimed.
    _wait_compiled()
    _draw()
    _wait_prebuilt()
    for n in range(ROUNDS):
        obj = _object(n)
        record = {"round": n, "slots": SLOTS, "backend": __import__("gpu").platform.backend_type_get()}
        record["queue_ms"], _ = _draw()
        record["compile_wait_ms"], record["requeue_draws"] = _wait_compiled([obj])
        record["first_ms"], record["first_pipelines"] = _draw([obj])
        record["prebuilt_wait_ms"] = _wait_prebuilt()
        record["again_ms"], record["again_pipelines"] = _draw([obj])
        record["view_ms"], record["view_pipelines"] = _view()
        _world(n)
        record["world_ms"], record["world_pipelines"] = _draw([])
        record["world_compile_wait_ms"], _ = _wait_compiled([])
        record["world_again_ms"], record["world_again_pipelines"] = _draw([])
        record["world_prebuilt_wait_ms"] = _wait_prebuilt()
        record["world_view_ms"], record["world_view_pipelines"] = _view()
        print("FIRST_DRAW", json.dumps(record), flush=True)
        records.append(record)
    (OUT / "first-draw.json").write_text(json.dumps({"binary": bpy.app.binary_path, "records": records}, indent=2))


def _wait_prebuilt():
    """Milliseconds until no pipeline state queued by a prebuild draw is still building (0 on builds without it)."""
    started = time.perf_counter()
    stats = _stats()
    while stats is not None and stats.get("pending", 0):
        if time.perf_counter() - started > WAIT_SECONDS:
            raise RuntimeError("Pipelines did not build in %d s" % WAIT_SECONDS)
        time.sleep(0.002)
        stats = _stats()
    return round((time.perf_counter() - started) * 1000.0, 1)


try:
    main()
finally:
    if bpy.app.background:
        sys.stdout.flush()
