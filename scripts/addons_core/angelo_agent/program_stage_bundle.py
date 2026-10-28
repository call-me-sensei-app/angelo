# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private stage serialization and hydration using the storage owner's cleanup rights.

The original module retains shared state and patchable dependencies.
Methods resolve those named dependencies at call time; this base owns no resources.
"""


from . import stage_bundle_folder
from .program_jobs import ProgramError


class ProgramStageBundleMixin:
    __slots__ = ()

    def retain_stage(self, stage):
        """File-backed immutable snapshot for previews and temporary inspection.

        Inspection callers must never mutate the active job's retained graph.
        Materialize it once, then use the existing independent hydration path.
        """
        from . import program_storage as storage
        from .stage_borrowing import borrows_any
        if isinstance(stage, storage.SuspendedStage) and stage.native_owner is None and not borrows_any(stage):
            return stage
        resident = self.resume_stage(stage)
        try:
            self.unborrow(resident)  # Inspected or accepted later: no live material in it.
            return self.suspend_stage(resident, use_memory=False)
        except Exception:
            self.discard(resident)
            raise

    def suspend_stage(self, stage, *, use_memory=True):
        from .exact_fence import end
        end()  # Evaluation, transfer of ownership or serialization follows.
        from . import program_storage as storage
        if isinstance(stage, storage.SuspendedStage):
            return stage
        self.ensure_main_thread()
        from .program_material_facts import snapshot_provenance
        snapshot_provenance(stage)
        # libraries.write copies view-layer bases; a newly-created alias must
        # be synced first, but only AFTER a conservative topology preflight.
        if stage.context is not None:
            self._update_stage(stage)
        metadata = {}
        if stage.context is not None:
            from .action_kernels import node_metadata
            metadata = node_metadata(stage.context)
        from . import stage_new_objects
        made = stage_new_objects.remember(stage)  # Their names are free in Main while suspended (stage_new_objects).
        descriptor = storage.SuspendedStage("", stage.scene.name, stage.binding,
                                    {handle: obj.name for handle, obj in stage.objects.items()},
                                    {alias: obj.name for alias, obj in stage.context.aliases.items()} if stage.context else {},
                                    {key: sorted(value) for key, value in stage.changes.items()}, dict(stage.logical_names),
                                    {obj.name: value for obj, value in stage.context.topology_versions.items()} if stage.context else {},
                                    {group.name: value for group, value in stage.context.node_bounds.items()} if stage.context else {},
                                    metadata, extras=dict(getattr(stage, "extras", {})))
        from .stage_borrowing import release
        release(stage)  # Neither a detached graph nor a bundle may reference a live material.
        if use_memory and self.memory_staging:
            from _bpy import _angelo_stage
            ids = list(stage.owned_ids) + (list(stage.context.created_ids) if stage.context else [])
            owned = list(storage._owned_cleanup_ids(storage.cleanup_identities(ids)).values())
            owner = _angelo_stage.detach(stage.scene, owned, self.scene)
            if owner is not None:
                descriptor.native_owner, descriptor.resident = owner, stage
                if stage.bundle:
                    self._remove_bundle(stage.bundle)
                    stage.bundle = ""
                return descriptor
        bundle = stage_bundle_folder.new_bundle()   # One private folder per process (stage_bundle_folder).
        try:
            stage_new_objects.mark(made)  # Only the private bundle holds the marker; loading removes it.
            storage._bpy().data.libraries.write(str(bundle), {stage.scene}, path_remap="ABSOLUTE", fake_user=False,
                                       compress=storage.STAGE_BUNDLE_COMPRESSION,
                                       _compression_min_size=storage.STAGE_BUNDLE_COMPRESSION_MIN_SIZE)
            storage.os.chmod(bundle, 0o600)
            descriptor.bundle = str(bundle)
            self._remove_stage_ids(stage, keep=set())
            if stage.bundle:
                self._remove_bundle(stage.bundle)
            return descriptor
        except Exception:
            stage_new_objects.unmark(made)
            stage_bundle_folder.remove(bundle)
            raise

    def resume_stage(self, stage):
        from .exact_fence import end
        end()  # Hydration adds IDs, and validation evaluates them.
        from . import program_storage as storage
        if not isinstance(stage, storage.SuspendedStage):
            return stage
        self.check_context(stage.binding)
        storage.require_cleanup_complete()
        from .program_commit_scope import for_backend
        scope = for_backend(self)
        if stage.native_owner is not None:
            from _bpy import _angelo_stage
            try:
                if scope is None:
                    _angelo_stage.attach(stage.native_owner)
                else:
                    _angelo_stage.attach(stage.native_owner, scope.native_scope)
                from .stage_borrowing import restore
                restore(stage.resident)  # The live materials it reads, back in their slots (or STALE).
                from .program_material_facts import snapshot_provenance
                from . import stage_new_objects
                stage_new_objects.reattached(stage.resident)  # Blender renames one whose name was taken meanwhile.
                snapshot_provenance(stage.resident)
                # The same originals, RNA values and kernel authority survive;
                # the preflight and exact validity checks still precede evaluation.
                self.validate_stage(stage.resident)
                return stage.resident
            except Exception as error:
                ids = list(stage.resident.owned_ids) + (list(stage.resident.context.created_ids)
                                                       if stage.resident.context else [])
                identities = storage.cleanup_identities(ids)
                try:
                    roots = tuple(storage._owned_cleanup_ids(identities).values())
                    storage._remove_owned(identities, lambda: scope.delete_stage(roots) if scope else self._remove_ids(roots))
                except ProgramError as cleanup:
                    raise storage._hydration_cleanup_error(error, cleanup) from error
                raise
        path = storage.Path(stage.bundle)
        if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
            raise ProgramError("The private staged preview bundle is unavailable", code='INTERNAL_ERROR')
        bpy = storage._bpy()
        before = storage._main_ids() if scope is None else None
        try:
            with bpy.data.libraries.load(str(path), link=False,
                                         **({"_angelo_capture": scope.native_scope} if scope else {})) as (source, destination):
                if source.scenes != [stage.scene_name]:
                    raise ProgramError("A retained bundle does not contain its exact private scene", code='INTERNAL_ERROR')
                destination.scenes = [stage.scene_name]
            scene = destination.scenes[0]
            if scene is None or not scene.get(storage.PROGRAM_STAGE[0]):
                raise ProgramError("A retained preview is not an admitted native stage", code='INTERNAL_ERROR')
            ids = (list(scope.stage_owned_ids()) if scope else
                   [block for pointer, block in storage._main_ids().items() if pointer not in before])
            # bpy.data.shape_keys has no remove(), so Main scans skip it: a Key
            # is owned through its mesh, curve or lattice, as when it was staged.
            if scope is None:
                ids += [block.shape_keys for block in ids if getattr(block, "shape_keys", None) is not None]
            if len(ids) > storage.MAX_STAGE_IDS:
                raise ProgramError("The hydrated preview exceeds its owned ID budget", code='LIMIT_EXCEEDED')
            collection = scene.collection.children[0]
            from . import stage_new_objects
            held = stage_new_objects.loaded(ids)  # Created IDs by the name they had, whatever name they hold now.
            objects = {handle: scene.objects.get(name) for handle, name in stage.object_names.items()}
            aliases = {alias: held.get(("OBJECT", name)) or scene.objects.get(name) for alias, name in stage.aliases.items()}
            if any(obj is None for obj in list(objects.values()) + list(aliases.values())):
                raise ProgramError("The retained preview lost an exact staged target", code='INTERNAL_ERROR')
            from .action_kernels import StagedActionContext, restore_node_metadata
            bound_scene = stage.binding.get("scene")
            extras = dict(stage.extras)
            moved = {}
            for (kind, old), block in held.items():
                if block.name != old:
                    moved.setdefault(kind, {})[old] = block.name
            stage_new_objects.follow(extras, moved)
            suspended = {block.as_pointer(): old for (kind, old), block in held.items() if kind == "OBJECT"}
            context = StagedActionContext(scene, collection, objects=objects, aliases=aliases,
                                          owned_ids=ids, view_layer=scene.view_layers[0],
                                          scene_handles=(bound_scene["handle"],) if bound_scene else (), extras=extras)
            for obj in scene.objects:
                context.topology_versions[obj] = stage.topology_versions.get(suspended.get(obj.as_pointer(), obj.name), 0)
            for group in ids:
                if isinstance(group, bpy.types.NodeTree) and group.name in stage.node_bounds:
                    context.node_bounds[group] = stage.node_bounds[group.name]
            restore_node_metadata(context, stage.node_metadata)
            hydrated = storage.Stage(scene, collection, scene.view_layers[0], stage.binding, context=context,
                             objects=objects, originals=stage.binding["originals"], owned_ids=ids,
                             changes={key: set(value) for key, value in stage.changes.items()},
                             logical_names=dict(stage.logical_names), bundle=stage.bundle, extras=extras)
            from .program_material_facts import restore_provenance
            restore_provenance(hydrated)
            from .stage_borrowing import restore
            restore(hydrated)  # The live materials it reads, back in their slots (or STALE).
            # Admission/preflight precedes every evaluated update, including a
            # hydrated scene with persisted kernel authority.
            self.validate_stage(hydrated)
            return hydrated
        except Exception as error:
            new = (list(scope.stage_owned_ids()) if scope else
                   [block for pointer, block in storage._main_ids().items() if pointer not in before])
            identities = storage.cleanup_identities(new)
            try:
                storage._remove_owned(identities, lambda: scope.delete_stage(tuple(new)) if scope else self._remove_ids(new))
            except ProgramError as cleanup:
                raise storage._hydration_cleanup_error(error, cleanup) from error
            raise
