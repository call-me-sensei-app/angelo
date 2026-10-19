# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Staged ``object_join`` and ``mesh_separate`` kernels (meshes only).

Join appends each source's base mesh, in the target's local space, to the
target's private mesh copy, mapping material slots by material; the sources
are then deleted at commit through ``object_delete``'s journaled path (the
staged copies stay in the stage, marked ``deleted``). Separate splits the
target's private mesh into parts (loose parts, material groups or the selected
faces): the target keeps the first part and each other part becomes a new
staged object, a private copy of the target holding only that part, which
commit links into the collections the target had when it was separated
(``program_names.NAMED``, program order). Source modifiers are not applied
(as Blender's Join); vertex groups, shape keys and object-linked materials are
refused rather than merged incorrectly.
"""

import bmesh
import bpy

from .refusal_codes import RefusalError
from .action_context import _mesh, _readback, _face_selection, _check_counts
from .action_schema_types import MAX_VERTICES
from .program_names import NAMED, committed_name, current_name, rename_new_object

MAX_PARTS = 64
# Separate copies the mesh once per part: bound the total copied vertices.
MAX_SEPARATE_WORK = 2000000


def _plain_mesh(context, obj, role):
    mesh = _mesh(context, obj, topology=True)
    if len(obj.vertex_groups):
        raise RefusalError('%s VERTEX_GROUPS: merging vertex groups is reviewed Python' % role, code='UNSUPPORTED')
    if any(slot.link == 'OBJECT' for slot in obj.material_slots):
        raise RefusalError('%s OBJECT_MATERIALS: object-linked material slots are reviewed Python' % role, code='UNSUPPORTED')
    if len(getattr(obj, 'particle_systems', ())):
        raise RefusalError('%s PARTICLE_SYSTEMS: merging or splitting particle systems is reviewed Python' % role, code='UNSUPPORTED')
    return mesh


def _material_key(material):
    """Staged materials are private clones: compare the live material they copy."""
    from .program_objects import MATERIAL_SOURCE
    if material is not None and MATERIAL_SOURCE in material.keys():
        return ('live', int(material[MATERIAL_SOURCE]))
    return ('id', None if material is None else material.as_pointer())


def join(context, obj, operation):
    from .action_object_management import require_deletable
    target_mesh = _plain_mesh(context, obj, 'object_join')
    references = operation['parameters']['sources']
    if any(reference.startswith('$') for reference in references):
        raise RefusalError('object_join joins existing objects; build new geometry into the target instead', code='VALIDATION')
    sources = [context.resolve(reference) for reference in references]
    if any(source == obj for source in sources) or len(set(sources)) != len(sources):
        raise RefusalError('object_join sources must be distinct and differ from the target', code='VALIDATION')
    for source in sources:
        if source.type != 'MESH':
            raise RefusalError('object_join joins mesh objects only', code='VALIDATION')
        _plain_mesh(context, source, 'object_join')
        if source.children:
            raise RefusalError('object_join SOURCE_HAS_CHILDREN: unparent the source\'s children first', code='VALIDATION')
        require_deletable(context, source, 'object_join')
    total = len(target_mesh.vertices) + sum(len(source.data.vertices) for source in sources)
    if total > MAX_VERTICES:
        raise RefusalError('The joined mesh exceeds the native vertex limit', code='LIMIT_EXCEEDED')
    from .action_budget import _update_stage
    _update_stage(context)
    inverse = obj.matrix_world.inverted()
    bm = bmesh.new()
    try:
        bm.from_mesh(target_mesh)
        for source in sources:
            part = source.data.copy()  # Temporary, unlinked; removed below.
            try:
                part.transform(inverse @ source.matrix_world)
                # Map each source slot's material onto the target's slots, appending missing ones.
                remap = []
                for slot in source.material_slots:
                    material = slot.material
                    keys = [_material_key(item) for item in target_mesh.materials]
                    if material is not None and _material_key(material) in keys:
                        remap.append(keys.index(_material_key(material)))
                    else:
                        if len(target_mesh.materials) >= 32:
                            raise RefusalError('The joined mesh would exceed 32 material slots', code='LIMIT_EXCEEDED')
                        target_mesh.materials.append(material)
                        remap.append(len(target_mesh.materials) - 1)
                if remap:
                    for polygon in part.polygons:
                        polygon.material_index = remap[min(polygon.material_index, len(remap) - 1)]
                if (inverse @ source.matrix_world).determinant() < 0:
                    part.flip_normals()
                bm.from_mesh(part)  # Appends to the geometry already in bm.
            finally:
                bpy.data.meshes.remove(part)
        _check_counts(len(bm.verts), len(bm.edges), len(bm.faces))
        bm.to_mesh(target_mesh)
        target_mesh.update()
    finally:
        bm.free()
    deleted = context.extras.setdefault('deleted', [])
    deleted.extend(reference for reference in references if reference not in deleted)
    context.topology_versions[obj] = context.topology_versions.get(obj, 0) + 1
    return _readback(obj, operation, joined=len(sources), topology_step=context.topology_versions[obj])


def _parts(mesh, params):
    """Face index groups: the target keeps the first, each other becomes an object."""
    mode = params['mode']
    if mode == 'MATERIAL':
        groups = {}
        for polygon in mesh.polygons:
            groups.setdefault(polygon.material_index, []).append(polygon.index)
        return [groups[key] for key in sorted(groups)]
    if mode == 'SELECTION':
        selected = {face.index for face in _face_selection(mesh, params)}
        rest = [polygon.index for polygon in mesh.polygons if polygon.index not in selected]
        return [rest, sorted(selected)]
    # LOOSE: faces connected through shared vertices.
    parent = list(range(len(mesh.vertices)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for polygon in mesh.polygons:
        first = find(polygon.vertices[0])
        for vertex in polygon.vertices[1:]:
            root = find(vertex)
            if root != first:
                parent[root] = first
    groups = {}
    for polygon in mesh.polygons:
        groups.setdefault(find(polygon.vertices[0]), []).append(polygon.index)
    return sorted(groups.values(), key=lambda faces: faces[0])


def _keep_only(mesh, faces):
    """Delete every face not in ``faces``, and the vertices and edges only they used."""
    keep = set(faces)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        doomed = [face for face in bm.faces if face.index not in keep]
        bmesh.ops.delete(bm, geom=doomed, context='FACES')
        loose = [vertex for vertex in bm.verts if not vertex.link_faces]
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()


def separate(context, obj, operation):
    mesh = _plain_mesh(context, obj, 'mesh_separate')
    animation = obj.animation_data
    if animation is not None and (animation.action is not None or len(animation.drivers) or len(animation.nla_tracks)):
        raise RefusalError('mesh_separate ANIMATED: separating an animated object is reviewed Python', code='UNSUPPORTED')
    if not len(mesh.polygons):
        raise RefusalError('mesh_separate needs a mesh with faces', code='VALIDATION')
    parts = [faces for faces in _parts(mesh, operation['parameters']) if faces]
    if len(parts) < 2:
        raise RefusalError('NOTHING_TO_SEPARATE: the mesh has a single %s part' % operation['parameters']['mode'].lower(),
                           code='VALIDATION')
    if len(parts) > MAX_PARTS or len(parts) * len(mesh.vertices) > MAX_SEPARATE_WORK:
        raise RefusalError('mesh_separate would create more parts than the bounded native capability', code='LIMIT_EXCEEDED')
    from .action_object_management import _membership
    reference = operation['target']
    base = current_name(context.extras, reference, obj)  # Never the private staged name.
    members = _membership(context, reference)  # The target's collections at this point of the program.
    created = []
    for faces in parts[1:]:
        part = context.track_id(obj.copy())
        data = context.track_id(mesh.copy())
        part.data = data
        rename_new_object(context, part, base, None, bpy.data.objects.get, requested=False)  # Cube.001 in program order.
        name = committed_name(context.extras, part)  # Its claim, or the name Blender gave (truncated when too long).
        _keep_only(data, faces)
        context.add_object(part)
        # By the committed name: commit gives a claimed part its name before linking it, and a part renamed while
        # its stage was suspended is re-keyed under its new name (program_memberships, stage_new_objects).
        context.extras.setdefault(NAMED, {})[name] = list(members)
        created.append({'name': name, 'faces': len(data.polygons), 'vertices': len(data.vertices)})
    _keep_only(mesh, parts[0])
    context.topology_versions[obj] = context.topology_versions.get(obj, 0) + 1
    return _readback(obj, operation, created=created, topology_step=context.topology_versions[obj])
