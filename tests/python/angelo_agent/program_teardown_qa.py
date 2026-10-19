# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native partial-removal and unsupported custom-link admission regressions."""

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import bpy
from angelo_agent import native_programs as native, program_storage as storage, scene_queries as queries
from angelo_agent.program_jobs import ProgramError, PREVIEWS, WRITER
from program_qa_helpers import args_for, move, no_stages, FullStore, spent_budget


class FailingBatchData:
    def __getattr__(self, name):
        return getattr(bpy.data, name)

    def batch_remove(self, **_kwargs):
        raise RuntimeError("injected native batch cleanup unavailable")


@patch.object(storage.ProgramStorageMixin, "memory_staging", False)
def partial_teardown_regressions(window, source, settle, check):
    """Release each job after a freed RNA wrapper, retaining exact Main survivors."""
    remove = native.BlenderProgramBackend._remove_ids
    fake_bpy = SimpleNamespace(types=bpy.types, data=FailingBatchData())

    def fail_after_first(self, ids):
        first = next(block for block in ids if isinstance(block, bpy.types.Object))
        remove(self, [first])
        raise RuntimeError("injected teardown failure AFTER first ID was freed")

    def assert_pending(label, baseline, fingerprint):
        check(label + '_retains_exact_main_ownership', storage.has_pending_cleanup() and
              native.runtime_is_staging() and native._fingerprint(source) == fingerprint and
              set(native._main_ids()) != baseline)
        try:
            native.begin(window, 'angelo_scene_program', move(window, source))
        except ProgramError as error:
            check(label + '_blocks_new_staging', 'blocked' in str(error))
        else:
            check(label + '_blocks_new_staging', False)
        check(label + '_has_no_writer_after_final', WRITER.owner is None)

    for label in ('cancel', 'suspension', 'preview_context', 'hydration'):
        baseline, fingerprint = set(native._main_ids()), native._fingerprint(source)
        job = native.begin(window, 'angelo_scene_program', move(window, source, mode='PREVIEW' if label == 'preview_context' else 'APPLY'))
        record = None
        if label == 'preview_context':
            result = settle(job)
            identity = result['preview_id']
            record = PREVIEWS.get(identity, native._owner(None, window))
        else:
            # Preparation alone spends this poll, so the next one must hydrate.
            with spent_budget():
                job.poll()
            if label in {'cancel', 'suspension'}:
                job.stage = job.backend.resume_stage(job.stage)
        with patch.object(storage, '_bpy', return_value=fake_bpy), patch.object(native.BlenderProgramBackend, '_remove_ids', new=fail_after_first):
            if label == 'cancel':
                result = job.cancel()
                check('partial_cancel_preserves_cleanup_warning', result['cancelled'] and result.get('cleanup_warning') and
                      result.get('cleanup_pending') and not result['scene_changed'] and job.stage is None)
            elif label == 'suspension':
                result = job.poll()
                check('partial_suspension_preserves_cleanup_warning', not result['success'] and result.get('cleanup_warning') and
                      result.get('cleanup_pending') and not result['scene_changed'] and job.stage is None)
            elif label == 'preview_context':
                try:
                    with native.preview_context(identity, window=window) as stage:
                        check('partial_preview_inspection_is_private', stage.scene != window.scene)
                except ProgramError as error:
                    check('partial_preview_context_preserves_cleanup_warning', error.cleanup_pending and 'AFTER first' in str(error) and
                          identity in PREVIEWS.records and Path(record['stage'].bundle).exists())
                else:
                    check('partial_preview_context_preserves_cleanup_warning', False)
            else:
                with patch.object(job.backend, 'validate_stage', side_effect=RuntimeError('injected hydration integrity failure')):
                    result = job.poll()
                check('partial_hydration_preserves_original_error_and_cleanup', not result['success'] and
                      'hydration integrity failure' in result['error'] and 'AFTER first' in result['cleanup_error'] and
                      result.get('cleanup_pending') and job.stage is None)
            assert_pending(label, baseline, fingerprint)
        check(label + '_cleanup_retry_removes_only_owned_survivors', not storage.cleanup_pending() and
              not native.runtime_is_staging() and no_stages() and set(native._main_ids()) == baseline and
              native._fingerprint(source) == fingerprint)
        if record is not None:
            native.begin(window, 'angelo_discard_preview', {'preview_id': identity})
            check('partial_preview_keeps_discardable_private_bundle', not Path(record['stage'].bundle).exists())


def custom_property_admission_regression(window, source, settle, check):
    """A declared or undeclared ID property is rejected before any stage exists."""
    foreign = window.scene.objects['Camera']
    image = bpy.data.images.new('Program readonly custom-link fixture', width=1, height=1)
    tree = source.data.materials[0].node_tree
    node = tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    baseline = set(native._main_ids())
    source['Program scalar property'] = 3.25
    source['Program string property'] = 'artist-owned label'
    source['Program group property'] = {'vector': [1., 2., 3.], 'nested': {'enabled': True}}
    for holder, label in ((source, 'object'), (source.data, 'mesh'), (image, 'readonly_image')):
        holder['Program ID link'] = foreign
        for declared in (False, True):
            handle = queries.object_handle(source)
            value = args_for(window, [{'kind':'transform', 'target':handle, 'parameters':{'location':[2,3,4]}}],
                             writes=[source], reads=[foreign] if declared else [], store=FullStore())
            fingerprint = native._fingerprint(source)
            with patch.object(native.BlenderProgramBackend, 'create_stage', side_effect=AssertionError('must reject before stage creation')):
                job = native.begin(window, 'angelo_scene_program', value, store=FullStore())
                result = settle(job)
            check('custom_id_' + label + ('_declared' if declared else '_undeclared') + '_rejected_without_pointer_change',
                  not result['success'] and 'ID-valued custom properties' in result['error'] and
                  holder['Program ID link'] == foreign and native._fingerprint(source) == fingerprint and
                  set(native._main_ids()) == baseline and no_stages())
        del holder['Program ID link']
    fingerprint = native._fingerprint(source)
    job = native.begin(window, 'angelo_scene_program', args_for(window, [{'kind':'transform', 'target':queries.object_handle(source),
                       'parameters':{'location':[2,3,4]}}], writes=[source], store=FullStore()), store=FullStore())
    result = settle(job)
    check('numeric_string_nested_custom_properties_remain_supported', result.get('ready_to_commit') and
          native._fingerprint(source) == fingerprint and set(native._main_ids()) == baseline and no_stages())
    image['Program ID link'] = foreign
    result = job.commit()
    check('readonly_custom_id_inserted_after_staging_invalidates_commit', not result['success'] and
          'ID-valued custom properties' in result['error'] and not result['scene_changed'] and
          image['Program ID link'] == foreign and native._fingerprint(source) == fingerprint and
          set(native._main_ids()) == baseline and no_stages())
    del image['Program ID link']
    for name in ('Program scalar property', 'Program string property', 'Program group property'):
        del source[name]
    tree.nodes.remove(node)
    bpy.data.images.remove(image)
