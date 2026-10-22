# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Held-out Mantaflow bakes as jobs: scenes the fluid job was never developed against.

``test_blender_fluid_jobs`` was written beside the job; this suite is not. Its
scenes vary everything that fixture holds fixed, and every check is the job's
general contract, not a number of the fixture:

* **Fire**: a stretched, moved, non-uniformly scaled box (scale never applied),
  a torus burner of fire, noise on (a second cache), the All cache type, frames
  3 to 12 (not starting at 1), resolution 40.
* **Candle**: a 15 cm domain (a uniform scale of 0.075 on a 2 m box), smoke and
  fire from a tiny sphere, the Modular cache type, a modifier and objects named
  with spaces, accents and CJK, resolution 32.
* **Pool**: a liquid domain left in Blender's default Replay type, with mesh,
  speed vectors and spray particles on (data, mesh and
  particle caches), resolution 44.

For each: the approval's grid equals the resolution Blender itself computes for
the domain once it has baked (``domain_resolution`` of the evaluated domain); the job is done; the
published folder holds a non-empty file for every frame of every cache the domain
writes (``job_fluid_plan.missing``, Mantaflow's own names); the domain reads it by
a path from the .blend; a Replay domain became All; the baked frames play (gas
density grows; the liquid has a surface mesh and a mesh file per frame).
Two refusals are held out too: a scene whose only flow of the domain's kind takes
fluid out, and a domain disabled in the viewport. The project folder's name has a
space, an em dash and an umlaut.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_fluid_varied``; check ``fluid-varied.json`` for ``success`` and
``phase == "complete"``. A build without Mantaflow or the fluid job records the
skip and succeeds.
"""

import sys
from pathlib import Path

import addon_utils
import bpy

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_jobs_harness import Harness, ended  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402
try:
    from angelo_agent import job_fluid_plan as plan  # noqa: E402
except ImportError:   # A build without the fluid job: the suite records the skip.
    plan = None

harness = Harness("ANGELO_FLUID_VARIED", "fluid-varied.json", "/tmp/angelo-fluid-varied-qa", timeout=900)
ctx = harness.ctx
PROJECT = harness.out / "Shot 042 — Küche"

CASES = {
    "fire": dict(domain="Hood Air Volume", flow="Burner Ring", kind="GAS", flow_type="FIRE", cache="ALL",
                 resolution=40, frames=(3, 12), output="sims/hood fire", noise=True),
    "candle": dict(domain="Fumée 煙 Domaine", flow="Mèche ☆", kind="GAS", flow_type="BOTH",
                   cache="MODULAR", resolution=32, frames=(1, 8), output="sims/candle",
                   modifier="Rauch Sim ☁"),
    "pool": dict(domain="Pool Volume", flow="Bucket Pour", kind="LIQUID", flow_type="LIQUID", cache="REPLAY",
                 resolution=44, frames=(1, 6), output="sims/pool"),
}


def build(case):
    spec = CASES[case]
    if case == "fire":
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(5.0, -3.0, 1.8))
        domain = bpy.context.object
        domain.scale = (2.5, 1.2, 1.8)
        bpy.ops.mesh.primitive_torus_add(location=(5.0, -3.0, 0.4), major_radius=0.5, minor_radius=0.08)
    elif case == "pool":
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-6.0, 4.0, 0.75))
        domain = bpy.context.object
        domain.scale = (4.0, 2.0, 1.5)
        bpy.ops.mesh.primitive_ico_sphere_add(radius=0.25, location=(-6.5, 4.0, 1.2))
    else:
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 9.0, 0.075))
        domain = bpy.context.object
        domain.scale = (0.075, 0.075, 0.075)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.012, location=(0.0, 9.0, 0.03))
    flow = bpy.context.object
    domain.name, flow.name = spec["domain"], spec["flow"]
    modifier = domain.modifiers.new(spec.get("modifier", "Fluid"), "FLUID")
    modifier.fluid_type = "DOMAIN"
    settings = modifier.domain_settings
    settings.domain_type = spec["kind"]
    settings.resolution_max = spec["resolution"]
    settings.cache_type = spec["cache"]
    settings.cache_frame_start, settings.cache_frame_end = spec["frames"]
    if spec.get("noise"):
        settings.use_noise, settings.noise_scale = True, 2
    if spec["kind"] == "LIQUID":
        settings.use_mesh, settings.use_speed_vectors, settings.use_spray_particles = True, True, True
    flow_modifier = flow.modifiers.new("Fluid", "FLUID")
    flow_modifier.fluid_type = "FLOW"
    flow_modifier.flow_settings.flow_type = spec["flow_type"]
    flow_modifier.flow_settings.flow_behavior = "INFLOW"
    return domain


def fluid(obj):
    return next(modifier for modifier in obj.modifiers if modifier.type == "FLUID")


def staged_sizes(folder):
    folder = Path(folder)
    return {path.relative_to(folder).as_posix(): path.stat().st_size for path in folder.rglob("*") if path.is_file()}


def density(domain, frame):
    bpy.context.scene.frame_set(frame)
    return sum(fluid(domain.evaluated_get(bpy.context.evaluated_depsgraph_get())).domain_settings.density_grid)


def plays(domain, spec):
    first, last = spec["frames"]
    if spec["kind"] == "LIQUID":
        bpy.context.scene.frame_set(last)
        return len(domain.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices) > 50
    late, early = density(domain, last), density(domain, first + 1)
    return late > early >= 0.0 and late > 0.0


def start(case):
    spec = CASES[case]
    domain = bpy.data.objects[spec["domain"]]
    ctx[case], _ = harness.start_job({"kind": "BAKE_SIMULATION", "target": object_handle(domain),
                                      "output": spec["output"]})
    ctx[case + "_facts"] = ctx["facts"][ctx[case]["id"]]
    harness.go(case)


def finish_case(case, check):
    spec, record = CASES[case], ctx[case]
    domain, facts = bpy.data.objects[spec["domain"]], ctx[case + "_facts"]
    report = harness.state.setdefault("cases", {})[case] = {"status": record["status"], "reason": record["reason"],
                                                            "facts": facts, "receipt": record["receipt"]}
    check(case + "_done", record["status"] == "done")
    settings = fluid(domain).domain_settings
    # Blender sets the grid on the evaluated domain when it simulates; the original object reads zeros.
    bpy.context.scene.frame_set(spec["frames"][0])
    evaluated = fluid(domain.evaluated_get(bpy.context.evaluated_depsgraph_get())).domain_settings
    report["blender_resolution"] = list(evaluated.domain_resolution)
    check(case + "_grid_is_blenders", list(facts["cells"]) == report["blender_resolution"])
    outputs = record["receipt"].get("outputs") or [{}]
    folder = PROJECT / spec["output"] / outputs[0].get("file", "?")
    expected = plan.expected_files(facts["cache_kinds"], tuple(spec["frames"]))
    report["missing"] = plan.missing(expected, staged_sizes(folder))
    minimum = 2 if spec.get("noise") else 3 if spec["kind"] == "LIQUID" else 1
    check(case + "_every_frame_of_every_cache_published", not report["missing"]
          and len(facts["cache_kinds"]) >= minimum)
    check(case + "_read_by_a_path_from_the_blend", settings.cache_directory.startswith("//")
          and (PROJECT / settings.cache_directory[2:]).resolve() == folder.resolve()
          and settings.cache_type != "REPLAY")
    check(case + "_plays_the_baked_frames", plays(domain, spec))
    if spec["kind"] == "LIQUID":   # Blender 5.2 keeps a liquid's speed vectors in its mesh files: one per frame.
        report["mesh_files"] = sorted(path.name for path in (folder / "mesh").iterdir())
        check(case + "_a_mesh_file_per_frame", len(report["mesh_files"]) == spec["frames"][1] - spec["frames"][0] + 1)
    check(case + "_no_hidden_staging", harness.hidden_left() == [])


def step(harness):
    phase, check = harness.state["phase"], harness.check
    if phase == "boot":
        try:
            from _bpy import _angelo_jobs
            available = plan is not None and bpy.app.build_options.fluid and "FLUID" in _angelo_jobs.JOB_TYPES
        except ImportError:
            available = False
        if not available:
            harness.state["skipped"] = "Mantaflow or the fluid job is not in this build"
            check("fluid_job_absence_recorded", True)
            harness.finish()
            return None
        scene = bpy.context.scene
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj)
        scene.frame_start, scene.frame_end = 1, 12
        for case in CASES:
            build(case)
        PROJECT.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / "shot 042.blend"))
        harness.rebind_workspace(PROJECT)
        # Held out: no gas flow that adds fluid (the burner only takes it out, the candle's is disabled).
        burner, wick = fluid(bpy.data.objects["Burner Ring"]), fluid(bpy.data.objects[CASES["candle"]["flow"]])
        burner.flow_settings.flow_behavior, wick.show_viewport = "OUTFLOW", False
        message = harness.refused({"kind": "BAKE_SIMULATION", "output": "sims/none",
                                   "target": object_handle(bpy.data.objects["Hood Air Volume"])})
        check("a_scene_with_no_flow_adding_fluid_is_refused", message is not None and "flow" in message.lower())
        burner.flow_settings.flow_behavior, wick.show_viewport = "INFLOW", True
        # Held out: a domain the viewport does not evaluate.
        pool = fluid(bpy.data.objects["Pool Volume"])
        pool.show_viewport = False
        message = harness.refused({"kind": "BAKE_SIMULATION", "output": "sims/none",
                                   "target": object_handle(bpy.data.objects["Pool Volume"])})
        check("a_domain_disabled_in_the_viewport_is_refused", message is not None and "viewport" in message)
        pool.show_viewport = True
        start("fire")
    elif phase in CASES and ended(ctx[phase]):
        finish_case(phase, check)
        order = list(CASES)
        if order.index(phase) + 1 < len(order):
            start(order[order.index(phase) + 1])
        else:
            harness.finish()
            return None
    return 0.2


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
