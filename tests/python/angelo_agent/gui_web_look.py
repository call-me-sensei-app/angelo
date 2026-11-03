# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replayable GUI scenario: the web preview in the scene's own look, against Angelo's viewport.

doc/angelo/web_export.rst ("The look") has the contract.

Run it inside a windowed Angelo whose bundled add-on is this checkout's, with isolated state folders and the
window flags of ``gui_launch.window_flags()``::

    Angelo.app/Contents/MacOS/Angelo --factory-startup --no-window-focus --enable-event-simulate \\
        --python tests/python/angelo_agent/gui_web_look.py

``ANGELO_QA_DIR`` names the output folder; ``ANGELO_QA_HOLD`` bounds the page-side checks of each scene to that
many seconds. Five unrelated scenes, each with another view transform and another kind of World (an HDRI turned
by a Mapping node, a Sky texture, a plain colour, a gradient, none), are shown in turn. For each one the export's
``angelo_look`` extras must name the nearest three.js tone mapping and the World's form, the page (a headless
browser, ``headless_page``) must report that it applied them, and the Compare view's own route (the page's camera
posted to ``compare``) must return Angelo's viewport from the scene camera. Each scene's folder keeps the page's
capture (``web.png``), Angelo's viewport (``viewport.png``) and the report, so the two can be laid side by side
(``web_look_compare.py``). An app without the look (1.0.8) still writes ``web.png``: the "before" picture.
"""

import json
import math
import os
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headless_page  # noqa: E402

bpy.context.preferences.view.show_splash = False
bpy.context.preferences.view.use_save_prompt = False
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gui-web-look"))
OUT.mkdir(parents=True, exist_ok=True)
HOLD = float(os.environ.get("ANGELO_QA_HOLD", "40"))
BROWSER = headless_page.Page(OUT / "browser.log")
STUDIO = Path(bpy.utils.system_resource("DATAFILES", path="studiolights")) / "world"
state = {"phase": "boot", "scenes": {}, "started": time.monotonic()}
# Blender's world (Z up) -> three.js's (Y up), row-major: the exported glTF's axes.
Z_UP_TO_Y_UP = ((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1))


def record():
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))


def material(name, colour, roughness=0.5, metallic=0.0):
    made = bpy.data.materials.new(name)
    if made.node_tree is None:
        made.use_nodes = True
    principled = made.node_tree.nodes["Principled BSDF"]
    principled.inputs["Base Color"].default_value = (*colour, 1.0)
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = metallic
    return made


def world(name):
    made = bpy.data.worlds.new(name)
    tree = made.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    background = tree.nodes.new("ShaderNodeBackground")
    tree.links.new(background.outputs[0], tree.nodes.new("ShaderNodeOutputWorld").inputs["Surface"])
    return made, tree, background


def hdri(name, file, degrees, strength):
    made, tree, background = world(name)
    texture = tree.nodes.new("ShaderNodeTexEnvironment")
    texture.image = bpy.data.images.load(str(STUDIO / file), check_existing=True)
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Rotation"].default_value[2] = math.radians(degrees)
    tree.links.new(tree.nodes.new("ShaderNodeTexCoord").outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs[0], texture.inputs["Vector"])
    tree.links.new(texture.outputs[0], background.inputs["Color"])
    background.inputs["Strength"].default_value = strength
    return made


def sky(name):
    made, tree, background = world(name)
    texture = tree.nodes.new("ShaderNodeTexSky")
    texture.sun_elevation = math.radians(25)
    tree.links.new(texture.outputs[0], background.inputs["Color"])
    background.inputs["Strength"].default_value = 0.4
    return made


def gradient(name):
    made, tree, background = world(name)
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    separate = tree.nodes.new("ShaderNodeSeparateXYZ")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.05, 0.03, 0.02, 1.0)
    ramp.color_ramp.elements[1].color = (0.35, 0.55, 0.9, 1.0)
    tree.links.new(coordinates.outputs["Generated"], separate.inputs[0])
    tree.links.new(separate.outputs["Z"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], background.inputs["Color"])
    return made


def flat(name, colour, strength):
    made, _tree, background = world(name)
    background.inputs["Color"].default_value = (*colour, 1.0)
    background.inputs["Strength"].default_value = strength
    return made


def add(kind, location, made, **options):
    getattr(bpy.ops.mesh, "primitive_%s_add" % kind)(location=location, **options)
    obj = bpy.context.active_object
    obj.data.materials.append(made)
    if kind in {"uv_sphere", "torus", "monkey", "cylinder", "cone"}:
        bpy.ops.object.shade_smooth()
    return obj


def light(kind, location, rotation, energy):
    bpy.ops.object.light_add(type=kind, location=location, rotation=rotation)
    bpy.context.active_object.data.energy = energy


def clear(scene):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.film_transparent = False


def scene_hdri(scene):
    scene.view_settings.view_transform = "Standard"
    scene.world = hdri("Courtyard", "courtyard.exr", 120, 0.9)
    add("uv_sphere", (0, 0, 1), material("Chrome", (0.9, 0.9, 0.9), 0.15, 1.0), radius=1, segments=64, ring_count=32)
    add("cube", (2.4, 0.5, 0.75), material("Clay", (0.7, 0.35, 0.2), 0.8), size=1.5)
    add("plane", (0, 0, 0), material("Ground", (0.4, 0.4, 0.38), 0.9), size=12)


def scene_sky(scene):
    scene.view_settings.view_transform = "Filmic"
    scene.view_settings.exposure = -0.5
    scene.world = sky("Dusk")
    add("torus", (0, 0, 1), material("Gold", (1.0, 0.75, 0.3), 0.3, 1.0), major_radius=1.2, minor_radius=0.4)
    add("plane", (0, 0, 0), material("Sand", (0.75, 0.65, 0.5), 0.95), size=16)
    light("SUN", (0, 0, 5), (math.radians(65), 0, math.radians(30)), 2.0)


def scene_flat(scene):
    scene.view_settings.view_transform = "AgX"
    scene.world = flat("Overcast", (0.3, 0.35, 0.4), 1.0)
    add("monkey", (0, 0, 1), material("Teal", (0.1, 0.5, 0.45), 0.4))
    add("plane", (0, 0, 0), material("Floor", (0.6, 0.6, 0.6), 0.7), size=10)
    light("POINT", (2, -2, 3), (0, 0, 0), 400.0)


def scene_gradient(scene):
    scene.view_settings.view_transform = "Khronos PBR Neutral"
    scene.world = gradient("Horizon")
    add("cylinder", (-1.2, 0, 1), material("Red", (0.8, 0.1, 0.08), 0.35), radius=0.7, depth=2)
    add("cone", (1.2, 0, 1), material("White", (0.85, 0.85, 0.85), 0.5), radius1=0.8, depth=2)
    light("SPOT", (0, -4, 5), (math.radians(40), 0, 0), 800.0)


def scene_none(scene):
    scene.view_settings.view_transform = "ACES 2.0"
    scene.world = None
    add("ico_sphere", (0, 0, 1), material("Blue", (0.15, 0.3, 0.8), 0.25), radius=1, subdivisions=3)
    add("plane", (0, 0, 0), material("Concrete", (0.5, 0.5, 0.5), 0.9), size=10)
    light("SUN", (0, 0, 5), (math.radians(40), math.radians(15), 0), 3.0)


SCENES = (("hdri_standard", scene_hdri, "LinearToneMapping", "IMAGE"),
          ("sky_filmic", scene_sky, "AgXToneMapping", "BAKE"),
          ("flat_agx", scene_flat, "AgXToneMapping", "COLOR"),
          ("gradient_neutral", scene_gradient, "NeutralToneMapping", "BAKE"),
          ("none_aces2", scene_none, "NeutralToneMapping", "NONE"))


def fetch(path):
    from angelo_agent import web_preview
    with urllib.request.urlopen(web_preview.url() + path, timeout=5) as response:
        return response.read()


def post(path, body):
    """A POST as the page sends it (its Host and Origin), as the Compare button does."""
    from angelo_agent import web_preview
    url = web_preview.url()
    origin = url.split("/", 3)[:3]
    request = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Content-Type": "application/json", "Origin": "/".join(origin), "Host": origin[2]})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


def page_camera(scene, glb):
    """The page's first camera: the scene camera, Y up, at the glTF's vertical field of view (app.js ``frame``)."""
    from angelo_agent import web_budget
    document, _binary = web_budget.read_glb(glb)
    yfov = math.degrees(document["cameras"][0]["perspective"]["yfov"])
    blender = [list(row) for row in scene.camera.matrix_world.normalized()]
    three = [[sum(Z_UP_TO_Y_UP[row][k] * blender[k][col] for k in range(4)) for col in range(4)] for row in range(4)]
    return [three[row][col] for col in range(4) for row in range(4)], yfov


def wait_for(condition, seconds):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        yield 0.25


def scenario():
    from angelo_agent import web_preview
    web_preview.open_page = lambda address: None   # The headless page opens it; never the person's browser.
    window = bpy.context.window_manager.windows[0]
    scene = window.scene
    looks = hasattr(web_preview, "_compare")   # An app with the look (1.0.9) or the "before" app.
    state["app_has_look"] = looks
    first = True
    for name, build, tone, kind in SCENES:
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        result = state["scenes"][name] = {"checks": {}}
        state["phase"] = name
        record()
        with bpy.context.temp_override(window=window):
            clear(scene)
            build(scene)
            bpy.ops.object.camera_add(location=(6.5, -6.0, 4.2), rotation=(math.radians(68), 0, math.radians(47)))
            scene.camera = bpy.context.active_object
            bpy.ops.object.select_all(action="DESELECT")
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.web_preview()   # Starts it, or exports this scene now when it runs.
        server = web_preview._state["server"]
        if first:
            BROWSER.open(web_preview.url())
            (OUT / "preview-url.txt").write_text(web_preview.url())
            first = False
        yield from wait_for(lambda: server.streams >= 1, HOLD)
        report = json.loads(fetch("report.json"))
        if looks and report.get("settings", {}).get("environment", {}).get("status") == "pending":
            yield from wait_for(lambda: json.loads(fetch("report.json")).get("settings", {}).get(
                "environment", {}).get("status") != "pending", HOLD)
            report = json.loads(fetch("report.json"))
        (folder / "report.json").write_text(json.dumps(report, indent=2))
        yield from wait_for(lambda: server.feedback.get("version") == server.version, HOLD)
        yield 2.0   # The World's picture decoded and the frame drawn.
        from angelo_agent import view_web   # The page's own picture, as angelo_view WEB takes it.
        target = folder / "web.png"
        references = SimpleNamespace(new_observation=lambda label: {"path": str(target), "name": label, "id": "web"},
                                     discard_observation=lambda reference: None)
        shown = view_web.source(window, {"image": "WEB"}, references, None)
        result["web_facts"] = shown.facts
        picture = target.read_bytes()
        result["checks"]["web_captured"] = {"ok": picture[:4] == b"\x89PNG", "detail": None}
        size = (int.from_bytes(picture[16:20], "big"), int.from_bytes(picture[20:24], "big"))   # The page's canvas.
        if looks:
            from angelo_agent import web_budget
            glb = fetch("scene.glb")
            document, _binary = web_budget.read_glb(glb)
            extras = document["scenes"][document.get("scene", 0)].get("extras", {}).get("angelo_look", {})
            environment = extras.get("environment") or {}
            applied = server.feedback.get("look", {})
            result["checks"]["extras_tone"] = {"ok": extras.get("toneMapping") == tone,
                                               "detail": extras.get("toneMapping")}
            result["checks"]["world_form"] = {"ok": report["settings"]["environment"]["kind"] == kind,
                                              "detail": report["settings"]["environment"]}
            result["checks"]["page_applied_tone"] = {"ok": applied.get("toneMapping") == tone, "detail": applied}
            result["checks"]["page_applied_world"] = {
                "ok": applied.get("environment", "") == ({"IMAGE": "equirect", "BAKE": "equirect", "COLOR": "color"}
                                                          .get(kind, "")), "detail": applied.get("environment")}
            wanted = 2 ** scene.view_settings.exposure
            result["checks"]["exposure"] = {"ok": abs(extras.get("toneMappingExposure", 0) - wanted) < 1e-4,
                                            "detail": extras.get("toneMappingExposure")}
            result["look"] = {"extras": {key: value for key, value in extras.items() if key != "environment"},
                              "environment": {key: value for key, value in environment.items() if key != "bufferView"}}
            result["findings"] = [item["code"] for item in report.get("findings", ())]
            matrix, fov = page_camera(scene, glb)
            seen = server.viewports
            post("compare", {"id": 1000 + len(state["scenes"]), "matrix": matrix, "fov": fov, "near": 0.05,
                             "far": 500.0, "width": size[0], "height": size[1]})
            yield from wait_for(lambda: server.viewports != seen, HOLD)
            picture = server.viewport_bytes()
            (folder / "viewport.png").write_bytes(picture)
            result["checks"]["viewport_drawn"] = {"ok": picture[:4] == b"\x89PNG",
                                                  "detail": json.loads(server.viewport_report())}
        record()
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.web_preview(stop=True)
    state["passed"] = all(check["ok"] for item in state["scenes"].values() for check in item["checks"].values())
    state["phase"] = "done"
    record()


def driver():
    try:
        return next(driver.steps)
    except StopIteration:
        BROWSER.close()
        bpy.ops.wm.quit_blender()
        return None
    except Exception:
        state["phase"] = "error"
        state["error"] = traceback.format_exc()
        record()
        BROWSER.close()
        bpy.ops.wm.quit_blender()
        return None


driver.steps = scenario()
bpy.app.timers.register(driver, first_interval=3.0)
sys.stdout.write("gui_web_look: output in %s\n" % OUT)
