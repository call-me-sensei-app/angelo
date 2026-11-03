# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The preflight's typed fixes: each changes one finding's subject the way its rule's fix says. Main thread, Object
Mode.

``apply(chosen, facts, target, fmt, options)`` runs each chosen ``(fix id, subject)`` and returns what each changed
(``{"fix", "subject", "changed": [{"what", "before", "after"}]}``), the ones that failed and whether anything may have
changed. The caller (``engine_check_host``) has re-run the check on the current scene, so every subject still has its
finding, and pushes the one Undo step that undoes all of them. Subjects are local data blocks by name (a linked
block of the same name is never the one changed). A fix that refuses raises ``FixRefused`` before it changes
anything; one that fails part way is reported as such, and the Undo step still covers it. Renames run last, so a
fix that names an object or action finds it under its checked name. doc/angelo/engine_preflight.rst ("Fixes") lists
what each does and when it is offered.
"""

import bpy

from . import engine_rules
from .engine_facts import deform_armature, geometry_keys

RENAMES = ("rename_armature", "loop_name")   # Run after every other fix: they change the names the others use.


class FixRefused(RuntimeError):
    """The fix doesn't apply to this subject now; nothing was changed."""


def _local(collection, name, what):
    block = collection.get((name, None))   # Local only: a linked block may share the name.
    if block is None or block.override_library is not None:
        raise FixRefused("%s %s is no longer an editable local data block" % (what, name))
    return block


def _object(name):
    return _local(bpy.data.objects, name, "Object")


def _round(values):
    return [round(value, 4) for value in values]


def _apply(objects, active, **flags):
    with bpy.context.temp_override(selected_editable_objects=objects, selected_objects=objects, active_object=active,
                                   object=active):
        result = bpy.ops.object.transform_apply(location=False, properties=False, **flags)
    if "FINISHED" not in result:   # Possibly after an earlier object of the same fix: not a clean refusal.
        raise RuntimeError("Blender couldn't apply the transform of %s" % active.name)


def apply_transform(subject, facts, context):
    """Rotation and scale of an armature applied (Object > Apply; its children keep their place), then each mesh
    child's own, so the meshes it deforms keep their world shape."""
    arm = _object(subject)
    children = [child for child in arm.children if child.type == "MESH"]
    before = {obj.name: (_round(obj.rotation_euler), _round(obj.scale)) for obj in [arm] + children}
    _apply([arm], arm, rotation=True, scale=True)
    for child in children:
        bpy.context.view_layer.update()
        _apply([child], child, rotation=True, scale=True)
    return [{"what": "%s rotation and scale" % obj.name, "before": list(before[obj.name]),
             "after": [_round(obj.rotation_euler), _round(obj.scale)]} for obj in [arm] + children]


def apply_scale(subject, facts, context):
    obj = _object(subject)
    before = _round(obj.scale)
    _apply([obj], obj, rotation=False, scale=True)
    return [{"what": "%s scale" % subject, "before": before, "after": _round(obj.scale)}]


def rename_armature(subject, facts, context):
    obj = _object(subject)
    taken = bpy.data.objects.get(("Armature", None))
    if taken is not None and taken != obj:
        raise FixRefused("Another object is already named Armature")
    obj.name = "Armature"
    return [{"what": "armature object name", "before": subject, "after": obj.name}]


def stash_action(subject, facts, context):
    """The action kept in a new muted NLA track, as Blender's Stash does, so the save and the export keep it."""
    action = _local(bpy.data.actions, subject, "Action")
    owner = next((item["owners"][0] for item in facts["actions"] if item["name"] == subject and item["owners"]), None)
    if owner is None or action.users:
        raise FixRefused("Action %s is no longer unused" % subject)
    obj = _object(owner)
    animation = obj.animation_data or obj.animation_data_create()
    track = animation.nla_tracks.new()
    track.name = "[Action Stash]"
    strip = track.strips.new(action.name, int(action.frame_range[0]), action)
    slot = next((slot for slot in getattr(action, "slots", ()) if slot.identifier == "OB" + obj.name), None)
    if slot is not None and hasattr(strip, "action_slot"):
        strip.action_slot = slot
    track.mute = True
    return [{"what": "%s NLA tracks" % obj.name, "before": None, "after": "%s (muted, %s)" % (track.name, action.name)}]


def loop_name(subject, facts, context):
    action = _local(bpy.data.actions, subject, "Action")
    wanted = subject + "-loop"
    action.name = wanted
    if action.name != wanted:   # Too long for a name, or taken: Blender changed it, so Godot wouldn't read it.
        action.name = subject
        raise FixRefused("%s can't take the -loop suffix (the name is too long or taken)" % subject)
    return [{"what": "action name", "before": subject, "after": action.name}]


def _flagged_modifiers(subject, facts, options):
    """The shading modifiers of ``subject`` the smooth_modifier rule flagged: those the export won't apply."""
    applies = options.get("export_apply", options.get("use_mesh_modifiers", True))
    described = next((item for item in facts["objects"] if item["name"] == subject), None)
    return [modifier["name"] for modifier in (described or {}).get("modifiers", ())
            if modifier["smooth"] and modifier["apply_safe"] and (not applies or not modifier["viewport"])]


def apply_modifier(subject, facts, context):
    obj = _object(subject)
    names = _flagged_modifiers(subject, facts, context["options"])
    if not names or obj.data.users != 1 or obj.data.shape_keys is not None:
        raise FixRefused("%s's shading modifier can't be applied (shared mesh or shape keys)" % subject)
    for name in names:
        if name not in obj.modifiers:
            raise FixRefused("%s no longer has the modifier %s" % (subject, name))
    applied = []
    for name in names:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            if "FINISHED" not in bpy.ops.object.modifier_apply(modifier=name):
                raise RuntimeError("Blender couldn't apply %s on %s (applied before it: %s)" % (
                    name, subject, ", ".join(applied) or "none"))
        applied.append(name)
    return [{"what": "%s modifiers" % subject, "before": names, "after": [m.name for m in obj.modifiers]}]


def link_data(subject, facts, context):
    """Every object with ``subject``'s identical geometry and materials shares ``subject``'s mesh (Link Object Data).
    The group is computed again from the objects as they are now (``engine_facts.geometry_keys``), so only a twin
    whose mesh and resolved materials still match is linked; the check's key alone never decides it."""
    keys = {item["name"]: item["geometry_key"] for item in facts["objects"]}
    key = keys.get(subject)
    source = _object(subject)
    if not key:
        raise FixRefused("%s no longer has a twin mesh" % subject)
    group = [source] + [_object(name) for name, other in sorted(keys.items()) if other == key and name != subject]
    now = geometry_keys(group)
    if now.get(subject) is None:
        raise FixRefused("%s no longer has a twin mesh" % subject)
    twins = [obj for obj in group[1:] if now.get(obj.name) == now[subject]]
    if not twins:
        raise FixRefused("No object still matches %s's mesh and materials" % subject)
    changed = []
    for obj in twins:
        if obj.data == source.data:
            continue
        before = obj.data.name
        obj.data = source.data
        changed.append({"what": "%s mesh" % obj.name, "before": before, "after": source.data.name})
    return changed


def _collision_mesh(kind, size):
    import bmesh
    mesh = bpy.data.meshes.new("collision")
    built = bmesh.new()
    try:
        if kind == "CUBE":
            bmesh.ops.create_cube(built, size=2.0 * size)
        else:
            bmesh.ops.create_uvsphere(built, u_segments=16, v_segments=8, radius=size)
        built.to_mesh(mesh)
    finally:
        built.free()
    return mesh


def collision_mesh(subject, facts, context):
    """The collision empty replaced by a box or sphere mesh of its displayed size. Everything that pointed at the
    empty points at the mesh (``ID.user_remap``: its collections, children, constraint and modifier targets); the
    mesh takes its name, parent, transform and visibility. Offered only for an empty with nothing the replacement
    couldn't keep (``engine_facts._replace_safe``)."""
    empty = _object(subject)
    if empty.type != "EMPTY" or empty.empty_display_type not in {"CUBE", "SPHERE"}:
        raise FixRefused("%s is no longer a box or sphere empty" % subject)
    if empty.animation_data is not None or empty.constraints or empty.parent_type != "OBJECT" or empty.keys() or any(
            collection.library is not None or collection.override_library is not None
            for collection in empty.users_collection):
        raise FixRefused("%s has animation, constraints, a bone parent, custom properties or linked collections"
                         % subject)
    shape = empty.empty_display_type
    obj = bpy.data.objects.new(subject + ".collision", _collision_mesh(shape, empty.empty_display_size))
    worlds = {child: child.matrix_world.copy() for child in empty.children}
    obj.parent = empty.parent
    obj.matrix_parent_inverse = empty.matrix_parent_inverse.copy()
    obj.matrix_basis = empty.matrix_basis.copy()
    obj.hide_render, obj.hide_viewport, obj.hide_select = empty.hide_render, empty.hide_viewport, empty.hide_select
    obj.display_type = "WIRE"
    hidden = empty.hide_get()
    empty.user_remap(obj)   # Collections, children, constraint and modifier targets now name the mesh.
    obj.hide_set(hidden)
    bpy.context.view_layer.update()
    for child, world in worlds.items():
        child.matrix_world = world   # Children keep their place under the mesh.
    bpy.data.objects.remove(empty, do_unlink=True)
    obj.name = subject
    obj.data.name = subject
    return [{"what": subject, "before": "empty (%s)" % shape.lower(), "after": "mesh (%s, wire display)" % (
        "box" if shape == "CUBE" else "sphere")}]


def hide_collider(subject, facts, context):
    obj = _object(subject)
    obj.hide_set(True)
    return [{"what": "%s in the viewport" % subject, "before": "shown", "after": "hidden"}]


def limit_influences(subject, facts, context):
    """At most the engine's influences per vertex from the armature that deforms it (the largest kept), normalized to
    1 over those: what the engine keeps, visible in Blender. Other vertex groups are not touched."""
    cap = context["cap"]
    obj = _object(subject)
    armature = deform_armature(obj)
    if obj.type != "MESH" or obj.data.users != 1 or armature is None:
        raise FixRefused("%s's mesh is shared or no longer skinned" % subject)
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    groups = {group.index: group for group in obj.vertex_groups if group.name in deform}
    if any(group.lock_weight for group in groups.values()):
        raise FixRefused("%s has locked weights" % subject)
    changed = 0
    for vertex in obj.data.vertices:
        weights = sorted(((item.weight, item.group) for item in vertex.groups if item.group in groups
                          and item.weight > 0.0), reverse=True)
        if len(weights) <= cap:
            continue
        kept, dropped = weights[:cap], weights[cap:]
        total = sum(weight for weight, _ in kept) or 1.0
        for _, index in dropped:
            groups[index].remove([vertex.index])
        for weight, index in kept:
            groups[index].add([vertex.index], weight / total, "REPLACE")
        changed += 1
    return [{"what": "%s bone influences per vertex" % subject,
             "before": "more than %d on %d vertices" % (cap, changed), "after": "at most %d, normalized" % cap}]


def colour_space(subject, facts, context):
    image = _local(bpy.data.images, subject, "Image")
    described = next((item for item in facts["images"] if item["name"] == subject), None)
    if described is None or len(described["roles"]) != 1:
        raise FixRefused("Image %s is no longer used one way" % subject)
    wanted = "sRGB" if described["roles"][0] == "colour" else "Non-Color"
    before = image.colorspace_settings.name
    image.colorspace_settings.name = wanted
    return [{"what": "%s colour space" % subject, "before": before, "after": wanted}]


FIX_FUNCTIONS = {"apply_transform": apply_transform, "apply_scale": apply_scale, "rename_armature": rename_armature,
                 "stash_action": stash_action, "loop_name": loop_name, "apply_modifier": apply_modifier,
                 "link_data": link_data, "collision_mesh": collision_mesh, "hide_collider": hide_collider,
                 "limit_influences": limit_influences, "colour_space": colour_space}
if set(FIX_FUNCTIONS) != set(engine_rules.FIXES.values()):
    raise RuntimeError("Every rule's fix needs its function, and only those")


def apply(chosen, facts, target, fmt, options):
    """Run each ``(fix id, subject)``, renames last; returns (applied, failed, touched): failed is [(fix, subject,
    English reason)], ``touched`` whether a failure may have changed something (it wasn't a refusal)."""
    context = {"cap": engine_rules.influence_cap(target, fmt, options), "options": options}
    ordered = sorted(dict.fromkeys(chosen), key=lambda item: item[0] in RENAMES)
    applied, failed, touched = [], [], False
    for fix, subject in ordered:
        try:
            changed = FIX_FUNCTIONS[fix](subject, facts, context)
        except FixRefused as refusal:
            failed.append((fix, subject, str(refusal)))
            continue
        except Exception as error:   # noqa: BLE001 - reported with what it may have changed; Undo covers it.
            touched = True
            reason = (str(error).strip().splitlines() or [type(error).__name__])[-1][:200]
            failed.append((fix, subject, "failed part way (Undo restores it): %s" % reason))
            continue
        applied.append({"fix": fix, "subject": subject, "changed": changed})
    return applied, failed, touched
