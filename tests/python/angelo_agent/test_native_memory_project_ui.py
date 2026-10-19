# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded host lifecycle queue; no project I/O in controller lookup/draw."""

import ast
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import memory_project_ui as project_ui


class MemoryProjectUITests(unittest.TestCase):
    def setUp(self):
        project_ui.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'actual.blend')
        self.document = ('current-native-instance', 1)
        self.controller = SimpleNamespace(document=self.document, document_invalid=False, state={'id': 'chat'},
            store=SimpleNamespace(directory=Path(self.directory.name)), bind_project=Mock(return_value=True), save=Mock())
        self.ui = SimpleNamespace(document_key=lambda: self.document, main_window=lambda: 'native-window',
            bpy=SimpleNamespace(data=SimpleNamespace(filepath=self.path)), _recovery_original_path='/must-not-attest/original.blend')
        self.receipt = SimpleNamespace(valid_for=Mock(return_value=True))

    def tearDown(self):
        if project_ui._worker:
            project_ui._worker.join(timeout=3)
        project_ui.clear()
        self.directory.cleanup()

    def await_worker(self):
        project_ui._worker.join(timeout=3)
        self.assertFalse(project_ui._worker.is_alive())

    def test_lookup_scheduling_has_no_attestation_or_project_reads(self):
        with patch.object(project_ui, 'capture_loaded_project') as capture, patch.object(project_ui, 'admit_saved_project') as admit:
            project_ui.schedule(self.ui, self.controller)
        capture.assert_not_called()
        admit.assert_not_called()
        self.assertEqual(len(project_ui._pending), 1)

    def test_attestation_is_main_thread_and_hash_is_only_file_worker(self):
        main = threading.get_ident()
        threads = {}
        loaded = object()
        def capture(*args):
            threads['capture'] = threading.get_ident()
            return loaded
        def admit(path, document, profile, *, loaded_project):
            threads['hash'] = threading.get_ident()
            self.assertEqual(path, self.path)
            self.assertIs(loaded_project, loaded)
            return self.receipt
        with patch.object(project_ui, 'capture_loaded_project', side_effect=capture), patch.object(project_ui, 'admit_saved_project', side_effect=admit):
            project_ui.schedule(self.ui, self.controller)
            project_ui.pump(self.ui)
            self.await_worker()
            project_ui.pump(self.ui)
        self.assertEqual(threads['capture'], main)
        self.assertNotEqual(threads['hash'], main)
        self.controller.bind_project.assert_called_once_with(self.receipt)
        self.controller.save.assert_called_once()

    def test_late_result_after_load_clear_cannot_rebind_new_scene(self):
        entered, release = threading.Event(), threading.Event()
        def admit(*args, **kwargs):
            entered.set()
            release.wait(3)
            return self.receipt
        with patch.object(project_ui, 'capture_loaded_project', return_value=object()), patch.object(project_ui, 'admit_saved_project', side_effect=admit):
            project_ui.schedule(self.ui, self.controller)
            project_ui.pump(self.ui)
            self.assertTrue(entered.wait(3))
            project_ui.clear()
            release.set()
            self.await_worker()
            project_ui.pump(self.ui)
        self.controller.bind_project.assert_not_called()

    def test_changed_path_and_document_drop_queued_lifecycle_request(self):
        project_ui.schedule(self.ui, self.controller)
        self.ui.bpy.data.filepath = '/different/project.blend'
        with patch.object(project_ui, 'capture_loaded_project') as capture:
            project_ui.pump(self.ui)
        capture.assert_not_called()
        self.controller.bind_project.assert_not_called()

    def test_queue_is_bounded_and_scene_policy_fields_are_not_changed(self):
        with patch.object(project_ui, 'admit_saved_project') as admit:
            for index in range(40):
                target = SimpleNamespace(**vars(self.controller))
                target.state = {'id': str(index), 'options': {'approval': 'READ_ONLY'}}
                project_ui.schedule(self.ui, target)
        admit.assert_not_called()
        self.assertEqual(len(project_ui._pending), 8)
        self.assertTrue(all(item[0].state['options']['approval'] == 'READ_ONLY' for item in project_ui._pending.values()))

    def test_source_guard_no_hash_in_get_controller_or_draw_or_worker_bpy(self):
        root = Path(project_ui.__file__).parent
        ui = ast.parse((root / 'ui.py').read_text())
        lookup = next(node for node in ui.body if isinstance(node, ast.FunctionDef) and node.name == 'get_controller')
        source = ast.unparse(lookup)
        self.assertIn('schedule(', source)
        self.assertNotIn('bind_current(', source)
        self.assertNotIn('admit_saved_project(', source)
        source = Path(project_ui.__file__).read_text()
        verify = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == 'verify')
        self.assertNotIn('bpy', ast.unparse(verify))
        self.assertIn('loaded_project=loaded', ast.unparse(verify))
        draw = next(node for node in ast.parse((root / 'chat_view.py').read_text()).body if isinstance(node, ast.FunctionDef) and node.name == 'draw')
        self.assertNotIn('admit_saved_project(', ast.unparse(draw))


if __name__ == '__main__':
    unittest.main()
