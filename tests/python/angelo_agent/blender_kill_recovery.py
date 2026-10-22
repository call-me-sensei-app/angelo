# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One launch of the kill-and-relaunch reproduction of an animation render's kept frames (VFX audit A1).

``kill_recovery_driver.py`` runs this script in the real app several times with the
same state folder and workspace, and kills the app from outside (``kill -9``) or
lets it quit, while a background render job runs. The phase comes from
``ANGELO_KILL_PHASE``:

* ``render``: save a tiny Cycles scene as ``shot.blend`` in the workspace and start
  an animation render job through the real executor; every 50 ms write
  ``status.json`` (the job record and the staging folder's files with their sizes)
  so the driver can kill at a chosen moment. ``ANGELO_KILL_QUIT_AT`` makes the app
  quit by itself once that many frames are done.
* ``check``: start again (the add-on's startup reconciliation keeps the frames),
  open ``shot.blend``, report what was published and whether each file is whole
  as Blender reads it, then render the missing frames from the card's resume and
  report the whole sequence.
* ``again``: start once more and report the published files unchanged.

Writes ``<ANGELO_QA_DIR>/kill-<phase>.json``. No provider, account or network.
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

OUT = Path(os.environ["ANGELO_KILL_DIR"])
# The workspace may be on another volume (a small disk image that fills); the reports never are.
WS = Path(os.environ.get("ANGELO_KILL_WORKSPACE", str(OUT / "workspace")))
WS.mkdir(parents=True, exist_ok=True)
PHASE = os.environ.get("ANGELO_KILL_PHASE", "render")
FORMAT = os.environ.get("ANGELO_KILL_FORMAT", "PNG")
FRAMES = int(os.environ.get("ANGELO_KILL_FRAMES", "24"))
SIZE = int(os.environ.get("ANGELO_KILL_SIZE", "96"))
SAMPLES = int(os.environ.get("ANGELO_KILL_SAMPLES", "256"))
QUIT_AT = int(os.environ.get("ANGELO_KILL_QUIT_AT", "0"))
REPORT = OUT / ("kill-%s.json" % PHASE)

from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

from angelo_agent import job_ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402

controller = SimpleNamespace(files=FileWorkspace(WS), state={"id": "kill-chat"},
                             executing_call=SimpleNamespace(id="call-1"))
state = {"phase": PHASE, "format": FORMAT, "started": time.time()}
ctx = {}


def window():
    return bpy.context.window_manager.windows[0]


def save():
    temporary = REPORT.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, default=str))
    os.replace(temporary, REPORT)


def listing(folder):
    return {path.relative_to(folder).as_posix(): path.stat().st_size
            for path in sorted(folder.rglob("*")) if path.is_file() and ".angelo-job-" not in path.as_posix()}


def blender_reads(path):
    """Whether Blender loads the file as an image of the job's size (its own decoder, not Angelo's check)."""
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:
        return False
    try:
        return tuple(image.size) == (SIZE, SIZE) and len(image.pixels) == SIZE * SIZE * 4
    finally:
        bpy.data.images.remove(image)


def scene_setup():
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_monkey_add()
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    scene.render.resolution_percentage = 100
    scene.frame_start, scene.frame_end = 1, FRAMES
    # The person's EXR Preview option: a job must still write exactly its planned files (no JPEG beside each EXR).
    scene.render.image_settings.file_format = 'OPEN_EXR'
    scene.render.image_settings.use_preview = True
    obj = bpy.context.active_object
    obj.rotation_euler = (0, 0, 0)
    obj.keyframe_insert("rotation_euler", frame=1)
    obj.rotation_euler = (0, 0, 6.28)
    obj.keyframe_insert("rotation_euler", frame=FRAMES)
    bpy.ops.wm.save_as_mainfile(filepath=str(WS / "shot.blend"))


def render_step():
    if "job" not in ctx:
        scene_setup()
        executor = BlenderExecutor(window())
        executor.studio_context = controller
        arguments = {"kind": "RENDER_ANIMATION", "output": "renders/shot", "frame_start": 1, "frame_end": FRAMES,
                     "format": FORMAT}
        executor.precondition("angelo_job_start", arguments)
        result = executor.execute("angelo_job_start", arguments)
        ctx["job"] = job_ui.manager().record(result["job"])
        state["job"] = result
    record = ctx["job"]
    staging = record["detail"].get("staging")
    state.update(record={key: record[key] for key in ("id", "status", "progress", "receipt")},
                 frames_done=record["detail"].get("frames_done", 0), staging=staging,
                 staged=listing(Path(staging)) if staging and Path(staging).is_dir() else {},
                 at=time.time())
    if staging:   # The staging folder is hidden from listing(): read it directly.
        state["staged"] = {path.name: path.stat().st_size for path in sorted(Path(staging).iterdir())
                           if path.is_file()}
    save()
    if QUIT_AT and state["frames_done"] >= QUIT_AT:
        state["quit"] = True
        save()
        bpy.ops.wm.quit_blender()
        return None
    if record["status"] in {"done", "failed", "cancelled", "interrupted"}:
        state["ended_by_itself"] = True
        save()
        bpy.ops.wm.quit_blender()
        return None
    return 0.05


def records():
    jobs = job_ui.manager(create=False)
    return {identity: {key: record[key] for key in ("status", "reason", "receipt", "kind")}
            | {"detail": {key: value for key, value in record["detail"].items()
                          if key in ("frames_done", "frames_total", "kept_frames", "staging", "resumed_by",
                                     "resume_refused", "recover_into")}}
            for identity, record in (jobs.store.records.items() if jobs else ())}


def check_step():
    jobs = job_ui.manager(create=False)
    if jobs is None:
        return 0.1   # The add-on's startup timer creates the manager and reconciles (job_ui._startup).
    if "opened" not in ctx:
        state["after_start"] = records()
        state["published"] = listing(WS / "renders") if (WS / "renders").is_dir() else {}
        state["hidden_left"] = sorted(str(path) for path in WS.rglob(".angelo-job-*"))
        state["blender_reads"] = {name: blender_reads(WS / "renders" / name) for name in state["published"]}
        ctx["opened"] = True
        save()
        bpy.ops.wm.open_mainfile(filepath=str(WS / "shot.blend"))
        return 0.5
    if PHASE == "again" or os.environ.get("ANGELO_KILL_NO_RESUME"):
        state["records"] = records()
        finish()
        return None
    if "resumed" not in ctx:
        original = next((record for record in jobs.store.records.values() if record["kind"] == "RENDER_ANIMATION"),
                        None)
        with bpy.context.temp_override(window=window()):   # As the card does, from its window.
            state["resume_state"] = job_ui.resume_state(original) if original else None
            later = job_ui.resume(original, controller, window()) if original else None
        ctx["resumed"] = later
        state["resumed"] = later["id"] if later else None
        state["resume_refused"] = original["detail"].get("resume_refused") if original else None
        save()
        if later is None:
            finish()
            return None
        return 0.2
    later = ctx["resumed"]
    if later["status"] not in {"done", "failed", "cancelled", "interrupted"}:
        return 0.2
    state["resume_record"] = {key: later[key] for key in ("status", "reason", "receipt")}
    folder = WS / "renders"
    state["published_after_resume"] = listing(folder)
    state["blender_reads_after_resume"] = {name: blender_reads(folder / name)
                                           for name in state["published_after_resume"]}
    state["records"] = records()
    finish()
    return None


def finish():
    state["ended"] = time.time()
    save()
    bpy.ops.wm.quit_blender()


def guarded(step):
    def run():
        try:
            return step()
        except Exception:
            state["error"] = traceback.format_exc()
            try:
                save()
            finally:
                bpy.ops.wm.quit_blender()
            return None
    return run


# Persistent: the check opens shot.blend, and a file load drops other timers.
bpy.app.timers.register(guarded(render_step if PHASE == "render" else check_step), first_interval=1.5,
                        persistent=True)
print("ANGELO_KILL_RECOVERY", PHASE, file=sys.stderr, flush=True)
