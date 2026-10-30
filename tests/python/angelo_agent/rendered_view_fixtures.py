# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenes and pixel checks for test_blender_rendered_view.

Blender-only helpers (they import ``bpy``); no provider, account or network.
Each scene makes one thing a render shows and a viewport observation may not:
the world's colour, a scene light's colour, a world volume. The colours are
saturated primaries so a region's mean colour says which of them reached it.
"""

import struct
import zlib
from pathlib import Path

import bpy

RED, BLUE, MAGENTA = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0)
# How far the named channels must exceed the others (0-255) to call a region that colour.
DOMINANCE = 60


def decode_png(path):
    """Rows of (r, g, b) tuples from an 8-bit RGB or RGBA PNG, any filter (no Blender Image is created)."""
    data, offset, body, header = Path(path).read_bytes(), 8, b"", None
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind, chunk = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            body += chunk
        offset += 12 + length
    width, height, depth, colour, _compression, _filter, interlace = header
    assert depth == 8 and colour in (2, 6) and interlace == 0, "expected 8-bit RGB(A), not interlaced"
    channels = 3 if colour == 2 else 4
    stride = width * channels
    raw = zlib.decompress(body)
    rows, previous = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for x in range(stride):
            left = line[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if kind == 1:
                line[x] = (line[x] + left) & 255
            elif kind == 2:
                line[x] = (line[x] + up) & 255
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 255
            elif kind == 4:
                estimate = left + up - corner
                a, b, c = abs(estimate - left), abs(estimate - up), abs(estimate - corner)
                line[x] = (line[x] + (left if a <= b and a <= c else up if b <= c else corner)) & 255
        rows.append([tuple(line[x:x + 3]) for x in range(0, stride, channels)])
        previous = line
    return rows


def png_size(path):
    data = Path(path).read_bytes()[:24]
    return struct.unpack(">II", data[16:24])


def mean(rows, region):
    """Mean (r, g, b) of ``region``: (x0, y0, x1, y1) as fractions of the image, top-left origin."""
    height, width = len(rows), len(rows[0])
    x0, y0, x1, y1 = region
    picked = [pixel for row in rows[int(y0 * height):max(int(y0 * height) + 1, int(y1 * height))]
              for pixel in row[int(x0 * width):max(int(x0 * width) + 1, int(x1 * width))]]
    return tuple(sum(pixel[channel] for pixel in picked) / len(picked) for channel in range(3))


def is_red(colour):
    return colour[0] > colour[1] + DOMINANCE and colour[0] > colour[2] + DOMINANCE


def is_blue(colour):
    return colour[2] > colour[0] + DOMINANCE and colour[2] > colour[1] + DOMINANCE


def is_magenta(colour):
    return min(colour[0], colour[2]) > colour[1] + DOMINANCE


def _world(scene, surface, volume=None):
    world = bpy.data.worlds.new("Lit World")
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (*surface, 1.0)
    background.inputs["Strength"].default_value = 1.0
    links.new(background.outputs["Background"], output.inputs["Surface"])
    if volume is not None:
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (*volume, 1.0)
        emission.inputs["Strength"].default_value = 0.08
        links.new(emission.outputs["Emission"], output.inputs["Volume"])
    scene.world = world
    return world


def _plane(scene):
    """A white diffuse floor that fills the default camera's frame."""
    mesh = bpy.data.meshes.new("Floor")
    mesh.from_pydata([(-30, -30, 0), (30, -30, 0), (30, 30, 0), (-30, 30, 0)], [], [(0, 1, 2, 3)])
    floor = bpy.data.objects.new("Floor", mesh)
    floor.location = (0, 0, -1)
    material = bpy.data.materials.new("Floor White")
    material.use_nodes = True
    principled = material.node_tree.nodes["Principled BSDF"]
    principled.inputs["Base Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    principled.inputs["Roughness"].default_value = 1.0
    mesh.materials.append(material)
    scene.collection.objects.link(floor)
    return floor


def _remove(names):
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)


def reset(scene, engine):
    """The factory scene's Cube and Camera with a small render size; lights, floors and worlds of earlier cases removed."""
    _remove(("Light", "Floor", "Blue Sun", "Haze"))
    scene.render.engine = engine
    scene.render.resolution_x, scene.render.resolution_y = 320, 180
    scene.render.resolution_percentage = 100
    if engine == "CYCLES":
        scene.cycles.device = "CPU"
        scene.cycles.samples = 8
        scene.cycles.use_adaptive_sampling = False
    else:
        scene.eevee.taa_render_samples = 8
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0


def red_world(scene, engine):
    """Only the world lights the scene, and it is red: the background behind the Cube is the world."""
    reset(scene, engine)
    _world(scene, RED)
    return {"region": (0.0, 0.0, 0.2, 0.2), "is": is_red, "what": "the red world behind the Cube"}


def blue_sun(scene, engine):
    """A black world and one blue sun: the white floor is lit by the scene's own light only."""
    reset(scene, engine)
    _world(scene, (0.0, 0.0, 0.0))
    _plane(scene)
    light = bpy.data.lights.new("Blue Sun", "SUN")
    light.color, light.energy = BLUE, 6.0
    sun = bpy.data.objects.new("Blue Sun", light)
    sun.rotation_euler = (0.3, 0.2, 0.0)
    scene.collection.objects.link(sun)
    return {"region": (0.0, 0.7, 0.3, 1.0), "is": is_blue, "what": "the floor under the blue sun"}


def _haze_box(scene):
    """A box around the camera and the floor whose volume glows magenta (an object volume)."""
    mesh = bpy.data.meshes.new("Haze")
    corners = [(x, y, z) for x in (-28, 28) for y in (-28, 28) for z in (-0.9, 20)]
    mesh.from_pydata(corners, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    box = bpy.data.objects.new("Haze", mesh)
    material = bpy.data.materials.new("Haze Glow")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output, emission = nodes.new("ShaderNodeOutputMaterial"), nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*MAGENTA, 1.0)
    emission.inputs["Strength"].default_value = 0.08
    links.new(emission.outputs["Emission"], output.inputs["Volume"])
    mesh.materials.append(material)
    scene.collection.objects.link(box)
    return box


def magenta_haze(scene, engine):
    """A black world and no light: only a magenta-glowing volume between camera and floor has colour.

    With EEVEE it is the world's own volume, which no viewport shading draws. Cycles renders no
    emission-only world volume, so there the haze is an object volume around the camera.
    """
    reset(scene, engine)
    if engine == "CYCLES":
        _world(scene, (0.0, 0.0, 0.0))
        _haze_box(scene)
    else:
        _world(scene, (0.0, 0.0, 0.0), volume=MAGENTA)
    _plane(scene)
    return {"region": (0.0, 0.7, 0.3, 1.0), "is": is_magenta, "what": "the magenta volume over the floor"}


CASES = (("world", red_world), ("light", blue_sun), ("volume", magenta_haze))


# The stand-in conversation and the event-loop harness of the two suites -----------------------------------

def conversation(workspace=None):
    """What ``view_render`` and the job tools need of a controller: an identity, a call and (for jobs) files."""
    from types import SimpleNamespace
    files = None
    if workspace is not None:
        from angelo_agent.files import FileWorkspace
        files = FileWorkspace(workspace)
    return SimpleNamespace(state={"id": "suite-chat"}, executing_call=SimpleNamespace(id="suite-chat/call-1"),
                           files=files, _epoch=1, _request_generation=1)


def pending_result(pending, seconds=240):
    """Generator: wait, through the event loop, for a pending look as ``Controller._pump_events`` does."""
    import time
    deadline = time.monotonic() + seconds
    while True:
        result = pending.poll()
        if result is not None:
            return result
        assert time.monotonic() < deadline, "the look never ended"
        yield


def wait_for(condition, seconds=120, what="the condition"):
    """Generator: let the event loop run until ``condition()`` holds."""
    import time
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for " + what
        yield


def drive(scenario, state, report, marker, interval=0.1):
    """Run the ``scenario`` generator one step per timer tick; write ``report`` and quit when it ends or fails.

    The timer survives a file load. Steps run without a context override (a file load inside one would
    leave it pointing at freed data): operators that need a window use ``in_view3d``.
    """
    import json
    import traceback

    steps = scenario()

    def finish():
        report.write_text(json.dumps(state, indent=2, default=str))
        print(marker, json.dumps({"success": state["success"], "checks": len(state["checks"]),
                                  "failed": state.get("failed_phase")}), flush=True)
        bpy.ops.wm.quit_blender()

    def tick():
        try:
            next(steps)
            return interval
        except StopIteration:
            state.update(success=bool(state["checks"]) and all(state["checks"].values()), phase="complete")
        except Exception:
            state.update(success=False, failed_phase=state.get("phase"), error=traceback.format_exc())
        finish()
        return None

    bpy.app.timers.register(tick, first_interval=1, persistent=True)


def in_view3d():
    """A context override with the first window's 3D Viewport, for operators run from a timer."""
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "VIEW_3D")
    region = next(region for region in area.regions if region.type == "WINDOW")
    return bpy.context.temp_override(window=window, area=area, region=region)


# What a look must leave as it found it -----------------------------------------------------------------

def rna_values(struct, depth=3):
    """Every value below ``struct``: nested settings recursively, other data-blocks by name, collections by size."""
    values = {}
    for prop in struct.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type":
            continue
        try:
            value = getattr(struct, name)
        except Exception as error:  # A value Blender cannot read in this state is part of the state too.
            values[name] = "unreadable: " + type(error).__name__
            continue
        if prop.type == "POINTER":
            if value is None or isinstance(value, bpy.types.ID):
                values[name] = None if value is None else "ID:" + value.name
            else:
                values[name] = rna_values(value, depth - 1) if depth else "..."
        elif prop.type == "COLLECTION":
            values[name] = len(value)
        elif isinstance(value, (str, bool, int, float)) or value is None:
            values[name] = value
        else:
            try:
                values[name] = tuple(value)
            except TypeError:
                values[name] = repr(value)
    return values


def differences(before, after, path=""):
    """Paths whose values differ between two ``rna_values`` (or any nested dict) snapshots."""
    if isinstance(before, dict) and isinstance(after, dict):
        found = []
        for key in sorted(set(before) | set(after)):
            found += differences(before.get(key, "<absent>"), after.get(key, "<absent>"), path + "/" + str(key))
        return found
    return [] if before == after else ["%s: %r -> %r" % (path, before, after)]


def id_names():
    """Every data-block of the file, by collection."""
    names = {}
    for attribute in dir(bpy.data):
        try:
            value = getattr(bpy.data, attribute)
        except Exception:
            continue
        if isinstance(value, bpy.types.bpy_prop_collection):
            names[attribute] = sorted(item.name for item in value)
    return names


def folder_listing(folder):
    from pathlib import Path as _Path
    return sorted((str(path.relative_to(folder)), path.stat().st_size, path.stat().st_mtime_ns)
                  for path in _Path(folder).rglob("*") if path.is_file())


def render_result():
    return next((image for image in bpy.data.images if image.type == "RENDER_RESULT"), None)


def persons_scene(scene):
    """Settings a look must put back exactly, none of them a default: an EXR output, odd sizes, caches on."""
    reset(scene, "CYCLES")
    scene.cycles.samples, scene.cycles.use_denoising = 77, False
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = 1920, 1080, 25
    render.border_min_x, render.border_max_y = 0.125, 0.875  # A render region they set up and switched off.
    render.filepath, render.use_file_extension = "//person/final_", True
    render.use_render_cache = True
    if hasattr(render, "use_auto_generate_texture_cache"):
        render.use_auto_generate_texture_cache = True
    settings = render.image_settings
    settings.file_format = "OPEN_EXR"
    settings.color_mode, settings.color_depth, settings.exr_codec = "RGBA", "32", "DWAA"
    scene.view_settings.exposure = -0.75
    scene.frame_set(7)
    _world(scene, RED)


def slow_scene(scene, engine="CYCLES"):
    """Quick to prepare and slow to sample on the CPU: a glowing volume the camera sits in, many bounces."""
    magenta_haze(scene, engine)
    scene.cycles.samples, scene.cycles.volume_bounces, scene.cycles.max_bounces = 4096, 12, 24
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    scene.render.threads_mode, scene.render.threads = "FIXED", 2  # Slow on any machine, and quick to stop.
    haze = bpy.data.objects["Haze"].active_material.node_tree
    scatter = haze.nodes.new("ShaderNodeVolumeScatter")
    scatter.inputs["Density"].default_value = 0.4
    add = haze.nodes.new("ShaderNodeAddShader")
    emission = next(node for node in haze.nodes if node.bl_idname == "ShaderNodeEmission")
    output = next(node for node in haze.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
    haze.links.new(emission.outputs[0], add.inputs[0])
    haze.links.new(scatter.outputs[0], add.inputs[1])
    haze.links.new(add.outputs[0], output.inputs["Volume"])


class HeldPreparation:
    """Holds each render before its first sample until ``release``: a ``render_pre`` handler, which Blender runs on
    the render job's thread before it evaluates and syncs the scene (``RE_RenderFrame``), waits there. A budget
    made to end while it holds provably ends during preparation, on a fast machine or a loaded one: a scene that is
    merely slow to prepare raced the budget's main-thread check (test_blender_rendered_view_safety on the 1.0.7
    cuts 744f498d0a6 and d72ea2aa3ea: preparation took ~0.5 s against a 0.3 s budget, and a check seen late let the
    first samples in). The handler is the fixture's own (``render_extensions.host_handler``), never add-on Python a
    look refuses. A context manager: leaving it releases and removes the handler; it never holds longer than
    ``limit`` seconds."""

    def __init__(self, limit=60.0):
        import threading
        from angelo_agent import render_extensions
        self.held, self._go, self.threads = threading.Event(), threading.Event(), []

        def hold(*_args):
            self.threads.append(threading.current_thread() is threading.main_thread())
            self.held.set()
            self._go.wait(limit)
        self._handler = render_extensions.host_handler(hold)

    def release(self):
        self._go.set()

    def __enter__(self):
        bpy.app.handlers.render_pre.append(self._handler)
        return self

    def __exit__(self, *_exc):
        self._go.set()
        if self._handler in bpy.app.handlers.render_pre:
            bpy.app.handlers.render_pre.remove(self._handler)
        return False


def heavy_to_prepare(scene):
    """Seconds of scene preparation before the first sample: a densely subdivided mesh for Cycles to build."""
    reset(scene, "CYCLES")
    _world(scene, RED)
    cube = bpy.data.objects["Cube"]
    modifier = cube.modifiers.get("Preparation") or cube.modifiers.new("Preparation", "SUBSURF")
    modifier.subdivision_type, modifier.levels, modifier.render_levels = "SIMPLE", 0, 9


# What each runtime is handed, decoded again ---------------------------------------------------------------

def decoded(data):
    """RGB rows (top row first, integers) of PNG bytes, decoded by Blender outside the code under test."""
    import imbuf
    import numpy
    buffer = imbuf.load_from_buffer(data)
    with buffer.with_buffer() as rows:
        pixels = numpy.asarray(rows).copy()
    return pixels[::-1, :, :3].astype(numpy.int32)


def payloads(references, arguments, result):
    """The pixels of ``result``'s picture as each runtime receives it: ``{"codex": rows, "claude": rows}``."""
    import base64
    from types import SimpleNamespace
    from angelo_agent.claude_evidence import ClaudeVisualEvidence
    from angelo_agent.providers import tool_content
    delivery = SimpleNamespace(references=references, models=[], settings={}, state={"options": {}}, turn_options={})
    text, image = tool_content(delivery, "angelo_view", result)
    prefix = "data:image/png;base64,"
    assert (text["type"], image["type"]) == ("inputText", "inputImage") and image["imageUrl"].startswith(prefix)
    evidence = ClaudeVisualEvidence(references)
    evidence.admit({"tool": "angelo_view", "arguments": arguments}, result)
    (block,) = references.claude_inputs(evidence.take_images())
    assert (block["type"], block["source"]["media_type"]) == ("image", "image/png")
    return {"codex": decoded(base64.b64decode(image["imageUrl"][len(prefix):])),
            "claude": decoded(base64.b64decode(block["source"]["data"]))}
