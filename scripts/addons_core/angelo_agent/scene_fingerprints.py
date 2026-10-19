# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Combine exact supported object/dependency state into immutable field hashes."""

from itertools import islice
import time
import bpy
from .scene_read_fields import MODIFIER_FIELDS, KNOWN_CONSTRAINTS
from .scene_read_values import (BudgetExceeded, SceneQueryError, hash_value, label,
                                vector, session_uid, pointer, unreadable_revision)
from .action_properties import OBJECT as _OBJECT_OWNER, PROPERTY_PATHS, plain
from .generic_settings import foreign_ids, object_pointers

# Object settings property_set can write, so the observed revision binds them.
OBJECT_SETTING_PATHS = tuple(path for path, spec in PROPERTY_PATHS.items() if spec[0] == _OBJECT_OWNER and spec[1] is None)
# Curve and text data are covered by their complete exact RNA (splines, points,
# body and formatting), bounded by this many recursive items.
MAX_CURVE_DATA_ITEMS = 200000


def collection_memberships(wanted, deadline, max_links):
    """Session_uid -> linking collections for the objects in `wanted`, or None.

    Matches Object.users_collection membership and order: Main collections in
    bpy.data order, then each scene's master collection in bpy.data.scenes order.
    That property is implemented in Python and rescans every collection's object
    list on each call, so per-object reads made whole-scene passes quadratic in
    the object count. This index visits every collection link once per pass but
    records only the pass's own objects, so its memory follows the bound scene
    even when other scenes or linked libraries hold many more links.

    The index is all-or-nothing: it returns None once the pass deadline passes
    (checked every 4,096 links) or the recorded links exceed max_links, and
    callers then read users_collection per object as before. A partial index is
    never returned or retained.
    """
    memberships = {}
    links = 0
    for owner in [*bpy.data.collections, *(scene.collection for scene in bpy.data.scenes)]:
        for index, member in enumerate(owner.objects):
            if index % 4096 == 0 and time.monotonic() > deadline:
                return None
            identity = member.session_uid
            if identity in wanted:
                links += 1
                if links > max_links:
                    return None
                memberships.setdefault(identity, []).append(owner)
    return memberships


def membership_reader(memberships):
    """Return obj -> tuple of its linking collections, preferring a complete index."""
    if memberships is None:
        return lambda obj: tuple(obj.users_collection)
    return lambda obj: tuple(memberships.get(obj.session_uid, ()))


def _dependency_targets(obj, unsupported):
    """UID -> (object, exclusively Mirror origin), with ordinary roles dominant.

    Mirror reads its origin's object transform, not its evaluated geometry.
    Retain every edge's role before deduplication: another use of the same
    object must keep the ordinary nested-modifier guard, in either order.
    """
    constraints = list(obj.constraints) + ([c for b in obj.pose.bones for c in b.constraints] if obj.pose else [])
    edges = [(obj.parent, False), *([(obj.data.dof.focus_object, False)] if obj.type == "CAMERA" else []),
             *[(getattr(m, f, None), f == "mirror_object" and m.type == "MIRROR")
               for m in obj.modifiers for f in MODIFIER_FIELDS.get(m.type, ())],
             *[(target, False) for m in obj.modifiers if m.type not in MODIFIER_FIELDS for target in object_pointers(m)],
             *[(target, False) for p in getattr(obj, "particle_systems", ()) if p.settings for target in object_pointers(p.settings)],
             *[(getattr(c, f, None), False) for c in constraints for f in ("target", "pole_target", "space_object")],
             *[(getattr(t, "target", None), False) for c in constraints for t in getattr(c, "targets", ())],
             *[(target, False) for c in obj.constraints if c.type not in KNOWN_CONSTRAINTS for target in object_pointers(c)]]
    targets = {}
    for value, mirror_only in edges:
        if isinstance(value, bpy.types.Object) and value != obj:
            uid = session_uid(value)
            if uid in targets:
                mirror_only = mirror_only and targets[uid][1]
            targets[uid] = value, mirror_only
        elif isinstance(value, bpy.types.Collection):
            unsupported.append("collection_instance_or_modifier_dependency")
    return targets


class ObjectFingerprinter:
    """Combine fresh readers with explicitly supplied identity/metric services.

    Identity and cache state remain owned by scene_queries. Only callbacks,
    reader services, bounds and the owner's counters are retained here.
    """
    def __init__(self, metadata, geometry, object_handle, object_ref, resource,
                 current_epoch, selection, visibility, counters, max_list):
        self.metadata = metadata
        self.geometry = geometry
        self.object_handle = object_handle
        self.object_ref = object_ref
        self.resource = resource
        self.current_epoch = current_epoch
        self.selection = selection
        self.visibility = visibility
        self.counters = counters
        self.max_list = max_list

    def curve_data(self, obj, work, unsupported):
        """Exact digest of a curve or text datablock, or a stated limitation."""
        from .program_dependencies import _rna_values
        from .program_jobs import ProgramError
        data = obj.data
        if data is None:
            unsupported.append("missing_curve_data")
            return hash_value(None)
        if getattr(data, "bevel_object", None) is not None or getattr(data, "taper_object", None) is not None:
            unsupported.append("curve_object_dependency")
        budget = [MAX_CURVE_DATA_ITEMS, MAX_CURVE_DATA_ITEMS * 8]
        try:
            value = _rna_values(data, budget=budget)
        except ProgramError:
            unsupported.append("curve_data_budget")
            return hash_value(None)
        work.take(items=MAX_CURVE_DATA_ITEMS - budget[0])
        return hash_value(value)

    def capture(self, window, obj, work, collections_of=None):
        """Small immutable field hashes plus allowlisted metadata; all mutable deps.

        Whole-scene passes supply collections_of from membership_reader(); a
        single-object capture reads Object.users_collection once instead.
        """
        self.counters["exact_checks"] += 1
        handle = self.object_handle(obj)
        epoch = self.current_epoch()
        users = collections_of(obj) if collections_of is not None else tuple(obj.users_collection)
        summary = dict(self.object_ref(obj), data=self.resource(obj.data) if obj.data else None,
                       selected=self.selection(window, obj), visible=self.visibility(window, obj), mode=obj.mode,
                       collections=[label(c.name) for c in islice(users, 32)],
                       collection_count=len(users), active=window.view_layer.objects.active == obj,
                       asset=self.resource(obj, "OBJECT").get("asset"))
        fields = {"name": hash_value(summary["name"]), "selection": hash_value([summary["selected"], summary["active"]]),
                  "visibility": hash_value([summary["visible"], obj.hide_viewport, obj.hide_render, obj.hide_select]),
                  "collections": hash_value([len(users), [[session_uid(c), label(c.name)] for c in islice(users, self.max_list)]]), "mode": hash_value(obj.mode),
                  "data": hash_value(summary["data"]), "asset": hash_value(summary["asset"])}
        complete = True
        unsupported = []
        captured = {}
        try:
            work.take(items=1)
            if len(users) > self.max_list:
                raise BudgetExceeded()
            captured["invalidation_ids"] = [session_uid(window.scene), *[session_uid(c) for c in users]]
            if obj.mode == "SCULPT":
                unsupported.append("live_sculpt_surface_requires_exact_sculpt_observation")
            if getattr(obj, "rigid_body", None) or getattr(obj, "rigid_body_constraint", None):
                unsupported.append("physics_or_particle_dependency")
            if len(getattr(obj, "particle_systems", ())):
                # Each system's ParticleSettings (per-system settings are the modifier's generic paths).
                from .action_particles import systems_revision
                fields["particles"] = systems_revision(obj, work, unsupported)
            field = getattr(obj, "field", None)
            if getattr(field, "type", "NONE") != "NONE":
                # A force field (property_set field.* paths): its writable settings, unless it uses a datablock.
                from .generic_settings import values as generic_values
                if foreign_ids(field):
                    unsupported.append("force_field_configuration")
                else:
                    fields["force_field"] = hash_value(generic_values(field, lambda value: label(value.name)))
            if getattr(obj, "instance_type", "NONE") != "NONE" or (obj.type == "EMPTY" and obj.data is not None):
                unsupported.append("instancing_or_empty_image_dependency")
            transform = {"location": vector(obj.location), "rotation_mode": obj.rotation_mode,
                         "rotation_euler": vector(obj.rotation_euler), "rotation_quaternion": vector(obj.rotation_quaternion),
                         "rotation_axis_angle": vector(obj.rotation_axis_angle) if hasattr(obj, "rotation_axis_angle") else None,
                         "scale": vector(obj.scale), "matrix_world": [vector(row) for row in obj.matrix_world],
                         "matrix_parent_inverse": [vector(row) for row in obj.matrix_parent_inverse],
                         "delta_location": vector(obj.delta_location), "delta_rotation_euler": vector(obj.delta_rotation_euler),
                         "delta_rotation_quaternion": vector(obj.delta_rotation_quaternion), "delta_scale": vector(obj.delta_scale),
                         "parent": self.object_ref(obj.parent) if obj.parent else None, "parent_type": obj.parent_type,
                         "parent_bone": label(obj.parent_bone), "lock_location": list(obj.lock_location),
                         "lock_rotation": list(obj.lock_rotation), "lock_scale": list(obj.lock_scale)}
            captured["transform"] = transform
            fields["transform"] = hash_value(transform)
            # Every Blender object has these settings; observation stand-ins may lack some.
            fields["settings"] = hash_value([plain(getattr(obj, path, None)) for path in OBJECT_SETTING_PATHS])
            if obj.type in {"CURVE", "FONT"}:
                fields["curve_data"] = self.curve_data(obj, work, unsupported)
            if obj.type == "GREASEPENCIL":  # Layers, frames and drawings: its complete exact RNA, as for curves.
                fields["grease_pencil_data"] = self.curve_data(obj, work, unsupported)
            if obj.type == "CURVES":  # Hair curves: points, curves and attributes, its complete exact RNA.
                fields["curves_data"] = self.curve_data(obj, work, unsupported)
            camera_light = self.metadata.camera_light(obj, work)
            if camera_light is not None:
                # Only camera/light revisions gain this field; others are unchanged.
                captured["camera_light"] = camera_light
                fields["camera_light"] = hash_value(camera_light)
            mesh = self.geometry.mesh(obj, work)
            captured["mesh"] = mesh
            fields["geometry"] = mesh["geometry_revision"] if mesh else hash_value(None)
            modifiers, unsupported_modifiers = self.metadata.modifiers(obj, work)
            captured["modifiers"] = modifiers
            fields["modifiers"] = hash_value(modifiers)
            unsupported.extend(unsupported_modifiers)
            materials = self.metadata.materials(obj, work)
            captured["materials"] = materials
            fields["materials"] = hash_value(materials)
            rig = self.metadata.rig(obj, work)
            captured["rig"] = rig
            fields["rig"] = hash_value(rig)
            if obj.type == "ARMATURE" and obj.data is not None:
                # Every value bone_set writes (and the armature data's settings), B-Bone handles included.
                from .bone_state import armature_state
                fields["bones"] = hash_value(armature_state(obj, work, lambda value: label(value.name)))
            unsupported.extend("constraint:" + c.type for c in obj.constraints
                               if c.type not in KNOWN_CONSTRAINTS and foreign_ids(c))
            if obj.pose:
                unsupported.extend("constraint:" + c.type for b in obj.pose.bones for c in b.constraints if c.type not in KNOWN_CONSTRAINTS)
            animation = {"object": self.metadata.animation(obj, work), "data": self.metadata.animation(obj.data, work) if obj.data else None,
                         "shape_keys": self.metadata.animation(obj.data.shape_keys, work) if obj.type == "MESH" and obj.data.shape_keys else None,
                         "materials": [self.metadata.animation(s.material, work) for s in obj.material_slots if s.material]}
            captured["animation"] = animation
            fields["animation"] = hash_value(animation)
            if any(a and any(not d["native"] for d in a["drivers"])
                   for a in [animation["object"], animation["data"], animation["shape_keys"], *animation["materials"]]):
                unsupported.append("animation_driver_expressions")  # Only a driver Blender cannot evaluate natively.
            if obj.type == "LATTICE":
                fields["lattice_data"] = self.lattice_data(obj, work, unsupported)
            from .node_graph_check import trees_revision  # Every node setting and frame node_edit can write.
            trees = trees_revision(obj, work, unsupported)
            if trees is not None:
                fields["node_trees"] = trees
            from .operator_state import revision_state  # What operator_run can change beyond the fields above.
            fields["operator_state"] = hash_value(revision_state(obj, work))
            if obj.type not in {"MESH", "EMPTY", "ARMATURE", "CAMERA", "LIGHT", "CURVE", "FONT", "LATTICE", "GREASEPENCIL",
                                "CURVES"}:
                # Descriptive queries remain available. Do not use incomplete data
                # coverage to authorize arbitrary native edits to another ID type.
                unsupported.append("unfingerprinted_data:" + obj.type)
            # Modifiers/constraints can depend on other geometry. Hash those original
            # targets too, but never recurse cycles or produce evaluated pointers.
            dependencies = []
            targets = _dependency_targets(obj, unsupported)
            if obj.instance_collection:
                unsupported.append("collection_instance_or_modifier_dependency")
            for target, mirror_only in targets.values():
                work.take(items=1)
                target_mesh = self.geometry.mesh(target, work)
                target_rig = self.metadata.rig(target, work)
                dependencies.append({"object": self.object_handle(target), "matrix_world": [vector(row) for row in target.matrix_world],
                                     "transform": self.metadata.settings(target, ("location", "rotation_mode", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                                                                     "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale", "hide_viewport", "hide_render")),
                                     "geometry": target_mesh["geometry_revision"] if target_mesh else None,
                                     "mesh_limitations": target_mesh.get("revision_limitations", []) if target_mesh else [],
                                     "rig": target_rig, "animation": self.metadata.animation(target, work),
                                     "data_animation": self.metadata.animation(target.data, work) if target.data else None,
                                     "shape_key_animation": self.metadata.animation(target.data.shape_keys, work) if target.type == "MESH" and target.data.shape_keys else None,
                                     "materials": self.metadata.materials(target, work)})
                if target_mesh:
                    unsupported.extend(target_mesh.get("revision_limitations", []))
                if target.type in {"CURVE", "FONT"}:
                    dependencies[-1]["curve_data"] = self.curve_data(target, work, unsupported)
                elif target.type == "LATTICE":
                    dependencies[-1]["lattice_data"] = self.lattice_data(target, work, unsupported)
                elif target.type in {"CAMERA", "LIGHT"}:
                    # A constraint or modifier target camera/light: its settings join the revision.
                    dependencies[-1]["camera_light"] = self.metadata.camera_light(target, work)
                elif target.type not in {"MESH", "EMPTY", "ARMATURE"}:
                    unsupported.append("unfingerprinted_dependency_data:" + target.type)
                # Only the modifier-stack refusal is role-specific. Parents,
                # constraints, drivers and live Sculpt keep their existing guards.
                # Physics may affect the transform independently of these fields.
                mirror_transform = mirror_only and not (getattr(target, "rigid_body", None) or
                                                        getattr(target, "rigid_body_constraint", None))
                if (target.modifiers and not mirror_transform) or target.constraints or (target.pose and any(b.constraints for b in target.pose.bones)) or target.parent:
                    unsupported.append("nested_dependency_configuration")
                if target.mode == "SCULPT":
                    unsupported.append("live_sculpt_dependency_requires_exact_sculpt_observation")
            fields["dependencies"] = hash_value(dependencies)
            captured["dependency_ids"] = list(targets)
            captured["dependencies"] = dependencies
        except BudgetExceeded:
            complete = False
        unsupported.extend(_nested_limitations([captured, summary]))
        if epoch != self.current_epoch():
            raise SceneQueryError("Document IDs were replaced during observation; search again")
        exact = complete and not unsupported
        # Stillness only where a revision is given: a capture that is not exact keeps the frame.
        state_still = exact and _still(obj, work)
        fields["context"] = context_hash(window, state_still, window.scene.frame_current,
                                         getattr(window.scene, "frame_subframe", 0.0))
        summary.update(revision_exact=exact, object_revision=hash_value([handle, fields]) if exact else None,
                       program_revision_supported=exact,
                       revision_limitations=sorted(set(unsupported)))
        if not complete:
            summary["uncertain_reason"] = "original_state_work_budget"
        elif unsupported:
            summary["uncertain_reason"] = "untracked_mutable_configuration"
        state = {"summary": summary, "fields": fields, "captured": captured, "exact": exact, "still": state_still}
        from .recovery_evidence import object_capture
        object_capture(state)
        return state

    def lattice_data(self, obj, work, unsupported):
        """A lattice cage: its writable settings and every point's deformed position (bounded)."""
        points = obj.data.points
        if len(points) > 4096:
            unsupported.append("lattice_size")
            return None
        work.take(items=len(points) + 8)
        from .generic_settings import values as generic_values
        return hash_value([generic_values(obj.data, lambda value: label(value.name)), [vector(p.co_deform) for p in points]])

    def revision(self, window, obj, work):
        """The exact object_revision, or the ``SceneQueryError`` naming why it is not exact."""
        return require_exact(self.capture(window, obj, work))["summary"]["object_revision"]

    def revision_at_frame(self, window, obj, work, frame_current):
        """The object_revision with only the context field's frame replaced by ``frame_current``.

        Equal to a declared revision observed at that frame exactly when every
        other field is unchanged, so a frame-only move is provable (program_context).
        For an object nothing moves in time it is the frame-free revision (``context_field``).
        One capture both gates exactness and is hashed: an incomplete (over
        budget) or untracked one raises ``SceneQueryError``, never a partial hash.
        """
        state = require_exact(self.capture(window, obj, work))
        fields = dict(state["fields"], context=context_hash(window, state["still"], frame_current, 0.0))
        return hash_value([state["summary"]["object"], fields])


def _still(obj, work):
    """``revision_time.still``, remembered for the pass in its budget's shared reads and charged one item."""
    from .revision_time import still, still_in_pass
    try:
        work.take(items=1)
    except BudgetExceeded:
        return False  # Out of work: the frame stays in, as before.
    memo = getattr(work, "shared", None)
    return still_in_pass(obj, memo) if isinstance(memo, dict) else still(obj)


def context_hash(window, still, frame_current, frame_subframe=0.0):
    """The ``context`` field: the view layer, with the frame (and a subframe that is not 0) unless ``still``.

    The subframe joins only when it is not 0, so every revision observed on a whole frame keeps its value; a
    Wave or other time-dependent result at frame 10.5 is not the one at frame 10.
    """
    if still:
        return hash_value([None, pointer(window.view_layer)])
    frame = [frame_current, float(frame_subframe)] if frame_subframe else frame_current
    return hash_value([frame, pointer(window.view_layer)])


def context_field(window, obj, frame_current):
    """The revision's context: the view layer, and the frame only where the object's state can depend on it.

    Fields above hash original data; an object's evaluated state (a Wave or
    geometry-nodes result, an animated or driven value, a constraint, a parent,
    physics) can change with the frame without any of them changing, so the
    frame guards that. An object nothing can move in time
    (``revision_time.still``) observed at one frame is exactly what it is at
    another, so its revision leaves the frame out: a script's or the person's
    frame change does not stale bindings it cannot affect. Whether the frame
    is in is itself hashed (None versus a number), so an object that gains
    animation or a time-dependent modifier changes revision.
    """
    from .revision_time import still
    return context_hash(window, still(obj), frame_current)


def require_exact(state):
    """A capture whose revision is exact, else the ``SceneQueryError`` naming why it is not."""
    if not state["exact"]:
        summary = state["summary"]
        budget = ["original_state_work_budget"] if summary.get("uncertain_reason") == "original_state_work_budget" else []
        raise unreadable_revision(sorted(set(summary.get("revision_limitations") or []) | set(budget))
                                  or ["original_state_work_budget"])
    return state


def _nested_limitations(value):
    """Every revision limitation (and driver use) anywhere in nested captured dicts and lists.

    Callers use only which limitations occur (emptiness and the sorted set), so
    the walk keeps an explicit stack instead of one generator per container: a
    material graph alone holds hundreds of them, read for every object.
    """
    found, stack = [], [value]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            found.extend(value.get("revision_limitations", ()))
            if any(not isinstance(driver, dict) or not driver.get("native") for driver in value.get("drivers") or ()):
                found.append("animation_driver_expressions")
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return found


def dependency_ids(state):
    ids, stack = set(), [state]
    while stack:  # An explicit stack: the same set as a recursive walk, without a call per container.
        value = stack.pop()
        if isinstance(value, dict):
            ids.update(value.get("invalidation_ids", ()))
            for field in ("object", "id"):
                item = value.get(field)
                if type(item) is str and (item.startswith("scn:") or item.startswith("dat:")):
                    ids.add(int(item.rsplit(":", 1)[1]))
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return frozenset(ids)
