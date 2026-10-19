# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Atomic allowlisted live writes with a pre-established rollback journal.

ProgramCommitMixin is called only inside the host's normal UNDO operator. It
requires verified private stages, the backend's _apply_settings callback and
storage cleanup. Ordinary cleanup can use an independently owned-ID fallback;
a native capture uses its exact rights and preserves refused residue."""

import contextlib

from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .scene_read_values import SceneQueryError, label, revision_omission
from .program_dependencies import _bpy, _uid, _embedded_links, _dependency_closure, _RNA_SKIP
from .program_storage import (retain_cleanup, cleanup_identities as capture_cleanup_identities,
                              _owned_cleanup_ids, protected_cleanup)
from .program_identities import _adopt_names, _assign_environment_image, _assign_images, _restore_source_identities
from .program_fields import (TRANSFORM_FIELDS as _TRANSFORM_FIELDS, transform_values as _transform_values,  # noqa: F401
                             set_transforms as _set_transforms, repoint)
from .action_properties import data_family
from .action_particles import restore_sources, transfer_systems
from .node_edit_commit import transfer_trees, transfer_world
from . import material_sharing as shared_edits, stage_names
from .work_clock import WorkClock
from .revision_budget import READ_BACK_SECONDS
# Re-exported: native_programs imports _set_settings; tests patch _sync_stack and _append_tracks here.
from .program_transfers import (_set_settings, _settings, _live_id, touched_paths, modified_row,  # noqa: F401
                                _transfer_settings, _sync_stack, _restore_stacks, _append_tracks, MAX_RESULT_ROWS,
                                active_state, restore_active_state, transfer_scene_settings)
from .program_memberships import (link_new_objects, verify_new_memberships,  # Tests patch these here.
                                  ProgramMembershipMixin)
from . import camera_guards, commit_fidelity, exact_fence, kept_values
from .program_commit_report import ProgramCommitReportMixin


class ProgramCommitMixin(ProgramCommitReportMixin, ProgramMembershipMixin):
    fences_before_apply = True  # ProgramJob.commit hands the controller's preparation fence to commit (final_checks).
    pending_before_apply = None

    def __init_subclass__(cls, **kwargs):  # Whichever class's commit resolves says whether it runs that fence.
        super().__init_subclass__(**kwargs)
        exact_fence.require_declared(cls, "commit", "fences_before_apply", ProgramCommitMixin)

    def commit(self, stage):
        """Swap only allowlisted changed families, with a rollback journal first."""
        before_apply, self.pending_before_apply = self.pending_before_apply, None
        from .program_commit_scope import for_backend
        scope = for_backend(self)
        observer = {"record_transfer": scope.record_transfer} if scope is not None else {}
        if scope is not None:
            scope.validate_stage(stage)
        deleted = tuple(scope.approved_deletions if scope is not None else stage.extras.get("deleted", ()))
        self.final_checks(stage, before_apply)  # Last stage evaluation, then every pre-write check in one exact fence.
        exact_fence.require_ran(before_apply, ProgramError)  # An override of final_checks that skipped it: no write.
        bpy = _bpy()
        source_map = {obj.as_pointer(): stage.originals[handle] for handle, obj in stage.objects.items()}
        new_objects = [obj for obj in stage.scene.objects if obj.as_pointer() not in source_map]
        source_map.update({obj.as_pointer(): obj for obj in new_objects})
        fidelity = commit_fidelity.capture(stage, new_objects)  # Before any write (commit_fidelity).
        world = self._world_before()  # What the World and its Sun lights showed, to report a change (never raises).
        journals, linked, new_groups, new_modifiers, new_constraints = [], [], [], [], []
        transferred_transforms, isolated = [], []
        settings_journal = []  # (holder, property name, previous value), restored in reverse.
        stack_journal = []  # Removed modifiers and previous stack orders, restored after new ones go.
        membership_journal = []  # ("link"|"unlink"|"created", collection, object or None), undone in reverse.
        new_tracks = []  # (animation data, NLA track) appended by push-downs.
        undo_journal = []  # Whole-stack undo callables (NLA, markers; anim_commit), run first on rollback.
        doomed = []  # Deleted objects to remove from Main once the commit has succeeded.
        kept_worlds, replaced_worlds, replaced_particles, replaced_trees = [], [], [], []
        keep = set()
        cameras = camera_guards.prepare(self, stage)  # The frame update and what orbits were measured from.
        for handle in stage.binding["writes"]:
            obj = stage.originals[handle]
            journals.append((obj, {"data": obj.data, "transforms": _transform_values(obj),
                                   "parent": obj.parent, "parent_type": obj.parent_type, "parent_bone": obj.parent_bone,
                                   "matrix_parent_inverse": obj.matrix_parent_inverse.copy(),
                                   "animation_existed": obj.animation_data is not None,
                                   "action": obj.animation_data.action if obj.animation_data else None,
                                   "slot": getattr(obj.animation_data, "action_slot", None) if obj.animation_data else None,
                                   "modifier_settings": {modifier.name: _settings(modifier) for modifier in obj.modifiers},
                                   "active": active_state(obj),  # Active modifier and NLA track (program_transfers).
                                   "constraint_settings": {item.name: _settings(item) for item in obj.constraints},
                                   "material_slots": [(slot.link, slot.material) for slot in obj.material_slots]}))
        try:
            # New staged dependencies become live references only when their
            # corresponding existing object is admitted in this exact write set.
            for handle in stage.binding["writes"]:
                original, staged = stage.originals[handle], stage.objects[handle]
                resources = stage.changes.get(handle, set())
                if resources & {"kind:transform_apply", "kind:origin_set"} and len(original.children):
                    raise ProgramError("Applying transforms or moving the origin would move this object's live children",
                                       code='UNSUPPORTED')
                self.mutation_started = True
                if resources & DATA_SWAP_RESOURCES:
                    # Shared-data policy: only this object receives the private
                    # staged copy; other users keep the original datablock.
                    if original.data is not None and original.data.users > 1:
                        isolated.append({"name": stage.logical_names[handle], "data_users_before": original.data.users})
                    original.data = staged.data
                    for target, source in zip(original.material_slots, staged.material_slots):
                        target.link, target.material = source.link, source.material
                if resources & _CURRENT_TRANSFORM_EFFECTS:
                    values = _transform_values(staged)
                    _set_transforms(original, values)
                    transferred_transforms.append((original, values))
                if "kind:parent_set" in resources:
                    original.parent = _live_id(staged.parent, source_map)
                    original.parent_type, original.parent_bone = staged.parent_type, staged.parent_bone
                    original.matrix_parent_inverse = staged.matrix_parent_inverse
                if "constraints" in resources:
                    # Constraint operators (operator_run): the whole stack by name, before the typed kinds append.
                    from .operator_state import sync_constraints
                    sync_constraints(original, staged, source_map, stack_journal, self._apply_settings,
                                     lambda message: ProgramError(message, code='INTERNAL_ERROR'))
                if "kind:constraint_add" in resources:
                    for source in list(staged.constraints)[len(original.constraints):]:
                        target = original.constraints.new(source.type)
                        new_constraints.append((original, target))
                        target.name = source.name
                        self._apply_settings(target, _settings(source), source_map)
                if "kind:constraint_configure" in resources:
                    for target in original.constraints:  # Existing constraints take their staged settings.
                        source = staged.constraints.get(target.name)
                        if source is None or source.type != target.type:
                            raise ProgramError("A constraint changed identity before commit", code='STALE_DEPENDENCY')
                        self._apply_settings(target, _settings(source), source_map)
                if "modifiers" in resources:
                    for source in staged.modifiers:
                        target = original.modifiers.get(source.name)
                        if target is None:
                            target = original.modifiers.new(source.name, source.type)
                            new_modifiers.append((original, target))
                        elif target.type != source.type:
                            raise ProgramError("A modifier changed identity before commit", code='STALE_DEPENDENCY')
                        self._apply_settings(target, _settings(source), source_map)
                    # Removed, applied and moved modifiers: match the staged stack.
                    _sync_stack(original, staged, stack_journal)
                if resources & {"modifiers", "particles"}:
                    # Edited or new ParticleSettings and per-system settings (action_particles).
                    transfer_systems(original, staged, new_modifiers, source_map, settings_journal, replaced_particles)
                if "node_trees" in resources:  # After any data swap and modifier transfer (node_edit_commit).
                    transfer_trees(original, staged, stage, next(j["data"] for o, j in journals if o == original),
                                   settings_journal, replaced_trees, isolated, **observer)
                if "vertex_groups" in resources:
                    for source in staged.vertex_groups:
                        if original.vertex_groups.get(source.name) is None:
                            target = original.vertex_groups.new(name=source.name)
                            new_groups.append((original, target))
                if "animation" in resources:
                    animation = original.animation_data_create()
                    animation.action = staged.animation_data.action if staged.animation_data else None
                    # A pushed-down Action leaves no active Action, and so no slot to assign.
                    if hasattr(animation, "action_slot") and staged.animation_data and animation.action is not None:
                        animation.action_slot = staged.animation_data.action_slot
                if "nla" in resources and "nla_stack" not in resources:  # A rebuilt stack holds the push-down too.
                    _append_tracks(original, staged, new_tracks)
                if resources & {"animation", "nla_stack"}:
                    from .anim_commit import transfer_object
                    transfer_object(stage, handle, original, staged, resources, settings_journal, undo_journal, isolated)
                if "pose" in resources:
                    # After any armature data swap (the pose was rebuilt on it): channels and settings, journaled.
                    from .bone_state import transfer_pose
                    transfer_pose(original, staged, source_map, settings_journal,
                                  stage.extras.get("posed_bones", {}).get(handle),
                                  stage.extras.get("posed_channels", {}).get(handle))
                if "properties" in resources:
                    # Object settings path by path; data paths moved with the swapped data above.
                    paths = [path for path in touched_paths(resources) if data_family(path) is None]
                    _transfer_settings(original, staged, paths, source_map, settings_journal, commit_scope=scope)
                    material_paths = [path.split(".", 1)[1] for path in touched_paths(resources)
                                      if data_family(path) == "material"]
                    if material_paths:
                        # In place on the live material the staged clone copies (the swapped
                        # data may already point at the clone), as Blender edits a material.
                        from .program_identities import settings_material
                        live_material = settings_material(staged.active_material, stage)  # None: made by this program.
                        shared_edits.record_property_edit(stage, live_material)  # Edited for all users: who.
                        if live_material is not None:
                            _transfer_settings(live_material, staged.active_material, material_paths, source_map,
                                               settings_journal, commit_scope=scope)
                if "kind:operator_run" in resources:
                    # Object-level state operators set beside the swapped data (slot and shape-key indexes, field).
                    from .operator_state import commit_object_state
                    commit_object_state(original, staged, settings_journal,
                                        lambda message: ProgramError(message, code='INTERNAL_ERROR'))
            scene = stage.binding.get("scene")
            if scene is not None and scene["write"] and scene["handle"] in stage.changes:
                self.mutation_started = True
                transfer_scene_settings(self.scene, stage, source_map, settings_journal, isolated, replaced_worlds,
                                        kept_worlds, scope)
                if "markers" in stage.changes[scene["handle"]]:
                    from .anim_markers import transfer as transfer_markers
                    transfer_markers(stage, self.scene, source_map, undo_journal)
                if "world" in stage.changes[scene["handle"]]:
                    # Shared-World policy: only this scene receives the edited copy.
                    live_world = self.scene.world
                    if live_world is not None and live_world.users > 1:
                        isolated.append({"name": label(live_world.name), "data_users_before": live_world.users})
                    settings_journal.append((self.scene, "world", live_world))
                    self.scene.world = stage.scene.world
                    if scope is not None:
                        scope.record_transfer(stage.scene.world, self.scene.world, "world_assigned", scene["handle"])
                    replaced_worlds.append((live_world, stage.scene.world))
                    _assign_environment_image(stage.scene.world, stage.extras)
                    kept_worlds.append(stage.scene.world)
                if "world_nodes" in stage.changes[scene["handle"]]:  # An edited World graph (node_edit_commit).
                    kept_worlds.append(transfer_world(self.scene, stage, settings_journal, replaced_trees, isolated, **observer))
            # Remap ONLY newly-owned IDs, never the user's old dependencies.
            # Object roots include OBJECT-linked material slots, pose actions,
            # constraints and existing modifiers whose pointers were changed.
            keep_roots = [obj for obj, _ in journals] + list(new_objects) + kept_worlds
            _restore_source_identities([obj for obj, _ in journals] + list(new_objects), stage, **observer)
            restore_sources(new_objects, stage)
            _assign_images(stage, **observer)
            replaced = []
            for obj, journal in journals:
                replaced.append((journal["data"], obj.data))
                if journal["data"] is not None and obj.data is not None and journal["data"] != obj.data:
                    replaced.append((getattr(journal["data"], "shape_keys", None), getattr(obj.data, "shape_keys", None),
                                     journal["data"]))
            from .anim_commit import replaced_actions, apply_renames
            replaced.extend(replaced_actions(stage, journals))  # The copy standing for each replaced Action.
            replaced.extend(replaced_worlds + replaced_particles + replaced_trees)
            _adopt_names(replaced, settings_journal)
            apply_renames(stage, settings_journal)  # action_set RENAME, after names are adopted.
            owned_identities = capture_cleanup_identities(stage.owned_ids + list(stage.context.created_ids))
            kept = []
            for block in _dependency_closure(keep_roots):
                pointer = block.as_pointer()
                if pointer in owned_identities and owned_identities[pointer] == (_uid(block), block.id_type):
                    kept.append(block)
                    for holder, prop, dependency in _embedded_links(block):
                        if isinstance(dependency, bpy.types.Object) and dependency.as_pointer() in source_map and not prop.is_readonly:
                            repoint(holder, prop, source_map[dependency.as_pointer()], dependency)
            stage_names.publish(stage.extras, kept, settings_journal)  # Kept copies take public names (stage_names).
            from .program_names import apply_names  # Renames and claimed names in program order, journaled.
            apply_names(stage, self.scene, new_objects, settings_journal)
            # Memberships (program_memberships): the program's collections, then each new object into exactly its
            # staged collections, then the declared objects' edits.
            self._create_collections(stage, membership_journal)
            link_new_objects(self, stage, new_objects, membership_journal, scope)
            self._apply_collection_intents(stage, membership_journal)
            verify_new_memberships(self, stage, new_objects)  # Exactly the staged collections, or a rollback.
            from .action_visibility import apply_intents
            created = {"$" + alias.lstrip("$"): obj for alias, obj in stage.context.aliases.items()}
            apply_intents(stage.extras, {**stage.originals, **created}, self.view_layer, settings_journal,
                          lambda message: ProgramError(message, code='INTERNAL_ERROR'),
                          stale=lambda message: ProgramError(message, code='STALE_DEPENDENCY'),
                          refused=lambda message: ProgramError(message, code='CHECK_FAILED'))
            if scope is not None:
                scope.validate_stage(stage)
            camera_guards.written(cameras, replaced)  # What the transfers stored, before any evaluation (animated_values).
            camera_guards.before_deletions(cameras, deleted)  # A measured camera the program deletes, read first.
            for handle in deleted:
                original = scope.approved_deletions[handle] if scope is not None else stage.originals[handle]
                doomed.extend(self._unlink_deleted(original, membership_journal))
            self.view_layer.update()
            camera_guards.frame_update(cameras)  # Blender's update after a frame change, before any check reads.
            kept_values.keep(cameras, stage, self.view_layer)  # Unkeyed values an Action's transfer re-evaluated.
            for obj, expected in transferred_transforms:
                if _transform_values(obj) != expected:
                    raise ProgramError("Committed current transform does not match its validated staged effect", code='INTERNAL_ERROR')
            matches = commit_fidelity.verify(fidelity)  # Live geometry and transforms are what the checks measured.
            commit_fidelity.verify_links(stage)  # Every linked relation the program asked for holds live.
            from .live_checks import verify_live  # Every check again, on the live objects (rolls back on failure).
            unobservable = set(getattr(self, "pending_unobservable", None) or ())
            live_checks = verify_live(self, stage, getattr(self, "pending_checks", None) or [], unobservable)
            camera_guards.verify(cameras)  # The cameras, at the frame the program ends on (rolls back on failure).
            camera_guards.measure(cameras)  # What an animation replaces of what it stored, measured, for the result.
            retained = _dependency_closure(keep_roots)
            keep = {block.as_pointer() for block in retained}
            if scope is not None:
                scope.seal_transfers(stage, new_objects, retained)
            # Capture immutable ownership BEFORE any cleanup can free RNA.
            cleanup_identities = capture_cleanup_identities(
                [block for block in stage.owned_ids + list(stage.context.created_ids)
                 if block.as_pointer() not in keep])
        except Exception as error:
            if scope is not None:
                scope.job.committed_transfers = None  # A rollback never publishes a partial correspondence.
            rollback_errors = []
            from .anim_commit import rollback as rollback_animation
            rollback_animation(undo_journal, rollback_errors)  # Before object journals may clear animation data.
            for action, collection, item in reversed(membership_journal):
                try:
                    if action == "link":
                        collection.objects.unlink(item)
                    elif action == "unlink":
                        collection.objects.link(item)
                    else:
                        if scope is None:
                            _bpy().data.collections.remove(collection)
                        else:
                            if collection in list(self.scene.collection.children):
                                self.scene.collection.children.unlink(collection)
                            scope.delete_created_collections((collection,))
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            for holder, name, value in reversed(settings_journal):
                try:
                    setattr(holder, name, value)
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            for obj in reversed(linked):
                with contextlib.suppress(Exception):
                    self.scene.collection.objects.unlink(obj)
            for collection, values in (("constraints", new_constraints), ("modifiers", new_modifiers), ("vertex_groups", new_groups)):
                for obj, item in reversed(values):
                    try:
                        getattr(obj, collection).remove(item)
                    except Exception as rollback:
                        rollback_errors.append(str(rollback))
            try:
                _restore_stacks(stack_journal)
            except Exception as rollback:
                rollback_errors.append(str(rollback))
            for animation, track in reversed(new_tracks):
                try:
                    animation.nla_tracks.remove(track)
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            for obj, journal in reversed(journals):
                try:
                    obj.data = journal["data"]
                    _set_transforms(obj, journal["transforms"])
                    obj.parent, obj.parent_type, obj.parent_bone = journal["parent"], journal["parent_type"], journal["parent_bone"]
                    obj.matrix_parent_inverse = journal["matrix_parent_inverse"]
                    for name, settings in journal["modifier_settings"].items():
                        self._apply_settings(obj.modifiers[name], settings, {})
                    for name, settings in journal["constraint_settings"].items():
                        if obj.constraints.get(name) is not None:
                            self._apply_settings(obj.constraints[name], settings, {})
                    for slot, (link, material) in zip(obj.material_slots, journal["material_slots"]):
                        slot.link, slot.material = link, material
                    if journal["animation_existed"]:
                        obj.animation_data.action = journal["action"]
                        if hasattr(obj.animation_data, "action_slot") and journal["action"] is not None:
                            obj.animation_data.action_slot = journal["slot"]
                    else:
                        obj.animation_data_clear()
                    restore_active_state(obj, journal["active"])
                except Exception as rollback:
                    rollback_errors.append(str(rollback))
            try:
                self.view_layer.update()
                camera_guards.rolled_back(cameras, settings_journal)  # Blender's update for the restored frame.
                kept_values.rolled_back(cameras, self.view_layer)  # Unkeyed values that update re-evaluated.
            except Exception as rollback:
                rollback_errors.append(str(rollback))
            failure = ProgramError("Native commit failed; " + ("rollback requires checkpoint recovery: " if rollback_errors
                                   else "") + str(error) + camera_guards.replaced(cameras),
                                   code=refusal_code(error), details=refusal_details(error))
            if rollback_errors:
                failure.partial_changes_possible = True
                failure.rollback_error = "; ".join(rollback_errors)[:1000]
            else:
                self.mutation_started = False
                # Reverse remaps also give staged copies the original live
                # dependencies. Their temporary users invalidate the binding
                # until stage cleanup; the job must verify after that cleanup.
                failure.rollback_restored = True
            raise failure from error
        removed_rows = [{"name": label(stage.logical_names[handle]), "type": stage.originals[handle].type}
                        for handle in deleted]
        released = [obj.data for obj in doomed if obj.data is not None]  # Before the objects are freed.
        cleanup_error = ""
        try:
            # A normal return proves every owned identity outside keep is gone,
            # which covers this narrower capture; no further Main scan needed.
            self._remove_stage_ids(stage, keep)
            remaining = {}
        except Exception as error:
            cleanup_error = str(error)[:1000]
            # An ordinary, unprotected cleanup can use its independent batch
            # fallback. A native refusal cannot gain public deletion authority.
            try:
                remaining = list(_owned_cleanup_ids(cleanup_identities).values())
                if scope is None and not protected_cleanup(cleanup_identities):
                    bpy.data.batch_remove(ids=remaining)
            except Exception as fallback:
                cleanup_error += "; fallback: " + str(fallback)[:1000]
            current = _owned_cleanup_ids(cleanup_identities)
            remaining = {pointer: identity for pointer, identity in cleanup_identities.items()
                         if pointer in current}
        retain_cleanup(remaining)
        if stage.bundle:
            self._remove_bundle(stage.bundle)
        # After cleanup: the stage collection no longer links the new objects,
        # so these are the revisions the next program's bind will recompute.
        live_revisions = self.post_commit_revisions(stage, new_objects)
        reshaped = commit_fidelity.dependents(self.scene, stage, [obj for obj, _ in journals] + new_objects)
        if scope is not None and doomed:
            try:
                # Last, after every check: nothing can roll back a freed ID.
                scope.delete_approved_objects(tuple(doomed))
            except Exception as error:
                cleanup_error += ("; " if cleanup_error else "") + "approved objects retained in data: " + str(error)[:200]
        elif scope is None:
            for obj in doomed:
                try:
                    bpy.data.objects.remove(obj)
                except Exception as error:
                    cleanup_error += ("; " if cleanup_error else "") + "deleted object kept in data: " + str(error)[:200]
        result = {"partial_changes_possible": False, "mutation_started": self.mutation_started,
                "verified": {"object_count": len(self.scene.objects), "write_count": len(stage.binding["writes"]),
                             "added_count": len(new_objects), "exact_preconditions": True, "matches_stage": matches,
                             "live_checks": live_checks},
                # Early in the result: bounded transcript handoffs keep the head.
                "live_revisions": live_revisions,
                "changes": {"added": [{"name": obj.name, "type": obj.type} for obj in new_objects[:MAX_RESULT_ROWS]],
                            "removed": removed_rows, "modified": self._committed_rows(stage),
                            "truncated": len(new_objects) > MAX_RESULT_ROWS, "scope": "Validated native staged transaction"}}
        result.update(self._commit_notes(stage, replaced, released))  # Leftover data; names commit had to change.
        result.update(self._world_after(world))  # The World and Sun lights it changed, as a script's result says.
        result.update(self._motion_after(stage, new_objects))  # Where what it edits that moves is, at a few frames.
        camera_guards.annotate(cameras, result)  # Unkeyed animated values its frame update replaced.
        if isolated:
            result["isolated_shared_data"] = isolated
        if stage.extras.get(shared_edits.EDITS):  # Materials edited for all their users, and who they are.
            result["shared_edits"] = stage.extras[shared_edits.EDITS]
        if reshaped:  # Objects outside the program whose shape follows one it reshaped: no check read them.
            result["unchecked_dependents"] = reshaped
        if unobservable:  # Checks no evaluation could measure (program_obligation_lifecycle: partial).
            result["unobservable_checks"] = sorted(unobservable)
        if stage.extras.get("unverified_effects"):  # Effects a kernel found unmeasurable while it ran.
            result["unverified_effects"] = list(stage.extras["unverified_effects"])
        if cleanup_error:
            # Return success so the host's ordinary operator still supplies an
            # Undo step for the completed live edit; never falsely say no edit.
            result.update(cleanup_error=cleanup_error, cleanup_warning=True,
                          recovery="The native edit committed. Inspect temporary data/Undo and restore the durable pre-request checkpoint if needed; do not automatically retry or undo.")
        if remaining:
            result.update(partial_changes_possible=True, temporary_ids_remaining=len(remaining), cleanup_pending=True)
        return result

    def verify_rollback(self, stage):
        """Read back the original binding and written paths, never undo history.

        The job calls this after owned stage cleanup, which may invoke callbacks.
        Only binding/originals/extras are used: staged RNA may already be freed.
        """
        from . import program_preimages
        self.verify(stage.binding)
        program_preimages.check(self, stage)


# Every written or created object of one program: at most 64 bound inputs plus
# one object per operation, so this cap is never reached by a valid program.
MAX_LIVE_REVISIONS = 96
# A read-back after an edit (revision_budget.READ_BACK_SECONDS): every written or created object gets a revision,
# so the next program binds them all; this bounds only a pathological commit (the operator already re-verified
# every bound revision, so written objects cost about that work once more).
POST_COMMIT_REVISION_SECONDS = READ_BACK_SECONDS
LIVE_REVISIONS_NOTE = ("Post-commit handles and exact object revisions from the production revision check. Bind a later "
                       "program or verification with them directly, without a lookup. Any later change, including a "
                       "selection, mode or frame change, makes them stale and that program fails closed. This commit "
                       "did not change objects that were only read.")


_CURRENT_TRANSFORM_EFFECTS = frozenset({"transforms", "kind:transform", "kind:parent_set", "kind:keyframe_insert"})
# Families whose commit replaces the written object's data with its staged copy.
DATA_SWAP_RESOURCES = frozenset({"mesh", "armature", "camera", "light", "curve", "data"})
