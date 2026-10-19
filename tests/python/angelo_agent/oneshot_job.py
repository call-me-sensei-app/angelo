# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background Blender jobs of the one-shot tests (run by ``oneshot_references`` and ``oneshot_report``).

    Blender -b --factory-startup --python-exit-code 1 --python oneshot_job.py -- --job JOB --scenario NAME --out DIR ...

``--job reference`` builds the test's ground truth from the factory scene and
self-checks it: the brief's checks must fail on the factory scene as a whole,
pass on the ground truth, and pass again on the scoring frame. It then saves
``ground-truth.blend``, renders every fixed camera as a reference JPEG,
cuts the test's source pictures (``SOURCE_IMAGES``; ``--sources`` gives their
verified paths) and writes the ray-cast capture. A failed self-check exits 1,
so no reference comes from a ground truth its own checks reject.

``--job capture`` opens a side's saved scene and does what ``scenario_job.py``
does for a scenario: every check again, review renders of every fixed view
with one fixed look, and the ray-cast capture.

``--job final`` renders the still a person reviews: the saved scene from the
brief's own camera position (``FINAL_VIEW``) with the scene's own engine,
samples, lights, world and colour management. Only what keeps the job bounded
is overridden, and recorded: the longest side (``FINAL_MAX_SIDE``), a Cycles
time limit and the file format. It also measures the pixel statistics of that
still, of the still the agent saved itself (``--still``), and of the factory
scene rendered from its own camera and from the brief's, which a finished
picture must not resemble. Nothing here is judged; ``oneshot_checks`` reads
the numbers on the host.

All three run in stock Blender, so both sides and the ground truth go through
one identical pipeline.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

import oneshot_images as images  # noqa: E402
import scenario_build_kit as kit  # noqa: E402
import scenario_capture as capture  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_specs as specs  # noqa: E402

REVIEW_RESOLUTION = (960, 540)
FINAL_MAX_SIDE = 1920
CYCLES_SECONDS = 600.0
BASELINE_SIDE = 480


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=("reference", "capture", "final"), required=True)
    parser.add_argument("--scenario", choices=specs.ONESHOT_NAMES, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--blend", default="")
    parser.add_argument("--still", default="", help="final: the still the agent saved itself")
    parser.add_argument("--sources", default="{}", help="reference: JSON {source file name: verified path}")
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--no-render", action="store_true", help="checks, crops and captures only (tests)")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def selfcheck(scenario, scene):
    """The brief's checks fail on the factory scene, pass on the ground truth and pass on the scoring frame."""
    turn = scenario.TURNS[0]
    before = checks.evaluate(bpy, scene, turn["checks"])
    started = time.perf_counter()
    scenario.build_turn(bpy, turn["id"])
    built_ms = round((time.perf_counter() - started) * 1000, 2)
    after = checks.evaluate(bpy, scene, turn["checks"])
    scene.frame_set(scenario.FRAME)
    final = checks.evaluate(bpy, scene, specs.all_checks(scenario.ID))
    report = {"turns": [{"turn": turn["id"], "fails_before": not before["success"], "passes_after": after["success"],
                         "checks": after["total"], "build_ms": built_ms,
                         "passing_before": [entry["label"] for entry in before["results"] if entry["passed"]],
                         "failed_after": [entry for entry in after["results"] if not entry["passed"]]}],
              "final": {"passed": final["passed"], "total": final["total"],
                        "failed": [entry for entry in final["results"] if not entry["passed"]]}}
    report["success"] = not before["success"] and after["success"] and final["success"]
    return report


def reference_job(args, out):
    scenario = specs.module(args.scenario)
    scene = bpy.context.scene
    report = selfcheck(scenario, scene)
    (out / "selfcheck.json").write_text(json.dumps(report, indent=2, default=str))
    if not report["success"]:
        print("ONESHOT_SELFCHECK_FAILED", json.dumps(report, default=str)[:6000], flush=True)
        return 1
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "ground-truth.blend"), copy=True, compress=True)
    # Source pictures first: they need nothing of the scene, and a bad crop box must not cost a render.
    crops = {}
    sources = json.loads(args.sources)
    for key, spec in getattr(scenario, "SOURCE_IMAGES", {}).items():
        picture = images.read_display(bpy, sources[spec["file"]])
        box = spec.get("box") or (0, 0, picture.shape[1], picture.shape[0])
        part = images.cropped(picture, box, spec.get("scale", 1))
        quality = images.write_within(bpy, part, out / (key + ".jpg"), specs.REFERENCE_MAX_BYTES)
        crops[key] = {"size": [int(part.shape[1]), int(part.shape[0])], "jpeg_quality": quality}
    (out / "crops.json").write_text(json.dumps(crops, indent=2))
    camera = scene.camera
    written = {}
    if not args.no_render:
        written = capture.render_views(bpy, scene, scenario.CAMERAS, out, specs.REFERENCE_RESOLUTION,
                                       samples=args.samples, frame=scenario.FRAME, max_bytes=specs.REFERENCE_MAX_BYTES)
    scene.camera = camera   # The renders leave the scene with their last fixed view.
    capture.match_render_visibility(bpy, scene)
    scene.frame_set(scenario.FRAME)
    capture.save_capture(out / "capture.npz", capture.capture(bpy, scene, scenario.CAMERAS))
    print("ONESHOT_REFERENCE_DONE", json.dumps({"renders": sorted(written), "crops": sorted(crops)}), flush=True)
    return 0


def capture_job(args, out):
    scenario = specs.module(args.scenario)
    bpy.ops.wm.open_mainfile(filepath=args.blend, load_ui=False)
    scene = bpy.context.scene
    scene.frame_set(scenario.FRAME)
    # The compliance pass reads the scene as the side left it, before rendering changes its settings.
    final = checks.evaluate(bpy, scene, specs.all_checks(scenario.ID))
    (out / "final-checks.json").write_text(json.dumps(final, indent=2, default=str))
    renders = {}
    if not args.no_render:
        renders = capture.render_views(bpy, scene, scenario.CAMERAS, out, REVIEW_RESOLUTION, samples=args.samples,
                                       frame=scenario.FRAME, prefix="render-")
    capture.match_render_visibility(bpy, scene)
    scene.frame_set(scenario.FRAME)
    capture.save_capture(out / "capture.npz", capture.capture(bpy, scene, scenario.CAMERAS))
    print("ONESHOT_CAPTURE_DONE", json.dumps({"renders": sorted(renders), "final_passed": final["passed"],
                                              "final_total": final["total"]}), flush=True)
    return 0


def _view_camera(scene, view):
    camera = kit.camera_object(bpy, "Harness Final View", view["location"], view["target"], view.get("lens", 50.0),
                               ortho_scale=view.get("ortho_scale"), clip_end=20000.0)
    camera.data.sensor_width, camera.data.sensor_fit = capture.SENSOR_WIDTH, "AUTO"
    scene.camera = camera
    return camera


def _render(scene, path):
    """One still of the scene as it is set up, as an 8-bit PNG; returns the seconds it took or an error."""
    settings = scene.render.image_settings
    settings.file_format, settings.color_mode, settings.color_depth = "PNG", "RGB", "8"
    scene.render.filepath, scene.render.use_file_extension = str(path), True
    started = time.monotonic()
    try:
        bpy.ops.render.render(write_still=True, scene=scene.name)
    except RuntimeError as error:
        return {"error": str(error)[:400]}
    return {"seconds": round(time.monotonic() - started, 2)}


def _baselines(scenario, out):
    """The factory scene (this process starts with it), from its own camera and from the brief's."""
    scene = bpy.context.scene
    width, height = scenario.FINAL_STILL["size"]
    scale = BASELINE_SIDE / max(width, height)
    scene.render.resolution_x, scene.render.resolution_y = max(4, round(width * scale)), max(4, round(height * scale))
    scene.render.resolution_percentage = 100
    report = {}
    for name, own_camera in (("default scene", True), ("default scene from the brief's camera", False)):
        if not own_camera:
            _view_camera(scene, scenario.CAMERAS[scenario.FINAL_VIEW])
        path = out / ("baseline-%s.png" % ("own-camera" if own_camera else "brief-camera"))
        outcome = _render(scene, path)
        report[name] = dict(outcome, **(images.file_statistics(bpy, path) if "error" not in outcome else {}))
    return report


def final_job(args, out):
    scenario = specs.module(args.scenario)
    report = {"baselines": _baselines(scenario, out), "overrides": {}}
    bpy.ops.wm.open_mainfile(filepath=args.blend, load_ui=False)
    scene = bpy.context.scene
    scene.frame_set(scenario.FRAME)
    render = scene.render
    report["scene"] = {"engine": render.engine, "resolution": [render.resolution_x, render.resolution_y,
                                                              render.resolution_percentage],
                       "view_transform": scene.view_settings.view_transform, "look": scene.view_settings.look,
                       "camera": scene.camera.name if scene.camera else None, "frame": scene.frame_current}
    _view_camera(scene, scenario.CAMERAS[scenario.FINAL_VIEW])
    longest = max(render.resolution_x, render.resolution_y) * render.resolution_percentage / 100.0
    if longest > FINAL_MAX_SIDE:
        render.resolution_percentage = max(1, int(100 * FINAL_MAX_SIDE / max(render.resolution_x, render.resolution_y)))
        report["overrides"]["resolution_percentage"] = render.resolution_percentage
    if render.engine == "CYCLES" and not 0 < scene.cycles.time_limit <= CYCLES_SECONDS:
        scene.cycles.time_limit = CYCLES_SECONDS
        report["overrides"]["cycles_time_limit_s"] = CYCLES_SECONDS
    report["overrides"]["file_format"] = "PNG, 8-bit RGB"
    path = out / "harness-final.png"
    report["harness"] = _render(scene, path)
    if "error" not in report["harness"]:
        report["harness"].update(images.file_statistics(bpy, path), file=path.name)
    if args.still:
        report["agent"] = dict(images.file_statistics(bpy, args.still, preview=out / "agent-final-preview.png"),
                               source=Path(args.still).name)
    (out / "final.json").write_text(json.dumps(report, indent=2, default=str))
    print("ONESHOT_FINAL_DONE", json.dumps({"harness": {k: v for k, v in report["harness"].items() if k != "thumbnail"},
                                            "overrides": report["overrides"]}, default=str), flush=True)
    return 0


def main():
    args = arguments()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    return {"reference": reference_job, "capture": capture_job, "final": final_job}[args.job](args, out)


sys.exit(main())
