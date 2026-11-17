# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile-ahead draws prepare what a view needs off the main thread, and the view is right (inventory C5).

The first view of an object with several new materials, or of a new world, held Blender's main loop 330-360 ms
(Mac 1): the compile-ahead draw that gave the newly compiled materials their first draw built their Metal pipeline
states in it, and EEVEE compiled a new world's shaders synchronously in any draw. In a real Angelo (background,
offscreen GPU), with materials and a world whose code no earlier run compiled (``fresh_shader``):

* A compile-ahead draw of an object with four just-compiled materials builds no pipeline state in the draw (Metal):
  it queues them (``_angelo_live.pipeline_stats``: ``pending``), they are built in the background, none fails, and the
  LIT view drawn afterwards builds none of its own. Other backends build in the draw as before, never queue.
* A compile-ahead LIT draw of a new world compiles none of its shaders, on every backend: the draw waits for no
  shader (``pipeline_stats``: ``shaders_waited``, a count, never a time) and the world reads queued or compiled after
  it (``shader_state(..., world=True)``), where a view of a world nothing prepared waits for its shaders in its own
  draw; a MATERIAL look's state never counts it.
* The view after that preparation shows each material and the world in their own colours (emission, Standard view
  transform), never a placeholder or the default world.
* A draw that meets a world a compile-ahead draw queued, while it is still queued, waits for it and shows it: a view
  (the draw ``render.opengl`` makes too) and an F12 render, each with a world of its own code. The person's viewport
  and the ``render.opengl`` operator need a window: test_blender_compile_ahead_window.py.
* Deleting what queued builds read leaves no build pending forever. A deleted material's shader is freed 60 s after
  its last user at the earliest (``gpu_pass.cc``), long after its builds; a shader is freed with builds still queued
  or running only at exit, where every material's is freed before the backend's builder (``GPU_exit``). So the run
  ends with builds pending (Metal: more than the builder has threads), and its exit status is the proof that the
  shaders took their queued builds back and waited for the running ones (``MTLPipelinePrebuilder::forget``).

Run: ``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import gpu
from mathutils import Matrix

assert bpy.app.background and '--factory-startup' in sys.argv   # It changes the scene: a disposable instance only.
gpu.init() if hasattr(gpu, "init") else None
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-compile-ahead"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fresh_shader  # noqa: E402
import rendered_view_fixtures as fx  # noqa: E402
import _bpy  # noqa: E402
from angelo_agent import rail_pictures  # noqa: E402

live = _bpy._angelo_live
report = {"checks": {}, "readings": {}, "binary": bpy.app.binary_path}
METAL = gpu.platform.backend_type_get() == 'METAL'
VIEW = Matrix.Translation((0.0, 0.0, -6.0))
LENS = rail_pictures.perspective(0.9, 1.0, 0.1, 100.0)
WAIT = 60.0
SIZE = 96   # The views' width and height in pixels.
COLOURS = {"Red": (1.0, 0.0, 0.0), "Green": (0.0, 1.0, 0.0), "Blue": (0.0, 0.0, 1.0), "Yellow": (1.0, 1.0, 0.0)}
SKY = (1.0, 0.0, 1.0)


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["readings"][name] = detail


def host():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def stats():
    return live.pipeline_stats()


def state(world=True):
    window, area, _region = host()
    return live.shader_state(window.view_layer.depsgraph, area.spaces.active, world=world)


def ahead(only=None, lit=True, read=False):
    """A compile-ahead draw; with ``read``, what it drew (rows of RGBA): pixels nothing ever uses, read here only to
    show the stand-in it draws while a world compiles."""
    window, area, region = host()
    offscreen = gpu.types.GPUOffScreen(64, 64)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            kwargs = dict({"only": only} if only is not None else {}, **({} if lit else {"lit": False}))
            live.draw(window.scene, offscreen, area.spaces.active, VIEW, LENS, view_layer=window.view_layer,
                      compile_ahead=True, **kwargs)
        return offscreen.texture_color.read().to_list() if read else None
    finally:
        offscreen.free()


def view(size=SIZE):
    """The LIT look's image render (what an agent's view draws), as rows of display-space RGBA."""
    window, area, region = host()
    space = area.spaces.active
    shading = space.shading
    kept = (shading.type, shading.use_scene_lights, shading.use_scene_world, space.overlay.show_overlays)
    shading.type, shading.use_scene_lights, shading.use_scene_world = 'MATERIAL', True, True
    space.overlay.show_overlays = False   # The grid floor and its axes would cross the picture.
    offscreen = gpu.types.GPUOffScreen(size, size)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            offscreen.draw_view3d(window.scene, window.view_layer, space, region, VIEW, LENS,
                                  do_color_management=True)
        return offscreen.texture_color.read().to_list()
    finally:
        offscreen.free()
        shading.type, shading.use_scene_lights, shading.use_scene_world, space.overlay.show_overlays = kept


def render(size=SIZE):
    """An F12 render of the scene through a camera placed as the views are, as rows of display-space RGB."""
    scene = bpy.context.scene
    if scene.camera is None:
        camera = bpy.data.objects.new("Ahead Camera", bpy.data.cameras.new("Ahead Camera"))
        scene.collection.objects.link(camera)
        camera.location, camera.data.angle, scene.camera = (0.0, 0.0, 6.0), 0.9, camera
    settings = scene.render
    settings.engine, settings.resolution_x, settings.resolution_y = 'BLENDER_EEVEE', size, size
    settings.resolution_percentage, settings.film_transparent = 100, False
    settings.image_settings.file_format, settings.filepath = 'PNG', str(OUT / "f12.png")
    bpy.context.view_layer.update()
    bpy.ops.render.render(write_still=True)
    return fx.decode_png(OUT / "f12.png")


def settle(only=None, world=True):
    """Seconds until nothing the scene draws is missing or queued (a missing one queued again by a compile-ahead
    draw, as view_warm does), then until no queued pipeline build is pending."""
    started = time.perf_counter()
    while True:
        found = state(world)
        if found is not None and not found[1] and not found[2]:
            break
        if time.perf_counter() - started > WAIT:
            raise RuntimeError("never compiled: %r" % (found[:3],))
        if found is not None and found[1]:
            ahead(only)
        time.sleep(0.01)
    while stats()["pending"]:
        if time.perf_counter() - started > WAIT:
            raise RuntimeError("pipeline builds never finished")
        time.sleep(0.005)
    return time.perf_counter() - started


def emission(name, colour, role, heavy=False):
    """An emission material of ``colour`` whose code no earlier run compiled: a chain of its own on object
    coordinates, scaled to a ten-thousandth of the strength, so it changes no pixel. ``heavy``: an image texture and
    a chain long enough that Blender also compiles an optimized variant of it, about 10 s later on Metal
    (``should_optimize_heuristic``: a texture, 8 uniforms and 60 nodes)."""
    made = bpy.data.materials.new(name)
    tree = made.node_tree
    tree.nodes.remove(tree.nodes["Principled BSDF"])
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (*colour, 1.0)
    coords = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(coords.outputs["Object"], separate.inputs[0])
    start = separate.outputs[0]
    if heavy:
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.new(name + " texture", 8, 8)
        tree.links.new(coords.outputs["UV"], texture.inputs["Vector"])
        mix = tree.nodes.new("ShaderNodeMath")
        mix.operation = 'ADD'
        tree.links.new(separate.outputs[0], mix.inputs[0])
        tree.links.new(texture.outputs["Alpha"], mix.inputs[1])
        start = mix.outputs[0]
    last = fresh_shader.chain(tree, start, 70 if heavy else 12, "compile_ahead:" + role)
    tiny = tree.nodes.new("ShaderNodeMath")
    tiny.operation, tiny.inputs[1].default_value = 'MULTIPLY_ADD', 0.0001
    tiny.inputs[2].default_value = 1.0
    tree.links.new(last, tiny.inputs[0])
    tree.links.new(tiny.outputs[0], emit.inputs["Strength"])
    tree.links.new(emit.outputs[0], tree.nodes["Material Output"].inputs["Surface"])
    return made


def world(colour, role):
    made = bpy.data.worlds.new("Ahead Sky")
    made.use_nodes = True
    tree = made.node_tree
    background = tree.nodes["Background"]
    background.inputs["Color"].default_value = (*colour, 1.0)
    coords = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(coords.outputs["Generated"], separate.inputs[0])
    last = fresh_shader.chain(tree, separate.outputs[0], 12, "compile_ahead_world:" + role)
    tiny = tree.nodes.new("ShaderNodeMath")
    tiny.operation, tiny.inputs[1].default_value = 'MULTIPLY_ADD', 0.0001
    tiny.inputs[2].default_value = 1.0
    tree.links.new(last, tiny.inputs[0])
    tree.links.new(tiny.outputs[0], background.inputs["Strength"])
    return made


def quad_grid(name):
    """Four faces side by side across the view, a material slot each."""
    mesh = bpy.data.meshes.new(name)
    verts, faces = [], []
    for i in range(4):
        x = -2.0 + i
        base = len(verts)
        verts += [(x + 0.05, -0.6, 0.0), (x + 0.95, -0.6, 0.0), (x + 0.95, 0.6, 0.0), (x + 0.05, 0.6, 0.0)]
        faces.append((base, base + 1, base + 2, base + 3))
    mesh.from_pydata(verts, [], faces)
    for index, polygon in enumerate(mesh.polygons):
        polygon.material_index = index
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def shows(pixel, colour):
    """Whether a display-space pixel (bytes) shows ``colour``'s hue: every channel that colour has well up, every
    other well down. A placeholder material and the default world are grey: neither passes for any of them."""
    return all((pixel[i] / 255.0 > 0.6) if colour[i] else (pixel[i] / 255.0 < 0.45) for i in range(3))


# Where each part's face and the background show in a SIZE view: the four faces lie across its middle row.
PARTS = {name: (SIZE // 2, int(SIZE * (0.24 + 0.17 * index))) for index, name in enumerate(COLOURS)}
# Beside, above and below the faces: only the world shows there.
BACKGROUND = ((SIZE // 2, 2), (SIZE // 2, SIZE - 3)) + tuple((y, x) for y in (2, SIZE - 3)
                                                            for x in (SIZE // 8, SIZE // 2, SIZE - SIZE // 8))


def parts_shown(rows):
    return {name: shows(rows[y][x], COLOURS[name]) for name, (y, x) in PARTS.items()}


def delta(before, after):
    return {key: round(after[key] - before[key], 2) for key in before}


def run():
    reads = hasattr(live, "pipeline_stats")
    check("this build reads pipeline statistics", reads and
          {"built_in_draw", "built_in_background", "pending", "failed", "shaders_waited"} <= set(stats()),
          stats() if reads else None)
    if not reads:   # A build without compile-ahead builds: every draw below would build and compile on the main thread.
        return
    scene = bpy.context.scene
    scene.view_settings.view_transform = 'Standard'
    scene.eevee.taa_render_samples = scene.eevee.taa_samples = 1
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene.world = None
    bpy.context.view_layer.update()
    # The session's own first look (engine shaders, its colour management), untimed, so what follows is new.
    ahead()
    settle()
    view()

    obj = quad_grid("Ahead Parts")
    for name, colour in COLOURS.items():
        obj.data.materials.append(emission("Ahead " + name, colour, name))
    bpy.context.view_layer.update()
    ahead([obj])   # Queues their compiles; then they compile in the background.
    settle([obj])
    before = stats()
    ahead([obj])   # Their first draw.
    first = delta(before, stats())
    if METAL:
        check("their first draw builds no pipeline state in the draw (Metal)", first["built_in_draw"] == 0, first)
        check("it queues them instead", first["pending"] >= 1, first)
    else:
        check("other backends never queue (they build as before)", first["pending"] == 0, first)
    waited = settle([obj])
    built = delta(before, stats())
    check("queued builds finish, none failed", stats()["pending"] == 0 and built["failed"] == 0,
          dict(built, waited_s=round(waited, 3)))
    before = stats()
    rows = view()
    drawn = delta(before, stats())
    if METAL:
        check("the view after them builds no pipeline state of its own (Metal)", drawn["built_in_draw"] == 0, drawn)
    shown = parts_shown(rows)
    check("the view shows every material in its own colour (none missing or a placeholder)", all(shown.values()),
          dict(shown, pixels=[rows[y][x] for y, x in PARTS.values()]))

    sky = world(SKY, "first")
    scene.world = sky
    bpy.context.view_layer.update()
    found = state()
    check("a new world reads missing for a LIT look", found[1] >= 1 and found[9] == sky.session_uid,
          list(found[:3]) + [found[9]])
    check("a MATERIAL look never counts the scene's world", not state(world=False)[1], list(state(world=False)[:3]))
    before = stats()
    started = time.perf_counter()
    ahead([])   # A draw of no object: the world alone.
    world_ms = (time.perf_counter() - started) * 1000
    asked = delta(before, stats())
    found = state()
    # Counted, never timed: where the draw itself is slow (a software renderer) the background compile can end
    # before the draw returns, so the world may read compiled already; the draw waited for no shader either way.
    check("a compile-ahead draw compiles none of a new world's shaders: it asks for them and waits for none",
          asked["shaders_waited"] == 0 and not found[1],
          dict(state=list(found[:3]), draw_ms=round(world_ms, 1), shaders_waited=asked["shaders_waited"]))
    settle([])
    before = stats()
    ahead([])   # Its first draw, once compiled.
    first = delta(before, stats())
    if METAL:
        check("the world's first draw builds no pipeline state in the draw (Metal)", first["built_in_draw"] == 0,
              first)
    settle([])
    before = stats()
    rows = view()
    drawn = delta(before, stats())
    if METAL:
        check("the view of the new world builds no pipeline state of its own (Metal)", drawn["built_in_draw"] == 0,
              drawn)
    sky_shown = [shows(rows[y][x], SKY) for y, x in BACKGROUND]
    check("the view shows the new world, never the default one", all(sky_shown),
          dict(shown=sky_shown, pixels=[rows[y][x] for y, x in BACKGROUND]))
    shown = parts_shown(rows)
    check("and still every material", all(shown.values()), dict(shown, pixels=[rows[y][x] for y, x in PARTS.values()]))
    # What that preparation avoids: a view of a world nothing prepared compiles it in its own draw (EEVEE waits for a
    # world's shaders in every draw but a compile-ahead one), so the count above can tell the two apart.
    scene.world = world((0.0, 1.0, 1.0), "unprepared")
    bpy.context.view_layer.update()
    before = stats()
    view()
    unprepared = delta(before, stats())
    check("a view of a world nothing prepared waits for its shaders in its own draw", unprepared["shaders_waited"] >= 1,
          unprepared)
    # A draw that meets a world a compile-ahead draw queued, still queued, waits for it: the stand-in (EEVEE's
    # default world) is the compile-ahead draw's alone. Where the compile ended before the draw (a software
    # renderer's own draw outlasts it) there is nothing to wait for; the picture is the world's either way.
    for name, draw in (("a view", view), ("an F12 render", render)):
        scene.world = world(SKY, "met queued by " + name)
        bpy.context.view_layer.update()
        stood_in = ahead([], read=True)
        corners = [stood_in[y][x] for y, x in ((1, 1), (1, 62), (62, 1), (62, 62))]
        check("the compile-ahead draw before " + name + " drew a stand-in, never the new world (the control)",
              not any(shows(pixel, SKY) for pixel in corners), [list(pixel) for pixel in corners])
        queued = bool(state()[2])
        before = stats()
        rows = draw()
        met = delta(before, stats())
        sky_shown = [shows(rows[y][x], SKY) for y, x in BACKGROUND]
        check(name + " that meets a world still queued waits for it and shows it, never the default world",
              all(sky_shown) and (met["shaders_waited"] >= 1 or not queued),
              dict(queued_when_drawn=queued, shaders_waited=met["shaders_waited"], shown=sky_shown,
                   pixels=[list(rows[y][x]) for y, x in BACKGROUND]))

    # Blender's optimized variant of a heavy material (Metal, about 10 s after it compiled) is another shader: drawn
    # as it arrives, it built every pipeline state in that draw (break-it C5 F2). Views keep being drawn while it
    # arrives; none builds a state in its draw, and its states are built in the background before it is used.
    scene.world = None
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    rich = quad_grid("Ahead Rich")
    for name, colour in COLOURS.items():
        rich.data.materials.append(emission("Rich " + name, colour, "rich " + name, heavy=True))
    bpy.context.view_layer.update()
    ahead([rich])
    settle([rich], world=False)
    ahead([rich])
    settle([rich], world=False)
    start, in_draw, warmed, rows = stats(), 0, False, None
    began = time.perf_counter()
    while time.perf_counter() - began < 25.0:
        state()   # In the background no window loop finishes compiles: this does (GPU_pass_cache_update).
        before = stats()
        rows = view()
        after = stats()
        in_draw += after["built_in_draw"] - before["built_in_draw"]
        warmed |= after["built_in_background"] > start["built_in_background"] or after["pending"] > 0
        time.sleep(0.25)
    settle([rich], world=False)
    before = stats()
    rows = view()
    in_draw += stats()["built_in_draw"] - before["built_in_draw"]
    if METAL:
        check("views while the optimized variants arrive build no pipeline state in their draw (Metal)", in_draw == 0,
              dict(in_draw=in_draw, stats=stats()))
        check("the optimized variants' states are built in the background (Metal)", warmed, stats())
    shown = parts_shown(rows)
    check("and the view still shows every material in its own colour", all(shown.values()),
          dict(shown, pixels=[rows[y][x] for y, x in PARTS.values()]))

    # What queued builds read, deleted while they may still be queued; then a quit with builds pending.
    doomed = quad_grid("Ahead Doomed")
    for name, colour in COLOURS.items():
        doomed.data.materials.append(emission("Doomed " + name, colour, "doomed " + name))
    bpy.context.view_layer.update()
    ahead([doomed])
    settle([doomed])
    ahead([doomed])
    for material in list(doomed.data.materials):
        bpy.data.materials.remove(material)
    bpy.data.objects.remove(doomed)
    bpy.context.view_layer.update()
    started = time.perf_counter()
    while stats()["pending"] and time.perf_counter() - started < WAIT:
        time.sleep(0.005)
    check("builds of deleted materials end (built or dropped), never pending forever", stats()["pending"] == 0,
          stats())
    # The quit with builds pending (the module's text): two objects' first draws, more builds than the builder has
    # threads, so some are still queued and some running when every shader is freed.
    last = [quad_grid("Ahead Last %d" % n) for n in range(2)]
    for n, obj in enumerate(last):
        for name, colour in COLOURS.items():
            obj.data.materials.append(emission("Last %d %s" % (n, name), colour, "last %d %s" % (n, name)))
    bpy.context.view_layer.update()
    ahead(last)
    settle(last)
    ahead(last)
    pending = stats()["pending"]
    report["readings"]["pending_at_quit"] = pending
    if METAL:
        check("the run ends with builds still pending (its exit status says the quit was clean)", pending > 4,
              stats())


try:
    run()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
except Exception:   # noqa: BLE001 - recorded, the run fails.
    report["error"] = traceback.format_exc()
    report["success"] = False
(OUT / "compile-ahead.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_COMPILE_AHEAD", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
