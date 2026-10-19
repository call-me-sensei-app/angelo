# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometry-nodes simulation bakes cached on disk as background jobs, on Blender's own bake job.

A saved project whose simulation moves a quad up 0.001 a frame. Checked on the
result: the published cache holds one file per frame, and the simulation reads
it (at frame 10 it stands where nine steps put it, which only a bake gives)
after the job, after the file is saved and reopened, and after the whole
project folder is copied elsewhere and opened there. Checked on the contract:
the scene references the cache by a path relative to the .blend and stores no
absolute path; an unsaved file and a workspace that is not the file's folder
are refused with what works; a cancel within the stop bound and a bake whose
frames could not be written remove what they staged, leave the bake settings
exactly as they were and the person's earlier cache untouched and in use;
the receipt identifies the cache; a second bake goes to a new folder and an
Alembic export job reads the baked motion.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_disk_simulation``; check ``disk-simulation.json`` for ``success``
and ``phase == "complete"``.
"""

import os
import shutil
import sys
import time
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
import long_jobs_fixtures as fx  # noqa: E402
import production_jobs_fixtures as pfx  # noqa: E402
from production_jobs_harness import Harness, ended, job_seconds  # noqa: E402
from angelo_agent import job_bakes, job_settings, job_ui  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

harness = Harness("ANGELO_DISK_SIMULATION", "disk-simulation.json", "/tmp/angelo-disk-simulation-qa", timeout=540)
ctx = harness.ctx
PROJECT, MOVED = harness.out / "project", harness.out / "moved project"
BAKED = 0.005  # Above this at frame 10 the simulation ran nine steps: it came from a bake.


def host():
    return bpy.data.objects["SimHost"]


def request(output, **fields):
    return dict({"kind": "BAKE_SIMULATION", "target": object_handle(host()), "output": output}, **fields)


def cache_files(folder):
    """``(meta files, blob files)`` of the one simulation cached below ``folder``."""
    files = pfx.files_below(folder)
    return (sum("/meta/" in name and name.endswith(".json") for name in files),
            sum("/blobs/" in name for name in files))


def no_absolute_path(obj):
    """No bake directory of the object names a place on this computer: each is empty or relative to the .blend."""
    paths = [path for settings in pfx.bake_settings_of(obj).values()
             for path in [settings["directory"]] + [bake[2] for bake in settings["bakes"]]]
    return all(not path or path.startswith("//") for path in paths)


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.simulation(scene, grid=50, frames=10)
        ctx["selection"] = pfx.selection_state(harness.window())
        message = harness.refused(request("caches/unsaved"))
        check("an_unsaved_file_is_refused_with_what_works", message is not None and "Save the file first" in message
              and "packed" in message)
        PROJECT.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / "shot.blend"))
        message = harness.refused(request("caches/elsewhere"))
        check("a_workspace_that_is_not_the_files_folder_is_refused", message is not None and "not the saved file's folder" in message)
        harness.rebind_workspace(PROJECT)  # As the app binds it for the next message once the file is saved.
        host().modifiers["Simulation"].bake_target = 'DISK'
        message = harness.refused({"kind": "BAKE_SIMULATION", "target": object_handle(host())})
        check("a_disk_bake_without_output_says_how", message is not None and "Pass output" in message)
        host().modifiers["Simulation"].bake_target = 'PACKED'
        ctx["before"] = pfx.bake_settings_of(host())
        ctx["a"], result = harness.start_job(request("caches/first"))
        approval = ctx["approvals"][ctx["a"]["id"]]
        harness.state["approval"] = approval
        check("approval_says_where_the_cache_goes_and_what_the_scene_reads", "Bakes to disk" in approval["body"]
              and "caches/first" in approval["body"] and "SimHost" in approval["body"]
              and "locked" in approval["warning"] and result["output"] == "caches/first")
        harness.go("a")
    elif phase == "a" and ended(ctx["a"]):
        record, bake_id = ctx["a"], host().modifiers["Simulation"].bakes[0].bake_id
        harness.state["receipt_a"] = record["receipt"]
        check("disk_bake_done", record["status"] == "done")
        check("cache_published_with_a_file_per_frame", cache_files(PROJECT / "caches/first") == (10, 10)
              and harness.hidden_left() == [])
        settings = pfx.bake_settings_of(host())
        harness.state["scene_references"] = settings
        check("scene_reads_the_cache_by_a_path_from_the_blend", settings["Simulation"]["target"] == 'DISK'
              and settings["Simulation"]["directory"] == "//caches/first/Simulation" and no_absolute_path(host()))
        check("the_simulation_plays_from_the_cache", pfx.simulated_height(host()) > BAKED)
        check("cache_identified_in_the_receipt", record["receipt"].get("outputs") == [
            {"file": "Simulation/%d" % bake_id, "cache": "Simulation/%d" % bake_id, "name": "SimHost", "frames": [1, 10]}])
        entry = next((item for item in harness.jobs_block() if item["job"] == record["id"]), {})
        check("jobs_block_carries_the_cache", entry.get("outputs") == record["receipt"]["outputs"]
              and entry.get("output") == "caches/first" and str(harness.out) not in str(entry))
        check("selection_and_frame_unchanged", pfx.selection_state(harness.window()) == ctx["selection"]
              and scene.frame_current == 1 and job_settings.TAG not in scene)
        ctx["first_files"] = pfx.file_hashes(PROJECT / "caches/first")
        bpy.ops.wm.save_mainfile()
        bpy.ops.wm.open_mainfile(filepath=str(PROJECT / "shot.blend"))
        harness.go("reopened")
        return 1.0
    elif phase == "reopened":
        check("the_cache_survives_reopening_the_file", pfx.simulated_height(host()) > BAKED
              and pfx.bake_settings_of(host())["Simulation"]["directory"] == "//caches/first/Simulation")
        shutil.copytree(PROJECT, MOVED)
        bpy.ops.wm.open_mainfile(filepath=str(MOVED / "shot.blend"))
        harness.go("moved")
        return 1.0
    elif phase == "moved":
        shutil.rmtree(PROJECT)  # Nothing may still be read from where the project was.
        check("the_cache_survives_moving_the_project", pfx.simulated_height(host()) > BAKED
              and no_absolute_path(host()) and pfx.file_hashes(MOVED / "caches/first") == ctx["first_files"])
        harness.rebind_workspace(MOVED)
        ctx["selection"] = pfx.selection_state(harness.window())
        # A second job on the result: an Alembic export of the baked motion, then a second bake into a new folder.
        ctx["abc"], _ = harness.start_job({"kind": "EXPORT_ALEMBIC", "output": "exports/baked.abc", "frame_start": 1,
                                           "frame_end": 10})
        harness.go("abc")
    elif phase == "abc" and ended(ctx["abc"]):
        check("an_export_job_reads_the_baked_simulation", ctx["abc"]["status"] == "done"
              and (MOVED / "exports/baked.abc").stat().st_size > 1000)
        # A bake into a taken cache folder is never refused and never overwrites it: it writes under the next free
        # name, which its approval states (job_output_names; test_native_job_free_names). The first cache's files
        # are checked unchanged after the second bake below.
        again = request("caches/first")
        message = harness.refused(again)
        card = harness.executor().precondition("angelo_job_start", again) if message is None else None
        writes = (getattr(card, "shown", None) or {}).get("writes")   # Shown on the card, never compared at the click.
        check("a_taken_cache_name_gets_the_next_free_one", message is None and str(writes or "").startswith("caches/first-j"))
        bpy.context.scene.frame_end = 6
        ctx["b"], _ = harness.start_job(request("caches/second"))
        harness.go("b")
    elif phase == "b" and ended(ctx["b"]):
        check("a_second_bake_goes_to_its_own_folder", ctx["b"]["status"] == "done"
              and cache_files(MOVED / "caches/second") == (6, 6)
              and pfx.bake_settings_of(host())["Simulation"]["directory"] == "//caches/second/Simulation"
              and pfx.file_hashes(MOVED / "caches/first") == ctx["first_files"])
        # Cancel: a simulation slow enough to stop mid-way. The earlier cache must stay in use afterwards.
        bpy.context.scene.frame_end = 250
        host().modifiers["Simulation"].node_group.nodes["Grid"].inputs["Vertices X"].default_value = 600
        host().modifiers["Simulation"].node_group.nodes["Grid"].inputs["Vertices Y"].default_value = 600
        ctx["before"], ctx["second_files"] = pfx.bake_settings_of(host()), pfx.file_hashes(MOVED / "caches/second")
        ctx["c"], _ = harness.start_job(request("caches/cancelled"))
        harness.go("c")
    elif phase == "c" and harness.running_with_progress(ctx["c"]):
        check("the_bake_writes_into_the_jobs_staging_only", str(MOVED / "caches" / ".angelo-job-") in
              host().modifiers["Simulation"].bake_directory and job_settings.TAG in bpy.context.scene)
        harness.cancel(ctx["c"])
        harness.go("c_end")
    elif phase == "c_end" and ended(ctx["c"]):
        harness.stopped("disk_simulation_bake")
        harness.progress_ok(ctx["c"], "disk_simulation_bake")
        record = ctx["c"]
        check("cancel_removes_the_partial_cache", record["status"] == "cancelled"
              and not (MOVED / "caches/cancelled").exists() and harness.hidden_left() == []
              and "partial cache was removed" in record["receipt"]["note"])
        check("cancel_leaves_the_bake_settings_as_they_were", pfx.bake_settings_of(host()) == ctx["before"]
              and job_settings.TAG not in bpy.context.scene)
        bpy.context.scene.frame_end = 6
        check("the_earlier_cache_is_untouched_and_in_use_again", pfx.file_hashes(MOVED / "caches/second") == ctx["second_files"]
              and pfx.simulated_height(host(), 6) > 0.004)
        check("selection_unchanged_after_cancel", pfx.selection_state(harness.window()) == ctx["selection"])
        # A bake whose frames cannot be written: the staging folder turns read-only while Blender bakes.
        host().modifiers["Simulation"].node_group.nodes["Grid"].inputs["Vertices X"].default_value = 50
        host().modifiers["Simulation"].node_group.nodes["Grid"].inputs["Vertices Y"].default_value = 50
        begin = job_bakes.SimulationBakeRunner._start

        def read_only_start(runner):
            os.chmod(runner.staging.path, 0o500)  # Before Blender's job exists: no frame can be written.
            begin(runner)
        job_bakes.SimulationBakeRunner._start = read_only_start
        try:
            ctx["d"], _ = harness.start_job(request("caches/unwritable"))
        finally:
            job_bakes.SimulationBakeRunner._start = begin
        runner = job_ui.manager().runners[ctx["d"]["id"]]
        staging, finish = runner.staging.path, runner.finish

        def writable_again(stopped):
            os.chmod(staging, 0o700)  # So the job can remove its own staging folder.
            return finish(stopped)
        runner.finish = writable_again
        harness.go("d")
    elif phase == "d" and ended(ctx["d"]):
        record = ctx["d"]
        harness.state["failure"] = {"status": record["status"], "reason": record["reason"], "receipt": record["receipt"]}
        check("a_bake_that_could_not_write_its_frames_is_never_done", record["status"] == "failed"
              and "before every frame was cached" in record["reason"] and not (MOVED / "caches/unwritable").exists()
              and harness.hidden_left() == [])
        check("failure_leaves_the_bake_settings_and_the_earlier_cache", pfx.bake_settings_of(host()) == ctx["before"]
              and pfx.file_hashes(MOVED / "caches/second") == ctx["second_files"]
              and pfx.simulated_height(host(), 6) > 0.004)
        # Blender alone: the same ten-frame bake to disk by its own operator, for the job's overhead.
        scene = bpy.context.scene
        scene.frame_end = 10
        modifier = host().modifiers["Simulation"]
        modifier.bake_directory = str(harness.out / "blender alone")
        ctx["raw_started"] = time.monotonic()
        with bpy.context.temp_override(window=harness.window(), selected_objects=[host()], active_object=host(),
                                       object=host()):
            bpy.ops.object.simulation_nodes_cache_bake('INVOKE_DEFAULT', selected=True)
        harness.go("raw")
    elif phase == "raw":
        from _bpy import _angelo_jobs
        if _angelo_jobs.progress(bpy.context.scene, "BAKE_GEOMETRY_NODES") is not None:
            return 0.02
        blender, job = round(time.monotonic() - ctx["raw_started"], 3), job_seconds(ctx["a"])
        harness.state["overhead_s"]["disk_simulation_bake"] = {"job": job, "blender_alone": blender,
                                                              "overhead": round(job - blender, 3)}
        check("blender_alone_writes_the_same_cache", cache_files(harness.out / "blender alone") == (10, 10))
        check("no_hidden_staging_left", harness.hidden_left() == [])
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
