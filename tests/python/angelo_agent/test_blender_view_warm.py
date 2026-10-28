# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The native half of ``view_warm`` in a real Angelo (background, offscreen GPU).

* ``_angelo_live.shader_state`` counts the materials EEVEE asks for, and only those (REVIEW-VIEW-WARM-2 H-1: a mesh
  displayed as Bounds, a point cloud's second slot and every slot under a view-layer material override were counted
  missing forever, so every view waited): a new material on a Solid mesh is missing, then queued by a compile-ahead
  draw; the others never count.
* A compile-ahead draw defers: a new heavy shader is queued right after it, and the draw (MATERIAL's look, no scene
  world) costs a fraction of a synchronous image render of an equally new one (BREAKIT-viewwarm VW-M-3), both sides
  warmed alike first.
* A value edit and a copy of a compiled material read ready once drawn (REVIEW-VIEW-SPREAD H3: the kept result went
  stale, since a GPU material made from a pass Blender already compiled changes no count the cache was keyed on).
* ``_angelo_live.draw(..., compile_ahead=True, only=[...])`` hides the other objects from that one draw and puts
  them back: a Solid picture of the whole scene and every object's visibility read the same before and after, after a
  draw that raised (an object that is no object), and after the scene was evaluated again.

Run: ``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-warm"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import _bpy  # noqa: E402
from angelo_agent import rail_pictures  # noqa: E402

live = _bpy._angelo_live
report = {"checks": {}, "readings": {}, "binary": bpy.app.binary_path}
VIEW = Matrix.Translation((0.0, 0.0, -12.0))
LENS = rail_pictures.perspective(0.9, 1.0, 0.1, 100.0)


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["readings"][name] = detail


def host():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def material(name):
    made = bpy.data.materials.new(name)
    noise = made.node_tree.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0 + len(name)
    made.node_tree.links.new(noise.outputs["Color"], made.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    return made


RUN = int(time.time() * 1000) % 9973   # Each run's shaders its own: the system's shader cache never holds them.


def heavy(name, salt):
    """A material of its own long math chain: a shader that takes Blender a while to compile, new in every run."""
    salt += RUN
    made = bpy.data.materials.new(name)
    tree = made.node_tree
    coords, separate = tree.nodes.new("ShaderNodeTexCoord"), tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(coords.outputs["Object"], separate.inputs[0])
    last = separate.outputs[0]
    for k in range(60 + salt % 17):
        math = tree.nodes.new("ShaderNodeMath")
        math.operation = ("ADD", "MULTIPLY", "SINE", "POWER", "MINIMUM", "MAXIMUM")[(k + salt) % 6]
        math.inputs[1].default_value = 0.37 + 0.01 * ((k * 7 + salt) % 13)
        tree.links.new(last, math.inputs[0])
        last = math.outputs[0]
    tree.links.new(last, tree.nodes["Principled BSDF"].inputs["Roughness"])
    return made


def image_render_ms():
    """A synchronous MATERIAL image render of the scene (what a view without the wait does), in ms."""
    window, area, region = host()
    space = area.spaces.active
    old, offscreen = space.shading.type, gpu.types.GPUOffScreen(64, 64)
    space.shading.type = 'MATERIAL'
    started = time.perf_counter()
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            offscreen.draw_view3d(window.scene, window.view_layer, space, region, VIEW, LENS, do_color_management=True)
    finally:
        space.shading.type = old
        offscreen.free()
    return (time.perf_counter() - started) * 1000


def cube(name, location, display='SOLID'):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    obj = bpy.data.objects.new(name, mesh)
    obj.location, obj.display_type = location, display
    bpy.context.scene.collection.objects.link(obj)
    return obj


def state():
    window, area, _region = host()
    return live.shader_state(window.view_layer.depsgraph, area.spaces.active)


def ahead(only=None, lit=True):
    window, area, region = host()
    offscreen = gpu.types.GPUOffScreen(64, 64)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            kwargs = dict({"only": only} if only is not None else {}, **({} if lit else {"lit": False}))
            live.draw(window.scene, offscreen, area.spaces.active, VIEW, LENS, view_layer=window.view_layer,
                      compile_ahead=True, **kwargs)
    finally:
        offscreen.free()


def solid_picture():
    """How many pixels of a Solid picture of the whole scene show geometry (no background)."""
    window, area, region = host()
    offscreen = gpu.types.GPUOffScreen(128, 128)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            live.draw(window.scene, offscreen, area.spaces.active, VIEW, LENS, view_layer=window.view_layer,
                      background=False)
        pixels = offscreen.texture_color.read()
        return sum(1 for row in pixels.to_list() for pixel in row if pixel[3] > 0)
    finally:
        offscreen.free()


def visibility():
    layer = bpy.context.view_layer
    return {obj.name: (obj.visible_get(view_layer=layer), obj.hide_get(view_layer=layer))
            for obj in bpy.context.scene.objects}


def run():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    solid = cube("Warm Solid", (-3.0, 0.0, 0.0))
    solid.data.materials.append(material("Warm Asked"))
    bounds = cube("Warm Bounds", (3.0, 0.0, 0.0), display='BOUNDS')
    bounds.data.materials.append(material("Warm Never Asked"))
    points = bpy.data.pointclouds.new("Warm Points") if hasattr(bpy.data, "pointclouds") else None
    if points is not None:
        cloud = bpy.data.objects.new("Warm Cloud", points)
        bpy.context.scene.collection.objects.link(cloud)
        points.materials.append(material("Warm Cloud First"))
        points.materials.append(material("Warm Cloud Second"))
    bpy.context.view_layer.update()
    found = state()
    names = set(found[7])   # Its readable names; materials are named by session UID (VW9-H-1a).
    report["readings"]["state_before"] = list(found[:3]) + [sorted(names)]
    check("a new material on a Solid mesh is missing", "Warm Asked" in names)
    check("the missing ones are named apart (what a queue draw draws)",
          len(found) > 7 and set(found[4]) <= set(found[3]) and len(found[4]) == found[1], list(found))
    check("a Bounds mesh's material is never counted", "Warm Never Asked" not in names)
    if points is not None:
        check("a point cloud's first slot is counted, its second never",
              "Warm Cloud First" in names and "Warm Cloud Second" not in names)
    started, draws = time.perf_counter(), 0
    while True:   # As view_warm does: a cold EEVEE asks for nothing until its own shaders are ready; queue again.
        ahead()
        draws += 1
        found = state()
        if not found[1] or time.perf_counter() - started > 60:
            break
        time.sleep(0.25)
    report["readings"]["state_after_queue"] = list(found[:3]) + [sorted(found[7]), draws]
    check("a compile-ahead draw asks for every one EEVEE draws: none missing", found[1] == 0, list(found[:3]))
    started = time.perf_counter()
    while state()[2] and time.perf_counter() - started < 60:
        time.sleep(0.02)
    check("queued ones compile in the background", state()[2] == 0, list(state()[:3]))

    # REVIEW-VIEW-SPREAD H3: a value edit or a copy gets its GPU material from a pass Blender already compiled, which
    # changes neither the depsgraph's evaluation count (after the edit's own) nor any compile count; the state read
    # after the draw must say so, or every view after an everyday edit waits out the stall.
    edited = bpy.data.materials["Warm Asked"]
    edited.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.123
    copy = edited.copy()
    copy.name = "Warm Copy"
    twin = cube("Warm Twin", (-3.0, 3.0, 0.0))
    twin.data.materials.append(copy)
    bpy.context.view_layer.update()
    found = state()
    check("a value edit and a copy are missing until drawn", {"Warm Asked", "Warm Copy"} <= set(found[7]),
          list(found[:3]) + [sorted(found[7])])
    started = time.perf_counter()
    while True:
        ahead()
        found = state()
        if not (found[1] or found[2]) or time.perf_counter() - started > 30:
            break
        time.sleep(0.05)
    check("after a draw they read ready, never stale", not (found[1] or found[2]),
          list(found[:3]) + [sorted(found[7]), round(time.perf_counter() - started, 2)])

    # BREAKIT-viewwarm VW-M-3: the compile is really deferred. Right after a compile-ahead draw a new heavy shader is
    # queued, not compiled, and the draw costs a fraction of a synchronous image render of an equally new one.
    # Both sides are warmed alike first, untimed, on the scene as it is (nothing new in it): the first draw of
    # MATERIAL's look in a session sets that look up (its studio light and world), which only the side timed first
    # would otherwise pay (C3's T4 run: 498 ms against 1404, every compile-ahead draw before it LIT).
    started = time.perf_counter()
    ahead(lit=False)
    warm = {"compile_ahead_ms": round((time.perf_counter() - started) * 1000, 1),
            "image_render_ms": round(image_render_ms(), 1)}
    started = time.perf_counter()
    while (found := state())[1] or found[2]:
        if time.perf_counter() - started > 60:
            break
        time.sleep(0.02)
    check("warmed alike, nothing new is left to compile before either side is timed", not (found[1] or found[2]),
          dict(warm, state=list(found[:3])))
    first = cube("Warm Heavy A", (0.0, -3.0, 0.0))
    first.data.materials.append(heavy("Warm Heavy A", 1))
    bpy.context.view_layer.update()
    started = time.perf_counter()
    ahead(lit=False)
    ahead_ms = (time.perf_counter() - started) * 1000
    found = state()
    check("a compile-ahead draw defers: the new shader is queued right after it", found[2] >= 1 and found[1] == 0,
          list(found[:3]) + [round(ahead_ms, 1)])
    second = cube("Warm Heavy B", (0.0, 3.0, 0.0))
    second.data.materials.append(heavy("Warm Heavy B", 4))
    bpy.context.view_layer.update()
    sync_ms = image_render_ms()
    check("it costs a fraction of a synchronous render of an equally new shader", ahead_ms * 3 < sync_ms,
          {"compile_ahead_ms": round(ahead_ms, 1), "image_render_ms": round(sync_ms, 1)})
    check("the state also names failed compiles (none here)", len(found) > 5 and list(found[5]) == [], list(found))
    started = time.perf_counter()
    while state()[2] and time.perf_counter() - started < 60:
        time.sleep(0.02)

    # VW9-H-1a: a linked library's material and a local one of the same name are two materials, never one.
    library = OUT / "view-warm-library.blend"
    shared = material("Warm Same Name")
    bpy.data.libraries.write(str(library), {shared})
    bpy.data.materials.remove(shared)
    with bpy.data.libraries.load(str(library), link=True) as (source, target):
        target.materials = ["Warm Same Name"]
    linked = target.materials[0]
    local = material("Warm Same Name")
    for index, made in enumerate((linked, local)):
        cube("Warm Twin %d" % index, (6.0, 3.0 * index, 0.0)).data.materials.append(made)
    bpy.context.view_layer.update()
    found = state()
    pending = {uid for uid, name in zip(found[3], found[7]) if name == "Warm Same Name"}
    check("two materials of one name are two, by session UID", len(pending) == 2 and
          {linked.session_uid, local.session_uid} == pending, list(found))
    started = time.perf_counter()
    while True:
        ahead()
        found = state()
        if not (found[1] or found[2]) or time.perf_counter() - started > 30:
            break
        time.sleep(0.05)

    override = material("Warm Override")
    bpy.context.view_layer.material_override = override
    bpy.context.view_layer.update()
    found = state()
    check("under a material override only the override is counted",
          set(found[7]) <= {"Warm Override"} and found[0] == 1, list(found[:3]) + [sorted(found[7])])
    bpy.context.view_layer.material_override = None
    bpy.context.view_layer.update()

    before, seen = visibility(), solid_picture()
    ahead(only=[solid])
    check("draw(only=) leaves every object's visibility", visibility() == before)
    check("draw(only=) leaves the whole scene drawn", solid_picture() == seen, seen)
    try:
        ahead(only=[solid, "not an object"])
        check("an object that is no object is refused", False)
    except TypeError:
        check("an object that is no object is refused", True)
    check("a refused draw leaves the scene drawn", solid_picture() == seen and visibility() == before)
    ahead(only=[bounds])
    solid.location.x -= 0.5
    bpy.context.view_layer.update()   # Evaluated again after a draw that hid objects.
    after = solid_picture()
    check("evaluated again, every object still drawn", after > 0 and visibility() == before, after)


try:
    run()
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
except Exception:   # noqa: BLE001 - recorded, the run fails.
    report["error"] = traceback.format_exc()
    report["success"] = False
(OUT / "view-warm.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_VIEW_WARM", json.dumps({"success": report["success"]}), flush=True)
if not report["success"]:
    sys.exit(1)
