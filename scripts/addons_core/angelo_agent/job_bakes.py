# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bake runners: Cycles texture bakes, physics (point cache) bakes and geometry-nodes simulation bakes.

Same contract as ``job_runners.Runner``. What each bake changes beyond its
output, and how a cancel undoes it:

* a texture bake writes into the target's images (no Blender Undo; Blender
  writes even a stopped bake's partial result). Up to ``MAX_RESTORE_PIXELS``
  the runner keeps a float copy and puts it back on cancel or failure; a UDIM
  image is read again from its saved tiles instead; the approval says which
  applies. Which images those are, and the objects a
  high-to-low bake reads, are ``job_bake_targets``';
* a physics bake bakes the caches that were not baked (Blender skips baked
  ones, which stay as they are); a cancel deletes only the bakes it made;
* a simulation bake replaces the target's earlier packed bake (Blender deletes
  it at start) with a packed one; a cancel deletes the partial bake. With an
  ``output`` it bakes to disk in the job's folder instead (``job_bake_caches``):
  a cancel removes what it staged and puts the bake settings back.
"""

import bpy

from . import job_bake_caches as caches
from . import job_bake_targets as targets
from .job_manager import JobStartError
from .job_runners import Runner, native
from .job_settings import scene_value
from .job_spec import JobRequestError, check_frames, check_resolution, check_samples, check_work
from .ui_text import n_

PUT_BACK = n_("The images were put back as they were.")
PARTIAL = n_("The images may keep a partial bake; Blender Undo can't restore them.")
NOT_BAKED = n_("Blender ended the bake without baking; see its Info log")
NOT_ALL_BAKED = n_("Blender ended the bake without writing every target image; see its Info log")
UNCHANGED = n_("The bake changed nothing: Blender refused it (see its Info log) or the images already held this result")
UNCHECKED = n_("The images had unsaved changes before the bake, so the job could not check what Blender wrote.")
NEW_REMOVED = n_("The new image was removed again.")
CACHE_REMOVED = n_("The partial cache was removed; the simulation's bake settings are as they were.")
# Cycles stores a position bake as the sum of its samples, not their mean (Blender 5.2: one sample stores a
# surface point 0.52, four store 2.06). A position needs no second sample, so the job bakes it with one,
# whatever the request or the scene asks, and its approval shows that.
ONE_SAMPLE = frozenset({"POSITION"})


class TextureBakeRunner(Runner):
    """A Cycles bake onto ``target``, from its own surface or from ``sources`` (high to low, with an optional cage).

    The objects reach Blender's bake through a context override, so the person's
    selection, active object and mode are never touched. With ``image``, or a
    ``resolution`` for a new image, the target's materials are pointed at that
    image for the job (``TargetNodes``) and put back when it ends; a new image
    that was not baked is removed again.
    """
    job_type = "OBJECT_BAKE"

    def __init__(self, window, request, workspace, target, sources=(), cage=None, image=None):
        super().__init__(window, request, workspace)
        self.target, self.sources, self.cage, self.image = target, list(sources), cage, image
        self.outcome, self.handlers, self.snapshots, self.nodes, self.clean = None, (), [], None, []
        self.reload = []  # UDIM images a stopped bake reads again from their saved tiles.
        self.new_size, self.created = request.get("resolution"), None
        self.passes, self.last_progress = 0, 0.0  # Blender's progress starts again for each source object.
        self.admit()

    def admit(self):
        target, request = self.target, self.request
        self.tiles = {}  # UDIM images: {name: {"baked": the tiles the target's faces lie in, "count": all tiles}}.
        targets.check_target(self.window, target)
        targets.check_sources(self.window, target, self.sources, self.cage)
        try:
            if self.new_size is not None:
                targets.TargetNodes(target, None).materials()  # Refuses a material a job can't point at an image.
                self.images, self.names = [], [targets.new_image_name(target, request["bake_type"])]
                sizes = [tuple(self.new_size)]
            else:
                if self.image is not None:
                    targets.check_image(self.image)
                    targets.TargetNodes(target, self.image).materials()
                    self.images = [self.image]
                else:
                    self.images = targets.active_images(target)
                self.names, sizes = [image.name for image in self.images], []
                for image in self.images:
                    if targets.is_tiled(image):
                        sizes += self._admit_tiles(image)
                    else:
                        check_resolution(image.size[0], image.size[1], "the image " + image.name)
                        sizes.append(tuple(image.size))
        except ReferenceError:
            raise JobRequestError("The image to bake into was removed") from None
        if self.new_size is not None:
            check_resolution(self.new_size[0], self.new_size[1], "the image " + self.names[0])
        self.samples = request.get("samples", scene_value(self.scene, "cycles.samples"))
        check_samples(self.samples, "the request" if "samples" in request else "the scene")
        if request["bake_type"] in ONE_SAMPLE:
            self.samples = 1
        side = max(max(size) for size in sizes)
        check_work(request, side, side, self.samples, max(1, len(self.sources)) * len(sizes))
        # A new image needs no copy (a bake that stops removes it); a UDIM image's copy is its own saved tiles.
        plain = [image for image in self.images if not targets.is_tiled(image)]
        self.unsaved = [image.name for image in self.images
                        if targets.is_tiled(image) and not targets.restorable_tiles(image)]
        self.restorable = self.new_size is not None or (
            not self.unsaved and sum(image.size[0] * image.size[1] for image in plain) <= targets.MAX_RESTORE_PIXELS)
        # Where rays start and how far they reach: the request's, else the person's own bake settings.
        bake = self.scene.render.bake
        self.extrusion = float(request.get("extrusion", bake.cage_extrusion))
        self.ray_distance = float(request.get("ray_distance", bake.max_ray_distance))

    def _admit_tiles(self, image):
        """A UDIM image's tile sizes, after checking each and that the target's faces lie in one of them."""
        targets.check_tiles(image)
        baked = targets.covered_tiles(self.target, image, explicit=self.image is not None)
        if not baked:
            raise JobRequestError("None of %s's faces lie in a tile of the image %s (its active UV map); move the UVs "
                                  "into a tile or add that tile" % (self.target.name, image.name))
        self.tiles[image.name] = {"baked": baked, "count": len(image.tiles)}
        return [tuple(tile.size) for tile in image.tiles]

    def describe(self):
        facts = {"images": list(self.names), "samples": self.samples, "engine": "CYCLES", "restorable": self.restorable}
        if self.tiles:
            facts["tiles"] = dict(self.tiles)
        if self.unsaved:
            facts["unsaved"] = list(self.unsaved)
        if self.new_size is not None:
            facts["new_image"] = list(self.new_size)
        if self.sources:
            facts.update(target=self.target.name, sources=[source.name for source in self.sources],
                         extrusion=round(self.extrusion, 6), ray_distance=round(self.ray_distance, 6))
        if self.cage is not None:
            facts["cage"] = self.cage.name
        return facts

    def _handler(self, outcome):
        def handler(*_args):
            self.outcome = self.outcome or outcome
        handler.angelo_job = self.identity
        return handler

    def _start(self):
        values = {"render.engine": "CYCLES"}
        if "samples" in self.request or self.request["bake_type"] in ONE_SAMPLE:
            values["cycles.samples"] = self.samples
        self.overrides.apply(values)
        self.handlers = ((bpy.app.handlers.object_bake_complete, self._handler("complete")),
                         (bpy.app.handlers.object_bake_cancel, self._handler("cancel")))
        for collection, handler in self.handlers:
            collection.append(handler)
        if self.new_size is not None:
            self.created = targets.create_image(self.names[0], self.new_size, self.request["bake_type"])
            self.image, self.images, self.names = self.created, [self.created], [self.created.name]
            self.overrides.note(targets.CREATED_KEY, self.created.name)  # The name Blender really gave it.
        self.clean = [not image.is_dirty for image in self.images]  # Before anything of this job touches them.
        if self.restorable and self.created is None:
            self.snapshots = targets.snapshot([image for image in self.images if not targets.is_tiled(image)])
            self.reload = [image for image in self.images if targets.is_tiled(image)]
        if self.image is not None:
            self.nodes = targets.TargetNodes(self.target, self.image)
            self.overrides.note(targets.RESTORE_KEY, self.nodes.apply())
        target, objects = self.target, [*self.sources, self.target]
        # A new image is cleared to its map's own background (neutral for a tangent normal map); others keep theirs.
        properties = {"type": self.request["bake_type"], "use_clear": self.created is not None,
                      "target": 'IMAGE_TEXTURES', "save_mode": 'INTERNAL', "use_selected_to_active": bool(self.sources)}
        if self.sources:  # Every ray setting is the job's own: nothing is read from the bake panel by Blender.
            properties.update(use_cage=self.cage is not None, cage_object=self.cage.name if self.cage else "",
                              cage_extrusion=self.extrusion, max_ray_distance=self.ray_distance)
        self._invoke(bpy.ops.object.bake, {"active_object": target, "object": target, "selected_objects": objects,
                                           "selected_editable_objects": objects}, **properties)

    def poll(self):
        state = super().poll()
        if state is not None and len(self.sources) > 1:
            progress = state["progress"]
            if progress + 0.2 < self.last_progress:  # The next source began.
                self.passes = min(self.passes + 1, len(self.sources) - 1)
            self.last_progress = progress
            state["progress"] = (self.passes + progress) / len(self.sources)
        return state

    def _release(self):
        """Remove the bake handlers and put the target's materials back; the pixels are ``_finish``'s."""
        for collection, handler in self.handlers:
            if handler in collection:
                collection.remove(handler)
        self.handlers = ()
        if self.nodes is not None:
            self.nodes.restore()
            self.nodes = None

    def _remove_created(self):
        """A new image that was not baked goes again (after ``_release``: no node uses it any more)."""
        if self.created is not None:
            try:
                targets.remove_created(self.created.name)
            except ReferenceError:
                pass  # The person removed it meanwhile.
            self.created, self.image, self.images = None, None, []

    def _undo_start(self):
        self._release()
        self._remove_created()
        super()._undo_start()

    def finish(self, stopped):
        self._release()
        return super().finish(stopped)

    def _put_back(self):
        """Undo a stopped bake: the float copies of plain images, and UDIM images from their saved tiles."""
        if not self.restorable:
            return False
        copied = targets.put_back(self.snapshots) if self.snapshots else True
        return all([targets.reload_tiles(image) for image in self.reload]) and copied

    def _evidence(self):
        """What the images themselves say about a bake Blender reports as complete (``job_bake_targets.written``)."""
        kept = dict((image, values) for image, values in self.snapshots)
        return [targets.written(image, clean, kept.get(image)) for image, clean in zip(self.images, self.clean)]

    def _finish(self, stopped):
        created = self.created is not None
        if stopped or self.outcome != "complete":
            note = NEW_REMOVED if created else PUT_BACK if self._put_back() else PARTIAL
            self.snapshots = []
            self._remove_created()
            if stopped or self.outcome == "cancel":
                return self._discard("cancelled", "", note)
            return self._discard("failed", NOT_BAKED, note)
        evidence = self._evidence()
        if False in evidence:
            untouched = all(wrote is False for wrote in evidence)
            reason = UNCHANGED if untouched and not any(self.clean) else NOT_BAKED if untouched else NOT_ALL_BAKED
            # One untouched image does not prove that the other images were untouched.
            # Keep the backups until mixed or unknown results have been put back.
            if created:
                note = NEW_REMOVED
            elif untouched:
                note = PUT_BACK  # Avoid marking untouched images dirty by writing their copies back.
            else:
                note = PUT_BACK if self._put_back() else PARTIAL
            self.snapshots = []
            self._remove_created()
            return self._discard("failed", reason, note)
        self.snapshots = []
        receipt = {"images": [image.name for image in self.images], "outputs": self._outputs()}
        if None in evidence:
            receipt["note"] = UNCHECKED
        if self.output is not None:
            receipt = self._publish_folder(receipt)
        return "done", "", receipt

    def _outputs(self):
        """What the bake produced, one entry per image or UDIM tile, saving a copy of each into the job's output.

        Every tile of a UDIM image is copied, so the published set loads as one
        image; the tiles the bake wrote carry its ``map``, the others hold what
        they held. Without an output only the written tiles are listed.
        """
        from .action_world import image_id
        if self.output is not None:
            self.staging.verify()  # Blender saves by path: it must still reach the folder held.
        entries, taken, bake_type = [], set(), self.request["bake_type"]
        for image in self.images:
            entry = {"image": image_id(image), "name": image.name}
            if not targets.is_tiled(image):
                entry["map"] = bake_type
                if self.output is not None:
                    entry["file"] = targets.copy_name(image, taken)
                    image.save(filepath=str(self.staging.path / entry["file"]), save_copy=True)
                entries.append(entry)
                continue
            baked, files = set(self.tiles[image.name]["baked"]), {}
            if self.output is not None:
                marked, files = targets.tile_copy_names(image, taken)
                image.save(filepath=str(self.staging.path / marked), save_copy=True)
            for tile in image.tiles:
                if tile.number in baked or files:
                    item = dict(entry, tile=tile.number)
                    if tile.number in baked:
                        item["map"] = bake_type
                    if files:
                        item["file"] = files[tile.number]
                    entries.append(item)
        return entries


def _point_caches(scene):
    """The physics caches Bake All Dynamics bakes (hair and keyed particles have none to bake)."""
    caches = []
    for obj in scene.objects:
        for modifier in obj.modifiers:
            system = getattr(modifier, "particle_system", None)
            if system is not None and (system.settings.type == 'HAIR' or system.settings.physics_type == 'KEYED'):
                continue
            cache = getattr(modifier, "point_cache", None) or getattr(system, "point_cache", None)
            if cache is not None:
                caches.append(cache)
    world = scene.rigidbody_world
    if world is not None and world.point_cache is not None:
        caches.append(world.point_cache)
    return caches


class PhysicsBakeRunner(Runner):
    """Bake All Dynamics. Caches already baked are skipped by Blender and kept as they are."""
    job_type = "POINTCACHE"

    def __init__(self, window, request, workspace):
        super().__init__(window, request, workspace)
        self.admit()

    def admit(self):
        caches = _point_caches(self.scene)
        if not caches:
            from .job_fluid import is_domain
            domains = [obj.name for obj in self.scene.objects if is_domain(obj)]
            raise JobRequestError("The scene has no physics or particle caches to bake" + (
                "; a fluid domain (%s) bakes with BAKE_SIMULATION, its target the domain and output a new folder"
                % ", ".join(domains[:4]) if domains else ""))
        self.caches = [cache for cache in caches if not cache.is_baked]
        self.kept = len(caches) - len(self.caches)
        if not self.caches:
            raise JobRequestError("Every physics cache is already baked; delete a bake to bake it again")
        if not bpy.data.filepath and any(cache.use_disk_cache for cache in self.caches):
            raise JobRequestError("A disk cache needs a saved file; save it first or turn off Disk Cache")
        # Bake All steps every frame from the earliest cache start to the latest end.
        frames = check_frames(min(cache.frame_start for cache in self.caches),
                              max(cache.frame_end for cache in self.caches), "the physics caches")
        check_work(self.request, 1, 1, 1, frames)

    @property
    def locks_interface(self):
        return True

    def describe(self):
        return {"caches": len(self.caches), "kept": self.kept}

    def _start(self):
        self._invoke(bpy.ops.ptcache.bake_all, bake=True)

    def _free_own(self):
        """Delete the bakes this job made (a stopped bake keeps its partial frames); others are untouched."""
        for cache in self.caches:
            try:
                if cache.is_baked:
                    with bpy.context.temp_override(window=self.window, point_cache=cache):
                        bpy.ops.ptcache.free_bake()
            except ReferenceError:
                pass  # Its object was removed meanwhile.

    def _finish(self, stopped):
        if stopped:
            self._free_own()
            return "cancelled", "", {"note": n_("Cancelled; the partial bake was removed, so the simulation runs live.")}
        baked = sum(cache.is_baked for cache in self.caches)
        if baked < len(self.caches):
            self._free_own()
            return "failed", n_("Blender ended the bake before every cache was baked; see its Info log"), {}
        return "done", "", {"caches": baked}


def _simulations(obj):
    """The object's simulation-zone bakes (``(modifier, bake)``) in modifiers shown in the viewport."""
    return [(modifier, bake) for modifier in obj.modifiers if modifier.type == 'NODES' and modifier.show_viewport
            for bake in modifier.bakes
            if bake.node is not None and bake.node.bl_idname == "GeometryNodeSimulationOutput"]


class SimulationBakeRunner(Runner):
    """Bake the target's geometry-nodes simulations: packed into the file, or with an ``output`` to disk there.

    Blender replaces an earlier packed bake when a bake starts. A disk bake goes
    into the job's own folder (``job_bake_caches``), never to a directory stored
    in the scene; the scene is pointed at the published cache only once it is whole.
    """
    job_type = "BAKE_GEOMETRY_NODES"

    def __init__(self, window, request, workspace, target):
        super().__init__(window, request, workspace)
        self.target, self.disk, self.caches = target, self.output is not None, []
        self.admit()

    def admit(self):
        target, scene = self.target, self.scene
        try:
            simulations = _simulations(target)
            if target.library is not None or target.override_library is not None:
                raise JobRequestError("%s is linked from another file; a bake needs an object of this file" % target.name)
        except ReferenceError:
            raise JobRequestError("The object to bake was removed") from None
        if not simulations:
            raise JobRequestError("%s has no geometry-nodes simulation to bake" % target.name)
        ranges = {}
        for modifier, bake in simulations:
            where = bake.bake_target if bake.bake_target != 'INHERIT' else modifier.bake_target
            if where != 'PACKED' and not self.disk:
                raise JobRequestError("%s bakes to disk. Pass output (a workspace folder) to bake its cache there "
                                      "as a job, or set its bake to Pack" % target.name)
            custom = bake.use_custom_simulation_frame_range
            start, end = (bake.frame_start, bake.frame_end) if custom else (scene.frame_start, scene.frame_end)
            frames = check_frames(start, end, "the simulation bake" if custom else "the scene")
            check_work(self.request, 1, 1, 1, frames)
            ranges[bake.bake_id] = (start, end)
        self.count = len(simulations)
        if self.disk:
            caches.blend_relative(self.output.parent)  # A saved file whose folder holds the output, or a refusal.
            self.caches = caches.plan(target, simulations, lambda bake: ranges[bake.bake_id])

    @property
    def locks_interface(self):
        return True

    def describe(self):
        facts = {"objects": [self.target.name], "simulations": self.count}
        if self.disk:
            facts["disk"] = True
        return facts

    def _selection(self):
        return {"selected_objects": [self.target], "active_object": self.target, "object": self.target}

    def _start(self):
        if self.disk:
            self.overrides.apply(caches.settings(self.target, self.caches, self.staging.path))
        self._invoke(bpy.ops.object.simulation_nodes_cache_bake, self._selection(), selected=True)
        if native().progress(self.scene, self.job_type) is None:
            # Blender asks before it replaces bake data it finds on disk. A job's folder is new, so this
            # is not expected; a bake that did not start is never reported as one that ran.
            raise JobStartError("Blender did not start the bake; see the Info log for its reason")

    def finish(self, stopped):
        if not self.disk:
            return super().finish(stopped)
        self.incomplete, failure = True, None
        try:
            if self.moved is None:
                self.incomplete = bool(caches.missing_frames(self.caches, self.staging.tree()))
            if stopped or self.incomplete:
                caches.forget_partial(self.window, self.target, self.caches)  # Before the settings go back.
        except Exception as error:  # The settings go back whatever happened here; the failure is then reported.
            failure = error
        result = super().finish(stopped)
        if failure is not None:
            raise failure
        return result

    def _finish(self, stopped):
        if not self.disk:
            if stopped:
                with bpy.context.temp_override(window=self.window, **self._selection()):
                    bpy.ops.object.simulation_nodes_cache_delete(selected=True)
                return "cancelled", "", {"note": n_("Cancelled; the partial bake was deleted, so the simulation runs live.")}
            return "done", "", {"objects": 1}
        if stopped:
            return self._discard("cancelled", "", CACHE_REMOVED)
        if self.incomplete:
            return self._discard("failed", n_("Blender ended the bake before every frame was cached; see its Info log"),
                                 CACHE_REMOVED)
        self.staging.publish_folder()
        # Before the staging is released: a failure here unpublishes (job_manager), so nothing stays half done.
        caches.point_at(self.target, self.caches, self.staging.published)
        return "done", "", self._published({"objects": 1, "outputs": caches.outputs(self.target, self.caches)})
