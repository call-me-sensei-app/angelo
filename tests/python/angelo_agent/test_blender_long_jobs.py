# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background jobs on Blender's own window-manager jobs: progress, cancel, restore and publishing.

Every job kind runs for real on tiny scenes (Cycles on the CPU): a still and an
animation render, a queued render, a texture bake, physics and simulation bakes,
USD and Alembic exports. Checked: progress stays in 0..1 and only grows, an ETA
appears, a cancel stops at the next safe point (its latency is recorded and
bounded), a cancelled animation keeps whole frames only, a cancelled texture
bake puts the image back, a cancelled physics bake removes only its own bakes,
the time budget stops a job, settings a job applied are restored (also from a
file saved while it ran), a queued request is measured on the person's own
settings, the scene guard refuses edits (and reads while Blender is locked),
outputs are new files in the workspace and nothing hidden is left behind, a
file load interrupts a running job and a crash leaves an interrupted record.
Agent requests go through the real executor (``BlenderExecutor`` and
``job_host``) with a stand-in conversation: no provider, account or network.

A window is required (window-manager jobs run from the event loop): run through
``run_native_suites.py --gui --include test_blender_long_jobs``; check
``long-jobs.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-long-jobs-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
OUTSIDE = OUT / "outside"  # Beside the workspace: no job may write here.
OUTSIDE.mkdir(exist_ok=True)
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_jobs_fixtures as fx  # noqa: E402
from angelo_agent import job_host, job_outputs, job_runners, job_settings, job_ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

REPORT = OUT / "long-jobs.json"
MAX_STOP_SECONDS = 15.0  # Cycles stops between samples; physics, simulation and exports between frames.
state = {"phase": "boot", "success": False, "checks": {}, "stop_latency_s": {}, "progress": {},
         "fixture": "Real Blender jobs on tiny scenes; stand-in conversation, no provider, account or network"}
ctx = {"samples": {}}
started = time.monotonic()
controller = SimpleNamespace(files=FileWorkspace(WS), state={"id": "suite-chat"},
                             executing_call=SimpleNamespace(id="call-1"))


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context = controller
    return value


def check(name, value):
    state["checks"][name] = bool(value)
    if not value:
        print("ANGELO_LONG_JOBS check failed:", name, flush=True)


def start_job(arguments, *, call="call-1"):
    """Approve-and-run as the controller does: precondition (approval facts), then execute."""
    controller.executing_call = SimpleNamespace(id=call)
    facts = executor().precondition("angelo_job_start", arguments)
    result = executor().execute("angelo_job_start", arguments)
    record = job_ui.manager().record(result["job"])
    ctx.setdefault("facts", {})[record["id"]] = json.loads(facts[2])
    ctx["started"] = ctx.get("started", 0) + 1
    return record, result


def ended(record):
    return record["status"] in {"done", "failed", "cancelled", "interrupted"}


def cancel(record, by="user"):
    ctx["cancel_at"] = time.monotonic()
    job_ui.manager().cancel(record["id"], by=by, session=None if by == "user" else "suite-chat")


def stopped(record, label):
    seconds = round(time.monotonic() - ctx["cancel_at"], 3)
    state["stop_latency_s"][label] = seconds
    check(label + "_stopped_within_bound", seconds < MAX_STOP_SECONDS)


def sample():
    record = job_ui.manager().active()
    if record is not None:
        ctx["samples"].setdefault(record["id"], []).append((round(record["progress"], 4), record["eta_s"]))


def progress_ok(record, label):
    values = [value for value, _ in ctx["samples"].get(record["id"], [])]
    state["progress"][label] = {"samples": len(values), "max": max(values, default=None),
                                "eta_seen": any(eta is not None for _, eta in ctx["samples"].get(record["id"], []))}
    check(label + "_progress_in_range_and_growing", all(0.0 <= value <= 1.0 for value in values)
          and all(b >= a for a, b in zip(values, values[1:])))


def running_with_progress(key):
    """True once the job runs with some progress; a job that ended first fails the phase (it was too fast)."""
    record = ctx[key]
    if ended(record):
        raise AssertionError("%s (%s) ended before it could be cancelled: %s" % (key, record["id"], record["status"]))
    return record["status"] == "running" and record["progress"] > 0.0


def hidden_left():
    return sorted(str(path.relative_to(WS)) for path in WS.rglob(".angelo-job-*"))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(phase="failed", success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_LONG_JOBS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def go(phase):
    state["phase"] = phase
    ctx["entered"] = time.monotonic()


def step():
    assert time.monotonic() - started < 420, "Long-jobs fixture timed out in " + state["phase"]
    sample()
    phase, jobs, scene = state["phase"], job_ui.manager(), bpy.context.scene
    if phase == "boot":
        fx.cycles_scene(scene)
        ctx["still"], result = start_job({"kind": "RENDER_STILL", "output": "renders/still"})
        check("start_returns_at_once_with_a_job_id", result["job"] == "j1" and result["status"] == "running"
              and "do not sleep or poll" in result["note"] and result["output"] == "renders/still.png")
        check("render_start_explains_the_bounded_view_wait",
              "call angelo_view with shading RENDERED and image j1" in result["note"]
              and "that call waits for the job, within bounds" in result["note"])
        go("still")
    elif phase == "still" and ended(ctx["still"]):
        record = ctx["still"]
        check("still_done_and_published", record["status"] == "done" and fx.is_png(WS / "renders/still.png", 64, 64))
        # The device its Cycles session really used, read as the session began (render_device_session): the
        # scene chose the CPU, so the card says so plainly and the model reads why.
        check("still_records_the_device_its_session_used",
              record["detail"].get("device_shown") == {"gpu": False, "reason": "scene"}
              and record["detail"].get("device") == "CPU" and "cycles.device" in record["detail"].get("device_why", ""))
        check("still_restored_settings", scene.render.filepath == "/tmp/" and job_settings.TAG not in scene)
        check("still_open_path_recorded", record["detail"]["output_path"] == str(WS / "renders/still.png"))
        # Long enough on an idle machine for an ETA (the manager estimates after 3 s and 2%).
        ctx["anim"], _ = start_job({"kind": "RENDER_ANIMATION", "output": "renders/anim", "frame_start": 1,
                                    "frame_end": 12, "samples": 4096, "resolution": [160, 160]})
        ctx["queued"], queued = start_job({"kind": "RENDER_STILL", "output": "renders/queued.png", "queue": True},
                                          call="call-2")
        check("queued_request_waits_with_its_position", queued["status"] == "queued" and queued["position"] == 1)
        check("queued_facts_are_the_persons_settings",
              ctx["facts"]["j3"]["resolution"] == [64, 64] and ctx["facts"]["j3"]["samples"] == 16)
        try:
            executor().precondition("angelo_job_start", {"kind": "RENDER_STILL", "output": "renders/third.png"})
            check("second_unqueued_request_refused", False)
        except Exception as error:
            check("second_unqueued_request_refused", "queue=true" in str(error))
        try:
            executor().precondition("angelo_create_cube", {"name": "Blocked", "size": 1, "location": [0, 0, 0]})
            check("scene_edit_refused_beside_a_render", False)
        except job_host.JobBusyError as error:
            check("scene_edit_refused_beside_a_render", "j2" in str(error))
        executor().execute("angelo_scene_search", {"query": "Cube"})
        check("scene_read_allowed_beside_a_render", True)
        go("anim")
    elif phase == "anim" and running_with_progress("anim") and ctx["anim"]["detail"].get("frames_done", 0) >= 1 \
            and ctx["anim"]["eta_s"] is not None:
        check("animation_applies_its_settings_only_while_it_runs",
              scene.cycles.samples == 4096 and scene.render.resolution_x == 160 and job_settings.TAG in scene)
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "saved-during-job.blend"), copy=True)
        result = executor().execute("angelo_job_cancel", {"job": ctx["anim"]["id"]})
        ctx["cancel_at"] = time.monotonic()
        check("agent_cancel_answers_at_once", (result["job"], result["status"], result["scene_changed"])
              == ("j2", "stopping", False))
        go("anim_end")
    elif phase == "anim_end" and ended(ctx["anim"]):
        stopped(ctx["anim"], "render_animation")
        record, folder = ctx["anim"], WS / "renders/anim"
        frames = sorted(os.listdir(folder)) if folder.is_dir() else []
        check("cancelled_animation_keeps_whole_frames", record["status"] == "cancelled" and record["reason"] == "agent"
              and frames and frames == ["frame_%04d.png" % n for n in range(1, len(frames) + 1)]
              and all(fx.is_png(folder / name, 160, 160) for name in frames)
              and record["receipt"]["kept"] == "%d/12 frames" % len(frames))
        progress_ok(record, "render_animation")
        check("render_eta_reported", state["progress"]["render_animation"]["eta_seen"])
        go("queued_end")
    elif phase == "queued_end" and ended(ctx["queued"]):
        record = ctx["queued"]
        check("queued_job_ran_on_restored_settings", record["status"] == "done" and record["detail"]["resolution"] == [64, 64]
              and fx.is_png(WS / "renders/queued.png", 64, 64))
        check("settings_restored_after_both", scene.cycles.samples == 16 and scene.render.resolution_x == 64
              and job_settings.TAG not in scene and hidden_left() == [])
        ctx["budget"], _ = start_job({"kind": "RENDER_ANIMATION", "output": "renders/budget", "frame_start": 1,
                                      "frame_end": 40, "samples": 4096, "resolution": [160, 160], "minutes": 0.1})
        go("budget")
    elif phase == "budget" and ended(ctx["budget"]):
        record = ctx["budget"]
        seconds = record["ended"] - record["started"]
        check("time_budget_stops_the_job", record["status"] == "cancelled" and record["reason"] == "time_budget"
              and 6.0 <= seconds < 6.0 + MAX_STOP_SECONDS)
        plane, image = fx.bake_plane(scene)
        ctx["plane"], ctx["image"], ctx["pixels"] = plane, image, fx.pixels(image)
        scene.render.engine = 'BLENDER_EEVEE'
        ctx["bake"], _ = start_job({"kind": "BAKE_TEXTURE", "target": object_handle(plane), "samples": 2048})
        check("texture_bake_facts", ctx["facts"][ctx["bake"]["id"]] == {
            "images": ["BakeTarget"], "samples": 2048, "engine": "CYCLES", "restorable": True})
        go("bake")
    elif phase == "bake" and running_with_progress("bake"):
        check("texture_bake_runs_on_cycles", scene.render.engine == 'CYCLES')
        cancel(ctx["bake"])
        go("bake_end")
    elif phase == "bake_end" and ended(ctx["bake"]):
        stopped(ctx["bake"], "texture_bake")
        progress_ok(ctx["bake"], "texture_bake")
        check("cancelled_bake_puts_the_image_back", ctx["bake"]["status"] == "cancelled"
              and fx.pixels(ctx["image"]) == ctx["pixels"] and "put back" in ctx["bake"]["receipt"]["note"])
        check("bake_restores_the_engine", scene.render.engine == 'BLENDER_EEVEE' and scene.cycles.samples == 16)
        ctx["bake2"], _ = start_job({"kind": "BAKE_TEXTURE", "target": object_handle(ctx["plane"]), "samples": 4,
                                     "output": "baked"})
        go("bake2")
    elif phase == "bake2" and ended(ctx["bake2"]):
        record = ctx["bake2"]
        check("finished_bake_changes_the_image_and_saves_a_copy", record["status"] == "done"
              and fx.pixels(ctx["image"]) != ctx["pixels"] and fx.is_png(WS / "baked/BakeTarget.png", 128, 128))
        scene.render.engine = 'CYCLES'
        ctx["kept_cache"] = fx.prebaked_softbody(scene)
        ctx["cloth"] = fx.cloth(scene, 250)
        ctx["physics"], _ = start_job({"kind": "BAKE_PHYSICS"})
        check("physics_facts_count_only_unbaked_caches", ctx["facts"][ctx["physics"]["id"]] == {"caches": 1, "kept": 1})
        go("physics")
    elif phase == "physics" and running_with_progress("physics"):
        try:
            executor().precondition("angelo_scene_search", {"query": "Cube"})
            check("locking_job_refuses_scene_reads", False)
        except job_host.JobBusyError as error:
            check("locking_job_refuses_scene_reads", "locked Blender" in str(error))
        try:
            job_host.refuse_turn_while_locked()
            check("locking_job_pauses_new_turns", False)
        except RuntimeError:
            check("locking_job_pauses_new_turns", True)
        cancel(ctx["physics"])
        go("physics_end")
    elif phase == "physics_end" and ended(ctx["physics"]):
        stopped(ctx["physics"], "physics_bake")
        progress_ok(ctx["physics"], "physics_bake")
        check("cancelled_physics_bake_removes_only_its_own", ctx["physics"]["status"] == "cancelled"
              and not ctx["cloth"].is_baked and ctx["kept_cache"].is_baked)
        ctx["cloth"].frame_end = 12
        ctx["physics2"], _ = start_job({"kind": "BAKE_PHYSICS"})
        go("physics2")
    elif phase == "physics2" and ended(ctx["physics2"]):
        check("physics_bake_done", ctx["physics2"]["status"] == "done" and ctx["cloth"].is_baked)
        try:
            executor().precondition("angelo_job_start", {"kind": "BAKE_PHYSICS"})
            check("nothing_left_to_bake_is_refused", False)
        except Exception as error:
            check("nothing_left_to_bake_is_refused", "already baked" in str(error))
        fx.clear_physics(scene)
        fx.packed_texture_material(bpy.data.objects["Cube"])  # The exporter writes it to textures/ beside the file.
        ctx["usd"], _ = start_job({"kind": "EXPORT_USD", "output": "exports/scene.usdc", "frame_start": 1, "frame_end": 5})
        go("usd")
    elif phase == "usd" and ended(ctx["usd"]):
        check("usd_export_done", ctx["usd"]["status"] == "done" and (WS / "exports/scene.usdc").stat().st_size > 1000
              and ctx["usd"]["receipt"]["bytes"] == (WS / "exports/scene.usdc").stat().st_size)
        textures = sorted(path.name for path in (WS / "exports/textures").iterdir()) if (WS / "exports/textures").is_dir() else []
        state["usd_textures"] = textures
        check("usd_textures_published_beside_the_file", "JobTexture.png" in textures
              and ctx["usd"]["receipt"]["files"] == 1 + len(textures))
        check("usd_references_all_resolve", job_runners.unresolved_usd_references(WS / "exports/scene.usdc") == [])
        check("usd_frame_range_restored", (scene.frame_start, scene.frame_end) == (1, 40))
        check("dangling_usd_reference_is_never_reported_done", fx.dangling_usd_refused(window(), WS))
        ctx["abc"], _ = start_job({"kind": "EXPORT_ALEMBIC", "output": "exports/scene.abc", "frame_start": 1, "frame_end": 5})
        go("abc")
    elif phase == "abc" and ended(ctx["abc"]):
        check("alembic_export_done", ctx["abc"]["status"] == "done" and (WS / "exports/scene.abc").is_file())
        fx.heavy_animation(ctx["plane"])
        ctx["abc2"], _ = start_job({"kind": "EXPORT_ALEMBIC", "output": "exports/heavy.abc", "frame_start": 1,
                                    "frame_end": 250})
        cancel(ctx["abc2"])  # At once: the export stops at its next frame.
        go("abc2")
    elif phase == "abc2" and ended(ctx["abc2"]):
        stopped(ctx["abc2"], "alembic_export")
        check("cancelled_export_writes_nothing", ctx["abc2"]["status"] == "cancelled"
              and not (WS / "exports/heavy.abc").exists() and hidden_left() == [])
        fx.remove_heavy_animation(ctx["plane"])
        # Between admission and start a queued job's folder becomes a link and the scene's range grows past
        # the bound: both are refused when they start, and nothing leaves the workspace.
        ctx["blocker"], _ = start_job({"kind": "RENDER_ANIMATION", "output": "renders/blocker", "frame_start": 1,
                                       "frame_end": 40, "samples": 4096, "resolution": [160, 160]})
        (WS / "swapped").mkdir()
        ctx["swap_job"], _ = start_job({"kind": "RENDER_STILL", "output": "swapped/image.png", "queue": True})
        ctx["frames_job"], _ = start_job({"kind": "RENDER_ANIMATION", "output": "renders/inherited", "queue": True})
        check("inherited_frames_admitted_within_the_bound", ctx["facts"][ctx["frames_job"]["id"]]["frames"] == [1, 40])
        (WS / "swapped").rmdir()
        (WS / "swapped").symlink_to(OUTSIDE, target_is_directory=True)
        scene.frame_end = 20000
        try:
            executor().precondition("angelo_job_start", {"kind": "RENDER_ANIMATION", "output": "renders/too_long",
                                                         "queue": True})
            check("inherited_frames_over_the_bound_refused_at_admission", False)
        except Exception as error:
            check("inherited_frames_over_the_bound_refused_at_admission",
                  "The frame range of the scene, 1 to 20000, is 20,000 frames" in str(error))
        cancel(ctx["blocker"])
        go("queued_refusals")
    elif phase == "queued_refusals" and ended(ctx["swap_job"]) and ended(ctx["frames_job"]):
        # Refused when it starts: the staging folder is never created through a link (job_outputs.Staging).
        check("link_before_start_refused_and_nothing_leaves", ctx["swap_job"]["status"] == "failed"
              and ctx["swap_job"]["reason"] == job_outputs.MOVED and os.listdir(OUTSIDE) == [])
        check("inherited_frames_over_the_bound_refused_at_start", ctx["frames_job"]["status"] == "failed"
              and "The frame range of the scene, 1 to 20000" in ctx["frames_job"]["reason"])
        scene.frame_end = 40
        (WS / "swapped").unlink()
        ctx["sim_object"] = fx.simulation(scene)
        try:
            executor().precondition("angelo_job_start", {"kind": "BAKE_SIMULATION"})
            check("simulation_needs_a_target", False)
        except Exception as error:
            check("simulation_needs_a_target", "needs: target" in str(error))
        ctx["sim"], _ = start_job({"kind": "BAKE_SIMULATION", "target": object_handle(ctx["sim_object"])})
        go("sim")
    elif phase == "sim" and running_with_progress("sim"):
        cancel(ctx["sim"])
        go("sim_end")
    elif phase == "sim_end" and ended(ctx["sim"]):
        stopped(ctx["sim"], "simulation_bake")
        progress_ok(ctx["sim"], "simulation_bake")
        check("cancelled_simulation_bake_is_deleted", ctx["sim"]["status"] == "cancelled" and not fx.has_bake(ctx["sim_object"]))
        scene.frame_end = 10
        ctx["sim2"], _ = start_job({"kind": "BAKE_SIMULATION", "target": object_handle(ctx["sim_object"])})
        go("sim2")
    elif phase == "sim2" and ended(ctx["sim2"]):
        check("simulation_bake_done_and_packed", ctx["sim2"]["status"] == "done" and fx.has_bake(ctx["sim_object"]))
        check("crash_keeps_whole_frames_and_removes_other_staging", fx.crash_reconciliation(OUT / "crash-state", WS))
        scene.frame_end = 40
        ctx["closing"], _ = start_job({"kind": "RENDER_ANIMATION", "output": "renders/closing", "samples": 2048,
                                       "resolution": [160, 160], "frame_start": 1, "frame_end": 6})
        go("closing")
    elif phase == "closing" and running_with_progress("closing"):
        bpy.ops.wm.open_mainfile(filepath=str(OUT / "saved-during-job.blend"))
        go("reopened")
        return 1.0
    elif phase == "reopened":
        record, scene = job_ui.manager().record(ctx["closing"]["id"]), bpy.context.scene
        check("file_load_interrupts_the_running_job", record is not None and record["status"] == "interrupted"
              and record["reason"] == "file_closed" and not (WS / "renders/closing").exists() and hidden_left() == [])
        check("file_saved_during_a_job_opens_with_its_own_settings", scene.cycles.samples == 16
              and scene.render.resolution_x == 64 and scene.render.filepath == "/tmp/" and job_settings.TAG not in scene)
        check("one_record_per_started_job", sorted(job_ui.manager().store.records, key=lambda i: int(i[1:]))
              == ["j%d" % number for number in range(1, ctx["started"] + 1)])
        check("no_hidden_staging_left", hidden_left() == [])
        finish()
        return None
    elif time.monotonic() - ctx.get("entered", started) > 120:
        raise AssertionError("Phase %s did not advance in 120 s" % phase)
    return 0.1


def tick():
    try:
        return step()
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
