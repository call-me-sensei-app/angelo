# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Mantaflow (smoke, fire, liquid) bake as a background job: ``BAKE_SIMULATION`` whose target is a fluid domain.

A fluid domain caches to a directory it names, and a script's ``fluid.bake_all``
bakes on the main thread (the app freezes and nothing stops it). This runner
bakes it as Blender's own job, into the job's folder, with progress and Cancel,
the same contract as the geometry-nodes disk bake (``job_bake_caches``):

* **Where it writes.** While it runs, the domain's cache directory is a folder of
  its own in the job's staging folder. When every frame of every cache the domain
  writes is there (``job_fluid_plan.missing``), the folder is published and the
  domain reads it by a path from the .blend (``//<output>/<modifier>``). That is
  the one scene change a finished bake leaves, with one more for a domain whose
  cache type was Replay (which simulates during playback and is not a bake): it
  becomes All, so the published cache is what plays.
* **Its state, never through RNA.** Setting a domain's cache type through RNA
  frees its cache, which deletes the files in whatever directory it names then,
  and its baked flags and pause frames cannot be set at all. So the job reads and
  writes that state directly (``_bpy._angelo_jobs.fluid_state`` and
  ``fluid_state_set``), keeps the person's state on the domain while it runs
  (``TAG``, so a file saved meanwhile is put back when it is opened:
  ``restore_tags``), and puts it back exactly on every outcome but a finished bake,
  a failed publication included (``finish``). The native write re-evaluates the
  domain as a side effect, never as a user edit, which would make Blender free a
  Replay cache at the start frame. Nothing of the person's cache is written or
  deleted: an earlier cache stays on disk and in use after a cancel, and on disk,
  unreferenced, after a finished bake. The scene frame a stopped bake leaves goes
  back too.
* **Stopping.** Mantaflow checks only ``G.is_break``; the native stop sets it for
  the ``FLUID`` job type. Escape or Pause Bake in Blender stops it too: that is a
  cancel ("stopped in Blender"), told apart from a failure by Blender's pause frame.
* **Whole means whole.** Every frame of every cache has a non-empty file, and
  Blender reported no bake error (``fluid_state``'s ``error``); otherwise nothing
  is published.
* **Before it starts** (``admit``, again when a queued job starts): a saved file
  whose folder is the chat's workspace; a folder name Mantaflow would not rewrite
  and a cache path Blender would not cut short (``MAX_DIRECTORY``); no fluid bake
  already running in the scene; a domain evaluated in the viewport, with a size on
  every axis (its mesh times its scale, as Blender measures it) and at least one
  flow that adds fluid of its kind (in its flow collection when it names one);
  frames within the job bounds; no more files than a publication walks (each
  cache by its own format, a liquid mesh's speed vectors included); the memory its
  grid needs within the computer's; at least ``DISK_MARGIN`` free. The approval states the grid,
  voxel size, frames, caches, the estimates and the free space.
* **While it runs**, the bytes per frame actually written are measured; when the
  remaining frames would not fit on the disk, or the files written approach the
  publication bound, the job stops and says so.

Refusals and reasons are model-facing English; fixed ones are marked with ``n_``.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

from . import job_bake_caches as caches
from . import job_fluid_plan as plan
from . import job_render_outputs
from .job_manager import JobStartError
from .job_outputs import MAX_ENTRIES, OutputMoved
from .job_runners import Runner, native
from .job_spec import JobRequestError, check_frames, check_work
from .ui_text import n_

TAG = "_angelo_fluid_restore"
CACHE_ALL = 2
REPLAY = "REPLAY"
DISK_CHECK_SECONDS = 5.0
FLUID_REMOVED = n_("The partial cache was removed; the fluid domain's cache settings are as they were.")
NOT_WHOLE = n_("Angelo ended the bake before every frame was cached; see its Info log")
LOW_DISK = n_("The disk would have filled before the bake ended, so it was stopped; free some space or lower the "
              "domain's resolution or frame range")
STOPPED_IN_BLENDER = n_("Stopped in Angelo (Escape or Pause Bake); the partial cache was removed")
BAKE_ERROR = n_("Angelo reported an error while baking (see its Info log); nothing was published")
TOO_MANY = n_("The bake was about to write more files than a job publishes, so it was stopped; ask the person for "
              "a shorter frame range")
MAX_LISTED = 8
# Bytes of the cache directory. Blender copies it into a FILE_MAXDIR (768) buffer before baking
# (fluid_validate_paths) and Mantaflow joins folder and file names below it: a longer path would be cut short and
# the cache written outside the job's folder.
MAX_DIRECTORY = 700
ENTRY_MARGIN = 100   # The bake stops this many entries before a publication's walk bound.


def fluid_modifier(obj):
    """The object's Mantaflow domain modifier, or None."""
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == 'FLUID' and modifier.fluid_type == 'DOMAIN':
            return modifier
    return None


def is_domain(obj):
    try:
        return fluid_modifier(obj) is not None
    except ReferenceError:
        return False


def _flows(scene, target, settings):
    """The scene's flow objects that would bring fluid of the domain's kind into it (``fluid_flow_objects``).

    A flow counts when its Fluid modifier is evaluated in the viewport, the object is not disabled there, it adds
    fluid (inflow or geometry, never only an outflow), its type is the domain's (smoke, fire or both for a gas,
    liquid for a liquid), and it is in the domain's flow collection when the domain names one.
    """
    group = settings.fluid_group
    members = set(group.all_objects) if group is not None else None
    found = []
    for obj in scene.objects:
        if obj is target or obj.hide_viewport or (members is not None and obj not in members):
            continue
        for modifier in obj.modifiers:
            if modifier.type != 'FLUID' or modifier.fluid_type != 'FLOW' or not modifier.show_viewport:
                continue
            flow = modifier.flow_settings
            liquid = flow.flow_type == 'LIQUID'
            if flow.flow_behavior != 'OUTFLOW' and liquid == (settings.domain_type == 'LIQUID'):
                found.append(obj)
                break
    return found


def _input_size(obj):
    """The domain's size as Blender measures it for its grid: its mesh's extent times its scale (``fluid.cc``).

    ``Object.dimensions`` is the evaluated result (a liquid's surface, an adaptive domain), not the box the grid
    is laid over.
    """
    vertices = obj.data.vertices if obj.type == 'MESH' else ()
    if not vertices:
        return tuple(abs(float(side)) for side in obj.dimensions)
    corners = [vertex.co for vertex in vertices]
    return tuple((max(co[i] for co in corners) - min(co[i] for co in corners)) * abs(obj.scale[i]) for i in range(3))


def _rebased(directory, saved_blend):
    """A ``//`` cache directory recorded beside ``saved_blend``, made relative to the file as it is saved now."""
    current = bpy.data.filepath
    if not directory.startswith("//") or not saved_blend or not current or \
            os.path.dirname(saved_blend) == os.path.dirname(current):
        return directory
    absolute = os.path.normpath(os.path.join(os.path.dirname(saved_blend), directory[2:]))
    return bpy.path.relpath(absolute)


def _world_box(obj):
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return [min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)]


def _overlaps(first, second):
    (low_a, high_a), (low_b, high_b) = first, second
    return all(low_a[i] <= high_b[i] and low_b[i] <= high_a[i] for i in range(3))


def physical_memory():
    """Bytes of physical memory, or None where it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64),
                            ("available", ctypes.c_uint64), ("page_total", ctypes.c_uint64),
                            ("page_available", ctypes.c_uint64), ("virtual_total", ctypes.c_uint64),
                            ("virtual_available", ctypes.c_uint64), ("extended", ctypes.c_uint64)]
            status = Status(ctypes.sizeof(Status))
            return status.total if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        return None


def _free_space(path):
    """Free bytes on the disk holding ``path`` (its nearest existing folder)."""
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return shutil.disk_usage(path).free


def _state_tuple(state, saved_blend=""):
    return (state["cache_type"], state["cache_flag"], tuple(state["pause"]),
            _rebased(state["directory"], saved_blend))


def restore_tags(busy=()):
    """Put back the domains a file saved while a fluid job ran still carry (their job is not running here).

    A tag is the file's data, so a malformed one is dropped with a log line and never stops the other objects or
    the rest of the load handler. It can set only what the file's own domain settings could already hold.
    """
    try:
        jobs = native()
    except JobStartError:
        return
    for obj in list(bpy.data.objects):
        try:
            value = obj.get(TAG) if obj.library is None else None
            if not isinstance(value, str):
                continue
            saved = json.loads(value)
            if isinstance(saved, dict) and saved.get("job") in busy:
                continue
            if isinstance(saved, dict) and isinstance(saved.get("state"), dict) and fluid_modifier(obj) is not None:
                jobs.fluid_state_set(obj, _state_tuple(saved["state"], saved.get("blend") or ""))
        except Exception as error:   # A file is untrusted data: one bad tag never stops the load.
            print("Angelo: could not put a fluid domain back:", obj.name, type(error).__name__, error)
        try:
            if TAG in obj:
                del obj[TAG]
        except Exception:
            pass


class FluidBakeRunner(Runner):
    """Bake one Mantaflow domain (``target``) into the job's folder, then point the domain at it."""
    job_type = "FLUID"

    def __init__(self, window, request, workspace, target):
        super().__init__(window, request, workspace)
        self.target, self.saved, self.low_disk, self.lacking = target, None, None, {}
        self.checked_at, self.incomplete, self.too_many, self.end_state = 0.0, True, None, None
        self.saved_blend, self.saved_frame = "", None
        self.admit()

    # Admission -----------------------------------------------------------

    def admit(self):
        target = self.target
        try:
            modifier = fluid_modifier(target)
            linked = target.library is not None or target.override_library is not None
        except ReferenceError:
            raise JobRequestError("The fluid domain to bake was removed") from None
        if modifier is None:
            raise JobRequestError("%s is not a fluid domain" % target.name)
        if linked:
            raise JobRequestError("%s is linked from another file; a bake needs an object of this file" % target.name)
        if self.output is None:
            raise JobRequestError("A fluid domain caches to disk: pass output (a new workspace folder) for its cache")
        caches.blend_relative(self.output.parent)
        unsafe = plan.unsafe_path(str(self.output), windows=sys.platform == "win32")
        if unsafe is not None:
            raise JobRequestError("Mantaflow rewrites the folder name %r (it has < > : \" | ? * or a control "
                                  "character), so its cache would land elsewhere; choose another output" % unsafe)
        settings = modifier.domain_settings
        if settings.is_cache_baking_any or native().progress(self.scene, self.job_type) is not None:
            raise JobRequestError("A fluid bake already runs in this scene; wait for it or stop it first")
        if not modifier.show_viewport or target.hide_viewport or target.name not in self.window.view_layer.objects:
            raise JobRequestError("%s is disabled in the viewport or not in the view layer, so Angelo would bake "
                                  "nothing; enable it first" % target.name)
        # <output folder's parent>/.angelo-job-<id>/<modifier folder>, the id at most 10 characters.
        length = len(str(self.output.parent).encode("utf-8")) + len(".angelo-job-j123456789//") + len(
            modifier.name.encode("utf-8"))
        if length > MAX_DIRECTORY:
            raise JobRequestError("The cache folder's path would be %d bytes long; Angelo cuts a fluid cache path at "
                                  "%d. Choose an output in a folder with a shorter path" % (length, MAX_DIRECTORY))
        size = _input_size(target)
        if min(size) <= 1e-6:
            raise JobRequestError("%s has no size on one axis; a fluid domain needs a volume" % target.name)
        kind = settings.domain_type
        self.cells = plan.grid(size, settings.resolution_max)
        noise = settings.noise_scale if kind == 'GAS' and settings.use_noise else 0
        self.estimate = plan.estimate(self.cells, kind, noise_scale=noise,
                                      particles_per_cell=settings.particle_number if kind == 'LIQUID' else 0)
        memory = physical_memory()
        if memory and self.estimate["memory"] > memory:
            needed = (target.name,) + self.cells + (plan.gib(self.estimate["memory"]), plan.gib(memory))
            raise JobRequestError("%s's %d×%d×%d grid needs about %.1f GiB of memory while it bakes (an estimate); "
                                  "this computer has %.1f GiB. Ask the person to lower the domain's resolution" % needed)
        self.frames = (settings.cache_frame_start, settings.cache_frame_end)
        count = check_frames(self.frames[0], self.frames[1], "the fluid domain's cache")
        check_work(self.request, 1, 1, 1, count)
        self.free = _free_space(self.output.parent)
        if self.free < plan.DISK_MARGIN:
            raise JobRequestError("Less than %d GiB is free on the disk that would hold the cache"
                                  % (plan.DISK_MARGIN // plan.GiB))
        domain_box = _world_box(target)
        flows = _flows(self.scene, target, settings)
        if not flows:
            raise JobRequestError("No %s flow object in the scene: nothing would enter %s, so the bake would be "
                                  "empty" % ("liquid" if kind == 'LIQUID' else "smoke or fire", target.name))
        self.outside = [obj.name for obj in flows if not _overlaps(_world_box(obj), domain_box)]
        self.flows = [obj.name for obj in flows]
        written = ["data"] + (["noise"] if noise else [])
        if kind == 'LIQUID':
            written += (["mesh"] if settings.use_mesh else []) + (
                ["particles"] if settings.use_spray_particles or settings.use_foam_particles
                or settings.use_bubble_particles else [])
        if settings.use_guide:
            written.append("guiding")
        self.written, self.kind, self.modifier_name = written, kind, modifier.name
        self.folder = job_render_outputs.folder_name(modifier.name, set())
        self.expected = plan.expected_files(written, self.frames)
        files = plan.most_files(written, self.frames, one_file_per_frame=settings.cache_data_format == 'OPENVDB',
                                mesh_files=1 + bool(kind == 'LIQUID' and settings.use_speed_vectors))
        if files > MAX_ENTRIES - ENTRY_MARGIN:
            raise JobRequestError("A bake of %s's %s over frames %d to %d can write more files than a job publishes "
                                  "(%d); ask the person for a shorter frame range"
                                  % (target.name, ", ".join(written), self.frames[0], self.frames[1], MAX_ENTRIES))
        self.replay, self.earlier = settings.cache_type == REPLAY, settings.has_cache_baked_any
        # A domain a script left without a cache folder: Blender's Replay writes its frames beside the app's
        # working directory. A finished bake gives it the published folder, so this is a fact, not a refusal.
        self.no_folder = not settings.cache_directory.strip()

    @property
    def locks_interface(self):
        return True

    def describe(self):
        size = tuple(round(float(side), 4) for side in _input_size(self.target))
        facts = {"objects": [self.target.name], "fluid": self.kind.lower(), "cells": list(self.cells),
                 "voxel": round(max(size) / max(self.cells), 6), "size": list(size), "frames": list(self.frames),
                 "cache_kinds": list(self.written),
                 "memory_gib": plan.gib(self.estimate["memory"]),
                 "disk_gib_at_most": plan.gib(self.estimate["frame"] * (self.frames[1] - self.frames[0] + 1)),
                 "disk_free_gib": plan.gib(self.free), "flows": self.flows[:MAX_LISTED]}
        if self.outside:
            facts["outside"] = self.outside[:MAX_LISTED]
        if self.replay:
            facts["replay"] = True
        if self.earlier:
            facts["earlier_cache"] = True
        if self.no_folder:
            facts["no_cache_folder"] = True
        return facts

    # Running -------------------------------------------------------------

    def _selection(self):
        return {"active_object": self.target, "object": self.target, "selected_objects": [self.target]}

    def _start(self):
        jobs = native()
        directory = str(Path(self.staging.path) / self.folder)
        if len(directory.encode("utf-8")) > MAX_DIRECTORY:
            raise JobStartError("The cache folder's path is too long for Angelo's fluid bake; choose a shorter output")
        self.saved, self.saved_blend, self.saved_frame = jobs.fluid_state(self.target), bpy.data.filepath, \
            self.scene.frame_current
        self.target[TAG] = json.dumps({"job": self.identity, "state": self.saved, "blend": self.saved_blend})
        cache_type = CACHE_ALL if self.replay else self.saved["cache_type"]
        jobs.fluid_state_set(self.target, (cache_type, 0, (0, 0, 0, 0, 0), directory))
        self._invoke(bpy.ops.fluid.bake_all, self._selection())
        if jobs.progress(self.scene, self.job_type) is None:
            raise JobStartError("Angelo did not start the fluid bake; see the Info log for its reason")

    def _undo_start(self):
        try:
            self._put_back()
        finally:
            super()._undo_start()

    def _put_back(self):
        """The person's cache state and frame as they were, and the tag gone. Nothing on disk is touched.

        Idempotent: the first call puts back, later ones do nothing (``saved`` is cleared only after it worked).
        """
        if self.saved is None:
            return
        try:
            if fluid_modifier(self.target) is not None:
                native().fluid_state_set(self.target, _state_tuple(self.saved, self.saved_blend))
            if TAG in self.target:
                del self.target[TAG]
        except ReferenceError:
            pass   # The domain was removed meanwhile: nothing of it is left to put back.
        self.saved = None
        if self.saved_frame is not None and self.scene.frame_current != self.saved_frame:
            self.scene.frame_current = self.saved_frame   # A stopped bake leaves the frame where it stopped.

    def poll(self):
        status = super().poll()
        now = time.monotonic()
        if self.staging is None or self.low_disk is not None or now - self.checked_at < DISK_CHECK_SECONDS:
            return status
        self.checked_at = now
        written, names = 0, []
        for folder, _dirs, files in os.walk(self.staging.path):
            for name in files:
                try:
                    written += os.stat(os.path.join(folder, name), follow_symlinks=False).st_size
                except OSError:
                    continue
                names.append(os.path.relpath(os.path.join(folder, name), self.staging.path).replace(os.sep, "/"))
        if len(names) > MAX_ENTRIES - ENTRY_MARGIN:   # A publication would refuse it: stop now, not after hours.
            self.too_many = len(names)
            self.stop()
            return status
        prefix = self.folder + "/"
        done = plan.frames_written([name[len(prefix):] for name in names if name.startswith(prefix)])
        total = self.frames[1] - self.frames[0] + 1
        needed, fits = plan.disk_outlook(written, done, total - done, _free_space(self.staging.path))
        if not fits:
            free = _free_space(self.staging.path)
            self.low_disk = {"disk_needed_gib": plan.gib(needed), "disk_free_gib": plan.gib(free)}
            self.stop()
        return status

    def _staged_sizes(self):
        """``{path below the cache folder: bytes}`` of what the bake wrote (no link: ``Staging.tree`` refuses one)."""
        prefix, root = self.folder + "/", Path(self.staging.path)
        return {path[len(prefix):]: os.stat(root / path, follow_symlinks=False).st_size
                for path in self.staging.tree() if path.startswith(prefix)}

    def _guarded(self):
        return self.low_disk is not None or self.too_many is not None

    def finish(self, stopped):
        """Check the cache, then restore and publish or discard; the domain is put back on every outcome but done."""
        try:
            if fluid_modifier(self.target) is not None:
                self.end_state = native().fluid_state(self.target)   # Blender's error and pause frame, as it ended.
            if self.moved is None and not self._guarded():
                self.lacking = plan.missing(self.expected, self._staged_sizes())
                self.incomplete = bool(self.lacking)
        except OutputMoved as error:   # A link or too many entries in staging: nothing is published.
            self.moved = error
        except Exception as error:     # The check could not prove the cache whole: it is not.
            print("Angelo: checking the fluid cache failed:", type(error).__name__, error)
            self.incomplete = True
        if stopped or self.incomplete or self.moved is not None or self._guarded() or self._error():
            self._put_back()   # Before anything else: the domain must never name the staging folder again.
        result = None
        try:
            result = super().finish(stopped)
            return result
        finally:
            if result is None or result[0] != "done":
                self._put_back()   # Publication failed, or anything after it: the person's state, whatever happened.

    def _error(self):
        return ((self.end_state or {}).get("error") or "").strip()

    def _finish(self, stopped):
        if self.low_disk is not None:
            status, reason, receipt = self._discard("failed", LOW_DISK, FLUID_REMOVED)
            return status, reason, dict(receipt, **self.low_disk)
        if self.too_many is not None:
            return self._discard("failed", TOO_MANY, FLUID_REMOVED)
        if stopped:
            return self._discard("cancelled", "", FLUID_REMOVED)
        if self._error():
            status, reason, receipt = self._discard("failed", BAKE_ERROR, FLUID_REMOVED)
            return status, reason, dict(receipt, blender_error=self._error()[:64])
        if self.incomplete:
            pause = (self.end_state or {}).get("pause", (self.frames[1],))[0]
            if pause < self.frames[1]:   # Blender's own loop stopped early: Escape or Pause Bake.
                return self._discard("cancelled", STOPPED_IN_BLENDER, FLUID_REMOVED)
            status, reason, receipt = self._discard("failed", NOT_WHOLE, FLUID_REMOVED)
            return status, reason, dict(receipt, missing={cache: frames[:MAX_LISTED]
                                                          for cache, frames in self.lacking.items()})
        self.staging.publish_folder()
        # Before the staging is released: a failure here unpublishes (job_manager) and ``finish`` puts the domain back.
        state = native().fluid_state(self.target)
        directory = "%s/%s" % (caches.blend_relative(self.staging.published), self.folder)
        native().fluid_state_set(self.target, (state["cache_type"], state["cache_flag"], tuple(state["pause"]),
                                               directory))
        output = {"file": self.folder, "cache": self.modifier_name, "name": self.target.name,
                  "frames": list(self.frames)}
        receipt = self._published({"objects": 1, "caches": len(self.written), "outputs": [output]})
        if TAG in self.target:
            del self.target[TAG]
        self.saved = None   # Done: from here on the published cache is the domain's.
        return "done", "", receipt
