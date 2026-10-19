# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual GUI injections for the scoped Sculpt restore lease.

The owning Sculpt fixture prepares its scene/checkpoint and calls ``run_case``
around a real grid-bearing Undo/Redo/history jump. Run destructive cases in
separate factory processes: a missing admission guard may otherwise close the
window or replace the test document before a receipt can be written.

Required matrix:
* Run each case in depsgraph PRE and POST during Preparing, using both reused
  and fully reread Main paths. Repeat history/owner refusals during Finalizing
  from a small Python render engine's view_update callback.
* Before the last fixture stroke, create a second Scene and ViewLayer so the
  corresponding removal would be admissible outside the lease. Supply a valid
  private checkpoint for read/revert/recover tests.
* Supply extra_actions for a linked Library reload, override reset/resync/destroy,
  and a valid closed private-stage detach. These must have positive controls in
  separate clean fixtures; invalid ownership/context is not admission evidence.
* Save probes additionally require unchanged destination/image state and no SAVE_PRE/POST
  callbacks during Preparing. Ordinary Save and Autosave must work during Finalizing
  and after scope exit. Engine/shading/RenderEngine class-owner probes refuse in both
  phases; supply class register/unregister extra_actions and assert class hooks were
  not called and bl_rna stayed attached.
* Test positive controls after the scope exits. Job operators need fresh small
  fixtures with complete parameters; this helper never launches them as controls.
* Worker-driver cases must log actual worker execution and attempt frame_set,
  ViewLayer.update, Depsgraph.update, evaluated_depsgraph_get, and graph-creating
  property setters before effects. Supply worker_api_actions for prepared object,
  node-tree, DataTransfer modifier, and cache fixtures. Verify their positive
  controls outside the lease; missing setup must not count as a refusal.
  Main-thread nested evaluations remain allowed and need separate exact-state,
  handler-order and surviving-final-editor-delivery checks.
* Compositor/preview/engine delivery and cancellation across later MemFiles are
  required independently; these API refusals do not prove job lifetime safety.

No fixture-only activation hook is used. The bridge observer is read-only and
exists only in WITH_GTESTS builds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import threading


def owner_signature(bpy) -> dict:
    """Observe original owners only; do not request graph evaluation in a handler."""
    return {
        'filepath': bpy.data.filepath,
        'scenes': tuple(sorted(
            (scene.name, scene.as_pointer(), tuple(
                (layer.name, layer.as_pointer()) for layer in scene.view_layers))
            for scene in bpy.data.scenes)),
        'libraries': tuple(sorted((lib.name, lib.as_pointer()) for lib in bpy.data.libraries)),
        'objects': tuple(sorted((obj.name, obj.as_pointer()) for obj in bpy.data.objects)),
        'windows': tuple(
            (win.as_pointer(), win.scene.as_pointer(), win.screen.as_pointer(),
             win.workspace.as_pointer(), win.view_layer.as_pointer(), tuple(
                 (area.as_pointer(), area.type, area.ui_type) for area in win.screen.areas))
            for wm in bpy.data.window_managers for win in wm.windows),
    }


def operations(bpy, checkpoint: Path, history_index: int, iterable_touched: list) -> dict:
    class SceneIds:
        def __iter__(self):
            iterable_touched.append(True)
            yield bpy.context.scene

    return {
        'undo_push': lambda: bpy.ops.ed.undo_push(message='Forbidden nested restore push'),
        'undo': lambda: bpy.ops.ed.undo(),
        'redo': lambda: bpy.ops.ed.redo(),
        'history': lambda: bpy.ops.ed.undo_history(item=history_index),
        'open': lambda: bpy.ops.wm.open_mainfile(filepath=str(checkpoint)),
        'save_mainfile': lambda: bpy.ops.wm.save_mainfile(
            filepath=str(checkpoint.with_name(checkpoint.stem + '-refused.blend'))),
        'save_as_mainfile': lambda: bpy.ops.wm.save_as_mainfile(
            filepath=str(checkpoint.with_name(checkpoint.stem + '-refused.blend'))),
        'save_auto_save': lambda: bpy.ops.wm.save_auto_save(),
        'render_engine': lambda: setattr(
            bpy.context.scene.render, 'engine', bpy.context.scene.render.engine),
        'update_render_engine': bpy.types.Scene.update_render_engine,
        'homefile': lambda: bpy.ops.wm.read_homefile(),
        'factory_reset': lambda: bpy.ops.wm.read_factory_settings(use_empty=True),
        'revert': lambda: bpy.ops.wm.revert_mainfile(),
        'recover_auto_save': lambda: bpy.ops.wm.recover_auto_save(filepath=str(checkpoint)),
        'window_close': lambda: bpy.ops.wm.window_close(),
        'area_close': lambda: bpy.ops.screen.area_close(),
        'area_type': lambda: setattr(bpy.context.area, 'type', 'TEXT_EDITOR'),
        'area_ui_type': lambda: setattr(bpy.context.area, 'ui_type', 'TEXT_EDITOR'),
        'window_scene': lambda: setattr(bpy.context.window, 'scene', bpy.context.scene),
        'window_workspace': lambda: setattr(
            bpy.context.window, 'workspace', bpy.context.window.workspace),
        'window_screen': lambda: setattr(bpy.context.window, 'screen', bpy.context.window.screen),
        'window_view_layer': lambda: setattr(
            bpy.context.window, 'view_layer', bpy.context.window.view_layer),
        'scene_remove': lambda: bpy.data.scenes.remove(bpy.context.scene),
        'view_layer_remove': lambda: bpy.context.scene.view_layers.remove(bpy.context.view_layer),
        'batch_remove': lambda: bpy.data.batch_remove(ids=SceneIds()),
        'orphans_purge': lambda: bpy.data.orphans_purge(do_recursive=True),
        'redraw': lambda: bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1),
        'render': lambda: bpy.ops.render.render('EXEC_DEFAULT'),
        'render_opengl': lambda: bpy.ops.render.opengl('EXEC_DEFAULT'),
        'uv_pack': lambda: bpy.ops.uv.pack_islands(),
        'simulation_bake': lambda: bpy.ops.object.simulation_nodes_cache_bake(),
        'mixdown': lambda: bpy.ops.sound.mixdown(filepath=str(checkpoint.with_suffix('.wav'))),
        'usd_export': lambda: bpy.ops.wm.usd_export(
            filepath=str(checkpoint.with_suffix('.usda')), as_background_job=True),
        'alembic_export': lambda: bpy.ops.wm.alembic_export(
            filepath=str(checkpoint.with_suffix('.abc')), as_background_job=True),
    }


def attempt_case(
    bpy,
    bridge,
    name: str,
    action: Callable,
    *,
    expected_phase: str,
    observe_history: Callable | None = None,
) -> dict:
    """Invoke once inside an already active lease; never assert only inside a handler."""
    record = {'name': name, 'phase': bridge._test_restore_phase()}
    if record['phase'] != expected_phase:
        raise AssertionError(f'Expected {expected_phase}, observed {record["phase"]}')
    before = owner_signature(bpy)
    history_before = observe_history() if observe_history else None
    try:
        record['result'] = action()
        # Preserve the status for diagnosis. A context-related CANCELLED is not proof that
        # this admission guard ran; public Python calls should supply a refusal error.
        record['refused'] = False
    except (RuntimeError, AttributeError) as exc:
        message = str(exc)
        record['error_type'] = type(exc).__name__
        record['error'] = message
        record['refused'] = (
            ('undo' in message.lower() and 'restor' in message.lower())
            or (isinstance(exc, AttributeError) and 'read-only' in message.lower())
        )
    record['owners_unchanged'] = owner_signature(bpy) == before
    record['history_unchanged'] = (
        observe_history() == history_before if observe_history else None)
    record['phase_after'] = bridge._test_restore_phase()
    return record


def run_case(
    bpy,
    bridge,
    name: str,
    trigger_restore: Callable,
    *,
    callback: str,
    checkpoint: Path,
    history_index: int = 0,
    observe_history: Callable | None = None,
    extra_actions: Mapping[str, Callable] | None = None,
) -> dict:
    """Register one real PRE/POST injection, invoke the outer restore, then check its receipt."""
    if callback not in {'pre', 'post'}:
        raise ValueError(callback)
    assert bridge._test_restore_phase() == 'Inactive'
    touched = []
    actions = operations(bpy, checkpoint, history_index, touched)
    actions.update(extra_actions or {})
    action = actions[name]
    receipts = []
    armed = True

    def handler(_scene, _depsgraph):
        nonlocal armed
        if not armed or bridge._test_restore_phase() != 'Preparing':
            return
        armed = False
        try:
            receipts.append(attempt_case(
                bpy, bridge, name, action, expected_phase='Preparing',
                observe_history=observe_history))
        except Exception as exc:
            # Blender clears callback exceptions, so keep the error for the outer assertion.
            receipts.append({'name': name, 'fixture_error': repr(exc)})

    handlers = getattr(bpy.app.handlers, f'depsgraph_update_{callback}')
    handlers.append(handler)
    try:
        result = trigger_restore()
    finally:
        if handler in handlers:
            handlers.remove(handler)
    assert result == {'FINISHED'}, result
    assert bridge._test_restore_phase() == 'Inactive'
    assert len(receipts) == 1, receipts
    record = receipts[0]
    assert record.get('refused'), record
    assert record.get('owners_unchanged'), record
    if observe_history:
        assert record.get('history_unchanged'), record
    assert record.get('phase_after') == 'Preparing', record
    assert not touched, 'batch_remove evaluated its argument before refusing'
    return record


def change_undo_preferences(bpy, bridge) -> dict:
    """Call from a lease handler, then verify these values again after the outer restore."""
    assert bridge._test_restore_phase() != 'Inactive'
    edit = bpy.context.preferences.edit
    edit.undo_steps = 2
    edit.undo_memory_limit = 1
    edit.use_global_undo = False
    return {
        'undo_steps': edit.undo_steps,
        'undo_memory_limit': edit.undo_memory_limit,
        'use_global_undo': edit.use_global_undo,
    }


def worker_api_actions(
    bpy, *, object_name: str | None = None, tree_name: str | None = None,
    modifier_name: str | None = None, cache_name: str | None = None,
    cache_layer_path: str | None = None,
) -> dict:
    """Resolve prepared original IDs by name at invocation, after any MemFile replacement.

    The cache fixture must already have a layer and supply a valid second layer path.
    Object probes must use original data that would otherwise request evaluation; supplied
    evaluated graphs and evaluated-only queries need separate positive controls.
    DataTransfer must have a valid source
    mesh with UV/color attributes. The tree must be a valid GeometryNodeTree.
    """
    actions = {}
    if object_name:
        actions.update({
            'object_ray_cast': lambda: bpy.data.objects[object_name].ray_cast(
                (0, 0, 10), (0, 0, -1)),
            'object_closest_point': lambda: bpy.data.objects[object_name].closest_point_on_mesh(
                (0, 0, 0)),
        })
    if tree_name:
        actions['tree_debug_graph'] = lambda: bpy.data.node_groups[
            tree_name].debug_lazy_function_graph()
    if modifier_name:
        if not object_name:
            raise ValueError('DataTransfer probe needs its owning object name')
        for prop in ('layers_uv_select_src', 'layers_vcol_vert_select_src',
                     'layers_vcol_loop_select_src'):
            actions['enum_get_' + prop] = lambda prop=prop: getattr(
                bpy.data.objects[object_name].modifiers[modifier_name], prop)
            actions['enum_set_' + prop] = lambda prop=prop: setattr(
                bpy.data.objects[object_name].modifiers[modifier_name], prop, 'ALL')
    if cache_name:
        if not cache_layer_path:
            raise ValueError('Cache probe needs a prepared valid second layer path')
        actions['cache_layer_new'] = lambda: bpy.data.cache_files[cache_name].layers.new(
            filepath=cache_layer_path)
        actions['cache_layer_remove'] = lambda: bpy.data.cache_files[cache_name].layers.remove(
            bpy.data.cache_files[cache_name].layers[0])
    return actions


def make_worker_driver_probe(
    bpy, bridge, scene_name: str, layer_name: str, receipts: list,
    *, extra_actions: Mapping[str, Callable] | None = None,
):
    """Install the returned callable in driver_namespace; require a recorded worker execution.

    The owning fixture supplies enough independent driven objects to exercise evaluation
    workers. A main-thread-only run is missing evidence and must not count as a pass.
    """
    main_ident = threading.get_ident()
    armed = True

    def probe():
        nonlocal armed
        if (not armed or threading.get_ident() == main_ident
                or bridge._test_restore_phase() == 'Inactive'):
            return 0.0
        armed = False
        scene = bpy.data.scenes[scene_name]
        layer = scene.view_layers[layer_name]
        graph = layer.depsgraph
        frame = (scene.frame_current, scene.frame_subframe)
        render_values = (scene.render.use_simplify, scene.render.simplify_volumes)
        particle = scene.tool_settings.particle_edit
        particle_values = (particle.show_particles, particle.display_step,
                           particle.type, particle.shape_object)
        actions = {
            'frame_set': lambda: scene.frame_set(frame[0] + 1),
            'view_layer_update': layer.update,
            'depsgraph_update': graph.update,
            'context_evaluated_depsgraph_get': bpy.context.evaluated_depsgraph_get,
            'use_simplify': lambda: setattr(scene.render, 'use_simplify', not render_values[0]),
            'simplify_volumes': lambda: setattr(scene.render, 'simplify_volumes', 0.25),
            'particle_show': lambda: setattr(particle, 'show_particles', not particle_values[0]),
            'particle_display_step': lambda: setattr(particle, 'display_step', 3),
            'particle_type': lambda: setattr(particle, 'type', particle_values[2]),
            'particle_shape': lambda: setattr(particle, 'shape_object', particle_values[3]),
        }
        actions.update(extra_actions or {})
        record = {'worker_ident': threading.get_ident(), 'main_ident': main_ident,
                  'phase': bridge._test_restore_phase(), 'attempts': {}}
        receipts.append(record)
        for name, action in actions.items():
            try:
                action()
                record['attempts'][name] = {'refused': False}
            except (RuntimeError, AttributeError) as exc:
                message = str(exc)
                record['attempts'][name] = {
                    'error': message,
                    'refused': (
                        ('undo' in message.lower() and 'restor' in message.lower())
                        or (isinstance(exc, AttributeError) and 'read-only' in message.lower())),
                }
            except Exception as exc:
                record['attempts'][name] = {'fixture_error': repr(exc), 'refused': False}
        record['frame_unchanged'] = (scene.frame_current, scene.frame_subframe) == frame
        record['render_values_unchanged'] = (
            scene.render.use_simplify, scene.render.simplify_volumes) == render_values
        record['particle_values_unchanged'] = (
            particle.show_particles, particle.display_step,
            particle.type, particle.shape_object) == particle_values
        return 0.0

    return probe
