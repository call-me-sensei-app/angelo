# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Public typed-kernel entry point, exclusively for disposable staged IDs.

The executor API remains here. Context/ownership, evaluation budgets, closed
node graphs and concrete domain actions live in explicit sibling modules.
Allocated IDs stay journaled for executor rollback/commit; no kernel resolves
global names, applies/deletes modifiers or touches filesystem/network resources."""

import bpy
from .refusal_codes import RefusalError
from .action_catalog import action_capabilities, validate_operation
from .action_context import StagedActionContext, _guard_index_scope
from .action_budget import assert_evaluation_budget
from .action_node_graphs import node_metadata, restore_node_metadata
from .action_mesh import _primitive, _mesh_edit, _uv_project, _uv_unwrap, _uv_smart_project, _uv_pack
from .action_modifiers import _modifier, _remove_modifier, _move_modifier, _apply_modifier
from .action_nodes import _material, _geometry
from .action_object import _transform, _parent, _constraint, _keyframe, _armature, _bind
from . import action_camera_light
from .action_property import apply_property_set
from .action_world import apply_world_configure
from . import action_object_management as management
from . import action_animation as animation
from . import action_join_separate as join_separate
from . import action_visibility as visibility
from . import action_curves as curves
from . import action_constraints as constraints
from . import action_bones as bones
from . import action_particles as particles
from . import action_operators as operators
from . import anim_keys, anim_key_edits, anim_fmodifiers, anim_copy, anim_actions, anim_nla, anim_markers
from .action_schemas import CONSTRAINT_SETTINGS


_ANIMATION_KERNELS = {'keyframe_edit': anim_key_edits.edit, 'fcurve_modifier': anim_fmodifiers.apply,
                      'keyframe_copy': anim_copy.apply, 'action_set': anim_actions.apply, 'nla_edit': anim_nla.apply}


def runtime_capabilities():
    """Catalog plus actual build availability; never advertise a silent no-op."""
    result = action_capabilities()
    opensubdiv = bool(bpy.app.build_options.opensubdiv)
    result['runtime_features'] = {'opensubdiv': opensubdiv}
    result['unavailable_in_this_build'] = [] if opensubdiv else [
        'modifier SUBSURF with nonzero levels', 'geometry node SUBDIVIDE_MESH with nonzero Level']
    return result



def apply_operation(context, operation):
    """Execute one validated action. Caller owns atomic staging/commit/rollback."""
    if not isinstance(context, StagedActionContext):
        raise RefusalError('Native kernels require an explicit StagedActionContext', code='INTERNAL_ERROR')
    operation = validate_operation(operation)
    kind = operation['kind']
    if kind == 'create_primitive':
        return _primitive(context, operation)
    if kind == 'armature_create':
        return _armature(context, operation)
    if kind in {'camera_create', 'light_create'}:
        return action_camera_light.create(context, operation)
    if kind == 'property_set':
        # The owner may be the stage scene, which has no evaluation budget of its own.
        return apply_property_set(context, operation)
    if kind == 'world_configure':
        return apply_world_configure(context, operation)
    if kind == 'visibility_set':
        return visibility.apply(context, operation)
    if kind == 'property_keyframe':
        return animation.key_properties(context, operation)
    if kind == 'object_duplicate':
        return management.duplicate(context, operation)
    if kind == 'object_convert':
        return curves.convert(context, operation)
    if kind == 'create_object':
        return management.create_object(context, operation)
    if kind == 'marker_edit':  # Timeline markers of the stage scene, the bound scene's stand-in.
        return anim_markers.apply(context, operation)
    if kind == 'node_edit':  # An object's tree or the stage scene's World; the kernel resolves and bounds its target.
        from . import node_edit
        return node_edit.apply(context, operation)
    if kind == 'batch':  # Copies of a staged unit; the kernel resolves and bounds its sources.
        from . import action_batch
        return action_batch.apply(context, operation)
    obj = context.resolve(operation['target'])
    assert_evaluation_budget(context, obj)
    _guard_index_scope(context, obj, operation)
    if kind == 'transform':
        return _transform(context, obj, operation)
    if kind == 'mesh_separate':
        return join_separate.separate(context, obj, operation)
    if kind == 'curve_points':
        return curves.set_points(context, obj, operation)
    if kind == 'operator_run':
        return operators.run(context, obj, operation)
    if kind == 'object_join':
        return join_separate.join(context, obj, operation)
    if kind.startswith('mesh_'):
        return _mesh_edit(context, obj, operation)
    if kind in {'modifier_add', 'modifier_configure'}:
        return _modifier(context, obj, operation)
    if kind == 'modifier_remove':
        return _remove_modifier(context, obj, operation)
    if kind == 'modifier_move':
        return _move_modifier(context, obj, operation)
    if kind == 'modifier_apply':
        return _apply_modifier(context, obj, operation)
    if kind == 'object_rename':
        return management.rename(context, obj, operation)
    if kind == 'object_delete':
        return management.delete(context, obj, operation)
    if kind == 'transform_apply':
        return management.apply_transform(context, obj, operation)
    if kind == 'origin_set':
        return management.set_origin(context, obj, operation)
    if kind == 'collection_link':
        return management.link_collection(context, obj, operation)
    if kind == 'keyframe_delete':
        return animation.delete_key(context, obj, operation)
    if kind == 'keyframe_configure':
        return anim_keys.configure(context, obj, operation)
    if kind in _ANIMATION_KERNELS:
        return _ANIMATION_KERNELS[kind](context, obj, operation)
    if kind == 'shape_key_add':
        return animation.add_shape_key(context, obj, operation)
    if kind == 'shape_key_value':
        return animation.set_shape_key(context, obj, operation)
    if kind == 'nla_push_down':
        return animation.push_down(context, obj, operation)
    if kind in {'material_principled', 'shader_nodes'}:
        return _material(context, obj, operation)
    if kind == 'material_assign':
        from .action_materials import assign
        return assign(context, obj, operation)
    if kind == 'geometry_nodes':
        return _geometry(context, obj, operation)
    if kind == 'uv_project':
        return _uv_project(context, obj, operation)
    if kind == 'uv_unwrap':
        return _uv_unwrap(context, obj, operation)
    if kind == 'uv_smart_project':
        return _uv_smart_project(context, obj, operation)
    if kind == 'uv_pack':
        return _uv_pack(context, obj, operation)
    if kind == 'parent_set':
        return _parent(context, obj, operation)
    if kind == 'constraint_add' and operation['parameters']['type'] not in CONSTRAINT_SETTINGS:
        return constraints.add(context, obj, operation)
    if kind == 'constraint_add':
        return _constraint(context, obj, operation)
    if kind == 'constraint_configure':
        return constraints.configure(context, obj, operation)
    if kind == 'keyframe_insert':
        return _keyframe(context, obj, operation)
    if kind == 'armature_bind':
        return _bind(context, obj, operation)
    if kind in {'camera_configure', 'light_configure'}:
        return action_camera_light.configure(context, obj, operation)
    if kind == 'bone_set':
        return bones.apply(context, obj, operation)
    if kind == 'particle_set':
        return particles.apply(context, obj, operation)
    raise RefusalError('No kernel is registered for this catalog action', code='INTERNAL_ERROR')
