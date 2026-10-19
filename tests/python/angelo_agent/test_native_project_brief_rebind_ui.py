# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit public-note review is not tool authorization or guessed continuity."""

import ast
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import display_text, project_brief_rebind_ui, ui_text
from angelo_agent.agent_workspace_ui import control_lines


class ProjectBriefRebindUITests(unittest.TestCase):
    def setUp(self):
        self.document = ('native-instance', 1)
        self.review = {'revision': 7, 'brief': {'objective': 'Preserve exact public goal', 'style': 'Clean',
            'constraints': ['No automatic changes'], 'dimensions': [{'width': .123456789012345}],
            'units': {'system': 'METRIC', 'scale_length': 1}}, 'planned_steps': ['Reobserve'],
            'references': [{'name': 'Historical reference', 'stale': True}], 'assets': []}
        self.controller = SimpleNamespace(state={'id': 'chat', 'options': {'approval': 'READ_ONLY'}, 'task_memory': {'approved': True}},
            busy=False, document_invalid=False, document=self.document, store=SimpleNamespace(directory=Path('/private/state')),
            project_rebind_review=lambda: copy.deepcopy(self.review), confirm_project_rebind=Mock(return_value=True), save=Mock())
        self.ui = SimpleNamespace(_controllers={'chat': self.controller}, get_controller=lambda: self.controller,
            document_key=Mock(return_value=self.document), main_window=lambda: 'native-window')
        self.capture = Mock(return_value='host-native-attestation')
        namespace = {'Operator': object, 'iface_': ui_text.iface_, 'rpt_': ui_text.rpt_, 'review_line_type': object, 'control_lines': control_lines,
            'review_identity': project_brief_rebind_ui.review_identity, 'ui': self.ui, 'capture_loaded_project': self.capture,
            'add_lines': lambda operator, values: operator.review_lines.extend(values),
            **{name: (lambda **kwargs: None) for name in ('BoolProperty', 'CollectionProperty', 'IntProperty', 'StringProperty')}}
        module = ast.parse(Path(project_brief_rebind_ui.__file__).read_text())
        definition = next(node for node in ast.walk(module) if isinstance(node, ast.ClassDef))
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(project_brief_rebind_ui.__file__), 'exec'), namespace)
        self.operator = namespace['ANGELO_OT_use_project_brief']()
        self.operator.session_id, self.operator.review_confirmed, self.operator.review_lines = 'chat', False, []
        self.operator.report = Mock()
        self.context = SimpleNamespace(window_manager=SimpleNamespace(invoke_props_dialog=Mock(return_value={'RUNNING_MODAL'})))

    def test_direct_execute_and_memory_approved_flag_cannot_bypass_real_review(self):
        self.operator.review_confirmed = True
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.controller.confirm_project_rebind.assert_not_called()
        self.capture.assert_not_called()

    def test_unchecked_exact_public_review_then_explicit_confirmation_is_metadata_only(self):
        self.assertEqual(self.operator.invoke(self.context, None), {'RUNNING_MODAL'})
        self.assertFalse(self.operator.review_confirmed)
        text = '\n'.join(self.operator.review_lines)
        for required in ('Preserve exact public goal', 'Width: 0.123456789012345', 'Historical reference', 'grants NO scene edits'):
            self.assertIn(required, text)
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.operator.review_confirmed = True
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        self.controller.confirm_project_rebind.assert_called_once_with('host-native-attestation', 7)
        self.assertEqual(self.controller.state['options']['approval'], 'READ_ONLY')

    def test_changed_exact_notes_or_document_rejects_confirmation(self):
        self.operator.invoke(self.context, None)
        self.operator.review_confirmed = True
        self.review['brief']['constraints'].append('Changed without revision')
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.controller.confirm_project_rebind.assert_not_called()
        self.operator.invoke(self.context, None)
        self.operator.review_confirmed = True
        self.ui.document_key.return_value = ('different', 2)
        self.assertEqual(self.operator.execute(self.context), {'CANCELLED'})
        self.capture.assert_not_called()

    def test_postconfirmation_metadata_save_failure_is_honest_finished_warning(self):
        self.operator.invoke(self.context, None)
        self.operator.review_confirmed = True
        self.controller.save.side_effect = OSError('Metadata storage unavailable')
        self.assertEqual(self.operator.execute(self.context), {'FINISHED'})
        self.assertIn('WARNING', self.operator.report.call_args.args[0])
        self.assertIn('No scene edits or tool approvals', self.operator.report.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
