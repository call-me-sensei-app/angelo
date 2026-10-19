# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative staged evaluation budgets and guarded graph updates.

Run these guards before any dependency-graph update and after topology edits.
Node bounds are recomputed from current upstream geometry, never reused as
a stale absolute estimate. Unknown stacks and pre-existing drivers fail closed.

The bound is loop-scale: ``max(V, 2E, 4F, L)`` of the mesh each modifier
passes on. Subdivision counts the viewport levels the stage evaluates (at most
3). Render levels (at most 6) and adaptive dicing are never evaluated by a
stage, a check or a solid or material view: Blender's own evaluation runs the
modifier at its viewport levels, and only a render reads the others. They are
held to a render-time ceiling instead: the staged bound times ``4^(render -
viewport)`` over every Subdivision modifier in the stack and its operands may
not pass ``MAX_RENDER_VERTICES`` (64 times the staged budget, the most the
earlier cap of 3 could leave), unless the object was already above it when it
was staged: then a program may edit or lower it but not raise it. A Bevel of
``s`` segments grows the bound at most
``(s + 1)^2 + 4`` times (strips, corner patches and odd-segment centre
polygons), ``2 (s + 1)^2 + 4`` with arc or patch miters. Blender 5.2's worst
cases over primitives, Suzanne, every miter and affect mode and 1-8 segments, a
cube each time, measure ``(s + 1)^2 + 1/3`` and ``(2s + 1)(s + 1)``. A
Boolean's result is estimated as both operands plus the smaller one for the cut
(Exact, Float and Manifold results measured at most 1.04 times both operands),
because a sound bound of its intersection curve before evaluation is quadratic
and refused every everyday cut on a subdivided mesh in recorded requests;
its evaluated result is held to the budget after the update instead
(``assert_evaluated``).
"""

from .refusal_codes import RefusalError
from .action_catalog import geometry_node_work
from .action_context import _mesh, _counts, _require_subdivision
from .action_node_graphs import _node_parameters
from .action_schema_types import (MAX_EVALUATED_BONES, MAX_EVALUATED_VERTICES, MAX_RENDER_VERTICES,
                                  MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS)


def _modifier_bound(context, obj, extra=None, replacing=None, visiting=None, shape_override=None, render=None,
                    prospective=False):
    """The staged (viewport) bound of ``obj``'s stack; ``render`` collects its render-over-viewport factor.

    The outermost call holds the stack to the render-time ceiling (module docstring). ``prospective``: the stack
    already holds an operation's new or reconfigured modifier, so it is not the stack as the stage found it.
    """
    mesh = _mesh(context, obj, readonly=True)  # Counts only: linked copies may share the data.
    outermost, render = render is None, [1] if render is None else render
    visiting = set(visiting or ())
    if obj in visiting:
        raise RefusalError('Modifier dependencies cannot form a cycle', code='VALIDATION')
    visiting.add(obj)
    counts = shape_override or dict(_counts(mesh), loops=len(mesh.loops))
    bound = max(counts['vertices'], counts['edges'] * 2, counts['faces'] * 4, counts.get('loops', 0))
    particles = 0  # Particle points are separate geometry beside the mesh the stack passes on.
    entries = [(modifier.type, modifier) for modifier in obj.modifiers if modifier != replacing]
    if extra:
        entries.append((extra['type'], extra['settings']))
    for kind, settings in entries:
        is_dict = isinstance(settings, dict)
        if kind == 'SUBSURF':
            levels, rendered = ((settings['levels'], settings['render_levels']) if is_dict
                                else (settings.levels, settings.render_levels))
            if levels > MAX_SUBDIVISION_LEVELS or rendered > MAX_SUBDIVISION_RENDER_LEVELS:
                raise RefusalError('Existing subdivision exceeds the bounded native capability (viewport levels up '
                                   'to %d, render levels up to %d)' % (MAX_SUBDIVISION_LEVELS, MAX_SUBDIVISION_RENDER_LEVELS),
                                   code='LIMIT_EXCEEDED')
            _require_subdivision(max(levels, rendered))
            bound *= 4 ** levels  # The stage evaluates viewport levels only.
            render[0] *= 4 ** max(0, rendered - levels)
        elif kind == 'MIRROR':
            axes = settings['axes'] if is_dict else list(settings.use_axis)
            bound *= 2 ** sum(axes)
        elif kind == 'SOLIDIFY':
            bound *= 3
        elif kind == 'BEVEL':
            segments = settings['segments'] if is_dict else settings.segments
            if segments > 8:
                raise RefusalError('Existing bevel exceeds the bounded native capability', code='LIMIT_EXCEEDED')
            bound *= bevel_factor(segments, not is_dict and (settings.miter_outer, settings.miter_inner)
                                  != ('MITER_SHARP', 'MITER_SHARP'))
        elif kind == 'BOOLEAN':
            operand = context.resolve(settings['operand']) if is_dict else settings.object
            if operand is None:
                raise RefusalError('Boolean modifiers require a staged operand', code='VALIDATION')
            context.assert_staged(operand)
            operand_bound = _modifier_bound(context, operand, visiting=visiting, render=render)
            # Both evaluated operands and the smaller one for the cut (module docstring); assert_evaluated
            # holds the evaluated result to the budget.
            bound += operand_bound + min(bound, operand_bound)
        elif kind == 'NODES':
            parameters = _node_parameters(context, settings.node_group)
            from .action_schemas import GEOMETRY_NODE_TYPES
            if any(node['type'] not in GEOMETRY_NODE_TYPES for node in parameters['nodes']):
                from .node_generic import mesh_area, tree_bound
                first = obj.modifiers[0] == settings  # Only then is the group input the base mesh.
                bound = tree_bound(settings.node_group, bound, (lambda: mesh_area(mesh)) if first else None)
            else:
                bound = geometry_node_work(parameters, {'vertices': bound, 'edges': 0, 'faces': 0})
            context.node_bounds[settings.node_group] = bound
        elif kind == 'ARRAY':
            if not is_dict:
                if settings.fit_type != 'FIXED_COUNT' or settings.start_cap or settings.end_cap:
                    raise RefusalError('Array fit-to-length/curve and caps are outside the bounded native capability',
                                       code='UNSUPPORTED')
                if settings.use_object_offset and settings.offset_object is not None:
                    context.assert_staged(settings.offset_object)
            count = settings['count'] if is_dict else settings.count
            if count > 1024:
                raise RefusalError('Existing array count is outside the bounded native capability', code='LIMIT_EXCEEDED')
            bound *= count
        elif kind == 'SCREW':
            steps = max(settings['steps'], settings['render_steps']) if is_dict else max(settings.steps, settings.render_steps)
            iterations = settings['iterations'] if is_dict else settings.iterations
            if not is_dict and settings.object is not None:
                context.assert_staged(settings.object)
            bound *= (steps + 1) * iterations
        elif kind == 'SHRINKWRAP':
            targets = [context.resolve(settings['target'])] if is_dict else [settings.target, settings.auxiliary_target]
            for target in targets:
                if target is not None:
                    context.assert_staged(target)
                    # Building the target's lookup tree is linear in its evaluated size.
                    bound += (_modifier_bound(context, target, visiting=visiting, render=render)
                              if target.type == 'MESH' else 0)
        elif kind == 'REMESH':
            mode = settings['mode'] if is_dict else settings.mode
            if mode == 'VOXEL':
                # Surface voxels of the evaluated bounds: at most 6 faces of (extent / size)^2 cells.
                size = settings['voxel_size'] if is_dict else settings.voxel_size
                extent = max(max(obj.dimensions), .000001) * 2
                cells = (extent / max(size, .000001)) ** 2 * 6
                if cells > MAX_EVALUATED_VERTICES:
                    raise RefusalError('Remesh voxel size is too small for the bounded native capability', code='LIMIT_EXCEEDED')
                bound = max(bound, int(cells))
            else:
                depth = settings['octree_depth'] if is_dict else settings.octree_depth
                bound = max(bound, 6 * 4 ** depth)
        elif kind == 'PARTICLE_SYSTEM' and not is_dict:
            from .action_particles import modifier_points
            particles += modifier_points(context, obj, settings,
                                         lambda target: _modifier_bound(context, target, visiting=visiting,
                                                                        render=render))
        elif kind == 'CURVE':
            curve = context.resolve(settings['object']) if is_dict else settings.object
            if curve is not None:
                context.assert_staged(curve)
        elif not is_dict and kind not in {'ARMATURE', 'SMOOTH', 'LAPLACIANSMOOTH', 'WEIGHTED_NORMAL', 'NORMAL_EDIT',
                                          'DISPLACE', 'TRIANGULATE', 'DECIMATE', 'WELD'}:
            # Any other type: its object dependencies are staged and its factor is known, or it fails closed.
            from .generic_settings import DENIED_MODIFIERS, bound_factor, object_pointers
            if kind in DENIED_MODIFIERS:
                raise RefusalError('Existing modifier topology is outside the bounded native capability: ' + kind, code='UNSUPPORTED')
            for target in object_pointers(settings):
                context.assert_staged(target)
                if target.type == 'MESH' and target != obj:
                    bound += _modifier_bound(context, target, visiting=visiting, render=render)  # Lookups read it.
            bound *= bound_factor(kind, settings)
        elif is_dict and kind not in {'ARMATURE', 'SMOOTH', 'LAPLACIANSMOOTH', 'WEIGHTED_NORMAL', 'NORMAL_EDIT',
                                      'DISPLACE', 'TRIANGULATE', 'DECIMATE', 'WELD'}:
            raise RefusalError('Existing modifier topology is outside the bounded native capability', code='UNSUPPORTED')
        if bound + particles > MAX_EVALUATED_VERTICES:
            raise RefusalError('Modifier stack exceeds the conservative evaluated topology budget', code='LIMIT_EXCEEDED')
    if outermost and render[0] > 1:
        _hold_render_bound(context, obj, bound * render[0], extra is None and replacing is None and not prospective)
    return bound


def _hold_render_bound(context, obj, value, existing):
    """Refuse a render-time bound above the ceiling, unless the staged object already had one at least as large.

    ``existing``: the bound of the stack as it is (no prospective modifier), which a stage records the
    first time it sees the object. Binding a live object (no staged context) records nothing and refuses
    nothing: a stage never evaluates render levels.
    """
    baselines = getattr(context, 'render_baselines', None)
    if baselines is None:
        return
    baseline = baselines.setdefault(obj, value) if existing else baselines.get(obj, 0)
    if value > max(MAX_RENDER_VERTICES, baseline):
        raise RefusalError('Subdivision render levels exceed the render-time topology budget: about %d elements at '
                           'render time (limit %d); lower render_levels or the mesh density, or use adaptive '
                           'subdivision with low render levels' % (value, max(MAX_RENDER_VERTICES, baseline)),
                           code='LIMIT_EXCEEDED')


def render_factor(obj):
    """How many times ``obj``'s own Subdivision modifiers grow its staged bound at render time."""
    factor = 1
    for modifier in obj.modifiers:
        if modifier.type == 'SUBSURF':
            factor *= 4 ** max(0, modifier.render_levels - modifier.levels)
    return factor



def bevel_factor(segments, mitered=False):
    """How many times a Bevel of ``segments`` can grow the loop-scale bound (module docstring)."""
    return (segments + 1) ** 2 * (2 if mitered else 1) + 4


def assert_evaluated(depsgraph, objects):
    """After an update: every staged Boolean result is within the budget its estimate does not prove."""
    for obj in objects:
        if obj.type == 'MESH' and any(modifier.type == 'BOOLEAN' and modifier.show_viewport for modifier in obj.modifiers):
            mesh = obj.evaluated_get(depsgraph).data
            if max(len(mesh.vertices), len(mesh.edges) * 2, len(mesh.polygons) * 4, len(mesh.loops)) > MAX_EVALUATED_VERTICES:
                raise RefusalError('Boolean result exceeds the evaluated topology budget', code='LIMIT_EXCEEDED')


def assert_evaluation_budget(context, obj, drivers=True):
    """Fail closed BEFORE any depsgraph update, including transform/UV programs.

    ``drivers``: also scan every staged datablock for drivers. That scan does
    not depend on ``obj``, so a caller checking every staged object asks for it
    once (``assert_stage_budgets``) so a pass over the stage stays linear.

    Call for every staged input/dependency after copying but before linking an
    evaluated view layer or finish_stage. Existing ARRAY/REMESH/unknown node
    graphs and too-large stacks are rejected without evaluating them. Call
    again after each operation; node graphs are rebound to current upstream
    geometry rather than trusting an absolute bound from their creation.
    """
    context.assert_staged(obj)
    if drivers:
        from .driver_rules import problem
        for block in context.owned_ids:
            for holder in (block, getattr(block, 'node_tree', None)):
                animation = getattr(holder, 'animation_data', None)
                for curve in animation.drivers if animation is not None else ():
                    found = problem(curve)
                    if found is not None:  # Python, or the stage's context: never evaluated by a stage (driver_rules).
                        raise RefusalError('Native staged evaluation runs only drivers Blender evaluates natively, '
                                           'not %s (%r drives %s)' % (found, str(holder.name)[:64], curve.data_path[:96]),
                                           code='UNSUPPORTED')
    if obj.type == 'MESH':
        return _modifier_bound(context, obj)
    if obj.type == 'ARMATURE' and len(obj.data.bones) > MAX_EVALUATED_BONES:
        raise RefusalError('Existing armature exceeds the bounded evaluation budget', code='LIMIT_EXCEEDED')
    if obj.type in {'GREASEPENCIL', 'CURVES'} and len(obj.modifiers):
        # Grease Pencil and hair modifiers (arrays, node groups) have no native evaluation bound.
        raise RefusalError('Existing Grease Pencil or hair-curve modifiers are outside the bounded native capability',
                           code='UNSUPPORTED')
    return 0



def assert_stage_budgets(context):
    """``assert_evaluation_budget`` for every staged object, with one driver scan for all of them.

    The pass reads the stage collection once and changes nothing, so membership
    is answered from that snapshot instead of one collection lookup per object.
    """
    from .stage_borrowing import assert_unanimated
    assert_unanimated(context)  # Live materials the stage reads in place: still unanimated, so never evaluated.
    staged_objects = list(context.collection.objects)
    context.members = {staged.as_pointer() for staged in staged_objects}
    try:
        for index, staged in enumerate(staged_objects):
            assert_evaluation_budget(context, staged, drivers=not index)
    finally:
        context.members = None


def _update_stage(context):
    # Some context-sensitive native kernels require a current view layer. Do
    # not evaluate *any* staged input before every stack/dependency is bounded.
    from .exact_fence import end
    end()  # Evaluation runs drivers and depsgraph handlers: no exact read before it is reused after it.
    assert_stage_budgets(context)
    from .kept_values import kept_update
    kept_update(context, context.view_layer.update)  # Unkeyed values Blender keeps stay (kept_values).
    assert_evaluated(context.view_layer.depsgraph, context.collection.objects)
