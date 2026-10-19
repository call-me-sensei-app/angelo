# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disposable staged ID ownership, base-mesh guards and bounded selections.

The executor supplies copies only. This module never resolves global names or
evaluates a dependency graph; action_budget owns pre-evaluation work limits."""

import math
import bpy
import numpy as np
from mathutils import Vector
from .refusal_codes import RefusalError
from .action_schema_types import MAX_VERTICES, MAX_EDGES, MAX_FACES
from .action_schemas import INDEXED_KINDS
from .action_topology import topology_digest

# A rule-based selector matches up to the program's mesh limit. In a recorded bridge trial a material on a
# valley's 5,121 upward faces met the 4,096 bound of explicit index lists, and its 20-operation program failed.
SELECTION_OVER = ('The rule-based %s selector matched more than %s %s, the typed programs\' mesh limit; narrow it '
                  '(%s) or edit with angelo_execute_python')


def _local(block):
    if block is None or block.library is not None or getattr(block, 'override_library', None) is not None:
        raise RefusalError('Native actions require local non-linked staged IDs', code='INTERNAL_ERROR')



def _require_subdivision(level):
    if level and not bpy.app.build_options.opensubdiv:
        raise RefusalError('Subdivision is unavailable: this Blender build lacks OpenSubdiv', code='UNSUPPORTED')



class StagedActionContext:
    """Concrete executor/kernel contract. Constructor objects MUST be copies.

    objects maps document handles to staged objects; aliases maps ``$name`` to
    creations in this program. private single-user object data is required for
    mutation. owned_ids can additionally contain copied Actions/node groups.
    The collection and its scene must be disposable staging resources.
    """

    def __init__(self, scene, collection, objects=None, aliases=None, owned_ids=None, view_layer=None,
                 scene_handles=(), extras=None):
        _local(scene)
        _local(collection)
        self.scene, self.collection = scene, collection
        # Document handles of the bound live scene. Its allow-listed settings
        # were copied into this private stage scene, which stands in for it.
        self.scene_handles = frozenset(scene_handles)
        # Small JSON state kernels hand to commit, persisted with the stage
        # (world_configure's intended environment image).
        self.extras = {} if extras is None else extras
        self.view_layer = view_layer or scene.view_layers[0]
        if self.view_layer.id_data != scene:
            raise RefusalError('The staged view layer must belong to the staged scene', code='INTERNAL_ERROR')
        self.objects, self.aliases = {}, {}
        self.created_ids = []
        self.owned_ids = set(owned_ids or ()) | {scene, collection}
        self.node_bounds = {}
        self.node_graphs = {}
        # Each staged object's render-time bound when first seen: a program never raises one past
        # MAX_RENDER_VERTICES, and an object already above it can still be edited or lowered (action_budget).
        self.render_baselines = {}
        self.members = None  # Pointers of the stage collection's objects during action_budget.assert_stage_budgets.
        self.borrowing = None  # Live materials the stage reads in place (stage_borrowing), set by the backend.
        self.topology_versions = {}
        for reference, staged in (objects or {}).items():
            self.admit_object(reference, staged)
        for alias, staged in (aliases or {}).items():
            reference = alias if alias.startswith('$') else '$' + alias
            self.aliases[reference] = staged
            self.owned_ids.add(staged)
            if staged.data is not None:
                self.owned_ids.add(staged.data)
            self.assert_staged(staged)
            self.topology_versions[staged] = 0

    def admit_object(self, reference, staged):
        if type(reference) is not str or not reference or reference.startswith('$') or reference in self.objects:
            raise RefusalError('Staged objects require unique document handles', code='INTERNAL_ERROR')
        self.objects[reference] = staged
        self.owned_ids.add(staged)
        if staged.data is not None:
            self.owned_ids.add(staged.data)
        self.assert_staged(staged)
        self.topology_versions[staged] = 0
        return staged

    def track_id(self, block):
        _local(block)
        if block not in self.owned_ids:
            self.owned_ids.add(block)
            self.created_ids.append(block)
        return block

    def refresh_id(self, previous, current):
        """Replace an owned RNA wrapper after its ID changes RNA subtype.

        Refined wrappers compare unequal even though they address the same ID.
        Preserve the exact ID identity and ledger size; this grants no ownership
        of a different ID and does not count a subtype change as a creation.
        """
        _local(current)
        identity = lambda block: (block.as_pointer(), block.session_uid, block.id_type)
        if previous not in self.owned_ids or identity(previous) != identity(current):
            raise RefusalError('Only the same already-owned ID may refresh its RNA wrapper', code='INTERNAL_ERROR')
        if previous != current:
            self.owned_ids.remove(previous)
            self.owned_ids.add(current)
            self.created_ids[:] = [current if block == previous else block for block in self.created_ids]
        return current

    def add_object(self, staged, alias=None):
        self.track_id(staged)
        if staged.data is not None and staged.data not in self.owned_ids:
            raise RefusalError('New staged object data must be journaled before linking', code='INTERNAL_ERROR')
        if alias and ('$' + alias) in self.aliases:
            raise RefusalError('A staged alias cannot be redefined', code='VALIDATION')
        self.collection.objects.link(staged)
        if alias:
            self.aliases['$' + alias] = staged
        self.topology_versions[staged] = 0
        self.assert_staged(staged)
        return staged

    def add_copy(self, staged, alias):
        """Link a new object a kernel just made (already tracked) under ``alias`` ('$name').

        ``add_object`` without its scan of the stage collection: the caller made
        ``staged`` and links it here, so the scan could only confirm this call.
        """
        if staged not in self.owned_ids or (staged.data is not None and staged.data not in self.owned_ids):
            raise RefusalError('New staged object data must be journaled before linking', code='INTERNAL_ERROR')
        if alias in self.aliases:
            raise RefusalError('A staged alias cannot be redefined', code='VALIDATION')
        _local(staged)
        self.collection.objects.link(staged)
        self.aliases[alias] = staged
        self.topology_versions[staged] = 0
        return staged

    def resolve(self, reference):
        if type(reference) is not str:
            raise RefusalError('A native target requires a staged reference', code='VALIDATION')
        if reference in self.scene_handles:
            raise RefusalError('The scene handle addresses scene settings (property_set), not an object', code='VALIDATION')
        if reference in self.extras.get('deleted', ()):
            raise RefusalError('This object is deleted earlier in the same program', code='VALIDATION')
        staged = self.aliases.get(reference) if reference.startswith('$') else self.objects.get(reference)
        if staged is None:
            raise RefusalError('Target is outside this staged program', code='VALIDATION')
        return self.assert_staged(staged)

    def assert_staged(self, staged):
        _local(staged)
        if staged not in self.owned_ids or not isinstance(staged, bpy.types.Object):
            raise RefusalError('A native action cannot mutate a non-staged object', code='INTERNAL_ERROR')
        # Names are unique among local objects, so the collection's member of this name is the object or nothing.
        # During one pass over the whole collection (members), its own snapshot answers without a lookup each.
        if (staged.as_pointer() not in self.members if self.members is not None
                else self.collection.objects.get(staged.name) != staged):
            raise RefusalError('Object is not linked to the disposable stage collection', code='INTERNAL_ERROR')
        # Every collection link adds a user, so a single-user object linked to
        # the stage collection has no other link. Object.users_collection scans
        # every collection in the file, so it is only the multi-user fallback.
        if staged.users != 1 and any(collection != self.collection for collection in staged.users_collection):
            raise RefusalError('Staged objects must not also be linked to a live collection', code='INTERNAL_ERROR')
        if staged.mode != 'OBJECT':
            raise RefusalError('Native action targets must be in Object mode', code='INTERNAL_ERROR')
        return staged

    def require_private(self, block):
        _local(block)
        if block not in self.owned_ids:
            raise RefusalError('Native mutation requires a private single-user staged data ID', code='INTERNAL_ERROR')
        if block.users > 1:
            raise RefusalError('Native mutation requires a private single-user staged data ID: linked copies made '
                               'earlier in this program share this data; edit it before copying, or in a later program',
                               code='INTERNAL_ERROR')
        return block

    def require_group_slots(self, block):
        """Staged mesh data a material kind gives a material, shared or not.

        A batch's linked copies share their unit's staged mesh. ``program_rules``
        admits a material write to such a group only when every member gets the
        same material on every face from a state without slots, so writing the
        shared data once is the write of each of them, and what commit keeps.
        """
        if block.users > 1:  # Only a group of this program's linked copies, every user one of them.
            linked = getattr(self, 'extras', {}).get('linked_duplicates', {})
            members = {'$' + reference.lstrip('$') if not reference.startswith('scn:') else reference
                       for reference in list(linked) + list(linked.values())}
            users = {('$' + reference.lstrip('$') if not str(reference).startswith('scn:') else reference)
                     for reference, obj in list(getattr(self, 'objects', {}).items()) + list(getattr(self, 'aliases', {}).items())
                     if getattr(obj, 'data', None) == block}
            if len(users) != block.users or not users <= members:
                return self.require_private(block)  # Refused in its own words.
        _local(block)
        if block not in self.owned_ids:
            raise RefusalError('Native mutation requires a private single-user staged data ID', code='INTERNAL_ERROR')
        return block

    def require_owned(self, block):
        """Staged data a kernel only reads (its counts, for a bound): owned by the stage, shared or not."""
        _local(block)
        if block not in self.owned_ids:
            raise RefusalError('Native actions read only staged data', code='INTERNAL_ERROR')
        return block



def _counts(mesh):
    return {'vertices': len(mesh.vertices), 'edges': len(mesh.edges), 'faces': len(mesh.polygons)}



def _check_counts(vertices, edges, faces):
    if vertices > MAX_VERTICES or edges > MAX_EDGES or faces > MAX_FACES:
        raise RefusalError('Native base mesh topology exceeds the bounded work limits', code='LIMIT_EXCEEDED')



def _mesh(context, obj, topology=False, readonly=False, slots=False):
    """The staged object's bounded, finite mesh; ``readonly`` admits data linked copies share (never to edit it), and
    ``slots`` a material kind's write to it (``require_group_slots``)."""
    context.assert_staged(obj)
    if obj.type != 'MESH':
        raise RefusalError('This native action requires a staged mesh object', code='VALIDATION')
    require = (getattr(context, 'require_owned', context.require_private) if readonly
               else getattr(context, 'require_group_slots', context.require_private) if slots else context.require_private)
    mesh = require(obj.data)
    _check_counts(len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    if mesh.is_editmode:
        raise RefusalError('Staged mesh data must not be in Edit mode', code='INTERNAL_ERROR')
    if topology and mesh.shape_keys is not None:
        raise RefusalError('Topology edits on shape-key meshes are not supported by this kernel', code='UNSUPPORTED')
    # Counts are bounded above before allocation. Read *all* coordinates fresh
    # from original RNA, never trust a pointer/count/depsgraph revision cache.
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', positions)
    if not np.isfinite(positions).all() or not (np.abs(positions) <= 100000).all():
        raise RefusalError('Staged mesh coordinates must be finite and bounded', code='VALIDATION')
    return mesh



def _selected(sequence, indexes, all_if_empty=False):
    if not indexes and all_if_empty:
        return list(sequence)
    if any(index >= len(sequence) for index in indexes):
        raise RefusalError('A topology index is outside the staged base mesh', code='VALIDATION')
    return [sequence[index] for index in indexes]



def _guard_index_scope(context, obj, operation):
    if operation['kind'] not in INDEXED_KINDS:
        return
    params = operation['parameters']
    specific = any(params.get(name) for name in ('face_indices', 'edge_indices', 'vertex_indices', 'seam_edge_indices'))
    specific |= operation['kind'] == 'armature_bind'
    if not specific:
        return
    generation = context.topology_versions.get(obj, 0)
    wanted = 0 if params['index_scope'] == 'INITIAL' else params['topology_step']
    if generation != wanted:
        raise RefusalError('Stale topology indexes: bind CURRENT indexes to the exact observed topology_step', code='STALE_REVISION')
    expected_digest = params.get('topology_digest')
    if expected_digest is not None and topology_digest(obj.data) != expected_digest:
        raise RefusalError('Planned topology selection no longer matches the exact post-merge base mesh; inspect and expand again',
                           code='STALE_REVISION')



def _face_selection(mesh, params):
    selector = params.get('face_selector')
    if selector is None:
        return _selected(mesh.polygons, params['face_indices'], all_if_empty=True)
    face_sets = None
    if selector['face_set'] is not None:
        face_sets = mesh.attributes.get('.sculpt_face_set')
        if face_sets is None or face_sets.domain != 'FACE' or face_sets.data_type != 'INT':
            raise RefusalError('Face-set selector requires the staged integer FACE .sculpt_face_set attribute', code='VALIDATION')
    selected = []
    normal = Vector(selector['normal']) if selector['normal'] is not None else None
    threshold = math.cos(selector['normal_angle'])
    for face in mesh.polygons:
        if normal is not None and face.normal.dot(normal) < threshold - 1e-7:
            continue
        if selector['material_index'] is not None and face.material_index != selector['material_index']:
            continue
        if face_sets is not None and face_sets.data[face.index].value != selector['face_set']:
            continue
        if not selector['area_min'] <= face.area <= selector['area_max']:
            continue
        selected.append(face)
        if len(selected) > MAX_FACES:  # A rule matches up to the mesh limit; 4,096 bounds only explicit index lists.
            raise RefusalError(SELECTION_OVER % ('face', format(MAX_FACES, ','), 'faces',
                                                 'normal_angle, material_index, face_set or area range'),
                               code='LIMIT_EXCEEDED')
    if not selected:
        raise RefusalError('Rule-based face selector matched no staged faces', code='VALIDATION')
    return selected



def _edge_selection(bm, params):
    selector = params.get('edge_selector')
    if selector is None:
        return _selected(bm.edges, params['edge_indices'])
    result = []
    for edge in bm.edges:
        if selector['boundary'] == 'ONLY' and not edge.is_boundary:
            continue
        if selector['boundary'] == 'EXCLUDE' and edge.is_boundary:
            continue
        if selector['sharp'] is not None and (not edge.smooth) != selector['sharp']:
            continue
        angle = edge.calc_face_angle(0) if edge.is_manifold else 0
        if selector['angle_min'] - 1e-7 <= angle <= selector['angle_max'] + 1e-7:
            result.append(edge)
            if len(result) > MAX_EDGES:
                raise RefusalError(SELECTION_OVER % ('edge', format(MAX_EDGES, ','), 'edges',
                                                     'angle range, boundary or sharp'), code='LIMIT_EXCEEDED')
    if not result:
        raise RefusalError('Rule-based edge selector matched no staged edges', code='VALIDATION')
    return result



def _readback(obj, operation, **extra):
    result = {'kind': operation['kind'], 'target': operation['target'], 'object': obj.name,
              'object_type': obj.type}
    if obj.type == 'MESH':
        result.update(_counts(obj.data))
    result.update(extra)
    return result



def _set_initial_transform(obj, params):
    obj.location = params['location']
    obj.rotation_mode = 'XYZ'
    obj.rotation_euler = params['rotation']
    obj.scale = params['scale']
