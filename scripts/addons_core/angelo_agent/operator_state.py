# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""State ``operator_run`` can change beyond the swapped data: digests, revisions and commit.

* ``data_digest``: the kernel's "did the operator change anything" proof. It
  hashes the object's data (mesh attributes and offsets by bulk reads, other
  data by its complete RNA), its shape keys and the object-level state below.
* ``revision_state``: what the observed ``object_revision`` did not cover and
  an allow-listed operator can change (active material and vertex-group
  indexes, shape-key locks and timing, lattice point selection and weights,
  bone collections and head/tail selection, mesh skin radii and marks), so a
  stale plan cannot overwrite a manual edit of them. Plain values with
  ``getattr`` defaults: observation stand-ins lack most RNA.
* ``commit_object_state``: copies the object-level part (and the parent
  inverse, which origin and parent-inverse operators change) to the live
  object after commit swapped its data, journaling each previous value.
* ``sync_constraints``: the ``constraints`` family of constraint operators:
  the live stack takes the staged one by name (removed, added, configured,
  ordered), with the whole previous stack journaled for ``restore_constraints``.
* ``pose_extras``: per bone, what the commit's ``pose`` family cannot carry
  (constraints, custom properties, the custom shape transform). Armature
  operators that copy pose channels (duplicate, symmetrize) or clear targets
  of removed bones change it on the stage only, so they are refused then.
* ``reindexed_dependents``: live children and Hooks that committing staged
  data with a new element order (or without a parent bone) would detach.
"""

from array import array
import hashlib
import json

# Object settings operators change besides the data (shape-key and material slot operators set them).
POSE_CHANNELS = ("location", "rotation_quaternion", "rotation_euler", "rotation_axis_angle", "scale")
OBJECT_INDEXES = ("active_material_index", "active_shape_key_index", "show_only_shape_key", "use_shape_key_edit_mode")
_ATTRIBUTE_LAYOUT = {"FLOAT": ("value", "f", 1), "INT": ("value", "i", 1), "INT8": ("value", "i", 1),
                     "BOOLEAN": ("value", "b", 1), "FLOAT_VECTOR": ("vector", "f", 3), "FLOAT2": ("vector", "f", 2),
                     "FLOAT_COLOR": ("color", "f", 4), "BYTE_COLOR": ("color", "f", 4), "QUATERNION": ("value", "f", 4),
                     "INT32_2D": ("value", "i", 2), "INT16_2D": ("value", "i", 2), "FLOAT4X4": ("value", "f", 16)}


def _read(collection, prop, code, width):
    values = array(code, [0]) * (len(collection) * width)
    if len(collection):
        collection.foreach_get(prop, values)
    return values


def _uid(value):
    return int(value.session_uid) if value is not None and hasattr(value, "session_uid") else None


def object_state(obj):
    """Object-level settings, slots, transforms and stacks an allow-listed operator may change (plain values)."""
    from .program_fields import transform_values
    groups = getattr(obj, "vertex_groups", None)
    state = {name: getattr(obj, name, None) for name in OBJECT_INDEXES}
    if hasattr(obj, "matrix_parent_inverse"):  # Observation stand-ins have no transforms.
        state["transforms"] = transform_values(obj)
        state["parent_inverse"] = [list(row) for row in obj.matrix_parent_inverse]
        state["modifiers"] = [[item.name, item.type] for item in obj.modifiers]
        state["constraints"] = [_constraint_values(item) for item in obj.constraints]
    state["slots"] = [[slot.link, _uid(slot.material)] for slot in getattr(obj, "material_slots", ())]
    state["groups"] = [[group.name, bool(group.lock_weight)] for group in groups or ()]
    state["active_group"] = getattr(groups, "active_index", None)
    field = getattr(obj, "field", None)
    if field is not None and getattr(field, "type", "NONE") != "NONE":
        from .generic_settings import values
        state["field"] = values(field, lambda value: _uid(value))
    return state


def _constraint_values(constraint):
    from .program_dependencies import _rna_values, fingerprint_budget
    return [constraint.name, constraint.type, _rna_values(constraint, budget=fingerprint_budget())]


def skin_state(mesh):
    """Skin radii and root/loose marks (a CustomData layer, not an attribute), or None without one."""
    layers = getattr(mesh, "skin_vertices", None)
    if not layers:
        return None
    data = layers[0].data
    return [_read(data, "radius", "f", 2).tobytes().hex(), _read(data, "use_root", "b", 1).tobytes().hex(),
            _read(data, "use_loose", "b", 1).tobytes().hex()]


def topology(mesh):
    """Counts and connectivity of a mesh (no coordinates): a change starts a new topology step."""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(repr((len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))).encode())
    digest.update(_read(mesh.edges, "vertices", "i", 2))
    digest.update(_read(mesh.loops, "vertex_index", "i", 1))
    digest.update(_read(mesh.polygons, "loop_start", "i", 1))
    return digest.hexdigest()


def _mesh(mesh, digest):
    digest.update(topology(mesh).encode())
    for attribute in mesh.attributes:
        layout = _ATTRIBUTE_LAYOUT.get(attribute.data_type)
        digest.update(json.dumps([attribute.name, attribute.domain, attribute.data_type, len(attribute.data)]).encode())
        if layout is not None:
            digest.update(_read(attribute.data, *layout))
    weights = getattr(mesh, "angelo_vertex_group_weights", None)
    if isinstance(weights, bytes):
        digest.update(weights)  # Angelo's native bulk (vertex, group, weight) records.
    else:
        for vertex in mesh.vertices:
            digest.update(json.dumps([[group.group, group.weight] for group in vertex.groups]).encode())
    digest.update(json.dumps([_uid(material) for material in mesh.materials]).encode())
    digest.update(json.dumps([mesh.uv_layers.active_index, mesh.attributes.active_index,
                              getattr(mesh.attributes, "active_color_name", None),
                              getattr(mesh.attributes, "default_color_name", None), skin_state(mesh)]).encode())


def _shape_keys(key, digest):
    if key is None:
        return
    digest.update(json.dumps([key.use_relative, key.eval_time, key.reference_key.name if key.reference_key else None])
                  .encode())
    for block in key.key_blocks:
        digest.update(json.dumps([block.name, block.value, block.mute, block.lock_shape, block.slider_min,
                                  block.slider_max, block.vertex_group, block.interpolation, block.frame,
                                  block.relative_key.name if block.relative_key else None]).encode())
        digest.update(_read(block.data, "co", "f", 3))


def data_digest(obj):
    """Exact digest of the object's data and object-level state, read fresh in Object mode."""
    from .program_dependencies import _rna_values, fingerprint_budget
    digest = hashlib.blake2b(digest_size=32)
    digest.update(json.dumps(object_state(obj), sort_keys=True, default=str).encode())
    data = obj.data
    if data is None:
        return digest.hexdigest()
    if obj.type == "MESH":
        _mesh(data, digest)
    else:
        digest.update(json.dumps(_rna_values(data, budget=fingerprint_budget()), sort_keys=True, default=str).encode())
    _shape_keys(getattr(data, "shape_keys", None), digest)
    if getattr(obj, "pose", None) is not None:  # Pose channels (selection is not committed, so not compared).
        digest.update(json.dumps([[bone.name, bone.rotation_mode] + [list(getattr(bone, name)) for name in POSE_CHANNELS]
                                  for bone in obj.pose.bones]).encode())
    return digest.hexdigest()


def revision_state(obj, work=None):
    """Operator-changeable state the rest of the object revision does not read (plain, bounded by its owner).

    ``work`` (the observation budget) is charged one item per lattice point or bone read.
    """
    groups = getattr(obj, "vertex_groups", None)
    state = [getattr(obj, "active_material_index", None), getattr(groups, "active_index", None)]
    data, kind = getattr(obj, "data", None), getattr(obj, "type", None)
    if kind == "MESH" and getattr(data, "skin_vertices", None):
        if work is not None:
            work.take(items=len(data.vertices) // 64 + 1)  # Three bulk reads of one value per vertex.
        state.append(skin_state(data))
    key = getattr(data, "shape_keys", None) if kind == "MESH" else None
    if key is not None:
        state.append([key.use_relative, key.eval_time] + [[block.lock_shape, block.frame] for block in key.key_blocks])
    if kind == "LATTICE" and getattr(data, "points", None) is not None and len(data.points) <= 4096:
        if work is not None:
            work.take(items=len(data.points))
        state.append([[point.select, [[group.group, group.weight] for group in point.groups]] for point in data.points])
    if kind == "ARMATURE" and getattr(data, "bones", None) is not None:
        bones = data.edit_bones if getattr(obj, "mode", "OBJECT") == "EDIT" else data.bones
        if work is not None:
            work.take(items=len(bones))
        state.append([[bone.name, [collection.name for collection in getattr(bone, "collections", ())],
                       bool(getattr(bone, "select_head", False)), bool(getattr(bone, "select_tail", False))]
                      for bone in bones])
        state.append([[collection.name, collection.is_visible, collection.parent.name if collection.parent else None]
                      for collection in getattr(data, "collections_all", ())])
        active = getattr(bones, "active", None)
        state.append(active.name if active is not None else None)
    return state


def commit_object_state(original, staged, journal, error_type):
    """Give the live object the staged object-level state, journaling (holder, name, previous) first.

    Runs after commit swapped the data, so slot and shape-key counts already
    match. Force-field settings are copied setting by setting; the live object
    must already have them (staging refuses to create them).
    """
    for name in OBJECT_INDEXES:
        value = getattr(staged, name)
        if getattr(original, name) != value:
            journal.append((original, name, getattr(original, name)))
            setattr(original, name, value)
            if getattr(original, name) != value:
                raise error_type("A committed operator setting did not retain its staged value: " + name)
    inverse = getattr(staged, "matrix_parent_inverse", None)  # Stand-ins in host tests have none.
    if inverse is not None and original.matrix_parent_inverse != inverse:  # Origin clears, parent-inverse applies.
        journal.append((original, "matrix_parent_inverse", original.matrix_parent_inverse.copy()))
        original.matrix_parent_inverse = staged.matrix_parent_inverse.copy()
    staged_field, live_field = getattr(staged, "field", None), getattr(original, "field", None)
    if staged_field is None:
        return
    if live_field is None:
        raise error_type("The live object has no force-field settings to receive the staged ones")
    from .rna_rules import resolve
    wanted, current = _field_values(staged_field), _field_values(live_field)
    # The field type first: it decides which other settings are available.
    for path in sorted((path for path in wanted if wanted[path] != current.get(path)), key=lambda path: path != "type"):
        holder, prop = resolve(live_field, path, owner_kind="STRUCT")
        source, _ = resolve(staged_field, path, owner_kind="STRUCT")
        journal.append((holder, prop.identifier, getattr(holder, prop.identifier)))
        setattr(holder, prop.identifier, getattr(source, prop.identifier))
    if _field_values(live_field) != wanted:
        raise error_type("Committed force-field settings do not match the staged ones")


def _field_values(field):
    """Writable force-field values except object pointers, which forcefield_toggle never sets."""
    from .generic_settings import values
    return {path: value for path, value in values(field, _uid).items() if not isinstance(value, dict)}


def _property(value):
    for method in ("to_dict", "to_list"):
        if hasattr(value, method):
            return getattr(value, method)()
    return value


def _extras_digest(constraints, properties, transform):
    value = json.dumps([constraints, properties, transform], sort_keys=True, default=str)
    return hashlib.blake2b(value.encode(), digest_size=16).hexdigest()


def pose_extras(obj):
    """Bone name -> digest of its constraints, custom properties and custom shape transform (staged pose)."""
    from .program_dependencies import _rna_values, fingerprint_budget
    budget = fingerprint_budget()
    return {bone.name: _extras_digest([_rna_values(constraint, budget=budget) for constraint in bone.constraints],
                                      {key: _property(bone[key]) for key in bone.keys()},
                                      bone.custom_shape_transform.name if bone.custom_shape_transform else None)
            for bone in obj.pose.bones}


def untransferred_pose_changes(before, after):
    """Bones whose pose extras differ from what the live pose keeps at commit: its own for an existing
    bone, none for a new one (a removed bone is not compared)."""
    empty = _extras_digest([], {}, None)
    return sorted(name for name, value in after.items() if value != before.get(name, empty))


def _layout(data):
    if hasattr(data, "polygons"):
        return topology(data)
    if hasattr(data, "splines"):
        return [[len(spline.bezier_points), len(spline.points)] for spline in data.splines]
    return len(data.points)


def reindexed_dependents(original, staged):
    """Why committing ``staged``'s data to the live ``original`` would detach live dependents, or None.

    Leaving Edit mode remaps vertex parents and Hook indices only for objects
    using the edited (staged) data (bmesh_to_mesh_calc_object_remap, curve
    hooks); the live children and the live object's own Hooks keep their old
    indices, and bone-parented children keep the bone's name. Conservative: any
    change of element layout counts as a new order.
    """
    if original.type == "ARMATURE":
        bones = {bone.name for bone in staged.data.bones}
        lost = sorted(child.name for child in original.children if child.parent_type == "BONE"
                      and child.parent_bone in original.data.bones and child.parent_bone not in bones)
        return "bone-parented children of a removed bone (%s)" % ", ".join(lost[:4]) if lost else None
    if original.type not in {"MESH", "CURVE", "LATTICE"} or _layout(original.data) == _layout(staged.data):
        return None
    if any(child.parent_type in {"VERTEX", "VERTEX_3"} for child in original.children):
        return "vertex-parented children"
    if any(modifier.type == "HOOK" and len(getattr(modifier, "vertex_indices", (0,))) for modifier in original.modifiers):
        return "Hook modifiers bound to element indices"
    return None


def _constraint_records(obj):
    from .program_transfers import _settings
    return [{"name": item.name, "type": item.type, "settings": _settings(item)} for item in obj.constraints]


def sync_constraints(original, staged, mapping, journal, apply_settings, error_type):
    """Give the live object the staged constraint stack by name: removed, added, configured and ordered.

    The whole previous stack is journaled first (``restore_constraints``
    rebuilds it on rollback). Settings go through the commit's own setter, so
    staged object pointers map to their live objects and each value is read
    back. A constraint whose name now has another type is replaced.
    """
    from .program_transfers import _settings
    journal.append(("constraints", original, _constraint_records(original)))
    wanted = [(item.name, item.type) for item in staged.constraints]
    for item in [item for item in original.constraints if (item.name, item.type) not in wanted]:
        original.constraints.remove(item)
    for source in staged.constraints:
        target = original.constraints.get(source.name)
        if target is None:
            target = original.constraints.new(source.type)
            target.name = source.name
            if target.name != source.name:
                raise error_type("A committed constraint did not keep its staged name")
        apply_settings(target, _settings(source), mapping)
    for index, (name, _kind) in enumerate(wanted):
        current = [item.name for item in original.constraints].index(name)
        if current != index:
            original.constraints.move(current, index)
    if [(item.name, item.type) for item in original.constraints] != wanted:
        raise error_type("The committed constraint stack does not match its validated staged stack")


def restore_constraints(obj, records):
    """Rebuild a journaled constraint stack from its RNA settings (commit rollback).

    Settings hold no collection, such as an Armature constraint's targets, so the kernel refuses constraint
    operators on a stack with one (``CONSTRAINT_TARGETS``) and none reaches this rebuild.
    """
    from .program_transfers import _set_settings
    for item in list(obj.constraints):
        obj.constraints.remove(item)
    for record in records:
        item = obj.constraints.new(record["type"])
        item.name = record["name"]
        _set_settings(item, record["settings"], {})
