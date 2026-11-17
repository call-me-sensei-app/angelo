# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No draw but the compile-ahead draw itself stands in for a world it queued (break-it C5 on 5.3), in a window.

A compile-ahead draw queues a new LIT world's shaders to the background and draws EEVEE's default world meanwhile:
its pixels are never used (``eevee_world.cc``, ``GPU_compile_ahead_begin``). Every other draw that meets that world
while it is still queued waits for it, as it would have compiled it. Here, for the person's viewport (a window
redraw), the ``render.opengl`` operator and an F12 render, each with a world of its own code (``fresh_shader``) and
colour: the compile-ahead draw, then at once the draw.

* Where the world still read queued when the draw was asked, the draw waited for a shader (``pipeline_stats``:
  ``shaders_waited``, a count), and the world reads compiled after it.
* Its picture shows that world's colour, never the default world's grey: the viewport's in a screenshot of the
  window, read where its 3D view is; the other two in the file they write.

Run by ``run_native_suites.py --gui --include test_blender_compile_ahead_window``. No provider, account or network.
It needs a window ``screen.screenshot`` can capture (the runner's ``window_capture`` input, ``native_suite_inputs``:
skipped with that reason where it cannot); started by hand where it cannot, it fails saying so.
Inspect compile-ahead-window.json; a zero exit status alone is not proof.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import os
from pathlib import Path
import sys
import tempfile

import bpy
import gpu
from mathutils import Matrix

assert "--factory-startup" in sys.argv and not bpy.app.background, "Use an isolated factory-startup window"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-compile-ahead-window"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fresh_shader  # noqa: E402
import rendered_view_fixtures as fx  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
import _bpy  # noqa: E402
from angelo_agent import rail_pictures  # noqa: E402

live = _bpy._angelo_live
report = {"checks": {}, "readings": {}, "binary": bpy.app.binary_path, "success": False,
          # What the runner found of this window before it started the suite (its window_capture input).
          "window_capture": os.environ.get("ANGELO_QA_WINDOW_CAPTURE", "not asked: started by hand")}
VIEW = Matrix.Translation((0.0, 0.0, -6.0))
LENS = rail_pictures.perspective(0.9, 1.0, 0.1, 100.0)
SIZE = 128   # The written pictures' width and height in pixels.
# Where only the world shows: the scene holds a camera and nothing else. Three corners of a picture, clear of the
# 3D view's header and toolbar and of anything shown over its middle (the splash) in the screenshot.
SKY = ((0.15, 0.70, 0.30, 0.90), (0.70, 0.70, 0.90, 0.90), (0.70, 0.15, 0.90, 0.30))


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["readings"][name] = detail


def host():
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    return window, area, next(region for region in area.regions if region.type == "WINDOW")


def state():
    window, area, _region = host()
    return live.shader_state(window.view_layer.depsgraph, area.spaces.active, world=True)


def ahead():
    """A compile-ahead LIT draw of no object: the world alone (as ``view_warm`` queues a missing one)."""
    window, area, region = host()
    offscreen = gpu.types.GPUOffScreen(64, 64)
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            live.draw(window.scene, offscreen, area.spaces.active, VIEW, LENS, view_layer=window.view_layer,
                      compile_ahead=True, only=[])
    finally:
        offscreen.free()


def new_world(colour, role):
    made = bpy.data.worlds.new("Window Sky " + role)
    made.use_nodes = True
    made.color = (0.0, 0.0, 0.0)   # What a viewport that has drawn nothing shows of a world: never its colour here.
    tree = made.node_tree
    background = tree.nodes["Background"]
    background.inputs["Color"].default_value = (*colour, 1.0)
    coords = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    tree.links.new(coords.outputs["Generated"], separate.inputs[0])
    last = fresh_shader.chain(tree, separate.outputs[0], 12, "compile_ahead_window:" + role)
    tiny = tree.nodes.new("ShaderNodeMath")   # A ten-thousandth of the chain on the strength: it changes no pixel.
    tiny.operation, tiny.inputs[1].default_value, tiny.inputs[2].default_value = 'MULTIPLY_ADD', 0.0001, 1.0
    tree.links.new(last, tiny.inputs[0])
    tree.links.new(tiny.outputs[0], background.inputs["Strength"])
    return made


def ready():
    found = state()
    return found is not None and not found[1] and not found[2]


def is_green(colour):
    return colour[1] > colour[0] + fx.DOMINANCE and colour[1] > colour[2] + fx.DOMINANCE


def viewport_shows(shows):
    """Whether the window, drawn again now, shows ``shows`` where the 3D view's sky is."""
    path, sky = viewport()
    rows = fx.decode_png(path)
    if not any(fx.mean(rows, (0.0, 0.0, 1.0, 1.0))):   # Not one lit pixel, the editors' own included.
        raise RuntimeError("This window cannot be captured (screen.screenshot wrote a black picture, as a software "
                           "OpenGL window on a virtual display does): its viewport's picture cannot be judged here")
    return all(shows(fx.mean(rows, region)) for region in sky)


def viewport():
    """The person's viewport: one redraw of the window, then the window as it shows, and where in that picture the
    3D view's ``SKY`` is (fractions of the window, top-left origin; an area is placed from the bottom left)."""
    path = OUT / "viewport.png"
    window, _area, region = host()
    with fx.in_view3d():
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        bpy.ops.screen.screenshot(filepath=str(path), check_existing=False)

    def across(fraction):
        return (region.x + fraction * region.width) / window.width

    def down(fraction):
        return 1.0 - (region.y + (1.0 - fraction) * region.height) / window.height
    return path, tuple((across(x0), down(y0), across(x1), down(y1)) for x0, y0, x1, y1 in SKY)


def opengl():
    scene = bpy.context.window_manager.windows[0].scene
    scene.render.filepath = str(OUT / "opengl.png")
    with fx.in_view3d():
        bpy.ops.render.opengl(view_context=True, write_still=True)
    return OUT / "opengl.png", SKY


def f12():
    scene = bpy.context.window_manager.windows[0].scene
    scene.render.filepath = str(OUT / "f12.png")
    with fx.in_view3d():
        bpy.ops.render.render(write_still=True)
    return OUT / "f12.png", SKY


CASES = (("the viewport", (1.0, 0.0, 0.0), fx.is_red, viewport),
         ("render.opengl", (0.0, 0.0, 1.0), fx.is_blue, opengl),
         ("an F12 render", (1.0, 0.0, 1.0), fx.is_magenta, f12))


def scenario():
    report["phase"] = "setup"
    window, area, _region = host()
    scene, space = window.scene, area.spaces.active
    stats = live.pipeline_stats() if hasattr(live, "pipeline_stats") else {}
    check("this build counts the shaders a draw waits for", "shaders_waited" in stats, stats)
    if "shaders_waited" not in stats:
        return
    for obj in [obj for obj in scene.objects if obj.type != 'CAMERA']:
        bpy.data.objects.remove(obj)
    settings = scene.render
    settings.engine, settings.resolution_x, settings.resolution_y = 'BLENDER_EEVEE', SIZE, SIZE
    settings.resolution_percentage, settings.film_transparent = 100, False
    settings.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'Standard'
    scene.eevee.taa_render_samples = scene.eevee.taa_samples = 1
    space.shading.type, space.shading.use_scene_lights, space.shading.use_scene_world = 'MATERIAL', True, True
    space.overlay.show_overlays = space.show_gizmo = False
    # The session's own first look, so what follows is new: the engine's shaders and a first world, prepared as a
    # view prepares one. The viewport's own EEVEE loads its shaders over several redraws and draws nothing until
    # then (the window shows the world's display colour meanwhile): waited for, until it shows that first world.
    scene.world = new_world((0.0, 1.0, 0.0), "the first look")
    window.view_layer.update()
    ahead()
    yield from fx.wait_for(ready, what="the first look's shaders")
    ahead()
    yield from fx.wait_for(lambda: viewport_shows(is_green), what="the viewport's first picture of a world")
    for name, colour, shows, draw in CASES:
        report["phase"] = name
        scene.world = new_world(colour, name)
        window.view_layer.update()
        ahead()
        queued = bool(state()[2])
        before = live.pipeline_stats()["shaders_waited"]
        path, sky = draw()
        waited = live.pipeline_stats()["shaders_waited"] - before
        after = state()
        check(name + " waits for a world still queued instead of standing in for it",
              (waited >= 1 or not queued) and not after[1] and not after[2],
              dict(queued_when_drawn=queued, shaders_waited=waited, after=list(after[:3])))
        rows = fx.decode_png(path)
        colours = [fx.mean(rows, region) for region in sky]
        check(name + " shows the world, never the default one", all(shows(colour) for colour in colours),
              dict(size=list(fx.png_size(path)), colours=[[round(value, 1) for value in colour] for colour in colours]))
        yield


fixture_boot.drive(scenario, report, OUT / "compile-ahead-window.json", "ANGELO_COMPILE_AHEAD_WINDOW")
