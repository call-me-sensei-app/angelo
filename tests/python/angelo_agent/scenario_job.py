# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background Blender jobs of the scenario benchmarks (run by ``scenario_references`` and the bench driver).

    Blender -b --factory-startup --python-exit-code 1 --python scenario_job.py -- --job JOB --scenario ID --out DIR [--blend FILE]

``--job reference`` builds the ground truth turn by turn from the factory
scene and self-checks it: before each turn that turn's checks must fail as a
whole (they measure the turn's own work), after it they must pass, and at the
end every turn's checks must still pass. It then saves ``ground-truth.blend``,
renders every camera as a reference JPEG (within ``REFERENCE_MAX_BYTES``) and
writes the ray-cast capture (``capture.npz``) and ``selfcheck.json``. A failed self-check exits 1, so no
reference is ever produced from a ground truth its own checks reject.

``--job capture`` opens a side's saved end-of-session scene (``--blend``),
re-runs every turn's checks on it first (rendering changes its render
settings and camera), then writes the same ray-cast capture plus beauty
renders of every view, for scoring and side-by-side review. Both jobs use stock Blender, so the two
sides and the ground truth go through one identical pipeline.
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

import scenario_capture as capture  # noqa: E402
import scenario_checks as checks  # noqa: E402
import scenario_specs as specs  # noqa: E402

REVIEW_RESOLUTION = (960, 540)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=("reference", "capture"), required=True)
    parser.add_argument("--scenario", choices=specs.SCENARIO_IDS, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--blend", default="")
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--no-render", action="store_true", help="captures and checks only (tests)")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def selfcheck(scenario, scene):
    """Each turn's checks fail before it and pass after it; all pass at the end (on the scoring frame)."""
    report = {"turns": [], "final": None, "success": False}
    for turn in scenario.TURNS:
        before = checks.evaluate(bpy, scene, turn["checks"])
        started = time.perf_counter()
        scenario.build_turn(bpy, turn["id"])
        built_ms = round((time.perf_counter() - started) * 1000, 2)
        after = checks.evaluate(bpy, scene, turn["checks"])
        trivially = [entry["label"] for entry in before["results"] if entry["passed"]]
        report["turns"].append({"turn": turn["id"], "fails_before": not before["success"], "passes_after": after["success"],
                                "checks": after["total"], "passing_before": trivially, "build_ms": built_ms,
                                "failed_after": [entry for entry in after["results"] if not entry["passed"]]})
    scene.frame_set(scenario.FRAME)
    final = checks.evaluate(bpy, scene, specs.all_checks(scenario.ID))
    report["final"] = {"passed": final["passed"], "total": final["total"],
                       "failed": [entry for entry in final["results"] if not entry["passed"]]}
    report["success"] = (all(turn["fails_before"] and turn["passes_after"] for turn in report["turns"])
                         and final["success"])
    return report


def reference_job(args, out):
    scenario = specs.module(args.scenario)
    scene = bpy.context.scene
    report = selfcheck(scenario, scene)
    (out / "selfcheck.json").write_text(json.dumps(report, indent=2))
    if not report["success"]:
        print("SCENARIO_SELFCHECK_FAILED", json.dumps(report)[:4000], flush=True)
        return 1
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "ground-truth.blend"), copy=True, compress=True)
    images = {}
    if not args.no_render:
        written = capture.render_views(bpy, scene, scenario.CAMERAS, out, specs.REFERENCE_RESOLUTION,
                                       samples=args.samples, frame=scenario.FRAME, max_bytes=specs.REFERENCE_MAX_BYTES)
        images = {view: Path(path).name for view, path in written.items()}
    # Renders leave the scene untouched apart from its camera; restore it before capturing.
    scene.camera = bpy.data.objects.get("Deck Camera") or scene.camera
    capture.match_render_visibility(bpy, scene)
    scene.frame_set(scenario.FRAME)
    capture.save_capture(out / "capture.npz", capture.capture(bpy, scene, scenario.CAMERAS))
    print("SCENARIO_REFERENCE_DONE", json.dumps({"images": images, "selfcheck": report["success"]}), flush=True)
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
    print("SCENARIO_CAPTURE_DONE", json.dumps({"renders": sorted(renders), "final_passed": final["passed"],
                                               "final_total": final["total"]}), flush=True)
    return 0


def main():
    args = arguments()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    return reference_job(args, out) if args.job == "reference" else capture_job(args, out)


sys.exit(main())
