# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replayable GUI scenario for the web export and live preview (doc/angelo/web_export.rst).

Run it inside a windowed Angelo whose bundled add-on is this checkout's, with isolated state folders and the
window flags of ``gui_launch.window_flags()`` (behind, input isolation)::

    Angelo.app/Contents/MacOS/Angelo --factory-startup --no-window-focus --enable-event-simulate \\
        --python tests/python/angelo_agent/gui_web_preview.py

``ANGELO_QA_DIR`` names the output folder (state.json, the agent route's .glb, the URL in preview-url.txt, the
page's capture in web-capture.png); ``ANGELO_QA_HOLD`` turns on the page-side checks and bounds them to that many
seconds. The page is opened in a headless browser (``headless_page``: no window, its own profile), so those checks
need no person; with ``ANGELO_QA_BROWSER=none`` (or no Chromium-family browser installed) the scenario waits the
hold for a person to open preview-url.txt instead. It never opens the person's browser (``web_preview.open_page``
is replaced) and quits Blender at the end, so the server's shutdown on quit is part of the check (the caller
confirms the port is closed afterwards).

Steps: build a small scene with what merges and what doesn't carry over; the person's operator starts the
preview; the page, its policy, report and .glb are fetched over loopback; a scene edit must produce a new version
after the debounce, and a selection change alone must not; the agent's route (``web_host``) runs angelo_web_check
and angelo_web_export into a temporary workspace; with a hold, the page reports each web shader's compile result, a
uniform edit reaches it without an export, and angelo_view's WEB source returns a capture that is not blank; Live
Web Preview's Stop ends it.
"""

import json
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

bpy.context.preferences.view.show_splash = False   # Never Blender's Quick Setup in an Angelo window.
bpy.context.preferences.view.use_save_prompt = False   # The quit at the end is part of the check, not a dialog.
if not addon_utils.check("angelo_agent")[1]:
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-gui-web-preview"))
OUT.mkdir(parents=True, exist_ok=True)
HOLD = float(os.environ.get("ANGELO_QA_HOLD", "0"))
BROWSER = headless_page.Page(OUT / "browser.log")
state = {"phase": "boot", "checks": {}, "started": time.monotonic()}
GLOW = """uniform vec3 tint; // color
uniform float pulse; // 0 4 = 2
void main() {
  float rim = pow(1.0 - abs(dot(normalize(vNormal), normalize(vViewPosition))), 2.0);
  gl_FragColor = vec4(tint * (0.3 + rim * (1.0 + 0.5 * sin(uTime * pulse))), 1.0);
}
"""


def record():
    (OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))


def check(name, value, detail=None):
    state["checks"][name] = {"ok": bool(value), "detail": detail}
    record()


def fetch(path):
    from angelo_agent import web_preview
    with urllib.request.urlopen(web_preview.url() + path, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def drawn_share(path):
    """The share of a picture's pixels that differ from its corner (the page's background): 0 for a blank canvas."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    rgb = pixels.reshape(-1, 4)[:, :3]
    return float((numpy.abs(rgb - rgb[0]).max(axis=1) > 0.04).mean()) if len(rgb) else 0.0


def material(name):
    made = bpy.data.materials.new(name)
    if made.node_tree is None:
        made.use_nodes = True
    return made


def build_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    stone = material("Stone")
    principled = stone.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (0.55, 0.5, 0.44, 1.0)
    principled.inputs["Roughness"].default_value = 0.65
    bpy.ops.mesh.primitive_plane_add(size=14)
    bpy.context.active_object.name = "Floor"
    bpy.context.active_object.data.materials.append(stone)
    for index in range(6):   # Static, one material: a merge.
        bpy.ops.mesh.primitive_cube_add(size=0.8, location=(-3 + index * 1.2, -1.5, 0.4 + 0.25 * (index % 3)))
        bpy.context.active_object.name = "Block%d" % index
        bpy.context.active_object.data.materials.append(stone)
    glow = material("Marble")   # A procedural material: it won't carry over.
    tree = glow.node_tree
    noise = tree.nodes.new("ShaderNodeTexNoise")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    tree.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], tree.nodes["Principled BSDF"].inputs["Base Color"])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.1, location=(0.5, 1.4, 1.1), segments=48, ring_count=24)
    sphere = bpy.context.active_object
    sphere.name = "Orb"
    sphere.data.materials.append(glow)
    bpy.ops.object.shade_smooth()
    shaders = OUT / "project" / "shaders"
    shaders.mkdir(parents=True, exist_ok=True)
    (shaders / "glow.glsl").write_text(GLOW)
    (shaders / "broken.glsl").write_text("uniform float gain;\nvoid main() {\n  gl_FragColor = vec4(gain);; oops\n}\n")
    for name, kind, location, path in (("Holo", "HOLOGRAM", (3.2, 1.6, 1.0), None),
                                       ("Glow", "FILE", (-2.6, 1.8, 0.9), shaders / "glow.glsl"),
                                       ("Broken", "FILE", (-0.6, 3.6, 0.6), shaders / "broken.glsl")):
        bpy.ops.mesh.primitive_torus_add(location=location, major_radius=0.8, minor_radius=0.28)
        obj = bpy.context.active_object
        obj.name = name
        made = material(name)
        obj.data.materials.append(made)
        made.angelo_web_shader.kind = kind
        if path is not None:
            made.angelo_web_shader.path = str(path)
    bpy.ops.object.light_add(type="SUN", rotation=(0.9, 0.2, 0.6))
    bpy.context.active_object.data.energy = 3.0
    bpy.ops.object.light_add(type="AREA", location=(-3, 3, 4))
    bpy.context.active_object.name = "Softbox"
    bpy.ops.object.camera_add(location=(7.5, -7.0, 5.0), rotation=(1.1, 0.0, 0.82))
    bpy.context.scene.camera = bpy.context.active_object
    return sphere


def scenario():
    from angelo_agent import web_host, web_preview
    from angelo_agent.files import FileWorkspace
    import angelo_agent
    root = Path(__file__).resolve().parents[3]
    mine = root / "scripts/addons_core/angelo_agent/web_preview.py"
    check("addon_is_this_checkout", Path(web_preview.__file__).read_bytes() == mine.read_bytes(), angelo_agent.__file__)
    opened = []
    web_preview.open_page = opened.append   # Never the person's browser in a scripted check.
    window = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=window):
        sphere = build_scene()
    with bpy.context.temp_override(window=window):
        result = bpy.ops.angelo.web_preview()
    check("operator_started", result == {"FINISHED"} and web_preview.running(), result)
    check("browser_asked_once", opened == [web_preview.url()], opened)
    (OUT / "preview-url.txt").write_text(web_preview.url())
    status, headers, page = fetch("")
    check("page_served", status == 200 and b"<svg" in page and "default-src 'none'" in
          headers.get("Content-Security-Policy", ""), headers.get("Content-Security-Policy"))
    report = json.loads(fetch("report.json")[2])
    codes = sorted({item["code"] for item in report.get("findings", ())})
    check("findings", {"material_nodes", "light_type", "web_shader"} <= set(codes), codes)
    shaded = report.get("web_shaders", {})
    check("web_shader_state", shaded.get("Holo", {}).get("kind") == "hologram" and "glow.glsl" in
          report.get("shader_sources", {}) and shaded.get("Glow", {}).get("uniforms", {}).get("pulse") == 2.0,
          {name: value.get("kind") for name, value in shaded.items()})
    glb_extras = fetch("scene.glb")[2]
    check("extras_in_glb", b"angelo_web_shader" in glb_extras)
    check("merged", report.get("merged", {}).get("objects", 0) >= 6, report.get("merged"))
    # The floor and six blocks share Stone (one draw); Orb and the three tori have a material each.
    check("budget", report.get("budget", {}).get("draw_calls") == 5, report.get("budget"))
    check("no_false_compositor", "compositor" not in codes, codes)
    glb = fetch("scene.glb")[2]
    check("glb_served", glb[:4] == b"glTF", len(glb))
    state["first"] = {"version": report.get("version"), "settings": report.get("settings"),
                      "budget": report.get("budget"), "findings": codes}
    yield 1.0
    exports = web_preview._state["exports"]
    for obj in bpy.data.objects:   # A selection change alone: no export.
        obj.select_set(obj.name == "Orb")
    yield 2.0
    check("selection_alone_no_export", web_preview._state["exports"] == exports, web_preview._state["exports"])
    sphere.location.z += 0.6       # A real edit: one export after the debounce.
    deadline = time.monotonic() + 10.0
    while web_preview._state["exports"] == exports and time.monotonic() < deadline:
        yield 0.25
    check("edit_reexports", web_preview._state["exports"] == exports + 1, web_preview._state["exports"])
    yield 2.0
    check("no_self_triggered_loop", web_preview._state["exports"] == exports + 1, web_preview._state["exports"])
    workspace = Path(tempfile.mkdtemp(prefix="angelo-web-workspace-"))
    controller = SimpleNamespace(files=FileWorkspace(str(workspace)))
    options = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}
    checked = web_host.execute(controller, window, "angelo_web_check", dict(options))
    check("agent_check", "estimate" in checked and checked["findings"] and "note" in checked,
          [item["code"] for item in checked["findings"]])
    exported = web_host.execute(controller, window, "angelo_web_export", dict(options, output="web/scene.glb"))
    written = workspace / exported["output"]
    check("agent_export", written.is_file() and str(workspace) not in json.dumps(exported), exported["output"])
    again = web_host.execute(controller, window, "angelo_web_export",
                             dict(options, merge=False, output="web/scene.glb"))
    check("never_overwrites", again["output"] != exported["output"], again["output"])
    with bpy.context.temp_override(window=window):   # The person's File > Export > Web (.glb, Three.js).
        person = bpy.ops.angelo.web_export(filepath=str(OUT / "person.glb"))
    check("person_export", person == {"FINISHED"} and (OUT / "person.glb").read_bytes()[:4] == b"glTF", person)
    state["agent"] = {"budget": exported["budget"], "unmerged_budget": again["budget"], "settings": exported["settings"]}
    if HOLD:   # A browser opens the page (headless, or a person's), then the page's side is checked.
        state["browser"] = "headless %s" % BROWSER.executable if BROWSER.open(web_preview.url()) else "a person"
        state["phase"] = "holding"
        record()
        server = web_preview._state["server"]
        deadline = time.monotonic() + HOLD
        while server.streams < 1 and time.monotonic() < deadline:
            yield 0.5
        check("page_opened", server.streams >= 1, server.streams)
        while not server.feedback.get("shaders") and time.monotonic() < deadline:
            yield 0.5
        statuses = {item["material"]: item for item in server.feedback.get("shaders", [])}
        check("shaders_compiled", statuses.get("Holo", {}).get("ok") and statuses.get("Glow", {}).get("ok"),
              statuses)
        check("compile_error_reported", statuses.get("Broken", {}).get("ok") is False
              and bool(statuses.get("Broken", {}).get("error")), statuses.get("Broken"))
        exports, seen = web_preview._state["exports"], server.feedback.get("version")
        bpy.data.materials["Holo"].angelo_web_shader.uniforms["opacity"].value = 0.95   # A slider moves.
        while server.feedback.get("version") == seen and time.monotonic() < deadline:
            yield 0.1
        check("uniform_live_without_export", server.feedback.get("version") != seen
              and web_preview._state["exports"] == exports, [seen, server.feedback.get("version")])
        yield 1.5
        from angelo_agent import view_web   # The agent's route: angelo_view image WEB's picture source.
        target = OUT / "web-capture.png"
        references = SimpleNamespace(new_observation=lambda name: {"path": str(target), "name": name, "id": "web"},
                                     discard_observation=lambda reference: None)
        shown = view_web.source(window, {"image": "WEB"}, references, None)
        picture = target.read_bytes() if target.is_file() else b""
        check("capture", picture[:4] == b"\x89PNG" and shown.framing == "WEB_PREVIEW"
              and {item["material"] for item in shown.facts["web_shaders"]} >= {"Holo", "Glow", "Broken"},
              {"bytes": len(picture), "facts": shown.facts})
        share = drawn_share(target) if picture[:4] == b"\x89PNG" else 0.0
        check("capture_not_blank", share > 0.05, round(share, 4))   # The scene drew, not only the background.
        state["feedback"] = server.feedback
        state["phase"] = "inspect"
        record()
        if BROWSER.process is None:   # A person's browser: time for their screenshot of it.
            while time.monotonic() < deadline:
                yield 1.0
        BROWSER.close()
    with bpy.context.temp_override(window=window):
        bpy.ops.angelo.web_preview(stop=True)
    check("stopped", not web_preview.running())
    with bpy.context.temp_override(window=window):   # Start again, then quit with it running.
        bpy.ops.angelo.web_preview()
    state["port_at_quit"] = int(web_preview.url().split(":")[2].split("/")[0])
    state["phase"] = "done"
    record()


def driver():
    runner = driver.steps
    try:
        wait = next(runner)
        return wait
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
sys.stdout.write("gui_web_preview: output in %s\n" % OUT)
