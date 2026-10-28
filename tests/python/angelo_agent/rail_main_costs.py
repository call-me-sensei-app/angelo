# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Main tile's choice on large scenes, for ``test_blender_rail_main_view.py`` (its ``suite`` module gives the
scene builders, the artist's view stand-in and ``check``): fields of 2,000 and 8,000 objects, and ~200k Geometry
Nodes instances under a scene camera that sees them and one aimed away, each counted in the decision pictures it
draws, against a decision picture's own measured time."""

import math
import time

import bpy

from angelo_agent import live_view, rail_pictures


def decisions(suite, window, area, region, found, samples=5):
    """``lead_view`` as the rail calls it, real decision pictures and all, after one untimed warm-up: the median ms
    of ``samples`` choices, the decision pictures each drew (counted), and the median
    ms of a decision picture itself (``shown_share``, read back as the choice reads it) and of the final picture."""
    import gpu
    scene, layer = window.scene, window.view_layer
    offscreen = gpu.types.GPUOffScreen(*suite.SIZE)
    window_, host_area, host_region = suite.host()
    drawn = [0]

    def shows(matrix, lens):
        drawn[0] += 1
        return rail_pictures.shown_share(scene, offscreen, host_area.spaces.active, matrix, lens, layer)

    def once():
        drawn[0], started = 0, time.perf_counter()
        chosen = rail_pictures.lead_view(window, area, region, found, shows=shows)
        return chosen, (time.perf_counter() - started) * 1000, drawn[0]

    def timed(job):
        started = time.perf_counter()
        job()
        return (time.perf_counter() - started) * 1000

    median = lambda values: sorted(values)[len(values) // 2]   # noqa: E731
    with bpy.context.temp_override(window=window_, area=host_area, region=host_region):
        once()
        fresh = [once() for _ in range(samples)]
        view, projection, source = fresh[-1][0]
        unit = [timed(lambda: shows(view, projection)) for _ in range(samples)]
        final = [timed(lambda: live_view.native_draw(scene, offscreen, host_area.spaces.active, view, projection,
                                                     view_layer=layer)) for _ in range(samples)]
    offscreen.free()
    return {"source": source, "decide_ms": round(median([ms for _, ms, _ in fresh]), 3),
            "decision_pictures": max(count for _, _, count in fresh),
            "decision_picture_ms": round(median(unit), 3), "picture_ms": round(median(final), 3)}


def scatter(suite, side):
    """``side`` x ``side`` cube instances on a 100 m grid made by Geometry Nodes (one object, side**2 instances)."""
    tree = bpy.data.node_groups.new("Main Scatter", 'GeometryNodeTree')
    tree.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    grid, cube = tree.nodes.new("GeometryNodeMeshGrid"), tree.nodes.new("GeometryNodeMeshCube")
    grid.inputs["Size X"].default_value = grid.inputs["Size Y"].default_value = 100.0
    grid.inputs["Vertices X"].default_value = grid.inputs["Vertices Y"].default_value = side
    cube.inputs["Size"].default_value = (0.1, 0.1, 0.1)
    on = tree.nodes.new("GeometryNodeInstanceOnPoints")
    out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(grid.outputs["Mesh"], on.inputs["Points"])
    tree.links.new(cube.outputs["Mesh"], on.inputs["Instance"])
    tree.links.new(on.outputs["Instances"], out.inputs[0])
    carrier = suite.box("Main Scatter", None, mesh=bpy.data.meshes.new("Main Scatter"))
    carrier.modifiers.new("Scatter", 'NODES').node_group = tree


OVERHEAD_MS = 8.0   # A choice costs the decision pictures it draws and this at most: the per-object walk it replaced
                    # cost ~27 ms at ~200k instances (the coordinator, 2026-10-25), so no walk fits in it.


def within_budget(suite, name, timing):
    """A choice draws at most two decision pictures, each counted, and costs those pictures, in the decision picture's
    own measured time, plus ``OVERHEAD_MS``."""
    unit = timing["decision_picture_ms"]
    suite.check(name + ": at most two decision pictures", timing["decision_pictures"] <= 2, timing["decision_pictures"])
    suite.check(name + ": a choice costs its pictures only",
          timing["decide_ms"] <= timing["decision_pictures"] * unit + OVERHEAD_MS, (timing["decide_ms"], unit))


def costs(suite, window):
    """The choice's cost on large scenes, against the picture it chooses for (coordinator, 2026-10-25): a lead redraw
    cost 75-100 ms at ~200k instances with up to two decision pictures and two per-object walks. Fields of 2,000 and
    8,000 objects with a view between them (no decision picture fills), and ~200k Geometry Nodes instances under a
    scene camera that sees them (one picture decides) and aimed away (two do)."""
    view_layer = window.view_layer
    for total in (2000, 8000):
        suite.clear()
        mesh = bpy.data.meshes.new("Main Cost Cube")
        mesh.from_pydata([(x * .1, y * .1, z * .1) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                         [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
        side = int(math.sqrt(total / 2))
        for index in range(total):   # Two fields either side: the view between them sees none.
            x = (index % side) * 0.5 + (100.0 if index % 2 else -100.0 - side * 0.5)
            suite.box("Main Cost %d" % index, None, (x, (index // 2 // side) * 0.5, 0.1), mesh=mesh)
        view_layer.update()
        rail_pictures._scene_boxes.clear()
        started = time.perf_counter()
        found = rail_pictures._scene_bounds(window)
        bounds_ms = round((time.perf_counter() - started) * 1000, 3)
        area, region = suite.artist((0.0, -10.0, 1.0), (0.0, 0.0, 1.0), 20.0)
        timing = dict(decisions(suite, window, area, region, found), bounds_read_ms=bounds_ms)
        suite.report["timings"]["objects_%d" % total] = timing
        suite.check("objects_%d: the view between the fields sees none" % total, timing["source"] == "bounds",
              timing["source"])
        within_budget(suite, "objects_%d" % total, timing)
    for name, target in (("instances_200k_camera_sees_them", (0.0, 0.0, 0.0)),
                         ("instances_200k_camera_aimed_away", (0.0, -400.0, 40.0))):
        suite.clear()
        scatter(suite, 450)   # 202,500 instances.
        suite.scene_camera((0.0, -120.0, 70.0), target)
        view_layer.update()
        rail_pictures._scene_boxes.clear()
        started = time.perf_counter()
        found = rail_pictures._scene_bounds(window)
        bounds_ms = round((time.perf_counter() - started) * 1000, 3)
        area, region = suite.artist((0.0, -120.0, 70.0), (0.0, 0.0, 0.0), 40.0)
        timing = dict(decisions(suite, window, area, region, found), bounds_read_ms=bounds_ms)
        suite.report["timings"][name] = timing
        suite.check(name + ": decided", timing["source"] == ("camera" if "sees" in name else "view"), timing["source"])
        within_budget(suite, name, timing)
