# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with RENDERED shading: the scene camera rendered by the scene's own engine.

The viewport shadings cannot show what a render shows: the scene's lights and
world, world volumes, render-level modifiers, true displacement and the
compositor (MATERIAL lights with a built-in HDRI). A RENDERED look is Blender's
own render job, the one F12 starts, at the requested size and at most
``view_spec.RENDER_SAMPLES`` samples. Its PNG goes to the conversation's private
reference folder and reaches the model through the image path of every view.

It follows the background-job contract (doc/angelo/long_jobs.rst) instead of
drawing on the main thread. The render is a window-manager job run through the
shared ``JobManager``: one job at a time, progress and Cancel on its chat card
and in the status bar, a wall-clock budget. The tool call stays pending
(``Controller.media_jobs``) until the job ends, so the interface is never
blocked. Unlike ``angelo_job_start`` a look needs no approval, is answered by
its own call and leaves no job record, because nothing of the person's is left
changed. A scene whose render would run Python from add-ons or scripts (an
add-on's render engine, handlers, scripted drivers: ``render_extensions``) gets
no automatic look: that needs the person's render approval, so the look is
refused and points at ``angelo_job_start``. Depsgraph-update handlers and
message-bus subscribers to the settings it sets are the exception the Auto
approval modes allow (typed edits there run them too); the look then names
them. After every look ``view_scene_check`` compares the
document with its state before, and the result never says the scene is
unchanged when that is not known. Otherwise:

* **Settings.** Resolution, samples, denoising, output path and image format
  are set on the scene for the job only. ``view_render_restore.LookSettings``
  first records them, and every image-format value a format change can alter,
  on the scene (``job_settings.TAG``), restores them when the job ends, and
  reports any value Blender would not take back. A file saved meanwhile is
  restored when it is next opened, as for any job.
* **The Render Result.** A look renders into its own slot
  (``view_render_restore.SLOT``) of Blender's Render Result image and puts the
  person's slot back, so it never replaces a render of theirs. Python can add
  a slot but not remove one: the slot stays in the image.
* **Files.** One PNG in the conversation's reference folder. Blender's render
  cache and texture-cache generation are off for the job.

The picture is what the render window shows: Blender writes it in the scene's
own view transform, look and exposure (``view_render_restore.picture_values``),
with its alpha, and ``view_picture`` delivers it opaque, transparent pixels
over a checkerboard. ``region`` renders a part of the frame through Blender's
render region, cropped, at up to the scene's full resolution, so the pixels of
the part are the final render's and not a scaled-down frame's.

A look is bounded by its samples, by ``RENDER_SECONDS`` and by
``RENDER_REQUEST_SECONDS`` of looks per request, for every runtime. Past
``RENDER_SECONDS`` it is stopped once it has samples to show: Blender still
writes the samples that finished, and the result says the image is partial. A
scene whose preparation takes most of that budget, or more, is not rendered for
nothing or for two noisy samples: it gets ``SAMPLE_SECONDS`` of sampling from
its first sample. Only the request's total ends a look without an image.
Model-facing text is English and never translated.
"""

import datetime
import time
from pathlib import Path

import bpy

from . import job_render_samples, render_extensions, scene_queries, view_cache, view_picture, view_scene_check
from .input_policy import tool_running
from .job_manager import JobStartError
from .job_runners import SAMPLES as SAMPLE_PATHS, RenderRunner, native
from .job_settings import DisplayNone
from .job_spec import PREVIEW, JobRequestError, native_jobs_available
from .job_store import TERMINAL
from .studio_agent import StudioPending
from .view_blur import rendered_blur
from .world_surface import lighting
from .view_framing import output_size
from .view_picture import MIN_REGION_PIXELS
from .view_render_restore import SLOT, LookSettings, ResultSlot, picture_values  # noqa: F401  (SLOT: the suites read it here)
from .view_render_restore import written_keys
from .view_spec import RENDER_REQUEST_SECONDS, RENDER_SAMPLES, RENDER_SECONDS, RENDERED, SCENE_CAMERA

MINIMUM_SECONDS = 15   # A look does not start with less of its request's budget left.
HEAVY_PREPARATION = 10  # Seconds of preparation from which a result says that LIT is the way to iterate.
SAMPLE_SECONDS = 20    # A look whose first sample came late still renders samples for this long.
GRACE_SECONDS = 45     # The pending call outlives the budget by the stop latency and the finish.
NO_CAMERA = ("The scene has no active camera, and a RENDERED look renders the scene camera. Add or choose a "
             "camera, or use an axis or FIT view with LIT shading (the scene's lights and world)")
NO_IMAGE = "Blender ended the render without writing an image; see its Info log"
# Other window-manager jobs of the person's that starting a render would stop (Blender stops them itself).
OTHER_JOBS = ("POINTCACHE", "BAKE_GEOMETRY_NODES", "USD_EXPORT", "ALEMBIC_EXPORT")
# Settings a look writes besides its render settings (ResultSlot, job_settings.DisplayNone): message-bus keys.
OTHER_WRITES = frozenset({("RenderSlots", "active_index"), ("PreferencesView", "render_display_type"),
                          ("Preferences", "is_dirty")})

_spent = {}    # conversation id -> (request, seconds of looks that request has rendered)
_pending = {}  # job id -> RenderPending, until its job ends


class LookRunner(RenderRunner):
    """One still of the scene camera on Blender's render job, written to a private PNG."""

    def __init__(self, window, request, path, limit, mode="ASK"):
        self.path, self.limit = Path(path), limit  # ``limit``: seconds after which a first sample ends the look.
        self.progress, self.began, self.first_sample, self.seconds = 0.0, None, None, 0.0
        self.unrestored, self.slot, self.over, self.exact_colour = [], None, False, True
        self.mode, self.check, self.scene_check, self.handlers_ran = mode, None, None, []  # Approval mode; check.
        self.unknown = False  # Whether this build could not list message-bus subscribers.
        super().__init__(window, request, None)  # Admission: camera, stereoscopy, File Output nodes, bounds.

    def admit(self):
        super().admit()
        # Add-on or script Python runs only after a render approval; the Auto modes let depsgraph handlers and
        # message-bus subscribers to the look's own settings run.
        written = written_keys(self.scene, self._values()) | OTHER_WRITES
        self.extensions = render_extensions.refuse_look(self.scene, self.mode, written)

    def _values(self):
        """The job's settings, in the order they are written (the image format last, its type first)."""
        scene, render, request = self.scene, self.scene.render, self.request
        frame = request.get("frame_size") or request["resolution"]
        values = {"render.resolution_x": frame[0], "render.resolution_y": frame[1],
                  "render.resolution_percentage": 100, "render.filepath": str(self.path),
                  "render.use_file_extension": False}
        if "border" in request:  # A region: Blender renders that part of the frame only and crops to it.
            values.update({"render.use_border": True, "render.use_crop_to_border": True})
            values.update(zip(("render.border_min_x", "render.border_min_y", "render.border_max_x",
                               "render.border_max_y"), request["border"]))
        values.update({"render." + name: False for name in ("use_render_cache", "use_auto_generate_texture_cache")
                       if hasattr(render, name)})
        if "samples" in request:  # With every rendered layer's count: held for the render (job_render_samples).
            values[SAMPLE_PATHS[render.engine]] = request["samples"]
            values.update(job_render_samples.settings(self.window, request["samples"], look=True))
        picture, self.exact_colour = picture_values(scene)
        values.update(picture)
        return values

    def start(self):
        native()
        if self.window.scene != self.scene:
            raise JobStartError("The look's scene is no longer shown in its window")
        self.admit()
        self.handlers_ran = self.extensions.view_python  # Run by the writes below (Auto modes only).
        self.unknown = self.extensions.subscribers is None
        self.check = view_scene_check.SceneCheck(self.window, deep=bool(self.handlers_ran))
        self.overrides, self.slot = LookSettings(self.scene, self.identity), ResultSlot()
        try:
            self.overrides.apply(self._values())
            self.slot.enter()
            with DisplayNone():
                self._invoke(bpy.ops.render.render, write_still=True, use_viewport=False)
        except Exception:
            self._restore()
            raise
        self.began = time.monotonic()

    def _restore(self):
        try:
            if self.overrides is not None:
                self.unrestored = self.overrides.restore() or self.unrestored
        finally:   # The person's render slot comes back whatever the settings did (break-it SW-M-1).
            if self.slot is not None:
                self.slot.leave()

    def poll(self):
        progress = native().progress(self.scene, self.job_type)
        if progress is None:
            return None
        elapsed = time.monotonic() - self.began
        if progress > 0 and self.first_sample is None:
            self.first_sample = elapsed
        self.progress = max(self.progress, min(1.0, progress))
        if self.progress > 0 and not self.over and elapsed > max(self.limit, self.first_sample + SAMPLE_SECONDS):
            self.over = True  # Past its budget with something to show: stop here (finish reports a partial look).
            self.stop()
        return {"progress": min(self.progress, 0.999)}

    def written(self):
        try:
            with self.path.open("rb") as handle:
                return handle.read(8) == b"\x89PNG\r\n\x1a\n"
        except OSError:
            return False

    def finish(self, stopped):
        """Restore the person's settings and slot, then say whether an image exists: ``(status, reason, receipt)``."""
        own = list(self.overrides.originals) if self.overrides is not None else []
        self._restore()
        if self.check is not None:  # Did anything else change the document meanwhile?
            self.scene_check = self.check.finish(own)
        self.seconds = time.monotonic() - self.began if self.began is not None else 0.0
        # A stopped render still writes the samples that finished; one stopped before its first has none.
        image = self.written() and (not (stopped or self.over) or self.progress > 0)
        checked = {"scene_check": self.scene_check} if self.scene_check is not None else {}
        if stopped:
            return "cancelled", "", dict(checked, image=image)  # The manager's reason stands.
        if self.over:
            return "cancelled", "time_budget", dict(checked, image=image)
        return ("done", "", dict(checked, image=True)) if image else ("failed", NO_IMAGE, dict(checked, image=False))

    def abandon(self):
        self._restore()


def _blender_busy(scene):
    """Why a render cannot start beside what Blender is doing for the person, or None."""
    if bpy.app.is_job_running("RENDER"):
        return "Blender is rendering; a RENDERED look has to wait until that render ends"
    if bpy.app.is_job_running("OBJECT_BAKE") or any(native().progress(scene, kind) is not None for kind in OTHER_JOBS):
        return "Blender is running a bake or an export that a render would stop; a RENDERED look has to wait for it"
    return None


def _request(controller):
    return (getattr(controller, "_epoch", 0), getattr(controller, "_request_generation", 0))


def _allowance(controller):
    """Seconds of looks the request has left (RuntimeError when too few to start one)."""
    request, spent = _spent.get(controller.state["id"], (None, 0.0))
    left = RENDER_REQUEST_SECONDS - (spent if request == _request(controller) else 0.0)
    if left < MINIMUM_SECONDS:
        raise RuntimeError(
            "This request has used its %d s of RENDERED looks. Work from what you have seen and tell the person; "
            "a full render is a job they approve (angelo_job_start RENDER_STILL)" % RENDER_REQUEST_SECONDS)
    return float(left)


def _size(scene, resolution, region=None):
    """What a look renders: ``(picture size, frame size, border)``.

    Without a region the frame has the scene's own pixel ratio and
    ``resolution`` as its longer side, and the picture is the frame. With one,
    the frame is as large as it takes for the part's longer side to be
    ``resolution``, but never larger than the scene's full resolution: more
    would be detail no render of this scene has. ``border`` is the part in
    Blender's own terms (fractions from the bottom-left corner).
    """
    render = scene.render
    if region is None:
        width = max(1, render.resolution_x * render.resolution_percentage // 100)
        height = max(1, render.resolution_y * render.resolution_percentage // 100)
        width, height = output_size(resolution, width / height)
        size = [max(4, width), max(4, height)]
        return size, size, None
    left, top, right, bottom = region
    full = (render.resolution_x, render.resolution_y)
    scale = min(1.0, resolution / max((right - left) * full[0], (bottom - top) * full[1]))
    frame = [max(4, round(full[0] * scale)), max(4, round(full[1] * scale))]
    size = [round((right - left) * frame[0]), round((bottom - top) * frame[1])]
    if min(size) < MIN_REGION_PIXELS:
        raise RuntimeError("That region is only %dx%d pixels of this scene's %dx%d render; choose a larger one"
                           % (size[0], size[1], full[0], full[1]))
    return size, frame, [left, 1.0 - bottom, right, 1.0 - top]


def begin(window, checked, references, controller):
    """Start one RENDERED look and return its pending result. Raises RuntimeError when nothing was started."""
    if controller is None or not isinstance(getattr(controller, "state", None), dict):
        raise RuntimeError("Bind a conversation before rendering a look")
    if "image" in checked:
        from .view_finished import finished_image
        return finished_image(window, checked, references, controller)
    if not native_jobs_available():
        raise RuntimeError("This build of Angelo cannot render looks; update the application")
    scene = window.scene
    if scene.camera is None:
        raise RuntimeError(NO_CAMERA)
    from . import job_ui, native_programs, view_capture
    if getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)():
        raise RuntimeError("A staged change is still in progress; finish or stop it before rendering a look")
    manager = job_ui.manager()
    active = manager.active()
    if active is not None:
        raise RuntimeError("Job %s (%s) is running, and one render runs at a time: a RENDERED look has to wait "
                           "until it ends" % (active["id"], active["kind"]))
    busy = _blender_busy(scene)
    if busy:
        raise RuntimeError(busy)
    if tool_running(window):
        raise RuntimeError("Finish the current Blender interaction before rendering a look")
    left = _allowance(controller)
    region = checked.get("region")
    size, frame, border = _size(scene, checked["resolution"], region)
    # The manager's own budget is the request's total; the look's budget ends it at its first sample (LookRunner.poll).
    request = {"kind": PREVIEW, "queue": False, "minutes": left / 60.0, "format": "PNG",
               "resolution": size, "frame_start": scene.frame_current, "frame_end": scene.frame_current}
    if border is not None:
        request.update(frame_size=frame, border=border)
    scene_samples = job_render_samples.scene_samples(window)  # The greatest layer count; None without samples.
    if scene_samples is not None:
        request["samples"] = max(1, min(RENDER_SAMPLES, scene_samples))
    with bpy.context.temp_override(window=window):
        window.view_layer.update()  # Targets report the revisions the render will see.
    epoch = scene_queries.document_epoch()
    _objects, targets = view_capture._targets(window, checked["targets"])
    camera = None  # A panoramic camera, a non-camera object or a part of the frame: no view can reuse it.
    if region is None:
        try:
            camera = view_capture._scene_camera(window, epoch, checked["resolution"])
        except RuntimeError:
            pass
    reference = references.new_observation("SCENE_CAMERA view · rendered" + (" · region" if region else ""))
    from .chat_settings import clean_options
    mode = clean_options(controller.state.get("options"))["approval"]
    try:
        runner = LookRunner(window, request, reference["path"], min(float(RENDER_SECONDS), left), mode)
        call = getattr(controller, "executing_call", None)
        record = manager.submit(controller.state["id"], getattr(call, "id", ""), request, runner)
    except Exception as error:
        references.discard_observation(reference)
        raise RuntimeError(str(error)) from None
    if record["status"] in TERMINAL:  # Blender refused to start the render; the runner left nothing behind.
        references.discard_observation(reference)
        raise RuntimeError(record["reason"] or "Blender did not start the render")
    context = {"epoch": epoch, "scene": scene.name[:128], "frame": scene.frame_current, "targets": targets,
               "resolution": checked["resolution"], "camera": camera, "reference": reference,
               "scene_samples": scene_samples, "region": region, "frame_size": frame,
               "color": view_picture.colour(scene), "own_border": region is None and bool(scene.render.use_border),
               "session": controller.state["id"], "request": _request(controller),
               "motion_blur": rendered_blur(scene, window.view_layer, scene.render.engine),
               "lighting": lighting(scene, window.view_layer)}
    pending = RenderPending(manager, record, runner, references, context)
    if runner.handlers_ran or runner.unknown:  # A stopped turn's answer cannot rule out what add-on Python did.
        pending.stopped_result = view_scene_check.claims(dict(RenderPending.stopped_result, error=(
            "The look was stopped with its turn.")), None, runner.handlers_ran, runner.unknown)
    _pending[record["id"]] = pending
    return pending


def ended(manager, record):
    """A look's job ended (job_ui): drop its record, charge its request, discard an image nobody waits for."""
    manager.store.remove(record["id"])
    pending = _pending.pop(record["id"], None)
    if pending is None:
        return
    request, spent = _spent.get(pending.context["session"], (None, 0.0))
    spent = spent if request == pending.context["request"] else 0.0
    _spent[pending.context["session"]] = (pending.context["request"], spent + pending.runner.seconds)
    if pending.closed:
        pending.discard()


class RenderPending(StudioPending):
    """The look's tool call while its job runs; ``Controller._pump_events`` polls it on the main thread."""

    status_text = "Rendering a look"
    activity_note = "Rendering the scene camera in the background. Cancel stops it; nothing in the scene changes."
    # What the call answers when its turn is stopped (Controller.cancel_tools), instead of the provider-work text.
    stopped_result = {"error": "The look was stopped with its turn; nothing in the scene changed.",
                      "read_only": True, "scene_changed": False}

    def __init__(self, manager, record, runner, references, context):
        super().__init__(self._poll_job, self._stop)
        self.manager, self.record, self.runner = manager, record, runner
        self.references, self.context = references, context
        self.timeout = record["request"]["minutes"] * 60 + GRACE_SECONDS

    def _poll_job(self):
        return _result(self) if self.record["status"] in TERMINAL else None

    def _stop(self):
        """The call was cancelled or expired, or its turn ended: stop the render; ``ended`` discards its image."""
        if self.record["status"] in TERMINAL:
            self.discard()
            return
        try:
            self.manager.cancel(self.record["id"], by="stopped")
        except JobRequestError:
            pass

    def discard(self):
        try:
            self.references.discard_observation(self.context["reference"])
        except (OSError, ValueError):
            pass


def _no_image(pending, reason, note):
    pending.discard()
    return {"read_only": True, "scene_changed": False, "views": [], "shading": RENDERED,
            "rendered": {"image": False, "ended": reason, "seconds": round(pending.runner.seconds, 1)}, "note": note}


def _result(pending):
    """The look's result, saying what the check after it found (``view_scene_check.claims``)."""
    runner = pending.runner
    return view_scene_check.claims(_outcome(pending), runner.scene_check, runner.handlers_ran, runner.unknown)


def _outcome(pending):
    """The finished look as an ``angelo_view`` result. An outcome without an image is a result too, not an error."""
    record, runner, context = pending.record, pending.runner, pending.context
    status, reason, facts = record["status"], record["reason"], record["detail"]
    budget, total = round(runner.limit), round(record["request"]["minutes"] * 60)
    side_effects = [{"code": "render_settings_not_restored",
                     "detail": "Blender did not take these settings back; tell the person: " + ", ".join(runner.unrestored[:8])}
                    ] if runner.unrestored else []
    partial = status == "cancelled" and reason == "time_budget"
    if status == "cancelled" and not (partial and record["receipt"].get("image")):
        if partial:
            outcome = _no_image(pending, reason, (
                "No image: Blender was still preparing the scene for rendering when this request's %d s of looks "
                "ran out, so no sample had finished (a first GPU render also compiles its kernels, once). A smaller "
                "resolution will not make preparation faster. Tell the person; a render with more time is a job "
                "they approve (angelo_job_start RENDER_STILL), which angelo_view then shows with image" % total))
        else:
            outcome = _no_image(pending, reason, "No image: the person cancelled this look. Do not start another "
                                "unless they ask." if reason == "user" else "No image: the look was cancelled.")
        return dict(outcome, side_effects=side_effects) if side_effects else outcome
    if status != "done" and not partial:
        pending.discard()
        error = ("The document was closed or reloaded during the look; nothing was rendered" if status == "interrupted"
                 else reason or NO_IMAGE)
        return dict({"error": error, "read_only": True, "scene_changed": False},
                    **({"side_effects": side_effects} if side_effects else {}))
    if scene_queries.document_epoch() != context["epoch"]:
        pending.discard()
        return {"error": "Document IDs were replaced during the look; search the scene and view again",
                "read_only": True, "scene_changed": False}
    from .view_capture import _public_camera
    reference = dict(context["reference"])
    try:  # Blender wrote the render with its alpha; the model gets it opaque, transparency over a checkerboard.
        png, picture = view_picture.scaled(reference["path"])
        Path(reference["path"]).write_bytes(png)
    except Exception as error:
        pending.discard()
        return {"error": "The rendered image could not be read (%s)" % str(error)[:120], "read_only": True,
                "scene_changed": False}
    reference["bytes"] = len(png)
    (width, height), region = picture["size"], context["region"]
    camera = context["camera"]
    if camera is not None:
        view_cache.cameras.remember(reference["id"], camera)  # A later SOLID or MATERIAL view may REUSE it.
    complete = status == "done"
    samples, engine = facts.get("samples"), facts.get("engine")
    rendered = {"engine": engine, "complete": complete, "seconds": round(runner.seconds, 1), "budget_seconds": budget,
                "color": context["color"], **{key: facts[key] for key in ("device", "device_why") if facts.get(key)}}
    if region:
        rendered.update(region=region, frame_size=context["frame_size"])
    if picture["transparent"]:
        rendered["transparent"] = picture["transparent"]
    if context.get("motion_blur"):  # As F12 renders it: a moving object is blurred, not misbuilt (view_blur).
        rendered["motion_blur"] = context["motion_blur"]
    if context.get("lighting"):  # What lit it, as the render started (world_surface.lighting).
        rendered["lighting"] = context["lighting"]
    if engine in SAMPLE_PATHS:
        rendered.update(samples=samples, scene_samples=context["scene_samples"])
        if complete or engine != "CYCLES":  # Cycles denoises as a render ends: whether a stopped one was is not known.
            rendered["denoised"] = engine == "CYCLES"
    took = "It took %s s" % round(runner.seconds, 1)
    if runner.first_sample is not None:
        # Why it took that long, so the model can choose: preparation is the scene's, sampling the look's.
        prepare = round(runner.first_sample, 1)
        rendered.update(prepare_seconds=prepare, sampling_seconds=round(max(0.0, runner.seconds - runner.first_sample), 1))
        took += ": %s s preparing the scene for rendering and %s s sampling" % (prepare, rendered["sampling_seconds"])
        if prepare >= HEAVY_PREPARATION:
            took += (". Preparation does not depend on the picture's size or samples, so every look of this scene "
                      "costs about as much: iterate with LIT (seconds) and render at checkpoints")
    request, spent = _spent.get(context["session"], (None, 0.0))
    rendered["request_seconds_left"] = max(0, round(RENDER_REQUEST_SECONDS - (spent if request == context["request"] else 0.0)))
    note = ("An actual %s render of %s, as F12 renders it (scene lights, world, volumes, compositing, render-level "
            "modifiers, the scene's %s view transform) at %dx%d"
            % (engine, "the scene camera" if not region else "one part of the scene camera's frame (region, of a "
               "%dx%d frame)" % tuple(context["frame_size"]), context["color"]["view_transform"], width, height))
    if engine in SAMPLE_PATHS:
        note += " and %d of the scene's %d samples" % (samples, context["scene_samples"])
    note += ": judge lighting, atmosphere and the %s from it. It is not a viewport image." % (
        "detail of that part" if region else "overall picture")
    note += " " + took + "."
    if context.get("motion_blur"):
        note += " The scene renders with motion blur, so anything moving at this frame is blurred."
    if context["own_border"]:
        note += " The scene's own render region is on, so only that part of the frame is rendered."
    note += view_picture.transparency_note(picture["transparent"])
    uncertainty = []
    if not runner.exact_colour:
        uncertainty.append({"code": "render_color_approximate", "detail": (
            "The scene's view uses custom curves and its output overrides the colour management; this picture has the "
            "scene's view transform, look and exposure but the output's curves")})
    if not complete:
        finished = max(1, round(runner.progress * samples)) if samples else None
        rendered["samples_finished"] = finished
        detail = ("Stopped at its %d s budget" % budget if runner.seconds < budget + 15 else
                  "Stopped after %d s, %d s into its samples (preparing the scene took %d s of its %d s budget)"
                  % (round(runner.seconds), SAMPLE_SECONDS, round(runner.first_sample or 0), budget))
        detail += (" after about %d of %d samples" % (finished, samples) if finished else "") + (
            ": the image is partial%s and noisier than a finished look" % (", may not be denoised," if engine == "CYCLES" else ""))
        uncertainty.append({"code": "render_partial", "detail": detail})
        note += " " + detail + "."
    view = dict(reference, view_id=reference["id"], view=SCENE_CAMERA, framing=SCENE_CAMERA, shading=RENDERED,
                width=width, height=height, cache_hit=False,
                camera=_public_camera(camera) if camera is not None else {"source": SCENE_CAMERA})
    if region:
        view["region"] = region
    return {"success": True, "read_only": True, "scene_changed": False, "document_epoch": context["epoch"],
            "scene": context["scene"], "frame": context["frame"], "coverage": "RENDERED_SCENE",
            "targets": context["targets"], "views": [view], "shading": RENDERED, "resolution": context["resolution"],
            "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds"),
            "uncertain": bool(uncertainty), "uncertainty": uncertainty, "side_effects": side_effects,
            "rendered": rendered, "note": note}
