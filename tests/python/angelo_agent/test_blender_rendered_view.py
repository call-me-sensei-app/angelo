# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What angelo_view shows of a scene's own lighting, world and volumes, in a factory-startup GUI.

Three tiny scenes each make one thing a render shows: a red world, a blue sun
on a white floor, a magenta world volume. For each, Blender's own render of the
scene camera is the ground truth, and every shading of ``angelo_view`` is asked
for the same camera.

* **The gap**: SOLID, WIREFRAME and MATERIAL show none of the three, although
  the render does. MATERIAL lights the scene with a built-in HDRI and ignores
  the world.
* **LIT** (EEVEE in the viewport with the scene's own lights and world) shows
  all three at once, also as an approximation of the Cycles scenes.
* **RENDERED** shows all three, with EEVEE and with Cycles as the scene's
  engine: a real render job through ``BlenderExecutor`` and the shared job
  manager, awaited like ``Controller._pump_events`` awaits it.
* The image reaches both runtimes: a Codex tool result carries it as an
  ``inputImage`` and a Claude plan round as an ``image`` block.
* ``image`` returns a finished ``RENDER_STILL`` job's own file, or Blender's
  Render Result, downscaled and without rendering.
* Seconds per look are recorded (median of five, the tiny scene).

Run through ``run_native_suites.py --gui --include test_blender_rendered_view``;
check ``rendered-view.json`` for ``success`` and ``phase == "complete"``. No
provider, account or paid request is used.
"""

import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-rendered-view-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

import rendered_view_fixtures as fx  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
from angelo_agent import job_ui, scene_queries, view_cache  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_evidence import ClaudeVisualEvidence  # noqa: E402
from angelo_agent.providers import tool_content  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_spec import RENDER_SAMPLES, SHADINGS  # noqa: E402

REPORT = OUT / "rendered-view.json"
VIEWPORT_SHADINGS = ("SOLID", "WIREFRAME", "MATERIAL")
state = {"phase": "boot", "success": False, "checks": {}, "colours": {}, "seconds": {}, "paid_requests": 0,
         "binary": bpy.app.binary_path, "shadings": list(SHADINGS)}
ctx = {"case": "boot"}
controller = fx.conversation(WS)
references = ReferenceStore(OUT, "5c1f2f0e-6a8e-4b7e-9c56-2a54b5b8d1a1")


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context, value.observation_store = controller, references
    return value


def keep(result, label):
    target = OUT / ("%s_%s.png" % (ctx["case"], label))
    target.write_bytes(Path(result["views"][0]["path"]).read_bytes())
    return fx.decode_png(target)


def view(shading, resolution=256):
    """Generator: one viewport view through the executor. A look whose shaders are still compiling is pending
    (``view_warm``): it is awaited through the event loop, as ``Controller._pump_events`` does."""
    view_cache.images.clear()
    result = executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": shading,
                                                "resolution": resolution})
    if not isinstance(result, dict):
        result = yield from fx.pending_result(result)
    return result, keep(result, shading.lower())


def look(resolution=256, **arguments):
    """Generator: one RENDERED look through the executor, awaited through the event loop."""
    started = time.perf_counter()
    pending = executor().execute("angelo_view", dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED",
                                                      "resolution": resolution}, **arguments))
    result = yield from fx.pending_result(pending)
    ctx["look_seconds"] = time.perf_counter() - started
    return result


def truth():
    """Blender's own render of the scene camera (the fixture's render, not the agent's)."""
    scene = window().scene
    path = OUT / ("%s_truth.png" % ctx["case"])
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    with fx.in_view3d():
        bpy.ops.render.render(write_still=True)
    return fx.decode_png(path)


def gap_and_looks(engine):
    for name, build in fx.CASES:
        state["phase"] = ctx["case"] = "%s_%s" % (name, engine.lower())
        expect = build(window().scene, engine)
        window().view_layer.update()
        yield
        colours = {"render": [round(value, 1) for value in fx.mean(truth(), expect["region"])]}
        check("%s_render_shows_it" % ctx["case"], expect["is"](colours["render"]))
        yield
        if engine == "BLENDER_EEVEE":  # The gap: what the agent could see before RENDERED existed.
            for shading in VIEWPORT_SHADINGS:
                result, pixels = yield from view(shading)
                colours[shading] = [round(value, 1) for value in fx.mean(pixels, expect["region"])]
                check("%s_absent_from_%s" % (ctx["case"], shading.lower()), not expect["is"](colours[shading]))
                check("%s_%s_read_only" % (ctx["case"], shading.lower()),
                      result["read_only"] and not result["scene_changed"])
        lit, pixels = yield from view("LIT")
        colours["LIT"] = [round(value, 1) for value in fx.mean(pixels, expect["region"])]
        check("%s_lit_shows_it" % ctx["case"], expect["is"](colours["LIT"]))
        check("%s_lit_says_what_it_is" % ctx["case"], lit["read_only"] and not lit["scene_changed"]
              and lit["lit_preview"]["scene_engine"] == engine
              and lit["lit_preview"]["approximation"] == (engine == "CYCLES") and not lit["views"][0]["cache_hit"])
        result = yield from look()
        colours["RENDERED"] = [round(value, 1) for value in fx.mean(keep(result, "rendered"), expect["region"])]
        check("%s_rendered_shows_it" % ctx["case"], expect["is"](colours["RENDERED"]))
        item, rendered = result["views"][0], result["rendered"]
        check("%s_rendered_result" % ctx["case"],
              result["read_only"] and not result["scene_changed"] and result["shading"] == "RENDERED"
              and rendered["engine"] == engine and rendered["complete"] and not result["uncertain"]
              and rendered["samples"] == min(RENDER_SAMPLES, 8) and (item["width"], item["height"]) == (256, 144)
              and fx.png_size(item["path"]) == (256, 144) and engine in result["note"])
        # What it cost and why: the seconds and, once a render is long enough for its first sample to be
        # seen (these tiny ones end within one poll), how they split into preparation and sampling.
        check("%s_rendered_says_what_it_cost" % ctx["case"], "It took " in result["note"] and rendered["seconds"] >= 0
              and ("prepare_seconds" not in rendered or abs(rendered["prepare_seconds"] + rendered["sampling_seconds"]
                                                            - rendered["seconds"]) < 0.25))
        check("%s_rendered_left_no_job" % ctx["case"], job_ui.manager().active() is None
              and not [r for r in job_ui.manager().store.records.values() if r["kind"] == "RENDER_PREVIEW"])
        state["colours"][ctx["case"]] = dict(colours, what=expect["what"], region=list(expect["region"]))
        ctx["last"] = result


def hand_off():
    """The same result as each runtime receives it (the stand-in providers of the host suite cover the rest)."""
    state["phase"] = "hand_off"
    result = ctx["last"]
    delivery = SimpleNamespace(references=references, models=[], settings={}, state={"options": {}}, turn_options={})
    content = tool_content(delivery, "angelo_view", result)
    check("codex_result_carries_one_png", [item["type"] for item in content] == ["inputText", "inputImage"]
          and content[1]["imageUrl"].startswith("data:image/png;base64,"))
    evidence = ClaudeVisualEvidence(references)
    operation = {"tool": "angelo_view", "arguments": {"views": ["SCENE_CAMERA"], "shading": "RENDERED",
                                                      "resolution": 256, "targets": []}}
    evidence.check_plan([operation] * 13)  # Looks are not among the twelve views a request keeps.
    evidence.admit(operation, result)
    blocks = references.claude_inputs(evidence.take_images())
    check("claude_round_carries_one_png", len(blocks) == 1 and blocks[0]["type"] == "image"
          and blocks[0]["source"]["media_type"] == "image/png" and not evidence.requests)
    again = executor().execute("angelo_view", {"views": [result["views"][0]["view_id"]], "shading": "SOLID",
                                               "resolution": 256})
    check("a_look_camera_can_be_reused_by_a_viewport_view",
          again["views"][0]["camera"]["matrix_world"] == result["views"][0]["camera"]["matrix_world"])
    yield


def job_image():
    """A finished still of a render job, shown downscaled on request (no render)."""
    state["phase"] = ctx["case"] = "job_image"
    scene = window().scene
    expect = fx.red_world(scene, "BLENDER_EEVEE")
    scene.render.resolution_x, scene.render.resolution_y = 1280, 720
    yield
    arguments = {"kind": "RENDER_STILL", "output": "renders/final.png"}
    executor().precondition("angelo_job_start", arguments)  # The approval's admission, as the controller runs it.
    started = executor().execute("angelo_job_start", arguments)
    record = job_ui.manager().record(started["job"])
    yield from fx.wait_for(lambda: record["status"] not in {"queued", "starting", "running", "stopping"},
                           what="the still job")
    check("still_job_done", record["status"] == "done" and (WS / "renders/final.png").is_file())
    timer = time.perf_counter()
    images = sorted(image.name for image in bpy.data.images)
    result = executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 512,
                                                "image": started["job"]})
    state["seconds"]["job_image_1280x720_to_512"] = round(time.perf_counter() - timer, 3)
    item = result["views"][0]
    check("job_image_is_the_jobs_file_downscaled", (item["width"], item["height"]) == (512, 288)
          and result["rendered"]["source_size"] == [1280, 720] and result["rendered"]["job"] == started["job"]
          and expect["is"](fx.mean(keep(result, "job"), expect["region"])) and result["read_only"])
    try:
        executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": "j999"})
        check("unknown_job_refused", False)
    except RuntimeError as error:
        check("unknown_job_refused", "No job j999" in str(error))
    timer = time.perf_counter()
    result = executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 384,
                                                "image": "RENDER_RESULT"})
    state["seconds"]["render_result_1280x720_to_384"] = round(time.perf_counter() - timer, 3)
    item = result["views"][0]
    check("render_result_is_blenders_last_render_downscaled", (item["width"], item["height"]) == (384, 216)
          and result["rendered"]["source_size"] == [1280, 720] and result["rendered"]["image"] == "RENDER_RESULT"
          and expect["is"](fx.mean(keep(result, "render_result"), expect["region"])) and result["read_only"]
          and result["side_effects"] == [])
    check("finished_images_leave_no_image_datablock", sorted(image.name for image in bpy.data.images) == images)
    check("finished_images_leave_only_their_reference",
          len([path for path in references.directory.glob("*") if path.stat().st_size == 0]) == 0)
    content = tool_content(SimpleNamespace(references=references, models=[], settings={}, state={"options": {}},
                                           turn_options={}), "angelo_view", result)
    check("finished_image_reaches_the_model", [entry["type"] for entry in content] == ["inputText", "inputImage"])
    scene.render.resolution_x, scene.render.resolution_y = 320, 180


def measure(engine):
    """Seconds of one look on the tiny scene, start to result, as the model waits for it (median of five)."""
    state["phase"] = ctx["case"] = "measure_" + engine.lower()
    fx.blue_sun(window().scene, engine)
    yield
    samples = []
    for _index in range(5):
        result = yield from look()
        samples.append((ctx["look_seconds"], result["rendered"]["seconds"]))
    state["seconds"][engine] = {"size": "256x144", "samples": 8, "runs": len(samples),
                                "call_median": round(statistics.median(s[0] for s in samples), 3),
                                "call_min": round(min(s[0] for s in samples), 3),
                                "call_max": round(max(s[0] for s in samples), 3),
                                "render_median": round(statistics.median(s[1] for s in samples), 3)}


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    yield
    yield from gap_and_looks("BLENDER_EEVEE")
    yield from hand_off()
    yield from gap_and_looks("CYCLES")
    yield from job_image()
    yield from measure("BLENDER_EEVEE")
    yield from measure("CYCLES")


fixture_boot.drive(scenario, state, REPORT, "ANGELO_RENDERED_VIEW")
