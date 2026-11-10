# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Changes operator's actual invoke/draw through its native popup eligibility seam.

Blender's uiTemplateOperatorPropertyButs checks WM_operator_repeat_check before
calling draw; non-macro operators without execute silently draw no UI. The
host adapter models that native seam, not native pixels. The disposable real
click fixture covers registration and the actual native event/render path.
"""
import suite_isolation  # noqa: F401
import ast
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/addons_core'))
from angelo_agent import chat_changes, changes_popup
from angelo_agent.ui_text import iface_


def operator_for(messages, labels):
    tree = ast.parse((ROOT / 'scripts/addons_core/angelo_agent/ui.py').read_text())
    definition = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ANGELO_OT_changes')
    def unexpected(*args):
        raise AssertionError('A normalized change report unexpectedly failed to draw')
    namespace = {'Operator': object, 'chat_changes': chat_changes, 'changes_popup': changes_popup,
                 'get_controller': lambda: NS(state={'messages': messages}),
                 'chat_view': NS(tool_title=lambda message: message['name']), 'iface_': iface_,
                 'paragraph': lambda layout, text, width: layout.label(text=text),
                 'chat_guard': NS(note=unexpected, parts=lambda message: ())}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), '<actual-changes-operator>', 'exec'), namespace)
    operator = namespace['ANGELO_OT_changes']()
    layout = NS(label=lambda **kw: labels.append(kw['text']), separator=lambda: None)
    layout.box = lambda: layout
    operator.layout = layout
    def invoke_popup(actual, width):
        # Exact reason observed natively in R4: missing exec prevents the UI callback entirely.
        if callable(getattr(actual, 'execute', None)):
            actual.draw(None)
        return {'RUNNING_MODAL'}
    return operator, NS(window_manager=NS(invoke_popup=invoke_popup)), namespace


def message(result):
    return {'role': 'activity', 'name': 'angelo_scene_program', 'status': 'completed',
            'text': 'Changed objects', 'result': result}


class ChangeChipDetails(unittest.TestCase):
    def test_actual_invocation_discloses_39_object_names(self):
        changes = {'added': [{'name': 'Added %02d' % i, 'type': 'MESH'} for i in range(37)],
                   'removed': [{'name': 'Removed %02d' % i, 'type': 'MESH'} for i in range(2)],
                   'modified': [], 'truncated': False}
        labels = []
        operator, context, _ = operator_for([message({'changes': changes})], labels)
        self.assertEqual(operator.invoke(context, None), {'RUNNING_MODAL'})
        for i in range(37):
            self.assertIn('+ Added %02d · MESH' % i, labels)
        for i in range(2):
            self.assertIn('− Removed %02d' % i, labels)
        self.assertFalse(any(label.startswith(('Written:', 'Deleted:')) for label in labels))

    def test_execute_is_read_only_and_does_not_resolve_or_change_chat(self):
        messages = [message({'changes': {'added': [], 'removed': [], 'modified': []}})]
        before = deepcopy(messages)
        operator, context, namespace = operator_for(messages, [])
        namespace['get_controller'] = lambda: self.fail('Read-only execute must not resolve/mutate a chat')
        execute = getattr(operator, 'execute', None)
        self.assertTrue(callable(execute), 'Native popup requires a read-only execute callback')
        self.assertEqual(execute(context), {'FINISHED'})
        self.assertEqual(messages, before)
        self.assertFalse(getattr(operator, 'bl_options', set()) & {'UNDO', 'REGISTER'})

    def test_actual_popup_preserves_explicit_file_paths_and_journal_scope(self):
        labels = []
        operator, context, _ = operator_for([message({'file_changes': {
            'written': ['exports/mesh.obj'], 'deleted': ['exports/old.obj']}, 'journaled': True})], labels)
        operator.invoke(context, None)
        self.assertIn('Written: exports/mesh.obj', labels)
        self.assertIn('Deleted: exports/old.obj', labels)
        self.assertIn("Restoring a snapshot reverts this file; Undo doesn't.", labels)

    def test_actual_popup_keeps_modified_fields_and_partial_change_warning(self):
        labels = []
        operator, context, _ = operator_for([message({'partial_changes_possible': True, 'changes': {
            'added': [], 'removed': [], 'modified': [{'name': 'Cube', 'fields': ['matrix', 'geometry']}],
            'truncated': False}})], labels)
        operator.invoke(context, None)
        self.assertIn('~ Cube · matrix, geometry', labels)
        self.assertIn('Operation failed; partial changes may remain.', labels)
        self.assertIn('Operation read-backs, not a live diff after manual edits/Undo.', labels)


if __name__ == '__main__':
    unittest.main()
