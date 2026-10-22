# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mantaflow bakes as background jobs (``BAKE_SIMULATION`` with a fluid domain), through the real executor.

A saved project with two smoke domains, each with its own flow: "Smoke", fresh
and in Blender's default Replay cache type, and "Earlier", whose person already
baked a cache into their own folder. Checked:

* refusals before anything runs: an unsaved file, no output, a folder name
  Mantaflow would rewrite, a scene with no flow of the domain's kind;
* a finished bake: the approval states the grid and the Replay change; a data
  file for every frame in the published cache; the domain reads it by a path from
  the .blend (also after saving and reopening), its type is All, the state tag is
  gone, nothing hidden is left, the jobs block identifies the cache;
* a cancel through the job's own stop (``_angelo_jobs.stop`` must set
  ``G.is_break``, the only flag Mantaflow checks) within the stop bound: the
  domain's whole cache state is exactly as before, the person's earlier cache is
  byte-identical and still read, nothing was published;
* a file saved with a running bake's state opens with the domain put back
  (``restore_tags``; planted, since Blender itself never saves during a bake),
  and malformed tags in a file are dropped harmlessly;
* a publication that fails after a whole bake still puts the domain back;
* a Replay domain the person played: a cancel puts back its state and the frame,
  and its replay cache survives the cancel and playback from the start frame
  (a user-edit depsgraph tag would have Blender free it there);
* a bake whose remaining frames would not fit on the disk stops itself and says
  so (free space reported low by a stand-in once two frames are measured).

A window is required: run through ``run_native_suites.py --gui --include
test_blender_fluid_jobs``; check ``fluid-jobs.json`` for ``success`` and
``phase == "complete"``. A build without Mantaflow or without the fluid job
records the skip and succeeds.
"""

import shutil
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
import production_jobs_fixtures as pfx  # noqa: E402
from production_jobs_harness import Harness, ended  # noqa: E402
from angelo_agent import job_ui  # noqa: E402
try:
    from angelo_agent import job_fluid, job_fluid_plan as plan  # noqa: E402
except ImportError:   # A build without the fluid job: the suite records the skip.
    job_fluid = plan = None
from angelo_agent.scene_queries import object_handle  # noqa: E402

harness = Harness("ANGELO_FLUID_JOBS", "fluid-jobs.json", "/tmp/angelo-fluid-jobs-qa", timeout=720)
ctx = harness.ctx
PROJECT = harness.out / "project"
FRAMES = 12


def domain(name):
    return bpy.data.objects[name]


def state(name):
    from _bpy import _angelo_jobs
    return _angelo_jobs.fluid_state(domain(name))


def smoke(name, x, cache_type, directory, frames):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(a, b, c) for a in (-1, 1) for b in (-1, 1) for c in (0, 2)], [],
                     [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    box = bpy.data.objects.new(name, mesh)
    box.location.x = x
    bpy.context.scene.collection.objects.link(box)
    box.modifiers.new("Fluid", 'FLUID').fluid_type = 'DOMAIN'
    settings = box.modifiers["Fluid"].domain_settings
    settings.domain_type, settings.resolution_max = 'GAS', 24
    settings.cache_type = cache_type
    settings.cache_directory = directory
    settings.cache_frame_start, settings.cache_frame_end = 1, frames
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(x, 0, 0.5))
    flow = bpy.context.object
    flow.name = name + " Flow"
    flow.modifiers.new("Fluid", 'FLUID').fluid_type = 'FLOW'
    flow.modifiers["Fluid"].flow_settings.flow_type = 'SMOKE'
    flow.modifiers["Fluid"].flow_settings.flow_behavior = 'INFLOW'


def density(name, frame):
    bpy.context.scene.frame_set(frame)
    evaluated = domain(name).evaluated_get(bpy.context.evaluated_depsgraph_get())
    return sum(evaluated.modifiers["Fluid"].domain_settings.density_grid)


def request(name, output=None):
    value = {"kind": "BAKE_SIMULATION", "target": object_handle(domain(name))}
    return dict(value, output=output) if output else value


def data_frames(folder):
    return sorted({plan.file_frame(path.name, "fluid_data") for path in Path(folder, "data").glob("fluid_data_*")})


def flows_shown(shown):
    for name in ("Smoke Flow", "Earlier Flow"):
        bpy.data.objects[name].modifiers["Fluid"].show_viewport = shown


def step(harness):
    phase, check = harness.state["phase"], harness.check
    if phase == "boot":
        try:
            from _bpy import _angelo_jobs
            available = job_fluid is not None and bpy.app.build_options.fluid and "FLUID" in _angelo_jobs.JOB_TYPES
        except ImportError:
            available = False
        if not available:
            harness.state["skipped"] = "Mantaflow or the fluid job is not in this build"
            check("fluid_job_absence_recorded", True)
            harness.finish()
            return None
        scene = bpy.context.scene
        for obj in [obj for obj in scene.objects if obj.type == 'MESH']:
            bpy.data.objects.remove(obj)
        scene.frame_start, scene.frame_end, scene.frame_current = 1, FRAMES, 1
        # Every domain names a cache folder, as Blender gives each new one: an empty path would make Replay write
        # its frames beside the process's working directory.
        smoke("Smoke", 0.0, 'REPLAY', "//smoke replay", FRAMES)
        smoke("Earlier", 10.0, 'ALL', "//person cache", 6)
        message = harness.refused(request("Smoke", "caches/unsaved"))
        check("an_unsaved_file_is_refused", message is not None and "Save the file first" in message)
        PROJECT.mkdir(exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / "fluid.blend"))
        harness.rebind_workspace(PROJECT)
        # The person's own earlier bake, by Blender's operator (synchronous here), into their own folder.
        with bpy.context.temp_override(window=harness.window(), active_object=domain("Earlier"),
                                       object=domain("Earlier"), selected_objects=[domain("Earlier")]):
            bpy.ops.fluid.bake_all()
        ctx["person_files"] = pfx.file_hashes(PROJECT / "person cache")
        check("the_persons_earlier_cache_exists", data_frames(PROJECT / "person cache") == list(range(1, 7)))
        domain("Earlier").modifiers["Fluid"].domain_settings.cache_frame_end = 600   # Long enough to cancel.
        ctx["earlier_state"] = state("Earlier")
        message = harness.refused(request("Smoke"))
        check("a_fluid_bake_without_output_says_how", message is not None and "pass output" in message)
        message = harness.refused(request("Smoke", "caches/bad:name"))
        check("a_folder_name_mantaflow_rewrites_is_refused", message is not None and "rewrites" in message)
        flows_shown(False)
        message = harness.refused(request("Smoke", "caches/noflow"))
        flows_shown(True)
        check("a_domain_with_no_flow_is_refused", message is not None and "No smoke or fire flow" in message)
        ctx["a"], result = harness.start_job(request("Smoke", "caches/smoke"))
        approval = ctx["approvals"][ctx["a"]["id"]]
        harness.state["approval"] = approval
        check("approval_states_the_grid_and_the_replay_change", "smoke" in approval["title"].lower()
              and "24×24×24 cells" in approval["body"] and "Replay to All" in approval["body"]
              and "caches/smoke" in approval["body"] and result["output"] == "caches/smoke")
        harness.go("a")
    elif phase == "a" and not ended(ctx["a"]):
        if ctx["a"]["status"] == "running" and "a_writes_into_staging_only" not in harness.state["checks"]:
            check("a_writes_into_staging_only", ".angelo-job-" in state("Smoke")["directory"]
                  and "_angelo_fluid_restore" in domain("Smoke"))
    elif phase == "a":
        record = ctx["a"]
        harness.state["receipt_a"] = record["receipt"]
        harness.progress_ok(record, "fluid_bake")
        check("fluid_bake_done", record["status"] == "done")
        frames = data_frames(PROJECT / "caches/smoke/Fluid")
        harness.state["published_frames"] = frames
        check("a_data_file_per_frame_published", frames == list(range(1, FRAMES + 1)) and harness.hidden_left() == [])
        after = state("Smoke")
        harness.state["state_after"] = after
        check("the_domain_reads_the_cache_by_a_path_from_the_blend", after["directory"] == "//caches/smoke/Fluid"
              and after["cache_type"] == 2 and "_angelo_fluid_restore" not in domain("Smoke"))
        check("the_baked_frames_play", density("Smoke", FRAMES) > density("Smoke", 2) > 0.0)
        entry = next((item for item in harness.jobs_block() if item["job"] == record["id"]), {})
        check("jobs_block_identifies_the_cache", entry.get("output") == "caches/smoke" and entry.get("outputs") == [
            {"file": "Fluid", "cache": "Fluid", "name": "Smoke", "frames": [1, FRAMES]}]
              and str(harness.out) not in str(entry))
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=str(PROJECT / "fluid.blend"))
        harness.go("reopened")
        return 1.0
    elif phase == "reopened":
        check("the_cache_survives_reopening", density("Smoke", FRAMES) > 0.0
              and state("Smoke")["directory"] == "//caches/smoke/Fluid")
        harness.rebind_workspace(PROJECT)
        ctx["earlier_state"] = state("Earlier")   # As Blender reads it back from the file.
        ctx["c"], _ = harness.start_job(request("Earlier", "caches/cancelled"))
        harness.go("c")
    elif phase == "c" and harness.running_with_progress(ctx["c"]):
        if state("Earlier")["pause"][0] >= 3:
            harness.cancel(ctx["c"])   # job_manager -> runner.stop -> _angelo_jobs.stop(scene, "FLUID").
            harness.go("c_end")
    elif phase == "c_end" and ended(ctx["c"]):
        harness.stopped("fluid_bake")   # Only G.is_break stops Mantaflow: a stop that missed it times out here.
        record = ctx["c"]
        check("cancel_publishes_nothing", record["status"] == "cancelled" and not (PROJECT / "caches/cancelled").exists()
              and harness.hidden_left() == [])
        check("cancel_puts_the_domain_state_back_exactly", state("Earlier") == ctx["earlier_state"]
              and "_angelo_fluid_restore" not in domain("Earlier"))
        check("the_persons_cache_is_untouched_and_read", pfx.file_hashes(PROJECT / "person cache") == ctx["person_files"]
              and density("Earlier", 6) > 0.0)
        # A file saved while a bake ran (never by Blender itself: its interface is locked and autosave waits for
        # jobs, so a save comes only from a script): the domain carries the job's state and the person's in the tag.
        from _bpy import _angelo_jobs
        import json
        domain("Earlier")[job_fluid.TAG] = json.dumps({"job": "j999999", "state": ctx["earlier_state"],
                                                       "blend": bpy.data.filepath})
        _angelo_jobs.fluid_state_set(domain("Earlier"), (2, 0, (0, 0, 0, 0, 0), str(PROJECT / ".angelo-job-j999999")))
        ctx["saved_copy"] = str(PROJECT / "saved mid job.blend")
        bpy.ops.wm.save_as_mainfile(filepath=ctx["saved_copy"], copy=True)
        bpy.ops.wm.open_mainfile(filepath=ctx["saved_copy"])
        harness.go("d_open")
        return 1.0
    elif phase == "d_open":
        check("a_file_saved_mid_bake_opens_with_the_domain_put_back", state("Earlier") == ctx["earlier_state"]
              and "_angelo_fluid_restore" not in domain("Earlier"))
        harness.rebind_workspace(PROJECT)
        # A file is untrusted data: malformed tags are dropped, never stop the other objects or the load handler.
        smoke_state = state("Smoke")
        for value in ("[]", "1", "{}", "not json", '{"state": 3}'):
            domain("Smoke")[job_fluid.TAG] = value
            bpy.data.objects["Smoke Flow"][job_fluid.TAG] = value
            job_fluid.restore_tags()
        check("malformed_tags_are_dropped_harmlessly", state("Smoke") == smoke_state
              and job_fluid.TAG not in domain("Smoke") and job_fluid.TAG not in bpy.data.objects["Smoke Flow"])
        # Publication fails after a whole bake: the domain must still be put back exactly.
        ctx["smoke_state"], ctx["frame_before"] = state("Smoke"), bpy.context.scene.frame_current
        ctx["p"], _ = harness.start_job(request("Smoke", "caches/unpublishable"))
        runner = job_ui.manager().runners[ctx["p"]["id"]]

        def refuse_publication():
            raise OSError(1, "Operation not permitted", "stand-in for a workspace without hard links")
        runner.staging.publish_folder = refuse_publication
        harness.go("p")
    elif phase == "p" and ended(ctx["p"]):
        record = ctx["p"]
        harness.state["publication_failure"] = {"status": record["status"], "reason": record["reason"]}
        check("a_failed_publication_puts_the_domain_back", record["status"] == "failed"
              and state("Smoke") == ctx["smoke_state"] and "_angelo_fluid_restore" not in domain("Smoke")
              and not (PROJECT / "caches/unpublishable").exists() and harness.hidden_left() == [])
        # A Replay domain whose person played it: its replay cache must survive a cancel and the next playback.
        smoke("Replay", 20.0, 'REPLAY', "//replay cache", 400)
        for frame in range(1, 6):
            bpy.context.scene.frame_set(frame)
        bpy.context.scene.frame_set(3)
        ctx["replay_files"] = pfx.file_hashes(PROJECT / "replay cache") if (PROJECT / "replay cache").exists() else {}
        ctx["replay_state"], ctx["frame_before"] = state("Replay"), bpy.context.scene.frame_current
        harness.state["replay_cache_files"] = len(ctx["replay_files"])
        ctx["r"], _ = harness.start_job(request("Replay", "caches/replay"))
        harness.go("r")
    elif phase == "r" and harness.running_with_progress(ctx["r"]):
        if state("Replay")["pause"][0] >= 3:
            harness.cancel(ctx["r"])
            harness.go("r_end")
    elif phase == "r_end" and ended(ctx["r"]):
        check("the_frame_is_put_back_after_a_cancel", bpy.context.scene.frame_current == ctx["frame_before"])
        check("a_replay_domain_is_put_back", state("Replay") == ctx["replay_state"])
        bpy.context.scene.frame_set(1)   # The start frame: where an invalidated Replay cache would be freed.
        bpy.context.scene.frame_set(2)
        after = pfx.file_hashes(PROJECT / "replay cache") if (PROJECT / "replay cache").exists() else {}
        check("the_persons_replay_cache_survives_cancel_and_playback", bool(ctx["replay_files"])
              and all(after.get(name) == digest for name, digest in ctx["replay_files"].items()))
        ctx["patched"] = (job_fluid._free_space, job_fluid.DISK_CHECK_SECONDS)
        ctx["e"], _ = harness.start_job(request("Earlier", "caches/full-disk"))
        job_fluid.DISK_CHECK_SECONDS = 0.0
        job_fluid._free_space = lambda _path: plan.DISK_MARGIN + 1   # Too little for the frames left.
        harness.go("e")
    elif phase == "e" and ended(ctx["e"]):
        job_fluid._free_space, job_fluid.DISK_CHECK_SECONDS = ctx.pop("patched")
        record = ctx["e"]
        harness.state["low_disk"] = {"status": record["status"], "reason": record["reason"], "receipt": record["receipt"]}
        check("a_bake_that_would_fill_the_disk_stops_itself", record["status"] == "failed"
              and "disk would have filled" in record["reason"] and record["receipt"].get("disk_needed_gib") is not None
              and not (PROJECT / "caches/full-disk").exists() and harness.hidden_left() == [])
        check("and_puts_the_domain_back", state("Earlier") == ctx["earlier_state"])
        shutil.rmtree(PROJECT / "caches", ignore_errors=True)
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
