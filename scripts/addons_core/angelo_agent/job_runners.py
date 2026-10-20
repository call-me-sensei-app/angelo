# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's own jobs behind the job contract: the runner base, renders and background exports.

The bake runners are in ``job_bakes``. Each runner starts one window-manager job
with Blender's operator (INVOKE, or the exporter's background-job option), reads
its progress and stops it through ``_bpy._angelo_jobs`` (Python has neither), and
on the job's end restores the settings it applied for the job (``job_settings``)
and publishes or discards its output (``job_outputs``).

Admission (``admit``, run by the constructor before approval and again at start,
since a queued job's scene can change meanwhile) refuses what Blender would refuse
or ask about later, and applies every documented bound (``job_spec``) to the values
the job will really use, the scene's inherited ones included: no camera, an
engine without samples, too many frames, pixels or samples, an object without UVs
or a bake image, a disk cache in an unsaved file, a simulation that bakes to disk
(Blender asks before replacing its files), a render whose files the job could
not name in advance. ``start`` attempts cleanup after a failed native start;
unclassified failures do not prove restoration. The output's folders are verified at start, on every poll and before
publishing (``job_outputs.Staging``); a link or moved folder stops the job and
publishes nothing. All of this runs on the main thread from the job timer or an
approved call, never inside a staged program.

Reasons and notes are English (the model reads them); fixed ones are marked with
``n_`` so the chat card can translate them where it shows them.
"""

from pathlib import Path

import bpy

from . import checkpoint_filesystem as cf
from . import job_outputs
from . import job_render_outputs as render_outputs
from . import render_extensions, render_reach
from .job_manager import NOT_PUBLISHED, JobStartError
from .job_render_samples import SAMPLES  # The scene's sample count per engine; view_render reads it here.
from .job_settings import DisplayNone, Overrides, scene_value
from .job_spec import FORMATS, PREVIEW, JobRequestError, check_frames, check_resolution, check_samples, check_work
from .ui_text import n_

NOTHING_WRITTEN = n_("Nothing was written.")
CANCELLED_NOTHING = n_("Cancelled; nothing was written.")
CHANGED = n_("The scene's views or File Output nodes changed since this render was approved, so it would write other "
             "files; start it again")
STRIPS_CHANGED = n_("The scenes the Sequencer renders changed since this render was approved; start it again")
UNEXPECTED = n_("The render wrote a file its approval did not list; nothing was published")
SILENT = n_("Some File Output items wrote no file: Blender writes one only when its input is an image.")


def native():
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        raise JobStartError("This build of Angelo cannot run background jobs; update the application", code="UNSUPPORTED") from None
    return _angelo_jobs


class Runner:
    job_type = ""
    skip_requested = False   # Its output's name was taken at admission: it writes under the next free one (job_host).
    beside = ()              # Names its exporter writes beside the output, published with it (taken: the set moves).

    def __init__(self, window, request, workspace):
        self.window, self.scene = window, window.scene
        self.scene_uid = self.scene.session_uid
        # The manager names the job (JobManager.submit) after admission and before start.
        self.identity, self.request, self.workspace = None, request, workspace
        self.output = job_outputs.admit_output(workspace, request["output"]) if request.get("output") else None
        self.output_name = request.get("output")  # Workspace-relative, as the agent and the card name it.
        self.staging = self.overrides = self.published = None
        self.moved = None  # The OutputMoved a poll found: the job was stopped and publishes nothing.

    publishes = "folder"  # How its output is published: job_output_names.fallback_name ("folder", "file", "file_set").

    @property
    def locks_interface(self):
        return False

    def admit(self):
        """Refuse (JobRequestError) what this job could not do now, on the values it would really use."""

    def describe(self):
        """Admission facts for the approval and the record (no absolute paths)."""
        return {}

    def start(self):
        """Start Blender's job; attempt cleanup on failure without claiming unproved recovery."""
        self.startup_effects_possible = False  # Only this admission phase can prove no startup effects.
        native()
        if self.window.scene != self.scene:
            raise JobStartError("The job's scene is no longer shown in its window", code="STALE_CONTEXT")
        self.admit()  # Again: a queued job's scene may have changed since its approval.
        render_extensions.recheck(self)  # The add-on or script Python it would run, too.
        self.startup_effects_possible = True  # Never reset by cleanup: callbacks may have other effects.
        try:
            self.overrides = Overrides(self.scene, self.identity)
            if self.output is not None:
                self.staging = job_outputs.Staging(self.workspace.root, self.output, self.identity,
                                                   self.skip_requested, self.publishes == "file_set",
                                                   self._announce).create()
            if self.staging is not None:
                self.staging.verify()  # Blender writes by path: the path must reach the folders held.
            self._start()
        except Exception:
            self._undo_start()
            raise

    def _start(self):
        raise NotImplementedError

    def _announce(self, relative):
        """Where a publication that takes more than one link goes, saved on the job's record before it starts (None
        when it is taken back), so a crash during it is said (``job_ui._reconcile``)."""
        from .job_manager import shared
        jobs = shared()
        record = jobs.store.get(self.identity) if jobs is not None and self.identity else None
        if record is not None:
            record["detail"].pop("publishing", None) if relative is None else record["detail"].__setitem__(
                "publishing", relative)
            jobs.store.save()

    def _undo_start(self):
        try:
            self.startup_unrestored = self.overrides.restore() if self.overrides is not None else []
        finally:
            self.abandon()

    def _check_output_path(self):
        """Stop the job at once if its output's folders stopped being the verified ones."""
        if self.staging is None or self.moved is not None:
            return
        try:
            self.staging.verify()
        except job_outputs.OutputMoved as error:
            self.moved = error
            try:
                self.stop()
            except Exception:
                pass  # Blender's job may have just ended; finish still publishes nothing.

    def poll(self):
        self._check_output_path()
        progress = native().progress(self.scene, self.job_type)
        return None if progress is None else {"progress": progress}

    def stop(self):
        native().stop(self.scene, self.job_type)

    def finish(self, stopped):
        """Restore the job's settings, then publish or discard: ``(status, reason, receipt)``."""
        unrestored = self.overrides.restore() if self.overrides is not None else []
        if self.moved is not None:
            status, reason, receipt = self._discard("failed", job_outputs.MOVED, NOT_PUBLISHED)
        else:
            try:
                status, reason, receipt = self._finish(stopped)
            except job_outputs.OutputMoved as error:  # Found again just before publishing.
                status, reason, receipt = self._discard("failed", str(error), NOT_PUBLISHED)
        if unrestored:  # Never silent: the person hears which of their settings Blender did not take back.
            receipt = dict(receipt, unrestored=unrestored[:8])
        return status, reason, receipt

    def abandon(self):
        if self.staging is not None:
            self.staging.discard()

    def _invoke(self, operator, override=None, **properties):
        try:
            with bpy.context.temp_override(window=self.window, **(override or {})):
                result = operator('INVOKE_DEFAULT', **properties)
        except RuntimeError as error:  # Blender's own refusal: its error report says what to change.
            reason = " ".join(str(error).split()).removeprefix("Error: ")
            raise JobStartError("Blender refused to start the job: %s" % reason[:300]) from None
        if not {'RUNNING_MODAL', 'FINISHED'} & set(result):
            raise JobStartError("Blender did not start the job; see the Info log for its reason")

    def _published(self, receipt):
        """Release the staging folder after a publication, and name the output when it took the next free name."""
        self.published = self.staging.published
        if self.published != self.output:  # The name (or one of the set's) was taken: never overwritten.
            receipt["output"] = self.staging.relative_output()
        receipt["files"] = self.staging.linked
        self.staging.close()
        return receipt

    def _publish_file(self, name, receipt=None):
        self.staging.publish_file(name)
        return self._published(dict(receipt or {}))

    def _publish_folder(self, receipt):
        self.staging.publish_folder()
        return self._published(dict(receipt))

    def _discard(self, status, reason, note):
        self.abandon()
        return status, reason, {"note": note}


class RenderRunner(Runner):
    """A still or an animation on Blender's render job.

    With an output folder of its own the job plans every file the render writes
    (``job_render_scene.Plan``): one per view, and one per compositor File
    Output item, all sent into its staging folder. A render without one (a
    subclass that writes a single private image) cannot redirect those files
    and refuses Stereoscopy and File Output nodes instead.
    """
    job_type = "RENDER"

    def __init__(self, window, request, workspace):
        super().__init__(window, request, workspace)
        self.suffixes = (FORMATS[request["format"]],)
        self.plan = self.approved = self.names = None
        self.frame_polled, self.view_pass, self.last_progress, self.reported = 0, 0, 0.0, 0.0
        self.admit()
        if self.plan is not None:
            self.approved = render_outputs.signature(self.plan.outputs)
            self.approved_strips = list(self.strip_scenes)
            if self.folder_output and request["kind"] == "RENDER_STILL":
                # Several files: the output is a folder named like the still, without its file suffix.
                self.output_name = request["output"][:-len(self.suffixes[0])]
                self.output = job_outputs.admit_output(workspace, self.output_name)

    @property
    def folder_output(self):
        """Whether the job publishes a folder: an animation, or a still of several files."""
        return self.request["kind"] != "RENDER_STILL" or not self.plan.single

    @property
    def publishes(self):
        return "folder" if self.folder_output else "file"

    def admit(self):
        scene, render = self.scene, self.scene.render
        if scene.camera is None:
            raise JobRequestError("The scene has no active camera to render from", code="VALIDATION")
        self.strip_scenes = []  # Other scenes the Sequencer renders, with their own settings (render_reach).
        if self.output is None:  # No folder of its own to send their files to.
            if render.use_multiview:
                raise JobRequestError("Stereoscopy writes several images per frame; turn it off for a background render", code="UNSUPPORTED")
            render_reach.admit_look(scene)
        else:
            from . import job_render_scene
            self.plan = job_render_scene.Plan(scene, self.request["format"])
            if self.approved is not None and render_outputs.signature(self.plan.outputs) != self.approved:
                raise JobRequestError(CHANGED, code="STALE_SETTING")
            self.strip_scenes = render_reach.admit_job(scene)
            if self.approved is not None and self.strip_scenes != self.approved_strips:
                raise JobRequestError(STRIPS_CHANGED, code="STALE_SETTING")
        self.facts = self._measure()

    def _measure(self):
        """The frames, resolution, samples and engine this render uses: the request's, else the person's settings."""
        scene, request = self.scene, self.request
        still = request["kind"] == "RENDER_STILL"
        start = request.get("frame_start", scene.frame_current if still else scene_value(scene, "frame_start"))
        end = start if still else request.get("frame_end", scene_value(scene, "frame_end"))
        explicit = "frame_start" in request and (still or "frame_end" in request)
        self.start_frame, self.end_frame = start, end
        self.total = check_frames(start, end, "the request" if explicit else "the scene")
        engine = scene_value(scene, "render.engine")
        if "samples" in request and engine not in SAMPLES:
            raise JobRequestError("The %s engine has no sample count; leave samples out" % engine, code="VALIDATION")
        from . import job_render_samples
        # What the job sets it holds for every frame; what it inherits counts at its greatest animated value.
        held, live = job_render_samples.reader(self.window), job_render_samples.reader(self.window, (start, end))
        percentage = live("render.resolution_percentage")
        width, height = request.get("resolution") or (live("render.resolution_x") * percentage // 100,
                                                      live("render.resolution_y") * percentage // 100)
        check_resolution(width, height, "the request" if "resolution" in request else "the scene")
        read = held if "samples" in request or request["kind"] == PREVIEW else live
        scene_samples = read(SAMPLES[engine]) if engine in SAMPLES else 1
        layers = job_render_samples.counts(self.window, scene_samples, requested=request.get("samples"),
                                          preserve_inherited=request["kind"] == PREVIEW, read=read)
        samples = max((count for _layer, count in layers), default=request.get("samples", scene_samples))
        check_samples(samples, "the rendered layers")
        views = max(1, len(self.plan.views)) if self.plan is not None else 1
        try:
            check_work(request, width, height, sum(count for _layer, count in layers), self.total * views)
        except JobRequestError as error:
            if len(layers) > 1:
                raise JobRequestError("Across the rendered layers: " + str(error), code=error.code, details=error.details) from None
            raise
        facts = {"engine": engine, "resolution": [width, height], "samples": samples, "frames": [start, end]}
        if engine == "CYCLES":  # The device it renders on, and an estimate from the last render there (render_device).
            from . import render_device
            render_device.describe(scene, facts)
        if request["kind"] != PREVIEW and (len(layers) > 1 or getattr(scene.render, "use_single_layer", False)):
            facts["layer_samples"] = [{"name": layer.name, "samples": count} for layer, count in layers]
        if getattr(self, "strip_scenes", None):  # Named in the approval: they render with their own settings.
            facts["strip_scenes"] = self.strip_scenes[:12]
        if self.plan is not None and not self.plan.single:
            # The exact files, for the approval: a still's by name, an animation's with #### for the frame.
            facts["outputs"] = render_outputs.entries(self.plan.outputs, start if still else None)
            if still:
                facts["output"] = request["output"][:-len(self.suffixes[0])]
        return facts

    @property
    def locks_interface(self):
        return bool(self.scene.render.use_lock_interface)

    def describe(self):
        return dict(self.facts)

    def _start(self):
        request, values = self.request, {}
        if "resolution" in request:
            values.update({"render.resolution_x": request["resolution"][0], "render.resolution_y": request["resolution"][1],
                           "render.resolution_percentage": 100})
        if "samples" in request:  # Every rendered layer's count and the policy too: held for every frame.
            from . import job_render_samples
            values[SAMPLES[self.scene.render.engine]] = request["samples"]
            values.update(job_render_samples.settings(self.window, request["samples"]))
        from . import job_render_scene
        # A still image format: a scene set to write a video offers no other until its media type changes.
        values.update({job_render_scene.FORMAT + "media_type": "IMAGE",
                       job_render_scene.FORMAT + "file_format": request["format"]})
        if request["format"] == "JPEG" and self.scene.render.image_settings.color_mode == 'RGBA':
            values["render.image_settings.color_mode"] = 'RGB'
        values.update(self.plan.settings(self.staging.path))
        values.update({"render.use_file_extension": True, "render.use_overwrite": True, "render.use_placeholder": False,
                       "frame_step": 1})
        self.overrides.apply(values, also_record=job_render_scene.FORMAT_DEPENDENTS)
        self.plan.check_paths(self.scene, self.start_frame)
        self.names = render_outputs.main_names(self.plan.outputs, self.start_frame, self.end_frame)
        with DisplayNone():
            self._invoke(bpy.ops.render.render, animation=True, frame_start=self.start_frame,
                         frame_end=self.end_frame, use_viewport=False)

    def _frames_done(self):
        """Finished frames: Blender writes a frame's own image files only once it finished rendering it."""
        written = {}
        for name in self.staging.files(self.suffixes):
            if name in self.names:
                written[self.names[name]] = written.get(self.names[name], 0) + 1
        per_frame = len(self.names) // self.total
        return sum(count == per_frame for count in written.values())

    def poll(self):
        self._check_output_path()
        progress = native().progress(self.scene, self.job_type)
        if progress is None:
            return None
        done, views = self._frames_done(), max(1, len(self.plan.views))
        # Blender renders a frame's views one after the other and starts its progress again for each.
        if done != self.frame_polled:
            self.frame_polled, self.view_pass = done, 0
        elif progress + 0.2 < self.last_progress:
            self.view_pass = min(self.view_pass + 1, views - 1)
        self.last_progress = progress
        within = (self.view_pass + progress) / views if done < self.total else 0
        self.reported = max(self.reported, (done + within) / self.total)  # Never backwards on the card.
        return {"progress": min(self.reported, 1.0 if done >= self.total else 0.999),
                "frames_done": done, "frames_total": self.total}

    def _finish(self, stopped):
        still = self.request["kind"] == "RENDER_STILL"
        found = render_outputs.sort_files(self.plan.outputs, self.start_frame, self.end_frame, self.staging.tree())
        if found["unexpected"]:  # Never publish what the approval did not list.
            status, reason, receipt = self._discard("failed", UNEXPECTED, NOT_PUBLISHED)
            receipt["unexpected"] = [name.rsplit("/", 1)[-1][:80] for name in found["unexpected"][:5]]
            return status, reason, receipt
        frames = found["frames"]
        if still:
            if stopped:
                return self._discard("cancelled", "", CANCELLED_NOTHING)
            if not frames:
                return self._discard("failed", n_("Blender ended the render without writing the image; see its Info log"),
                                     NOTHING_WRITTEN)
            if self.plan.single:
                return "done", "", self._publish_file(render_outputs.file_name(self.plan.outputs[0], frames[0]))
            return "done", "", self._publish_folder(self._receipt(found, frames[0]))
        complete = len(frames) == self.total and not stopped
        status = "done" if complete else "cancelled" if stopped else "failed"
        reason = "" if complete or stopped else n_("Blender ended the render early; see its Info log")
        if not frames:
            return self._discard(status, reason, n_("No frame finished; nothing was written."))
        self.staging.remove_files(found["drop"])  # An unfinished frame's File Output files: never half a frame.
        receipt = {} if self.plan.single else self._receipt(found, None)
        if not complete:
            # "kept" is the model's; the card counts with the numbers ("frames_total", in the language shown).
            receipt.update(kept="%d/%d frames" % (len(frames), self.total), frames_total=self.total,
                           note=n_("Kept the frames that finished; each is a whole frame."))
        return status, reason, self._publish_folder(receipt)

    def _receipt(self, found, frame):
        """The outputs that wrote a file (their identity for the agent), and the File Output items that wrote none."""
        silent = set(found["silent"])
        written = [output for index, output in enumerate(self.plan.outputs) if index not in silent]
        receipt = {"outputs": render_outputs.entries(written, frame)}
        if silent:
            receipt.update(missing=[entry["file"] for entry in render_outputs.entries(
                [self.plan.outputs[index] for index in sorted(silent)], frame)][:8], note=SILENT)
        return receipt

    def _published(self, receipt):
        receipt = super()._published(receipt)
        if self.folder_output and self.request["kind"] == "RENDER_STILL" and "output" not in receipt:
            receipt["output"] = self.output_name
        return receipt


def unresolved_usd_references(path):
    """The asset references of a published USD that don't resolve to an existing file (empty when all do)."""
    from pxr import UsdUtils
    _layers, assets, unresolved = UsdUtils.ComputeAllDependencies(str(path))
    return sorted({str(item) for item in unresolved} | {str(item) for item in assets if not Path(str(item)).is_file()})


class ExportRunner(Runner):
    """USD or Alembic, written by Blender's own background export job into staging, then published as a set."""
    publishes = "file_set"

    def __init__(self, window, request, workspace):
        super().__init__(window, request, workspace)
        self.job_type = "USD_EXPORT" if request["kind"] == "EXPORT_USD" else "ALEMBIC_EXPORT"
        # The USD exporter writes the materials' textures into ./textures and volumes into ./volumes beside the file
        # (_start; io/usd usd_writer_volume.cc): a set. Alembic writes the file alone.
        usd = request["kind"] == "EXPORT_USD"
        self.beside, self.publishes = (("textures", "volumes"), "file_set") if usd else ((), "file")
        self.animated = "frame_start" in request
        self.admit()

    def admit(self):
        start = self.request.get("frame_start", self.scene.frame_current)
        end = self.request.get("frame_end", start)
        self.frames = (start, end)
        check_work(self.request, 1, 1, 1, check_frames(start, end))

    @property
    def locks_interface(self):
        return True

    def describe(self):
        return {"frames": list(self.frames) if self.animated else None}

    def _start(self):
        path = str(self.staging.path / self.output.name)
        with bpy.context.temp_override(window=self.window):
            if self.job_type == "USD_EXPORT":
                if self.animated:  # USD exports the scene's frame range: set it for the job.
                    self.overrides.apply({"frame_start": self.frames[0], "frame_end": self.frames[1]})
                # Textures go beside the file (./textures), relative, so the published set is self-contained.
                result = bpy.ops.wm.usd_export(filepath=path, check_existing=False, as_background_job=True,
                                               export_animation=self.animated, export_textures_mode='NEW',
                                               relative_paths=True)
            else:
                result = bpy.ops.wm.alembic_export(filepath=path, check_existing=False, as_background_job=True,
                                                   start=self.frames[0], end=self.frames[1])
        if 'FINISHED' not in result:
            raise JobStartError("Blender did not start the export; see the Info log for its reason")

    def _finish(self, stopped):
        if stopped:
            return self._discard("cancelled", "", CANCELLED_NOTHING)
        if self.output.name not in self.staging.files((self.output.suffix.lower(),)):
            return self._discard("failed", n_("Blender ended the export without writing the file; see its Info log"),
                                 NOTHING_WRITTEN)
        size = cf.entry_info(self.staging.folder, self.output.name).st_size
        if self.job_type == "USD_EXPORT":   # Checked on the staged set (same layout), so publishing is the last step.
            missing = unresolved_usd_references(self.staging.path / self.output.name)
            if missing:  # Never report success with a reference that doesn't resolve.
                status, reason, receipt = self._discard("failed", DANGLING, NOT_PUBLISHED)
                receipt["missing"] = [name.rsplit("/", 1)[-1] for name in missing[:5]]
                return status, reason, receipt
        self.staging.publish_file(self.output.name)
        return "done", "", self._published({"bytes": size})


DANGLING = n_("The exported USD references files that don't exist; nothing was published")


def build(window, request, workspace, target=None, resolved=None):
    """The runner for one validated request (raises JobRequestError at admission).

    ``target`` and ``resolved`` are the datablocks the request's handles and resource IDs name
    (``job_host._admit``): a request never reaches Blender data by a name.
    """
    runner = _runner(window, request, workspace, target, resolved)
    render_extensions.bind(runner)  # Its approval names the add-on or script Python it would run and binds to it.
    return runner


def _runner(window, request, workspace, target, resolved):
    kind = request["kind"]
    if kind in {"RENDER_STILL", "RENDER_ANIMATION"}:
        return RenderRunner(window, request, workspace)
    from . import job_bakes
    if kind == "BAKE_TEXTURE":
        return job_bakes.TextureBakeRunner(window, request, workspace, target, **(resolved or {}))
    if kind == "BAKE_PHYSICS":
        return job_bakes.PhysicsBakeRunner(window, request, workspace)
    if kind == "BAKE_SIMULATION":
        return job_bakes.SimulationBakeRunner(window, request, workspace, target)
    return ExportRunner(window, request, workspace)
