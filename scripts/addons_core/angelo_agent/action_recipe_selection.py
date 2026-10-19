# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only merge planning with exact surviving-face lineage and topology.

The public recipe adapter owns the fresh source revision. Temporary BMesh/RNA
data is never linked, never written back to the source, and always discarded.
The real program must match the planned post-merge topology before using IDs.
"""

from collections import Counter

import bpy
import bmesh

from .refusal_codes import RefusalError

from .action_topology import bounded_topology, topology_digest


def plan_clean_mesh_faces(obj, parameters):
    """Retain an explicit initial face selection through a deterministic merge.

    Removed/replaced faces are rejected rather than turning an empty selection
    into the catalog's "all faces" convention. A digest catches any index-order
    drift between planning, staging, serialization and actual merge execution.
    """
    if obj.type != 'MESH' or obj.mode != 'OBJECT' or obj.data.is_editmode:
        raise RefusalError('Explicit clean-mesh faces require Object-mode base mesh data', code="UNSUPPORTED")
    mesh = obj.data
    bounded_topology(mesh)
    if mesh.shape_keys is not None:
        raise RefusalError('Clean-mesh topology planning does not support shape keys', code="UNSUPPORTED")
    # Validate finite coordinates too, before constructing a bounded BMesh.
    topology_digest(mesh)
    faces, vertices = parameters['face_indices'], parameters['vertex_indices']
    if any(i >= len(mesh.polygons) for i in faces) or any(i >= len(mesh.vertices) for i in vertices):
        raise RefusalError('Clean-mesh selection is outside the observed base mesh', code="VALIDATION")
    bm, temporary = bmesh.new(), None
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        lineage = bm.faces.layers.int.new('Angelo Recipe Face Lineage')
        lineage_name = lineage.name
        for face in bm.faces:
            face[lineage] = face.index + 1
        bmesh.ops.remove_doubles(bm, verts=[bm.verts[i] for i in vertices], dist=parameters['distance'])
        temporary = bpy.data.meshes.new('__angelo_recipe_selection')
        bm.normal_update()
        bm.to_mesh(temporary)
        temporary.update()
        attribute = temporary.attributes.get(lineage_name)
        counts = Counter(item.value - 1 for item in attribute.data)
        surviving = {item.value - 1: index for index, item in enumerate(attribute.data)}
        if any(counts[i] != 1 for i in faces):
            raise RefusalError('Merge removes or replaces an explicitly selected face; inspect the merged mesh and choose a new face selection', code="VALIDATION")
        return {'face_indices': [surviving[i] for i in faces], 'topology_digest': topology_digest(temporary)}
    finally:
        bm.free()
        if temporary is not None:
            bpy.data.meshes.remove(temporary)
