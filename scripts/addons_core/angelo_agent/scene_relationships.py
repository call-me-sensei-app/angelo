# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed dependency edges and bound-scene incoming native object users."""

from itertools import islice
import bpy
from .scene_read_fields import MODIFIER_FIELDS
from .scene_read_values import BudgetExceeded, dump, label, session_uid


class RelationshipReader:
    """Bounded semantic edges/native users; never stores IDs or cache state."""
    def __init__(self, object_ref, resource, object_handle, max_list):
        self.object_ref = object_ref
        self.resource = resource
        self.object_handle = object_handle
        self.max_list = max_list

    def edges(self, obj, work):
        source = self.object_ref(obj)
        result = []

        def add(kind, target, owner=None):
            if target is None:
                return
            work.take(items=1)
            resource = self.object_ref(target) if isinstance(target, bpy.types.Object) else self.resource(target)
            result.append({"kind": kind, "from": source, "to": resource, "owner": owner})

        add("PARENT", obj.parent)
        add("DATA", obj.data)
        if obj.type == "CAMERA":
            add("DATA", obj.data.dof.focus_object, {"field": "dof.focus_object"})
        add("INSTANCE_COLLECTION", obj.instance_collection)
        for slot in obj.material_slots:
            add("MATERIAL", slot.material)
        for modifier in obj.modifiers:
            for field in MODIFIER_FIELDS.get(modifier.type, ("object", "target")):
                value = getattr(modifier, field, None)
                if hasattr(value, "session_uid"):
                    add("RIG" if modifier.type == "ARMATURE" else "MODIFIER", value, {"name": label(modifier.name), "type": modifier.type, "field": field})
        for constraint in obj.constraints:
            for field in ("target", "pole_target", "space_object"):
                add("CONSTRAINT", getattr(constraint, field, None), {"name": label(constraint.name), "type": constraint.type, "field": field})
            for target in getattr(constraint, "targets", ()):
                add("CONSTRAINT", getattr(target, "target", None), {"name": label(constraint.name), "type": constraint.type})
        if obj.pose:
            for bone in obj.pose.bones:
                for constraint in bone.constraints:
                    for field in ("target", "pole_target", "space_object"):
                        add("CONSTRAINT", getattr(constraint, field, None), {"bone": label(bone.name), "name": label(constraint.name), "type": constraint.type, "field": field})
        animation = obj.animation_data
        if animation:
            add("ANIMATION", animation.action)
            for track in animation.nla_tracks:
                for strip in track.strips:
                    add("ANIMATION", strip.action, {"track": label(track.name), "strip": label(strip.name)})
        return result

    def read(self, window, obj, direction, kinds, work, scan_limit):
        result = []
        complete = True
        try:
            if direction in {"DEPENDENCIES", "BOTH"} and any(m.type not in MODIFIER_FIELDS for m in obj.modifiers):
                # Native incoming ID users are broad, but the outgoing field reader
                # remains deliberately closed. Do not claim complete RNA coverage.
                complete = False
            if direction in {"DEPENDENCIES", "BOTH"}:
                result.extend(dict(edge, direction="DEPENDENCY") for edge in self.edges(obj, work))
            if direction in {"USES", "BOTH"}:
                materials = {session_uid(s.material): s.material for s in obj.material_slots if s.material}
                for other in islice(window.scene.objects, scan_limit):
                    work.take(items=1)
                    if other == obj:
                        continue
                    for edge in self.edges(other, work):
                        if edge["to"].get("object") == self.object_handle(obj):
                            result.append(dict(edge, direction="USE"))
                    if obj.data and other.data == obj.data:
                        result.append({"kind": "SHARED_DATA", "direction": "USE", "from": self.object_ref(other),
                                       "to": self.object_ref(obj), "resource": self.resource(obj.data)})
                    shared = {session_uid(s.material): s.material for s in other.material_slots if s.material and session_uid(s.material) in materials}
                    for material in shared.values():
                        result.append({"kind": "SHARED_MATERIAL", "direction": "USE", "from": self.object_ref(other),
                                       "to": self.object_ref(obj), "resource": self.resource(material, "MATERIAL")})
                complete = complete and len(window.scene.objects) <= scan_limit
                # Native ID uses find targets of unsupported/less common modifiers
                # without traversing arbitrary RNA or exposing custom property data.
                # Only Object users are admitted and only bound-scene users emitted.
                native = getattr(bpy.data, "user_map", None)
                if native and len(bpy.data.objects) <= self.max_list:
                    cost = sum(1 + len(o.modifiers) + len(o.constraints) + len(o.material_slots)
                               + (len(o.pose.bones) if o.pose else 0)
                               + (len(o.animation_data.drivers) if o.animation_data else 0) for o in bpy.data.objects)
                    work.take(items=cost)
                    users = native(subset=[obj], value_types={"OBJECT"}).get(obj, ())
                    work.take()
                    known = {e["from"].get("object") for e in result if e.get("direction") == "USE" and e["kind"] not in {"SHARED_DATA", "SHARED_MATERIAL"} and e["to"].get("object") == self.object_handle(obj)}
                    in_scene = {session_uid(o) for o in islice(window.scene.objects, scan_limit)}
                    for user in users:
                        work.take(items=1)
                        if session_uid(user) in in_scene and user != obj and self.object_handle(user) not in known:
                            result.append({"kind": "ID_USER", "direction": "USE", "from": self.object_ref(user), "to": self.object_ref(obj)})
                elif native:
                    complete = False
        except BudgetExceeded:
            complete = False
        unique = {dump(edge): edge for edge in result if kinds is None or edge["kind"] in kinds}
        return [unique[k] for k in sorted(unique)], complete
