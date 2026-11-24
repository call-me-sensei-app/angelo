# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bake check's pictures, drawn in Blender (``bake_check``; doc/angelo/long_jobs.rst, "Bake check").

``Pictures(target, baked, sources)`` draws what the bake read (the target's own materials, or for a high-to-low bake
its sources) and the target with the baked material, from ``bake_check.views`` framing the target, one picture per
render, in a scene of its own. A job's ``step()`` never holds the main thread for a render: it starts each picture as
Blender's render job (as the person's F12 does, without opening a render view: ``job_settings.DisplayNone``) and
reads it once that job has ended, so the interface keeps moving (a picture rendered in place held it 0.6 s on the
reference Mac: bake_maps_job's stall check, 2026-11-20); ``check()`` renders in place, for scripts. The scene has
linked copies (the baked one with the baked material on object-level slots), one sun and a dim uniform world,
Cycles with a fixed seed and the denoiser, transparent film for the coverage. The person's scene, objects, mesh and
materials are only read, and everything it adds is removed by ``close`` (also when it fails); its renders go into
Angelo's own Render Result slot and the person's slot is put back (``view_render_restore``), so their last render
stays. ``facts()`` is ``bake_check.compare`` of the pictures.
"""

import os
import time

import numpy as np

import bpy
from mathutils import Vector

from . import bake_check, color_spaces, edit_tool_hold, private_roots
from .view_framing import SENSOR_WIDTH_MM
from .job_settings import DisplayNone
from .view_render_restore import ResultSlot

SIZE = 128            # Pixels on a side: enough for the worst tile (bake_check.TILE) to mean a region.
SAMPLES = 32          # With the denoiser and one seed, an unchanged material compares well under 1 delta E.
SUN = (0.6, 0.3, 0.75)   # The light's direction toward it: over the camera's shoulder, so shading reads.
SUN_STRENGTH = 3.0
WORLD = 0.08          # A dim, even fill: shadows keep their colour without a second light.
SCENE_NAME = "Angelo Bake Check"
RENDER_JOB = "RENDER"   # Blender's render job type, as job_runners' render runners name it.
CLOSE_POLL = 0.05     # Seconds between looks for a stopped check render's end before its scene is removed.
# Starting a render job does what the person's F12 does first (render_internal.cc, screen_render_invoke): it stops
# animation playback and ends every other running job, waiting for each on the main thread. A check picture starts
# only when that would end nothing: no playback, no one's work (``_angelo_jobs.running_work``), and none of the job
# types below (Blender's own query). Listings and prefetches it doesn't see relaunch by themselves. An Angelo-started
# render without F12's setup is the next release's native option (doc/angelo/long_jobs.rst, "Bake check").
QUIET_TYPES = ("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE", "COMPOSITE", "SHADER_COMPILATION")
START_WAIT = 30.0     # Seconds one picture waits for a quiet moment before the check gives up, saying so in its facts.
RETRIES = 1           # Another render ended a picture's job (the person's F12, the status bar's cancel): tries again.
_foreign_renders = [0]   # Renders of other scenes started since import (``_count_render``, a handler that only marks).


class Pictures:
    def __init__(self, target, baked, sources=(), size=SIZE, samples=SAMPLES, blocking=False):
        self.target, self.baked, self.sources = target, baked, tuple(sources)
        self.size, self.samples, self.blocking = size, samples, blocking
        self._reset_run()
        self.originals, self.bakeds = [], []
        self.ids = []
        # Angelo's own scratch folder (private_roots): its pictures go with it at close.
        self.scratch = private_roots.TemporaryDirectory("angelo-bake-check-", ignore_cleanup_errors=True)
        self.folder = self.scratch.name
        corners = [target.matrix_world @ Vector(corner) for corner in target.bound_box]
        center = sum(corners, Vector()) / 8.0
        radius = max((corner - center).length for corner in corners) or 1.0
        self.views = bake_check.views(tuple(center), radius)
        self.scene = None
        self.copies = ()
        self.camera = None
        self.had_result = _render_result() is not None   # A Render Result the check makes, it removes again.
        # The deferred close, through the one choke point for Angelo's main-thread work (edit_tool_hold): it removes
        # data-blocks and the person's Render Result slot, so never during the person's own operation.
        self._close_timer = edit_tool_hold.guarded(self._close_later)

    def _reset_run(self):
        self.pending, self.slot = None, None   # The picture whose render job runs: (path, which).
        self.cancelled, self.retries, self.foreign_since = False, 0, None
        self.waiting_since, self.waited, self.gave_up = None, 0.0, None

    def _own(self, block):
        self.ids.append(block)
        return block

    def _setup(self):
        scene = self.scene = self._own(bpy.data.scenes.new(SCENE_NAME))
        render = scene.render
        render.engine = "CYCLES"
        render.resolution_x = render.resolution_y = self.size
        render.resolution_percentage = 100
        render.film_transparent = True
        # The copies share the person's meshes and materials: while the job thread reads them (its sync), edits wait,
        # as Lock Interface does for the person's own render (doc/angelo/long_jobs.rst, "Bake check").
        render.use_lock_interface = True
        render.image_settings.file_format, render.image_settings.color_depth = "OPEN_EXR", "32"
        color_spaces.view_untonemapped(scene.view_settings)   # The pictures compare display values as they are.
        cycles = scene.cycles
        cycles.samples, cycles.seed, cycles.use_denoising = self.samples, 0, True
        cycles.device = "GPU" if _gpu() else "CPU"
        world = self._own(bpy.data.worlds.new(SCENE_NAME))
        world.color = (WORLD, WORLD, WORLD)
        scene.world = world
        sun = self._own(bpy.data.objects.new(SCENE_NAME + " Sun", self._own(bpy.data.lights.new(SCENE_NAME, "SUN"))))
        sun.data.energy = SUN_STRENGTH
        sun.rotation_euler = Vector(SUN).to_track_quat("Z", "Y").to_euler()
        scene.collection.objects.link(sun)
        camera_data = self._own(bpy.data.cameras.new(SCENE_NAME))
        camera_data.sensor_width = SENSOR_WIDTH_MM
        self.camera = self._own(bpy.data.objects.new(SCENE_NAME + " Camera", camera_data))
        scene.collection.objects.link(self.camera)
        scene.camera = self.camera
        # Copies share their meshes, so the originals show their own materials.
        originals = [self._own(obj.copy()) for obj in (self.sources or (self.target,))]
        baked = self._own(self.target.copy())
        for slot in baked.material_slots:
            slot.link, slot.material = "OBJECT", self.baked
        for copy, source in [(copy, obj) for copy, obj in zip(originals, self.sources or (self.target,))] + [
                (baked, self.target)]:
            copy.parent = None
            copy.matrix_world = source.matrix_world
            scene.collection.objects.link(copy)
        self.copies = (originals, [baked])

    def done(self):
        return self.gave_up is not None or len(self.bakeds) == len(self.views)

    def step(self):
        """Advance by one picture; True once every view is drawn with both materials, or once the check gave up
        waiting (``facts`` says so). A job's step starts the next picture's render job when that would end none of
        the person's work, or, once that job has ended, reads its picture; ``blocking`` renders in place."""
        if self.scene is None:
            self._setup()
        if self.pending is None:
            if not self.blocking and not self._quiet():
                return self.done()
            self._begin()
            if not self.blocking:
                return False
        elif self.busy():
            return False
        return self._collect()

    def _quiet(self):
        """Whether a picture may start now; while not, the wait counts toward ``START_WAIT``."""
        blockers = blockers_now()
        now = time.monotonic()
        if not blockers:
            if self.waiting_since is not None:
                self.waited += now - self.waiting_since
            self.waiting_since = None
            return True
        self.waiting_since = self.waiting_since or now
        if now - self.waiting_since > START_WAIT:
            self.waited += now - self.waiting_since
            self.gave_up = {"skipped": "busy", "waited_s": round(self.waited, 1), "blocked_by": blockers[:4],
                            "pictures": len(self.originals) + len(self.bakeds)}
        return False

    def busy(self):
        """Whether this check's render job still runs (never, rendering in place; never, once its scene is freed)."""
        if self.pending is None or self.blocking or self.scene is None:
            return False
        try:
            return _native().progress(self.scene, RENDER_JOB) is not None
        except ReferenceError:
            return False

    def cancel(self):
        """Stop this check's running render once (a stopped job); ``busy`` turns False when Blender has ended it."""
        if self.busy() and not self.cancelled:
            self.cancelled = True
            _native().stop(self.scene, RENDER_JOB)

    def _begin(self):
        index = len(self.bakeds)
        which = 0 if len(self.originals) == index else 1
        view = self.views[index]
        location = Vector(view["location"])
        self.camera.location = location
        self.camera.rotation_euler = (Vector(view["look_at"]) - location).to_track_quat("-Z", "Y").to_euler()
        self.camera.data.lens = view["lens"]
        for number, group in enumerate(self.copies):
            for copy in group:
                copy.hide_render = number != which
        path = os.path.join(self.folder, "%d-%d.exr" % (index, which))
        self.scene.render.filepath = path
        _count_renders()
        self.slot = ResultSlot()
        self.slot.enter()
        self.pending, self.foreign_since = (path, which), _foreign_renders[0]
        try:
            if self.blocking:
                bpy.ops.render.render(write_still=True, scene=self.scene.name)
                return
            with DisplayNone():
                result = bpy.ops.render.render("INVOKE_DEFAULT", write_still=True, scene=self.scene.name,
                                               use_viewport=False)
            if not {"RUNNING_MODAL", "FINISHED"} & set(result):
                raise RuntimeError("Blender did not start the bake check's render")
        except BaseException:
            self._leave_slot()
            self.pending = None
            raise

    def _foreign(self):
        """Whether another scene's render started since this picture's (the person's F12 renders into the active
        slot, which is Angelo's while a picture runs)."""
        return self.foreign_since is not None and _foreign_renders[0] != self.foreign_since

    def _leave_slot(self):
        """Put the person's Render Result slot back, unless another render ran since: what that render made stays
        where it is, in view."""
        if self.slot is not None:
            if not self._foreign():
                self.slot.leave()
            self.slot = None

    def _collect(self):
        path, which = self.pending
        # Not stopped by the job yet no picture: another render (the person's F12, which ends this one) or the status
        # bar's cancel ended it, or Blender refused it inside its job.
        interrupted = not os.path.isfile(path) and not self.cancelled
        self._leave_slot()
        self.pending = None
        if not os.path.isfile(path):   # Cancelled, ended by another render, or refused inside its job.
            if interrupted and self.retries < RETRIES:
                self.retries += 1
                return False   # The same picture again, at the next quiet moment.
            raise RuntimeError("The bake check's render was interrupted (another render, or its cancel button)"
                               if interrupted else "The bake check's render wrote no picture")
        image = bpy.data.images.load(path, check_existing=False)
        try:
            width, height = image.size
            pixels = np.empty(width * height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
        finally:
            bpy.data.images.remove(image)   # The file stays in the scratch folder until close.
        picture = pixels.reshape(height, width, 4)[::-1]   # Rows from the top.
        (self.originals if which == 0 else self.bakeds).append(picture)
        return self.done()

    def facts(self):
        if self.gave_up is not None:
            return dict(self.gave_up)
        facts = bake_check.compare(self.originals, self.bakeds)
        if self.waited:
            facts["waited_s"] = round(self.waited, 1)
        return facts

    def close(self):
        """Remove everything the check added (scene, copies, camera, light, world); safe to call twice. With its render
        job still running, that job is stopped first and the removal waits for it to end, on a guarded timer: the job
        renders the check's scene, so removing it under the job would free what the job reads. The timer is not
        persistent on purpose: a file load ends the job before it frees the document, and the scratch folder's
        finalizer and family sweep remove the pictures."""
        if self.busy():
            self.cancel()
            if not bpy.app.timers.is_registered(self._close_timer):
                bpy.app.timers.register(self._close_timer, first_interval=CLOSE_POLL)
            return
        foreign = self._foreign()
        self.pending = None
        self._leave_slot()
        for block in reversed(self.ids):
            try:
                collection = getattr(bpy.data, _collection(block))
                if block.name in collection and collection[block.name] == block:
                    collection.remove(block)
            except ReferenceError:
                pass
        self.ids = []
        result = _render_result()
        if result is not None and not self.had_result and not foreign and not _shown(result):
            bpy.data.images.remove(result)
        self.scratch.cleanup()

    def _close_later(self):
        if self.busy():
            return CLOSE_POLL
        self.close()
        return None


def advance(pictures, stopping, run_step):
    """One poll of a job's check of one target: None while it must be polled again, else its facts (the caller then
    closes it). Stopping, a running picture's job is stopped and the facts wait for that job's end, since it reads the
    check's scene; an error is a fact, never a failure (the maps and materials are done)."""
    if stopping:
        if pictures.busy():
            pictures.cancel()
            return None
        return {"skipped": "stopped"}
    try:
        return pictures.facts() if run_step() else None
    except Exception as error:
        return {"error": str(error)[:200]}


def blockers_now():
    """What starting a render job would end now: playback in a window, someone's running work, a running job of the
    types F12's setup ends (``QUIET_TYPES``). Empty when a picture may start."""
    found = []
    try:
        from . import live_windows   # The one window walk: skips a window between screens (no screen to ask).
        if any(window.screen.is_animation_playing for window in live_windows.windows(bpy.context.window_manager)):
            found.append("playback")
    except (AttributeError, ReferenceError):
        pass
    try:
        found += ["%s:%s" % pair for pair in _native().running_work()]
    except (ImportError, AttributeError):
        pass
    for kind in QUIET_TYPES:
        if bpy.app.is_job_running(kind) and not any(entry.startswith(kind + ":") for entry in found):
            found.append(kind)
    return found


def _count_render(scene, *_args):
    """``render_init``: a render of another scene than a check's started (marks only; the next poll reads it)."""
    if getattr(scene, "name", None) != SCENE_NAME:
        _foreign_renders[0] += 1


def _count_renders():
    if _count_render not in bpy.app.handlers.render_init:
        bpy.app.handlers.render_init.append(_count_render)


def _native():
    from _bpy import _angelo_jobs   # Blender's own job list, as the job runners read it (job_runners.native).
    return _angelo_jobs


def _collection(block):
    for kind, name in ((bpy.types.Scene, "scenes"), (bpy.types.World, "worlds"), (bpy.types.Object, "objects"),
                       (bpy.types.Camera, "cameras"), (bpy.types.Light, "lights")):
        if isinstance(block, kind):
            return name
    raise TypeError(type(block).__name__)


def _render_result():
    return next((image for image in bpy.data.images if image.type == "RENDER_RESULT"), None)


def _shown(image):
    """Whether an editor shows ``image`` (removing it then would empty that editor under the person)."""
    from . import live_windows
    return any(getattr(area.spaces.active, "image", None) == image
               for _window, area in live_windows.window_areas(bpy.context.window_manager))


def _gpu():
    prefs = bpy.context.preferences.addons.get("cycles")
    return prefs is not None and prefs.preferences.compute_device_type not in ("", "NONE")


def check(target, baked, sources=(), size=SIZE, samples=SAMPLES):
    """Every picture at once and the facts (scripts and tests; a job steps ``Pictures`` itself)."""
    pictures = Pictures(target, baked, sources, size, samples, blocking=True)
    try:
        while not pictures.step():
            pass
        return pictures.facts()
    finally:
        pictures.close()
