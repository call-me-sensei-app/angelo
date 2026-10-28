# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start a real render job, wait for it inside the turn and look at it, in a factory-startup GUI.

The scene is a glowing magenta haze that Cycles samples slowly on two threads,
so a job is still rendering when its picture is asked for.

* **Wait, then look.** ``angelo_job_start`` starts a real still; a view of
  ``LAST_JOB`` asked at once stays pending while the job renders, Blender's
  event loop keeps running, and the call is answered with the job's own
  picture: magenta, in the PNG inside both runtimes' payloads. The published
  file is the workspace's, and nothing else of the scene changed.
* **A job too long for the turn.** With the request's waiting allowance used,
  the call answers "still rendering" with the job's progress and no error, the
  job goes on, and asking again in the same request is answered at once. A job
  whose pace says that it will not end in the time left is answered as soon as
  that pace is known.
* **Cancel and Stop.** The person's Cancel ends the job and the waiting call
  says so; stopping the turn ends the wait and leaves the job rendering.

Run through ``run_native_suites.py --gui --include test_blender_view_wait``;
check ``view-wait.json`` for ``success`` and ``phase == "complete"``. No
provider, account or paid request.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-wait-qa"))
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
from angelo_agent import job_ui, scene_queries, view_spec, view_wait  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

REPORT = OUT / "view-wait.json"
state = {"phase": "boot", "success": False, "checks": {}, "seconds": {}, "answers": {}, "paid_requests": 0,
         "binary": bpy.app.binary_path}
controller = fx.conversation(WS)
references = ReferenceStore(OUT, "5c1f2f0e-6a8e-4b7e-9c56-2a54b5b8d1a1")
LOOK = {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}
RUNNING = {"queued", "starting", "running", "stopping"}


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context, value.observation_store = controller, references
    return value


def start_job(output, samples):
    """A real still job of the slow scene, approved as the controller would approve it."""
    arguments = {"kind": "RENDER_STILL", "output": output, "samples": samples, "resolution": [320, 180]}
    executor().precondition("angelo_job_start", arguments)
    started = executor().execute("angelo_job_start", arguments)
    return started, job_ui.manager().record(started["job"])


def ask(**arguments):
    return executor().execute("angelo_view", dict(LOOK, **arguments))


def wait_then_look():
    state["phase"] = "wait_then_look"
    scene = window().scene
    fx.slow_scene(scene)
    # Blender names the linear colour space the first time a file format is assigned, and a job's runner assigns one.
    scene.render.image_settings.file_format = scene.render.image_settings.file_format
    yield
    before = fx.rna_values(scene.render)
    started, record = start_job("renders/review.png", 12)
    check("the_job_start_says_how_to_see_its_picture", "image %s" % started["job"] in started["note"])
    began = time.perf_counter()
    pending = ask(image="LAST_JOB")
    overlapping = ask(image=started["job"])
    check("a_view_of_a_rendering_job_is_pending", isinstance(pending, view_wait.JobWait) and record["status"] in RUNNING
          and pending.identity == started["job"])
    ticks, progress = 0, set()
    while True:  # The event loop runs between these steps: the interface is not blocked while the call waits.
        result = pending.poll()
        if result is not None:
            break
        ticks += 1
        progress.add(round(float(record["progress"]), 2))
        assert time.perf_counter() - began < 240, "the job never ended"
        yield
    seconds = time.perf_counter() - began
    state["seconds"]["waited_for_a_real_still"] = round(seconds, 2)
    overlap_result = overlapping.poll()
    used = view_wait.WAIT_SECONDS - view_wait.allowance(controller)
    state["seconds"]["shared_overlap_wait"] = round(used, 2)
    check("overlapping_real_job_waits_count_elapsed_time_once", isinstance(overlap_result, dict)
          and overlap_result.get("success") and abs(used - seconds) < 0.25)
    state["answers"]["picture"] = {key: result.get(key) for key in ("rendered", "note")}
    check("the_event_loop_ran_while_it_waited", ticks >= 3 and len(progress) >= 2)
    check("the_call_is_answered_with_the_jobs_picture", result.get("success") and len(result["views"]) == 1
          and result["rendered"]["job"] == started["job"] and result["rendered"]["source_size"] == [320, 180]
          and abs(result["rendered"]["waited_seconds"] - seconds) < 2 and record["status"] == "done")
    pixels = fx.payloads(references, dict(LOOK, image="LAST_JOB"), result)
    for runtime, rows in pixels.items():
        check("the_picture_is_the_render_%s" % runtime, fx.is_magenta(rows.reshape(-1, 3).mean(axis=0)))
    state["answers"]["settings_changed"] = fx.differences(before, fx.rna_values(scene.render))
    check("the_job_published_its_file_and_left_the_settings", (WS / "renders/review.png").is_file()
          and state["answers"]["settings_changed"] == [])
    again = ask(image=started["job"])
    check("a_finished_job_is_shown_at_once", isinstance(again, dict) and len(again["views"]) == 1
          and "waited_seconds" not in again["rendered"])


def too_long_for_the_turn():
    state["phase"] = "too_long"
    controller._request_generation += 1
    allowance, view_spec.WAIT_SECONDS, view_wait.WAIT_SECONDS = view_spec.WAIT_SECONDS, 3, 3
    started, record = start_job("renders/final.png", 4096)
    began = time.perf_counter()
    result = yield from fx.pending_result(ask(image=started["job"]))
    seconds = time.perf_counter() - began
    state["seconds"]["answered_after_the_allowance"] = round(seconds, 2)
    state["answers"]["wait_used"] = result
    check("a_job_longer_than_the_allowance_is_an_answer_not_an_error", "error" not in result and result["views"] == []
          and result["waiting"]["ended"] == "wait_used" and 2.5 < seconds < 8 and result["job"]["status"] in RUNNING)
    check("the_answer_says_what_to_do", "End your turn and tell the person" in result["note"]
          and "image %s in a later message" % started["job"] in result["note"])
    check("the_job_goes_on", record["status"] in RUNNING and job_ui.manager().active()["id"] == started["job"])
    at_once = ask(image=started["job"])
    check("asking_again_in_the_same_request_is_answered_at_once", isinstance(at_once, dict)
          and at_once["waiting"]["ended"] == "wait_used")
    # A new request with a longer allowance: the job's pace now says that it will not end in time.
    controller._request_generation += 1
    view_spec.WAIT_SECONDS = view_wait.WAIT_SECONDS = 30
    settle, view_wait.SETTLE_SECONDS = view_wait.SETTLE_SECONDS, 2
    began = time.perf_counter()
    result = yield from fx.pending_result(ask(image="LAST_JOB"))
    seconds = time.perf_counter() - began
    state["seconds"]["answered_from_the_estimate"] = round(seconds, 2)
    state["answers"]["will_not_end_in_time"] = result
    check("a_job_whose_pace_exceeds_the_time_left_is_not_waited_for", result["waiting"]["ended"] == "will_not_end_in_time"
          and seconds < 20 and result["job"]["eta_seconds"] > 45 and "so the call stopped waiting" in result["note"]
          and record["status"] in RUNNING)
    # The person cancels the job from its card while a call waits for it.
    controller._request_generation += 1
    pending = ask(image=started["job"])
    yield
    job_ui.manager().cancel(started["job"], by="user")
    result = yield from fx.pending_result(pending)
    check("the_persons_cancel_ends_the_job_and_the_call_says_so", record["status"] == "cancelled"
          and "the person cancelled job %s" % started["job"] in result["note"] and "error" not in result)
    view_spec.WAIT_SECONDS = view_wait.WAIT_SECONDS = allowance
    view_wait.SETTLE_SECONDS = settle


def stop_leaves_the_job():
    state["phase"] = "stop"
    controller._request_generation += 1
    started, record = start_job("renders/stopped.png", 4096)
    pending = ask(image="LAST_JOB")
    yield
    yield
    pending.cancel()  # Controller.cancel_tools: the turn was stopped.
    yield
    check("stop_ends_the_wait_and_not_the_job", record["status"] in RUNNING and pending.closed
          and "the job itself goes on" in pending.stopped_result["error"])
    job_ui.manager().cancel(started["job"], by="user")
    yield from fx.wait_for(lambda: record["status"] not in RUNNING, what="the cancelled job")


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    yield
    yield from wait_then_look()
    yield from too_long_for_the_turn()
    yield from stop_leaves_the_job()


fixture_boot.drive(scenario, state, REPORT, "ANGELO_VIEW_WAIT")
