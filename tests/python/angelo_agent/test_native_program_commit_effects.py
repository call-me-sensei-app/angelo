# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Commit field transfer contracts independent of Blender and provider runtimes."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.program_commit import _settings, _set_settings, _set_transforms, _CURRENT_TRANSFORM_EFFECTS, ProgramCommitMixin
from angelo_agent.program_jobs import ProgramError
from angelo_agent.program_fields import remap_parent
from angelo_agent.native_programs import BlenderProgramBackend
from angelo_agent.scene_read_values import SceneQueryError, revision_omission


class CommitEffectTests(unittest.TestCase):
    def test_pointer_and_enum_properties_do_not_require_is_array(self):
        pointer = SimpleNamespace(as_pointer=lambda: 91)
        properties = [SimpleNamespace(identifier='object', type='POINTER', is_readonly=False),
                      SimpleNamespace(identifier='operation', type='ENUM', is_readonly=False),
                      SimpleNamespace(identifier='axes', type='BOOLEAN', is_readonly=False, is_array=True)]
        holder = SimpleNamespace(bl_rna=SimpleNamespace(properties=properties), object=pointer,
                                 operation='INTERSECT', axes=[True, False, True])
        values = _settings(holder)
        self.assertIs(values['object'], pointer)
        self.assertEqual(values['operation'], 'INTERSECT')
        self.assertEqual(values['axes'], (True, False, True))

    def test_pointer_transfer_remaps_staged_object_identity(self):
        staged = SimpleNamespace(as_pointer=lambda: 11)
        original = SimpleNamespace(as_pointer=lambda: 12)
        target = SimpleNamespace(object=None, axes=[False, False, False])
        _set_settings(target, {'object': staged, 'axes': (True, False, True)}, {11: original})
        self.assertIs(target.object, original)
        self.assertEqual(tuple(target.axes), (True, False, True))

    def test_silent_setting_transfer_failure_is_not_success(self):
        class IgnoredPointer:
            @property
            def object(self):
                return None
            @object.setter
            def object(self, _value):
                pass
        with self.assertRaisesRegex(ProgramError, 'did not retain'):
            _set_settings(IgnoredPointer(), {'object': SimpleNamespace(as_pointer=lambda: 11)}, {})

    def test_rotation_mode_is_copied_before_current_channels_and_rollback(self):
        class ConvertingMode:
            def __init__(self):
                self.order = []
            def __setattr__(self, name, value):
                if name != 'order':
                    self.order.append(name)
                if name == 'rotation_mode':
                    object.__setattr__(self, 'rotation_euler', (0., 0., 0.))
                object.__setattr__(self, name, value)
        target = ConvertingMode()
        values = {'rotation_euler': (0., 0., 1.5), 'rotation_mode': 'XYZ', 'location': (1., 2., 3.)}
        _set_transforms(target, values)
        self.assertEqual(target.order[0], 'rotation_mode')
        self.assertEqual(target.rotation_euler, values['rotation_euler'])
        self.assertIn('kind:keyframe_insert', _CURRENT_TRANSFORM_EFFECTS)
        self.assertIn('transforms', _CURRENT_TRANSFORM_EFFECTS)

    def test_modifier_ui_active_and_expansion_flags_are_not_semantic_settings(self):
        fields = [('is_active', 'BOOLEAN'), ('show_expanded', 'BOOLEAN'), ('show_viewport', 'BOOLEAN')]
        holder = SimpleNamespace(bl_rna=SimpleNamespace(properties=[SimpleNamespace(identifier=name,type=kind,is_readonly=False)
                                 for name,kind in fields]), is_active=True, show_expanded=False, show_viewport=True)
        self.assertEqual(_settings(holder), {'show_viewport':True})

    def test_parent_identity_remap_preserves_inverse_and_local_fields(self):
        class Inverse:
            def __init__(self, value):
                self.value = value
            def copy(self):
                return Inverse(self.value)
        class Child:
            def __setattr__(self, name, value):
                if name == 'parent':
                    object.__setattr__(self, 'matrix_parent_inverse', Inverse(0))
                object.__setattr__(self, name, value)
        from angelo_agent.program_fields import TRANSFORM_FIELDS
        child = Child()
        for name in TRANSFORM_FIELDS:
            setattr(child, name, 'XYZ' if name == 'rotation_mode' else (1.,2.,3.))
        child.parent_type, child.parent_bone = 'BONE', 'Bone'
        child.matrix_parent_inverse = Inverse(42)
        parent = object()
        remap_parent(child, parent)
        self.assertIs(child.parent, parent)
        self.assertEqual(child.matrix_parent_inverse.value, 42)
        self.assertEqual(child.parent_type, 'BONE')
        self.assertEqual(child.parent_bone, 'Bone')
        self.assertEqual(child.location, (1.,2.,3.))


class LiveRevisionTests(unittest.TestCase):
    """Post-commit handles/revisions come from the backend's production revision path."""

    class Backend(ProgramCommitMixin):
        _revision = BlenderProgramBackend._revision

        def __init__(self, store):
            self.store, self.window = store, 'window'

    @staticmethod
    def obj(uid, name, kind='MESH'):
        return SimpleNamespace(name=name, type=kind, as_pointer=lambda: uid)

    def setUp(self):
        self.written, self.made, self.curve = self.obj(1, 'Written'), self.obj(2, 'Made\nName' * 20), self.obj(3, 'Curve', 'CURVE')
        calls = self.calls = []

        def revision(window, obj):
            calls.append(obj.name)
            if obj.type == 'CURVE':
                raise SceneQueryError('Exact program revision unavailable: unfingerprinted_data:CURVE')
            return 'r%d' % obj.as_pointer()

        self.store = SimpleNamespace(object_revision=revision, object_handle=lambda obj: 'scn:e:%d' % obj.as_pointer(),
                                     document_epoch=lambda: 'e', SceneQueryError=SceneQueryError)
        self.stage = SimpleNamespace(originals={'scn:e:1': self.written}, binding={'writes': ('scn:e:1',)},
                                     context=SimpleNamespace(aliases={'made': self.made}))

    def test_written_and_created_objects_report_exact_revisions_roles_and_aliases(self):
        result = self.Backend(self.store).post_commit_revisions(self.stage, [self.made, self.curve], clock=lambda: 0.)
        written, made, curve = result['objects']
        self.assertEqual((result['status'], result['truncated']), ('COMMITTED', False))
        self.assertEqual(written, {'handle': 'scn:e:1', 'name': 'Written', 'type': 'MESH', 'role': 'WRITTEN',
                                   'object_revision': 'r1'})
        self.assertEqual((made['role'], made['alias'], made['object_revision']), ('CREATED', '$made', 'r2'))
        self.assertEqual(len(made['name']), 128, 'Names are bounded untrusted labels')
        self.assertEqual(curve['revision_omitted'], {'reason': 'UNSUPPORTED', 'detail': 'unfingerprinted_data:CURVE'})
        self.assertNotIn('object_revision', curve)

    def test_budget_is_cooperative_and_never_skips_the_first_object(self):
        ticks = iter([0., 1., 2.])
        result = self.Backend(self.store).post_commit_revisions(self.stage, [self.made, self.curve], clock=lambda: next(ticks))
        self.assertEqual(result['objects'][0]['object_revision'], 'r1')
        self.assertEqual([row['revision_omitted']['reason'] for row in result['objects'][1:]], ['BUDGET', 'BUDGET'])
        self.assertEqual(self.calls, ['Written'], 'An exhausted budget starts no further revision')

    def test_a_non_string_revision_is_an_omission_not_an_invented_value(self):
        self.store.object_revision = lambda window, obj: {'uncertain': True}
        row = self.Backend(self.store).post_commit_revisions(self.stage, [], clock=lambda: 0.)['objects'][0]
        self.assertEqual(row['revision_omitted']['reason'], 'UNSUPPORTED')
        self.assertTrue(row['revision_omitted']['detail'].startswith('revision_error:'))


class RevisionOmissionTests(unittest.TestCase):
    def test_closed_codes_budget_and_unknown_errors_name_only_their_type(self):
        self.assertEqual(revision_omission(SceneQueryError('Exact program revision unavailable: original_state_work_budget')),
                         {'reason': 'BUDGET', 'detail': 'original_state_work_budget'})
        self.assertEqual(revision_omission(SceneQueryError('No codes here')), {'reason': 'UNSUPPORTED', 'detail': 'No codes here'})
        self.assertEqual(revision_omission(RuntimeError('secret /path/name')),
                         {'reason': 'UNSUPPORTED', 'detail': 'revision_error:RuntimeError'})


if __name__ == '__main__':
    unittest.main()
