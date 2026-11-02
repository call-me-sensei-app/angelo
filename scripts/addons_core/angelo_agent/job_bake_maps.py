# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``BAKE_MAPS``: every PBR map of one or more objects as a texture set, on Blender's own bake jobs.

Contract: doc/angelo/long_jobs.rst, "Texture sets". Same contract as
``job_runners.Runner``: admitted before approval and again at start, Blender's
progress and Cancel, the job's settings restored, its files staged and published
all at once. Each target's ``bake_maps_plan.TargetPlan`` lists the bakes it needs;
the runner starts them one after another as Cycles' object bake (INVOKE, so
Angelo stays usable, on the GPU like any Cycles job), reads each result on the
main thread when its job ends (``bake_maps_steps``) and, once a target's bakes
are done, finishes and writes its files on a worker thread (``bake_maps_write``)
while the next target bakes. The person's images are never written: every bake
goes into a scratch image that is removed again. With ``material`` a baked
material is made from the published files (``bake_maps_material``).

A cancel stops the running bake, removes every temporary node, group and image
and publishes nothing; a failure does the same and says which bake failed.
Main thread only, except ``write_set``.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import bpy

from . import bake_maps_material, bake_maps_plan, bake_maps_steps as steps, bake_maps_write, job_bake_targets as targets
from .bake_maps_queue import WriteQueue
from .bake_maps_spec import FORMATS, BakeMapsError, files, set_files, stems
from .job_runners import Runner, native
from .job_settings import scene_value
from .job_spec import JobRequestError, check_resolution, check_samples, check_work
from .ui_text import n_

NOTHING = n_("Nothing was published, and the scene is as it was.")
MEMORY_SHARE = 0.5   # Of this computer's memory a set may hold at once while bakes and writing overlap.
STEP_FAILED = n_("Angelo ended a bake of the texture set without writing it; see its Info log")
WRITE_FAILED = n_("Angelo could not write the texture set's files")
WRITE_SHARE = 0.1   # The progress bar's share for finishing and writing the files.
UNKNOWN_MEMORY = 8 * 2 ** 30   # The budget's base where this computer's memory cannot be read.


class TextureSetRunner(Runner):
    """Bake ``targets`` (from ``sources`` when given) into one folder of maps."""
    job_type = "OBJECT_BAKE"

    def __init__(self, window, request, workspace, targets_, sources=(), cage=None):
        super().__init__(window, request, workspace)
        self.objects, self.sources, self.cage = list(targets_), list(sources), cage
        self.plans, self.queue, self.run, self.outcome, self.handlers = [], [], None, None, ()
        self.notes, self.sums, self.writer, self.pool = steps.new_notes(), {}, None, None
        self.done = self.total = 0
        self.failure, self.stop_requested, self.deadline_at, self.stopped_bake = None, False, None, False
        self.chain = self._chain   # One callable, so the timer can be found and removed again.
        self.admit()

    # -- admission ------------------------------------------------------------------------------------------
    def admit(self):
        request, scene = self.request, self.scene
        width, height = request["resolution"]
        check_resolution(width, height)
        for target in self.objects:
            targets.check_target(self.window, target)
            targets.check_sources(self.window, target, self.sources, self.cage)
        bake = scene.render.bake
        request.setdefault("extrusion", float(bake.cage_extrusion))
        request.setdefault("ray_distance", float(bake.max_ray_distance))
        if "samples" in request:
            check_samples(request["samples"])
        try:
            self.plans = [bake_maps_plan.plan(target, self.sources, self.cage, request) for target in self.objects]
        except ReferenceError:
            raise JobRequestError("An object of this bake was removed") from None
        bakes = sum(len(plan.steps) * max(1, len(plan.tiles)) for plan in self.plans)
        samples = max([step.samples for plan in self.plans for step in plan.steps] or [1])
        check_work(request, width, height, samples, max(1, bakes) * max(1, len(self.sources)))
        # File-name stems, unique however the file system folds case, and the whole set's files checked at once.
        for plan, stem in zip(self.plans, stems([plan.target.name for plan in self.plans])):
            plan.stem = stem
        try:
            set_files(request["packing"], FORMATS[request.get("format", "PNG")],
                      [(plan.stem, plan.channels, plan.tiles) for plan in self.plans])
        except BakeMapsError as error:
            raise JobRequestError(str(error), code="VALIDATION") from None
        from .job_fluid import physical_memory
        memory = physical_memory()
        self.gpu = steps.on_gpu(self.scene)
        needed = steps.memory_needed(self.plans, (width, height), request.get("format", "PNG"), self.gpu)
        # What the set may hold at once (bakes and writing overlap within it); a set whose own smallest pipeline
        # needs more runs it without overlap, as Blender's bakes one by one would, and only one beyond this
        # computer's memory is refused.
        self.budget = max(MEMORY_SHARE * (memory or UNKNOWN_MEMORY), needed)
        if memory and needed > memory:
            raise JobRequestError("This texture set needs about %.1f GiB of memory at %dx%d%s (an estimate); this "
                                  "computer has %.1f GiB. Ask the person whether to use a smaller resolution or fewer "
                                  "UDIM tiles" % (needed / 2 ** 30, width, height,
                                                  " per tile" if any(p.tiles for p in self.plans) else "",
                                                  memory / 2 ** 30), code="LIMIT_EXCEEDED")

    def describe(self):
        plans = self.plans
        facts = {"engine": "CYCLES", "targets": [plan.target.name for plan in plans],
                 "channels": {plan.target.name: list(plan.channels) for plan in plans},
                 "bakes": sum(len(plan.steps) for plan in plans), "resolution": list(self.request["resolution"]),
                 "packing": self.request["packing"], "format": self.request.get("format", "PNG"),
                 "files": sum(len(files(self.request["packing"], plan.channels)) * max(1, len(plan.tiles))
                              for plan in plans)}
        tiles = {plan.target.name: plan.tiles for plan in plans if plan.tiles}
        if tiles:
            facts["tiles"] = tiles
        approximated = sorted({name for plan in plans for name in plan.facts.get("approximated", ())})
        if approximated:
            facts["approximated"] = approximated[:8]
        if self.sources:
            facts.update(target=self.objects[0].name, sources=[source.name for source in self.sources],
                         extrusion=round(self.request["extrusion"], 6),
                         ray_distance=round(self.request["ray_distance"], 6))
        if self.cage is not None:
            facts["cage"] = self.cage.name
        if self.request.get("material"):
            facts["material"] = True
        return facts

    # -- running --------------------------------------------------------------------------------------------
    def _handler(self, outcome):
        def handler(*_args):
            self.outcome = self.outcome or outcome
            if outcome == "complete" and self.run is not None and not bpy.app.timers.is_registered(self.chain):
                # The next bake starts as soon as Blender's job has ended, not at the manager's next poll (0.25 s).
                bpy.app.timers.register(self.chain, first_interval=0.0)
        handler.angelo_job = self.identity
        return handler

    def _chain(self):
        """Read a finished bake and start the next once Blender's job is gone; ``poll`` does the same if this runs
        late, and handles every other end (a stop, a refusal)."""
        if self.run is None or self.stop_requested or self.outcome != "complete":
            return None
        try:
            if native().progress(self.scene, self.job_type) is not None:
                return 0.005
            if self.run.wrote():
                self._complete_step()
        except Exception as error:   # Said by finish, as a start refusal is.
            self.failure = error
        return None

    def _start(self):
        self.overrides.apply(dict(steps.SCENE_VALUES))
        self.handlers = ((bpy.app.handlers.object_bake_complete, self._handler("complete")),
                         (bpy.app.handlers.object_bake_cancel, self._handler("cancel")))
        for collection, handler in self.handlers:
            collection.append(handler)
        self.queue = [(index, step) for index, plan in enumerate(self.plans) for step in plan.steps]
        self.total = len(self.queue)
        self.deadline_at = time.monotonic() + float(self.request["minutes"]) * 60
        self.pool = ThreadPoolExecutor(max_workers=bake_maps_write.WORKERS, thread_name_prefix="angelo-bake-maps")
        self.writer = WriteQueue(self.pool, self.budget, lambda: self.stop_requested)
        self.sums = {index: {} for index in range(len(self.plans))}
        for index, plan in enumerate(self.plans):
            if not plan.steps:
                self._write(index)   # Every channel folded to a constant: nothing to bake.
        self._next()

    def _next(self):
        """Start the next bake when the memory budget has room for it (else ``poll`` tries again once files are
        written), or nothing when every bake has run."""
        if not self.queue:
            self.run = None
            return
        index, step = self.queue[0]
        plan = self.plans[index]
        cost = steps.step_bytes(plan, step, tuple(self.request["resolution"]), self.gpu)
        if not self.writer.room(cost):
            self.run = None
            return
        self.queue.pop(0)
        self.writer.reserve_bake(cost)   # Until its scratch image is freed (``_complete_step``, ``_undone``).
        self.run = steps.StepRun(plan, step, tuple(self.request["resolution"]), self.notes, self.request.get("samples"))
        self.run.index = index
        self.outcome = None
        try:
            self.run.prepare()
        finally:
            self.overrides.note(steps.NOTES_KEY, self.notes)   # Whatever was added is put back, even from a save.
        self.overrides.apply({"cycles.samples": step.samples})
        self._rearm()
        objects = self.run.objects()
        self._invoke(bpy.ops.object.bake, {"active_object": plan.target, "object": plan.target,
                                           "selected_objects": objects, "selected_editable_objects": objects},
                     **self.run.operator_properties(self.request, self.request["margin"]))

    def _note(self):
        """Say how far the set is, replacing Blender's report about the removed scratch image (bake_maps_card)."""
        operator = getattr(bpy.ops.angelo, "bake_maps_note", None)
        if operator is None or self.window is None:
            return
        try:
            with bpy.context.temp_override(window=self.window):
                operator('INVOKE_DEFAULT', done=self.done, total=self.total)
        except RuntimeError:
            pass   # Only words in the top bar: the job goes on.

    def _rearm(self):
        """The rest of the time budget as the next bake's own deadline (Blender clears one when its job ends)."""
        from . import job_deadline
        job_deadline.rearm(self.scene, self.job_type, max(0.1, self.deadline_at - time.monotonic()))

    def _write(self, index):
        """Hand a target whose bakes are all read to the writing queue."""
        plan = self.plans[index]
        sums = dict(self.sums.pop(index, {}))
        sums.update(plan.constants)
        work = {"target": plan.target.name, "stem": plan.stem, "tiles": list(plan.tiles),
                "size": tuple(self.request["resolution"]), "sums": sums, "channels": list(plan.channels),
                "request": dict(self.request), "folder": str(self.staging.path)}
        self.writer.hand_over(index, work)

    def poll(self):
        self._check_output_path()
        units = self.total + WRITE_SHARE * self.total + 1e-9
        self.writer.pump()
        if self.run is None and self.queue and not (self.stop_requested or self.stopped_bake or self.failure):
            try:
                self._next()   # Waited for memory: files written since may have made room.
            except Exception as error:
                self.failure = error
        if self.run is not None:
            progress = native().progress(self.scene, self.job_type)
            if progress is not None:
                return {"progress": (self.done + progress) / units}
            if self.outcome != "complete" or self.stop_requested or not self.run.wrote():
                # Stopped (a cancel, the budget) or refused inside Blender's job: finish says which, once the
                # files being written meanwhile are (a worker checks the stop between files).
                self.stopped_bake, self.run = True, self._undone(self.run)
                return self._writing(units)
            try:
                self._complete_step()
            except Exception as error:   # A refusal at start: reported by finish, nothing published.
                self.failure = error
            if self.failure is not None:
                return self._writing(units)
            return {"progress": self.done / units}
        return self._writing(units)

    def _complete_step(self):
        """Read the bake that ended and put back what it added; write its target once its last bake is in; start the
        next bake."""
        run, self.run = self.run, None
        try:
            arrays = run.read()
            self.sums[run.index].update(arrays)
            self.writer.hold_baked(run.index, bake_maps_write.array_bytes(arrays))
        finally:
            run.undo()
            self.writer.release_bake()
            self.overrides.note(steps.NOTES_KEY, self.notes)
        self.done += 1
        self._note()
        if not any(index == run.index for index, _step in self.queue):
            self._write(run.index)
        self._next()

    def _writing(self, units):
        """Progress while files are still being written or a bake waits for memory, else None (the job has ended)."""
        self.writer.pump()
        waiting = self.queue and not (self.stop_requested or self.stopped_bake or self.failure)
        if self.writer.busy() or waiting:
            return {"progress": (self.done + WRITE_SHARE * self.total * self.writer.progress()) / units}
        return None

    def _undone(self, run):
        """Put back what a bake that will not be read added; None for the runner's ``run``."""
        run.undo()
        self.writer.release_bake()
        self.overrides.note(steps.NOTES_KEY, self.notes)
        return None

    def stop(self):
        self.stop_requested = True
        if self.run is not None:
            native().stop(self.scene, self.job_type)

    def _release(self):
        if bpy.app.timers.is_registered(self.chain):
            bpy.app.timers.unregister(self.chain)
        for collection, handler in self.handlers:
            if handler in collection:
                collection.remove(handler)
        self.handlers = ()
        if self.run is not None:
            self.run = None
        if self.writer is not None:
            self.writer.release_bake()
        steps.restore_notes(self.notes)
        if self.pool is not None:   # Every future has ended by now (``poll`` waits for them), so this never blocks.
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None

    def _undo_start(self):
        self._release()
        super()._undo_start()

    def finish(self, stopped):
        self._release()
        return super().finish(stopped)

    def _finish(self, stopped):
        if stopped or self.stop_requested:
            return self._discard("cancelled", "", NOTHING)
        if self.failure is not None:
            return self._discard("failed", str(self.failure)[:300], NOTHING)
        if self.stopped_bake or self.done < self.total or self.queue:
            return self._discard("failed", STEP_FAILED, NOTHING)
        if self.writer.error is not None:
            return self._discard("failed", "%s (%s)" % (WRITE_FAILED, type(self.writer.error).__name__), NOTHING)
        results = self.writer.results
        outputs, facts = [], {}
        for index, (entries, target_facts) in sorted(results.items()):
            name = self.plans[index].target.name
            outputs += [dict(entry, target=name) for entry in entries]
            if target_facts:
                facts[name] = target_facts
        receipt = self._publish_folder({"outputs": outputs, "maps": len(outputs)})
        if facts:
            receipt["facts"] = facts
        if self.request.get("material"):
            receipt["materials"] = self._materials(results)
        return "done", "", receipt

    def _materials(self, results):
        """A baked material per target, reading the published files (best effort: the maps are already safe)."""
        made = []
        for index, (entries, target_facts) in sorted(results.items()):
            plan = self.plans[index]
            try:
                made.append(bake_maps_material.create(plan.target.name, entries, self.published, self.request,
                                                      plan.channels, target_facts))
            except (RuntimeError, ReferenceError, KeyError) as error:
                made.append({"target": plan.target.name, "error": str(error)[:200]})
        return made


def scene_samples(scene):
    """The person's own Cycles samples (the approval shows what the bake replaces only for its own settings)."""
    return scene_value(scene, "cycles.samples")
