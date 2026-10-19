# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verification of a private stage and its bounded change summary.

Checks run against the stage overlaid on the bound scene, reading the intents
commit will carry out (memberships, deletions, renames, visibility, images);
``delta`` is the bounded preview of what commit would change.
"""

import copy
import json
import math
from array import array

from .program_jobs import ProgramError
from .scene_read_values import all_finite
from .program_commit import DATA_SWAP_RESOURCES
from .program_transfers import MAX_RESULT_ROWS
from .program_names import CLAIMS, committed_name, private_name
from . import stage_names
from .program_dependencies import (MAX_TOTAL_VERTICES, MAX_STAGE_IDS, _bpy, _bpy_vector, _embedded_links,  # noqa: F401
                                   _mutable_dependencies, linked_once)


class ProgramVerificationMixin:
    def _verification_context(self, stage, overrides=None):
        from .scene_validation import VerificationContext
        deleted = set(stage.extras.get("deleted", ()))
        targets = {handle: obj for handle, obj in stage.objects.items() if handle not in deleted}
        targets.update({"$" + key.lstrip("$"): value for key, value in stage.context.aliases.items()})
        targets.update({handle: stage.scene for handle in stage.context.scene_handles})
        replaced = {obj.as_pointer() for obj in stage.originals.values()}
        removed = {stage.objects[handle].as_pointer() for handle in deleted}
        overlay = ([obj for obj in self.scene.objects if obj.as_pointer() not in replaced]
                   + [obj for obj in stage.scene.objects if obj.as_pointer() not in removed])
        bindings = {handle: {"handle": handle, "revision": revision} for handle, revision in stage.binding["revisions"].items()}
        context = VerificationContext(targets=targets, scene_objects=overlay, bindings=bindings,
                                      depsgraph=stage.view_layer.depsgraph)
        context.logical_names = {handle: stage.extras.get("renames", {}).get(handle, name)
                                 for handle, name in stage.logical_names.items()}
        context.shown_name = _staged_names(stage, overrides)  # Never a private name (a check's name fields).
        from .anim_commit import committed_names
        context.action_names = committed_names(stage)  # Staged Action copies read under their committed names.
        # Staged objects sit in the private stage collection; membership checks
        # read the memberships commit will produce.
        context.memberships = {reference: list(names) for reference, names in stage.extras.get("memberships", {}).items()}
        context.memberships.update({handle: list(names) for handle, names in stage.extras.get("live_memberships", {}).items()
                                    if handle not in context.memberships})
        if "live_memberships" in stage.extras:  # New objects: where commit links them (program_memberships).
            from .action_object_management import _membership
            context.memberships.update({alias: _membership(stage.context, alias) for alias in targets
                                        if alias.startswith("$") and alias not in context.memberships})
        if "live_hidden" in stage.extras:
            # Staged copies have no Base in the bound view layer: checks read the hide state commit will produce.
            # A collection's exclude is the flag the program ends with: an edit of a parent sets its descendants.
            from .view_layer_exclusion import effective_collections
            intents = stage.extras.get("visibility", {})
            context.visibility = {"objects": {**stage.extras["live_hidden"], **intents.get("objects", {})},
                                  "collections": effective_collections(stage.extras)}
            context.view_layer = self.view_layer
        if "world_image_original" in stage.extras or "world_image" in stage.extras:
            # The staged World holds no image; checks compare the image commit will assign.
            from .action_world import intended_image
            context.environment_images = {handle: intended_image(stage.extras) for handle in stage.context.scene_handles}
        return context


    def _scene_change(self, stage):
        """The bound scene's handle and name when this program wrote its settings, else None."""
        from .scene_read_values import label
        scene = stage.binding.get("scene")
        if scene is None or scene["handle"] not in stage.changes:
            return None
        return {"handle": scene["handle"], "name": label(self.scene.name)}


    def _live_value(self, owner, path):
        from .action_properties import holder_and_name, plain, value_kind
        from . import scene_queries
        holder, name = holder_and_name(owner, path)
        value = getattr(holder, name)
        if holder.bl_rna.properties[name].type == "POINTER":
            handle = scene_queries.object_handle(value) if value is not None else None
            return handle if value_kind(path) == "object" or handle is None else {"object": handle}
        return plain(value)


    def _staged_value(self, stage, owner, path):
        from .action_property import read
        return read(stage.context, owner, path)


    def validate(self, stage, check):
        from .scene_validation import verify
        self._update_stage(stage)
        check = copy.deepcopy(check)
        guard = check.pop("__host_material", None)
        link_guard = check.pop("__host_node_links", None)
        identity = None
        shown = {}  # A staged copy's private name -> the name the program and the person know (in results only).
        if guard is not None:
            from .program_material_facts import guard_material
            material, identity = guard_material(stage, check, guard)
            if not identity["passed"]:
                return {"success": False, "passed": False, "status": "fail", "kind": check["kind"], "checks": [identity],
                        "message": "The required material identity or exact slot changed"}
        target = check.get("target")
        obj = stage.objects.get(target)
        holder = obj if obj is not None else stage.context.aliases.get(target)  # A new object may hold one too.
        if check["kind"] == "object_name" and private_name(check["expected"]):
            # A private staged name can match on the stage but never live (program_spec refuses it in a plan).
            reason = "a private staged name is never an object's committed name"
            return {"success": False, "passed": False, "status": "fail", "kind": "object_name", "message": "object_name: " + reason,
                    "checks": [{"index": 0, "kind": "object_name", "target": target, "status": "fail", "passed": False,
                                "reason": reason}]}
        # Checks keep the names the program wrote: a staged object, material or group is read under the name
        # commit gives it (_staged_names). A guarded material is the proven slot's, whatever its name.
        overrides = {material.as_pointer(): check["material"]} if guard is not None else None
        if guard is not None and check["kind"] in {"material_assignment", "material_values"} and not check.get("all_faces"):
            from .program_material_facts import selected_material_report
            report = selected_material_report(material, check)
        else:
            report = verify(self._verification_context(stage, overrides), [check])
        if identity is not None:
            report["checks"].append(identity)
        if _mentions_private(report["checks"]):  # Another staged object a check named (a parent, the first hit).
            from .program_names import current_name
            shown.update((obj.name, current_name(stage.extras, handle, obj)) for handle, obj in stage.objects.items())
            shown.update(stage.extras.get(CLAIMS, {}))
        if shown:
            shown = {key: value for key, value in shown.items() if key != value}
            report["checks"] = _shown_names(report["checks"], shown)
        if stage_names.mentions(report["checks"]):
            report["checks"] = stage_names.shown(stage.extras, report["checks"])
        if link_guard is not None:
            from .program_node_link_checks import verify_absent_links
            owner = stage.scene if target in stage.context.scene_handles else holder
            links = verify_absent_links(owner, check, link_guard, _staged_names(stage, overrides))
            report["checks"].insert(0 if not links["passed"] else len(report["checks"]), links)
            if not links["passed"]:
                report.update(success=False, status=links["status"])
        return dict(report, passed=report["success"], kind=check["kind"], message=failure_text(check["kind"], report))


    def final_checks(self, stage, before_apply=None):
        """Every check commit makes before its first live write, after the stage's last evaluation.

        Order: evaluate the stage (drivers and depsgraph handlers can run), then,
        reading only, in one exact fence: stage isolation and validity, the
        controller's preparation fence (a fresh exact precondition of every
        input), fresh revisions and fingerprints against the binding, and the
        written settings' live values. Nothing between them can change the
        document, so each input's closure, fingerprint, custom-link admission
        and revision is read once and serves every check (exact_fence).
        """
        from . import exact_fence, program_preimages
        self._update_stage(stage)
        with exact_fence.fence():
            self.check_stage(stage)
            if before_apply is not None:
                before_apply()
            self.verify(stage.binding)
            program_preimages.check(self, stage)  # Generic settings changed live since staging: stale.

    def prepare_commit(self, stage):
        from .exact_fence import end
        end()  # Writes the stage's provenance markers.
        from .program_material_facts import snapshot_provenance, strip_provenance
        snapshot_provenance(stage)
        strip_provenance(stage)


    def validate_stage(self, stage):
        """Evaluate the stage, then prove its isolation and validity (check_stage)."""
        self._update_stage(stage)
        self.check_stage(stage)

    def check_stage(self, stage):
        """Isolation, Object Mode, topology and finite-value checks of an evaluated stage; reads only.

        Commit runs it inside its exact fence, after its last stage evaluation, so
        the originals' closures it walks serve the checks that follow.
        """
        from . import exact_fence
        with exact_fence.fence():
            self._check_stage(stage)

    def _check_stage(self, stage):
        from .action_catalog import MAX_VERTICES, MAX_EDGES, MAX_FACES
        bpy = _bpy()
        ids = list(stage.owned_ids) + list(stage.context.created_ids)
        if len({block.as_pointer() for block in ids}) > MAX_STAGE_IDS:
            raise ProgramError("Native stage exceeded its owned dependency budget", code='LIMIT_EXCEEDED')
        originals = {block.as_pointer() for obj in stage.originals.values() for block in _mutable_dependencies(obj)}
        from .stage_borrowing import resident_pointers
        borrowed = resident_pointers(stage)  # Live materials in their recorded slots, which no kernel can write.
        vertices, links, checked = 0, linked_once(), set()
        for obj in stage.scene.objects:
            if obj.library or obj.mode != "OBJECT" or obj.as_pointer() in originals:
                raise ProgramError("Stage isolation or Object Mode validation failed", code='INTERNAL_ERROR')
            if obj.data is not None and obj.data.as_pointer() in originals:
                raise ProgramError("A staged object shares mutable data with the artist scene", code='INTERNAL_ERROR')
            if obj.type == "MESH":
                if len(obj.data.vertices) > MAX_VERTICES or len(obj.data.edges) > MAX_EDGES or len(obj.data.polygons) > MAX_FACES:
                    raise ProgramError("The staged result exceeds native topology limits", code='LIMIT_EXCEEDED')
                vertices += len(obj.data.vertices)
                coordinates = array("f", [0]) * (len(obj.data.vertices) * 3)
                obj.data.vertices.foreach_get("co", coordinates)
                if not all_finite(coordinates):
                    raise ProgramError("The staged result contains non-finite geometry", code='INTERNAL_ERROR')
            if not all(math.isfinite(float(value)) for row in obj.matrix_world for value in row):
                raise ProgramError("The staged result contains non-finite transforms", code='INTERNAL_ERROR')
            for block in _mutable_dependencies(obj, links, borrowed):
                if block.as_pointer() in originals:
                    raise ProgramError("A staged mutable material, action or node group still aliases original data",
                                       code='INTERNAL_ERROR')
                if block.as_pointer() in checked:
                    continue  # Data, a material or a tree linked copies share: checked with the first of them.
                checked.add(block.as_pointer())
                for _, _, dependency in links(block):
                    if isinstance(dependency, bpy.types.Object) and dependency.name not in stage.scene.objects:
                        raise ProgramError("A staged dependency points outside its isolated scene", code='INTERNAL_ERROR')
        if vertices > MAX_TOTAL_VERTICES:
            raise ProgramError("The staged program exceeds the combined geometry budget", code='LIMIT_EXCEEDED')
        from .program_fields import layer_parent_swaps
        layer_parent_swaps(stage, DATA_SWAP_RESOURCES)  # A parented layer's inverse cannot be carried live.
        from .operator_state import reindexed_dependents
        for handle in stage.binding["writes"]:
            if "kind:operator_run" in stage.changes.get(handle, ()) and handle in stage.objects:
                reason = reindexed_dependents(stage.originals[handle], stage.objects[handle])
                if reason:
                    raise ProgramError("operator_run REINDEXED_DEPENDENTS: committing the staged result would detach "
                                       "the live object's " + reason + "; reparent or remove them first, or use "
                                       "reviewed Python", code='UNSUPPORTED')


    def delta(self, stage):
        from .action_camera_light import object_settings
        added = [obj for obj in stage.scene.objects if not any(obj == value for value in stage.objects.values())]
        deleted = [handle for handle in stage.extras.get("deleted", ()) if handle in stage.objects]
        modified = [modified_row(handle, stage.logical_names[handle], resources)
                    for handle, resources in stage.changes.items() if handle in stage.objects and handle not in deleted]
        scene_row = self._scene_change(stage)
        if scene_row is not None:
            modified.append(modified_row(scene_row["handle"], scene_row["name"], stage.changes[scene_row["handle"]]))
        shown = added[:MAX_RESULT_ROWS]  # A batch can add a thousand objects: rows for the first, counts for all.
        data = {"added": [{"name": committed_name(stage.extras, obj), "type": obj.type,
                            "vertices": len(obj.data.vertices) if obj.type == "MESH" else 0} for obj in shown],
                "removed": [{"handle": handle, "name": stage.logical_names[handle], "type": stage.objects[handle].type}
                            for handle in deleted],
                "modified": modified, "object_count_before": len(self.scene.objects),
                "object_count_after": len(self.scene.objects) + len(added) - len(deleted),
                "truncated": len(added) > MAX_RESULT_ROWS,
                "scope": "Exact bound staged-object delta; unchanged live objects are not rewritten",
                "summary": f"{len(added)} object(s) added; {len(modified)} object(s) modified; {len(deleted)} object(s) "
                           "deleted. No live edits until Accept/commit."}
        geometry = []
        # An input's row carries its handle: its logical name may not be the name commit gives it (a rename).
        inputs = [(h, obj) for h, obj in stage.objects.items() if h not in deleted]
        for handle, obj in inputs + [(None, obj) for obj in shown]:
            corners = [list(obj.matrix_world @ _bpy_vector(corner)) for corner in obj.bound_box]
            bounds = [[min(point[axis] for point in corners) for axis in range(3)],
                      [max(point[axis] for point in corners) for axis in range(3)]]
            item = {"name": stage.logical_names[handle] if handle is not None else committed_name(stage.extras, obj),
                    "type": obj.type, "bounds": bounds, "matrix": [float(v) for row in obj.matrix_world for v in row]}
            if handle is not None:
                item["handle"] = handle
            if obj.type == "MESH":
                item["mesh_counts"] = {"vertices": len(obj.data.vertices), "edges": len(obj.data.edges), "faces": len(obj.data.polygons)}
            elif obj.type == "ARMATURE":
                item["bones"] = len(obj.data.bones)
            item.update(object_settings(obj))
            geometry.append(item)
        data["geometry"] = geometry
        data["geometry_scope"] = "Bounded summaries only; native staged pixels are returned in views"
        details = []
        for handle, resources in stage.changes.items():
            if handle not in stage.objects or handle in deleted:
                continue
            before, after = stage.originals[handle], stage.objects[handle]
            item = {"handle": handle, "name": stage.logical_names[handle], "changes": {}}
            for name in ("location", "rotation_euler", "scale"):
                old, new = list(getattr(before, name)), list(getattr(after, name))
                if old != new:
                    item["changes"][name] = {"before": old, "after": new}
            if before.type == "MESH" and "mesh" in resources:
                item["changes"]["mesh_counts"] = {
                    "before": [len(before.data.vertices), len(before.data.edges), len(before.data.polygons)],
                    "after": [len(after.data.vertices), len(after.data.edges), len(after.data.polygons)]}
            if "modifiers" in resources:
                item["changes"]["modifiers"] = {"before": [[m.name, m.type] for m in before.modifiers],
                                                  "after": [[m.name, m.type] for m in after.modifiers]}
            if "constraints" in resources:
                item["changes"]["constraints"] = {"before": [[c.name, c.type] for c in before.constraints],
                                                    "after": [[c.name, c.type] for c in after.constraints]}
            if "materials" in resources:
                item["changes"]["materials"] = {"before": [slot.material.name if slot.material else None for slot in before.material_slots],
                                                  "after": [kept_name(stage.extras, slot.material) if slot.material
                                                            else None for slot in after.material_slots]}
            if resources & {"armature", "pose"} and before.type == "ARMATURE":
                from .bone_state import changes
                live_names = {obj.as_pointer(): stage.logical_names[key] for key, obj in stage.objects.items()}
                item["changes"]["bones"] = changes(before, after, live_names)
            if resources & {"particles", "modifiers"} and any(m.type == "PARTICLE_SYSTEM" for m in after.modifiers):
                from .action_particles import delta as particle_rows
                item["changes"]["particles"] = particle_rows(before, after, lambda obj: next(
                    (stage.logical_names[h] for h, c in stage.objects.items() if c == obj), obj.name[:64]))
            if "node_trees" in resources:
                from .node_edit_commit import delta_rows
                item["changes"]["node_trees"] = delta_rows(before, after, lambda block: _node_tree_name(stage, handle, block))
            for family in ("camera", "light"):
                if family in resources:
                    item["changes"][family] = {"before": object_settings(before)[family], "after": object_settings(after)[family]}
            if handle in stage.extras.get("renames", {}):
                item["changes"]["name"] = {"before": stage.logical_names[handle], "after": stage.extras["renames"][handle]}
            if handle in stage.extras.get("collections", {}):
                item["changes"]["collections"] = {"before": stage.extras.get("live_memberships", {}).get(handle),
                                                  "after": stage.extras.get("memberships", {}).get(handle)}
            properties = touched_paths(resources)
            if properties:
                item["changes"]["properties"] = {path: {"before": self._live_value(before, path),
                                                         "after": self._staged_value(stage, after, path)}
                                                  for path in properties}
            if resources & DATA_SWAP_RESOURCES and before.data is not None and before.data.users > 1:
                item["changes"]["shared_data"] = {"users_before": before.data.users,
                                                  "policy": "Only this object receives the edited copy; other users keep the original data"}
            details.append(item)
        if scene_row is not None:
            changes = {"properties": {
                path: {"before": self._live_value(self.scene, path), "after": self._staged_value(stage, stage.scene, path)}
                for path in touched_paths(stage.changes[scene_row["handle"]])}}
            if "world" in stage.changes[scene_row["handle"]]:
                from .action_world import COMMITTED_NAME, intended_image, summary
                live_world = self.scene.world
                changes["world"] = {"before": summary(live_world, intended_image({"world_image_original":
                                                                                  stage.extras.get("world_image_original")})),
                                    "after": summary(stage.scene.world, intended_image(stage.extras),
                                                     stage.extras.get(COMMITTED_NAME))}
                if live_world is not None and live_world.users > 1:
                    changes["shared_data"] = {"users_before": live_world.users,
                                              "policy": "Only this scene receives the edited World; other users keep the original"}
            if "world_nodes" in stage.changes[scene_row["handle"]]:
                from .node_edit_commit import world_row
                changes["world_nodes"] = world_row(self.scene.world, stage.scene.world)
            details.append({"handle": scene_row["handle"], "name": scene_row["name"], "changes": changes})
        data["details"] = details
        if stage_names.mentions(data):  # A kept copy's private name: the name it stands for.
            data = stage_names.shown(stage.extras, data)
        if stage.binding["program"]["mode"] == "PREVIEW":
            if self.controller is not None:
                from .program_preview import capture
                pixels = capture(self.window, stage.scene, getattr(self.controller, "references", None))
                data.update(pixels)
            if any(obj.type in {"CAMERA", "LIGHT"} for obj in stage.scene.objects):
                note = ("Cameras and lights are not drawn, and scene lights do not shade this solid studio-lit preview; "
                        "review their staged settings and checks.")
                data["preview_note"] = (data.get("preview_note", "") + " " + note).strip()
        return data


def _mentions_private(value):
    if isinstance(value, dict):
        return any(_mentions_private(item) for item in value.values())
    if isinstance(value, list):
        return any(_mentions_private(item) for item in value)
    return private_name(value)


def _staged_names(stage, overrides=None):
    """How checks on the stage read an ID's name: the name commit gives it, never a staged private name.

    A declared object reads its pending rename or live name, a new object its claim, a staged material or node
    group the live one it stands for (or, for a copy commit keeps, the exact name reserved for it), anything else
    its public name. ``overrides``: ID pointer -> name, for a guarded material proven by its slot.
    """
    declared = {obj.as_pointer(): handle for handle, obj in stage.objects.items()}
    fixed = dict(overrides or {})

    def shown(block):
        pointer = block.as_pointer()
        if pointer in fixed:
            return fixed[pointer]
        if pointer in declared:
            handle = declared[pointer]
            return stage.extras.get("renames", {}).get(handle, stage.logical_names[handle])
        if block.id_type == "OBJECT":
            return committed_name(stage.extras, block)
        if block.id_type in ("MATERIAL", "NODETREE"):
            return kept_name(stage.extras, block)
        return stage_names.public(stage.extras, block.name)
    return shown


def kept_name(extras, block):
    """A staged material's or group's committed name: its live original's, or a kept copy's reserved name
    (an isolated copy, ``node_edit`` ISOLATE, commits beside its original as "<name>.001")."""
    from .node_edit_spec import MODE
    if block.name.startswith(stage_names.PREFIX) and (block.get(MODE) == "ASSIGN" or not _has_source(block)):
        return stage_names.reserve(extras, block, _taken(block))
    return _committed_material_name(block)


def _has_source(block):
    from .program_objects import MATERIAL_SOURCE
    from .node_edit_spec import EDITED
    keys = block.keys()
    return MATERIAL_SOURCE in keys or EDITED in keys


def _taken(block):
    collection = _bpy().data.node_groups if block.id_type == "NODETREE" else _bpy().data.materials
    return lambda name: collection.get((name, None)) is not None  # A local ID: the namespace commit names in.


def _shown_names(value, shown):
    """Check rows as the program knows the objects and materials: a staged copy's private name never reaches it."""
    if isinstance(value, dict):
        return {key: _shown_names(item, shown) for key, item in value.items()}
    if isinstance(value, list):
        return [_shown_names(item, shown) for item in value]
    return shown.get(value, value) if isinstance(value, str) else value


def _node_tree_name(stage, handle, block):
    from .node_edit import logical_name
    return logical_name(stage.context, handle, block)


def touched_paths(resources):
    """Allow-listed setting paths a program wrote on one owner, in allow-list order."""
    from .action_properties import ORDER
    return sorted((field[5:] for field in resources if field.startswith("prop:")), key=lambda path: ORDER.get(path, 0))


def modified_row(handle, name, resources):
    """One modified-owner row: changed families, plus the written setting paths if any."""
    row = {"handle": handle, "name": name,
           "fields": sorted(field for field in resources if not field.startswith(("kind:", "prop:")))}
    properties = touched_paths(resources)
    if properties:
        row["properties"] = properties
    return row


def failure_text(kind, report):
    """A failed check's kind with its reason or what it observed, so a corrected plan need not guess."""
    item = report["checks"][0] if report.get("checks") else {}
    if item.get("passed"):
        return kind
    if "reason" in item:
        return kind + ": " + item["reason"]
    observed = json.dumps(item.get("observed"), separators=(",", ":"), default=str)
    return kind + " (observed " + (observed if len(observed) <= 400 else observed[:400] + "...") + ")"


def _committed_material_name(material, source=False):
    """The name a staged slot's material (or a modifier's node group) will have live: a clone, or a
    copy edited in place for every user (node_edit REMAP), commits as its original. ``source``: the
    live ID any staged copy stands for (an isolated node_edit copy too), by the session UID it carries."""
    from .program_objects import MATERIAL_SOURCE as _MATERIAL_SOURCE
    from .node_edit_spec import EDITED, MODE, is_edited
    if material is None:
        return None
    marker = (_MATERIAL_SOURCE if _MATERIAL_SOURCE in material.keys() else
              EDITED if is_edited(material) and (source or material.get(MODE) == "REMAP") else None)
    if marker is not None:
        import bpy
        uid = int(material[marker])
        collection = bpy.data.node_groups if material.id_type == "NODETREE" else bpy.data.materials  # A group too.
        original = next((item for item in collection if int(item.session_uid) == uid), None)
        if original is not None:
            return original.name
    return stage_names.public({}, material.name)  # A kept private copy: the name it takes at commit.
