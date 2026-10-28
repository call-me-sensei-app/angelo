# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private graph cloning, closed kernel execution and prospective-scene checks.

ProgramObjectsMixin requires the backend's live scene/window/view_layer and
bind/verify/context methods. It owns isolated copies, never live writes; its
storage peer removes every Main ID before an externally observable yield."""

import contextlib
import uuid

from . import exact_fence
from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .legacy_names import PROGRAM_STAGE
from .program_storage import require_cleanup_complete
from .program_fields import remap_parent
from .program_dependencies import Stage, MAX_STAGE_IDS, _bpy, _embedded_links
from .program_verification import touched_paths, modified_row  # noqa: F401 (re-exported)
from .program_names import (CLAIMS, FORWARD, KEPT_ELSEWHERE, REPEATS, REQUESTED, held_repeats, private_name,
                            requested_names)
from . import stage_names


# Stage-only ID property naming the live material or node group a staged clone
# copies. No kernel edits an existing material or node group, so commit maps
# such clones back to their originals (program_identities._restore_source_identities).
MATERIAL_SOURCE = "_angelo_stage_source_uid"


class ProgramObjectsMixin:
    def create_stage(self, binding):
        self.ensure_main_thread()
        require_cleanup_complete()
        kept, forward = self.plan_names(binding)  # Before staging: an ambiguous name refuses here (program_names).
        # Materials a program cannot write stay live in their slots and are
        # cleared before every yield (stage_borrowing). Decided by reads, inside
        # the job's exact fence when bind just walked the same closures.
        from . import stage_borrowing
        borrowing = stage_borrowing.plan(binding)
        exact_fence.end()  # From here on, staging creates and edits IDs.
        bpy = _bpy()
        suffix = uuid.uuid4().hex
        scene = bpy.data.scenes.new("__angelo_stage_" + suffix)
        # A new scene takes the Auto-Keying preference (Blender's scene defaults): no staged operator keys.
        scene.tool_settings.use_keyframe_insert_auto = False
        collection = bpy.data.collections.new("__angelo_stage_objects_" + suffix)
        scene.collection.children.link(collection)
        scene.frame_current = self.scene.frame_current
        scene.frame_subframe = self.scene.frame_subframe
        scene[PROGRAM_STAGE[0]] = suffix
        stage = Stage(scene, collection, scene.view_layers[0], binding, originals=binding["originals"],
                      owned_ids=[scene, collection], borrowing=borrowing)
        stage.extras[stage_names.STAGE] = suffix[:8]  # Copies take private names (stage_names), never "X.001".
        try:
            # The complete dependency graph must be private before its first
            # serialized yield; incomplete clone graphs would serialize live
            # original dependencies into the bundle. This is one bounded phase.
            for handle in binding["handles"]:
                self.copy_object(stage, handle)
            if borrowing is not None and len(stage.owned_ids) + stage_borrowing.clone_bound(stage) > MAX_STAGE_IDS:
                # An inspection privatizes the borrowed slots (retain_stage); if that might not fit in the
                # stage's ID budget, clone now, so the budget refuses here, at staging, as it did before.
                self.unborrow(stage)
            stage.extras["live_names"] = dict(stage.logical_names)
            stage.extras[REQUESTED] = requested_names(binding["program"]["operations"])  # Copies avoid them.
            stage.extras[REPEATS] = held_repeats(binding["program"]["operations"], _bpy().data.objects.get)
            if kept:
                stage.extras[KEPT_ELSEWHERE] = kept
            if forward:
                stage.extras[FORWARD] = forward
            from .anim_stage import record
            record(stage, self.scene)  # Live Action users and sources, NLA strip Actions, markers (anim_stage).
            kinds = {operation["kind"] for operation in binding["program"]["operations"]}
            checks = binding["obligations"].checks()
            collection_kinds = {"collection_link", "object_duplicate", "mesh_separate", "visibility_set",
                                "object_convert", "batch"}
            if kinds & collection_kinds or any(check.get("kind") == "collection_membership" for check in checks):
                self.record_collections(stage)  # A membership check reads these, never the private stage collection.
            if "visibility_set" in kinds or any(check.get("kind") == "visibility" for check in checks):
                self.record_visibility(stage)
            if binding.get("scene"):
                self.copy_scene_settings(stage)
            from . import program_preimages
            program_preimages.stage_scene_paths(self, stage)
            from .material_sharing import stage_property_facts
            stage_property_facts(stage)  # Bound users of the materials property_set writes in place, for the kernel.
            if kinds & {"node_edit", "material_principled", "shader_nodes"}:
                # Sharing facts of every tree node_edit or a material edit in place may change; a private World copy.
                from .node_edit_commit import prepare
                prepare(self, stage)
            program_preimages.capture(self, stage)
            self.finish_stage(stage)
            from .program_material_facts import initialize_clones
            initialize_clones(stage)
            return stage
        except Exception:
            self.discard(stage)
            raise


    def _track(self, stage, block):
        if block is not None and not any(item == block for item in stage.owned_ids):
            stage.owned_ids.append(block)
            if len(stage.owned_ids) > MAX_STAGE_IDS:
                raise ProgramError("The private stage exceeds its dependency ID budget", code='LIMIT_EXCEEDED')
        return block


    def _clone_action(self, stage, block):
        animation = getattr(block, "animation_data", None)
        if animation is None:
            return
        if animation.action:
            slot_handle = getattr(animation, "action_slot_handle", None)
            live = animation.action
            animation.action = stage_names.hide(stage.extras, self._track(stage, live.copy()), live.name)
            if slot_handle is not None:
                with contextlib.suppress(AttributeError, TypeError):
                    animation.action_slot_handle = slot_handle
        for track in animation.nla_tracks:
            for strip in track.strips:
                if strip.action:
                    live = strip.action
                    copy = self._track(stage, live.copy())
                    strip.action = stage_names.hide(stage.extras, copy, live.name)


    def _clone_tree(self, stage, tree, stack=None):
        if tree is None:
            return None
        stack = set() if stack is None else set(stack)
        if tree.as_pointer() in stack:
            raise ProgramError("Recursive node groups cannot be isolated by bounded native programs", code='UNSUPPORTED')
        stack.add(tree.as_pointer())
        result = self._track(stage, tree.copy())
        if not tree.is_embedded_data:
            stage_names.hide(stage.extras, result, tree.name)
            result[MATERIAL_SOURCE] = int(tree.session_uid)
            from .program_material_facts import record_clone
            record_clone(stage, tree, result, "group")
        self._clone_action(stage, result)
        for node in result.nodes:
            if getattr(node, "node_tree", None):
                node.node_tree = self._clone_tree(stage, node.node_tree, stack)
        return result


    def _clone_material(self, stage, material):
        private = stage_names.hide(stage.extras, self._track(stage, material.copy()), material.name)
        # No kernel edits an existing material (material kinds create new ones),
        # so commit gives the live object its original back (program_commit).
        private[MATERIAL_SOURCE] = int(material.session_uid)
        from .program_material_facts import record_clone
        record_clone(stage, material, private, "live")
        self._clone_action(stage, private)
        if private.node_tree:
            if private.node_tree == material.node_tree:
                raise ProgramError("This material's node tree did not become private", code='INTERNAL_ERROR')
            self._track(stage, private.node_tree)
            self._clone_action(stage, private.node_tree)
            for node in private.node_tree.nodes:
                if getattr(node, "node_tree", None):
                    node.node_tree = self._clone_tree(stage, node.node_tree)
        return private


    def unborrow(self, stage):
        """Give a resident stage private clones of the live materials it borrows (stage_borrowing.privatize)."""
        from .stage_borrowing import privatize

        def clone(material):
            tracked = len(stage.owned_ids)
            private = self._clone_material(stage, material)
            if stage.context is not None:  # Kernels' ownership checks read the context's set.
                stage.context.owned_ids.update(stage.owned_ids[tracked:])
            return private
        privatize(stage, clone)


    def copy_object(self, stage, handle):
        exact_fence.end()
        if handle in stage.objects:
            return  # Native preparation already isolated the complete graph.
        obj = stage.originals[handle]
        duplicate = self._track(stage, obj.copy())
        stage.logical_names[handle] = obj.name
        duplicate.name = "__angelo_copy_" + stage.scene[PROGRAM_STAGE[0]][:8] + "_" + obj.name
        if obj.data is not None:
            duplicate.data = stage_names.hide(stage.extras, self._track(stage, obj.data.copy()), obj.data.name)
            if getattr(duplicate.data, "shape_keys", None):
                if duplicate.data.shape_keys == obj.data.shape_keys:
                    raise ProgramError("This object's shape-key data cannot be isolated safely", code='INTERNAL_ERROR')
                stage_names.hide(stage.extras, self._track(stage, duplicate.data.shape_keys), obj.data.shape_keys.name)
                # Shape-key animation is private too: a staged key edit never writes the live Action.
                self._clone_action(stage, duplicate.data.shape_keys)
            self._clone_action(stage, duplicate.data)
        self._clone_action(stage, duplicate)
        from . import stage_borrowing
        # One private copy per live material, however many of the object's slots hold it: two copies split one
        # material's faces between two names, and a check over all its faces counted half of them. A material
        # the program cannot write stays live in its slots instead (stage_borrowing).
        clones = {}

        def clone(material):
            if material.as_pointer() not in clones:
                clones[material.as_pointer()] = self._clone_material(stage, material)
            return clones[material.as_pointer()]

        if hasattr(duplicate.data, "materials"):
            for index, material in enumerate(tuple(duplicate.data.materials)):
                if material is None:
                    continue
                if stage_borrowing.borrows(stage, material):
                    stage_borrowing.record(stage, handle, "DATA", index, material)
                    continue
                duplicate.data.materials[index] = clone(material)
        # OBJECT-linked material slots are independent from the copied data.
        for index, slot in enumerate(duplicate.material_slots):
            if slot.link == "OBJECT" and slot.material:
                if stage_borrowing.borrows(stage, slot.material):
                    stage_borrowing.record(stage, handle, "OBJECT", index, slot.material)
                    continue
                slot.material = clone(slot.material)
        for modifier in duplicate.modifiers:
            if getattr(modifier, "node_group", None):
                modifier.node_group = self._clone_tree(stage, modifier.node_group)
        from .action_particles import clone_settings
        clone_settings(self, stage, duplicate)  # Shared ParticleSettings: each system gets a marked private copy.
        stage.objects[handle] = duplicate
        stage.collection.objects.link(duplicate)


    def plan_names(self, binding):
        """The deleted objects another scene keeps, and the requests that take a name released later (program_names)."""
        from .action_catalog import deleted_references
        from .program_names import kept_elsewhere, plan_names
        operations, originals = binding["program"]["operations"], binding["originals"]
        deleted = {reference for operation in operations for reference in deleted_references(operation)}
        kept = kept_elsewhere(originals, deleted, [self.scene.collection, *_scene_collections(self.scene)]) if deleted else []
        return kept, plan_names(operations, {handle: obj.name for handle, obj in originals.items()}, kept)


    def record_collections(self, stage):
        """The bound scene's collection names and each declared object's memberships, for intents."""
        from .action_object_management import MASTER_COLLECTION
        collections = list(_scene_collections(self.scene))
        stage.extras["scene_collections"] = [collection.name for collection in collections]
        local = {collection.name for collection in collections if collection.library is None}
        # A library collection cannot hold a local object, and never stands for a local one of its name.
        stage.extras["linked_collections"] = sorted({collection.name for collection in collections
                                                     if collection.library is not None and collection.name not in local})
        stage.extras["live_memberships"] = {
            handle: [MASTER_COLLECTION] * any(item == original for item in self.scene.collection.objects)
            + [collection.name for collection in collections if any(item == original for item in collection.objects)]
            for handle, original in stage.originals.items()}


    def record_visibility(self, stage):
        """Each declared object's view-layer hide state (None: no Base), which staged visibility checks read, and
        the view layer's collection tree, which tells whether an object will have a Base (action_visibility)."""
        from .action_visibility import hidden_state, layer_tree, LAYER_TREE
        stage.extras["live_hidden"] = {handle: hidden_state(original, self.view_layer)
                                       for handle, original in stage.originals.items()}
        stage.extras[LAYER_TREE] = layer_tree(self.view_layer)


    def copy_scene_settings(self, stage):
        """Stand the private stage scene in for the bound scene's allow-listed settings."""
        from .scene_settings import copy_settings
        staged = {original.as_pointer(): stage.objects[handle] for handle, original in stage.originals.items()}
        try:
            copy_settings(self.scene, stage.scene, staged)
        except (ValueError, TypeError, AttributeError) as error:
            raise ProgramError("The bound scene's settings could not be staged exactly: " + str(error)[:240],
                               code=refusal_code(error), details=refusal_details(error)) from error
        if any(operation["kind"] == "world_configure" for operation in stage.binding["program"]["operations"]):
            self.copy_world(stage)


    def copy_world(self, stage):
        """Give the stage scene a private copy of the bound World, without its environment image.

        Only the supported background graph is copied; the live image is
        recorded by session UID and assigned again at commit.
        """
        from .action_world_spec import WorldGraphError, world_graph
        world = self.scene.world
        if world is None:
            stage.extras["world_image_original"] = None
            return
        if world.library is not None or getattr(world, "override_library", None) is not None:
            raise ProgramError("A linked World cannot be configured natively", code='UNSUPPORTED')
        try:
            graph = world_graph(world)
        except WorldGraphError as error:
            raise ProgramError(str(error), code='UNSUPPORTED') from error
        image = graph["environment"].image if graph["environment"] is not None else None
        stage.extras["world_image_original"] = int(image.session_uid) if image is not None else None
        from .action_world import COMMITTED_NAME
        private = stage_names.hide(stage.extras, self._track(stage, world.copy()), world.name)
        # Users besides this stage: the bound scene alone means the copy takes the World's name at commit;
        # otherwise the copy keeps a name of its own, reserved now. Commit corrects either if the users change.
        users = world.users - int(stage.scene.world == world)
        taken = lambda name: _bpy().data.worlds.get((name, None)) is not None  # noqa: E731
        stage.extras[COMMITTED_NAME] = world.name if users <= 1 else stage_names.reserve(stage.extras, private, taken)
        if private.node_tree is not None:
            self._track(stage, private.node_tree)
        environment = world_graph(private)["environment"]
        if environment is not None:
            environment.image = None
        stage.scene.world = private


    def finish_stage(self, stage):
        exact_fence.end()  # Remaps pointers while walking links.
        from .stage_borrowing import owns_animated
        if owns_animated(stage):
            # A staged copy is animated although _plan saw no animation. Nothing has evaluated this new
            # stage yet, so nothing went through a borrowed slot: clone now, as for a program that borrows nothing.
            self.unborrow(stage)
        from .action_kernels import StagedActionContext
        mapping = {obj.as_pointer(): stage.objects[handle] for handle, obj in stage.originals.items()}
        for block in tuple(stage.owned_ids):
            if isinstance(block, (_bpy().types.Scene, _bpy().types.Collection)):
                continue  # Fresh stage container settings contain no source object links.
            for holder, prop, dependency in _embedded_links(block):
                if isinstance(dependency, _bpy().types.Object):
                    private = mapping.get(dependency.as_pointer())
                    if private is None and not any(dependency == value for value in stage.objects.values()):
                        raise ProgramError("An undeclared object dependency would escape the private stage: " +
                                           dependency.name[:64] + " via " + holder.bl_rna.identifier + "." + prop.identifier,
                                               code='INTERNAL_ERROR')
                    if private is not None:
                        if prop.is_readonly:
                            raise ProgramError("A readonly dependency cannot be remapped safely", code='UNSUPPORTED')
                        if isinstance(holder, _bpy().types.Object) and prop.identifier == "parent":
                            remap_parent(holder, private)
                        else:
                            setattr(holder, prop.identifier, private)
        scene = stage.binding.get("scene")
        from .kept_values import FOLLOW, follow
        stage.extras[FOLLOW] = follow(stage.binding["program"])  # What the program has Blender evaluate.
        stage.context = StagedActionContext(stage.scene, stage.collection, objects=stage.objects,
                                           owned_ids=stage.owned_ids, view_layer=stage.view_layer,
                                           scene_handles=(scene["handle"],) if scene else (), extras=stage.extras)
        stage.context.borrowing = stage.borrowing
        self._update_stage(stage)


    def _update_stage(self, stage):
        exact_fence.end()  # Evaluation runs drivers and depsgraph handlers.
        from .action_budget import assert_evaluated, assert_stage_budgets
        from .kept_values import kept_update
        assert_stage_budgets(stage.context)
        kept_update(stage.context, stage.view_layer.update)  # Unkeyed values Blender keeps stay (kept_values).
        assert_evaluated(stage.view_layer.depsgraph, stage.collection.objects)


    @contextlib.contextmanager
    def _override(self, stage):
        """The program's window, then the stage's scene and view layer as one pair in an override of their own.

        Given in the same call, the window resets the scene to the artist's (``CTX_wm_window_set`` drops a
        Python ``scene`` override) while ``view_layer`` stays the stage's. An operator a kernel then calls
        without binding the pair again (``action_particles.copy_settings``) has Blender update a depsgraph
        of the artist's scene with the stage's view layer, and the app crashes.
        """
        bpy = _bpy()
        with bpy.context.temp_override(window=self.window), \
                bpy.context.temp_override(scene=stage.scene, view_layer=stage.view_layer):
            yield


    def apply(self, stage, operation):
        exact_fence.end()  # A kernel edits the stage.
        from .action_catalog import resource_dependencies
        from .action_kernels import apply_operation
        from .program_material_facts import begin_operation, finish_operation
        proof = begin_operation(stage, operation)
        with self._override(stage):
            outcome = apply_operation(stage.context, operation)
        finish_operation(stage, operation, proof)
        claims = stage.extras.get(CLAIMS)
        if claims and isinstance(outcome, dict) and outcome.get("object") in claims:
            outcome["object"] = claims[outcome["object"]]  # A new object's committed name, never its placeholder.
        elif isinstance(outcome, dict) and private_name(outcome.get("object")):
            # A declared object's private copy ("__angelo_copy_<id>_Cube"): the name the person knows it by.
            from .program_names import current_name
            reference = next((handle for handle, obj in stage.objects.items() if obj.name == outcome["object"]), None)
            if reference is not None:
                outcome["object"] = current_name(stage.extras, reference, stage.objects[reference])
        if isinstance(outcome, dict) and stage_names.mentions(outcome):  # A staged copy's private name.
            outcome = stage_names.shown(stage.extras, outcome)
        target = operation["target"]
        if target is not None:
            dependencies = resource_dependencies(operation)
            resources = stage.changes.setdefault(target, set())
            mutates = dependencies["mutates"]
            if isinstance(outcome, dict) and outcome.get("edited_in_place"):
                resources.add("node_trees")  # An existing material edited in place (node_edit_commit.transfer_trees).
                if outcome.get("shared_policy") == "ALL_USERS" and not outcome.get("assigned_faces"):
                    # Only the tree changed: the mesh is not swapped, so an instance keeps its shared data.
                    mutates = [family for family in mutates if family not in ("mesh", "materials")]
            resources.update(mutates)
            resources.add("kind:" + operation["kind"])
            # Each written setting path; commit transfers exactly these.
            resources.update("prop:" + path for path in dependencies["properties"])
        for reference, families in (outcome.get("writes") or {}).items() if isinstance(outcome, dict) else ():
            stage.changes.setdefault(reference, set()).update(families)  # A batch moved its declared sources.
        return outcome


def _scene_collections(scene):
    """Every collection in the scene's hierarchy below its master collection, once each."""
    seen, pending = set(), list(scene.collection.children)
    while pending:
        collection = pending.pop(0)
        if collection.as_pointer() in seen:
            continue
        seen.add(collection.as_pointer())
        yield collection
        pending.extend(collection.children)
